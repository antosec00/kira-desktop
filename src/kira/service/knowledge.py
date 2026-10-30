"""Progressive, owned knowledge lookup; reading never runs a procedure."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path
from typing import Any

from kira import __version__
from kira.domain.contracts import HOST_PERMISSIONS, Json, KiraError
from kira.domain.providers import WEB_BACKENDS
from kira.domain.souls import SOUL_TOOL_NAMES, soul_tool_specs
from kira.domain.knowledge import (
    KNOWLEDGE_SCOPES, KNOWLEDGE_TOOL_NAMES, MAX_BOOTSTRAP_BYTES, MAX_QUERY_CHARS,
    MAX_READ_CHARS, MAX_RESULT_BYTES, MAX_SEARCH_ITEMS, knowledge_tool_specs,
)
from kira.service.history_tools import HistoryTools
from kira.service.storage import encode
from kira.service.tool_policy import kira_tools_enabled

METHODS = frozenset({"knowledge.snapshot", "knowledge.search", "knowledge.read"})
_RESOURCE_ROOT = Path(__file__).resolve().parent.parent / "resources" / "knowledge"
_GROUPS = {"app": ("app",), "capabilities": ("capabilities",),
           "chat": ("goal", "notes", "summary", "history"),
           "project": ("memory", "research"), "tools": ("tools",), "soul": ("soul",)}
_ALL_GROUPS = tuple(group for groups in _GROUPS.values() for group in groups)
_CAPABILITIES = (
    ("knowledge", "Kira knowledge", {"kira.help_search", "kira.help_read"}),
    ("history", "Original chat sources", {"kira.help_search", "kira.help_read",
                                           "kira_history.search", "kira_history.read"}),
    ("project_tools", "Reusable project tools", {"kira.project_tools"}),
    ("code_index", "Semantic codebase index", {"kira.code_index"}),
    ("canvas", "Interactive Canvas and live preview artifacts", {"kira.canvas"}),
    ("mcp_tools", "External MCP servers", {"kira.mcp_tools"}),
    ("research", "Accepted project research", {"kira.research_list", "kira.research_read",
                                                "kira.help_search", "kira.help_read"}),
    ("teams", "Managed project teams", {"kira.team_delegate", "kira.team_status", "kira.team_resume"}),
    ("browser", "Isolated browser", {"browser.navigate", "browser.snapshot"}),
    ("computer", "Visual browser control", {"computer.observe", "computer.act"}),
    ("host_commands", "Host command execution", {"run_command"}),
)


def _text(value: Any, name: str, maximum: int = 128, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or "\0" in value or len(value) > maximum
            or not empty and not value.strip()):
        raise KiraError("INVALID_ARGUMENT", f"{name} must be bounded text")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise KiraError("INVALID_ARGUMENT", f"Invalid {name}")
    return value


def _clip(value: str, maximum: int) -> str:
    data = value.encode("utf-8")
    return data[:maximum].decode("utf-8", "ignore") if len(data) > maximum else value


def _revision(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:24]


def _matches(query: str, text: str) -> bool:
    return all(term in text.casefold() for term in query.casefold().split())


class Knowledge:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.store = service.store
        self.history = HistoryTools(self.store)
        self._registrations: dict[str, Json] = {}
        self._cursor_key = secrets.token_bytes(32)
        self._catalog: list[Json] | None = None
        self.store.db.create_function("knowledge_casefold", 1,
            lambda value: str(value).casefold(), deterministic=True)

    def _scope(self, project_id: str, session_id: str) -> tuple[Json, Json]:
        _text(project_id, "project_id")
        _text(session_id, "session_id")
        row = self.store.db.execute(
            "SELECT p.id,p.name,p.permission FROM projects p JOIN sessions s ON s.project_id=p.id "
            "WHERE p.id=? AND s.id=? AND NOT EXISTS "
            "(SELECT 1 FROM team_members m WHERE m.session_id=s.id)",
            (project_id, session_id)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Knowledge belongs to an ordinary chat in this project")
        return dict(row), self.store.one("sessions", session_id)

    def _guides(self) -> list[Json]:
        if self._catalog is None:
            raw = (_RESOURCE_ROOT / "index.json").read_bytes()
            if len(raw) > 65536:
                raise KiraError("LIMIT_REACHED", "Knowledge guide index exceeds its limit")
            catalog = json.loads(raw)
            if not isinstance(catalog, list) or len(catalog) > 64:
                raise KiraError("INVALID_KNOWLEDGE", "Invalid knowledge guide index")
            seen = set()
            for entry in catalog:
                if (not isinstance(entry, dict) or set(entry) != {
                        "id", "title", "summary", "keywords", "revision"}
                        or not isinstance(entry["id"], str)
                        or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", entry["id"])
                        or entry["id"] in seen):
                    raise KiraError("INVALID_KNOWLEDGE", "Invalid knowledge guide entry")
                for name in ("title", "summary", "keywords"):
                    _text(entry[name], name, 2048)
                if not isinstance(entry["revision"], str) or not re.fullmatch(r"[0-9a-f]{24}", entry["revision"]):
                    raise KiraError("INVALID_KNOWLEDGE", "Invalid bundled guide revision")
                seen.add(entry["id"])
            self._catalog = catalog
        return self._catalog

    def register(self, run_id: str, tools: Any, *, transport: str = "unknown") -> None:
        """Record exact admitted names, without claiming model/probe success."""
        if transport not in {"unknown", "unavailable", "confirmed_per_call"}:
            raise KiraError("INVALID_ARGUMENT", "Unknown knowledge transport observation")
        run = self.store.one("runs", run_id)
        session = self.store.one("sessions", run["session_id"])
        self._scope(session["project_id"], session["id"])
        names: set[str] = set()
        if isinstance(tools, (set, frozenset)):
            names = set(tools)
        elif isinstance(tools, list):
            for namespace in tools:
                if isinstance(namespace, str):
                    names.add(namespace)
                elif isinstance(namespace, dict):
                    for tool in namespace.get("tools", []):
                        names.add(str(namespace.get("name")) + "." + str(tool.get("name")))
        if len(names) > 256 or any(not isinstance(name, str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_.]{0,127}", name) for name in names):
            raise KiraError("INVALID_ARGUMENT", "Invalid registered knowledge tool names")
        previous = self._registrations.get(run_id, {})
        self._registrations[run_id] = {
            "session_id": session["id"], "project_id": session["project_id"],
            "names": names, "observed": previous.get("observed", set()),
            "transport": transport, "mode": previous.get("mode", "execute")}

    def forget(self, run_id: str) -> None:
        self._registrations.pop(run_id, None)

    def _registration(self, session_id: str) -> Json | None:
        for run_id, registration in reversed(list(self._registrations.items())):
            if registration["session_id"] != session_id:
                continue
            row = self.store.db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
            if row and row[0] in {"running", "awaiting_input"}:
                return registration
        return None

    def _sharing(self, project_id: str) -> bool:
        row = self.store.db.execute("SELECT enabled FROM research_settings WHERE project_id=?",
                                    (project_id,)).fetchone()
        return bool(row and row[0])

    @staticmethod
    def _web_unavailable(identity: str, session: Json) -> str | None:
        """Why this chat cannot be offered Kira's browser or Computer, when that is known.

        Otherwise availability stays unknown until a turn registers the tools: it
        also depends on the browser engine, the mode and, with Ollama, the model.
        """
        backend = session["backend"]
        if backend not in WEB_BACKENDS:
            return "This provider route does not expose Kira's browser actor."
        if identity == "computer" and backend == "deepseek_web":
            return "This provider route does not expose Kira's Computer; the browser is text only."
        if not session["settings"].get("browser_enabled", True):
            return "The browser is turned off in this chat's web settings."
        return None

    def _capabilities(self, project: Json, session: Json) -> list[Json]:
        registration = self._registration(session["id"])
        names = registration["names"] if registration else set()
        observed = registration["observed"] if registration else set()
        mode = registration["mode"] if registration else "execute"
        enabled = kira_tools_enabled(session)
        result = []
        for identity, label, candidates in _CAPABILITIES:
            registered = sorted(names & candidates)
            confirmed = sorted(observed & candidates)
            allowed = True
            reason = "Provider transport is not yet observed for this capability."
            if identity == "host_commands":
                allowed = project["permission"] in HOST_PERMISSIONS and mode == "execute"
                reason = "Arbitrary host commands require Execute and trusted-host."
            elif identity == "research":
                allowed = self._sharing(project["id"])
                reason = "Model research access requires enabled sharing and accepted records."
            elif identity == "project_tools":
                reason = "Read/describe are metadata; register needs writable Execute, test/run trusted-host."
            elif identity in {"browser", "computer"}:
                reason = "Availability also depends on the owned browser engine, mode and chat consent."
            status = ("disabled" if not enabled else "blocked" if not allowed else
                      "registered" if registered else "unknown")
            if not registered and identity in {"browser", "computer"}:
                unavailable = self._web_unavailable(identity, session)
                if unavailable is not None:
                    status, reason = "disabled", unavailable
            capability = {"id": identity, "label": label, "status": status, "reason": reason,
                "source": "service_registration" if registration else "configuration",
                "registered": bool(registered), "allowed": bool(enabled and allowed),
                "registered_tools": registered, "observed_tools": confirmed,
                "transport": "confirmed_per_call" if confirmed else "unknown",
                "ref": "capability:" + identity}
            if identity == "project_tools":
                actions = ["describe", "search", "read"] if enabled else []
                if enabled and mode == "execute" and project["permission"] != "read_only":
                    actions.append("register")
                    if project["permission"] in HOST_PERMISSIONS:
                        actions.extend(["test", "run"])
                capability["permitted_actions"] = actions
            result.append(capability)
        return result

    def _summary(self, session_id: str, *, body: bool = False) -> Json | None:
        # Readable, source-linked claims only. Never select native items/metrics.
        columns = "revision,model,generation,updated_at" + (",memory" if body else "")
        row = self.store.db.execute(
            f"SELECT {columns} FROM direct_context_states WHERE session_id=? AND memory<>'[]' "
            "ORDER BY updated_at DESC,revision DESC LIMIT 1", (session_id,)).fetchone()
        return dict(row) if row else None

    def _anchor(self, session_id: str, run_id: str | None = None) -> Json:
        row = self.store.db.execute(
            "SELECT substr(objective,1,1600) objective,length(objective) chars,status "
            "FROM session_goals WHERE session_id=? AND status='active'", (session_id,)).fetchone()
        goal = None
        if row:
            excerpt = _clip(row["objective"], 1200)
            goal = {"ref": "chat:goal", "title": "Saved active goal", "excerpt": excerpt,
                    "status": "active", "truncated": len(excerpt) < row["chars"]}
        if run_id is None:
            request = self.store.db.execute(
                "SELECT id,substr(prompt,1,1000) prompt,length(prompt) chars,state FROM runs "
                "WHERE session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (session_id,)).fetchone()
        else:
            request = self.store.db.execute(
                "SELECT id,substr(prompt,1,1000) prompt,length(prompt) chars,state FROM runs "
                "WHERE session_id=? AND id=?", (session_id, run_id)).fetchone()
        current = None
        if request:
            excerpt = _clip(request["prompt"], 700)
            service_trigger = self.store.is_service_run(request["id"])
            current = {"ref": "chat:request:" + request["id"],
                       "title": ("Automatic goal continuation trigger" if self.store.run_metadata(request["id"]).get("source") == "goal_continuation"
                                 else "Automatic Team result trigger") if service_trigger else "Current request",
                       "origin": "service" if service_trigger else "user",
                       "excerpt": excerpt, "state": request["state"],
                       "truncated": len(excerpt) < request["chars"]}
        count = self.store.db.execute("SELECT COUNT(*) FROM chat_context_notes WHERE session_id=?",
                                      (session_id,)).fetchone()[0]
        summary = self._summary(session_id)
        return {"goal": goal, "request": current, "notes": {"count": count, "ref": "chat:notes"},
                "generated_memory": {"available": summary is not None,
                                     "ref": "chat:summary" if summary else None}}

    def _tool_count(self, project_id: str) -> int:
        if not self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                      "AND name='project_tools'").fetchone():
            return 0
        return int(self.store.db.execute("SELECT COUNT(*) FROM project_tools WHERE project_id=?",
                                         (project_id,)).fetchone()[0])

    def _sources(self, project_id: str, session_id: str, *, for_model: bool,
                 guides: bool = True) -> list[Json]:
        sharing = self._sharing(project_id)
        research = (self.store.db.execute("SELECT COUNT(*) FROM research_records "
            "WHERE project_id=? AND accepted=1", (project_id,)).fetchone()[0]
            if sharing or not for_model else None)
        notes = self.store.db.execute("SELECT COUNT(*) FROM chat_context_notes WHERE session_id=?",
                                      (session_id,)).fetchone()[0]
        runs = self.store.db.execute("SELECT COUNT(*) FROM runs WHERE session_id=?",
                                     (session_id,)).fetchone()[0]
        memory = self.store.db.execute("SELECT length(memory)>2 FROM projects WHERE id=?",
                                       (project_id,)).fetchone()[0]
        return [
            {"id": "guides", "label": "Kira guides", "count": len(self._guides()) if guides else None,
             "available": True, "ref": "app:identity"},
            {"id": "chat", "label": "Original chat sources", "count": runs, "unit": "runs",
             "available": bool(runs), "ref": "app:context"},
            {"id": "notes", "label": "Important chat notes", "count": notes,
             "available": bool(notes), "ref": "chat:notes"},
            {"id": "project_memory", "label": "Project memory", "count": int(bool(memory)),
             "available": bool(memory), "ref": "project:memory"},
            {"id": "research", "label": "Accepted project records", "count": research,
             "available": bool(research), "sharing_enabled": sharing, "ref": "app:context"},
            {"id": "tools", "label": "Project tools", "count": self._tool_count(project_id),
             "available": getattr(self.service, "project_tools", None) is not None,
             "ref": "tools:catalog"},
        ]

    def snapshot(self, project_id: str, session_id: str) -> Json:
        project, session = self._scope(project_id, session_id)
        return {"identity": {"name": "Kira", "version": __version__, "backend": session["backend"],
                "model": session["model"], "reasoning_effort": session["settings"].get("reasoning_effort")},
            "capabilities": self._capabilities(project, session), "anchor": self._anchor(session_id),
            "sources": self._sources(project_id, session_id, for_model=False)}

    def bootstrap(self, run_id: str, project: Json, session: Json, mode: str) -> str:
        self._scope(project["id"], session["id"])
        if self.store.db.execute("SELECT 1 FROM runs WHERE id=? AND session_id=?",
                                 (run_id, session["id"])).fetchone() is None:
            raise KiraError("NOT_FOUND", "Knowledge anchor request belongs to another chat")
        anchor = self._anchor(session["id"], run_id)
        sources = self._sources(project["id"], session["id"], for_model=True, guides=False)
        content = ("\nKira knowledge anchor: explicit saved goal and current request are distinct; "
                   "later user corrections take precedence. Excerpts are incomplete where marked. "
                   "Read only relevant sources through registered help tools; procedures do not execute.\n")
        payload = {"anchor": anchor, "sources": [{key: value for key, value in item.items()
                   if key in {"id", "count", "ref", "sharing_enabled"}} for item in sources]}
        result = content + encode(payload)
        if len(result.encode()) > MAX_BOOTSTRAP_BYTES:
            for name in ("request", "goal"):
                if anchor[name]:
                    anchor[name]["excerpt"] = _clip(anchor[name]["excerpt"], 300)
                    anchor[name]["truncated"] = True
            result = content + encode(payload)
        if len(result.encode()) > MAX_BOOTSTRAP_BYTES:
            raise KiraError("LIMIT_REACHED", "Knowledge anchor exceeds its bound")
        return result

    @staticmethod
    def _item(ref: str, title: str, kind: str, summary: str, revision: Any,
              provenance: Json) -> Json:
        result = {"ref": ref, "title": _clip(title, 160), "kind": kind,
                  "summary": _clip(summary, 512), "revision": revision, "provenance": provenance}
        if len(encode(result).encode()) > 2048:
            raise KiraError("LIMIT_REACHED", "Knowledge search metadata exceeds its bound")
        return result

    @staticmethod
    def _small_page(items: list[Json], cursor: Any, limit: int) -> tuple[list[Json], Any]:
        offset = 0 if cursor is None else _integer(cursor, "source cursor", 0, 100000)
        page = items[offset:offset + limit]
        return page, offset + len(page) if offset + len(page) < len(items) else None

    def _fingerprint(self, project_id: str, session_id: str, query: str, scope: str,
                     for_model: bool) -> str:
        return hashlib.sha256(encode([project_id, session_id, query, scope, for_model]).encode()).hexdigest()

    def _cursor(self, fingerprint: str, group: int, after: Any) -> str:
        raw = base64.urlsafe_b64encode(encode([fingerprint, group, after]).encode()).decode().rstrip("=")
        signature = hmac.new(self._cursor_key, raw.encode(), hashlib.sha256).hexdigest()[:32]
        return "k1." + raw + "." + signature

    def _parse_cursor(self, value: Any, fingerprint: str, groups: tuple[str, ...]) -> tuple[int, Any]:
        if value is None:
            return 0, None
        _text(value, "cursor", 2048)
        try:
            prefix, raw, signature = value.split(".")
            expected = hmac.new(self._cursor_key, raw.encode(), hashlib.sha256).hexdigest()[:32]
            if prefix != "k1" or not hmac.compare_digest(signature, expected):
                raise ValueError
            binding, index, after = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            if binding != fingerprint or type(index) is not int or not 0 <= index < len(groups):
                raise ValueError
            return index, after
        except (ValueError, TypeError):
            raise KiraError("INVALID_ARGUMENT", "Knowledge cursor is stale or belongs to another query") from None

    def search(self, project_id: str, session_id: str, query: str, scope: str = "all",
               limit: int = 8, cursor: str | None = None, for_model: bool = True,
               run_id: str | None = None) -> Json:
        project, session = self._scope(project_id, session_id)
        query = _text(query, "query", MAX_QUERY_CHARS, empty=True).strip()
        if not isinstance(scope, str) or scope not in KNOWLEDGE_SCOPES:
            raise KiraError("INVALID_ARGUMENT", "Invalid knowledge scope")
        _integer(limit, "limit", 1, MAX_SEARCH_ITEMS)
        groups = _ALL_GROUPS if scope == "all" else _GROUPS[scope]
        fingerprint = self._fingerprint(project_id, session_id, query, scope, for_model)
        index, after = self._parse_cursor(cursor, fingerprint, groups)
        items: list[Json] = []
        while index < len(groups) and len(items) < limit:
            page, next_after = self._group(groups[index], project, session, query,
                                           limit - len(items), after, for_model, run_id)
            items.extend(page)
            if next_after is not None:
                after = next_after
                break
            index += 1
            after = None
        if len(items) == limit and after is None:
            # A source group ending exactly at the page boundary is not proof
            # that another matching result exists in later groups.
            while index < len(groups):
                probe, next_after = self._group(groups[index], project, session, query, 1, None, for_model, run_id)
                if probe or next_after is not None:
                    break
                index += 1
        more = index < len(groups)
        return {"items": items, "has_more": more,
                "next_cursor": self._cursor(fingerprint, index, after) if more else None}

    def _group(self, group: str, project: Json, session: Json, query: str,
               limit: int, cursor: Any, for_model: bool, run_id: str | None = None) -> tuple[list[Json], Any]:
        project_id, session_id = project["id"], session["id"]
        item = self._item
        if group == "soul":
            souls = getattr(self.service, "souls", None)
            if souls is None:
                return [], None
            found = (souls.search(run_id, query, limit=limit, cursor=cursor) if run_id else
                     souls.search_current(project_id, session_id, query, limit=limit, cursor=cursor))
            return found["items"], found.get("next_cursor") if found.get("has_more") else None
        if group == "app":
            entries = [item("app:" + guide["id"], guide["title"], "guide", guide["summary"], guide["revision"],
                            {"scope": "app", "origin": "bundled_guide", "application_version": __version__})
                       for guide in self._guides() if _matches(query, " ".join(guide.values()))]
            return self._small_page(entries, cursor, limit)
        if group == "capabilities":
            entries = [item(cap["ref"], cap["label"], "capability", cap["reason"], __version__,
                            {"scope": "run", "origin": cap["source"], "status": cap["status"],
                             "registered": cap["registered"], "allowed": cap["allowed"],
                             "transport": cap["transport"]}) for cap in self._capabilities(project, session)
                       if _matches(query, encode(cap))]
            return self._small_page(entries, cursor, limit)
        if group == "goal":
            row = self.store.db.execute("SELECT objective,status FROM session_goals WHERE session_id=?",
                                        (session_id,)).fetchone()
            entries = [item("chat:goal", "Saved chat goal", "goal", row["objective"],
                            _revision(encode(dict(row))),
                            {"scope": "chat", "origin": "user", "status": row["status"]})] \
                if row and _matches(query, "goal objective obiettivo " + row["objective"]) else []
            return self._small_page(entries, cursor, limit)
        if group == "notes":
            entries = [item("chat:note:" + row["id"], "Important chat note", "note", row["content"],
                            _revision(row["content"]), {"scope": "chat", "origin": "user"})
                       for row in self.store.db.execute(
                           "SELECT id,content FROM chat_context_notes WHERE session_id=? ORDER BY created_at,id",
                           (session_id,)) if _matches(query, "note pin nota importante " + row["content"])]
            return self._small_page(entries, cursor, limit)
        if group == "summary":
            summary = self._summary(session_id, body=True)
            entries = [item("chat:summary", "Generated chat memory", "summary", summary["memory"],
                            _revision(summary["memory"]), {"scope": "chat", "origin": "model_claim",
                                                          "model": summary["model"]})] \
                if summary and _matches(query, "summary memory memoria sintesi " + summary["memory"]) else []
            return self._small_page(entries, cursor, limit)
        if group == "history":
            if query:
                result = self.history.search(session_id, query, limit=limit, before_id=cursor)
                entries = [item("chat:" + source["source_id"],
                    "Chat " + source["kind"] + " · " + source["created_at"], source["kind"],
                    source["excerpt"], source["source_id"], {"scope": "chat", "origin": source["role"],
                    "historical_data": True, **({"evidence": source["evidence"]} if "evidence" in source else {})})
                    for source in result["sources"]]
                return entries, result["next_before_id"] if result["has_more"] else None
            self.history.sync(session_id)
            before = self.history._source(session_id, cursor)["id"] if cursor is not None else 2**63 - 1
            rows = self.store.db.execute("SELECT id,kind,role,created_at,substr(content,1,800) preview "
                "FROM chat_history_sources WHERE session_id=? AND id<? ORDER BY id DESC LIMIT ?",
                (session_id, before, limit + 1)).fetchall()
            entries = [item(f"chat:h:{row['id']}", "Chat " + row["kind"] + " · " + row["created_at"],
                row["kind"], row["preview"], f"h:{row['id']}", {"scope": "chat", "origin": row["role"],
                "historical_data": True}) for row in rows[:limit]]
            return entries, f"h:{rows[limit - 1]['id']}" if len(rows) > limit else None
        if group == "memory":
            raw = self.store.db.execute("SELECT memory FROM projects WHERE id=?", (project_id,)).fetchone()[0]
            entries = [item("project:memory", "Project memory", "project_memory", raw, _revision(raw),
                            {"scope": "project", "origin": "user"})] \
                if raw != "{}" and _matches(query, "project memory memoria progetto " + raw) else []
            return self._small_page(entries, cursor, limit)
        if group == "research":
            sharing = self._sharing(project_id)
            if for_model and not sharing:
                return [], None
            bound = 2**63 - 1
            if cursor is not None:
                if not isinstance(cursor, str) or not re.fullmatch(r"r:[1-9][0-9]{0,17}", cursor):
                    raise KiraError("INVALID_ARGUMENT", "Invalid research search cursor")
                bound = int(cursor[2:])
                if self.store.db.execute("SELECT 1 FROM research_records WHERE project_id=? AND rowid=?",
                                          (project_id, bound)).fetchone() is None:
                    raise KiraError("INVALID_ARGUMENT", "Research cursor belongs to another project")
            conditions = "".join(" AND instr(knowledge_casefold(title||' '||body),?)>0"
                                  for _ in query.split())
            rows = self.store.db.execute(
                "SELECT id,revision,title,substr(body,1,800) body,assessment,rowid cursor,"
                "json_extract(record,'$.origin') origin FROM research_records "
                "WHERE project_id=? AND accepted=1 AND rowid<?" + conditions +
                " ORDER BY rowid DESC LIMIT ?", (project_id, bound, *query.casefold().split(), limit + 1)).fetchall()
            entries = [item(f"project:record:{record['id']}@{record['revision']}", record["title"],
                "research", record["body"], record["revision"], {"scope": "project", "origin": record["origin"],
                "accepted": True, "assessment": record["assessment"], "sharing_enabled": sharing})
                for record in rows[:limit]]
            return entries, f"r:{rows[limit - 1]['cursor']}" if len(rows) > limit else None
        if group == "tools":
            owner = getattr(self.service, "project_tools", None)
            if owner is None:
                return [], None
            result = owner.data.list(project_id, query=query, limit=limit, cursor=cursor)
            entries = [item(f"tool:{tool['id']}@{tool['revision']}", tool["name"], "project_tool",
                tool["description"], tool["revision"], {"scope": "project", "origin": "tool_registry",
                "enabled": tool["enabled"], "test_state": tool["test_state"]})
                for tool in result["items"]]
            return entries, result["next_cursor"] if result["has_more"] else None
        raise KiraError("INVALID_ARGUMENT", "Unknown knowledge source")

    def _page(self, requested: str, ref: str, title: str, kind: str, content: str,
              provenance: Json, offset: int, limit: int, revision: Any = None) -> Json:
        version = revision if revision is not None else _revision(content)
        canonical = ref if revision is not None else ref + "@" + str(version)
        if requested not in {ref, canonical}:
            raise KiraError("STALE_SOURCE", "Knowledge source changed; search or read its current reference")
        if offset > len(content):
            raise KiraError("INVALID_ARGUMENT", "Knowledge offset exceeds the source")
        end = min(len(content), offset + limit)
        result = {"requested_ref": requested, "ref": canonical, "title": title, "kind": kind,
                  "revision": version, "content": content[offset:end], "offset": offset,
                  "next_offset": end if end < len(content) else None, "truncated": end < len(content),
                  "provenance": provenance}
        while len(encode(result).encode()) > MAX_RESULT_BYTES and end > offset:
            end = offset + (end - offset) // 2
            result.update(content=content[offset:end], next_offset=end, truncated=True)
        if len(encode(result).encode()) > MAX_RESULT_BYTES:
            raise KiraError("LIMIT_REACHED", "Knowledge source metadata exceeds its bound")
        return result

    def read(self, project_id: str, session_id: str, ref: str, offset: int = 0,
             limit: int = MAX_READ_CHARS, for_model: bool = True, run_id: str | None = None) -> Json:
        project, session = self._scope(project_id, session_id)
        _text(ref, "ref", 256)
        _integer(offset, "offset", 0, 2**31 - 1)
        _integer(limit, "limit", 1, MAX_READ_CHARS)
        if ref.startswith("soul:"):
            souls = getattr(self.service, "souls", None)
            if souls is None:
                raise KiraError("NOT_FOUND", "No identity memory is available")
            return (souls.read(run_id, ref, offset=offset, limit=limit) if run_id else
                    souls.read_current(project_id, session_id, ref, offset=offset, limit=limit))
        base = ref.split("@", 1)[0]
        def page(title: str, kind: str, content: str, provenance: Json) -> Json:
            return self._page(ref, base, title, kind, content, provenance, offset, limit)
        if base.startswith("app:"):
            identity = base[4:]
            guide = next((entry for entry in self._guides() if entry["id"] == identity), None)
            if guide is None:
                raise KiraError("NOT_FOUND", "Unknown bundled knowledge guide")
            raw = (_RESOURCE_ROOT / (identity + ".md")).read_bytes()
            if len(raw) > 65536:
                raise KiraError("LIMIT_REACHED", "Knowledge guide exceeds its bound")
            if hashlib.sha256(raw).hexdigest()[:24] != guide["revision"]:
                raise KiraError("INVALID_KNOWLEDGE", "Bundled knowledge guide does not match its index")
            return page(guide["title"], "guide", raw.decode(),
                        {"scope": "app", "origin": "bundled_guide", "application_version": __version__})
        if base.startswith("capability:"):
            capability = next((entry for entry in self._capabilities(project, session)
                               if entry["ref"] == base), None)
            if capability is None:
                raise KiraError("NOT_FOUND", "Unknown capability reference")
            return page(capability["label"], "capability", encode(capability),
                        {"scope": "run", "origin": capability["source"]})
        if base == "chat:goal":
            row = self.store.db.execute("SELECT objective,status FROM session_goals WHERE session_id=?",
                                        (session_id,)).fetchone()
            if row is None:
                raise KiraError("NOT_FOUND", "This chat has no saved goal")
            return page("Saved chat goal", "goal", encode(dict(row)),
                        {"scope": "chat", "origin": "user", "status": row["status"]})
        if base == "chat:notes":
            notes = [dict(row) for row in self.store.db.execute(
                "SELECT id,content,created_at,updated_at FROM chat_context_notes WHERE session_id=? "
                "ORDER BY created_at,id", (session_id,))]
            return page("Important chat notes", "notes", encode(notes), {"scope": "chat", "origin": "user"})
        if base.startswith("chat:note:"):
            row = self.store.db.execute("SELECT content FROM chat_context_notes WHERE session_id=? AND id=?",
                                        (session_id, base[10:])).fetchone()
            if row is None:
                raise KiraError("NOT_FOUND", "Note does not belong to this chat")
            return page("Important chat note", "note", row[0], {"scope": "chat", "origin": "user"})
        if base == "chat:summary":
            summary = self._summary(session_id, body=True)
            if summary is None:
                raise KiraError("NOT_FOUND", "This chat has no readable generated summary")
            return page("Generated chat memory", "summary", summary["memory"],
                        {"scope": "chat", "origin": "model_claim", "model": summary["model"],
                         "generated_at": summary["updated_at"]})
        if base.startswith("chat:request:"):
            row = self.store.db.execute("SELECT prompt FROM runs WHERE session_id=? AND id=?",
                                        (session_id, base[13:])).fetchone()
            if row is None:
                raise KiraError("NOT_FOUND", "Request does not belong to this chat")
            if self.store.is_service_run(base[13:]):
                return page("Automatic goal continuation trigger" if self.store.run_metadata(base[13:]).get("source") == "goal_continuation"
                            else "Automatic Team result trigger", "service", row[0],
                            {"scope": "chat", "origin": "service", "new_user_authority": False})
            return page("User request", "user", row[0], {"scope": "chat", "origin": "user"})
        if re.fullmatch(r"chat:h:[0-9]{1,18}", ref):
            source = self.history.read(session_id, ref[5:], offset=offset, limit=limit)
            return {"requested_ref": ref, "ref": ref, "title": "Original chat " + source["kind"],
                "kind": source["kind"], "revision": source["source_id"], "content": source["content"],
                "offset": source["offset"], "next_offset": source["next_offset"],
                "truncated": source["has_more"], "provenance": {key: value for key, value in source.items()
                    if key not in {"content", "offset", "next_offset", "has_more", "complete"}}}
        if base == "project:memory":
            content = self.store.db.execute("SELECT memory FROM projects WHERE id=?", (project_id,)).fetchone()[0]
            return page("Project memory", "project_memory", content, {"scope": "project", "origin": "user"})
        match = re.fullmatch(r"project:record:([A-Za-z0-9_-]{1,128})@([1-9][0-9]{0,9})(?:/source/([0-7]))?", ref)
        if match:
            sharing = self._sharing(project_id)
            if for_model and not sharing:
                raise KiraError("DISABLED", "Project research sharing is disabled")
            record = self.service.research.data.get(project_id, match[1], int(match[2]), accepted_only=True)["record"]
            provenance = {"scope": "project", "origin": record["origin"], "accepted": True,
                          "assessment": record["assessment"], "sharing_enabled": sharing}
            if match[3] is not None:
                index = int(match[3])
                if index >= len(record["sources"]):
                    raise KiraError("NOT_FOUND", "Research source is not linked by this record")
                source = self.service.research.data.source_read(project_id, record["sources"][index], offset, limit)
                return {"requested_ref": ref, "ref": ref, "title": record["title"] + " · linked source",
                    "kind": "research_source", "revision": record["revision"], "content": source["content"],
                    "offset": source["offset"], "next_offset": source["next_offset"] if source.get("has_more") else None,
                    "truncated": source.get("has_more", False),
                    "provenance": {**provenance, **{key: source[key] for key in
                        ("availability", "integrity", "provenance", "binary") if key in source}}}
            content = encode({"record": record, "linked_sources": [
                ref + "/source/" + str(index) for index in range(len(record["sources"]))]})
            return self._page(ref, ref, record["title"], "research", content, provenance,
                              offset, limit, record["revision"])
        if base == "tools:catalog":
            content = ("Search scope tools to discover this project's registered tools, then read a selected "
                       "tool reference. The catalog has " + str(self._tool_count(project_id)) +
                       " entries. Reading this source does not load every implementation or run any tool.")
            return page("Project tool catalog", "project_tools", content,
                        {"scope": "project", "origin": "tool_registry"})
        match = re.fullmatch(r"tool:([A-Za-z0-9_-]{1,128})@([1-9][0-9]{0,9})", ref)
        if match and getattr(self.service, "project_tools", None) is not None:
            tool = self.service.project_tools.data.get(project_id, match[1], revision=int(match[2]))["tool"]
            return self._page(ref, ref, tool["name"], "project_tool", encode(tool),
                {"scope": "project", "origin": "tool_registry", "enabled": tool["enabled"],
                 "test_state": tool["test_state"]}, offset, limit, int(match[2]))
        raise KiraError("NOT_FOUND", "Unknown or unauthorized knowledge reference")

    async def dispatch(self, method: str, params: Json) -> Json:
        extra = {"knowledge.snapshot": set(), "knowledge.search": {"query", "scope", "limit", "cursor"},
                 "knowledge.read": {"ref", "offset", "limit"}}
        if method not in extra or not isinstance(params, dict) or set(params) - {
                "project_id", "session_id", *extra[method]}:
            raise KiraError("INVALID_ARGUMENT", "Unexpected knowledge request fields")
        project_id = _text(params.get("project_id"), "project_id")
        session_id = _text(params.get("session_id"), "session_id")
        if method == "knowledge.snapshot":
            return self.snapshot(project_id, session_id)
        if method == "knowledge.search":
            return self.search(project_id, session_id, params.get("query", ""),
                scope=params.get("scope", "all"), limit=params.get("limit", 8),
                cursor=params.get("cursor"), for_model=False)
        return self.read(project_id, session_id, _text(params.get("ref"), "ref", 256), offset=params.get("offset", 0),
                         limit=params.get("limit", MAX_READ_CHARS), for_model=False)

    def attach(self, session: Json, project: Json, run_id: str, cancel: asyncio.Event,
               mode: str, options: Json) -> Json:
        if self.store.db.execute("SELECT 1 FROM team_members WHERE session_id=?", (session["id"],)).fetchone():
            return options
        self._scope(project["id"], session["id"])
        if not kira_tools_enabled(session) or session["backend"] not in {
                "codex", "openai_chatgpt", "ollama", "deepseek_account", "deepseek_web",
                "claude_code", "google_cloud"}:
            return options
        result = dict(options)
        namespaces = [{**namespace, "tools": list(namespace["tools"])}
                      for namespace in options.get("client_tools", [])]
        kira = next((namespace for namespace in namespaces if namespace.get("name") == "kira"), None)
        if kira is None:
            kira = {"type": "namespace", "name": "kira", "description": "Kira project tools and knowledge.", "tools": []}
            namespaces.append(kira)
        existing = {tool.get("name") for tool in kira["tools"]}
        kira["tools"].extend(tool for tool in knowledge_tool_specs() if tool["name"] not in existing)
        souls = getattr(self.service, "souls", None)
        if souls is not None:
            selected = self.store.db.execute(
                "SELECT snapshot FROM soul_run_snapshots WHERE run_id=? AND soul_id IS NOT NULL",
                (run_id,)).fetchone()
            if selected and (json.loads(selected[0]).get("soul") or {}).get("mode") in {"suggest", "adaptive"}:
                kira["tools"].extend(tool for tool in soul_tool_specs() if tool["name"] not in existing)
        previous = options.get("client_tool_call")

        def active() -> None:
            _, actual_session = self._scope(project["id"], session["id"])
            run = self.store.one("runs", run_id)
            if (getattr(self.service, "_closing", False) or cancel.is_set()
                    or run_id not in self.service.tasks or run["session_id"] != session["id"]
                    or run["state"] not in {"running", "awaiting_input"}
                    or not kira_tools_enabled(actual_session)):
                raise KiraError("CANCELLED", "The origin turn no longer accepts knowledge callbacks")

        async def call(call_id: str, name: str, arguments: Json) -> Json:
            if name in SOUL_TOOL_NAMES and souls is not None:
                try:
                    active()
                    if name not in self._registrations[run_id]["names"]:
                        raise KiraError("UNSUPPORTED", "Profile learning is not registered in this turn")
                except KiraError as exc:
                    return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
                self._registrations[run_id]["observed"].add(name)
                await self.service.emit(run_id, "tool.started", {"tool_id": call_id, "name": name,
                                                               "arguments": arguments})
                try:
                    active()
                    value = await souls.tool_call(run_id, call_id, name, arguments)
                except KiraError as exc:
                    value = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
                value = self.service.redactor.clean(value)
                await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name,
                                                                 "result": value})
                return value
            if name not in KNOWLEDGE_TOOL_NAMES:
                if previous is None:
                    raise KiraError("UNSUPPORTED", "This Kira callback is not registered")
                value = dict(await previous(call_id, name, arguments))
                registration = self._registrations.get(run_id)
                if registration is not None and name in registration["names"]:
                    registration["observed"].add(name)
                return value
            try:
                active()
            except KiraError as exc:
                return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
            await self.service.emit(run_id, "tool.started", {"tool_id": call_id, "name": name,
                                                           "arguments": arguments})
            try:
                active()
                if not isinstance(arguments, dict):
                    raise KiraError("INVALID_ARGUMENT", "Knowledge arguments must be an object")
                self._registrations[run_id]["observed"].add(name)
                if name == "kira.help_search" and not set(arguments) - {"query", "scope", "limit", "cursor"}:
                    value = self.search(project["id"], session["id"], arguments.get("query", ""),
                        scope=arguments.get("scope", "all"), limit=arguments.get("limit", 8),
                        cursor=arguments.get("cursor"), for_model=True, run_id=run_id)
                elif name == "kira.help_read" and not set(arguments) - {"ref", "offset", "limit"}:
                    value = self.read(project["id"], session["id"], _text(arguments.get("ref"), "ref", 256),
                        offset=arguments.get("offset", 0), limit=arguments.get("limit", MAX_READ_CHARS),
                        for_model=True, run_id=run_id)
                else:
                    raise KiraError("INVALID_ARGUMENT", "Unexpected knowledge tool fields")
                if len(encode(value).encode()) > MAX_RESULT_BYTES:
                    raise KiraError("LIMIT_REACHED", "Knowledge result exceeds its bound")
                value = {"ok": True, **value}
            except (KiraError, OSError, UnicodeError) as exc:
                value = {"ok": False, "error": {"code": exc.code if isinstance(exc, KiraError) else "SOURCE_UNAVAILABLE",
                                                "message": str(exc)}}
            value = self.service.redactor.clean(value)
            await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name, "result": value})
            return value

        result.update(client_tools=namespaces, client_tool_call=call)
        self.register(run_id, namespaces)
        self._registrations[run_id]["mode"] = mode
        return result
