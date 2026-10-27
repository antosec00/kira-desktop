"""DeepSeek Web text and DSML tool-call envelope parsing."""

import asyncio
import base64
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from kira.backends.deepseek_web import DeepSeekWebBackend, DeepSeekWebError, DeepSeekWebStream
from kira.domain.web import browser_tool_specs


@pytest.fixture(autouse=True)
def _without_host_network_settings(monkeypatch):
    # The backend's own httpx client reads proxy and CA settings from the
    # environment; a host SOCKS proxy or CA path must not decide these tests.
    for name in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                 "http_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_deepseek_dsml_browser_navigate_maps_to_registered_kira_tool():
    response = (
        '<|DSML|calls><|DSML|invoke name="browser.navigate">'
        '<|DSML|parameter name="url" string="true">https://www.google.com/</|DSML|parameter>'
        '</|DSML|invoke></|DSML|calls>'
    )
    calls = DeepSeekWebBackend._parse_tool_calls(
        response, True, {"browser.navigate": "browser__navigate"})
    assert calls == [{"name": "browser__navigate",
                      "arguments": {"url": "https://www.google.com/"}}]


def test_deepseek_dsml_fullwidth_pipe_variant_is_normalized():
    response = (
        '<｜｜DSML｜｜ calls>\n'
        '<｜｜DSML｜｜ invoke name="browser__snapshot">\n\n'
        '</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'
    )
    calls = DeepSeekWebBackend._parse_tool_calls(response, True, {})
    assert calls == [{"name": "browser__snapshot", "arguments": {}}]


def test_deepseek_dsml_call_in_single_code_fence_is_parsed():
    response = (
        '```dsml\n<|DSML|calls><|DSML|invoke name="browser.snapshot">'
        '</|DSML|invoke></|DSML|calls>\n```'
    )

    calls = DeepSeekWebBackend._parse_tool_calls(response, True, {})

    assert calls == [{"name": "browser.snapshot", "arguments": {}}]


def test_deepseek_malformed_dsml_is_rejected_before_tool_dispatch():
    with pytest.raises(DeepSeekWebError, match="Malformed DeepSeek DSML"):
        DeepSeekWebBackend._parse_tool_calls(
            '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="browser__snapshot">',
            True, {})


def test_only_parse_errors_before_tool_execution_are_repairable():
    assert DeepSeekWebBackend._is_repairable_tool_format_error(
        DeepSeekWebError("Malformed DeepSeek DSML call envelope; no tool was executed"))
    assert not DeepSeekWebBackend._is_repairable_tool_format_error(
        DeepSeekWebError("Kira tools are disabled for this run"))


def test_tool_format_repair_is_bounded():
    assert DeepSeekWebBackend._should_retry_tool_format(0)
    assert DeepSeekWebBackend._should_retry_tool_format(1)
    assert not DeepSeekWebBackend._should_retry_tool_format(2)


def test_deepseek_json_tool_call_in_markdown_json_fence_is_parsed():
    response = '''```json
{"kira_tool_call":{"name":"list_files","arguments":{"path":"."}}}
```'''

    calls = DeepSeekWebBackend._parse_tool_calls(response, True, {})

    assert calls == [{"name": "list_files", "arguments": {"path": "."}}]


def test_deepseek_json_tool_call_with_surrounding_prose_is_still_rejected():
    response = (
        'Here is the tool call:\n```json\n'
        '{"kira_tool_call":{"name":"list_files","arguments":{"path":"."}}}\n```'
    )

    with pytest.raises(DeepSeekWebError, match="Malformed Kira JSON tool envelope"):
        DeepSeekWebBackend._parse_tool_calls(response, True, {})


def test_deepseek_system_prompt_requests_json_tool_envelopes_only():
    ctx = SimpleNamespace(assistant_instructions="Kira rules", supplied_context="Project context",
                          permission="read_only")
    schema = [{"type": "function", "function": {"name": "list_files", "parameters": {}}}]

    prompt = DeepSeekWebBackend._system_context(ctx, schema, vision=False)

    assert '"kira_tool_call"' in prompt
    assert "Do not use DSML" in prompt
    assert "<|DSML|" not in prompt


@pytest.mark.parametrize("event", [
    {"response_message_id": 123456789},
    {"p": "response/message_id", "o": "SET", "v": 123456789},
])
def test_deepseek_web_stream_keeps_numeric_response_message_id(event):
    stream = DeepSeekWebStream()
    stream.apply(event)

    assert stream.message_id == 123456789


def streamed(events):
    stream = DeepSeekWebStream()
    for event in events:
        stream.apply(event)
    return stream.texts()


