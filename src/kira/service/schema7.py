"""Private durable direct context and source-addressable conversation archive."""

CONTEXT_SCHEMA = """
CREATE TABLE direct_context_states (
 session_id TEXT NOT NULL REFERENCES sessions(id), generation INTEGER NOT NULL,
 account_id TEXT NOT NULL, model TEXT NOT NULL, revision INTEGER NOT NULL,
 items TEXT NOT NULL, source_cursor TEXT, memory TEXT NOT NULL, metrics TEXT NOT NULL,
 completed_run_id TEXT REFERENCES runs(id), boundary TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(session_id,generation,account_id,model)
);
CREATE TABLE direct_context_checkpoints (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 generation INTEGER NOT NULL, account_id TEXT NOT NULL, model TEXT NOT NULL,
 revision INTEGER NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX direct_context_checkpoint_session ON direct_context_checkpoints(session_id,created_at);
CREATE TABLE chat_context_notes (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), content TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX chat_context_note_session ON chat_context_notes(session_id,created_at);
CREATE TABLE direct_context_operations (
 request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 generation INTEGER NOT NULL, account_id TEXT NOT NULL, model TEXT NOT NULL,
 fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE direct_context_reservations (
 request_id TEXT PRIMARY KEY REFERENCES direct_context_operations(request_id),
 project_id TEXT NOT NULL REFERENCES projects(id), root TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE chat_history_sources (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
 run_id TEXT NOT NULL REFERENCES runs(id), event_seq INTEGER NOT NULL,
 kind TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
 attachments TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id,event_seq)
);
CREATE INDEX chat_history_source_session ON chat_history_sources(session_id,id);
CREATE TABLE chat_history_scans (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), last_seq INTEGER NOT NULL
);
"""
