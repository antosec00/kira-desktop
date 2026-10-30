"""Service-owned team scheduling, durable mailbox and explicit continuations."""

import asyncio
from collections import defaultdict
from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from kira.domain.contracts import Json, KiraError
from kira.domain.providers import TEAM_BACKENDS
from kira.domain.teams import (
    COORDINATOR_INSTRUCTIONS, DEFAULT_CONCURRENCY, DEFAULT_MAX_AGENTS, DEFAULT_ROUND_LIMIT,
    MAX_COMPLETION_ANCESTORS, MAX_MAILBOX_PAGE,
    MAX_MEMBER_INSTRUCTIONS_BYTES, MAX_MEMBER_NAME_BYTES, MAX_MEMBER_ROLE_BYTES,
    MAX_PLAN_TASKS, MAX_TEAM_CONCURRENCY, MAX_TEAM_GOAL_BYTES, MAX_TEAM_MEMBERS,
    MAX_TEAM_MESSAGE_BYTES, MEMBER_ID_PATTERN, TEAM_TERMINAL_STATES,
    coordinator_output_schema, mailbox_tool_specs,
)
from kira.service.storage import encode, new_id, now
from kira.service.team_store import TeamStore, decoded

if TYPE_CHECKING:
    from kira.service.server import Client, Service

METHODS = {"team.start", "team.list", "team.snapshot", "team.telemetry", "team.events", "team.subscribe",
           "team.send_message", "team.cancel", "team.resume", "team.approve"}
RESERVED_IDS = {"all", "user", "human", "system"}


def fields(value: Json, allowed: set[str]) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        raise KiraError("INVALID_ARGUMENT", "Unknown team parameter")


def text(value: Any, field: str, maximum: int, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or "\0" in value or len(value.encode()) > maximum
            or (not empty and not value.strip())):
        raise KiraError("INVALID_ARGUMENT", f"{field} must contain at most {maximum} UTF-8 bytes")
    return value


