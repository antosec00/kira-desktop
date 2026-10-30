"""Durable prompt queue and exact-turn delivery, owned by the service."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

from kira.domain.contracts import Json, KiraError, RunControl, TERMINAL_STATES
from kira.service.project_context import ProjectChats
from kira.service.storage import encode, new_id, now

if TYPE_CHECKING:
    from kira.service.server import Service

MODES = frozenset({"execute", "plan", "review", "recap"})
QUEUE_METHODS = {"queue." + name for name in ("list", "add", "edit", "reorder", "remove", "pause", "resume")}
CONTROL_METHODS = {"run.steer", "session.compact"}
MAX_ITEMS = 64
MAX_PROMPT = 8000
MAX_GUIDANCE_REQUESTS = 128
MAX_GUIDANCE_BATCH = 32
MAX_GUIDANCE_BATCH_BYTES = 256 * 1024
# Refusals that depend only on the current state (active work, an archived chat,
# a Team handoff, browser settings changing, a draining service): the request
# itself stays valid, so it waits in the paused queue for an explicit resume.
TRANSIENT_REFUSALS = frozenset({"BUSY", "ARCHIVED", "SHUTTING_DOWN"})


def text(params: Json, key: str, maximum: int = 128, *, empty: bool = False) -> str:
    value = params.get(key)
    try:
        valid = (isinstance(value, str) and (empty or bool(value.strip())) and "\0" not in value
                 and len(value.encode("utf-8")) <= maximum
                 and len(encode(value).encode("utf-8")) <= maximum + 2)
    except UnicodeError:
        valid = False
    if not valid:
        raise KiraError("INVALID_ARGUMENT", f"{key} must be nonempty text of at most {maximum} encoded UTF-8 bytes")
    assert isinstance(value, str)
    return value


def fields(params: Json, allowed: set[str]) -> None:
    if not isinstance(params, dict) or set(params) - allowed:
        raise KiraError("INVALID_ARGUMENT", "Unexpected parameters")


def mode(params: Json) -> str:
    value = params.get("mode", "execute")
    if not isinstance(value, str) or value not in MODES:
        raise KiraError("INVALID_ARGUMENT", "Unknown run mode")
    return value


class PromptQueue:
    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = service.store
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.poller: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.poller = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        while not self.service._closing:
            await asyncio.sleep(1)
            for row in self.store.all("SELECT DISTINCT project_id FROM prompt_queue WHERE status='pending'"):
                self.kick(row["project_id"])

    def recover(self) -> None:
        self.store.db.execute("UPDATE queue_controls SET paused=1,updated_at=?", (now(),))
        # A crash can occur after the first item commit and before its controls
        # row exists. Every recovered project must therefore be explicitly paused.
        for project in self.store.all("SELECT DISTINCT project_id FROM prompt_queue"):
            self.set_paused(project["project_id"], True)
        for row in self.store.all("SELECT * FROM prompt_queue WHERE status IN ('starting','running')"):
            runs = self.store.all("SELECT * FROM runs WHERE request_id=?", (row["request_id"],))
            run = runs[0] if runs else None
            status = run["state"] if run and run["state"] in TERMINAL_STATES else "interrupted"
            self.store.db.execute("UPDATE prompt_queue SET status=?,run_id=?,error=?,updated_at=? WHERE id=?",
                (status, run["id"] if run else row["run_id"], "Service restarted; queue requires explicit resume", now(), row["id"]))

    def paused(self, project_id: str) -> bool:
        row = self.store.db.execute("SELECT paused FROM queue_controls WHERE project_id=?", (project_id,)).fetchone()
        return bool(row[0]) if row else False

    def set_paused(self, project_id: str, value: bool) -> None:
        self.store.db.execute("INSERT INTO queue_controls VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE "
                              "SET paused=excluded.paused,updated_at=excluded.updated_at", (project_id, int(value), now()))

    def _item(self, row: Json) -> Json:
        return {**row, "item_id": row["id"], "attachments": self.service.image_inputs.metadata(
            self.service.image_inputs.linked("queue", row["id"]))}

    def _previous(self, request_id: str, session_id: str, prompt: str, selected_mode: str,
                  attachments: list[str]) -> Json | None:
        previous = self.store.all("SELECT * FROM prompt_queue WHERE request_id=?", (request_id,))
        if not previous:
            return None
        item = previous[0]
        if (item["session_id"], item["prompt"], item["mode"]) != (session_id, prompt, selected_mode) \
                or self.service.image_inputs.linked("queue", item["id"]) != attachments:
            raise KiraError("CONFLICT", "request_id was already used with different queue input")
        return self._item(item)

    def listing(self, project_id: str) -> Json:
        self.store.one("projects", project_id)
        active = self.store.all("SELECT * FROM prompt_queue WHERE project_id=? AND status IN ('pending','starting','running') "
                                "ORDER BY position,created_at,id LIMIT ?", (project_id, MAX_ITEMS))
        recent = self.store.all("SELECT id,project_id,session_id,request_id,substr(prompt,1,200) AS prompt,mode,position,status,"
                                "run_id,substr(error,1,1000) AS error,created_at,updated_at FROM prompt_queue WHERE project_id=? "
                                "AND status NOT IN ('pending','starting','running','removed') ORDER BY updated_at DESC,id DESC LIMIT 10", (project_id,))
        return {"items": [self._item(row) for row in active + recent],
                "paused": self.paused(project_id), "limit": MAX_ITEMS}

    async def dispatch(self, method: str, params: Json) -> Json:
        allowed = {"project_id"}
        if method == "queue.add":
            allowed = {"session_id", "request_id", "prompt", "mode", "attachments"}
        elif method == "queue.edit":
            allowed |= {"item_id", "prompt"}
        elif method == "queue.remove":
            allowed.add("item_id")
        elif method == "queue.reorder":
            allowed.add("item_ids")
        fields(params, allowed)
        if method == "queue.add":
            session = self.store.one("sessions", text(params, "session_id"))
            project_id = session["project_id"]
            ProjectChats(self.store).owned_session(project_id, session["id"])
            if ProjectChats(self.store).enrich(session).get("archived"):
                raise KiraError("ARCHIVED", "Restore this chat before adding work")
            attachments = self.service.image_inputs.identifiers(params.get("attachments", []))
            prompt = self.service.redactor.text(text(params, "prompt", MAX_PROMPT, empty=bool(attachments)))
            request_id, selected_mode = text(params, "request_id"), mode(params)
            item = self._previous(request_id, session["id"], prompt, selected_mode, attachments)
            if item is not None:
                return {"item": item, **self.listing(project_id)}
            if self.store.db.execute("SELECT 1 FROM runs WHERE request_id=?", (request_id,)).fetchone():
                raise KiraError("CONFLICT", "request_id already belongs to a run")
            if session["backend"] == "openai_chatgpt":
                await self.service.direct_accounts.prepare(session["settings"])
            if self.store.one("sessions", session["id"]) != session:
                raise KiraError("CONFLICT", "The chat provider changed while preparing the queue; add again")
            if ProjectChats(self.store).enrich(session).get("archived"):
                raise KiraError("ARCHIVED", "Restore this chat before adding work")
            if self.service._closing or self.service.stopping.is_set():
                raise KiraError("BUSY", "Service is stopping; add the queued request after restart")
            item = self._previous(request_id, session["id"], prompt, selected_mode, attachments)
            if item is not None:
                return {"item": item, **self.listing(project_id)}
            if self.store.db.execute("SELECT 1 FROM runs WHERE request_id=?", (request_id,)).fetchone():
                raise KiraError("CONFLICT", "request_id already belongs to a run")
            self.service.image_inputs.validate(session, attachments)
            count = self.store.db.execute("SELECT count(*) FROM prompt_queue WHERE project_id=? AND status IN ('pending','starting','running')", (project_id,)).fetchone()[0]
            if count >= MAX_ITEMS:
                raise KiraError("QUEUE_FULL", "Queue contains 64 unfinished requests")
            position = self.store.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM prompt_queue WHERE project_id=?", (project_id,)).fetchone()[0]
            identifier, timestamp = new_id(), now()
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                self.store.db.execute("INSERT INTO prompt_queue VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, project_id, session["id"], request_id, prompt, selected_mode, position, "pending", None, None, timestamp, timestamp))
                for index, attachment in enumerate(attachments):
                    self.store.db.execute("INSERT INTO queue_image_attachments VALUES (?,?,?)",
                                          (identifier, attachment, index))
                if not self.store.db.execute("SELECT 1 FROM queue_controls WHERE project_id=?", (project_id,)).fetchone():
                    self.set_paused(project_id, False)
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
            self.kick(project_id)
            item = self.store.all("SELECT * FROM prompt_queue WHERE id=?", (identifier,))[0]
            return {"item": self._item(item), **self.listing(project_id)}
        project_id = text(params, "project_id")
        self.store.one("projects", project_id)
        if method == "queue.list":
            return self.listing(project_id)
        if method in {"queue.pause", "queue.resume"}:
            self.set_paused(project_id, method == "queue.pause")
            if method == "queue.resume":
                self.kick(project_id)
        elif method in {"queue.edit", "queue.remove"}:
            identifier = text(params, "item_id")
            rows = self.store.all("SELECT * FROM prompt_queue WHERE id=? AND project_id=?", (identifier, project_id))
            if not rows:
                raise KiraError("NOT_FOUND", "Queued request not found")
            item = rows[0]
            if item["status"] in {"starting", "running"} or method == "queue.edit" and item["status"] != "pending":
                raise KiraError("BUSY", "This request has already been dispatched")
            if method == "queue.edit":
                attachments = self.service.image_inputs.linked("queue", identifier)
                prompt = self.service.redactor.text(text(params, "prompt", MAX_PROMPT, empty=bool(attachments)))
                self.store.db.execute("UPDATE prompt_queue SET prompt=?,updated_at=? WHERE id=?", (prompt, now(), identifier))
            else:
                # Retain identity as a tombstone: a delayed add retry cannot
                # resurrect a request the user deliberately removed.
                self.store.db.execute("UPDATE prompt_queue SET status='removed',updated_at=? WHERE id=?", (now(), identifier))
        elif method == "queue.reorder":
            identifiers = params.get("item_ids")
            pending = [row["id"] for row in self.store.all("SELECT id FROM prompt_queue WHERE project_id=? AND status='pending'", (project_id,))]
            if (not isinstance(identifiers, list) or len(identifiers) > MAX_ITEMS
                    or not all(isinstance(value, str) for value in identifiers)
                    or len(identifiers) != len(set(identifiers)) or set(identifiers) != set(pending)):
                raise KiraError("CONFLICT", "Supply exactly the current pending request identifiers")
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                for position, identifier in enumerate(identifiers):
                    self.store.db.execute("UPDATE prompt_queue SET position=?,updated_at=? WHERE id=?", (position, now(), identifier))
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
        return self.listing(project_id)

    def kick(self, project_id: str) -> None:
        if self.service._closing or self.service.stopping.is_set() or self.paused(project_id):
            return
        existing = self.tasks.get(project_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self._dispatch_next(project_id))
        self.tasks[project_id] = task
        task.add_done_callback(lambda finished: self.tasks.pop(project_id, None) if self.tasks.get(project_id) is finished else None)

    async def _dispatch_next(self, project_id: str) -> None:
        try:
            if self.paused(project_id) or self.service._closing or self.service._settings_changing:
                return
            project = self.store.one("projects", project_id)
            if self.store.project_active(project_id) or self.store.reserved(project["root"]):
                return
            rows = self.store.all("SELECT * FROM prompt_queue WHERE project_id=? AND status='pending' ORDER BY position,created_at,id LIMIT 1", (project_id,))
            if not rows:
                return
            item = rows[0]
            self.store.db.execute("UPDATE prompt_queue SET status='starting',error=NULL,updated_at=? WHERE id=?",
                                  (now(), item["id"]))
            try:
                result = await self.service.dispatch("run.start", {"session_id": item["session_id"],
                    "prompt": item["prompt"], "request_id": item["request_id"], "mode": item["mode"],
                    "attachments": self.service.image_inputs.linked("queue", item["id"])})
            except Exception as exc:
                status = ("pending" if isinstance(exc, KiraError) and exc.code in TRANSIENT_REFUSALS
                          else "failed")
                self.store.db.execute("UPDATE prompt_queue SET status=?,error=?,updated_at=? WHERE id=?",
                                      (status, self.service.redactor.text(str(exc))[:2000], now(), item["id"]))
                self.set_paused(project_id, True)
                return
            self.store.db.execute("UPDATE prompt_queue SET status='running',run_id=?,updated_at=? WHERE id=?",
                                  (result["run"]["id"], now(), item["id"]))
        except Exception:
            self.set_paused(project_id, True)

    def finished(self, run_id: str) -> None:
        run = self.store.one("runs", run_id)
        session = self.store.one("sessions", run["session_id"])
        project_id = session["project_id"]
        status = run["state"] if run["state"] in TERMINAL_STATES else "interrupted"
        self.store.db.execute("UPDATE prompt_queue SET status=?,error=?,updated_at=? WHERE run_id=?",
                              (status, run.get("error"), now(), run_id))
        if status != "completed":
            self.set_paused(project_id, True)
        self.kick(project_id)

    async def close(self) -> None:
        if self.poller:
            self.poller.cancel()
            await asyncio.gather(self.poller, return_exceptions=True)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class TurnControls:
    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = service.store
        self.tasks: set[asyncio.Task[Any]] = set()
        self._inboxes: dict[str, tuple[object, Json]] = {}

    def recover(self) -> None:
        self._inboxes.clear()
        for row in self.store.all("SELECT * FROM steering_requests WHERE status='pending'"):
            self._settle_unfinished(row, "Service restarted; guidance was not replayed")

    def _scope(self, run_id: str) -> Json:
        run = self.store.one("runs", run_id)
        session = self.store.one("sessions", run["session_id"])
        project = self.store.one("projects", session["project_id"])
        settings = {key: value for key, value in session["settings"].items() if key != "chat_title"}
        if (session["backend"] in {"openai_chatgpt", "ollama", "deepseek_account", "google_cloud"}
                and settings.get("browser_action_approval", "ask") in ("ask", "always")):
            # This explicit desktop consent can change during a turn. Web
            # checks the current policy again before each browser effect;
            # it is not a provider identity change. Keep every other setting
            # (and malformed policy values) in the frozen guidance binding.
            settings.pop("browser_action_approval", None)
        binding = self.store.db.execute("SELECT generation FROM run_provider_bindings WHERE run_id=?",
                                        (run_id,)).fetchone()
        return {"run_id": run_id, "session_id": session["id"], "project_id": project["id"],
                "backend": session["backend"], "model": session["model"],
                "account_id": settings.get("openai_account_id"),
                "reasoning_effort": settings.get("reasoning_effort"),
                "generation": int(binding[0]) if binding else 0,
                "settings_sha256": hashlib.sha256(encode(settings).encode()).hexdigest(),
                "project_sha256": hashlib.sha256(encode({"root": project["root"],
                    "permission": project["permission"]}).encode()).hexdigest()}

    def bind_run(self, run_id: str, session: Json) -> RunControl | None:
        """Open service-owned admission before a boundary provider starts work."""
        if (session.get("backend") not in {"openai_chatgpt", "ollama", "deepseek_account", "google_cloud"} or session.get("team_id")
                or self.service.teams.data.link(run_id) is not None):
            return None
        scope = self._scope(run_id)
        if (scope["session_id"] != session["id"] or scope["backend"] != session["backend"]
                or scope["model"] != session["model"]
                or scope["account_id"] != session["settings"].get("openai_account_id")
                or scope["reasoning_effort"] != session["settings"].get("reasoning_effort")):
            raise KiraError("CONFLICT", "Guidance binding differs from the admitted provider run")
        if run_id in self._inboxes:
            raise KiraError("CONFLICT", "Guidance is already bound to this execution")
        token = object()
        self._inboxes[run_id] = token, scope

        async def boundary(action: str, params: Json) -> Json:
            # No await inside a boundary: admission, claim and sealing are
            # serialized with run.steer on the service event loop.
            current = self._inboxes.get(run_id)
            run = self.store.one("runs", run_id)
            if (current is None or current[0] is not token or self.service._closing
                    or run["state"] not in {"running", "awaiting_input"}):
                raise KiraError("NOT_ACTIVE", "This execution no longer accepts guidance boundaries")
            if self._scope(run_id) != scope:
                self.finished(run_id, "Provider or project binding changed before guidance delivery")
                raise KiraError("CONFLICT", "Guidance no longer belongs to the current provider binding")
            if action == "accepted":
                fields(params, {"delivery_id"})
                delivery = text(params, "delivery_id")
                rows = [row for row in self.store.all("SELECT * FROM steering_requests WHERE run_id=?", (run_id,))
                        if json.loads(row["result"]).get("delivery_id") == delivery]
                if not rows:
                    raise KiraError("NOT_FOUND", "Guidance delivery identity is not owned by this execution")
                if any(row["status"] not in {"pending", "accepted"} for row in rows):
                    raise KiraError("DELIVERY_UNKNOWN", "Guidance delivery was already closed without confirmation")
                for row in rows:
                    if row["status"] == "accepted":
                        continue
                    result = {**json.loads(row["result"]), "accepted": True,
                              "status": "accepted", "delivery_state": "provider_accepted",
                              "model_consumption_confirmed": False}
                    self._commit(row["request_id"], "accepted", result, [
                        ("steering.accepted", {"request_id": row["request_id"], **result})])
                return {"accepted": True, "delivery_id": delivery, "model_consumption_confirmed": False}
            fields(params, set())
            rows = self.store.all("SELECT * FROM steering_requests WHERE run_id=? AND status='pending' "
                                  "ORDER BY rowid", (run_id,))
            queued = [row for row in rows if json.loads(row["result"]).get("delivery_state") == "queued"]
            if action == "pending":
                return {"pending": bool(queued)}
            if action == "seal":
                if rows:
                    return {"sealed": False}
                self._inboxes.pop(run_id, None)
                return {"sealed": True}
            if action != "take":
                raise KiraError("INVALID_ARGUMENT", "Unknown guidance boundary action")
            if any(json.loads(row["result"]).get("delivery_state") == "submitted" for row in rows):
                raise KiraError("DELIVERY_UNKNOWN", "Previous guidance has no confirmed provider response")
            if not queued:
                return {"messages": [], "delivery_id": ""}
            delivery, messages, size = new_id(), [], 0
            for row in queued[:MAX_GUIDANCE_BATCH]:
                message = {"request_id": row["request_id"], "content": row["prompt"]}
                message_size = len(encode(message).encode())
                if size + message_size > MAX_GUIDANCE_BATCH_BYTES:
                    break
                size += message_size
                result = {**json.loads(row["result"]), "delivery_state": "submitted",
                          "delivery_id": delivery, "accepted": False}
                self._commit(row["request_id"], "pending", result, [
                    ("message.user", {"content": row["prompt"], "source": "steer",
                                      "request_id": row["request_id"], "delivery_state": "submitted"}),
                    ("steering.submitted", {"request_id": row["request_id"], **result})])
                messages.append(message)
            return {"messages": messages, "delivery_id": delivery}

        return boundary

    def _commit(self, request_id: str, status: str, result: Json,
                events: list[tuple[str, Json]]) -> None:
        for event in self.store.steering(request_id, status, result, events):
            self.service._publish(event["run_id"], event)

    def _settle_unfinished(self, row: Json, reason: str) -> None:
        previous = json.loads(row["result"])
        unsent = previous.get("delivery_state") == "queued"
        result = {**previous, "accepted": False, "automatic_replay": False,
                  "delivery_state": "not_delivered" if unsent else "unknown",
                  "status": "rejected" if unsent else "unknown",
                  "request_id": row["request_id"], "run_id": row["run_id"],
                  "model_consumption_confirmed": False, "error": reason}
        status = "rejected" if unsent else "unknown"
        self._commit(row["request_id"], status, result, [
            ("steering.failed", {"request_id": row["request_id"], "status": status, **result})])

    def finished(self, run_id: str, reason: str = "Execution ended before guidance delivery was confirmed") -> None:
        """Close admission before terminal publication; never replay pending input."""
        self._inboxes.pop(run_id, None)
        for row in self.store.all("SELECT * FROM steering_requests WHERE run_id=? AND status='pending'", (run_id,)):
            self._settle_unfinished(row, reason)

    async def dispatch(self, method: str, params: Json) -> Json:
        if method == "run.steer":
            return await self.steer(params)
        return await self.compact(params)

    async def steer(self, params: Json) -> Json:
        fields(params, {"run_id", "request_id", "prompt"})
        run_id, request_id = text(params, "run_id"), text(params, "request_id")
        prompt = self.service.redactor.text(text(params, "prompt", 32_000))
        result: Json
        previous = self.store.all("SELECT * FROM steering_requests WHERE request_id=?", (request_id,))
        if previous:
            row = previous[0]
            if (row["run_id"], row["prompt"]) != (run_id, prompt):
                raise KiraError("CONFLICT", "request_id was already used with different steering input")
            return {**json.loads(row["result"]), "status": row["status"], "request_id": request_id,
                    "run_id": run_id, "automatic_replay": False}
        run = self.store.one("runs", run_id)
        session = self.store.one("sessions", run["session_id"])
        ProjectChats(self.store).owned_session(session["project_id"], session["id"])
        if run["state"] not in {"running", "awaiting_input"}:
            raise KiraError("NOT_RUNNING", "This execution no longer accepts instructions")
        inbox = self._inboxes.get(run_id)
        if inbox is not None:
            if self.service._closing or self._scope(run_id) != inbox[1]:
                raise KiraError("CONFLICT", "Guidance no longer belongs to the admitted execution")
            count = self.store.db.execute("SELECT count(*) FROM steering_requests WHERE run_id=?", (run_id,)).fetchone()[0]
            if count >= MAX_GUIDANCE_REQUESTS:
                raise KiraError("LIMIT", "Guidance request limit reached for this execution")
            result = {"accepted": False, "status": "pending", "delivery_state": "queued",
                      "request_id": request_id, "run_id": run_id, "binding": inbox[1],
                      "automatic_replay": False, "model_consumption_confirmed": False}
            for event in self.store.steering(request_id, "pending", result, [
                    ("steering.requested", {"request_id": request_id, "content": prompt, **result})],
                    run_id=run_id, prompt=prompt):
                self.service._publish(run_id, event)
            return result
        control = self.service._run_controls.get(run_id)
        if control is None:
            raise KiraError("UNAVAILABLE", "This provider turn does not currently support steering; add the request to the queue")
        submitted = {"accepted": False, "status": "pending", "delivery_state": "submitted",
                     "transport": "turn/steer", "request_id": request_id, "run_id": run_id,
                     "automatic_replay": False, "model_consumption_confirmed": False}
        for event in self.store.steering(request_id, "pending", submitted, [
                ("steering.requested", {"content": prompt, **submitted})], run_id=run_id, prompt=prompt):
            self.service._publish(run_id, event)
        try:
            result = await asyncio.wait_for(control("steer", {"request_id": request_id, "prompt": prompt}), 30)
            current = self.store.all("SELECT * FROM steering_requests WHERE request_id=?", (request_id,))[0]
            if current["status"] != "pending":
                return {**json.loads(current["result"]), "status": current["status"], "request_id": request_id,
                        "run_id": run_id, "automatic_replay": False}
            if not result.get("accepted"):
                raise KiraError("PROVIDER_REJECTED", "Provider did not confirm steering acceptance")
            if not isinstance(result.get("turn_id"), str) or not 1 <= len(result["turn_id"]) <= 256:
                raise KiraError("DELIVERY_UNKNOWN", "Provider returned an invalid turn acknowledgment")
        except BaseException as exc:
            current = self.store.all("SELECT * FROM steering_requests WHERE request_id=?", (request_id,))[0]
            if current["status"] != "pending":
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return {**json.loads(current["result"]), "status": current["status"], "request_id": request_id,
                        "run_id": run_id, "automatic_replay": False}
            status = "rejected" if isinstance(exc, KiraError) and exc.code in {"PROVIDER_REJECTED", "NOT_RUNNING", "NOT_ACTIVE", "UNSUPPORTED", "INVALID_ARGUMENT", "INVALID_PARAMS", "LIMIT", "CONFLICT"} else "unknown"
            result = {"accepted": False, "status": status, "delivery_state": "not_delivered" if status == "rejected" else "unknown",
                      "request_id": request_id, "run_id": run_id,
                      "error": self.service.redactor.text(str(exc))[:2000], "automatic_replay": False,
                      "model_consumption_confirmed": False}
            for event in self.store.steering(request_id, status, result, [
                    ("steering.failed", {"request_id": request_id, "status": status, **result})]):
                self.service._publish(run_id, event)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise KiraError("STEERING_REJECTED" if status == "rejected" else "DELIVERY_UNKNOWN", result["error"]) from exc
        result = {"accepted": True, "request_id": request_id, "run_id": run_id, "turn_id": result["turn_id"],
                  "status": "accepted", "delivery_state": "provider_accepted", "model_consumption_confirmed": False}
        for event in self.store.steering(request_id, "accepted", result, [
                ("message.user", {"content": prompt, "source": "steer", "request_id": request_id,
                                  "delivery_state": "provider_accepted"}),
                ("steering.accepted", result)]):
            self.service._publish(run_id, event)
        return result

    async def compact(self, params: Json) -> Json:
        fields(params, {"project_id", "session_id", "request_id"})
        project_id, session_id, request_id = (text(params, key) for key in ("project_id", "session_id", "request_id"))
        session = ProjectChats(self.store).owned_session(project_id, session_id)
        project = self.store.one("projects", project_id)
        fingerprint = hashlib.sha256(encode({"method": "session.compact", "session_id": session_id, "project_id": project_id}).encode()).hexdigest()
        previous = self.store.all("SELECT * FROM session_forks WHERE request_id=?", (request_id,))
        if previous:
            row = previous[0]
            if row["fingerprint"] != fingerprint or row["method"] != "session.compact":
                raise KiraError("CONFLICT", "request_id belongs to different input")
            return {"status": row["status"], "request_id": request_id, **json.loads(row["result"]), "automatic_replay": False}
        if session["backend"] != "codex" or not session.get("backend_ref"):
            raise KiraError("UNSUPPORTED", "Native compaction requires an existing supported provider thread")
        if self.store.project_active(project_id) or self.store.reserved(project["root"]):
            raise KiraError("BUSY", "Finish active work before compacting context")
        timestamp = now()
        self.store.db.execute("INSERT INTO session_forks (request_id,fingerprint,project_id,source_session_id,root,status,result,created_at,updated_at,method) VALUES (?,?,?,?,?,'pending','{}',?,?,?)",
                              (request_id, fingerprint, project_id, session_id, project["root"], timestamp, timestamp, "session.compact"))
        task = asyncio.create_task(self._compact(request_id, session, project))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"status": "pending", "request_id": request_id, "automatic_replay": False}

    async def _compact(self, request_id: str, session: Json, project: Json) -> None:
        backend = None
        status = "unknown"
        result: Json = {}
        cancelled = False
        try:
            backend = self.service._backend(session["backend"])
            manage = getattr(backend, "manage_thread", None)
            if manage is None:
                raise KiraError("UNSUPPORTED", "Provider does not support native compaction")
            result = await manage("compact", session, {**project, "permission": "read_only"}, {"timeout_seconds": 90},
                                 provider_started=lambda pid: self.service.control_processes.register(request_id, pid))
            if not result.get("compacted"):
                raise KiraError("DELIVERY_UNKNOWN", "Provider did not confirm context compaction")
            status = "completed"
        except BaseException as exc:
            status = "failed" if backend is None or isinstance(exc, KiraError) and exc.code in {"UNSUPPORTED", "INVALID_ARGUMENT", "PROVIDER_REJECTED", "BUSY"} else "unknown"
            result = {"error": self.service.redactor.text(str(exc))[:2000]}
            cancelled = isinstance(exc, asyncio.CancelledError)
        finally:
            if backend is not None and not await self.service._finish_control(request_id, backend, require_registered=status == "completed"):
                status = "unknown"
                result = {"error": "Provider cleanup is unconfirmed; project remains reserved"}
            self.store.db.execute("UPDATE session_forks SET status=?,result=?,error=?,updated_at=? WHERE request_id=?",
                                  (status, encode(result), result.get("error"), now(), request_id))
            if hasattr(self.service, "chat_lanes") and not self.service._closing:
                await self.service.chat_lanes.after_project(project["id"])
        if cancelled:
            raise asyncio.CancelledError

    async def close(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
