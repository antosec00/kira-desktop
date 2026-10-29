"""Durable, opt-in goal continuation without uncertain execution replay.

The service owns admission and cycle claims. A model checkpoint is an assessment,
never a provider terminal outcome or independent proof of achievement.
"""

import asyncio
from contextlib import suppress
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kira.domain.autonomous_goals import GOAL_INSTRUCTIONS, GOAL_TOOL_NAMES, goal_tool_specs
from kira.domain.contracts import Json, KiraError
from kira.domain.providers import WEB_BACKENDS
from kira.service.history_tools import mirrors_kira_tool
from kira.service.storage import encode, new_id, now
from kira.service.teams import fields, fingerprint, integer, text
from kira.service.tool_policy import kira_tools_enabled

if TYPE_CHECKING:
    from kira.service.server import Service

METHODS = {"goal.get", "goal.start", "goal.pause", "goal.resume"}
_TERMINAL_SQL = "('completed','failed','cancelled','interrupted')"
# Provider item identities, timings and process numbers differ on every call.
_VOLATILE_RECEIPT_FIELDS = frozenset({"id", "durationMs", "processId"})
_MAX_PROGRESS_RECEIPTS = 256


def _characters(value: Any, field: str, maximum: int, *, empty: bool = False) -> str:
    """Checkpoint text is bounded in characters, as its tool schema declares."""
    if (not isinstance(value, str) or "\0" in value or len(value) > maximum
            or (not empty and not value.strip())):
        raise KiraError("INVALID_ARGUMENT", f"{field} must contain at most {maximum} characters")
    return value


