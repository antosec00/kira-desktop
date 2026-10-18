"""Goal callback wiring in the real adapters, using only synthetic transports."""

import json

import httpx
import pytest

from kira.backends import codex
from kira.backends.client_tools import ClientToolError, validate_tools
from kira.backends.ollama import OllamaBackend
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.assistant import assistant_tool_specs
from kira.domain.autonomous_goals import goal_tool_specs
from kira.domain.computer import computer_tool_specs
from kira.domain.web import browser_tool_specs
from kira.service.history_tools import history_tool_specs
from test_backends_openai_chatgpt import FakeAuth, call, context, message, terminal, wire
from test_backends_ollama import ChunkStream, context as ollama_context, install_transport
from test_backends_teams import callback as codex_callback, completed as codex_completed
from test_backends_teams import context as codex_context, server as server


def arguments(name):
    return {} if name == "status" else {"status": "completed", "summary": "Completed synthetic writing task",
        "next_step": "", "evidence": [], "deliverable": "The requested synthetic text."}


def test_goal_namespace_fits_with_existing_web_computer_and_history(tmp_path):
    ctx, _, _ = context(tmp_path)
    ctx.client_tools = [*assistant_tool_specs(), {"type": "namespace", "name": "kira_history",
                        "description": "Original recorded history", "tools": [{"type": "function", "name": tool["name"],
                            "description": tool["description"], "inputSchema": tool["parameters"]}
                            for tool in history_tool_specs()]}, *browser_tool_specs(),
                        *computer_tool_specs(), goal_tool_specs(writable=True)]
    async def callback(*_):
        return {"ok": True}
    ctx.client_tool_call = callback
    assert len(ctx.client_tools) == 5
    assert {"goal.status", "goal.checkpoint", "computer.act", "browser.navigate"} <= validate_tools(ctx)
    ctx.client_tools.append(goal_tool_specs(writable=False))
    with pytest.raises(ClientToolError):
        validate_tools(ctx)


@pytest.mark.parametrize("name", ["status", "checkpoint"])
async def test_direct_goal_namespace_maps_back_to_exact_scoped_callback(tmp_path, name):
    ctx, _, executed = context(tmp_path)
    ctx.client_tools = [goal_tool_specs(writable=True)]
    received, bodies = [], []
    args = arguments(name)
    async def callback(identity, tool, payload):
        received.append((identity, tool, payload))
        return {"ok": True, "committed_completion": False, "checkpoint": payload}
    ctx.client_tool_call = callback
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        definition = next(item for item in body["tools"] if item["name"] == "goal")
        assert {tool["name"] for tool in definition["tools"]} == {"status", "checkpoint"}
        assert body["model"] == ctx.model
        if len(bodies) == 1:
            return wire([terminal([{**call(name=name, namespace="goal", args=args),
                                    "arguments": json.dumps(args)}])])
        assert json.loads(body["input"][-1]["output"])["committed_completion"] is False
        return wire([terminal([message("Synthetic cycle finished")])])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed", result
        assert received == [("call-1", "goal." + name, args)]
        assert not executed
    finally:
        await backend.close()


@pytest.mark.parametrize("name", ["status", "checkpoint"])
async def test_ollama_goal_flattening_preserves_callback_and_result(tmp_path, name):
    ctx, _, executed = ollama_context(tmp_path)
    ctx.client_tools = [goal_tool_specs(writable=True)]
    received, bodies = [], []
    args = arguments(name)
    async def callback(identity, tool, payload):
        received.append((tool, payload))
        return {"ok": True, "committed_completion": False}
    ctx.client_tool_call = callback
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        body = json.loads(request.content)
        bodies.append(body)
        assert {"goal__status", "goal__checkpoint"} <= {item["function"]["name"] for item in body["tools"]}
        if len(bodies) == 1:
            return httpx.Response(200, stream=ChunkStream([{"message": {"tool_calls": [
                {"function": {"name": "goal__" + name, "arguments": args}}]}, "done": True}]))
        assert any(row["role"] == "tool" and json.loads(row["content"])["committed_completion"] is False
                   for row in body["messages"])
        return httpx.Response(200, stream=ChunkStream([{"message": {"content": "Cycle finished"}, "done": True}]))
    backend = OllamaBackend()
    backend._probed[ctx.model] = True
    await install_transport(backend, handler)
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed", result
        assert received == [("goal." + name, args)] and not executed
    finally:
        await backend.close()


@pytest.mark.parametrize("name", ["status", "checkpoint"])
async def test_codex_goal_dynamic_tools_are_correlated_and_deduplicated(tmp_path, server, name):
    ctx, calls, _ = codex_context(tmp_path)
    ctx.client_tools = [goal_tool_specs(writable=True)]
    args = arguments(name)
    server.queued = [codex_callback(namespace="goal", tool=name, arguments=args),
                     codex_callback(42, namespace="goal", tool=name, arguments=args), codex_completed()]
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed", result
    assert calls == [("call-1", "goal." + name, args)]
    rpc = server.instances[0]
    assert dict(rpc.requests)["thread/start"]["dynamicTools"] == ctx.client_tools
    assert rpc.responses[0][1] == rpc.responses[1][1]
    assert rpc.responses[0][1]["success"] is True
