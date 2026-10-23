"""Identity-bound provider cleanup using private harmless children or inert records."""

import asyncio
import json
import os
import signal
import sys

import pytest

from kira.domain.contracts import KiraError
from kira.service import processes as module
from kira.service.processes import ProcessRegistry
from kira.service.storage import Store, now


@pytest.fixture
def registry(tmp_path):
    store = Store(tmp_path / "processes.sqlite3")
    store.db.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
        ("project", "Fixture", str(tmp_path), "", "read_only", "{}", "then", "then"),
    )
    store.db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
        ("session", "project", "codex", "fixture", "{}", None, "then"),
    )
    run, _ = store.create_run("session", "request", "Harmless process fixture")
    yield ProcessRegistry(store), run["id"]
    store.close()


def record(pid=41000, **changes):
    return {
        "pid": pid,
        "uid": os.getuid(),
        "boot_id": module.boot_id(),
        "state": "S",
        "ppid": os.getpid(),
        "pgid": 41000,
        "sid": 41000,
        "start_ticks": "100",
        "pid_namespace": module.pid_namespace(os.getpid()),
        **changes,
    }


def insert(registry, run_id, leader):
    registry.store.db.execute(
        "INSERT INTO provider_processes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            leader["pid"],
            leader["uid"],
            leader["boot_id"],
            leader["start_ticks"],
            leader["pgid"],
            leader["sid"],
            registry.store_id,
            "active",
            None,
            now(),
        ),
    )
    registry._remember(run_id, leader)


@pytest.mark.parametrize("changed", [{"start_ticks": "101"}, {"uid": 123456}, {"sid": 41001}])
def test_changed_identity_never_opens_pidfd_or_signals(registry, monkeypatch, changed):
    owner, _ = registry
    expected = record(ownership=owner.ownership)
    monkeypatch.setattr(module, "identity", lambda pid: {**expected, **changed})

    def forbidden(*args):
        raise AssertionError("No descriptor/signal permitted for mismatched identity")

    monkeypatch.setattr(os, "pidfd_open", forbidden)
    monkeypatch.setattr(signal, "pidfd_send_signal", forbidden)
    if "start_ticks" in changed:
        # Another start time under the recorded PID: the recorded process exited.
        assert owner._signal(expected, signal.SIGTERM) is False
        return
    with pytest.raises(KiraError, match="identity changed"):
        owner._signal(expected, signal.SIGTERM)


