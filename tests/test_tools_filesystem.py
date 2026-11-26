import hashlib
import os
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from kira.tools import filesystem
from kira.tools.filesystem import FileTools


def test_read_hash_edit_create_and_conflict(tmp_path):
    tools = FileTools(tmp_path)
    target = tmp_path / "value.txt"
    target.write_text("original")
    read = tools.read_file({"path": "value.txt"})
    assert read["content"] == "original"
    assert read["sha256"] == hashlib.sha256(b"original").hexdigest()
    result = tools.write_file({"path": "value.txt", "content": "changed", "expected_sha256": read["sha256"]})
    assert result["ok"] and target.read_text() == "changed"
    with pytest.raises(KiraError, match="hash differs"):
        tools.write_file({"path": "value.txt", "content": "stale", "expected_sha256": read["sha256"]})
    with pytest.raises(KiraError):
        tools.write_file({"path": "value.txt", "content": "clobber"})
    tools.write_file({"path": "fresh.txt", "content": "created", "expected_sha256": None})
    assert (tmp_path / "fresh.txt").read_text() == "created"
    assert not list(tmp_path.glob(".kira-*.tmp"))


@pytest.mark.parametrize("value", ["../outside", "a/../../outside", "/etc/passwd", "nul\0name"])
def test_path_boundary_rejects_invalid_paths(tmp_path, value):
    with pytest.raises(KiraError):
        FileTools(tmp_path).read_file({"path": value})


def test_absolute_inside_path_is_supported(tmp_path):
    path = tmp_path / "inside.txt"
    path.write_text("inside")
    assert FileTools(tmp_path).read_file({"path": str(path)})["content"] == "inside"


def test_leaf_and_parent_symlinks_cannot_read_or_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "private.txt"
    secret.write_text("outside fixture")
    root = tmp_path / "project"
    root.mkdir()
    (root / "leaf").symlink_to(secret)
    (root / "directory").symlink_to(outside, target_is_directory=True)
    tools = FileTools(root)
    for path in ("leaf", "directory/private.txt"):
        with pytest.raises((OSError, KiraError)):
            tools.read_file({"path": path})
        with pytest.raises((OSError, KiraError)):
            tools.write_file({"path": path, "content": "overwrite"})
    assert secret.read_text() == "outside fixture"


def test_replaced_project_root_symlink_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside fixture")
    project = tmp_path / "project"
    project.mkdir()
    tools = FileTools(project)
    project.rmdir()
    project.symlink_to(outside, target_is_directory=True)
    with pytest.raises(KiraError, match="Symbolic links are not followed") as error:
        tools.read_file({"path": "outside.txt"})
    assert error.value.code == "PATH_DENIED"
    with pytest.raises(KiraError, match="root changed"):
        FileTools(project)


def test_special_file_is_rejected_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(KiraError, match="regular"):
        FileTools(tmp_path).read_file({"path": "pipe"})


def test_create_race_preserves_other_writer(tmp_path, monkeypatch):
    original_link = os.link

    def race(source, destination, **kwargs):
        (tmp_path / destination).write_text("another writer")
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(KiraError, match="Another writer"):
        FileTools(tmp_path).write_file({"path": "race.txt", "content": "ours"})
    assert (tmp_path / "race.txt").read_text() == "another writer"


def test_existing_file_modified_while_preparing_is_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "race.txt"
    target.write_text("original")
    tools = FileTools(tmp_path)
    original_read = tools._read
    calls = 0

    def race(parent, name):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_text("concurrent update")
        return original_read(parent, name)

    monkeypatch.setattr(tools, "_read", race)
    with pytest.raises(KiraError, match="changed while"):
        tools.write_file({"path": "race.txt", "content": "ours", "expected_sha256": hashlib.sha256(b"original").hexdigest()})
    assert target.read_text() == "concurrent update"


def test_bounded_read_search_and_no_symlink_walk(tmp_path):
    (tmp_path / "small.txt").write_text("alpha\nbeta needle\n")
    (tmp_path / "large.txt").write_bytes(b"z" * 200)
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    tools = FileTools(tmp_path, max_bytes=100)
    with pytest.raises(KiraError, match="exceeds"):
        tools.read_file({"path": "large.txt"})
    result = tools.search_files({"query": "needle"})
    assert result["matches"] == [{"path": "small.txt", "line": 2, "text": "beta needle"}]
    assert next(item for item in tools.list_files({})["entries"] if item["name"] == "loop")["kind"] == "symlink"


def test_file_mode_preserved_and_no_temporary_files(tmp_path):
    target: Path = tmp_path / "executable"
    target.write_text("old")
    target.chmod(0o750)
    tools = FileTools(tmp_path)
    tools.write_file({"path": "executable", "content": "new", "expected_sha256": tools.read_file({"path": "executable"})["sha256"]})
    assert target.stat().st_mode & 0o777 == 0o750
    assert sorted(item.name for item in tmp_path.iterdir()) == ["executable"]


