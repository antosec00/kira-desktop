"""Additive schema for service-owned project teams; existing tables stay intact."""

TEAM_SCHEMA = """
CREATE TABLE team_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 request_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
 goal TEXT NOT NULL, mode TEXT NOT NULL, backend TEXT NOT NULL, model TEXT NOT NULL,
 settings TEXT NOT NULL, permission TEXT NOT NULL, root TEXT NOT NULL,
 state TEXT NOT NULL, phase TEXT NOT NULL, round_index INTEGER NOT NULL DEFAULT 0,
 round_limit INTEGER NOT NULL, concurrency INTEGER NOT NULL, max_agents INTEGER NOT NULL,
 parent_team_id TEXT REFERENCES team_runs(id), roster_frozen INTEGER NOT NULL DEFAULT 0,
 summary TEXT NOT NULL DEFAULT '', error TEXT, pause_reason TEXT,
 last_seq INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX team_runs_project ON team_runs(project_id,created_at);
CREATE TABLE team_members (
 team_id TEXT NOT NULL REFERENCES team_runs(id), id TEXT NOT NULL,
 session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id), name TEXT NOT NULL,
 role TEXT NOT NULL, instructions TEXT NOT NULL, access TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'idle', run_id TEXT REFERENCES runs(id),
 last_output TEXT NOT NULL DEFAULT '', last_message_seq INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(team_id,id)
);
CREATE TABLE team_steps (
 team_id TEXT NOT NULL REFERENCES team_runs(id), round_index INTEGER NOT NULL,
 id TEXT NOT NULL, member_id TEXT NOT NULL, title TEXT NOT NULL,
 instructions TEXT NOT NULL, access TEXT NOT NULL, depends_on TEXT NOT NULL,
 state TEXT NOT NULL, run_id TEXT UNIQUE REFERENCES runs(id), error TEXT,
 outcome TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(team_id,round_index,id),
 FOREIGN KEY(team_id,member_id) REFERENCES team_members(team_id,id)
);
CREATE TABLE team_run_links (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), team_id TEXT NOT NULL REFERENCES team_runs(id),
 member_id TEXT NOT NULL, round_index INTEGER NOT NULL, step_id TEXT,
 coordinator INTEGER NOT NULL,
 FOREIGN KEY(team_id,member_id) REFERENCES team_members(team_id,id)
);
CREATE TABLE team_events (
 team_id TEXT NOT NULL REFERENCES team_runs(id), seq INTEGER NOT NULL,
 type TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(team_id,seq)
);
CREATE TABLE team_messages (
 id TEXT PRIMARY KEY, team_id TEXT NOT NULL REFERENCES team_runs(id), seq INTEGER NOT NULL,
 sender_id TEXT NOT NULL, sender_name TEXT NOT NULL, recipient_id TEXT NOT NULL,
 content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(team_id,seq)
);
CREATE TABLE team_calls (
 scope TEXT NOT NULL, call_id TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(scope,call_id)
);
CREATE TABLE project_reservations (
 owner_id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL CHECK(owner_kind IN ('run','team')),
 project_id TEXT NOT NULL REFERENCES projects(id), root TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE provider_processes (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), pid INTEGER NOT NULL, uid INTEGER NOT NULL,
 boot_id TEXT NOT NULL, start_ticks TEXT NOT NULL, pgid INTEGER NOT NULL, sid INTEGER NOT NULL,
 store_id TEXT NOT NULL, state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE runtime_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE provider_process_members (
 run_id TEXT NOT NULL REFERENCES runs(id), pid INTEGER NOT NULL, identity TEXT NOT NULL,
 PRIMARY KEY(run_id,pid)
);
CREATE TABLE run_cleanup (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), state TEXT NOT NULL,
 boot_id TEXT NOT NULL, error TEXT
);
"""
