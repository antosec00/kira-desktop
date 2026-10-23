"""Fixture-backed lifecycle/IPC tests; these are not live provider acceptance."""

import asyncio
import copy
import json
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import MAX_FRAME_BYTES, KiraError
from kira.domain.paths import Paths
from kira.service.redaction import Redactor
from kira.service.server import Service


REQUEST = {"request_id": "provider-private-correlation", "blocking": False, "questions": [
    {"id": "direction", "header": "Direction", "question": "Which direction?", "isOther": True,
     "options": [{"label": "North", "description": "Go north"}, {"label": "South", "description": "Go south"}]}]}
ANSWER = {"direction": {"answers": ["North"]}}


class QuestionBackend:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.context = None

    async def discover(self):
        return {"available": True, "models": []}

    async def run(self, ctx):
        self.context = ctx
        self.started.set()
        release = asyncio.create_task(self.release.wait())
        cancelled = asyncio.create_task(ctx.cancel.wait())
        try:
            await asyncio.wait({release, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            return {"state": "cancelled" if ctx.cancel.is_set() else "completed"}
        finally:
            release.cancel()
            cancelled.cancel()
            await asyncio.gather(release, cancelled, return_exceptions=True)

    async def close(self):
        pass


@pytest_asyncio.fixture
async def runtime(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-input-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        backend = QuestionBackend()
        service = Service(Paths.from_env(), backend_factory=lambda name, settings: backend)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Questions fixture", "root": str(tmp_path)}))["project"]
        session = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "ollama", "model": "fixture-only"}))["session"]
        run = (await service.dispatch("run.start", {
            "session_id": session["id"], "request_id": "fixture-run", "prompt": "Fixture questions"}))["run"]
        await asyncio.wait_for(backend.started.wait(), 30)
        yield service, backend, project, run
        await service.close()


async def ipc(paths, method, params):
    reader, writer = await asyncio.open_unix_connection(paths.socket, limit=MAX_FRAME_BYTES)
    try:
        writer.write((json.dumps({"version": 1, "id": "fixture-ipc", "method": method, "params": params}) + "\n").encode())
        await writer.drain()
        packet = json.loads(await asyncio.wait_for(reader.readline(), 3))
        assert "error" not in packet, packet
        return packet["result"]
    finally:
        writer.close()
        await writer.wait_closed()


def events(service, run):
    return service.store.all("SELECT * FROM events WHERE run_id=? AND type LIKE 'input.%' ORDER BY seq", (run["id"],))


async def test_async_question_survives_client_reconnect_while_run_continues(runtime):
    service, backend, project, run = runtime
    ctx = backend.context
    result = await ctx.request_input(REQUEST)
    assert result["status"] == "pending" and result["request_id"] != REQUEST["request_id"]
    assert service.store.one("runs", run["id"])["state"] == "running"
    await ctx.emit("message.completed", {"content": "Independent work continues"})
    first = await ipc(service.paths, "input.list", {"project_id": project["id"]})
    second = await ipc(service.paths, "input.list", {"project_id": project["id"]})
    assert first == second and first["requests"][0]["status"] == "pending"
    assert REQUEST["request_id"] not in json.dumps(first)
    submitted = await ipc(service.paths, "input.answer", {
        "run_id": run["id"], "request_id": result["request_id"], "answers": ANSWER})
    assert submitted["accepted"] and not submitted["request"]["received"]
    assert not any(e["type"] == "input.received" for e in events(service, run))
    read = await ctx.read_input(result["request_id"], False)
    assert read["answers"] == ANSWER and read["received"]
    await ctx.read_input(result["request_id"], False)
    assert [e["type"] for e in events(service, run)] == ["input.requested", "input.answered", "input.received"]
    assert (await service.dispatch("hello", {}))["capabilities"]["input_questions"]


