"""Deterministic native question protocol, separate from live model evidence."""

import asyncio
import copy
import json

import pytest

from kira.backends import codex
from kira.domain.contracts import KiraError
from test_backends_codex import FakeServer, context, event, terminal


QUESTION = {"id": "theme", "header": "Theme", "question": "Choose a fixture color",
            "isOther": True, "options": [{"label": "Blue", "description": "Cool"},
                                         {"label": "Green", "description": "Natural"}]}
ANSWER = {"theme": {"answers": ["Blue"]}}


def question(request_id=91, **params):
    return {"id": request_id, **event("item/tool/requestUserInput", itemId="input-fixture",
                                     isBlocking=True, questions=[copy.deepcopy(QUESTION)], **params)}


class QuestionServer(FakeServer):
    feature_enabled = True
    steer_error = False
    steer_turn = "turn-fixture"

    async def start(self, *, experimental_api=True):  # Same default as codex.AppServer.
        self.experimental = experimental_api

    async def request(self, method, params, timeout=30):
        if method == "turn/steer":
            self.requests.append((method, params))
            if self.steer_error:
                raise codex.RpcError("Fixture active turn no longer accepts input")
            return {"turnId": self.steer_turn}
        if method == "experimentalFeature/list":
            self.requests.append((method, params))
            return {"data": [{"name": name, "enabled": self.feature_enabled}
                             for name in codex.QUESTION_FEATURES], "nextCursor": None}
        return await super().request(method, params, timeout)


@pytest.fixture
def server(monkeypatch):
    # Pin the inherited FakeServer state too; monkeypatch restores all of it afterwards.
    for name, value in {"instances": [], "queued": [], "resolved_model": "fixture-model",
                        "resolved_effort": "ultra", "feature_enabled": True,
                        "steer_error": False, "steer_turn": "turn-fixture"}.items():
        monkeypatch.setattr(QuestionServer, name, value)
    monkeypatch.setattr(codex, "AppServer", QuestionServer)
    return QuestionServer


def question_context(tmp_path):
    ctx, events, approvals, executions = context(tmp_path)
    calls, reads = [], []
    async def request(payload):
        calls.append(payload)
        return {"ok": True, "request_id": "durable-input", "status": "answered", "answers": ANSWER}
    async def read(identity, wait):
        reads.append((identity, wait))
        return {"ok": True, "request_id": identity, "status": "answered", "answers": ANSWER}
    ctx.request_input, ctx.read_input = request, read
    return ctx, events, approvals, executions, calls, reads


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 10)


async def test_native_question_preserves_selection_and_uses_separate_input_callback(tmp_path, server):
    ctx, events, approvals, executions, calls, _ = question_context(tmp_path)
    server.queued = [question()]
    run = asyncio.create_task(codex.CodexBackend().run(ctx))
    await until(lambda: server.instances and server.instances[0].responses)
    rpc = server.instances[0]
    await rpc.notifications.put(terminal("completed"))
    assert (await asyncio.wait_for(run, 30))["state"] == "completed"
    assert rpc.responses == [(91, {"answers": ANSWER})]
    assert ("input.delivered", {"request_id": "durable-input",
                                "transport": "json-rpc-response"}) in events
    assert calls == [{"request_id": "input-fixture", "blocking": True, "questions": [QUESTION]}]
    assert not approvals and not executions
    params = next(p for m, p in rpc.requests if m == "thread/start")
    assert params["config"]["features"] == dict.fromkeys(codex.QUESTION_FEATURES, True)
    assert params["model"] == "fixture-model"
    assert params["config"]["model_reasoning_effort"] == "ultra"
    assert params["sandbox"] == "workspace-write"
    assert rpc.experimental


