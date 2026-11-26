"""Bounded descriptor-relative file tools; symbolic links are never followed."""

import contextlib
import errno
import hashlib
import itertools
import os
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from kira.domain import compat
from kira.domain.contracts import KiraError
from kira.domain.project_paths import (
    OUTSIDE_PROJECT,
    git_metadata_name,
    host_parts,
    project_relative_parts,
    windows_name_problem,
)

WINDOWS = sys.platform == "win32"
MAX_ENTRIES = 1000
MAX_REPORTED_SKIPS = 20
# Files and directories created by the tools get the conventional user modes
# (umask 022) whatever umask the service runs with; replaced files keep theirs.
NEW_FILE_MODE = 0o644
NEW_DIRECTORY_MODE = 0o755
SYMLINK_REFUSED = "Symbolic links are not followed; use the real file or directory path"
GIT_REFUSED = "Git metadata (.git) can only be changed with Full access"
# Reparse tag of a Windows junction or mounted folder: a directory that leads elsewhere.
MOUNT_POINT_TAG = 0xA0000003
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


def link_like(info: os.stat_result) -> bool:
    """A symbolic link or, on Windows, a junction: an entry that leads to another place."""
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) == MOUNT_POINT_TAG


def entry_kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    return "file" if stat.S_ISREG(mode) else "special"


def _real_path(path: Path) -> Path:
    """The real location of ``path`` with links and Windows name aliases resolved."""
    real = os.path.realpath(path)
    for prefix, plain in (("\\\\?\\UNC\\", "\\\\"), ("\\\\?\\", "")):
        if real.startswith(prefix):
            real = plain + real[len(prefix):]
            break
    return Path(real)


