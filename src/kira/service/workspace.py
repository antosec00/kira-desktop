"""Project continuity and explicit, durable desktop intents for IPC v1."""

import asyncio
import hashlib
import json
import os
import platform
import stat
from pathlib import Path

from kira import __version__
from kira.domain.contracts import EVENT_SCHEMA_VERSION, SCHEMA_VERSION, Json, KiraError
from kira.domain.paths import Paths
from kira.service.desktop import ACTION_IDS, Desktop, private_directory
from kira.service.redaction import Redactor
from kira.service.storage import Store, encode, new_id, now
from kira.tools.filesystem import FileTools

MAX_WATCHES = 64
MAX_WATCH_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
DEFAULT_TOOLS = ["folder", "terminal", "editor"]
METHODS = {
    "workspace.get", "workspace.update", "workspace.watch", "workspace.unwatch", "workspace.launch",
    "desktop.actions", "desktop.launch", "system.diagnostics", "diagnostics.export",
}


def fields(params: Json, allowed: set[str]) -> None:
    if set(params) - allowed:
        raise KiraError("INVALID_ARGUMENT", "Unknown workspace or desktop parameter")


def text(params: Json, key: str, maximum: int = 128, empty: bool = False) -> str:
    value = params.get(key)
    if (not isinstance(value, str) or "\0" in value or
            len(value.encode("utf-8")) > maximum or (not empty and not value.strip())):
        raise KiraError("INVALID_ARGUMENT", f"{key} must be a string of at most {maximum} bytes")
    return value


def relative_file(params: Json) -> str:
    path = text(params, "path", 4096)
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise KiraError("PATH_DENIED", "Watch paths must be relative files inside the project")
    return candidate.as_posix()


def unknown_result(request_id: str, action_id: str) -> Json:
    return {"request_id": request_id, "action_id": action_id, "status": "unknown",
            "message": "Invio non confermato. L'azione non viene ripetuta automaticamente."}