async def test_blocking_question_waits_for_committed_answer_and_restores_running(runtime):
    service, backend, project, run = runtime
    task = asyncio.create_task(backend.context.request_input({**REQUEST, "blocking": True}))
    await asyncio.sleep(0)
    assert not task.done()
    assert service.store.one("runs", run["id"])["state"] == "awaiting_input"
    pending = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": pending["request_id"], "answers": ANSWER})
    result = await asyncio.wait_for(task, 1)
    assert result["answers"] == ANSWER and result["received"]
    assert service.store.one("runs", run["id"])["state"] == "running"
    assert [e["type"] for e in events(service, run)] == ["input.requested", "input.answered", "input.received"]


async def test_provider_cancels_async_wait_closes_form_without_auto_answer(runtime):
    service, backend, project, run = runtime
    receipt = await backend.context.request_input(REQUEST)
    waiter = asyncio.create_task(backend.context.read_input(receipt["request_id"], True))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    state = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert state["status"] == "closed" and state["reason"] == "provider_request_cancelled"
    assert not state["received"] and "answers" not in state
    with pytest.raises(KiraError, match="closed"):
        await service.dispatch("input.answer", {"run_id": run["id"], "request_id": receipt["request_id"], "answers": ANSWER})


async def test_cancel_run_unblocks_native_wait_and_closes_question(runtime):
    service, backend, project, run = runtime
    task = asyncio.create_task(backend.context.request_input({**REQUEST, "blocking": True}))
    await asyncio.sleep(0)
    await service.dispatch("run.cancel", {"run_id": run["id"]})
    result = await asyncio.wait_for(task, 1)
    assert result["status"] == "closed" and "answers" not in result
    state = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert state["status"] == "closed" and not state["received"]


async def test_duplicates_conflicts_and_concurrent_clients(runtime):
    service, backend, project, run = runtime
    one, two = await asyncio.gather(backend.context.request_input(REQUEST), backend.context.request_input(REQUEST))
    assert one == two and len(events(service, run)) == 1
    changed = copy.deepcopy(REQUEST)
    changed["questions"][0]["question"] = "Changed question"
    with pytest.raises(KiraError) as conflict:
        await backend.context.request_input(changed)
    assert conflict.value.code == "CONFLICT"
    params = {"run_id": run["id"], "request_id": one["request_id"], "answers": ANSWER}
    first, second = await asyncio.gather(service.dispatch("input.answer", params), service.dispatch("input.answer", params))
    assert first == second and len(events(service, run)) == 2
    with pytest.raises(KiraError, match="different answer"):
        await service.dispatch("input.answer", {**params, "answers": {"direction": {"answers": ["South"]}}})


async def test_terminal_closes_saved_but_unreceived_answer_without_replay(runtime):
    service, backend, project, run = runtime
    result = await backend.context.request_input(REQUEST)
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": result["request_id"], "answers": ANSWER})
    task = service.tasks[run["id"]]
    backend.release.set()
    await asyncio.wait_for(task, 30)
    state = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert state["status"] == "closed" and state["answers"] == ANSWER and not state["received"]
    assert not any(e["type"] == "input.received" for e in events(service, run))
    assert (await backend.context.read_input(result["request_id"], False))["status"] == "closed"
    assert len(service.store.all("SELECT * FROM runs")) == 1


@pytest.mark.parametrize("retrieved", [False, True])
async def test_recovery_closes_orphan_question_and_preserves_saved_answer(runtime, retrieved):
    service, backend, project, run = runtime
    result = await backend.context.request_input(REQUEST)
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": result["request_id"], "answers": ANSWER})
    if retrieved:
        assert (await backend.context.read_input(result["request_id"], False))["received"]
    backup = service.store.backup()
    await service.close()
    # Isolated fixture copy represents crash before terminal finalization, with no
    # provider process identity. Never open or copy the user's active data store.
    service.paths.db.unlink()
    backup.rename(service.paths.db)
    recovered = Service(service.paths, backend_factory=lambda name, settings: QuestionBackend())
    await recovered.start()
    try:
        state = (await recovered.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
        assert state["status"] == "closed" and state["reason"] == "service_restarted"
        assert state["answers"] == ANSWER and state["received"] == retrieved and not state["delivered"]
        assert recovered.store.one("runs", run["id"])["state"] == "interrupted"
        assert not recovered.tasks
    finally:
        await recovered.close()


async def test_redaction_applies_before_question_and_answer_persistence(runtime):
    service, backend, project, run = runtime
    service.redactor = Redactor(values=("fixture-secret-value",))
    question = copy.deepcopy(REQUEST)
    question["questions"][0]["question"] = "Choose for fixture-secret-value"
    result = await backend.context.request_input(question)
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": result["request_id"],
        "answers": {"direction": {"answers": ["Other fixture-secret-value"]}}})
    persisted = json.dumps(events(service, run))
    assert "fixture-secret-value" not in persisted and "[REDACTED]" in persisted
    delivered = await backend.context.read_input(result["request_id"], False)
    assert delivered["answers"]["direction"]["answers"] == ["Other [REDACTED]"]


