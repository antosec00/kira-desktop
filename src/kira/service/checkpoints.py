"""Shadow Git workspace snapshots and instant rollback without touching user .git."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import TYPE_CHECKING, Any
import uuid

from kira.domain.contracts import WRITE_PERMISSIONS, Json, KiraError
from kira.service.desktop import private_directory
from kira.service.storage import encode, new_id, now
from kira.service.workbench import EXCLUDED_DIRS, MAX_DIFF, _private
from kira.tools.filesystem import NEW_FILE_MODE, SYMLINK_REFUSED, FileTools

if TYPE_CHECKING:
    from kira.service.server import Service

METHODS = frozenset({
    "checkpoint.list",
    "checkpoint.create",
    "checkpoint.diff",
    "checkpoint.restore",
})

MAX_CHECKPOINTS = 50
MAX_CHECKPOINT_FILES = 5_000
MAX_CHECKPOINT_FILE_BYTES = 8 * 1024 * 1024
MAX_CHECKPOINT_TOTAL_BYTES = 64 * 1024 * 1024


def _text(params: Json, key: str, maximum: int = 128, *, default: str | None = None,
          empty: bool = False) -> str:
    value = params.get(key, default)
    if not isinstance(value, str) or "\0" in value or (not empty and not value.strip()):
        raise KiraError("INVALID_ARGUMENT", f"{key} must contain text")
    if len(value.encode("utf-8", "replace")) > maximum:
        raise KiraError("INVALID_ARGUMENT", f"{key} exceeds {maximum} bytes")
    return value


def _fields(params: Json, allowed: set[str]) -> None:
    if not isinstance(params, dict) or set(params) - allowed:
        raise KiraError("INVALID_ARGUMENT", "Unknown checkpoint parameter")


def _git_env(git_dir: Path, index_file: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in {"PAGER", "GIT_PAGER"}
    }
    env.update(
        GIT_DIR=str(git_dir),
        GIT_INDEX_FILE=str(index_file),
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="Kira Shadow Checkpoint",
        GIT_AUTHOR_EMAIL="checkpoints@kira.local",
        GIT_COMMITTER_NAME="Kira Shadow Checkpoint",
        GIT_COMMITTER_EMAIL="checkpoints@kira.local",
        LC_ALL="C",
    )
    return env


def _run_shadow_git(
    git_dir: Path,
    index_file: Path,
    work_tree: Path | None,
    args: list[str],
    *,
    input_data: bytes = b"",
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[bytes]:
    binary = shutil.which("git")
    if not binary:
        raise KiraError("UNAVAILABLE", "git is required for workspace checkpoints")
    argv = [
        binary,
        "--no-pager",
        "-c", "core.bare=false",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false",
        "-c", "core.quotePath=false",
        "-c", "core.autocrlf=false",
        "-c", "core.safecrlf=false",
        "-c", "gc.auto=0",
    ]
    if work_tree is not None:
        argv.extend([f"--work-tree={work_tree}", "--literal-pathspecs"])
    argv.extend(args)
    try:
        return subprocess.run(
            argv,
            cwd=work_tree or git_dir,
            env=_git_env(git_dir, index_file),
            input=input_data,
            capture_output=True,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise KiraError("TIMEOUT", "Shadow checkpoint git operation timed out") from exc
    except OSError as exc:
        raise KiraError("FILE_ERROR", f"Shadow checkpoint git failed: {exc}") from exc


def _walk_safe_project_files(root: Path) -> list[str]:
    """Enumerate regular, non-symlink, non-secret project files using descriptor-relative O_NOFOLLOW."""
    tools = FileTools(root, MAX_CHECKPOINT_FILE_BYTES)
    collected: list[str] = []
    total_bytes = 0

    def visit(relative: tuple[str, ...], depth: int) -> None:
        nonlocal total_bytes
        if depth > 32 or len(collected) >= MAX_CHECKPOINT_FILES:
            return
        try:
            with tools._directory(relative) as parent:
                with os.scandir(parent) as entries:
                    names = sorted(
                        entry.name for entry in itertools.islice(entries, MAX_CHECKPOINT_FILES + 1)
                    )
                for name in names:
                    if len(collected) >= MAX_CHECKPOINT_FILES or total_bytes >= MAX_CHECKPOINT_TOTAL_BYTES:
                        return
                    try:
                        name.encode("utf-8")
                    except UnicodeEncodeError:
                        continue
                    rel_parts = (*relative, name)
                    rel_posix = "/".join(rel_parts)
                    if _private(rel_posix):
                        continue
                    try:
                        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISLNK(info.st_mode):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if name.lower() not in EXCLUDED_DIRS:
                            visit(rel_parts, depth + 1)
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    if info.st_size > MAX_CHECKPOINT_FILE_BYTES:
                        continue
                    if total_bytes + info.st_size > MAX_CHECKPOINT_TOTAL_BYTES:
                        continue
                    total_bytes += info.st_size
                    collected.append(rel_posix)
        except (OSError, KiraError):
            if depth == 0:
                raise

    visit((), 0)
    return collected


def _write_restored_file(tools: FileTools, rel_path: str, data: bytes, executable: bool) -> None:
    parts = tools._parts(rel_path)
    if not parts or _private(rel_path):
        raise KiraError("PATH_DENIED", "Refusing to restore a protected or invalid path")
    created: list[tuple[str, ...]] = []
    with tools._directory(parts[:-1], readable=False, created=created) as parent:
        name = parts[-1]
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise KiraError("PATH_DENIED", SYMLINK_REFUSED)
            if not stat.S_ISREG(info.st_mode):
                raise KiraError("PATH_DENIED", "Cannot overwrite non-regular file during restore")
        except FileNotFoundError:
            pass
        temporary = f".kira-restore-{uuid.uuid4().hex}.tmp"
        mode = 0o755 if executable else NEW_FILE_MODE
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                with contextlib.suppress(OSError):
                    os.fchmod(stream.fileno(), mode)
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            tools._sync_directory(parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)


def _remove_restored_file(tools: FileTools, rel_path: str) -> bool:
    parts = tools._parts(rel_path)
    if not parts or _private(rel_path):
        return False
    try:
        with tools._directory(parts[:-1], readable=False) as parent:
            name = parts[-1]
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                return False
            os.unlink(name, dir_fd=parent)
            tools._sync_directory(parent)
            return True
    except (FileNotFoundError, KiraError, OSError):
        return False


class Checkpoints:
    """Manage per-project shadow Git snapshots, diffs and instant workspace rollback."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = service.store
        self.paths = service.paths
        self.redactor = service.redactor
        self._locks: dict[str, asyncio.Lock] = {}
        self._active_runs: dict[str, dict[str, Any]] = {}

    def _lock(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())

    def _project_dir(self, project_id: str) -> tuple[Path, Path, Path, Path]:
        base = private_directory(self.paths.data / "checkpoints")
        proj_dir = private_directory(base / project_id)
        git_dir = proj_dir / "repo.git"
        index_file = proj_dir / "index"
        meta_file = proj_dir / "checkpoints.json"
        return proj_dir, git_dir, index_file, meta_file

    def _ensure_repo_sync(self, project_id: str) -> tuple[Path, Path, Path]:
        _, git_dir, index_file, meta_file = self._project_dir(project_id)
        if git_dir.is_symlink() or index_file.is_symlink() or meta_file.is_symlink():
            raise KiraError("UNSAFE_PATH", "Checkpoint storage must not contain symlinks")
        if not (git_dir / "HEAD").exists():
            git_dir.mkdir(mode=0o700, exist_ok=True)
            binary = shutil.which("git")
            if not binary:
                raise KiraError("UNAVAILABLE", "git is required for workspace checkpoints")
            res = subprocess.run(
                [binary, f"--git-dir={git_dir}", "init", "--bare", "--quiet"],
                cwd=git_dir,
                env=_git_env(git_dir, index_file),
                capture_output=True,
                timeout=10,
                check=False,
            )
            if res.returncode != 0:
                raise KiraError("FILE_ERROR", "Could not initialize shadow checkpoint repository")
        return git_dir, index_file, meta_file

    def _load_records_sync(self, meta_file: Path) -> list[dict[str, Any]]:
        if not meta_file.exists():
            return []
        try:
            raw = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
        except Exception:
            pass
        return []

    def _save_records_sync(self, meta_file: Path, records: list[dict[str, Any]]) -> None:
        bounded = records[:MAX_CHECKPOINTS]
        temp = meta_file.with_name(f".checkpoints-{uuid.uuid4().hex}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encode(bounded))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, meta_file)
        finally:
            temp.unlink(missing_ok=True)

    def _capture_tree_sync(self, project_id: str, root: Path) -> str:
        git_dir, index_file, _ = self._ensure_repo_sync(project_id)
        safe_files = _walk_safe_project_files(root)
        ls_res = _run_shadow_git(git_dir, index_file, root, ["ls-files", "--cached", "-z"])
        indexed_files = [
            entry.decode("utf-8", "surrogateescape")
            for entry in ls_res.stdout.split(b"\0")
            if entry
        ] if ls_res.returncode == 0 else []
        all_candidates = sorted(set(safe_files) | set(indexed_files))
        # If an indexed file is now excluded/private or was deleted, remove it from the shadow index
        safe_set = set(safe_files)
        to_remove = [path for path in indexed_files if path not in safe_set]
        if to_remove:
            _run_shadow_git(
                git_dir,
                index_file,
                root,
                ["update-index", "--force-remove", "-z", "--stdin"],
                input_data=b"\0".join(p.encode("utf-8") for p in to_remove) + b"\0",
            )
        if safe_files:
            up_res = _run_shadow_git(
                git_dir,
                index_file,
                root,
                ["update-index", "--add", "--remove", "-z", "--stdin"],
                input_data=b"\0".join(p.encode("utf-8") for p in safe_files) + b"\0",
            )
            if up_res.returncode != 0 and not all_candidates:
                raise KiraError("FILE_ERROR", "Could not stage files into shadow checkpoint index")
        tree_res = _run_shadow_git(git_dir, index_file, root, ["write-tree"])
        if tree_res.returncode != 0:
            raise KiraError("FILE_ERROR", "Could not write shadow checkpoint tree")
        return tree_res.stdout.decode("ascii", "replace").strip()

    def _changed_files_sync(self, git_dir: Path, index_file: Path, tree_a: str, tree_b: str) -> list[dict[str, str]]:
        if tree_a == tree_b:
            return []
        res = _run_shadow_git(
            git_dir,
            index_file,
            None,
            ["diff-tree", "-r", "--no-commit-id", "--no-renames", "--name-status", "-z", tree_a, tree_b],
        )
        if res.returncode != 0 or not res.stdout:
            return []
        parts = res.stdout.rstrip(b"\0").split(b"\0")
        changes: list[dict[str, str]] = []
        idx = 0
        while idx + 1 < len(parts):
            status = parts[idx].decode("utf-8", "replace")
            path = parts[idx + 1].decode("utf-8", "replace")
            changes.append({"status": status[:1], "path": path})
            idx += 2
        return changes

    def _create_checkpoint_sync(
        self,
        project_id: str,
        root: Path,
        *,
        kind: str,
        label: str,
        session_id: str | None = None,
        run_id: str | None = None,
        before_tree: str | None = None,
        only_if_changed: bool = False,
    ) -> dict[str, Any] | None:
        git_dir, index_file, meta_file = self._ensure_repo_sync(project_id)
        current_tree = self._capture_tree_sync(project_id, root)
        records = self._load_records_sync(meta_file)
        parent_tree = before_tree if before_tree is not None else (records[0]["tree"] if records else None)
        if only_if_changed and parent_tree == current_tree:
            return None
        files = (
            self._changed_files_sync(git_dir, index_file, parent_tree, current_tree)
            if parent_tree
            else []
        )
        record: dict[str, Any] = {
            "id": new_id(),
            "project_id": project_id,
            "session_id": session_id,
            "run_id": run_id,
            "kind": kind,
            "label": self.redactor.text(label.strip() or "Workspace checkpoint")[:160],
            "tree": current_tree,
            "parent_tree": parent_tree,
            "files_changed": len(files),
            "paths": [item["path"] for item in files[:25]],
            "created_at": now(),
        }
        records.insert(0, record)
        self._save_records_sync(meta_file, records)
        return record

    def _diff_checkpoint_sync(self, project_id: str, checkpoint_id: str) -> dict[str, Any]:
        git_dir, index_file, meta_file = self._ensure_repo_sync(project_id)
        records = self._load_records_sync(meta_file)
        record = next((item for item in records if item.get("id") == checkpoint_id), None)
        if record is None:
            raise KiraError("NOT_FOUND", "Checkpoint not found")
        base_tree = record.get("parent_tree")
        target_tree = record["tree"]
        if not base_tree or base_tree == target_tree:
            # Compare checkpoint tree against current workspace tree if parent_tree is identical or absent
            project = self.store.one("projects", project_id)
            current_tree = self._capture_tree_sync(project_id, Path(project["root"]))
            if current_tree != target_tree:
                base_tree, target_tree = target_tree, current_tree
            else:
                return {
                    "checkpoint": record,
                    "files": [],
                    "diff": "",
                    "truncated": False,
                }
        files = self._changed_files_sync(git_dir, index_file, base_tree, target_tree)
        diff_res = _run_shadow_git(
            git_dir,
            index_file,
            None,
            ["diff-tree", "-p", "--no-commit-id", "-M", base_tree, target_tree],
        )
        raw_diff = diff_res.stdout[:MAX_DIFF + 1]
        truncated = len(raw_diff) > MAX_DIFF
        diff_text = self.redactor.text(raw_diff[:MAX_DIFF].decode("utf-8", "replace"))
        return {
            "checkpoint": record,
            "files": files,
            "diff": diff_text,
            "truncated": truncated,
        }

    def _list_tree_entries_sync(self, git_dir: Path, index_file: Path, tree_sha: str) -> dict[str, tuple[str, str]]:
        """Return mapping of {rel_path: (mode_str, blob_sha)} for a shadow Git tree."""
        res = _run_shadow_git(git_dir, index_file, None, ["ls-tree", "-r", "-z", tree_sha])
        if res.returncode != 0:
            raise KiraError("FILE_ERROR", "Could not read shadow checkpoint tree")
        entries: dict[str, tuple[str, str]] = {}
        for item in res.stdout.split(b"\0"):
            if not item or b"\t" not in item:
                continue
            header, raw_path = item.split(b"\t", 1)
            parts = header.decode("ascii", "replace").split()
            if len(parts) >= 3 and parts[1] == "blob":
                rel_path = raw_path.decode("utf-8", "surrogateescape")
                if not _private(rel_path):
                    entries[rel_path] = (parts[0], parts[2])
        return entries

    def _restore_checkpoint_sync(
        self,
        project_id: str,
        root: Path,
        checkpoint_id: str,
        target: str,
    ) -> dict[str, Any]:
        git_dir, index_file, meta_file = self._ensure_repo_sync(project_id)
        records = self._load_records_sync(meta_file)
        record = next((item for item in records if item.get("id") == checkpoint_id), None)
        if record is None:
            raise KiraError("NOT_FOUND", "Checkpoint not found")

        if target == "before":
            target_tree = record.get("parent_tree") or record["tree"]
        elif target == "after":
            target_tree = record["tree"]
        else:
            raise KiraError("INVALID_ARGUMENT", "target must be 'before' or 'after'")

        # Automatically snapshot the current workspace before modifying any file
        current_tree = self._capture_tree_sync(project_id, root)
        backup = None
        if current_tree != target_tree:
            backup = self._create_checkpoint_sync(
                project_id,
                root,
                kind="pre_restore_backup",
                label=f"Backup before restore ({record['label'][:60]})",
                session_id=record.get("session_id"),
                run_id=record.get("run_id"),
                before_tree=target_tree,
                only_if_changed=True,
            )

        current_entries = self._list_tree_entries_sync(git_dir, index_file, current_tree)
        target_entries = self._list_tree_entries_sync(git_dir, index_file, target_tree)

        tools = FileTools(root, MAX_CHECKPOINT_FILE_BYTES)
        restored_files: list[str] = []
        removed_files: list[str] = []

        # Remove tracked files that did not exist in the target tree
        for rel_path in sorted(set(current_entries) - set(target_entries)):
            if _remove_restored_file(tools, rel_path):
                removed_files.append(rel_path)

        # Write or update files from the target tree
        for rel_path, (mode_str, blob_sha) in sorted(target_entries.items()):
            cur = current_entries.get(rel_path)
            if cur == (mode_str, blob_sha):
                # Verify the file still exists on disk with matching size
                try:
                    parts = tools._parts(rel_path)
                    with tools._directory(parts[:-1], readable=False) as parent:
                        os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                    continue
                except (OSError, KiraError):
                    pass
            blob_res = _run_shadow_git(git_dir, index_file, None, ["cat-file", "blob", blob_sha])
            if blob_res.returncode != 0:
                raise KiraError("FILE_ERROR", f"Missing blob for {rel_path} in checkpoint")
            executable = mode_str.endswith("755")
            _write_restored_file(tools, rel_path, blob_res.stdout, executable)
            restored_files.append(rel_path)

        # Synchronize the shadow index with the restored workspace
        self._capture_tree_sync(project_id, root)
        return {
            "ok": True,
            "checkpoint": record,
            "target": target,
            "restored_tree": target_tree,
            "restored_files": restored_files,
            "removed_files": removed_files,
            "backup_checkpoint": backup,
        }

    async def before_run(self, run_id: str, project: Json, session: Json, prompt: str, mode: str) -> None:
        """Capture pre-run shadow tree for writable execute runs."""
        if mode != "execute" or project.get("permission") not in WRITE_PERMISSIONS:
            return
        if shutil.which("git") is None:
            return
        project_id = str(project["id"])
        root = Path(project["root"])
        try:
            async with self._lock(project_id):
                before_tree = await asyncio.to_thread(self._capture_tree_sync, project_id, root)
            self._active_runs[run_id] = {
                "project_id": project_id,
                "root": root,
                "session_id": str(session["id"]),
                "prompt": prompt,
                "before_tree": before_tree,
            }
        except Exception:
            # Never block a run if git or the filesystem cannot snapshot
            pass

    async def after_run(self, run_id: str) -> dict[str, Any] | None:
        """Capture post-run shadow tree and persist an automatic checkpoint if files changed."""
        info = self._active_runs.pop(run_id, None)
        if info is None:
            return None
        project_id = info["project_id"]
        root = info["root"]
        try:
            async with self._lock(project_id):
                return await asyncio.to_thread(
                    self._create_checkpoint_sync,
                    project_id,
                    root,
                    kind="auto_run",
                    label=info["prompt"] or "Run checkpoint",
                    session_id=info["session_id"],
                    run_id=run_id,
                    before_tree=info["before_tree"],
                    only_if_changed=True,
                )
        except Exception:
            return None

    async def dispatch(self, method: str, params: Json) -> Json:
        if method == "checkpoint.list":
            _fields(params, {"project_id", "session_id"})
            project = self.store.one("projects", _text(params, "project_id"))
            session_id = _text(params, "session_id") if "session_id" in params and params.get("session_id") else None
            _, _, _, meta_file = self._project_dir(project["id"])
            records = await asyncio.to_thread(self._load_records_sync, meta_file)
            if session_id:
                records = [
                    item for item in records
                    if item.get("session_id") in {None, session_id}
                ]
            return {"checkpoints": records}

        if method == "checkpoint.create":
            _fields(params, {"project_id", "session_id", "label"})
            project = self.store.one("projects", _text(params, "project_id"))
            self.service.workbench._idle(project)
            session_id = _text(params, "session_id") if "session_id" in params and params.get("session_id") else None
            label = _text(params, "label", 200, default="Manual checkpoint")
            async with self._lock(project["id"]):
                record = await asyncio.to_thread(
                    self._create_checkpoint_sync,
                    project["id"],
                    Path(project["root"]),
                    kind="manual",
                    label=label,
                    session_id=session_id,
                    only_if_changed=False,
                )
            return {"checkpoint": record}

        if method == "checkpoint.diff":
            _fields(params, {"project_id", "checkpoint_id"})
            project = self.store.one("projects", _text(params, "project_id"))
            checkpoint_id = _text(params, "checkpoint_id")
            async with self._lock(project["id"]):
                return await asyncio.to_thread(
                    self._diff_checkpoint_sync,
                    project["id"],
                    checkpoint_id,
                )

        if method == "checkpoint.restore":
            _fields(params, {"project_id", "checkpoint_id", "target", "session_id"})
            project = self.store.one("projects", _text(params, "project_id"))
            self.service.workbench._idle(project)
            if project["permission"] not in WRITE_PERMISSIONS:
                raise KiraError("PERMISSION_DENIED", "Project write permission is required to restore files")
            checkpoint_id = _text(params, "checkpoint_id")
            target = _text(params, "target", 16, default="before")
            async with self._lock(project["id"]):
                outcome = await asyncio.to_thread(
                    self._restore_checkpoint_sync,
                    project["id"],
                    Path(project["root"]),
                    checkpoint_id,
                    target,
                )
            # Record a handoff note in the active session so the model is aware of the workspace rollback
            session_id = (
                _text(params, "session_id")
                if "session_id" in params and params.get("session_id")
                else outcome["checkpoint"].get("session_id")
            )
            if session_id:
                with contextlib.suppress(KiraError):
                    session = self.store.one("sessions", session_id)
                    if session["project_id"] == project["id"]:
                        settings = dict(session["settings"])
                        note = (
                            f"[Workspace restored to checkpoint '{outcome['checkpoint']['label']}' "
                            f"(target={target}, restored={len(outcome['restored_files'])}, "
                            f"removed={len(outcome['removed_files'])}) at {now()}]"
                        )
                        existing = str(settings.get("handoff", "")).strip()
                        settings["handoff"] = (existing + "\n" + note).strip()[-100_000:]
                        self.store.db.execute(
                            "UPDATE sessions SET settings=? WHERE id=?",
                            (encode(settings), session_id),
                        )
            return outcome

        raise KiraError("METHOD_NOT_FOUND", f"Unknown checkpoint method: {method}")