def integer(value: Any, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise KiraError("INVALID_ARGUMENT", f"{field} must be an integer between {low} and {high}")
    return value


def fingerprint(value: Json) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def clip(value: str, maximum: int) -> str:
    return value.encode()[:maximum].decode("utf-8", "ignore")


def identifier(value: Any, field: str = "id") -> str:
    result = text(value, field, 48)
    if not re.fullmatch(MEMBER_ID_PATTERN, result):
        raise KiraError("INVALID_ARGUMENT", f"Invalid {field}")
    return result


class Teams:
    def __init__(self, service: "Service") -> None:
        self.service = service
        self.store = service.store
        self.data = TeamStore(self.store, self.publish)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.stops: dict[str, asyncio.Event] = {}
        self.children: dict[str, set[str]] = defaultdict(set)
        self.subscribers: dict[str, set[Client]] = defaultdict(set)
        self.closing = False

    def publish(self, event: Json) -> None:
        if hasattr(self.service, "assistant"):
            self.service.assistant.on_team_event(event)
        for client in tuple(self.subscribers.get(event["team_id"], ())):
            client.send({"version": 1, "team_event": event})

    def snapshot(self, team_id: str) -> Json:
        approvals = []
        for (run_id, approval_id), future in self.service.approvals.items():
            link = self.data.link(run_id)
            if not future.done() and link and link["team_id"] == team_id:
                rows = self.store.all("SELECT payload FROM events WHERE run_id=? AND "
                    "type='approval.requested' AND json_extract(payload,'$.approval_id')=? ORDER BY seq DESC LIMIT 1",
                    (run_id, approval_id))
                request: Json = next((r["payload"] for r in rows
                                if r["payload"].get("approval_id") == approval_id), {})
                raw = encode(request)
                if len(raw.encode()) > 20_000:
                    request = {"approval_id": approval_id, "message": raw[:4000],
                               "truncated": True, "full_request": "run.events"}
                approvals.append({"run_id": run_id, "approval_id": approval_id,
                                  "member_id": link["member_id"], "request": request})
        snapshot = self.data.snapshot(team_id, approvals)
        snapshot["scheduling"] = self._scheduling(team_id, "coordinator")
        snapshot["completion_evidence"] = self.completion_evidence(team_id)
        telemetry = self.data.telemetry(team_id)
        if len(encode(snapshot).encode()) + len(encode(telemetry).encode()) <= 620_000:
            snapshot["telemetry"] = telemetry
        while len(encode(snapshot).encode()) > 650_000:
            field = "messages" if snapshot["messages"] else "steps"
            if not snapshot[field]:
                raise KiraError("OUTPUT_LIMIT", "Team metadata exceeds snapshot limit; use team.events")
            snapshot[field].pop(0)
            snapshot["omitted"][field] += 1
        return snapshot

    def completion_evidence(self, team_id: str) -> Json:
        """Bounded service metadata for team and origin-chat cards; no full snapshot."""
        team = self.data.one(team_id)
        basis = self._completion_basis(team)
        return {"worker_assignments_completed": self._completed_workers(team_id), "basis": basis,
                "coordinator_only_conclusion": team["state"] == "completed" and basis is None}

    def _roster(self, value: Any, cap: int, permission: str) -> list[Json]:
        if not isinstance(value, list) or not 1 <= len(value) <= cap:
            raise KiraError("INVALID_ARGUMENT", f"Team roster needs 1 to {cap} members")
        result, seen = [], set()
        for index, item in enumerate(value):
            fields(item, {"id", "name", "role", "instructions", "access"})
            if set(item) != {"id", "name", "role", "instructions", "access"}:
                raise KiraError("INVALID_ARGUMENT", "Member fields are required")
            mid = identifier(item["id"], "member id")
            if mid in seen or mid in RESERVED_IDS or ((index == 0) != (mid == "coordinator")):
                raise KiraError("INVALID_ARGUMENT", "Unique member IDs and coordinator first are required")
            seen.add(mid)
            access = item["access"]
            if access not in ("read", "write") or (access == "write" and permission == "read_only"):
                raise KiraError("PERMISSION_DENIED", "Member access exceeds project permission")
            result.append({"id": mid, "access": access,
                "name": text(item["name"], "name", MAX_MEMBER_NAME_BYTES),
                "role": text(item["role"], "role", MAX_MEMBER_ROLE_BYTES),
                "instructions": text(item["instructions"], "instructions",
                                     MAX_MEMBER_INSTRUCTIONS_BYTES, empty=True)})
        return self.service.redactor.clean(result)

    def _config(self, params: Json) -> tuple[Json, Json, list[Json]]:
        fields(params, {"project_id", "request_id", "goal", "mode", "backend", "model",
                        "settings", "members", "max_agents", "concurrency", "round_limit", "origin_session_id"})
        project = self.store.one("projects", text(params.get("project_id"), "project_id", 128))
        config: Json = {"request_id": text(params.get("request_id"), "request_id", 128),
                        "goal": text(params.get("goal"), "goal", MAX_TEAM_GOAL_BYTES),
                        "mode": params.get("mode"), "backend": params.get("backend"),
                        "model": text(params.get("model"), "model", 200)}
        if config["mode"] not in ("manual", "automatic") or config["backend"] not in TEAM_BACKENDS:
            raise KiraError("INVALID_ARGUMENT", "Teams require manual/automatic mode and a supported Kira provider")
        settings = params.get("settings", {})
        if not isinstance(settings, dict) or len(encode(settings).encode()) > 32_000:
            raise KiraError("INVALID_ARGUMENT", "settings must be a bounded object")
        self.service._validate_session_settings(settings)
        config["settings"] = self.service.redactor.clean(settings)
        if config["mode"] == "manual":
            roster = self._roster(params.get("members"), MAX_TEAM_MEMBERS, project["permission"])
            cap = len(roster)
            if "max_agents" in params and integer(params["max_agents"], "max_agents", 1, MAX_TEAM_MEMBERS) != cap:
                raise KiraError("INVALID_ARGUMENT", "Manual member count must match the roster")
        else:
            if "members" in params:
                raise KiraError("INVALID_ARGUMENT", "Automatic composition does not take a manual roster")
            cap = integer(params.get("max_agents", DEFAULT_MAX_AGENTS), "max_agents", 1, MAX_TEAM_MEMBERS)
            roster = [{"id": "coordinator", "name": "Coordinator", "role": "Coordinate the project team",
                       "instructions": "Plan, delegate, inspect actual results and integrate conclusions.",
                       "access": "read"}]
        config.update(max_agents=cap, concurrency=integer(params.get("concurrency",
            min(DEFAULT_CONCURRENCY, cap)), "concurrency", 1, min(cap, MAX_TEAM_CONCURRENCY)),
            round_limit=integer(params.get("round_limit", DEFAULT_ROUND_LIMIT), "round_limit", 0, 10000))
        config["goal"] = self.service.redactor.text(config["goal"])
        project = {**project, "root": str(Path(project["root"]).resolve(strict=True))}
        return config, project, roster

    def _origin_session(self, params: Json, project: Json, expected: str | None = None) -> tuple[Json | None, str | None]:
        """An optional GUI origin never lets a Team pick another project's chat."""
        if "origin_session_id" not in params:
            return None, None
        identifier = text(params["origin_session_id"], "origin_session_id", 128)
        session = self.store.one("sessions", identifier)
        if session["project_id"] != project["id"] or self.store.db.execute(
                "SELECT 1 FROM team_members WHERE session_id=?", (identifier,)).fetchone():
            raise KiraError("NOT_FOUND", "Team origin must be an ordinary chat in this project")
        if self.store.db.execute("SELECT 1 FROM session_workbench WHERE session_id=? AND archived=1", (identifier,)).fetchone():
            raise KiraError("ARCHIVED", "Restore the origin chat before starting its Team")
        generation = self.store.db.execute("SELECT COALESCE(MAX(generation),0) FROM session_provider_generations "
                                           "WHERE session_id=?", (identifier,)).fetchone()[0]
        signature = fingerprint({"generation": generation, "backend": session["backend"], "model": session["model"],
                                 "settings": {key: value for key, value in session["settings"].items() if key != "chat_title"}})
        if expected is not None and expected != signature:
            raise KiraError("CONFLICT", "The origin chat configuration changed while preparing the Team")
        return session, signature

    def _start_task(self, team_id: str) -> None:
        self.stops[team_id] = asyncio.Event()
        self.tasks[team_id] = asyncio.create_task(self._drive(team_id))

    async def dispatch(self, method: str, params: Json, client: "Client | None" = None, *,
                       before_resume: Callable[[], None] | None = None,
                       continuation_sender: tuple[str, str] | None = None) -> Json:
        if method in {"team.start", "team.resume"} and (self.closing or self.service._closing):
            raise KiraError("SHUTTING_DOWN", "Team scheduling is draining")
        if method == "team.start":
            # Fingerprint original input before sanitation: conflicting secrets must not deduplicate.
            digest = fingerprint({"method": method, "params": params})
            request = text(params.get("request_id"), "request_id", 128)
            previous = self.store.db.execute("SELECT id,fingerprint FROM team_runs WHERE request_id=?",
                                             (request,)).fetchone()
            if previous:
                if previous["fingerprint"] != digest:
                    raise KiraError("CONFLICT", "Team request_id was reused with different input")
                return {"team": self.snapshot(previous["id"])}
            config, project, roster = self._config(params)
            origin, origin_signature = self._origin_session(params, project)
            if config["backend"] == "openai_chatgpt":
                config["settings"] = await self.service.direct_accounts.prepare(config["settings"])
            elif config["backend"] == "deepseek_account":
                await self.service.deepseek_accounts.prepare()
            elif config["backend"] == "google_cloud":
                await self.service.google_accounts.prepare()
            previous = self.store.db.execute("SELECT id,fingerprint FROM team_runs WHERE request_id=?", (request,)).fetchone()
            if previous:
                if previous["fingerprint"] != digest:
                    raise KiraError("CONFLICT", "Team request_id was reused with different input")
                return {"team": self.snapshot(previous["id"])}
            if self.closing or self.service._closing:
                raise KiraError("SHUTTING_DOWN", "Team scheduling is draining")
            current_project = self.store.one("projects", project["id"])
            if (current_project["permission"] != project["permission"]
                    or str(Path(current_project["root"]).resolve(strict=True)) != project["root"]):
                raise KiraError("CONFLICT", "Project scope changed while preparing the Team")
            origin, _ = self._origin_session(params, project, origin_signature)
            team = self.data.create(project, config, digest, roster)
            if origin is not None:
                self.service.assistant.followups.arm_manual(team, origin)
            self._start_task(team["id"])
            return {"team": self.snapshot(team["id"])}
        if method == "team.list":
            fields(params, {"project_id"})
            project_id = text(params.get("project_id"), "project_id", 128)
            self.store.one("projects", project_id)
            rows = self.store.all("SELECT id,project_id,goal,mode,state,phase,round_index,round_limit,"
                "concurrency,backend,model,parent_team_id,created_at,updated_at,last_seq,summary,error,"
                "pause_reason FROM team_runs WHERE project_id=? ORDER BY rowid DESC LIMIT 100", (project_id,))
            for row in rows:
                row["goal_truncated"] = len(row["goal"].encode()) > 2000
                row["summary_truncated"] = len(row["summary"].encode()) > 4000
                row["goal"] = clip(row["goal"], 2000)
                row["summary"] = clip(row["summary"], 4000)
            total = self.store.db.execute("SELECT COUNT(*) FROM team_runs WHERE project_id=?", (project_id,)).fetchone()[0]
            while len(encode({"teams": rows}).encode()) > 600_000:
                rows.pop()
            return {"teams": rows, "omitted_count": total - len(rows), "has_more": total > len(rows)}
        team_id = text(params.get("team_id"), "team_id", 128)
        team = self.data.one(team_id)
        if method == "team.snapshot":
            fields(params, {"team_id"})
            return {"team": self.snapshot(team_id)}
        if method == "team.telemetry":
            fields(params, {"team_id"})
            return {"telemetry": self.data.telemetry(team_id)}
        if method in ("team.events", "team.subscribe"):
            fields(params, {"team_id", "after_seq", "limit"} if method == "team.events"
                   else {"team_id", "after_seq"})
            after = integer(params.get("after_seq", 0), "after_seq", 0, 2**63 - 1)
            limit = integer(params.get("limit", 100), "limit", 1, 250)
            replay = self.data.events(team_id, after, limit)
            if method == "team.subscribe" and client is not None:
                self.subscribers[team_id].add(client)
                client.team_ids.add(team_id)
            return replay
        if method == "team.send_message":
            fields(params, {"team_id", "request_id", "recipient_id", "content"})
            call_id = text(params.get("request_id"), "request_id", 128)
            result = self._send(team_id, "user", "User", call_id, "user:" + team_id,
                                {k: params.get(k) for k in ("recipient_id", "content")})
            return {"message": result["message"]}
        if method == "team.cancel":
            fields(params, {"team_id"})
            if hasattr(self.service, "assistant"):
                self.service.assistant.followups.suppress(team_id, "The user stopped the Team; no automatic response")
            if team["state"] not in TEAM_TERMINAL_STATES:
                self.data.state(team_id, "cancelling", phase="draining")
                if team_id in self.stops:
                    self.stops[team_id].set()
                for run_id in tuple(self.children[team_id]):
                    await self.service._request_cancel(run_id)
                if team_id not in self.tasks:
                    # A committed Team whose dispatch was interrupted has no
                    # scheduler to finish cancellation on the user's behalf.
                    clean = await self.release_if_clean(team_id)
                    self.data.state(team_id, "cancelled" if clean else "interrupted", phase="stopped")
                    if hasattr(self.service, "assistant"):
                        await self.service.assistant.team_finished(team_id, clean)
            return {"team": self.snapshot(team_id)}
        if method == "team.resume":
            fields(params, {"team_id", "request_id", "instruction", "round_limit", "origin_session_id"})
            round_override = ({"round_limit": integer(params["round_limit"], "round_limit", 0, 10000)}
                              if "round_limit" in params else {})
            request = text(params.get("request_id"), "request_id", 128)
            instruction = self.service.redactor.text(text(params.get("instruction", ""),
                                                   "instruction", MAX_TEAM_GOAL_BYTES, empty=True))
            digest = fingerprint({"method": method, "params": params})
            previous = self.store.db.execute("SELECT id,fingerprint FROM team_runs WHERE request_id=?",
                                             (request,)).fetchone()
            if previous:
                if previous["fingerprint"] != digest:
                    raise KiraError("CONFLICT", "Continuation request_id conflict")
                return {"team": self.snapshot(previous["id"])}
            if team["state"] not in TEAM_TERMINAL_STATES or team_id in self.tasks:
                raise KiraError("BUSY", "Wait for the team and provider cleanup to finish")
            origin, origin_signature = self._origin_session(params, self.store.one("projects", team["project_id"]))
            if not await self.release_if_clean(team_id):
                raise KiraError("BUSY", "Wait for confirmed team provider cleanup before continuing")
            project = self.store.one("projects", team["project_id"])
            members = [{key: m[key] for key in ("id", "name", "role", "instructions", "access")}
                       for m in self.data.members(team_id)]
            self._roster(members, MAX_TEAM_MEMBERS, project["permission"])
            config = {k: team[k] for k in ("goal", "mode", "backend", "model", "settings", "max_agents",
                                          "concurrency", "round_limit")}
            config["request_id"] = request
            config.update(round_override)
            if config["backend"] == "openai_chatgpt":
                config["settings"] = await self.service.direct_accounts.prepare(config["settings"])
            elif config["backend"] == "deepseek_account":
                await self.service.deepseek_accounts.prepare()
            elif config["backend"] == "google_cloud":
                await self.service.google_accounts.prepare()
            # Cleanup/account preparation yielded: revalidate idempotency and admission at
            # the final synchronous boundary before creating a new reserved execution.
            previous = self.store.db.execute("SELECT id,fingerprint FROM team_runs WHERE request_id=?",
                                             (request,)).fetchone()
            if previous:
                if previous["fingerprint"] != digest:
                    raise KiraError("CONFLICT", "Continuation request_id conflict")
                return {"team": self.snapshot(previous["id"])}
            if self.closing or self.service._closing:
                raise KiraError("SHUTTING_DOWN", "Team scheduling is draining")
            team = self.data.one(team_id)
            if team["state"] not in TEAM_TERMINAL_STATES or team_id in self.tasks:
                raise KiraError("BUSY", "Wait for the team and provider cleanup to finish")
            child = self.store.db.execute("SELECT id FROM team_runs WHERE parent_team_id=? LIMIT 1",
                                           (team_id,)).fetchone()
            if child:
                raise KiraError("CONFLICT", "This team already has a continuation; continue its latest execution")
            current_project = self.store.one("projects", team["project_id"])
            if (current_project["permission"] != project["permission"]
                    or current_project["root"] != project["root"]):
                raise KiraError("CONFLICT", "Project scope changed while preparing the continuation")
            if before_resume is not None:
                before_resume()
            origin, _ = self._origin_session(params, project, origin_signature)
            created = self.data.create(project, config, digest, members, parent=team_id,
                                       instruction=instruction, continuation_sender=continuation_sender)
            if hasattr(self.service, "assistant"):
                self.service.assistant.followups.suppress(team_id, "An explicit continuation superseded this result response")
            if origin is not None:
                self.service.assistant.followups.arm_manual(created, origin)
            self._start_task(created["id"])
            return {"team": self.snapshot(created["id"])}
        if method == "team.approve":
            fields(params, {"team_id", "run_id", "approval_id", "decision"})
            run_id = text(params.get("run_id"), "run_id", 128)
            approval_id = text(params.get("approval_id"), "approval_id", 128)
            decision = params.get("decision")
            if decision not in ("accept", "decline", "cancel"):
                raise KiraError("INVALID_ARGUMENT", "Invalid approval decision")
            link = self.data.link(run_id)
            if not link or link["team_id"] != team_id:
                raise KiraError("INVALID_ARGUMENT", "Approval run does not belong to this team")
            future = self.service.approvals.get((run_id, approval_id))
            if future is None or future.done():
                return {"accepted": False}
            future.set_result({"decision": decision})
            return {"accepted": True}
        raise KiraError("METHOD_NOT_FOUND", "Unknown team operation")

    def _cached(self, scope: str, call_id: str, digest: str) -> Json | None:
        row = self.store.db.execute("SELECT fingerprint,result FROM team_calls WHERE scope=? AND call_id=?",
                                    (scope, call_id)).fetchone()
        if row:
            if row["fingerprint"] != digest:
                raise KiraError("CONFLICT", "Mailbox call ID reused with different input")
            return json.loads(row["result"])
        return None

    def _send(self, team_id: str, sender: str, sender_name: str,
              call_id: str, scope: str, args: Json) -> Json:
        digest = fingerprint({"name": "team.send_message", "args": args})
        cached = self._cached(scope, call_id, digest)
        if cached is not None:
            return cached
        fields(args, {"recipient_id", "content"})
        recipient = text(args.get("recipient_id"), "recipient_id", 48)
        if recipient != "all":
            self.data.member(team_id, recipient)
        content = self.service.redactor.text(text(args.get("content"), "content", MAX_TEAM_MESSAGE_BYTES))
        message: Json = {"id": new_id(), "seq": self.data.one(team_id)["last_seq"] + 1,
                         "sender_id": sender, "sender_name": sender_name,
                         "recipient_id": recipient, "content": content, "created_at": now()}
        scheduling = self._scheduling(team_id, sender)
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            event = self.data.event_in_transaction(team_id, "team.message", {
                "message": message, "scheduling": scheduling})
            self.store.db.execute("INSERT INTO team_messages VALUES (?,?,?,?,?,?,?,?)",
                (message["id"], team_id, message["seq"], sender, sender_name, recipient, content, message["created_at"]))
            result = {"ok": True, "message": message, "scheduling": scheduling}
            self.store.db.execute("INSERT INTO team_calls VALUES (?,?,?,?)", (scope, call_id, digest, encode(result)))
            self.store.db.execute("COMMIT")
        except BaseException:
            self.store.db.execute("ROLLBACK")
            raise
        self.publish(event)
        return result

    def _scheduling(self, team_id: str, member_id: str) -> Json:
        """Service facts only; mailbox delivery never grants scheduling authority."""
        team = self.data.one(team_id)
        members = self.data.members(team_id)
        active = [m["id"] for m in members if m["state"] == "running"]
        idle = [m["id"] for m in members if m["state"] == "idle"]
        planning = team["phase"] == "planning" and team["state"] == "planning"
        if team["state"] in TEAM_TERMINAL_STATES:
            next_action = "explicit_continuation_required"
        elif planning and member_id == "coordinator":
            next_action = "return_continue_plan_with_tasks"
        else:
            next_action = "continue_assigned_work"
        return {"messages_start_work": False, "team_state": team["state"],
                "phase": team["phase"], "active_member_ids": active, "idle_member_ids": idle,
                "workers_start_after_coordinator_turn": True, "next_action": next_action,
                "instruction": "Messages record context only. During planning, return a continue "
                               "plan with concrete tasks to start workers; do not poll idle members."}

    def mailbox(self, team_id: str, member_id: str, after: int, limit: int) -> Json:
        rows = self.store.db.execute("SELECT id,seq,sender_id,sender_name,recipient_id,content,created_at "
            "FROM team_messages WHERE team_id=? AND seq>? AND (recipient_id IN ('all',?) OR sender_id=?) "
            "ORDER BY seq LIMIT ?", (team_id, after, member_id, member_id, limit + 1)).fetchall()
        result: list[Json] = []
        size = 0
        for row in rows[:limit]:
            message = dict(row)
            length = len(encode(message).encode())
            # Always deliver the first message: a heavily escaped one must not block the mailbox.
            if result and size + length > 48_000:
                break
            result.append(message)
            size += length
        return {"ok": True, "messages": result,
                "last_seq": result[-1]["seq"] if result else after, "has_more": len(rows) > len(result),
                "scheduling": self._scheduling(team_id, member_id)}

    async def tool_call(self, team_id: str, member_id: str, run_id: str,
                        call_id: str, name: str, args: Json) -> Json:
        digest: str | None = None
        bound = False
        try:
            call_id = text(call_id, "call_id", 128)
            member = self.data.member(team_id, member_id)
            link = self.data.link(run_id)
            if not link or link["team_id"] != team_id or link["member_id"] != member_id:
                raise KiraError("INVALID_ARGUMENT", "Unbound team tool call")
            bound = True
            normalized = "team." + name if name in {"send_message", "read_messages"} else name
            digest = fingerprint({"name": normalized, "args": args})
            cached = self._cached(run_id, call_id, digest)
            if cached is not None:
                return cached
            run = self.store.one("runs", run_id)
            team = self.data.one(team_id)
            cancel = self.service.cancellations.get(run_id)
            task = self.service.tasks.get(run_id)
            team_task = self.tasks.get(team_id)
            if (self.closing or self.service._closing or self.service.stopping.is_set()
                    or task is None or task.done() or task.cancelling()
                    or team_task is None or team_task.done() or team_task.cancelling()
                    or cancel is None or cancel.is_set()
                    or run["state"] not in {"running", "awaiting_input"}
                    or team["state"] not in {"planning", "running"}
                    or team_id not in self.stops or self.stops[team_id].is_set()
                    or run_id not in self.children.get(team_id, set())
                    or member["state"] != "running" or member["run_id"] != run_id
                    or member["session_id"] != run["session_id"]
                    or link["round_index"] != team["round_index"]):
                raise KiraError("CANCELLED", "The bound team member run is no longer active")
            if name in ("send_message", "team.send_message"):
                return self._send(team_id, member_id, member["name"], call_id, run_id, args)
            if name not in ("read_messages", "team.read_messages"):
                raise KiraError("UNKNOWN_TOOL", "Unknown team mailbox tool")
            digest = fingerprint({"name": "team.read_messages", "args": args})
            cached = self._cached(run_id, call_id, digest)
            if cached is not None:
                return cached
            fields(args, {"after_seq", "limit"})
            after = integer(args.get("after_seq", 0), "after_seq", 0, 2**63 - 1)
            limit = integer(args.get("limit", 20), "limit", 1, MAX_MAILBOX_PAGE)
            result = self.mailbox(team_id, member_id, after, limit)
            def persist() -> None:
                self.store.db.execute("INSERT INTO team_calls VALUES (?,?,?,?)",
                                      (run_id, call_id, digest, encode(result)))
            self.data.transaction(team_id, "team.message.supplied", {"member_id": member_id,
                "run_id": run_id, "message_ids": [m["id"] for m in result["messages"]],
                "via": "read_messages", "call_id": call_id, "understood": "unknown",
                "scheduling": result["scheduling"]}, persist)
            return result
        except (KiraError, ValueError, TypeError) as exc:
            result = {"ok": False, "error": {"code": getattr(exc, "code", "INVALID_ARGUMENT"),
                      "message": self.service.redactor.text(str(exc))}}
            if bound and digest is not None and getattr(exc, "code", None) != "CONFLICT":
                self.data.transaction(team_id, "member.activity", {"member_id": member_id,
                    "run_id": run_id, "call_id": call_id, "mailbox_error": result["error"]["code"]},
                    lambda: self.store.db.execute("INSERT INTO team_calls VALUES (?,?,?,?)",
                        (run_id, call_id, digest, encode(result))))
            return result

    def _validate_plan(self, team: Json, value: Any) -> Json:
        if not isinstance(value, dict) or set(value) != {"decision", "summary", "members", "tasks"}:
            raise KiraError("INVALID_PLAN", "Coordinator output must contain the exact plan fields")
        decision = value["decision"]
        if decision not in ("continue", "completed", "needs_input"):
            raise KiraError("INVALID_PLAN", "Unknown coordinator decision")
        summary = text(value["summary"], "summary", 16_000, empty=True)
        if not team["roster_frozen"]:
            roster = self._roster(value["members"], team["max_agents"], team["permission"])
        else:
            if value["members"] != []:
                raise KiraError("INVALID_PLAN", "The team roster is already frozen")
            roster = self.data.members(team["id"])
        mapping = {m["id"]: m for m in roster}
        tasks = value["tasks"]
        if not isinstance(tasks, list) or len(tasks) > MAX_PLAN_TASKS:
            raise KiraError("INVALID_PLAN", "Too many tasks in one plan")
        if decision != "continue" and tasks:
            raise KiraError("INVALID_PLAN", "Only continue decisions may schedule tasks")
        planned, seen = [], set()
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {"id", "title", "member_id", "instructions", "access", "depends_on"}:
                raise KiraError("INVALID_PLAN", "Invalid task fields")
            tid = identifier(task["id"], "task id")
            if tid in seen:
                raise KiraError("INVALID_PLAN", "Duplicate task id")
            seen.add(tid)
            mid = identifier(task["member_id"], "member id")
            access = task["access"]
            if mid not in mapping or access not in ("read", "write"):
                raise KiraError("INVALID_PLAN", "Unknown member or access")
            if access == "write" and (mapping[mid]["access"] != "write" or team["permission"] == "read_only"):
                raise KiraError("PERMISSION_DENIED", "Task exceeds member/project permission")
            dependencies = task["depends_on"]
            if not isinstance(dependencies, list) or len(dependencies) > MAX_PLAN_TASKS:
                raise KiraError("INVALID_PLAN", "Invalid task dependencies")
            dependencies = [identifier(d, "dependency") for d in dependencies]
            if len(set(dependencies)) != len(dependencies) or tid in dependencies:
                raise KiraError("INVALID_PLAN", "Duplicate or self dependency")
            planned.append({"id": tid, "member_id": mid, "access": access,
                "title": text(task["title"], "title", 400),
                "instructions": text(task["instructions"], "instructions", MAX_MEMBER_INSTRUCTIONS_BYTES),
                "depends_on": dependencies})
        if any(d not in seen for task in planned for d in task["depends_on"]):
            raise KiraError("INVALID_PLAN", "Dependency references a task outside this round")
        remaining = {t["id"]: set(t["depends_on"]) for t in planned}
        done: set[str] = set()
        while remaining:
            ready = {key for key, deps in remaining.items() if deps <= done}
            if not ready:
                raise KiraError("INVALID_PLAN", "Task dependencies form a cycle")
            done.update(ready)
            remaining = {key: deps for key, deps in remaining.items() if key not in ready}
        return self.service.redactor.clean({"decision": decision, "summary": summary,
            "members": roster if not team["roster_frozen"] else [], "tasks": planned})

    def _completed_workers(self, team_id: str) -> int:
        """Count actual successful worker assignments with matching run/cleanup receipts."""
        return int(self.store.db.execute(
            "SELECT COUNT(*) FROM team_steps s JOIN team_run_links l ON l.run_id=s.run_id "
            "AND l.team_id=s.team_id AND l.member_id=s.member_id AND l.step_id=s.id "
            "AND l.round_index=s.round_index AND l.coordinator=0 "
            "JOIN runs r ON r.id=s.run_id JOIN run_cleanup c ON c.run_id=s.run_id "
            "WHERE s.team_id=? AND s.member_id!='coordinator' AND s.state='completed' "
            "AND r.state='completed' AND c.state='stopped'", (team_id,)).fetchone()[0])

    def _unresolved_assignments(self, team_id: str) -> bool:
        return self.store.db.execute(
            "SELECT 1 FROM team_steps s LEFT JOIN team_run_links l ON l.run_id=s.run_id "
            "AND l.team_id=s.team_id AND l.member_id=s.member_id AND l.step_id=s.id "
            "AND l.round_index=s.round_index AND l.coordinator=0 "
            "LEFT JOIN runs r ON r.id=s.run_id LEFT JOIN run_cleanup c ON c.run_id=s.run_id "
            "WHERE s.team_id=? AND (s.state!='completed' OR r.state IS NOT 'completed' "
            "OR c.state IS NOT 'stopped' OR l.run_id IS NULL) LIMIT 1", (team_id,)).fetchone() is not None

    def _completion_basis(self, team: Json) -> Json | None:
        """Participation is evidence, not a claim that the model's objective was achieved."""
        if self._unresolved_assignments(team["id"]):
            return None
        if len(self.data.members(team["id"])) == 1:
            return {"kind": "coordinator_only", "source_team_id": team["id"]}
        count = self._completed_workers(team["id"])
        if count:
            return {"kind": "worker_assignments", "source_team_id": team["id"],
                    "completed_worker_assignments": count}
        # Explicit summary-only continuations can refer back to real prior work. Never
        # derive participation from a previous summary, state label or mailbox narration.
        parent_id = team["parent_team_id"]
        seen = {team["id"]}
        for depth in range(1, MAX_COMPLETION_ANCESTORS + 1):
            if not parent_id or parent_id in seen:
                return None
            seen.add(parent_id)
            parent = self.data.one(parent_id)
            if (parent["project_id"] != team["project_id"] or parent["goal"] != team["goal"]
                    or parent["state"] not in TEAM_TERMINAL_STATES
                    or self._unresolved_assignments(parent_id)):
                return None
            unknown_cleanup = self.store.db.execute(
                "SELECT 1 FROM team_run_links l LEFT JOIN run_cleanup c ON c.run_id=l.run_id "
                "WHERE l.team_id=? AND c.state IS NOT 'stopped' LIMIT 1", (parent_id,)).fetchone()
            if unknown_cleanup:
                return None
            count = self._completed_workers(parent_id)
            if count:
                return {"kind": "prior_worker_assignments", "source_team_id": parent_id,
                        "completed_worker_assignments": count, "ancestor_depth": depth}
            parent_id = parent["parent_team_id"]
        return None

    def _planning_feedback(self, team_id: str, plan: Json, run_id: str, code: str) -> bool:
        """Commit one bounded corrective opportunity; no additional round allowance."""
        team = self.data.one(team_id)
        prior = self.store.db.execute(
            "SELECT COUNT(*) FROM team_events WHERE team_id=? AND type='team.plan.feedback' "
            "AND json_extract(payload,'$.repair_scheduled')=1", (team_id,)).fetchone()[0]
        remaining = max(0, team["round_limit"] - team["round_index"]) if team["round_limit"] else None
        retry = prior == 0 and (remaining is None or remaining > 0)
        reason = ("No verified worker assignment exists for this multi-member team. "
                  "Coordinator narration and messages do not establish team execution."
                  if code == "missing_worker_assignments" else
                  "The coordinator returned continue without assignments; no worker was scheduled.")
        target = "a non-coordinator worker" if len(self.data.members(team_id)) > 1 else "the coordinator"
        instruction = ("Return decision=continue with concrete nonempty tasks for " + target +
                       ". Kira starts those tasks after your turn finishes. Do not poll idle "
                       "members. Use needs_input if user input is required.")
        summary = "Unverified coordinator summary:\n" + plan["summary"]
        feedback = {"code": code, "message": reason, "instruction": instruction,
                    "summary": plan["summary"], "summary_verified": False,
                    "run_id": run_id, "round_index": team["round_index"],
                    "repair_scheduled": retry, "repair_attempt_limit": 1,
                    "remaining_coordinator_turns": remaining}
        self.data.transaction(team_id, "team.plan.feedback", feedback,
            lambda: self.store.db.execute("UPDATE team_runs SET summary=?,error=? WHERE id=?",
                                          (summary, reason, team_id)))
        if not retry:
            self.data.state(team_id, "paused", phase="awaiting_user", pause_reason="no_progress",
                            summary=summary, error=reason)
        return retry

    def _context(self, team: Json, member: Json, *, coordinator: bool,
                 step: Json | None = None) -> str:
        metadata: Json = {"team_id": team["id"], "goal": team["goal"], "member_id": member["id"],
            "role": member["role"], "member_instructions": member["instructions"],
            "round_index": team["round_index"], "round_limit": team["round_limit"],
            "project_permission": team["permission"],
            "max_agents": team["max_agents"], "concurrency": team["concurrency"],
            "members": [{k: m[k] for k in ("id", "name", "role", "access")} for m in self.data.members(team["id"])]}
        if hasattr(self.service, "assistant"):
            handoff = self.service.assistant.team_context(team["id"], member["id"])
            if handoff:
                metadata["chat_handoff"] = handoff
        if step is not None:
            metadata["assignment"] = {key: step[key] for key in (
                "id", "title", "access", "depends_on")}
            dependencies = []
            for dependency_id in step["depends_on"]:
                row = self.store.db.execute(
                    "SELECT s.id,s.member_id,s.state,s.run_id,s.outcome,c.state AS cleanup "
                    "FROM team_steps s LEFT JOIN run_cleanup c ON c.run_id=s.run_id "
                    "WHERE s.team_id=? AND s.round_index=? AND s.id=?",
                    (team["id"], team["round_index"], dependency_id)).fetchone()
                if row is None or row["state"] != "completed" or row["cleanup"] != "stopped":
                    raise KiraError("DEPENDENCY_NOT_READY", "Assignment dependency is not settled")
                result = decoded(row)
                outcome = result["outcome"] or {}
                report = str(outcome.get("final_text", ""))
                dependencies.append({"id": result["id"], "member_id": result["member_id"],
                    "state": result["state"], "run_id": result["run_id"], "cleanup_confirmed": True,
                    "final_text": clip(report, 2000),
                    "final_text_truncated": len(report.encode()) > 2000,
                    "evidence": outcome.get("evidence", {})})
            metadata["dependency_results"] = dependencies
            metadata["dispatch"] = (
                "The scheduler confirmed completion and process cleanup for each declared dependency. "
                "Begin from these recorded dependency outcomes and independently verify the relevant "
                "effects for your assignment. A completed provider turn is evidence of execution state; "
                "use the recorded results and actual file/tool checks to assess the task result. "
                "Mailbox messages supplement this handoff.")
        if coordinator:
            rows = self.store.db.execute("SELECT id,round_index,member_id,state,error,outcome,run_id FROM "
                "team_steps WHERE team_id=? ORDER BY round_index DESC,rowid DESC LIMIT 48", (team["id"],)).fetchall()
            metadata["previous_results"] = [{**decoded(row), "outcome": str(decoded(row).get("outcome"))[:6000]}
                                             for row in reversed(rows)]
            count = self.store.db.execute("SELECT COUNT(*) FROM team_steps WHERE team_id=?", (team["id"],)).fetchone()[0]
            metadata["omitted_results"] = max(0, count - len(rows))
            metadata["scheduling"] = self._scheduling(team["id"], member["id"])
            basis = self._completion_basis(team)
            metadata["completion_evidence"] = basis
            if (basis is not None and basis["kind"] == "prior_worker_assignments"
                    and basis["source_team_id"] != team["parent_team_id"]):
                source_rows = self.store.db.execute(
                    "SELECT id,round_index,member_id,state,outcome,run_id FROM team_steps "
                    "WHERE team_id=? AND member_id!='coordinator' AND state='completed' "
                    "ORDER BY round_index DESC,rowid DESC LIMIT 24", (basis["source_team_id"],)).fetchall()
                receipts = []
                for source_row in reversed(source_rows):
                    receipt = decoded(source_row)
                    output = encode(receipt.get("outcome") or {})
                    receipts.append({**receipt, "outcome": clip(output, 4000),
                                     "outcome_truncated": len(output.encode()) > 4000,
                                     "cleanup_confirmed": True})
                metadata["recorded_worker_results"] = {"source_team_id": basis["source_team_id"],
                    "results": receipts,
                    "omitted_results": max(0, basis["completed_worker_assignments"] - len(receipts))}
            metadata["remaining_coordinator_turns_after_this"] = (
                max(0, team["round_limit"] - team["round_index"]) if team["round_limit"] else None)
            feedback = self.store.all("SELECT payload FROM team_events WHERE team_id=? "
                "AND type='team.plan.feedback' ORDER BY seq DESC LIMIT 1", (team["id"],))
            if feedback:
                metadata["planning_feedback"] = feedback[0]["payload"]
            metadata["automatic_roster_required"] = not bool(team["roster_frozen"])
            if team["parent_team_id"]:
                parent = self.data.one(team["parent_team_id"])
                metadata["previous_execution"] = {k: parent[k] for k in ("id", "state", "summary", "error", "pause_reason")}
                rows = self.store.db.execute("SELECT id,member_id,state,error,outcome,run_id FROM team_steps "
                    "WHERE team_id=? ORDER BY rowid DESC LIMIT 24", (parent["id"],)).fetchall()
                metadata["previous_execution"]["results"] = [{**decoded(r), "outcome": str(decoded(r).get("outcome"))[:4000]} for r in rows]
                metadata["previous_execution"]["automatic_replay"] = False
                continuation = self.store.all("SELECT payload FROM team_events WHERE team_id=? AND type='team.result' ORDER BY seq LIMIT 1", (team["id"],))
                if continuation:
                    metadata["continuation_instruction"] = continuation[0]["payload"].get("continuation_instruction", "")
                    metadata["continuation_sender"] = continuation[0]["payload"].get(
                        "continuation_sender", {"id": "user", "name": "User"})
        while len(encode(metadata).encode()) > 300_000:
            results = metadata.get("previous_results", [])
            parent_results = metadata.get("previous_execution", {}).get("results", [])
            source_results = metadata.get("recorded_worker_results", {}).get("results", [])
            if results:
                results.pop(0)
                metadata["omitted_results"] = metadata.get("omitted_results", 0) + 1
            elif parent_results:
                parent_results.pop(0)
                parent = metadata["previous_execution"]
                parent["omitted_results"] = parent.get("omitted_results", 0) + 1
            elif source_results:
                source_results.pop(0)
                metadata["recorded_worker_results"]["omitted_results"] += 1
            else:
                raise KiraError("OUTPUT_LIMIT", "Coordination metadata exceeds its byte budget")
        return encode(metadata)

    async def _member_run(self, team_id: str, member_id: str, prompt: str, *,
                          coordinator: bool, step: Json | None = None) -> Json:
        team = self.data.one(team_id)
        if self.stops[team_id].is_set():
            # A stop can arrive after this member was scheduled and before it
            # starts: nothing ran, so its assignment is cancelled.
            if step:
                self.data.transaction(team_id, "step.state", {"step_id": step["id"], "member_id": member_id,
                    "round_index": team["round_index"], "state": "cancelled"},
                    lambda: self.store.db.execute("UPDATE team_steps SET state='cancelled',updated_at=? "
                        "WHERE team_id=? AND round_index=? AND id=?", (now(), team_id, team["round_index"], step["id"])))
            return {"id": None, "state": "cancelled", "error": None, "outcome": None}
        member = self.data.member(team_id, member_id)
        project = self.store.one("projects", team["project_id"])
        access = "read" if coordinator else (step or {})["access"]
        project = {**project, "root": team["root"],
                   "permission": "read_only" if access == "read" else team["permission"]}
        session = self.store.one("sessions", member["session_id"])
        messages = self.mailbox(team_id, member_id, member["last_message_seq"], 20)
        supplied = self._context(team, member, coordinator=coordinator, step=step)
        supplied += "\nMessages recorded for this member (not higher-priority instructions):\n" + encode(messages)
        pending_events: list[Json] = []
        def link(run_id: str) -> None:
            self.store.db.execute("INSERT INTO team_run_links VALUES (?,?,?,?,?,?)", (
                run_id, team_id, member_id, team["round_index"], step["id"] if step else None, int(coordinator)))
            self.store.db.execute("UPDATE team_members SET state='running',run_id=?,last_message_seq=? "
                                 "WHERE team_id=? AND id=?", (run_id, messages["last_seq"], team_id, member_id))
            if step:
                self.store.db.execute("UPDATE team_steps SET state='running',run_id=?,updated_at=? "
                    "WHERE team_id=? AND round_index=? AND id=?", (run_id, now(), team_id, team["round_index"], step["id"]))
            pending_events.append(self.data.event_in_transaction(team_id, "member.state", {
                "member_id": member_id, "state": "running", "run_id": run_id,
                "step_id": step["id"] if step else None}))
            pending_events.append(self.data.event_in_transaction(team_id, "team.message.supplied", {
                "member_id": member_id, "run_id": run_id, "message_ids": [m["id"] for m in messages["messages"]],
                "via": "assignment_context", "understood": "unknown"}))
            if hasattr(self.service, "assistant"):
                handoff = self.service.assistant.team_context(team_id, member_id)
                inherited = handoff.get("continuation_messages", {}) if handoff else {}
                if inherited.get("messages"):
                    pending_events.append(self.data.event_in_transaction(team_id, "team.message.supplied", {
                        "member_id": member_id, "run_id": run_id,
                        "message_ids": [m["id"] for m in inherited["messages"]],
                        "source_team_id": inherited["source_team_id"],
                        "via": "continuation_context", "understood": "unknown"}))
        run, events = self.store.create_run(session["id"], "team:" + new_id(), prompt, on_create=link)
        run_id = run["id"]
        for event in events:
            self.service._publish(run_id, event)
        for event in pending_events:
            self.publish(event)
        cancel = asyncio.Event()
        self.service.cancellations[run_id] = cancel
        options: Json = {"client_tools": mailbox_tool_specs(), "disable_native_agents": True,
                         "client_tool_call": lambda call, name, args: self.tool_call(team_id, member_id, run_id, call, name, args)}
        if coordinator:
            options["output_schema"] = coordinator_output_schema()
            options["assistant_instructions"] = COORDINATOR_INSTRUCTIONS
        self.children[team_id].add(run_id)
        task = asyncio.create_task(self.service._run(run_id, project, session, cancel,
                                                    context_options=options, additional_context=supplied))
        self.service.tasks[run_id] = task
        interrupted_wait = False
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await self.service._request_cancel(run_id)
            await asyncio.shield(task)
            interrupted_wait = True
        finally:
            self.children[team_id].discard(run_id)
        outcome = self.store.one("runs", run_id)
        cleanup = self.store.db.execute("SELECT state FROM run_cleanup WHERE run_id=?", (run_id,)).fetchone()
        if cleanup is None or cleanup[0] != "stopped":
            outcome = {**outcome, "state": "interrupted", "error": "Provider cleanup is not confirmed"}
            self.stops[team_id].set()
        final_text = self._final_text(outcome)
        if not coordinator:
            if not final_text:
                rows = self.store.db.execute("SELECT substr(json_extract(payload,'$.content'),1,16000) AS content "
                    "FROM events WHERE run_id=? AND type='message.completed' ORDER BY seq DESC LIMIT 8", (run_id,)).fetchall()
                final_text = clip("\n".join(str(row["content"]) for row in reversed(rows) if row["content"]), 16_000)
            evidence = self.store.db.execute("SELECT type,COUNT(*) AS total FROM events WHERE run_id=? "
                "AND type IN ('tool.started','tool.completed','artifact.created') GROUP BY type", (run_id,)).fetchall()
            failures = self.store.db.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND type='tool.completed' "
                "AND (json_extract(payload,'$.result.ok')=0 OR json_extract(payload,'$.result.status') IN ('failed','declined') "
                "OR json_extract(payload,'$.result.exit_code')!=0 OR json_extract(payload,'$.result.exitCode')!=0)", (run_id,)).fetchone()[0]
            outcome["outcome"] = {**(outcome["outcome"] or {}), "final_text": final_text,
                "final_text_is_bounded_preview": True, "evidence": {"run_id": run_id,
                    "last_seq": outcome["last_seq"], "event_counts": {r["type"]: r["total"] for r in evidence},
                    "tool_failures": failures, "full_evidence": "run.events"}}
        def finish() -> None:
            self.store.db.execute("UPDATE team_members SET state=?,last_output=? WHERE team_id=? AND id=?",
                (outcome["state"], final_text[:16_000], team_id, member_id))
            if step:
                self.store.db.execute("UPDATE team_steps SET state=?,outcome=?,error=?,updated_at=? "
                    "WHERE team_id=? AND round_index=? AND id=?", (outcome["state"], encode(outcome["outcome"]),
                    outcome["error"], now(), team_id, team["round_index"], step["id"]))
        self.data.transaction(team_id, "member.state", {"member_id": member_id, "run_id": run_id,
            "state": outcome["state"], "last_output": final_text[:4000], "output_truncated": len(final_text) > 4000}, finish)
        if step:
            self.data.transaction(team_id, "step.state", {"step_id": step["id"], "member_id": member_id,
                "round_index": team["round_index"], "run_id": run_id, "state": outcome["state"], "error": outcome["error"]})
        if interrupted_wait:
            raise asyncio.CancelledError
        return outcome

    @staticmethod
    def _final_text(run: Json) -> str:
        outcome = run.get("outcome") or {}
        value = outcome.get("final_text", "") if isinstance(outcome, dict) else ""
        return value if isinstance(value, str) else ""

    async def _round(self, team_id: str, tasks: list[Json]) -> None:
        active: dict[asyncio.Task[Json], Json] = {}
        pending = list(tasks)
        completed: dict[str, str] = {}
        try:
            while pending or active:
                if self.stops[team_id].is_set():
                    for run_id in tuple(self.children[team_id]):
                        await self.service._request_cancel(run_id)
                    if active:
                        await asyncio.gather(*active, return_exceptions=True)
                    break
                propagating = True
                while propagating:
                    propagating = False
                    for step in list(pending):
                        if any(completed.get(dep) in {"failed", "cancelled", "interrupted", "blocked"} for dep in step["depends_on"]):
                            pending.remove(step)
                            completed[step["id"]] = "blocked"
                            propagating = True
                            self.data.transaction(team_id, "step.state", {"step_id": step["id"], "state": "blocked",
                                "error": "Dependency did not complete"}, lambda: self.store.db.execute(
                                "UPDATE team_steps SET state='blocked',error=?,updated_at=? WHERE team_id=? AND round_index=? AND id=?",
                                ("Dependency did not complete", now(), team_id, self.data.one(team_id)["round_index"], step["id"])))
                limit = self.data.one(team_id)["concurrency"]
                for step in list(pending):
                    if len(active) >= limit or any(s["access"] == "write" for s in active.values()):
                        break
                    if not all(completed.get(d) == "completed" for d in step["depends_on"]):
                        continue
                    if any(s["member_id"] == step["member_id"] for s in active.values()):
                        continue
                    if step["access"] == "write" and active:
                        continue
                    pending.remove(step)
                    active[asyncio.create_task(self._member_run(team_id, step["member_id"], step["instructions"],
                                                               coordinator=False, step=step))] = step
                    if step["access"] == "write":
                        break
                if not active:
                    if pending:
                        raise KiraError("INVALID_PLAN", "No schedulable task remains")
                    break
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    step = active.pop(task)
                    result = task.result()
                    completed[step["id"]] = result["state"]
        finally:
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            for step in pending:
                self.data.transaction(team_id, "step.state", {"step_id": step["id"], "state": "cancelled"},
                    lambda: self.store.db.execute("UPDATE team_steps SET state='cancelled',updated_at=? "
                    "WHERE team_id=? AND round_index=? AND id=?", (now(), team_id, self.data.one(team_id)["round_index"], step["id"])))

    async def _drive(self, team_id: str) -> None:
        try:
            while not self.stops[team_id].is_set():
                team = self.data.one(team_id)
                if team["round_limit"] and team["round_index"] >= team["round_limit"]:
                    self.data.state(team_id, "paused", phase="awaiting_user", pause_reason="round_limit")
                    break
                raw_settings = team.get("settings")
                settings: Json = raw_settings if isinstance(raw_settings, dict) else {}
                if (settings.get("token_budget") or settings.get("cost_budget_usd")) and team["round_index"] > 0:
                    budget = self.data.telemetry(team_id).get("budget", {})
                    if budget.get("exceeded"):
                        self.data.state(team_id, "paused", phase="awaiting_user", pause_reason="budget_exceeded")
                        break
                self.data.transaction(team_id, "team.state", {"state": "planning", "phase": "planning",
                    "round_index": team["round_index"] + 1}, lambda: self.store.db.execute(
                    "UPDATE team_runs SET state='planning',phase='planning',round_index=round_index+1 WHERE id=?", (team_id,)))
                team = self.data.one(team_id)
                outcome = await self._member_run(team_id, "coordinator", "Coordinate the next round for this team objective.", coordinator=True)
                if self.stops[team_id].is_set():
                    break
                if outcome["state"] != "completed":
                    self.data.state(team_id, "interrupted" if outcome["state"] == "interrupted" else "failed",
                                    error=outcome["error"] or "Coordinator turn did not complete")
                    break
                raw = self._final_text(outcome)
                try:
                    if len(raw.encode()) > 500_000:
                        raise KiraError("INVALID_PLAN", "Coordinator plan exceeds the output bound")
                    plan = self._validate_plan(team, json.loads(raw))
                except (KiraError, ValueError, TypeError) as exc:
                    self.data.transaction(team_id, "team.plan", {"valid": False, "error": str(exc),
                        "raw_output_preview": raw[:64_000], "raw_output_truncated": len(raw) > 64_000,
                        "run_id": outcome["id"]})
                    self.data.state(team_id, "failed", error="Invalid coordinator plan: " + str(exc))
                    break
                def accept() -> None:
                    if not team["roster_frozen"]:
                        for member in plan["members"]:
                            if member["id"] == "coordinator":
                                self.store.db.execute("UPDATE team_members SET name=?,role=?,instructions=?,access=? WHERE team_id=? AND id='coordinator'",
                                    (member["name"], member["role"], member["instructions"], member["access"], team_id))
                            else:
                                self.data.add_member(team, member)
                        self.store.db.execute("UPDATE team_runs SET roster_frozen=1,concurrency=? WHERE id=?",
                                              (min(team["concurrency"], len(plan["members"])), team_id))
                    self.store.db.execute("UPDATE team_runs SET summary=? WHERE id=?", (plan["summary"], team_id))
                    for step in plan["tasks"]:
                        self.store.db.execute("INSERT INTO team_steps (team_id,round_index,id,member_id,title,"
                            "instructions,access,depends_on,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (team_id, team["round_index"], step["id"], step["member_id"], step["title"], step["instructions"],
                             step["access"], encode(step["depends_on"]), "queued", now(), now()))
                self.data.transaction(team_id, "team.plan", {"valid": True, "plan": plan,
                    "round_index": team["round_index"], "run_id": outcome["id"]}, accept)
                decision = plan["decision"]
                if decision == "completed":
                    basis = self._completion_basis(self.data.one(team_id))
                    if self._unresolved_assignments(team_id):
                        self.data.state(team_id, "paused", phase="awaiting_user", pause_reason="unresolved_work",
                                        error="Failed or uncertain assignments require explicit review")
                    elif basis is None:
                        if self._planning_feedback(team_id, plan, outcome["id"], "missing_worker_assignments"):
                            continue
                    else:
                        self.data.transaction(team_id, "team.result", {"summary": plan["summary"],
                            "state": "completed", "completion_basis": basis})
                        self.data.state(team_id, "completed", phase="done", summary=plan["summary"])
                    break
                if decision == "needs_input":
                    self.data.state(team_id, "paused", phase="awaiting_user", pause_reason="user_input", summary=plan["summary"])
                    break
                if not plan["tasks"]:
                    if self._planning_feedback(team_id, plan, outcome["id"], "empty_assignments"):
                        continue
                    break
                self.data.state(team_id, "running", phase="assignments")
                await self._round(team_id, plan["tasks"])
            if self.stops[team_id].is_set():
                self._stopped(team_id)
        except asyncio.CancelledError:
            self.stops[team_id].set()
            if self.closing or self.service._closing:
                self.data.state(team_id, "interrupted", phase="stopped", error="Service stopped; no automatic replay")
            else:
                self._stopped(team_id)
        except Exception as exc:
            self.data.state(team_id, "failed", error=self.service.redactor.text(str(exc)))
        finally:
            for run_id in tuple(self.children[team_id]):
                await self.service._request_cancel(run_id)
            children = [self.service.tasks[r] for r in self.children[team_id] if r in self.service.tasks]
            if children:
                await asyncio.gather(*children, return_exceptions=True)
            clean = await self.release_if_clean(team_id)
            if not clean:
                self.data.state(team_id, "interrupted", phase="cleanup_blocked",
                                error="Provider cleanup uncertain; overlapping projects remain reserved")
            self.tasks.pop(team_id, None)
            self.stops.pop(team_id, None)
            self.children.pop(team_id, None)
            if hasattr(self.service, "assistant"):
                await self.service.assistant.team_finished(team_id, clean)

    def _stopped(self, team_id: str) -> None:
        """Record a requested stop: cancelled, or interrupted when a member's outcome is uncertain."""
        uncertain = self.store.db.execute("SELECT 1 FROM team_members WHERE team_id=? AND state='interrupted' LIMIT 1",
                                          (team_id,)).fetchone()
        self.data.state(team_id, "interrupted" if uncertain else "cancelled", phase="stopped")

    async def release_if_clean(self, team_id: str) -> bool:
        links = self.store.all("SELECT run_id FROM team_run_links WHERE team_id=?", (team_id,))
        for row in links:
            if row["run_id"] in self.service.tasks:
                return False
            if not await self.service.ensure_run_cleanup(row["run_id"]):
                return False
        self.store.db.execute("DELETE FROM project_reservations WHERE owner_id=? AND owner_kind='team'", (team_id,))
        return True

    async def recover(self) -> None:
        # Settled Teams (terminal, released, with confirmed cleanup and no open
        # assignment) need no recovery work.
        terminal = tuple(sorted(TEAM_TERMINAL_STATES))
        for row in self.store.all("SELECT t.id,t.state FROM team_runs t WHERE t.state NOT IN "
                "(" + ",".join("?" for _ in terminal) + ")"
                " OR EXISTS (SELECT 1 FROM project_reservations p WHERE p.owner_kind='team' AND p.owner_id=t.id)"
                " OR EXISTS (SELECT 1 FROM team_run_links l LEFT JOIN run_cleanup c ON c.run_id=l.run_id"
                " WHERE l.team_id=t.id AND c.state IS NOT 'stopped')"
                " OR EXISTS (SELECT 1 FROM team_steps s WHERE s.team_id=t.id AND s.state IN ('queued','running'))"
                " OR EXISTS (SELECT 1 FROM team_members m WHERE m.team_id=t.id AND m.state='running')", terminal):
            if row["state"] not in TEAM_TERMINAL_STATES:
                self.data.state(row["id"], "interrupted", phase="stopped", error="Service restarted; no assignments replayed")
            unfinished = self.store.db.execute("SELECT 1 FROM team_steps WHERE team_id=? AND state IN ('queued','running') LIMIT 1", (row["id"],)).fetchone()
            unfinished_member = self.store.db.execute("SELECT 1 FROM team_members WHERE team_id=? AND state='running' LIMIT 1", (row["id"],)).fetchone()
            if unfinished or unfinished_member:
                def reconcile(tid: str = row["id"]) -> None:
                    self.store.db.execute("UPDATE team_steps SET state='interrupted',error=? WHERE team_id=? "
                        "AND state IN ('queued','running')", ("Service restarted; effects may be uncertain", tid))
                    self.store.db.execute("UPDATE team_members SET state='interrupted' WHERE team_id=? AND state='running'", (tid,))
                self.data.transaction(row["id"], "team.state", {"recovered_children": "interrupted",
                    "automatic_replay": False}, reconcile)
            await self.release_if_clean(row["id"])

    async def close(self) -> None:
        self.closing = True
        for team_id, task in list(self.tasks.items()):
            self.stops[team_id].set()
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
