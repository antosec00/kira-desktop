"""Actual SQLite v1 data fixtures, not a mocked migration or native-provider test."""

import os
import sqlite3

import pytest

from kira.domain.contracts import KiraError
from kira.service import storage
from kira.service.storage import Store

V1_SCHEMA = """
CREATE TABLE projects (id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT NOT NULL,
 instructions TEXT NOT NULL,permission TEXT NOT NULL,memory TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE sessions (id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
 backend TEXT NOT NULL,model TEXT NOT NULL,settings TEXT NOT NULL,backend_ref TEXT,created_at TEXT NOT NULL);
CREATE TABLE runs (id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
 request_id TEXT NOT NULL UNIQUE,prompt TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,error TEXT,outcome TEXT,last_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE events (run_id TEXT NOT NULL REFERENCES runs(id),seq INTEGER NOT NULL,type TEXT NOT NULL,
 timestamp TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,seq));
CREATE TABLE artifacts (id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES runs(id),path TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT,
 tool_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE INDEX sessions_project ON sessions(project_id);
CREATE INDEX runs_session ON runs(session_id,created_at);
CREATE INDEX artifacts_project ON artifacts(project_id);
PRAGMA user_version=1;
"""
OLD_TABLES = ("projects", "sessions", "runs", "events", "artifacts", "settings")


def v1_database(path):
    database = sqlite3.connect(path, isolation_level=None)
    database.executescript(V1_SCHEMA)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA wal_autocheckpoint=0")
    database.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
        (
            "project",
            "Migration fixture",
            "/fixture-only",
            "Keep instructions",
            "project_write",
            '{"objective":"keep memory"}',
            "then",
            "then",
        ),
    )
    database.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
        (
            "session",
            "project",
            "codex",
            "selected-model",
            '{"reasoning_effort":"high"}',
            "fixture-backend-reference",
            "then",
        ),
    )
    database.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "run",
            "session",
            "original-request",
            "Original prompt",
            "completed",
            "then",
            "then",
            None,
            '{"evidence":"original"}',
            1,
        ),
    )
    database.execute(
        "INSERT INTO events VALUES (?,?,?,?,?)",
        ("run", 1, "message.completed", "then", '{"content":"Original answer"}'),
    )
    database.execute(
        "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
        (
            "artifact",
            "project",
            "run",
            "/fixture-only/result",
            "project_file",
            "abc",
            "tool",
            "then",
        ),
    )
    database.execute("INSERT INTO settings VALUES (?,?)", ("tool_timeout", "75"))
    return database


def records(database):
    return {
        table: [tuple(row) for row in database.execute(f"SELECT * FROM {table}")]
        for table in OLD_TABLES
    }


def test_migrate_populated_v1_preserves_rows_and_consistent_private_wal_backup(tmp_path):
    path = tmp_path / "kira.sqlite3"
    old = v1_database(path)
    before = records(old)
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    store = Store(path)
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert records(store.db) == before
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        backups = list(tmp_path.glob("*.pre-v16-*.bak"))
        assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
            assert records(backup) == before
        assert store.events("run")["events"][0]["schema_version"] == 1
        assert (
            store.event("run", "backend.notice", {"message": "after migration"})["schema_version"]
            == 1
        )
        new, emitted = store.create_run("session", "new-request", "new prompt")
        assert {event["schema_version"] for event in emitted} == {1}
        assert all(event["schema_version"] == 1 for event in store.events(new["id"])["events"])
    finally:
        store.close()
        old.close()
    reopened = Store(path)
    reopened.close()
    assert len(list(tmp_path.glob("*.pre-v16-*.bak"))) == 1


def test_failed_migration_rolls_back_every_addition_and_preserves_v1(tmp_path):
    path = tmp_path / "broken.sqlite3"
    old = v1_database(path)
    before = records(old)
    old.execute("CREATE TABLE file_watches (unexpected TEXT)")
    old.close()
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 1
        assert records(database) == before
        assert (
            database.execute("SELECT name FROM sqlite_master WHERE name='workspaces'").fetchone()
            is None
        )
    assert len(list(tmp_path.glob("*.pre-v16-*.bak"))) == 1


