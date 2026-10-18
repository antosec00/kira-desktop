"""Additive chat controls; existing history and provider references stay intact."""

WORKBENCH_SCHEMA = """
CREATE TABLE session_workbench (
 session_id TEXT PRIMARY KEY REFERENCES sessions(id),
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
 parent_session_id TEXT REFERENCES sessions(id), source_run_id TEXT REFERENCES runs(id),
 fork_mode TEXT, source_context TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE session_goals (
 session_id TEXT PRIMARY KEY REFERENCES sessions(id), objective TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','paused','completed')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE session_forks (
 request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
 project_id TEXT NOT NULL REFERENCES projects(id),
 source_session_id TEXT NOT NULL REFERENCES sessions(id), root TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','completed','failed','unknown')),
 result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 method TEXT NOT NULL DEFAULT 'session.fork', error TEXT,
 cleanup_blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE run_modes (
 run_id TEXT PRIMARY KEY REFERENCES runs(id),
 mode TEXT NOT NULL CHECK(mode IN ('execute','plan','review','recap'))
);
CREATE TABLE prompt_queue (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT NOT NULL REFERENCES sessions(id), request_id TEXT NOT NULL UNIQUE,
 prompt TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('execute','plan','review','recap')),
 position INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','starting','running','completed','failed','cancelled','interrupted','removed')),
 run_id TEXT REFERENCES runs(id), error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX prompt_queue_order ON prompt_queue(project_id,status,position);
CREATE TABLE queue_controls (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), paused INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE TABLE steering_requests (
 request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), prompt TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','unknown')),
 result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE control_processes (
 run_id TEXT PRIMARY KEY REFERENCES session_forks(request_id),
 pid INTEGER NOT NULL, uid INTEGER NOT NULL, boot_id TEXT NOT NULL,
 start_ticks TEXT NOT NULL, pgid INTEGER NOT NULL, sid INTEGER NOT NULL,
 store_id TEXT NOT NULL, state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE control_process_members (
 run_id TEXT NOT NULL REFERENCES control_processes(run_id), pid INTEGER NOT NULL,
 identity TEXT NOT NULL, PRIMARY KEY(run_id,pid)
);
"""