async def test_native_async_question_observes_work_then_delivers_actual_answer(tmp_path, server):
    ctx, events, _, _, calls, reads = question_context(tmp_path)
    released = asyncio.Event()
    async def request(payload):
        calls.append(payload)
        return {"ok": True, "request_id": "durable-input", "status": "pending", "blocking": False}
    async def read(identity, wait):
        reads.append((identity, wait))
        await released.wait()
        return {"ok": True, "status": "answered", "answers": ANSWER}
    ctx.request_input, ctx.read_input = request, read
    req = question()
    req["params"]["isBlocking"] = False
    server.queued = [req, event("item/agentMessage/delta", itemId="independent", delta="Working")]
    run = asyncio.create_task(codex.CodexBackend().run(ctx))
    await until(lambda: reads and any(k == "message.delta" for k, _ in events))
    rpc = server.instances[0]
    assert not rpc.responses and not run.done()
    assert calls[0]["blocking"] is False and reads == [("durable-input", True)]
    released.set()
    await until(lambda: rpc.responses)
    await rpc.notifications.put(terminal("completed"))
    assert (await asyncio.wait_for(run, 30))["state"] == "completed"
    assert rpc.responses == [(91, {"answers": ANSWER})]


@pytest.mark.parametrize("cause", ["cancel", "disconnect", "resolved", "completed"])
async def test_question_wait_does_not_hide_terminal_or_cancellation(tmp_path, server, cause):
    ctx, _, _, _, _, _ = question_context(tmp_path)
    waiting, cancelled = asyncio.Event(), asyncio.Event()
    async def request(_):
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    ctx.request_input = request
    server.queued = [question()]
    run = asyncio.create_task(codex.CodexBackend().run(ctx))
    await asyncio.wait_for(waiting.wait(), 2)
    rpc = server.instances[0]
    if cause == "cancel":
        ctx.cancel.set()
    elif cause == "disconnect":
        await rpc.notifications.put({"method": "kira/disconnected", "params": {"error": "fixture"}})
    elif cause == "resolved":
        await rpc.notifications.put(event("serverRequest/resolved", requestId=91))
        await asyncio.wait_for(cancelled.wait(), 2)
        await rpc.notifications.put(terminal("completed"))
    else:
        await rpc.notifications.put(terminal("completed"))
    outcome = await asyncio.wait_for(run, 2)
    assert cancelled.is_set()
    assert outcome["state"] == {"cancel": "cancelled", "disconnect": "interrupted"}.get(cause, "completed")
    assert not rpc.responses


@pytest.mark.parametrize("change", ["secret", "duplicates", "oversized", "wrong_boolean", "missing_turn", "rpc_id"])
async def test_invalid_questions_never_reach_service(tmp_path, server, change):
    ctx, _, _, _, calls, _ = question_context(tmp_path)
    req = question()
    if change == "secret":
        req["params"]["questions"][0]["isSecret"] = True
    elif change == "duplicates":
        req["params"]["questions"] *= 2
    elif change == "oversized":
        req["params"]["questions"][0]["question"] = "x" * 65_536
    elif change == "wrong_boolean":
        req["params"]["isBlocking"] = "false"
    elif change == "missing_turn":
        req["params"].pop("turnId")
    else:
        req["id"] = "x" * 513
    server.queued = [req]
    result = await asyncio.wait_for(codex.CodexBackend().run(ctx), 2)
    assert result["state"] == "interrupted" and not calls
    assert not server.instances[0].responses


async def test_duplicate_identical_rpc_reuses_answer_conflicting_rpc_stops(tmp_path, server):
    ctx, _, _, _, calls, _ = question_context(tmp_path)
    server.queued = [question()]
    run = asyncio.create_task(codex.CodexBackend().run(ctx))
    await until(lambda: server.instances and server.instances[0].responses)
    rpc = server.instances[0]
    await rpc.notifications.put(question())
    await until(lambda: len(rpc.responses) == 2)
    assert len(calls) == 1
    changed = question()
    changed["params"]["questions"][0]["question"] = "Different prompt"
    await rpc.notifications.put(changed)
    assert (await asyncio.wait_for(run, 30))["state"] == "interrupted"
    assert len(calls) == 1


