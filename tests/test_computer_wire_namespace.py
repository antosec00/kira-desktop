"""Synthetic custom-computer routing; no provider-native computer entitlement."""

import copy
import json

import httpx
import pytest

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.backends.openai_web import browser_wire_request
from kira.domain.computer import computer_tool_specs
from kira.domain.web import browser_tool_specs
from test_backends_openai_chatgpt import FakeAuth, call, context, message, terminal, wire


def historical_items():
    literal = 'computer.observe browser.navigate {"namespace":"computer"}'
    return [
        {"role": "user", "content": literal},
        {"type": "reasoning", "encrypted_content": literal},
        call(name="observe", namespace="computer", call_id="old-observation"),
        {"type": "function_call_output", "call_id": "old-observation", "namespace": "computer",
         "output": literal},
        call(name="act", namespace="kira_computer", call_id="old-action"),
        {"type": "function_call_output", "call_id": "old-action", "output": literal},
        call(name="navigate", namespace="browser", call_id="old-navigation"),
        message(literal),
    ]


def assert_native_alias(items):
    assert all(item.get("namespace") not in {"computer", "browser"} for item in items
               if item.get("type") in {"function_call", "function_call_output"})
    assert next(item for item in items if item.get("call_id") == "old-observation")["namespace"] \
        == "kira_computer"


def test_wire_translation_preserves_literal_content_opaque_state_and_original_history():
    body = {"model": "fixture-model", "instructions": "Literal computer.observe browser.navigate guidance",
            "tools": [*browser_tool_specs(), *computer_tool_specs(), {"type": "web_search"}],
            "input": historical_items()}
    original = copy.deepcopy(body)
    converted = browser_wire_request(body)
    assert body == original
    assert [tool.get("name") for tool in converted["tools"]] == ["kira_browser", "kira_computer", None]
    assert [tool["type"] for tool in converted["tools"]] == ["namespace", "namespace", "web_search"]
    assert converted["instructions"].startswith(body["instructions"] + "\n")
    assert_native_alias(converted["input"])
    aliases = {"computer": "kira_computer", "browser": "kira_browser"}
    for before, after in zip(original["input"], converted["input"], strict=True):
        expected = {**before, "namespace": aliases[before["namespace"]]} if before.get("type") in {
            "function_call", "function_call_output"} and before.get("namespace") in aliases else before
        assert after == expected
    assert browser_wire_request(converted) == converted


@pytest.mark.parametrize("operation", ["count", "compact", "fold", "summary"])
async def test_auxiliary_requests_translate_computer_history_without_mutation(tmp_path, operation):
    ctx, _, _ = context(tmp_path)
    items = historical_items()
    original = copy.deepcopy(items)
    requests = []
    compacted = {"type": "compaction", "encrypted_content": "unchanged-computer.observe"}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert_native_alias(body["input"])
        assert body["input"][:2] == original[:2]
        if operation == "count":
            assert request.url.path == "/v1/responses/input_tokens"
            assert body["tools"][0]["name"] == "kira_computer"
            return httpx.Response(200, json={"input_tokens": 128})
        if operation == "compact":
            assert request.url.path == "/v1/responses/compact"
            return httpx.Response(200, json={"output": [compacted]})
        if operation == "fold":
            return wire([terminal([compacted])])
        return wire([terminal([message('{"summary":"literal computer.observe","memory":[]}')])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    body = {"model": ctx.model, "input": items, "instructions": "Fixture instructions",
            "tools": computer_tool_specs()}
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
                "summary": "literal computer.observe", "memory": []}
        assert len(requests) == 1
        assert items == original and body == original_body
    finally:
        await backend.close()
