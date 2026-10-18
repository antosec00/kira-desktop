"""Project tool definitions, immutable bytes, and exact durable receipts."""

import builtins
import hashlib
import json
import platform
import sqlite3
import sys
from typing import Any

from kira.domain.contracts import HOST_PERMISSIONS, Json, KiraError
from kira.domain.project_tools import MAX_RESULT_BYTES, canonical, fingerprint, integer, text
from kira.service.storage import Store, encode, new_id, now


def runtime_identity() -> Json:
    return {"python": platform.python_version(), "implementation": sys.implementation.name,
            "protocol": 1}


class ProjectToolStore:
    def __init__(self, store: Store) -> None:
        self.store, self.db = store, store.db
        self.db.create_function("project_tool_casefold", 1, lambda value: str(value).casefold(), deterministic=True)

    def _row(self, project_id: str, tool_id: str) -> sqlite3.Row:
        self.store.one("projects", text(project_id, "project_id"))
        row = self.db.execute("SELECT * FROM project_tools WHERE id=? AND project_id=?",
                              (text(tool_id, "tool_id"), project_id)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Tool does not belong to this project")
        return row

    def _last_test(self, tool_id: str, revision: int) -> Json | None:
        row = self.db.execute("SELECT id,state,result,updated_at FROM project_tool_operations "
            "WHERE tool_id=? AND revision=? AND action='test' ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (tool_id, revision)).fetchone()
        if row is None:
            return None
        result = json.loads(row["result"]) if row["result"] else {}
        test = result.get("test", {})
        return {"operation_id": row["id"], "state": row["state"],
                "passed": test.get("passed") is True and row["state"] == "completed",
                "cases_passed": test.get("cases_passed", 0), "cases_total": test.get("cases_total", 0),
                "completed_at": row["updated_at"] if row["result"] else None,
                "runtime": test.get("runtime")}

    def _metadata(self, row: sqlite3.Row, revision: int) -> Json:
        definition = self.db.execute("SELECT manifest,source_path,source_sha256,revision_sha256,"
            "length(source) source_bytes,created_at FROM project_tool_revisions WHERE tool_id=? AND revision=?",
            (row["id"], revision)).fetchone()
        if definition is None:
            raise KiraError("NOT_FOUND", "Tool revision not found")
        manifest = json.loads(definition["manifest"])
        test = self._last_test(row["id"], revision)
        state = "untested" if test is None else "passed" if test["passed"] else (
            "unknown" if test["state"] in {"admitted", "running", "unknown", "cancelled"} else "failed")
        current = revision == row["revision"]
        enabled = bool(row["enabled"]) and current
        permission = self.store.one("projects", row["project_id"])["permission"]
        return {"id": row["id"], "project_id": row["project_id"], "name": manifest["name"],
            "description": manifest["description"], "revision": revision, "current_revision": row["revision"],
            "enabled": enabled, "is_current": current, "source_path": definition["source_path"],
            "source_sha256": definition["source_sha256"], "revision_sha256": definition["revision_sha256"],
            "source_bytes": definition["source_bytes"], "created_at": definition["created_at"],
            "updated_at": row["updated_at"], "test_state": state, "last_test": test,
            "required_permission": "trusted_host", "runnable": bool(enabled and test and test["passed"]
                and test["runtime"] == runtime_identity() and permission in HOST_PERMISSIONS)}

    def get(self, project_id: str, tool_id: str, revision: int | None = None) -> Json:
        row = self._row(project_id, tool_id)
        selected = row["revision"] if revision is None else integer(revision, "revision")
        metadata = self._metadata(row, selected)
        manifest = self.db.execute("SELECT manifest FROM project_tool_revisions WHERE tool_id=? AND revision=?",
                                   (tool_id, selected)).fetchone()[0]
        return {"project_id": project_id, "tool": {**metadata, "manifest": json.loads(manifest)}}

    def list(self, project_id: str, query: str = "", limit: int = 10, cursor: str | None = None) -> Json:
        self.store.one("projects", text(project_id, "project_id"))
        text(query, "query", 200, empty=True)
        integer(limit, "limit", 1, 20)
        bound = 2**63 - 1
        if cursor is not None:
            text(cursor, "cursor")
            if not cursor.startswith("t:") or not cursor[2:].isdigit():
                raise KiraError("INVALID_ARGUMENT", "Invalid project tool cursor")
            bound = integer(int(cursor[2:]), "cursor", 1, 2**63 - 1)
            if self.db.execute("SELECT 1 FROM project_tools WHERE project_id=? AND rowid=?",
                               (project_id, bound)).fetchone() is None:
                raise KiraError("INVALID_ARGUMENT", "Tool cursor belongs to another project")
        rows = self.db.execute("SELECT t.rowid cursor,t.* FROM project_tools t JOIN project_tool_revisions r "
            "ON r.tool_id=t.id AND r.revision=t.revision WHERE t.project_id=? AND t.rowid<? "
            "AND instr(project_tool_casefold(t.name||' '||json_extract(r.manifest,'$.description')),?)>0 "
            "ORDER BY t.rowid DESC LIMIT ?", (project_id, bound, query.casefold(), limit + 1)).fetchall()
        items = self._page([self._metadata(row, row["revision"]) for row in rows[:limit]])
        more = len(rows) > len(items)
        return {"project_id": project_id, "items": items, "has_more": more,
                "next_cursor": f"t:{rows[len(items) - 1]['cursor']}" if more else None}

    @staticmethod
    def _page(items: builtins.list[Json]) -> builtins.list[Json]:
        result: builtins.list[Json] = []
        total = 1024
        for item in items:
            size = len(canonical(item).encode("utf-8"))
            if result and total + size > MAX_RESULT_BYTES:
                break
            result.append(item)
            total += size
        return result

    def history(self, project_id: str, tool_id: str, *, cursor: str | None = None, limit: int = 10) -> Json:
        row = self._row(project_id, tool_id)
        integer(limit, "limit", 1, 20)
        before = row["revision"] + 1
        if cursor is not None:
            text(cursor, "cursor")
            if not cursor.startswith("v:") or not cursor[2:].isdigit():
                raise KiraError("INVALID_ARGUMENT", "Invalid tool history cursor")
            before = integer(int(cursor[2:]), "cursor", 1, row["revision"])
        rows = self.db.execute("SELECT revision FROM project_tool_revisions WHERE tool_id=? AND revision<? "
                              "ORDER BY revision DESC LIMIT ?", (tool_id, before, limit + 1)).fetchall()
        items = self._page([self._metadata(row, item["revision"]) for item in rows[:limit]])
        more = len(rows) > len(items)
        return {"project_id": project_id, "tool_id": tool_id, "items": items, "has_more": more,
                "next_cursor": f"v:{rows[len(items) - 1]['revision']}" if more else None}

    def configure(self, project_id: str, request_id: str, tool_id: str,
                  expected_revision: int, enabled: Any) -> Json:
        text(request_id, "request_id")
        integer(expected_revision, "expected_revision")
        if type(enabled) is not bool:
            raise KiraError("INVALID_ARGUMENT", "Tool activation requires a boolean")
        digest = fingerprint([project_id, tool_id, expected_revision, enabled])
        previous = self.db.execute("SELECT * FROM project_tool_mutations WHERE request_id=?", (request_id,)).fetchone()
        if previous is not None:
            if previous["project_id"] != project_id or previous["fingerprint"] != digest:
                raise KiraError("CONFLICT", "Tool configuration identity was reused")
            return json.loads(previous["result"])
        row = self._row(project_id, tool_id)
        if row["revision"] != expected_revision:
            raise KiraError("CONFLICT", "The tool revision changed before configuration")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE project_tools SET enabled=?,updated_at=? WHERE id=?",
                            (int(enabled), now(), tool_id))
            result = self.get(project_id, tool_id)
            self.db.execute("INSERT INTO project_tool_mutations VALUES (?,?,?,?,?,?)",
                (request_id, project_id, "configure", digest, encode(result), now()))
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reserve(self, project_id: str, session_id: str, run_id: str, call_id: str,
                action: str, payload: Json, *, parent_id: str | None = None) -> tuple[Json, Json | None]:
        text(call_id, "call_id", 256)
        digest = fingerprint([action, payload])
        row = self.db.execute("SELECT * FROM project_tool_operations WHERE run_id=? AND call_id=?",
                              (run_id, call_id)).fetchone()
        if row is not None:
            if (row["fingerprint"] != digest or row["project_id"] != project_id
                    or row["session_id"] != session_id):
                raise KiraError("CONFLICT", "Project tool call identity was reused with different arguments")
            if row["result"] is None:
                raise KiraError("UNKNOWN_OUTCOME", "Tool invocation is pending or uncertain; it will not be repeated")
            return dict(row), json.loads(row["result"])
        if self.db.execute("SELECT COUNT(*) FROM project_tool_operations WHERE run_id=?", (run_id,)).fetchone()[0] >= 512:
            raise KiraError("OUTPUT_LIMIT", "This turn's project tool invocation limit was reached")
        identifier, timestamp = new_id(), now()
        tool_id, revision = payload.get("tool_id"), payload.get("revision")
        if tool_id is not None:
            self._row(project_id, tool_id)
        self.db.execute("INSERT INTO project_tool_operations "
            "(id,project_id,session_id,run_id,call_id,action,fingerprint,parent_id,tool_id,revision,state,result,"
            "cleanup_state,started,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'admitted',NULL,'stopped',0,?,?)",
            (identifier, project_id, session_id, run_id, call_id, action, digest, parent_id, tool_id, revision,
             timestamp, timestamp))
        return self.operation(identifier), None

    def operation(self, operation_id: str) -> Json:
        row = self.db.execute("SELECT * FROM project_tool_operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Tool operation not found")
        return dict(row)

    def finish(self, operation_id: str, result: Json, *, state: str | None = None,
               cleanup: str | None = None) -> Json:
        state = state or ("completed" if result.get("ok") else "failed")
        self.db.execute("UPDATE project_tool_operations SET state=?,result=?,updated_at=?,"
            "cleanup_state=COALESCE(?,cleanup_state) WHERE id=?",
            (state, encode(result), now(), cleanup, operation_id))
        return result

    def register(self, operation_id: str, project_id: str, payload: Json, source: bytes) -> Json:
        manifest = payload["manifest"]
        identifier = payload.get("tool_id") or new_id()
        prior = self._row(project_id, identifier) if payload.get("tool_id") else None
        if prior is not None and prior["revision"] != payload["expected_revision"]:
            raise KiraError("CONFLICT", "Tool revision changed before registration")
        if prior is None and self.db.execute("SELECT COUNT(*) FROM project_tools WHERE project_id=?",
                                            (project_id,)).fetchone()[0] >= 100:
            raise KiraError("OUTPUT_LIMIT", "This project has reached its 100-tool catalog limit")
        revision = prior["revision"] + 1 if prior else 1
        digest = fingerprint([manifest, payload["source_path"], payload["expected_sha256"]])
        timestamp = now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if prior:
                self.db.execute("UPDATE project_tools SET name=?,revision=?,updated_at=? WHERE id=?",
                                (manifest["name"], revision, timestamp, identifier))
            else:
                self.db.execute("INSERT INTO project_tools VALUES (?,?,?,?,1,?,?)",
                                (identifier, project_id, manifest["name"], revision, timestamp, timestamp))
            self.db.execute("INSERT INTO project_tool_revisions VALUES (?,?,?,?,?,?,?,?)",
                (identifier, revision, canonical(manifest), source, payload["source_path"],
                 hashlib.sha256(source).hexdigest(), digest, timestamp))
            self.db.execute("UPDATE project_tool_operations SET tool_id=?,revision=? WHERE id=?",
                            (identifier, revision, operation_id))
            result = {"ok": True, "operation_id": operation_id, **self.get(project_id, identifier)}
            self.finish(operation_id, result)
            self.db.execute("COMMIT")
            return result
        except sqlite3.IntegrityError as exc:
            self.db.execute("ROLLBACK")
            raise KiraError("CONFLICT", "This project already has a tool with that name") from exc
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def source(self, project_id: str, tool_id: str, revision: int) -> bytes:
        self._row(project_id, tool_id)
        row = self.db.execute("SELECT source,source_sha256,manifest,source_path,revision_sha256 FROM project_tool_revisions WHERE tool_id=? AND revision=?",
                              (tool_id, revision)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Tool revision not found")
        source = bytes(row["source"])
        if (hashlib.sha256(source).hexdigest() != row["source_sha256"]
                or fingerprint([json.loads(row["manifest"]), row["source_path"], row["source_sha256"]]) != row["revision_sha256"]):
            raise KiraError("CONFLICT", "Registered tool source integrity failed")
        return source
