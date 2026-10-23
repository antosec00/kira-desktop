"""Synthetic hosted-web wire checks; no real web search or plan inference."""

import base64
import copy
import json

import httpx
import pytest
from markdown_it import MarkdownIt

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.backends.openai_responses import ResponsesError
from kira.backends.openai_web import (
    MAX_LINKS,
    MAX_SCREENSHOT_IMAGE_BYTES,
    MAX_SUMMARY_BYTES,
    SCREENSHOT_OMISSION,
    action_summary,
    safe_url,
    trim_browser_screenshots,
    validate_browser_image,
)
from test_backends_openai_chatgpt import FakeAuth, call, context, message, terminal, wire
from test_direct_context_backend import context as durable_context
from test_direct_context_backend import unsupported_count


def search(action=None, *, identity="web-fixture", status="completed"):
    return {"type": "web_search_call", "id": identity, "status": status,
            "action": action or {"type": "search", "queries": ["Synthetic documentation"],
                "sources": [{"type": "url", "url": "https://example.com/docs", "title": "Docs"}]}}


def cited_message(text="Documented answer.", annotations=None):
    item = message(text)
    item["content"][0]["annotations"] = annotations if annotations is not None else [
        {"type": "url_citation", "url": "https://example.com/docs", "title": "Example docs",
         "start_index": 0, "end_index": len(text)}]
    return item


@pytest.mark.parametrize("settings,expected", [
    ({}, True), ({"web_search": "live"}, True), ({"web_search": "cached"}, False),
    ({"web_search": "disabled"}, None), ({"tools": False}, None),
    ({"tools": False, "web_search": "live"}, None)])
async def test_request_mode_preserves_exact_model_effort_and_route(tmp_path, settings, expected):
    ctx, _, executed = context(tmp_path)
    ctx.session["settings"].update(settings)
    bodies = []

    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        bodies.append(body)
        assert body["model"] == ctx.model and body["reasoning"] == {"effort": "xhigh", "summary": "auto"}
        native = [tool for tool in body.get("tools", []) if tool["type"] == "web_search"]
        assert native == ([] if expected is None else [
            {"type": "web_search", "external_web_access": expected}])
        assert ("web_search_call.action.sources" in body["include"]) is (expected is not None)
        return wire([terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert len(bodies) == 1 and not executed
    finally:
        await backend.close()


@pytest.mark.parametrize("mode", [True, None, "automatic", {"external_web_access": True}])
async def test_invalid_web_mode_fails_before_inference(tmp_path, mode):
    ctx, _, _ = context(tmp_path)
    ctx.session["settings"]["web_search"] = mode

    def handler(request):
        raise AssertionError("Invalid settings must not start inference")

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "Web search mode" in result["error"]
    finally:
        await backend.close()


@pytest.mark.parametrize("action", [
    {"type": "search", "query": "documentation", "queries": ["documentation"]},
    {"type": "open_page", "url": "https://example.com/docs"},
    {"type": "find_in_page", "url": "https://example.com/docs", "pattern": "API"}])
async def test_hosted_actions_are_observed_once_without_local_execution(tmp_path, action):
    ctx, events, executed = context(tmp_path)
    item = search(action)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([{"type": "response.output_item.added", "output_index": 0,
               "item": search(status="in_progress")},
              {"type": "response.web_search_call.searching", "output_index": 0,
               "item_id": item["id"]},
              {"type": "response.web_search_call.completed", "output_index": 0,
               "item_id": item["id"]},
              {"type": "response.output_item.done", "output_index": 0, "item": item},
              terminal([item, cited_message()])])))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        started = [payload for kind, payload in events if kind == "tool.started"]
        completed = [payload for kind, payload in events if kind == "tool.completed"]
        assert len(started) == len(completed) == 1 and not executed
        assert started[0]["tool_id"] == completed[0]["tool_id"]
        assert completed[0]["result"] == {"ok": True, "status": "completed",
            "provider": "openai_chatgpt", "execution": "provider_hosted",
            "evidence": "provider_stream", "mode": "live", **action_summary(item, complete=True)}
    finally:
        await backend.close()


