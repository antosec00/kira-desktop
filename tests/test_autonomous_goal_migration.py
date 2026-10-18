"""Schema-12 goals remain passive and every original row survives migration."""

import sqlite3

import pytest

from kira.domain.contracts import KiraError
from kira.service import storage
from kira.service.schema12 import FULL_ACCESS_SCHEMA
from kira.service.storage import Store
from test_full_access_migration import schema, v11_database
from test_service_migration import V13_TABLES, V14_TABLES, V15_TABLES, V16_TABLES, all_table_records


def previous_profile(path):
    database = v11_database(path)
    database.executescript("BEGIN IMMEDIATE;" + FULL_ACCESS_SCHEMA + "PRAGMA user_version=12; COMMIT;")
    database.execute("UPDATE session_goals SET status='active',objective='Retain this manual objective'")
    database.execute("INSERT INTO settings VALUES ('goal-fixture','Keep committed WAL state')")
    return database


def test_additive_upgrade_preserves_full_profile_and_does_not_arm_old_goal(tmp_path):
    path = tmp_path / "profile.sqlite3"
    old = previous_profile(path)
    before, ddl = all_table_records(old), schema(old)
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    new = Store(path)
    try:
        after = all_table_records(new.db)
        assert {name: after[name] for name in before} == before
        assert set(after) - set(before) == set(V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        assert all(after[name] == [] for name in V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        assert all(schema(new.db)[name] == value for name, value in ddl.items())
        assert new.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert new.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not new.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        new.close()
        old.close()
    saved = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(saved) == 1
    backup = saved.pop()
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert all_table_records(db) == before
        assert schema(db) == ddl
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
    Store(path).close()
    assert set(tmp_path.glob("*.pre-v16-*.bak")) - backups == {backup}


def test_failed_upgrade_rolls_back_new_tables_and_keeps_original_backup(tmp_path):
    path = tmp_path / "collision.sqlite3"
    old = previous_profile(path)
    old.execute("CREATE TABLE goal_commands (original TEXT)")
    old.execute("INSERT INTO goal_commands VALUES ('Keep original collision record')")
    before, ddl = all_table_records(old), schema(old)
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    with pytest.raises(sqlite3.OperationalError, match="goal_commands"):
        Store(path)
    assert all_table_records(old) == before and schema(old) == ddl
    assert old.execute("PRAGMA user_version").fetchone()[0] == 12
    assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    old.close()
    saved = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(saved) == 1
    with sqlite3.connect(saved.pop()) as db:
        assert all_table_records(db) == before and schema(db) == ddl


def test_previous_reader_refuses_upgrade_but_accepts_preserved_backup(tmp_path, monkeypatch):
    path = tmp_path / "reader.sqlite3"
    old = previous_profile(path)
    before = all_table_records(old)
    old.close()
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    Store(path).close()
    backup, = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 12)
    with pytest.raises(KiraError, match="newer"):
        Store(path)
    preserved = Store(backup)
    assert preserved.db.execute("PRAGMA user_version").fetchone()[0] == 12
    assert all_table_records(preserved.db) == before
    preserved.close()
