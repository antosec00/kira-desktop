"""DeepSeek Web turns end to end against a simulated site; no network or real token."""

import asyncio
import json

import httpx
import pytest

from kira.backends import deepseek_web
from kira.backends.deepseek_web import (
    MAX_STREAM_FRAGMENTS, WEB, DeepSeekWebBackend, DeepSeekWebError, DeepSeekWebStream, PowSolver,
)
from kira.domain.contracts import RunContext

STANDARD_CAPABILITIES = {"streaming", "tools", "cancellation", "resume", "usage", "images",
                         "context_controls"}
CHALLENGE = {"algorithm": "DeepSeekHashV1", "challenge": "c", "salt": "s", "signature": "sig",
             "difficulty": 1, "expire_at": 1, "target_path": "/api/v0/chat/completion"}


@pytest.fixture(autouse=True)
def _immediate_retries(monkeypatch):
    monkeypatch.setattr(deepseek_web, "EMPTY_REPLY_RETRY_SECONDS", (0, 0, 0))


@pytest.fixture(autouse=True)
def _without_host_network_settings(monkeypatch):
    # The backend's own httpx client reads proxy and CA settings from the
    # environment; a host SOCKS proxy or CA path must not decide these tests.
    for name in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                 "http_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)


class Accounts:
    token = "synthetic-web-token"

    def status(self):
        return {"connected": True}

    async def expire(self, token):
        raise AssertionError("the synthetic token must not expire")


class Solver:
    async def solve(self, challenge):
        assert challenge == CHALLENGE
        return "synthetic-pow"


def sse(*events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def answer(text, *, finished=True, message_id=2, thinking=""):
    fragments = ([{"type": "THINK", "content": thinking}] if thinking else []) + [
        {"type": "RESPONSE", "content": text}]
    events = [{"response_message_id": message_id}, {"v": {"response": {"fragments": fragments}}}]
    if finished:
        events.append({"p": "response/status", "v": "FINISHED"})
    return sse(*events)


class Site:
    """Simulated chat.deepseek.com endpoints; completions are answered in order."""

    def __init__(self, *completions, challenge=None):
        self.completions = list(completions)
        self.challenge = challenge or {"code": 0, "data": {"biz_code": 0,
                                                           "biz_data": {"challenge": CHALLENGE}}}
        self.prompts: list[str] = []
        self.bodies: list[dict] = []

    def __call__(self, request):
        path = request.url.path
        if path == "/api/v0/users/current":
            return httpx.Response(200, json={"code": 0, "data": {"biz_data": {}}})
        if path == "/api/v0/chat/create_pow_challenge":
            return httpx.Response(200, json=self.challenge)
        if path == "/api/v0/chat_session/create":
            return httpx.Response(200, json={"code": 0, "data": {"biz_code": 0, "biz_data": {
                "chat_session": {"id": "remote-chat"}}}})
        assert path == "/api/v0/chat/completion"
        self.bodies.append(json.loads(request.content))
        self.prompts.append(self.bodies[-1]["prompt"])
        completion = self.completions.pop(0)
        if isinstance(completion, httpx.Response):
            return completion
        if isinstance(completion, httpx.AsyncByteStream):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=completion)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=completion)


def backend(site):
    instance = DeepSeekWebBackend(Accounts())
    instance.client = httpx.AsyncClient(base_url=WEB, transport=httpx.MockTransport(site))
    instance._solver = Solver()
    return instance


def context(events, cancel=None):
    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute_tool(name, arguments):
        return {"ok": True, "entries": []}

    async def approve(_request):
        raise AssertionError("no approval expected")

    return RunContext(run_id="run-web", project={"id": "project", "name": "Synthetic",
                      "root": "/nonexistent", "permission": "read_only"},
                      session={"backend": "deepseek_web", "model": "deepseek-reasoner",
                               "settings": {}},
                      prompt="Synthetic request", supplied_context="", emit=emit,
                      execute_tool=execute_tool, approve=approve, cancel=cancel or asyncio.Event())


async def run(site, events, cancel=None):
    instance = backend(site)
    try:
        return await asyncio.wait_for(instance.run(context(events, cancel)), 5)
    finally:
        await instance.close()


def kinds(events, kind):
    return [payload for name, payload in events if name == kind]


def summary_events(events):
    return [(name, payload) for name, payload in events if name.startswith("reasoning.summary.")]


