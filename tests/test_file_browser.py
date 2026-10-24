"""Kira's file chooser engine on synthetic folders; nothing outside tmp_path is changed."""

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QUrl

from kira.ui.file_browser import (
    FileBrowser, build_preview, file_kind, list_directory, name_problem, natural_key, sort_entries,
)

unprivileged = pytest.mark.skipif(os.geteuid() == 0, reason="Permission bits do not restrict root")
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db0000000"
                    "0049454e44ae426082")


def tree(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "beta").mkdir()
    (root / "Alpha").mkdir()
    (root / ".secret").mkdir()
    (root / "file10.txt").write_text("ten")
    (root / "file2.txt").write_text("two two")
    (root / "notes.md").write_text("# Title\n\nline\n")
    (root / "photo.png").write_bytes(PNG)
    (root / ".env").write_text("SYNTHETIC=1")
    return root


@pytest.fixture
def browser(qapp, tmp_path):
    workspace = SimpleNamespace(paths=SimpleNamespace(config=tmp_path / "config"), projects=[], opened=[])
    workspace._open_local_path = workspace.opened.append
    instance = FileBrowser(workspace)
    instance._wsl = False
    yield instance, workspace
    instance.deleteLater()


def wait_listing(qtbot, instance):
    qtbot.waitUntil(lambda: not instance.loading, timeout=5000)


def names(instance):
    return [row["name"] for row in instance.entries]


def test_kinds_and_natural_order():
    assert file_kind("a.PNG", False) == "image" and file_kind("x.py", False) == "code"
    assert file_kind("x.tar", False) == "archive" and file_kind("README", False) == "file"
    assert file_kind(".gitignore", False) == "text" and file_kind("dir.png", True) == "folder"
    assert sorted(["file10", "File2", "file1"], key=natural_key) == ["file1", "File2", "file10"]


def test_folders_come_first_in_every_order(tmp_path):
    rows, truncated = list_directory(tree(tmp_path))
    assert not truncated
    assert [row["name"] for row in sort_entries(rows, "name", False)][:3] == [".secret", "Alpha", "beta"]
    by_size = [row["name"] for row in sort_entries(rows, "size", True) if not row["isDir"]]
    assert by_size[0] == "photo.png" and by_size[-1] == "file10.txt"
    assert all(row["isDir"] for row in sort_entries(rows, "modified", False)[:3])


def test_listing_is_bounded(tmp_path):
    for index in range(12):
        (tmp_path / f"item{index}").write_text("x")
    rows, truncated = list_directory(tmp_path, limit=5)
    assert len(rows) == 5 and truncated


def test_names_are_checked_before_touching_the_disk():
    assert name_problem("") and name_problem("  ") and name_problem("a/b") and name_problem("..")
    assert name_problem("x" * 300)
    assert name_problem("Nuova cartella") == "" and name_problem("é.md") == ""


def test_preview_describes_text_binary_image_and_folder(tmp_path):
    root = tree(tmp_path)
    text = build_preview(root / "notes.md")
    assert text["kind"] == "text" and text["text"].startswith("# Title") and text["size"] == 14
    (root / "blob.bin").write_bytes(b"\x00\x01binary")
    assert "text" not in build_preview(root / "blob.bin")
    image = build_preview(root / "photo.png")
    assert image["imageUrl"] == QUrl.fromLocalFile(str(root / "photo.png")).toString()
    folder = build_preview(root)
    assert folder["kind"] == "folder" and folder["items"] == 9 and folder["permissions"].startswith("d")


def test_listing_hidden_entries_filter_and_patterns(browser, qtbot, tmp_path):
    instance, _ = browser
    root = tree(tmp_path / "work")
    instance.begin("open", "tests", str(root))
    wait_listing(qtbot, instance)
    assert names(instance) == ["Alpha", "beta", "file2.txt", "file10.txt", "notes.md", "photo.png"]
    assert instance.hiddenCount == 2 and instance.writable
    instance.setShowHidden(True)
    assert names(instance)[0] == ".secret" and ".env" in names(instance)
    instance.setShowHidden(False)
    instance.setFilter("FILE")
    assert names(instance) == ["file2.txt", "file10.txt"]
    instance.setFilter("")
    instance.setPatterns(["*.md"])
    assert names(instance) == ["Alpha", "beta", "notes.md"]
    instance.setPatterns(["*"])
    assert len(names(instance)) == 6
    instance.setSort("size")
    assert names(instance)[2] == "photo.png" and instance.sortDescending


