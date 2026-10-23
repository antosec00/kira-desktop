"""Read-only provider allowance snapshots and attributed local usage records."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import TYPE_CHECKING

from kira.domain.account_usage import normalize_claude_plan, unavailable_claude_usage
from kira.domain.contracts import Json, KiraError
from kira.service.storage import now

if TYPE_CHECKING:
    from kira.service.server import Service

MAX_EVENTS = 10_000
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")


class AccountUsage:
    def __init__(self, service: Service):
        self.service = service
        self._codex_lock = asyncio.Lock()

    def local(self, backend: str, account_id: str | None, observed_at: str) -> Json:
        since = (datetime.fromisoformat(observed_at) - timedelta(days=7)).isoformat()
        # Unbound legacy runs are attributable only while their session has never
        # changed provider/account. Missing evidence must not follow today's account.
        selection = """FROM runs r JOIN sessions s ON s.id=r.session_id
            LEFT JOIN run_provider_bindings b ON b.run_id=r.id
            WHERE r.created_at>=? AND r.created_at<=? AND COALESCE(b.backend,s.backend)=?
            AND (b.run_id IS NOT NULL OR NOT EXISTS
                (SELECT 1 FROM session_provider_generations g WHERE g.session_id=s.id AND g.generation>0))"""
        args: tuple[object, ...] = (since, observed_at, backend)
        if backend == "openai_chatgpt":
            selection += " AND json_extract(COALESCE(b.settings,s.settings),'$.openai_account_id')=?"
            args += (account_id,)
        store = self.service.store
        runs = store.db.execute("SELECT count(*) " + selection, args).fetchone()[0]
        rows = store.all("WITH selected AS (SELECT r.id,r.created_at " + selection + ") "
            "SELECT e.run_id,e.seq,e.payload FROM events e JOIN selected r ON r.id=e.run_id "
            "WHERE e.type='usage' ORDER BY r.created_at DESC,r.id DESC,e.seq DESC LIMIT ?",
            (*args, MAX_EVENTS + 1))
        measured: dict[tuple[str, int | str], Json] = {}
        maintenance_requests = 0
        rejected = False
        for row in rows[:MAX_EVENTS]:
            payload = row["payload"]
            if payload.get("scope") == "account":
                continue  # Plan-limit notices describe the account, not a request.
            round_ = payload.get("round")
            if payload.get("source") == backend and payload.get("scope") == "context_compaction":
                identity = payload.get("measurement_id")
                if isinstance(identity, str) and 1 <= len(identity) <= 128:
                    key = (row["run_id"], identity)
                    if key not in measured:
                        measured[key] = payload
                        maintenance_requests += 1
                    continue
            if (payload.get("source") != backend or type(round_) is not int
                    or not 0 <= round_ <= 1_000_000):
                rejected = True
                continue
            measured.setdefault((row["run_id"], round_), payload)
        coverage = {field: sum(type(item.get(field)) is int and 0 <= item[field] < 2**53
                              for item in measured.values()) for field in TOKEN_FIELDS}
        totals = {field: sum(item[field] for item in measured.values())
                  if measured and coverage[field] == len(measured) else None for field in TOKEN_FIELDS}
        measured_runs = len({identity for identity, _ in measured})
        manual_requests = 0
        manual_truncated = False
        if backend == "openai_chatgpt" and account_id:
            operations = store.db.execute("SELECT result FROM direct_context_operations WHERE account_id=? "
                "AND created_at>=? AND created_at<=? ORDER BY created_at DESC LIMIT ?",
                (account_id, since, observed_at, MAX_EVENTS + 1)).fetchall()
            manual_truncated = len(operations) > MAX_EVENTS
            rejected |= manual_truncated
            for operation in operations[:MAX_EVENTS]:
                usage = json.loads(operation[0]).get("usage", {})
                count = usage.get("requests", 0)
                if type(count) is not int or count <= 0:
                    continue
                manual_requests += count
                for field in TOKEN_FIELDS:
                    value = usage.get(field)
                    covered = usage.get("field_coverage", {}).get(field, count if type(value) is int else 0)
                    if type(covered) is int and 0 <= covered <= count:
                        coverage[field] += covered
                    if type(value) is int and 0 <= value < 2**63:
                        if totals[field] is None and not measured and manual_requests == count:
                            totals[field] = value
                        else:
                            previous = totals[field]
                            if previous is not None:
                                totals[field] = previous + value
                    else:
                        totals[field] = None
        complete = (bool(measured or manual_requests) and measured_runs == runs and not rejected and len(rows) <= MAX_EVENTS
                    and all(coverage[field] == len(measured) + manual_requests for field in ("input_tokens", "output_tokens", "total_tokens")))
        return {"period_days": 7, "since": since, "until": observed_at,
                "runs": runs, "runs_with_usage": measured_runs, "requests": len(measured) + manual_requests,
                "context_compaction_requests": maintenance_requests + manual_requests,
                **totals, "coverage": "complete" if complete else "partial" if measured or manual_requests else "none",
                "field_coverage": coverage, "truncated": len(rows) > MAX_EVENTS or manual_truncated,
                "scope": "kira_recorded", "is_plan_allowance": False}

    async def read(self, backend: str, account_id: str | None) -> Json:
        if backend == "codex":
            async with self._codex_lock:
                if self.service._closing or self.service.stopping.is_set() or self.service._settings_changing:
                    raise KiraError("BUSY", "Provider settings or service state are changing")
                provider = self.service._control_backends.get("codex")
                if provider is None:
                    provider = self.service._backend("codex")
                    self.service._control_backends["codex"] = provider
                read = getattr(provider, "account_usage", None)
                if read is None:
                    return self.unavailable(backend, "unsupported")
                try:
                    result = await asyncio.wait_for(read(), timeout=20)
                except Exception:
                    return self.unavailable(backend, "unavailable")
                if self.service._settings_changing or self.service._control_backends.get("codex") is not provider:
                    return self.unavailable(backend, "unavailable")
                return dict(self.service.redactor.clean(result))
        observed_at = now()
        if backend == "claude_code":
            return {**self.claude_plan(), "local_usage": self.local(backend, None, observed_at)}
        account: Json = {"type": "local", "label": "Ollama", "plan_type": None}
        if backend == "openai_chatgpt":
            status = await self.service.direct_accounts.status()
            selected = account_id or status.get("active_account_id")
            found = next((item for item in status.get("accounts", []) if item.get("id") == selected), None)
            if found is None:
                return {**self.unavailable(backend, "not_connected"), "account": {"id": selected}}
            account = {key: found.get(key) for key in ("id", "label", "connected", "plan_enabled")}
            account.update(type="chatgpt", plan_type=None)
            account_id = found["id"]
        return {"available": True, "source": backend, "scope": "kira_recorded", "observed_at": observed_at,
                "account": account, "limits": [], "reason": "not_exposed" if backend == "openai_chatgpt" else "not_applicable",
                "local_usage": self.local(backend, account_id, observed_at)}

    def claude_plan(self) -> Json:
        """The latest plan-limit notice Claude Code emitted during a Kira run.

        Claude Code derives it from its own API responses; Kira records it as an
        account-scoped usage event and reads it back here. No provider call, no
        credential access and no account identity are involved.
        """
        rows = self.service.store.all(
            "SELECT payload, timestamp FROM events WHERE type='usage' "
            "AND json_extract(payload,'$.source')='claude_code' "
            "AND json_extract(payload,'$.measurement')='plan_limits' "
            "ORDER BY timestamp DESC, seq DESC LIMIT 1")
        if not rows:
            return unavailable_claude_usage("no_observation")
        return normalize_claude_plan(rows[0]["payload"], rows[0]["timestamp"])

    @staticmethod
    def unavailable(backend: str, reason: str) -> Json:
        return {"available": False, "source": backend, "scope": "provider_account",
                "observed_at": datetime.now(UTC).isoformat(), "account": {}, "limits": [], "reason": reason}
