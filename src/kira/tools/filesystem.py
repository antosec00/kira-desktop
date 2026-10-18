"""Bounded descriptor-relative file tools; symbolic links are never followed."""

import errno
import hashlib
import itertools
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from kira.domain.contracts import KiraError

MAX_ENTRIES = 1000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileTools:
    def __init__(self, root: Path, max_bytes: int = 1_048_576, *,
                 allow_external: bool = False) -> None:
        self.root = root.absolute()
        if self.root.resolve(strict=True) != self.root:
            raise KiraError("PATH_DENIED", "Project root changed to a symlink")
        self.max_bytes = max_bytes
        self.allow_external = allow_external

    def _parts(self, value: Any) -> tuple[str, ...]:
        if not isinstance(value, str) or "\0" in value:
            raise KiraError("INVALID_ARGUMENT", "path must be a string without NUL")
        candidate = Path(value)
        if self.allow_external:
            # Normalize lexically, never with resolve(): an explicit host path
            # still must pass every descriptor-relative O_NOFOLLOW check below.
            # Relative/default paths continue to start in the current project.
            if value == "~" or value.startswith("~/"):
                try:
                    candidate = candidate.expanduser()
                except RuntimeError as exc:
                    raise KiraError("INVALID_ARGUMENT", "Home directory is unavailable") from exc
            if not candidate.is_absolute():
                candidate = self.root / candidate
            return Path(os.path.normpath(candidate)).parts[1:]
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(self.root)
            except ValueError as exc:
                raise KiraError("PATH_DENIED", "Path is outside the project") from exc
        if ".." in candidate.parts:
            raise KiraError("PATH_DENIED", "Parent traversal is not allowed")
        return tuple(part for part in candidate.parts if part not in {"", "."})

    def _absolute_path(self, parts: tuple[str, ...]) -> Path:
        return (Path("/") if self.allow_external else self.root).joinpath(*parts)

    def _reported_path(self, parts: tuple[str, ...]) -> str:
        return str(self._absolute_path(parts)) if self.allow_external else "/".join(parts)

    def _open_at(self, parent: int, name: str, flags: int) -> int:
        try:
            return os.open(name, flags | os.O_NOFOLLOW, dir_fd=parent)
        except OSError as exc:
            # Preserve the historical errors in project-scoped mode. In full
            # access, explain the remaining no-symlink contract explicitly.
            if self.allow_external and exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                try:
                    symlink = stat.S_ISLNK(os.stat(name, dir_fd=parent,
                                                 follow_symlinks=False).st_mode)
                except OSError:
                    symlink = False
                if symlink or exc.errno == errno.ELOOP:
                    raise KiraError("PATH_DENIED", "Symbolic links are not followed; use the "
                                    "real file or directory path") from exc
            raise

    @contextmanager
    def _directory(self, parts: tuple[str, ...]) -> Iterator[int]:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            prefix = () if self.allow_external else self.root.parts[1:]
            for part in (*prefix, *parts):
                child = self._open_at(descriptor, part, os.O_RDONLY | os.O_DIRECTORY)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    def _read(self, parent: int, name: str) -> tuple[bytes, os.stat_result]:
        descriptor = self._open_at(parent, name, os.O_RDONLY | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise KiraError("PATH_DENIED", "Only regular files can be read")
            if info.st_size > self.max_bytes:
                raise KiraError("OUTPUT_LIMIT", f"File exceeds {self.max_bytes} bytes")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(self.max_bytes + 1)
            if len(data) > self.max_bytes:
                raise KiraError("OUTPUT_LIMIT", "File changed beyond the configured size limit")
            return data, info
        finally:
            os.close(descriptor)

    def read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        parts = self._parts(args.get("path"))
        if not parts:
            raise KiraError("INVALID_ARGUMENT", "A file path is required")
        with self._directory(parts[:-1]) as parent:
            data, _ = self._read(parent, parts[-1])
        return {"ok": True, "path": self._reported_path(parts),
                "content": data.decode("utf-8", "replace"),
                "sha256": digest(data), "bytes": len(data)}

    def write_file(self, args: dict[str, Any]) -> dict[str, Any]:
        parts = self._parts(args.get("path"))
        content = args.get("content")
        expected = args.get("expected_sha256")
        if not parts or not isinstance(content, str):
            raise KiraError("INVALID_ARGUMENT", "write_file requires a path and string content")
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64):
            raise KiraError("INVALID_ARGUMENT", "expected_sha256 must be a SHA-256 string or null")
        data = content.encode("utf-8")
        if len(data) > self.max_bytes:
            raise KiraError("OUTPUT_LIMIT", "Content exceeds the configured file size limit")
        with self._directory(parts[:-1]) as parent:
            old: os.stat_result | None = None
            try:
                current, old = self._read(parent, parts[-1])
            except FileNotFoundError:
                if expected is not None:
                    raise KiraError("CONFLICT", "Expected file no longer exists") from None
            else:
                if expected is None or digest(current) != expected:
                    raise KiraError("CONFLICT", "File hash differs; read the current file before writing")
            temporary = f".kira-{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    if old:
                        os.fchmod(stream.fileno(), stat.S_IMODE(old.st_mode) & 0o777)
                if old is None:
                    # link is atomic create-only, even when another writer races us.
                    try:
                        os.link(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent,
                                follow_symlinks=False)
                    except FileExistsError as exc:
                        raise KiraError("CONFLICT", "Another writer created the target") from exc
                else:
                    latest, latest_info = self._read(parent, parts[-1])
                    if ((latest_info.st_dev, latest_info.st_ino, latest_info.st_mtime_ns,
                         latest_info.st_size) !=
                        (old.st_dev, old.st_ino, old.st_mtime_ns, old.st_size)
                        or digest(latest) != expected):
                        raise KiraError("CONFLICT", "File changed while preparing the write")
                    os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        path = self._absolute_path(parts)
        return {"ok": True, "path": str(path), "sha256": digest(data),
                "bytes": len(data),
                "kind": "project_file" if path.is_relative_to(self.root) else "host_file"}

    def list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        parts = self._parts(args.get("path", "."))
        with self._directory(parts) as directory:
            items = []
            with os.scandir(directory) as entries:
                names = sorted(entry.name for entry in itertools.islice(entries, MAX_ENTRIES + 1))
            for name in names[:MAX_ENTRIES]:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                kind = ("symlink" if stat.S_ISLNK(info.st_mode) else
                        "directory" if stat.S_ISDIR(info.st_mode) else "file")
                items.append({"name": name, "kind": kind, "bytes": info.st_size})
        return {"ok": True, "entries": items, "truncated": len(names) > MAX_ENTRIES,
                **({"path": self._reported_path(parts)} if self.allow_external else {})}

    def search_files(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        if not isinstance(query, str) or not query or len(query) > 4096:
            raise KiraError("INVALID_ARGUMENT", "query must contain 1–4096 characters")
        parts = self._parts(args.get("path", "."))
        matches: list[dict[str, Any]] = []
        examined = 0
        bytes_examined = 0
        truncated = False
        # fd recursion enforces the same no-symlink policy as read and write.
        def visit(relative: tuple[str, ...], depth: int) -> None:
            nonlocal examined, bytes_examined, truncated
            if depth > 32:
                truncated = True
                return
            with self._directory(relative) as parent:
                with os.scandir(parent) as entries:
                    names = sorted(entry.name for entry in itertools.islice(entries, MAX_ENTRIES + 1))
                for name in names:
                    if examined >= MAX_ENTRIES or len(matches) >= 100:
                        truncated = True
                        return
                    examined += 1
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if name not in {".git", ".venv", "node_modules"}:
                            visit((*relative, name), depth + 1)
                    elif stat.S_ISREG(info.st_mode) and info.st_size <= self.max_bytes:
                        if bytes_examined + info.st_size > max(self.max_bytes, 16 * 1024 * 1024):
                            truncated = True
                            return
                        bytes_examined += info.st_size
                        try:
                            data, _ = self._read(parent, name)
                        except (OSError, KiraError):
                            continue
                        if b"\0" in data:
                            continue
                        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                            if query in line:
                                matches.append({"path": self._reported_path((*relative, name)),
                                                "line": number, "text": line[:2000]})
                                if len(matches) >= 100:
                                    truncated = True
                                    return
        visit(parts, 0)
        return {"ok": True, "matches": matches, "truncated": truncated, "files_examined": examined,
                "bytes_examined": bytes_examined,
                "limits": {"entries": MAX_ENTRIES, "matches": 100, "depth": 32,
                           "search_bytes": max(self.max_bytes, 16 * 1024 * 1024)}}

    def hash_file(self, path: str) -> dict[str, Any]:
        """Hash a stable regular file without decoding it or following symlinks."""
        parts = self._parts(path)
        if not parts:
            raise KiraError("INVALID_ARGUMENT", "A file path is required")
        with self._directory(parts[:-1]) as parent:
            descriptor = self._open_at(parent, parts[-1], os.O_RDONLY | os.O_NONBLOCK)
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise KiraError("PATH_DENIED", "Only regular files can be watched")
                if before.st_size > self.max_bytes:
                    raise KiraError("OUTPUT_LIMIT", f"File exceeds {self.max_bytes} bytes")
                result = hashlib.sha256()
                size = 0
                while block := os.read(descriptor, min(131072, self.max_bytes - size + 1)):
                    size += len(block)
                    if size > self.max_bytes:
                        raise KiraError("OUTPUT_LIMIT", f"File exceeds {self.max_bytes} bytes")
                    result.update(block)
                after = os.fstat(descriptor)
                current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                def identity(info: os.stat_result) -> tuple[int, ...]:
                    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(current):
                    raise KiraError("CONFLICT", "File changed while calculating its reference hash")
                return {"path": self._reported_path(parts), "sha256": result.hexdigest(),
                        "bytes": size}
            finally:
                os.close(descriptor)
