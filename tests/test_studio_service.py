"""Studio durable-world tests with synthetic projects, no real provider or profile."""

import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kira.domain.contracts import KiraError
from kira.domain.studio import poster_scene
from kira.service.storage import Store, now
from kira.service.studio import Studio


def fixture(tmp_path):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    path = tmp_path / "studio.sqlite3"
    store = Store(path)
    project_id = str(uuid4())
    store.db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                     (project_id, "synthetic", str(tmp_path), "", "read_only",
                      "{}", now(), now()))
    studio = Studio(SimpleNamespace(store=store, paths=SimpleNamespace(data=data)))
    return studio, store, project_id


@pytest.mark.asyncio
async def test_default_off_no_world_or_worker_and_explicit_activation(tmp_path):
    studio, store, pid = fixture(tmp_path)
    try:
        snapshot = await studio.dispatch("studio.status", {"project_id": pid})
        assert snapshot == {"enabled": False, "revision": 0, "scene": {}, "history": []}
        assert list(store.db.execute("SELECT * FROM studio_worlds")) == []
        assert not studio._renders
        with pytest.raises(KiraError):
            await studio.dispatch("studio.render", {"project_id": pid})
        with pytest.raises(KiraError):
            await studio.dispatch("studio.poster", {"project_id": pid,
                "request_id": str(uuid4()), "expected_revision": 0, "text": "Synthetic poster caption"})
        assert list(store.db.execute("SELECT * FROM studio_worlds")) == []
        state = await studio.dispatch("studio.enable", {"project_id": pid,
            "request_id": str(uuid4()), "expected_enabled": False, "enabled": True})
        assert state["enabled"] is True and state["revision"] == 0
        assert not studio._renders
    finally:
        await studio.close()
        store.close()


@pytest.mark.asyncio
async def test_poster_edit_conflict_idempotency_restore_and_restart(tmp_path):
    studio, store, pid = fixture(tmp_path)
    try:
        await studio.dispatch("studio.enable", {"project_id": pid, "request_id": str(uuid4()),
                                               "expected_enabled": False, "enabled": True})
        request_id = str(uuid4())
        request = {"project_id": pid, "request_id": request_id,
                   "expected_revision": 0, "text": "Synthetic poster caption"}
        first = await studio.dispatch("studio.poster", request)
        assert first["revision"] == 1 and first["scene"] == poster_scene("Synthetic poster caption")
        assert await studio.dispatch("studio.poster", request) == first
        with pytest.raises(KiraError):
            await studio.dispatch("studio.poster", {**request, "text": "Changed"})
        change = await studio.dispatch("studio.patch", {
            "project_id": pid, "request_id": str(uuid4()), "expected_revision": 1,
            "object_id": "message", "values": {"color": "#182b68"}})
        assert change["revision"] == 2
        assert change["scene"]["objects"][-1]["color"] == "#182b68"
        assert change["scene"]["objects"][:-1] == first["scene"]["objects"][:-1]
        with pytest.raises(KiraError):
            await studio.dispatch("studio.patch", {
                "project_id": pid, "request_id": str(uuid4()), "expected_revision": 1,
                "object_id": "message", "values": {"text": "Stale"}})
        restored = await studio.dispatch("studio.restore", {
            "project_id": pid, "request_id": str(uuid4()),
            "expected_revision": 2, "target_revision": 1})
        assert restored["revision"] == 3 and restored["scene"] == first["scene"]
        assert len(restored["history"]) == 3
        await studio.dispatch("studio.enable", {"project_id": pid,
            "request_id": str(uuid4()), "expected_enabled": True, "enabled": False})
        disabled = await studio.dispatch("studio.status", {"project_id": pid})
        assert not disabled["enabled"] and disabled["revision"] == 3
    finally:
        await studio.close()
        store.close()
    reopened = Store(tmp_path / "studio.sqlite3")
    try:
        another = Studio(SimpleNamespace(store=reopened, paths=SimpleNamespace(
            data=tmp_path / "data")))
        persisted = await another.dispatch("studio.status", {"project_id": pid})
        assert persisted["revision"] == 3 and persisted["scene"] == first["scene"]
        assert persisted["enabled"] is False and not another._renders
        await another.close()
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_malformed_and_cross_project_operations_are_atomic(tmp_path):
    studio, store, pid = fixture(tmp_path)
    other = str(uuid4())
    store.db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                     (other, "other", str(tmp_path), "", "read_only",
                      "{}", now(), now()))
    try:
        await studio.dispatch("studio.enable", {"project_id": pid, "request_id": str(uuid4()),
                                               "expected_enabled": False, "enabled": True})
        with pytest.raises(KiraError):
            await studio.dispatch("studio.poster", {
                "project_id": pid, "request_id": str(uuid4()), "expected_revision": 0,
                "text": "<iframe>\n" + "X" * 200})
        assert (await studio.dispatch("studio.status", {"project_id": pid}))["revision"] == 0
        assert (await studio.dispatch("studio.status", {"project_id": other}))["enabled"] is False
        with pytest.raises(KiraError):
            await studio.dispatch("studio.poster", {
                "project_id": other, "request_id": str(uuid4()),
                "expected_revision": 0, "text": "Not allowed"})
        assert store.db.execute("SELECT COUNT(*) FROM studio_revisions").fetchone()[0] == 0
    finally:
        await studio.close()
        store.close()