@pytest.mark.parametrize("change", [
    {"questions": []}, {"blocking": "false"}, {"extra": True},
    {"questions": [{"id": "one", "question": "Secret?", "isSecret": True}]},
])
async def test_malformed_request_has_no_persisted_event(runtime, change):
    service, backend, project, run = runtime
    with pytest.raises(KiraError) as error:
        await backend.context.request_input({**REQUEST, **change})
    assert error.value.code == "INVALID_ARGUMENT"
    assert events(service, run) == []


async def test_request_limit_project_and_run_isolation(runtime, tmp_path):
    service, backend, project, run = runtime
    receipts = [await backend.context.request_input({**REQUEST, "request_id": str(i)}) for i in range(16)]
    with pytest.raises(KiraError) as failure:
        await backend.context.request_input({**REQUEST, "request_id": "overflow"})
    assert failure.value.code == "OUTPUT_LIMIT"
    other_path = tmp_path / "other"
    other_path.mkdir()
    other = (await service.dispatch("project.create", {"root": str(other_path), "name": "Other"}))["project"]
    assert not (await service.dispatch("input.list", {"project_id": other["id"]}))["requests"]
    with pytest.raises(KiraError) as missing:
        await service.dispatch("input.answer", {"run_id": "different-run", "request_id": receipts[0]["request_id"], "answers": ANSWER})
    assert missing.value.code == "NOT_FOUND"
    first = await service.dispatch("input.list", {"project_id": project["id"], "limit": 10})
    second = await service.dispatch("input.list", {"project_id": project["id"], "offset": first["next_offset"], "limit": 10})
    assert len(first["requests"]) == 10 and len(second["requests"]) == 6
    assert first["has_more"] and not second["has_more"]
    assert len({q["request_id"] for q in first["requests"] + second["requests"]}) == 16


@pytest.mark.parametrize("answers", [{}, {"direction": {"answers": [""]}},
    {"direction": {"answers": ["North", "South"]}}, {"unknown": {"answers": ["North"]}},
    {"direction": {"answers": ["x" * 16_384]}}])
async def test_invalid_answers_never_wake_or_persist(runtime, answers):
    service, backend, project, run = runtime
    result = await backend.context.request_input(REQUEST)
    with pytest.raises(KiraError) as error:
        await service.dispatch("input.answer", {"run_id": run["id"], "request_id": result["request_id"], "answers": answers})
    assert error.value.code == "INVALID_ARGUMENT"
    assert len(events(service, run)) == 1


async def test_approval_and_information_question_wait_states_remain_independent(runtime):
    service, backend, project, run = runtime
    question = asyncio.create_task(backend.context.request_input({**REQUEST, "blocking": True}))
    approval = asyncio.create_task(service._approve(run["id"], {"message": "Fixture permission"}, backend.context.cancel))
    await asyncio.sleep(0)
    approval_id = next(key[1] for key in service.approvals)
    await service.dispatch("run.approve", {"run_id": run["id"], "approval_id": approval_id, "decision": "decline"})
    assert (await approval)["decision"] == "decline"
    assert service.store.one("runs", run["id"])["state"] == "awaiting_input"
    request = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": request["request_id"], "answers": ANSWER})
    assert (await question)["received"]
    assert service.store.one("runs", run["id"])["state"] == "running"
    assert service.store.one("projects", project["id"])["permission"] == "read_only"


