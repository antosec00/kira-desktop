"""Real populated schema-11 upgrade; synthetic history, no personal profile."""

import json
import sqlite3

import pytest

from kira.domain.contracts import KiraError
from kira.service import storage
from kira.service.schema11 import LIVE_CONVERSATION_SCHEMA
from kira.service.storage import Store
from test_live_conversation_migration import v10_database
from test_service_migration import V13_TABLES, V14_TABLES, V15_TABLES, V16_TABLES, all_table_records


def schema(database):
    return {(kind, name): ddl for kind, name, ddl in database.execute(
        "SELECT type,name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")}


def v11_database(path):
    """Use the shipped v11 lane constraint, with all three original values."""
    database = v10_database(path)
    database.executescript("BEGIN IMMEDIATE;" + LIVE_CONVERSATION_SCHEMA
                          + "PRAGMA user_version=11; COMMIT;")
    database.execute(
        "INSERT INTO team_runs "
        "(id,project_id,request_id,fingerprint,goal,mode,backend,model,settings,permission,"
        "root,state,phase,round_limit,concurrency,max_agents,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("team", "project", "team-request", "team-fingerprint", "Retain fixture team goal",
         "manual", "openai_chatgpt", "direct-fixture-model", '{"reasoning_effort":"high"}',
         "trusted_host", "/fixture-only", "completed", "completed", 0, 1, 2, "then", "later"))
    for number, permission in enumerate(("read_only", "project_write", "trusted_host")):
        run_id = f"lane-run-{number}"
        lane = "team_conversation" if permission == "read_only" else "team_followup"
        database.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, "session", f"lane-request-{number}", "Retain fixture lane context",
             "completed", "then", "later", None, '{"confirmed":true}', 1))
        database.execute("INSERT INTO run_lanes VALUES (?,?,?,?,?)",
                         (run_id, lane, "team", permission, "then"))
        database.execute("INSERT INTO run_modes VALUES (?,?)", (run_id, "execute"))
        database.execute("INSERT INTO events VALUES (?,?,?,?,?)", (
            run_id, 1, "backend.notice", "then", json.dumps({
                "source": "team_followup" if lane == "team_followup" else "service",
                "message": "Retain fixture service evidence", "team_id": "team"})))
    database.execute("UPDATE assistant_delegations SET team_id='team' WHERE id='delegation'")
    database.execute(
        "INSERT INTO assistant_followups VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("team", "delegation", "project", "session", "run", "automatic-followup-request",
         '{"backend":"openai_chatgpt","model":"direct-fixture-model",'
         '"settings":{"reasoning_effort":"high"},"permission":"trusted_host"}',
         "completed", "lane-run-2", None, "then", "later"))
    database.execute("INSERT INTO settings VALUES (?,?)",
                     ("fixture-v11", "Preserve committed WAL and exact original lane scope"))
    assert database.execute("PRAGMA user_version").fetchone()[0] == 11
    assert not database.execute("PRAGMA foreign_key_check").fetchall()
    return database


def test_v11_full_access_upgrade_retains_every_row_and_only_rebuilds_lane_constraint(tmp_path):
    path = tmp_path / "v11.sqlite3"
    original = v11_database(path)
    before = all_table_records(original)
    before_schema = schema(original)
    before_backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    assert len(before["run_lanes"]) == 3 and len(before["assistant_followups"]) == 1
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        original.execute("UPDATE run_lanes SET effective_permission='full_access' "
                         "WHERE run_id='lane-run-2'")

    upgraded = Store(path)
    try:
        assert {name: rows for name, rows in all_table_records(upgraded.db).items() if name not in V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES} == before
        assert all(all_table_records(upgraded.db)[name] == [] for name in V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        after_schema = schema(upgraded.db)
        assert set(before_schema) <= set(after_schema)
        assert {name for kind, name in set(after_schema) - set(before_schema) if kind == "table"} == set(V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        assert {name for name in before_schema if after_schema[name] != before_schema[name]} == {
            ("table", "run_lanes")}
        assert "full_access" in after_schema[("table", "run_lanes")]
        assert "full_access" not in before_schema[("table", "run_lanes")]
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert upgraded.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not upgraded.db.execute("PRAGMA foreign_key_check").fetchall()
        assert upgraded.one("sessions", "session")["settings"] == {
            "reasoning_effort": "high", "openai_account_id": "fixture-account"}
        assert upgraded.run_lane("lane-run-2")["effective_permission"] == "trusted_host"
        upgraded.db.execute("BEGIN IMMEDIATE")
        upgraded.db.execute("UPDATE run_lanes SET effective_permission='full_access' "
                            "WHERE run_id='lane-run-2'")
        assert upgraded.run_lane("lane-run-2")["effective_permission"] == "full_access"
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            upgraded.db.execute("UPDATE run_lanes SET effective_permission='invalid'")
        upgraded.db.execute("ROLLBACK")
        assert {name: rows for name, rows in all_table_records(upgraded.db).items() if name not in V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES} == before
        assert all(all_table_records(upgraded.db)[name] == [] for name in V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
    finally:
        upgraded.close()
        original.close()

    added = set(tmp_path.glob("*.pre-v16-*.bak")) - before_backups
    assert len(added) == 1
    backup = added.pop()
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 11
        assert all_table_records(database) == before
        assert schema(database) == before_schema
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not database.execute("PRAGMA foreign_key_check").fetchall()
    reopened = Store(path)
    assert {name: rows for name, rows in all_table_records(reopened.db).items() if name not in V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES} == before
    assert all(all_table_records(reopened.db)[name] == [] for name in V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
    reopened.close()
    assert set(tmp_path.glob("*.pre-v16-*.bak")) - before_backups == {backup}


def test_v11_collision_keeps_original_lanes_followups_and_private_backup(tmp_path):
    path = tmp_path / "v11-collision.sqlite3"
    original = v11_database(path)
    original.execute("CREATE TABLE run_lanes_v12 (sentinel TEXT)")
    original.execute("INSERT INTO run_lanes_v12 VALUES ('Retain collision evidence')")
    before = all_table_records(original)
    before_schema = schema(original)
    before_backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    try:
        with pytest.raises(sqlite3.OperationalError, match="run_lanes_v12 already exists"):
            Store(path)
        assert original.execute("PRAGMA user_version").fetchone()[0] == 11
        assert all_table_records(original) == before
        assert schema(original) == before_schema
        assert original.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not original.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        original.close()
    added = set(tmp_path.glob("*.pre-v16-*.bak")) - before_backups
    assert len(added) == 1
    backup = added.pop()
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 11
        assert all_table_records(database) == before
        assert schema(database) == before_schema


def test_v11_reader_refuses_current_and_can_open_original_private_backup(tmp_path, monkeypatch):
    path = tmp_path / "v11-reader.sqlite3"
    original = v11_database(path)
    before = all_table_records(original)
    original.close()
    before_backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    Store(path).close()
    added = set(tmp_path.glob("*.pre-v16-*.bak")) - before_backups
    assert len(added) == 1
    backup = added.pop()
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 11)
    with pytest.raises(KiraError, match="newer") as error:
        Store(path)
    assert error.value.code == "INCOMPATIBLE_SCHEMA"
    restored = Store(backup)
    assert restored.db.execute("PRAGMA user_version").fetchone()[0] == 11
    assert all_table_records(restored.db) == before
    assert restored.run_lane("lane-run-2")["effective_permission"] == "trusted_host"
    restored.close()
