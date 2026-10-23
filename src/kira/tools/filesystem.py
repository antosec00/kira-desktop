"""Bounded descriptor-relative file tools; symbolic links are never followed."""

import contextlib
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
MAX_REPORTED_SKIPS = 20
# Files and directories created by the tools get the conventional user modes
# (umask 022) whatever umask the service runs with; replaced files keep theirs.
NEW_FILE_MODE = 0o644
NEW_DIRECTORY_MODE = 0o755
SYMLINK_REFUSED = "Symbolic links are not followed; use the real file or directory path"
# link() errors meaning that the filesystem has no hard links (FAT, exFAT, some FUSE).
NO_HARD_LINKS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS})
# fsync() errors meaning that the filesystem cannot sync a directory.
NO_DIRECTORY_SYNC = frozenset({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def display_name(name: str) -> tuple[str, bool]:
    """Return a JSON-safe directory entry name and whether its bytes are not UTF-8.

    Linux names are bytes. Python keeps undecodable bytes as lone surrogates,
    which UTF-8 JSON cannot carry: such names are shown with backslash escapes
    and cannot be addressed through these tools.
    """
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return os.fsencode(name).decode("utf-8", "backslashreplace"), True
    return name, False


def entry_kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    return "file" if stat.S_ISREG(mode) else "special"


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
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            # Lone surrogates stand for undecodable name bytes, which are not addressable.
            raise KiraError("INVALID_ARGUMENT", "path must be valid Unicode text") from None
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

    def _shown_path(self, parts: tuple[str, ...]) -> tuple[str, bool]:
        names = [display_name(part) for part in parts]
        return (self._reported_path(tuple(name for name, _ in names)),
                any(undecodable for _, undecodable in names))

    def _open_at(self, parent: int, name: str, flags: int) -> int:
        try:
            return os.open(name, flags | os.O_NOFOLLOW, dir_fd=parent)
        except OSError as exc:
            # O_NOFOLLOW reports a final symbolic link as ELOOP, or as ENOTDIR
            # when a directory is required: every scope refuses it explicitly.
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                try:
                    symlink = stat.S_ISLNK(os.stat(name, dir_fd=parent,
                                                 follow_symlinks=False).st_mode)
                except OSError:
                    symlink = False
                if symlink or exc.errno == errno.ELOOP:
                    raise KiraError("PATH_DENIED", SYMLINK_REFUSED) from exc
            raise

    @contextmanager
    def _directory(self, parts: tuple[str, ...], *, readable: bool = True,
                   created: list[tuple[str, ...]] | None = None) -> Iterator[int]:
        """Yield a descriptor of a directory reached without following symlinks.

        Components are opened with O_PATH, which needs only search permission,
        so a traverse-only ancestor (for example mode 0711) does not block the
        tools. The final directory is opened for reading only when ``readable``
        is true, as listing requires; an O_PATH descriptor serves as ``dir_fd``.
        With ``created``, missing directories below the project root (any
        missing directory with full access) are created and recorded there.
        """
        prefix = () if self.allow_external else self.root.parts[1:]
        components = (*prefix, *parts)
        descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate(components):
                final = index == len(components) - 1
                flags = (os.O_RDONLY if readable and final else os.O_PATH) | os.O_DIRECTORY
                try:
                    child = self._open_at(descriptor, part, flags)
                except FileNotFoundError:
                    if created is None or index < len(prefix):
                        raise
                    if self._make_directory(descriptor, part):
                        created.append(components[len(prefix):index + 1])
                    child = self._open_at(descriptor, part, flags)
                os.close(descriptor)
                descriptor = child
            if readable and not components:
                child = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    def _make_directory(self, parent: int, name: str) -> bool:
        """Create one missing directory; False when another writer created it first."""
        try:
            os.mkdir(name, NEW_DIRECTORY_MODE, dir_fd=parent)
        except FileExistsError:
            return False
        try:
            descriptor = self._open_at(parent, name, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return True
        try:
            # mkdir applies the service umask; set the conventional mode explicitly.
            with contextlib.suppress(OSError):
                os.fchmod(descriptor, NEW_DIRECTORY_MODE)
        finally:
            os.close(descriptor)
        return True

    def _open_regular(self, parent: int, name: str, use: str) -> tuple[int, os.stat_result]:
        """Open a regular file read-only; links, devices, FIFOs and sockets are never opened."""
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise KiraError("PATH_DENIED", SYMLINK_REFUSED)
        if not stat.S_ISREG(info.st_mode):
            raise KiraError("PATH_DENIED", f"Only regular files can be {use}")
        descriptor = self._open_at(parent, name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise KiraError("PATH_DENIED", f"Only regular files can be {use}")
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise KiraError("CONFLICT", "File was replaced while it was being opened")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor, opened

    def _read(self, parent: int, name: str) -> tuple[bytes, os.stat_result]:
        descriptor, info = self._open_regular(parent, name, "read")
        try:
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
        with self._directory(parts[:-1], readable=False) as parent:
            data, _ = self._read(parent, parts[-1])
        try:
            content, lossy = data.decode("utf-8"), False
        except UnicodeDecodeError:
            content, lossy = data.decode("utf-8", "replace"), True
        result = {"ok": True, "path": self._reported_path(parts), "content": content,
                  "sha256": digest(data), "bytes": len(data)}
        if lossy:
            # Invalid bytes are shown as U+FFFD, so this content is not the file
            # and write_file refuses to replace it.
            result.update(encoding="non_utf8", lossy=True)
        return result

    def write_file(self, args: dict[str, Any]) -> dict[str, Any]:
        parts = self._parts(args.get("path"))
        content = args.get("content")
        expected = args.get("expected_sha256")
        if not parts or not isinstance(content, str):
            raise KiraError("INVALID_ARGUMENT", "write_file requires a path and string content")
        if not self.allow_external and any(part.casefold() == ".git" for part in parts):
            # Git metadata can define hooks and filter commands that later run
            # as the user; project-scoped writing must not reach it.
            raise KiraError("PATH_DENIED", "Git metadata (.git) can only be changed with Full access")
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64):
            raise KiraError("INVALID_ARGUMENT", "expected_sha256 must be a SHA-256 string or null")
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:
            raise KiraError("INVALID_ARGUMENT", "content must be valid Unicode text") from None
        if len(data) > self.max_bytes:
            raise KiraError("OUTPUT_LIMIT", "Content exceeds the configured file size limit")
        created: list[tuple[str, ...]] = []
        with contextlib.ExitStack() as stack:
            try:
                # A create-only write also creates missing parent directories. A
                # compare-and-swap write needs the file it read: nothing is created.
                parent = stack.enter_context(self._directory(
                    parts[:-1], readable=False, created=created if expected is None else None))
            except FileNotFoundError:
                if expected is None:
                    raise
                raise KiraError("CONFLICT", "Expected file no longer exists") from None
            old: os.stat_result | None = None
            try:
                current, old = self._read(parent, parts[-1])
            except FileNotFoundError:
                if expected is not None:
                    raise KiraError("CONFLICT", "Expected file no longer exists") from None
            else:
                if expected is None:
                    raise KiraError("CONFLICT", "File hash differs; read the current file before writing")
                try:
                    current.decode("utf-8")
                except UnicodeDecodeError:
                    raise KiraError("UNSUPPORTED", "The current file is not valid UTF-8, so "
                                    "write_file would replace bytes that read_file cannot show; "
                                    "change it with a command or outside Kira") from None
                if digest(current) != expected:
                    raise KiraError("CONFLICT", "File hash differs; read the current file before writing")
            temporary = f".kira-{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    if old is not None:
                        os.fchmod(stream.fileno(), stat.S_IMODE(old.st_mode) & 0o777)
                    else:
                        with contextlib.suppress(OSError):
                            os.fchmod(stream.fileno(), NEW_FILE_MODE)
                if old is None:
                    self._publish_new(parent, temporary, parts[-1], data)
                else:
                    latest, latest_info = self._read(parent, parts[-1])
                    if ((latest_info.st_dev, latest_info.st_ino, latest_info.st_mtime_ns,
                         latest_info.st_size) !=
                        (old.st_dev, old.st_ino, old.st_mtime_ns, old.st_size)
                        or digest(latest) != expected):
                        raise KiraError("CONFLICT", "File changed while preparing the write")
                    os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                self._sync_directory(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        path = self._absolute_path(parts)
        result = {"ok": True, "path": str(path), "sha256": digest(data),
                  "bytes": len(data),
                  "kind": "project_file" if path.is_relative_to(self.root) else "host_file"}
        if created:
            result["created_directories"] = [str(self._absolute_path(item)) for item in created]
        return result

    def _publish_new(self, parent: int, temporary: str, name: str, data: bytes) -> None:
        try:
            # link is atomic create-only, even when another writer races us.
            os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
        except FileExistsError as exc:
            raise KiraError("CONFLICT", "Another writer created the target") from exc
        except OSError as exc:
            if exc.errno not in NO_HARD_LINKS:
                raise
            self._create_directly(parent, name, data)

    @staticmethod
    def _create_directly(parent: int, name: str, data: bytes) -> None:
        """Create-only write for filesystems without hard links (FAT, exFAT).

        O_EXCL still never replaces a file that another writer created, but the
        content is written in place instead of being published atomically.
        """
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                 | os.O_NOCTTY, NEW_FILE_MODE, dir_fd=parent)
        except FileExistsError as exc:
            raise KiraError("CONFLICT", "Another writer created the target") from exc
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(descriptor)
            with contextlib.suppress(OSError):
                os.fchmod(descriptor, NEW_FILE_MODE)
        except BaseException:
            # Remove the partial file, but only while the name still refers to it.
            with contextlib.suppress(OSError):
                ours = os.fstat(descriptor)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (ours.st_dev, ours.st_ino) == (current.st_dev, current.st_ino):
                    os.unlink(name, dir_fd=parent)
            raise
        finally:
            os.close(descriptor)

    @staticmethod
    def _sync_directory(parent: int) -> None:
        """Persist the new directory entry; ``parent`` may be an O_PATH descriptor."""
        try:
            descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent)
        except PermissionError:
            # A traverse-only directory cannot be opened to sync it; the file
            # content itself was already synced.
            return
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in NO_DIRECTORY_SYNC:
                raise
        finally:
            os.close(descriptor)

    def list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        parts = self._parts(args.get("path", "."))
        with self._directory(parts) as directory:
            items = []
            with os.scandir(directory) as entries:
                names = sorted(entry.name for entry in itertools.islice(entries, MAX_ENTRIES + 1))
            for name in names[:MAX_ENTRIES]:
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue  # Removed after the directory was read.
                shown, undecodable = display_name(name)
                item: dict[str, Any] = {"name": shown, "kind": entry_kind(info.st_mode),
                                        "bytes": info.st_size}
                if undecodable:
                    item["undecodable"] = True
                items.append(item)
        return {"ok": True, "entries": items, "truncated": len(names) > MAX_ENTRIES,
                **({"path": self._reported_path(parts)} if self.allow_external else {})}

    def search_files(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        if not isinstance(query, str) or not query or len(query) > 4096:
            raise KiraError("INVALID_ARGUMENT", "query must contain 1–4096 characters")
        parts = self._parts(args.get("path", "."))
        search_bytes = max(self.max_bytes, 16 * 1024 * 1024)
        matches: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        counts = {"examined": 0, "bytes": 0, "large": 0, "unreadable": 0}
        truncated = False

        def skip(relative: tuple[str, ...], reason: str) -> None:
            # Anything not searched makes the result incomplete, and says why.
            nonlocal truncated
            truncated = True
            counts["large" if reason == "too_large" else "unreadable"] += 1
            if len(skipped) < MAX_REPORTED_SKIPS:
                path, undecodable = self._shown_path(relative)
                skipped.append({"path": path, "reason": reason,
                                **({"undecodable": True} if undecodable else {})})

        def examine(parent: int, relative: tuple[str, ...], name: str, depth: int) -> bool:
            """Search one entry; False ends this directory because a limit was reached."""
            nonlocal truncated
            if counts["examined"] >= MAX_ENTRIES or len(matches) >= 100:
                truncated = True
                return False
            counts["examined"] += 1
            path = (*relative, name)
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                skip(path, "vanished")
                return True
            if stat.S_ISDIR(info.st_mode):
                if name not in {".git", ".venv", "node_modules"}:
                    visit(path, depth + 1)
                return True
            if not stat.S_ISREG(info.st_mode):
                return True
            if info.st_size > self.max_bytes:
                skip(path, "too_large")
                return True
            if counts["bytes"] + info.st_size > search_bytes:
                truncated = True
                return False
            counts["bytes"] += info.st_size
            try:
                data, _ = self._read(parent, name)
            except FileNotFoundError:
                skip(path, "vanished")
                return True
            except KiraError as exc:
                skip(path, "too_large" if exc.code == "OUTPUT_LIMIT" else "unreadable")
                return True
            except OSError:
                skip(path, "unreadable")
                return True
            if b"\0" in data:
                return True
            for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if query in line:
                    shown, undecodable = self._shown_path(path)
                    match: dict[str, Any] = {"path": shown, "line": number, "text": line[:2000]}
                    if undecodable:
                        match["undecodable"] = True
                    matches.append(match)
                    if len(matches) >= 100:
                        truncated = True
                        return False
            return True

        # fd recursion enforces the same no-symlink policy as read and write.
        def visit(relative: tuple[str, ...], depth: int) -> None:
            nonlocal truncated
            if depth > 32:
                truncated = True
                return
            try:
                with self._directory(relative) as parent:
                    with os.scandir(parent) as entries:
                        names = sorted(entry.name for entry in
                                       itertools.islice(entries, MAX_ENTRIES + 1))
                    for name in names:
                        if not examine(parent, relative, name, depth):
                            return
            except (OSError, KiraError) as exc:
                # The requested directory must be readable. Below it, a directory
                # that cannot be read or has vanished is reported and skipped.
                if depth == 0:
                    raise
                skip(relative, "vanished" if isinstance(exc, FileNotFoundError) else "unreadable")

        visit(parts, 0)
        return {"ok": True, "matches": matches, "truncated": truncated,
                "files_examined": counts["examined"], "bytes_examined": counts["bytes"],
                "skipped_large": counts["large"], "skipped_unreadable": counts["unreadable"],
                **({"skipped": skipped} if skipped else {}),
                "limits": {"entries": MAX_ENTRIES, "matches": 100, "depth": 32,
                           "search_bytes": search_bytes}}

    def hash_file(self, path: str) -> dict[str, Any]:
        """Hash a stable regular file without decoding it or following symlinks."""
        parts = self._parts(path)
        if not parts:
            raise KiraError("INVALID_ARGUMENT", "A file path is required")
        with self._directory(parts[:-1], readable=False) as parent:
            descriptor, before = self._open_regular(parent, parts[-1], "watched")
            try:
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
