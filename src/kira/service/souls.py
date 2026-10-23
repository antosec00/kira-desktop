"""Service-owned personalized identities and provenance-checked learning suggestions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from kira.domain.contracts import Json, KiraError
from kira.domain.souls import (
    PREFERENCES, canonical, capsule, fields, integer, preference, text, validate_profile,
)
from kira.service.soul_store import SoulStore
from kira.service.project_context import ProjectChats
from kira.service.storage import new_id, now
from kira.service.tool_policy import kira_tools_enabled

_MUTATIONS = {
    "soul.create": ({"request_id", "name", "core", "mode"}, {"request_id", "name", "core"}),
    "soul.update": ({"request_id", "soul_id", "expected_revision", "name", "core", "mode"},
                    {"request_id", "soul_id", "expected_revision"}),
    "soul.bind": ({"request_id", "project_id", "session_id", "soul_id"},
                  {"request_id", "project_id", "session_id", "soul_id"}),
    "soul.rollback": ({"request_id", "soul_id", "expected_revision", "target_revision"},
                      {"request_id", "soul_id", "expected_revision", "target_revision"}),
    "soul.archive": ({"request_id", "soul_id", "expected_revision"}, {"request_id", "soul_id", "expected_revision"}),
    "soul.decide": ({"request_id", "soul_id", "expected_revision", "proposal_id", "decision"},
                    {"request_id", "soul_id", "expected_revision", "proposal_id", "decision"}),
    "soul.forget": ({"request_id", "soul_id", "expected_revision", "memory_id"},
                    {"request_id", "soul_id", "expected_revision", "memory_id"}),
    "soul.preference": ({"request_id", "soul_id", "expected_revision", "key", "value"},
                        {"request_id", "soul_id", "expected_revision", "key", "value"}),
    "soul.feedback": ({"request_id", "soul_id", "expected_revision", "memory_id", "helpful"},
                      {"request_id", "soul_id", "expected_revision", "memory_id", "helpful"}),
    "soul.import": ({"request_id", "profile"}, {"request_id", "profile"}),
}
_READS = {
    "soul.list": ({"offset", "limit"}, set()),
    "soul.get": ({"soul_id"}, {"soul_id"}),
    "soul.snapshot": ({"project_id", "session_id"}, {"project_id", "session_id"}),
    "soul.preview": ({"name", "core", "mode", "preferences"}, {"name", "core"}),
    "soul.proposals": ({"soul_id", "project_id", "session_id", "offset", "limit"}, {"soul_id"}),
    "soul.memories": ({"soul_id", "project_id", "session_id", "query", "offset", "limit"}, {"soul_id"}),
    "soul.history": ({"soul_id", "offset", "limit"}, {"soul_id"}),
    "soul.source": ({"soul_id", "source_id", "project_id", "session_id", "offset", "limit"},
                    {"soul_id", "source_id", "project_id", "session_id"}),
    "soul.export": ({"soul_id"}, {"soul_id"}),
}
METHODS = frozenset({*_MUTATIONS, *_READS})
_ALIASES = {"tono": "tone", "dettaglio": "detail", "collaborazione": "collaboration", "lingua": "language"}
_COMMAND = re.compile(r"/anima preferenza (tone|detail|collaboration|language|tono|dettaglio|collaborazione|lingua)=([^\r\n]+)")


class Souls:
    def __init__(self, service: Any) -> None:
        self.service, self.store = service, service.store
        self.data = SoulStore(self.store)
        self.db = self.store.db
        self._cursor_key = secrets.token_bytes(32)

    def _scope(self, project_id: Any, session_id: Any) -> tuple[Json, Json]:
        project = self.store.one("projects", text(project_id, "project_id"))
        session = self.store.one("sessions", text(session_id, "session_id"))
        if session["project_id"] != project_id or self.db.execute(
                "SELECT 1 FROM team_members WHERE session_id=?", (session_id,)).fetchone():
            raise KiraError("NOT_FOUND", "A Kira profile belongs to an ordinary chat in this project")
        return project, session

    def _page(self, params: Json, values: list[Json], key: str) -> Json:
        offset = integer(params.get("offset", 0), "offset", 0, 1000000)
        limit = integer(params.get("limit", 20), "limit", 1, 50)
        selected = values[offset:offset + limit]
        more = offset + len(selected) < len(values)
        return {key: selected, "has_more": more, "next_offset": offset + len(selected) if more else None}

    def _profile_preferences(self, values: Any) -> Json:
        if not isinstance(values, dict) or set(values) - PREFERENCES:
            raise KiraError("INVALID_ARGUMENT", "Unknown collaboration preferences")
        result = {}
        for key, value in values.items():
            _, clean = preference(key, value)
            if clean is not None:
                result[key] = {"value": clean, "source_id": None, "source_order": self.data.event_cursor()}
        return result

    async def dispatch(self, method: str, params: Json) -> Json:
        contract = _MUTATIONS.get(method, _READS.get(method))
        if contract is None:
            raise KiraError("METHOD_NOT_FOUND", "Unknown Kira profile method")
        fields(params, *contract)
        if method in _MUTATIONS:
            if getattr(self.service, "_closing", False):
                raise KiraError("BUSY", "The service is stopping")
            if method == "soul.bind" and not self._recorded(params):
                project, session, _, changed = self._binding_change(params)
                web = getattr(self.service, "web", None)
                if changed and web is not None:
                    # The change retires the provider generation, and with it the
                    # chat's browser, as a provider change does. Manual control refuses it.
                    await web.before_configuration(project["id"], session["id"])
            return self.data.operation(method, params, lambda: self._mutate(method, params))
        if method == "soul.list":
            offset = integer(params.get("offset", 0), "offset", 0, 1000000)
            limit = integer(params.get("limit", 20), "limit", 1, 50)
            rows = self.db.execute("SELECT id FROM souls ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                                   (limit + 1, offset)).fetchall()
            return {"souls": [self.data.get(row[0]) for row in rows[:limit]],
                    "has_more": len(rows) > limit, "next_offset": offset + limit if len(rows) > limit else None}
        if method == "soul.preview":
            validate_profile(params["name"], params["core"], params.get("mode", "suggest"))
            preview = capsule({"id": "preview", "revision": 1, "name": params["name"], "core": params["core"],
                "preferences": self._profile_preferences(params.get("preferences", {}))}, selected=True)
            return {"preview": preview}
        if method == "soul.snapshot":
            snapshot = self.snapshot(params["project_id"], params["session_id"])
            session = self.store.one("sessions", params["session_id"])
            return {"snapshot": snapshot, "session": ProjectChats(self.store).enrich(session)}
        soul = self.data.get(text(params.get("soul_id"), "soul_id"))
        if method == "soul.get":
            return {"soul": soul}
        if method == "soul.export":
            return {"profile": {"format": "kira-soul-v1", "name": soul["name"], "core": soul["core"], "mode": soul["mode"],
                "preferences": {key: item["value"] for key, item in soul["preferences"].items() if item["value"] is not None}}}
        if method == "soul.history":
            offset = integer(params.get("offset", 0), "offset", 0, 1000000)
            limit = integer(params.get("limit", 20), "limit", 1, 50)
            rows = self.db.execute("SELECT * FROM soul_revisions WHERE soul_id=? ORDER BY revision DESC LIMIT ? OFFSET ?",
                                   (soul["id"], limit + 1, offset)).fetchall()
            history = []
            for row in rows[:limit]:
                detail = self.data.get(soul["id"], row["revision"])
                detail.update(reason=row["reason"], created_at=row["created_at"])
                history.append(detail)
            return {"history": history, "has_more": len(rows) > limit,
                    "next_offset": offset + limit if len(rows) > limit else None}
        project_id, session_id = params.get("project_id"), params.get("session_id")
        if session_id is not None:
            if project_id is None:
                raise KiraError("INVALID_ARGUMENT", "A chat filter requires its project")
            self._scope(project_id, session_id)
        elif project_id is not None:
            self.store.one("projects", text(project_id, "project_id"))
        if method == "soul.source":
            return {"source": self._source(soul["id"], params["source_id"], params["project_id"], params["session_id"],
                params.get("offset", 0), params.get("limit", 4000))}
        if method == "soul.proposals":
            clauses, values = ["soul_id=?"], [soul["id"]]
            if project_id is not None:
                clauses.append("project_id=?")
                values.append(project_id)
            if session_id is not None:
                clauses.append("(scope='project' OR session_id=?)")
                values.append(session_id)
            offset = integer(params.get("offset", 0), "offset", 0, 1000000)
            limit = integer(params.get("limit", 20), "limit", 1, 50)
            rows = self.db.execute("SELECT * FROM soul_proposals WHERE " + " AND ".join(clauses) +
                " ORDER BY created_at DESC,id LIMIT ? OFFSET ?", [*values, limit + 1, offset]).fetchall()
            return {"proposals": [self.data.proposal(dict(row)) for row in rows[:limit]],
                    "has_more": len(rows) > limit, "next_offset": offset + limit if len(rows) > limit else None}
        memories = self.data.memories(soul["id"], soul["revision"], project_id, session_id)
        query = text(params.get("query", ""), "query", 800, empty=True)
        words = query.casefold().split()
        return self._page(params, [item for item in memories if all(word in
            (item["statement"] + " " + item["applicability"]).casefold() for word in words)], "memories")

    def _mutate(self, method: str, params: Json) -> Json:
        if method == "soul.create":
            return {"soul": self.data.create(params["name"], params["core"], params.get("mode", "suggest"))}
        if method == "soul.import":
            profile = params["profile"]
            fields(profile, {"format", "name", "core", "mode", "preferences"}, {"format", "name", "core", "mode", "preferences"})
            if profile["format"] != "kira-soul-v1":
                raise KiraError("INVALID_ARGUMENT", "Unsupported Kira profile format")
            preferences = self._profile_preferences(profile["preferences"])
            soul = self.data.create(profile["name"], profile["core"], profile["mode"])
            if preferences:
                soul["preferences"] = preferences
                soul = self.data.save(soul, soul["revision"], "user_import")
            return {"soul": soul}
        if method == "soul.bind":
            return self._bind(params)
        soul = self.data.check(text(params.get("soul_id"), "soul_id"), params.get("expected_revision"),
                               archived=method == "soul.rollback")
        expected = soul["revision"]
        if method == "soul.rollback":
            return {"soul": self.data.rollback(soul["id"], expected, params["target_revision"])}
        if method == "soul.decide":
            return self.data.decide(soul["id"], expected, text(params["proposal_id"], "proposal_id"), params["decision"])
        if method == "soul.preference":
            key, value = preference(params["key"], params["value"])
            source_id, timestamp = new_id(), now()
            original = canonical({"key": key, "value": value, "request_id": params["request_id"]})
            order = self.data.event_cursor()
            self.db.execute("INSERT INTO soul_sources VALUES (?,?,NULL,NULL,NULL,NULL,?,'user.preference',?,?,?)",
                (source_id, soul["id"], order, original, hashlib.sha256(original.encode()).hexdigest(), timestamp))
            soul["preferences"][key] = {"value": value, "source_id": source_id, "source_order": order}
            return {"soul": self.data.save(soul, expected, "user_preference")}
        if method in {"soul.forget", "soul.feedback"}:
            memory_id = text(params["memory_id"], "memory_id")
            records = self.data.memories(soul["id"], expected)
            memories = [item["id"] for item in records]
            if memory_id not in memories:
                raise KiraError("NOT_FOUND", "This experience is not active in the selected profile")
            if method == "soul.feedback":
                if type(params["helpful"]) is not bool:
                    raise KiraError("INVALID_ARGUMENT", "Usefulness feedback must be a boolean")
                current = next(item["score"] for item in records if item["id"] == memory_id)
                score = max(-2, min(2, current + (1 if params["helpful"] else -1)))
                return {"soul": self.data.save(soul, expected, "usefulness_feedback", memory_ids=memories,
                    memory_scores={memory_id: score}), "feedback": {"memory_id": memory_id, "helpful": params["helpful"],
                    "score": score, "meaning": "user-rated usefulness for retrieval; not evidence of truth"}}
            memories.remove(memory_id)
            return {"soul": self.data.save(soul, expected, "memory_retired", memory_ids=memories)}
        if method == "soul.archive":
            soul["archived"] = True
        elif method == "soul.update":
            if not set(params) & {"name", "core", "mode"}:
                raise KiraError("INVALID_ARGUMENT", "Provide at least one profile change")
            soul.update({key: params[key] for key in ("name", "core", "mode") if key in params})
        return {"soul": self.data.save(soul, expected, "archived" if method == "soul.archive" else "user_edit")}

    def _recorded(self, params: Json) -> bool:
        request_id = params.get("request_id")
        return isinstance(request_id, str) and self.db.execute(
            "SELECT 1 FROM soul_operations WHERE request_id=?", (request_id,)).fetchone() is not None

    def _binding_change(self, params: Json) -> tuple[Json, Json, Any, bool]:
        """Validate a selection; a change also requires an idle chat without pending work."""
        project, session = self._scope(params["project_id"], params["session_id"])
        soul_id = params["soul_id"]
        if soul_id is not None:
            soul = self.data.get(text(soul_id, "soul_id"))
            if soul["archived"]:
                raise KiraError("ARCHIVED", "Restore this profile before selecting it")
        previous = self.db.execute("SELECT soul_id,epoch FROM soul_bindings WHERE session_id=?", (session["id"],)).fetchone()
        changed = (previous is None and soul_id is not None) or (previous is not None and previous["soul_id"] != soul_id)
        if changed:
            self.service.workbench._idle(project)
            if self.db.execute("SELECT 1 FROM autonomous_goals WHERE session_id=? AND current=1 AND status='active'",
                               (session["id"],)).fetchone():
                raise KiraError("BUSY", "Pause this chat's autonomous objective before changing its Kira profile")
            if self.db.execute("SELECT 1 FROM prompt_queue WHERE session_id=? AND status IN ('pending','starting')",
                               (session["id"],)).fetchone():
                raise KiraError("BUSY", "Finish or remove queued prompts before changing this Kira profile")
        return project, session, previous, changed

    def _bind(self, params: Json) -> Json:
        project, session, previous, changed = self._binding_change(params)
        soul_id = params["soul_id"]
        if changed:
            self.service.provider_routing.reset_native(session)
        epoch = (previous["epoch"] if previous else 0) + int(changed or previous is None)
        self.db.execute("INSERT INTO soul_bindings VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "soul_id=excluded.soul_id,epoch=excluded.epoch,updated_at=excluded.updated_at",
            (session["id"], project["id"], soul_id, epoch, now()))
        return {"snapshot": self.snapshot(project["id"], session["id"]), "identity_changed": changed,
                "conversation_retained": True}

    def snapshot(self, project_id: str, session_id: str) -> Json:
        _, session = self._scope(project_id, session_id)
        binding = self.db.execute("SELECT * FROM soul_bindings WHERE session_id=?", (session_id,)).fetchone()
        soul = self.data.get(binding["soul_id"]) if binding and binding["soul_id"] else None
        selected = binding is not None
        enabled = soul is not None and not soul["archived"]
        memories = self.data.memories(soul["id"], soul["revision"], project_id, session_id) if enabled and soul else []
        preview = capsule(soul, selected=selected, tools=kira_tools_enabled(session),
                          memory_count=len(memories))
        return {"project_id": project_id, "session_id": session_id, "soul": soul,
            "epoch": binding["epoch"] if binding else 0, "selected": selected, "disabled": selected and not enabled,
            "memory_ids": [memory["id"] for memory in memories], "preview": preview}

    def context(self, session: Json) -> str:
        """Read canonical current state for maintenance; never learn from compaction."""
        return str(self.snapshot(session["project_id"], session["id"])["preview"]["text"])

    def bind_run(self, run_id: str, project: Json, session: Json) -> str:
        """Freeze exactly once; repeated calls and compaction return identical bytes."""
        self._scope(project["id"], session["id"])
        run = self.store.one("runs", text(run_id, "run_id"))
        if run["session_id"] != session["id"]:
            raise KiraError("NOT_FOUND", "The run belongs to another chat")
        with self.data.atomic():
            row = self.db.execute("SELECT capsule FROM soul_run_snapshots WHERE run_id=?", (run_id,)).fetchone()
            if row:
                return str(row[0])
            self._explicit_preference(run, project, session)
            snapshot = self.snapshot(project["id"], session["id"])
            soul, preview = snapshot["soul"], snapshot["preview"]
            self.db.execute("INSERT INTO soul_run_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, project["id"], session["id"], soul["id"] if soul else None,
                 soul["revision"] if soul else None, snapshot["epoch"], int(snapshot["selected"]),
                 canonical(snapshot), preview["text"], preview["sha256"], now()))
            return str(preview["text"])

    def _explicit_preference(self, run: Json, project: Json, session: Json) -> None:
        match = _COMMAND.fullmatch(run["prompt"])
        if match is None:
            return
        snapshot = self.snapshot(project["id"], session["id"])
        soul = snapshot["soul"]
        if soul is None or soul["archived"] or soul["mode"] == "manual":
            return
        key, value = preference(_ALIASES.get(match[1], match[1]), match[2])
        sources = self.db.execute("SELECT seq,payload FROM events WHERE run_id=? AND type='message.user' ORDER BY seq", (run["id"],)).fetchall()
        source = next((row for row in sources if json.loads(row["payload"]).get("content") == run["prompt"]), None)
        if source is None:
            return
        proposal = self.data.propose(soul["id"], project["id"], session["id"], {
            "kind": "preference", "scope": "chat", "statement": run["prompt"],
            "applicability": "Explicit user collaboration preference", "key": key, "value": value,
            "source_run_id": run["id"], "source_seq": source["seq"]})
        if soul["mode"] == "adaptive" and proposal["status"] == "pending":
            self.data.decide(soul["id"], soul["revision"], proposal["id"], "accept")

    def _run_snapshot(self, run_id: str, *, active: bool = True) -> Json:
        run = self.store.one("runs", text(run_id, "run_id"))
        session = self.store.one("sessions", run["session_id"])
        self._scope(session["project_id"], session["id"])
        if active and (run["state"] not in {"running", "awaiting_input"} or getattr(self.service, "_closing", False)):
            raise KiraError("CANCELLED", "The originating run is no longer active")
        row = self.db.execute("SELECT snapshot FROM soul_run_snapshots WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "This run has no frozen Kira profile")
        return dict(json.loads(row[0]))

    async def tool_call(self, run_id: str, call_id: str, name: str, args: Json) -> Json:
        try:
            if name != "kira.soul_propose":
                raise KiraError("UNSUPPORTED", "Unknown profile tool")
            text(call_id, "call_id", 256)
            fields(args, {"kind", "scope", "statement", "applicability", "source_run_id", "source_seq", "key", "value"},
                   {"statement", "applicability", "source_run_id", "source_seq"})
            snapshot = self._run_snapshot(run_id)
            soul = snapshot["soul"]
            if soul is None or soul["archived"] or soul["mode"] == "manual":
                raise KiraError("PERMISSION_DENIED", "The selected Kira profile does not accept learning suggestions")
            digest = self.data.fingerprint({"name": name, "args": args})
            with self.data.atomic():
                row = self.db.execute("SELECT * FROM soul_tool_calls WHERE run_id=? AND call_id=?", (run_id, call_id)).fetchone()
                if row:
                    if row["fingerprint"] != digest:
                        raise KiraError("CONFLICT", "Profile tool call_id already has different arguments")
                    return dict(json.loads(row["result"]))
                proposal = self.data.propose(soul["id"], snapshot["project_id"], snapshot["session_id"], args)
                result = {"ok": True, "proposal": proposal, "applied": False, "review_required": True}
                self.db.execute("INSERT INTO soul_tool_calls VALUES (?,?,?,?)", (run_id, call_id, digest, canonical(result)))
                return result
        except KiraError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}

    def _enabled(self, snapshot: Json) -> Json:
        soul = snapshot["soul"]
        if soul is None or soul["archived"]:
            raise KiraError("NOT_FOUND", "No personalized Kira profile is active in this chat")
        return dict(soul)

    def _ref(self, soul: Json, kind: str, identifier: str) -> str:
        return f"soul:{soul['id']}@{soul['revision']}:{kind}:{identifier}"

    def _cursor(self, offset: int, binding: str) -> str:
        payload = canonical({"offset": offset, "binding": binding}).encode()
        return base64.urlsafe_b64encode(payload).decode() + "." + hmac.new(self._cursor_key, payload, hashlib.sha256).hexdigest()

    def _cursor_offset(self, cursor: Any, binding: str) -> int:
        if cursor is None:
            return 0
        text(cursor, "cursor", 2048)
        try:
            body, signature = cursor.split(".")
            payload = base64.urlsafe_b64decode(body)
            if not hmac.compare_digest(signature, hmac.new(self._cursor_key, payload, hashlib.sha256).hexdigest()):
                raise ValueError
            value = json.loads(payload)
            if value["binding"] != binding:
                raise ValueError
            return integer(value["offset"], "cursor offset", 0, 1000000)
        except (ValueError, KeyError, TypeError) as exc:
            raise KiraError("INVALID_ARGUMENT", "The experience cursor belongs to another query or profile revision") from exc

    def _search(self, snapshot: Json, query: str, limit: int, cursor: str | None) -> Json:
        text(query, "query", 800, empty=True)
        if len(query) > 200:
            raise KiraError("LIMIT_REACHED", "Experience search accepts at most 200 characters")
        integer(limit, "limit", 1, 20)
        if snapshot["soul"] is None or snapshot["disabled"]:
            return {"items": [], "has_more": False, "next_cursor": None}
        soul = self._enabled(snapshot)
        binding = self.data.fingerprint({"project": snapshot["project_id"], "chat": snapshot["session_id"],
            "soul": soul["id"], "revision": soul["revision"], "epoch": snapshot["epoch"], "query": query, "limit": limit})
        offset = self._cursor_offset(cursor, binding)
        memories = self.data.memories(soul["id"], soul["revision"], snapshot["project_id"], snapshot["session_id"])
        words = query.casefold().split()
        found = [item for item in memories if item["id"] in snapshot["memory_ids"] and all(word in
            (item["statement"] + " " + item["applicability"]).casefold() for word in words)]
        def relevance(item: Json) -> tuple[int, int, int]:
            statement = item["statement"].casefold()
            combined = statement + " " + item["applicability"].casefold()
            return (sum(word in statement for word in words),
                    sum(min(3, combined.count(word)) for word in words), item["score"])
        # Stable sort preserves creation chronology for equally relevant/useful records.
        found.sort(key=relevance, reverse=True)
        selected = found[offset:offset + limit]
        items = [{"ref": self._ref(soul, "memory", item["id"]), "kind": "soul_memory", "title": item["statement"][:160],
            "summary": item["applicability"][:300], "revision": soul["revision"], "scope": item["scope"],
            "usefulness": item["score"],
            "source_ref": self._ref(soul, "source", item["source_id"])} for item in selected]
        more = offset + len(selected) < len(found)
        return {"items": items, "has_more": more, "next_cursor": self._cursor(offset + len(selected), binding) if more else None}

    def search(self, run_id: str, query: str, limit: int = 8, cursor: str | None = None) -> Json:
        return self._search(self._run_snapshot(run_id), query, limit, cursor)

    def search_current(self, project_id: str, session_id: str, query: str,
                       limit: int = 8, cursor: str | None = None) -> Json:
        return self._search(self.snapshot(project_id, session_id), query, limit, cursor)

    def _source(self, soul_id: str, source_id: str, project_id: str, session_id: str,
                offset: int, limit: int) -> Json:
        text(source_id, "source_id")
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 12000)
        row = self.db.execute("SELECT * FROM soul_sources WHERE id=? AND soul_id=?", (source_id, soul_id)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Profile evidence not found")
        project_shared = self.db.execute("SELECT 1 FROM soul_memories WHERE source_id=? AND project_id=? AND scope='project'",
                                         (source_id, project_id)).fetchone()
        if row["project_id"] is not None and (row["project_id"] != project_id or
                row["session_id"] != session_id and not project_shared):
            raise KiraError("NOT_FOUND", "Original evidence belongs to another chat")
        content = row["original"]
        chunk = content[offset:offset + limit]
        truncated = offset + len(chunk) < len(content)
        return {"id": source_id, "kind": row["kind"], "project_id": row["project_id"], "session_id": row["session_id"],
            "run_id": row["run_id"], "seq": row["seq"], "event_order": row["event_order"], "sha256": row["digest"],
            "content": chunk, "offset": offset, "next_offset": offset + len(chunk) if truncated else None,
            "truncated": truncated, "source_bytes": len(content.encode()), "format": "original-event-payload-json"}

    def _read(self, snapshot: Json, ref: str, offset: int, limit: int) -> Json:
        text(ref, "ref", 256)
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 12000)
        soul = self._enabled(snapshot)
        prefix = f"soul:{soul['id']}@{soul['revision']}:"
        if not ref.startswith(prefix):
            raise KiraError("STALE_REFERENCE", "Discover this profile's current scoped experience reference again")
        kind, _, identifier = ref[len(prefix):].partition(":")
        memories = self.data.memories(soul["id"], soul["revision"], snapshot["project_id"], snapshot["session_id"])
        allowed = [item for item in memories if item["id"] in snapshot["memory_ids"]]
        if kind == "source":
            if identifier not in {item["source_id"] for item in allowed}:
                raise KiraError("NOT_FOUND", "This source was not admitted to this profile snapshot")
            source = self._source(soul["id"], identifier, snapshot["project_id"], snapshot["session_id"], offset, limit)
            return {"ref": ref, "requested_ref": ref, "revision": soul["revision"], **source,
                    "provenance": {"origin": "original_service_event", "run_id": source["run_id"], "seq": source["seq"]}}
        memory = next((item for item in allowed if item["id"] == identifier), None) if kind == "memory" else None
        if memory is None:
            raise KiraError("NOT_FOUND", "This experience was not admitted to this profile snapshot")
        content = canonical({"statement": memory["statement"], "applicability": memory["applicability"],
            "classification": "user-accepted interpretation; evidence is not proof of general validity",
            "scope": memory["scope"], "source_ref": self._ref(soul, "source", memory["source_id"])})
        chunk = content[offset:offset + limit]
        truncated = offset + len(chunk) < len(content)
        return {"ref": ref, "requested_ref": ref, "revision": soul["revision"], "content": chunk,
            "offset": offset, "next_offset": offset + len(chunk) if truncated else None, "truncated": truncated,
            "provenance": {"origin": "accepted_soul_experience", "source_id": memory["source_id"]}}

    def read(self, run_id: str, ref: str, offset: int = 0, limit: int = 12000) -> Json:
        return self._read(self._run_snapshot(run_id), ref, offset, limit)

    def read_current(self, project_id: str, session_id: str, ref: str, offset: int = 0, limit: int = 12000) -> Json:
        return self._read(self.snapshot(project_id, session_id), ref, offset, limit)
