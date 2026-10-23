"""Permit explicit full access on durable result lanes, retaining every row."""

FULL_ACCESS_SCHEMA = """
CREATE TABLE run_lanes_v12 (
 run_id TEXT PRIMARY KEY REFERENCES runs(id),
 lane TEXT NOT NULL CHECK(lane IN ('team_conversation','team_followup')),
 team_id TEXT NOT NULL REFERENCES team_runs(id),
 effective_permission TEXT NOT NULL CHECK(effective_permission IN ('read_only','project_write','trusted_host','full_access')),
 created_at TEXT NOT NULL
);
INSERT INTO run_lanes_v12 SELECT * FROM run_lanes;
DROP TABLE run_lanes;
ALTER TABLE run_lanes_v12 RENAME TO run_lanes;
"""
