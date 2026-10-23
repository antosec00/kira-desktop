"""Durable chat-scoped Studio consent and attributed agent revisions.

Only the Studio revision table is rebuilt, to accept 'agent' as an origin.
Original revision bytes and indexes survive; core Kira tables are untouched.
"""

STUDIO_AGENT_SCHEMA = """
ALTER TABLE studio_revisions RENAME TO studio_revisions_v15;
DROP INDEX studio_revisions_project;
CREATE TABLE studio_revisions (
    project_id TEXT NOT NULL REFERENCES studio_worlds(project_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(revision > 0),
    scene TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('user','restore','agent')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id,revision)
);
INSERT INTO studio_revisions(project_id,revision,scene,origin,created_at)
SELECT project_id,revision,scene,origin,created_at FROM studio_revisions_v15;
DROP TABLE studio_revisions_v15;
CREATE INDEX studio_revisions_project ON studio_revisions(project_id,revision DESC);

CREATE TABLE studio_grants (
    project_id TEXT NOT NULL REFERENCES studio_worlds(project_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    generation INTEGER NOT NULL DEFAULT 0,
    provider_backend TEXT NOT NULL,
    provider_model TEXT NOT NULL,
    provider_generation INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id)
);

CREATE TABLE studio_agent_commits (
    project_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    tool_call_id TEXT NOT NULL,
    PRIMARY KEY(project_id,revision),
    FOREIGN KEY(project_id,revision)
       REFERENCES studio_revisions(project_id,revision) ON DELETE CASCADE
);
CREATE INDEX studio_commits_run ON studio_agent_commits(run_id);
"""
