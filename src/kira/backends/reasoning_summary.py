"""Bounded display of the reasoning text a provider shows its users, never hidden reasoning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard

from kira.domain.contracts import Json, RunContext

MAX_SUMMARY_BYTES = 65_536
MAX_SUMMARY_PARTS = 64
MAX_SUMMARY_ID = 256


def identity(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and 0 < len(value) <= MAX_SUMMARY_ID


def index(value: Any) -> TypeGuard[int]:
    return type(value) is int and 0 <= value < MAX_SUMMARY_PARTS


def clipped(value: str, limit: int) -> tuple[str, bool]:
    # No unchecked surrogate from optional provider metadata may interrupt work.
    raw = value.encode("utf-8", errors="replace")
    return raw[:max(0, limit)].decode("utf-8", errors="ignore"), len(raw) > limit


@dataclass
class SummaryPart:
    item_id: str
    text: str = ""
    complete: bool = False
    truncated: bool = False
    published: tuple[str, bool, bool] | None = None


class PublicSummaries:
    """One run's optional display state, separate from native conversation state."""

    def __init__(self, ctx: RunContext, provider: str):
        self.ctx = ctx
        self.provider = provider
        self.parts: dict[tuple[str, int], SummaryPart] = {}
        self.bindings: dict[int, str] = {}
        self.scope = -1
        self.bytes = 0
        self.available = False
        self.started = False
        self.unsupported = False
        self.limits: set[str] = set()

    def new_response(self, scope: int) -> None:
        self.scope = scope
        self.bindings.clear()

    async def status(self, status: str, reason: str | None = None) -> None:
        self.started = True
        self.unsupported = status == "unsupported"
        await self.ctx.emit("reasoning.summary.status", {
            "provider": self.provider, "source": "provider_summary", "status": status,
            **({"reason": reason} if reason else {})})

    async def limit(self, reason: str) -> None:
        if reason in {"summary_item_limit", "summary_byte_limit", "summary_event_limit"} and reason not in self.limits:
            self.limits.add(reason)
            await self.status("unavailable", reason)

    def _part(self, key: str, part_index: int) -> SummaryPart | None:
        pair = (key, part_index)
        part = self.parts.get(pair)
        if part is None and len(self.parts) < MAX_SUMMARY_PARTS:
            # Application IDs stay bounded and never carry provider-controlled text.
            part = SummaryPart(f"summary:{len(self.parts)}")
            self.parts[pair] = part
        return part

    async def update(self, key: str, part_index: Any, text: Any, *, complete: bool,
                     delta: bool = False, truncated: bool = False) -> None:
        if type(part_index) is int and part_index >= MAX_SUMMARY_PARTS:
            await self.limit("summary_item_limit")
            return
        if not index(part_index) or not isinstance(text, str):
            return
        part = self._part(key, part_index)
        if part is None:
            await self.limit("summary_item_limit")
            return
        if delta and part.complete:
            return
        if truncated and not delta and part.text.startswith(text):
            text = part.text
        previous_text = part.text
        old_size = len(part.text.encode("utf-8"))
        remaining = MAX_SUMMARY_BYTES - self.bytes + (0 if delta else old_size)
        bounded, cut = clipped(text, remaining)
        if delta:
            part.text += bounded
        else:
            part.text = bounded
        self.bytes += len(part.text.encode("utf-8")) - old_size
        part.truncated = part.truncated or cut or truncated
        part.complete = part.complete or complete
        payload: Json = {"item_id": part.item_id, "provider": self.provider,
                        "source": "provider_summary"}
        if bounded:
            self.available = True
        if cut:
            await self.limit("summary_byte_limit")
        if delta and bounded:
            await self.ctx.emit("reasoning.summary.delta", {**payload, "content": bounded})
        elif not complete and part.text != previous_text:
            if part.text.startswith(previous_text):
                await self.ctx.emit("reasoning.summary.delta", {
                    **payload, "content": part.text[len(previous_text):]})
            else:
                await self.ctx.emit("reasoning.summary.completed", {
                    **payload, "content": part.text, "complete": False,
                    "truncated": part.truncated})
        if complete:
            stamp = (part.text, part.complete, part.truncated)
            if stamp != part.published and (part.text or part.truncated):
                await self.ctx.emit("reasoning.summary.completed", {
                    **payload, "content": part.text, "complete": True,
                    "truncated": part.truncated})
                part.published = stamp

    async def openai_item(self, item: Json, output_index: Any, *, complete: bool) -> None:
        if not index(output_index) or item.get("type") != "reasoning" or not identity(item.get("id")):
            return
        item_id = item["id"]
        previous = self.bindings.get(output_index)
        if previous is not None and previous != item_id:
            return
        if item_id in self.bindings.values() and previous != item_id:
            return
        self.bindings[output_index] = item_id
        summary = item.get("summary")
        if not isinstance(summary, list):
            return
        if len(summary) > MAX_SUMMARY_PARTS:
            await self.limit("summary_item_limit")
        for part_index, part in enumerate(summary[:MAX_SUMMARY_PARTS]):
            if isinstance(part, dict) and part.get("type") == "summary_text":
                await self.update(f"{self.scope}:{item_id}", part_index, part.get("text"),
                                  complete=complete)

    async def openai_event(self, event: Json) -> None:
        kind = event.get("type")
        if not isinstance(kind, str):
            return
        if kind in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            if isinstance(item, dict):
                await self.openai_item(item, event.get("output_index"),
                                      complete=kind == "response.output_item.done")
            return
        if kind not in {"response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
                        "response.reasoning_summary_part.done"}:
            return
        output_index, part_index = event.get("output_index"), event.get("summary_index")
        item_id = event.get("item_id")
        if not index(output_index) or not identity(item_id) or self.bindings.get(output_index) != item_id:
            return
        if type(part_index) is int and part_index >= MAX_SUMMARY_PARTS:
            await self.limit("summary_item_limit")
            return
        if not index(part_index):
            return
        if kind == "response.reasoning_summary_part.done":
            part = event.get("part")
            if not isinstance(part, dict) or part.get("type") != "summary_text":
                return
            text = part.get("text")
        else:
            text = event.get("delta" if kind.endswith(".delta") else "text")
        await self.update(f"{self.scope}:{item_id}", part_index, text,
                          complete=not kind.endswith(".delta"), delta=kind.endswith(".delta"))

    async def codex_event(self, method: str, params: Json) -> None:
        if isinstance(params.get("summaryLimit"), str):
            await self.limit(params["summaryLimit"])
        if method in {"item/started", "item/completed"}:
            item = params.get("item")
            if not isinstance(item, dict) or item.get("type") != "reasoning" or not identity(item.get("id")):
                return
            summary = item.get("summary")
            if isinstance(summary, list):
                if len(summary) > MAX_SUMMARY_PARTS:
                    await self.limit("summary_item_limit")
                for part_index, text in enumerate(summary[:MAX_SUMMARY_PARTS]):
                    await self.update(item["id"], part_index, text, complete=method == "item/completed",
                                      truncated=item.get("summaryTruncated") is True)
        elif method == "item/reasoning/summaryTextDelta" and identity(params.get("itemId")):
            await self.update(params["itemId"], params.get("summaryIndex"), params.get("delta"),
                              delta=True, complete=False, truncated=params.get("summaryTruncated") is True)

    async def finish(self) -> None:
        if not self.started and not self.available:
            return
        for part in self.parts.values():
            if part.text and part.published is None:
                await self.ctx.emit("reasoning.summary.completed", {
                    "item_id": part.item_id, "provider": self.provider, "source": "provider_summary",
                    "content": part.text, "complete": part.complete, "truncated": part.truncated})
                part.published = (part.text, part.complete, part.truncated)
        if not self.available and not self.unsupported and not self.limits:
            await self.status("unavailable", "no_public_summary")


