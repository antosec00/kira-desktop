"""Synthetic Kira's Computer provider loop, image bounds and callback ownership."""

import asyncio
import copy
import json

import httpx
import pytest

from kira.backends import client_tools
from kira.backends.client_tools import ClientToolBridge, ClientToolError, validate_tools
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.backends.openai_web import SCREENSHOT_OMISSION, trim_browser_screenshots
from kira.domain.assistant import assistant_tool_specs
from kira.domain.computer import COMPUTER_TOOL_NAMES, computer_tool_specs
from kira.domain.contracts import KiraError
from kira.domain.web import browser_tool_specs
from kira.service.history_tools import history_tool_specs
from test_backends_openai_chatgpt import FakeAuth, call, context, terminal, wire
from test_direct_context_backend import context as durable_context
from test_direct_context_backend import unsupported_count

PIXEL = {"type": "input_image", "image_url": "data:image/png;base64,c3ludGhldGlj", "detail": "auto"}


def register(ctx, callback):
    ctx.client_tools = computer_tool_specs()
    ctx.client_tool_call = callback


async def test_observe_act_loop_delivers_original_pixels_and_preserves_model_settings(tmp_path):
    ctx, events, executed = context(tmp_path)
    ctx.session["model"] = "fixture-coordinate-model"
    ctx.session["settings"]["reasoning_effort"] = "high"
    bodies, callbacks, images = [], [], []

    async def callback(identity, name, arguments):
        callbacks.append((identity, name, arguments))
        return {"ok": True, "browser_image_id": identity + "-image", "observation_id": identity + "-view",
                "viewport": {"width": 1280, "height": 800}, "action_executed": name == "computer.act"}

    async def image(identity):
        images.append(identity)
        return PIXEL

    register(ctx, callback)
    ctx.tool_image = image
    original_tools = copy.deepcopy(ctx.client_tools)

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["model"] == "fixture-coordinate-model" and body["reasoning"] == {"effort": "high", "summary": "auto"}
        assert not any(tool["type"] in {"computer", "computer_use_preview"} for tool in body["tools"])
        namespace = next(tool for tool in body["tools"] if tool.get("name") == "kira_computer")
        assert {tool["name"] for tool in namespace["tools"]} == {"observe", "act"}
        assert "Kira's Computer" in body["instructions"] and "action_executed=true" in body["instructions"]
        if len(bodies) == 1:
            return wire([terminal([{**call(name="observe", namespace="kira_computer"), "arguments": "{}"}])])
        output = body["input"][-1]["output"]
        assert output[1] == {**PIXEL, "detail": "original"}
        result = json.loads(output[0]["text"])
        assert result["viewport"] == {"width": 1280, "height": 800}
        if len(bodies) == 2:
            assert result["observation_id"] == "call-1-view"
            return wire([terminal([call(name="act", namespace="kira_computer", call_id="call-2",
                args={"observation_id": result["observation_id"], "action": "click", "x": 30, "y": 40})])])
        assert result["observation_id"] == "call-2-view" and result["action_executed"]
        return wire([terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert callbacks == [("call-1", "computer.observe", {}), ("call-2", "computer.act", {
            "observation_id": "call-1-view", "action": "click", "x": 30, "y": 40})]
        assert images == ["call-1-image", "call-2-image"]
        assert ctx.client_tools == original_tools and PIXEL["detail"] == "auto"
        assert not executed and "data:image" not in json.dumps(events)
    finally:
        await backend.close()


@pytest.mark.parametrize("failure", ["denied", "partial", "unknown"])
async def test_failed_actions_preserve_outcome_without_pixels_or_automatic_replay(tmp_path, failure):
    ctx, _, _ = context(tmp_path)
    calls, bodies = [], []
    result = {"ok": False, "browser_image_id": "untrusted-unused-image", "error": {"code": failure},
              "action_executed": {"denied": False, "partial": True, "unknown": None}[failure]}

    async def callback(identity, name, arguments):
        calls.append(identity)
        return result

    async def image(identity):
        raise AssertionError("Failed actions cannot claim verified image delivery")

    register(ctx, callback)
    ctx.tool_image = image

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return wire([terminal([call(name="act", namespace="kira_computer", args={
                "observation_id": "fixture", "action": "click", "x": 10, "y": 20})])])
        assert json.loads(body["input"][-1]["output"]) == result
        return wire([terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert calls == ["call-1"] and len(bodies) == 2
    finally:
        await backend.close()


@pytest.mark.parametrize("failure", ["missing_callback", "missing_identity", "foreign_image", "invalid_pixels"])
async def test_computer_image_delivery_failure_stops_before_another_action(tmp_path, failure):
    ctx, _, _ = context(tmp_path)
    requests, callbacks = [], []

    async def callback(identity, name, arguments):
        callbacks.append(identity)
        return {"ok": True, **({"browser_image_id": "fixture-image"} if failure != "missing_identity" else {})}

    async def image(identity):
        if failure == "foreign_image":
            raise KiraError("INVALID_ATTACHMENT", "Synthetic ownership rejection")
        return {"type": "input_image", "image_url": "file:///unread/provider/path.png"}

    register(ctx, callback)
    ctx.tool_image = image if failure != "missing_callback" else None

    def handler(request):
        requests.append(request)
        return wire([terminal([{**call(name="observe", namespace="kira_computer"), "arguments": "{}"}])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] in {"failed", "interrupted"}
        assert len(requests) == 1 and callbacks == ["call-1"]
    finally:
        await backend.close()


def image_history():
    operations = [("computer", "observe"), ("browser", "screenshot"),
                  ("kira_computer", "act"), ("kira_browser", "screenshot")]
    return [item for number, (namespace, name) in enumerate(operations) for item in (
        call(name=name, namespace=namespace, call_id=f"old-{number}"),
        {"type": "function_call_output", "call_id": f"old-{number}", "output": [
            {"type": "input_text", "text": json.dumps({"ok": True,
                "browser_image_id": f"image-{number}", "observation_id": f"view-{number}"})},
            copy.deepcopy(PIXEL)]})]


def image_calls(items):
    return [item["call_id"] for item in items if item.get("type") == "function_call_output"
            and isinstance(item.get("output"), list)
            and any(part.get("type") == "input_image" for part in item["output"])]


def test_shared_image_budget_uses_only_typed_browser_and_computer_calls():
    items = image_history()
    original = copy.deepcopy(items)
    unrelated = [
        {"role": "user", "content": [copy.deepcopy(PIXEL)]},
        call(name="act", namespace="files", call_id="unrelated"),
        {"type": "function_call_output", "call_id": "unrelated", "output": [
            {"type": "input_text", "text": '{"browser_image_id":"fake"}'}, copy.deepcopy(PIXEL)]},
        call(name="snapshot", namespace="computer", call_id="unsupported"),
        {"type": "function_call_output", "call_id": "unsupported", "output": [copy.deepcopy(PIXEL)]}]
    items.extend(copy.deepcopy(unrelated))
    trim_browser_screenshots(items)
    assert items[-len(unrelated):] == unrelated
    assert image_calls(items) == ["old-2", "old-3", "unrelated", "unsupported"]
    for index in range(4):
        assert items[index * 2] == original[index * 2]
        assert items[index * 2 + 1]["output"][0] == original[index * 2 + 1]["output"][0]
        if index < 2:
            assert items[index * 2 + 1]["output"][1] == {"type": "input_text", "text": SCREENSHOT_OMISSION}
    once = copy.deepcopy(items)
    trim_browser_screenshots(items)
    assert items == once


async def test_mixed_image_budget_applies_before_requests_and_checkpoints(tmp_path):
    saved = {"schema": 1, "account_id": "fixture-account", "model": "fixture-model",
        "provider_generation": 0, "items": image_history(), "cursor": None,
        "memory": [], "compactions": 0, "source_ids": [], "capabilities": {}}
    ctx, _, saves, _, _ = durable_context(tmp_path, saved=saved)
    ctx.context_catalog = {"context_window": 100_000}
    requests = []

    async def callback(identity, name, arguments):
        return {"ok": True, "browser_image_id": identity + "-image", "observation_id": identity + "-view"}

    async def image(identity):
        return copy.deepcopy(PIXEL)

    register(ctx, callback)
    ctx.client_tools += browser_tool_specs()
    ctx.tool_image = image

    def handler(request):
        body = json.loads(request.content)
        assert len(image_calls(body["input"])) <= 2
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        requests.append(body)
        operations = [("kira_computer", "observe"), ("kira_browser", "screenshot"), ("kira_computer", "act")]
        if len(requests) <= len(operations):
            namespace, name = operations[len(requests) - 1]
            return wire([terminal([call(name=name, namespace=namespace, call_id=f"new-{len(requests)}")])])
        return wire([terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert len(requests) == 4
        assert all(len(image_calls(state["items"])) <= 2 for state in saves)
        assert image_calls(saves[-1]["items"]) == ["new-2", "new-3"]
        assert len(image_calls(saved["items"])) == 4
    finally:
        await backend.close()


async def test_four_namespaces_and_computer_bridge_retry_are_bounded(tmp_path):
    ctx, _, _ = context(tmp_path)
    effects = []

    async def callback(identity, name, arguments):
        effects.append((identity, name))
        return {"ok": True, "action_executed": True}

    register(ctx, callback)
    history = {"type": "namespace", "name": "kira_history", "description": "Synthetic chat history",
        "tools": [{"type": "function", "name": item["name"], "description": item["description"],
                   "inputSchema": item["parameters"]} for item in history_tool_specs()]}
    ctx.client_tools += assistant_tool_specs() + [history] + browser_tool_specs()
    names = validate_tools(ctx)
    assert COMPUTER_TOOL_NAMES <= names and len(ctx.client_tools) == 4
    bridge = ClientToolBridge(ctx, names, ctx.run_id, ctx.run_id)
    params = {"threadId": ctx.run_id, "turnId": ctx.run_id, "callId": "one-action",
              "namespace": "computer", "tool": "act", "arguments": {
                  "observation_id": "view", "action": "click", "x": 1, "y": 2}}
    first, second = await asyncio.gather(bridge.call(params), bridge.call(params))
    assert first == second and effects == [("one-action", "computer.act")]
    with pytest.raises(ClientToolError, match="different input"):
        await bridge.call({**params, "arguments": {**params["arguments"], "x": 9}})
    assert effects == [("one-action", "computer.act")]


async def test_computer_manual_or_approval_wait_is_cancelable_without_short_tool_timeout(tmp_path, monkeypatch):
    ctx, _, _ = context(tmp_path)
    monkeypatch.setattr(client_tools, "CLIENT_TOOL_TIMEOUT", 0.001)
    entered, released = asyncio.Event(), asyncio.Event()

    async def callback(identity, name, arguments):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    register(ctx, callback)
    bridge = ClientToolBridge(ctx, validate_tools(ctx), ctx.run_id, ctx.run_id)
    pending = asyncio.create_task(bridge.call({"threadId": ctx.run_id, "turnId": ctx.run_id,
        "callId": "waiting", "namespace": "computer", "tool": "observe", "arguments": {}}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.015)
        assert not pending.done()
        ctx.cancel.set()
        response = await asyncio.wait_for(pending, 1)
        assert response["success"] is False
        assert json.loads(response["contentItems"][0]["text"])["error"]["code"] == "CANCELLED"
        assert released.is_set()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
