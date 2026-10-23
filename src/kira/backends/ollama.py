"""Native Ollama inference and a small service-owned tool loop.

Only complete terminal messages can cause tools to execute. Thinking fields are
returned to Ollama in memory when required, but never published as UI events.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import math
import uuid
import weakref
from collections.abc import AsyncIterable, AsyncIterator, Awaitable
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx

from kira.backends.client_tools import (
    MAX_CLIENT_TOOL_BYTES,
    ClientToolBridge,
    ClientToolError,
    validate_tools,
)
from kira.backends.guidance import BoundaryGuidance, deferred_tool_result
from kira.domain.contracts import HOST_PERMISSIONS, WRITE_PERMISSIONS, Json, KiraError, RunContext
from kira.domain.questions import question_tool_specs, validate_request

T = TypeVar("T")
MAX_RECORD_BYTES = 1_048_576
MAX_METADATA_BYTES = 262_144
METADATA_TIMEOUT = 10.0
DISCOVERY_TIMEOUT = 20.0
OLLAMA_CHAT_READ_TIMEOUT = 600.0
MAX_MODELS = 64
MAX_MODEL_NAME_BYTES = 512
MAX_DETAIL_BYTES = 32_768
MAX_DISCOVERY_BYTES = 512_000
MAX_CACHE_BYTES = 512_000
MAX_BROWSER_IMAGE_BYTES = 2 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 65_536
MAX_ERROR_DETAIL_CHARS = 500
MAX_TOOL_PROBES = 256
CAPABILITIES = {
    "streaming": True, "tools": False, "cancellation": True, "resume": True,
    "usage": True, "images": False, "context_controls": True,
    "steering": True,
}


def _tool(name: str, description: str, properties: Json, required: list[str]) -> Json:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }}


TOOL_SCHEMAS = [
    _tool("list_files", "List files.", {"path": {"type": "string"}}, []),
    _tool("search_files", "Find literal text in files.",
          {"path": {"type": "string"}, "query": {"type": "string"}}, ["query"]),
    _tool("read_file", "Read a file and its SHA256.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("write_file", "Write file atomically. For existing files use SHA256 from read_file; "
          "null expected_sha256 creates a new file only.",
          {"path": {"type": "string"}, "content": {"type": "string"},
           "expected_sha256": {"type": ["string", "null"]}},
          ["path", "content", "expected_sha256"]),
    _tool("run_command", "Run an argv array in the project as the host user; no implicit shell. "
          "Inspect exit_code, stdout and stderr to verify the result.",
          {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
           "timeout": {"type": "number", "exclusiveMinimum": 0}}, ["argv"]),
]


def local_tool_schemas(permission: str) -> list[Json]:
    """Offer the effective service scope without mutating shared schemas."""
    path_scope = (
        " Absolute or project-relative paths; external paths allowed as current OS user."
        if permission == "full_access" else
        " Project paths only."
    )
    result = []
    for tool in TOOL_SCHEMAS:
        function = tool["function"]
        name = function["name"]
        if name == "write_file" and permission not in WRITE_PERMISSIONS:
            continue
        if name == "run_command" and permission not in HOST_PERMISSIONS:
            continue
        description = function["description"]
        if name != "run_command":
            description += path_scope
        result.append({**tool, "function": {**function, "description": description}})
    return result


def _browser_image_tool(name: str) -> bool:
    return (name == "browser.screenshot"
            or name in {"computer.observe", "computer.act"})


def _browser_tool_images(messages: list[Json]) -> None:
    """Keep only the two latest typed browser/computer images in model context."""
    images = [(index, len(message["images"])) for index, message in enumerate(messages)
              if message.get("role") == "tool" and isinstance(message.get("tool_name"), str)
              and message["tool_name"] in {"browser__screenshot", "computer__observe", "computer__act"}
              and isinstance(message.get("images"), list)]
    total = sum(count for _, count in images)
    for index, count in images:
        if total <= 2:
            break
        messages[index].pop("images", None)
        total -= count


def _ollama_image(image: Any) -> str:
    """Validate Kira's owned PNG data URL and return Ollama's base64 image value."""
    prefix = "data:image/png;base64,"
    if not isinstance(image, dict) or image.get("type") != "input_image" \
            or not isinstance(image.get("image_url"), str) or not image["image_url"].startswith(prefix):
        raise StreamError("Browser screenshot image delivery returned invalid pixels")
    value = image["image_url"][len(prefix):]
    if len(value) > ((MAX_BROWSER_IMAGE_BYTES + 2) // 3) * 4:
        raise StreamError("Browser screenshot exceeds the 2 MiB image limit")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StreamError("Browser screenshot image delivery returned invalid pixels") from exc
    if not raw or len(raw) > MAX_BROWSER_IMAGE_BYTES:
        raise StreamError("Browser screenshot exceeds the 2 MiB image limit")
    return value


class StreamError(Exception):
    """A malformed or incomplete native NDJSON response."""


class OllamaMissingUserQuery(StreamError):
    """The model's chat template rejected a tool continuation without a user query."""


class OllamaHTTPError(httpx.HTTPStatusError):
    """An HTTP rejection with the bounded `error` text of Ollama's response body."""

    def __init__(self, response: httpx.Response, detail: str) -> None:
        super().__init__(f"Ollama HTTP {response.status_code}" + (f" — {detail}" if detail else ""),
                         request=response.request, response=response)
        self.safe_detail = detail


