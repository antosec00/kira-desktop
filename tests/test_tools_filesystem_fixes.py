"""File tool boundaries: encodings, names, modes, permissions and partial searches."""

import errno
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from kira.tools import filesystem
from kira.tools.filesystem import FileTools

unprivileged = pytest.mark.skipif(os.geteuid() == 0, reason="Permission bits do not restrict root")


@pytest.fixture
def private_umask():
    """Run like the packaged service, whose systemd unit sets UMask=0077."""
    previous = os.umask(0o077)
    yield
    os.umask(previous)


def wire(value):
    """The service encodes tool results as UTF-8 JSON."""
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def make_bytes_name(directory: Path, name: bytes, content: bytes) -> None:
    with open(os.path.join(os.fsencode(directory), name), "wb") as stream:
        stream.write(content)


@pytest.fixture
def restore_modes():
    changed: list[Path] = []
    yield changed
    for path in changed:
        path.chmod(0o755)


# TL-3: non-UTF-8 content is reported as lossy and never written back.
def test_non_utf8_file_is_reported_lossy_and_never_rewritten(tmp_path):
    original = "caffè latte\n".encode("latin-1")
    (tmp_path / "latin.txt").write_bytes(original)
    tools = FileTools(tmp_path)
    read = tools.read_file({"path": "latin.txt"})
    assert read["lossy"] is True and read["encoding"] == "non_utf8"
    assert "�" in read["content"] and read["sha256"] == hashlib.sha256(original).hexdigest()
    with pytest.raises(KiraError, match="not valid UTF-8") as error:
        tools.write_file({"path": "latin.txt", "content": read["content"],
                          "expected_sha256": read["sha256"]})
    assert error.value.code == "UNSUPPORTED"
    assert (tmp_path / "latin.txt").read_bytes() == original
    (tmp_path / "plain.txt").write_text("caffè\n", encoding="utf-8")
    plain = tools.read_file({"path": "plain.txt"})
    assert "lossy" not in plain and "encoding" not in plain


# TL-4: undecodable names are shown safely and cannot be addressed.
def test_undecodable_names_are_json_safe_and_not_addressable(tmp_path):
    make_bytes_name(tmp_path, b"caff\xe8.txt", b"needle\n")
    (tmp_path / "plain.txt").write_text("needle\n")
    tools = FileTools(tmp_path)
    listed = tools.list_files({})
    wire(listed)
    entry = next(item for item in listed["entries"] if item.get("undecodable"))
    assert entry == {"name": "caff\\xe8.txt", "kind": "file", "bytes": 7, "undecodable": True}
    searched = tools.search_files({"query": "needle"})
    wire(searched)
    assert {"path": "caff\\xe8.txt", "line": 1, "text": "needle", "undecodable": True} in searched["matches"]
    assert {"path": "plain.txt", "line": 1, "text": "needle"} in searched["matches"]
    with pytest.raises(KiraError, match="valid Unicode") as error:
        tools.read_file({"path": "caff\udce8.txt"})
    assert error.value.code == "INVALID_ARGUMENT"
    with pytest.raises(FileNotFoundError):
        tools.read_file({"path": entry["name"]})
    with pytest.raises(KiraError, match="valid Unicode"):
        tools.write_file({"path": "new.txt", "content": "lone \udce8 surrogate", "expected_sha256": None})
    assert not (tmp_path / "new.txt").exists()


# TL-9: created files and directories do not inherit the service's private umask.
def test_new_files_and_directories_use_conventional_modes(tmp_path, private_umask):
    tools = FileTools(tmp_path)
    tools.write_file({"path": "pkg/sub/module.py", "content": "VALUE = 1\n", "expected_sha256": None})
    assert (tmp_path / "pkg/sub/module.py").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "pkg").stat().st_mode & 0o777 == 0o755
    assert (tmp_path / "pkg/sub").stat().st_mode & 0o777 == 0o755
    private = tmp_path / "private.txt"
    private.write_text("old")
    private.chmod(0o600)
    tools.write_file({"path": "private.txt", "content": "new",
                      "expected_sha256": hashlib.sha256(b"old").hexdigest()})
    assert private.stat().st_mode & 0o777 == 0o600


# TL-10: create-only writes create missing parents inside the allowed scope only.
def test_create_only_write_creates_missing_parent_directories(tmp_path):
    tools = FileTools(tmp_path)
    result = tools.write_file({"path": "docs/guide/intro.md", "content": "# Intro\n",
                               "expected_sha256": None})
    assert (tmp_path / "docs/guide/intro.md").read_text() == "# Intro\n"
    assert result["created_directories"] == [str(tmp_path / "docs"), str(tmp_path / "docs/guide")]
    again = tools.write_file({"path": "docs/guide/next.md", "content": "next", "expected_sha256": None})
    assert "created_directories" not in again
    with pytest.raises(KiraError, match="no longer exists") as error:
        tools.write_file({"path": "missing/file.txt", "content": "x", "expected_sha256": "0" * 64})
    assert error.value.code == "CONFLICT" and not (tmp_path / "missing").exists()
    with pytest.raises(KiraError, match="Git metadata"):
        tools.write_file({"path": "fresh/.git/hooks/pre-commit", "content": "x", "expected_sha256": None})
    assert not (tmp_path / "fresh").exists()