class FileTools:
    def __init__(self, root: Path, max_bytes: int = 1_048_576, *,
                 allow_external: bool = False) -> None:
        self.root = root.absolute()
        if self.root.resolve(strict=True) != self.root:
            raise KiraError("PATH_DENIED", "Project root changed to a symlink")
        self.max_bytes = max_bytes
        self.allow_external = allow_external
        self._real_root: Path | None = None

    def _parts(self, value: Any) -> tuple[str, ...]:
        if not isinstance(value, str) or "\0" in value:
            raise KiraError("INVALID_ARGUMENT", "path must be a string without NUL")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            # Lone surrogates stand for undecodable name bytes, which are not addressable.
            raise KiraError("INVALID_ARGUMENT", "path must be valid Unicode text") from None
        if self.allow_external:
            # Normalize lexically, never with resolve(): an explicit host path
            # still must pass every descriptor-relative O_NOFOLLOW check below.
            # Relative/default paths continue to start in the current project.
            if value == "~" or value.startswith(("~/", "~\\")):
                try:
                    value = str(Path(value).expanduser())
                except RuntimeError as exc:
                    raise KiraError("INVALID_ARGUMENT", "Home directory is unavailable") from exc
            return host_parts(value, self.root)
        return project_relative_parts(value, self.root)

    def _anchored(self, parts: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
        """The anchor a path starts from and the entry names below it.

        Only a full-access path on Windows can carry its own anchor (another
        drive, a share, a device prefix) as first part; names never contain
        ``\\``, ``/`` or ``:``, so a part with one of them is that anchor.
        """
        if self.allow_external and WINDOWS and parts and any(mark in parts[0] for mark in "\\/:"):
            return parts[0], parts[1:]
        return self.root.anchor or "/", parts

    def _absolute_path(self, parts: tuple[str, ...]) -> Path:
        if self.allow_external:
            anchor, names = self._anchored(parts)
            return Path(anchor).joinpath(*names)
        return self.root.joinpath(*parts)

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
                   created: list[tuple[str, ...]] | None = None,
                   refuse_git: bool = False) -> Iterator[int]:
        """Yield a descriptor of a directory reached without following symlinks.

        Components are opened with O_PATH, which needs only search permission,
        so a traverse-only ancestor (for example mode 0711) does not block the
        tools. The final directory is opened for reading only when ``readable``
        is true, as listing requires; an O_PATH descriptor serves as ``dir_fd``.
        With ``created``, missing directories below the project root (any
        missing directory with full access) are created and recorded there.
        With ``refuse_git``, a directory that is Git metadata under any
        spelling is refused. Outside full access a Windows junction is
        refused like a symbolic link, and the directory reached must really
        lie inside the project.
        """
        anchor, names = self._anchored(parts)
        for name in names:
            # Every name is one entry: ".." or a separator would walk elsewhere.
            if name in {"", ".", ".."} or "/" in name or (WINDOWS and ("\\" in name or ":" in name)):
                raise KiraError("PATH_DENIED", "A path component is not the name of one entry")
        confined = not self.allow_external
        prefix = self.root.parts[1:] if confined else ()
        components = (*prefix, *names)
        descriptor = os.open(anchor, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate(components):
                final = index == len(components) - 1
                flags = (os.O_RDONLY if readable and final else os.O_PATH) | os.O_DIRECTORY
                if confined and index >= len(prefix):
                    self._refuse_junction(descriptor, part)
                    if refuse_git and self._names_git_metadata(descriptor, part):
                        raise KiraError("PATH_DENIED", GIT_REFUSED)
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
            if confined:
                self._confirm_inside(descriptor)
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _refuse_junction(parent: int, name: str) -> None:
        """Refuse a Windows junction as a path component, like a symbolic link.

        O_NOFOLLOW already refuses symbolic links everywhere; a junction is a
        directory to Windows and would be entered.
        """
        if not WINDOWS:
            return
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if link_like(info):
            raise KiraError("PATH_DENIED", SYMLINK_REFUSED)

    def _confirm_inside(self, descriptor: int) -> None:
        """Verify where a directory really is, on systems that reach it by path.

        Windows has no descriptor-relative open here: a directory is reached
        by joining names. Whatever the names were, the real location of the
        directory must lie inside the real project folder, so a name that
        Windows resolved to another place is refused even when no lexical rule
        knew that spelling.
        """
        reached = compat.directory_path(descriptor)
        if reached is None:
            return
        if self._real_root is None:
            self._real_root = _real_path(self.root)
        real = _real_path(reached)
        if real != self._real_root and self._real_root not in real.parents:
            raise KiraError("PATH_DENIED", OUTSIDE_PROJECT)

    @staticmethod
    def _names_git_metadata(parent: int, name: str) -> bool:
        """Whether ``name`` in this directory is Git metadata, under any spelling."""
        if git_metadata_name(name):
            return True
        try:
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError:
            return False  # Not there yet: a new entry has only its spelling.
        folder = compat.directory_path(parent)
        if folder is not None:
            # Windows: the real name of the entry, whatever alias reached it.
            return git_metadata_name(_real_path(folder / name).name)
        try:
            metadata = os.stat(".git", dir_fd=parent, follow_symlinks=False)
        except OSError:
            return False
        return (entry.st_dev, entry.st_ino) == (metadata.st_dev, metadata.st_ino)

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
        if link_like(info):
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
        if not self._anchored(parts)[1]:
            raise KiraError("INVALID_ARGUMENT", "A file path is required")
        with self._directory(parts[:-1], readable=False) as parent:
            data, _ = self._read(parent, parts[-1])
        try:
            content, lossy = data.decode("utf-8"), False
        except UnicodeDecodeError:
            content, lossy = data.decode("utf-8", "replace"), True
        replaced = content.count("\ufffd") if lossy else 0
        if b"\x00" in data[:8192] or (replaced >= 32 and replaced * 10 > len(content)):
            # Compiled artifacts, archives and images are not text: decoding them yields
            # megabytes of replacement characters that cost a model far more than text and
            # carry no meaning. Report what the file is instead of its bytes.
            return {"ok": True, "path": self._reported_path(parts), "content": "",
                    "sha256": digest(data), "bytes": len(data), "encoding": "binary", "lossy": True,
                    "binary": True,
                    "note": "Binary file: its content is not text and was not returned. "
                            "Inspect it with a suitable command instead of reading it."}
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
        if not self._anchored(parts)[1] or not isinstance(content, str):
            raise KiraError("INVALID_ARGUMENT", "write_file requires a path and string content")
        guard_git = not self.allow_external
        if guard_git and any(git_metadata_name(part) for part in parts):
            # Git metadata can define hooks and filter commands that later run
            # as the user; project-scoped writing must not reach it, whatever
            # spelling a file system resolves to ".git".
            raise KiraError("PATH_DENIED", GIT_REFUSED)
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
                    parts[:-1], readable=False, created=created if expected is None else None,
                    refuse_git=guard_git))
            except FileNotFoundError:
                if expected is None:
                    raise
                raise KiraError("CONFLICT", "Expected file no longer exists") from None
            if guard_git and self._names_git_metadata(parent, parts[-1]):
                raise KiraError("PATH_DENIED", GIT_REFUSED)
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
                shown, undecodable = display_name(name)
                if WINDOWS and windows_name_problem(name):
                    # Written by another system under a name Windows cannot
                    # address as a plain entry: listed, never opened.
                    item: dict[str, Any] = {"name": shown, "kind": "special", "bytes": 0}
                else:
                    try:
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # Removed after the directory was read.
                    item = {"name": shown, "bytes": info.st_size,
                            "kind": "symlink" if link_like(info) else entry_kind(info.st_mode)}
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
            if WINDOWS and windows_name_problem(name):
                return True  # Not a plain entry for Windows: like a special file.
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                skip(path, "vanished")
                return True
            if link_like(info):
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
        if not self._anchored(parts)[1]:
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
