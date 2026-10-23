"""Per-member provider cleanup with synthetic process tables; no real process is signalled."""

import os
import signal

import pytest

from kira.service import processes as module
from kira.service.processes import ProcessRegistry, exited, observed
from kira.service.storage import Store, now

FAKE_DESCRIPTOR = 10**6


@pytest.fixture
def registry(tmp_path):
    store = Store(tmp_path / "processes.sqlite3")
    store.db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                     ("project", "Fixture", str(tmp_path), "", "read_only", "{}", "then", "then"))
    store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                     ("session", "project", "codex", "fixture", "{}", None, "then"))
    run, _ = store.create_run("session", "request", "Synthetic process fixture")
    yield ProcessRegistry(store), run["id"]
    store.close()


def record(pid=41000, **changes):
    return {"pid": pid, "uid": os.getuid(), "boot_id": module.boot_id(), "state": "S",
            "ppid": os.getpid(), "pgid": 41000, "sid": 41000, "start_ticks": "100",
            "pid_namespace": module.pid_namespace(os.getpid()), **changes}


def insert(owner, run_id, leader):
    owner.store.db.execute(
        "INSERT INTO provider_processes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, leader["pid"], leader["uid"], leader["boot_id"], leader["start_ticks"],
         leader["pgid"], leader["sid"], owner.store_id, "active", None, now()))
    owner._remember(run_id, leader)


def simulate(monkeypatch, table, *, survivors=()):
    """Serve identities from a synthetic table; a signalled entry exits unless it survives."""
    sent = []
    monkeypatch.setattr(module, "identity", lambda pid: table.get(pid))
    monkeypatch.setattr(module, "processes", lambda: list(table.values()))
    monkeypatch.setattr(os, "pidfd_open", lambda pid: FAKE_DESCRIPTOR + pid)
    real_close = os.close

    def close(descriptor):
        if descriptor < FAKE_DESCRIPTOR:
            real_close(descriptor)

    def deliver(descriptor, sig):
        pid = descriptor - FAKE_DESCRIPTOR
        sent.append((pid, sig))
        if pid not in survivors:
            table.pop(pid, None)

    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(signal, "pidfd_send_signal", deliver)
    return sent


def state(owner):
    return owner.store.db.execute("SELECT state FROM provider_processes").fetchone()[0]


async def test_member_with_changed_session_does_not_block_signals_to_others(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    child = record(pid=41001, ppid=41000, start_ticks="101")
    escaped = record(pid=41002, ppid=41000, start_ticks="102")
    insert(owner, run_id, leader)
    owner._remember(run_id, child)
    owner._remember(run_id, escaped)
    # The last member later called setsid(): same process, new session.
    table = {41000: dict(leader), 41001: dict(child),
             41002: {**escaped, "pgid": 41002, "sid": 41002}}
    sent = simulate(monkeypatch, table)
    assert not await owner.cleanup(run_id)
    assert (41001, signal.SIGTERM) in sent and (41000, signal.SIGTERM) in sent
    assert all(pid != 41002 for pid, _ in sent)
    assert state(owner) == "unknown"  # The changed member is still unconfirmed.


async def test_reused_member_pid_proves_the_member_exited(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    member = record(pid=41001, ppid=41000, start_ticks="101")
    insert(owner, run_id, leader)
    owner._remember(run_id, member)
    # An unrelated process now holds the member's PID in another session.
    unrelated = record(pid=41001, ppid=1, pgid=50000, sid=50000, start_ticks="900")
    table = {41000: dict(leader), 41001: unrelated}
    sent = simulate(monkeypatch, table, survivors={41001})
    assert await owner.cleanup(run_id)
    assert sent == [(41000, signal.SIGTERM)]
    assert state(owner) == "stopped"


async def test_reused_leader_pid_still_signals_an_escaped_member(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    escaped = record(pid=41001, ppid=41000, pgid=41001, sid=41001, start_ticks="101")
    insert(owner, run_id, leader)
    owner._remember(run_id, escaped)
    # The leader exited and its PID was reused outside the provider tree.
    unrelated = record(pid=41000, ppid=1, pgid=50000, sid=50000, start_ticks="900")
    table = {41000: unrelated, 41001: dict(escaped)}
    sent = simulate(monkeypatch, table, survivors={41000})
    assert await owner.cleanup(run_id)
    assert sent == [(41001, signal.SIGTERM)]
    assert state(owner) == "stopped"


async def test_observation_forgets_exited_members_with_empty_sessions(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    insert(owner, run_id, leader)
    finished = [record(pid=42000 + index, ppid=41000, pgid=42000 + index, sid=42000 + index,
                       start_ticks=str(200 + index)) for index in range(50)]
    sibling = record(pid=41001, ppid=41000, start_ticks="101")
    for member in [*finished, sibling]:
        owner._remember(run_id, member)
    # The sibling exited too, but its session (the leader's) is still alive.
    table = {41000: dict(leader)}
    simulate(monkeypatch, table)
    await owner.observe(run_id)
    assert sorted(member["pid"] for member in owner._members(run_id)) == [41000, 41001]


async def test_reused_pid_inside_the_tree_records_the_new_descendant(registry, monkeypatch):
    owner, run_id = registry
    leader = record()
    stale = record(pid=41001, ppid=41000, start_ticks="101")
    insert(owner, run_id, leader)
    owner._remember(run_id, stale)
    descendant = record(pid=41001, ppid=41000, start_ticks="300")
    table = {41000: dict(leader), 41001: descendant}
    sent = simulate(monkeypatch, table)
    await owner.observe(run_id)
    current = next(member for member in owner._members(run_id) if member["pid"] == 41001)
    assert current["start_ticks"] == "300"
    assert await owner.cleanup(run_id)
    assert (41001, signal.SIGTERM) in sent and state(owner) == "stopped"


def test_permission_denied_identity_uses_the_readable_start_time(monkeypatch):
    def denied(pid):
        raise PermissionError("synthetic: namespace unreadable")

    monkeypatch.setattr(module, "identity", denied)
    current = observed(os.getpid())
    assert current is not None and current["uid"] is None
    assert exited(record(pid=os.getpid(), start_ticks="1"), current)
    same_start = record(pid=os.getpid(), start_ticks=current["start_ticks"])
    assert not exited(same_start, current)
    assert not module.same_process(same_start, current)
