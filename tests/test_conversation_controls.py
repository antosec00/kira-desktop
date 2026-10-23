"""Service controls with synthetic providers; no authenticated model execution."""

import asyncio
import json
import tempfile
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode


class Provider:
    def __init__(self, controls):
        self.controls = controls
        self.release = asyncio.Event()
        self.ctx = None
        self.state = "completed"

    async def discover(self):
        return {"available": True, "models": [], "capabilities": {}}

    async def run(self, ctx):
        self.ctx = ctx
        self.controls.append(self)
        ctx.register_control(self.control)
        await self.release.wait()
        await ctx.emit("message.completed", {"content": "Synthetic response"})
        ctx.register_control(None)
        return {"state": self.state, "outcome": {"turn_id": "synthetic-turn"}}

    async def control(self, action, params):
        assert action == "steer"
        return {"accepted": True, "turn_id": "synthetic-turn", "request_id": params["request_id"]}

    async def close(self):
        pass


async def until(predicate):
    try:
        async with asyncio.timeout(30):
            while not predicate():
                await asyncio.sleep(.01)
    except TimeoutError:
        raise AssertionError("Expected service condition did not settle") from None


@pytest_asyncio.fixture
async def workbench_runtime(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-controls-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        providers = []
        service = Service(paths, backend_factory=lambda *args: Provider(providers))
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Fixture", "root": str(tmp_path), "permission": "trusted_host"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex", "model": "selected-fixture", "settings": {"reasoning_effort": "ultra"}}))["session"]
        try:
            yield service, providers, project, session
        finally:
            await service.close()


async def start(fixture, request_id="first", mode="execute"):
    service, providers, _, session = fixture
    number = len(providers)
    run = (await service.dispatch("run.start", {"session_id": session["id"], "request_id": request_id, "prompt": "Fixture task", "mode": mode}))["run"]
    await until(lambda: len(providers) > number)
    return run


async def add(fixture, identity, prompt="Queued fixture", mode="execute"):
    service, _, _, session = fixture
    return await service.dispatch("queue.add", {"session_id": session["id"], "request_id": identity, "prompt": prompt, "mode": mode})


async def test_queue_is_durable_ordered_editable_and_removed_identity_stays_removed(workbench_runtime):
    service, providers, project, session = workbench_runtime
    run = await start(workbench_runtime)
    one = (await add(workbench_runtime, "one"))["item"]
    two = (await add(workbench_runtime, "two"))["item"]
    removed = (await add(workbench_runtime, "removed"))["item"]
    await service.dispatch("queue.remove", {"project_id": project["id"], "item_id": removed["id"]})
    duplicate = await add(workbench_runtime, "removed")
    assert duplicate["item"]["status"] == "removed"
    await service.dispatch("queue.edit", {"project_id": project["id"], "item_id": two["id"], "prompt": "Edited second"})
    await service.dispatch("queue.reorder", {"project_id": project["id"], "item_ids": [two["id"], one["id"]]})
    assert len(providers) == 1
    providers[0].release.set()
    await until(lambda: len(providers) == 2)
    assert providers[1].ctx.prompt == "Edited second"
    assert providers[1].ctx.model == session["model"]
    assert providers[1].ctx.settings["reasoning_effort"] == "ultra"
    await service.dispatch("queue.pause", {"project_id": project["id"]})
    providers[1].release.set()
    await until(lambda: not service.tasks)
    assert len(providers) == 2
    await service.dispatch("queue.resume", {"project_id": project["id"]})
    await until(lambda: len(providers) == 3)
    providers[2].release.set()
    await until(lambda: not service.tasks)
    listing = await service.dispatch("queue.list", {"project_id": project["id"]})
    assert {row["status"] for row in listing["items"]} == {"completed"}
    assert all(row["request_id"] != "removed" for row in listing["items"])
    assert service.store.one("runs", run["id"])["state"] == "completed"


async def test_failed_run_pauses_queue_and_restart_requires_explicit_resume(workbench_runtime):
    service, providers, project, _ = workbench_runtime
    await start(workbench_runtime)
    await add(workbench_runtime, "waiting")
    providers[0].state = "failed"
    providers[0].release.set()
    await until(lambda: not service.tasks)
    assert (await service.dispatch("queue.list", {"project_id": project["id"]}))["paused"]
    paths = service.paths
    await service.close()
    restored = Service(paths, backend_factory=lambda *args: Provider(providers))
    await restored.start()
    try:
        listing = await restored.dispatch("queue.list", {"project_id": project["id"]})
        assert listing["paused"] and listing["items"][0]["status"] == "pending"
        assert len(providers) == 1
        await restored.dispatch("queue.resume", {"project_id": project["id"]})
        await until(lambda: len(providers) == 2)
        providers[1].release.set()
        await until(lambda: not restored.tasks)
    finally:
        await restored.close()


