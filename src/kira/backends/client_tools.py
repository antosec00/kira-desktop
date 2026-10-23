"""Bounded, correlated callbacks for Kira assistant and team mailbox tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from kira.domain.assistant import ASSISTANT_TOOL_NAMES
from kira.domain.autonomous_goals import GOAL_TOOL_NAMES
from kira.domain.computer import COMPUTER_TOOL_NAMES
from kira.domain.research import RESEARCH_TOOL_NAMES
from kira.domain.knowledge import KNOWLEDGE_TOOL_NAMES
from kira.domain.project_tools import PROJECT_TOOL_NAMES
from kira.domain.souls import SOUL_TOOL_NAMES
from kira.domain.studio import STUDIO_TOOL_NAMES
from kira.domain.contracts import Json, RunContext
from kira.domain.web import BROWSER_ACTIONS, BROWSER_TOOL_NAMES

KIRA_CLIENT_TOOL_NAMES = (ASSISTANT_TOOL_NAMES | RESEARCH_TOOL_NAMES | KNOWLEDGE_TOOL_NAMES
                         | PROJECT_TOOL_NAMES | SOUL_TOOL_NAMES | STUDIO_TOOL_NAMES)
CLIENT_TOOL_NAMES = (frozenset({"team.send_message", "team.read_messages", "kira_history.search", "kira_history.read"})
                    | KIRA_CLIENT_TOOL_NAMES | BROWSER_TOOL_NAMES | COMPUTER_TOOL_NAMES | GOAL_TOOL_NAMES)
CLIENT_NAMESPACES = frozenset({"team", "kira", "kira_history", "browser", "computer", "goal"})
MAX_CLIENT_TOOL_BYTES = 65_536
MAX_CLIENT_TOOL_CALLS = 2_048
MAX_CLIENT_TOOL_CACHE_BYTES = 8 * 1024 * 1024
CLIENT_TOOL_TIMEOUT = 60.0


class ClientToolError(Exception):
    """The bridge cannot safely continue a client-tool exchange."""


def encoded(value: Any) -> bytes:
    """Use the same strict JSON representation for bounds and retry identity."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ClientToolError("Client-tool data must be finite JSON") from exc


def validate_tools(ctx: RunContext) -> set[str]:
    if not ctx.client_tools:
        if ctx.client_tool_call is not None:
            raise ClientToolError("Client-tool callback requires registered tools")
        return set()
    if ctx.client_tool_call is None:
        raise ClientToolError("Registered tools require a client-tool callback")
    if len(encoded(ctx.client_tools)) > MAX_CLIENT_TOOL_BYTES:
        raise ClientToolError("Client-tool definitions exceed 64 KiB")
    if not 1 <= len(ctx.client_tools) <= 5:
        raise ClientToolError("Register bounded Kira/team, history, browser, computer and goal namespaces")
    names: set[str] = set()
    namespaces: set[str] = set()
    for namespace in ctx.client_tools:
        if not isinstance(namespace, dict) or namespace.get("type") != "namespace" \
                or namespace.get("name") not in CLIENT_NAMESPACES \
                or not isinstance(namespace.get("description"), str) \
                or namespace["name"] in namespaces:
            raise ClientToolError("Invalid Kira client-tool namespace")
        namespaces.add(namespace["name"])
        if {"team", "kira"} <= namespaces:
            raise ClientToolError("Kira and managed team tools cannot share the same scope")
        functions = namespace.get("tools")
        if not isinstance(functions, list) or not 1 <= len(functions) <= (
                len(BROWSER_ACTIONS) if namespace["name"] == "browser" else
                len(KIRA_CLIENT_TOOL_NAMES)
                if namespace["name"] == "kira" else 2):
            raise ClientToolError("Register a bounded set of supported client tools")
        for tool in functions:
            if not isinstance(tool, dict):
                raise ClientToolError("Client-tool definitions must be objects")
            name = f"{namespace['name']}.{tool.get('name')}"
            schema = tool.get("inputSchema")
            if tool.get("type") != "function" or name not in CLIENT_TOOL_NAMES or name in names \
                    or not isinstance(tool.get("description"), str) \
                    or not isinstance(schema, dict) or schema.get("type") != "object":
                raise ClientToolError("Invalid or unsupported Kira client-tool definition")
            names.add(name)
    return names


def failure(code: str, message: str) -> Json:
    return {"success": False, "contentItems": [{"type": "inputText", "text": json.dumps(
        {"ok": False, "error": {"code": code, "message": message}})}]}


