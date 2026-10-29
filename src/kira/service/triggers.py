"""Event-Driven Triggers & Background Automation (Cron, File Watcher, Git Commit, Webhook)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from kira.domain.contracts import Json, KiraError
from kira.domain.pricing import calculate_cost_telemetry, evaluate_budget
from kira.domain.triggers import (
    TRIGGER_KINDS,
    cron_matches,
    diff_workspace_mtimes,
    generate_webhook_secret,
    next_cron_fire,
    read_git_head,
    scan_workspace_mtimes,
    validate_cron_expression,
)
from kira.service.storage import new_id, now
from kira.service.teams import fields, integer, text

if TYPE_CHECKING:
    from kira.service.server import Service

METHODS = {
    "trigger.list",
    "trigger.create",
    "trigger.toggle",
    "trigger.delete",
    "trigger.fire",
}

_TERMINAL_SQL = "('completed','failed','cancelled','interrupted')"

TRIGGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_triggers (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('cron','file_watch','git_commit','webhook')),
    schedule TEXT NOT NULL,
    prompt TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    debounce_sec INTEGER NOT NULL DEFAULT 3 CHECK(debounce_sec >= 0),
    max_fires INTEGER NOT NULL DEFAULT 0 CHECK(max_fires >= 0),
    fire_count INTEGER NOT NULL DEFAULT 0 CHECK(fire_count >= 0),
    status TEXT NOT NULL DEFAULT 'armed' CHECK(status IN ('armed','paused','budget_paused','completed')),
    last_fired_at TEXT,
    last_run_id TEXT,
    last_event_summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS automation_triggers_project ON automation_triggers(project_id, session_id, enabled);
"""


