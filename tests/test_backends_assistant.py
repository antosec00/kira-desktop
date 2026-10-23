"""Bounded assistant transport tests; model inference is not simulated evidence."""

import asyncio
import copy
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kira.backends import client_tools, codex, kira_mcp
from kira.backends.client_tools import ClientToolBridge, ClientToolError, validate_tools
from kira.domain.assistant import ASSISTANT_TOOL_NAMES, assistant_tool_specs
from kira.domain.contracts import RunContext
from kira.domain.knowledge import knowledge_tool_specs
from kira.domain.project_tools import PROJECT_TOOL_CALL_TIMEOUT, project_tool_specs
from kira.domain.teams import mailbox_tool_specs


def context(tmp_path):
    calls, events = [], []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def forbidden(*_):
        raise AssertionError("The bridge cannot execute native tools or grant permissions")

    async def callback(*args):
        calls.append(args)
        return {"ok": True, "fixture": "Bound to the originating chat"}

    ctx = RunContext("fixture-run", {"root": str(tmp_path), "permission": "read_only"},
                     {"model": "fixture-model", "settings": {"reasoning_effort": "xhigh"}},
                     "Organize fixture work", "Fixture context", emit, forbidden, forbidden,
                     asyncio.Event(), client_tools=assistant_tool_specs(),
                     client_tool_call=callback)
    return ctx, calls, events


def callback_params(**overrides):
    return {"threadId": "fixture-run", "turnId": "fixture-run", "callId": "one-call",
            "namespace": "kira", "tool": "environment", "arguments": {}, **overrides}


def test_schema_has_no_provider_or_project_control_and_cannot_mutate_shared_schema(tmp_path):
    ctx, _, _ = context(tmp_path)
    assert validate_tools(ctx) == ASSISTANT_TOOL_NAMES
    schemas = assistant_tool_specs()[0]["tools"]
    for tool in schemas:
        fields = tool["inputSchema"]["properties"]
        assert not {"project_id", "root", "model", "backend", "permission", "settings"} & fields.keys()
        assert tool["inputSchema"]["additionalProperties"] is False
    schemas[0]["name"] = "mutated"
    assert assistant_tool_specs()[0]["tools"][0]["name"] == "environment"
    ctx.client_tools += mailbox_tool_specs()
    with pytest.raises(ClientToolError, match="cannot share the same scope"):
        validate_tools(ctx)


async def test_concurrent_duplicate_has_one_effect_and_conflicting_identity_is_rejected(tmp_path):
    ctx, calls, _ = context(tmp_path)
    gate = asyncio.Event()

    async def callback(*args):
        calls.append(args)
        await gate.wait()
        return {"ok": True}

    ctx.client_tool_call = callback
    bridge = ClientToolBridge(ctx, set(ASSISTANT_TOOL_NAMES), ctx.run_id, ctx.run_id)
    first = asyncio.create_task(bridge.call(callback_params()))
    second = asyncio.create_task(bridge.call(callback_params()))
    await asyncio.sleep(0)
    gate.set()
    replies = await asyncio.gather(first, second)
    assert replies[0] == replies[1] and len(calls) == 1
    with pytest.raises(ClientToolError, match="different input"):
        await bridge.call(callback_params(arguments={"unexpected": True}))
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["exception", "invalid_result", "timeout"])
async def test_unknown_callback_outcome_is_never_dispatched_again(tmp_path, monkeypatch, failure):
    ctx, calls, _ = context(tmp_path)
    monkeypatch.setattr(client_tools, "CLIENT_TOOL_TIMEOUT", 0.01)

    async def callback(*args):
        calls.append(args)
        if failure == "exception":
            raise ValueError("private fixture details")
        if failure == "invalid_result":
            return {"ok": "unverified"}
        await asyncio.Event().wait()

    ctx.client_tool_call = callback
    bridge = ClientToolBridge(ctx, set(ASSISTANT_TOOL_NAMES), ctx.run_id, ctx.run_id)
    for _ in range(2):
        with pytest.raises(ClientToolError) as error:
            await bridge.call(callback_params())
        assert "private fixture details" not in str(error.value)
    assert len(calls) == 1


