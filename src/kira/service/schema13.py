"""Opt-in autonomous goals; historical manual objectives remain unarmed."""

AUTONOMOUS_GOALS_SCHEMA = """
CREATE TABLE autonomous_goals (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT NOT NULL REFERENCES sessions(id),
 current INTEGER NOT NULL DEFAULT 1 CHECK(current IN (0,1)),
 objective TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','paused','blocked','completed','interrupted')),
 binding TEXT NOT NULL,
 cycle_count INTEGER NOT NULL DEFAULT 0 CHECK(cycle_count>=0),
 max_cycles INTEGER NOT NULL DEFAULT 0 CHECK(max_cycles>=0),
 no_progress_count INTEGER NOT NULL DEFAULT 0 CHECK(no_progress_count>=0),
 summary TEXT NOT NULL DEFAULT '', next_step TEXT NOT NULL DEFAULT '',
 evidence TEXT NOT NULL DEFAULT '[]', completion_basis TEXT,
 error TEXT, waiting_reason TEXT NOT NULL DEFAULT '',
 last_run_id TEXT REFERENCES runs(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX autonomous_goal_current ON autonomous_goals(session_id) WHERE current=1;
CREATE INDEX autonomous_goal_schedule ON autonomous_goals(status,current,updated_at);
CREATE TABLE goal_cycles (
 goal_id TEXT NOT NULL REFERENCES autonomous_goals(id),
 cycle_number INTEGER NOT NULL CHECK(cycle_number>0),
 run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
 state TEXT NOT NULL CHECK(state IN ('running','completed','failed','cancelled','interrupted')),
 checkpoint TEXT, progress_signature TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(goal_id,cycle_number)
);
CREATE TABLE goal_commands (
 request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
 goal_id TEXT NOT NULL REFERENCES autonomous_goals(id),
 result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE goal_tool_calls (
 run_id TEXT NOT NULL REFERENCES runs(id), call_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(run_id,call_id)
);
"""