FINISHED = {"p": "response/status", "o": "SET", "v": "FINISHED"}


def test_elided_path_and_operation_keep_reasoning_out_of_the_reply():
    # The site omits "p" and "o" that repeat the previous event's. Read naively,
    # this turn showed ", sono qui. Rispondo in italiano, breve.C! Sì, ci sono."
    events = [
        {"v": {"response": {"message_id": 2, "fragments": [{"id": 1, "type": "THINK", "content": ""}]}}},
        {"p": "response/fragments/-1/content", "o": "APPEND", "v": "Ciao"},
        {"v": ","}, {"v": " sono qui."}, {"v": " Rispondo in italiano, breve."},
        {"p": "response/fragments/-1/elapsed_secs", "o": "SET", "v": 0.9},
        {"p": "response/fragments", "o": "APPEND", "v": [{"id": 2, "type": "RESPONSE", "content": "C"}]},
        {"p": "response/fragments/-1/content", "v": "iao"},
        {"v": "!"}, {"v": " Sì, ci sono."}, {"v": " Come posso aiutarti?"},
        FINISHED,
    ]

    assert streamed(events) == ("Ciao! Sì, ci sono. Come posso aiutarti?",
                                "Ciao, sono qui. Rispondo in italiano, breve.")


def test_reasoning_streamed_right_after_the_snapshot_stays_reasoning():
    events = [
        {"v": {"response": {"fragments": [{"type": "THINK", "content": "The"}]}}},
        {"v": " user"}, {"v": " asks who I am."},
        {"p": "response/fragments/-1/content", "o": "APPEND", "v": " Answer in Italian."},
        {"p": "response/fragments", "v": [{"type": "RESPONSE", "content": "S"}]},
        {"p": "response/fragments/-1/content", "v": "ono"}, {"v": " Kira."},
        FINISHED,
    ]

    assert streamed(events) == ("Sono Kira.", "The user asks who I am. Answer in Italian.")


def test_batch_members_use_paths_relative_to_the_batch():
    stream = DeepSeekWebStream()
    stream.apply({"v": {"response": {"fragments": [{"type": "RESPONSE", "content": "Done"}]}}})
    stream.apply({"p": "response", "o": "BATCH", "v": [
        {"p": "fragments/-1/content", "o": "APPEND", "v": " here."},
        {"p": "accumulated_token_usage", "v": 12}, {"p": "status", "v": "FINISHED"}]})

    assert stream.texts() == ("Done here.", "") and stream.finished


def test_value_after_a_non_text_patch_extends_the_last_fragment():
    events = [
        {"v": {"response": {"fragments": [{"type": "RESPONSE", "content": "Hello"}]}}},
        {"p": "response/fragments/-1/elapsed_secs", "o": "SET", "v": 1.5},
        {"v": " world"},
    ]

    assert streamed(events) == ("Hello world", "")


SITE_NOTICE = {"id": 3, "type": "TIP", "style": "WARNING", "hide_on_wip": True,
               "content": "This response is AI-generated, for reference only."}


def test_site_notice_fragments_never_reach_the_reply():
    # The site appends this notice to some replies; read as reply text, it
    # broke valid tool calls into "malformed" envelopes.
    call = json.dumps({"kira_tool_call": {"name": "list_files", "arguments": {"path": "."}}})
    events = [
        {"v": {"response": {"fragments": [{"type": "THINK", "content": "Listing first."}]}}},
        {"p": "response/fragments", "o": "APPEND", "v": [{"type": "RESPONSE", "content": call}]},
        {"p": "response/fragments", "o": "APPEND", "v": [SITE_NOTICE]},
        FINISHED,
    ]

    visible, thinking = streamed(events)
    assert (visible, thinking) == (call, "Listing first.")
    assert DeepSeekWebBackend._parse_tool_calls(visible, True, {}) == [
        {"name": "list_files", "arguments": {"path": "."}}]


def test_tool_call_wrapped_in_tool_call_tags_is_accepted():
    text = '<tool_call>\n{"kira_tool_call": {"name": "list_files", "arguments": {"path": "."}}}\n</tool_call>'

    assert DeepSeekWebBackend._parse_tool_calls(text, True, {}) == [
        {"name": "list_files", "arguments": {"path": "."}}]


def test_literal_line_breaks_inside_a_tool_call_string_are_accepted():
    text = '{"kira_tool_call": {"name": "write_file", "arguments": {"path": "notes.md", "content": "# Title\n\n\tBody"}}}'

    calls = DeepSeekWebBackend._parse_tool_calls(text, True, {})
    assert calls[0]["arguments"]["content"] == "# Title\n\n\tBody"


