"""Synthetic research transport qualification; no live provider inference."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from kira.backends import codex, kira_mcp
from kira.backends.client_tools import ClientToolBridge, validate_tools
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.assistant import ASSISTANT_TOOL_NAMES, assistant_tool_specs
from kira.domain.computer import COMPUTER_TOOL_NAMES, computer_tool_specs
from kira.domain.research import RESEARCH_TOOL_NAMES, research_tool_specs
from kira.domain.web import BROWSER_TOOL_NAMES, browser_tool_specs
from kira.service.history_tools import history_tool_specs
from test_backends_assistant import AssistantServer, context as assistant_context
from test_backends_openai_chatgpt import FakeAuth, call, context, message, terminal, wire

ALL_KIRA_NAMES = ASSISTANT_TOOL_NAMES | RESEARCH_TOOL_NAMES


def research_namespace():
    namespaces = assistant_tool_specs()
    namespaces[0]["tools"].extend(research_tool_specs())
    return namespaces


def complete_inventory():
    history = {"type": "namespace", "name": "kira_history", "description": "Original chat sources",
               "tools": [{"type": "function", "name": item["name"], "description": item["description"],
                          "inputSchema": item["parameters"]} for item in history_tool_specs()]}
    return [*research_namespace(), history, *browser_tool_specs(), *computer_tool_specs()]


def test_research_uses_existing_kira_namespace_with_full_direct_inventory(tmp_path):
    ctx, _, _ = assistant_context(tmp_path)
    ctx.client_tools = complete_inventory()
    original = copy.deepcopy(ctx.client_tools)
    names = validate_tools(ctx)
    assert names == ALL_KIRA_NAMES | BROWSER_TOOL_NAMES | COMPUTER_TOOL_NAMES | {
        "kira_history.search", "kira_history.read"}
    assert [namespace["name"] for namespace in ctx.client_tools] == ["kira", "kira_history", "browser", "computer"]
    assert len(ctx.client_tools[0]["tools"]) == len(ALL_KIRA_NAMES)
    assert ctx.client_tools == original
    assert len(assistant_tool_specs()[0]["tools"]) == len(ASSISTANT_TOOL_NAMES)


async def test_research_callback_rejects_other_run_without_dispatch(tmp_path):
    ctx, calls, _ = assistant_context(tmp_path)
    ctx.client_tools = research_namespace()
    bridge = ClientToolBridge(ctx, validate_tools(ctx), ctx.run_id, ctx.run_id)
    result = await bridge.call({"threadId": "unrelated-run", "turnId": ctx.run_id,
        "callId": "foreign-call", "namespace": "kira", "tool": "research_list", "arguments": {}})
    assert not result["success"] and calls == []


async def test_real_local_mcp_stdio_lists_calls_and_deduplicates_research(tmp_path):
    ctx, calls, _ = assistant_context(tmp_path)
    ctx.client_tools = research_namespace()
    relay = kira_mcp.KiraMcpRelay(ctx, set(ALL_KIRA_NAMES))
    process = None
    try:
        overlay = await relay.start()
        process = await asyncio.create_subprocess_exec(overlay["command"], *overlay["args"],
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        async def request(identity, method, params):
            process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": identity, "method": method,
                "params": params}) + "\n").encode())
            await process.stdin.drain()
            return json.loads(await asyncio.wait_for(process.stdout.readline(), 30))["result"]

        initialized = await request(1, "initialize", {"protocolVersion": "2025-06-18"})
        assert initialized["serverInfo"]["name"] == "Kira Desktop" and relay.ready
        tools = (await request(2, "tools/list", {}))["tools"]
        assert {"kira." + tool["name"] for tool in tools} == ALL_KIRA_NAMES
        proposal = {"record": {"kind": "hypothesis", "title": "Synthetic proposal"}}
        first = await request(3, "tools/call", {"name": "research_propose", "arguments": proposal})
        assert not first["isError"]
        assert await request(3, "tools/call", {"name": "research_propose", "arguments": proposal}) == first
        assert len(calls) == 1 and calls[0][1:] == ("kira.research_propose", proposal)
        read = await request(4, "tools/call", {"name": "research_read", "arguments": {"record_id": "synthetic-record"}})
        assert not read["isError"] and calls[-1][1] == "kira.research_read"
        rejected = await request(5, "tools/call", {"name": "run_command", "arguments": {"argv": ["false"]}})
        assert rejected["isError"] and len(calls) == 2
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0 and await process.stderr.read() == b""
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await relay.close()
    assert not relay.directory.exists()


class ResearchServer(AssistantServer):
    instances = []
    omit_research_tool = False

    async def request(self, method, params, timeout=30):
        if method == "mcpServerStatus/list":
            self.requests.append((method, copy.deepcopy(params)))
            names = ALL_KIRA_NAMES - {"kira.research_read"} if self.omit_research_tool else ALL_KIRA_NAMES
            return {"data": [{"name": "kira", "runtimeStatus": "connected", "tools": {
                name: {"name": name.split(".")[1]} for name in names}}]}
        if method == "turn/start":
            self.requests.append((method, copy.deepcopy(params)))
            proposal = {"call_id": "research-proposal", "name": "kira.research_propose",
                        "arguments": {"record": {"kind": "experiment", "title": "Synthetic planned check"}}}
            result = await kira_mcp._relay_call(self.config, proposal)
            assert await kira_mcp._relay_call(self.config, proposal) == result
            await kira_mcp._relay_call(self.config, {"call_id": "research-read", "name": "kira.research_read",
                                                   "arguments": {"record_id": "synthetic-record"}})
            await self.notifications.put({"method": "turn/completed", "params": {
                "threadId": "fixture-thread", "turn": {"id": "fixture-turn", "status": "completed"}}})
            return {"turn": {"id": "fixture-turn"}}
        return await super().request(method, params, timeout)


@pytest.mark.parametrize("resume", [False, True])
async def test_codex_new_and_resumed_threads_require_all_kira_tools_and_keep_configuration(
        tmp_path, monkeypatch, resume):
    ctx, calls, events = assistant_context(tmp_path)
    ctx.client_tools = research_namespace()
    ctx.assistant_instructions = "Research proposals remain unaccepted until explicit review."
    if resume:
        ctx.session["backend_ref"] = "fixture-thread"
    ResearchServer.instances = []
    monkeypatch.setattr(ResearchServer, "omit_research_tool", False)
    monkeypatch.setattr(codex, "AppServer", ResearchServer)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed"
    assert [item[1] for item in calls] == ["kira.research_propose", "kira.research_read"]
    rpc = ResearchServer.instances[0]
    requests = dict(rpc.requests)
    thread = requests["thread/resume" if resume else "thread/start"]
    assert thread["model"] == "fixture-model"
    assert thread["config"]["model_reasoning_effort"] == requests["turn/start"]["effort"] == "xhigh"
    assert thread["developerInstructions"] == ctx.assistant_instructions
    assert set(thread["config"]["mcp_servers"]) == {"kira"}
    assert set(thread["config"]["mcp_servers"]["kira"]["tools"]) == {
        name.split(".")[1] for name in ALL_KIRA_NAMES}
    assert requests["turn/start"]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert thread["approvalPolicy"] == "never"
    assert "dynamicTools" not in thread and "access_programs" not in thread["config"]
    assert not Path(thread["config"]["mcp_servers"]["kira"]["args"][-1]).exists()
    assert rpc.closed and rpc.config["capability"] not in str(events)


async def test_codex_missing_research_inventory_prevents_inference(tmp_path, monkeypatch):
    ctx, calls, _ = assistant_context(tmp_path)
    ctx.client_tools = research_namespace()
    ResearchServer.instances = []
    monkeypatch.setattr(ResearchServer, "omit_research_tool", True)
    monkeypatch.setattr(codex, "AppServer", ResearchServer)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed" and calls == []
    assert "turn/start" not in dict(ResearchServer.instances[0].requests)
    assert ResearchServer.instances[0].closed


async def test_direct_responses_research_calls_preserve_model_and_complete_inventory(tmp_path):
    ctx, events, executed = context(tmp_path, permission="read_only")
    ctx.client_tools = complete_inventory()
    requests, calls = [], []
    proposal = {"record": {"kind": "issue", "title": "Synthetic unresolved question"}}

    async def callback(call_id, name, arguments):
        calls.append((call_id, name, arguments))
        return {"ok": True, "record": {"id": "synthetic-record", "revision": 1,
            "title": "Synthetic unresolved question", "accepted": name != "kira.research_propose"}}

    ctx.client_tool_call = callback

    def handler(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "fixture-model" and body["reasoning"] == {"effort": "xhigh", "summary": "auto"}
        namespaces = {tool["name"]: tool for tool in body["tools"] if tool["type"] == "namespace"}
        assert {"kira", "kira_history", "kira_browser", "kira_computer"} <= namespaces.keys()
        assert {tool["name"] for tool in namespaces["kira"]["tools"]} == {
            name.split(".")[1] for name in ALL_KIRA_NAMES}
        if len(requests) == 1:
            return wire([terminal([call(name="research_propose", namespace="kira", args=proposal, call_id="proposal-call")])])
        outputs = [json.loads(item["output"]) for item in body["input"] if item.get("type") == "function_call_output"]
        assert outputs[0]["record"]["accepted"] is False
        if len(requests) == 2:
            return wire([terminal([call(name="research_read", namespace="kira", args={"record_id": "synthetic-record"}, call_id="read-call")])])
        assert len(outputs) == 2 and outputs[1]["record"]["id"] == "synthetic-record"
        return wire([terminal([message("Synthetic transport complete")])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed", result
    finally:
        await backend.close()
    assert len(requests) == 3 and not executed
    assert calls == [("proposal-call", "kira.research_propose", proposal),
                     ("read-call", "kira.research_read", {"record_id": "synthetic-record"})]
    assert any(kind == "message.completed" and payload.get("content") == "Synthetic transport complete"
               for kind, payload in events)
