"""Codex relay regressions: goal tools, malformed MCP frames and relay shutdown."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from kira.backends import codex, kira_mcp
from kira.backends.client_tools import validate_tools
from kira.domain.assistant import ASSISTANT_TOOL_NAMES, assistant_tool_specs
from kira.domain.autonomous_goals import goal_tool_specs
from kira.domain.contracts import RunContext

CHECKPOINT = {"status": "continue", "summary": "Synthetic progress", "next_step": "Synthetic next step",
              "evidence": []}


def context(tmp_path, client_tools):
    calls, events = [], []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def forbidden(*_):
        raise AssertionError("The relay cannot execute native tools or grant permissions")

    async def callback(call_id, name, arguments):
        calls.append((call_id, name, arguments))
        return {"ok": True, "fixture": name}

    ctx = RunContext("fixture-run", {"root": str(tmp_path), "permission": "read_only"},
                     {"model": "fixture-model", "settings": {"reasoning_effort": "xhigh"}},
                     "Continue the synthetic goal", "Fixture context", emit, forbidden, forbidden,
                     asyncio.Event(), client_tools=client_tools, client_tool_call=callback)
    return ctx, calls, events


class McpProcess:
    """The real per-run MCP adapter, driven as Codex drives it over stdio."""

    def __init__(self, process):
        self.process = process

    @classmethod
    async def start(cls, overlay):
        return cls(await asyncio.create_subprocess_exec(
            overlay["command"], *overlay["args"], stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE))

    async def send(self, data: bytes):
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def reply(self):
        return json.loads(await asyncio.wait_for(self.process.stdout.readline(), 10))

    async def request(self, request_id, method, params):
        await self.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method,
                                    "params": params}).encode() + b"\n")
        return await self.reply()

    async def stop(self):
        if self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        return self.process.returncode


class RelayServer:
    """Codex App Server stand-in that attaches the real MCP adapter to the thread."""

    instances: list["RelayServer"] = []
    script: list[tuple[str, dict]] = []

    def __init__(self, _binary):
        self.process = SimpleNamespace(pid=123456)
        self.notifications = asyncio.Queue()
        self.requests = []
        self.closed = False
        self.mcp = None
        self.listed = []
        self.results = {}
        self.instances.append(self)

    async def start(self, **_):
        pass

    async def close(self):
        self.closed = True
        if self.mcp is not None:
            await self.mcp.stop()

    async def request(self, method, params, timeout=30):
        self.requests.append((method, copy.deepcopy(params)))
        if method == "thread/goal/get":
            return {"goal": None}
        if method in {"thread/start", "thread/resume"}:
            self.mcp = await McpProcess.start(params["config"]["mcp_servers"]["kira"])
            assert "result" in await self.mcp.request(1, "initialize", {"protocolVersion": "2025-06-18"})
            self.listed = (await self.mcp.request(2, "tools/list", {}))["result"]["tools"]
            return {"thread": {"id": "fixture-thread"}, "model": params["model"],
                    "reasoningEffort": params["config"]["model_reasoning_effort"],
                    "sandbox": {"type": "readOnly", "networkAccess": False}}
        if method == "experimentalFeature/list":
            return {"data": [{"name": name, "enabled": False} for name in codex.NATIVE_AGENT_FEATURES]}
        if method == "mcpServerStatus/list":
            # Codex reports the catalog the MCP server listed, under the server's names.
            return {"data": [{"name": "kira", "runtimeStatus": "connected",
                              "tools": {tool["name"]: {"name": tool["name"]} for tool in self.listed}}]}
        if method == "turn/start":
            for request_id, (name, arguments) in enumerate(self.script, start=3):
                self.results[name] = (await self.mcp.request(request_id, "tools/call", {
                    "name": name, "arguments": arguments}))["result"]
            await self.notifications.put({"method": "turn/completed", "params": {
                "threadId": "fixture-thread", "turn": {"id": "fixture-turn", "status": "completed"}}})
            return {"turn": {"id": "fixture-turn"}}
        raise AssertionError(method)


@pytest.mark.parametrize("resume", [False, True])
async def test_codex_relays_kira_and_goal_namespaces_in_one_chat(tmp_path, monkeypatch, resume):
    ctx, calls, _ = context(tmp_path, [*assistant_tool_specs(), goal_tool_specs(writable=True)])
    if resume:
        ctx.session["backend_ref"] = "fixture-thread"
    RelayServer.instances = []
    RelayServer.script = [("goal__checkpoint", CHECKPOINT), ("environment", {}), ("goal__status", {})]
    monkeypatch.setattr(codex, "AppServer", RelayServer)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed", result
    rpc = RelayServer.instances[0]
    requests = dict(rpc.requests)
    thread = requests["thread/resume" if resume else "thread/start"]
    assert "dynamicTools" not in thread and "turn/start" in requests
    listed = {tool["name"] for tool in rpc.listed}
    assert listed == ({name.split(".", 1)[1] for name in ASSISTANT_TOOL_NAMES}
                      | {"goal__status", "goal__checkpoint"})
    overlay = thread["config"]["mcp_servers"]["kira"]
    assert overlay["tools"]["goal__checkpoint"] == {"approval_mode": "approve"}
    assert set(overlay["enabled_tools"]) == listed
    assert [(name, arguments) for _, name, arguments in calls] == [
        ("goal.checkpoint", CHECKPOINT), ("kira.environment", {}), ("goal.status", {})]
    assert not any(item["isError"] for item in rpc.results.values())
    assert rpc.closed and await rpc.mcp.stop() == 0


async def test_read_only_goal_lane_relays_only_goal_status(tmp_path, monkeypatch):
    ctx, calls, _ = context(tmp_path, [*assistant_tool_specs(), goal_tool_specs(writable=False)])
    RelayServer.instances = []
    RelayServer.script = [("goal__checkpoint", CHECKPOINT), ("goal__status", {})]
    monkeypatch.setattr(codex, "AppServer", RelayServer)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed", result
    rpc = RelayServer.instances[0]
    assert "goal__checkpoint" not in {tool["name"] for tool in rpc.listed}
    assert rpc.results["goal__checkpoint"]["isError"] is True
    assert [name for _, name, _ in calls] == ["goal.status"]


async def test_stdio_adapter_answers_malformed_frames_and_keeps_serving(tmp_path):
    ctx, calls, _ = context(tmp_path, assistant_tool_specs())
    relay = kira_mcp.KiraMcpRelay(ctx, validate_tools(ctx))
    mcp = None
    try:
        mcp = await McpProcess.start(await relay.start())
        assert "result" in await mcp.request(1, "initialize", {"protocolVersion": "2025-06-18"})
        for frame in (b"[1, 2]\n", b"{not json}\n", b'{"value": NaN}\n',
                      b'{"padding": "' + b"x" * (kira_mcp.MCP_FRAME_BYTES + 4096) + b'"}\n'):
            await mcp.send(frame)
            reply = await mcp.reply()
            assert reply["id"] is None and reply["error"]["code"] == -32700, reply
        result = await mcp.request(2, "tools/call", {"name": "environment", "arguments": {}})
        assert result["id"] == 2 and not result["result"]["isError"]
        assert [name for _, name, _ in calls] == ["kira.environment"]
        assert await mcp.stop() == 0
    finally:
        if mcp is not None:
            await mcp.stop()
        await relay.close()


async def test_relay_close_cancels_in_flight_calls_before_waiting_for_connections(tmp_path):
    ctx, _, _ = context(tmp_path, assistant_tool_specs())
    entered = asyncio.Event()

    async def blocked(*_):
        entered.set()
        await asyncio.Event().wait()

    ctx.client_tool_call = blocked
    relay = kira_mcp.KiraMcpRelay(ctx, validate_tools(ctx))
    await relay.start()
    descriptor = kira_mcp._descriptor(relay.directory / "bridge.json")
    pending = asyncio.create_task(kira_mcp._relay_call(descriptor, {
        "call_id": "one", "name": "kira.environment", "arguments": {}}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(relay.close(), 5)
        assert not relay.tasks and not relay.directory.exists()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await relay.close()