async def test_unanchored_session_is_not_adopted_or_signalled(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    unknown = record(pid=41001, ppid=1, start_ticks="200")
    insert(owner, run_id, leader)
    monkeypatch.setattr(module, "identity", lambda pid: None if pid == leader["pid"] else unknown)
    monkeypatch.setattr(module, "processes", lambda: [unknown])

    def forbidden(*args):
        raise AssertionError("Unanchored session must retain ownership uncertainty")

    monkeypatch.setattr(owner, "_signal", forbidden)
    assert not await owner.cleanup(run_id)
    assert [m["pid"] for m in owner._members(run_id)] == [leader["pid"]]
    assert owner.store.db.execute("SELECT state FROM provider_processes").fetchone()[0] == "unknown"


async def test_recorded_descendant_anchors_its_private_session(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    descendant = record(pid=41001, ppid=1, start_ticks="101")
    grandchild = record(pid=41002, ppid=41001, start_ticks="102")
    insert(owner, run_id, leader)
    owner._remember(run_id, descendant)
    monkeypatch.setattr(
        module, "identity", lambda pid: None if pid == leader["pid"] else descendant
    )
    monkeypatch.setattr(module, "processes", lambda: [descendant, grandchild])
    await owner.observe(run_id)
    assert {m["pid"] for m in owner._members(run_id)} == {41000, 41001, 41002}


async def test_real_owned_child_is_registered_and_drained(registry):
    owner, run_id = registry
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(120)",
        start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await owner.register(run_id, child.pid)
        assert await owner.cleanup(run_id)
        await asyncio.wait_for(child.wait(), 3)
        assert child.returncode == -signal.SIGTERM
        assert (
            owner.store.db.execute("SELECT state FROM provider_processes").fetchone()[0]
            == "stopped"
        )
        assert not owner.monitors
    finally:
        if child.returncode is None:
            child.terminate()
            await child.wait()
        await owner.close()


async def test_wrong_store_identity_retains_unknown_without_signal(registry, monkeypatch):
    owner, run_id = registry
    insert(owner, run_id, record())
    owner.store.db.execute("UPDATE provider_processes SET store_id='different-private-store'")

    def forbidden(*args):
        raise AssertionError("No signal across store ownership")

    monkeypatch.setattr(owner, "_signal", forbidden)
    assert not await owner.cleanup(run_id)


async def test_database_backup_does_not_confer_original_process_authority(
    registry, tmp_path, monkeypatch
):
    owner, run_id = registry
    leader = record()
    insert(owner, run_id, leader)
    backup = owner.store.backup(tmp_path / "copied.sqlite3")
    copied_store = Store(backup)
    copied = ProcessRegistry(copied_store)
    monkeypatch.setattr(module, "identity", lambda pid: leader if pid == leader["pid"] else None)

    def forbidden(*args):
        raise AssertionError("A copied database must never adopt or signal the original provider")

    monkeypatch.setattr(copied, "_signal", forbidden)
    monkeypatch.setattr(copied, "_remember", forbidden)
    try:
        assert copied.store_id == owner.store_id
        assert copied.origin != owner.origin
        assert not await copied.cleanup(run_id)
        assert (
            copied.store.db.execute("SELECT state FROM provider_processes").fetchone()[0]
            == "unknown"
        )
        assert (
            owner.store.db.execute("SELECT state FROM provider_processes").fetchone()[0] == "active"
        )
        assert len(copied._members(run_id)) == len(owner._members(run_id)) == 1
    finally:
        copied_store.close()


async def test_same_namespace_quiescent_backup_reconciles_without_signals_or_rebinding(
    registry, tmp_path, monkeypatch
):
    owner, run_id = registry
    insert(owner, run_id, record())
    backup = owner.store.backup(tmp_path / "quiescent-copy.sqlite3")
    copied_store = Store(backup)
    copied = ProcessRegistry(copied_store)
    original_identity = copied._members(run_id)
    monkeypatch.setattr(module, "identity", lambda pid: None)
    monkeypatch.setattr(module, "processes", lambda: [])

    def forbidden(*args):
        raise AssertionError("Absence reconciliation must not acquire process authority")

    monkeypatch.setattr(copied, "_signal", forbidden)
    monkeypatch.setattr(copied, "_remember", forbidden)
    try:
        assert await copied.cleanup(run_id)
        assert copied._members(run_id) == original_identity
        assert (
            copied.store.db.execute("SELECT state FROM provider_processes").fetchone()[0]
            == "stopped"
        )
        assert (
            owner.store.db.execute("SELECT state FROM provider_processes").fetchone()[0] == "active"
        )
    finally:
        copied_store.close()


async def test_foreign_namespace_absence_does_not_prove_processes_stopped(registry, monkeypatch):
    owner, run_id = registry
    namespace = {"device": 900, "inode": 900}
    ownership = {**owner.ownership, "origin": {**owner.origin, "pid_namespace": namespace}}
    insert(owner, run_id, record(pid_namespace=namespace, ownership=ownership))
    monkeypatch.setattr(module, "identity", lambda pid: None)
    monkeypatch.setattr(module, "processes", lambda: [])

    def forbidden(*args):
        raise AssertionError("Foreign namespace records grant no signalling authority")

    monkeypatch.setattr(owner, "_signal", forbidden)
    assert not await owner.cleanup(run_id)
    assert owner.store.db.execute("SELECT state FROM provider_processes").fetchone()[0] == "unknown"


async def test_other_live_service_owner_blocks_same_store_cleanup(registry, monkeypatch):
    owner, run_id = registry
    service = record(pid=42000, pgid=42000, sid=42000, start_ticks="90")
    leader = record(ownership={**owner.ownership, "service": service})
    insert(owner, run_id, leader)
    monkeypatch.setattr(
        module, "identity", lambda pid: {leader["pid"]: leader, service["pid"]: service}.get(pid)
    )

    def forbidden(*args):
        raise AssertionError("Another living service retains its process authority")

    monkeypatch.setattr(owner, "_signal", forbidden)
    assert not await owner.cleanup(run_id)


async def test_same_store_orphan_recovery_remains_authorized(registry, monkeypatch):
    owner, run_id = registry
    service = record(pid=42000, pgid=42000, sid=42000, start_ticks="90")
    leader = record(ownership={**owner.ownership, "service": service})
    insert(owner, run_id, leader)
    alive = True
    signals = []
    monkeypatch.setattr(
        module, "identity", lambda pid: leader if pid == leader["pid"] and alive else None
    )
    monkeypatch.setattr(module, "processes", lambda: [leader] if alive else [])

    def signal_owned(expected, sig):
        nonlocal alive
        owner._assert_owner(expected)
        if alive:
            signals.append((expected["pid"], sig))
            alive = False

    monkeypatch.setattr(owner, "_signal", signal_owned)
    assert await owner.cleanup(run_id)
    assert signals == [(leader["pid"], signal.SIGTERM)]


async def test_legacy_active_identity_without_origin_is_never_adopted(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    insert(owner, run_id, leader)
    legacy = {key: value for key, value in leader.items() if key != "pid_namespace"}
    owner.store.db.execute("UPDATE provider_process_members SET identity=?", (json.dumps(legacy),))
    monkeypatch.setattr(module, "identity", lambda pid: None)
    monkeypatch.setattr(module, "processes", lambda: [])

    def forbidden(*args):
        raise AssertionError("Legacy unresolved records have no recoverable process authority")

    monkeypatch.setattr(owner, "_signal", forbidden)
    assert not await owner.cleanup(run_id)


def test_origin_rechecked_before_pidfd_signal(registry, monkeypatch):
    owner, _ = registry
    expected = record(ownership=owner.ownership)
    monkeypatch.setattr(module, "identity", lambda pid: expected)
    origins = iter([owner.origin, {**owner.origin, "inode": owner.origin["inode"] + 1}])
    monkeypatch.setattr(owner, "_current_origin", lambda: next(origins))
    monkeypatch.setattr(os, "pidfd_open", lambda pid: 999999)
    monkeypatch.setattr(os, "close", lambda descriptor: None)

    def forbidden(*args):
        raise AssertionError(
            "Changed store origin must prevent signalling after descriptor acquisition"
        )

    monkeypatch.setattr(signal, "pidfd_send_signal", forbidden)
    with pytest.raises(KiraError, match="origin differs"):
        owner._signal(expected, signal.SIGTERM)


async def test_nested_target_namespace_keeps_observer_namespace_authority(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    insert(owner, run_id, leader)
    descendant = record(
        pid=41001, ppid=41000, start_ticks="101", pid_namespace={"device": 901, "inode": 901}
    )
    owner._remember(run_id, descendant)
    stored = next(r for r in owner._members(run_id) if r["pid"] == descendant["pid"])
    # Numeric PIDs came from the same observer namespace, although a sandbox child
    # is itself in a nested PID namespace. Preserve that existing cleanup workflow.
    owner._assert_owner(stored)
    monkeypatch.setattr(module, "identity", lambda pid: None)
    monkeypatch.setattr(module, "processes", lambda: [])
    assert await owner._absent_without_signals(
        run_id, dict(owner.store.db.execute("SELECT * FROM provider_processes").fetchone())
    )


@pytest.mark.parametrize(
    "missing", ["pid", "uid", "boot_id", "start_ticks", "pgid", "sid", "pid_namespace"]
)
def test_incomplete_service_owner_never_grants_authority(registry, monkeypatch, missing):
    owner, _ = registry
    original_service = record(pid=42000, pgid=42000, sid=42000, start_ticks="90")
    incomplete = {key: value for key, value in original_service.items() if key != missing}
    expected = record(ownership={**owner.ownership, "service": incomplete})
    monkeypatch.setattr(
        module, "identity", lambda pid: original_service if pid == 42000 else expected
    )

    def forbidden(*args):
        raise AssertionError("Incomplete service identity cannot acquire a signal descriptor")

    monkeypatch.setattr(os, "pidfd_open", forbidden)
    with pytest.raises(KiraError, match="ownership is unavailable"):
        owner._signal(expected, signal.SIGTERM)


@pytest.mark.parametrize("origin", [None, [], 42, "copied"])
async def test_invalid_origin_retains_uncertainty_instead_of_aborting_recovery(
    registry, monkeypatch, origin
):
    owner, run_id = registry
    insert(owner, run_id, record(ownership={**owner.ownership, "origin": origin}))
    monkeypatch.setattr(module, "identity", lambda pid: None)
    monkeypatch.setattr(module, "processes", lambda: [])

    def forbidden(*args):
        raise AssertionError("Malformed origin must not acquire process authority")

    monkeypatch.setattr(owner, "_signal", forbidden)
    assert not await owner.cleanup(run_id)
    assert owner.store.db.execute("SELECT state FROM provider_processes").fetchone()[0] == "unknown"
