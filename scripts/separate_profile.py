#!/usr/bin/env python3
"""Copy a stopped private Kira profile and preserve its original path as an alias.

The caller must stop the exact GUI/service first. Both store locks remain held
through copy, compatibility-link cutover and the caller's launcher update. No
SQLite rows, stored paths or external project files are rewritten. The retained
archive is a rollback copy, not a second active profile.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path
from typing import Any


def table_fingerprints(database: Path) -> dict[str, Any]:
    # Caller holds the store lock and has stopped its service. Refuse outstanding
    # WAL rather than silently omit committed records using immutable mode.
    wal = database.with_name(database.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("Database still has WAL data; complete its clean shutdown first")
    result = {}
    with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            quoted = name.replace('"', '""')
            hashes = sorted(hashlib.sha256(json.dumps(row, ensure_ascii=False, default=str,
                separators=(",", ":")).encode()).hexdigest()
                for row in db.execute('SELECT * FROM "' + quoted + '"'))
            result[name] = {"rows": len(hashes), "sha256": hashlib.sha256("\n".join(hashes).encode()).hexdigest()}
    return result


def file_manifest(root: Path) -> dict[str, Any]:
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if info.st_uid != os.getuid():
            raise PermissionError("Profile entries must belong to the current user")
        name = str(path.relative_to(root))
        if stat.S_ISLNK(info.st_mode):
            result[name] = {"kind": "link", "target": os.readlink(path)}
        elif stat.S_ISDIR(info.st_mode):
            result[name] = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
        elif stat.S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            result[name] = {"kind": "file", "bytes": info.st_size,
                            "mode": stat.S_IMODE(info.st_mode), "sha256": digest}
        else:
            raise RuntimeError("Profile contains a socket or special file; stop its service first")
    return result


class ProfileSeparation:
    """Exclusive, reversible cutover before the destination service is started."""

    def __init__(self, source: Path, destination: Path, archive: Path):
        self.source, self.destination, self.archive = source.absolute(), destination.absolute(), archive.absolute()
        self._fds: list[int] = []
        self.switched = False
        self.copied = False
        self.report: dict[str, Any] = {}

    def _lock(self, path: Path, *, create: bool = False) -> None:
        flags = os.O_RDWR | os.O_NOFOLLOW
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise PermissionError("Unsafe store lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self._fds.append(fd)

    def __enter__(self) -> ProfileSeparation:
        try:
            for target in (self.destination, self.archive):
                if target.exists() or target.is_symlink():
                    raise FileExistsError(f"Destination already exists: {target}")
            if self.source.is_symlink() or not self.source.is_dir():
                raise ValueError("Source must be the original real profile directory")
            if self.source.stat().st_uid != os.getuid():
                raise PermissionError("Source must belong to the current user")
            paths = [p.resolve() for p in (self.source, self.destination, self.archive)]
            if any(a == b or a.is_relative_to(b) for i, a in enumerate(paths)
                   for j, b in enumerate(paths) if i != j):
                raise ValueError("Profile roots must be distinct and non-nested")
            for name in ("config", "data", "state", "runtime"):
                directory = self.source / name
                if directory.is_symlink() or not directory.is_dir():
                    raise ValueError("Private profile subdirectories must be real directories")
            for name in ("data/artifacts", "data/attachments"):
                directory = self.source / name
                if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                    raise ValueError("Private content directories must not be links")
            database = self.source / "data/kira.sqlite3"
            if database.is_symlink() or not database.is_file():
                raise ValueError("Database must be a real file")
            self._lock(database.with_suffix(".lock"))
            # Capture byte evidence after a clean stop and under the exclusive lock.
            original = file_manifest(self.source)
            tables = table_fingerprints(database)
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            self.destination.mkdir(mode=0o700)
            (self.destination / "data").mkdir(mode=0o700)
            self._lock(self.destination / "data/kira.lock", create=True)
            shutil.copytree(self.source, self.destination, symlinks=True, dirs_exist_ok=True)
            self.destination.chmod(0o700)
            for fd, path in zip(self._fds, (database.with_suffix(".lock"), self.destination / "data/kira.lock"), strict=True):
                assert os.fstat(fd).st_ino == path.stat().st_ino, "Lock inode was replaced"
            copied = file_manifest(self.destination)
            assert copied == original, "Copied profile differs from source"
            assert table_fingerprints(self.destination / "data/kira.sqlite3") == tables
            for name, entry in copied.items():
                if entry["kind"] == "file":
                    with (self.destination / name).open("rb") as stream:
                        os.fsync(stream.fileno())
            self.copied = True
            self.report = {"source": str(self.source), "destination": str(self.destination),
                           "archive": str(self.archive), "files": copied, "tables": tables,
                           "destination_root_mode": "0700", "byte_copy_verified": True}
            return self
        except BaseException:
            self._release()
            # Never remove source or an existing destination on failure. A partial
            # new destination remains private for inspection and explicit recovery.
            raise

    def switch(self) -> None:
        if not self.copied or self.switched:
            raise RuntimeError("Profile is not ready for cutover")
        if self.archive.exists() or self.archive.is_symlink():
            raise FileExistsError(self.archive)
        self.source.rename(self.archive)
        try:
            self.source.symlink_to(self.destination, target_is_directory=True)
        except BaseException:
            if self.source.exists() or self.source.is_symlink():
                raise RuntimeError(
                    "Original path was recreated during cutover; verified archive and "
                    "destination are preserved. Inspect both before manual recovery."
                ) from None
            self.archive.rename(self.source)
            raise
        self.switched = True
        assert os.path.samefile(self.source / "data/kira.sqlite3", self.destination / "data/kira.sqlite3")
        assert os.path.samefile(self.source / "data/kira.lock", self.destination / "data/kira.lock")
        for directory in {self.source.parent, self.destination.parent, self.destination}:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.report["compatibility_alias_verified"] = True

    def rollback_before_start(self) -> None:
        if not self.switched:
            return
        if not self.source.is_symlink() or self.source.readlink() != self.destination:
            raise RuntimeError("Compatibility path changed; refusing automatic rollback")
        self.source.unlink()
        self.archive.rename(self.source)
        self.switched = False

    def _release(self) -> None:
        for fd in reversed(self._fds):
            os.close(fd)
        self._fds.clear()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if exc is not None:
                self.rollback_before_start()
        finally:
            self._release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Require a stopped service and perform the cutover")
    args = parser.parse_args()
    if not args.apply:
        print("Plan only: preserve the original as archive and link its old path to the copied destination.")
        print(json.dumps({k: str(getattr(args, k).absolute()) for k in ("source", "destination", "archive")}))
        return
    with ProfileSeparation(args.source, args.destination, args.archive) as move:
        move.switch()
        print(json.dumps({"copied": True, "tables_verified": len(move.report["tables"]),
                          "compatibility_alias_verified": True, "archive": str(move.archive)}))


if __name__ == "__main__":
    main()
