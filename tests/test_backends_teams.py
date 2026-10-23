"""Deterministic team bridge controls, separate from live model qualification."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from kira.backends import codex
from kira.backends.client_tools import MAX_CLIENT_TOOL_BYTES, ClientToolBridge, ClientToolError
from kira.domain.contracts import RunContext


def tool_specs():
    return [{"type": "namespace", "name": "team", "description": "Project team mailbox",
             "tools": [{"type": "function", "name": name, "description": name,
                        "inputSchema": {"type": "object", "properties": {},
                                        "additionalProperties": False}}
                       for name in ("send_message", "read_messages")]}]


def notification(method, **params):
    return {"method": method, "params": {"threadId": "team-thread", "turnId": "team-turn",
                                         **params}}


def callback(request_id=41, call_id="call-1", **overrides):
    params = {"callId": call_id, "namespace": "team", "tool": "send_message",
              "arguments": {"to": "reviewer", "content": "Fixture result"}, **overrides}
    return {"id": request_id, **notification("item/tool/call", **params)}


def completed():
    return notification("turn/completed", turn={"id": "team-turn", "status": "completed"})


def context(tmp_path):
    calls, events = [], []
    async def emit(kind, payload):
        events.append((kind, payload))
    async def execute(*_):
        raise AssertionError("A Codex native tool must not execute through Kira")
    async def approve(_):
        raise AssertionError("No approval should be fabricated")
    async def client_call(call_id, name, args):
        calls.append((call_id, name, args))
        return {"ok": True, "message_id": "fixture-message", "seq": 1}
    ctx = RunContext("fixture-team-run", {"root": str(tmp_path), "permission": "read_only"},
                     {"model": "fixture-model", "settings": {"reasoning_effort": "ultra"}},
                     "Coordinate fixture work", "Fixture project context", emit, execute,
                     approve, asyncio.Event(), client_tools=tool_specs(),
                     client_tool_call=client_call, disable_native_agents=True)
    return ctx, calls, events


class TeamServer:
    instances = []
    queued = []
    feature_result = None

    def __init__(self, _):
        self.notifications = asyncio.Queue()
        self.requests, self.responses = [], []
        self.process = SimpleNamespace(pid=34567)
        self.experimental = False
        self.started = False
        self.closed = False
        self.instances.append(self)

    async def start(self, *, experimental_api=True):
        # Same default as the production AppServer.start.
        self.experimental = experimental_api
        self.started = True

    async def close(self):
        self.closed = True

    async def request(self, method, params, timeout=30):
        self.requests.append((method, copy.deepcopy(params)))
        if method == "thread/goal/get":
            return {"goal": None}
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "team-thread"}, "model": params["model"],
                    "reasoningEffort": params["config"]["model_reasoning_effort"],
                    "sandbox": {"type": "readOnly", "networkAccess": False}}
        if method == "experimentalFeature/list":
            return self.feature_result if self.feature_result is not None else {
                "data": [{"name": name, "enabled": False} for name in codex.NATIVE_AGENT_FEATURES],
                "nextCursor": None}
        if method == "turn/start":
            for entry in self.queued:
                await self.notifications.put(entry)
            return {"turn": {"id": "team-turn"}}
        if method == "turn/interrupt":
            await self.notifications.put(notification("turn/completed", turn={
                "id": "team-turn", "status": "interrupted"}))
            return {}
        raise AssertionError(method)

    async def respond(self, request_id, result):
        self.responses.append((request_id, result))


@pytest.fixture
def server(monkeypatch):
    TeamServer.instances, TeamServer.queued = [], []
    TeamServer.feature_result = None
    monkeypatch.setattr(codex, "AppServer", TeamServer)
    return TeamServer


async def test_registered_mailbox_executes_once_and_native_tools_remain_observations(tmp_path, server):
    ctx, calls, events = context(tmp_path)
    dynamic = {"id": "call-1", "type": "dynamicToolCall", "namespace": "team",
               "tool": "send_message", "arguments": {"content": "Fixture"}}
    native = {"id": "native-1", "type": "commandExecution", "status": "completed",
              "exitCode": 0, "aggregatedOutput": "Fixture native output"}
    server.queued = [notification("item/started", item=dynamic), callback(), callback(42),
                     notification("item/completed", item={**dynamic, "status": "completed"}),
                     notification("item/completed", item=native), completed()]
    recorded = []
    async def started(pid):
        assert not server.instances[0].requests
        recorded.append(pid)
    ctx.provider_started = started
    result = await codex.CodexBackend().run(ctx)
    rpc = server.instances[0]
    assert result["state"] == "completed"
    assert recorded == [34567] and rpc.experimental and rpc.closed
    assert len(calls) == 1 and calls[0][:2] == ("call-1", "team.send_message")
    assert rpc.responses[0][1] == rpc.responses[1][1]
    response = rpc.responses[0][1]
    assert response["success"] is True
    assert json.loads(response["contentItems"][0]["text"])["message_id"] == "fixture-message"
    assert [payload["name"] for kind, payload in events if kind.startswith("tool.")] == [
        "codex.commandExecution"]
    request_map = dict(rpc.requests)
    assert request_map["thread/start"]["dynamicTools"] == tool_specs()
    assert request_map["thread/start"]["config"]["features"] == dict.fromkeys(
        codex.NATIVE_AGENT_FEATURES, False)
    assert request_map["experimentalFeature/list"]["threadId"] == "team-thread"
    assert request_map["turn/start"]["model"] == "fixture-model"
    assert request_map["turn/start"]["effort"] == "ultra"
    assert request_map["turn/start"]["sandboxPolicy"]["type"] == "readOnly"


async def test_resume_restores_thread_tools_without_redefining_them(tmp_path, server):
    ctx, calls, _ = context(tmp_path)
    ctx.session["backend_ref"] = "team-thread"
    server.queued = [callback(tool="read_messages", arguments={"after_seq": 0}), completed()]
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    rpc = server.instances[0]
    assert rpc.requests[0] == ("thread/goal/get", {"threadId": "team-thread"})
    assert rpc.requests[1][0] == "thread/resume"
    assert "dynamicTools" not in rpc.requests[1][1]
    assert rpc.experimental
    assert calls[0][1] == "team.read_messages"


@pytest.mark.parametrize("features", [
    {"data": [{"name": "multi_agent", "enabled": True}]},
    {"data": [{"name": "multi_agent", "enabled": False}]},
    {"data": [{"name": "multi_agent", "enabled": 0}]},
    {"data": None},
    {"data": [], "nextCursor": "repeated"},
])
async def test_team_fails_before_inference_if_native_delegation_is_unverified(
        tmp_path, server, features):
    ctx, calls, _ = context(tmp_path)
    server.feature_result = features
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed"
    assert not calls
    assert not any(method == "turn/start" for method, _ in server.instances[0].requests)
    assert server.instances[0].closed


async def test_provider_ownership_failure_prevents_thread_and_turn_start(tmp_path, server):
    ctx, calls, _ = context(tmp_path)
    async def reject(_):
        raise RuntimeError("Fixture ownership persistence failure")
    ctx.provider_started = reject
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed" and "ownership" in result["error"]
    assert not server.instances[0].requests and server.instances[0].closed
    assert not calls


@pytest.mark.parametrize("overrides,code", [
    ({"threadId": "unrelated-thread"}, "WRONG_RUN"),
    ({"turnId": "unrelated-turn"}, "WRONG_RUN"),
    ({"callId": ""}, "INVALID_CALL_ID"),
    ({"namespace": "other"}, "UNSUPPORTED_TOOL"),
    ({"tool": "run_command"}, "UNSUPPORTED_TOOL"),
    ({"arguments": "unparsed JSON"}, "INVALID_ARGUMENTS"),
    ({"arguments": {"content": "x" * MAX_CLIENT_TOOL_BYTES}}, "ARGUMENTS_TOO_LARGE"),
    ({"arguments": {"content": float("inf")}}, "INVALID_ARGUMENTS"),
])
async def test_invalid_callback_returns_structured_failure_without_effects(
        tmp_path, server, overrides, code):
    ctx, calls, _ = context(tmp_path)
    request = callback()
    request["params"].update(overrides)
    server.queued = [request, completed()]
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    response = server.instances[0].responses[0][1]
    assert response["success"] is False
    assert json.loads(response["contentItems"][0]["text"])["error"]["code"] == code
    assert not calls


async def test_conflicting_callback_identity_stops_without_another_effect(tmp_path, server):
    ctx, calls, _ = context(tmp_path)
    server.queued = [callback(), callback(42, arguments={"content": "changed"}), completed()]
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "interrupted"
    assert "reused" in result["error"] and len(calls) == 1
    assert len(server.instances[0].responses) == 1


@pytest.mark.parametrize("response", [
    {"ok": True, "content": "x" * MAX_CLIENT_TOOL_BYTES},
    {"ok": "yes"},
    {"ok": True, "content": float("nan")},
])
async def test_invalid_callback_outcome_never_claims_safe_failure_or_replays(
        tmp_path, server, response):
    ctx, calls, _ = context(tmp_path)
    async def invalid(*args):
        calls.append(args)
        return response
    ctx.client_tool_call = invalid
    server.queued = [callback(), callback(42), completed()]
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "interrupted" and len(calls) == 1
    assert not server.instances[0].responses


async def test_callback_exception_is_unknown_and_does_not_expose_exception_payload(tmp_path, server):
    ctx, _, _ = context(tmp_path)
    async def failing(*_):
        raise RuntimeError("private fixture payload")
    ctx.client_tool_call = failing
    server.queued = [callback(), completed()]
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "interrupted"
    assert "private fixture payload" not in str(result)
    assert "durable state" in result["error"]


async def test_cancellation_during_mailbox_wait_replies_and_requires_terminal_confirmation(
        tmp_path, server):
    ctx, _, _ = context(tmp_path)
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def waiting(*_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    ctx.client_tool_call = waiting
    server.queued = [callback()]
    task = asyncio.create_task(codex.CodexBackend().run(ctx))
    await asyncio.wait_for(entered.wait(), 2)
    ctx.cancel.set()
    result = await asyncio.wait_for(task, 2)
    assert result["state"] == "cancelled" and result["outcome"]["interruption_confirmed"]
    assert stopped.is_set()
    rpc = server.instances[0]
    assert rpc.responses[0][1]["success"] is False
    assert any(method == "turn/interrupt" for method, _ in rpc.requests)


async def test_structured_planner_preserves_final_text_and_needs_no_experimental_tools(
        tmp_path, server):
    ctx, calls, _ = context(tmp_path)
    ctx.client_tools, ctx.client_tool_call = [], None
    ctx.output_schema = {"type": "object", "properties": {"agents": {"type": "array"}}}
    final = '{"agents": [{"name": "Reviewer"}]}'
    server.queued = [
        notification("item/completed", item={"id": "comment", "type": "agentMessage",
                                              "phase": "commentary", "text": "Planning fixture"}),
        notification("item/completed", item={"id": "final", "type": "agentMessage",
                                              "phase": "final_answer", "text": final}),
        completed()]
    result = await codex.CodexBackend().run(ctx)
    assert result["outcome"]["final_text"] == final and not calls
    rpc = server.instances[0]
    requests = dict(rpc.requests)
    # A planner registers no dynamic (experimental) tools on its thread.
    assert "dynamicTools" not in requests["thread/start"]
    assert requests["turn/start"]["outputSchema"] == ctx.output_schema


async def test_structured_output_does_not_use_commentary_as_final_text(tmp_path, server):
    ctx, _, _ = context(tmp_path)
    ctx.output_schema = {"type": "object"}
    server.queued = [notification("item/completed", item={"id": "comment", "type": "agentMessage",
                      "phase": "commentary", "text": "Not a structured final result"}), completed()]
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed" and result["outcome"]["final_text"] is None


async def test_retry_cache_resource_bound_precedes_next_effect(tmp_path):
    ctx, calls, _ = context(tmp_path)
    bridge = ClientToolBridge(ctx, {"team.send_message"}, "team-thread", "team-turn")
    bridge.cache_bytes = 8 * 1024 * 1024
    with pytest.raises(ClientToolError, match="resource bound"):
        await bridge.call(callback()["params"])
    assert not calls


async def test_invalid_tool_registration_is_rejected_before_server_start(tmp_path, server):
    ctx, _, _ = context(tmp_path)
    ctx.client_tools[0]["tools"][0]["name"] = "execute_shell"
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed"
    assert not server.instances[0].started and server.instances[0].closed