async def test_recovery_pauses_orphaned_first_item_when_controls_commit_was_lost(workbench_runtime):
    service, providers, project, _ = workbench_runtime
    await service.dispatch("queue.pause", {"project_id": project["id"]})
    await add(workbench_runtime, "first-item-crash")
    await service.prompt_queue.close()
    service.store.db.execute("DELETE FROM queue_controls WHERE project_id=?", (project["id"],))
    service.prompt_queue.recover()
    assert service.prompt_queue.listing(project["id"])["paused"]
    service.prompt_queue.kick(project["id"])
    await asyncio.sleep(.02)
    assert not providers and not service.tasks


@pytest.mark.parametrize("selected_mode", ["plan", "review", "recap"])
async def test_readonly_modes_enforced_by_service_without_changing_saved_permissions(workbench_runtime, selected_mode):
    service, providers, project, session = workbench_runtime
    run = await start(workbench_runtime, mode=selected_mode)
    ctx = providers[0].ctx
    assert ctx.mode == selected_mode and ctx.permission == "read_only"
    assert ctx.model == session["model"] and ctx.settings["reasoning_effort"] == "ultra"
    result = await ctx.execute_tool("write_file", {"path": "forbidden.txt", "content": "no", "expected_sha256": None})
    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert service.store.one("projects", project["id"])["permission"] == "trusted_host"
    with pytest.raises(KiraError, match="different input"):
        await service.dispatch("run.start", {"session_id": session["id"], "request_id": "first", "prompt": "Fixture task", "mode": "execute"})
    providers[0].release.set()
    await until(lambda: run["id"] not in service.tasks)


async def test_review_supplies_bounded_change_data_without_granting_command_execution(workbench_runtime, monkeypatch):
    service, providers, project, _ = workbench_runtime
    async def changes(method, params):
        assert method == "project.changes" and params == {"project_id": project["id"]}
        return {"available": True, "diff": "Synthetic changed-line marker\n" + "x" * 120_000, "truncated": False}
    monkeypatch.setattr(service.workbench, "dispatch", changes)
    await start(workbench_runtime, mode="review")
    context = providers[0].ctx
    assert "Synthetic changed-line marker" in context.supplied_context
    assert "preview is incomplete" in context.supplied_context
    assert "x" * 100_001 not in context.supplied_context
    denied = await context.execute_tool("run_command", {"argv": ["true"]})
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    providers[0].release.set()
    await until(lambda: not service.tasks)


async def test_steering_persists_before_delivery_and_duplicate_is_not_sent_twice(workbench_runtime):
    service, providers, project, _ = workbench_runtime
    run = await start(workbench_runtime)
    calls = []
    async def control(action, params):
        rows = service.store.all("SELECT * FROM steering_requests WHERE request_id=?", (params["request_id"],))
        assert rows[0]["status"] == "pending"
        calls.append(params)
        return {"accepted": True, "turn_id": "synthetic-turn"}
    service._run_controls[run["id"]] = control
    params = {"run_id": run["id"], "request_id": "steer-one", "prompt": "Use the extra fixture"}
    result = await service.dispatch("run.steer", params)
    assert result["accepted"]
    assert (await service.dispatch("run.steer", params))["status"] == "accepted"
    assert len(calls) == 1
    events = service.store.events(run["id"])["events"]
    guidance = [row for row in events if row["type"] == "message.user" and row["payload"].get("source") == "steer"]
    assert len(guidance) == 1 and guidance[0]["payload"]["content"] == params["prompt"]
    assert {"role": "user", "content": params["prompt"]} in service.store.history(run["session_id"])
    exported = await service.dispatch("project.export", {"project_id": project["id"]})
    original = json.loads(exported["text"])
    assert [entry["content"] for entry in original["messages"] if entry["role"] == "user"] == ["Fixture task", params["prompt"]]
    with pytest.raises(KiraError, match="different"):
        await service.dispatch("run.steer", {**params, "prompt": "Other instruction"})
    providers[0].release.set()
    await until(lambda: not service.tasks)
    with pytest.raises(KiraError, match="no longer"):
        await service.dispatch("run.steer", {**params, "request_id": "late"})


async def test_failed_acceptance_commit_never_leaves_accepted_without_transcript(workbench_runtime):
    service, providers, _, _ = workbench_runtime
    run = await start(workbench_runtime)
    service.store.db.execute("CREATE TRIGGER reject_steering BEFORE INSERT ON events "
        "WHEN NEW.type='steering.accepted' BEGIN SELECT RAISE(ABORT,'synthetic event failure'); END")
    params = {"run_id": run["id"], "request_id": "atomic-steer", "prompt": "Guidance with lost commit"}
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        await service.dispatch("run.steer", params)
    assert service.store.all("SELECT status FROM steering_requests WHERE request_id='atomic-steer'")[0]["status"] == "pending"
    assert not any(row["payload"].get("source") == "steer" for row in service.store.events(run["id"])["events"])
    service.store.db.execute("DROP TRIGGER reject_steering")
    service.turn_controls.recover()
    assert (await service.dispatch("run.steer", params))["status"] == "unknown"
    providers[0].release.set()
    await until(lambda: not service.tasks)