async def test_empty_terminal_accumulates_native_items_citations_and_private_context(tmp_path):
    ctx, events, saves, executed, _ = durable_context(tmp_path)
    item = search()
    answer = cited_message()
    original = copy.deepcopy(answer)

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        return wire([{"type": "response.output_item.done", "output_index": 0, "item": item},
                     {"type": "response.output_text.delta", "output_index": 1,
                      "delta": "Documented answer."},
                     {"type": "response.output_item.done", "output_index": 1, "item": answer},
                     terminal([])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed" and not executed
        assert item in saves[-1]["items"] and original in saves[-1]["items"]
        delta = next(payload for kind, payload in events if kind == "message.delta")
        final = next(payload for kind, payload in events if kind == "message.completed")
        assert final["item_id"] == delta["item_id"]
        assert final["content"] == delta["content"] + "\n\n[Example docs](<https://example.com/docs>)"
        assert answer == original
    finally:
        await backend.close()


async def test_native_web_item_survives_followup_tool_round(tmp_path):
    ctx, events, executed = context(tmp_path)
    item = search()
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return wire([terminal([item, call()])])
        assert item in body["input"]
        assert body["input"][-1]["type"] == "function_call_output"
        return wire([terminal([cited_message()])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert len(executed) == 1 and len(bodies) == 2
        assert len([kind for kind, _ in events if kind == "tool.started"]) == 1
    finally:
        await backend.close()


async def test_provider_denial_has_no_automatic_fallback_or_retry(tmp_path):
    ctx, events, executed = context(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(403, json={"error": {"code": "unsupported_feature",
            "param": "tools", "message": "Provider-reflected private fixture"}})

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "unsupported_feature" in result["error"]
        assert result["outcome"]["http_status"] == 403
        assert len(requests) == 1 and not executed
        assert events == [
            ("reasoning.summary.status", {"provider": "openai_chatgpt", "source": "provider_summary",
                                           "status": "requested"}),
            ("reasoning.summary.status", {"provider": "openai_chatgpt", "source": "provider_summary",
                                           "status": "unavailable", "reason": "no_public_summary"}),
        ]
        assert "Provider-reflected" not in json.dumps(result)
    finally:
        await backend.close()


@pytest.mark.parametrize("item", [
    search(status="in_progress"), search(status="failed"), search(identity=""),
    search({"type": "search", "query": 42}), search({"type": "shell", "query": "fixture"}),
    search({"type": []}), {**search(), "action": []},
    search({"type": "open_page", "url": "file:///etc/passwd"}),
    search({"type": "open_page", "url": 42}),
    search({"type": "find_in_page", "pattern": "fixture"}),
    search({"type": "find_in_page", "url": None, "pattern": "fixture"}),
    search({"type": "find_in_page", "url": "https://example.com"})])
async def test_malformed_or_unfinished_hosted_items_never_complete_run(tmp_path, item):
    ctx, events, executed = context(tmp_path)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([terminal([item, message()])])))
    try:
        assert (await backend.run(ctx))["state"] != "completed" and not executed
        assert not any(kind == "message.completed" for kind, _ in events)
        assert not any(kind == "tool.completed" and payload["result"]["ok"] for kind, payload in events)
    finally:
        await backend.close()


@pytest.mark.parametrize("metadata,expected", [
    ({}, {"action_details_available": False}),
    ({"action": None}, {"action_details_available": False}),
    ({"action": {"type": "open_page"}}, {"action": "open_page", "destination_available": False}),
    ({"action": {"type": "open_page", "url": None}}, {"action": "open_page", "destination_available": False}),
])
@pytest.mark.parametrize("empty_terminal", [False, True])
async def test_confirmed_web_actions_accept_optional_metadata_without_inventing_urls(tmp_path, metadata, expected, empty_terminal):
    ctx, events, executed = context(tmp_path)
    item = {"type": "web_search_call", "id": "synthetic-optional-web", "status": "completed", **metadata}
    original = copy.deepcopy(item)
    answer = cited_message()
    records = [{"type": "response.output_item.added", "output_index": 0,
                "item": {"type": "web_search_call", "id": item["id"], "status": "in_progress"}},
               {"type": "response.output_text.delta", "output_index": 1, "delta": "Documented answer."},
               {"type": "response.output_item.done", "output_index": 0, "item": item},
               {"type": "response.output_item.done", "output_index": 1, "item": answer},
               terminal([] if empty_terminal else [item, answer])]
    requests = []
    def handler(request):
        requests.append(request)
        return wire(records)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert not executed and len(requests) == 1 and item == original
        completed = [payload for kind, payload in events if kind == "tool.completed"]
        assert len(completed) == 1
        result = completed[0]["result"]
        assert result == {"ok": True, "status": "completed", "provider": "openai_chatgpt",
                          "execution": "provider_hosted", "evidence": "provider_stream", "mode": "live", **expected}
        assert "url" not in result
        message_result = next(payload for kind, payload in events if kind == "message.completed")
        assert "Documented answer." in message_result["content"]
        assert "https://example.com/docs" in message_result["content"]
    finally:
        await backend.close()


@pytest.mark.parametrize("metadata", [{}, {"action": None}, {"action": {"type": "open_page", "url": None}}])
@pytest.mark.parametrize("termination", ["failed", "incomplete", "eof"])
async def test_optional_web_metadata_never_substitutes_for_response_completion(tmp_path, metadata, termination):
    ctx, events, executed = context(tmp_path)
    item = {"type": "web_search_call", "id": "synthetic-unconfirmed-web", "status": "completed", **metadata}
    records = [{"type": "response.output_item.done", "output_index": 0, "item": item}]
    if termination == "failed":
        records.append({"type": "response.failed", "response": {"error": {"code": "synthetic_denied"}}})
    elif termination == "incomplete":
        records.append({"type": "response.incomplete", "response": {"status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"}}})
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request: wire(records)))
    try:
        result = await backend.run(ctx)
        assert result["state"] == ("failed" if termination == "failed" else "interrupted")
        assert not executed
        completed = [payload for kind, payload in events if kind == "tool.completed"]
        assert len(completed) == 1 and completed[0]["result"]["status"] == "unconfirmed"
        assert completed[0]["result"]["ok"] is False
        assert not any(kind == "message.completed" for kind, _ in events)
    finally:
        await backend.close()


