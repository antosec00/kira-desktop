"""Explicit full-access file tools retain bounded, no-symlink operations."""

import hashlib
import os
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from kira.tools import filesystem
from kira.tools.filesystem import FileTools


@pytest.fixture
def directories(tmp_path):
    project = tmp_path / "project"
    sibling = tmp_path / "sibling"
    project.mkdir()
    sibling.mkdir()
    return project, sibling


@pytest.mark.parametrize("relative", [False, True])
def test_external_paths_require_explicit_opt_in(directories, relative):
    project, sibling = directories
    target = sibling / "note.txt"
    target.write_text("sibling fixture")
    path = "../sibling/note.txt" if relative else str(target)
    tools = FileTools(project)
    for operation, args in (
        (tools.read_file, {"path": path}),
        (tools.write_file, {"path": path, "content": "changed"}),
        (tools.list_files, {"path": "../sibling" if relative else str(sibling)}),
        (tools.search_files, {"path": "../sibling" if relative else str(sibling),
                              "query": "fixture"}),
    ):
        with pytest.raises(KiraError) as error:
            operation(args)
        assert error.value.code == "PATH_DENIED"
    with pytest.raises(KiraError):
        tools.hash_file(path)
    assert target.read_text() == "sibling fixture"


@pytest.mark.parametrize("relative", [False, True])
def test_explicit_external_read_hash_create_and_compare_swap(directories, relative):
    project, sibling = directories
    target = sibling / "note.txt"
    path = "../sibling/note.txt" if relative else str(target)
    tools = FileTools(project, allow_external=True)
    created = tools.write_file({"path": path, "content": "initial"})
    assert created["path"] == str(target)
    assert created["kind"] == "host_file"
    read = tools.read_file({"path": path})
    hashed = tools.hash_file(path)
    assert read["content"] == "initial"
    assert read["path"] == hashed["path"] == str(target)
    assert read["sha256"] == hashed["sha256"]
    with pytest.raises(KiraError, match="hash differs"):
        tools.write_file({"path": path, "content": "unconditional overwrite"})
    changed = tools.write_file({"path": path, "content": "updated",
                                "expected_sha256": read["sha256"]})
    assert target.read_text() == "updated"
    assert changed["path"] == str(target)
    assert changed["sha256"] == hashlib.sha256(b"updated").hexdigest()
    with pytest.raises(KiraError, match="hash differs"):
        tools.write_file({"path": path, "content": "stale",
                          "expected_sha256": read["sha256"]})
    assert sorted(item.name for item in sibling.iterdir()) == ["note.txt"]


def test_full_access_default_list_and_search_stay_in_project(directories):
    project, sibling = directories
    (project / "inside.txt").write_text("needle inside")
    (sibling / "outside.txt").write_text("needle outside")
    tools = FileTools(project, allow_external=True)
    listed = tools.list_files({})
    assert listed["path"] == str(project)
    assert [entry["name"] for entry in listed["entries"]] == ["inside.txt"]
    searched = tools.search_files({"query": "needle"})
    assert searched["matches"] == [{"path": str(project / "inside.txt"),
                                     "line": 1, "text": "needle inside"}]
    written = tools.write_file({"path": "new.txt", "content": "project file"})
    assert written["path"] == str(project / "new.txt")
    assert written["kind"] == "project_file"


def test_relative_parent_normalization_and_explicit_home(directories, monkeypatch):
    project, sibling = directories
    tools = FileTools(project, allow_external=True)
    target = sibling / "note.txt"
    result = tools.write_file({"path": "../sibling/unused/../note.txt", "content": "fixture"})
    assert result["path"] == str(target)
    assert result["kind"] == "host_file"
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(sibling) + str(path)[1:])
    assert tools.read_file({"path": "~/note.txt"})["path"] == str(target)
    assert tools.list_files({"path": "~"})["path"] == str(sibling)


@pytest.mark.parametrize("bad_path", [None, 1, ["note"], "bad\0path"])
def test_full_access_still_rejects_invalid_paths(directories, bad_path):
    project, _ = directories
    with pytest.raises(KiraError) as error:
        FileTools(project, allow_external=True).read_file({"path": bad_path})
    assert error.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("parent_link", [False, True])
def test_full_access_refuses_file_and_parent_symlinks(directories, parent_link):
    project, sibling = directories
    target = sibling / "target.txt"
    target.write_text("fixture preserved")
    link = sibling / "link"
    link.symlink_to(sibling if parent_link else target, target_is_directory=parent_link)
    path = str(link / "target.txt" if parent_link else link)
    tools = FileTools(project, allow_external=True)
    for operation, args in (
        (tools.read_file, {"path": path}),
        (tools.write_file, {"path": path, "content": "overwrite"}),
    ):
        with pytest.raises(KiraError, match="Symbolic links are not followed"):
            operation(args)
    with pytest.raises(KiraError, match="Symbolic links are not followed"):
        tools.hash_file(path)
    if parent_link:
        with pytest.raises(KiraError, match="Symbolic links are not followed"):
            tools.list_files({"path": str(link)})
        with pytest.raises(KiraError, match="Symbolic links are not followed"):
            tools.search_files({"path": str(link), "query": "fixture"})
    assert target.read_text() == "fixture preserved"


