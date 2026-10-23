"""Durable safe-boundary delivery with synthetic providers and blocked tools."""

import asyncio
import copy
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from kira.backends.ollama import OllamaBackend
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.contracts import KiraError, RunContext
from kira.domain.paths import Paths
from kira.service.portable_history import portable_history
from kira.service.server import Service
from kira.service.storage import encode
from test_backends_openai_chatgpt import call, message, terminal, wire
from test_backends_ollama import ChunkStream, install_transport


class Auth:
    async def access_token(self, account):
        assert account == "synthetic-account"
        return "synthetic-token"

    async def status(self):
        return {"active_account_id": "synthetic-account"}


def no_provider(*_args):
    # Tests build their synthetic adapters directly; the service must never
    # fall back to a real provider (local HTTP or a subprocess).
    raise AssertionError("this test must not instantiate a provider")


@pytest_asyncio.fixture
async def inbox(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-guidance-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=no_provider)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic guidance", "root": str(tmp_path),
            "permission": "trusted_host"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "ollama",
            "model": "synthetic-model", "settings": {"tools": True, "reasoning_effort": "xhigh"}}))["session"]
        run, _ = service.store.create_run(session["id"], "synthetic-run-request", "Read the fixture once")
        await service.emit(run["id"], "run.state", {"state": "running"})
        try:
            yield service, project, session, run
        finally:
            service.turn_controls.finished(run["id"], "Synthetic fixture closed")
            await service.close()


def args(run, identity="guidance-1", prompt="Use the newer synthetic instruction"):
    return {"run_id": run["id"], "request_id": identity, "prompt": prompt}


def records(service, run, kind):
    return [event["payload"] for event in service.store.events(run["id"])["events"] if event["type"] == kind]


async def test_durable_receipts_deduplicate_and_distinguish_acceptance_from_understanding(inbox):
    service, _, session, run = inbox
    boundary = service.turn_controls.bind_run(run["id"], session)
    assert boundary is not None
    params = args(run)
    first = await service.dispatch("run.steer", params)
    assert first["status"] == "pending" and first["delivery_state"] == "queued" and not first["accepted"]
    assert first["binding"]["model"] == session["model"]
    assert first["binding"]["reasoning_effort"] == "xhigh"
    assert await service.dispatch("run.steer", params) == first
    assert len(records(service, run, "steering.requested")) == 1
    assert not [row for row in records(service, run, "message.user") if row.get("source") == "steer"]
    with pytest.raises(KiraError, match="different"):
        await service.dispatch("run.steer", {**params, "prompt": "Different instruction"})
    assert await boundary("pending", {}) == {"pending": True}
    batch = await boundary("take", {})
    assert batch["messages"] == [{"request_id": params["request_id"], "content": params["prompt"]}]
    claimed = await service.dispatch("run.steer", params)
    assert claimed["delivery_state"] == "submitted" and not claimed["accepted"]
    with pytest.raises(KiraError, match="Previous guidance"):
        await boundary("take", {})
    with pytest.raises(KiraError, match="not owned"):
        await boundary("accepted", {"delivery_id": "foreign-delivery"})
    await boundary("accepted", {"delivery_id": batch["delivery_id"]})
    await boundary("accepted", {"delivery_id": batch["delivery_id"]})
    accepted = await service.dispatch("run.steer", params)
    assert accepted["status"] == "accepted" and accepted["delivery_state"] == "provider_accepted"
    assert accepted["accepted"] and accepted["model_consumption_confirmed"] is False
    assert len(records(service, run, "steering.accepted")) == 1
    assert len([row for row in records(service, run, "message.user") if row.get("source") == "steer"]) == 1
    assert sum(row.get("content") == params["prompt"] for row in service.store.history(session["id"])) == 1
    history, _ = portable_history(service.store, session["id"], "future-run")
    assert sum(row.get("content") == params["prompt"] for row in history) == 1
    assert await boundary("seal", {}) == {"sealed": True}
    # A sealed inbox no longer admits guidance; the client is told to queue it.
    with pytest.raises(KiraError) as late:
        await service.dispatch("run.steer", args(run, "too-late"))
    assert late.value.code == "UNAVAILABLE"
    with pytest.raises(KiraError, match="no longer"):
        await boundary("pending", {})


