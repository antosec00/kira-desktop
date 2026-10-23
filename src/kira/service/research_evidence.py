"""Project-owned durable sources; filesystem work accepts prepared metadata only."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from kira.domain.contracts import Json, KiraError
from kira.domain.research import MAX_FILE_BYTES, MAX_SOURCE_BYTES, integer, source_ref, text
from kira.service.storage import Store, encode

SOURCE_EVENTS = frozenset({"message.user", "message.completed", "tool.started", "tool.completed",
    "run.state", "artifact.created", "input.requested", "input.answered", "input.delivered",
    "input.delivery_failed", "backend.notice"})


def _page(content: str, offset: int, limit: int, metadata: Json) -> Json:
    integer(offset, "offset", 0, 2**31 - 1)
    integer(limit, "limit", 1, 16000)
    if offset > len(content):
        raise KiraError("INVALID_ARGUMENT", "Source offset exceeds the available content")
    end = min(len(content), offset + limit)
    result = {**metadata, "content": content[offset:end], "offset": offset,
              "next_offset": end, "has_more": end < len(content)}
    while len(encode(result).encode()) > MAX_SOURCE_BYTES and end > offset:
        end = offset + (end - offset) // 2
        result.update(content=content[offset:end], next_offset=end, has_more=end < len(content))
    return result


def _read_registered(plan: Json) -> tuple[bytes, str]:
    path = Path(plan["path"])
    descriptor = parent = -1
    try:
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        for component in path.parent.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
            os.close(parent)
            parent = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise KiraError("PATH_DENIED", "Source is not an exclusive regular file")
        if plan["private"] and (before.st_uid != os.getuid() or before.st_mode & 0o077):
            raise KiraError("PATH_DENIED", "Service artifact is not private")
        if before.st_size > MAX_FILE_BYTES:
            raise KiraError("OUTPUT_LIMIT", "Artifact exceeds the research read bound")
        data = bytearray()
        while block := os.read(descriptor, min(131072, MAX_FILE_BYTES - len(data) + 1)):
            data.extend(block)
            if len(data) > MAX_FILE_BYTES:
                raise KiraError("OUTPUT_LIMIT", "Artifact exceeds the research read bound")
        after = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        def identity(info: os.stat_result) -> tuple[int, ...]:
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise KiraError("CONFLICT", "Artifact changed during verification")
        return bytes(data), hashlib.sha256(data).hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


class ResearchEvidence:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    def run(self, project_id: str, run_id: str) -> Json:
        text(project_id, "project_id")
        text(run_id, "run_id")
        self.store.one("projects", project_id)
        row = self.db.execute("SELECT r.* FROM runs r JOIN sessions s ON s.id=r.session_id "
                              "WHERE r.id=? AND s.project_id=?", (run_id, project_id)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Research source run does not belong to this project")
        return self.store.record(row)

    def provenance(self, project_id: str, run: Json) -> Json:
        binding = self.db.execute("SELECT * FROM run_provider_bindings WHERE run_id=?", (run["id"],)).fetchone()
        provider: Json = {"backend": None, "model": None, "reasoning_effort": None,
                          "account_id": None, "generation": None}
        if binding is not None:
            settings = json.loads(binding["settings"])
            provider.update(backend=binding["backend"], model=binding["model"],
                reasoning_effort=settings.get("reasoning_effort"),
                account_id=settings.get("openai_account_id"), generation=binding["generation"])
        team = self.db.execute("SELECT team_id,member_id FROM team_run_links WHERE run_id=?",
                               (run["id"],)).fetchone()
        return {"project_id": project_id, "session_id": run["session_id"], "run_id": run["id"],
                "run_state": run["state"], "provider": provider, "team": dict(team) if team else None,
                "created_at": run["created_at"], "claim_verification": False}

    def prepare(self, project_id: str, value: Any) -> Json:
        ref = source_ref(value)
        artifact: Json | None = None
        if ref["kind"] == "artifact":
            row = self.db.execute("SELECT * FROM artifacts WHERE id=? AND project_id=?",
                                   (ref["artifact_id"], project_id)).fetchone()
            if row is None:
                raise KiraError("NOT_FOUND", "Research artifact does not belong to this project")
            artifact = dict(row)
            run = self.run(project_id, artifact["run_id"])
        else:
            run = self.run(project_id, ref["run_id"])
        provenance = self.provenance(project_id, run)
        plan: Json = {"source": ref, "provenance": provenance,
                      "availability": "available", "integrity": "durable_record"}
        if ref["kind"] == "run":
            plan["content"] = encode({key: run.get(key) for key in
                                      ("id", "prompt", "state", "error", "outcome", "created_at", "updated_at")})
        elif ref["kind"] == "event":
            row = self.db.execute("SELECT * FROM events WHERE run_id=? AND seq=?",
                                  (run["id"], ref["seq"])).fetchone()
            if row is None or row["type"] not in SOURCE_EVENTS:
                raise KiraError("NOT_FOUND", "Public research source event not found")
            payload = json.loads(row["payload"])
            plan["content"] = encode({"run_id": run["id"], "seq": row["seq"],
                                      "type": row["type"], "timestamp": row["timestamp"], "payload": payload})
            provenance.update(event_type=row["type"], event_seq=row["seq"])
            plan["upstream_truncated"] = payload.get("truncated") is True
        else:
            assert artifact is not None
            plan["artifact"] = {key: artifact[key] for key in ("id", "kind", "sha256", "created_at", "tool_id")}
            event = self.db.execute("SELECT payload FROM events WHERE run_id=? AND type='artifact.created' "
                "AND timestamp=? AND json_extract(payload,'$.path')=? "
                "AND json_extract(payload,'$.sha256') IS ? ORDER BY seq LIMIT 1",
                (run["id"], artifact["created_at"], artifact["path"], artifact["sha256"])).fetchone()
            metadata = json.loads(event[0]) if event else {}
            plan["upstream_truncated"] = metadata.get("truncated") if type(metadata.get("truncated")) is bool else None
            if type(metadata.get("bytes")) is int:
                plan["artifact"]["reported_bytes"] = metadata["bytes"]
            path = Path(artifact["path"])
            root = Path(self.store.one("projects", project_id)["root"])
            data = self.store.path.parent.absolute()
            private = True
            allowed = False
            if path.is_absolute() and ".." not in path.parts:
                if artifact["kind"] in {"project_file", "file"}:
                    allowed = path.is_relative_to(root) and path != root
                    private = False
                elif artifact["kind"] == "tool_output":
                    allowed = path.parent == data / "artifacts" / run["id"]
                elif artifact["kind"] == "browser_screenshot":
                    item = self.db.execute("SELECT metadata FROM image_attachments WHERE id=?", (path.stem,)).fetchone()
                    if item is not None:
                        meta = json.loads(item[0])
                        allowed = (path == data / "image-attachments" / (path.stem + ".png")
                                   and meta.get("sha256") == artifact["sha256"])
            if not allowed or not isinstance(artifact["sha256"], str):
                plan.update(availability="unavailable", integrity="unverifiable", content="")
            else:
                plan["file_plan"] = {"path": str(path), "sha256": artifact["sha256"], "private": private,
                                     "kind": artifact["kind"]}
        return plan

    @staticmethod
    def finish(prepared: Json, offset: int = 0, limit: int = 16000) -> Json:
        integer(offset, "offset", 0, 2**31 - 1)
        integer(limit, "limit", 1, 16000)
        result = {key: value for key, value in prepared.items() if key not in {"file_plan", "content"}}
        content = prepared.get("content", "")
        plan = prepared.get("file_plan")
        if plan:
            try:
                data, actual = _read_registered(plan)
                if actual != plan["sha256"]:
                    result.update(availability="changed", integrity="mismatch")
                else:
                    result.update(availability="available", integrity="sha256_match", bytes=len(data))
                    try:
                        content = data.decode("utf-8")
                        if b"\0" in data or plan["kind"] == "browser_screenshot":
                            content = ""
                            result["binary"] = True
                    except UnicodeDecodeError:
                        result["binary"] = True
                        content = ""
            except FileNotFoundError:
                result.update(availability="missing", integrity="unavailable")
            except (OSError, KiraError):
                result.update(availability="unavailable", integrity="unverifiable")
        if result["availability"] != "available":
            return {**result, "content": "", "offset": offset, "next_offset": offset, "has_more": False}
        return _page(content, offset, limit, result)

    def sources(self, project_id: str, run_id: str | None = None,
                cursor: str | None = None, limit: int = 20) -> Json:
        self.store.one("projects", text(project_id, "project_id"))
        integer(limit, "limit", 1, 30)
        bound = 2**63 - 1
        prefix = "e" if run_id else "r"
        if cursor is not None:
            text(cursor, "cursor")
            parts = cursor.split(":")
            if len(parts) != 2 or parts[0] != prefix or not parts[1].isdigit():
                raise KiraError("INVALID_ARGUMENT", "Invalid source cursor")
            bound = integer(int(parts[1]), "cursor", 1)
        items = []
        if run_id is None:
            if cursor is not None and self.db.execute("SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id "
                    "WHERE r.rowid=? AND s.project_id=?", (bound, project_id)).fetchone() is None:
                raise KiraError("INVALID_ARGUMENT", "Source cursor does not belong to this project")
            rows = self.db.execute("SELECT r.*,r.rowid cursor FROM runs r JOIN sessions s ON s.id=r.session_id "
                "WHERE s.project_id=? AND r.rowid<? ORDER BY r.rowid DESC LIMIT ?", (project_id, bound, limit + 1)).fetchall()
            for row in rows[:limit]:
                run = self.store.record(row)
                items.append({"ref": {"kind": "run", "run_id": run["id"]},
                    "label": run["prompt"][:200], "label_truncated": len(run["prompt"]) > 200,
                    "provenance": self.provenance(project_id, run), "state": run["state"], "cursor": f"r:{row['cursor']}"})
        else:
            run = self.run(project_id, run_id)
            if cursor is not None and self.db.execute("SELECT 1 FROM events WHERE run_id=? AND seq=?", (run_id, bound)).fetchone() is None:
                raise KiraError("INVALID_ARGUMENT", "Source cursor does not belong to this run")
            placeholders = ",".join("?" for _ in SOURCE_EVENTS)
            rows = self.db.execute(f"SELECT * FROM events WHERE run_id=? AND seq<? AND type IN ({placeholders}) "
                "ORDER BY seq DESC LIMIT ?", (run_id, bound, *sorted(SOURCE_EVENTS), limit + 1)).fetchall()
            provenance = self.provenance(project_id, run)
            for row in rows[:limit]:
                payload = json.loads(row["payload"])
                ref: Json = {"kind": "event", "run_id": run_id, "seq": row["seq"]}
                if row["type"] == "artifact.created":
                    artifact = self.db.execute("SELECT id FROM artifacts WHERE run_id=? AND project_id=? AND path=? "
                        "AND sha256 IS ? ORDER BY rowid DESC LIMIT 1", (run_id, project_id, payload.get("path"), payload.get("sha256"))).fetchone()
                    if artifact:
                        ref = {"kind": "artifact", "artifact_id": artifact[0]}
                label = str(payload.get("name") or payload.get("content") or payload.get("kind") or payload.get("state") or "")
                items.append({"ref": ref, "label": row["type"] + (" · " + label[:160] if label else ""),
                    "provenance": {**provenance, "event_type": row["type"], "event_seq": row["seq"], "timestamp": row["timestamp"]},
                    "state": run["state"], "cursor": f"e:{row['seq']}"})
        return {"project_id": project_id, "items": items, "has_more": len(rows) > limit,
                "next_cursor": items[-1]["cursor"] if len(rows) > limit and items else None}