class Workspaces:
    def __init__(self, store: Store, paths: Paths, redactor: Redactor) -> None:
        self.store, self.paths, self.redactor = store, paths, redactor
        self.desktop = Desktop()

    def project(self, params: Json, required: bool = True) -> Json | None:
        if not required and "project_id" not in params:
            return None
        return self.store.one("projects", text(params, "project_id"))

    def preferences(self, project_id: str) -> Json:
        row = self.store.db.execute("SELECT tools,next_step FROM workspaces WHERE project_id=?",
                                    (project_id,)).fetchone()
        return {"tools": json.loads(row["tools"]), "next_step": row["next_step"]} if row else {
            "tools": list(DEFAULT_TOOLS), "next_step": ""}

    async def get(self, project: Json) -> Json:
        project_id = project["id"]
        preferences = self.preferences(project_id)
        latest = self.store.all(
            "SELECT r.id,r.state,r.updated_at,r.outcome,r.error FROM runs r JOIN sessions s "
            "ON r.session_id=s.id WHERE s.project_id=? ORDER BY r.updated_at DESC,r.rowid DESC LIMIT 1",
            (project_id,))
        last_run = None
        if latest:
            run = latest[0]
            summary = run["error"] or (encode(run["outcome"]) if run["outcome"] else "")
            last_run = {"id": run["id"], "state": run["state"], "updated_at": run["updated_at"],
                        "summary": summary[:2000]}
        counts = self.store.db.execute(
            "SELECT SUM(r.state NOT IN ('completed','failed','cancelled','interrupted')),"
            "SUM(r.state='interrupted') FROM runs r JOIN sessions s ON r.session_id=s.id "
            "WHERE s.project_id=?", (project_id,)).fetchone()
        watches = self.store.all("SELECT path,sha256 FROM file_watches WHERE project_id=? ORDER BY path",
                                 (project_id,))
        # SQLite stays on the service event loop; filesystem reads use one bounded worker.
        watches = await asyncio.to_thread(self._check_watches, project["root"], watches)
        captures = self.store.all("SELECT id,path,sha256,created_at FROM captures WHERE project_id=? "
                                  "ORDER BY created_at DESC LIMIT 100", (project_id,))
        capture_count = self.store.db.execute("SELECT COUNT(*) FROM captures WHERE project_id=?",
                                              (project_id,)).fetchone()[0]
        return {"project_id": project_id, **preferences,
                "resume": {"last_run": last_run, "active_count": counts[0] or 0,
                           "interrupted_count": counts[1] or 0}, "watches": watches,
                "captures": captures, "capture_count": capture_count,
                "captures_truncated": capture_count > len(captures),
                "watch_limits": {"count": MAX_WATCHES, "bytes_per_file": MAX_WATCH_BYTES}}

    @staticmethod
    def _check_watches(root: str, watches: list[Json]) -> list[Json]:
        for watch in watches:
            watch.update({"current_sha256": None, "checked_at": now()})
            try:
                current = FileTools(Path(root), MAX_WATCH_BYTES).hash_file(watch["path"])
                watch["current_sha256"] = current["sha256"]
                watch["state"] = "unchanged" if current["sha256"] == watch["sha256"] else "changed"
            except FileNotFoundError:
                watch.update(state="missing", reason="File o cartella del progetto non presente.")
            except (OSError, KiraError, RuntimeError) as exc:
                reason = str(exc) if isinstance(exc, KiraError) else "File non leggibile in sicurezza."
                watch.update(state="unavailable", reason=reason)
        return watches

    async def dispatch(self, method: str, params: Json) -> Json:
        if method in {"system.diagnostics", "diagnostics.export"}:
            fields(params, set())
            report = self.diagnostics()
            if method == "system.diagnostics":
                return report
            directory = private_directory(self.paths.data / "diagnostics")
            export_path = directory / f"diagnostics-{new_id()}.json"
            descriptor = os.open(export_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            return {"path": str(export_path)}
        if method == "desktop.actions":
            fields(params, {"project_id", "query"})
            project = self.project(params, required=False)
            query = text({"query": params.get("query", "")}, "query", 2000, empty=True)
            return {"actions": self.desktop.catalog(project, query)}
        if method == "desktop.launch":
            fields(params, {"action_id", "project_id", "request_id", "query"})
            project = self.project(params, required=False)
            action_id = text(params, "action_id")
            if action_id not in ACTION_IDS:
                raise KiraError("INVALID_ARGUMENT", "Unknown desktop action")
            query = text({"query": params.get("query", "")}, "query", 2000,
                         empty=action_id != "browser.search")
            if action_id != "browser.search" and query:
                raise KiraError("INVALID_ARGUMENT", "query is only accepted for browser.search")
            return await self.launch(method, params, project, [action_id], query)
        allowed = {"project_id"}
        if method == "workspace.update":
            allowed |= {"tools", "next_step"}
        elif method in {"workspace.watch", "workspace.unwatch"}:
            allowed.add("path")
        elif method == "workspace.launch":
            allowed.add("request_id")
        fields(params, allowed)
        project = self.project(params)
        assert project is not None
        project_id = project["id"]
        if method == "workspace.update":
            preferences = self.preferences(project_id)
            if "tools" in params:
                tools = params["tools"]
                if (not isinstance(tools, list) or len(tools) > 3
                        or any(not isinstance(tool, str) or tool not in DEFAULT_TOOLS for tool in tools)
                        or len(set(tools)) != len(tools)):
                    raise KiraError("INVALID_ARGUMENT", "tools must be distinct folder/terminal/editor choices")
                preferences["tools"] = list(tools)
            if "next_step" in params:
                preferences["next_step"] = self.redactor.text(text(params, "next_step", 8000, empty=True))
            self.store.db.execute("INSERT INTO workspaces VALUES (?,?,?,?) ON CONFLICT(project_id) "
                                  "DO UPDATE SET tools=excluded.tools,next_step=excluded.next_step,"
                                  "updated_at=excluded.updated_at",
                                  (project_id, encode(preferences["tools"]), preferences["next_step"], now()))
        elif method in {"workspace.watch", "workspace.unwatch"}:
            path = relative_file(params)
            if method == "workspace.unwatch":
                self.store.db.execute("DELETE FROM file_watches WHERE project_id=? AND path=?",
                                      (project_id, path))
            else:
                try:
                    reference = await asyncio.to_thread(
                        lambda: FileTools(Path(project["root"]), MAX_WATCH_BYTES).hash_file(path))
                except (OSError, RuntimeError) as exc:
                    raise KiraError("UNAVAILABLE", "Reference file cannot be read safely") from exc
                exists = self.store.db.execute("SELECT 1 FROM file_watches WHERE project_id=? AND path=?",
                                                (project_id, path)).fetchone()
                count = self.store.db.execute("SELECT COUNT(*) FROM file_watches WHERE project_id=?",
                                               (project_id,)).fetchone()[0]
                if not exists and count >= MAX_WATCHES:
                    raise KiraError("OUTPUT_LIMIT", f"At most {MAX_WATCHES} file references per project")
                self.store.db.execute("INSERT INTO file_watches VALUES (?,?,?,?) "
                                      "ON CONFLICT(project_id,path) DO UPDATE SET "
                                      "sha256=excluded.sha256,created_at=excluded.created_at",
                                      (project_id, reference["path"], reference["sha256"], now()))
        elif method == "workspace.launch":
            actions = ["project." + tool for tool in self.preferences(project_id)["tools"]]
            return await self.launch(method, params, project, actions, "")
        return await self.get(project)

    async def launch(self, method: str, params: Json, project: Json | None,
                     actions: list[str], query: str) -> Json:
        request_id = text(params, "request_id")
        # Preferences are a snapshot at first dispatch. Retrying an identical request
        # returns that intent even after the user changes workspace preferences.
        normalized = {"project_id": project["id"] if project else None}
        if method == "desktop.launch":
            normalized.update(action_id=actions[0], query=query)
        fingerprint = hashlib.sha256(encode(normalized).encode()).hexdigest()
        previous = self.store.db.execute("SELECT * FROM desktop_requests WHERE request_id=?",
                                         (request_id,)).fetchone()
        if previous:
            if previous["method"] != method or previous["fingerprint"] != fingerprint:
                raise KiraError("CONFLICT", "request_id was already used with different input")
            return dict(json.loads(previous["result"]))
        results = [unknown_result(request_id if method == "desktop.launch" else
                                  f"{request_id}:{index}", action_id)
                   for index, action_id in enumerate(actions)]
        result: Json = results[0] if method == "desktop.launch" else {
            "request_id": request_id, "results": results}
        self.store.db.execute("INSERT INTO desktop_requests VALUES (?,?,?,?,?,?,?,?)",
                              (request_id, method, fingerprint, normalized["project_id"], "pending",
                               encode(result), now(), now()))
        for index, action_id in enumerate(actions):
            # Cancellation/crash preserves the precommitted unknown state. No implicit retry.
            confirmed = await self._execute(action_id, project, results[index]["request_id"], query)
            results[index] = confirmed
            if method == "desktop.launch":
                result = confirmed
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                artifact = confirmed.get("artifact")
                if artifact:
                    assert project is not None
                    self.store.db.execute("INSERT INTO captures VALUES (?,?,?,?,?)",
                                          (artifact["id"], project["id"], artifact["path"],
                                           artifact["sha256"], artifact["created_at"]))
                self.store.db.execute("UPDATE desktop_requests SET result=?,updated_at=? WHERE request_id=?",
                                      (encode(result), now(), request_id))
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
        self.store.db.execute("UPDATE desktop_requests SET status='finished',updated_at=? WHERE request_id=?",
                              (now(), request_id))
        return result

    async def _execute(self, action_id: str, project: Json | None, request_id: str, query: str) -> Json:
        result = unknown_result(request_id, action_id)
        available = next(action for action in self.desktop.catalog(project) if action["id"] == action_id)
        if not available["available"]:
            result.update(status="unavailable", message=available["reason"])
            return result
        capture_path = None
        try:
            if action_id == "screenshot.capture":
                directory = private_directory(self.paths.data / "captures")
                capture_path = directory / f"{new_id()}.png"
            await self.desktop.launch(action_id, project, query, capture_path)
            if capture_path:
                artifact = await asyncio.to_thread(self._capture, capture_path)
                result.update(status="completed", message="Schermata salvata e verificata.", artifact=artifact)
            else:
                result.update(status="requested", message="Apertura richiesta all'applicazione nativa.")
        except (KiraError, OSError, TimeoutError, RuntimeError) as exc:
            message = str(exc) if isinstance(exc, KiraError) else "L'applicazione non ha completato la richiesta."
            result.update(status="failed", message=self.redactor.text(message))
            if capture_path:
                capture_path.unlink(missing_ok=True)
        return result

    @staticmethod
    def _capture(path: Path) -> Json:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 33 <= info.st_size <= MAX_CAPTURE_BYTES:
                raise KiraError("CAPTURE_FAILED", "No complete screenshot was created")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(MAX_CAPTURE_BYTES + 1)
            # PNG signature, IHDR and terminal IEND reject empty/partial placeholders.
            if (len(data) != info.st_size or not data.startswith(b"\x89PNG\r\n\x1a\n")
                    or data[12:16] != b"IHDR" or data[-12:] != b"\0\0\0\0IEND\xaeB`\x82"):
                raise KiraError("CAPTURE_FAILED", "Screenshot output is incomplete or not PNG")
            after = os.fstat(descriptor)
            if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, path.lstat().st_ino):
                raise KiraError("CAPTURE_FAILED", "Screenshot changed while verifying it")
            os.fchmod(descriptor, 0o600)
            return {"id": new_id(), "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                    "created_at": now()}
        finally:
            os.close(descriptor)

    def diagnostics(self) -> Json:
        try:
            release = platform.freedesktop_os_release()
        except OSError:
            release = {}
        operating_system = release.get("ID")
        version = release.get("VERSION_ID", "")
        architecture = platform.machine()
        session = os.environ.get("XDG_SESSION_TYPE", "")
        active = self.store.db.execute("SELECT COUNT(*) FROM runs WHERE state NOT IN "
                                       "('completed','failed','cancelled','interrupted')").fetchone()[0]
        # Whitelist values too: never serialize arbitrary environment or platform strings.
        return {"app_version": __version__, "database_schema": SCHEMA_VERSION,
                "event_schema": EVENT_SCHEMA_VERSION,
                "platform": {"os": operating_system if operating_system in {"ubuntu", "debian"} else "other",
                             "version": version if version in {"24.04", "26.04"} else "other",
                             "architecture": architecture if architecture in {"x86_64", "aarch64"} else "other",
                             "session": session if session in {"wayland", "x11", "tty"} else "unknown"},
                "service": {"active_run_count": active}, "actions": self.desktop.catalog()}