async def test_malformed_tool_namespace_is_rejected_without_effect(tmp_path):
    ctx, calls, _ = context(tmp_path)
    bridge = ClientToolBridge(ctx, set(ASSISTANT_TOOL_NAMES), ctx.run_id, ctx.run_id)
    result = await bridge.call(callback_params(namespace=["kira"]))
    assert result["success"] is False and not calls


async def test_mcp_disconnect_cancels_callback_and_retains_uncertain_identity(tmp_path):
    ctx, calls, _ = context(tmp_path)
    entered, exited = asyncio.Event(), asyncio.Event()

    async def callback(*args):
        calls.append(args)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    ctx.client_tool_call = callback
    relay = kira_mcp.KiraMcpRelay(ctx, set(ASSISTANT_TOOL_NAMES))
    await relay.start()
    try:
        descriptor = kira_mcp._descriptor(relay.directory / "bridge.json")
        request = {"call_id": "one", "name": "kira.environment", "arguments": {}}
        pending = asyncio.create_task(kira_mcp._relay_call(descriptor, request))
        await asyncio.wait_for(entered.wait(), 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await asyncio.wait_for(exited.wait(), 1)
        repeat = await kira_mcp._relay_call(descriptor, request)
        assert repeat["isError"] and len(calls) == 1
    finally:
        await relay.close()


async def test_relay_is_private_bound_and_removed_on_close(tmp_path):
    ctx, calls, _ = context(tmp_path)
    relay = kira_mcp.KiraMcpRelay(ctx, set(ASSISTANT_TOOL_NAMES))
    try:
        overlay = await relay.start()
        assert relay.directory is not None
        assert stat.S_IMODE(relay.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((relay.directory / "bridge.json").stat().st_mode) == 0o600
        assert stat.S_IMODE((relay.directory / "relay.sock").stat().st_mode) == 0o600
        assert relay.capability not in json.dumps(overlay)
        assert overlay["tools"] == {name.split(".", 1)[1]: {"approval_mode": "approve"}
                                     for name in ASSISTANT_TOOL_NAMES}
        assert "default_tools_approval_mode" not in overlay
        descriptor = kira_mcp._descriptor(relay.directory / "bridge.json")
        reply = await kira_mcp._relay_call(descriptor, {
            "call_id": "one", "name": "kira.environment", "arguments": {}})
        assert not reply["isError"] and calls == [("one", "kira.environment", {})]
        wrong = {**descriptor, "capability": "0" * 64}
        with pytest.raises((ValueError, OSError)):
            await kira_mcp._relay_call(wrong, {
                "call_id": "two", "name": "kira.environment", "arguments": {}})
        assert len(calls) == 1
    finally:
        await relay.close()
    assert not relay.directory.exists() and not relay.tasks and not relay.capability


async def test_relay_closes_pending_callback_and_does_not_replay_cancelled_identity(tmp_path):
    ctx, calls, _ = context(tmp_path)
    entered, exited = asyncio.Event(), asyncio.Event()

    async def callback(*args):
        calls.append(args)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    ctx.client_tool_call = callback
    relay = kira_mcp.KiraMcpRelay(ctx, set(ASSISTANT_TOOL_NAMES))
    pending = None
    try:
        await relay.start()
        descriptor = kira_mcp._descriptor(relay.directory / "bridge.json")
        request = {"call_id": "one", "name": "kira.environment", "arguments": {}}
        pending = asyncio.create_task(kira_mcp._relay_call(descriptor, request))
        await asyncio.wait_for(entered.wait(), 1)
        ctx.cancel.set()
        result = await asyncio.wait_for(pending, 2)
        assert result["isError"] and exited.is_set()
        # Retrying the cancelled identity returns its recorded cancellation; the
        # callback is never dispatched again.
        repeat = await asyncio.wait_for(kira_mcp._relay_call(descriptor, request), 10)
        assert repeat == result and len(calls) == 1
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await relay.close()
    assert not relay.tasks and not relay.directory.exists()


@pytest.mark.parametrize("extended", [False, True])
async def test_stdio_actual_process_initializes_lists_and_calls_relay(tmp_path, extended):
    ctx, calls, _ = context(tmp_path)
    if extended:
        ctx.client_tools[0]["tools"] += knowledge_tool_specs() + project_tool_specs()
    names = validate_tools(ctx)
    relay = kira_mcp.KiraMcpRelay(ctx, names)
    process = None
    try:
        overlay = await relay.start()
        process = await asyncio.create_subprocess_exec(
            overlay["command"], *overlay["args"], stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        async def request(request_id, method, params):
            process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id,
                                            "method": method, "params": params}) + "\n").encode())
            await process.stdin.drain()
            return json.loads(await asyncio.wait_for(process.stdout.readline(), 30))["result"]

        initialized = await request(1, "initialize", {"protocolVersion": "2025-06-18"})
        assert initialized["serverInfo"]["name"] == "Kira Desktop" and relay.ready
        tools = (await request(2, "tools/list", {}))["tools"]
        assert {"kira." + tool["name"] for tool in tools} == names
        if extended:
            assert overlay["tool_timeout_sec"] == PROJECT_TOOL_CALL_TIMEOUT
            gateway = next(item for item in tools if item["name"] == "project_tools")
            assert gateway["inputSchema"]["properties"]["payload"]["type"] == "object"
        result = await request(3, "tools/call", {"name": "environment", "arguments": {}})
        repeat = await request(3, "tools/call", {"name": "environment", "arguments": {}})
        assert not result["isError"] and repeat == result and len(calls) == 1
        assert calls[0][1:] == ("kira.environment", {})
        unknown = await request(4, "tools/call", {"name": "execute", "arguments": {}})
        assert unknown["isError"] and len(calls) == 1
        if extended:
            arguments = {"action": "describe", "payload": {"action": "register"}}
            gateway_result = await request(5, "tools/call", {"name": "project_tools", "arguments": arguments})
            assert not gateway_result["isError"] and calls[-1][1:] == ("kira.project_tools", arguments)
            guide_result = await request(6, "tools/call", {"name": "help_read", "arguments": {"ref": "app:identity"}})
            assert not guide_result["isError"] and calls[-1][1] == "kira.help_read"
            continuation = {"delegation_id": "fixture-delegation", "round_limit": 0}
            resumed = await request(7, "tools/call", {"name": "team_resume", "arguments": continuation})
            assert not resumed["isError"] and calls[-1][1:] == ("kira.team_resume", continuation)
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0 and await process.stderr.read() == b""
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await relay.close()


