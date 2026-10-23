"""Service-owned subscription credentials and atomic admission boundaries.

Credentials never enter SQLite, events or project context. The OAuth manager is
shared by all direct provider instances so token rotation has one owner.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from kira.domain.contracts import Json, KiraError

if TYPE_CHECKING:
    from kira.backends.openai_auth import ChatGPTAuth
    from kira.service.server import Service

BACKEND = "openai_chatgpt"
PENDING_LOGIN = {"awaiting_browser", "exchanging"}


class DirectAccounts:
    def __init__(self, service: Service) -> None:
        self.service = service
        self._auth: ChatGPTAuth | None = None
        self.changing = False

    @property
    def auth(self) -> ChatGPTAuth:
        if self._auth is None:
            from kira.backends.openai_auth import ChatGPTAuth
            self._auth = ChatGPTAuth(self.service.paths.config / "openai-chatgpt",
                                    protect_secrets=functools.partial(
                                        self.service.redactor.set_provider_values,
                                        owner=BACKEND))
        return self._auth

    def busy(self) -> bool:
        db = self.service.store.db
        checks = (
            "SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id "
            "WHERE s.backend=? AND (r.state NOT IN ('completed','failed','cancelled','interrupted') "
            "OR EXISTS (SELECT 1 FROM project_reservations p WHERE p.owner_kind='run' AND p.owner_id=r.id)) LIMIT 1",
            "SELECT 1 FROM team_runs t WHERE t.backend=? AND (t.state NOT IN "
            "('completed','failed','cancelled','interrupted','paused') OR EXISTS "
            "(SELECT 1 FROM project_reservations p WHERE p.owner_kind='team' AND p.owner_id=t.id)) LIMIT 1",
            "SELECT 1 FROM prompt_queue q JOIN sessions s ON s.id=q.session_id "
            "WHERE s.backend=? AND q.status IN ('pending','starting','running') LIMIT 1",
            "SELECT 1 FROM assistant_delegations d JOIN sessions s ON s.id=d.session_id "
            "WHERE s.backend=? AND d.state='pending' LIMIT 1",
        )
        return (any(db.execute(query, (BACKEND,)).fetchone() for query in checks)
                or db.execute("SELECT 1 FROM direct_context_operations WHERE status='pending' LIMIT 1").fetchone() is not None
                or db.execute("SELECT 1 FROM browser_sessions WHERE manual=1 OR state='unknown' LIMIT 1").fetchone() is not None)

    async def status(self) -> Json:
        result = await self.auth.status()
        busy = self.changing or self.busy()
        return {**result, "mutation_allowed": not busy,
                "busy_reason": "Finish direct work and queued requests before changing accounts" if busy else ""}

    async def prepare(self, settings: Json, *, bind_active: bool = False) -> Json:
        """Validate before admission; caller commits its reservation without awaiting.

        Test backend factories implement their own identity boundary and do not
        initiate OAuth. Production providers always use the protected manager.
        """
        if set(settings) & {"service_tier", "temperature", "seed", "think", "num_ctx"}:
            raise KiraError("UNSUPPORTED_SETTING", "OpenAI direct does not support these provider-specific settings")
        if self.changing:
            raise KiraError("BUSY", "ChatGPT account settings are changing")
        if self.service.backend_factory is not None and self._auth is None:
            return dict(settings)
        status = await self.auth.status()
        if self.changing or status.get("login", {}).get("state") in PENDING_LOGIN:
            raise KiraError("BUSY", "Finish or cancel ChatGPT sign-in before starting direct work")
        account_id = status.get("active_account_id") if bind_active else settings.get("openai_account_id", status.get("active_account_id"))
        account = next((a for a in status.get("accounts", []) if a.get("id") == account_id), None)
        if not account or not account.get("connected"):
            raise KiraError("AUTH_REQUIRED", "Sign in with this chat's ChatGPT account before using OpenAI direct")
        if not account.get("plan_enabled"):
            raise KiraError("PLAN_PERMISSION_REQUIRED", "Enable Use your ChatGPT plan for Kira in ChatGPT settings")
        return {**settings, "openai_account_id": account_id}

    async def dispatch(self, action: str, account_id: str | None = None) -> Json:
        if action == "status":
            return await self.status()
        if self.changing or self.busy():
            raise KiraError("BUSY", "Finish direct work and queued requests before changing accounts")
        self.changing = True
        try:
            if action in {"logout", "select"}:
                await self.service.web.close_account(account_id if action == "logout" else None)
            if action == "login":
                return await self.auth.login(account_id)
            if action == "select":
                if not account_id:
                    raise KiraError("INVALID_ARGUMENT", "account_id is required")
                return await self.auth.select(account_id)
            if action == "logout":
                return await self.auth.logout(account_id)
            if action == "cancel":
                return await self.auth.cancel_login()
            raise KiraError("INVALID_ARGUMENT", "Unsupported ChatGPT account action")
        finally:
            self.changing = False

    async def close(self) -> None:
        if self._auth is not None:
            await self._auth.close()
            self._auth = None