async def test_interrupted_provider_web_is_visible_as_unconfirmed(tmp_path):
    ctx, events, executed = context(tmp_path)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([{"type": "response.web_search_call.in_progress", "output_index": 0,
               "item_id": "web-fixture"},
              {"type": "response.failed", "response": {"error": {"code": "server_error"}}}])))
    try:
        assert (await backend.run(ctx))["state"] == "failed" and not executed
        result = next(payload["result"] for kind, payload in events if kind == "tool.completed")
        assert result["ok"] is False and result["status"] == "unconfirmed"
    finally:
        await backend.close()


@pytest.mark.parametrize("failure", [True, False])
async def test_finalized_web_item_without_successful_response_is_unconfirmed(tmp_path, failure):
    ctx, events, executed = context(tmp_path)
    records = [{"type": "response.output_item.done", "output_index": 0, "item": search()}]
    if failure:
        records.append({"type": "response.failed", "response": {"error": {"code": "server_error"}}})
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(
        lambda request: wire(records)))
    try:
        result = await backend.run(ctx)
        assert result["state"] == ("failed" if failure else "interrupted") and not executed
        assert len([payload for kind, payload in events if kind == "tool.started"]) == 1
        completed = [payload for kind, payload in events if kind == "tool.completed"]
        assert len(completed) == 1
        assert completed[0]["result"]["ok"] is False
        assert completed[0]["result"]["status"] == "unconfirmed"
        assert not any(kind == "message.completed" for kind, _ in events)
    finally:
        await backend.close()


