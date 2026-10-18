"""Native chat operation ownership, recovery and profile-copy boundaries."""

import asyncio
import copy
import json
import os
import signal
import sys
from types import SimpleNamespace

import pytest

from kira.backends import codex
from kira.service.processes import ProcessRegistry
from kira.service.storage import Store, now
from test_backends_workbench import compaction_events, context
from test_backends_workbench import server as server


@pytest.fixture
def operation_store(tmp_path):
    store = Store(tmp_path / "operations.sqlite3")
    store.db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                     ("project", "Fixture", str(tmp_path), "", "read_only", "{}", now(), now()))
    store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                     ("session", "project", "codex", "fixture-model", "{}", "fixture-thread", now()))
    store.db.execute("INSERT INTO session_forks (request_id,fingerprint,project_id,source_session_id,"
                     "root,status,result,created_at,updated_at,method) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("control-request", "fixture-fingerprint", "project", "session", str(tmp_path),
                      "pending", "{}", now(), now(), "session.compact"))
    yield store, "control-request"
    store.close()


async def child():
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(120)", start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)


async def stop(process):
    if process.returncode is None:
        process.terminate()
        await asyncio.wait_for(process.wait(), 3)


async def test_control_recovery_cleans_owned_child_without_touching_run_processes(operation_store):
    store, request_id = operation_store
    controls = ProcessRegistry(store, kind="control")
    runs = ProcessRegistry(store)
    run, _ = store.create_run("session", "run-request", "Synthetic process fixture")
    control_child, run_child = await child(), await child()
    try:
        await controls.register(request_id, control_child.pid)
        await runs.register(run["id"], run_child.pid)
        assert store.db.execute("SELECT pid FROM control_processes").fetchone()[0] == control_child.pid
        assert store.db.execute("SELECT pid FROM provider_processes").fetchone()[0] == run_child.pid
        assert controls._members(request_id)[0]["ownership"]["kind"] == "control"
        store.db.execute("UPDATE session_forks SET status='unknown' WHERE request_id=?", (request_id,))
        assert store.reserved(store.one("projects", "project")["root"])
        await controls.recover()
        await asyncio.wait_for(control_child.wait(), 3)
        assert control_child.returncode == -signal.SIGTERM
        assert run_child.returncode is None
        assert store.db.execute("SELECT state FROM control_processes").fetchone()[0] == "stopped"
        assert store.db.execute("SELECT state FROM provider_processes").fetchone()[0] == "active"
        assert not store.reserved(store.one("projects", "project")["root"])
        assert not controls.monitors
    finally:
        await controls.cleanup(request_id)
        await runs.cleanup(run["id"])
        await stop(control_child)
        await stop(run_child)
        await controls.close()
        await runs.close()


async def test_copied_profile_cannot_signal_live_control_child(operation_store, tmp_path, monkeypatch):
    store, request_id = operation_store
    owner = ProcessRegistry(store, kind="control")
    process = await child()
    copied = None
    copied_store = None
    try:
        await owner.register(request_id, process.pid)
        copied_store = Store(store.backup(tmp_path / "copied-profile.sqlite3"))
        copied = ProcessRegistry(copied_store, kind="control")
        assert copied.store_id == owner.store_id and copied.origin != owner.origin

        def forbidden(*_):
            raise AssertionError("Copied profile must not adopt or signal the original child")

        monkeypatch.setattr(copied, "_signal", forbidden)
        monkeypatch.setattr(copied, "_remember", forbidden)
        assert not await copied.cleanup(request_id)
        assert process.returncode is None
        assert copied_store.db.execute("SELECT state FROM control_processes").fetchone()[0] == "unknown"
        assert store.db.execute("SELECT state FROM control_processes").fetchone()[0] == "active"
        assert len(copied._members(request_id)) == len(owner._members(request_id)) == 1
    finally:
        if copied:
            await copied.close()
        if copied_store:
            copied_store.close()
        await owner.cleanup(request_id)
        await stop(process)
        await owner.close()


async def test_control_identity_cannot_be_reclassified_as_run_authority(operation_store, monkeypatch):
    store, request_id = operation_store
    owner = ProcessRegistry(store, kind="control")
    process = await child()
    try:
        await owner.register(request_id, process.pid)
        original = owner._members(request_id)[0]
        forged = copy.deepcopy(original)
        forged["ownership"]["kind"] = "run"
        store.db.execute("UPDATE control_process_members SET identity=? WHERE run_id=?",
                         (json.dumps(forged), request_id))
        with monkeypatch.context() as patch:
            def forbidden(*_):
                raise AssertionError("A different registry kind grants no signal authority")
            patch.setattr(owner, "_signal", forbidden)
            assert not await owner.cleanup(request_id)
        assert process.returncode is None
        assert store.db.execute("SELECT state FROM control_processes").fetchone()[0] == "unknown"
        store.db.execute("UPDATE control_process_members SET identity=? WHERE run_id=?",
                         (json.dumps(original), request_id))
    finally:
        await owner.cleanup(request_id)
        await stop(process)
        await owner.close()


@pytest.mark.parametrize("kind", ["unknown", "provider_processes", "control;DROP TABLE runs"])
def test_registry_table_selection_rejects_unknown_kinds(operation_store, kind):
    store, _ = operation_store
    with pytest.raises(ValueError, match="registry kind"):
        ProcessRegistry(store, kind=kind)
    assert store.db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


@pytest.mark.parametrize("action", ["fork", "compact"])
async def test_management_records_owned_process_before_any_thread_operation(tmp_path, server, action):
    ctx, _ = context(tmp_path)
    server.compact_events = compaction_events()
    original_start = server.start

    async def start(self, **kwargs):
        self.process = SimpleNamespace(pid=34567)
        await original_start(self, **kwargs)

    server.start = start
    seen = []

    async def register(pid):
        assert server.instances[-1].requests == []
        seen.append(pid)

    try:
        result = await codex.CodexBackend().manage_thread(action, ctx.session, ctx.project, {},
                                                       provider_started=register)
    finally:
        server.start = original_start
    assert seen == [34567] and server.instances[-1].closed
    assert result.get("compacted") is True if action == "compact" else result["backend_ref"] == "fork-thread"


async def test_management_ownership_registration_failure_prevents_thread_effects(tmp_path, server):
    ctx, _ = context(tmp_path)
    original_start = server.start

    async def start(self, **kwargs):
        self.process = SimpleNamespace(pid=os.getpid())
        await original_start(self, **kwargs)

    server.start = start

    async def reject(_):
        raise RuntimeError("Synthetic ownership persistence failure")

    try:
        with pytest.raises(codex.KiraError) as error:
            await codex.CodexBackend().manage_thread("fork", ctx.session, ctx.project, {},
                                                   provider_started=reject)
    finally:
        server.start = original_start
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert not server.instances[-1].requests and server.instances[-1].closed


@pytest.mark.parametrize("status", ["active", "paused", "blocked", "budgetLimited", "usageLimited"])
async def test_resume_refuses_unfinished_native_goal_without_mutation_or_inference(tmp_path, server, status):
    ctx, _ = context(tmp_path)
    goal = {"threadId": "source-thread", "status": status, "objective": "Synthetic native goal"}
    server.goal = copy.deepcopy(goal)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed" and "unfinished native goal" in result["error"]
    assert server.instances[-1].requests == [("thread/goal/get", {"threadId": "source-thread"})]
    assert server.goal == goal and server.instances[-1].closed
