"""Durable provider transitions within an ordinary Kira conversation.

This module only selects a provider configuration. It never starts a provider,
replays tools, changes an account registration, or rewrites conversation events.
Native references belong to one generation and cannot cross a transition.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from kira.backends.claude_code import BACKEND as CLAUDE_CODE_BACKEND, require_sdk as require_claude_code
from kira.domain.contracts import Json, KiraError
from kira.domain.providers import WEB_BACKENDS
from kira.domain.web import WEB_SETTINGS
from kira.service.direct_accounts import BACKEND as DIRECT_BACKEND
from kira.service.project_context import ProjectChats
from kira.service.storage import encode, now

if TYPE_CHECKING:
    from kira.service.server import Service

METHODS = frozenset({"session.providers", "session.switch"})
DEEPSEEK_BACKEND = "deepseek_account"
DEEPSEEK_WEB_BACKEND = "deepseek_web"
GOOGLE_CLOUD_BACKEND = "google_cloud"
BACKENDS = ("ollama", "codex", DIRECT_BACKEND, DEEPSEEK_BACKEND, DEEPSEEK_WEB_BACKEND,
            CLAUDE_CODE_BACKEND, GOOGLE_CLOUD_BACKEND)
SHARED_SETTINGS = frozenset({"tools", "max_tool_rounds", "max_output_bytes"})
_OWN_SETTINGS = {
    # The desktop offers every effort a model's catalog declares; Ollama keeps
    # such a choice although its native API does not use it.
    "ollama": SHARED_SETTINGS | {"num_ctx", "temperature", "seed", "think", "reasoning_effort"},
    "codex": SHARED_SETTINGS | {"reasoning_effort", "service_tier"},
    DIRECT_BACKEND: SHARED_SETTINGS | {"reasoning_effort", "openai_account_id"},
    DEEPSEEK_BACKEND: SHARED_SETTINGS | {"reasoning_effort"},
    DEEPSEEK_WEB_BACKEND: SHARED_SETTINGS,
    CLAUDE_CODE_BACKEND: SHARED_SETTINGS | {"reasoning_effort"},
    GOOGLE_CLOUD_BACKEND: SHARED_SETTINGS | {"reasoning_effort", "temperature"},
}
# The settings a chat can save for each provider. Browser preferences belong to
# every provider that receives Kira's browser tools (session.web.update).
PROVIDER_SETTINGS: dict[str, frozenset[str]] = {
    backend: settings | (WEB_SETTINGS if backend in WEB_BACKENDS else frozenset())
    for backend, settings in _OWN_SETTINGS.items()
}
CHAT_SETTINGS = frozenset({"chat_title", "handoff"})
MAX_TRANSITIONS = 100


def _text(params: Json, key: str, maximum: int = 128, *, empty: bool = False) -> str:
    value = params.get(key)
    try:
        valid = (isinstance(value, str) and "\0" not in value
                 and (empty and value == "" or bool(value.strip()))
                 and len(encode(value).encode("utf-8")) <= maximum + 2)
    except UnicodeError:
        valid = False
    if not valid:
        raise KiraError("INVALID_ARGUMENT", f"{key} must be bounded text")
    assert isinstance(value, str)
    return value


class ProviderRouting:
    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = service.store

    def current_generation(self, session_id: str) -> int:
        row = self.store.db.execute(
            "SELECT MAX(generation) FROM session_provider_generations WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0

    def ensure_generation(self, session: Json) -> int:
        """Create the initial generation, within the caller's admission boundary."""
        generation = self.current_generation(session["id"])
        self.store.db.execute(
            "INSERT OR IGNORE INTO session_provider_generations "
            "(session_id,generation,backend,model,settings,backend_ref,created_at,retired_at) "
            "VALUES (?,?,?,?,?,?,?,NULL)",
            (session["id"], generation, session["backend"], session["model"],
             encode(session["settings"]), session.get("backend_ref"), session["created_at"]),
        )
        return generation

    def refresh_generation(self, session: Json) -> int:
        """Keep the active snapshot current without modifying retired generations."""
        generation = self.ensure_generation(session)
        row = self.store.db.execute(
            "SELECT backend,retired_at FROM session_provider_generations "
            "WHERE session_id=? AND generation=?", (session["id"], generation),
        ).fetchone()
        if row is None or row["retired_at"] is not None or row["backend"] != session["backend"]:
            raise KiraError("CONFLICT", "The active provider generation does not match the chat")
        self.store.ensure_run_bindings(session, generation)
        self.store.db.execute(
            "UPDATE session_provider_generations SET model=?,settings=?,backend_ref=? "
            "WHERE session_id=? AND generation=? AND retired_at IS NULL",
            (session["model"], encode(session["settings"]), session.get("backend_ref"),
             session["id"], generation),
        )
        return generation

    def reset_native(self, session: Json) -> int:
        """Retire native state on an admitted identity change, within its transaction.

        Ordinary transcript remains owned by this chat. This prevents opaque state
        reuse, not deletion of already delivered information. No inference, account
        change, permission change, or model/settings change is performed here.
        """
        if not self.store.db.in_transaction:
            raise KiraError("CONFLICT", "Identity rebinding requires an atomic transaction")
        generation = self.refresh_generation(session)
        timestamp = now()
        self.store.db.execute(
            "UPDATE session_provider_generations SET retired_at=? WHERE session_id=? AND generation=?",
            (timestamp, session["id"], generation))
        self.store.db.execute("UPDATE sessions SET backend_ref=NULL WHERE id=?", (session["id"],))
        self.store.db.execute(
            "INSERT INTO session_provider_generations "
            "(session_id,generation,backend,model,settings,backend_ref,created_at,retired_at) "
            "VALUES (?,?,?,?,?,NULL,?,NULL)",
            (session["id"], generation + 1, session["backend"], session["model"],
             encode(session["settings"]), timestamp))
        return generation + 1

    def _latest(self, session: Json, backend: str) -> Json | None:
        if session["backend"] == backend:
            return session
        rows = self.store.all(
            "SELECT * FROM session_provider_generations WHERE session_id=? AND backend=? "
            "ORDER BY generation DESC LIMIT 1", (session["id"], backend),
        )
        return rows[0] if rows else None

    @staticmethod
    def _summary(row: Json) -> Json:
        # Report only settings the provider uses: an effort saved for DeepSeek
        # Web by an older version is not offered back as a choice.
        supported = PROVIDER_SETTINGS.get(row["backend"], frozenset())
        return {"backend": row["backend"], "model": row["model"],
                "reasoning_effort": (row["settings"].get("reasoning_effort", "")
                                     if "reasoning_effort" in supported else ""),
                "account_id": row["settings"].get("openai_account_id", "")}

    def snapshot(self, project_id: str, session_id: str) -> Json:
        """Read configured providers without discovery, authentication, or writes."""
        chats = ProjectChats(self.store)
        session = chats.owned_session(project_id, session_id)
        providers: list[Json] = []
        for backend in BACKENDS:
            latest = self._latest(session, backend)
            providers.append({
                **(self._summary(latest) if latest else {
                    "backend": backend, "model": "", "reasoning_effort": "", "account_id": ""}),
                "used": latest is not None, "current": backend == session["backend"],
            })
        rows = self.store.all(
            "SELECT * FROM session_provider_generations WHERE session_id=? "
            "ORDER BY generation DESC LIMIT ?", (session_id, MAX_TRANSITIONS + 1),
        )
        transitions = [{**self._summary(row), "generation": row["generation"],
                        "created_at": row["created_at"]} for row in reversed(rows[:MAX_TRANSITIONS])]
        return {"session": chats.enrich(session),
                "current_generation": self.current_generation(session_id),
                "providers": providers, "transitions": transitions,
                "transitions_truncated": len(rows) > MAX_TRANSITIONS}

    def _admit(self, project_id: str, session_id: str) -> Json:
        if self.service._closing or self.service.stopping.is_set():
            raise KiraError("SHUTTING_DOWN", "Service is draining; provider changes are not accepted")
        if self.service._settings_changing:
            raise KiraError("BUSY", "Provider settings are being updated")
        chats = ProjectChats(self.store)
        session = chats.owned_session(project_id, session_id)
        if chats.enrich(session)["archived"]:
            raise KiraError("ARCHIVED", "Restore this chat before changing its provider")
        project = self.store.one("projects", project_id)
        self.service.workbench._idle(project)
        queries = (
            ("SELECT 1 FROM prompt_queue WHERE project_id=? "
             "AND status IN ('pending','starting','running') LIMIT 1",
             "Finish or remove queued requests before changing the chat provider"),
            ("SELECT 1 FROM team_runs WHERE project_id=? "
             "AND state NOT IN ('completed','failed','cancelled','interrupted','paused') LIMIT 1",
             "Wait for active project teams before changing the chat provider"),
            ("SELECT 1 FROM assistant_delegations WHERE project_id=? "
             "AND state IN ('pending','running') LIMIT 1",
             "Wait for pending project delegations before changing the chat provider"),
            ("SELECT 1 FROM run_cleanup c JOIN runs r ON r.id=c.run_id "
             "JOIN sessions s ON s.id=r.session_id WHERE s.project_id=? "
             "AND c.state!='stopped' LIMIT 1",
             "Wait for provider cleanup before changing the chat provider"),
        )
        for query, message in queries:
            if self.store.db.execute(query, (project_id,)).fetchone():
                raise KiraError("BUSY", message)
        return session

    def _receipt(self, request_id: str, digest: str, project_id: str, session_id: str) -> Json | None:
        row = self.store.db.execute(
            "SELECT * FROM session_provider_switches WHERE request_id=?", (request_id,),
        ).fetchone()
        if row is None:
            return None
        if (row["fingerprint"], row["project_id"], row["session_id"]) != (
                digest, project_id, session_id):
            raise KiraError("CONFLICT", "request_id already belongs to a different provider change")
        return json.loads(row["result"])

    def _result(self, project_id: str, session_id: str, receipt: Json, *, replayed: bool) -> Json:
        chats = ProjectChats(self.store)
        # A delayed retry may follow another successful switch. Always publish
        # the actual current row while retaining the original immutable receipt.
        return {"session": chats.enrich(chats.owned_session(project_id, session_id)),
                "current_generation": self.current_generation(session_id),
                "switch": receipt, "replayed": replayed}

    def _configuration(self, session: Json, backend: str, effort: str, account_id: str | None) -> Json:
        previous = self._latest(session, backend)
        source = previous["settings"] if previous else session["settings"]
        # Selecting a different provider chooses its separately saved settings.
        # First use carries only common settings; no field crosses providers. A
        # saved setting this provider does not use (from an older version) has
        # no effect and is left out; the retired generation keeps its record.
        allowed = PROVIDER_SETTINGS[backend] if previous else SHARED_SETTINGS
        config = {key: value for key, value in source.items() if key in allowed}
        config.update({key: value for key, value in session["settings"].items() if key in CHAT_SETTINGS})
        if backend == DEEPSEEK_BACKEND:
            # DeepSeek runs always install Kira's service-owned tool loop;
            # do not inherit an Ollama-only tools-off preference.
            config["tools"] = True
        elif backend == DEEPSEEK_WEB_BACKEND:
            # Kira wraps the text-only web transport in a validated tool loop.
            config["tools"] = True
        if effort and "reasoning_effort" in PROVIDER_SETTINGS[backend]:
            config["reasoning_effort"] = effort
        else:
            # DeepSeek Web uses no reasoning effort; it is not saved.
            config.pop("reasoning_effort", None)
        if account_id is not None:
            config["openai_account_id"] = account_id
        self.service._validate_session_settings({key: value for key, value in config.items()
                                                 if key not in CHAT_SETTINGS})
        return config

    async def dispatch(self, method: str, params: Json) -> Json:
        allowed = {"project_id", "session_id"}
        if method == "session.switch":
            allowed |= {"request_id", "backend", "model", "reasoning_effort", "account_id"}
        elif method != "session.providers":
            raise KiraError("METHOD_NOT_FOUND", "Unknown provider routing operation")
        if not isinstance(params, dict) or set(params) - allowed:
            raise KiraError("INVALID_ARGUMENT", "Unexpected provider routing parameters")
        project_id, session_id = _text(params, "project_id"), _text(params, "session_id")
        if method == "session.providers":
            return self.snapshot(project_id, session_id)
        request_id, backend = _text(params, "request_id"), _text(params, "backend", 32)
        if backend not in BACKENDS:
            raise KiraError("INVALID_ARGUMENT", "Unknown backend")
        model = _text(params, "model", 200)
        effort = _text(params, "reasoning_effort", 80, empty=True)
        account_id = _text(params, "account_id", 80) if "account_id" in params else None
        if account_id is not None and backend != DIRECT_BACKEND:
            raise KiraError("INVALID_ARGUMENT", "Account selection requires OpenAI direct")
        normalized = {"project_id": project_id, "session_id": session_id,
                      "backend": backend, "model": model, "reasoning_effort": effort,
                      "account_id": account_id}
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        # Validate ownership even for an idempotent readback.
        ProjectChats(self.store).owned_session(project_id, session_id)
        if receipt := self._receipt(request_id, digest, project_id, session_id):
            return self._result(project_id, session_id, receipt, replayed=True)
        source = self._admit(project_id, session_id)
        generation = self.current_generation(session_id)
        config = self._configuration(source, backend, effort, account_id)
        if backend == DIRECT_BACKEND:
            config = await self.service.direct_accounts.prepare(config)
        elif backend == DEEPSEEK_BACKEND:
            await self.service.deepseek_accounts.prepare()
        elif backend == DEEPSEEK_WEB_BACKEND:
            await self.service.deepseek_web_accounts.prepare()
        elif backend == CLAUDE_CODE_BACKEND:
            require_claude_code()
        elif backend == GOOGLE_CLOUD_BACKEND:
            await self.service.google_accounts.prepare()
        if (backend != source["backend"] or model != source["model"] or
                config.get("openai_account_id") != source["settings"].get("openai_account_id")):
            await self.service.web.before_configuration(project_id, session_id)
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            # Authentication can yield. A duplicate may have completed while
            # awaiting it, or another client may have edited this conversation.
            receipt = self._receipt(request_id, digest, project_id, session_id)
            if receipt is not None:
                self.store.db.execute("COMMIT")
                return self._result(project_id, session_id, receipt, replayed=True)
            current = self._admit(project_id, session_id)
            if current != source or self.current_generation(session_id) != generation:
                raise KiraError("CONFLICT", "The chat changed while choosing its provider; reload its settings")
            generation = self.refresh_generation(source)
            transitioned = (backend != source["backend"] or config.get("openai_account_id") !=
                            source["settings"].get("openai_account_id"))
            if transitioned:
                config.pop("browser_action_approval", None)
            timestamp = now()
            changed = transitioned or model != source["model"] or config != source["settings"]
            if transitioned:
                self.store.db.execute(
                    "UPDATE session_provider_generations SET retired_at=? "
                    "WHERE session_id=? AND generation=?", (timestamp, session_id, generation),
                )
                generation += 1
            backend_ref = None if transitioned else source.get("backend_ref")
            self.store.db.execute(
                "UPDATE sessions SET backend=?,model=?,settings=?,backend_ref=? "
                "WHERE id=? AND project_id=?",
                (backend, model, encode(config), backend_ref, session_id, project_id),
            )
            if transitioned:
                self.store.db.execute(
                    "INSERT INTO session_provider_generations "
                    "(session_id,generation,backend,model,settings,backend_ref,created_at,retired_at) "
                    "VALUES (?,?,?,?,?,?,?,NULL)",
                    (session_id, generation, backend, model, encode(config), None, timestamp),
                )
            else:
                self.refresh_generation(self.store.one("sessions", session_id))
            receipt = {"request_id": request_id, "generation": generation,
                       "backend": backend, "model": model,
                       "account_id": config.get("openai_account_id", ""),
                       "changed": changed, "transitioned": transitioned}
            self.store.db.execute(
                "INSERT INTO session_provider_switches "
                "(request_id,fingerprint,project_id,session_id,generation,result,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (request_id, digest, project_id, session_id, generation, encode(receipt), timestamp),
            )
            self.store.db.execute("COMMIT")
        except BaseException:
            if self.store.db.in_transaction:
                self.store.db.execute("ROLLBACK")
            raise
        return self._result(project_id, session_id, receipt, replayed=False)
