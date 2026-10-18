"""Durable chat-to-team handoffs and correlated assistant operation results."""

ASSISTANT_SCHEMA = """
CREATE TABLE assistant_delegations (
 id TEXT PRIMARY KEY,
 origin_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT NOT NULL REFERENCES sessions(id),
 call_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 config TEXT NOT NULL,
 team_id TEXT REFERENCES team_runs(id),
 state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed','cancelled','interrupted','paused')),
 summary TEXT NOT NULL DEFAULT '', error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX assistant_delegations_session ON assistant_delegations(session_id,created_at);
CREATE INDEX assistant_delegations_team ON assistant_delegations(team_id);
CREATE TABLE assistant_calls (
 run_id TEXT NOT NULL REFERENCES runs(id), call_id TEXT NOT NULL,
 name TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(run_id,call_id)
);
"""