@pytest.mark.parametrize("submitted", [False, True])
@pytest.mark.parametrize("ending", ["cancel", "restart"])
async def test_unfinished_delivery_stays_durable_and_never_replays(inbox, submitted, ending):
    service, _, session, run = inbox
    boundary = service.turn_controls.bind_run(run["id"], session)
    params = args(run)
    await service.dispatch("run.steer", params)
    batch = await boundary("take", {}) if submitted else None
    if ending == "restart":
        service.turn_controls.recover()
    else:
        await service.emit(run["id"], "run.state", {"state": "cancelling"})
        service.turn_controls.finished(run["id"], "Synthetic cancellation")
    recovered = await service.dispatch("run.steer", params)
    assert recovered["status"] == ("unknown" if submitted else "rejected")
    assert recovered["delivery_state"] == ("unknown" if submitted else "not_delivered")
    assert not recovered["accepted"] and not recovered["automatic_replay"]
    assert records(service, run, "steering.failed")[-1]["delivery_state"] == recovered["delivery_state"]
    assert not records(service, run, "steering.accepted")
    if batch:
        with pytest.raises(KiraError) as late:
            await boundary("accepted", {"delivery_id": batch["delivery_id"]})
        assert late.value.code == "NOT_ACTIVE"
    assert await service.dispatch("run.steer", params) == recovered


@pytest.mark.parametrize("changed", ["model", "account", "effort", "permission", "generation"])
async def test_binding_changes_close_old_callback_without_sending_guidance(inbox, changed):
    service, project, session, run = inbox
    boundary = service.turn_controls.bind_run(run["id"], session)
    await service.dispatch("run.steer", args(run))
    if changed == "model":
        service.store.db.execute("UPDATE sessions SET model='other-model' WHERE id=?", (session["id"],))
    elif changed in {"account", "effort"}:
        key = "openai_account_id" if changed == "account" else "reasoning_effort"
        service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?",
                                 (encode({**session["settings"], key: "different"}), session["id"]))
    elif changed == "permission":
        service.store.db.execute("UPDATE projects SET permission='read_only' WHERE id=?", (project["id"],))
    else:
        # Create a valid different generation before switching the binding.
        service.store.db.execute("INSERT INTO session_provider_generations "
            "SELECT session_id,generation+1,backend,model,settings,backend_ref,created_at,retired_at "
            "FROM session_provider_generations WHERE session_id=?", (session["id"],))
        service.store.db.execute("UPDATE run_provider_bindings SET generation=generation+1 WHERE run_id=?", (run["id"],))
    with pytest.raises(KiraError, match="binding"):
        await boundary("take", {})
    saved = await service.dispatch("run.steer", args(run))
    assert saved["delivery_state"] == "not_delivered" and not saved["accepted"]
    assert not records(service, run, "steering.submitted")


async def test_boundary_batch_limits_and_finish_admission_are_serialized(inbox):
    service, _, session, run = inbox
    boundary = service.turn_controls.bind_run(run["id"], session)
    for index in range(35):
        await service.dispatch("run.steer", args(run, f"guide-{index:02}", f"Synthetic guidance {index}"))
    assert await boundary("seal", {}) == {"sealed": False}
    batch = await boundary("take", {})
    assert len(batch["messages"]) == 32
    assert [row["request_id"] for row in batch["messages"]] == [f"guide-{index:02}" for index in range(32)]
    await boundary("accepted", {"delivery_id": batch["delivery_id"]})
    remaining = await boundary("take", {})
    assert len(remaining["messages"]) == 3
    await boundary("accepted", {"delivery_id": remaining["delivery_id"]})
    # New admission just before sealing cannot be silently dropped.
    await service.dispatch("run.steer", args(run, "last"))
    assert await boundary("seal", {}) == {"sealed": False}
    last = await boundary("take", {})
    await boundary("accepted", {"delivery_id": last["delivery_id"]})
    assert await boundary("seal", {}) == {"sealed": True}
    assert len(records(service, run, "steering.accepted")) == 36