SYSTEM_PYTHON = "/usr/bin/python3"


def system_python_version():
    """The host interpreter's (major, minor) version, or None when it cannot run."""
    script = "import sys; print(*sys.version_info[:2])"
    try:
        output = subprocess.run([SYSTEM_PYTHON, "-I", "-c", script], capture_output=True,
                                text=True, timeout=30, check=True).stdout
        return tuple(int(part) for part in output.split())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


async def test_installed_private_library_runs_with_system_python_and_unrelated_cwd(tmp_path):
    # The installed library deliberately runs under the host interpreter, which
    # must support the syntax of the Python version this source tree targets.
    version = system_python_version()
    if version is None or version < sys.version_info[:2]:
        pytest.skip(f"{SYSTEM_PYTHON} is missing or older than "
                    f"Python {sys.version_info.major}.{sys.version_info.minor}")
    ctx, calls, _ = context(tmp_path)
    relay = kira_mcp.KiraMcpRelay(ctx, set(ASSISTANT_TOOL_NAMES))
    process = None
    try:
        overlay = await relay.start()
        package = Path(kira_mcp.__file__).resolve().parents[1]
        library = tmp_path / "opt" / "kira-desktop" / "lib"
        shutil.copytree(package, library / "kira", ignore=shutil.ignore_patterns("__pycache__"))
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        poison = unrelated / "kira"
        poison.mkdir()
        (poison / "__init__.py").write_text("raise RuntimeError('Untrusted cwd import')")
        entry = library / "kira" / "backends" / "kira_mcp_entry.py"
        process = await asyncio.create_subprocess_exec(
            SYSTEM_PYTHON, "-I", str(entry), overlay["args"][-1], cwd=unrelated,
            env={"PATH": os.defpath, "PYTHONPATH": str(unrelated)},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        for request_id, method, params in [
            (1, "initialize", {"protocolVersion": "2025-06-18"}),
            (2, "tools/call", {"name": "environment", "arguments": {}}),
        ]:
            process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id,
                                            "method": method, "params": params}) + "\n").encode())
            await process.stdin.drain()
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
            assert "error" not in response and not response["result"].get("isError")
        assert calls[0][1] == "kira.environment"
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0 and await process.stderr.read() == b""
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await relay.close()