class ClientToolBridge:
    """Deduplicate provider callbacks; the service owns durable tool effects."""

    def __init__(self, ctx: RunContext, names: set[str], thread_id: str, turn_id: str) -> None:
        self.ctx, self.names = ctx, names
        self.thread_id, self.turn_id = thread_id, turn_id
        self.responses: dict[str, tuple[str, Json]] = {}
        self.failures: dict[str, str] = {}
        self.cache_bytes = 0
        self._lock = asyncio.Lock()

    async def call(self, params: Json) -> Json:
        # MCP clients may issue concurrent requests or retry before the first
        # response. Serialize identity reservation with dispatch, not just cache
        # insertion, so a repeated call can never cause two callback effects.
        async with self._lock:
            return await self._call(params)

    async def _call(self, params: Json) -> Json:
        if params.get("threadId") != self.thread_id or params.get("turnId") != self.turn_id:
            return failure("WRONG_RUN", "The client-tool call does not belong to this active run")
        call_id = params.get("callId")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 256:
            return failure("INVALID_CALL_ID", "A bounded nonempty client-tool call ID is required")
        namespace, tool, arguments = params.get("namespace"), params.get("tool"), params.get("arguments")
        if not isinstance(namespace, str) or namespace not in CLIENT_NAMESPACES \
                or not isinstance(tool, str):
            return failure("UNSUPPORTED_TOOL", "Only registered Kira tools are available")
        name = f"{namespace}.{tool}"
        if name not in self.names:
            return failure("UNSUPPORTED_TOOL", "This Kira tool was not registered")
        if not isinstance(arguments, dict):
            return failure("INVALID_ARGUMENTS", "Client-tool arguments must be a JSON object")
        try:
            argument_bytes = encoded(arguments)
        except ClientToolError:
            return failure("INVALID_ARGUMENTS", "Client-tool arguments must be finite JSON")
        if len(argument_bytes) > MAX_CLIENT_TOOL_BYTES:
            return failure("ARGUMENTS_TOO_LARGE", "Client-tool arguments exceed 64 KiB")
        fingerprint = hashlib.sha256(name.encode() + b"\0" + argument_bytes).hexdigest()
        cached = self.responses.get(call_id)
        if cached:
            if cached[0] != fingerprint:
                raise ClientToolError("Client-tool call ID was reused with different input")
            return cached[1]
        previous_failure = self.failures.get(call_id)
        if previous_failure is not None:
            if previous_failure != fingerprint:
                raise ClientToolError("Client-tool call ID was reused with different input")
            raise ClientToolError("Client-tool delivery is unknown; inspect durable state")
        # Reserve capacity before the effect, including nested JSON string escaping.
        if len(self.responses) + len(self.failures) >= MAX_CLIENT_TOOL_CALLS or (
                self.cache_bytes + 2 * MAX_CLIENT_TOOL_BYTES + 1024 > MAX_CLIENT_TOOL_CACHE_BYTES):
            raise ClientToolError("Client-tool retry cache reached its documented resource bound")
        if self.ctx.cancel.is_set():
            response = failure("CANCELLED", "The run was cancelled before client-tool dispatch")
        else:
            # Keep an uncertain identity even when the callback raises or its
            # result is invalid after an effect. Such a call is never replayed.
            self.failures[call_id] = fingerprint
            result = await self._execute(call_id, name, arguments)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ClientToolError("Client-tool callback returned an invalid outcome")
            data = encoded(result)
            if len(data) > MAX_CLIENT_TOOL_BYTES:
                raise ClientToolError("Client-tool callback result exceeds 64 KiB; inspect durable state")
            response = {"success": result["ok"], "contentItems": [
                {"type": "inputText", "text": data.decode("utf-8")}]}
        self.responses[call_id] = fingerprint, response
        self.failures.pop(call_id, None)
        self.cache_bytes += len(encoded(response))
        return response

    async def _execute(self, call_id: str, name: str, arguments: Json) -> Json:
        assert self.ctx.client_tool_call is not None
        pending = asyncio.ensure_future(self.ctx.client_tool_call(call_id, name, arguments))
        cancelled = asyncio.create_task(self.ctx.cancel.wait())
        try:
            # Browser interactions may wait for a real user decision. Their
            # engine calls have separate bounded deadlines; cancellation still
            # interrupts the wait and uncertain actions are never replayed.
            timeout = None if name.startswith(("browser.", "computer.")) or name in PROJECT_TOOL_NAMES else CLIENT_TOOL_TIMEOUT
            done, _ = await asyncio.wait({pending, cancelled}, timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise ClientToolError("Client-tool callback timed out; inspect durable state")
            if pending in done:
                try:
                    return await pending
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Callback failures may occur after a durable effect: do not encourage retry.
                    raise ClientToolError("Client-tool callback failed; inspect durable state") from exc
            return {"ok": False, "error": {"code": "CANCELLED",
                    "message": "Client-tool callback was cancelled; consult durable events"}}
        finally:
            cancelled.cancel()
            pending.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
            cleanup_timeout = 5 if name in PROJECT_TOOL_NAMES else 4 if name.startswith(("browser.", "computer.")) else 1
            done, _ = await asyncio.wait({pending}, timeout=cleanup_timeout)
            if pending in done:
                await asyncio.gather(pending, return_exceptions=True)
            else:
                # Never wait indefinitely on a callback that suppresses cancellation.
                pending.add_done_callback(_consume_task)
                raise ClientToolError(f"Client-tool cancellation was not confirmed within {cleanup_timeout} seconds")


def _consume_task(task: asyncio.Future[Json]) -> None:
    if not task.cancelled():
        task.exception()
