"""Opt-in Studio worlds, immutable revisions and request receipts.

Each project has one Studio scene. Studio records grant no execution, host
access or provider tool authority.
"""

STUDIO_SCHEMA = """
CREATE TABLE studio_worlds (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE studio_revisions (
    project_id TEXT NOT NULL REFERENCES studio_worlds(project_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(revision > 0),
    scene TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('user','restore')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id,revision)
);
CREATE TABLE studio_operations (
    project_id TEXT NOT NULL REFERENCES studio_worlds(project_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id,request_id)
);
CREATE INDEX studio_revisions_project ON studio_revisions(project_id,revision DESC);
"""