def test_old_schema_reader_refuses_v2_and_retained_backup_is_v1(tmp_path, monkeypatch):
    path = tmp_path / "kira.sqlite3"
    v1_database(path).close()
    Store(path).close()
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 1)
    with pytest.raises(KiraError, match="newer"):
        Store(path)
    old = Store(next(tmp_path.glob("*.bak")))
    assert old.one("projects", "project")["memory"] == {"objective": "keep memory"}
    old.close()


def test_backup_is_private_before_sqlite_opens_it(tmp_path, monkeypatch):
    store = Store(tmp_path / "new.sqlite3")
    connect = sqlite3.connect
    target = tmp_path / "snapshot.sqlite3"

    def checked_connect(path, *args, **kwargs):
        assert path == target
        assert os.stat(path).st_mode & 0o777 == 0o600
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(storage.sqlite3, "connect", checked_connect)
    store.backup(target)
    store.close()


V2_WORKSPACE_SCHEMA = """
CREATE TABLE workspaces (project_id TEXT PRIMARY KEY REFERENCES projects(id),tools TEXT NOT NULL,
 next_step TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE file_watches (project_id TEXT NOT NULL REFERENCES projects(id),path TEXT NOT NULL,
 sha256 TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(project_id,path));
CREATE TABLE desktop_requests (request_id TEXT PRIMARY KEY,method TEXT NOT NULL,fingerprint TEXT NOT NULL,
 project_id TEXT REFERENCES projects(id),status TEXT NOT NULL,result TEXT NOT NULL,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE captures (id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),path TEXT NOT NULL,
 sha256 TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX captures_project ON captures(project_id,created_at);
PRAGMA user_version=2;
"""
V2_TABLES = OLD_TABLES + ("workspaces", "file_watches", "desktop_requests", "captures")


def v2_database(path):
    old = v1_database(path)
    old.executescript(V2_WORKSPACE_SCHEMA)
    old.execute(
        "INSERT INTO workspaces VALUES (?,?,?,?)",
        ("project", '["terminal"]', "Retained next step", "then"),
    )
    old.execute(
        "INSERT INTO file_watches VALUES (?,?,?,?)",
        ("project", "result.txt", "fixture-sha", "then"),
    )
    old.execute(
        "INSERT INTO desktop_requests VALUES (?,?,?,?,?,?,?,?)",
        (
            "desktop-request",
            "workspace.open",
            "fingerprint",
            "project",
            "completed",
            "{}",
            "then",
            "then",
        ),
    )
    old.execute(
        "INSERT INTO captures VALUES (?,?,?,?,?)",
        ("capture", "project", "capture.png", "fixture-sha", "then"),
    )
    return old


def v2_records(database):
    return {
        table: list(map(tuple, database.execute(f"SELECT * FROM {table}"))) for table in V2_TABLES
    }