def test_malformed_envelope_error_says_where_it_broke():
    with pytest.raises(DeepSeekWebError) as raised:
        DeepSeekWebBackend._parse_tool_calls('{"kira_tool_call": {"name": "list_files"} trailing', True, {})

    message = str(raised.value)
    assert "at character" in message and message.endswith("no tool was executed")


def test_legacy_reasoning_and_content_paths_continue_separately():
    events = [
        {"p": "response/thinking_content", "v": "Checking"}, {"v": " the request."},
        {"p": "response/content", "v": "Answer"}, {"v": " ready."},
    ]

    assert streamed(events) == ("Answer ready.", "Checking the request.")


def test_deepseek_remote_session_accepts_numeric_parent_message_id():
    token = "synthetic-token"
    ctx = SimpleNamespace(run_id="run-2", model="deepseek-reasoner")
    backend_ref = DeepSeekWebBackend._remote_session_ref(ctx, token, "remote-chat", 123456789)
    ctx.session = {"backend_ref": backend_ref}
    ctx.history = [{"role": "user", "run_id": "run-2", "content": "Follow up"}]

    state = DeepSeekWebBackend._remote_session(ctx, token)

    assert state is not None
    assert state["chat_session_id"] == "remote-chat"
    assert state["parent_message_id"] == 123456789


def test_deepseek_prompt_fingerprint_tracks_live_scope_and_tool_catalog():
    ctx = SimpleNamespace(
        project={"id": "project-1", "name": "test", "root": "/tmp/test",
                 "permission": "full_access", "instructions": "Keep changes scoped.",
                 "memory": {"decision": "preserve files"}},
        session={"soul_id": "soul-1", "soul_revision": 2},
        settings={"tools": True, "reasoning_effort": None, "handoff": ""},
        model="deepseek-reasoner", mode="execute", assistant_instructions="Kira rules",
    )
    schema = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]

    original = DeepSeekWebBackend._context_fingerprint(ctx, schema, vision=True)
    assert DeepSeekWebBackend._context_fingerprint(ctx, schema, vision=True) == original

    changed_project = SimpleNamespace(**vars(ctx))
    changed_project.project = {**ctx.project, "permission": "read_only"}
    changed_tool = [{"type": "function", "function": {"name": "list_files", "parameters": {}}}]
    assert DeepSeekWebBackend._context_fingerprint(changed_project, schema, vision=True) != original
    assert DeepSeekWebBackend._context_fingerprint(ctx, changed_tool, vision=True) != original


def test_deepseek_continuation_prompt_omits_bootstrap_context():
    ctx = SimpleNamespace(
        project={"id": "project-1", "name": "test", "permission": "read_only"},
        session={"model": "deepseek-reasoner"}, model="deepseek-reasoner",
        mode="plan", prompt="Summarize the previous answer.",
    )
    schemas = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]

    prompt = DeepSeekWebBackend._continuation_prompt(ctx, schemas, vision=True)

    assert "supplied earlier in this same chat" in prompt
    assert "permission read_only; mode plan" in prompt
    assert "read_file" in prompt
    assert '"kira_tool_call"' in prompt
    assert "Do not use DSML" in prompt
    assert ctx.prompt in prompt
    assert len(prompt) < 1000


def test_deepseek_sends_full_context_only_for_new_or_changed_bootstrap():
    ctx = SimpleNamespace(
        project={"id": "project-1", "name": "test", "permission": "read_only"},
        session={"model": "deepseek-reasoner"}, model="deepseek-reasoner",
        mode="plan", prompt="Follow up.",
    )
    schemas = []
    fingerprint = "context-fingerprint"
    full_context = "FULL KIRA BOOTSTRAP"
    remote = {"parent_message_id": 123, "context_fingerprint": fingerprint}

    compact = DeepSeekWebBackend._turn_prompt(
        ctx, schemas, full_context, fingerprint, remote, vision=False)
    changed = DeepSeekWebBackend._turn_prompt(
        ctx, schemas, full_context, fingerprint, {**remote, "context_fingerprint": "old"}, vision=False)
    missing = DeepSeekWebBackend._turn_prompt(
        ctx, schemas, full_context, fingerprint, {"parent_message_id": None}, vision=False)

    assert "FULL KIRA BOOTSTRAP" not in compact
    assert "FULL KIRA BOOTSTRAP" in changed
    assert "FULL KIRA BOOTSTRAP" in missing

    interrupted = DeepSeekWebBackend._turn_prompt(
        ctx, schemas, full_context, fingerprint,
        {"parent_message_id": 123, "context_fingerprint": fingerprint, "interrupted": True},
        vision=False)
    assert "previous Kira turn ended" in interrupted
    assert "same DeepSeek chat" in interrupted


