"""Additive user-owned identities, immutable revisions and scoped experience."""

SOULS_SCHEMA = """
CREATE TABLE souls (
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=1),
 learning_floor INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE soul_revisions (
 soul_id TEXT NOT NULL REFERENCES souls(id), revision INTEGER NOT NULL,
 name TEXT NOT NULL, core TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('manual','suggest','adaptive')),
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
 preferences TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(soul_id,revision)
);
CREATE TABLE soul_bindings (
 session_id TEXT PRIMARY KEY REFERENCES sessions(id), project_id TEXT NOT NULL REFERENCES projects(id),
 soul_id TEXT REFERENCES souls(id), epoch INTEGER NOT NULL CHECK(epoch>0), updated_at TEXT NOT NULL
);
CREATE TABLE soul_run_snapshots (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT NOT NULL REFERENCES sessions(id), soul_id TEXT REFERENCES souls(id), revision INTEGER,
 epoch INTEGER NOT NULL, selected INTEGER NOT NULL CHECK(selected IN (0,1)),
 snapshot TEXT NOT NULL, capsule TEXT NOT NULL, capsule_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE soul_sources (
 id TEXT PRIMARY KEY, soul_id TEXT NOT NULL REFERENCES souls(id),
 project_id TEXT REFERENCES projects(id), session_id TEXT REFERENCES sessions(id),
 run_id TEXT REFERENCES runs(id), seq INTEGER, event_order INTEGER NOT NULL,
 kind TEXT NOT NULL, original TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(soul_id,run_id,seq)
);
CREATE TABLE soul_proposals (
 id TEXT PRIMARY KEY, soul_id TEXT NOT NULL REFERENCES souls(id), source_id TEXT NOT NULL REFERENCES soul_sources(id),
 project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 kind TEXT NOT NULL CHECK(kind IN ('lesson','preference')),
 scope TEXT NOT NULL CHECK(scope IN ('chat','project')),
 statement TEXT NOT NULL, applicability TEXT NOT NULL, preference_key TEXT, preference_value TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','superseded')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(soul_id,source_id,kind)
);
CREATE TABLE soul_memories (
 id TEXT PRIMARY KEY, soul_id TEXT NOT NULL REFERENCES souls(id),
 proposal_id TEXT NOT NULL UNIQUE REFERENCES soul_proposals(id), source_id TEXT NOT NULL REFERENCES soul_sources(id),
 project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 scope TEXT NOT NULL CHECK(scope IN ('chat','project')), kind TEXT NOT NULL,
 statement TEXT NOT NULL, applicability TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE soul_revision_memories (
 soul_id TEXT NOT NULL, revision INTEGER NOT NULL, memory_id TEXT NOT NULL REFERENCES soul_memories(id),
 score INTEGER NOT NULL DEFAULT 0 CHECK(score BETWEEN -2 AND 2),
 PRIMARY KEY(soul_id,revision,memory_id),
 FOREIGN KEY(soul_id,revision) REFERENCES soul_revisions(soul_id,revision)
);
CREATE TABLE soul_operations (
 request_id TEXT PRIMARY KEY, method TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE soul_tool_calls (
 run_id TEXT NOT NULL REFERENCES runs(id), call_id TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(run_id,call_id)
);
CREATE INDEX soul_proposals_profile ON soul_proposals(soul_id,status,created_at);
CREATE INDEX soul_memories_scope ON soul_memories(soul_id,project_id,session_id);
CREATE INDEX soul_sources_event ON soul_sources(soul_id,event_order);
"""
