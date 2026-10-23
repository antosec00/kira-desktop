"""Transactional identity ledger. No provider calls, filesystem or inferred learning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from kira.domain.contracts import Json, KiraError
from kira.domain.souls import MAX_MEMORIES, MAX_SOURCE_BYTES, canonical, capsule, integer, text, validate_profile
from kira.service.history_tools import mirrors_kira_tool
from kira.service.storage import Store, new_id, now


class SoulStore:
    def __init__(self, store: Store) -> None:
        self.store, self.db = store, store.db
        self._savepoint = 0

    @contextmanager
    def atomic(self) -> Iterator[None]:
        self._savepoint += 1
        point = f"soul_{self._savepoint}"
        self.db.execute(f"SAVEPOINT {point}")
        try:
            yield
        except BaseException:
            self.db.execute(f"ROLLBACK TO {point}")
            self.db.execute(f"RELEASE {point}")
            raise
        else:
            self.db.execute(f"RELEASE {point}")

    @staticmethod
    def fingerprint(value: Any) -> str:
        try:
            return hashlib.sha256(canonical(value).encode()).hexdigest()
        except (TypeError, ValueError, UnicodeError) as exc:
            raise KiraError("INVALID_ARGUMENT", "Profile input must be finite UTF-8 JSON") from exc

    def operation(self, method: str, params: Json, action: Callable[[], Json]) -> Json:
        request_id = text(params.get("request_id"), "request_id")
        digest = self.fingerprint({"method": method, "params": params})
        with self.atomic():
            row = self.db.execute("SELECT * FROM soul_operations WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row["fingerprint"] != digest or row["method"] != method:
                    raise KiraError("CONFLICT", "Profile request_id already has different input")
                return dict(json.loads(row["result"]))
            result = action()
            result["receipt"] = {"request_id": request_id, "method": method, "status": "completed"}
            self.db.execute("INSERT INTO soul_operations VALUES (?,?,?,?,?)",
                            (request_id, method, digest, canonical(result), now()))
            return result

    def event_cursor(self) -> int:
        return int(self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM events").fetchone()[0])

    def get(self, soul_id: str, revision: int | None = None) -> Json:
        text(soul_id, "soul_id")
        if revision is not None:
            integer(revision, "revision", 1)
        row = self.db.execute("SELECT r.*,s.created_at origin_created,s.updated_at,s.learning_floor "
            "FROM souls s JOIN soul_revisions r ON r.soul_id=s.id "
            "WHERE s.id=? AND r.revision=COALESCE(?,s.revision)", (soul_id, revision)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Kira profile revision not found")
        count = self.db.execute("SELECT COUNT(*) FROM soul_revision_memories WHERE soul_id=? AND revision=?",
                               (soul_id, row["revision"])).fetchone()[0]
        result: Json = {"id": soul_id, "name": row["name"], "core": row["core"], "mode": row["mode"],
            "revision": row["revision"], "archived": bool(row["archived"]),
            "preferences": json.loads(row["preferences"]), "memory_count": count,
            "created_at": row["origin_created"], "updated_at": row["updated_at"]}
        result["preview"] = capsule(result, selected=True, memory_count=count)
        return result

    def check(self, soul_id: str, expected: Any, *, archived: bool = False) -> Json:
        integer(expected, "expected_revision", 1)
        soul = self.get(soul_id)
        if soul["revision"] != expected:
            raise KiraError("CONFLICT", "Profile changed; reload its current revision")
        if soul["archived"] and not archived:
            raise KiraError("ARCHIVED", "Restore an earlier revision before editing this profile")
        return soul

    def memories(self, soul_id: str, revision: int, project_id: str | None = None,
                 session_id: str | None = None) -> list[Json]:
        clauses, values = ["r.soul_id=?", "r.revision=?"], [soul_id, revision]
        if project_id is not None:
            clauses.append("m.project_id=?")
            values.append(project_id)
        if session_id is not None:
            clauses.append("(m.scope='project' OR m.session_id=?)")
            values.append(session_id)
        rows = self.db.execute("SELECT m.*,r.score FROM soul_revision_memories r JOIN soul_memories m ON m.id=r.memory_id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY m.created_at DESC,m.id", values).fetchall()
        return [dict(row) for row in rows]

    def save(self, soul: Json, expected: int, reason: str, *, memory_ids: list[str] | None = None,
             memory_scores: dict[str, int] | None = None) -> Json:
        validate_profile(soul["name"], soul["core"], soul["mode"])
        capsule({**soul, "revision": expected + 1}, selected=True)
        timestamp = now()
        if self.db.execute("UPDATE souls SET revision=?,updated_at=? WHERE id=? AND revision=?",
                           (expected + 1, timestamp, soul["id"], expected)).rowcount != 1:
            raise KiraError("CONFLICT", "Profile changed; reload its current revision")
        self.db.execute("INSERT INTO soul_revisions VALUES (?,?,?,?,?,?,?,?,?)",
            (soul["id"], expected + 1, soul["name"], soul["core"], soul["mode"],
             int(soul["archived"]), canonical(soul["preferences"]), reason, timestamp))
        if memory_ids is None:
            self.db.execute("INSERT INTO soul_revision_memories SELECT soul_id,?,memory_id,score FROM soul_revision_memories "
                            "WHERE soul_id=? AND revision=?", (expected + 1, soul["id"], expected))
        else:
            if len(memory_ids) > MAX_MEMORIES:
                raise KiraError("LIMIT_REACHED", "Profile experience limit reached; retire unused memories first")
            scores = {item["id"]: item["score"] for item in self.memories(soul["id"], expected)}
            if memory_scores is not None:
                if set(memory_scores) - set(memory_ids):
                    raise KiraError("INVALID_ARGUMENT", "Usefulness refers to an inactive experience")
                scores.update(memory_scores)
            for identifier in memory_ids:
                score = integer(scores.get(identifier, 0), "usefulness score", -2, 2)
                self.db.execute("INSERT INTO soul_revision_memories VALUES (?,?,?,?)",
                                (soul["id"], expected + 1, identifier, score))
        return self.get(soul["id"])

    def create(self, name: str, core: str, mode: str) -> Json:
        validate_profile(name, core, mode)
        identifier, timestamp = new_id(), now()
        self.db.execute("INSERT INTO souls VALUES (?,1,0,?,?)", (identifier, timestamp, timestamp))
        self.db.execute("INSERT INTO soul_revisions VALUES (?,1,?,?,?,0,'{}','created',?)",
                        (identifier, name, core, mode, timestamp))
        return self.get(identifier)

    def rollback(self, soul_id: str, expected: int, target: int) -> Json:
        current = self.check(soul_id, expected, archived=True)
        old = self.get(soul_id, integer(target, "target_revision", 1, current["revision"]))
        memories = self.memories(soul_id, target)
        self.db.execute("UPDATE souls SET learning_floor=MAX(learning_floor,?) WHERE id=?",
                        (self.event_cursor(), soul_id))
        self.db.execute("UPDATE soul_proposals SET status='superseded',updated_at=? WHERE soul_id=? AND status='pending'",
                        (now(), soul_id))
        return self.save(old, expected, f"rollback:{target}", memory_ids=[item["id"] for item in memories],
                         memory_scores={item["id"]: item["score"] for item in memories})

    def capture_source(self, soul_id: str, project_id: str, session_id: str,
                       source_run_id: Any, source_seq: Any) -> Json:
        text(source_run_id, "source_run_id")
        integer(source_seq, "source_seq", 1)
        row = self.db.execute("SELECT e.rowid event_order,e.*,r.session_id FROM events e JOIN runs r ON r.id=e.run_id "
            "JOIN sessions s ON s.id=r.session_id WHERE e.run_id=? AND e.seq=? AND s.id=? AND s.project_id=?",
            (source_run_id, source_seq, session_id, project_id)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Learning evidence must be an original event in this chat")
        if row["type"] not in {"message.user", "tool.completed"}:
            raise KiraError("INVALID_SOURCE", "Only original user messages and tool outcomes are learning sources")
        if row["type"] == "tool.completed":
            payload = json.loads(row["payload"])
            name = payload.get("name")
            if (not isinstance(name, str) or name.startswith(("kira.", "kira_", "kira_history."))
                    or mirrors_kira_tool(name, payload.get("result"))):
                raise KiraError("INVALID_SOURCE", "Recalled knowledge, generated suggestions, Kira metadata and "
                                "provider copies of Kira tool calls are not new learning evidence")
        floor = self.db.execute("SELECT learning_floor FROM souls WHERE id=?", (soul_id,)).fetchone()
        if floor is None or row["event_order"] <= floor[0]:
            raise KiraError("STALE_SOURCE", "This evidence predates the last profile rollback")
        original = text(row["payload"], "original evidence", MAX_SOURCE_BYTES)
        digest = hashlib.sha256(original.encode()).hexdigest()
        prior = self.db.execute("SELECT * FROM soul_sources WHERE soul_id=? AND run_id=? AND seq=?",
                               (soul_id, source_run_id, source_seq)).fetchone()
        if prior:
            if prior["digest"] != digest or prior["event_order"] != row["event_order"]:
                raise KiraError("CONFLICT", "Original evidence changed after it was recorded")
            return dict(prior)
        identifier = new_id()
        self.db.execute("INSERT INTO soul_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, soul_id, project_id, session_id, source_run_id, source_seq,
             row["event_order"], row["type"], original, digest, now()))
        return dict(self.db.execute("SELECT * FROM soul_sources WHERE id=?", (identifier,)).fetchone())

    def propose(self, soul_id: str, project_id: str, session_id: str, args: Json) -> Json:
        kind, scope = args.get("kind", "lesson"), args.get("scope", "chat")
        if (not isinstance(kind, str) or kind not in {"lesson", "preference"}
                or not isinstance(scope, str) or scope not in {"chat", "project"}):
            raise KiraError("INVALID_ARGUMENT", "Invalid experience kind or scope")
        statement = text(args.get("statement"), "statement", 1600)
        applicability = text(args.get("applicability"), "applicability", 600)
        source = self.capture_source(soul_id, project_id, session_id, args.get("source_run_id"), args.get("source_seq"))
        key, value = args.get("key"), args.get("value")
        if kind == "preference":
            from kira.domain.souls import preference
            key, value = preference(key, value)
            if value is None or source["kind"] != "message.user":
                raise KiraError("INVALID_SOURCE", "A preference requires an original user message and a value")
        elif key is not None or value is not None:
            raise KiraError("INVALID_ARGUMENT", "Lessons cannot set collaboration preferences")
        prior = self.db.execute("SELECT * FROM soul_proposals WHERE soul_id=? AND source_id=? AND kind=?",
                               (soul_id, source["id"], kind)).fetchone()
        if prior:
            expected = (scope, statement, applicability, key, value)
            actual = tuple(prior[field] for field in ("scope", "statement", "applicability", "preference_key", "preference_value"))
            if expected != actual:
                raise KiraError("CONFLICT", "This evidence already supports a different proposal of this kind")
            return self.proposal(dict(prior))
        count = self.db.execute("SELECT COUNT(*) FROM soul_proposals WHERE soul_id=? AND status='pending'", (soul_id,)).fetchone()[0]
        if count >= 512:
            raise KiraError("LIMIT_REACHED", "Review pending profile suggestions before adding more")
        identifier, timestamp = new_id(), now()
        self.db.execute("INSERT INTO soul_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, soul_id, source["id"], project_id, session_id, kind, scope, statement,
             applicability, key, value, "pending", timestamp, timestamp))
        return self.proposal(dict(self.db.execute("SELECT * FROM soul_proposals WHERE id=?", (identifier,)).fetchone()))

    @staticmethod
    def proposal(row: Json) -> Json:
        result = dict(row)
        result["key"] = result.pop("preference_key")
        result["value"] = result.pop("preference_value")
        result["effect_scope"] = "profile" if row["kind"] == "preference" else row["scope"]
        return result

    def decide(self, soul_id: str, expected: int, proposal_id: str, decision: str) -> Json:
        soul = self.check(soul_id, expected)
        row = self.db.execute("SELECT p.*,s.event_order FROM soul_proposals p JOIN soul_sources s ON s.id=p.source_id "
                              "WHERE p.id=? AND p.soul_id=?", (proposal_id, soul_id)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Profile suggestion not found")
        if row["status"] != "pending":
            raise KiraError("CONFLICT", "Profile suggestion was already reviewed or superseded")
        if not isinstance(decision, str) or decision not in {"accept", "reject"}:
            raise KiraError("INVALID_ARGUMENT", "Unknown profile suggestion decision")
        status = "rejected"
        if decision == "accept":
            floor = self.db.execute("SELECT learning_floor FROM souls WHERE id=?", (soul_id,)).fetchone()[0]
            if row["event_order"] <= floor:
                raise KiraError("STALE_SOURCE", "This evidence predates the last profile rollback")
            status = "accepted"
            if row["kind"] == "preference":
                previous = soul["preferences"].get(row["preference_key"], {})
                # User edits at the same evidence cursor outrank delayed event interpretations.
                if row["event_order"] <= previous.get("source_order", -1):
                    status = "superseded"
                else:
                    soul["preferences"][row["preference_key"]] = {
                        "value": row["preference_value"], "source_id": row["source_id"], "source_order": row["event_order"]}
                    soul = self.save(soul, expected, "preference_accepted")
            else:
                memory_id = new_id()
                self.db.execute("INSERT INTO soul_memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (memory_id, soul_id, proposal_id, row["source_id"], row["project_id"], row["session_id"],
                     row["scope"], row["kind"], row["statement"], row["applicability"], now()))
                memory_ids = [item["id"] for item in self.memories(soul_id, expected)] + [memory_id]
                soul = self.save(soul, expected, "lesson_accepted", memory_ids=memory_ids)
        self.db.execute("UPDATE soul_proposals SET status=?,updated_at=? WHERE id=?", (status, now(), proposal_id))
        proposal = self.proposal(dict(self.db.execute("SELECT * FROM soul_proposals WHERE id=?", (proposal_id,)).fetchone()))
        return {"soul": soul, "proposal": proposal}
