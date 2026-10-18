"""Additive, revisioned project research records and exact mutation receipts."""

RESEARCH_SCHEMA = """
CREATE TABLE research_records (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 revision INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
 assessment TEXT NOT NULL, accepted INTEGER NOT NULL, title TEXT NOT NULL,
 body TEXT NOT NULL, record TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX research_record_project ON research_records(project_id);
CREATE INDEX research_record_context ON research_records(project_id,accepted,updated_at);
CREATE TABLE research_revisions (
 record_id TEXT NOT NULL REFERENCES research_records(id), revision INTEGER NOT NULL,
 record TEXT NOT NULL, editor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(record_id,revision)
);
CREATE TABLE research_operations (
 request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 method TEXT NOT NULL, fingerprint TEXT NOT NULL, record_id TEXT REFERENCES research_records(id),
 result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE research_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), enabled INTEGER NOT NULL,
 revision INTEGER NOT NULL, updated_at TEXT NOT NULL
);
"""