async def test_reasoning_reaches_only_the_reasoning_panel():
    events = []
    result = await run(Site(answer("Visible answer", thinking="SYNTHETIC-SITE-REASONING")), events)

    assert result["state"] == "completed"
    assert result["outcome"]["content"] == "Visible answer"
    assert kinds(events, "message.completed") == [{"content": "Visible answer", "item_id": "web:0"}]
    assert kinds(events, "reasoning.summary.status") == [
        {"provider": "deepseek_web", "source": "provider_summary", "status": "requested"}]
    assert kinds(events, "reasoning.summary.completed") == [
        {"item_id": "summary:0", "provider": "deepseek_web", "source": "provider_summary",
         "content": "SYNTHETIC-SITE-REASONING", "complete": True, "truncated": False}]
    others = [event for event in events if not event[0].startswith("reasoning.summary.")]
    assert "SYNTHETIC-SITE-REASONING" not in json.dumps([others, result])


async def test_long_reasoning_streams_in_batches_before_the_reply():
    thinking = "".join(f" step {index}." for index in range(120))
    pieces = [thinking[start:start + 9] for start in range(0, len(thinking), 9)]
    site_events = [{"response_message_id": 2},
                   {"v": {"response": {"fragments": [{"type": "THINK", "content": ""}]}}},
                   {"p": "response/fragments/-1/content", "o": "APPEND", "v": pieces[0]},
                   *({"v": piece} for piece in pieces[1:]),
                   {"p": "response/fragments", "o": "APPEND", "v": [{"type": "RESPONSE", "content": "Fin"}]},
                   {"p": "response/fragments/-1/content", "v": "ished."},
                   {"p": "response/status", "o": "SET", "v": "FINISHED"}]
    events = []
    result = await run(Site(sse(*site_events)), events)

    assert result["outcome"]["content"] == "Finished."
    deltas = kinds(events, "reasoning.summary.delta")
    assert 1 < len(deltas) < len(pieces) / 4
    assert "".join(delta["content"] for delta in deltas) == thinking[:len("".join(
        delta["content"] for delta in deltas))]
    assert kinds(events, "reasoning.summary.completed")[-1]["content"] == thinking


SITE_NOTICE = {"type": "TIP", "style": "WARNING", "hide_on_wip": True,
               "content": "This response is AI-generated, for reference only."}


def with_site_notice(text, *, message_id):
    return sse({"response_message_id": message_id},
               {"v": {"response": {"fragments": [{"type": "THINK", "content": "Planning."},
                                                 {"type": "RESPONSE", "content": text}]}}},
               {"p": "response/fragments", "o": "APPEND", "v": [SITE_NOTICE]},
               {"p": "response/status", "o": "SET", "v": "FINISHED"})


async def test_site_notice_after_a_tool_call_does_not_need_a_correction():
    call = json.dumps({"kira_tool_call": {"name": "list_files", "arguments": {"path": "."}}})
    site = Site(with_site_notice(call, message_id=2), with_site_notice("No files yet.", message_id=3))
    events = []
    result = await run(site, events)

    assert result["state"] == "completed"
    assert result["outcome"]["content"] == "No files yet."
    # The call ran at once: the second prompt carries its result, not a correction.
    assert len(site.prompts) == 2 and site.prompts[1].startswith("Kira tool results")
    assert "AI-generated" not in json.dumps([kinds(events, "message.completed"), result])


async def test_flash_model_reports_that_it_does_not_reason():
    site = Site(answer("Done."))
    events = []
    instance = backend(site)
    ctx = context(events)
    ctx.session["model"] = "deepseek-v4-flash"
    try:
        result = await asyncio.wait_for(instance.run(ctx), 5)
    finally:
        await instance.close()

    assert result["state"] == "completed" and site.bodies[0]["thinking_enabled"] is False
    assert summary_events(events) == [
        ("reasoning.summary.status", {"provider": "deepseek_web", "source": "provider_summary",
                                      "status": "unavailable", "reason": "thinking_disabled"})]


async def test_incomplete_response_interrupts_the_turn_and_keeps_the_remote_chat():
    events = []
    result = await run(Site(answer("Partial answer", finished=False)), events)

    assert result["state"] == "interrupted"
    assert "ended the response before it was complete" in result["error"]
    assert result["outcome"]["tool_execution"] == "not_started"
    assert not kinds(events, "message.completed")
    reference = kinds(events, "session.updated")[-1]["backend_ref"]
    state = json.loads(reference.split(":", 2)[-1])
    assert state["chat_session_id"] == "remote-chat" and state["interrupted"] is True


async def test_repeated_invalid_tool_calls_interrupt_without_an_injected_answer():
    events = []
    broken = '{"kira_tool_call": {"name": "list_files", "arguments": '
    site = Site(answer(broken), answer(broken), answer(broken))
    result = await run(site, events)

    assert result["state"] == "interrupted"
    assert "invalid tool call" in result["error"]
    assert len(site.prompts) == 3
    assert not kinds(events, "message.completed")
    assert kinds(events, "session.updated")