async def test_claim_failure_preserves_message_and_does_not_forge_submission(inbox):
    service, _, session, run = inbox
    boundary = service.turn_controls.bind_run(run["id"], session)
    await service.dispatch("run.steer", args(run))
    service.store.db.execute("CREATE TRIGGER fail_guidance BEFORE INSERT ON events "
        "WHEN NEW.type='steering.submitted' BEGIN SELECT RAISE(ABORT,'synthetic commit failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        await boundary("take", {})
    assert (await service.dispatch("run.steer", args(run)))["delivery_state"] == "queued"
    assert not [row for row in records(service, run, "message.user") if row.get("source") == "steer"]
    service.store.db.execute("DROP TRIGGER fail_guidance")
    service.turn_controls.finished(run["id"], "Synthetic preparation failed")
    assert (await service.dispatch("run.steer", args(run)))["delivery_state"] == "not_delivered"


async def test_guidance_fifo_byte_bound_and_admission_limit_keep_duplicate_identity(inbox, monkeypatch):
    service, _, session, run = inbox
    boundary = service.turn_controls.bind_run(run["id"], session)
    monkeypatch.setattr("kira.service.conversation_controls.MAX_GUIDANCE_REQUESTS", 9)
    # Opaque identities do not define order; neither do wall-clock ties.
    identities = [f"guide-{index}" for index in reversed(range(9))]
    for identity in identities:
        await service.dispatch("run.steer", args(run, identity, "x" * 32_000))
    with pytest.raises(KiraError) as error:
        await service.dispatch("run.steer", args(run, "over-limit"))
    assert error.value.code == "LIMIT"
    assert (await service.dispatch("run.steer", args(run, identities[0], "x" * 32_000)))["delivery_state"] == "queued"
    batch = await boundary("take", {})
    assert [row["request_id"] for row in batch["messages"]] == identities[:8]
    assert len(encode(batch["messages"]).encode()) <= 256 * 1024
    await boundary("accepted", {"delivery_id": batch["delivery_id"]})
    assert (await boundary("take", {}))["messages"][0]["request_id"] == identities[8]


@pytest.mark.parametrize("reply", ["accepted", "rejected"])
async def test_late_native_acceptance_cannot_override_finished_unknown_receipt(inbox, reply):
    service, _, _, run = inbox
    entered, release = asyncio.Event(), asyncio.Event()

    async def native(action, values):
        entered.set()
        await release.wait()
        if reply == "rejected":
            raise KiraError("PROVIDER_REJECTED", "Synthetic late rejection")
        return {"accepted": True, "turn_id": "synthetic-native-turn"}

    service._run_controls[run["id"]] = native
    pending = asyncio.create_task(service.dispatch("run.steer", args(run)))
    await asyncio.wait_for(entered.wait(), 30)
    service.turn_controls.finished(run["id"], "Synthetic turn ended")
    release.set()
    result = await asyncio.wait_for(pending, 30)
    assert result["status"] == "unknown" and result["delivery_state"] == "unknown"
    assert not result["accepted"] and not records(service, run, "steering.accepted")


async def make_adapter(service, session, run, provider, handler, monkeypatch):
    session = copy.deepcopy(session)
    session["backend"] = "openai_chatgpt" if provider.startswith("direct") else "ollama"
    if provider.startswith("direct"):
        session["settings"]["openai_account_id"] = "synthetic-account"
    service.store.db.execute("UPDATE sessions SET backend=?,settings=? WHERE id=?",
                             (session["backend"], encode(session["settings"]), session["id"]))
    if provider.startswith("direct"):
        backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    else:
        backend = OllamaBackend()
        await install_transport(backend, handler)

        async def show(model):
            return {"capabilities": ["tools"]}

        async def probe(model, cancel):
            return True

        monkeypatch.setattr(backend, "_show", show)
        monkeypatch.setattr(backend, "_probe_tools", probe)
    return backend, session, service.turn_controls.bind_run(run["id"], session)


def response(provider, *, tools=False, text="Synthetic final response"):
    if provider.startswith("direct"):
        return wire([terminal([call(args={"path": "first.txt"}, call_id="first"),
                               call(args={"path": "second.txt"}, call_id="second")]
                              if tools else [message(text)])])
    calls = [{"function": {"name": "read_file", "arguments": {"path": path}}}
             for path in ("first.txt", "second.txt")]
    return httpx.Response(200, stream=ChunkStream([{"done": True, "message": {
        "role": "assistant", **({"tool_calls": calls} if tools else {"content": text})}}]))


@pytest.mark.parametrize("provider", ["direct", "direct_context", "ollama"])
async def test_guidance_during_tool_waits_then_is_sent_once_and_defers_unstarted_calls(inbox, monkeypatch, provider):
    service, project, session, run = inbox
    bodies, effects, saves = [], [], []
    tool_entered, tool_release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        bodies.append(json.loads(request.content))
        return response(provider, tools=len(bodies) == 1)

    backend, session, boundary = await make_adapter(service, session, run, provider, handler, monkeypatch)

    async def execute(name, arguments):
        effects.append((name, copy.deepcopy(arguments)))
        tool_entered.set()
        await tool_release.wait()
        effects.append("completed-once")
        return {"ok": True, "content": "Synthetic original tool result"}

    async def approve(values):
        raise AssertionError("No permission changes")

    async def save(value):
        snapshot = copy.deepcopy(value)
        snapshot["revision"] = len(saves) + 1
        saves.append(snapshot)
        return snapshot

    ctx = RunContext(run["id"], project, session, run["prompt"], "", lambda kind, data: service.emit(run["id"], kind, data),
        execute, approve, asyncio.Event(), guidance=boundary,
        context_save=save if provider == "direct_context" else None,
        context_catalog={"context_window": 100_000, "context_features": {
            "input_tokens": False, "server_compaction": False}})
    original = copy.deepcopy((ctx.project, ctx.session))
    task = asyncio.create_task(backend.run(ctx))
    try:
        await asyncio.wait_for(tool_entered.wait(), 3)
        params = args(run, prompt="Cancel remaining old steps and inspect the newer fixture.")
        first = await service.dispatch("run.steer", params)
        assert first["delivery_state"] == "queued"
        assert await service.dispatch("run.steer", params) == first
        await asyncio.sleep(.01)
        assert effects == [("read_file", {"path": "first.txt"})] and not task.done()
        tool_release.set()
        outcome = await asyncio.wait_for(task, 3)
        assert outcome["state"] == "completed" and len(bodies) == 2
        assert effects == [("read_file", {"path": "first.txt"}), "completed-once"]
        messages = bodies[1]["input" if provider.startswith("direct") else "messages"]
        assert sum(item.get("role") == "user" and item.get("content") == params["prompt"] for item in messages) == 1
        if provider.startswith("direct"):
            outputs = [item for item in messages if item.get("type") == "function_call_output"]
            assert {item["call_id"] for item in outputs} == {"first", "second"}
            deferred = json.loads(next(item["output"] for item in outputs if item["call_id"] == "second"))
        else:
            outputs = [item for item in messages if item.get("role") == "tool"]
            assert len(outputs) == 2
            deferred = json.loads(outputs[-1]["content"])
        assert deferred["error"]["code"] == "DEFERRED_FOR_USER_GUIDANCE" and deferred["executed"] is False
        receipt = await service.dispatch("run.steer", params)
        assert receipt["delivery_state"] == "provider_accepted" and not receipt["model_consumption_confirmed"]
        assert len(records(service, run, "steering.requested")) == len(records(service, run, "steering.accepted")) == 1
        assert len([row for row in records(service, run, "message.user") if row.get("source") == "steer"]) == 1
        assert (ctx.project, ctx.session) == original
        assert all(body["model"] == "synthetic-model" for body in bodies)
        if provider.startswith("direct"):
            assert all(body["reasoning"] == {"effort": "xhigh", "summary": "auto"} for body in bodies)
        if saves:
            assert saves[-1]["boundary"] == "response_complete"
            assert sum(item.get("content") == params["prompt"] for item in saves[-1]["items"]) == 1

            async def followup_emit(kind, data):
                pass

            following = replace(ctx, run_id="synthetic-followup", prompt="Continue from the saved checkpoint",
                                context_state=copy.deepcopy(saves[-1]), emit=followup_emit, guidance=None)
            assert (await backend.run(following))["state"] == "completed"
            assert len(bodies) == 3
            assert sum(item.get("content") == params["prompt"] for item in bodies[-1]["input"]) == 1
            assert effects == [("read_file", {"path": "first.txt"}), "completed-once"]
    finally:
        tool_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await backend.close()


@pytest.mark.parametrize("consume", [True, False])
async def test_real_service_opens_guidance_during_preparation_and_settles_before_terminal(tmp_path, monkeypatch, consume):
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []

    class Provider:
        async def discover(self):
            return {"available": True, "models": [], "capabilities": {"steering": True}}

        async def run(self, ctx):
            observed.append(ctx)
            assert ctx.guidance is not None
            if consume:
                batch = await ctx.guidance("take", {})
                assert len(batch["messages"]) == 1
                await ctx.guidance("accepted", {"delivery_id": batch["delivery_id"]})
            # Deliberately do not seal: the service must close admission even
            # for an adapter which exits without consuming its saved inbox.
            return {"state": "completed"}

        async def close(self):
            pass

    with tempfile.TemporaryDirectory(prefix="kira-guidance-service-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: Provider())
        await service.start()
        original_emit = service.emit

        async def hold_context(run_id, kind, payload):
            if kind == "context.supplied":
                entered.set()
                await release.wait()
            await original_emit(run_id, kind, payload)

        monkeypatch.setattr(service, "emit", hold_context)
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic service boundary",
                "root": str(tmp_path), "permission": "read_only"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "ollama", "model": "synthetic-live-model", "settings": {
                    "tools": False, "reasoning_effort": "xhigh"}}))["session"]
            run = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "synthetic-admitted-request", "prompt": "Read synthetic context"}))["run"]
            await asyncio.wait_for(entered.wait(), 3)
            assert not observed and run["id"] not in service._run_controls
            queued = await service.dispatch("run.steer", args(run))
            assert queued["delivery_state"] == "queued"
            release.set()
            async with asyncio.timeout(3):
                while run["id"] in service.tasks:
                    await asyncio.sleep(.01)
            assert len(observed) == 1
            assert observed[0].model == "synthetic-live-model"
            assert observed[0].settings["reasoning_effort"] == "xhigh"
            receipt = await service.dispatch("run.steer", args(run))
            assert receipt["delivery_state"] == ("provider_accepted" if consume else "not_delivered")
            events = service.store.events(run["id"])["events"]
            receipt_seq = max(event["seq"] for event in events if event["type"] == (
                "steering.accepted" if consume else "steering.failed"))
            terminal_seq = max(event["seq"] for event in events if event["type"] == "run.state"
                               and event["payload"].get("state") == "completed")
            assert receipt_seq < terminal_seq
            with pytest.raises(KiraError) as closed:
                await observed[0].guidance("pending", {})
            assert closed.value.code == "NOT_ACTIVE"
            assert service.store.one("sessions", session["id"])["settings"] == session["settings"]
        finally:
            release.set()
            await service.close()