def test_schema14_migration_keeps_existing_projects_and_creates_private_backup(tmp_path):
    db_path = tmp_path / "previous.sqlite3"
    store = Store(db_path)
    pid = str(uuid4())
    store.db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                     (pid, "retained", str(tmp_path), "important", "read_only",
                      "{}", now(), now()))
    store.close()
    old = sqlite3.connect(db_path, isolation_level=None)
    for table in ("studio_agent_commits", "studio_grants",
                  "studio_operations", "studio_revisions", "studio_worlds"):
        old.execute(f"DROP TABLE {table}")
    old.execute("PRAGMA user_version=14")
    old.close()
    migrated = Store(db_path)
    try:
        assert migrated.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert migrated.one("projects", pid)["instructions"] == "important"
        assert migrated.db.execute("SELECT COUNT(*) FROM studio_worlds").fetchone()[0] == 0
        assert migrated.db.execute("PRAGMA foreign_key_check").fetchall() == []
        backups = list(tmp_path.glob("*.pre-v16-*.bak"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as old_backup:
            assert old_backup.execute("PRAGMA user_version").fetchone()[0] == 14
            assert old_backup.execute("SELECT instructions FROM projects WHERE id=?",
                                      (pid,)).fetchone()[0] == "important"
    finally:
        migrated.close()



@pytest.mark.asyncio
async def test_schema15_to16_preserves_user_poster_and_history(tmp_path):
    """A user who tried M1 must retain their poster on the provider upgrade."""
    studio, store, pid = fixture(tmp_path)
    try:
        await studio.dispatch("studio.enable", {
            "project_id": pid, "request_id": str(uuid4()),
            "enabled": True, "expected_enabled": False,
        })
        await studio.dispatch("studio.poster", {
            "project_id": pid, "request_id": str(uuid4()),
            "text": "Synthetic poster caption", "expected_revision": 0,
        })
        before = await studio.dispatch("studio.status", {"project_id": pid})
    finally:
        await studio.close()
        store.close()
    # An M1 v15 profile has no chat-grant or agent-attribution tables.
    old = sqlite3.connect(tmp_path / "studio.sqlite3", isolation_level=None)
    old.execute("DROP TABLE studio_agent_commits")
    old.execute("DROP TABLE studio_grants")
    old.execute("PRAGMA user_version=15")
    old.close()
    upgraded = Store(tmp_path / "studio.sqlite3")
    try:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        recovered = Studio(SimpleNamespace(
            store=upgraded, paths=SimpleNamespace(data=tmp_path / "data")))
        state = await recovered.dispatch("studio.status", {"project_id": pid})
        assert state == before
        assert upgraded.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert upgraded.db.execute(
            "SELECT origin FROM studio_revisions WHERE project_id=?", (pid,)
        ).fetchone()["origin"] == "user"
        backup, = list(tmp_path.glob("*.pre-v16-*.bak"))
        with sqlite3.connect(backup) as prior:
            assert prior.execute("PRAGMA user_version").fetchone()[0] == 15
            assert prior.execute("SELECT COUNT(*) FROM studio_revisions").fetchone()[0] == 1
        await recovered.close()
    finally:
        upgraded.close()