def test_full_access_refuses_replaced_project_root(directories):
    project, sibling = directories
    (sibling / "target.txt").write_text("fixture")
    tools = FileTools(project, allow_external=True)
    project.rmdir()
    project.symlink_to(sibling, target_is_directory=True)
    with pytest.raises(KiraError, match="Symbolic links are not followed"):
        tools.read_file({"path": "target.txt"})


def test_external_fifo_is_rejected_without_blocking(directories):
    project, sibling = directories
    path = sibling / "pipe"
    os.mkfifo(path)
    tools = FileTools(project, allow_external=True)
    with pytest.raises(KiraError, match="regular"):
        tools.read_file({"path": str(path)})
    with pytest.raises(KiraError, match="regular"):
        tools.write_file({"path": str(path), "content": "not written"})
    with pytest.raises(KiraError, match="regular"):
        tools.hash_file(str(path))
    assert tools.search_files({"path": str(sibling), "query": "fixture"})["matches"] == []


@pytest.mark.parametrize("symlink", [False, True])
def test_external_create_race_never_clobbers_new_target(directories, monkeypatch, symlink):
    project, sibling = directories
    target = sibling / "race.txt"
    other = sibling / "other.txt"
    other.write_text("preserved")
    link = os.link

    def race(source, destination, **kwargs):
        if symlink:
            target.symlink_to(other)
        else:
            target.write_text("another writer")
        return link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(KiraError, match="Another writer"):
        FileTools(project, allow_external=True).write_file({"path": str(target), "content": "ours"})
    assert other.read_text() == "preserved"
    assert target.is_symlink() if symlink else target.read_text() == "another writer"
    assert not list(sibling.glob(".kira-*.tmp"))


@pytest.mark.parametrize("symlink", [False, True])
def test_external_compare_swap_rechecks_target(directories, monkeypatch, symlink):
    project, sibling = directories
    target = sibling / "race.txt"
    target.write_text("original")
    other = sibling / "other.txt"
    other.write_text("preserved")
    tools = FileTools(project, allow_external=True)
    read = tools._read
    calls = 0

    def race(parent, name):
        nonlocal calls
        calls += 1
        if calls == 2:
            if symlink:
                target.unlink()
                target.symlink_to(other)
            else:
                target.write_text("another writer")
        return read(parent, name)

    monkeypatch.setattr(tools, "_read", race)
    with pytest.raises(KiraError) as error:
        tools.write_file({"path": str(target), "content": "ours",
                          "expected_sha256": hashlib.sha256(b"original").hexdigest()})
    assert error.value.code == ("PATH_DENIED" if symlink else "CONFLICT")
    assert other.read_text() == "preserved"
    assert target.is_symlink() if symlink else target.read_text() == "another writer"
    assert not list(sibling.glob(".kira-*.tmp"))


@pytest.mark.parametrize("relative", [False, True])
def test_external_search_is_bounded_and_returns_absolute_paths(directories, relative):
    project, sibling = directories
    (sibling / "matches.txt").write_text("needle\n" * 110)
    (sibling / "too_large.txt").write_text("needle" * 1000)
    (sibling / "loop").symlink_to(sibling, target_is_directory=True)
    path = "../sibling" if relative else str(sibling)
    tools = FileTools(project, max_bytes=1000, allow_external=True)
    result = tools.search_files({"path": path, "query": "needle"})
    assert result["truncated"] is True
    assert len(result["matches"]) == 100
    assert all(item["path"] == str(sibling / "matches.txt") for item in result["matches"])
    assert result["bytes_examined"] <= result["limits"]["search_bytes"]
    listed = tools.list_files({"path": path})
    assert listed["path"] == str(sibling)
    assert next(item for item in listed["entries"] if item["name"] == "loop")["kind"] == "symlink"
    with pytest.raises(KiraError, match="exceeds"):
        tools.read_file({"path": str(sibling / "too_large.txt")})
    with pytest.raises(KiraError, match="size limit"):
        tools.write_file({"path": str(sibling / "new.txt"), "content": "x" * 1001})


def test_external_list_and_search_keep_entry_bounds(directories, monkeypatch):
    project, sibling = directories
    for number in range(8):
        (sibling / f"file-{number}.txt").write_text("needle")
    monkeypatch.setattr(filesystem, "MAX_ENTRIES", 3)
    tools = FileTools(project, allow_external=True)
    listed = tools.list_files({"path": str(sibling)})
    assert listed["truncated"] is True
    assert len(listed["entries"]) == 3
    found = tools.search_files({"path": str(sibling), "query": "needle"})
    assert found["truncated"] is True
    assert found["files_examined"] == 3
    assert all(Path(item["path"]).parent == sibling for item in found["matches"])