@pytest.mark.parametrize("unsafe", ["descriptor_mode", "directory_mode", "symlink"])
async def test_descriptor_rejects_unsafe_paths(tmp_path, unsafe):
    ctx, _, _ = context(tmp_path)
    relay = kira_mcp.KiraMcpRelay(ctx, set(ASSISTANT_TOOL_NAMES))
    await relay.start()
    descriptor = relay.directory / "bridge.json"
    try:
        if unsafe == "descriptor_mode":
            descriptor.chmod(0o644)
        elif unsafe == "directory_mode":
            relay.directory.chmod(0o755)
        else:
            alternate = tmp_path / "descriptor.json"
            alternate.write_bytes(descriptor.read_bytes())
            descriptor.unlink()
            descriptor.symlink_to(alternate)
        with pytest.raises((ValueError, OSError)):
            kira_mcp._descriptor(descriptor)
    finally:
        await relay.close()


class AssistantServer:
    instances = []
    valid_tools = True

    def __init__(self, _):
        self.process = SimpleNamespace(pid=123456)
        self.notifications = asyncio.Queue()
        self.requests = []
        self.closed = False
        self.instances.append(self)

    async def start(self, **_):
        pass

    async def close(self):
        self.closed = True

    async def request(self, method, params, timeout=30):
        self.requests.append((method, copy.deepcopy(params)))
        if method == "thread/goal/get":
            return {"goal": None}
        if method in {"thread/start", "thread/resume"}:
            overlay = params["config"]["mcp_servers"]["kira"]
            self.config = kira_mcp._descriptor(Path(overlay["args"][-1]))
            await kira_mcp._relay_call(self.config, {"method": "ready"})
            return {"thread": {"id": "fixture-thread"}, "model": params["model"],
                    "reasoningEffort": params["config"]["model_reasoning_effort"],
                    "sandbox": {"type": "readOnly", "networkAccess": False}}
        if method == "experimentalFeature/list":
            return {"data": [{"name": name, "enabled": False}
                             for name in codex.NATIVE_AGENT_FEATURES]}
        if method == "mcpServerStatus/list":
            return {"data": [{"name": "kira", "runtimeStatus": "connected", "tools": {
                name: {"name": name.split(".")[1]} for name in (
                    self.config["tools"] if self.valid_tools else [])}}]}
        if method == "turn/start":
            call = {"call_id": "fixture-call", "name": "kira.environment", "arguments": {}}
            result = await kira_mcp._relay_call(self.config, call)
            assert await kira_mcp._relay_call(self.config, call) == result
            for method_name, status in (("item/started", "inProgress"),
                                        ("item/completed", "completed")):
                await self.notifications.put({"method": method_name, "params": {
                    "threadId": "fixture-thread", "turnId": "fixture-turn",
                    "item": {"type": "mcpToolCall", "id": "fixture-native", "server": "kira",
                             "tool": "environment", "arguments": {}, "status": status,
                             "result": result if status == "completed" else None}}})
            await self.notifications.put({"method": "turn/completed", "params": {
                "threadId": "fixture-thread", "turn": {"id": "fixture-turn", "status": "completed"}}})
            return {"turn": {"id": "fixture-turn"}}
        raise AssertionError(method)


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("extended", [False, True])
async def test_codex_attaches_tools_with_config_for_existing_and_new_threads(
        tmp_path, monkeypatch, resume, extended):
    ctx, calls, events = context(tmp_path)
    if extended:
        ctx.client_tools[0]["tools"] += knowledge_tool_specs() + project_tool_specs()
    ctx.assistant_instructions = "You are the Kira Desktop assistant, using verified tools."
    if resume:
        ctx.session["backend_ref"] = "fixture-thread"
    monkeypatch.setattr(AssistantServer, "instances", [])
    monkeypatch.setattr(AssistantServer, "valid_tools", True)
    monkeypatch.setattr(codex, "AppServer", AssistantServer)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed" and len(calls) == 1
    assert calls[0][1] == "kira.environment"
    rpc = AssistantServer.instances[0]
    requests = dict(rpc.requests)
    thread = requests["thread/resume" if resume else "thread/start"]
    assert thread["developerInstructions"] == ctx.assistant_instructions
    assert "Fixture context" not in thread["developerInstructions"]
    assert "dynamicTools" not in thread
    assert thread["model"] == "fixture-model"
    assert requests["turn/start"]["effort"] == "xhigh"
    assert "access_programs" not in thread["config"]
    assert set(thread["config"]["mcp_servers"]) == {"kira"}
    assert thread["approvalPolicy"] == requests["turn/start"]["approvalPolicy"] == "never"
    assert requests["turn/start"]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert thread["config"]["mcp_servers"]["kira"]["tools"] == {
        name.split(".", 1)[1]: {"approval_mode": "approve"} for name in validate_tools(ctx)}
    assert not Path(thread["config"]["mcp_servers"]["kira"]["args"][-1]).exists()
    assert rpc.closed
    observed = [(kind, payload) for kind, payload in events if kind.startswith("tool.")]
    assert [kind for kind, _ in observed] == ["tool.started", "tool.completed"]
    assert observed[-1][1]["result"]["observed_only"] is True
    assert rpc.config["capability"] not in str(events)


