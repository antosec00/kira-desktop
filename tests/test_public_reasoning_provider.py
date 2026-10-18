"""Synthetic public-summary delivery, isolation and admission-retry checks."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from kira.backends.codex import AppServer
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.backends.reasoning_summary import MAX_SUMMARY_BYTES, PublicSummaries
from test_backends_codex import context as codex_context
from test_backends_codex import event, terminal
from test_backends_codex import fake as fake  # noqa: F401
from test_backends_openai_chatgpt import FakeAuth, call, context, message, wire
from test_backends_openai_chatgpt import terminal as response_terminal


def reasoning(text="Public explanation", **extras):
    return {"id": "reasoning-1", "type": "reasoning", "summary": [
        {"type": "summary_text", "text": text}], "encrypted_content": "opaque-state-canary",
        "content": [{"type": "reasoning_text", "text": "private-content-canary"}], **extras}


def summary_event(kind, **values):
    return {"type": "response.reasoning_summary_" + kind, "item_id": "reasoning-1",
            "output_index": 0, "summary_index": 0, **values}


async def test_public_summary_streams_before_answer_and_reconciles_all_completion_forms(tmp_path):
    ctx, events, executed = context(tmp_path)
    item = reasoning()
    records = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": reasoning(summary=[])},
        {"type": "response.reasoning_text.delta", "delta": "private-delta-canary"},
        summary_event("text.delta", delta="Public "),
        summary_event("text.delta", delta="explanation"),
        summary_event("text.done", text="Public explanation"),
        summary_event("part.done", part={"type": "summary_text", "text": "Public explanation"}),
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        response_terminal([item, message("Final answer")])]
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return wire(records)

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handle))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert bodies[0]["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert bodies[0]["model"] == "fixture-model"
    summaries = [(kind, payload) for kind, payload in events if kind.startswith("reasoning.summary")]
    assert [payload["content"] for kind, payload in summaries if kind.endswith(".delta")] == ["Public ", "explanation"]
    done = [payload for kind, payload in summaries if kind.endswith(".completed")]
    assert len(done) == 1 and done[0]["content"] == "Public explanation" and done[0]["complete"]
    assert events.index(next(item for item in events if item[0] == "reasoning.summary.delta")) < events.index(
        next(item for item in events if item[0] == "message.completed"))
    assert not executed
    assert all(canary not in json.dumps(events) for canary in (
        "opaque-state-canary", "private-content-canary", "private-delta-canary"))
    assert all("Public" not in p.get("content", "") for k, p in events if k.startswith("message."))


async def test_terminal_only_summary_preserves_native_opaque_tool_continuation(tmp_path):
    ctx, events, executed = context(tmp_path)
    native = reasoning()
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return wire([response_terminal([native, call()])]) if len(bodies) == 1 else wire([response_terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handle))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert native in bodies[1]["input"]
    assert len(executed) == 1
    assert len([p for k, p in events if k == "reasoning.summary.completed"]) == 1


@pytest.mark.parametrize("status,code,param,retry", [
    (400, "unsupported_parameter", "reasoning.summary", True),
    (400, "unsupported_value", "reasoning[summary]", True),
    (400, "unsupported_parameter", "reasoning", False),
    (400, "invalid_request_error", "reasoning.summary", False),
    (401, "unsupported_parameter", "reasoning.summary", False),
    (429, "unsupported_parameter", "reasoning.summary", False),
    (503, "unsupported_parameter", "reasoning.summary", False),
])
async def test_only_optional_summary_http_admission_rejection_retries(tmp_path, status, code, param, retry):
    ctx, events, executed = context(tmp_path)
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(status, json={"error": {"code": code, "param": param}})
        return wire([response_terminal([call()])]) if len(bodies) == 2 else wire([response_terminal()])

    auth = FakeAuth()
    backend = OpenAIChatGPTBackend(auth, transport=httpx.MockTransport(handle))
    try:
        result = await backend.run(ctx)
    finally:
        await backend.close()
    assert len(bodies) == (3 if retry else 1)
    assert len(executed) == int(retry)
    assert result["state"] == ("completed" if retry else "failed")
    if retry:
        assert bodies[1] == {**bodies[0], "reasoning": {"effort": "xhigh"}}
        assert bodies[2]["reasoning"] == {"effort": "xhigh"}
        assert len(set(auth.accounts)) == 1
        assert any(k == "reasoning.summary.status" and p["status"] == "unsupported" for k, p in events)


@pytest.mark.parametrize("failure", ["sse_error", "disconnect", "cancel"])
async def test_summary_never_replays_accepted_or_uncertain_request(tmp_path, failure):
    ctx, events, executed = context(tmp_path)
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "disconnect":
            raise httpx.ReadError("fixture disconnected")
        if failure == "cancel":
            ctx.cancel.set()
        return wire([{"type": "response.created", "response": {}},
            {"type": "error", "error": {"code": "unsupported_parameter", "param": "reasoning.summary"}}])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handle))
    try:
        assert (await backend.run(ctx))["state"] != "completed"
    finally:
        await backend.close()
    assert len(requests) == 1 and not executed


async def test_unknown_summary_identity_and_invalid_optional_content_do_not_break_output(tmp_path):
    ctx, events, executed = context(tmp_path)
    records = [summary_event("text.delta", delta="unbound-canary"),
        {"type": "response.output_item.added", "output_index": 0, "item": reasoning(summary=[])},
        summary_event("text.delta", item_id="wrong", delta="wrong-item-canary"),
        summary_event("text.delta", summary_index=True, delta="bad-index-canary"),
        summary_event("text.delta", delta={"text": "bad-type-canary"}),
        response_terminal([reasoning(summary=[{"type": "reasoning_text", "text": "raw-type-canary"}]), message()])]
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda r: wire(records)))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert "canary" not in json.dumps(events)
    assert any(k == "reasoning.summary.status" and p["status"] == "unavailable" for k, p in events)


async def test_summary_display_bounds_do_not_interrupt_tool_execution(tmp_path):
    ctx, events, executed = context(tmp_path)
    seen = 0

    def handle(request):
        nonlocal seen
        seen += 1
        if seen == 1:
            return wire([response_terminal([reasoning("é" * MAX_SUMMARY_BYTES), call()])])
        return wire([response_terminal()])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handle))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    done = [p for k, p in events if k == "reasoning.summary.completed"]
    assert len(done[0]["content"].encode()) == MAX_SUMMARY_BYTES and done[0]["truncated"]
    assert len(executed) == 1


async def test_run_summary_bound_covers_multiple_provider_responses(tmp_path):
    ctx, events, _ = context(tmp_path)
    summaries = PublicSummaries(ctx, "openai_chatgpt")
    for scope in range(70):
        summaries.new_response(scope)
        await summaries.openai_item(reasoning("part" * 400), 0, complete=True)
    await summaries.finish()
    assert len(summaries.parts) <= 64
    assert sum(len(p["content"].encode()) for k, p in events if k.endswith(".completed")) <= MAX_SUMMARY_BYTES


@pytest.mark.parametrize("maintenance", ["native", "readable"])
async def test_auxiliary_compaction_requests_do_not_request_or_publish_summaries(tmp_path, maintenance):
    ctx, events, _ = context(tmp_path)
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return wire([response_terminal([reasoning(), message(json.dumps({"summary": "Historical handoff", "memory": []}))])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handle))
    try:
        if maintenance == "native":
            await backend.context_fold([], ctx, "account-a", "fixture instructions")
        else:
            await backend.context_summary([], [], [], ctx, "account-a")
    finally:
        await backend.close()
    assert bodies and "summary" not in bodies[0].get("reasoning", {})
    assert not any(k.startswith("reasoning.summary") for k, _ in events)


async def test_codex_public_only_and_exact_active_turn_correlation(tmp_path, fake):
    from kira.backends.codex import CodexBackend
    ctx, events, _, executed = codex_context(tmp_path)
    fake.queued = [
        event("item/reasoning/summaryTextDelta", itemId="r1", summaryIndex=0, delta="Public "),
        event("item/reasoning/summaryTextDelta", itemId="r1", summaryIndex=0, delta="summary"),
        event("item/reasoning/summaryTextDelta", threadId="unrelated", itemId="r1", summaryIndex=0, delta="wrong-canary"),
        event("item/reasoning/textDelta", itemId="r1", delta="raw-canary"),
        event("item/completed", item={"id": "r1", "type": "reasoning", "summary": ["Public summary"], "content": ["private-canary"]}),
        terminal("completed")]
    backend = CodexBackend()
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert "canary" not in json.dumps(events) and not executed
    assert dict(fake.instances[0].requests)["turn/start"]["summary"] == "auto"
    done = [p for k, p in events if k == "reasoning.summary.completed"]
    assert len(done) == 1 and done[0]["content"] == "Public summary"


async def test_codex_reader_strips_private_fields_and_caps_optional_queue_text():
    reader = asyncio.StreamReader(limit=8 * 1024 * 1024)
    rpc = AppServer("fixture")
    rpc.process = SimpleNamespace(stdout=reader)
    values = [event("item/reasoning/textDelta", delta="private-stream-canary"),
              event("codex/event/agent_reasoning", message="legacy-canary"),
              event("item/reasoning/summaryTextDelta", itemId="r1", summaryIndex=0,
                    delta="é" * MAX_SUMMARY_BYTES, private="extra-canary"),
              event("item/completed", item={"id": "r1", "type": "reasoning", "summary": ["Public"],
                    "content": ["private-content-canary"], "encrypted_content": "opaque-canary"})]
    for value in values:
        reader.feed_data(json.dumps(value).encode() + b"\n")
    reader.feed_eof()
    await rpc._read()
    queued = []
    while not rpc.notifications.empty():
        queued.append(rpc.notifications.get_nowait())
    assert "canary" not in json.dumps(queued)
    assert rpc._summary_filter.bytes <= MAX_SUMMARY_BYTES
    public = [e for e in queued if e["method"] != "kira/disconnected"]
    assert len(public) == 2
    assert public[0]["params"]["summaryTruncated"]


async def test_interrupted_summary_remains_partial_and_does_not_confirm_answer(tmp_path):
    ctx, events, executed = context(tmp_path)
    records = [{"type": "response.output_item.added", "output_index": 0, "item": reasoning(summary=[])},
               summary_event("text.delta", delta="Public partial summary")]
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request: wire(records)))
    try:
        assert (await backend.run(ctx))["state"] == "interrupted"
    finally:
        await backend.close()
    done = [p for k, p in events if k == "reasoning.summary.completed"]
    assert len(done) == 1 and done[0]["content"] == "Public partial summary" and not done[0]["complete"]
    assert not executed and not any(k.startswith("message.") for k, _ in events)


async def test_65th_summary_part_marks_visible_limit_without_interrupting_work(tmp_path):
    ctx, events, _ = context(tmp_path)
    summaries = PublicSummaries(ctx, "openai_chatgpt")
    await summaries.status("requested")
    for position in range(70):
        await summaries.update(f"fixture-{position}", 0, "Short public part", complete=True)
    assert len(summaries.parts) == 64
    limit = [p for k, p in events if k == "reasoning.summary.status" and p.get("reason") == "summary_item_limit"]
    assert len(limit) == 1 and limit[0]["status"] == "unavailable"


@pytest.mark.parametrize("provider", ["openai_chatgpt", "codex"])
async def test_nonempty_started_summary_is_visible_before_following_delta(tmp_path, provider):
    ctx, events, _ = context(tmp_path)
    summaries = PublicSummaries(ctx, provider)
    if provider == "openai_chatgpt":
        summaries.new_response(0)
        await summaries.openai_event({"type": "response.output_item.added", "output_index": 0,
                                      "item": reasoning("Initial ")})
        assert events[-1][1]["content"] == "Initial "
        await summaries.openai_event(summary_event("text.delta", delta="continuation"))
    else:
        await summaries.codex_event("item/started", {"item": {
            "id": "r1", "type": "reasoning", "summary": ["Initial "]}})
        assert events[-1][1]["content"] == "Initial "
        await summaries.codex_event("item/reasoning/summaryTextDelta", {
            "itemId": "r1", "summaryIndex": 0, "delta": "continuation"})
    assert "".join(p["content"] for k, p in events if k == "reasoning.summary.delta") == "Initial continuation"
    await summaries.finish()
    assert events[-1][1]["content"] == "Initial continuation" and not events[-1][1]["complete"]


@pytest.mark.parametrize("provider", ["openai_chatgpt", "codex"])
async def test_changed_authoritative_completion_replaces_public_summary(tmp_path, provider):
    ctx, events, _ = context(tmp_path)
    summaries = PublicSummaries(ctx, provider)
    if provider == "openai_chatgpt":
        summaries.new_response(0)
        await summaries.openai_event({"type": "response.output_item.added", "output_index": 0,
                                      "item": reasoning(summary=[])})
        await summaries.openai_event(summary_event("text.done", text="Earlier completion"))
        await summaries.openai_item(reasoning("Authoritative terminal summary"), 0, complete=True)
    else:
        await summaries.codex_event("item/completed", {"item": {
            "id": "r1", "type": "reasoning", "summary": ["Earlier completion"]}})
        await summaries.codex_event("item/completed", {"item": {
            "id": "r1", "type": "reasoning", "summary": ["Authoritative terminal summary"]}})
    done = [p for k, p in events if k == "reasoning.summary.completed"]
    assert [p["content"] for p in done] == ["Earlier completion", "Authoritative terminal summary"]
    assert done[0]["item_id"] == done[1]["item_id"] and all(p["complete"] for p in done)


def test_codex_foreign_early_scope_cannot_spend_active_text_event_or_item_budget():
    from kira.backends.reasoning_summary import CodexSummaryFilter
    summaries = CodexSummaryFilter()
    summaries.event(event("item/reasoning/summaryTextDelta", threadId="foreign-thread", itemId="r1",
                          summaryIndex=0, delta="x" * MAX_SUMMARY_BYTES))
    # Real notifications may arrive before the turn/start acknowledgement.
    actual = summaries.event(event("item/reasoning/summaryTextDelta", itemId="r1", summaryIndex=0,
                                   delta="Actual public summary"))
    assert actual["params"]["delta"] == "Actual public summary"
    assert not actual["params"]["summaryTruncated"]
    summaries.bind_thread("thread-fixture")
    summaries.bind_turn("thread-fixture", "turn-fixture")
    assert summaries.bytes == len("Actual public summary")
    for _ in range(513):
        assert summaries.event(event("item/reasoning/summaryTextDelta", turnId="foreign-turn", itemId="r1",
                                     summaryIndex=0, delta="foreign")) is None
    followup = summaries.event(event("item/reasoning/summaryTextDelta", itemId="r1", summaryIndex=0, delta=" continued"))
    assert followup["params"]["delta"] == " continued" and not followup["params"]["summaryTruncated"]


def test_codex_same_authoritative_completion_does_not_double_charge_budget():
    from kira.backends.reasoning_summary import CodexSummaryFilter
    summaries = CodexSummaryFilter()
    text = "x" * 40_000
    summaries.event(event("item/reasoning/summaryTextDelta", itemId="r1", summaryIndex=0, delta=text))
    completed = summaries.event(event("item/completed", item={"id": "r1", "type": "reasoning", "summary": [text]}))
    assert completed["params"]["item"]["summary"] == [text]
    assert not completed["params"]["item"].get("summaryTruncated")
    assert summaries.bytes == 40_000
    following = summaries.event(event("item/reasoning/summaryTextDelta", itemId="r2", summaryIndex=0, delta="y" * 20_000))
    assert len(following["params"]["delta"]) == 20_000 and not following["params"]["summaryTruncated"]
    assert summaries.bytes == 60_000


async def test_codex_reader_limits_are_visible_in_active_summary_events(tmp_path):
    from kira.backends.reasoning_summary import CodexSummaryFilter
    ctx, events, _ = context(tmp_path)
    filtered = CodexSummaryFilter()
    summaries = PublicSummaries(ctx, "codex")
    for part_index in range(65):
        value = filtered.event(event("item/reasoning/summaryTextDelta", itemId=f"r{part_index}",
                                     summaryIndex=0, delta="part"))
        if value:
            await summaries.codex_event(value["method"], value["params"])
    assert len(summaries.parts) == 64
    assert any(k == "reasoning.summary.status" and p.get("reason") == "summary_item_limit" for k, p in events)