async def test_unregistered_tool_name_gets_a_correction_request():
    events = []
    unknown = json.dumps({"kira_tool_call": {"name": "not_registered", "arguments": {}}})
    site = Site(answer(unknown), answer("Answered without tools", message_id=3))
    result = await run(site, events)

    assert result["state"] == "completed"
    assert result["outcome"]["content"] == "Answered without tools"
    assert len(site.prompts) == 2
    assert "rejected it" in site.prompts[1] and "not_registered" in site.prompts[1]
    assert not kinds(events, "tool.started")


async def test_stop_closes_a_streaming_response_promptly():
    started = asyncio.Event()
    cancel = asyncio.Event()

    class Hanging(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"response_message_id": 2}\n\n'
            started.set()
            await asyncio.Event().wait()

    events = []
    instance = backend(Site(Hanging()))
    try:
        task = asyncio.create_task(instance.run(context(events, cancel)))
        await asyncio.wait_for(started.wait(), 5)
        cancel.set()
        result = await asyncio.wait_for(task, 2)
    finally:
        await instance.close()

    assert result == {"state": "cancelled", "outcome": {
        "scope": "client_stream_closed", "remote_generation_termination_confirmed": False}}


async def test_http_error_reports_status_and_code_but_never_the_body():
    events = []
    error = httpx.Response(400, json={"code": 40001, "msg": "SYNTHETIC-PROVIDER-BODY", "data": None})
    with pytest.raises(DeepSeekWebError) as raised:
        await run(Site(error), events)

    assert str(raised.value) == "DeepSeek web completion returned HTTP 400 (code 40001)"


def busy():
    return httpx.Response(429, json={"code": 42901, "msg": "SYNTHETIC-PROVIDER-BODY", "data": None})


async def test_rate_limited_request_is_resent_and_then_answered():
    site = Site(busy(), busy(), answer("Done."))
    events = []
    result = await run(site, events)

    assert result["state"] == "completed" and result["outcome"]["content"] == "Done."
    assert len(site.prompts) == 3 and len(set(site.prompts)) == 1
    notices = [payload["message"] for payload in kinds(events, "backend.notice")]
    assert notices[:2] == ["DeepSeek Web sent no reply (HTTP 429 (code 42901)); resending the same "
                           "request in 0 s"] * 2
    assert "SYNTHETIC-PROVIDER-BODY" not in json.dumps([events, result])


async def test_persistent_empty_replies_interrupt_after_three_new_attempts():
    envelope = {"code": 0, "data": {"biz_code": 3, "biz_msg": "SYNTHETIC-PROVIDER-BODY"}}
    site = Site(*(httpx.Response(200, json=envelope) for _ in range(4)))
    events = []
    result = await run(site, events)

    assert result["state"] == "interrupted" and len(site.prompts) == 4
    assert result["error"].startswith(
        "DeepSeek Web sent no reply, also after 3 new attempts (biz_code 3); no tool")
    assert result["outcome"]["tool_execution"] == "not_started"
    assert "SYNTHETIC-PROVIDER-BODY" not in json.dumps([events, result])


async def test_site_error_notice_is_never_read_as_reply_text():
    site = Site(sse({"type": "error", "content": "SYNTHETIC-PROVIDER-BODY", "finish_reason": "busy"}),
                answer("Done."))
    events = []
    result = await run(site, events)

    assert result["outcome"]["content"] == "Done." and len(site.prompts) == 2
    assert "type error, finish_reason busy" in kinds(events, "backend.notice")[0]["message"]
    assert "SYNTHETIC-PROVIDER-BODY" not in json.dumps([events, result])