async def test_uncertain_steer_never_replays_or_becomes_an_accepted_chat_message(workbench_runtime):
    service, providers, _, _ = workbench_runtime
    run = await start(workbench_runtime)
    calls = []
    async def uncertain(action, params):
        calls.append(params)
        raise ConnectionError("Synthetic lost acknowledgment")
    service._run_controls[run["id"]] = uncertain
    params = {"run_id": run["id"], "request_id": "uncertain", "prompt": "Guidance"}
    with pytest.raises(KiraError) as error:
        await service.dispatch("run.steer", params)
    assert error.value.code == "DELIVERY_UNKNOWN"
    result = await service.dispatch("run.steer", params)
    assert result["status"] == "unknown" and not result["accepted"] and not result["automatic_replay"]
    assert len(calls) == 1
    assert not any(row["payload"].get("source") == "steer" for row in service.store.events(run["id"])["events"])
    providers[0].release.set()
    await until(lambda: not service.tasks)


async def test_queue_validates_scope_limits_and_archived_chat(workbench_runtime, tmp_path):
    service, _, project, session = workbench_runtime
    await service.dispatch("queue.pause", {"project_id": project["id"]})
    one = (await add(workbench_runtime, "one"))["item"]
    other = (await service.dispatch("project.create", {"name": "Other", "root": str(tmp_path)}))["project"]
    with pytest.raises(KiraError) as foreign:
        await service.dispatch("queue.edit", {"project_id": other["id"], "item_id": one["id"], "prompt": "foreign"})
    assert foreign.value.code == "NOT_FOUND"
    with pytest.raises(KiraError) as oversized:
        await add(workbench_runtime, "too-large", "é" * 4001)
    assert oversized.value.code == "INVALID_ARGUMENT"
    with pytest.raises(KiraError) as incomplete:
        await service.dispatch("queue.reorder", {"project_id": project["id"], "item_ids": []})
    assert incomplete.value.code == "CONFLICT"
    with pytest.raises(KiraError, match="queued requests"):
        await service.dispatch("session.archive", {"project_id": project["id"], "session_id": session["id"], "archived": True})
    await service.dispatch("queue.remove", {"project_id": project["id"], "item_id": one["id"]})
    await service.dispatch("session.archive", {"project_id": project["id"], "session_id": session["id"], "archived": True})
    assert not (await service.dispatch("session.list", {"project_id": project["id"]}))["sessions"]
    assert (await service.dispatch("session.list", {"project_id": project["id"], "archived": True}))["sessions"][0]["archived"]
    with pytest.raises(KiraError, match="Restore"):
        await add(workbench_runtime, "archived")


async def test_maximum_queue_remains_readable_with_json_escaping(workbench_runtime):
    service, _, project, _ = workbench_runtime
    await service.dispatch("queue.pause", {"project_id": project["id"]})
    for oversized in ('"' * 8000, '\\' * 8000, 'x' + '\x01' * 7999):
        with pytest.raises(KiraError, match="encoded"):
            await add(workbench_runtime, "reject-escaped", oversized)
    for index in range(64):
        result = await add(workbench_runtime, f"valid-{index}", 'x' + '\x01' * 1330)
        assert len(encode(result).encode()) < 700_000
    assert len(encode(await service.dispatch("queue.list", {"project_id": project["id"]})).encode()) < 700_000


async def test_active_goal_is_context_only_until_explicit_run_and_pause_omits_it(workbench_runtime):
    service, providers, project, session = workbench_runtime
    params = {"project_id": project["id"], "session_id": session["id"]}
    await service.dispatch("goal.update", {**params, "objective": "Synthetic long term target", "status": "active"})
    assert not providers and not service.tasks
    await start(workbench_runtime)
    assert "Synthetic long term target" in providers[0].ctx.supplied_context
    providers[0].release.set()
    await until(lambda: not service.tasks)
    await service.dispatch("goal.update", {**params, "status": "paused"})
    await start(workbench_runtime, request_id="after-pause")
    assert "Synthetic long term target" not in providers[1].ctx.supplied_context
    providers[1].release.set()
    await until(lambda: not service.tasks)