def test_deepseek_interrupted_turn_retains_remote_thread_and_marks_resume():
    token = "synthetic-token"
    ctx = SimpleNamespace(run_id="run-3", model="deepseek-reasoner")
    backend_ref = DeepSeekWebBackend._remote_session_ref(
        ctx, token, "remote-chat", 123456789, "fingerprint", interrupted=True)
    state = json.loads(backend_ref.split(":", 2)[-1])

    assert state["chat_session_id"] == "remote-chat"
    assert state["parent_message_id"] == 123456789
    assert state["interrupted"] is True


def test_browser_screenshot_is_advertised_only_for_vision_models():
    namespace = browser_tool_specs()[0]
    ctx = SimpleNamespace(
        settings={"tools": True}, permission="read_only", client_tools=[namespace],
        request_input=None, read_input=None,
    )
    text_schemas, _, _ = DeepSeekWebBackend._tool_catalog(ctx, vision=False)
    vision_schemas, _, _ = DeepSeekWebBackend._tool_catalog(ctx, vision=True)
    def tool_name(item):
        return item.get("name") or item["function"]["name"]

    assert "browser__screenshot" not in {tool_name(item) for item in text_schemas}
    assert "browser__screenshot" in {tool_name(item) for item in vision_schemas}


def catalog_context(permission, *, questions=False):
    return SimpleNamespace(
        settings={"tools": True}, permission=permission, client_tools=browser_tool_specs(),
        request_input=object() if questions else None, read_input=object() if questions else None,
        assistant_instructions="When asked, use browser.close.", supplied_context="",
    )


def test_catalog_uses_one_nested_function_shape_with_wire_names_in_descriptions():
    ctx = catalog_context("full_access", questions=True)
    schemas, aliases, question_aliases = DeepSeekWebBackend._tool_catalog(ctx, vision=True)

    assert schemas and all(set(item) == {"type", "function"} and item["type"] == "function"
                           and set(item["function"]) == {"name", "description", "parameters"}
                           for item in schemas)
    names = {item["function"]["name"] for item in schemas}
    assert {"browser__close", "browser__close_tab", "questions__request_input"} <= names
    assert aliases["browser__close"] == "browser.close"
    assert question_aliases["questions__read_input"] == "read_input"
    canonical = {alias.replace("__", ".", 1) for alias in names if "__" in alias}
    for item in schemas:
        description = item["function"]["description"]
        assert not any(re.search(r"(?<![\w.])" + re.escape(name) + r"(?!\w)", description)
                       for name in canonical), description


@pytest.mark.parametrize("permission,scope", [
    ("read_only", "Project paths only."),
    ("project_write", "Project paths only."),
    ("full_access", "external paths allowed as current OS user."),
])
def test_system_prompt_states_wire_names_and_path_scope_once(permission, scope):
    ctx = catalog_context(permission)
    schemas, _, _ = DeepSeekWebBackend._tool_catalog(ctx, vision=True)
    prompt = DeepSeekWebBackend._system_context(ctx, schemas, vision=True)

    # Shared instructions keep canonical names; the catalog header maps them.
    assert "use browser.close." in prompt
    assert "browser.close is browser__close" in prompt
    assert "Use browser__screenshot when" in prompt
    assert "Use browser.screenshot" not in prompt
    assert prompt.count(scope) == 1
    file_descriptions = [item["function"]["description"] for item in schemas
                         if item["function"]["name"] in {"list_files", "read_file"}]
    assert file_descriptions and not any(scope in text for text in file_descriptions)


def test_system_prompt_without_file_tools_omits_path_scope():
    ctx = catalog_context("read_only")
    ctx.settings = {"tools": True}
    schemas = [{"type": "function", "function": {
        "name": "browser__close", "description": "Close.", "parameters": {}}}]
    prompt = DeepSeekWebBackend._system_context(ctx, schemas, vision=False)

    assert "Project paths only." not in prompt
    assert "browser.close is browser__close" in prompt


