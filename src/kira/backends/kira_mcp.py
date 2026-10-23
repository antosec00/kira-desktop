"""Ephemeral MCP adapter for one service-bound Kira assistant run.

The provider's thread-local config contains only a private descriptor path, never
the capability. The descriptor and Unix socket disappear on run completion. The
relay calls the existing RunContext callback; it has no general service RPC,
database access, model selection or ability to impersonate another project.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import json
import os
import secrets
import socket
import stat
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

from kira import __version__
from kira.backends.client_tools import KIRA_CLIENT_TOOL_NAMES, ClientToolBridge, ClientToolError, encoded
from kira.domain.assistant import assistant_tool_specs
from kira.domain.autonomous_goals import GOAL_TOOL_NAMES, goal_tool_specs
from kira.domain.research import research_tool_specs
from kira.domain.knowledge import knowledge_tool_specs
from kira.domain.project_tools import PROJECT_TOOL_CALL_TIMEOUT, PROJECT_TOOL_NAMES, project_tool_specs
from kira.domain.souls import soul_tool_specs
from kira.domain.studio import studio_tool_specs
from kira.domain.contracts import Json, RunContext

MCP_SERVER_NAME = "kira"
MCP_FRAME_BYTES = 196_608
MAX_RELAY_CLIENTS = 16
MCP_TIMEOUT = 65.0
# Kira's own tools and, in a chat with an autonomous goal, the goal tools.
KIRA_RELAY_TOOLS = KIRA_CLIENT_TOOL_NAMES | GOAL_TOOL_NAMES
PARSE_ERROR: Json = {"code": -32700, "message": "Parse error"}


def mcp_tool_name(name: str) -> str:
    """The relay's MCP name for a registered tool.

    Kira's own tools keep their bare names; another namespace is flattened as
    ``<namespace>__<tool>``, which no Kira tool name contains.
    """
    namespace, tool = name.split(".", 1)
    return tool if namespace == MCP_SERVER_NAME else f"{namespace}__{tool}"


def _relay_specs() -> dict[str, Json]:
    """Typed definitions of every relayable tool, keyed by registered name."""
    kira = [*assistant_tool_specs()[0]["tools"], *research_tool_specs(), *knowledge_tool_specs(),
            *project_tool_specs(), *soul_tool_specs(), *studio_tool_specs()]
    specs = {f"{MCP_SERVER_NAME}.{tool['name']}": tool for tool in kira}
    specs.update({f"goal.{tool['name']}": tool for tool in goal_tool_specs(writable=True)["tools"]})
    return specs


def _failure(message: str) -> Json:
    return {"content": [{"type": "text", "text": json.dumps({"ok": False,
            "error": {"code": "KIRA_TOOL_UNAVAILABLE", "message": message}})}],
            "isError": True}


def _same_user(writer: asyncio.StreamWriter) -> bool:
    peer = writer.get_extra_info("socket")
    if peer is None:
        return False
    try:
        _, uid, _ = struct.unpack("3i", peer.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return bool(uid == os.getuid())
    except (AttributeError, OSError, struct.error):
        return False


def _decode(data: bytes) -> Json:
    value = json.loads(data)
    if not isinstance(value, dict) or len(encoded(value)) > MCP_FRAME_BYTES:
        raise ValueError("Invalid bounded object")
    return value


class KiraMcpRelay:
    """Owned Unix relay; the opaque run binding never comes from MCP input."""

    def __init__(self, ctx: RunContext, names: set[str]) -> None:
        if not names or not names <= KIRA_RELAY_TOOLS:
            raise ClientToolError("The assistant relay requires registered Kira tools")
        self.ctx, self.names = ctx, names
        self.bridge = ClientToolBridge(ctx, names, ctx.run_id, ctx.run_id)
        self.directory: Path | None = None
        self.server: asyncio.Server | None = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.capability = secrets.token_hex(32)
        self.closed = False
        self.ready = False

    async def start(self) -> Json:
        self.directory = Path(tempfile.mkdtemp(prefix="kira-tool-", dir="/tmp"))
        try:
            path = self.directory / "relay.sock"
            self.server = await asyncio.start_unix_server(
                self._accept, path=str(path), limit=MCP_FRAME_BYTES)
            path.chmod(0o600)
            descriptor = self.directory / "bridge.json"
            fd = os.open(descriptor, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump({"socket": str(path), "capability": self.capability,
                           "tools": sorted(self.names)}, output)
            return {"command": sys.executable,
                    "args": ["-I", str(Path(__file__).with_name("kira_mcp_entry.py").resolve()),
                             str(descriptor)],
                    "enabled": True, "required": True, "startup_timeout_sec": 10,
                    "tool_timeout_sec": PROJECT_TOOL_CALL_TIMEOUT if self.names & PROJECT_TOOL_NAMES else MCP_TIMEOUT,
                    # These are the service's already-authorized, run-bound
                    # callbacks. Codex's generic MCP approval prompt cannot be
                    # answered under approvalPolicy=never. Grant only these exact
                    # names; native tools, other MCP servers and the project's
                    # execution/sandbox permission remain untouched.
                    "tools": {mcp_tool_name(name): {"approval_mode": "approve"}
                              for name in sorted(self.names)},
                    "enabled_tools": sorted(mcp_tool_name(name) for name in self.names)}
        except BaseException:
            await self.close()
            raise

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.closed or len(self.tasks) >= MAX_RELAY_CLIENTS:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        operation: asyncio.Task[Json] | None = None
        disconnected: asyncio.Task[bytes] | None = None
        try:
            if not _same_user(writer):
                return
            data = await asyncio.wait_for(reader.readline(), 10)
            request = _decode(data)
            supplied = request.pop("capability", None)
            if not isinstance(supplied, str) or not hmac.compare_digest(
                    self.capability, supplied) or self.closed:
                return
            if request == {"method": "ready"}:
                self.ready = True
                response: Json = {"ok": True}
            else:
                if set(request) != {"call_id", "name", "arguments"}:
                    raise ValueError("Invalid relay request")
                name = request["name"]
                if not isinstance(name, str) or name not in self.names:
                    raise ValueError("Unregistered relay tool")
                namespace, tool = name.split(".", 1)
                operation = asyncio.create_task(self.bridge.call({
                    "threadId": self.ctx.run_id, "turnId": self.ctx.run_id,
                    "callId": request["call_id"], "namespace": namespace,
                    "tool": tool, "arguments": request["arguments"],
                }))
                disconnected = asyncio.create_task(reader.read(1))
                done, _ = await asyncio.wait({operation, disconnected},
                                             return_when=asyncio.FIRST_COMPLETED)
                if operation not in done:
                    # A cancelled/disconnected MCP request cannot be replayed
                    # under its same identity after an uncertain effect.
                    return
                result = await operation
                response = {"content": [{"type": "text", "text": item["text"]}
                                        for item in result["contentItems"]],
                            "isError": not result["success"]}
            writer.write(encoded(response) + b"\n")
            await asyncio.wait_for(writer.drain(), 5)
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, ClientToolError, TimeoutError):
            # Exception messages can contain caller data or filesystem details.
            with contextlib.suppress(OSError, TimeoutError):
                writer.write(encoded(_failure(
                    "The Kira tool did not confirm completion; inspect its durable state.")) + b"\n")
                await asyncio.wait_for(writer.drain(), 1)
        finally:
            for task in (operation, disconnected):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (operation, disconnected) if task is not None),
                                 return_exceptions=True)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def close(self) -> None:
        self.closed = True
        if self.server is not None:
            self.server.close()
        # wait_closed() also waits for open connections, which an in-flight
        # call keeps open: end the calls first.
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        if self.server is not None:
            await self.server.wait_closed()
        if self.directory is not None:
            for name in ("bridge.json", "relay.sock"):
                with contextlib.suppress(FileNotFoundError):
                    (self.directory / name).unlink()
            with contextlib.suppress(FileNotFoundError):
                self.directory.rmdir()
        self.capability = ""


def _descriptor(path: Path) -> Json:
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() \
            or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("Descriptor directory is not private")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096:
            raise ValueError("Descriptor is not private")
        value = _decode(stream.read(4097))
    if set(value) != {"socket", "capability", "tools"} \
            or not isinstance(value["capability"], str) or len(value["capability"]) != 64 \
            or value["socket"] != str(path.parent / "relay.sock") \
            or not isinstance(value["tools"], list) or not value["tools"] \
            or not all(isinstance(name, str) and name in KIRA_RELAY_TOOLS
                       for name in value["tools"]):
        raise ValueError("Invalid descriptor")
    info = Path(value["socket"]).lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() \
            or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Relay socket is not private")
    return value


async def _relay_call(config: Json, request: Json) -> Json:
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(PROJECT_TOOL_CALL_TIMEOUT
                                   if request.get("name") in PROJECT_TOOL_NAMES else MCP_TIMEOUT):
            reader, writer = await asyncio.open_unix_connection(
                config["socket"], limit=MCP_FRAME_BYTES)
            if not _same_user(writer):
                raise ValueError("Unexpected relay owner")
            writer.write(encoded({**request, "capability": config["capability"]}) + b"\n")
            await writer.drain()
            return _decode(await reader.readline())
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


async def _frame(reader: asyncio.StreamReader) -> bytes | None:
    """Read one newline-delimited frame: b"" at end of input, None when it is oversized.

    An oversized frame is discarded through its newline, so its remainder is
    never taken for a frame of its own.
    """
    try:
        return await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        return exc.partial
    except asyncio.LimitOverrunError as exc:
        consumed = exc.consumed
    while True:
        await reader.readexactly(consumed)
        try:
            await reader.readuntil(b"\n")
            return None
        except asyncio.IncompleteReadError:
            return None
        except asyncio.LimitOverrunError as exc:
            consumed = exc.consumed


async def _stdio(config: Json) -> None:
    """Small MCP JSON-RPC surface; no resources, prompts or arbitrary methods."""
    reader = asyncio.StreamReader(limit=MCP_FRAME_BYTES)
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    pending: dict[str, asyncio.Task[None]] = {}
    initialized = False
    registered = {mcp_tool_name(name): name for name in config["tools"]}

    def respond(request_id: Any, result: Json | None = None, error: Json | None = None) -> None:
        reply: Json = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            reply["error"] = error
        else:
            reply["result"] = result
        sys.stdout.buffer.write(encoded(reply) + b"\n")
        sys.stdout.buffer.flush()

    async def dispatch(request: Json, identity: str) -> None:
        try:
            method, params = request["method"], request.get("params", {})
            if method == "ping":
                result: Json = {}
            elif method in {"resources/list", "resources/templates/list", "prompts/list"}:
                result = {"resources/list": {"resources": []},
                          "resources/templates/list": {"resourceTemplates": []},
                          "prompts/list": {"prompts": []}}[method]
            elif method == "tools/list":
                result = {"tools": [{**{key: value for key, value in tool.items() if key != "type"},
                                     "name": mcp_tool_name(name)}
                                    for name, tool in _relay_specs().items() if name in config["tools"]]}
            elif method == "tools/call" and isinstance(params, dict):
                name, arguments = params.get("name"), params.get("arguments", {})
                if not isinstance(name, str) or name not in registered:
                    result = _failure("The requested Kira tool is not registered.")
                else:
                    # JSON-RPC numeric and string IDs remain different identities.
                    call_id = "mcp-" + identity
                    result = await _relay_call(config, {"call_id": call_id,
                        "name": registered[name], "arguments": arguments})
            else:
                respond(request["id"], error={"code": -32601, "message": "Method not found"})
                return
            respond(request["id"], result)
        except asyncio.CancelledError:
            respond(request["id"], _failure("The Kira tool call was cancelled."))
            raise
        except (ValueError, OSError, TimeoutError, ClientToolError):
            respond(request["id"], _failure(
                "The Kira relay is unavailable; do not assume the action completed."))
        finally:
            pending.pop(identity, None)

    try:
        while (frame := await _frame(reader)) != b"":
            try:
                request = _decode(frame) if frame is not None else None
            except (ValueError, ClientToolError, RecursionError):
                request = None
            if request is None:
                # Not one bounded JSON object: answer with JSON-RPC's parse
                # error and keep serving later requests.
                respond(None, error=PARSE_ERROR)
                continue
            method = request.get("method")
            params = request.get("params", {})
            if request.get("jsonrpc") != "2.0" or not isinstance(method, str) \
                    or not isinstance(params, dict):
                respond(None, error={"code": -32600, "message": "Invalid request"})
                continue
            if "id" not in request:
                if method == "notifications/cancelled":
                    identity = encoded(params.get("requestId")).decode()
                    if identity in pending:
                        pending[identity].cancel()
                continue
            request_id = request["id"]
            if type(request_id) not in {int, str} or len(encoded(request_id)) > 200:
                respond(None, error={"code": -32600, "message": "Invalid request ID"})
                continue
            identity = encoded(request_id).decode()
            if method == "initialize" and not initialized:
                ready = await _relay_call(config, {"method": "ready"})
                if ready != {"ok": True}:
                    raise ValueError("Relay did not initialize")
                version = params.get("protocolVersion")
                supported = {"2024-11-05", "2025-03-26", "2025-06-18"}
                respond(request_id, {
                    "protocolVersion": version if isinstance(version, str) and version in supported
                    else "2025-06-18",
                    "serverInfo": {"name": "Kira Desktop", "version": __version__},
                    "capabilities": {"tools": {}},
                    "instructions": "Kira tools are bound to the current project and chat. "
                                    "A queued team starts only after this turn completes."})
                initialized = True
            elif not initialized or identity in pending or len(pending) >= MAX_RELAY_CLIENTS:
                respond(request_id, error={"code": -32600, "message": "Request not available"})
            else:
                pending[identity] = asyncio.create_task(dispatch(request, identity))
    finally:
        transport.close()
        for task in tuple(pending.values()):
            task.cancel()
        await asyncio.gather(*pending.values(), return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Private Kira per-run MCP adapter")
    parser.add_argument("descriptor", type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(_stdio(_descriptor(args.descriptor)))
    except (OSError, ValueError, ClientToolError, TimeoutError):
        # Do not print descriptors, capability tokens, tool input or tracebacks.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