async def test_unregistered_or_unreconciled_hosted_web_fails(tmp_path):
    for disabled in (True, False):
        ctx, events, executed = context(tmp_path)
        ctx.session["settings"]["web_search"] = "disabled" if disabled else "live"
        backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
            wire([{"type": "response.web_search_call.in_progress", "output_index": 0,
                   "item_id": "web-fixture"}, terminal([message()])])))
        try:
            assert (await backend.run(ctx))["state"] != "completed" and not executed
            assert not any(kind == "message.completed" for kind, _ in events)
        finally:
            await backend.close()


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///tmp/file", "data:text/html,hi",
    "https://user:password@example.com", "https://example.com\n/x", "https://example.com:wrong",
    "https://example.com/\\other", "https:///missing-host", "//example.com", "https://example.com:0"])
def test_citation_destinations_do_not_allow_active_schemes_or_credentials(url):
    assert safe_url(url) is None


async def test_citations_escape_titles_deduplicate_and_keep_structured_output_valid(tmp_path):
    annotations = [
        {"type": "url_citation", "url": "javascript:alert(1)", "title": "Unsafe"},
        {"type": "url_citation", "url": "https://example.com/a(b)",
         "title": "[Injected](javascript:alert(1))\n**Title**"},
        {"type": "url_citation", "url": "https://example.com/a(b)", "title": "Duplicate"}]
    item = cited_message('{"answer":"value"}', annotations)
    for structured in (False, True):
        ctx, events, _ = context(tmp_path)
        if structured:
            ctx.output_schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
            wire([terminal([item])])))
        try:
            result = await backend.run(ctx)
            assert result["state"] == "completed"
            content = next(payload["content"] for kind, payload in events if kind == "message.completed")
            if structured:
                assert json.loads(content) == {"answer": "value"}
                assert result["outcome"]["final_text"] == content
            else:
                html = MarkdownIt().render(content)
                assert html.count("<a ") == 1 and 'href="https://example.com/a(b)"' in html
                assert 'href="javascript:' not in html and "Duplicate" not in html
        finally:
            await backend.close()


def test_hosted_sources_queries_and_citations_have_explicit_bounds():
    sources = [{"url": "https://example.com/" + "𝕏" * 2000 + str(index), "title": "𝕏" * 256}
               for index in range(100)]
    summary = action_summary(search({"type": "search", "queries": ["𝕏" * 2000] * 100,
                                     "sources": sources}), complete=True)
    assert summary["truncated"] and len(summary["queries"]) == 8
    assert len(summary["sources"]) < MAX_LINKS
    assert len(json.dumps(summary, ensure_ascii=False).encode()) < MAX_SUMMARY_BYTES + 1024
    annotations = [{"type": "url_citation", "url": f"https://example.com/{index}", "title": "Docs"}
                   for index in range(100)]
    rendered = OpenAIChatGPTBackend._message(cited_message(annotations=annotations), citations=True)
    assert rendered.count("](<https://") == MAX_LINKS


async def test_citation_expansion_counts_against_output_budget(tmp_path):
    ctx, events, _ = context(tmp_path)
    ctx.session["settings"]["max_output_bytes"] = 30
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([{"type": "response.output_text.delta", "output_index": 0,
               "delta": "Documented answer."}, terminal([cited_message()])])))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted" and "byte limit" in result["error"]
        assert not any(kind == "message.completed" for kind, _ in events)
    finally:
        await backend.close()


def register_browser(ctx, callback):
    ctx.client_tools = [{"type": "namespace", "name": "browser", "description": "Browser tools",
        "tools": [{"type": "function", "name": name, "description": name,
            "inputSchema": {"type": "object", "properties": {}}}
            for name in ("screenshot", "snapshot")]}]
    ctx.client_tool_call = callback


