"""Durable information questions, independent of permission approvals.

Only redacted question/answer events are persisted. Provider correlation keys are
kept in memory; they cannot address another run or be exposed through IPC.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from kira.domain.contracts import TERMINAL_STATES, Json, KiraError
from kira.domain.questions import MAX_INPUT_REQUESTS, validate_answers, validate_request
from kira.service.storage import encode, new_id

if TYPE_CHECKING:
    from kira.service.server import Service

METHODS = {"input.list", "input.answer"}
# How an adapter handed a retrieved answer to its provider; never proof of model use.
DELIVERY_TRANSPORTS = frozenset({"json-rpc-response", "turn/steer", "responses-http",
                                 "ollama-chat", "claude-code-mcp", "deepseek-web-messages"})
EVENTS = {"input.requested", "input.answered", "input.received", "input.closed", "input.delivery_failed", "input.delivered"}


class Questions:
    def __init__(self, service: "Service") -> None:
        self.service = service
        self.lock = asyncio.Lock()
        self.correlations: dict[tuple[str, str], tuple[str, Json]] = {}
        self.waiters: dict[tuple[str, str], asyncio.Future[None]] = {}

    def _state(self, run_id: str, request_id: str) -> Json:
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise KiraError("INVALID_ARGUMENT", "Invalid information question identifier")
        records = self.service.store.all(
            "SELECT type,payload FROM events WHERE run_id=? AND type IN "
            "('input.requested','input.answered','input.received','input.closed','input.delivery_failed','input.delivered') "
            "AND json_extract(payload,'$.request_id')=? ORDER BY seq", (run_id, request_id))
        state: Json = {}
        for record in records:
            payload = record["payload"]
            if record["type"] == "input.requested":
                state = {"run_id": run_id, **payload, "received": False, "delivered": False}
            elif state and record["type"] == "input.delivery_failed":
                state.update(payload, status="closed", received=False, delivered=False)
            elif state and record["type"] == "input.delivered":
                state["delivered"] = True
                if "transport" in payload:
                    state["delivery_transport"] = payload["transport"]
            elif state and record["type"] == "input.received":
                state["received"] = True
            elif state:
                state.update(payload)
        if not state:
            raise KiraError("NOT_FOUND", "Question not found in this run")
        return state

    @staticmethod
    def _result(state: Json) -> Json:
        return {"ok": True, **{key: state[key] for key in (
            "request_id", "status", "blocking", "answers", "received", "delivered", "delivery_transport", "reason") if key in state}}

    async def dispatch(self, method: str, params: Json) -> Json:
        from kira.service.server import fields, integer, string
        if method == "input.list":
            fields(params, {"project_id", "offset", "limit"})
            project_id = string(params, "project_id", maximum=128)
            self.service.store.one("projects", project_id)
            offset = integer(params, "offset", 0, 0, 2**31 - 1)
            limit = integer(params, "limit", 100, 1, 100)
            return self.list(project_id, offset, limit)
        fields(params, {"run_id", "request_id", "answers"})
        return await self.answer(string(params, "run_id", maximum=128),
                                 string(params, "request_id", maximum=128), params.get("answers"))

    def list(self, project_id: str, offset: int, limit: int) -> Json:
        # Read only question events, never a project's transcript. Closed/answered
        # history is bounded separately so a long history cannot hide open forms.
        rows = self.service.store.all(
            "WITH requested AS (SELECT e.run_id,e.seq,e.timestamp,"
            "json_extract(e.payload,'$.request_id') request_id, EXISTS(SELECT 1 FROM events f "
            "WHERE f.run_id=e.run_id AND f.type IN ('input.answered','input.closed','input.delivery_failed') "
            "AND json_extract(f.payload,'$.request_id')=json_extract(e.payload,'$.request_id')) finished "
            "FROM events e JOIN runs r ON r.id=e.run_id JOIN sessions s ON s.id=r.session_id "
            "WHERE s.project_id=? AND e.type='input.requested'), "
            "history AS (SELECT * FROM requested WHERE finished=1 "
            "ORDER BY timestamp DESC,run_id,seq DESC LIMIT 100), "
            "visible AS (SELECT * FROM requested WHERE finished=0 UNION ALL SELECT * FROM history) "
            "SELECT * FROM visible ORDER BY timestamp DESC,run_id,seq DESC LIMIT ? OFFSET ?",
            (project_id, limit + 1, offset))
        requests: list[Json] = []
        size = 0
        for row in rows[:limit]:
            state = self._state(row["run_id"], row["request_id"])
            item_size = len(encode(state).encode("utf-8"))
            if requests and size + item_size > 700_000:
                break
            requests.append(state)
            size += item_size
        return {"requests": requests, "has_more": len(rows) > len(requests),
                "next_offset": offset + len(requests)}

    def _active(self, run_id: str, cancel: asyncio.Event) -> bool:
        return (not cancel.is_set() and
                self.service.store.one("runs", run_id)["state"] not in TERMINAL_STATES | {"cancelling"})

    async def request(self, run_id: str, request: Json, cancel: asyncio.Event) -> Json:
        normalized = validate_request(request)
        provider_id = normalized["request_id"]
        # Correlation identifiers must retain their meaning after redaction.
        cleaned = self.service.redactor.clean({**normalized, "request_id": "internal"})
        if any(a["id"] != b["id"] for a, b in zip(normalized["questions"], cleaned["questions"], strict=True)):
            raise KiraError("INVALID_ARGUMENT", "Question identifier conflicts with redaction")
        cleaned = validate_request(cleaned)
        async with self.lock:
            if not self._active(run_id, cancel):
                raise KiraError("CANCELLED", "Run is no longer accepting questions")
            previous = self.correlations.get((run_id, provider_id))
            if previous:
                request_id, original = previous
                if original != cleaned:
                    raise KiraError("CONFLICT", "Provider question id was reused with different input")
                state = self._state(run_id, request_id)
            else:
                count = self.service.store.db.execute(
                    "SELECT COUNT(*) FROM events WHERE run_id=? AND type='input.requested'", (run_id,)).fetchone()[0]
                if count >= MAX_INPUT_REQUESTS:
                    raise KiraError("OUTPUT_LIMIT", "Run reached its information question limit")
                request_id = new_id()
                await self.service.emit(run_id, "input.requested", {
                    "request_id": request_id, "questions": cleaned["questions"],
                    "blocking": cleaned["blocking"], "status": "pending"})
                self.correlations[(run_id, provider_id)] = (request_id, cleaned)
                state = self._state(run_id, request_id)
            if state["blocking"] and state["status"] == "pending":
                if self.service.store.one("runs", run_id)["state"] == "running":
                    await self.service.emit(run_id, "run.state", {"state": "awaiting_input"})
        if state["blocking"]:
            return await self.read(run_id, request_id, True, cancel)
        return self._result(state)

    async def answer(self, run_id: str, request_id: str, answers: Any) -> Json:
        async with self.lock:
            state = self._state(run_id, request_id)
            normalized = validate_answers(state["questions"], answers)
            normalized = validate_answers(state["questions"], self.service.redactor.clean(normalized))
            if "answers" in state:
                if state["answers"] != normalized:
                    raise KiraError("CONFLICT", "This question already has a different answer")
                return {"accepted": True, "request": state}
            if (state["status"] != "pending" or self.service.store.one("runs", run_id)["state"]
                    in TERMINAL_STATES | {"cancelling"}):
                raise KiraError("CONFLICT", "This question is closed")
            await self.service.emit(run_id, "input.answered", {
                "request_id": request_id, "answers": normalized, "status": "answered"})
            # emit() commits synchronously before publishing; wake only afterwards.
            self._wake(run_id, request_id)
            return {"accepted": True, "request": self._state(run_id, request_id)}

    async def delivered(self, run_id: str, payload: Json) -> None:
        """Persist the adapter's explicit confirmation of successful delivery."""
        from kira.service.server import fields, string
        fields(payload, {"request_id", "transport"})
        request_id = string(payload, "request_id", maximum=128)
        transport = payload.get("transport")
        if transport is not None and (not isinstance(transport, str)
                                      or transport not in DELIVERY_TRANSPORTS):
            raise KiraError("INVALID_ARGUMENT", "Unsupported answer delivery transport")
        async with self.lock:
            state = self._state(run_id, request_id)
            if state["status"] != "answered" or not state["received"]:
                raise KiraError("CONFLICT", "Only a retrieved answer can be confirmed as delivered")
            if not state["delivered"]:
                await self.service._emit_cleaned(run_id, "input.delivered", {"request_id": request_id,
                    **({"transport": transport} if transport is not None else {})})

    async def delivery_failed(self, run_id: str, payload: Json) -> None:
        """An adapter read is not proof that the provider accepted the answer."""
        from kira.service.server import fields, string
        fields(payload, {"request_id", "reason"})
        request_id = string(payload, "request_id", maximum=128)
        reason = string(payload, "reason", maximum=2048)
        async with self.lock:
            self._state(run_id, request_id)
            if self.service.store.db.execute(
                    "SELECT 1 FROM events WHERE run_id=? AND type='input.delivery_failed' "
                    "AND json_extract(payload,'$.request_id')=? LIMIT 1", (run_id, request_id)).fetchone():
                return
            cleaned = self.service.redactor.clean({"request_id": request_id, "reason": reason})
            await self.service._emit_cleaned(run_id, "input.delivery_failed", cleaned)
            self._wake(run_id, request_id)
            await self._restore_state(run_id)

    def _wake(self, run_id: str, request_id: str) -> None:
        waiter = self.waiters.get((run_id, request_id))
        if waiter is not None and not waiter.done():
            waiter.set_result(None)

    async def _close(self, run_id: str, request_id: str, reason: str) -> None:
        state = self._state(run_id, request_id)
        if state["status"] != "closed" and not state["delivered"]:
            await self.service.emit(run_id, "input.closed", {
                "request_id": request_id, "status": "closed", "reason": reason})
        self._wake(run_id, request_id)

    async def _restore_state(self, run_id: str) -> None:
        if self.service.store.one("runs", run_id)["state"] != "awaiting_input":
            return
        blocking = self.service.store.db.execute(
            "SELECT 1 FROM events e WHERE e.run_id=? AND e.type='input.requested' "
            "AND json_extract(e.payload,'$.blocking')=1 AND NOT EXISTS(SELECT 1 FROM events f "
            "WHERE f.run_id=e.run_id AND f.type IN ('input.received','input.closed','input.delivery_failed') "
            "AND json_extract(f.payload,'$.request_id')=json_extract(e.payload,'$.request_id')) LIMIT 1",
            (run_id,)).fetchone()
        if not blocking and not any(key[0] == run_id for key in self.service.approvals):
            await self.service.emit(run_id, "run.state", {"state": "running"})

    async def read(self, run_id: str, request_id: str, wait: bool, cancel: asyncio.Event) -> Json:
        if type(wait) is not bool:
            raise KiraError("INVALID_ARGUMENT", "wait must be a boolean")
        waiter: asyncio.Future[None] | None = None
        stop: asyncio.Task[bool] | None = None
        try:
            while True:
                async with self.lock:
                    state = self._state(run_id, request_id)
                    if not self._active(run_id, cancel):
                        await self._close(run_id, request_id, "run_ended")
                        state = self._state(run_id, request_id)
                    if state["status"] == "answered":
                        if not state["received"]:
                            await self.service.emit(run_id, "input.received", {"request_id": request_id})
                            state["received"] = True
                            await self._restore_state(run_id)
                        return self._result(state)
                    if state["status"] == "closed" or not wait:
                        return self._result(state)
                    waiter = self.waiters.get((run_id, request_id))
                    if waiter is None or waiter.done():
                        waiter = asyncio.get_running_loop().create_future()
                        self.waiters[(run_id, request_id)] = waiter
                stop = asyncio.create_task(cancel.wait())
                waiting: set[asyncio.Future[Any]] = {waiter, stop}
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
                stop = None
        except asyncio.CancelledError:
            async with self.lock:
                await self._close(run_id, request_id, "provider_request_cancelled")
                await self._restore_state(run_id)
            raise
        finally:
            if stop is not None:
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
            # Do not cancel a shared future: another provider reader may be waiting.
            if waiter is not None and waiter.done():
                self.waiters.pop((run_id, request_id), None)

    async def close_run(self, run_id: str, reason: str) -> None:
        async with self.lock:
            for row in self.service.store.all(
                    "SELECT json_extract(payload,'$.request_id') request_id FROM events "
                    "WHERE run_id=? AND type='input.requested'", (run_id,)):
                await self._close(run_id, row["request_id"], reason)
            for key in [key for key in self.correlations if key[0] == run_id]:
                self.correlations.pop(key, None)

    async def recover(self) -> None:
        for row in self.service.store.all(
                "SELECT DISTINCT e.run_id FROM events e WHERE e.type='input.requested' "
                "AND NOT EXISTS(SELECT 1 FROM events f WHERE f.run_id=e.run_id "
                "AND f.type IN ('input.closed','input.delivered','input.delivery_failed') "
                "AND json_extract(f.payload,'$.request_id')=json_extract(e.payload,'$.request_id'))"):
            await self.close_run(row["run_id"], "service_restarted")