# Tool-probe outcomes last as long as the service, which runs one event loop
# for its lifetime; a separate event loop starts without another's outcomes.
_TOOL_PROBES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, ...], bool]] = (
    weakref.WeakKeyDictionary())


def _tool_probes() -> dict[tuple[str, ...], bool]:
    return _TOOL_PROBES.setdefault(asyncio.get_running_loop(), {})


def _revision(detail: Json) -> str:
    """Fingerprint of a model's catalog detail, which changes when the model does."""
    return hashlib.sha256(json.dumps(detail, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


# Appended to the system instructions of Ollama turns that carry verified tools.
TOOL_CALL_GUIDANCE = (
    "\nWhen a request requires inspection or an action, call the relevant registered "
    "Kira tool instead of only announcing what you plan to do. After each tool result, "
    "continue with the next useful tool call while work remains. Finish with a result "
    "only when the requested work is complete, or state the concrete blocker. Never "
    "claim an action or progress without a corresponding tool result.")
# Appended when the chat has no callable tools at all.
NO_TOOLS_GUIDANCE = (
    "\nNo callable tools are enabled for this chat. Do not emit XML-like tool-call "
    "text, shell commands or simulated tool results, and never claim that you opened "
    "a page or changed anything. If the user asks you to act, explain that tools are "
    "disabled in the chat settings.")


def _error_text(body: bytes) -> str:
    """The `error` text of an Ollama error body on one printable line, or ""."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    if not isinstance(error, str):
        return ""
    return " ".join("".join(char if char.isprintable() else " " for char in error).split())


def _is_missing_user_query_error(body: bytes) -> bool:
    return "no user query found in messages" in _error_text(body).casefold()


async def _error_body(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk[:MAX_ERROR_BODY_BYTES - len(body)])
        if len(body) >= MAX_ERROR_BODY_BYTES:
            break
    return bytes(body)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode())


def _model_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > MAX_MODEL_NAME_BYTES:
        raise StreamError("Ollama model name is missing or exceeds its size limit")
    return value


def _metadata_mapping(value: Any, field: str) -> Json:
    if not isinstance(value, dict):
        raise StreamError(f"Ollama {field} must be an object")
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if visited > 2048 or depth > 8:
            raise StreamError(f"Ollama {field} exceeds metadata structure limits")
        if isinstance(item, dict):
            if any(not isinstance(key, str) or len(key) > 256 for key in item):
                raise StreamError(f"Ollama {field} contains an invalid key")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and len(item) > 16_384:
            raise StreamError(f"Ollama {field} contains an oversized value")
    if _json_size(value) > MAX_DETAIL_BYTES:
        raise StreamError(f"Ollama {field} exceeds metadata size limit")
    return value


def _models(value: Any) -> list[Json]:
    if not isinstance(value, list) or len(value) > MAX_MODELS:
        raise StreamError("Ollama models must be a list within the catalog limit")
    result: list[Json] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise StreamError("Ollama model entry must be an object")
        model = _model_name(item.get("name", item.get("model")))
        if model in names:
            raise StreamError("Ollama catalog contains duplicate model names")
        names.add(model)
        entry: Json = {"name": model}
        for key in ("size", "size_vram", "context_length"):
            if key in item:
                number = item[key]
                if type(number) is not int or not 0 <= number <= 2**63 - 1:
                    raise StreamError(f"Ollama model {key} must be a nonnegative integer")
                entry[key] = number
        for key in ("digest", "modified_at", "expires_at"):
            if key in item:
                if not isinstance(item[key], str) or len(item[key]) > 256:
                    raise StreamError(f"Ollama model {key} must be a bounded string")
                entry[key] = item[key]
        entry["details"] = _metadata_mapping(item.get("details", {}), "details")
        result.append(entry)
    return result


def _detail(value: Json) -> Json:
    capabilities = value.get("capabilities", [])
    if not isinstance(capabilities, list) or len(capabilities) > 32 or any(
        not isinstance(item, str) or len(item) > 64 for item in capabilities
    ):
        raise StreamError("Ollama capabilities must be a bounded list of strings")
    result = {"capabilities": capabilities,
              "model_info": _metadata_mapping(value.get("model_info", {}), "model_info"),
              "details": _metadata_mapping(value.get("details", {}), "details")}
    # Changes when the model is pulled or created again under the same name.
    modified = value.get("modified_at")
    if isinstance(modified, str) and len(modified) <= 64:
        result["modified_at"] = modified
    if _json_size(result) > MAX_DETAIL_BYTES:
        raise StreamError("Ollama model detail exceeds metadata size limit")
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


async def ndjson(chunks: AsyncIterable[bytes]) -> AsyncIterator[Json]:
    """Decode only complete UTF-8 records, accepting a final record without LF."""
    buffer = bytearray()
    async for chunk in chunks:
        buffer.extend(chunk)
        while b"\n" in buffer:
            line, _, tail = buffer.partition(b"\n")
            buffer = bytearray(tail)
            if len(line) > MAX_RECORD_BYTES:
                raise StreamError("Ollama record exceeds 1 MiB")
            if line.strip():
                yield _decode(line)
        if len(buffer) > MAX_RECORD_BYTES:
            raise StreamError("Ollama record exceeds 1 MiB")
    if buffer.strip():
        yield _decode(buffer)


def _decode(line: bytes | bytearray) -> Json:
    try:
        value = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise StreamError("Malformed Ollama NDJSON record") from exc
    if not isinstance(value, dict):
        raise StreamError("Ollama record must be an object")
    return value


async def cancellable(awaitable: Awaitable[T], cancel: asyncio.Event) -> T:
    operation = asyncio.ensure_future(awaitable)
    cancellation = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait({operation, cancellation},
                                     return_when=asyncio.FIRST_COMPLETED)
        if cancellation in done and cancel.is_set():
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation
            raise asyncio.CancelledError
        return await operation
    finally:
        cancellation.cancel()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, cancellation, return_exceptions=True)


def validate_call(name: str, arguments: Any, allowed: list[Json]) -> Json | None:
    schema = next((s["function"]["parameters"] for s in allowed
                   if s["function"]["name"] == name), None)
    problem = None
    if schema is None:
        problem = "Unknown or unauthorized tool"
    elif not isinstance(arguments, dict):
        problem = "Tool arguments must be an object"
    elif set(arguments) - set(schema["properties"]):
        problem = "Unexpected tool argument"
    elif set(schema["required"]) - set(arguments):
        problem = "Missing required tool argument"
    else:
        for key, value in arguments.items():
            kind = schema["properties"][key]["type"]
            if kind == ["string", "null"]:
                valid = value is None or isinstance(value, str)
            elif kind == "string":
                valid = isinstance(value, str)
            elif kind == "array":
                valid = (isinstance(value, list) and len(value) > 0
                         and all(isinstance(v, str) and "\0" not in v for v in value))
            elif kind == "number":
                valid = type(value) in (float, int) and 0 < value <= 86400
            else:
                valid = False
            if not valid:
                problem = f"Invalid argument: {key}"
                break
    return {"ok": False, "error": {"code": "INVALID_TOOL", "message": problem}} if problem else None


def repair_interrupted_history(history: list[Json]) -> tuple[list[Json], int]:
    """Label missing historical tool outcomes; never turn replay into execution."""
    repaired: list[Json] = []
    missing = 0
    index = 0
    while index < len(history):
        message = history[index]
        repaired.append(message)
        index += 1
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        received: list[str] = []
        while index < len(history) and history[index].get("role") == "tool":
            result = history[index]
            repaired.append(result)
            received.append(result.get("tool_name", ""))
            index += 1
        for call in message["tool_calls"]:
            name = call.get("function", {}).get("name", "")
            if name in received:
                received.remove(name)
                continue
            missing += 1
            repaired.append({"role": "tool", "tool_name": name, "content": json.dumps({
                "ok": False, "error": {"code": "UNKNOWN_OUTCOME", "message":
                    "The previous run ended before a provider tool result was recorded. "
                    "The operation may or may not have occurred. Inspect current files and "
                    "durable evidence before further side effects; do not replay blindly."}})})
    return repaired, missing


class OllamaBackend:
    def __init__(self, endpoint: str = "http://127.0.0.1:11434") -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Ollama endpoint must be an explicit HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Ollama endpoint must not contain credentials, query or fragment")
        self.endpoint = endpoint.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.endpoint, trust_env=False,
                                       timeout=httpx.Timeout(180.0, connect=5.0))
        self._details: dict[str, Json] = {}
        self._probed: dict[str, bool] = {}

    async def _json(self, method: str, path: str, body: Json | None = None) -> Json:
        try:
            async with asyncio.timeout(METADATA_TIMEOUT):
                async with self.client.stream(method, path, json=body, timeout=METADATA_TIMEOUT,
                                              headers={"Accept-Encoding": "identity"}) as response:
                    # Reject compression before aiter_bytes can invoke a decoder.
                    identity = response.headers.get("content-encoding", "identity").strip().lower() in {
                        "", "identity"}
                    if response.status_code >= 400 and identity:
                        raise OllamaHTTPError(response, _error_text(
                            await _error_body(response))[:MAX_ERROR_DETAIL_CHARS])
                    response.raise_for_status()
                    if not identity:
                        raise StreamError("Ollama metadata requires identity content encoding")
                    length = response.headers.get("content-length")
                    if length is not None:
                        try:
                            declared = int(length)
                        except ValueError as exc:
                            raise StreamError("Invalid Ollama metadata Content-Length") from exc
                        if not 0 <= declared <= MAX_METADATA_BYTES:
                            raise StreamError("Ollama metadata exceeds response byte limit")
                    data = bytearray()
                    async for chunk in response.aiter_bytes(
                        chunk_size=min(16_384, MAX_METADATA_BYTES + 1)
                    ):
                        if len(chunk) > MAX_METADATA_BYTES - len(data):
                            raise StreamError("Ollama metadata exceeds response byte limit")
                        data.extend(chunk)
        except TimeoutError as exc:
            raise StreamError("Ollama metadata exceeded total request deadline") from exc
        try:
            value = json.loads(data, parse_constant=_invalid_constant, parse_float=_finite_float)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise StreamError("Invalid Ollama metadata JSON") from exc
        if not isinstance(value, dict):
            raise StreamError("Ollama response must be an object")
        return value

    async def _show(self, model: str, *, refresh: bool = False) -> Json:
        model = _model_name(model)
        if not refresh and model in self._details:
            return self._details[model]
        detail = _detail(await self._json("POST", "/api/show", {"model": model}))
        size = _json_size(detail) + len(model.encode())
        if size > MAX_CACHE_BYTES:
            raise StreamError("Ollama model detail exceeds cache byte limit")
        self._details.pop(model, None)
        while self._details and (len(self._details) >= MAX_MODELS or
              sum(_json_size(item) + len(key.encode()) for key, item in self._details.items())
              + size > MAX_CACHE_BYTES):
            oldest = next(iter(self._details))
            self._details.pop(oldest)
            self._probed.pop(oldest, None)
        self._details[model] = detail
        return detail

    async def discover(self) -> Json:
        try:
            async with asyncio.timeout(DISCOVERY_TIMEOUT):
                return await self._discover()
        except (httpx.HTTPError, ValueError, StreamError, TimeoutError) as exc:
            return {"available": False, "models": [], "capabilities": CAPABILITIES,
                    "endpoint": self.endpoint,
                    "error": f"Ollama unavailable: {str(exc)[:200] or type(exc).__name__}"}

    async def _discover(self) -> Json:
        tags = _models((await self._json("GET", "/api/tags")).get("models"))
        running_error = None
        try:
            running = _models((await self._json("GET", "/api/ps")).get("models"))
        except (httpx.HTTPError, ValueError, StreamError) as exc:
            running = []
            running_error = str(exc)[:200]
        models: list[Json] = []
        result: Json = {"available": True, "models": models, "capabilities": CAPABILITIES,
                        "endpoint": self.endpoint, "running_models": running,
                        "device_memory_used_bytes": None}
        if running_error:
            result["running_models_error"] = running_error
        self._bound_discovery(result)
        for item in tags:
            model = item["name"]
            metadata_error = None
            try:
                detail = await self._show(model, refresh=True)
            except (httpx.HTTPError, ValueError, StreamError) as exc:
                detail = {}
                metadata_error = str(exc)[:200]
                self._details.pop(model, None)
                self._probed.pop(model, None)
            probe = "not_run" if metadata_error else self._probe_outcome("ollama", model, detail)
            verified = probe == "passed"
            entry = {"id": model, "name": model,
                     "capabilities": {**CAPABILITIES, "tools": verified,
                                      "images": "vision" in detail.get("capabilities", [])},
                     "tools_metadata": "tools" in detail.get("capabilities", []),
                     "tools_verified": verified, "tool_probe": probe,
                     "size_bytes": item.get("size"), "digest": item.get("digest"),
                     "details": item["details"], "model_info": detail.get("model_info", {}),
                     "loaded": next((m for m in running if m["name"] == model), None)}
            if metadata_error:
                entry["metadata_error"] = metadata_error
            models.append(entry)
            self._bound_discovery(result)
        return result

    @staticmethod
    def _bound_discovery(result: Json) -> None:
        if _json_size(result) > MAX_DISCOVERY_BYTES:
            raise StreamError("Ollama discovery exceeds serialized response limit")

    async def _stream(self, body: Json, ctx: RunContext | None = None,
                      item_id: str | None = None) -> tuple[Json, Json]:
        message: Json = {"role": "assistant", "content": ""}
        calls: list[Json] = []
        fragmented: dict[int, Json] = {}
        total_bytes = 0
        limit = int(ctx.settings.get("max_output_bytes", 4 * 1024 * 1024)) if ctx else 65536
        timeout = httpx.Timeout(OLLAMA_CHAT_READ_TIMEOUT, connect=5.0, write=180.0, pool=5.0)
        async with self.client.stream("POST", "/api/chat", json=body,
                                      timeout=timeout) as response:
            if response.status_code >= 400:
                error_body = await _error_body(response)
                if _is_missing_user_query_error(error_body):
                    raise OllamaMissingUserQuery(
                        "The Ollama model template rejected a tool continuation because it found no user query")
                raise OllamaHTTPError(response, _error_text(error_body)[:MAX_ERROR_DETAIL_CHARS])
            response.raise_for_status()
            async for record in ndjson(response.aiter_bytes()):
                if "error" in record:
                    raise StreamError(str(record["error"])[:1000])
                part = record.get("message", {})
                if not isinstance(part, dict):
                    raise StreamError("Invalid Ollama message")
                for field in ("content", "thinking"):
                    value = part.get(field, "")
                    if not isinstance(value, str):
                        raise StreamError("Invalid Ollama text field")
                    total_bytes += len(value.encode())
                    if total_bytes > limit:
                        raise StreamError(f"Ollama response exceeded configured {limit} byte limit")
                    if value:
                        message[field] = message.get(field, "") + value
                        if field == "content" and ctx:
                            await ctx.emit("message.delta", {"content": value, "item_id": item_id})
                tool_calls = part.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    raise StreamError("Invalid Ollama tool_calls")
                for call in tool_calls:
                    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                        raise StreamError("Invalid Ollama tool call")
                    function = call["function"]
                    if isinstance(function.get("arguments"), str):
                        # Some compatible native servers split string arguments by index.
                        index = function.get("index", call.get("index", 0))
                        if not isinstance(index, int):
                            raise StreamError("Invalid Ollama tool index")
                        dest = fragmented.setdefault(index, {"function": {
                            "name": "", "arguments": "", "index": index}})["function"]
                        if function.get("name"):
                            if dest["name"] and dest["name"] != function["name"]:
                                raise StreamError("Conflicting tool fragment names")
                            dest["name"] = function["name"]
                        dest["arguments"] += function["arguments"]
                    else:
                        calls.append(call)
                    total_bytes += len(json.dumps(call).encode())
                    if total_bytes > limit:
                        raise StreamError("Ollama tool requests exceeded configured output limit")
                if record.get("done") is True:
                    for call in fragmented.values():
                        try:
                            call["function"]["arguments"] = json.loads(
                                call["function"]["arguments"])
                        except ValueError as exc:
                            raise StreamError("Incomplete tool arguments") from exc
                        calls.append(call)
                    if calls:
                        message["tool_calls"] = calls
                    return message, record
        raise StreamError("Ollama stream ended without done=true; outcome is unknown")

    async def _probe_tools(self, model: str, ctx: RunContext) -> bool:
        """Verify tool calling once per service for a provider, endpoint and model revision.

        Only an answer is remembered: the exact call, any other reply, or a
        rejection of the tool request. Transport failures propagate, and an
        unusable response keeps only this run conversational.
        """
        model = _model_name(model)
        if model in self._probed:
            return self._probed[model]
        provider = str(ctx.session.get("backend", "ollama"))
        settings = ctx.settings
        key = (provider, self.endpoint, model, _revision(await self._show(model)),
               json.dumps(settings.get("think")))
        probes = _tool_probes()
        if key in probes:
            return self._remember_probe(model, probes[key])
        await ctx.emit("backend.notice", {"message": f"Verifying {provider} tool calling with a "
                                          "side-effect-free probe", "model": model})
        token = uuid.uuid4().hex
        probe = _tool("kira_probe", "Echo the supplied probe value without side effects",
                      {"value": {"type": "string"}}, ["value"])
        # The chat's context size keeps Ollama from reloading the model for the turn.
        body = {"model": model, "stream": True, "messages": [{"role": "user", "content":
                f"Call kira_probe exactly once with value {token}. Do not answer in text."}],
                "tools": [probe], "options": {"temperature": 0, **{
                    name: settings[name] for name in ("num_ctx",) if name in settings}}}
        if "think" in settings:
            body["think"] = settings["think"]
        try:
            message, _ = await cancellable(self._stream(body), ctx.cancel)
        except httpx.HTTPStatusError as exc:
            # These failures are not evidence that the model lacks tool
            # support. Do not send the user's prompt after quota/auth errors.
            if exc.response.status_code in {401, 402, 403, 429} or exc.response.status_code >= 500:
                raise
            passed = False
        except (ValueError, StreamError):
            return False
        else:
            calls = message.get("tool_calls", [])
            passed = (len(calls) == 1 and calls[0].get("function", {}).get("name") == "kira_probe"
                      and calls[0]["function"].get("arguments") == {"value": token})
        while len(probes) >= MAX_TOOL_PROBES:
            probes.pop(next(iter(probes)))
        probes[key] = passed
        return self._remember_probe(model, passed)

    def _probe_outcome(self, provider: str, model: str, detail: Json) -> str:
        """The remembered probe outcome for a model revision: passed, failed or not_run.

        A pass under any chat thinking mode counts. Reading never sends a probe.
        """
        scope = (provider, self.endpoint, model, _revision(detail))
        outcomes = [passed for key, passed in _tool_probes().items() if key[:4] == scope]
        return "passed" if any(outcomes) else "failed" if outcomes else "not_run"

    def _remember_probe(self, model: str, passed: bool) -> bool:
        if len(self._probed) >= MAX_MODELS:
            self._probed.pop(next(iter(self._probed)))
        self._probed[model] = passed
        return passed

    async def run(self, ctx: RunContext) -> Json:
        provider = str(ctx.session.get("backend", "ollama"))
        pending_question_answers: set[str] = set()
        try:
            return await cancellable(self._run(ctx, pending_question_answers), ctx.cancel)
        except asyncio.CancelledError:
            if not ctx.cancel.is_set():
                raise
            return {"state": "cancelled", "outcome": {"scope": "client_stream_closed",
                    "remote_generation_termination_confirmed": False}}
        except httpx.HTTPStatusError as exc:
            detail = getattr(exc, "safe_detail", "")
            suffix = f" — {detail}" if isinstance(detail, str) and detail else ""
            return {"state": "failed", "error": f"{provider} HTTP {exc.response.status_code}{suffix}"}
        except (httpx.HTTPError, ValueError, StreamError, ClientToolError) as exc:
            return {"state": "interrupted", "error": f"{type(exc).__name__}: {str(exc)[:1000]}"}
        finally:
            for identity in tuple(pending_question_answers):
                await ctx.emit("input.delivery_failed", {"request_id": identity,
                    "reason": f"{provider} did not confirm receipt of the saved answer; no automatic retry."})
                pending_question_answers.discard(identity)

    async def _run(self, ctx: RunContext, pending_question_answers: set[str]) -> Json:
        provider = str(ctx.session.get("backend", "ollama"))
        validate_tools(ctx)
        detail = await self._show(ctx.model)
        vision = "vision" in detail.get("capabilities", [])
        project_tools_enabled = bool(ctx.settings.get("tools", True))
        client_namespaces = ctx.client_tools
        if not project_tools_enabled:
            # Keep the direct provider's safe browser/history/questions tools
            # available without enabling project/file/host tools.
            client_namespaces = [namespace for namespace in ctx.client_tools
                                 if namespace.get("name") in {"browser", "computer", "kira_history"}]
        questions_available = ctx.request_input is not None and ctx.read_input is not None
        tools_requested = project_tools_enabled or bool(client_namespaces) or questions_available
        enabled = (tools_requested
                   and "tools" in detail.get("capabilities", []))
        if enabled:
            enabled = await self._probe_tools(ctx.model, ctx)
        if not enabled and tools_requested:
            await ctx.emit("backend.notice", {"message": "Chat-only mode: tool capability was "
                            "not verified", "model": ctx.model, "tools_verified": False})
        elif enabled:
            await ctx.emit("backend.notice", {"message": f"{provider} tool probe passed",
                                               "model": ctx.model, "tools_verified": True})
        schemas = local_tool_schemas(ctx.permission) if enabled and project_tools_enabled else []
        client_names: set[str] = set()
        client_aliases: dict[str, str] = {}
        question_aliases: dict[str, str] = {}
        if enabled:
            for namespace in client_namespaces:
                functions = namespace["tools"]
                if not vision and namespace["name"] == "computer":
                    continue
                if not vision and namespace["name"] == "browser":
                    functions = [tool for tool in functions if tool["name"] != "screenshot"]
                for tool in functions:
                    canonical = namespace["name"] + "." + tool["name"]
                    alias = canonical.replace(".", "__")
                    if alias in client_aliases or any(item["function"]["name"] == alias for item in schemas):
                        raise StreamError("Kira tool names collide with native tools")
                    client_aliases[alias] = canonical
                    client_names.add(canonical)
                    description = tool["description"]
                    if ctx.session.get("backend") == "ollama":
                        description = description.replace("browser.", "browser__").replace(
                            "computer.", "computer__")
                    schemas.append({"type": "function", "function": {
                        "name": alias, "description": description,
                        "parameters": tool["inputSchema"]}})
            if questions_available:
                for tool in question_tool_specs()["tools"]:
                    alias = "questions__" + tool["name"]
                    question_aliases[alias] = tool["name"]
                    schemas.append({"type": "function", "function": {
                        "name": alias, "description": tool["description"],
                        "parameters": tool["inputSchema"]}})
        bridge = ClientToolBridge(ctx, client_names, ctx.run_id, ctx.run_id) if client_names else None
        messages: list[Json] = []
        assistant_instructions = ctx.assistant_instructions
        if ctx.output_schema is not None and schemas:
            assistant_instructions += (
                "\nWhen this turn is complete, return exactly one JSON object matching this "
                "schema. Registered tool calls remain separate function calls. Schema: "
                + json.dumps(ctx.output_schema, ensure_ascii=False, separators=(",", ":")))
        if provider == "ollama" and schemas:
            assistant_instructions += TOOL_CALL_GUIDANCE
        if not schemas:
            assistant_instructions += NO_TOOLS_GUIDANCE
        elif question_aliases:
            assistant_instructions += (
                "\nUse questions__request_input for missing information or choices. "
                "Never ask for passwords, tokens or permission through a question. "
                "Use questions__read_input with the returned request_id; a pending result "
                "is not an answer or approval.")
        if assistant_instructions:
            messages.append({"role": "system", "content": assistant_instructions})
        if ctx.supplied_context:
            messages.append({"role": "user", "content": "Kira-supplied project context:\n" + ctx.supplied_context})
        durable_context = self._deepseek_context(ctx)
        active_turn: list[Json] = [{"role": "user", "content": ctx.prompt}]
        if durable_context is not None:
            await durable_context.prepare(messages, schemas)
            messages = await durable_context.before_request(active_turn)
        else:
            history, missing_results = repair_interrupted_history(ctx.history)
            if missing_results:
                await ctx.emit("backend.notice", {"message": "Historical tool outcomes are unknown. "
                               "Continuation must inspect verified state before further side effects.",
                               "unknown_tool_outcomes": missing_results})
            fields = {"role", "content", "tool_calls", "tool_name"}
            if provider != "ollama" and vision:
                # DeepSeek Account attaches an earlier request's images by its run.
                fields.add("run_id")
            for item in history:
                if item.get("role") in {"user", "assistant", "tool"}:
                    messages.append({k: v for k, v in item.items() if k in fields})
            messages.append(active_turn[0])
        rounds = 0
        recent_results: list[str] = []
        max_rounds = int(ctx.settings.get("max_tool_rounds", 0))
        guidance = BoundaryGuidance(ctx)
        request_index = 0
        overflow_recovered = False
        missing_query_recovered = False
        # The first durable request was prepared above, before any guidance
        # was taken; every later request is prepared here.
        prepared = durable_context is not None

        while True:
            extra = await guidance.take()
            if extra:
                recent_results.clear()
                prepared = False
                if durable_context is not None:
                    active_turn.extend(extra)
                else:
                    messages.extend(extra)
            if durable_context is not None and not prepared:
                messages = await durable_context.before_request(active_turn)
            prepared = False
            body: Json = {"model": ctx.model, "messages": messages, "stream": True}
            if schemas:
                body["tools"] = schemas
            options = {k: ctx.settings[k] for k in ("num_ctx", "temperature", "seed")
                       if k in ctx.settings}
            if options:
                body["options"] = options
            if ctx.output_schema is not None and not schemas:
                body["format"] = ctx.output_schema
            if "think" in ctx.settings:
                body["think"] = ctx.settings["think"]
            item_id = f"{ctx.run_id}:{provider}:{request_index}"
            try:
                message, terminal = await self._stream(body, ctx, item_id)
            except OllamaMissingUserQuery:
                if provider != "ollama" or missing_query_recovered:
                    raise
                # Some Qwen-family Ollama templates require an explicit user
                # turn after tool-only history. The preceding tool effects are
                # already recorded in `messages`; preserve them and restate the
                # original request once so the template can continue.
                messages.append({"role": "user", "content": (
                    "Kira is continuing the original user request after the local model template "
                    "rejected a tool continuation. Original request:\n" + ctx.prompt + "\n\n"
                    "The preceding tool calls and results are already part of the conversation. "
                    "Do not repeat completed operations. Continue from their recorded results; "
                    "inspect current state before any new side effect. If the available evidence "
                    "is sufficient, answer the user now.")})
                missing_query_recovered = True
                recent_results.clear()
                await ctx.emit("backend.notice", {"message": "Ollama's model template rejected the tool continuation; Kira is retrying once with the original request and completed tool results."})
                continue
            except httpx.ReadTimeout as exc:
                if provider != "ollama":
                    raise
                raise StreamError(
                    f"Ollama sent no stream data for {OLLAMA_CHAT_READ_TIMEOUT:.0f} seconds; "
                    "a long prompt evaluation may have exceeded the read timeout"
                ) from exc
            except StreamError as exc:
                if (durable_context is not None and provider == "deepseek_account"
                        and getattr(exc, "context_overflow", False) and not overflow_recovered):
                    await durable_context.compact_for_overflow(active_turn)
                    overflow_recovered = True
                    continue
                raise
            overflow_recovered = False
            if durable_context is not None:
                durable_context.observe(messages, terminal)
            for identity in tuple(pending_question_answers):
                await ctx.emit("input.delivered", {"request_id": identity,
                                                    "transport": "ollama-chat" if provider == "ollama"
                                                    else f"{provider}-messages"})
                pending_question_answers.discard(identity)
            await guidance.accepted()
            usage = {"source": provider, "measurement": "provider", "round": request_index}
            request_index += 1
            for source, target in [("prompt_eval_count", "input_tokens"),
                                   ("eval_count", "output_tokens"),
                                   ("eval_duration", "eval_duration_ns"),
                                   ("total_duration", "total_duration_ns"),
                                   ("load_duration", "load_duration_ns")]:
                if terminal.get(source) is not None:
                    usage[target] = terminal[source]
            await ctx.emit("usage", usage)
            if durable_context is not None:
                active_turn.append(message)
            else:
                messages.append(message)
            public_message = {k: v for k, v in message.items() if k != "thinking"}
            calls = message.get("tool_calls", [])
            await ctx.emit("message.completed", {"content": message.get("content", ""),
                            "item_id": item_id,
                            "provider_messages": [public_message]})
            if terminal.get("tool_input_incomplete") is True:
                return {"state": "interrupted", "error": f"{provider} returned incomplete tool arguments; no tool from that response was executed. Continue explicitly.",
                        "outcome": {"done_reason": "tool_input_incomplete",
                                    "tool_execution": "not_started",
                                    "prompt_tokens": terminal.get("prompt_eval_count"),
                                    "output_tokens": terminal.get("eval_count")}}
            if not calls:
                if terminal.get("done_reason") == "length":
                    prompt_tokens = terminal.get("prompt_eval_count")
                    output_tokens = terminal.get("eval_count")
                    counts = (f" after {prompt_tokens} prompt and {output_tokens} generated tokens"
                              if type(prompt_tokens) is int and type(output_tokens) is int else "")
                    if (provider == "deepseek_account"
                            and type(terminal.get("max_output_tokens")) is int
                            and type(output_tokens) is int
                            and output_tokens >= terminal["max_output_tokens"]):
                        limit = terminal["max_output_tokens"]
                        return {"state": "interrupted", "error": f"{provider} reached the request output cap of {limit} tokens{counts}; response may be incomplete",
                                "outcome": {"done_reason": "length", "prompt_tokens": prompt_tokens,
                                            "output_tokens": output_tokens, "max_output_tokens": limit}}
                    requested = ctx.settings.get("num_ctx")
                    context = (f" (requested num_ctx={requested})" if requested else
                               " (provider-default num_ctx)" if provider == "ollama" else "")
                    return {"state": "interrupted", "error": f"{provider} stopped generation at its "
                            f"token/context limit{counts}{context}; response may be incomplete",
                            "outcome": {"done_reason": "length", "prompt_tokens": prompt_tokens,
                                        "output_tokens": output_tokens, "requested_num_ctx": requested}}
                if not await guidance.seal():
                    continue
                if ctx.output_schema is not None:
                    if len(message.get("content", "").encode("utf-8")) > MAX_CLIENT_TOOL_BYTES:
                        return {"state": "failed", "error": f"Structured {provider} output exceeds 64 KiB"}
                    try:
                        structured = json.loads(message.get("content", ""),
                                                parse_constant=_invalid_constant)
                    except (ValueError, TypeError, RecursionError):
                        return {"state": "failed", "error": f"{provider} did not return valid structured JSON"}
                    if not isinstance(structured, dict):
                        return {"state": "failed", "error": f"{provider} structured output must be an object"}
                if durable_context is not None:
                    await durable_context.complete(active_turn)
                return {"state": "completed", "outcome": {"model": ctx.model,
                        "tool_rounds": rounds, "tools_verified": enabled,
                        **({"history_mode": "durable_compacted_context"}
                           if durable_context is not None else {}),
                        **({"final_text": message.get("content", "")}
                           if ctx.output_schema is not None else {})}}
            if not schemas:
                return {"state": "failed", "error": "Unverified model requested tools in chat-only mode"}
            if max_rounds and rounds >= max_rounds:
                return {"state": "failed", "error": f"Configured tool round limit ({max_rounds}) reached"}
            rounds += 1
            tool_messages = []
            for position, call in enumerate(calls):
                if ctx.cancel.is_set():
                    raise asyncio.CancelledError
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments")
                is_client = isinstance(name, str) and name in client_aliases
                is_question = isinstance(name, str) and name in question_aliases
                # Kira callbacks validate their own exact contracts. Native file
                # tools retain their smaller argument vocabulary.
                result = None if is_client or is_question else validate_call(name, arguments, schemas)
                if result is None and await guidance.pending():
                    result = deferred_tool_result()
                    await ctx.emit("tool.completed", {
                        "tool_id": f"{ctx.run_id}:{provider}-deferred:{rounds}:{position}",
                        "name": client_aliases.get(name, name), "result": result, "execution": "not_started"})
                if result is None:
                    if is_client:
                        assert bridge is not None
                        namespace_name, tool_name = client_aliases[name].split(".", 1)
                        reply = await bridge.call({"threadId": ctx.run_id, "turnId": ctx.run_id,
                            "callId": f"{ctx.run_id}:{provider}-client:{rounds}:{position}",
                            "namespace": namespace_name, "tool": tool_name, "arguments": arguments})
                        result = json.loads(reply["contentItems"][0]["text"])
                    elif is_question:
                        question_id = f"{ctx.run_id}:{provider}-question:{rounds}:{position}"
                        operation = question_aliases[name]
                        try:
                            if operation == "request_input":
                                if not isinstance(arguments, dict) or set(arguments) != {"blocking", "questions"}:
                                    raise KiraError("INVALID_ARGUMENT", "Invalid question fields")
                                request = validate_request({**arguments, "request_id": question_id})
                                assert ctx.request_input is not None
                                result = await ctx.request_input(request)
                            elif operation == "read_input":
                                if (not isinstance(arguments, dict)
                                        or set(arguments) != {"request_id", "wait"}
                                        or not isinstance(arguments.get("request_id"), str)
                                        or type(arguments.get("wait")) is not bool):
                                    raise KiraError("INVALID_ARGUMENT", "Invalid question lookup")
                                assert ctx.read_input is not None
                                result = await ctx.read_input(arguments["request_id"], arguments["wait"])
                            else:
                                raise StreamError(f"Unknown registered {provider} question tool")
                        except KiraError as exc:
                            result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
                        if (result.get("ok") is True and result.get("status") == "answered"
                                and isinstance(result.get("request_id"), str)):
                            pending_question_answers.add(result["request_id"])
                    else:
                        result = await ctx.execute_tool(name, arguments)
                tool_message = {"role": "tool", "tool_name": name,
                                "content": json.dumps(result, ensure_ascii=False)}
                if isinstance(call.get("id"), str):
                    tool_message["tool_call_id"] = call["id"]
                canonical = client_aliases.get(name, "") if isinstance(name, str) else ""
                if (vision and canonical and _browser_image_tool(canonical)
                        and result.get("ok") is True):
                    image_id = result.get("browser_image_id")
                    if not isinstance(image_id, str) or not 1 <= len(image_id) <= 256:
                        raise StreamError("Browser screenshot omitted its owned image identity")
                    if ctx.tool_image is None:
                        raise StreamError("Browser screenshot image delivery is unavailable")
                    tool_message["images"] = [_ollama_image(await ctx.tool_image(image_id))]
                if durable_context is not None:
                    active_turn.append(tool_message)
                else:
                    messages.append(tool_message)
                # With durable context, tool images exist only in the active turn.
                _browser_tool_images(active_turn if durable_context is not None else messages)
                tool_messages.append({key: value for key, value in tool_message.items()
                                      if key != "images"})
            await ctx.emit("backend.notice", {"message": f"{provider} tool results returned to model",
                                               "provider_messages": tool_messages})
            signature = hashlib.sha256(json.dumps([calls, tool_messages],
                                                  sort_keys=True).encode()).hexdigest()
            recent_results.append(signature)
            recent_results = recent_results[-3:]
            if len(recent_results) == 3 and len(set(recent_results)) == 1:
                return {"state": "failed", "error": "No progress: the same tool requests and "
                        "results repeated three consecutive times. Continue explicitly."}

    def _deepseek_context(self, ctx: RunContext) -> Any | None:
        if (ctx.session.get("backend") != "deepseek_account" or ctx.context_save is None):
            return None
        from kira.backends.deepseek_context import DeepSeekContext
        return DeepSeekContext(self, ctx)

    async def close(self) -> None:
        await self.client.aclose()
