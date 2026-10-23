"""Synthetic regression for the provider-reserved browser namespace."""

import copy
import json

import httpx
import pytest

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.backends.openai_web import browser_wire_request
from kira.domain.web import browser_tool_specs
from test_backends_openai_chatgpt import FakeAuth, call, context, message, terminal, wire


def historical_items():
    literal = 'browser.navigate {"namespace":"browser"}'
    return [
        {"role": "user", "content": literal},
        {"type": "reasoning", "encrypted_content": literal},
        call(name="navigate", namespace="browser", args={"url": "https://example.com"}),
        {"type": "function_call_output", "call_id": "call-1", "namespace": "browser",
         "output": literal},
        call(name="snapshot", namespace="kira_browser", call_id="call-2"),
        {"type": "function_call_output", "call_id": "call-2", "output": literal},
        message(literal),
    ]


def assert_native_alias(items):
    assert all(item.get("namespace") != "browser" for item in items
               if item.get("type") in {"function_call", "function_call_output"})
    assert next(item for item in items if item.get("call_id") == "call-1")["namespace"] \
        == "kira_browser"


def test_wire_alias_changes_only_owned_definitions_and_native_identity_fields():
    body = {"model": "fixture-model", "instructions": "Literal browser.navigate guidance",
            "tools": [*browser_tool_specs(), {"type": "web_search", "external_web_access": True}],
            "input": historical_items()}
    original = copy.deepcopy(body)
    converted = browser_wire_request(body)
    assert body == original
    assert converted["tools"][0]["name"] == "kira_browser"
    assert "kira_browser.tabs" in next(tool["description"] for tool in converted["tools"][0]["tools"]
                                        if tool["name"] == "select_tab")
    assert converted["tools"][1] == body["tools"][1]
    assert converted["instructions"].startswith(body["instructions"] + "\n")
    assert_native_alias(converted["input"])
    for before, after in zip(original["input"], converted["input"], strict=True):
        expected = {**before, "namespace": "kira_browser"} if before.get("type") in {
            "function_call", "function_call_output"} and before.get("namespace") == "browser" else before
        assert after == expected
    assert browser_wire_request(converted) == converted


async def test_registered_browser_alias_dispatches_logically_and_retains_native_output(tmp_path):
    ctx, _, executed = context(tmp_path)
    callbacks, bodies = [], []
    ctx.client_tools = browser_tool_specs()
    original_tools = copy.deepcopy(ctx.client_tools)

    async def callback(identity, name, arguments):
        callbacks.append((identity, name, arguments))
        return {"ok": True, "text": "Literal browser.navigate page text"}

    ctx.client_tool_call = callback

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert all(tool.get("name") != "browser" for tool in body["tools"])
        browser = next(tool for tool in body["tools"] if tool.get("name") == "kira_browser")
        assert len(browser["tools"]) == 15
        assert any(tool["type"] == "web_search" for tool in body["tools"])
        if len(bodies) == 1:
            return wire([terminal([call(name="navigate", namespace="kira_browser",
                                       args={"url": "https://example.com"})])])
        assert_native_alias(body["input"])
        assert json.loads(body["input"][-1]["output"])["text"] == "Literal browser.navigate page text"
        return wire([terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert callbacks == [("call-1", "browser.navigate", {"url": "https://example.com"})]
        assert ctx.client_tools == original_tools
        assert len(bodies) == 2 and not executed
    finally:
        await backend.close()


@pytest.mark.parametrize("operation", ["count", "compact", "fold", "summary"])
async def test_auxiliary_requests_alias_old_calls_without_changing_history(tmp_path, operation):
    ctx, _, _ = context(tmp_path)
    items = historical_items()
    original = copy.deepcopy(items)
    requests = []
    compacted = {"type": "compaction", "encrypted_content": "unchanged-browser.navigate"}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert_native_alias(body["input"])
        assert body["input"][0] == original[0]
        assert body["input"][1] == original[1]
        if operation == "count":
            assert request.url.path == "/v1/responses/input_tokens"
            assert body["tools"][0]["name"] == "kira_browser"
            return httpx.Response(200, json={"input_tokens": 128})
        if operation == "compact":
            assert request.url.path == "/v1/responses/compact"
            return httpx.Response(200, json={"output": [compacted]})
        if operation == "fold":
            return wire([terminal([compacted])])
        return wire([terminal([message('{"summary":"literal browser.navigate","memory":[]}')])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    body = {"model": ctx.model, "input": items, "instructions": "Fixture instructions",
            "tools": browser_tool_specs()}
    original_body = copy.deepcopy(body)
    try:
        if operation == "count":
            assert await backend.context_count(body, "account-a") == 128
        elif operation == "compact":
            assert await backend._context_request("/responses/compact", body, "account-a") == {
                "output": [compacted]}
        elif operation == "fold":
            assert (await backend.context_fold(items, ctx, "account-a", "Fixture instructions"))["items"] \
                == [compacted]
        else:
            assert await backend.context_summary(items, [], [], ctx, "account-a") == {
                "summary": "literal browser.navigate", "memory": []}
        assert len(requests) == 1
        assert items == original and body == original_body
    finally:
        await backend.close()


async def test_generic_tool_rejection_does_not_disable_tools_or_retry(tmp_path):
    ctx, _, executed = context(tmp_path)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"type": "invalid_request_error",
            "param": "tools", "code": None, "message": "Provider-reflected private fixture"}})

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed"
        assert result["outcome"]["param"] == "tools"
        assert len(requests) == 1 and not executed
        assert "Provider-reflected" not in json.dumps(result)
    finally:
        await backend.close()