def test_navigation_history_and_selection_of_a_file(browser, qtbot, tmp_path):
    instance, _ = browser
    root = tree(tmp_path)
    instance.begin("folder", "tests", str(root))
    wait_listing(qtbot, instance)
    assert instance.crumbs[-1] == {"label": root.name, "path": str(root)}
    assert instance.navigate(str(root / "notes.md")) == ""
    wait_listing(qtbot, instance)
    assert instance.path == str(root) and instance.selectName == "notes.md"
    assert instance.navigate("beta") == ""
    wait_listing(qtbot, instance)
    assert instance.path == str(root / "beta") and instance.canGoBack and not instance.canGoForward
    instance.goBack()
    wait_listing(qtbot, instance)
    assert instance.path == str(root) and instance.canGoForward and instance.selectName == "beta"
    instance.goForward()
    wait_listing(qtbot, instance)
    instance.goUp()
    wait_listing(qtbot, instance)
    assert instance.path == str(root)
    assert "non trovato" in instance.navigate(str(root / "missing"))
    assert instance.navigate("file://" + str(root / "Alpha")) == ""
    wait_listing(qtbot, instance)
    assert instance.path == str(root / "Alpha")


def test_windows_paths_need_wsl(browser):
    instance, _ = browser
    assert "WSL" in instance.navigate("C:\\Progetti\\Sintetico")


def test_create_and_rename_never_replace_entries(browser, qtbot, tmp_path):
    instance, _ = browser
    root = tree(tmp_path)
    instance.begin("folder", "tests", str(root))
    wait_listing(qtbot, instance)
    assert instance.createFolder("Nuova cartella") == ""
    wait_listing(qtbot, instance)
    assert (root / "Nuova cartella").is_dir() and instance.selectName == "Nuova cartella"
    assert "Esiste già" in instance.createFolder("Nuova cartella")
    assert instance.createFolder("a/b") != "" and not (root / "a").exists()
    assert "Esiste già" in instance.rename(str(root / "notes.md"), "file2.txt")
    assert (root / "file2.txt").read_text() == "two two"
    assert instance.rename(str(root / "notes.md"), "renamed.md") == ""
    wait_listing(qtbot, instance)
    assert (root / "renamed.md").exists() and not (root / "notes.md").exists()
    assert instance.selectName == "renamed.md"


def test_save_target_adds_the_suffix_and_reports_existing_files(browser, qtbot, tmp_path):
    instance, _ = browser
    root = tree(tmp_path)
    instance.begin("save", "tests", str(root))
    wait_listing(qtbot, instance)
    created = instance.saveTarget("export", "md")
    assert created["path"] == str(root / "export.md") and not created["exists"] and created["error"] == ""
    assert instance.saveTarget("notes.md", "md")["exists"]
    assert instance.saveTarget("notes", "md")["exists"]
    assert "cartella" in instance.saveTarget("beta", "")["error"]
    assert instance.saveTarget("", "md")["error"]


@unprivileged
def test_unwritable_and_unreadable_folders(browser, qtbot, tmp_path):
    instance, _ = browser
    locked = tmp_path / "locked"
    locked.mkdir()
    closed = tmp_path / "closed"
    closed.mkdir()
    try:
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
        closed.chmod(0)
        instance.begin("save", "tests", str(locked))
        wait_listing(qtbot, instance)
        assert not instance.writable
        assert "permesso" in instance.saveTarget("x", "md")["error"]
        assert "permesso" in instance.createFolder("new")
        assert instance.navigate(str(closed)) == ""
        wait_listing(qtbot, instance)
        assert "permesso" in instance.error and instance.entries == []
    finally:
        locked.chmod(0o700)
        closed.chmod(0o700)


def test_remembered_folders_survive_a_restart(browser, qtbot, tmp_path):
    instance, workspace = browser
    root = tree(tmp_path / "remember")
    instance.begin("folder", "project", str(root))
    wait_listing(qtbot, instance)
    instance.setSort("modified")
    instance.remember(str(root / "beta"))
    state = workspace.paths.config / "file-browser.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert json.loads(state.read_text())["last"] == {"project": str(root / "beta")}
    second = FileBrowser(workspace)
    second._wsl = False
    second.begin("folder", "project", "")
    wait_listing(qtbot, second)
    assert second.path == str(root / "beta") and second.sortKey == "modified"
    assert any(place["path"] == str(root / "beta") and place["icon"] == "resume" for place in second.places)
    second.deleteLater()


def test_places_list_home_projects_and_root(browser, tmp_path):
    instance, workspace = browser
    project = tmp_path / "project"
    project.mkdir()
    workspace.projects = [{"name": "Synthetic project", "root": str(project)},
                          {"name": "Gone", "root": str(tmp_path / "gone")}]
    instance.begin("folder", "tests", str(tmp_path))
    places = instance.places
    assert places[0]["path"] == str(Path.home()) and places[0]["icon"] == "home"
    assert {"group": "Progetti di Kira", "label": "Synthetic project", "path": str(project),
            "icon": "projects"} in places
    assert all(place["label"] != "Gone" for place in places)
    assert places[-1]["path"] == "/"


def test_open_externally_only_for_existing_paths(browser, tmp_path):
    instance, workspace = browser
    instance.openExternally(str(tmp_path))
    instance.openExternally(str(tmp_path / "missing"))
    assert workspace.opened == [str(tmp_path)]
