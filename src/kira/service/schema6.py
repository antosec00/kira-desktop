"""Provider segments and immutable, explicitly selected image inputs."""

CONTINUITY_SCHEMA = """
CREATE TABLE session_provider_generations (
    session_id TEXT NOT NULL REFERENCES sessions(id), generation INTEGER NOT NULL,
    backend TEXT NOT NULL, model TEXT NOT NULL, settings TEXT NOT NULL,
    backend_ref TEXT, created_at TEXT NOT NULL, retired_at TEXT,
    PRIMARY KEY(session_id,generation)
);
CREATE TABLE session_provider_switches (
    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL REFERENCES sessions(id),
    generation INTEGER NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE run_provider_bindings (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), session_id TEXT NOT NULL,
    generation INTEGER NOT NULL, backend TEXT NOT NULL, model TEXT NOT NULL,
    settings TEXT NOT NULL, backend_ref TEXT, created_at TEXT NOT NULL,
    FOREIGN KEY(session_id,generation) REFERENCES session_provider_generations(session_id,generation)
);
CREATE TABLE image_attachments (
    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE run_image_attachments (
    run_id TEXT NOT NULL REFERENCES runs(id), attachment_id TEXT NOT NULL REFERENCES image_attachments(id),
    position INTEGER NOT NULL, PRIMARY KEY(run_id,attachment_id), UNIQUE(run_id,position)
);
CREATE TABLE queue_image_attachments (
    queue_id TEXT NOT NULL REFERENCES prompt_queue(id), attachment_id TEXT NOT NULL REFERENCES image_attachments(id),
    position INTEGER NOT NULL, PRIMARY KEY(queue_id,attachment_id), UNIQUE(queue_id,position)
);
"""