@pytest.mark.asyncio
async def test_web_vision_upload_uses_pow_and_waits_for_file_processing(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/upload_file"):
            assert request.headers["x-ds-pow-response"] == "pow-upload"
            assert request.headers["x-model-type"] == "vision"
            assert request.headers["x-hif-leim"] == "hif-value"
            return httpx.Response(200, json={"data": {"biz_data": {"id": "file-vision-test"}}})
        assert request.url.path.endswith("/fetch_files")
        assert request.url.params["file_ids"] == "file-vision-test"
        return httpx.Response(200, json={"data": {"biz_data": {"files": [
            {"id": "file-vision-test", "status": "SUCCESS"}]}}})

    class Accounts:
        token = "opaque"

        async def expire(self, token):
            raise AssertionError("the synthetic token should not expire")

    backend = DeepSeekWebBackend(Accounts())
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(base_url="https://chat.deepseek.com",
                                       transport=httpx.MockTransport(handler))

    async def challenge(token, target):
        assert target == "/api/v0/file/upload_file"
        return "pow-upload"

    monkeypatch.setattr(backend, "_challenge", challenge)
    fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\nx").decode("ascii")
    image = {"type": "input_image", "image_url": "data:image/png;base64," + fake_png,
             "detail": "auto"}
    # The adapter checks the PNG signature and encoded size; Kira's image cache
    # has already performed full PNG/container validation before this point.
    try:
        file_id = await backend._upload_image("opaque", image, "hif-value", asyncio.Event())
        assert file_id == "file-vision-test"
        assert len(requests) == 2
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("biz_data", "expected"), [
    ({"chat_session": {"id": "nested-chat", "seq_id": 1}, "ttl_seconds": 259200}, "nested-chat"),
    ({"id": "flat-chat"}, "flat-chat"),
    ({"chat_session_id": "legacy-chat"}, "legacy-chat"),
])
async def test_web_chat_session_id_is_read_from_current_and_older_shapes(biz_data, expected):
    def handler(request):
        assert request.url.path == "/api/v0/chat_session/create"
        return httpx.Response(200, json={"code": 0, "data": {"biz_code": 0, "biz_data": biz_data}})

    backend = DeepSeekWebBackend(SimpleNamespace(token="opaque"))
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(base_url="https://chat.deepseek.com",
                                       transport=httpx.MockTransport(handler))
    try:
        assert await backend._create_session("opaque") == expected
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "message"), [
    ({"code": 0, "data": {"biz_code": 0, "biz_data": {"chat_session": {}}}}, "did not return"),
    ({"code": 0, "data": {"biz_code": 0, "biz_data": None}}, "did not return"),
    ({"code": 0, "data": None}, "did not return"),
    ({"code": 0, "data": {"biz_code": 3, "biz_msg": "busy", "biz_data": None}}, r"\(code 3\)"),
])
async def test_web_chat_session_without_id_fails_safely(body, message):
    backend = DeepSeekWebBackend(SimpleNamespace(token="opaque"))
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(
        base_url="https://chat.deepseek.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    try:
        with pytest.raises(DeepSeekWebError, match=message):
            await backend._create_session("opaque")
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_web_stream_lines_are_split_across_chunks_and_bounded():
    from kira.backends.deepseek_web import _bounded_lines

    class Body(httpx.AsyncByteStream):
        def __init__(self, chunks):
            self.chunks = chunks

        async def __aiter__(self):
            for chunk in self.chunks:
                yield chunk

    response = httpx.Response(200, stream=Body([b"data: {\"a\"", b":1}\r\ndata: x\n\ndata: \xc3", b"\xa8"]))
    assert [line async for line in _bounded_lines(response, 1024)] == [
        'data: {"a":1}', "data: x", "", "data: è"]
    oversized = httpx.Response(200, stream=Body([b"x" * 600, b"x" * 600]))
    with pytest.raises(DeepSeekWebError, match="size limit"):
        _ = [line async for line in _bounded_lines(oversized, 1024)]


def test_pow_solver_rejects_out_of_bounds_allocation_and_oversized_challenge():
    from kira.backends.deepseek_web import PowSolver

    class Memory:
        def __init__(self):
            self.data = bytearray(16)

        def data_len(self, store):
            return len(self.data)

        def write(self, store, data, start):
            self.data[start:start + len(data)] = data

    solver = PowSolver.__new__(PowSolver)
    solver._store = object()
    solver._memory = Memory()
    solver._alloc = lambda store, size: 4
    assert solver._write("abc") == (4, 3) and solver._memory.data[4:7] == b"abc"
    solver._alloc = lambda store, size: 14
    with pytest.raises(DeepSeekWebError, match="outside its linear memory"):
        solver._write("abc")
    solver._lock = asyncio.Lock()
    challenge = {"algorithm": "DeepSeekHashV1", "challenge": "c" * 1025, "salt": "s",
                 "expire_at": 1, "difficulty": 1}
    with pytest.raises(DeepSeekWebError, match="Invalid PoW challenge"):
        asyncio.run(solver.solve(challenge))