async def test_oversized_http_error_body_is_not_read_to_the_end():
    pulled = []

    class Large(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(64):
                pulled.append(1)
                yield b"x" * 65_536

    with pytest.raises(DeepSeekWebError) as raised:
        await run(Site(httpx.Response(400, stream=Large())), [])

    assert str(raised.value) == "DeepSeek web completion returned HTTP 400"
    assert len(pulled) <= 2


@pytest.mark.parametrize("body", [
    {"code": 40003, "msg": "SYNTHETIC", "data": None},
    {"code": 0, "data": {"biz_code": 7, "biz_data": None}},
    {"code": 0, "data": None},
    {"code": 0, "data": {"biz_code": 0, "biz_data": {"challenge": None}}},
    ["unexpected"],
])
async def test_malformed_pow_challenge_responses_fail_safely(body):
    instance = backend(Site(challenge=body))
    try:
        with pytest.raises(DeepSeekWebError, match="PoW challenge"):
            await instance._challenge("synthetic-web-token", "/api/v0/chat/completion")
    finally:
        await instance.close()


async def test_non_json_pow_challenge_response_fails_safely():
    def handler(request):
        return httpx.Response(200, content=b"<html>maintenance</html>")

    instance = backend(Site())
    instance.client = httpx.AsyncClient(base_url=WEB, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DeepSeekWebError, match="invalid PoW challenge response"):
            await instance._challenge("synthetic-web-token", "/api/v0/chat/completion")
    finally:
        await instance.close()


@pytest.mark.parametrize("change", [
    {"expire_at": "tomorrow"}, {"difficulty": None}, {"difficulty": float("inf")},
    {"challenge": None}, {"salt": 7}, {"algorithm": "X" * 10_000},
])
async def test_pow_challenge_fields_are_validated_before_solving(change):
    solver = PowSolver.__new__(PowSolver)
    solver._lock = asyncio.Lock()
    with pytest.raises(DeepSeekWebError) as raised:
        await solver.solve({**CHALLENGE, **change})
    assert len(str(raised.value)) < 200


def test_far_fragment_index_is_rejected_without_allocating_it():
    stream = DeepSeekWebStream()
    with pytest.raises(DeepSeekWebError, match="out-of-range"):
        stream.apply({"p": "response/fragments/300000/content", "o": "APPEND", "v": "x"})
    assert len(stream.fragments) <= MAX_STREAM_FRAGMENTS


def test_fragment_count_is_bounded_and_small_gaps_still_apply():
    stream = DeepSeekWebStream()
    assert stream.apply({"p": "response/fragments/1/content", "o": "APPEND", "v": "Visible"}) == "Visible"
    batch = [{"type": "RESPONSE", "content": ""}] * 128
    with pytest.raises(DeepSeekWebError, match="fragment limit"):
        for _ in range(MAX_STREAM_FRAGMENTS // 128 + 1):
            stream.apply({"p": "response/fragments", "o": "APPEND", "v": batch})
    assert len(stream.fragments) <= MAX_STREAM_FRAGMENTS


async def test_discovery_reports_the_standard_capability_keys():
    instance = backend(Site())
    try:
        result = await instance.discover()
    finally:
        await instance.close()

    assert result["available"] is True
    assert set(result["capabilities"]) == STANDARD_CAPABILITIES
    assert result["capabilities"]["cancellation"] is True
    for model in result["models"]:
        assert set(model["capabilities"]) == STANDARD_CAPABILITIES
        assert model["capabilities"]["images"] is True and model["capabilities"]["tools"] is True


@pytest.mark.parametrize(("model", "thinking"), [("deepseek-reasoner", True),
                                                 ("deepseek-v4-flash", False)])
async def test_reasoner_model_turns_on_the_web_thinking_mode(model, thinking):
    site = Site(answer("Done."))
    events = []
    instance = backend(site)
    ctx = context(events)
    ctx.session["model"] = model
    try:
        result = await asyncio.wait_for(instance.run(ctx), 5)
    finally:
        await instance.close()
    assert result["state"] == "completed"
    assert site.bodies[0]["thinking_enabled"] is thinking


async def test_multiple_standalone_json_tool_calls_execute_in_one_round():
    batch_text = (
        '{"kira_tool_call":{"name":"list_files","arguments":{"path":"."}}}\n'
        '{"kira_tool_call":{"name":"list_files","arguments":{"path":"src"}}}'
    )
    site = Site(answer(batch_text, message_id=2), answer("Both listed in one round.", message_id=3))
    events = []
    result = await run(site, events)
    assert result["state"] == "completed"
    assert result["outcome"]["tool_rounds"] == 1
    assert len(site.prompts) == 2


async def test_rate_limited_pow_challenge_is_retried_and_succeeds():
    pow_calls = 0

    def handler(request):
        nonlocal pow_calls
        path = request.url.path
        if path == "/api/v0/chat_session/create":
            return httpx.Response(200, json={"code": 0, "data": {"biz_code": 0, "biz_data": {"chat_session": {"id": "remote-chat"}}}})
        if path == "/api/v0/chat/create_pow_challenge":
            pow_calls += 1
            if pow_calls == 1:
                return httpx.Response(429, json={"code": 42901, "msg": "Too frequent", "data": None})
            return httpx.Response(200, json={"code": 0, "data": {"biz_code": 0, "biz_data": {"challenge": CHALLENGE}}})
        assert path == "/api/v0/chat/completion"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=answer("Recovered after 429 on PoW."))

    instance = backend(Site())
    instance.client = httpx.AsyncClient(base_url=WEB, transport=httpx.MockTransport(handler))
    events = []
    try:
        result = await asyncio.wait_for(instance.run(context(events)), 5)
    finally:
        await instance.close()
    assert result["state"] == "completed"
    assert pow_calls == 2

