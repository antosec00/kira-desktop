"""Adapters consume service-owned user guidance only at paired safe boundaries."""

from __future__ import annotations

import asyncio

from kira.domain.contracts import Json, KiraError, RunContext


class GuidanceError(ValueError):
    """An uncertain inbox receipt must stop rather than resend user input."""


class BoundaryGuidance:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.delivery = ""

    async def _call(self, action: str, values: Json) -> Json:
        if self.ctx.cancel.is_set():
            raise asyncio.CancelledError
        assert self.ctx.guidance is not None
        try:
            result = await self.ctx.guidance(action, values)
        except KiraError as exc:
            raise GuidanceError(f"Guidance boundary unavailable ({exc.code}); no input was replayed") from exc
        if not isinstance(result, dict):
            raise GuidanceError("Invalid guidance boundary receipt")
        return result

    async def take(self) -> list[Json]:
        if self.ctx.guidance is None:
            return []
        if self.delivery:
            raise GuidanceError("Previous guidance has no confirmed provider response")
        batch = await self._call("take", {})
        messages, delivery = batch.get("messages"), batch.get("delivery_id")
        if not isinstance(messages, list) or len(messages) > 32 or not isinstance(delivery, str):
            raise GuidanceError("Invalid guidance batch")
        identities: set[str] = set()
        size = 0
        for message in messages:
            if not isinstance(message, dict) or set(message) != {"request_id", "content"}:
                raise GuidanceError("Invalid guidance message")
            identity, content = message["request_id"], message["content"]
            if (not isinstance(identity, str) or not 1 <= len(identity) <= 128 or identity in identities
                    or not isinstance(content, str) or not content.strip() or "\0" in content
                    or len(content.encode()) > 32_000):
                raise GuidanceError("Invalid guidance message content")
            identities.add(identity)
            size += len(content.encode())
        if size > 256 * 1024 or bool(messages) != bool(delivery) or len(delivery) > 128:
            raise GuidanceError("Invalid guidance delivery identity or byte limit")
        self.delivery = delivery
        return [{"role": "user", "content": message["content"]} for message in messages]

    async def accepted(self) -> None:
        if not self.delivery:
            return
        receipt = await self._call("accepted", {"delivery_id": self.delivery})
        if receipt.get("accepted") is not True or receipt.get("delivery_id") != self.delivery:
            raise GuidanceError("Provider acceptance of guidance was not recorded")
        self.delivery = ""

    async def pending(self) -> bool:
        if self.ctx.guidance is None:
            return False
        result = await self._call("pending", {})
        if type(result.get("pending")) is not bool:
            raise GuidanceError("Invalid pending-guidance receipt")
        return bool(result["pending"])

    async def seal(self) -> bool:
        if self.ctx.guidance is None:
            return True
        result = await self._call("seal", {})
        if type(result.get("sealed")) is not bool:
            raise GuidanceError("Invalid final guidance boundary")
        return bool(result["sealed"])


def deferred_tool_result() -> Json:
    return {"ok": False, "executed": False, "error": {
        "code": "DEFERRED_FOR_USER_GUIDANCE",
        "message": "This tool was not started because new user guidance arrived. "
                   "Read that guidance before issuing any new tool call."}}