async def test_compaction_is_pending_until_terminal_provider_evidence_and_reserves_root(workbench_runtime, monkeypatch):
    service, _, project, session = workbench_runtime
    service.store.db.execute("UPDATE sessions SET backend_ref='fixture-thread' WHERE id=?", (session["id"],))
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    bindings = []
    class Compactor:
        process = None
        async def manage_thread(self, action, saved, scope, params, **callbacks):
            # Checked in the test body: an assertion here would only fail the compaction.
            bindings.append((saved["model"], scope["permission"]))
            self.process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)", start_new_session=True)
            await callbacks["provider_started"](self.process.pid)
            calls.append(action)
            entered.set()
            await release.wait()
            return {"compacted": True, "backend_ref": saved["backend_ref"]}
        async def close(self):
            if self.process and self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()
    monkeypatch.setattr(service, "_backend", lambda backend: Compactor())
    params = {"project_id": project["id"], "session_id": session["id"], "request_id": "compact-once"}
    assert (await service.dispatch("session.compact", params))["status"] == "pending"
    await asyncio.wait_for(entered.wait(), 30)
    assert bindings == [(session["model"], "read_only")]
    assert (await service.dispatch("session.compact", params))["status"] == "pending"
    with pytest.raises(KiraError, match="active"):
        await service.dispatch("settings.update", {"codex_binary": "different-fixture-binary"})
    with pytest.raises(KiraError, match="active"):
        await service.dispatch("run.start", {"session_id": session["id"], "request_id": "overlap", "prompt": "Must wait"})
    release.set()
    await until(lambda: not service.turn_controls.tasks)
    result = await service.dispatch("session.compact", params)
    assert result["status"] == "completed" and result["compacted"]
    assert calls == ["compact"]
    assert not service.store.reserved(project["root"])
    assert not service.store.all("SELECT * FROM runs")
    assert json.loads(service.store.all("SELECT result FROM session_forks")[0]["result"])["compacted"]


async def test_settings_change_serializes_new_admission_during_provider_close(workbench_runtime):
    service, providers, project, session = workbench_runtime
    entered, release = asyncio.Event(), asyncio.Event()
    class PreviousControl:
        async def close(self):
            entered.set()
            await release.wait()
    service._control_backends["codex"] = PreviousControl()
    change = asyncio.create_task(service.dispatch("settings.update", {"codex_binary": "fixture-new-binary"}))
    await asyncio.wait_for(entered.wait(), 30)
    try:
        for method, params in (("run.start", {"session_id": session["id"], "request_id": "race", "prompt": "Fixture"}),
                               ("session.compact", {"project_id": project["id"], "session_id": session["id"], "request_id": "compact"}),
                               ("backend.discover", {"backend": "codex"})):
            with pytest.raises(KiraError, match="being updated"):
                await service.dispatch(method, params)
        await add(workbench_runtime, "queued-during-change")
        await asyncio.sleep(.03)
        assert not providers
        assert (await service.dispatch("queue.list", {"project_id": project["id"]}))["items"][0]["status"] == "pending"
    finally:
        release.set()
        await asyncio.wait_for(change, 30)
    assert not service._settings_changing
    service.prompt_queue.kick(project["id"])
    await until(lambda: len(providers) == 1)
    providers[0].release.set()
    await until(lambda: not service.tasks)


async def test_compactor_construction_failure_finishes_durable_operation(workbench_runtime, monkeypatch):
    service, _, project, session = workbench_runtime
    service.store.db.execute("UPDATE sessions SET backend_ref='fixture-thread' WHERE id=?", (session["id"],))
    def unavailable(name):
        raise ValueError("Synthetic backend construction failed")
    monkeypatch.setattr(service, "_backend", unavailable)
    params = {"project_id": project["id"], "session_id": session["id"], "request_id": "construction-failed"}
    assert (await service.dispatch("session.compact", params))["status"] == "pending"
    await until(lambda: not service.turn_controls.tasks)
    assert (await service.dispatch("session.compact", params))["status"] == "failed"
    assert not service.store.reserved(project["root"])


async def test_native_success_without_process_proof_stays_reserved(workbench_runtime, monkeypatch):
    service, _, project, session = workbench_runtime
    service.store.db.execute("UPDATE sessions SET backend_ref='fixture-thread' WHERE id=?", (session["id"],))
    class Unregistered:
        async def manage_thread(self, *args, **kwargs):
            return {"compacted": True}
        async def close(self):
            pass
    monkeypatch.setattr(service, "_backend", lambda name: Unregistered())
    params = {"project_id": project["id"], "session_id": session["id"], "request_id": "no-proof"}
    await service.dispatch("session.compact", params)
    await until(lambda: not service.turn_controls.tasks)
    result = await service.dispatch("session.compact", params)
    assert result["status"] == "unknown" and not result.get("compacted")
    assert service.store.reserved(project["root"])
    with pytest.raises(KiraError, match="active"):
        await service.dispatch("settings.update", {"codex_binary": "other"})
