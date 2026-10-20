"""Project-owned opt-in Studio worlds, durable grants and trusted renderer.

UI grants are never callable by model tools. Every agent edit rechecks its run,
provider generation, chat, effective write scope and live opt-in grant.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import TYPE_CHECKING, Any
from collections.abc import Callable
import uuid

from kira.domain.contracts import Json, KiraError, WRITE_PERMISSIONS
from kira.domain.studio import MAX_SCENE_BYTES, patch_scene, poster_scene, validate_scene
from kira.service.project_context import ProjectChats
from kira.service.storage import encode, now
from kira.service.tool_policy import kira_tools_enabled

if TYPE_CHECKING:
    from kira.service.server import Service

METHODS = frozenset({
    "studio.status", "studio.enable", "studio.poster", "studio.patch",
    "studio.restore", "studio.render", "studio.export", "studio.history",
    "studio.grant",
})
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_REVISIONS = 2000


def _fields(args: Json, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(args, dict) or set(args) - (required | (optional or set())) \
            or required - set(args):
        raise KiraError("INVALID_ARGUMENT", "Unsupported Studio request fields")


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise KiraError("INVALID_ARGUMENT", f"{label} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise KiraError("INVALID_ARGUMENT", f"{label} must be a UUID") from exc
    if str(parsed) != value.lower():
        raise KiraError("INVALID_ARGUMENT", f"{label} must be a canonical UUID")
    return value


def _rev(value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_REVISIONS:
        raise KiraError("INVALID_ARGUMENT", "Invalid Studio revision")
    return value


class Studio:
    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = service.store
        self._renders: dict[str, asyncio.subprocess.Process] = {}

    def _project(self, args: Json) -> str:
        project_id = _uuid(args.get("project_id"), "project_id")
        self.store.one("projects", project_id)
        return project_id

    def _session(self, project_id: str, session_id: str) -> Json:
        session = ProjectChats(self.store).owned_session(project_id, session_id)
        if (self.store.db.execute("SELECT 1 FROM team_members WHERE session_id=?",
                                  (session_id,)).fetchone()
                or ProjectChats(self.store).enrich(session).get("archived")):
            raise KiraError("UNSUPPORTED", "Studio AI grants require an ordinary active chat")
        return session

    def grant_state(self, project_id: str, session_id: str) -> Json:
        self._session(project_id, session_id)
        row = self.store.db.execute(
            "SELECT enabled,generation,provider_backend,provider_model,"
            "provider_generation FROM studio_grants "
            "WHERE project_id=? AND session_id=?", (project_id, session_id)
        ).fetchone()
        session = self._session(project_id, session_id)
        current_generation = self.service.provider_routing.current_generation(session_id)
        enabled = bool(row and row["enabled"]
                       and row["provider_backend"] == session["backend"]
                       and row["provider_model"] == session["model"]
                       and row["provider_generation"] == current_generation)
        return {"agent_enabled": enabled, "grant_generation":
                int(row["generation"]) if row else 0}

    def _world(self, project_id: str, session_id: str | None = None) -> Json:
        row = self.store.db.execute(
            "SELECT enabled,revision FROM studio_worlds WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None:
            state: Json = {"enabled": False, "revision": 0, "scene": {}, "history": []}
            if session_id is not None:
                state.update(self.grant_state(project_id, session_id))
            return state
        revision = int(row["revision"])
        scene: Json = {}
        if revision:
            item = self.store.db.execute(
                "SELECT scene FROM studio_revisions WHERE project_id=? AND revision=?",
                (project_id, revision),
            ).fetchone()
            if item is None:
                raise KiraError("INTEGRITY", "Studio revision is missing")
            scene = validate_scene(json.loads(item["scene"]))
        history = [
            {"revision": r["revision"], "created_at": r["created_at"], "origin": r["origin"]}
            for r in self.store.db.execute(
                "SELECT revision,created_at,origin FROM studio_revisions "
                "WHERE project_id=? ORDER BY revision DESC LIMIT 20", (project_id,)
            )
        ]
        state = {"enabled": bool(row["enabled"]), "revision": revision,
                 "scene": scene, "history": history}
        if session_id is not None:
            state.update(self.grant_state(project_id, session_id))
        return state

    def _create_world(self, project_id: str) -> None:
        self.store.db.execute(
            "INSERT OR IGNORE INTO studio_worlds"
            "(project_id,enabled,revision,created_at,updated_at) "
            "VALUES (?,0,0,?,?)", (project_id, now(), now())
        )

    def _mutate(self, project_id: str, request_id: str, method: str, values: Json,
                *, agent: Json | None = None) -> Json:
        receipt_method = method if agent is None else "agent:" + method
        payload = encode({"method": receipt_method, "params": values}).encode("utf-8")
        fingerprint = hashlib.sha256(payload).hexdigest()
        db = self.store.db
        db.execute("BEGIN IMMEDIATE")
        try:
            self._create_world(project_id)
            old = db.execute(
                "SELECT fingerprint,result FROM studio_operations "
                "WHERE project_id=? AND request_id=?", (project_id, request_id)
            ).fetchone()
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise KiraError("CONFLICT", "Studio request ID reused with different arguments")
                result = json.loads(old["result"])
                db.execute("COMMIT")
                return result

            state = self._world(project_id)
            if method == "studio.enable":
                expected = values["expected_enabled"]
                enabled = values["enabled"]
                if type(expected) is not bool or type(enabled) is not bool:
                    raise KiraError("INVALID_ARGUMENT", "Studio enablement requires booleans")
                if state["enabled"] != expected:
                    raise KiraError("CONFLICT", "Studio activation changed; reload first")
                db.execute(
                    "UPDATE studio_worlds SET enabled=?,updated_at=? WHERE project_id=?",
                    (int(enabled), now(), project_id)
                )
                if not enabled:
                    # Disabling a world also revokes all prior per-chat grants.
                    # A later re-enable must never resurrect model authority.
                    db.execute(
                        "UPDATE studio_grants SET enabled=0,generation=generation+1,"
                        "updated_at=? WHERE project_id=? AND enabled=1",
                        (now(), project_id),
                    )
            else:
                if not state["enabled"]:
                    raise KiraError("STUDIO_DISABLED", "Enable Studio in the project UI first")
                revision = _rev(values["expected_revision"])
                if state["revision"] != revision:
                    raise KiraError("CONFLICT", "Studio scene has changed; reload before editing")
                if revision >= MAX_REVISIONS:
                    raise KiraError("LIMIT_REACHED", "Export world history before continuing")
                if method == "studio.poster":
                    scene = poster_scene(
                        values["text"],
                        blue=values.get("text_color", "#2467c9"),
                        background=values.get("background", "#f0e7f3"),
                    )
                elif method == "studio.patch":
                    if not state["scene"]:
                        raise KiraError("NOT_FOUND", "Create a poster first")
                    scene = patch_scene(state["scene"], values["object_id"], values["values"])
                elif method == "studio.restore":
                    target = _rev(values["target_revision"], minimum=1)
                    row = db.execute(
                        "SELECT scene FROM studio_revisions WHERE project_id=? AND revision=?",
                        (project_id, target)
                    ).fetchone()
                    if row is None:
                        raise KiraError("NOT_FOUND", "Studio target revision does not exist")
                    scene = validate_scene(json.loads(row["scene"]))
                else:
                    raise KiraError("METHOD_NOT_FOUND", "Unsupported Studio mutation")
                new_revision = revision + 1
                origin = ("agent" if agent is not None else
                          "restore" if method == "studio.restore" else "user")
                db.execute(
                    "INSERT INTO studio_revisions(project_id,revision,scene,origin,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (project_id, new_revision, encode(scene), origin, now())
                )
                if agent is not None:
                    db.execute(
                        "INSERT INTO studio_agent_commits "
                        "(project_id,revision,session_id,run_id,tool_call_id) "
                        "VALUES (?,?,?,?,?)",
                        (project_id, new_revision, agent["session_id"],
                         agent["run_id"], agent["call_id"]),
                    )
                db.execute(
                    "UPDATE studio_worlds SET revision=?,updated_at=? WHERE project_id=?",
                    (new_revision, now(), project_id)
                )
            result = self._world(project_id)
            db.execute(
                "INSERT INTO studio_operations"
                "(project_id,request_id,fingerprint,result,created_at) "
                "VALUES(?,?,?,?,?)",
                (project_id, request_id, fingerprint, encode(result), now())
            )
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        if method == "studio.enable" and values["enabled"] is False:
            process = self._renders.get(project_id)
            if process and process.returncode is None:
                process.terminate()
        return result

    async def dispatch(self, method: str, args: Json) -> Json:
        if method not in METHODS:
            raise KiraError("METHOD_NOT_FOUND", "Unknown Studio method")
        patterns = {
            "studio.status": (set(), {"session_id"}),
            "studio.history": (set(), set()),
            "studio.enable": ({"enabled", "expected_enabled", "request_id"}, set()),
            "studio.grant": (
                {"session_id", "enabled", "expected_enabled", "request_id"}, set()),
            "studio.poster": ({"text", "expected_revision", "request_id"},
                              {"text_color", "background"}),
            "studio.patch": ({"object_id", "values", "expected_revision", "request_id"}, set()),
            "studio.restore": ({"target_revision", "expected_revision", "request_id"}, set()),
            "studio.render": (set(), set()),
            "studio.export": (set(), set()),
        }
        required, optional = patterns[method]
        _fields(args, {"project_id"} | required, optional)
        project_id = self._project(args)
        if method in {"studio.status", "studio.history"}:
            session_id = (str(args["session_id"]) if "session_id" in args else None)
            state = self._world(project_id, session_id)
            return {"history": state["history"]} if method == "studio.history" else state
        if method in {"studio.render", "studio.export"}:
            return await self._render(project_id, export=method == "studio.export")
        request_id = _uuid(args["request_id"], "request_id")
        if method == "studio.grant":
            return self._grant(project_id, request_id, args)
        payload = {key: value for key, value in args.items()
                   if key not in {"project_id", "request_id"}}
        result = self._mutate(project_id, request_id, method, payload)
        return result

    def _grant(self, project_id: str, request_id: str, args: Json) -> Json:
        """Only private user IPC, not registered in any provider tool catalog."""
        session_id = _uuid(args["session_id"], "session_id")
        self._session(project_id, session_id)
        enabled, expected = args["enabled"], args["expected_enabled"]
        if type(enabled) is not bool or type(expected) is not bool:
            raise KiraError("INVALID_ARGUMENT", "Studio grants require boolean values")
        if enabled and self.store.one("projects", project_id)["permission"] \
                not in WRITE_PERMISSIONS:
            raise KiraError("PERMISSION_DENIED", "Model edits require project write access")
        if enabled and not kira_tools_enabled(self._session(project_id, session_id)):
            raise KiraError("PERMISSION_DENIED", "Enable chat tools before Studio AI editing")
        fingerprint = hashlib.sha256(encode({
            "method": "studio.grant", "session_id": session_id,
            "enabled": enabled, "expected_enabled": expected,
        }).encode("utf-8")).hexdigest()
        db = self.store.db
        db.execute("BEGIN IMMEDIATE")
        try:
            self._create_world(project_id)
            receipt = db.execute(
                "SELECT fingerprint,result FROM studio_operations "
                "WHERE project_id=? AND request_id=?", (project_id, request_id),
            ).fetchone()
            if receipt is not None:
                if receipt["fingerprint"] != fingerprint:
                    raise KiraError("CONFLICT", "Studio grant request ID was reused")
                result = json.loads(receipt["result"])
                db.execute("COMMIT")
                return result
            if enabled and not self._world(project_id)["enabled"]:
                raise KiraError("STUDIO_DISABLED", "Enable Studio before granting AI edits")
            current = self.grant_state(project_id, session_id)
            selected = self._session(project_id, session_id)
            provider_generation = self.service.provider_routing.current_generation(session_id)
            if current["agent_enabled"] != expected:
                raise KiraError("CONFLICT", "Studio chat permission changed; reload first")
            generation = current["grant_generation"]
            if enabled != expected:
                generation += 1
                db.execute(
                    "INSERT INTO studio_grants"
                    "(project_id,session_id,enabled,generation,provider_backend,"
                    "provider_model,provider_generation,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(project_id,session_id) DO UPDATE SET "
                    "enabled=excluded.enabled,generation=excluded.generation,"
                    "provider_backend=excluded.provider_backend,"
                    "provider_model=excluded.provider_model,"
                    "provider_generation=excluded.provider_generation,"
                    "updated_at=excluded.updated_at",
                    (project_id, session_id, int(enabled), generation,
                     selected["backend"], selected["model"], provider_generation, now()),
                )
            result = {"agent_enabled": enabled, "grant_generation": generation}
            db.execute(
                "INSERT INTO studio_operations"
                "(project_id,request_id,fingerprint,result,created_at) VALUES(?,?,?,?,?)",
                (project_id, request_id, fingerprint, encode(result), now()),
            )
            db.execute("COMMIT")
            return result
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise

    def attach(self, session: Json, project: Json, run_id: str,
               cancel: asyncio.Event, mode: str, options: Json) -> Json:
        """Add one run-scoped model gateway only with current human consent."""
        from kira.service.studio_agent import attach_studio
        return attach_studio(self, session, project, run_id, cancel, mode, options)

    def _directory(self, name: str) -> Path:
        root = self.service.paths.data / "studio"
        # XDG profile path belongs to Kira. Reject replacement of the Studio dir.
        if root.is_symlink():
            raise KiraError("UNSAFE_PATH", "Studio directory is a symlink")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = root / name
        if target.is_symlink():
            raise KiraError("UNSAFE_PATH", "Studio output directory is a symlink")
        target.mkdir(mode=0o700, exist_ok=True)
        return target

    @staticmethod
    def _atomic_png(directory: Path, name: str, png: bytes) -> Path:
        if len(png) > MAX_PNG_BYTES or not png.startswith(PNG_SIGNATURE):
            raise KiraError("RENDER_FAILED", "Studio renderer did not produce a bounded PNG")
        file_name = directory / name
        if file_name.is_symlink():
            raise KiraError("UNSAFE_PATH", "Refusing Studio output symlink")
        fd, temporary = tempfile.mkstemp(prefix=".studio-", suffix=".tmp", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(png)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, file_name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return file_name

    async def _render(self, project_id: str, *, export: bool,
                      guard: Callable[[], None] | None = None) -> Json:
        if guard is not None:
            guard()
        state = self._world(project_id)
        if not state["enabled"] or not state["scene"]:
            raise KiraError("STUDIO_DISABLED", "Studio is not enabled or has no scene")
        if project_id in self._renders:
            raise KiraError("BUSY", "Studio is already rendering this project")
        revision = state["revision"]
        scene = validate_scene(state["scene"])
        raw = encode(scene).encode("utf-8")
        if len(raw) > MAX_SCENE_BYTES:
            raise KiraError("INVALID_ARGUMENT", "Studio scene exceeds worker limit")
        # Package-owned interpreter/module, no shell and no model-generated code.
        # This worker is a crash/fault boundary, not a general same-UID sandbox.
        env = {"QT_QPA_PLATFORM": "offscreen", "PATH": os.defpath,
               "LANG": os.environ.get("LANG", "C.UTF-8")}
        # Packaged .deb uses python -I with /opt/kira-desktop/lib injected by
        # its launcher. A plain python -m here would fail to locate bundled Kira.
        # Anchor this child to the already-imported, trusted Kira package root.
        package_root = str(Path(__file__).resolve().parents[2])
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "-c",
            "import runpy,sys; sys.path.insert(0,sys.argv[1]); "
            "runpy.run_module('kira.tools.studio_worker',run_name='__main__')",
            package_root,
            cwd=str(self.service.paths.data),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True
        )
        self._renders[project_id] = process
        try:
            try:
                output, _ = await asyncio.wait_for(process.communicate(raw), timeout=12)
            except (TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
            if process.returncode != 0:
                raise KiraError("RENDER_FAILED", "Studio graphics worker failed")
            if not output.startswith(PNG_SIGNATURE) or len(output) > MAX_PNG_BYTES:
                raise KiraError("RENDER_FAILED", "Studio returned invalid image data")
            if guard is not None:
                guard()
            updated = self._world(project_id)
            if not updated["enabled"] or updated["revision"] != revision:
                raise KiraError("CONFLICT", "Studio changed while rendering")
            preview = self._atomic_png(self._directory("previews"),
                                       f"{project_id}.png", output)
            if not export:
                return {"path": str(preview), "revision": revision}
            destination = self._directory("exports")
            exported = self._atomic_png(destination,
                                        f"poster-{project_id}-r{revision}-"
                                        f"{uuid.uuid4().hex[:8]}.png",
                                        output)
            return {"path": str(exported), "revision": revision}
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if self._renders.get(project_id) is process:
                del self._renders[project_id]

    async def close(self) -> None:
        processes = list(self._renders.values())
        for process in processes:
            if process.returncode is None:
                process.kill()
        if processes:
            await asyncio.gather(*(process.wait() for process in processes),
                                 return_exceptions=True)
        self._renders.clear()