def test_parent_creation_never_follows_symbolic_links(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "linked").symlink_to(outside, target_is_directory=True)
    (project / "file").write_text("not a directory")
    for scope in (FileTools(project), FileTools(project, allow_external=True)):
        with pytest.raises(KiraError, match="Symbolic links") as error:
            scope.write_file({"path": "linked/new/file.txt", "content": "x", "expected_sha256": None})
        assert error.value.code == "PATH_DENIED"
        with pytest.raises(NotADirectoryError):
            scope.write_file({"path": "file/new.txt", "content": "x", "expected_sha256": None})
    assert not list(outside.iterdir())


def test_full_access_creates_external_parents(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = tmp_path / "elsewhere/notes/today.txt"
    result = FileTools(project, allow_external=True).write_file(
        {"path": str(target), "content": "external", "expected_sha256": None})
    assert target.read_text() == "external" and result["kind"] == "host_file"
    assert result["created_directories"] == [str(tmp_path / "elsewhere"), str(tmp_path / "elsewhere/notes")]


# TL-11: a traverse-only ancestor (execute without read) does not block the tools.
@unprivileged
def test_traverse_only_ancestor_still_allows_every_tool(tmp_path, restore_modes):
    outer = tmp_path / "outer"
    project = outer / "project"
    project.mkdir(parents=True)
    (project / "notes.txt").write_text("needle\n")
    outer.chmod(0o311)
    restore_modes.append(outer)
    tools = FileTools(project)
    assert tools.read_file({"path": "notes.txt"})["content"] == "needle\n"
    assert [entry["name"] for entry in tools.list_files({})["entries"]] == ["notes.txt"]
    assert tools.search_files({"query": "needle"})["matches"][0]["path"] == "notes.txt"
    assert tools.hash_file("notes.txt")["bytes"] == 7
    tools.write_file({"path": "out/created.txt", "content": "ok", "expected_sha256": None})
    read = tools.read_file({"path": "out/created.txt"})
    tools.write_file({"path": "out/created.txt", "content": "changed", "expected_sha256": read["sha256"]})
    assert (project / "out/created.txt").read_text() == "changed"


@unprivileged
def test_traverse_only_parent_directory_accepts_writes(tmp_path, restore_modes):
    drop = tmp_path / "drop"
    drop.mkdir()
    drop.chmod(0o311)
    restore_modes.append(drop)
    tools = FileTools(tmp_path)
    tools.write_file({"path": "drop/report.txt", "content": "delivered", "expected_sha256": None})
    assert tools.read_file({"path": "drop/report.txt"})["content"] == "delivered"


# TL-2 and TL-12: partial searches keep their matches and say what was skipped.
@unprivileged
def test_search_skips_unreadable_and_large_entries_but_keeps_matches(tmp_path, restore_modes):
    (tmp_path / "a_found.txt").write_text("needle one\n")
    locked = tmp_path / "b_locked"
    locked.mkdir()
    (locked / "hidden.txt").write_text("needle hidden\n")
    locked.chmod(0)
    restore_modes.append(locked)
    secret = tmp_path / "c_secret.txt"
    secret.write_text("needle secret\n")
    secret.chmod(0)
    (tmp_path / "d_large.txt").write_text("needle " * 100)
    (tmp_path / "e_found.txt").write_text("needle two\n")
    try:
        result = FileTools(tmp_path, max_bytes=200).search_files({"query": "needle"})
    finally:
        secret.chmod(0o644)
    assert [match["path"] for match in result["matches"]] == ["a_found.txt", "e_found.txt"]
    assert result["truncated"] is True
    assert result["skipped_large"] == 1 and result["skipped_unreadable"] == 2
    assert {(item["path"], item["reason"]) for item in result["skipped"]} == {
        ("b_locked", "unreadable"), ("c_secret.txt", "unreadable"), ("d_large.txt", "too_large")}


def test_search_reports_a_directory_that_vanishes(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("needle\n")
    (tmp_path / "gone").mkdir()
    (tmp_path / "z.txt").write_text("needle\n")
    tools = FileTools(tmp_path)
    directory = tools._directory

    def vanishing(parts, **kwargs):
        if parts and parts[-1] == "gone":
            (tmp_path / "gone").rmdir()
        return directory(parts, **kwargs)

    monkeypatch.setattr(tools, "_directory", vanishing)
    result = tools.search_files({"query": "needle"})
    assert [match["path"] for match in result["matches"]] == ["a.txt", "z.txt"]
    assert result["skipped"] == [{"path": "gone", "reason": "vanished"}] and result["truncated"]


def test_complete_search_is_not_truncated(tmp_path):
    (tmp_path / "one.txt").write_text("needle\n")
    (tmp_path / "data.bin").write_bytes(b"needle\0binary")
    result = FileTools(tmp_path).search_files({"query": "needle"})
    assert result["truncated"] is False and "skipped" not in result
    assert result["skipped_large"] == result["skipped_unreadable"] == 0


# TL-21: special files are rejected before they are opened.
def test_special_files_are_never_opened(tmp_path, monkeypatch):
    os.mkfifo(tmp_path / "pipe")
    opened = []
    original = os.open

    def recording(path, flags, *args, **kwargs):
        opened.append((path, flags))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    tools = FileTools(tmp_path)
    with pytest.raises(KiraError, match="regular"):
        tools.read_file({"path": "pipe"})
    with pytest.raises(KiraError, match="regular"):
        tools.hash_file("pipe")
    with pytest.raises(KiraError, match="regular"):
        FileTools(tmp_path, allow_external=True).read_file({"path": "/dev/null"})
    assert not [path for path, _ in opened if path in {"pipe", "null"}]
    (tmp_path / "plain.txt").write_text("text")
    tools.read_file({"path": "plain.txt"})
    flags = next(flags for path, flags in opened if path == "plain.txt")
    assert flags & os.O_NOCTTY and flags & os.O_NOFOLLOW


# TL-22: filesystems without hard links still get create-only writes.
@pytest.mark.parametrize("code", [errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS])
def test_create_only_write_without_hard_links(tmp_path, monkeypatch, private_umask, code):
    def unsupported(*args, **kwargs):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(os, "link", unsupported)
    result = FileTools(tmp_path).write_file({"path": "fat.txt", "content": "on FAT", "expected_sha256": None})
    assert (tmp_path / "fat.txt").read_text() == "on FAT"
    assert result["sha256"] == hashlib.sha256(b"on FAT").hexdigest()
    assert (tmp_path / "fat.txt").stat().st_mode & 0o777 == 0o644
    assert sorted(item.name for item in tmp_path.iterdir()) == ["fat.txt"]


def test_direct_create_never_replaces_another_writer(tmp_path, monkeypatch):
    def unsupported_after_race(source, destination, **kwargs):
        (tmp_path / destination).write_text("another writer")
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", unsupported_after_race)
    with pytest.raises(KiraError, match="Another writer"):
        FileTools(tmp_path).write_file({"path": "race.txt", "content": "ours", "expected_sha256": None})
    assert (tmp_path / "race.txt").read_text() == "another writer"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["race.txt"]


def test_unrelated_link_failure_is_not_hidden(tmp_path, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "link", full)
    with pytest.raises(OSError, match="No space"):
        FileTools(tmp_path).write_file({"path": "x.txt", "content": "x", "expected_sha256": None})
    assert not list(tmp_path.iterdir())


# TL-28: listing survives races, reports special files and refuses symlinks in every scope.
def test_listing_skips_vanished_entries_and_reports_special_files(tmp_path, monkeypatch):
    (tmp_path / "kept.txt").write_text("kept")
    (tmp_path / "removed.txt").write_text("removed")
    (tmp_path / "folder").mkdir()
    os.mkfifo(tmp_path / "pipe")
    original = os.stat

    def racing(path, *args, **kwargs):
        if path == "removed.txt":
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", racing)
    entries = {item["name"]: item["kind"] for item in FileTools(tmp_path).list_files({})["entries"]}
    assert entries == {"folder": "directory", "kept.txt": "file", "pipe": "special"}


@pytest.mark.parametrize("external", [False, True])
def test_symlinks_are_path_denied_in_every_scope(tmp_path, external):
    target = tmp_path / "real"
    target.mkdir()
    (target / "file.txt").write_text("real")
    (tmp_path / "leaf").symlink_to(target / "file.txt")
    (tmp_path / "folder").symlink_to(target, target_is_directory=True)
    tools = FileTools(tmp_path, allow_external=external)
    operations = (
        lambda: tools.read_file({"path": "leaf"}),
        lambda: tools.read_file({"path": "folder/file.txt"}),
        lambda: tools.write_file({"path": "leaf", "content": "x", "expected_sha256": None}),
        lambda: tools.list_files({"path": "folder"}),
        lambda: tools.search_files({"path": "folder", "query": "real"}),
        lambda: tools.hash_file("leaf"),
    )
    for operation in operations:
        with pytest.raises(KiraError, match="Symbolic links are not followed") as error:
            operation()
        assert error.value.code == "PATH_DENIED"
    assert (target / "file.txt").read_text() == "real"
    assert stat.S_ISLNK((tmp_path / "leaf").lstat().st_mode)


def test_entry_bounds_still_apply_with_race_tolerance(tmp_path, monkeypatch):
    for number in range(5):
        (tmp_path / f"file-{number}.txt").write_text("needle")
    monkeypatch.setattr(filesystem, "MAX_ENTRIES", 2)
    listed = FileTools(tmp_path).list_files({})
    assert listed["truncated"] and len(listed["entries"]) == 2