@pytest.mark.parametrize("path", [".git/config", ".git/hooks/pre-commit", "sub/.git/config",
                                  ".GIT/info/attributes"])
def test_project_scoped_writes_never_reach_git_metadata(tmp_path, path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / ".git" / "info").mkdir()
    (tmp_path / "sub" / ".git").mkdir(parents=True)
    with pytest.raises(KiraError, match="Git metadata"):
        FileTools(tmp_path).write_file({"path": path, "content": "[filter]\n", "expected_sha256": None})
    assert not (tmp_path / path).exists() or path.startswith(".GIT")
    FileTools(tmp_path).write_file({"path": ".gitignore", "content": "build/\n", "expected_sha256": None})
    assert (tmp_path / ".gitignore").read_text() == "build/\n"


@pytest.mark.parametrize("path", [".git./config", ".git /config", ".git. ./hooks/pre-commit", "GIT~1/config",
                                  "git~1/hooks/pre-commit", "GI1A2B~1/config", ".git::$INDEX_ALLOCATION/config",
                                  ".git:stream/config", "sub/.git./config", "sub/GIT~1/config", ".git.", "GIT~1"])
def test_project_scoped_writes_refuse_every_spelling_of_git_metadata(tmp_path, path):
    # NTFS resolves these names to ".git", on Windows and on Windows drives mounted in Linux.
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / "sub" / ".git").mkdir(parents=True)
    before = sorted(str(item.relative_to(tmp_path)) for item in tmp_path.rglob("*"))
    with pytest.raises(KiraError, match="Git metadata") as caught:
        FileTools(tmp_path).write_file({"path": path, "content": "[core]\n", "expected_sha256": None})
    assert caught.value.code == "PATH_DENIED"
    assert sorted(str(item.relative_to(tmp_path)) for item in tmp_path.rglob("*")) == before
    # Full access is the explicit way to change Git metadata.
    result = FileTools(tmp_path, allow_external=True).write_file(
        {"path": ".git/hooks/fixture", "content": "x", "expected_sha256": None})
    assert result["ok"]


def test_project_scoped_writes_refuse_another_name_of_the_git_entry(tmp_path):
    # A second name of the same entry (a short name on NTFS; here a hard link
    # to a ".git" file) is Git metadata whatever it is called.
    (tmp_path / ".git").write_text("gitdir: elsewhere\n")
    os.link(tmp_path / ".git", tmp_path / "alias")
    tools = FileTools(tmp_path)
    current = tools.read_file({"path": "alias"})["sha256"]
    with pytest.raises(KiraError, match="Git metadata"):
        tools.write_file({"path": "alias", "content": "gitdir: /attacker\n", "expected_sha256": current})
    assert (tmp_path / "alias").read_text() == "gitdir: elsewhere\n"
    tools.write_file({"path": "ordinary.txt", "content": "x", "expected_sha256": None})
    assert (tmp_path / "ordinary.txt").read_text() == "x"


@pytest.mark.parametrize("names", [("..",), ("sub", ".."), (".",), ("",), ("sub/other",)])
def test_directory_walk_accepts_only_single_entry_names(tmp_path, names):
    (tmp_path / "project" / "sub" / "other").mkdir(parents=True)
    tools = FileTools(tmp_path / "project")
    with pytest.raises(KiraError, match="one entry") as caught:
        with tools._directory(names):
            pass
    assert caught.value.code == "PATH_DENIED"


def test_names_with_spaces_accents_and_dots_keep_working(tmp_path):
    tools = FileTools(tmp_path)
    for path in ("folder with spaces/perché.txt", "a.b.c/file.v1.2.txt", "copy~1.txt", ".env.example",
                 "it's & (1) [x] #1.txt", "deep/er/and/deeper/notes.md"):
        created = tools.write_file({"path": path, "content": "inside", "expected_sha256": None})
        assert created["kind"] == "project_file"
        assert tools.read_file({"path": path})["content"] == "inside"
        assert tools.read_file({"path": str(tmp_path / path)})["content"] == "inside"
    listed = {entry["name"]: entry["kind"] for entry in tools.list_files({})["entries"]}
    assert listed["folder with spaces"] == "directory" and listed["copy~1.txt"] == "file"
    assert {match["path"] for match in tools.search_files({"query": "inside"})["matches"]} >= {
        "folder with spaces/perché.txt", "a.b.c/file.v1.2.txt"}


