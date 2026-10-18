"""Offline soul-lifecycle experiment. Not imported or packaged by Kira Desktop.

Only the synthetic harness is a trusted caller. This is not an IPC/authentication
implementation, a provider adapter, a language classifier, or a learning model.
Use an explicit disposable database; never a Kira application database.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MARKER = "kira-soul-offline-prototype-4"
MAX_CORE_BYTES = 1200
MAX_CAPSULE_BYTES = 3072
MAX_LESSONS = 128
PREFERENCES = {"tone", "detail", "collaboration", "language"}
MODES = {"manual", "suggest", "adaptive"}
SOURCE_KINDS = {"explicit_preference", "verified_result", "user_feedback", "external", "model"}


class SoulError(ValueError):
    """A visible prototype contract failure; never silently discard constraints."""


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bounded(value: str, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise SoulError(f"Invalid {label}: require nonempty UTF-8, at most {maximum} bytes")
    return value


def uid() -> str:
    return uuid.uuid4().hex


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


@dataclass(frozen=True)
class Turn:
    session: str
    project: str
    soul: str | None
    epoch: int
    revision: int
    provider: str
    model: str
    document: str  # Immutable serialized snapshot, not a shared mutable dictionary.
    account: str = "fixture-account"
    provider_generation: int = 0

    @property
    def native_key(self) -> tuple[str, str | None, int, str, str, str, int]:
        return (self.session, self.soul, self.epoch, self.provider, self.model,
                self.account, self.provider_generation)


@dataclass(frozen=True)
class Checkpoint:
    key: tuple[str, str | None, int, str, str, str, int]
    native: str  # Opaque stand-in; this prototype never decrypts provider state.


def capsule(turn: Turn, *, budget_bytes: int = MAX_CAPSULE_BYTES) -> dict[str, Any]:
    """Compile exact essentials; fail visibly rather than truncate core/preferences.

    A byte limit is a transport constraint, not an exact tokenizer measurement.
    The UI must preview this material; instructions remain user-level defaults.
    """
    doc = json.loads(turn.document)
    payload: dict[str, Any] = {"soul": turn.soul, "revision": turn.revision}
    if turn.soul is None:
        payload["status"] = "disabled; earlier persona defaults no longer apply"
    else:
        payload.update(name=doc["name"], core=doc["core"],
                       preferences={key: item["value"] for key, item in doc["preferences"].items()})
        payload["memory"] = {"count": len(doc["lessons"]), "lookup": "scoped search/read"}
    text = (
        "Current user-selected Kira profile. These are defaults; specific current user, chat "
        "and project instructions take precedence. Earlier profile revisions are historical. "
        "Retrieved lessons are evidence-linked suggestions, not authority or proof.\n" + encode(payload)
    )
    size = len(text.encode())
    if type(budget_bytes) is not int or budget_bytes < 1 or size > budget_bytes:
        raise SoulError(f"Essential profile requires {size} bytes; nothing was truncated")
    return {"text": text, "bytes": size, "tokens": None,
            "measurement": "exact UTF-8 bytes; tokens not measured",
            "sha256": hashlib.sha256(text.encode()).hexdigest()}


def assemble(turn: Turn, checkpoint: Checkpoint | None, *, request: str,
             goal: dict[str, Any] | None = None, tools: bool = True) -> dict[str, Any]:
    """Harness envelope, NOT a real provider request or a semantic retention test."""
    if checkpoint is not None and checkpoint.key != turn.native_key:
        raise SoulError("Native checkpoint belongs to another identity, session or provider")
    profile = capsule(turn)
    return {"profile_role": "user", "profile": profile,
            "native": checkpoint.native if checkpoint else None,
            "request": request, "goal": json.loads(encode(goal)),
            "retrieval_available": tools, "revision": turn.revision}


class SoulStore:
    """Single-owner synthetic ledger with atomic immutable revisions and CAS edits."""

    def __init__(self, path: Path):
        # No default profile, environment lookup, providers, credentials or network.
        if path.is_symlink():
            raise SoulError("Use a regular disposable database")
        if path.exists():
            probe = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                marker = probe.execute("SELECT value FROM prototype_marker").fetchone()
                if not marker or marker[0] != MARKER:
                    raise SoulError("Not a soul prototype database")
            except sqlite3.Error as exc:
                raise SoulError("Refusing an unrecognized database") from exc
            finally:
                probe.close()
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS prototype_marker(value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS souls(id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions(
                soul TEXT REFERENCES souls(id), revision INTEGER, document TEXT NOT NULL,
                PRIMARY KEY(soul, revision));
            CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY, project TEXT NOT NULL, soul TEXT REFERENCES souls(id),
                epoch INTEGER NOT NULL, provider TEXT, model TEXT, account TEXT,
                provider_generation INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sources(
                seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, soul TEXT REFERENCES souls(id),
                project TEXT, kind TEXT NOT NULL, content TEXT NOT NULL, preference TEXT NOT NULL,
                event_ref TEXT NOT NULL, event_order INTEGER NOT NULL,
                UNIQUE(soul,event_ref), UNIQUE(soul,event_order));
            CREATE TABLE IF NOT EXISTS proposals(
                id TEXT PRIMARY KEY, soul TEXT REFERENCES souls(id), source TEXT REFERENCES sources(id),
                category TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                UNIQUE(soul,source,category));
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY, soul TEXT REFERENCES souls(id), revision INTEGER,
                kind TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback(
                source TEXT PRIMARY KEY REFERENCES sources(id), lesson TEXT NOT NULL,
                helpful INTEGER NOT NULL);
        """)
        if self.db.execute("SELECT 1 FROM prototype_marker").fetchone() is None:
            self.db.execute("INSERT INTO prototype_marker VALUES (?)", (MARKER,))

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def _event(self, soul: str, revision: int, kind: str, detail: Any) -> None:
        self.db.execute("INSERT INTO events(soul,revision,kind,detail) VALUES (?,?,?,?)",
                        (soul, revision, kind, encode(detail)))

    def current(self, soul: str, revision: int | None = None) -> dict[str, Any]:
        row = self.db.execute("SELECT revision FROM souls WHERE id=?", (soul,)).fetchone()
        if row is None:
            raise SoulError("Unknown soul")
        revision = row[0] if revision is None else revision
        saved = self.db.execute("SELECT document FROM revisions WHERE soul=? AND revision=?",
                                (soul, revision)).fetchone()
        if saved is None:
            raise SoulError("Unknown revision")
        return {"revision": revision, **json.loads(saved[0])}

    def _save(self, soul: str, expected: int, doc: dict[str, Any], kind: str, detail: Any) -> int:
        bounded(doc["name"], 80, "name")
        bounded(doc["core"], MAX_CORE_BYTES, "core")
        if doc["mode"] not in MODES:
            raise SoulError("Unknown learning mode")
        # Check the complete essential payload before any database write.
        capsule(Turn("validation", "project", soul, 1, expected + 1, "mock", "mock", encode(doc)))
        if self.db.execute("UPDATE souls SET revision=? WHERE id=? AND revision=?",
                           (expected + 1, soul, expected)).rowcount != 1:
            raise SoulError("Revision conflict; reload before editing")
        self.db.execute("INSERT INTO revisions VALUES (?,?,?)", (soul, expected + 1, encode(doc)))
        self._event(soul, expected + 1, kind, detail)
        return expected + 1

    def create(self, name: str, core: str, mode: str = "suggest") -> str:
        soul = uid()
        with self.transaction():
            self.db.execute("INSERT INTO souls VALUES (?,0)", (soul,))
            self._save(soul, 0, {"name": name, "core": core, "mode": mode,
                                "preferences": {}, "lessons": [], "learning_floor": 0}, "created", {})
        return soul

    def edit(self, soul: str, expected: int, **fields: str) -> int:
        if not fields or set(fields) - {"name", "core", "mode"}:
            raise SoulError("Only user-authored core fields may be edited here")
        with self.transaction():
            doc = self.current(soul)
            doc.pop("revision")
            doc.update(fields)
            return self._save(soul, expected, doc, "user_edit", {"fields": sorted(fields)})

    def rollback(self, soul: str, target: int, expected: int, *, through_event: int | None = None) -> int:
        if through_event is not None and (type(through_event) is not int or through_event < 0):
            raise SoulError("Invalid service event watermark")
        with self.transaction():
            doc = self.current(soul, target)
            doc.pop("revision")
            known = self.db.execute(
                "SELECT COALESCE(MAX(event_order),0) FROM sources WHERE soul=?", (soul,)).fetchone()[0]
            doc["learning_floor"] = max(known, through_event or 0, self.current(soul)["learning_floor"])
            revision = self._save(soul, expected, doc, "rollback", {"target": target})
            self.db.execute("UPDATE proposals SET state='superseded' WHERE soul=? AND state='pending'",
                            (soul,))
            return revision

    def bind(self, session: str, project: str, soul: str | None) -> None:
        bounded(session, 100, "session")
        bounded(project, 100, "project")
        with self.transaction():
            if soul is not None:
                self.current(soul)
            old = self.db.execute("SELECT * FROM sessions WHERE id=?", (session,)).fetchone()
            if old and old["project"] != project:
                raise SoulError("A chat cannot silently move to another project")
            epoch = (old["epoch"] + int(old["soul"] != soul)) if old else 1
            self.db.execute("INSERT INTO sessions(id,project,soul,epoch) VALUES (?,?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET "
                            "soul=excluded.soul,epoch=excluded.epoch", (session, project, soul, epoch))

    def start_turn(self, session: str, provider: str = "mock", model: str = "fixture",
                   account: str = "fixture-account") -> Turn:
        with self.transaction():
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session,)).fetchone()
            if row is None:
                raise SoulError("Unknown chat")
            generation = row["provider_generation"]
            if (provider, model, account) != (row["provider"], row["model"], row["account"]):
                generation += 1
                self.db.execute("UPDATE sessions SET provider=?,model=?,account=?,provider_generation=? "
                                "WHERE id=?", (provider, model, account, generation, session))
            doc = self.current(row["soul"]) if row["soul"] else {"revision": 0}
            revision = doc.pop("revision")
            if row["soul"]:
                doc["lessons"] = [item for item in doc["lessons"]
                                  if item["project"] == row["project"]]
            return Turn(session, row["project"], row["soul"], row["epoch"], revision,
                        provider, model, encode(doc), account, generation)

    def record_source(self, soul: str, kind: str, content: str, *, project: str | None = None,
                      preference: dict[str, str] | None = None, event_ref: str | None = None,
                      event_order: int | None = None) -> str:
        """Fixture service ledger only. The model must NEVER select kind/ownership."""
        self.current(soul)
        bounded(content, 16000, "source")
        if kind not in SOURCE_KINDS:
            raise SoulError("Invalid source kind")
        if preference:
            if kind != "explicit_preference" or set(preference) != {"key", "value"}:
                raise SoulError("Structured preference requires explicit user provenance")
            if preference["key"] not in PREFERENCES:
                raise SoulError("Goals, tools, effort and permissions are not learned preferences")
            bounded(preference["value"], 160, "preference")
        elif kind == "explicit_preference":
            raise SoulError("No implicit language interpretation in this prototype")
        # Missing event_ref means a new synthetic fixture event. Production MUST
        # bind this to an existing service-owned run/event identity, not model text.
        event_ref = event_ref or "fixture:" + uid()
        bounded(event_ref, 200, "event reference")
        if event_order is not None and (type(event_order) is not int or event_order < 1):
            raise SoulError("Original service event order must be a positive integer")
        with self.transaction():
            old = self.db.execute("SELECT * FROM sources WHERE soul=? AND event_ref=?",
                                  (soul, event_ref)).fetchone()
            if old:
                if (old["project"], old["kind"], old["content"], old["preference"]) != (
                        project, kind, content, encode(preference)):
                    raise SoulError("Conflicting reuse of a durable source event")
                if event_order is not None and event_order != old["event_order"]:
                    raise SoulError("Conflicting original event order")
                return old["id"]
            if event_order is None:
                known = self.db.execute("SELECT COALESCE(MAX(event_order),0) FROM sources WHERE soul=?",
                                        (soul,)).fetchone()[0]
                event_order = 1 + max(known, self.current(soul)["learning_floor"])
            source = uid()
            self.db.execute("INSERT INTO sources(id,soul,project,kind,content,preference,event_ref,event_order) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (source, soul, project, kind, content, encode(preference), event_ref, event_order))
            return source

    def propose(self, soul: str, source: str, *, summary: str = "", tags: tuple[str, ...] = ()) -> str:
        with self.transaction():
            doc = self.current(soul)
            if doc["mode"] == "manual":
                raise SoulError("Learning is disabled")
            row = self.db.execute("SELECT * FROM sources WHERE id=? AND soul=?",
                                  (source, soul)).fetchone()
            if row is None:
                raise SoulError("Source belongs to another soul or is missing")
            if row["kind"] == "explicit_preference":
                if row["project"] is not None:
                    raise SoulError("Project preferences cannot silently become global personality")
                category, payload = "preference", json.loads(row["preference"])
            else:
                bounded(summary, 2000, "lesson")
                if len(tags) > 8:
                    raise SoulError("Too many discovery tags")
                for tag in tags:
                    bounded(tag, 40, "tag")
                category, payload = "lesson", {"summary": summary, "tags": list(tags)}
            old = self.db.execute("SELECT * FROM proposals WHERE soul=? AND source=? AND category=?",
                                  (soul, source, category)).fetchone()
            if old:
                if old["payload"] != encode(payload):
                    raise SoulError("Conflicting reuse of a source proposal")
                return old["id"]
            proposal = uid()
            self.db.execute("INSERT INTO proposals VALUES (?,?,?,?,?,'pending')",
                            (proposal, soul, source, category, encode(payload)))
            self._event(soul, doc["revision"], "proposed", {"proposal": proposal, "source": source})
            return proposal

    def accept(self, soul: str, proposal: str, expected: int, *, automatic: bool = False) -> int:
        with self.transaction():
            doc = self.current(soul)
            doc.pop("revision")
            item = self.db.execute("SELECT p.*,s.kind,s.project,s.event_order FROM proposals p JOIN sources s "
                                   "ON p.source=s.id WHERE p.id=? AND p.soul=?",
                                   (proposal, soul)).fetchone()
            if item is None or item["state"] != "pending":
                raise SoulError("Proposal unavailable or already decided")
            if doc["mode"] == "manual":
                raise SoulError("Learning is disabled")
            if item["event_order"] <= doc["learning_floor"]:
                raise SoulError("Source predates the latest rollback; a fresh user decision is required")
            if automatic and (doc["mode"] != "adaptive" or item["category"] != "preference"
                              or item["kind"] != "explicit_preference"):
                raise SoulError("Automatic learning requires opted-in explicit user preference")
            payload = json.loads(item["payload"])
            if item["category"] == "preference":
                newest = self.db.execute("SELECT MAX(event_order) FROM sources WHERE soul=? "
                    "AND kind='explicit_preference' AND project IS NULL "
                    "AND json_extract(preference,'$.key')=?", (soul, payload["key"])).fetchone()[0]
                if newest != item["event_order"]:
                    raise SoulError("Preference was superseded by a later explicit user correction")
                doc["preferences"][payload["key"]] = {
                    "value": payload["value"], "source": item["source"]}
            else:
                if item["kind"] != "verified_result" or item["project"] is None:
                    raise SoulError("A lesson requires a scoped observed result, not external/model claims")
                if len(doc["lessons"]) >= MAX_LESSONS:
                    raise SoulError("Lesson archive limit reached; no silent deletion")
                doc["lessons"].append({"id": proposal, "project": item["project"], **payload,
                    "source": item["source"], "support": "source-linked interpretation; not proof",
                    "helpful": 0, "unhelpful": 0})
            revision = self._save(soul, expected, doc, "learned", {
                "proposal": proposal, "automatic": automatic, "source": item["source"]})
            self.db.execute("UPDATE proposals SET state='accepted' WHERE id=?", (proposal,))
            return revision

    def feedback(self, soul: str, lesson: str, source: str, helpful: bool, expected: int) -> int:
        with self.transaction():
            doc = self.current(soul)
            revision = doc.pop("revision")
            row = self.db.execute("SELECT * FROM sources WHERE id=? AND soul=?", (source, soul)).fetchone()
            target = next((item for item in doc["lessons"] if item["id"] == lesson), None)
            if (row is None or row["kind"] != "user_feedback" or target is None
                    or row["project"] != target["project"] or type(helpful) is not bool):
                raise SoulError("Feedback requires a user source in the same project and soul")
            old = self.db.execute("SELECT * FROM feedback WHERE source=?", (source,)).fetchone()
            if old:
                if old["lesson"] != lesson or old["helpful"] != int(helpful):
                    raise SoulError("Conflicting reuse of feedback source")
                return revision  # Idempotent delivery; no extra weight or revision.
            if row["event_order"] <= doc["learning_floor"]:
                raise SoulError("Feedback predates the rollback; a fresh user decision is required")
            target["helpful" if helpful else "unhelpful"] += 1
            revision = self._save(soul, expected, doc, "feedback", {"source": source, "lesson": lesson})
            self.db.execute("INSERT INTO feedback VALUES (?,?,?)", (source, lesson, int(helpful)))
            return revision

    def history(self, soul: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute(
            "SELECT seq,revision,kind,detail FROM events WHERE soul=? ORDER BY seq", (soul,))]

    def read_source(self, turn: Turn, source: str, *, offset: int = 0, limit: int = 1000) -> dict[str, Any]:
        doc = json.loads(turn.document)
        allowed = {item["source"] for item in doc.get("lessons", [])}
        allowed.update(item["source"] for item in doc.get("preferences", {}).values())
        if source not in allowed:
            raise SoulError("Source is outside the active snapshot")
        row = self.db.execute("SELECT * FROM sources WHERE id=? AND soul=?", (source, turn.soul)).fetchone()
        if row is None or row["project"] not in (None, turn.project):
            raise SoulError("Foreign or missing original source")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 2000:
            raise SoulError("Invalid source page bounds")
        content = row["content"][offset:offset + limit]
        end = offset + len(content)
        return {"content": content, "kind": row["kind"], "event_ref": row["event_ref"],
                "event_order": row["event_order"],
                "next_offset": end if end < len(row["content"]) else None}


def search(turn: Turn, query: str, *, limit: int = 4) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= 10:
        raise SoulError("Search limit must be 1..10")
    bounded(query, 200, "query")
    items = json.loads(turn.document).get("lessons", [])
    ranked = []
    for item in items:
        matches = len(words(query) & words(item["summary"] + " " + " ".join(item["tags"])))
        if matches:
            usefulness = max(-2, min(2, item["helpful"] - item["unhelpful"]))
            ranked.append((matches, usefulness, item))
    ranked.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]["id"]))
    return [{"ref": f"soul:{turn.soul}:{turn.revision}:lesson:{item['id']}",
             "summary": item["summary"][:160], "summary_truncated": len(item["summary"]) > 160,
             "source": item["source"], "support": item["support"]}
            for _, _, item in ranked[:limit]]


def read(turn: Turn, ref: str, *, offset: int = 0, limit: int = 1000) -> dict[str, Any]:
    prefix = f"soul:{turn.soul}:{turn.revision}:lesson:"
    if not ref.startswith(prefix):
        raise SoulError("Reference is foreign or stale; search the active snapshot")
    item = next((item for item in json.loads(turn.document).get("lessons", [])
                 if item["id"] == ref[len(prefix):]), None)
    if item is None:
        raise SoulError("Lesson is outside this turn's project or snapshot")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 2000:
        raise SoulError("Invalid page bounds")
    content = item["summary"][offset:offset + limit]
    end = offset + len(content)
    return {"content": content, "source": item["source"], "support": item["support"],
            "next_offset": end if end < len(item["summary"]) else None,
            "revision": turn.revision}
