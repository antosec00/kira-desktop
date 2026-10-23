"""Single-writer SQLite store with transactional run events and consistent backups."""

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from kira.domain.contracts import (
    EVENT_SCHEMA_VERSION, SCHEMA_VERSION, TERMINAL_STATES, Json, KiraError,
)
from kira.service.schema3 import TEAM_SCHEMA
from kira.service.schema4 import WORKBENCH_SCHEMA
from kira.service.schema5 import ASSISTANT_SCHEMA
from kira.service.schema6 import CONTINUITY_SCHEMA
from kira.service.schema7 import CONTEXT_SCHEMA
from kira.service.schema8 import BROWSER_SCHEMA
from kira.service.schema9 import RESEARCH_SCHEMA
from kira.service.schema10 import PROJECT_TOOLS_SCHEMA
from kira.service.schema11 import LIVE_CONVERSATION_SCHEMA
from kira.service.schema12 import FULL_ACCESS_SCHEMA
from kira.service.schema13 import AUTONOMOUS_GOALS_SCHEMA
from kira.service.schema14 import SOULS_SCHEMA
from kira.service.schema15 import STUDIO_SCHEMA
from kira.service.schema16 import STUDIO_AGENT_SCHEMA


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        if path.is_symlink():
            raise KiraError("UNSAFE_PATH", "Database must not be a symlink")
        self.db = sqlite3.connect(path, isolation_level=None)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        version = int(self.db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            self.db.close()
            raise KiraError("INCOMPATIBLE_SCHEMA", f"Database schema {version} is newer than this service")
        if version < SCHEMA_VERSION:
            try:
                if path.stat().st_size > 0:
                    self.backup(path.with_name(f"{path.name}.pre-v{SCHEMA_VERSION}-{uuid.uuid4().hex}.bak"))
                self._migrate(version)
            except BaseException:
                self.db.close()
                raise
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")

    def _migrate(self, version: int) -> None:
        if version not in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}:
            raise KiraError("INCOMPATIBLE_SCHEMA", f"No migration from schema {version}")
        initial = """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL,
                instructions TEXT NOT NULL, permission TEXT NOT NULL,
                memory TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                backend TEXT NOT NULL, model TEXT NOT NULL, settings TEXT NOT NULL,
                backend_ref TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                request_id TEXT NOT NULL UNIQUE, prompt TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT,
                outcome TEXT, last_seq INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE events (
                run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
                type TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(run_id,seq)
            );
            CREATE TABLE artifacts (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                run_id TEXT NOT NULL REFERENCES runs(id), path TEXT NOT NULL,
                kind TEXT NOT NULL, sha256 TEXT, tool_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX sessions_project ON sessions(project_id);
            CREATE INDEX runs_session ON runs(session_id,created_at);
            CREATE INDEX artifacts_project ON artifacts(project_id);
        """
        workspace = """
            CREATE TABLE workspaces (
                project_id TEXT PRIMARY KEY REFERENCES projects(id),
                tools TEXT NOT NULL, next_step TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE file_watches (
                project_id TEXT NOT NULL REFERENCES projects(id), path TEXT NOT NULL,
                sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id,path)
            );
            CREATE TABLE desktop_requests (
                request_id TEXT PRIMARY KEY, method TEXT NOT NULL, fingerprint TEXT NOT NULL,
                project_id TEXT REFERENCES projects(id), status TEXT NOT NULL,
                result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE captures (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX captures_project ON captures(project_id,created_at);
        """
        try:
            self.db.executescript("BEGIN IMMEDIATE;" + (initial if version == 0 else "")
                                 + (workspace if version < 2 else "")
                                 + (TEAM_SCHEMA + "INSERT INTO runtime_metadata VALUES ('store_id','"
                                    + uuid.uuid4().hex + "');" if version < 3 else "")
                                 + (WORKBENCH_SCHEMA if version < 4 else "")
                                 + (ASSISTANT_SCHEMA if version < 5 else "")
                                 + (CONTINUITY_SCHEMA if version < 6 else "")
                                 + (CONTEXT_SCHEMA if version < 7 else "")
                                 + (BROWSER_SCHEMA if version < 8 else "")
                                 + (RESEARCH_SCHEMA if version < 9 else "")
                                 + (PROJECT_TOOLS_SCHEMA if version < 10 else "")
                                 + (LIVE_CONVERSATION_SCHEMA if version < 11 else "")
                                 + (FULL_ACCESS_SCHEMA if version < 12 else "")
                                 + (AUTONOMOUS_GOALS_SCHEMA if version < 13 else "")
                                 + (SOULS_SCHEMA if version < 14 else "")
                                 + (STUDIO_SCHEMA if version < 15 else "")
                                 + (STUDIO_AGENT_SCHEMA if version < 16 else "")
                                 + f"PRAGMA user_version={SCHEMA_VERSION}; COMMIT;")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    @staticmethod
    def record(row: sqlite3.Row) -> Json:
        result = dict(row)
        for key in ("memory", "settings", "outcome", "payload"):
            if key in result and result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def one(self, table: str, identifier: str) -> Json:
        assert table in {"projects", "sessions", "runs"}
        row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", f"{table.rstrip('s')} not found")
        return self.record(row)

    def all(self, sql: str, args: tuple[Any, ...] = ()) -> list[Json]:
        return [self.record(row) for row in self.db.execute(sql, args)]

    def project_active(self, project_id: str) -> list[Json]:
        return self.all("SELECT r.* FROM runs r JOIN sessions s ON r.session_id=s.id "
                        "WHERE s.project_id=? AND r.state NOT IN ('completed','failed','cancelled','interrupted')",
                        (project_id,))

    def reserved(self, root: str, *, except_owner: str | None = None) -> list[Json]:
        candidate = Path(root).resolve()
        matches = []
        reservations = self.all("SELECT * FROM project_reservations")
        reservations += self.all("SELECT request_id AS owner_id,'context' AS owner_kind,project_id,root,created_at FROM direct_context_reservations")
        for row in reservations:
            other = Path(row["root"]).resolve()
            if row["owner_id"] != except_owner and (
                    candidate == other or candidate.is_relative_to(other)
                    or other.is_relative_to(candidate)):
                matches.append(row)
        for row in self.all("SELECT request_id AS owner_id,project_id,root FROM session_forks f WHERE status='pending' OR cleanup_blocked=1 "
                            "OR EXISTS (SELECT 1 FROM control_processes c WHERE c.run_id=f.request_id AND c.state!='stopped')"):
            other = Path(row["root"]).resolve()
            if row["owner_id"] != except_owner and (
                    candidate == other or candidate.is_relative_to(other)
                    or other.is_relative_to(candidate)):
                matches.append({**row, "owner_kind": "chat_control"})
        return matches

    def conversation_team(self, project: Json) -> str | None:
        """Identify the one same-project Team compatible with a read-only chat."""
        root = Path(project["root"]).resolve()
        reservations = self.reserved(str(root))
        if (len(reservations) != 1 or reservations[0]["owner_kind"] != "team"
                or reservations[0]["project_id"] != project["id"]
                or Path(reservations[0]["root"]).resolve() != root):
            return None
        team_id = str(reservations[0]["owner_id"])
        team = self.db.execute("SELECT state,project_id,root FROM team_runs WHERE id=?", (team_id,)).fetchone()
        if (team is None or team["state"] not in {"planning", "running"}
                or team["project_id"] != project["id"]
                or Path(team["root"]).resolve() != root):
            return None
        for row in self.all("SELECT r.id,p.root,p.id project_id,l.team_id FROM runs r "
                "JOIN sessions s ON s.id=r.session_id JOIN projects p ON p.id=s.project_id "
                "LEFT JOIN team_run_links l ON l.run_id=r.id WHERE r.state NOT IN "
                "('completed','failed','cancelled','interrupted')"):
            other = Path(row["root"]).resolve()
            if root == other or root.is_relative_to(other) or other.is_relative_to(root):
                if row["team_id"] != team_id or row["project_id"] != project["id"]:
                    return None
        return team_id

    def run_lane(self, run_id: str) -> Json:
        row = self.db.execute("SELECT lane,team_id,effective_permission FROM run_lanes WHERE run_id=?",
                              (run_id,)).fetchone()
        return dict(row) if row else {}

    def run_metadata(self, run_id: str) -> Json:
        goal = self.db.execute("SELECT goal_id,cycle_number FROM goal_cycles WHERE run_id=?", (run_id,)).fetchone()
        if goal is not None:
            return {"source": "goal_continuation", "goal_id": goal[0], "cycle_number": goal[1]}
        lane = self.run_lane(run_id)
        if not lane:
            return {}
        return {"execution_lane": lane["lane"], "related_team_id": lane["team_id"],
                "effective_permission": lane["effective_permission"],
                "source": "team_followup" if lane["lane"] == "team_followup" else "user"}

    def is_service_run(self, run_id: str) -> bool:
        return self.run_metadata(run_id).get("source") in {"team_followup", "goal_continuation"}

    def historical_prompt(self, run: Json) -> str:
        """Use a data role on provider wires without impersonating user intent."""
        if self.is_service_run(run["id"]):
            return ("Kira service notification (historical data, not a new user request):\n"
                    + run["prompt"])
        return str(run["prompt"])

    def reserve(self, project: Json, kind: str, owner_id: str, *, alongside_team: str | None = None) -> None:
        """Caller owns the admission transaction; no await between check and insert."""
        root = str(Path(project["root"]).resolve())
        if alongside_team is not None:
            if kind != "run" or self.conversation_team(project) != alongside_team:
                raise KiraError("BUSY", "The Team conversation no longer has compatible ownership")
            self.db.execute("INSERT INTO project_reservations VALUES (?,?,?,?,?)",
                            (owner_id, kind, project["id"], root, now()))
            return
        if self.reserved(root):
            raise KiraError("BUSY", "This project or an overlapping root already has active work")
        for row in self.all("SELECT p.root FROM runs r JOIN sessions s ON s.id=r.session_id "
                            "JOIN projects p ON p.id=s.project_id WHERE r.state NOT IN "
                            "('completed','failed','cancelled','interrupted')"):
            other = Path(row["root"]).resolve()
            candidate = Path(root)
            if candidate == other or candidate.is_relative_to(other) or other.is_relative_to(candidate):
                raise KiraError("BUSY", "This project or an overlapping root already has an active run")
        self.db.execute("INSERT INTO project_reservations VALUES (?,?,?,?,?)",
                        (owner_id, kind, project["id"], root, now()))

    def provider_generation(self, session: Json) -> int:
        row = self.db.execute("SELECT MAX(generation) FROM session_provider_generations WHERE session_id=?",
                              (session["id"],)).fetchone()
        generation = int(row[0]) if row and row[0] is not None else 0
        self.db.execute("INSERT OR IGNORE INTO session_provider_generations VALUES (?,?,?,?,?,?,?,NULL)",
                        (session["id"], generation, session["backend"], session["model"],
                         encode(session["settings"]), session.get("backend_ref"), session["created_at"]))
        return generation

    def ensure_run_bindings(self, session: Json, generation: int) -> None:
        """Freeze legacy attribution before the mutable session changes provider."""
        for run in self.all("SELECT r.* FROM runs r WHERE session_id=? AND NOT EXISTS "
                            "(SELECT 1 FROM run_provider_bindings b WHERE b.run_id=r.id)", (session["id"],)):
            backend, model, settings = session["backend"], session["model"], dict(session["settings"])
            for event in self.all("SELECT payload FROM events WHERE run_id=? AND type='run.state' ORDER BY seq",
                                  (run["id"],)):
                evidence = event["payload"]
                if evidence.get("backend"):
                    backend, model = evidence["backend"], evidence.get("model", model)
                    if "reasoning_effort" in evidence:
                        settings["reasoning_effort"] = evidence["reasoning_effort"]
                    break
            self.db.execute("INSERT OR IGNORE INTO run_provider_bindings VALUES (?,?,?,?,?,?,?,?)",
                            (run["id"], session["id"], generation, backend, model, encode(settings),
                             session.get("backend_ref"), run["created_at"]))

    def create_run(self, session_id: str, request_id: str, prompt: str, *,
                   reserve: bool = False,
                   alongside_team: str | None = None,
                   source: str = "user",
                   source_metadata: Json | None = None,
                   attachments: list[Json] | None = None,
                   on_create: Callable[[str], None] | None = None) -> tuple[Json, list[Json]]:
        identifier, timestamp = new_id(), now()
        initial: list[Json] = [
            {"schema_version": EVENT_SCHEMA_VERSION, "run_id": identifier, "seq": 1,
             "type": "run.state", "timestamp": timestamp, "payload": {"state": "queued"}},
            {"schema_version": EVENT_SCHEMA_VERSION, "run_id": identifier, "seq": 2,
             "type": "message.user", "timestamp": timestamp, "payload": {"content": prompt, **({"attachments": attachments} if attachments else {})}},
        ]
        if source in {"team_followup", "goal_continuation"}:
            initial[0]["payload"].update({"source": source, **(source_metadata or {})})
            initial[1] = {"schema_version": EVENT_SCHEMA_VERSION, "run_id": identifier, "seq": 2,
                          "type": "backend.notice", "timestamp": timestamp, "payload": {
                              "message": ("Team result available; Kira is continuing the original conversation."
                                  if source == "team_followup" else
                                  "Kira is continuing the explicitly authorized chat goal."),
                              "content": prompt,
                              "source": source, **(source_metadata or {})}}
        elif source != "user":
            raise KiraError("INVALID_ARGUMENT", "Unknown run source")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if reserve:
                session = self.one("sessions", session_id)
                self.reserve(self.one("projects", session["project_id"]), "run", identifier,
                             alongside_team=alongside_team)
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (identifier, session_id, request_id, prompt, "queued", timestamp,
                             timestamp, None, None, 2))
            session = self.one("sessions", session_id)
            generation = self.provider_generation(session)
            self.ensure_run_bindings(session, generation)
            for position, item in enumerate(attachments or []):
                self.db.execute("INSERT INTO run_image_attachments VALUES (?,?,?)",
                                (identifier, item["id"], position))
            for event in initial:
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                                (identifier, event["seq"], event["type"], timestamp, encode(event["payload"])))
            if on_create is not None:
                on_create(identifier)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.one("runs", identifier), initial

    def event(self, run_id: str, event_type: str, payload: Json, *,
              mutation: Callable[[], Any] | None = None) -> Json:
        """Commit event and its materialized state in one transaction."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if mutation is not None:
                mutation()
            run = self.one("runs", run_id)
            sequence = run["last_seq"] + 1
            timestamp = now()
            if event_type == "run.state":
                self.db.execute(
                    "UPDATE runs SET state=?,error=?,outcome=?,last_seq=?,updated_at=? WHERE id=?",
                    (payload["state"], payload.get("error"),
                     encode(payload.get("outcome")) if payload.get("outcome") is not None else None,
                     sequence, timestamp, run_id),
                )
            else:
                self.db.execute("UPDATE runs SET last_seq=?,updated_at=? WHERE id=?",
                                (sequence, timestamp, run_id))
            if event_type == "session.updated" and "backend_ref" in payload:
                session = self.one("sessions", run["session_id"])
                generation = self.provider_generation(session)
                self.ensure_run_bindings(session, generation)
                binding = self.db.execute("SELECT * FROM run_provider_bindings WHERE run_id=?", (run_id,)).fetchone()
                assert binding is not None
                self.db.execute("UPDATE run_provider_bindings SET backend_ref=? WHERE run_id=?",
                                (payload["backend_ref"], run_id))
                if binding["generation"] == generation and binding["backend"] == session["backend"]:
                    self.db.execute("UPDATE session_provider_generations SET backend_ref=? WHERE session_id=? AND generation=? AND retired_at IS NULL",
                                    (payload["backend_ref"], run["session_id"], generation))
                    self.db.execute("UPDATE sessions SET backend_ref=? WHERE id=?",
                                    (payload["backend_ref"], run["session_id"]))
            if event_type == "artifact.created":
                session = self.one("sessions", run["session_id"])
                self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                                (new_id(), session["project_id"], run_id, payload["path"],
                                 payload.get("kind", "file"), payload.get("sha256"),
                                 payload.get("tool_id"), timestamp))
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                            (run_id, sequence, event_type, timestamp, encode(payload)))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"schema_version": EVENT_SCHEMA_VERSION, "run_id": run_id, "seq": sequence,
                "type": event_type, "timestamp": timestamp, "payload": payload}

    def events(self, run_id: str, after: int = 0, limit: int = 250) -> Json:
        run = self.one("runs", run_id)
        rows = self.all("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                        (run_id, after, limit))
        result: list[Json] = []
        byte_count = 0
        for row in rows:
            row["schema_version"] = EVENT_SCHEMA_VERSION
            size = len(encode(row).encode("utf-8"))
            if result and byte_count + size > 700_000:
                break
            result.append(row)
            byte_count += size
        cursor = result[-1]["seq"] if result else after
        return {"events": result, "last_seq": run["last_seq"], "has_more": cursor < run["last_seq"]}

    def steering(self, request_id: str, status: str, result: Json,
                 events: list[tuple[str, Json]], *, run_id: str | None = None,
                 prompt: str | None = None) -> list[Json]:
        """Commit delivery identity and its transcript evidence together."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            timestamp = now()
            if run_id is not None:
                assert prompt is not None and status == "pending"
                self.db.execute("INSERT INTO steering_requests VALUES (?,?,?,?,?,?,?)",
                                (request_id, run_id, prompt, status, encode(result), timestamp, timestamp))
            else:
                row = self.db.execute("SELECT run_id FROM steering_requests WHERE request_id=?", (request_id,)).fetchone()
                if row is None:
                    raise KiraError("NOT_FOUND", "Steering intent not found")
                run_id = row[0]
                self.db.execute("UPDATE steering_requests SET status=?,result=?,updated_at=? WHERE request_id=?",
                                (status, encode(result), timestamp, request_id))
            assert run_id is not None
            run = self.one("runs", run_id)
            sequence = run["last_seq"]
            committed = []
            for kind, payload in events:
                sequence += 1
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                                (run_id, sequence, kind, timestamp, encode(payload)))
                committed.append({"schema_version": EVENT_SCHEMA_VERSION, "run_id": run_id,
                                  "seq": sequence, "type": kind, "timestamp": timestamp, "payload": payload})
            self.db.execute("UPDATE runs SET last_seq=?,updated_at=? WHERE id=?", (sequence, timestamp, run_id))
            self.db.execute("COMMIT")
            return committed
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def history(self, session_id: str) -> list[Json]:
        """Portable recent messages; provider tool messages remain intact when present."""
        runs = self.all("SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC LIMIT 30",
                        (session_id,))
        messages: list[Json] = []
        budget = 200_000
        for run in reversed(runs):
            messages.append({"role": "user", "content": self.historical_prompt(run)})
            events = self.all("SELECT * FROM events WHERE run_id=? AND type IN ('message.completed','backend.notice','message.user') ORDER BY seq",
                              (run["id"],))
            for event in events:
                payload = event["payload"]
                if event["type"] == "message.user" and payload.get("source") == "steer":
                    messages.append({"role": "user", "content": payload["content"]})
                    continue
                if isinstance(payload.get("provider_messages"), list):
                    messages.extend(payload["provider_messages"])
                elif event["type"] == "message.completed" and payload.get("content"):
                    messages.append({"role": "assistant", "content": payload["content"]})
        while len(messages) > 1 and len(encode(messages)) > budget:
            messages.pop(0)
            while messages and messages[0].get("role") != "user":
                messages.pop(0)
        return messages

    def recover(self) -> list[Json]:
        events = []
        for row in self.all("SELECT * FROM runs ORDER BY created_at"):
            if row["state"] not in TERMINAL_STATES:
                events.append(self.event(row["id"], "run.state", {
                    "state": "interrupted", "error": "Service restarted with an unfinished run. "
                    "Effects may be uncertain; no operation was replayed.",
                    "outcome": {"effects": "unknown", "automatic_replay": False},
                }))
        # Native intents already contain unknown placeholders. Completed subactions
        # remain recorded; never replay the remaining uncertain effects.
        self.db.execute("UPDATE desktop_requests SET status='unknown',updated_at=? "
                        "WHERE status='pending'", (now(),))
        return events

    def backup(self, destination: Path | None = None) -> Path:
        destination = destination or self.path.with_name(f"kira-backup-{uuid.uuid4().hex}.sqlite3")
        if destination.exists() or destination.is_symlink():
            raise KiraError("CONFLICT", "Backup target already exists")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600)
        os.close(descriptor)
        try:
            target = sqlite3.connect(destination)
            try:
                self.db.backup(target)
            finally:
                target.close()
            with destination.open("rb") as stream:
                os.fsync(stream.fileno())
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def close(self) -> None:
        self.db.close()
