"""Native Qt clipboard/drop interactions with synthetic drafts, without provider runs."""

import copy
import json
import os
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication, QImage
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui import attachments
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def visual(root, name):
    if root.objectName() == name:
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


@pytest.fixture
def editor(qapp, qtbot, tmp_path, monkeypatch, qtlog):
    workspace = Workspace(Paths(*(tmp_path / p for p in ("config", "data", "state", "ipc"))))
    workspace._project = {"id": "synthetic-project", "name": "Attachment fixture", "root": str(tmp_path)}
    workspace._connected_value = True
    calls = []
    requests = [{"run_id": "fixture-run", "request_id": "question-one", "status": "pending",
                 "questions": [{"id": "file", "header": "Documento", "question": "Quale file?",
                                "options": [], "isOther": False}]}]

    def request(method, params, done, failed=None, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "input.list":
            done({"requests": copy.deepcopy(requests), "has_more": False})
        return True

    monkeypatch.setattr(workspace.client, "request", request)
    workspace.projectOpened.emit()
    engine = create_engine(workspace)
    bridge = engine.rootContext().contextProperty("attachmentInputs")
    bridge._wsl = False
    window = engine.rootObjects()[0]
    window.resize(880, 620)
    qtbot.wait(50)
    yield workspace, window, calls, requests
    QGuiApplication.clipboard().clear()
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any("binding loop" in record.message.lower() for record in qtlog.records), [record.message for record in qtlog.records]


def target(editor, qtbot, question):
    workspace, window, _, _ = editor
    if question:
        workspace.inputs.refresh()
        dialog = window.findChild(QObject, "inputDialog")
        qtbot.waitUntil(lambda: dialog.property("opened"))
        area = visual(window.contentItem(), "inputText_0")
    else:
        dialog = window.findChild(QObject, "inputDialog")
        expression(dialog, "close()")
        area = visual(window.contentItem(), "prompt")
    assert area is not None
    qtbot.wait(80)
    area.forceActiveFocus()
    return area


def paste(window, mime):
    QGuiApplication.clipboard().setMimeData(mime)
    QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)


@pytest.mark.parametrize("question", [False, True])
def test_plain_text_paste_replaces_selection_without_attachment_delimiters(editor, qtbot, question):
    _, window, calls, _ = editor
    area = target(editor, qtbot, question)
    area.setProperty("text", "before SELECT after")
    expression(area, "select(7, 13)")
    mime = QMimeData()
    mime.setText("Unicode è 🐶\nnext")
    paste(window, mime)
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    assert area.property("text") == "before Unicode è 🐶\nnext after", area.property("attachmentError")
    assert not any(method in ("run.start", "input.answer") for method, _ in calls)
    if question:
        assert editor[0].inputs.drafts["file"]["text"] == area.property("text")


@pytest.mark.parametrize("question", [False, True])
def test_image_clipboard_creates_private_png_path_and_retains_draft(editor, qtbot, question):
    workspace, window, calls, _ = editor
    area = target(editor, qtbot, question)
    area.setProperty("text", "Guarda qui:")
    area.setProperty("cursorPosition", len("Guarda qui:"))
    image = QImage(24, 20, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue)
    mime = QMimeData()
    mime.setImageData(image)
    paste(window, mime)
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    body = area.property("text")
    assert body.startswith("Guarda qui:\n"), area.property("attachmentError")
    raw = body.split("\n", 1)[1]
    path = Path(json.loads(raw) if raw.startswith('"') else raw)
    assert path.parent == workspace.paths.data / "attachments"
    assert path.stat().st_mode & 0o777 == 0o600
    assert QImage(str(path)).size() == image.size()
    assert not any(method in ("run.start", "input.answer") for method, _ in calls)
    if question:
        assert workspace.inputs.drafts["file"]["text"] == body
        assert workspace.inputs.complete


@pytest.mark.parametrize("question", [False, True])
def test_native_multi_file_drop_in_main_and_question(editor, qtbot, tmp_path, question):
    workspace, window, calls, _ = editor
    area = target(editor, qtbot, question)
    paths = [tmp_path / "primo documento.txt", tmp_path / "secondo.pdf"]
    for path in paths:
        path.write_text("Synthetic local attachment")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    position = area.mapToScene(QPointF(area.width() / 2, area.height() / 2))
    enter = QDragEnterEvent(QPoint(round(position.x()), round(position.y())),
                            Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(window, enter)
    assert enter.isAccepted(), (area.width(), area.height(), area.isVisible(), position)
    drop = QDropEvent(position, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(window, drop)
    assert drop.isAccepted()
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    assert area.property("text") == f'{json.dumps(str(paths[0]))}\n{paths[1]}'
    if question:
        assert workspace.inputs.drafts["file"]["text"] == area.property("text")
    assert not any(method in ("run.start", "input.answer") for method, _ in calls)


def test_image_preparation_blocks_send_and_stale_project_result(editor, qtbot, monkeypatch):
    workspace, window, calls, _ = editor
    area = target(editor, qtbot, False)
    area.setProperty("text", "Unsent draft")
    original = attachments.prepare_attachment
    release = Event()

    def delayed(*args, **kwargs):
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(attachments, "prepare_attachment", delayed)
    mime = QMimeData()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue)
    mime.setImageData(image)
    paste(window, mime)
    try:
        assert area.property("attachmentBusy") and area.property("readOnly")
        assert not visual(window.contentItem(), "sendButton").isEnabled()
        QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        assert not any(method == "run.start" for method, _ in calls)
        workspace._project = {"id": "different-project", "name": "Other", "root": "/tmp"}
        workspace.changed.emit()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    assert area.property("text") == "Unsent draft"
    assert area.property("attachmentError")
    assert not list((workspace.paths.data / "attachments").glob("*.png"))


def test_unavailable_file_displays_error_without_changing_answer(editor, qtbot, tmp_path):
    workspace, window, calls, _ = editor
    area = target(editor, qtbot, True)
    area.setProperty("text", "Keep my answer")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "does-not-exist.txt"))])
    paste(window, mime)
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    assert area.property("text") == "Keep my answer"
    assert workspace.inputs.drafts["file"]["text"] == "Keep my answer"
    status = visual(window.contentItem(), "inputAttachmentStatus_0")
    assert status.isVisible() and status.property("text")
    assert not any(method == "input.answer" for method, _ in calls)