@pytest.mark.parametrize("tool,ok", [("screenshot", True), ("screenshot", False), ("snapshot", True)])
async def test_only_successful_owned_browser_screenshot_supplies_model_pixels(tmp_path, tool, ok):
    ctx, events, _ = context(tmp_path)
    images, callbacks, bodies = [], [], []
    # Small synthetic PNG payload; actual decoding/ownership belongs to service.
    pixel = {"type": "input_image", "image_url": "data:image/png;base64,c3ludGhldGlj", "detail": "auto"}
    metadata = {"ok": ok, "browser_image_id": "browser-image-fixture",
                "path": "/unread/provider/path.png"}

    async def callback(identity, name, arguments):
        callbacks.append((identity, name))
        return metadata

    async def tool_image(identity):
        images.append(identity)
        return pixel

    register_browser(ctx, callback)
    ctx.tool_image = tool_image

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return wire([terminal([call(name=tool, namespace="kira_browser", args={"fixture": True})])])
        output = body["input"][-1]["output"]
        if tool == "screenshot" and ok:
            assert output == [{"type": "input_text", "text": json.dumps(metadata, ensure_ascii=False,
                separators=(",", ":"), sort_keys=True)}, pixel]
        else:
            assert isinstance(output, str) and json.loads(output) == metadata
        return wire([terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert len(callbacks) == 1 and len(bodies) == 2
        assert callbacks == [("call-1", f"browser.{tool}")]
        assert bodies[-1]["input"][-2]["namespace"] == "kira_browser"
        assert images == (["browser-image-fixture"] if tool == "screenshot" and ok else [])
        assert "data:image" not in json.dumps(events)
    finally:
        await backend.close()


@pytest.mark.parametrize("failure", ["missing_callback", "missing_identity", "invalid_pixels"])
async def test_browser_screenshot_unavailable_delivery_fails_visibly(tmp_path, failure):
    ctx, events, _ = context(tmp_path)
    requests = []

    async def callback(identity, name, arguments):
        return {"ok": True, **({"browser_image_id": "fixture-image"}
                               if failure != "missing_identity" else {})}

    async def tool_image(identity):
        return {"type": "input_image", "image_url": "file:///unread/provider/path.png"}

    register_browser(ctx, callback)
    ctx.tool_image = tool_image if failure != "missing_callback" else None

    def handler(request):
        requests.append(request)
        return wire([terminal([call(name="screenshot", namespace="kira_browser", args={"fixture": True})])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "Browser screenshot" in result["error"]
        assert len(requests) == 1 and not any(kind == "message.completed" for kind, _ in events)
    finally:
        await backend.close()


def screenshot_context(count=4, namespace="browser"):
    pixel = {"type": "input_image", "image_url": "data:image/png;base64,c3ludGhldGlj", "detail": "auto"}
    return [item for number in range(count) for item in (
        call(name="screenshot", namespace=namespace, call_id=f"old-call-{number}"),
        {"type": "function_call_output", "call_id": f"old-call-{number}", "output": [
            {"type": "input_text", "text": json.dumps({"ok": True,
                "browser_image_id": f"old-image-{number}", "path": "/never/read/from/metadata.png"})},
            copy.deepcopy(pixel)]})]


def screenshot_image_count(items):
    return sum(part.get("type") == "input_image" for item in items
               if item.get("type") == "function_call_output" and isinstance(item.get("output"), list)
               for part in item["output"])


@pytest.mark.parametrize("namespace", ["browser", "kira_browser"])
def test_working_screenshots_keep_latest_two_and_preserve_metadata_and_other_images(namespace):
    items = screenshot_context(namespace=namespace)
    original = copy.deepcopy(items)
    pixel = copy.deepcopy(items[-1]["output"][-1])
    user = {"role": "user", "content": [{"type": "input_text", "text": "User image"}, pixel]}
    unrelated = [call(name="read_file", namespace="files", call_id="other-tool"),
        {"type": "function_call_output", "call_id": "other-tool", "output": [
            {"type": "input_text", "text": '{"browser_image_id":"does-not-establish-ownership"}'},
            copy.deepcopy(pixel)]}]
    orphan = {"type": "function_call_output", "call_id": "not-a-browser-call", "output": [
        copy.deepcopy(pixel)]}
    items = [user, *items, *unrelated, orphan]
    trim_browser_screenshots(items)
    assert items[0] == user and items[-3:] == [*unrelated, orphan]
    for number in range(4):
        output = items[2 + number * 2]["output"]
        assert output[0] == original[1 + number * 2]["output"][0]
        assert output[1] == ({"type": "input_text", "text": SCREENSHOT_OMISSION}
                             if number < 2 else pixel)
    once = copy.deepcopy(items)
    trim_browser_screenshots(items)
    assert items == once


@pytest.mark.parametrize("oversize", [False, True])
def test_browser_image_byte_limit_counts_decoded_data_and_rejects_invalid_base64(oversize):
    data = b"x" * (MAX_SCREENSHOT_IMAGE_BYTES + int(oversize))
    image = {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(data).decode()}
    if oversize:
        with pytest.raises(ResponsesError, match="2 MiB"):
            validate_browser_image(image)
    else:
        validate_browser_image(image)
    image["image_url"] = "data:image/png;base64,not!base64"
    with pytest.raises(ResponsesError, match="invalid pixels"):
        validate_browser_image(image)


async def test_screenshot_budget_applies_to_historical_context_each_followup_and_checkpoint(tmp_path):
    saved = {"schema": 1, "account_id": "fixture-account", "model": "fixture-model",
        "provider_generation": 0, "items": screenshot_context(), "cursor": None,
        "memory": [], "compactions": 0, "source_ids": [], "capabilities": {}}
    ctx, events, saves, _, _ = durable_context(tmp_path, saved=saved)
    ctx.context_catalog = {"context_window": 100_000}
    requests, pixel_ids = [], []
    pixels = {"type": "input_image", "image_url": "data:image/png;base64,c3ludGhldGlj", "detail": "auto"}
    ctx.image_inputs = {ctx.run_id: [copy.deepcopy(pixels)]}

    async def callback(identity, name, arguments):
        return {"ok": True, "browser_image_id": identity.replace("call", "image")}

    async def image(identity):
        pixel_ids.append(identity)
        return copy.deepcopy(pixels)

    register_browser(ctx, callback)
    ctx.tool_image = image

    def handler(request):
        body = json.loads(request.content)
        assert screenshot_image_count(body["input"]) <= 2
        assert all(item.get("namespace") != "browser" for item in body["input"]
                   if item.get("type") == "function_call")
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        requests.append(body)
        current = next(item for item in body["input"] if item.get("role") == "user"
                       and isinstance(item.get("content"), list))
        assert current["content"][-1] == pixels
        if len(requests) <= 4:
            return wire([terminal([call(name="screenshot", namespace="kira_browser",
                                       call_id=f"new-call-{len(requests)}")])])
        return wire([terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert len(requests) == 5 and len(pixel_ids) == 4
        assert all(screenshot_image_count(state["items"]) <= 2 for state in saves)
        assert screenshot_image_count(saves[-1]["items"]) == 2
        final = saves[-1]["items"]
        assert all(item["namespace"] == "kira_browser" for item in final
                   if item.get("type") == "function_call"
                   and item["call_id"].startswith("new-call-"))
        retained = [item["call_id"] for item in final if item.get("type") == "function_call_output"
                    and any(part.get("type") == "input_image" for part in item["output"])]
        assert retained == ["new-call-3", "new-call-4"]
        # The caller's saved checkpoint and artifacts are independent originals.
        assert screenshot_image_count(saved["items"]) == 4
        assert "data:image" not in json.dumps(events)
    finally:
        await backend.close()