async def test_cancelled_blocking_provider_request_restores_run_without_closing_other_question(runtime):
    service, backend, project, run = runtime
    asynchronous = await backend.context.request_input(REQUEST)
    blocking = asyncio.create_task(backend.context.request_input({**REQUEST, "request_id": "blocking", "blocking": True}))
    await asyncio.sleep(0)
    blocking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocking
    assert service.store.one("runs", run["id"])["state"] == "running"
    state = await backend.context.read_input(asynchronous["request_id"], False)
    assert state["status"] == "pending" and not state["received"]


async def test_maximal_form_pages_stay_inside_ipc_without_truncating_pending_questions(runtime):
    service, backend, project, run = runtime
    questions = [{"id": f"question-{i}", "question": "Q" * 11_000,
        "options": [{"label": "One", "description": "A" * 3_500},
                    {"label": "Two", "description": "B" * 3_500}]} for i in range(3)]
    receipts = [await backend.context.request_input({"request_id": str(i), "blocking": False, "questions": questions})
                for i in range(16)]
    first = await ipc(service.paths, "input.list", {"project_id": project["id"]})
    assert first["has_more"] and 0 < len(first["requests"]) < 16
    assert len(json.dumps(first).encode()) < 750_000
    second = await ipc(service.paths, "input.list", {"project_id": project["id"], "offset": first["next_offset"]})
    assert not second["has_more"]
    combined = first["requests"] + second["requests"]
    assert {r["request_id"] for r in receipts} == {q["request_id"] for q in combined}
    assert all(len(q["questions"][0]["question"]) == 11_000 for q in combined)


async def test_old_pending_question_survives_hundred_item_finished_history_limit(runtime):
    service, backend, project, run = runtime
    pending = await backend.context.request_input(REQUEST)
    for i in range(7):
        archived, _ = service.store.create_run(run["session_id"], f"archive-{i}", "Fixture history")
        for j in range(15):
            await service.questions.request(archived["id"], {**REQUEST, "request_id": str(j)}, asyncio.Event())
        await service.emit(archived["id"], "run.state", {"state": "completed"})
    page = await service.dispatch("input.list", {"project_id": project["id"]})
    tail = await service.dispatch("input.list", {"project_id": project["id"], "offset": page["next_offset"]})
    assert page["has_more"] and len(page["requests"]) == 100
    assert len(tail["requests"]) == 1 and tail["requests"][0]["request_id"] == pending["request_id"]
    assert tail["requests"][0]["status"] == "pending"
    assert not tail["has_more"]


async def test_delivered_answer_stays_delivered_after_terminal_and_recovery(runtime):
    service, backend, project, run = runtime
    result = await backend.context.request_input(REQUEST)
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": result["request_id"], "answers": ANSWER})
    assert (await backend.context.read_input(result["request_id"], False))["received"]
    await backend.context.emit("input.delivered", {"request_id": result["request_id"], "transport": "turn/steer"})
    task = service.tasks[run["id"]]
    backend.release.set()
    await asyncio.wait_for(task, 30)
    await service.questions.recover()
    state = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert state["status"] == "answered" and state["received"] and state["delivered"]
    assert state["delivery_transport"] == "turn/steer"
    assert [e["type"] for e in events(service, run)] == [
        "input.requested", "input.answered", "input.received", "input.delivered"]