class AutonomousGoals:
    def __init__(self, service: "Service") -> None:
        self.service = service
        self.store = service.store
        self.admissions: set[str] = set()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    @staticmethod
    def _decode(row: Any) -> Json:
        result = dict(row)
        for key in ("binding", "evidence"):
            result[key] = json.loads(result[key])
        return result

    def _one(self, goal_id: str) -> Json:
        row = self.store.db.execute("SELECT * FROM autonomous_goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "No autonomous goal with this identity")
        return self._decode(row)

    def current(self, session_id: str) -> Json | None:
        row = self.store.db.execute("SELECT * FROM autonomous_goals WHERE session_id=? AND current=1",
                                    (session_id,)).fetchone()
        return self._decode(row) if row else None

    def metadata(self, run_id: str) -> Json:
        row = self.store.db.execute("SELECT goal_id,cycle_number FROM goal_cycles WHERE run_id=?",
                                    (run_id,)).fetchone()
        return {"source": "goal_continuation", **dict(row)} if row else {}

    def is_goal_run(self, run_id: str) -> bool:
        return bool(self.metadata(run_id))

    def _public(self, row: Json) -> Json:
        value = {key: row.get(key) for key in ("id", "project_id", "session_id", "objective", "status",
            "cycle_count", "max_cycles", "summary", "next_step", "error", "last_run_id", "waiting_reason",
            "evidence", "completion_basis", "created_at", "updated_at", "no_progress_count")}
        value.update(automatic=True, source="kira_service", objective_achievement="model_assessed"
                     if row["status"] == "completed" and row.get("completion_basis") else "not_established")
        cycles = self.store.db.execute("SELECT r.id,r.session_id,r.request_id,r.state,r.created_at,r.updated_at,r.last_seq,"
            "c.goal_id,c.cycle_number FROM goal_cycles c JOIN runs r ON r.id=c.run_id "
            "WHERE c.goal_id=? ORDER BY c.cycle_number DESC LIMIT 33", (row["id"],)).fetchall()
        value["runs"] = [{**dict(item), "source": "goal_continuation"} for item in reversed(cycles[:32])]
        value["runs_truncated"] = len(cycles) > 32
        value["run"] = None
        if row.get("last_run_id"):
            run = self.store.one("runs", row["last_run_id"])
            public_run = {key: run.get(key) for key in ("id", "session_id", "request_id", "state",
                "created_at", "updated_at", "last_seq", "error")}
            public_run.update(self.metadata(run["id"]))
            value["run"] = public_run
        return value

    def snapshot(self, session_id: str) -> Json:
        manual = self.store.db.execute("SELECT * FROM session_goals WHERE session_id=?", (session_id,)).fetchone()
        row = self.current(session_id)
        result: Json = {"goal": dict(manual) if manual else None, "autonomous": self._public(row) if row else None}
        if hasattr(self.service, "triggers"):
            session_row = self.store.db.execute(
                "SELECT project_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session_row:
                result["triggers"] = self.service.triggers.list_for_project(
                    str(session_row["project_id"]), session_id
                )
        return result

    def _scope(self, project_id: str, session_id: str) -> tuple[Json, Json]:
        return self.service.assistant._scope(project_id, session_id)

    def _binding(self, project: Json, session: Json) -> Json:
        if not kira_tools_enabled(session):
            raise KiraError("UNSUPPORTED", "Autonomous goals require the selected model's Kira tools")
        if session["backend"] not in {"codex", "openai_chatgpt", "ollama", "deepseek_account",
                                      "claude_code"}:
            raise KiraError("UNSUPPORTED", "This provider does not expose Kira goal tools")
        if self.store.db.execute("SELECT 1 FROM session_workbench WHERE session_id=? AND archived=1",
                                 (session["id"],)).fetchone():
            raise KiraError("ARCHIVED", "Restore the chat before starting an autonomous goal")
        return {**self.service.assistant._binding(session), "settings": session["settings"],
                "root": str(Path(project["root"]).resolve(strict=True)), "permission": project["permission"]}

    def _check_binding(self, row: Json) -> tuple[Json, Json]:
        project, session = self._scope(row["project_id"], row["session_id"])
        current = self._binding(project, session)
        # A cosmetic chat title is not execution identity. All other settings are
        # frozen so changes require an explicit resume using the new selection.
        policy = current["settings"].get("browser_action_approval", "ask")
        frozen_policy = row["binding"]["settings"].get("browser_action_approval", "ask")
        allow_live_browser_policy = (session["backend"] in WEB_BACKENDS
            and isinstance(policy, str) and policy in {"ask", "always"}
            and isinstance(frozen_policy, str) and frozen_policy in {"ask", "always"})
        def clean(settings: Json) -> Json:
            return {key: value for key, value in settings.items() if key != "chat_title"
                    and not (allow_live_browser_policy and key == "browser_action_approval")}
        actual = {**current, "settings": clean(current["settings"])}
        frozen = {**row["binding"], "settings": clean(row["binding"]["settings"])}
        if actual != frozen:
            raise KiraError("CONFLICT", "Goal provider, account, model, effort or execution scope changed; explicitly resume")
        settings = dict(row["binding"]["settings"])
        if allow_live_browser_policy:
            settings["browser_action_approval"] = current["settings"].get("browser_action_approval", "ask")
        return project, {**session, "settings": settings}

    def _ordinary_idle(self, project_id: str) -> None:
        if self.store.db.execute("SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id "
                f"WHERE s.project_id=? AND r.state NOT IN {_TERMINAL_SQL} "
                "AND NOT EXISTS (SELECT 1 FROM team_run_links l WHERE l.run_id=r.id) LIMIT 1",
                (project_id,)).fetchone():
            raise KiraError("BUSY", "Finish the current ordinary conversation before starting or resuming a goal")

    def _notify(self, row: Json) -> None:
        if row.get("last_run_id"):
            event = self.store.event(row["last_run_id"], "goal.state", self.service.redactor.clean(self._public(row)))
            self.service._publish(row["last_run_id"], event)

    def _state(self, row: Json, status: str, error: str | None = None, waiting_reason: str = "") -> Json:
        self.store.db.execute("UPDATE autonomous_goals SET status=?,error=?,waiting_reason=?,updated_at=? WHERE id=?",
            (status, self.service.redactor.text(error) if error else None, waiting_reason, now(), row["id"]))
        if row["current"]:
            self.store.db.execute("UPDATE session_goals SET status=?,updated_at=? WHERE session_id=?",
                (status if status in {"active", "completed"} else "paused", now(), row["session_id"]))
        updated = self._one(row["id"])
        self._notify(updated)
        return updated

    def pause_session(self, session_id: str, reason: str = "The user paused autonomous continuation") -> None:
        row = self.current(session_id)
        if row and row["status"] == "active":
            self._state(row, "paused", reason)
        self.wake()

    async def legacy_update(self, params: Json) -> Json:
        fields(params, {"project_id", "session_id", "objective", "status"})
        session_id = text(params.get("session_id"), "session_id", 128)
        project_id = text(params.get("project_id"), "project_id", 128)
        project, _ = self._scope(project_id, session_id)
        row = self.current(session_id)
        if row:
            status = params.get("status")
            if "objective" in params or not isinstance(status, str) or status not in {"paused", "completed"}:
                raise KiraError("CONFLICT", "Use goal.start to replace this autonomous objective or goal.resume to resume it")
            self.service.workbench._idle(project)
            self._state(row, "paused", "The user changed the legacy goal status; autonomous continuation is paused")
        result = await self.service.workbench.dispatch("goal.update", params)
        current = self.current(session_id)
        return {**result, "autonomous": self._public(current) if current else None}

    async def dispatch(self, method: str, params: Json) -> Json:
        allowed = {"project_id", "session_id"}
        if method != "goal.get":
            allowed.add("request_id")
        if method == "goal.start":
            allowed |= {"objective", "max_cycles"}
        if method not in METHODS:
            raise KiraError("METHOD_NOT_FOUND", "Unknown autonomous goal method")
        fields(params, allowed)
        project_id = text(params.get("project_id"), "project_id", 128)
        session_id = text(params.get("session_id"), "session_id", 128)
        project, session = self._scope(project_id, session_id)
        if method == "goal.get":
            return self.snapshot(session_id)
        request_id = text(params.get("request_id"), "request_id", 128)
        digest = fingerprint({"method": method, **params})
        receipt = self.store.db.execute("SELECT * FROM goal_commands WHERE request_id=?", (request_id,)).fetchone()
        if receipt:
            if receipt["fingerprint"] != digest:
                raise KiraError("CONFLICT", "Goal command identity was reused with different input")
            return {**self.snapshot(session_id), "receipt": json.loads(receipt["result"])}
        if self._closing or self.service._closing or self.service.stopping.is_set():
            raise KiraError("BUSY", "Goal scheduling is draining")
        row = self.current(session_id)
        binding: Json = {}
        objective = ""
        maximum = 0
        if method in {"goal.start", "goal.resume"}:
            self._ordinary_idle(project_id)
            binding = self._binding(project, session)
            if method == "goal.start":
                objective = self.service.redactor.text(text(params.get("objective"), "objective", 12000))
                maximum = integer(params.get("max_cycles", 0), "max_cycles", 0, 1000000)
            elif row is None:
                raise KiraError("NOT_FOUND", "This chat has no explicitly started autonomous goal")
            elif row["status"] == "completed":
                raise KiraError("CONFLICT", "This goal is completed; start a new goal explicitly")
            elif row["max_cycles"] and row["cycle_count"] >= row["max_cycles"]:
                raise KiraError("LIMIT_REACHED", "The explicit cycle budget is exhausted; start a new goal with a new budget")
        elif row is None:
            raise KiraError("NOT_FOUND", "This chat has no explicitly started autonomous goal")
        timestamp = now()
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            if method == "goal.start":
                if row:
                    self.store.db.execute("UPDATE autonomous_goals SET current=0,status=CASE WHEN status='active' "
                        "THEN 'paused' ELSE status END,updated_at=? WHERE id=?", (timestamp, row["id"]))
                goal_id = new_id()
                self.store.db.execute("INSERT INTO autonomous_goals "
                    "(id,project_id,session_id,objective,status,binding,max_cycles,created_at,updated_at) "
                    "VALUES (?,?,?,?,'active',?,?,?,?)",
                    (goal_id, project_id, session_id, objective, encode(binding), maximum, timestamp, timestamp))
                self.store.db.execute("INSERT INTO session_goals VALUES (?,?, 'active',?,?) "
                    "ON CONFLICT(session_id) DO UPDATE SET objective=excluded.objective,status='active',updated_at=excluded.updated_at",
                    (session_id, objective, timestamp, timestamp))
            else:
                assert row is not None
                goal_id = row["id"]
                if method == "goal.resume":
                    self.store.db.execute("UPDATE autonomous_goals SET status='active',binding=?,no_progress_count=0,"
                        "error=NULL,waiting_reason='',updated_at=? WHERE id=?", (encode(binding), timestamp, goal_id))
                    self.store.db.execute("UPDATE session_goals SET status='active',updated_at=? WHERE session_id=?",
                        (timestamp, session_id))
                elif row["status"] != "completed":
                    self.store.db.execute("UPDATE autonomous_goals SET status='paused',waiting_reason='',error=NULL,"
                                          "updated_at=? WHERE id=?", (timestamp, goal_id))
                    self.store.db.execute("UPDATE session_goals SET status='paused',updated_at=? WHERE session_id=?",
                        (timestamp, session_id))
            result = {"request_id": request_id, "method": method, "goal_id": goal_id, "accepted": True}
            self.store.db.execute("INSERT INTO goal_commands VALUES (?,?,?,?,?)",
                (request_id, digest, goal_id, encode(result), timestamp))
            self.store.db.execute("COMMIT")
        except BaseException:
            self.store.db.execute("ROLLBACK")
            raise
        self._notify(self._one(goal_id))
        self.wake()
        return {**self.snapshot(session_id), "receipt": result}

    def recover(self) -> None:
        # A durable active row is authorization, not evidence that the prior
        # service completed every effect. Restart therefore never dispatches it.
        self.store.db.execute("UPDATE goal_cycles SET state='interrupted',updated_at=? WHERE state='running'", (now(),))
        for item in self.store.db.execute("SELECT id FROM autonomous_goals WHERE status='active'").fetchall():
            row = self._one(item[0])
            self._state(row, "interrupted", "Service restarted; inspect recorded work and explicitly resume. No cycle was replayed.")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            self.wake()

    def wake(self) -> None:
        self._wake.set()

    async def close(self) -> None:
        self._closing = True
        self.wake()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _waiting(self, row: Json) -> str:
        project_id = row["project_id"]
        if self.service._settings_changing or row["session_id"] in self.service.web.configuring:
            return "browser_configuration"
        if self.store.db.execute("SELECT 1 FROM prompt_queue WHERE project_id=? AND status IN ('pending','starting') LIMIT 1",
                                 (project_id,)).fetchone():
            return "user_queue"
        if self.store.db.execute("SELECT 1 FROM assistant_followups WHERE project_id=? "
                "AND state IN ('armed','pending','running') LIMIT 1", (project_id,)).fetchone():
            return "team_followup"
        if self.store.db.execute("SELECT 1 FROM team_runs WHERE project_id=? "
                "AND state NOT IN ('completed','failed','cancelled','interrupted','paused') LIMIT 1", (project_id,)).fetchone():
            return "team_active"
        if self.store.db.execute("SELECT 1 FROM assistant_delegations WHERE project_id=? AND state='pending' LIMIT 1",
                                 (project_id,)).fetchone():
            return "team_active"
        active = self.store.db.execute("SELECT r.id FROM runs r JOIN sessions s ON s.id=r.session_id "
            f"WHERE s.project_id=? AND r.state NOT IN {_TERMINAL_SQL} LIMIT 1", (project_id,)).fetchone()
        if active:
            return "cycle_running" if active[0] == row.get("last_run_id") else "conversation_active"
        if self.store.db.execute("SELECT 1 FROM project_reservations WHERE project_id=? LIMIT 1",
                                 (project_id,)).fetchone():
            return "project_busy"
        if self.store.db.execute("SELECT 1 FROM browser_sessions WHERE project_id=? AND manual=1 "
                "AND state NOT IN ('closed','stopped') LIMIT 1", (project_id,)).fetchone():
            return "manual_browser"
        return ""

    def _set_wait(self, row: Json, reason: str) -> None:
        if row["waiting_reason"] != reason:
            self.store.db.execute("UPDATE autonomous_goals SET waiting_reason=? WHERE id=?", (reason, row["id"]))
            self._notify(self._one(row["id"]))

    async def _loop(self) -> None:
        while not self._closing:
            self._wake.clear()
            if not self.service._closing and not self.service.stopping.is_set():
                for item in self.store.db.execute("SELECT id FROM autonomous_goals WHERE current=1 AND status='active' "
                        "ORDER BY updated_at,id").fetchall():
                    try:
                        await self._schedule(self._one(item[0]))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        row = self._one(item[0])
                        if row["status"] == "active":
                            self._state(row, "blocked", "Goal admission stopped: " + self.service.redactor.text(str(exc)))
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 1.0)

    async def _schedule(self, row: Json) -> None:
        if row["status"] != "active" or not row["current"]:
            return
        reason = self._waiting(row)
        if reason:
            self._set_wait(row, reason)
            return
        if row["max_cycles"] and row["cycle_count"] >= row["max_cycles"]:
            self._state(row, "paused", "The explicit cycle budget is exhausted")
            return
        project, session = self._check_binding(row)
        self.admissions.add(session["id"])
        try:
            if session["backend"] == "openai_chatgpt":
                await self.service.direct_accounts.prepare(session["settings"])
            # Account preparation yields. Every admission condition must still hold.
            row = self._one(row["id"])
            if self._closing or self.service._closing or self.service.stopping.is_set() or row["status"] != "active" or not row["current"]:
                return
            project, session = self._check_binding(row)
            reason = self._waiting(row)
            if reason:
                self._set_wait(row, reason)
                return
            cycle = row["cycle_count"] + 1
            request_id = f"goal:{row['id']}:{cycle}"
            prompt = ("Kira service continuation of the explicitly authorized autonomous goal; this is not a new user message. "
                "Use the supplied original objective and checkpoint, respect later user corrections, inspect recorded outcomes "
                "before acting, and complete the next useful step. Save progress with goal.checkpoint before ending. "
                "Do not repeat uncertain effects or silently replace the objective.")

            def claim(run_id: str) -> None:
                current = self._one(row["id"])
                if (current["status"] != "active" or not current["current"]
                        or current["cycle_count"] + 1 != cycle):
                    raise KiraError("CONFLICT", "The autonomous goal claim changed")
                self._check_binding(current)
                self.store.db.execute("INSERT INTO run_modes VALUES (?,'execute')", (run_id,))
                self.service.souls.bind_run(run_id, project, session)
                stamp = now()
                self.store.db.execute("INSERT INTO goal_cycles (goal_id,cycle_number,run_id,state,checkpoint,created_at,updated_at) "
                    "VALUES (?,?,?,'running',NULL,?,?)",
                    (row["id"], cycle, run_id, stamp, stamp))
                self.store.db.execute("UPDATE autonomous_goals SET cycle_count=?,last_run_id=?,waiting_reason='cycle_running',"
                    "error=NULL,updated_at=? WHERE id=?", (cycle, run_id, stamp, row["id"]))
            try:
                run, initial = self.store.create_run(session["id"], request_id, prompt, reserve=True,
                    source="goal_continuation", source_metadata={"goal_id": row["id"], "cycle_number": cycle}, on_create=claim)
            except KiraError as exc:
                if exc.code == "BUSY":
                    self._set_wait(row, "project_busy")
                    return
                raise
            for event in initial:
                self.service._publish(run["id"], event)
            cancel = asyncio.Event()
            self.service.cancellations[run["id"]] = cancel
            self.service.tasks[run["id"]] = asyncio.create_task(self.service._run(run["id"], project, session, cancel))
            self._notify(self._one(row["id"]))
        finally:
            self.admissions.discard(session["id"])

    def context(self, session_id: str) -> str:
        row = self.current(session_id)
        if row is None:
            return ""
        payload = {key: row.get(key) for key in ("id", "objective", "status", "cycle_count", "max_cycles",
                    "summary", "next_step", "evidence", "completion_basis", "error")}
        corrections = self.store.db.execute("SELECT e.run_id,e.seq,substr(json_extract(e.payload,'$.content'),1,2000) AS content,"
            "length(json_extract(e.payload,'$.content'))>2000 AS content_truncated "
            "FROM events e JOIN runs r ON r.id=e.run_id WHERE r.session_id=? AND e.type='message.user' "
            "AND e.timestamp>=? ORDER BY r.rowid DESC,e.seq DESC LIMIT 7", (session_id, row["created_at"])).fetchall()
        payload["later_user_messages"] = [{**dict(item), "content_truncated": bool(item["content_truncated"])}
                                          for item in reversed(corrections[:6])]
        payload["later_user_messages_omitted"] = len(corrections) > 6
        return ("\nKira autonomous goal anchor, explicitly enabled by the user. The objective is retained verbatim; "
            "the checkpoint is bounded model-authored progress, not independently verified fact. A paused, blocked, "
            "completed or interrupted goal does not authorize automatic work. Honor later user corrections. "
            "Use available history/help retrieval for original details; the summary is not the whole conversation. "
            "When a later_user_messages excerpt is content_truncated, retrieve its original run/seq before "
            "interpreting the correction; omitted text can contain essential constraints.\n" + encode(payload))

    def _evidence_scope(self, session_id: str, run_id: str) -> bool:
        return bool(self.store.db.execute("SELECT 1 FROM runs r WHERE r.id=? AND (r.session_id=? OR EXISTS "
            "(SELECT 1 FROM team_run_links l JOIN assistant_delegations d ON d.team_id=l.team_id "
            "WHERE l.run_id=r.id AND d.session_id=?))", (run_id, session_id, session_id)).fetchone())

    def _evidence(self, session_id: str, value: Any) -> list[Json]:
        if not isinstance(value, list) or len(value) > 24:
            raise KiraError("INVALID_ARGUMENT", "evidence must contain at most 24 run/seq references")
        result: list[Json] = []
        seen: set[tuple[str, int]] = set()
        for reference in value:
            fields(reference, {"run_id", "seq"})
            run_id = text(reference.get("run_id"), "run_id", 128)
            seq = integer(reference.get("seq"), "seq", 1, 2**63 - 1)
            if not self._evidence_scope(session_id, run_id):
                raise KiraError("INVALID_ARGUMENT", "Evidence does not belong to this goal's chat or delegated Team")
            row = self.store.db.execute("SELECT type,payload FROM events WHERE run_id=? AND seq=?", (run_id, seq)).fetchone()
            if row is None:
                raise KiraError("INVALID_ARGUMENT", "Evidence event does not exist")
            payload = json.loads(row["payload"])
            good_tool = (row["type"] == "tool.completed" and isinstance(payload.get("result"), dict)
                         and payload["result"].get("ok") is True and not str(payload.get("name", "")).startswith("goal.")
                         and not mirrors_kira_tool(payload.get("name"), payload["result"]))
            good_artifact = row["type"] == "artifact.created" and bool(payload.get("sha256"))
            good_text = row["type"] == "message.completed" and bool(str(payload.get("content") or "").strip())
            if not (good_tool or good_artifact or good_text):
                raise KiraError("INVALID_ARGUMENT", "Evidence must reference a confirmed tool result, artifact or explicit deliverable")
            if (run_id, seq) not in seen:
                seen.add((run_id, seq))
                result.append({"run_id": run_id, "seq": seq, "type": row["type"],
                               **({"tool_name": payload.get("name", "")} if good_tool else {})})
        return result

    def recent_evidence(self, session_id: str) -> list[Json]:
        rows = self.store.db.execute("SELECT e.run_id,e.seq FROM events e JOIN runs r ON r.id=e.run_id "
            "WHERE (r.session_id=? OR EXISTS (SELECT 1 FROM team_run_links l JOIN assistant_delegations d "
            "ON d.team_id=l.team_id WHERE l.run_id=r.id AND d.session_id=?)) "
            "AND e.type IN ('tool.completed','artifact.created','message.completed') "
            "ORDER BY r.rowid DESC,e.seq DESC LIMIT 100", (session_id, session_id)).fetchall()
        result: list[Json] = []
        for row in rows:
            try:
                result.extend(self._evidence(session_id, [dict(row)]))
            except KiraError:
                continue
            if len(result) == 24:
                break
        return result

    def _checkpoint(self, goal: Json, run_id: str, args: Json) -> Json:
        fields(args, {"status", "summary", "next_step", "evidence", "deliverable"})
        status = text(args.get("status"), "status", 16)
        if status not in {"continue", "completed", "blocked"}:
            raise KiraError("INVALID_ARGUMENT", "Checkpoint status must be continue, completed or blocked")
        summary = self.service.redactor.text(_characters(args.get("summary"), "summary", 6000))
        next_step = self.service.redactor.text(_characters(args.get("next_step"), "next_step", 3000,
                                                           empty=status == "completed"))
        evidence = self._evidence(goal["session_id"], args.get("evidence"))
        deliverable = self.service.redactor.text(_characters(args.get("deliverable", ""), "deliverable", 12000,
                                                             empty=True))
        if status == "completed" and not evidence and not deliverable.strip():
            raise KiraError("INVALID_ARGUMENT", "Completion needs recorded evidence or an explicit text deliverable")
        if status == "completed" and self.store.db.execute("SELECT 1 FROM assistant_delegations WHERE session_id=? "
                "AND state='pending' LIMIT 1", (goal["session_id"],)).fetchone():
            raise KiraError("BUSY", "A queued Team must finish and its result be inspected before goal completion")
        checkpoint = {"status": status, "summary": summary, "next_step": next_step, "evidence": evidence,
                      "deliverable": deliverable, "assessment": "model_assessed", "created_at": now(),
                      "event_seq": self.store.one("runs", run_id)["last_seq"]}
        self.store.db.execute("UPDATE goal_cycles SET checkpoint=?,updated_at=? WHERE run_id=? AND state='running'",
                              (encode(checkpoint), now(), run_id))
        return {"ok": True, "checkpoint": checkpoint, "committed_completion": False,
                "condition": "provider_completed_and_cleanup_confirmed"}

    def attach(self, session: Json, project: Json, run_id: str, cancel: asyncio.Event,
               mode: str, options: Json) -> Json:
        goal = self.current(session["id"])
        if goal is None or not kira_tools_enabled(session):
            return options
        metadata = self.metadata(run_id)
        writable = bool(metadata.get("goal_id") == goal["id"] and mode == "execute")
        namespaces = [{**namespace, "tools": list(namespace["tools"])} for namespace in options.get("client_tools", [])]
        namespaces.append(goal_tool_specs(writable=writable))
        previous = options.get("client_tool_call")

        async def call(call_id: str, name: str, arguments: Json) -> Json:
            if name not in GOAL_TOOL_NAMES:
                if previous is None:
                    raise KiraError("UNSUPPORTED", "This Kira callback is not registered")
                return dict(await previous(call_id, name, arguments))
            run = self.store.one("runs", run_id)
            if (cancel.is_set() or self.service._closing or run_id not in self.service.tasks
                    or run["session_id"] != session["id"] or run["state"] not in {"running", "awaiting_input"}):
                return {"ok": False, "error": {"code": "CANCELLED", "message": "This goal callback is no longer active"}}
            digest = fingerprint({"name": name, "arguments": arguments})
            old = self.store.db.execute("SELECT fingerprint,result FROM goal_tool_calls WHERE run_id=? AND call_id=?",
                                       (run_id, call_id)).fetchone()
            if old:
                if old["fingerprint"] != digest:
                    return {"ok": False, "error": {"code": "CONFLICT", "message": "Goal tool identity reused with different input"}}
                return json.loads(old["result"])
            await self.service.emit(run_id, "tool.started", {"tool_id": call_id, "name": name, "arguments": arguments})
            try:
                if cancel.is_set() or self.service._closing:
                    raise KiraError("CANCELLED", "The goal callback was cancelled before mutation")
                current = self.current(session["id"])
                if current is None or current["id"] != goal["id"]:
                    raise KiraError("CONFLICT", "This goal was replaced")
                if name == "goal.status":
                    fields(arguments, set())
                    result = {"ok": True, "goal": self._public(current), "recent_evidence": self.recent_evidence(session["id"])}
                else:
                    if not writable or current["status"] not in {"active", "paused"}:
                        raise KiraError("PERMISSION_DENIED", "Only the current goal-owned execution can save its checkpoint")
                    self._check_binding(current)
                    result = self._checkpoint(current, run_id, arguments)
            except (KiraError, ValueError, OSError) as exc:
                result = {"ok": False, "error": {"code": exc.code if isinstance(exc, KiraError) else "INVALID_ARGUMENT",
                                                 "message": str(exc)}}
            result = self.service.redactor.clean(result)
            self.store.db.execute("INSERT INTO goal_tool_calls VALUES (?,?,?,?)", (run_id, call_id, digest, encode(result)))
            await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name, "result": result})
            return result

        guidance = (GOAL_INSTRUCTIONS if writable and goal["status"] == "active" else
            "The saved Kira autonomous goal is available through goal.status for reference. This ordinary "
            "turn responds to the current user request, not an instruction to continue or finish the goal. "
            "Goal state is service-owned; this turn cannot checkpoint, start, resume or complete it. "
            "A paused or stopped goal remains paused unless the user explicitly resumes it.")
        return {**options, "client_tools": namespaces, "client_tool_call": call,
                "assistant_instructions": str(options.get("assistant_instructions", "")) + "\n" + guidance}

    def _dependencies_pending(self, row: Json) -> bool:
        return (self.store.db.execute("SELECT 1 FROM assistant_delegations WHERE session_id=? "
                    "AND state='pending' LIMIT 1", (row["session_id"],)).fetchone() is not None
            or self.store.db.execute("SELECT 1 FROM assistant_followups WHERE session_id=? "
                    "AND state IN ('armed','pending','running') LIMIT 1", (row["session_id"],)).fetchone() is not None
            or self.store.db.execute("SELECT 1 FROM team_runs WHERE project_id=? AND state NOT IN "
                    "('completed','failed','cancelled','interrupted','paused') LIMIT 1", (row["project_id"],)).fetchone() is not None)

    def _guidance_error(self, run_id: str) -> str | None:
        failed = self.store.db.execute("SELECT request_id,status,result FROM steering_requests WHERE run_id=? "
            "AND status IN ('pending','rejected','unknown') ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        if failed is None:
            return None
        result = json.loads(failed["result"])
        detail = str(result.get("error") or "Provider delivery was not confirmed")[:1500]
        return ("A newer user instruction was not confirmed delivered (" + failed["status"] +
                "); inspect the guidance receipt before explicitly resuming. " + detail)

    def _uncertain_effects(self, run_id: str) -> bool:
        unconfirmed = self.store.db.execute("SELECT 1 FROM events s WHERE s.run_id=? AND s.type='tool.started' "
            "AND NOT EXISTS (SELECT 1 FROM events c WHERE c.run_id=s.run_id AND c.type='tool.completed' "
            "AND json_extract(c.payload,'$.tool_id')=json_extract(s.payload,'$.tool_id')) LIMIT 1", (run_id,)).fetchone()
        if unconfirmed:
            return True
        return self.store.db.execute("SELECT 1 FROM events WHERE run_id=? AND type='tool.completed' AND "
            "(json_extract(payload,'$.result.action_outcome')='unknown' OR "
            "json_extract(payload,'$.result.retry_safe')=0 OR json_extract(payload,'$.result.outcome')='unknown') LIMIT 1",
            (run_id,)).fetchone() is not None

    def _evidence_signature(self, run_id: str, seq: int) -> str:
        event = self.store.db.execute("SELECT type,payload FROM events WHERE run_id=? AND seq=?", (run_id, seq)).fetchone()
        payload = json.loads(event["payload"])
        if event["type"] == "tool.completed":
            started = self.store.db.execute("SELECT payload FROM events WHERE run_id=? AND type='tool.started' "
                "AND json_extract(payload,'$.tool_id')=? ORDER BY seq LIMIT 1", (run_id, payload.get("tool_id"))).fetchone()
            arguments = json.loads(started[0]).get("arguments") if started else None
            result = payload.get("result")
            if isinstance(result, dict) and result.get("observed_only") is True:
                result = {key: value for key, value in result.items() if key not in _VOLATILE_RECEIPT_FIELDS}
            value = {"type": event["type"], "name": payload.get("name"), "arguments": arguments, "result": result}
        elif event["type"] == "artifact.created":
            value = {"type": event["type"], **{key: payload.get(key) for key in ("path", "sha256", "kind")}}
        else:
            value = {"type": event["type"], "content": payload.get("content")}
        return fingerprint(value)

    def _progress(self, cycle: Any, checkpoint: Json | None) -> tuple[bool, list[str]]:
        signatures: set[str] = set()
        receipts = 0
        for event in self.store.db.execute("SELECT seq,payload FROM events WHERE run_id=? AND type='tool.completed' "
                "AND json_extract(payload,'$.result.ok')=1 AND json_extract(payload,'$.name') NOT LIKE 'goal.%' "
                "ORDER BY seq", (cycle["run_id"],)):
            payload = json.loads(event["payload"])
            if mirrors_kira_tool(payload.get("name"), payload.get("result")):
                continue
            signatures.add(self._evidence_signature(cycle["run_id"], event["seq"]))
            receipts += 1
            if receipts == _MAX_PROGRESS_RECEIPTS:
                break
        if checkpoint:
            if checkpoint["deliverable"].strip():
                signatures.add(fingerprint({"deliverable": checkpoint["deliverable"]}))
            for item in checkpoint["evidence"]:
                # New receipt IDs for an identical read are not new progress.
                # Compare recorded meaning, never run IDs or event timestamps.
                signatures.add(self._evidence_signature(item["run_id"], item["seq"]))
        previous: set[str] = set()
        for old in self.store.db.execute("SELECT progress_signature FROM goal_cycles WHERE goal_id=? "
                "AND cycle_number<? ORDER BY cycle_number DESC LIMIT 12", (cycle["goal_id"], cycle["cycle_number"])):
            previous.update(json.loads(old[0]))
        return bool(signatures - previous), sorted(signatures)

    async def team_finished(self, team_id: str, cleaned: bool) -> None:
        team = self.service.teams.data.one(team_id)
        if cleaned and team["state"] not in {"cancelled", "interrupted"}:
            self.wake()
            return
        # Only the Team's actual originating goal/chat can be affected. A stop
        # in another conversation must not pause unrelated goals in the project.
        origins = self.store.db.execute("SELECT session_id FROM assistant_followups WHERE team_id=? "
            "UNION SELECT session_id FROM assistant_delegations WHERE team_id=?", (team_id, team_id)).fetchall()
        for origin in origins:
            row = self.current(origin["session_id"])
            if row and row["status"] == "active":
                status = "paused" if cleaned and team["state"] == "cancelled" else "interrupted"
                self._state(row, status, "The delegated Team was stopped by the user; autonomous continuation is paused"
                    if status == "paused" else "The delegated Team ended with uncertain execution or cleanup; inspect it before resuming")
        self.wake()

    async def after_run(self, run_id: str, cleaned: bool) -> None:
        cycle = self.store.db.execute("SELECT * FROM goal_cycles WHERE run_id=?", (run_id,)).fetchone()
        if cycle is None:
            # A failed result followup or newer ordinary instruction must not be
            # silently followed by autonomous actions that assume its success.
            run = self.store.one("runs", run_id)
            row = self.current(run["session_id"])
            guidance_error = self._guidance_error(run_id)
            if row and row["status"] == "active" and (not cleaned or run["state"] != "completed" or guidance_error):
                self._state(row, "interrupted", guidance_error or
                    "A chat turn did not complete cleanly; inspect it before resuming the goal")
            self.wake()
            return
        if cycle["state"] != "running":
            return
        row = self._one(cycle["goal_id"])
        run = self.store.one("runs", run_id)
        state = run["state"] if cleaned and run["state"] in {"completed", "failed", "cancelled"} else "interrupted"
        self.store.db.execute("UPDATE goal_cycles SET state=?,updated_at=? WHERE run_id=?", (state, now(), run_id))
        if state != "completed":
            if row["status"] == "active":
                self._state(row, "interrupted" if state in {"cancelled", "interrupted"} else "blocked",
                    str(run.get("error") or "Goal cycle did not finish with confirmed cleanup; no automatic replay"))
            self.wake()
            return
        handoff = self.store.db.execute("SELECT state,error FROM assistant_delegations WHERE origin_run_id=? "
            "AND state IN ('failed','interrupted','cancelled') LIMIT 1", (run_id,)).fetchone()
        if handoff:
            if row["status"] == "active":
                status = "paused" if handoff["state"] == "cancelled" else "interrupted" if handoff["state"] == "interrupted" else "blocked"
                self._state(row, status, "The managed Team handoff did not complete: " + str(handoff["error"] or handoff["state"]))
            self.wake()
            return
        guidance_error = self._guidance_error(run_id)
        if guidance_error:
            if row["status"] == "active":
                self._state(row, "interrupted", guidance_error)
            self.wake()
            return
        if self._uncertain_effects(run_id):
            if row["status"] == "active":
                self._state(row, "interrupted", "A tool outcome is unconfirmed; inspect recorded effects before explicitly resuming")
            self.wake()
            return
        checkpoint = json.loads(cycle["checkpoint"]) if cycle["checkpoint"] else None
        useful, signatures = self._progress(cycle, checkpoint)
        self.store.db.execute("UPDATE goal_cycles SET progress_signature=? WHERE run_id=?", (encode(signatures), run_id))
        if checkpoint:
            self.store.db.execute("UPDATE autonomous_goals SET summary=?,next_step=?,evidence=?,updated_at=? WHERE id=?",
                (checkpoint["summary"], checkpoint["next_step"], encode(checkpoint["evidence"]), now(), row["id"]))
        no_progress = 0 if useful else row["no_progress_count"] + 1
        self.store.db.execute("UPDATE autonomous_goals SET no_progress_count=?,waiting_reason='',updated_at=? WHERE id=?",
                              (no_progress, now(), row["id"]))
        row = self._one(row["id"])
        if row["status"] == "active":
            newer_user_input = bool(checkpoint and self.store.db.execute("SELECT 1 FROM events WHERE run_id=? "
                "AND seq>? AND type='message.user' LIMIT 1", (run_id, checkpoint.get("event_seq", 0))).fetchone())
            dependencies = self._dependencies_pending(row)
            if checkpoint and checkpoint["status"] == "completed" and not dependencies and not newer_user_input:
                has_execution = any(item["type"] in {"tool.completed", "artifact.created"} for item in checkpoint["evidence"])
                basis = "model_assessed_with_execution_evidence" if has_execution else "model_assessed_deliverable"
                self.store.db.execute("UPDATE autonomous_goals SET completion_basis=? WHERE id=?", (basis, row["id"]))
                self.store.db.execute("UPDATE session_goals SET status='completed',updated_at=? WHERE session_id=?",
                                      (now(), row["session_id"]))
                self._state(row, "completed")
            elif checkpoint and checkpoint["status"] == "blocked":
                self._state(row, "blocked", checkpoint["next_step"])
            elif no_progress >= 3:
                self._state(row, "blocked", "No recorded progress in three consecutive goal cycles")
            elif row["max_cycles"] and row["cycle_count"] >= row["max_cycles"]:
                self._state(row, "paused", "The explicit cycle budget is exhausted")
            else:
                self._notify(row)
        else:
            self._notify(row)
        self.wake()