async def test_missing_mcp_tools_prevents_inference_and_cleans_up(tmp_path, monkeypatch):
    ctx, calls, _ = context(tmp_path)
    # Subclasses in other modules inherit valid_tools; monkeypatch restores it.
    monkeypatch.setattr(AssistantServer, "instances", [])
    monkeypatch.setattr(AssistantServer, "valid_tools", False)
    monkeypatch.setattr(codex, "AppServer", AssistantServer)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed" and not calls
    rpc = AssistantServer.instances[0]
    requests = dict(rpc.requests)
    assert "turn/start" not in requests
    assert not Path(requests["thread/start"]["config"]["mcp_servers"]["kira"]["args"][-1]).exists()
    assert rpc.closed


async def test_cancel_during_tool_connection_has_no_turn_and_cleans_up(tmp_path, monkeypatch):
    ctx, calls, _ = context(tmp_path)

    class StartingServer(AssistantServer):
        async def request(self, method, params, timeout=30):
            if method == "mcpServerStatus/list":
                ctx.cancel.set()
                return {"data": [{"name": "kira", "runtimeStatus": "starting", "tools": {}}]}
            return await super().request(method, params, timeout)

    StartingServer.instances = []
    monkeypatch.setattr(codex, "AppServer", StartingServer)
    result = await codex.CodexBackend().run(ctx)
    assert result == {"state": "cancelled", "outcome": {"started": False}} and not calls
    requests = dict(StartingServer.instances[0].requests)
    assert "turn/start" not in requests
    assert not Path(requests["thread/start"]["config"]["mcp_servers"]["kira"]["args"][-1]).exists()


async def test_kira_mcp_approval_failure_stays_visible_without_dispatching_an_action(tmp_path):
    ctx, calls, events = context(tmp_path)
    backend = codex.CodexBackend()
    for method in ("item/started", "item/completed"):
        await backend._item(ctx, method, {"type": "mcpToolCall", "id": "fixture",
                                         "server": "kira", "tool": "environment",
                                         "status": "failed", "error": {
                                             "message": "Fixture approval policy rejected tool"}}, set())
    assert not calls
    assert [kind for kind, _ in events] == ["tool.started", "tool.completed"]
    assert events[-1][1]["result"]["ok"] is False
    assert "approval policy" in events[-1][1]["result"]["error"]["message"]
    await backend._item(ctx, "item/completed", {"type": "mcpToolCall", "id": "external",
                                               "server": "other", "status": "completed"}, set())
    assert events[-1][0] == "tool.completed" and events[-1][1]["result"]["observed_only"]
