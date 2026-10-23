"""Actual additive schema-13 upgrade, with synthetic preserved history and goals."""

import sqlite3

import pytest

from kira.domain.contracts import KiraError
from kira.service import storage
from kira.service.schema13 import AUTONOMOUS_GOALS_SCHEMA
from kira.service.storage import Store
from test_autonomous_goal_migration import previous_profile
from test_full_access_migration import schema
from test_service_migration import V14_TABLES, V15_TABLES, V16_TABLES, all_table_records


def v13_profile(path):
    db = previous_profile(path)
    db.executescript("BEGIN IMMEDIATE;" + AUTONOMOUS_GOALS_SCHEMA + "PRAGMA user_version=13; COMMIT;")
    db.execute("INSERT INTO autonomous_goals "
        "(id,project_id,session_id,objective,status,binding,created_at,updated_at) "
        "VALUES ('fixture-goal','project','session','Retain objective','paused','{}','then','then')")
    db.execute("INSERT INTO settings VALUES ('soul-upgrade-fixture','Retain previous settings')")
    return db


def test_v13_migration_keeps_every_table_record_and_leaves_identities_unselected(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    old = v13_profile(path)
    before, ddl = all_table_records(old), schema(old)
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    new = Store(path)
    try:
        after = all_table_records(new.db)
        assert {name: after[name] for name in before} == before
        assert set(after) - set(before) == set(V14_TABLES + V15_TABLES + V16_TABLES)
        assert all(after[name] == [] for name in V14_TABLES + V15_TABLES + V16_TABLES)
        assert all(schema(new.db)[key] == value for key, value in ddl.items())
        assert new.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert new.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not new.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        new.close()
        old.close()
    backup, = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 13
        assert all_table_records(db) == before
    Store(path).close()
    assert set(tmp_path.glob("*.pre-v16-*.bak")) - backups == {backup}


def test_failed_soul_migration_keeps_original_schema_and_rows(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    old = v13_profile(path)
    old.execute("CREATE TABLE soul_operations(fixture TEXT)")
    old.execute("INSERT INTO soul_operations VALUES ('Retain collision')")
    before, ddl = all_table_records(old), schema(old)
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Store(path)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 13
    assert all_table_records(old) == before and schema(old) == ddl
    old.close()


def test_previous_binary_refuses_new_schema_and_can_read_backup(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite3"
    old = v13_profile(path)
    before = all_table_records(old)
    old.close()
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    Store(path).close()
    backup, = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 13)
    with pytest.raises(KiraError, match="newer"):
        Store(path)
    retained = Store(backup)
    try:
        assert all_table_records(retained.db) == before
    finally:
        retained.close()