async def test_wrong_thread_question_cannot_reach_service_or_resolve_current_question(tmp_path, server):
    ctx, events, _, _, calls, _ = question_context(tmp_path)
    waiting, cancelled = asyncio.Event(), asyncio.Event()
    async def request(payload):
        calls.append(payload)
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    ctx.request_input = request
    wrong = question()
    wrong["params"]["threadId"] = "other-thread"
    server.queued = [wrong, question()]
    run = asyncio.create_task(codex.CodexBackend().run(ctx))
    await asyncio.wait_for(waiting.wait(), 2)
    rpc = server.instances[0]
    await rpc.notifications.put(event("serverRequest/resolved", requestId=91, threadId="other-thread"))
    # Notifications are handled in order, and a resolution cancels its question before
    # the next one is read: once this later delta is observed, the foreign resolution
    # has been fully handled.
    await rpc.notifications.put(event("item/agentMessage/delta", itemId="marker", delta="Marker"))
    def marker_seen():
        return any(kind == "message.delta" and value.get("item_id") == "marker"
                   for kind, value in events)
    await until(lambda: run.done() or marker_seen())
    assert marker_seen() and not run.done()
    assert len(calls) == 1 and not cancelled.is_set()
    await rpc.notifications.put(terminal("completed"))
    assert (await asyncio.wait_for(run, 30))["state"] == "completed"


async def test_service_failure_is_unknown_not_success_and_no_default_answer(tmp_path, server):
    ctx, _, _, _, _, _ = question_context(tmp_path)
    async def request(_):
        raise KiraError("LIMIT", "Fixture durable limit")
    ctx.request_input = request
    server.queued = [question()]
    assert (await codex.CodexBackend().run(ctx))["state"] == "interrupted"
    assert not server.instances[0].responses


async def test_resume_applies_question_features_without_resetting_thread(tmp_path, server):
    ctx, _, _, _, _, _ = question_context(tmp_path)
    ctx.session["backend_ref"] = "thread-fixture"
    server.queued = [terminal("completed")]
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    requests = server.instances[0].requests
    assert requests[0] == ("thread/goal/get", {"threadId": "thread-fixture"})
    assert requests[1][0] == "thread/resume"
    assert requests[1][1]["threadId"] == "thread-fixture"
    assert "dynamicTools" not in requests[1][1]
    assert not any(method == "thread/start" for method, _ in requests)


async def test_unavailable_native_question_features_fail_before_inference(tmp_path, server):
    ctx, _, _, _, _, _ = question_context(tmp_path)
    server.feature_enabled = False
    assert (await codex.CodexBackend().run(ctx))["state"] == "failed"
    assert not any(method == "turn/start" for method, _ in server.instances[0].requests)


async def test_question_callbacks_that_ignore_cancellation_do_not_hang_provider_close(tmp_path, server):
    ctx, _, _, _, _, _ = question_context(tmp_path)
    waiting, release = asyncio.Event(), asyncio.Event()
    async def request(_):
        waiting.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        return {"ok": True, "status": "closed"}
    ctx.request_input = request
    server.queued = [question()]
    run = asyncio.create_task(codex.CodexBackend().run(ctx))
    await asyncio.wait_for(waiting.wait(), 2)
    await server.instances[0].notifications.put(terminal("completed"))
    try:
        # The provider waits a fixed second for the callback; leave ample headroom.
        result = await asyncio.wait_for(run, 10)
        assert result["state"] == "interrupted"
        assert "one second" in result["error"]
        assert server.instances[0].closed
    finally:
        release.set()
        await asyncio.sleep(0)


def async_message(item_id="call_async_fixture", **changes):
    return event("item/completed", item={"type": "agentMessage", "id": item_id,
        "text": "Choose a fixture color\n- Blue\n- Green", "delivery": "async",
        "questions": [{"title": "Choose a fixture color", "options": ["Blue", "Green"]}], **changes})