def test_v2_migration_preserves_ten_tables_and_old_reader_backup(tmp_path, monkeypatch):
    path = tmp_path / "v2.sqlite3"
    old = v2_database(path)
    before = v2_records(old)
    old_schema = dict(old.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    assert path.with_name(path.name + "-wal").stat().st_size
    store = Store(path)
    assert v2_records(store.db) == before
    new_schema = dict(store.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    assert all(new_schema[table] == old_schema[table] for table in V2_TABLES)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 16
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    store.close()
    old.close()
    backups = list(tmp_path.glob("*.pre-v16-*.bak"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
        assert v2_records(backup) == before
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 2)
    with pytest.raises(KiraError, match="newer"):
        Store(path)
    restored = Store(backups[0])
    assert v2_records(restored.db) == before
    restored.close()


def test_v2_failed_migration_rolls_back_additions_and_ten_tables(tmp_path):
    path = tmp_path / "collision.sqlite3"
    old = v2_database(path)
    before = v2_records(old)
    old.execute("CREATE TABLE team_steps (unexpected TEXT)")
    old.close()
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as retained:
        assert retained.execute("PRAGMA user_version").fetchone()[0] == 2
        assert v2_records(retained) == before
        assert (
            retained.execute("SELECT name FROM sqlite_master WHERE name='team_runs'").fetchone()
            is None
        )


V4_TABLES = ("control_process_members", "control_processes", "steering_requests", "queue_controls", "prompt_queue", "run_modes",
             "session_forks", "session_goals", "session_workbench")
V6_TABLES = ("queue_image_attachments", "run_image_attachments", "image_attachments",
             "run_provider_bindings", "session_provider_switches", "session_provider_generations")
V13_TABLES = ("goal_tool_calls", "goal_commands", "goal_cycles", "autonomous_goals")
V16_TABLES = ("studio_agent_commits", "studio_grants")
V15_TABLES = ("studio_operations", "studio_revisions", "studio_worlds")
V14_TABLES = ("soul_tool_calls", "soul_operations", "soul_revision_memories", "soul_memories",
              "soul_proposals", "soul_sources", "soul_run_snapshots", "soul_bindings",
              "soul_revisions", "souls")
V11_TABLES = ("assistant_followups", "run_lanes")
V10_TABLES = ("project_tool_process_members", "project_tool_processes", "project_tool_operations",
              "project_tool_mutations", "project_tool_revisions", "project_tools")
V9_TABLES = ("research_operations", "research_settings", "research_revisions", "research_records")
V8_TABLES = ("browser_process_members", "browser_processes", "browser_sessions")
V7_TABLES = ("direct_context_reservations", "direct_context_operations", "direct_context_states",
             "direct_context_checkpoints", "chat_context_notes", "chat_history_sources", "chat_history_scans")


def v3_database(path):
    """Populate the preserved schema-3 tables, removing only new fixture tables."""
    old = v2_database(path)
    old.close()
    store = Store(path)
    store.close()
    database = sqlite3.connect(path, isolation_level=None)
    for table in (*V16_TABLES, *V15_TABLES, *V14_TABLES, *V13_TABLES, *V11_TABLES, *V10_TABLES, *V9_TABLES, *V8_TABLES, *V7_TABLES, *V6_TABLES, "assistant_calls", "assistant_delegations", *V4_TABLES):
        database.execute(f'DROP TABLE "{table}"')
    database.execute("PRAGMA user_version=3")
    return database


def test_populated_v3_upgrade_preserves_every_table_and_creates_original_schema_backup(tmp_path):
    path = tmp_path / "v3.sqlite3"
    old = v3_database(path)
    names = [row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    before = {name: list(old.execute(f'SELECT * FROM "{name}"')) for name in names}
    old.close()
    backups_before = set(tmp_path.glob("*.bak"))
    store = Store(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 16
    assert {name: list(map(tuple, store.db.execute(f'SELECT * FROM "{name}"'))) for name in names} == before
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    store.close()
    backups = set(tmp_path.glob("*.bak")) - backups_before
    assert len(backups) == 1
    with sqlite3.connect(backups.pop()) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 3
        assert {name: list(backup.execute(f'SELECT * FROM "{name}"')) for name in names} == before


def test_v3_failed_upgrade_rolls_back_all_new_tables(tmp_path):
    path = tmp_path / "collision-v3.sqlite3"
    old = v3_database(path)
    old.execute("CREATE TABLE session_goals (collision TEXT)")
    old.close()
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 3
        assert not database.execute("SELECT 1 FROM sqlite_master WHERE name='session_workbench'").fetchone()
        assert database.execute("SELECT model FROM sessions WHERE id='session'").fetchone()[0] == "selected-model"


def v4_database(path):
    old = v3_database(path)
    old.close()
    Store(path).close()
    database = sqlite3.connect(path, isolation_level=None)
    for table in (*V16_TABLES, *V15_TABLES, *V14_TABLES, *V13_TABLES, *V11_TABLES, *V10_TABLES, *V9_TABLES, *V8_TABLES, *V7_TABLES, *V6_TABLES):
        database.execute(f'DROP TABLE "{table}"')
    database.execute("DROP TABLE assistant_calls")
    database.execute("DROP TABLE assistant_delegations")
    database.execute("INSERT INTO session_goals VALUES ('session','Synthetic persistent goal','active','before','before')")
    database.execute("INSERT INTO queue_controls VALUES ('project',1,'before')")
    database.execute("PRAGMA user_version=4")
    return database


def test_v4_upgrade_retains_all_existing_rows_and_original_backup(tmp_path):
    path = tmp_path / "v4.sqlite3"
    previous = v4_database(path)
    tables = [row[0] for row in previous.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    before = {name: list(previous.execute(f'SELECT * FROM "{name}"')) for name in tables}
    previous.close()
    backups_before = set(tmp_path.glob("*.bak"))
    store = Store(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 16
    assert {name: list(map(tuple, store.db.execute(f'SELECT * FROM "{name}"'))) for name in tables} == before
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    assert not store.all("SELECT * FROM assistant_delegations")
    store.close()
    backups = set(tmp_path.glob("*.bak")) - backups_before
    assert len(backups) == 1
    with sqlite3.connect(backups.pop()) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 4
        assert {name: list(backup.execute(f'SELECT * FROM "{name}"')) for name in tables} == before


def test_v4_failed_upgrade_preserves_old_schema_and_data(tmp_path):
    path = tmp_path / "v4-conflict.sqlite3"
    previous = v4_database(path)
    previous.execute("CREATE TABLE assistant_calls (collision TEXT)")
    previous.close()
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not database.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_delegations'").fetchone()
        assert database.execute("SELECT objective FROM session_goals").fetchone()[0] == "Synthetic persistent goal"


def v5_database(path):
    previous = v4_database(path)
    previous.close()
    Store(path).close()
    database = sqlite3.connect(path, isolation_level=None)
    database.execute("PRAGMA foreign_keys=ON")
    for table in (*V16_TABLES, *V15_TABLES, *V14_TABLES, *V13_TABLES, *V11_TABLES, *V10_TABLES, *V9_TABLES, *V8_TABLES, *V7_TABLES, *V6_TABLES):
        database.execute(f'DROP TABLE "{table}"')
    database.execute("INSERT INTO assistant_delegations "
        "(id,origin_run_id,project_id,session_id,call_id,fingerprint,config,state,summary,created_at,updated_at) "
        "VALUES ('delegation','run','project','session','call','fingerprint',"
        "'{\"goal\":\"Retain completed team context\"}','completed','Retain summary','then','then')")
    database.execute("INSERT INTO assistant_calls VALUES "
        "('run','call','team_delegate','fingerprint','{\"ok\":true,\"delegation_id\":\"delegation\"}')")
    database.execute("INSERT INTO session_workbench VALUES "
        "('session',0,NULL,NULL,NULL,'Retain copied context','then','then')")
    database.execute("INSERT INTO run_modes VALUES ('run','review')")
    database.execute("INSERT INTO prompt_queue VALUES "
        "('queue','project','session','queued-request','Retain pending request','plan',0,"
        "'pending',NULL,NULL,'then','then')")
    database.execute("INSERT INTO steering_requests VALUES "
        "('steer-request','run','Retain accepted steering','accepted','{\"accepted\":true}','then','then')")
    database.execute("PRAGMA user_version=5")
    return database


def all_table_records(database):
    names = [row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: list(map(tuple, database.execute(f'SELECT * FROM "{name}" ORDER BY rowid')))
            for name in names}


def test_populated_v5_upgrade_preserves_all_tables_and_original_private_backup(tmp_path):
    path = tmp_path / "v5.sqlite3"
    previous = v5_database(path)
    before = all_table_records(previous)
    old_schema = dict(previous.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    previous.close()
    backups_before = set(tmp_path.glob("*.pre-v16-*.bak"))
    upgraded = Store(path)
    try:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        after = all_table_records(upgraded.db)
        assert {name: after[name] for name in before} == before
        assert set(after) - set(before) == set(V6_TABLES + V7_TABLES + V8_TABLES + V9_TABLES + V10_TABLES + V11_TABLES + V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        new_schema = dict(upgraded.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        assert {name: new_schema[name] for name in old_schema} == old_schema
        assert upgraded.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert upgraded.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert upgraded.one("sessions", "session")["backend_ref"] == "fixture-backend-reference"
        assert upgraded.one("sessions", "session")["settings"] == {"reasoning_effort": "high"}
    finally:
        upgraded.close()
    backups = set(tmp_path.glob("*.pre-v16-*.bak")) - backups_before
    assert len(backups) == 1
    backup_path = backups.pop()
    assert backup_path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 5
        assert all_table_records(backup) == before
        assert backup.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v5_failed_upgrade_rolls_back_every_new_table_and_preserves_original_backup(tmp_path):
    path = tmp_path / "v5-conflict.sqlite3"
    previous = v5_database(path)
    # Fail the final schema-6 table, after all other additions were attempted.
    previous.execute("CREATE TABLE queue_image_attachments (collision TEXT)")
    previous.execute("INSERT INTO queue_image_attachments VALUES ('Retain colliding table')")
    before = all_table_records(previous)
    old_schema = dict(previous.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    previous.close()
    backups_before = set(tmp_path.glob("*.pre-v16-*.bak"))
    with pytest.raises(sqlite3.OperationalError, match="queue_image_attachments"):
        Store(path)
    with sqlite3.connect(path) as retained:
        assert retained.execute("PRAGMA user_version").fetchone()[0] == 5
        assert all_table_records(retained) == before
        assert dict(retained.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")) == old_schema
        assert retained.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backups = set(tmp_path.glob("*.pre-v16-*.bak")) - backups_before
    assert len(backups) == 1
    with sqlite3.connect(backups.pop()) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 5
        assert all_table_records(backup) == before


def v6_database(path):
    """A previous release with provider segments, images, queue and team evidence."""
    previous = v5_database(path)
    previous.close()
    Store(path).close()
    database = sqlite3.connect(path, isolation_level=None)
    database.execute("PRAGMA foreign_keys=ON")
    for table in (*V16_TABLES, *V15_TABLES, *V14_TABLES, *V13_TABLES, *V11_TABLES, *V10_TABLES, *V9_TABLES, *V8_TABLES, *V7_TABLES):
        database.execute(f'DROP TABLE "{table}"')
    database.execute("INSERT INTO session_provider_generations VALUES "
        "('session',0,'codex','selected-model','{\"reasoning_effort\":\"high\"}',"
        "'original-native-reference','before','later')")
    database.execute("INSERT INTO session_provider_generations VALUES "
        "('session',1,'openai_chatgpt','direct-fixture-model',"
        "'{\"reasoning_effort\":\"high\",\"openai_account_id\":\"fixture-account\"}',NULL,'later',NULL)")
    database.execute("UPDATE sessions SET backend='openai_chatgpt',model='direct-fixture-model',"
        "settings='{\"reasoning_effort\":\"high\",\"openai_account_id\":\"fixture-account\"}',backend_ref=NULL WHERE id='session'")
    database.execute("INSERT INTO run_provider_bindings VALUES "
        "('run','session',0,'codex','selected-model','{\"reasoning_effort\":\"high\"}',"
        "'original-native-reference','before')")
    database.execute("INSERT INTO session_provider_switches VALUES "
        "('switch-request','stable-fingerprint','project','session',1,"
        "'{\"generation\":1,\"changed\":true}','later')")
    database.execute("INSERT INTO image_attachments VALUES "
        "('00000000-0000-4000-8000-000000000001',"
        "'{\"id\":\"00000000-0000-4000-8000-000000000001\",\"name\":\"fixture.png\","
        "\"mime\":\"image/png\",\"bytes\":100,\"width\":8,\"height\":8,"
        "\"sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}', 'later')")
    database.execute("INSERT INTO run_image_attachments VALUES ('run','00000000-0000-4000-8000-000000000001',0)")
    database.execute("INSERT INTO queue_image_attachments VALUES ('queue','00000000-0000-4000-8000-000000000001',0)")
    database.execute("PRAGMA user_version=6")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA wal_autocheckpoint=0")
    database.execute("INSERT INTO settings VALUES ('fixture-v6','Retain committed WAL content')")
    return database


def test_populated_v6_upgrade_preserves_every_original_schema_row_and_consistent_backup(tmp_path):
    path = tmp_path / "v6.sqlite3"
    previous = v6_database(path)
    before = all_table_records(previous)
    old_schema = dict(previous.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    backups_before = set(tmp_path.glob("*.pre-v16-*.bak"))
    assert path.with_name(path.name + "-wal").stat().st_size
    upgraded = Store(path)
    try:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        after = all_table_records(upgraded.db)
        assert {name: after[name] for name in before} == before
        assert set(after) - set(before) == set(V7_TABLES + V8_TABLES + V9_TABLES + V10_TABLES + V11_TABLES + V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        new_schema = dict(upgraded.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        assert {name: new_schema[name] for name in old_schema} == old_schema
        assert upgraded.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert upgraded.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert upgraded.one("sessions", "session")["settings"] == {
            "reasoning_effort": "high", "openai_account_id": "fixture-account"}
    finally:
        upgraded.close()
        previous.close()
    backups = set(tmp_path.glob("*.pre-v16-*.bak")) - backups_before
    assert len(backups) == 1
    saved = backups.pop()
    assert saved.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(saved) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 6
        assert all_table_records(backup) == before
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("PRAGMA foreign_key_check").fetchall() == []
    Store(path).close()
    assert set(tmp_path.glob("*.pre-v16-*.bak")) - backups_before == {saved}


def test_v6_failed_upgrade_keeps_original_data_and_rolls_back_all_context_tables(tmp_path):
    path = tmp_path / "v6-collision.sqlite3"
    previous = v6_database(path)
    previous.execute("CREATE TABLE chat_history_scans (collision TEXT)")
    previous.execute("INSERT INTO chat_history_scans VALUES ('Keep colliding original')")
    before = all_table_records(previous)
    old_schema = dict(previous.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    previous.close()
    backups_before = set(tmp_path.glob("*.pre-v16-*.bak"))
    with pytest.raises(sqlite3.OperationalError, match="chat_history_scans"):
        Store(path)
    with sqlite3.connect(path) as retained:
        assert retained.execute("PRAGMA user_version").fetchone()[0] == 6
        assert all_table_records(retained) == before
        assert dict(retained.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")) == old_schema
    backups = set(tmp_path.glob("*.pre-v16-*.bak")) - backups_before
    assert len(backups) == 1
    with sqlite3.connect(backups.pop()) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 6
        assert all_table_records(backup) == before


def v7_database(path):
    previous = v6_database(path)
    previous.close()
    Store(path).close()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    for table in (*V16_TABLES, *V15_TABLES, *V14_TABLES, *V13_TABLES, *V11_TABLES, *V10_TABLES, *V9_TABLES, *V8_TABLES):
        db.execute(f'DROP TABLE "{table}"')
    db.execute("INSERT INTO chat_context_notes VALUES "
               "('original-note','session','Keep original note','before','before')")
    db.execute("INSERT INTO direct_context_states VALUES "
               "('session',1,'fixture-account','direct-fixture-model',1,"
               "'[{\"type\":\"reasoning\",\"encrypted_content\":\"synthetic-opaque-context\"}]',"
               "NULL,'[]','{}','run','response_complete','before')")
    db.execute("PRAGMA user_version=7")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("INSERT INTO settings VALUES ('fixture-v7','Retain committed browser migration input')")
    return db


def test_v7_browser_migration_preserves_all_existing_rows_and_original_backup(tmp_path):
    path = tmp_path / "v7.sqlite3"
    original = v7_database(path)
    before = all_table_records(original)
    schema = dict(original.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    upgraded = Store(path)
    try:
        after = all_table_records(upgraded.db)
        assert {key: after[key] for key in before} == before
        assert set(after) - set(before) == set(V8_TABLES + V9_TABLES + V10_TABLES + V11_TABLES + V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        new_schema = dict(upgraded.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        assert {key: new_schema[key] for key in schema} == schema
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert upgraded.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not upgraded.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        upgraded.close()
        original.close()
    new_backups = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(new_backups) == 1
    with sqlite3.connect(new_backups.pop()) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 7
        assert all_table_records(backup) == before


def test_v7_failed_browser_migration_is_transactional(tmp_path):
    path = tmp_path / "v7-collision.sqlite3"
    original = v7_database(path)
    original.execute("CREATE TABLE browser_process_members (original TEXT)")
    original.execute("INSERT INTO browser_process_members VALUES ('Preserve original collision')")
    before = all_table_records(original)
    original.close()
    with pytest.raises(sqlite3.OperationalError, match="browser_process_members"):
        Store(path)
    with sqlite3.connect(path) as retained:
        assert retained.execute("PRAGMA user_version").fetchone()[0] == 7
        assert all_table_records(retained) == before


def v8_database(path):
    """Released baseline with browser records and durable native context."""
    previous = v7_database(path)
    previous.close()
    Store(path).close()
    database = sqlite3.connect(path, isolation_level=None)
    database.execute("PRAGMA foreign_keys=ON")
    for table in (*V16_TABLES, *V15_TABLES, *V14_TABLES, *V13_TABLES, *V11_TABLES, *V10_TABLES, *V9_TABLES):
        database.execute(f'DROP TABLE "{table}"')
    database.execute("INSERT INTO browser_sessions VALUES "
        "('fixture-browser','session','project',1,'fixture-account',1,'closed',0,'run',NULL,'before','later')")
    database.execute("INSERT INTO browser_processes VALUES "
        "('fixture-browser',12345,1000,'fixture-boot','fixture-ticks',12345,12345,"
        "'fixture-store','closed',NULL,'before')")
    database.execute("INSERT INTO browser_process_members VALUES "
        "('fixture-browser',12345,'{\"pid\":12345,\"state\":\"closed\"}')")
    database.execute("PRAGMA user_version=8")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA wal_autocheckpoint=0")
    database.execute("INSERT INTO settings VALUES ('fixture-v8','Retain original committed research migration input')")
    return database


def test_v8_research_migration_preserves_all_original_tables_and_private_wal_backup(tmp_path):
    path = tmp_path / "v8.sqlite3"
    original = v8_database(path)
    before = all_table_records(original)
    schema = dict(original.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    upgraded = Store(path)
    try:
        after = all_table_records(upgraded.db)
        assert {key: after[key] for key in before} == before
        assert set(after) - set(before) == set(V9_TABLES + V10_TABLES + V11_TABLES + V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        assert all(after[table] == [] for table in V9_TABLES)
        new_schema = dict(upgraded.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        assert {key: new_schema[key] for key in schema} == schema
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert upgraded.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not upgraded.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        upgraded.close()
        original.close()
    new_backups = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(new_backups) == 1
    snapshot = new_backups.pop()
    assert snapshot.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(snapshot) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 8
        assert all_table_records(backup) == before
        assert not backup.execute("PRAGMA foreign_key_check").fetchall()
    Store(path).close()
    assert set(tmp_path.glob("*.pre-v16-*.bak")) - backups == {snapshot}


def test_v8_research_migration_collision_rolls_back_all_additions_and_keeps_backup(tmp_path):
    path = tmp_path / "v8-collision.sqlite3"
    original = v8_database(path)
    original.execute("CREATE TABLE research_settings (original TEXT)")
    original.execute("INSERT INTO research_settings VALUES ('Keep this preexisting table')")
    before = all_table_records(original)
    schema = dict(original.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    original.close()
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    with pytest.raises(sqlite3.OperationalError, match="research_settings"):
        Store(path)
    with sqlite3.connect(path) as retained:
        assert retained.execute("PRAGMA user_version").fetchone()[0] == 8
        assert all_table_records(retained) == before
        assert dict(retained.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")) == schema
        assert retained.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    new_backups = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(new_backups) == 1
    with sqlite3.connect(new_backups.pop()) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 8
        assert all_table_records(backup) == before


def v9_database(path):
    previous = v8_database(path)
    previous.close()
    Store(path).close()
    database = sqlite3.connect(path, isolation_level=None)
    database.execute("PRAGMA foreign_keys=ON")
    for table in (*V16_TABLES, *V15_TABLES, *V14_TABLES, *V13_TABLES, *V11_TABLES, *V10_TABLES):
        database.execute(f'DROP TABLE "{table}"')
    database.execute("INSERT INTO research_records VALUES "
        "('fixture-record','project',1,'finding','draft','unverified',1,"
        "'Synthetic evidence','Original fixture body','{}','then','then')")
    database.execute("INSERT INTO research_revisions VALUES "
        "('fixture-record',1,'{}','user','Original fixture reason','then')")
    database.execute("INSERT INTO research_operations VALUES "
        "('fixture-receipt','project','create','fingerprint','fixture-record','{}','then')")
    database.execute("INSERT INTO research_settings VALUES ('project',1,1,'then')")
    database.execute("PRAGMA user_version=9")
    return database


def test_v9_project_tools_migration_preserves_all_original_rows_and_consistent_backup(tmp_path):
    path = tmp_path / "v9.sqlite3"
    original = v9_database(path)
    before = all_table_records(original)
    schemas = dict(original.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    upgraded = Store(path)
    try:
        after = all_table_records(upgraded.db)
        assert {name: after[name] for name in before} == before
        assert set(after) - set(before) == set(V10_TABLES + V11_TABLES + V13_TABLES + V14_TABLES + V15_TABLES + V16_TABLES)
        assert all(after[name] == [] for name in V10_TABLES)
        current = dict(upgraded.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        assert all(current[name] == ddl for name, ddl in schemas.items())
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert upgraded.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not upgraded.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        upgraded.close()
        original.close()
    added = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(added) == 1
    snapshot = added.pop()
    assert snapshot.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(snapshot) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 9
        assert all_table_records(backup) == before
    Store(path).close()
    assert set(tmp_path.glob("*.pre-v16-*.bak")) - backups == {snapshot}


def test_v9_project_tools_migration_failure_rolls_back_and_retains_original_backup(tmp_path):
    path = tmp_path / "v9-collision.sqlite3"
    original = v9_database(path)
    original.execute("CREATE TABLE project_tool_mutations (original TEXT)")
    original.execute("INSERT INTO project_tool_mutations VALUES ('Retain fixture collision')")
    before = all_table_records(original)
    backups = set(tmp_path.glob("*.pre-v16-*.bak"))
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Store(path)
    assert original.execute("PRAGMA user_version").fetchone()[0] == 9
    assert all_table_records(original) == before
    original.close()
    added = set(tmp_path.glob("*.pre-v16-*.bak")) - backups
    assert len(added) == 1
    with sqlite3.connect(added.pop()) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 9
        assert all_table_records(backup) == before
