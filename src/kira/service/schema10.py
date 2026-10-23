"""Immutable project tools and run-owned, recoverable invocation receipts."""

PROJECT_TOOLS_SCHEMA = """
CREATE TABLE project_tools (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 name TEXT NOT NULL, revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,name)
);
CREATE TABLE project_tool_revisions (
 tool_id TEXT NOT NULL REFERENCES project_tools(id), revision INTEGER NOT NULL,
 manifest TEXT NOT NULL, source BLOB NOT NULL, source_path TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, revision_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(tool_id,revision)
);
CREATE TABLE project_tool_mutations (
 request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 method TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE project_tool_operations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT NOT NULL REFERENCES sessions(id), run_id TEXT NOT NULL REFERENCES runs(id),
 call_id TEXT NOT NULL, action TEXT NOT NULL, fingerprint TEXT NOT NULL,
 parent_id TEXT REFERENCES project_tool_operations(id),
 tool_id TEXT REFERENCES project_tools(id), revision INTEGER,
 state TEXT NOT NULL, result TEXT, cleanup_state TEXT NOT NULL,
 started INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(run_id,call_id)
);
CREATE INDEX project_tool_operations_run ON project_tool_operations(run_id,cleanup_state);
CREATE INDEX project_tool_operations_test ON project_tool_operations(tool_id,revision,action,created_at);
CREATE TABLE project_tool_processes (
 run_id TEXT PRIMARY KEY REFERENCES project_tool_operations(id),
 pid INTEGER NOT NULL, uid INTEGER NOT NULL, boot_id TEXT NOT NULL,
 start_ticks TEXT NOT NULL, pgid INTEGER NOT NULL, sid INTEGER NOT NULL,
 store_id TEXT NOT NULL, state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE project_tool_process_members (
 run_id TEXT NOT NULL REFERENCES project_tool_processes(run_id), pid INTEGER NOT NULL,
 identity TEXT NOT NULL, PRIMARY KEY(run_id,pid)
);
"""
