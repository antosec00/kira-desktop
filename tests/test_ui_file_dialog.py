"""Kira's file chooser in the real QML window, on synthetic folders only."""

import copy
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def visual(root, name):
    if root.objectName() == name and root.isVisible():
        return root
    for child in root.childItems():
        if found := visual(child, name):
            return found
    return None


def expression(obj, source):
    expr = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    result, _ = expr.evaluate()
    assert not expr.hasError(), expr.error().toString()
    return result


def click(window, item, button=Qt.MouseButton.LeftButton, modifier=Qt.KeyboardModifier.NoModifier, *, double=False):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    if double:
        QTest.mouseDClick(window, button, modifier, point)
    else:
        QTest.mouseClick(window, button, modifier, point)


def type_text(window, text):
    for character in text:
        QTest.keyClick(window, character)


def tree(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "Alpha").mkdir()
    (root / "beta").mkdir()
    (root / "notes.md").write_text("# Notes\n")
    (root / "photo.txt").write_text("text")
    (root / ".hidden").write_text("x")
    return root


@pytest.fixture
def ui(qapp, qtbot, tmp_path, monkeypatch, qtlog):
    workspace = Workspace(Paths(*(tmp_path / p for p in ("config", "data", "state", "ipc"))))
    workspace._project = {"id": "synthetic-project", "name": "Chooser fixture", "root": str(tmp_path)}
    workspace._connected_value = True
    calls = []

    def request(method, params, done, failed=None, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "input.list":
            done({"requests": [], "has_more": False})
        return True

    monkeypatch.setattr(workspace.client, "request", request)
    workspace.projectOpened.emit()
    engine = create_engine(workspace)
    engine.rootContext().contextProperty("attachmentInputs")._wsl = False
    engine.rootContext().contextProperty("fileBrowser")._wsl = False
    window = engine.rootObjects()[0]
    window.resize(1280, 820)
    window.requestActivate()
    QTest.qWaitForWindowActive(window, 3000)
    qtbot.wait(50)
    yield workspace, window, engine, tree(tmp_path / "files")
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any("binding loop" in record.message.lower() for record in qtlog.records)
    assert not any("TypeError" in record.message or "ReferenceError" in record.message
                   for record in qtlog.records), [record.message for record in qtlog.records]


def open_dialog(window, qtbot, name, folder, **properties):
    dialog = window.findChild(QObject, name)
    dialog.setProperty("currentFolder", str(folder))
    for key, value in properties.items():
        dialog.setProperty(key, value)
    expression(dialog, "open()")
    browser = QQmlEngine.contextForObject(dialog).contextProperty("fileBrowser")
    qtbot.waitUntil(lambda: dialog.property("opened") and not browser.loading and browser.path == str(folder))
    qtbot.waitUntil(lambda: visual(window.contentItem(), "fileDialogEntries") is not None)
    return dialog, browser


def selection(dialog):
    value = dialog.property("selection")
    return list(value.toVariant() if hasattr(value, "toVariant") else value)


def entry_names(browser):
    return [row["name"] for row in browser.entries]


def test_folder_choice_puts_a_plain_path_in_the_new_project_form(ui, qtbot):
    workspace, window, _, root = ui
    dialog, browser = open_dialog(window, qtbot, "projectFolderDialog", root)
    assert entry_names(browser) == ["Alpha", "beta", "notes.md", "photo.txt"]
    summary = visual(window.contentItem(), "fileDialogSummary")
    assert summary.property("text").endswith(str(root))
    expression(dialog, f"selectOnly(indexOfPath('{root / 'Alpha'}'))")
    assert summary.property("text").endswith(str(root / "Alpha"))
    click(window, visual(window.contentItem(), "fileDialogAccept"))
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert dialog.property("selectedPath") == str(root / "Alpha")
    assert window.findChild(QObject, "projectRootInput").property("text") == str(root / "Alpha")


def test_double_click_keyboard_and_type_ahead_navigation(ui, qtbot):
    _, window, _, root = ui
    dialog, browser = open_dialog(window, qtbot, "projectFolderDialog", root)
    entries = visual(window.contentItem(), "fileDialogEntries")
    first_row = entries.property("contentItem").childItems()
    row = next(item for item in first_row if item.property("height") and item.y() >= 0
               and abs(item.y() - min(child.y() for child in first_row if child.property("height"))) < 1)
    click(window, row, double=True)
    qtbot.waitUntil(lambda: browser.path == str(root / "Alpha") and not browser.loading)
    QTest.keyClick(window, Qt.Key.Key_Backspace)
    qtbot.waitUntil(lambda: browser.path == str(root) and not browser.loading)
    qtbot.waitUntil(lambda: selection(dialog) == [str(root / "Alpha")])
    type_text(window, "no")
    assert selection(dialog) == [str(root / "notes.md")]
    QTest.keyClick(window, Qt.Key.Key_Up)
    assert selection(dialog) == [str(root / "beta")]
    QTest.keyClick(window, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: browser.path == str(root / "beta") and not browser.loading)
    QTest.keyClick(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    qtbot.waitUntil(lambda: browser.path == str(root) and not browser.loading)
    QTest.keyClick(window, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
    assert ".hidden" in entry_names(browser)
    QTest.keyClick(window, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not dialog.property("visible"))


def test_typed_path_and_its_problem(ui, qtbot):
    _, window, _, root = ui
    dialog, browser = open_dialog(window, qtbot, "projectFolderDialog", root)
    QTest.keyClick(window, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    field = visual(window.contentItem(), "fileDialogPathField")
    assert field is not None and field.property("text") == str(root)
    field.setProperty("text", str(root / "missing"))
    QTest.keyClick(window, Qt.Key.Key_Return)
    problem = visual(window.contentItem(), "fileDialogProblem")
    assert problem is not None and browser.path == str(root)
    field.setProperty("text", str(root / "beta"))
    QTest.keyClick(window, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: browser.path == str(root / "beta") and not browser.loading)
    assert visual(window.contentItem(), "fileDialogProblem") is None
    assert visual(window.contentItem(), "fileDialogState").property("text") == "Questa cartella è vuota."
    expression(dialog, "reject()")


def test_new_folder_and_inline_rename(ui, qtbot):
    _, window, _, root = ui
    dialog, browser = open_dialog(window, qtbot, "projectFolderDialog", root)
    click(window, visual(window.contentItem(), "fileDialogNewFolder"))
    name = visual(window.contentItem(), "fileDialogNewFolderName")
    qtbot.waitUntil(lambda: name.hasActiveFocus())
    assert name.property("selectedText") == "Nuova cartella"
    type_text(window, "Corso")
    QTest.keyClick(window, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: selection(dialog) == [str(root / "Corso")])
    assert (root / "Corso").is_dir() and not dialog.property("creatingFolder")
    expression(dialog, f"selectOnly(indexOfPath('{root / 'notes.md'}'))")
    entries = visual(window.contentItem(), "fileDialogEntries")
    entries.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_F2)
    rename = visual(window.contentItem(), "fileDialogRenameField")
    qtbot.waitUntil(lambda: rename.hasActiveFocus())
    assert rename.property("selectedText") == "notes"
    type_text(window, "appunti")
    QTest.keyClick(window, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: selection(dialog) == [str(root / "appunti.md")])
    assert (root / "appunti.md").read_text() == "# Notes\n" and not (root / "notes.md").exists()
    expression(dialog, "reject()")


def test_save_asks_before_replacing_and_adds_the_suffix(ui, qtbot):
    _, window, _, root = ui
    dialog, browser = open_dialog(window, qtbot, "projectExportDialog", root)
    assert entry_names(browser) == ["Alpha", "beta", "notes.md"]
    assert dialog.property("saveText") == "Chooser fixture - chat"
    dialog.setProperty("saveText", "notes")
    accept = visual(window.contentItem(), "fileDialogAccept")
    click(window, accept)
    assert dialog.property("confirmOverwrite") and dialog.property("visible")
    assert visual(window.contentItem(), "fileDialogOverwrite") is not None
    dialog.setProperty("saveText", "fresh")
    assert not dialog.property("confirmOverwrite")
    click(window, accept)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert dialog.property("selectedPath") == str(root / "fresh.md")


def test_replace_confirmation_accepts_the_existing_file(ui, qtbot):
    _, window, _, root = ui
    dialog, _ = open_dialog(window, qtbot, "projectExportDialog", root)
    dialog.setProperty("saveText", "notes.md")
    click(window, visual(window.contentItem(), "fileDialogAccept"))
    click(window, visual(window.contentItem(), "fileDialogReplace"))
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert dialog.property("selectedPath") == str(root / "notes.md")


def test_attachments_select_several_files_and_reach_the_composer(ui, qtbot):
    _, window, _, root = ui
    area = window.findChild(QObject, "prompt")
    expression(area, "chooseAttachments()")
    dialog = window.findChild(QObject, "promptAttachmentPicker")
    browser = QQmlEngine.contextForObject(dialog).contextProperty("fileBrowser")
    qtbot.waitUntil(lambda: dialog.property("opened") and not browser.loading)
    assert browser.navigate(str(root)) == ""
    qtbot.waitUntil(lambda: browser.path == str(root) and not browser.loading and bool(browser.entries))
    accept = visual(window.contentItem(), "fileDialogAccept")
    assert accept.property("text") == "Allega" and not accept.property("enabled")
    expression(dialog, f"selectOnly(indexOfPath('{root / 'notes.md'}'))")
    expression(dialog, f"toggle(indexOfPath('{root / 'photo.txt'}'))")
    expression(dialog, f"toggle(indexOfPath('{root / 'Alpha'}'))")
    assert accept.property("text") == "Allega (2)"
    click(window, accept)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    qtbot.waitUntil(lambda: str(root / "notes.md") in area.property("text") and str(root / "photo.txt") in area.property("text"))
    assert str(root / "Alpha") not in area.property("text")


def test_places_and_details(ui, qtbot):
    workspace, window, engine, root = ui
    dialog, browser = open_dialog(window, qtbot, "projectFolderDialog", root)
    assert {"group": "Progetti di Kira", "label": "Chooser fixture", "path": str(root.parent),
            "icon": "projects"} in browser.places or not workspace.projects
    expression(dialog, f"selectOnly(indexOfPath('{root / 'notes.md'}'))")
    qtbot.waitUntil(lambda: browser.preview.get("path") == str(root / "notes.md"))
    preview = visual(window.contentItem(), "fileDialogTextPreview")
    assert preview is not None and preview.property("text") == "# Notes"
    click(window, visual(window.contentItem(), "fileDialogDetailsToggle"))
    assert visual(window.contentItem(), "fileDialogDetails") is None
    expression(dialog, "reject()")
    QGuiApplication.clipboard().clear()