@dataclass
class CodexSummaryScope:
    parts: dict[tuple[str, int], str]
    limits: set[str]
    bytes: int = 0
    events: int = 0


class CodexSummaryFilter:
    """Strip private fields before queueing, with separate bounded early scopes.

    Notifications may arrive before turn/start acknowledges the actual turn ID.
    Keep at most eight independently bounded scopes until that ID is known; a
    foreign scope cannot spend the active turn's text, item or event allowance.
    """

    def __init__(self) -> None:
        self.scopes: dict[tuple[str, str], CodexSummaryScope] = {}
        self.thread_id: str | None = None
        self.turn_id: str | None = None

    @property
    def bytes(self) -> int:
        return sum(scope.bytes for scope in self.scopes.values())

    def bind_thread(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.scopes = {key: value for key, value in self.scopes.items() if key[0] == thread_id}

    def bind_turn(self, thread_id: str, turn_id: str) -> None:
        self.thread_id, self.turn_id = thread_id, turn_id
        self.scopes = {key: value for key, value in self.scopes.items() if key == (thread_id, turn_id)}

    def _scope(self, params: Json) -> CodexSummaryScope | None:
        key = (params["threadId"], params["turnId"])
        if (self.thread_id is not None and key[0] != self.thread_id) or (
                self.turn_id is not None and key[1] != self.turn_id):
            return None
        scope = self.scopes.get(key)
        if scope is None and len(self.scopes) < 8:
            scope = CodexSummaryScope({}, set())
            self.scopes[key] = scope
        return scope

    @staticmethod
    def _limit(scope: CodexSummaryScope, clean: Json, reason: str) -> Json | None:
        if reason in scope.limits:
            return None
        scope.limits.add(reason)
        return {"method": "kira/reasoningSummaryLimit", "params": {**clean, "summaryLimit": reason}}

    @staticmethod
    def _text(scope: CodexSummaryScope, key: tuple[str, int], value: Any,
              *, delta: bool) -> tuple[str, bool] | None:
        if not isinstance(value, str):
            return None
        previous = scope.parts.get(key, "")
        old_size = len(previous.encode("utf-8"))
        text, cut = clipped(value, MAX_SUMMARY_BYTES - scope.bytes + (0 if delta else old_size))
        current = previous + text if delta else text
        scope.parts[key] = current
        scope.bytes += len(current.encode("utf-8")) - old_size
        return text, cut

    def event(self, value: Json) -> Json | None:
        if "id" in value:
            return None
        method, params = value.get("method"), value.get("params", {})
        if not isinstance(params, dict) or not all(identity(params.get(key)) for key in ("threadId", "turnId")):
            return None
        if method not in {"item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
                          "item/started", "item/completed"}:
            return None
        scope = self._scope(params)
        if scope is None:
            return None
        clean: Json = {key: params[key] for key in ("threadId", "turnId")}
        if scope.events >= 512:
            return self._limit(scope, clean, "summary_event_limit")
        if method in {"item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded"}:
            item_id, part_index = params.get("itemId"), params.get("summaryIndex")
            if not identity(item_id) or type(part_index) is not int or part_index < 0:
                return None
            key = (item_id, part_index)
            if part_index >= MAX_SUMMARY_PARTS or key not in scope.parts and len(scope.parts) >= MAX_SUMMARY_PARTS:
                return self._limit(scope, clean, "summary_item_limit")
            clean.update(itemId=item_id, summaryIndex=part_index)
            if method == "item/reasoning/summaryTextDelta":
                text = self._text(scope, key, params.get("delta"), delta=True)
                if text is None:
                    return None
                clean.update(delta=text[0], summaryTruncated=text[1])
                if text[1]:
                    clean["summaryLimit"] = "summary_byte_limit"
            else:
                scope.parts.setdefault(key, "")
        else:
            item = params.get("item")
            if not isinstance(item, dict) or item.get("type") != "reasoning" or not identity(item.get("id")):
                return None
            clean_item: Json = {"id": item["id"], "type": "reasoning", "summary": []}
            summary = item.get("summary")
            if not isinstance(summary, list):
                return None
            if len(summary) > MAX_SUMMARY_PARTS:
                clean["summaryLimit"] = "summary_item_limit"
            for part_index, part in enumerate(summary[:MAX_SUMMARY_PARTS]):
                key = (item["id"], part_index)
                if key not in scope.parts and len(scope.parts) >= MAX_SUMMARY_PARTS:
                    clean["summaryLimit"] = "summary_item_limit"
                    break
                text = self._text(scope, key, part, delta=False)
                # Invalid optional entries never shift later indices.
                clean_item["summary"].append(text[0] if text else None)
                if text and text[1]:
                    clean_item["summaryTruncated"] = True
                    clean.setdefault("summaryLimit", "summary_byte_limit")
            clean["item"] = clean_item
        scope.events += 1
        return {"method": method, "params": clean}
