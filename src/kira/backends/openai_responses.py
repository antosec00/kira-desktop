"""Bounded Responses wire primitives; no credentials or provider reasoning in errors."""

from __future__ import annotations

import json
import math
import re
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from kira.domain.contracts import Json

MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_STREAM_BYTES = 32 * 1024 * 1024
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_CATALOG_BYTES = 1024 * 1024
MAX_CALL_BYTES = 65_536
MAX_CALLS = 2048
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 64


class ResponsesError(Exception):
    """Only application-authored messages and validated machine codes are public."""

    def __init__(self, message: str, *, state: str = "interrupted", details: Json | None = None):
        super().__init__(message)
        self.state = state
        self.details = details or {}


class OutputItemValidationError(ResponsesError):
    """Local output-envelope rejection with shape-only, non-content diagnostics."""

    def __init__(self, event: Json):
        index, item = event.get("output_index"), event.get("item")
        code = ("output_item_index_type" if type(index) is not int else
                "output_item_index_range" if index < 0 else
                "output_item_limit" if index >= MAX_ITEMS else "output_item_object_type")
        kinds = {"message", "reasoning", "compaction", "function_call", "web_search_call"}
        kind = item.get("type") if isinstance(item, dict) else None
        event_type = event.get("type")
        details: Json = {"source": "kira_output_validation", "code": code,
            "event_type": event_type if isinstance(event_type, str) and event_type in {
                "response.output_item.added", "response.output_item.done"} else "other",
            "output_index_type": type(index).__name__, "max_output_items": MAX_ITEMS,
            "item_object_type": type(item).__name__,
            "item_type": kind if isinstance(kind, str) and kind in kinds else "other"}
        if type(index) is int and -1_000_000 <= index <= 1_000_000:
            details["output_index"] = index
        super().__init__("OpenAI returned an invalid output item index or object", details=details)