def test_context_menu_paste_and_file_picker_open(editor, qtbot, tmp_path):
    _, window, _, _ = editor
    area = target(editor, qtbot, False)
    file = tmp_path / "chosen.txt"
    file.write_text("Synthetic file-picker fixture")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    QGuiApplication.clipboard().setMimeData(mime)
    action = area.findChild(QObject, "promptAttachmentPaste")
    assert action is not None
    expression(action, "triggered()")
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    assert area.property("text") == str(file)
    area.setProperty("text", "")
    picker = area.findChild(QObject, "promptAttachmentPicker")
    assert picker is not None
    expression(area, "chooseAttachments()")
    assert picker.property("visible")
    assert picker.property("openingContext") == area.property("attachmentContext")
    expression(picker, "close()")
    assert visual(window.contentItem(), "promptAttach").isVisible()


def test_file_picker_result_cannot_cross_context(editor, qtbot):
    area = target(editor, qtbot, False)
    area.setProperty("text", "Original draft")
    picker = area.findChild(QObject, "promptAttachmentPicker")
    picker.setProperty("openingContext", "different-conversation")
    expression(picker, "accepted()")
    assert area.property("text") == "Original draft"
    assert "conversazione" in area.property("attachmentError")


def test_pending_image_cannot_follow_question_switch(editor, qtbot, monkeypatch):
    workspace, window, calls, requests = editor
    area = target(editor, qtbot, True)
    area.setProperty("text", "Original answer")
    requests.append({**copy.deepcopy(requests[0]), "request_id": "question-two"})
    workspace.inputs.refresh()
    original = attachments.prepare_attachment
    release = Event()

    def delayed(*args, **kwargs):
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(attachments, "prepare_attachment", delayed)
    mime = QMimeData()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue)
    mime.setImageData(image)
    paste(window, mime)
    try:
        assert area.property("attachmentBusy")
        assert not visual(window.contentItem(), "inputSubmit").isEnabled()
        other_key = next(row["key"] for row in workspace.inputs.requests
                         if row["request_id"] == "question-two")
        workspace.inputs.select(other_key)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    assert area.property("text") == ""
    assert not list((workspace.paths.data / "attachments").glob("*.png"))
    first_key = next(row["key"] for row in workspace.inputs.requests
                     if row["request_id"] == "question-one")
    workspace.inputs.select(first_key)
    assert area.property("text") == "Original answer"
    assert not any(method == "input.answer" for method, _ in calls)


@pytest.mark.parametrize("question", [False, True])
def test_file_paste_replaces_only_selected_text(editor, qtbot, tmp_path, question):
    _, window, _, _ = editor
    area = target(editor, qtbot, question)
    area.setProperty("text", "before SELECT after")
    expression(area, "select(7, 13)")
    file = tmp_path / "replace-selection.txt"
    file.write_text("Synthetic attachment")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    paste(window, mime)
    qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
    assert area.property("text") == f"before {file} after", area.property("attachmentError")


def test_raw_png_drop_is_routed_to_its_own_window_with_two_engines(editor, qtbot, tmp_path):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    workspace, window, _, _ = editor
    area = target(editor, qtbot, False)
    second = Workspace(Paths(*(tmp_path / "second" / p
                               for p in ("config", "data", "state", "ipc"))))
    second._project = {"id": "second-project", "name": "Another fixture", "root": str(tmp_path)}
    engine = create_engine(second)
    engine.rootContext().contextProperty("attachmentInputs")._wsl = False
    other_window = engine.rootObjects()[0]
    other_window.hide()
    try:
        image = QImage(7, 9, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.blue)
        data = QByteArray()
        buffer = QBuffer(data)
        assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "PNG")
        buffer.close()
        mime = QMimeData()
        mime.setData("image/png", data)
        assert not mime.hasImage()
        point = area.mapToScene(QPointF(area.width() / 2, area.height() / 2))
        enter = QDragEnterEvent(QPoint(round(point.x()), round(point.y())),
                                Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(window, enter)
        assert enter.isAccepted()
        drop = QDropEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(window, drop)
        assert drop.isAccepted()
        qtbot.waitUntil(lambda: not area.property("attachmentBusy"))
        path = Path(area.property("text"))
        assert path.parent == workspace.paths.data / "attachments"
        assert QImage(str(path)).size() == image.size()
        assert not (second.paths.data / "attachments").exists()
    finally:
        other_window.setProperty("closeConfirmed", True)
        other_window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        second.close()
        second.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_empty_plain_insertion_keeps_selected_draft(editor, qtbot):
    area = target(editor, qtbot, False)
    area.setProperty("text", "Keep SELECT intact")
    expression(area, "select(5, 11)")
    expression(area, "insertPlainText('')")
    assert area.property("text") == "Keep SELECT intact"
    assert area.property("selectedText") == "SELECT"
