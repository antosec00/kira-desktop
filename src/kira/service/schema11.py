"""Read-only conversations alongside Teams and durable completion followups."""

LIVE_CONVERSATION_SCHEMA = """
CREATE TABLE run_lanes (
 run_id TEXT PRIMARY KEY REFERENCES runs(id),
 lane TEXT NOT NULL CHECK(lane IN ('team_conversation','team_followup')),
 team_id TEXT NOT NULL REFERENCES team_runs(id),
 effective_permission TEXT NOT NULL CHECK(effective_permission IN ('read_only','project_write','trusted_host')),
 created_at TEXT NOT NULL
);
CREATE TABLE assistant_followups (
 team_id TEXT PRIMARY KEY REFERENCES team_runs(id),
 delegation_id TEXT REFERENCES assistant_delegations(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT NOT NULL REFERENCES sessions(id),
 origin_run_id TEXT REFERENCES runs(id),
 request_id TEXT NOT NULL UNIQUE,
 binding TEXT NOT NULL, state TEXT NOT NULL,
 run_id TEXT UNIQUE REFERENCES runs(id), error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX assistant_followups_pending ON assistant_followups(project_id,state,created_at);
"""