class _Junction:
    """A stat result as Windows reports a junction: a directory with the mount point tag."""

    st_reparse_tag = filesystem.MOUNT_POINT_TAG

    def __init__(self, info):
        self._info = info

    def __getattr__(self, name):
        return getattr(self._info, name)


def _report_as_junction(monkeypatch, junction_name):
    real_stat = os.stat

    def windows_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        return _Junction(info) if path == junction_name and "dir_fd" in kwargs else info

    monkeypatch.setattr(filesystem, "WINDOWS", True)
    monkeypatch.setattr(filesystem.os, "stat", windows_stat)


def test_windows_junction_is_refused_like_a_symbolic_link(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "junction").mkdir(parents=True)
    (root / "junction" / "private.txt").write_text("reached through the junction")
    (root / "plain").mkdir()
    (root / "plain" / "notes.txt").write_text("needle inside")
    tools = FileTools(root)
    _report_as_junction(monkeypatch, "junction")
    for call in (lambda: tools.read_file({"path": "junction/private.txt"}),
                 lambda: tools.hash_file("junction/private.txt"),
                 lambda: tools.list_files({"path": "junction"}),
                 lambda: tools.search_files({"query": "reached", "path": "junction"}),
                 lambda: tools.write_file({"path": "junction/new.txt", "content": "x", "expected_sha256": None}),
                 lambda: tools.write_file({"path": "junction/deeper/new.txt", "content": "x",
                                           "expected_sha256": None})):
        with pytest.raises(KiraError, match="Symbolic links are not followed") as caught:
            call()
        assert caught.value.code == "PATH_DENIED"
    assert sorted(item.name for item in (root / "junction").iterdir()) == ["private.txt"]
    # It is listed as a link and a search from above does not enter it.
    kinds = {entry["name"]: entry["kind"] for entry in tools.list_files({})["entries"]}
    assert kinds == {"junction": "symlink", "plain": "directory"}
    found = tools.search_files({"query": "e"})
    assert [match["path"] for match in found["matches"]] == ["plain/notes.txt"]
    # Full access keeps following it, as before.
    assert FileTools(root, allow_external=True).read_file(
        {"path": "junction/private.txt"})["content"] == "reached through the junction"


def test_directory_really_outside_the_project_is_refused(tmp_path, monkeypatch):
    # Windows reaches a directory by joined names: wherever those names really
    # led, a location outside the project is refused.
    root = tmp_path / "project"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "notes.txt").write_text("inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    tools = FileTools(root)
    monkeypatch.setattr(filesystem.compat, "directory_path", lambda descriptor: outside)
    for call in (lambda: tools.read_file({"path": "sub/notes.txt"}), lambda: tools.list_files({}),
                 lambda: tools.write_file({"path": "sub/new.txt", "content": "x", "expected_sha256": None})):
        with pytest.raises(KiraError, match="outside the project") as caught:
            call()
        assert caught.value.code == "PATH_DENIED"
    assert not (root / "sub" / "new.txt").exists() and not list(outside.iterdir())
    monkeypatch.setattr(filesystem.compat, "directory_path", lambda descriptor: root / "sub")
    assert tools.read_file({"path": "sub/notes.txt"})["content"] == "inside"
    monkeypatch.setattr(filesystem.compat, "directory_path", lambda descriptor: tmp_path / "project-other")
    with pytest.raises(KiraError, match="outside the project"):
        tools.read_file({"path": "sub/notes.txt"})


def test_windows_entries_that_are_not_plain_names_are_listed_but_never_opened(tmp_path, monkeypatch):
    # Another system can leave names that Windows would open as a device or as another entry.
    for name in ("nul", "aux.txt", "trailing.", "plain.txt"):
        (tmp_path / name).write_text("needle")
    tools = FileTools(tmp_path)
    monkeypatch.setattr(filesystem, "WINDOWS", True)
    opened: list[str] = []
    real_stat = os.stat

    def recording_stat(path, *args, **kwargs):
        if "dir_fd" in kwargs:
            opened.append(str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(filesystem.os, "stat", recording_stat)
    kinds = {entry["name"]: entry["kind"] for entry in tools.list_files({})["entries"]}
    assert kinds == {"nul": "special", "aux.txt": "special", "trailing.": "special", "plain.txt": "file"}
    assert [match["path"] for match in tools.search_files({"query": "needle"})["matches"]] == ["plain.txt"]
    assert not {"nul", "aux.txt", "trailing."} & set(opened)


@pytest.mark.parametrize("name", ["C:", "notes.txt:stream", "back\\slash"])
def test_windows_directory_walk_refuses_drive_stream_and_separator_names(tmp_path, monkeypatch, name):
    tools = FileTools(tmp_path)
    monkeypatch.setattr(filesystem, "WINDOWS", True)
    with pytest.raises(KiraError, match="one entry"):
        with tools._directory((name,)):
            pass
