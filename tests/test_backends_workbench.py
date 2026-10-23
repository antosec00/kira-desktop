"""Synthetic native chat controls; no live provider qualification is implied."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

from kira.backends import codex
from kira.domain.contracts import KiraError, RunContext


def notice(method, **values):
    return {"method": method, "params": {"threadId": "source-thread", **values}}


def completion(status="completed", turn_id="native-turn"):
    return notice("turn/completed", turn={"id": turn_id, "status": status})


def compaction_events():
    return [notice("turn/started", turn={"id": "compact-turn", "status": "inProgress"}),
            notice("item/started", turnId="compact-turn",
                   item={"type": "contextCompaction", "id": "compact-item"}),
            notice("item/completed", turnId="compact-turn",
                   item={"type": "contextCompaction", "id": "compact-item"}),
            completion(turn_id="compact-turn")]


def context(tmp_path, mode="execute", permission="read_only"):
    events = []

    async def emit(kind, value):
        events.append((kind, value))

    async def reject(*_):
        raise AssertionError("Native controls must not execute Kira tools or approvals")

    ctx = RunContext("fixture-run", {"root": str(tmp_path), "permission": permission},
                     {"model": "fixture-model", "settings": {"reasoning_effort": "ultra",
                      "service_tier": "priority"}, "backend_ref": "source-thread"},
                     "Fixture request", "Fixture supplied context", emit, reject, reject,
                     asyncio.Event(), mode=mode)
    return ctx, events


class WorkbenchServer:
    instances = []
    run_events = []
    compact_events = []
    steering_error = None
    steering_turn = "native-turn"
    fork_error = None
    fork_id = "fork-thread"
    model = "fixture-model"
    effort = "ultra"
    planning = True
    thread_status = "notLoaded"
    resumed_status = "idle"
    goal = None

    def __init__(self, _):
        self.notifications = asyncio.Queue()
        self.requests = []
        self.sent = []
        self.closed = False
        self.instances.append(self)

    async def start(self, *, experimental_api=True):
        self.experimental = experimental_api

    async def close(self):
        self.closed = True

    async def send(self, value):
        self.sent.append(copy.deepcopy(value))

    async def request(self, method, params, timeout=30):
        self.requests.append((method, copy.deepcopy(params)))
        if method == "thread/read":
            return {"thread": {"id": "source-thread", "status": {"type": self.thread_status}}}
        if method == "thread/goal/get":
            return {"goal": self.goal}
        if method in {"thread/start", "thread/resume", "thread/fork"}:
            if method == "thread/fork" and self.fork_error:
                raise self.fork_error
            return {"thread": {"id": self.fork_id if method == "thread/fork" else "source-thread",
                               "status": {"type": self.resumed_status}},
                    "model": self.model, "reasoningEffort": self.effort,
                    "sandbox": {"type": {"read-only": "readOnly", "workspace-write":
                                           "workspaceWrite", "danger-full-access":
                                           "dangerFullAccess"}[params["sandbox"]],
                                "networkAccess": False}}
        if method == "collaborationMode/list":
            return {"data": [{"mode": "plan", "model": "unwanted-preset-model",
                              "reasoning_effort": "low"}] if self.planning else []}
        if method == "turn/start":
            for event in self.run_events:
                await self.notifications.put(event)
            return {"turn": {"id": "native-turn", "status": "inProgress"}}
        if method == "turn/steer":
            if self.steering_error:
                raise self.steering_error
            return {"turnId": self.steering_turn}
        if method == "thread/compact/start":
            for event in self.compact_events:
                await self.notifications.put(event)
            return {}
        if method == "turn/interrupt":
            await self.notifications.put(completion("interrupted", params["turnId"]))
            return {}
        raise AssertionError(method)


@pytest.fixture
def server(monkeypatch):
    for key, value in {"instances": [], "run_events": [], "compact_events": [],
                       "steering_error": None, "steering_turn": "native-turn",
                       "fork_error": None, "fork_id": "fork-thread", "model": "fixture-model",
                       "effort": "ultra", "planning": True, "thread_status": "notLoaded",
                       "resumed_status": "idle", "goal": None}.items():
        monkeypatch.setattr(WorkbenchServer, key, value)
    monkeypatch.setattr(codex, "AppServer", WorkbenchServer)
    return WorkbenchServer


async def active_run(ctx, server):
    registered = []
    ready = asyncio.Event()

    def register(handler):
        registered.append(handler)
        if handler is not None:
            ready.set()

    ctx.register_control = register
    task = asyncio.create_task(codex.CodexBackend().run(ctx))
    await asyncio.wait_for(ready.wait(), 2)
    return task, registered, server.instances[-1]


async def finish_run(task, rpc):
    await rpc.notifications.put(completion())
    result = await asyncio.wait_for(task, 2)
    assert result["state"] == "completed"
    return result


async def wait_for_request(task, server, method):
    """Wait, bounded, until the newest server received ``method`` while the task runs."""
    async with asyncio.timeout(10):
        while not server.instances or not any(name == method
                                              for name, _ in server.instances[-1].requests):
            if task.done():
                outcome = task.exception() or task.result()
                pytest.fail(f"The task ended before {method}: {outcome!r}")
            await asyncio.sleep(0)


async def test_steering_targets_only_registered_turn_and_deduplicates(tmp_path, server):
    ctx, _ = context(tmp_path)
    task, handlers, rpc = await active_run(ctx, server)
    handler = handlers[0]
    values = {"request_id": "steer-fixture", "prompt": "Focus on this fixture"}
    first, second = await asyncio.gather(handler("steer", values), handler("steer", values))
    assert first == second == {"accepted": True, "thread_id": "source-thread",
                               "turn_id": "native-turn", "request_id": "steer-fixture"}
    requests = [value for method, value in rpc.requests if method == "turn/steer"]
    assert requests == [{"threadId": "source-thread", "expectedTurnId": "native-turn",
                         "clientUserMessageId": "steer-fixture",
                         "input": [{"type": "text", "text": "Focus on this fixture"}]}]
    with pytest.raises(KiraError, match="different content"):
        await handler("steer", {**values, "prompt": "Different fixture"})
    result = await finish_run(task, rpc)
    assert result["outcome"]["turn_id"] == "native-turn"
    assert handlers[-1] is None and rpc.closed
    with pytest.raises(KiraError) as inactive:
        await handler("steer", {**values, "request_id": "after-end"})
    assert inactive.value.code == "NOT_ACTIVE"


@pytest.mark.parametrize("failure,expected", [(TimeoutError(), "DELIVERY_UNKNOWN"),
                         (codex.ProtocolError("Fixture disconnection"), "DELIVERY_UNKNOWN"),
                         (codex.RpcError("Fixture no active turn"), "PROVIDER_REJECTED")])
async def test_steering_uncertainty_and_rejection_are_never_resent(tmp_path, server, failure, expected):
    server.steering_error = failure
    ctx, _ = context(tmp_path)
    task, handlers, rpc = await active_run(ctx, server)
    for _ in range(2):
        with pytest.raises(KiraError) as error:
            await handlers[0]("steer", {"request_id": "same-request", "prompt": "Fixture"})
        assert error.value.code == expected
    assert sum(method == "turn/steer" for method, _ in rpc.requests) == 1
    await finish_run(task, rpc)


async def test_wrong_steering_acknowledgement_is_not_delivery_evidence(tmp_path, server):
    server.steering_turn = "another-turn"
    ctx, _ = context(tmp_path)
    task, handlers, rpc = await active_run(ctx, server)
    with pytest.raises(KiraError) as error:
        await handlers[0]("steer", {"request_id": "fixture-request", "prompt": "Fixture"})
    assert error.value.code == "DELIVERY_UNKNOWN"
    await finish_run(task, rpc)


async def test_cancelling_a_steering_wait_does_not_allow_resend(tmp_path, server, monkeypatch):
    ctx, _ = context(tmp_path)
    task, handlers, rpc = await active_run(ctx, server)
    native_request = rpc.request
    started = asyncio.Event()

    async def request(method, params, timeout=30):
        if method == "turn/steer":
            rpc.requests.append((method, params))
            started.set()
            await asyncio.Event().wait()
        return await native_request(method, params, timeout)

    monkeypatch.setattr(rpc, "request", request)
    values = {"request_id": "fixture-cancelled-request", "prompt": "Fixture"}
    waiting = asyncio.create_task(handlers[0]("steer", values))
    await asyncio.wait_for(started.wait(), 2)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    with pytest.raises(KiraError) as error:
        await handlers[0]("steer", values)
    assert error.value.code == "DELIVERY_UNKNOWN"
    assert sum(method == "turn/steer" for method, _ in rpc.requests) == 1
    await finish_run(task, rpc)


@pytest.mark.parametrize("values", [{"request_id": "fixture", "prompt": ""},
    {"request_id": "fixture", "prompt": "x" * 65_537},
    {"request_id": "fixture", "prompt": "Fixture", "model": "other"},
    {"request_id": "", "prompt": "Fixture"}])
async def test_invalid_steering_never_reaches_provider(tmp_path, server, values):
    ctx, _ = context(tmp_path)
    task, handlers, rpc = await active_run(ctx, server)
    with pytest.raises(KiraError) as error:
        await handlers[0]("steer", values)
    assert error.value.code == "INVALID_PARAMS"
    assert not any(method == "turn/steer" for method, _ in rpc.requests)
    await finish_run(task, rpc)


async def test_control_is_unregistered_on_provider_task_cancellation(tmp_path, server):
    ctx, _ = context(tmp_path)
    task, handlers, rpc = await active_run(ctx, server)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handlers[-1] is None and rpc.closed


async def test_plan_uses_native_mode_without_preset_model_or_effort(tmp_path, server):
    server.run_events = [completion()]
    ctx, _ = context(tmp_path, "plan")
    before = copy.deepcopy(ctx.session)
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    rpc = server.instances[-1]
    params = dict(rpc.requests)["turn/start"]
    assert rpc.experimental
    assert params["collaborationMode"] == {"mode": "plan", "settings": {
        "model": "fixture-model", "reasoning_effort": "ultra", "developer_instructions": None}}
    assert params["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert params["effort"] == "ultra" and params["serviceTier"] == "priority"
    assert ctx.session == before


async def test_unknown_native_plan_mode_fails_before_inference(tmp_path, server):
    server.planning = False
    ctx, _ = context(tmp_path, "plan")
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed" and "planning mode" in result["error"]
    assert not any(method == "turn/start" for method, _ in server.instances[-1].requests)


@pytest.mark.parametrize("mode", ["execute", "review", "recap"])
async def test_ordinary_modes_clear_sticky_native_plan_and_preserve_provider_default_effort(
        tmp_path, server, mode):
    server.run_events = [completion()]
    ctx, _ = context(tmp_path, mode)
    del ctx.session["settings"]["reasoning_effort"]
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    params = dict(server.instances[-1].requests)["turn/start"]
    assert "effort" not in params
    assert params["collaborationMode"]["mode"] == "default"
    assert params["collaborationMode"]["settings"]["reasoning_effort"] == "ultra"


@pytest.mark.parametrize("mode", ["plan", "review", "recap"])
async def test_nonexecute_mode_rejects_write_permission_before_provider_start(tmp_path, server, mode):
    ctx, _ = context(tmp_path, mode, "trusted_host")
    assert (await codex.CodexBackend().run(ctx))["state"] == "failed"
    assert not server.instances[-1].requests and server.instances[-1].closed


async def test_fork_preserves_source_and_exact_boundary_without_starting_a_turn(tmp_path, server):
    ctx, _ = context(tmp_path)
    before = copy.deepcopy(ctx.session)
    result = await codex.CodexBackend().manage_thread("fork", ctx.session, ctx.project,
                                                   {"last_turn_id": "chosen-turn"})
    assert result == {"backend_ref": "fork-thread", "source_backend_ref": "source-thread",
                      "model": "fixture-model", "reasoning_effort": "ultra",
                      "last_turn_id": "chosen-turn"}
    rpc = server.instances[-1]
    methods = [method for method, _ in rpc.requests]
    assert methods == ["thread/read", "thread/fork"]
    params = dict(rpc.requests)["thread/fork"]
    assert params["lastTurnId"] == "chosen-turn" and params["deferGoalContinuation"] is True
    assert params["model"] == "fixture-model" and params["config"]["model_reasoning_effort"] == "ultra"
    assert params["serviceTier"] == "priority" and params["sandbox"] == "read-only"
    assert ctx.session == before and rpc.closed


@pytest.mark.parametrize("key,value", [("fork_id", "source-thread"), ("model", "wrong-model"),
                                      ("effort", "low")])
async def test_unconfirmed_fork_never_changes_source_or_retries(tmp_path, server, key, value):
    setattr(server, key, value)
    ctx, _ = context(tmp_path)
    with pytest.raises(KiraError) as error:
        await codex.CodexBackend().manage_thread("fork", ctx.session, ctx.project, {})
    assert error.value.code == "DELIVERY_UNKNOWN"
    rpc = server.instances[-1]
    assert sum(method == "thread/fork" for method, _ in rpc.requests) == 1
    assert ctx.session["backend_ref"] == "source-thread" and rpc.closed


async def test_explicit_fork_rejection_is_not_unknown_delivery(tmp_path, server):
    server.fork_error = codex.RpcError("Fixture boundary not found")
    ctx, _ = context(tmp_path)
    with pytest.raises(KiraError) as error:
        await codex.CodexBackend().manage_thread("fork", ctx.session, ctx.project,
                                              {"last_turn_id": "missing-turn"})
    assert error.value.code == "PROVIDER_REJECTED"
    assert server.instances[-1].closed


async def test_compact_requires_item_lifecycle_and_matching_terminal(tmp_path, server):
    ctx, _ = context(tmp_path)
    task = asyncio.create_task(codex.CodexBackend().manage_thread(
        "compact", ctx.session, ctx.project, {}))
    await wait_for_request(task, server, "thread/compact/start")
    rpc = server.instances[-1]
    assert not task.done(), "The immediate compact acknowledgement is not completion"
    await rpc.notifications.put({"method": "turn/completed", "params": {
        "threadId": "unrelated-thread", "turn": {"id": "unrelated-turn", "status": "completed"}}})
    for event in compaction_events():
        await rpc.notifications.put(event)
    result = await asyncio.wait_for(task, 2)
    assert result == {"backend_ref": "source-thread", "turn_id": "compact-turn", "compacted": True}
    assert [method for method, _ in rpc.requests] == ["thread/read", "thread/goal/get",
                                                    "thread/resume", "thread/compact/start"]
    assert rpc.closed and ctx.session["backend_ref"] == "source-thread"


@pytest.mark.parametrize("events", [[completion(turn_id="compact-turn")],
    [compaction_events()[0], compaction_events()[-1]],
    [compaction_events()[0], compaction_events()[2]],
    [*compaction_events()[:-1], completion("failed", "compact-turn")],
    [compaction_events()[0], notice("item/started", turnId="compact-turn",
                                  item={"id": "unexpected", "type": "commandExecution"})]])
async def test_compaction_missing_or_unexpected_evidence_stays_unknown(tmp_path, server, events):
    server.compact_events = events
    ctx, _ = context(tmp_path)
    with pytest.raises(KiraError) as error:
        await codex.CodexBackend().manage_thread("compact", ctx.session, ctx.project, {})
    assert error.value.code == "DELIVERY_UNKNOWN"
    rpc = server.instances[-1]
    assert sum(method == "thread/compact/start" for method, _ in rpc.requests) == 1
    assert not any(method == "turn/start" for method, _ in rpc.requests) and rpc.closed


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_compaction_timeout_or_cancel_interrupts_only_owned_turn_and_closes(
        tmp_path, server, mode):
    server.compact_events = [compaction_events()[0]]
    ctx, _ = context(tmp_path)
    task = asyncio.create_task(codex.CodexBackend().manage_thread(
        "compact", ctx.session, ctx.project, {"timeout_seconds": 0.05 if mode == "timeout" else 10}))
    await wait_for_request(task, server, "thread/compact/start")
    if mode == "cancel":
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    else:
        with pytest.raises(KiraError) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.code == "DELIVERY_UNKNOWN"
    rpc = server.instances[-1]
    assert dict(rpc.requests)["turn/interrupt"] == {"threadId": "source-thread",
                                                    "turnId": "compact-turn"}
    assert rpc.closed


@pytest.mark.parametrize("status", ["active", "systemError", "unexpected"])
async def test_thread_management_refuses_unverified_idle_state(tmp_path, server, status):
    server.thread_status = status
    ctx, _ = context(tmp_path)
    with pytest.raises(KiraError) as error:
        await codex.CodexBackend().manage_thread("fork", ctx.session, ctx.project, {})
    assert error.value.code == "BUSY"
    assert [method for method, _ in server.instances[-1].requests] == ["thread/read"]
    assert server.instances[-1].closed


async def test_compaction_does_not_resume_an_unfinished_native_goal(tmp_path, server):
    server.goal = {"status": "active", "objective": "Fixture goal"}
    ctx, _ = context(tmp_path)
    with pytest.raises(KiraError) as error:
        await codex.CodexBackend().manage_thread("compact", ctx.session, ctx.project, {})
    assert error.value.code == "BUSY"
    assert [method for method, _ in server.instances[-1].requests] == ["thread/read", "thread/goal/get"]
    assert server.instances[-1].closed


def test_selected_native_contracts_are_unchanged_generated_artifacts():
    root = Path(__file__).resolve().parents[1] / "protocol-schema/codex-app-server-0.162.1"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    fork = json.loads((root / "v2/ThreadForkParams.json").read_text())
    assert {"lastTurnId", "deferGoalContinuation"} <= fork["properties"].keys()
    steer = json.loads((root / "v2/TurnSteerParams.json").read_text())
    assert "expectedTurnId" in steer["required"] and "clientUserMessageId" in steer["properties"]
    turn = json.loads((root / "v2/TurnStartParams.json").read_text())
    assert turn["definitions"]["ModeKind"]["enum"] == ["plan", "default"]


@pytest.mark.parametrize("operation", ["plan", "fork", "compact"])
async def test_native_network_permission_mismatch_is_rejected(tmp_path, server, monkeypatch, operation):
    original = WorkbenchServer.request

    async def request(self, method, params, timeout=30):
        result = await original(self, method, params, timeout)
        if method in {"thread/start", "thread/resume", "thread/fork"}:
            result["sandbox"]["networkAccess"] = True
        return result

    monkeypatch.setattr(WorkbenchServer, "request", request)
    ctx, _ = context(tmp_path, "plan" if operation == "plan" else "execute")
    backend = codex.CodexBackend()
    if operation == "plan":
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "network access" in result["error"]
    else:
        with pytest.raises(KiraError, match="network access"):
            await backend.manage_thread(operation, ctx.session, ctx.project, {})
    rpc = server.instances[-1]
    assert not any(method in {"turn/start", "thread/compact/start"} for method, _ in rpc.requests)
    assert rpc.closed


async def test_native_fork_must_be_idle_after_deferred_goal_copy(tmp_path, server):
    server.resumed_status = "active"
    ctx, _ = context(tmp_path)
    with pytest.raises(KiraError) as error:
        await codex.CodexBackend().manage_thread("fork", ctx.session, ctx.project, {})
    assert error.value.code == "DELIVERY_UNKNOWN"
    assert server.instances[-1].closed


async def test_compaction_item_without_exact_turn_id_cannot_confirm_success(tmp_path, server):
    events = compaction_events()
    del events[1]["params"]["turnId"]
    server.compact_events = events
    ctx, _ = context(tmp_path)
    with pytest.raises(KiraError) as error:
        await codex.CodexBackend().manage_thread("compact", ctx.session, ctx.project, {})
    assert error.value.code == "DELIVERY_UNKNOWN"
    assert server.instances[-1].closed


@pytest.mark.parametrize("agent,expected", [("kira_desktop/0.162.1 (Fixture)", True),
    ("kira_desktop/0.163.0 (Fixture)", True), ("kira_desktop/0.161.0 (Fixture)", False),
    ("unknown", False)])
async def test_discovery_native_support_is_version_gated_and_not_access_evidence(
        server, monkeypatch, agent, expected):
    rpc = WorkbenchServer("fixture")
    rpc.initialize_result = {"userAgent": agent}

    async def request(method, params, timeout=30):
        if method == "account/read":
            return {"account": None}
        if method == "model/list":
            return {"data": [{"model": "fixture-model", "displayName": "Fixture model"}],
                    "nextCursor": None}
        raise AssertionError(method)

    monkeypatch.setattr(rpc, "request", request)
    backend = codex.CodexBackend()
    backend._control = rpc
    try:
        catalog = await backend.discover()
        assert catalog["available"]
        for key in ("steering", "planning", "native_fork", "native_compaction"):
            assert catalog["capabilities"][key] is expected
            assert catalog["models"][0]["capabilities"][key] is expected
        assert catalog["models"][0]["access_verified"] is False
    finally:
        await backend.close()


async def test_public_native_plan_stream_uses_authoritative_completed_text_and_ignores_reasoning(
        tmp_path, server):
    ctx, events = context(tmp_path, "plan")
    final = "## Proposed implementation\n\n1. Read `input.json`.\n2. Create the requested output after approval."
    plan = {"id": "proposed-plan", "type": "plan", "text": final}
    server.run_events = [
        notice("item/completed", turnId="native-turn", item={
            "id": "intro", "type": "agentMessage", "text": "I will prepare the plan."}),
        notice("item/reasoning/textDelta", turnId="native-turn", itemId="hidden",
               delta="Synthetic reasoning sentinel"),
        notice("item/completed", turnId="native-turn", item={
            "id": "hidden", "type": "reasoning", "text": "Synthetic reasoning sentinel"}),
        notice("item/started", turnId="native-turn", item={**plan, "text": ""}),
        notice("item/plan/delta", turnId="native-turn", itemId="proposed-plan",
               delta="Provisional plan text that will be revised."),
        notice("item/completed", turnId="native-turn", item=plan),
        notice("item/completed", turnId="native-turn", item=plan),
        completion(),
    ]
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed"
    delivered = [(kind, payload) for kind, payload in events
                 if payload.get("item_id") == "proposed-plan"]
    assert delivered == [
        ("message.delta", {"content": "Provisional plan text that will be revised.",
                           "item_id": "proposed-plan", "source": "codex.plan"}),
        ("message.completed", {"content": final, "item_id": "proposed-plan",
                               "source": "codex.plan", "phase": "final_answer"}),
    ]
    assert "Synthetic reasoning sentinel" not in json.dumps(events)
    assert not any(kind.startswith("tool.") for kind, _ in events)


async def test_native_plan_without_stream_still_delivers_visible_markdown(tmp_path, server):
    ctx, events = context(tmp_path, "plan")
    final = "# Fixture plan\n\n- Preserve `value`.\n- Verify the requested result."
    server.run_events = [notice("item/completed", turnId="native-turn", item={
        "id": "final-plan", "type": "plan", "text": final}), completion()]
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    visible = [(kind, payload) for kind, payload in events if kind.startswith("message.")]
    assert [kind for kind, _ in visible] == ["message.delta", "message.completed"]
    assert all(payload["content"] == final and payload["item_id"] == "final-plan"
               for _, payload in visible)
    assert visible[-1][1]["source"] == "codex.plan"


async def test_interrupted_native_plan_stream_is_not_falsely_marked_completed(tmp_path, server):
    ctx, events = context(tmp_path, "plan")
    server.run_events = [notice("item/plan/delta", turnId="native-turn", itemId="partial-plan",
                                delta="1. Inspect the fixture."), completion("interrupted")]
    assert (await codex.CodexBackend().run(ctx))["state"] == "interrupted"
    assert any(kind == "message.delta" and payload["source"] == "codex.plan"
               for kind, payload in events)
    assert not any(kind == "message.completed" for kind, _ in events)


async def test_structured_native_plan_final_text_is_available_to_caller(tmp_path, server):
    ctx, events = context(tmp_path, "plan")
    ctx.output_schema = {"type": "object", "properties": {"steps": {"type": "array"}}}
    final = '{"steps":["Inspect the fixture"]}'
    server.run_events = [notice("item/completed", turnId="native-turn", item={
        "id": "structured-plan", "type": "plan", "text": final}), completion()]
    result = await codex.CodexBackend().run(ctx)
    assert result["outcome"]["final_text"] == final
    assert any(kind == "message.completed" and payload["content"] == final
               for kind, payload in events)