@pytest.mark.parametrize("provider", ["direct", "ollama"])
async def test_guidance_during_final_response_continues_without_cancelling_stream(inbox, monkeypatch, provider):
    service, project, session, run = inbox
    bodies = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            entered.set()
            await release.wait()
        return response(provider, text=f"Synthetic response {len(bodies)}")

    backend, session, boundary = await make_adapter(service, session, run, provider, handler, monkeypatch)

    async def unexpected(*values):
        raise AssertionError("This response does not execute tools")

    ctx = RunContext(run["id"], project, session, run["prompt"], "", lambda kind, data: service.emit(run["id"], kind, data),
        unexpected, unexpected, asyncio.Event(), guidance=boundary)
    task = asyncio.create_task(backend.run(ctx))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await service.dispatch("run.steer", args(run))
        assert not task.done()
        release.set()
        assert (await asyncio.wait_for(task, 3))["state"] == "completed"
        assert len(bodies) == 2
        completed = records(service, run, "message.completed")
        assert len(completed) == 2 and len({item["item_id"] for item in completed}) == 2
        assert len(records(service, run, "steering.accepted")) == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await backend.close()


@pytest.mark.parametrize("provider", ["direct", "ollama"])
@pytest.mark.parametrize("ending", ["cancel", "lost_response"])
async def test_adapter_cancellation_or_lost_response_never_replays_guidance(inbox, monkeypatch, provider, ending):
    service, project, session, run = inbox
    bodies, effects = [], []
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return response(provider, tools=True)
        raise httpx.ReadError("Synthetic provider response was lost", request=request)

    backend, session, boundary = await make_adapter(service, session, run, provider, handler, monkeypatch)

    async def execute(name, arguments):
        effects.append((name, copy.deepcopy(arguments)))
        entered.set()
        await release.wait()
        effects.append("completed-once")
        return {"ok": True}

    async def approve(values):
        raise AssertionError("No approval expected")

    ctx = RunContext(run["id"], project, session, run["prompt"], "", lambda kind, data: service.emit(run["id"], kind, data),
        execute, approve, asyncio.Event(), guidance=boundary)
    task = asyncio.create_task(backend.run(ctx))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        params = args(run)
        await service.dispatch("run.steer", params)
        assert not task.done() and effects == [("read_file", {"path": "first.txt"})]
        if ending == "cancel":
            ctx.cancel.set()
        else:
            release.set()
        outcome = await asyncio.wait_for(task, 3)
        assert outcome["state"] == ("cancelled" if ending == "cancel" else "interrupted")
        service.turn_controls.finished(run["id"], "Synthetic run finished")
        saved = await service.dispatch("run.steer", params)
        assert saved["delivery_state"] == ("not_delivered" if ending == "cancel" else "unknown")
        assert saved["status"] == ("rejected" if ending == "cancel" else "unknown")
        assert not saved["accepted"] and not saved["automatic_replay"]
        assert not records(service, run, "steering.accepted")
        assert len(bodies) == (1 if ending == "cancel" else 2)
        assert len(effects) == (1 if ending == "cancel" else 2)
        assert await service.dispatch("run.steer", params) == saved
        assert len(bodies) == (1 if ending == "cancel" else 2)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await backend.close()
