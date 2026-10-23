import hashlib
import os
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
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