class TriggerService:
    """Service-owned background trigger engine with anti-loop suppression, debounce, coalescing, and budget pause."""

    def __init__(self, service: "Service") -> None:
        self.service = service
        self.store = service.store
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._file_baselines: dict[str, dict[str, int]] = {}
        self._git_baselines: dict[str, str] = {}
        self._cron_last_minute: dict[str, str] = {}
        self._pending_events: dict[str, dict[str, Any]] = {}
        self._project_cooldown_until: dict[str, float] = {}
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.store.db.executescript(TRIGGER_SCHEMA)

    def start(self) -> None:
        self._ensure_schema()
        if self._task is None or self._task.done():
            self._closing = False
            self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        self._closing = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    def _public(self, row: Any) -> Json:
        d = dict(row)
        d["enabled"] = bool(d.get("enabled"))
        d["webhook_secret"] = str(d.get("schedule") or "") if d.get("kind") == "webhook" else ""
        d["last_detail"] = str(d.get("last_event_summary") or "")
        next_fire = None
        if d["enabled"] and d["kind"] == "cron":
            with suppress(ValueError):
                nf = next_cron_fire(d["schedule"])
                if nf is not None:
                    next_fire = nf.isoformat()
        d["next_fire_at"] = next_fire
        pending = self._pending_events.get(d["id"])
        d["coalesced_count"] = int(pending["count"]) if pending else 0
        return d

    def _one(self, trigger_id: str) -> Json:
        row = self.store.db.execute(
            "SELECT * FROM automation_triggers WHERE id=?", (trigger_id,)
        ).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Trigger non trovato")
        return self._public(row)

    def list_for_project(self, project_id: str, session_id: str | None = None) -> list[Json]:
        if not project_id:
            return []
        if session_id:
            rows = self.store.db.execute(
                "SELECT * FROM automation_triggers WHERE project_id=? AND session_id=? ORDER BY created_at DESC",
                (project_id, session_id),
            ).fetchall()
        else:
            rows = self.store.db.execute(
                "SELECT * FROM automation_triggers WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [self._public(r) for r in rows]

    def _prime_baseline(self, trigger: Json, project_root: Path) -> None:
        tid = trigger["id"]
        kind = trigger["kind"]
        if kind == "file_watch":
            self._file_baselines[tid] = scan_workspace_mtimes(project_root, trigger["schedule"])
        elif kind == "git_commit":
            self._git_baselines[tid] = read_git_head(project_root)

    def _project_busy(self, project_id: str) -> bool:
        if self.store.db.execute(
            f"SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id "
            f"WHERE s.project_id=? AND r.state NOT IN {_TERMINAL_SQL} LIMIT 1",
            (project_id,),
        ).fetchone():
            return True
        if self.store.db.execute(
            "SELECT 1 FROM team_runs WHERE project_id=? AND state NOT IN "
            "('completed','failed','cancelled','interrupted','paused') LIMIT 1",
            (project_id,),
        ).fetchone():
            return True
        if self.store.db.execute(
            "SELECT 1 FROM project_reservations WHERE project_id=? LIMIT 1",
            (project_id,),
        ).fetchone():
            return True
        return False

    def after_run(self, run_id: str) -> None:
        """Suppress self-triggered file/git loops when a Kira run finishes."""
        row = self.store.db.execute(
            "SELECT s.project_id, p.root FROM runs r JOIN sessions s ON s.id=r.session_id "
            "JOIN projects p ON p.id=s.project_id WHERE r.id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return
        project_id = str(row["project_id"])
        root = Path(str(row["root"]))
        # Apply post-run anti-loop cooldown and refresh file/git baselines
        self._project_cooldown_until[project_id] = time.monotonic() + 3.0
        for trig in self.list_for_project(project_id):
            if trig["kind"] in {"file_watch", "git_commit"}:
                self._prime_baseline(trig, root)
                self._pending_events.pop(trig["id"], None)

    def _session_budget_status(self, session: Json) -> Json:
        raw_settings = session.get("settings")
        settings: Json = raw_settings if isinstance(raw_settings, dict) else {}
        raw_nested = settings.get("budget")
        nested_budget: Json = raw_nested if isinstance(raw_nested, dict) else {}
        token_budget = int(settings.get("token_budget") or nested_budget.get("max_tokens") or 0)
        cost_budget_usd = float(settings.get("cost_budget_usd") or nested_budget.get("max_cost_usd") or 0.0)
        if token_budget <= 0 and cost_budget_usd <= 0.0:
            return {"active": False, "exceeded": False}
        # Sum usage events in this session
        rows = self.store.db.execute(
            "SELECT e.payload FROM events e JOIN runs r ON r.id=e.run_id "
            "WHERE r.session_id=? AND e.type='usage'",
            (session["id"],),
        ).fetchall()
        tot_in = 0
        tot_cache = 0
        tot_out = 0
        tot_all = 0
        for r in rows:
            with suppress(ValueError, TypeError):
                p = json.loads(r["payload"])
                u = p.get("usage") if isinstance(p.get("usage"), dict) else p
                in_tok = int(u.get("input_tokens") or 0)
                out_tok = int(u.get("output_tokens") or 0)
                tot_in += in_tok
                tot_cache += int(u.get("cached_input_tokens") or 0)
                tot_out += out_tok
                tot_all += int(u.get("total_tokens") or (in_tok + out_tok))
        cost = calculate_cost_telemetry(
            {
                "input_tokens": tot_in,
                "cached_input_tokens": tot_cache,
                "output_tokens": tot_out,
                "total_tokens": tot_all,
            },
            backend=str(session.get("backend") or "codex"),
            model=str(session.get("model") or ""),
        )
        return evaluate_budget(
            tot_all,
            cost["total_cost_usd"],
            token_budget=token_budget,
            cost_budget_usd=cost_budget_usd,
        )

    async def handle(self, method: str, params: Json) -> Json:
        if method == "trigger.list":
            fields(params, {"project_id", "session_id"})
            project_id = text(params.get("project_id"), "project_id", 128)
            session_id = text(params.get("session_id"), "session_id", 128) if params.get("session_id") else None
            return {"triggers": self.list_for_project(project_id, session_id)}

        if method == "trigger.create":
            fields(
                params,
                {"project_id", "session_id", "name", "kind", "schedule", "prompt", "debounce_sec", "max_fires", "enabled"},
            )
            project_id = text(params.get("project_id"), "project_id", 128)
            session_id = text(params.get("session_id"), "session_id", 128)
            project = self.store.one("projects", project_id)
            session = self.store.one("sessions", session_id)
            if session["project_id"] != project["id"]:
                raise KiraError("INVALID_ARGUMENT", "Session does not belong to this project")
            kind = text(params.get("kind"), "kind", 32)
            if kind not in TRIGGER_KINDS:
                raise KiraError("INVALID_ARGUMENT", f"Unsupported trigger kind: {kind}")

            raw_schedule = str(params.get("schedule") or "").strip()
            if kind == "cron":
                try:
                    schedule = validate_cron_expression(raw_schedule or "*/15 * * * *")
                except ValueError as exc:
                    raise KiraError("INVALID_ARGUMENT", f"Espressione cron non valida: {exc}") from exc
            elif kind == "file_watch":
                schedule = raw_schedule or "src/**/*.py"
                if len(schedule) > 256 or "\0" in schedule:
                    raise KiraError("INVALID_ARGUMENT", "Filtro glob non valido")
            elif kind == "git_commit":
                schedule = raw_schedule or "HEAD"
            else:
                schedule = raw_schedule or generate_webhook_secret()

            prompt = text(params.get("prompt"), "prompt", 16000)
            default_name = {
                "cron": f"Cron ({schedule})",
                "file_watch": f"File Watch ({schedule})",
                "git_commit": f"Git Commit ({schedule})",
                "webhook": "Webhook locale",
            }[kind]
            name = str(params.get("name") or default_name).strip()[:120] or default_name
            debounce_sec = integer(params.get("debounce_sec", 3), "debounce_sec", 0, 300)
            max_fires = integer(params.get("max_fires", 0), "max_fires", 0, 10000)
            enabled = 0 if params.get("enabled") is False else 1
            init_status = "armed" if enabled else "paused"

            tid = new_id()
            stamp = now()
            self.store.db.execute(
                "INSERT INTO automation_triggers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    project_id,
                    session_id,
                    name,
                    kind,
                    schedule,
                    prompt,
                    enabled,
                    debounce_sec,
                    max_fires,
                    0,
                    init_status,
                    None,
                    None,
                    "",
                    stamp,
                    stamp,
                ),
            )
            created = self._one(tid)
            self._prime_baseline(created, Path(project["root"]))
            self.wake()
            return {"trigger": created, "triggers": self.list_for_project(project_id, session_id)}

        if method == "trigger.toggle":
            fields(params, {"project_id", "trigger_id", "enabled"})
            tid = text(params.get("trigger_id"), "trigger_id", 128)
            enabled = bool(params.get("enabled"))
            trig = self._one(tid)
            project = self.store.one("projects", trig["project_id"])
            new_status = "armed" if enabled else "paused"
            self.store.db.execute(
                "UPDATE automation_triggers SET enabled=?, status=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, new_status, now(), tid),
            )
            updated = self._one(tid)
            if enabled:
                self._prime_baseline(updated, Path(project["root"]))
            else:
                self._pending_events.pop(tid, None)
            self.wake()
            return {"trigger": updated, "triggers": self.list_for_project(trig["project_id"], trig["session_id"])}

        if method == "trigger.delete":
            fields(params, {"project_id", "trigger_id"})
            tid = text(params.get("trigger_id"), "trigger_id", 128)
            trig = self._one(tid)
            self.store.db.execute("DELETE FROM automation_triggers WHERE id=?", (tid,))
            self._file_baselines.pop(tid, None)
            self._git_baselines.pop(tid, None)
            self._cron_last_minute.pop(tid, None)
            self._pending_events.pop(tid, None)
            return {"deleted": tid, "triggers": self.list_for_project(trig["project_id"], trig["session_id"])}

        if method == "trigger.fire":
            fields(params, {"project_id", "trigger_id", "event_detail", "payload", "secret"})
            tid = text(params.get("trigger_id"), "trigger_id", 128)
            trig = self._one(tid)
            if trig["kind"] == "webhook" and params.get("secret") is not None:
                if str(params.get("secret")) != trig["schedule"]:
                    raise KiraError("PERMISSION_DENIED", "Token webhook non valido")
            raw_detail = params.get("event_detail") or params.get("payload") or "Attivazione manuale / evento esterno"
            detail = str(raw_detail).strip()[:500]
            self.queue_event(tid, detail, immediate=True)
            await self.evaluate_tick()
            updated = self._one(tid)
            return {"trigger": updated, "triggers": self.list_for_project(trig["project_id"], trig["session_id"])}

        raise KiraError("METHOD_NOT_FOUND", f"Unknown trigger method: {method}")

    def queue_event(
        self,
        trigger_id: str,
        summary: str,
        *,
        files: list[str] | None = None,
        immediate: bool = False,
        now_mono: float | None = None,
    ) -> None:
        """Coalesce one or more trigger events into a single pending batch."""
        cur_mono = now_mono if now_mono is not None else time.monotonic()
        entry = self._pending_events.get(trigger_id)
        if entry is None:
            entry = {
                "first_mono": cur_mono,
                "last_mono": 0.0 if immediate else cur_mono,
                "count": 1,
                "files": set(files or []),
                "summary": summary,
                "immediate": immediate,
            }
            self._pending_events[trigger_id] = entry
        else:
            entry["count"] = int(entry["count"]) + 1
            if files:
                entry["files"].update(files)
            if immediate:
                entry["immediate"] = True
                entry["last_mono"] = 0.0
            else:
                entry["last_mono"] = cur_mono
            if entry["files"]:
                sorted_files = sorted(entry["files"])
                preview = ", ".join(sorted_files[:4])
                if len(sorted_files) > 4:
                    preview += f" (+{len(sorted_files) - 4} file)"
                entry["summary"] = f"File modificati ({len(sorted_files)}): {preview}"
            else:
                entry["summary"] = f"{summary} ({entry['count']} eventi accorpati)"
        self.wake()

    async def _loop(self) -> None:
        while not self._closing:
            self._wake.clear()
            if not self.service._closing and not self.service.stopping.is_set():
                with suppress(Exception):
                    await self.evaluate_tick()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 1.5)

    async def evaluate_tick(
        self,
        *,
        now_dt: datetime | None = None,
        now_mono: float | None = None,
    ) -> list[str]:
        """Evaluate all enabled triggers once and return list of launched run_ids."""
        cur_dt = now_dt or datetime.now(UTC)
        cur_mono = now_mono if now_mono is not None else time.monotonic()
        launched: list[str] = []

        rows = self.store.db.execute(
            "SELECT * FROM automation_triggers WHERE enabled=1 AND status='armed' ORDER BY created_at"
        ).fetchall()

        for raw in rows:
            trig = self._public(raw)
            tid = trig["id"]
            project_id = trig["project_id"]
            try:
                project = self.store.one("projects", project_id)
                session = self.store.one("sessions", trig["session_id"])
            except KiraError:
                continue

            root = Path(project["root"])
            busy = self._project_busy(project_id)
            in_cooldown = cur_mono < self._project_cooldown_until.get(project_id, 0.0)

            # 1. Anti-loop protection: while a Kira run is active on this project,
            # continuously keep file/git baselines updated so Kira's own edits never trigger a loop.
            if busy:
                if trig["kind"] in {"file_watch", "git_commit"}:
                    self._prime_baseline(trig, root)
                continue

            # 2. Detect new events from Cron, File Watcher, or Git Commit Watcher
            kind = trig["kind"]
            if kind == "cron":
                minute_key = cur_dt.strftime("%Y-%m-%dT%H:%M")
                if self._cron_last_minute.get(tid) != minute_key:
                    with suppress(ValueError):
                        if cron_matches(trig["schedule"], cur_dt):
                            self._cron_last_minute[tid] = minute_key
                            self.queue_event(
                                tid,
                                f"Pianificazione Cron ({trig['schedule']}) alle {cur_dt.strftime('%H:%M UTC')}",
                                immediate=True,
                                now_mono=cur_mono,
                            )
            elif kind == "file_watch":
                if not in_cooldown:
                    current_mtimes = scan_workspace_mtimes(root, trig["schedule"])
                    if tid not in self._file_baselines:
                        self._file_baselines[tid] = current_mtimes
                    else:
                        changed = diff_workspace_mtimes(self._file_baselines[tid], current_mtimes)
                        self._file_baselines[tid] = current_mtimes
                        if changed:
                            preview = ", ".join(changed[:4])
                            if len(changed) > 4:
                                preview += f" (+{len(changed) - 4} file)"
                            self.queue_event(
                                tid,
                                f"File modificati ({len(changed)}): {preview}",
                                files=changed,
                                immediate=False,
                                now_mono=cur_mono,
                            )
            elif kind == "git_commit":
                if not in_cooldown:
                    current_head = read_git_head(root)
                    if tid not in self._git_baselines:
                        self._git_baselines[tid] = current_head
                    elif current_head and current_head != self._git_baselines[tid]:
                        self._git_baselines[tid] = current_head
                        self.queue_event(
                            tid,
                            f"Nuovo commit Git rilevato ({current_head[:8]})",
                            immediate=False,
                            now_mono=cur_mono,
                        )

            # 3. Check if a pending (possibly coalesced) event has passed its debounce window
            pending = self._pending_events.get(tid)
            if not pending:
                continue
            debounce_wait = 0.0 if pending.get("immediate") else float(trig.get("debounce_sec") or 0)
            if not pending.get("immediate") and (cur_mono - float(pending["last_mono"])) < debounce_wait:
                continue
            if in_cooldown and not pending.get("immediate"):
                continue

            # 4. Check Token & Cost Budget before launching background execution
            budget = self._session_budget_status(session)
            if budget.get("exceeded"):
                self._pending_events.pop(tid, None)
                reason = "Pausa automatica: budget di token/costi della sessione raggiunto"
                self.store.db.execute(
                    "UPDATE automation_triggers SET enabled=0, status='budget_paused', "
                    "last_event_summary=?, updated_at=? WHERE id=?",
                    (reason, now(), tid),
                )
                continue

            # 5. Launch the triggered run!
            event_summary = str(pending["summary"])
            self._pending_events.pop(tid, None)
            fire_num = int(trig["fire_count"]) + 1
            request_id = f"trigger:{tid}:{fire_num}:{int(time.time() * 1000)}"
            prompt_text = (
                f"[Trigger automatico '{trig['name']}' ({event_summary})]\n"
                f"{trig['prompt']}"
            )
            try:
                run, initial = self.store.create_run(
                    session["id"],
                    request_id,
                    prompt_text,
                    reserve=True,
                )
            except KiraError as exc:
                if exc.code == "BUSY":
                    # Re-queue coalesced event for next idle tick
                    self.queue_event(tid, event_summary, immediate=False)
                    continue
                raise

            max_fires = int(trig.get("max_fires") or 0)
            reached_max = max_fires > 0 and fire_num >= max_fires
            new_status = "completed" if reached_max else "armed"
            new_enabled = 0 if reached_max else 1
            stamp = now()
            self.store.db.execute(
                "UPDATE automation_triggers SET fire_count=?, enabled=?, status=?, "
                "last_fired_at=?, last_run_id=?, last_event_summary=?, updated_at=? WHERE id=?",
                (fire_num, new_enabled, new_status, stamp, run["id"], event_summary, stamp, tid),
            )
            for event in initial:
                self.service._publish(run["id"], event)
            cancel = asyncio.Event()
            self.service.cancellations[run["id"]] = cancel
            self.service.tasks[run["id"]] = asyncio.create_task(
                self.service._run(run["id"], project, session, cancel)
            )
            launched.append(run["id"])

        return launched
