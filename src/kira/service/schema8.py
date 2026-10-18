"""Browser actors outlive individual turns; process ownership remains durable."""

BROWSER_SCHEMA = """
CREATE TABLE browser_sessions (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 project_id TEXT NOT NULL REFERENCES projects(id), generation INTEGER NOT NULL,
 account_id TEXT NOT NULL, persistent INTEGER NOT NULL,
 state TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0,
 last_run_id TEXT REFERENCES runs(id), error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX browser_session_owner ON browser_sessions(session_id,state);
CREATE TABLE browser_processes (
 run_id TEXT PRIMARY KEY REFERENCES browser_sessions(id),
 pid INTEGER NOT NULL, uid INTEGER NOT NULL, boot_id TEXT NOT NULL,
 start_ticks TEXT NOT NULL, pgid INTEGER NOT NULL, sid INTEGER NOT NULL,
 store_id TEXT NOT NULL, state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE browser_process_members (
 run_id TEXT NOT NULL REFERENCES browser_processes(run_id), pid INTEGER NOT NULL,
 identity TEXT NOT NULL, PRIMARY KEY(run_id,pid)
);
"""
