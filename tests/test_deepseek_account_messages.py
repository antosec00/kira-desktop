"""DeepSeek Account Messages wire format: images, tool calls and output caps; no network."""

import asyncio
import base64
import json

import httpx
import pytest

from kira.backends.deepseek_account import API, DeepSeekAccountBackend, _anthropic_messages
from kira.backends.deepseek_context import context_policy
from kira.backends.ollama import StreamError, _ollama_image
from kira.domain.contracts import RunContext

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"synthetic-pixels").decode("ascii")
PNG_URL = "data:image/png;base64," + PNG
MODEL = "deepseek-fixture"


class Account:
    token = "synthetic-deepseek-token"
    proxy_enabled = False
    proxy_api_key = ""
    proxy_url = "http://127.0.0.1:4000"

    async def expire(self, token):
        raise AssertionError("the synthetic token must not expire")


def sse(*events):
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def text_reply(text="Synthetic answer", stop_reason="end_turn"):
    return sse(("message_start", {"message": {"usage": {"input_tokens": 10}}}),
               ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
               ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": text}}),
               ("message_delta", {"delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": 3}}),
               ("message_stop", {}))


async def emit(_kind, _payload):
    return None


async def unused(*_args):
    raise AssertionError("not used by these wire tests")


def context(images=None):
    return RunContext(run_id="run-current", project={"id": "project", "root": "/nonexistent",
                      "permission": "read_only"},
                      session={"backend": "deepseek_account", "model": MODEL, "settings": {}},
                      prompt="Describe the attached picture", supplied_context="", emit=emit,
                      execute_tool=unused, approve=unused, cancel=asyncio.Event(),
                      image_inputs={"run-current": images or []})


def backend(responses, requests):
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=responses.pop(0))

    instance = DeepSeekAccountBackend(Account())
    instance.client = httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler))
    instance._details[MODEL] = {"name": MODEL, "capabilities": ["tools", "vision"], "model_info": {}}
    return instance


def tool_turn(images=None):
    tool = {"role": "tool", "tool_name": "browser__screenshot", "tool_call_id": "call-1",
            "content": json.dumps({"ok": True, "title": "Synthetic page"})}
    if images is not None:
        tool["images"] = images
    return [{"role": "system", "content": "Kira rules"},
            {"role": "user", "content": "Describe the attached picture"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function",
             "function": {"name": "browser__screenshot", "arguments": {}}}]},
            tool]


def image_blocks(blocks):
    return [block for block in blocks if isinstance(block, dict) and block.get("type") == "image"]


@pytest.mark.parametrize("image", [
    _ollama_image({"type": "input_image", "image_url": PNG_URL, "detail": "auto"}),
    PNG_URL,
])
def test_screenshot_tool_result_reaches_the_model_as_an_image(image):
    messages, system = _anthropic_messages(tool_turn([image]), {})

    assert system == "Kira rules"
    result = messages[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "call-1"
    assert result["content"][0] == {"type": "text", "text": json.dumps({"ok": True, "title": "Synthetic page"})}
    assert image_blocks(result["content"]) == [{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": PNG}}]


def test_every_tool_image_is_kept_and_invalid_pixels_are_rejected():
    messages, _ = _anthropic_messages(tool_turn([PNG, PNG_URL]), {})
    assert len(image_blocks(messages[-1]["content"][0]["content"])) == 2
    with pytest.raises(StreamError, match="invalid Kira tool image"):
        _anthropic_messages(tool_turn(["not base64!"]), {})


async def test_attached_image_stays_on_the_request_in_later_tool_rounds():
    requests = []
    instance = backend([text_reply()], requests)
    attachment = {"type": "input_image", "image_url": PNG_URL, "detail": "auto"}
    try:
        await instance._stream({"model": MODEL, "messages": tool_turn()}, context([attachment]), "item")
    finally:
        await instance.close()

    messages = requests[0]["messages"]
    request = next(message for message in messages if message["role"] == "user" and any(
        block.get("text") == "Describe the attached picture" for block in message["content"]))
    assert image_blocks(request["content"]) == [{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": PNG}}]
    tool_results = messages[-1]["content"]
    assert tool_results[0]["type"] == "tool_result" and not image_blocks(tool_results)


async def test_tool_call_cut_at_the_output_cap_is_discarded_not_run_with_empty_input():
    truncated = sse(
        ("message_start", {"message": {"usage": {"input_tokens": 10}}}),
        ("content_block_start", {"index": 0, "content_block": {
            "type": "tool_use", "id": "call-cut", "name": "write_file", "input": {}}}),
        ("message_delta", {"delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 64}}),
        ("message_stop", {}))
    instance = backend([truncated], [])
    try:
        message, terminal = await instance._stream({"model": MODEL, "messages": [
            {"role": "user", "content": "Write the file"}]}, context(), "item")
    finally:
        await instance.close()

    assert "tool_calls" not in message
    assert terminal["tool_input_incomplete"] is True


async def test_complete_tool_call_without_input_deltas_still_runs():
    complete = sse(
        ("message_start", {"message": {"usage": {"input_tokens": 10}}}),
        ("content_block_start", {"index": 0, "content_block": {
            "type": "tool_use", "id": "call-env", "name": "kira__environment", "input": {}}}),
        ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 8}}),
        ("message_stop", {}))
    instance = backend([complete], [])
    try:
        message, terminal = await instance._stream({"model": MODEL, "messages": [
            {"role": "user", "content": "Check the environment"}]}, context(), "item")
    finally:
        await instance.close()

    assert message["tool_calls"] == [{"id": "call-env", "type": "function", "function": {
        "name": "kira__environment", "arguments": {}}}]
    assert terminal["tool_input_incomplete"] is False


@pytest.mark.parametrize(("window", "maximum", "expected"), [
    (65_536, None, 32_768),
    (131_072, None, 65_536),
    (1_000_000, 256_000, 65_536),
    (None, None, 65_536),
])
async def test_requested_output_fits_the_window_beside_admitted_input(window, maximum, expected):
    requests = []
    instance = backend([text_reply()], requests)
    instance._details[MODEL].update(context_window=window, max_output_tokens=maximum)
    try:
        _, terminal = await instance._stream({"model": MODEL, "messages": [
            {"role": "user", "content": "Hello"}]}, context(), "item")
    finally:
        await instance.close()

    assert requests[0]["max_tokens"] == terminal["max_output_tokens"] == expected
    if window:
        policy = context_policy({"context_window": window, "max_output_tokens": maximum})
        assert policy["compact_threshold"] + expected <= window


async def test_requests_without_a_run_context_are_capped_by_the_window_too():
    requests = []
    instance = backend([text_reply(), text_reply()], requests)
    instance._details[MODEL].update(context_window=65_536, max_output_tokens=None)
    try:
        await instance._stream({"model": MODEL, "messages": [{"role": "user", "content": "Probe"}]})
        await instance._stream({"model": MODEL, "max_tokens": 8192, "messages": [
            {"role": "user", "content": "Summarize"}]})
    finally:
        await instance.close()

    assert [request["max_tokens"] for request in requests] == [32_768, 8192]