async def test_actual_async_message_metadata_stores_question_and_steers_correlated_answer(tmp_path, server):
    ctx, events, _, _, calls, reads = question_context(tmp_path)
    release = asyncio.Event()
    async def request(payload):
        calls.append(payload)
        return {"ok": True, "status": "pending", "request_id": "durable-input"}
    async def read(identity, wait):
        reads.append((identity, wait))
        await release.wait()
        return {"ok": True, "status": "answered", "answers": {"q1": {"answers": ["Violet <3"]}}}
    ctx.request_input, ctx.read_input = request, read
    server.queued = [async_message(), event("item/agentMessage/delta", itemId="work", delta="Working")]
    run = asyncio.create_task(codex.CodexBackend().run(ctx))
    await until(lambda: reads and any(p.get("content") == "Working" for _, p in events))
    assert calls[0]["blocking"] is False
    assert calls[0]["request_id"] == "call_async_fixture"
    assert calls[0]["questions"][0]["isOther"] is True
    rpc = server.instances[0]
    assert not any(m == "turn/steer" for m, _ in rpc.requests)
    release.set()
    await until(lambda: any(k == "input.delivered" for k, _ in events))
    params = next(p for m, p in rpc.requests if m == "turn/steer")
    assert params["threadId"] == "thread-fixture" and params["expectedTurnId"] == "turn-fixture"
    assert params["clientUserMessageId"] == "durable-input"
    content = params["input"][0]["text"]
    answer = json.loads(content.removeprefix("<send_user_message_question_reply>")
                        .removesuffix("</send_user_message_question_reply>"))
    assert answer == [{"answer": "Violet <3", "question": "Choose a fixture color",
        "questionItemId": '["request_user_input_async","call_async_fixture",0]'}]
    await rpc.notifications.put(terminal("completed"))
    assert (await asyncio.wait_for(run, 30))["state"] == "completed"
    assert not rpc.responses


@pytest.mark.parametrize("cause", ["rpc_error", "wrong_turn"])
async def test_async_delivery_failure_remains_visible_and_never_retries(tmp_path, server, cause):
    ctx, events, _, _, _, _ = question_context(tmp_path)
    async def request(_):
        return {"ok": True, "status": "answered", "request_id": "durable-input",
                "answers": {"q1": {"answers": ["Blue"]}}}
    ctx.request_input = request
    if cause == "rpc_error":
        server.steer_error = True
    else:
        server.steer_turn = "other-turn"
    server.queued = [async_message()]
    assert (await codex.CodexBackend().run(ctx))["state"] == "interrupted"
    assert [k for k, _ in events if k.startswith("input.")] == ["input.delivery_failed"]
    assert sum(m == "turn/steer" for m, _ in server.instances[0].requests) == 1


async def test_plain_text_question_does_not_fabricate_structured_request(tmp_path, server):
    ctx, _, _, _, calls, reads = question_context(tmp_path)
    server.queued = [async_message(questions=None), terminal("completed")]
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    assert not calls and not reads


@pytest.mark.parametrize("changes", [{"questions": [{"title": "Fixture", "options": 0}]},
    {"delivery": None}, {"questions": [{"title": "Fixture", "extra": "unsupported"}]}])
async def test_invalid_async_metadata_does_not_reach_service(tmp_path, server, changes):
    ctx, _, _, _, calls, _ = question_context(tmp_path)
    server.queued = [async_message(**changes)]
    assert (await codex.CodexBackend().run(ctx))["state"] == "interrupted"
    assert not calls


async def test_native_rpc_write_failure_marks_saved_answer_delivery_failed(tmp_path, server, monkeypatch):
    ctx, events, _, _, _, _ = question_context(tmp_path)
    async def broken_write(self, request_id, response):
        raise codex.ProtocolError("Fixture pipe closed before reply")
    monkeypatch.setattr(server, "respond", broken_write)
    server.queued = [question()]
    assert (await codex.CodexBackend().run(ctx))["state"] == "interrupted"
    assert [k for k, _ in events if k.startswith("input.")] == ["input.delivery_failed"]