def encoded(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ResponsesError("Responses data must be finite UTF-8 JSON") from exc


def _constant(value: str) -> Any:
    raise ValueError("Nonfinite JSON")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON")
    return number


def decode(raw: bytes | str) -> Json:
    try:
        result = json.loads(raw, parse_constant=_constant, parse_float=_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ResponsesError("Malformed Responses JSON") from exc
    if not isinstance(result, dict):
        raise ResponsesError("Responses event must be an object")
    return result


async def sse(chunks: AsyncIterable[bytes]) -> AsyncIterator[Json]:
    """Decode LF/CRLF SSE across arbitrary UTF-8 chunks with strict byte bounds."""
    buffer = bytearray()
    lines: list[bytes] = []
    event_name: bytes | None = None
    event_bytes = total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            raise ResponsesError("Responses stream exceeded its byte limit")
        buffer.extend(chunk)
        while b"\n" in buffer:
            raw, _, tail = buffer.partition(b"\n")
            buffer = bytearray(tail)
            line = bytes(raw).removesuffix(b"\r")
            event_bytes += len(raw) + 1
            if event_bytes > MAX_EVENT_BYTES:
                raise ResponsesError("Responses event exceeded its byte limit")
            if not line:
                if lines:
                    data = b"\n".join(lines)
                    if data.strip() != b"[DONE]":
                        item = decode(data)
                        if event_name and item.get("type") != event_name.decode("ascii", "replace"):
                            raise ResponsesError("Responses event name disagrees with its JSON type")
                        yield item
                lines, event_name, event_bytes = [], None, 0
            elif line.startswith(b"data:"):
                lines.append(line[5:].removeprefix(b" "))
            elif line.startswith(b"event:"):
                event_name = line[6:].strip()
        if len(buffer) + event_bytes > MAX_EVENT_BYTES:
            raise ResponsesError("Responses event exceeded its byte limit")
    if buffer.strip() or lines:
        raise ResponsesError("Responses connection ended with an incomplete event")


def machine_value(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]:-]{1,160}", value):
        return value
    return None


def provider_failure(payload: Json, *, status: int | None = None,
                     request_id: str | None = None, state: str = "failed") -> ResponsesError:
    """Keep status/body shape/codes, never echo potentially reflected secrets."""
    details: Json = {"body_shape": "error" if isinstance(payload.get("error"), dict)
                     else "detail" if "detail" in payload else "other"}
    if status is not None:
        details["http_status"] = status
    if safe_request := machine_value(request_id):
        details["request_id"] = safe_request
    error = payload.get("error", {})
    if isinstance(error, dict):
        for key in ("code", "param", "type"):
            if safe := machine_value(error.get(key)):
                details[key] = safe
    code = details.get("code", "request_failed")
    message = f"OpenAI request failed ({code}"
    if status is not None:
        message += f", HTTP {status}"
    message += ")"
    return ResponsesError(message, state=state, details=details)


class OutputItems:
    """Retain finalized stream items until a successful response terminal exists.

    Some subscription responses leave terminal ``output`` empty and publish the
    complete items only through ``response.output_item.done``. This accumulator
    never executes tools or publishes reasoning; it only reconciles native data.
    """

    def __init__(self) -> None:
        self.added: dict[int, tuple[str, str | None]] = {}
        self.done: dict[int, Json] = {}
        self.streamed: set[int] = set()
        self.bytes = 0

    @staticmethod
    def _identity(item: Json) -> tuple[str, str | None]:
        kind, identity = item.get("type"), item.get("id")
        if not isinstance(kind, str) or not 1 <= len(kind) <= 128 or (
                identity is not None and (not isinstance(identity, str) or not 1 <= len(identity) <= 256)):
            raise ResponsesError("OpenAI returned an invalid output item identity")
        return kind, identity

    def observe(self, event: Json) -> None:
        index, item = event.get("output_index"), event.get("item")
        if type(index) is not int or not 0 <= index < MAX_ITEMS or not isinstance(item, dict):
            raise OutputItemValidationError(event)
        identity = self._identity(item)
        previous = self.added.get(index)
        if previous is not None and previous != identity:
            raise ResponsesError("OpenAI output item identity changed within the stream")
        if event.get("type") == "response.output_item.added":
            if index in self.done and self._identity(self.done[index]) != identity:
                raise ResponsesError("OpenAI output item identity conflicts with its completed item")
            self.added[index] = identity
            return
        if item.get("status") not in {None, "completed"}:
            raise ResponsesError("OpenAI output item was not completed")
        if index in self.done:
            if encoded(self.done[index]) != encoded(item):
                raise ResponsesError("OpenAI repeated a completed output index with different content")
            return
        size = len(encoded(item))
        if self.bytes + size > MAX_STREAM_BYTES:
            raise ResponsesError("OpenAI completed output items exceed their byte limit")
        self.bytes += size
        self.done[index] = item

    def finish(self, terminal: Json) -> Json:
        output = terminal.get("output")
        if not isinstance(output, list) or len(output) > MAX_ITEMS or any(
                not isinstance(item, dict) for item in output):
            raise ResponsesError("OpenAI returned an invalid terminal output list")
        if not output and self.done:
            if set(self.done) != set(range(len(self.done))):
                raise ResponsesError("OpenAI completed output items have missing indexes")
            output = [self.done[index] for index in range(len(self.done))]
        indexes = set(range(len(output)))
        if (set(self.added) | set(self.done) | self.streamed) - indexes:
            raise ResponsesError("OpenAI response completed before all output items were finalized")
        for index, identity in self.added.items():
            if self._identity(output[index]) != identity:
                raise ResponsesError("OpenAI terminal output identity conflicts with its stream")
        for index, item in self.done.items():
            if encoded(output[index]) != encoded(item):
                raise ResponsesError("OpenAI terminal output conflicts with its completed stream item")
        return {**terminal, "output": output}
