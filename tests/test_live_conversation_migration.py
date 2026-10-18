"""Additive v10 upgrade and provenance of automatic service turns."""

import sqlite3

import pytest

from kira.service.storage import Store
from test_service_migration import V13_TABLES, V14_TABLES, V15_TABLES, V16_TABLES, all_table_records, v9_database


def v10_database(path):
    old = v9_database(path)
    old.close()
    Store(path).close()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    for table in V16_TABLES + V15_TABLES + V14_TABLES + V13_TABLES:
        db.execute(f'DROP TABLE "{table}"')
    db.execute("DROP TABLE assistant_followups")
    db.execute("DROP TABLE run_lanes")
    db.execute("PRAGMA user_version=10")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("INSERT INTO settings VALUES ('v10-fixture','Retain current release settings')")
    return db


def test_v10_upgrade_preserves_all_existing_rows_and_arms_no_historical_teams(tmp_path):
    path = tmp_path / "v10.sqlite3"
    old = v10_database(path)
    before = all_table_records(old)
    schemas = dict(old.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    assert path.with_name(path.name + "-wal").stat().st_size
    upgraded = Store(path)
    try:
        after = all_table_records(upgraded.db)
        assert {name: after[name] for name in before} == before
        assert set(after) - set(before) == {"run_lanes", "assistant_followups", *V13_TABLES, *V14_TABLES, *V15_TABLES, *V16_TABLES}
        assert after["run_lanes"] == after["assistant_followups"] == []
        current = dict(upgraded.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        assert all(current[name] == ddl for name, ddl in schemas.items())
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert upgraded.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not upgraded.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        upgraded.close()
        old.close()
    added = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(added) == 1
    snapshot = added.pop()
    assert snapshot.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(snapshot) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert all_table_records(db) == before
    Store(path).close()
    assert set(tmp_path.glob("*.pre-v16-*.bak")) - backups == {snapshot}


def test_v10_migration_failure_rolls_back_both_tables_and_keeps_original(tmp_path):
    path = tmp_path / "collision.sqlite3"
    old = v10_database(path)
    old.execute("CREATE TABLE assistant_followups (original TEXT)")
    old.execute("INSERT INTO assistant_followups VALUES ('Retain synthetic collision')")
    before = all_table_records(old)
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Store(path)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 10
    assert all_table_records(old) == before
    assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    old.close()