async def test_delivery_failure_after_adapter_read_remains_closed_after_terminal_and_restart(runtime):
    service, backend, project, run = runtime
    service.redactor = Redactor(values=("fixture-private-provider-detail",))
    result = await backend.context.request_input(REQUEST)
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": result["request_id"], "answers": ANSWER})
    assert (await backend.context.read_input(result["request_id"], False))["received"]
    failure = {"request_id": result["request_id"], "reason": "Provider refused fixture-private-provider-detail"}
    await backend.context.emit("input.delivery_failed", failure)
    await backend.context.emit("input.delivery_failed", failure)
    state = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert state["status"] == "closed" and not state["received"] and state["answers"] == ANSWER
    assert state["reason"] == "Provider refused [REDACTED]"
    assert (await backend.context.read_input(result["request_id"], False))["status"] == "closed"
    task = service.tasks[run["id"]]
    backend.release.set()
    await asyncio.wait_for(task, 30)
    expected = ["input.requested", "input.answered", "input.received", "input.delivery_failed"]
    assert [e["type"] for e in events(service, run)] == expected
    await service.close()
    recovered = Service(service.paths, backend_factory=lambda name, settings: QuestionBackend())
    await recovered.start()
    try:
        actual = (await recovered.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
        assert actual == state
        assert not recovered.tasks
        assert [e["type"] for e in events(recovered, run)] == expected
        assert len(recovered.store.all("SELECT * FROM runs")) == 1
    finally:
        await recovered.close()


async def test_delivery_failure_wakes_pending_reader_and_validates_correlation(runtime):
    service, backend, project, run = runtime
    task = asyncio.create_task(backend.context.request_input({**REQUEST, "blocking": True}))
    await asyncio.sleep(0)
    question = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    with pytest.raises(KiraError, match="not found"):
        await backend.context.emit("input.delivery_failed", {"request_id": "unknown", "reason": "Fixture refusal"})
    with pytest.raises(KiraError, match="Unknown parameter"):
        await backend.context.emit("input.delivery_failed", {
            "request_id": question["request_id"], "reason": "Fixture refusal", "answers": ANSWER})
    with pytest.raises(KiraError, match="at most"):
        await backend.context.emit("input.delivery_failed", {"request_id": question["request_id"], "reason": "x" * 2048})
    await backend.context.emit("input.delivery_failed", {"request_id": question["request_id"], "reason": "Fixture refusal"})
    assert (await task)["status"] == "closed"
    assert service.store.one("runs", run["id"])["state"] == "running"
    assert [e["type"] for e in events(service, run)] == ["input.requested", "input.delivery_failed"]


async def test_delivery_requires_answer_read_and_acknowledgement_then_failure_is_terminal(runtime):
    service, backend, project, run = runtime
    receipt = await backend.context.request_input(REQUEST)
    payload = {"request_id": receipt["request_id"]}
    assert not receipt["received"] and not receipt["delivered"]
    with pytest.raises(KiraError, match="retrieved answer"):
        await backend.context.emit("input.delivered", payload)
    await service.dispatch("input.answer", {"run_id": run["id"], **payload, "answers": ANSWER})
    with pytest.raises(KiraError, match="retrieved answer"):
        await backend.context.emit("input.delivered", payload)
    read = await backend.context.read_input(receipt["request_id"], False)
    assert read["received"] and not read["delivered"]
    await backend.context.emit("input.delivered", payload)
    await backend.context.emit("input.delivered", payload)
    state = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert state["delivered"] and state["received"] and state["status"] == "answered"
    assert [e["type"] for e in events(service, run)].count("input.delivered") == 1
    await backend.context.emit("input.delivery_failed", {**payload, "reason": "Provider rejected delivery"})
    failed = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert not failed["delivered"] and not failed["received"] and failed["status"] == "closed"
    with pytest.raises(KiraError, match="retrieved answer"):
        await backend.context.emit("input.delivered", payload)


async def test_terminal_without_delivery_confirmation_closes_retrieved_answer(runtime):
    service, backend, project, run = runtime
    result = await backend.context.request_input(REQUEST)
    await service.dispatch("input.answer", {"run_id": run["id"], "request_id": result["request_id"], "answers": ANSWER})
    assert (await backend.context.read_input(result["request_id"], False))["received"]
    task = service.tasks[run["id"]]
    backend.release.set()
    await asyncio.wait_for(task, 30)
    state = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"][0]
    assert state["status"] == "closed" and state["received"] and not state["delivered"]
    assert state["answers"] == ANSWER and state["reason"] == "run_ended"
    with pytest.raises(KiraError, match="retrieved answer"):
        await backend.context.emit("input.delivered", {"request_id": result["request_id"]})
