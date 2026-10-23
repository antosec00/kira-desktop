"""Real Qt interaction with bounded, literal tool previews using synthetic output."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


@pytest.fixture
def workspace(qapp, tmp_path):
    view = Workspace(Paths(*(tmp_path / p for p in ("config", "data", "state", "runtime"))))
    view._project = {"id": "synthetic", "name": "Tool preview fixture", "root": str(tmp_path)}
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def ui(workspace, qtbot, qtlog):
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(880, 620)
    qtbot.wait(60)
    yield window
    assert not any("binding loop" in record.message.lower() for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def control(ui, name, index=0):
    result = item(ui.contentItem(), f"{name}_{index}")
    assert result is not None
    return result


def append_tool(workspace, body, **fields):
    return workspace.transcript.append(kind="tool", title="File check", body=body,
                                       status=fields.pop("status", "confermato"), **fields)


def click(ui, button):
    assert button.isVisible() and button.isEnabled()
    center = button.mapToScene(button.boundingRect().center())
    assert 0 < center.x() < ui.width() and 0 < center.y() < ui.height()
    QTest.mouseClick(ui, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(center.x()), round(center.y())))


def expand_receipt(workspace, ui, qtbot, index=0):
    receipt = control(ui, "toolReceipt", index)
    assert receipt.isVisible() and not receipt.property("expanded")
    assert not control(ui, "messageText", index).isVisible()
    click(ui, receipt)
    qtbot.waitUntil(lambda: workspace.transcript.rows[index]["receiptExpanded"])
    qtbot.wait(40)
    assert control(ui, "messageText", index).isVisible()


def test_short_tool_stays_literal_and_has_no_toggle(workspace, ui, qtbot):
    source = "<b>result</b> **literal**\nexit 0"
    append_tool(workspace, source)
    qtbot.wait(70)
    expand_receipt(workspace, ui, qtbot)
    text = control(ui, "messageText")
    assert text.property("text") == source
    assert evaluate(text, "Number(textFormat)") == 0
    assert not control(ui, "outputToggle").isVisible()


@pytest.mark.parametrize("source", [
    "\n".join(f"Line {index}: <b>literal</b> **result**" for index in range(60)),
    "x" * 1400,
    "x" * 3000,
])
def test_long_output_mouse_keyboard_toggle_and_full_copy(workspace, ui, qtbot, source):
    append_tool(workspace, source)
    qtbot.wait(80)
    expand_receipt(workspace, ui, qtbot)
    text = control(ui, "messageText")
    button = control(ui, "outputToggle")
    assert button.isVisible() and text.height() <= 144
    assert text.property("text") == workspace.transcript.rows[0]["previewBody"]
    QMetaObject.invokeMethod(control(ui, "messageCopy"), "clicked")
    assert QGuiApplication.clipboard().text() == source
    # Actual mouse interaction for opening, keyboard interaction for closing.
    click(ui, button)
    qtbot.wait(60)
    assert workspace.transcript.rows[0]["outputExpanded"] is True
    assert text.property("text") == source
    assert evaluate(text, "Number(textFormat)") == 0
    assert text.height() > 144
    assert not ui.property("followOutput")
    button.forceActiveFocus()
    QTest.keyClick(ui, Qt.Key.Key_Space)
    qtbot.wait(60)
    assert workspace.transcript.rows[0]["outputExpanded"] is False
    assert text.height() <= 144
    assert button.property("text") == "Mostra tutto"
    for _ in range(3):
        QMetaObject.invokeMethod(button, "clicked")
        qtbot.wait(20)
    assert text.property("text") == source
    assert workspace.transcript.rows[0]["body"] == source


def test_expansion_survives_streaming_and_recycled_delegates(workspace, ui, qtbot):
    source = "first\n" * 50
    for index in range(35):
        append_tool(workspace, source, toolId=f"tool-{index}")
    qtbot.wait(120)
    ui.setProperty("followOutput", False)
    conversation = item(ui.contentItem(), "conversation")
    QMetaObject.invokeMethod(conversation, "positionViewAtBeginning")
    qtbot.wait(80)
    expand_receipt(workspace, ui, qtbot, 0)
    QMetaObject.invokeMethod(control(ui, "outputToggle", 0), "clicked")
    qtbot.wait(60)
    workspace.transcript.update(0, body=source + "completed\n")
    qtbot.wait(60)
    assert control(ui, "messageText", 0).property("text") == source + "completed\n"
    assert workspace.transcript.rows[0]["outputExpanded"] is True
    assert workspace.transcript.rows[0]["receiptExpanded"] is True
    assert workspace.transcript.rows[1]["outputExpanded"] is False
    assert workspace.transcript.rows[1]["receiptExpanded"] is False
    QMetaObject.invokeMethod(conversation, "positionViewAtEnd")
    qtbot.wait(80)
    assert control(ui, "outputToggle", 34).property("text") == "Mostra tutto"
    assert not control(ui, "toolReceipt", 34).property("expanded")
    assert not control(ui, "outputToggle", 34).isVisible()
    QMetaObject.invokeMethod(conversation, "positionViewAtBeginning")
    qtbot.wait(80)
    assert control(ui, "outputToggle", 0).property("text") == "Riduci"
    assert control(ui, "toolReceipt", 0).property("expanded")
    assert control(ui, "messageText", 0).property("text") == source + "completed\n"


def test_expand_keeps_card_anchor_and_ignores_deferred_autofollow(workspace, ui, qtbot):
    for index in range(25):
        append_tool(workspace, f"Result {index}\n" * 30)
    qtbot.wait(100)
    ui.setProperty("followOutput", False)
    conversation = item(ui.contentItem(), "conversation")
    evaluate(conversation, "positionViewAtIndex(10, ListView.Beginning)")
    qtbot.wait(70)
    expand_receipt(workspace, ui, qtbot, 10)
    entry = control(ui, "messageEntry", 10)
    before = entry.y() - conversation.property("contentY")
    # Queue the same follow callback as a streaming update, then expand before it runs.
    ui.setProperty("followOutput", True)
    workspace.transcriptChanged.emit()
    QMetaObject.invokeMethod(control(ui, "outputToggle", 10), "clicked")
    qtbot.wait(100)
    after = control(ui, "messageEntry", 10).y() - conversation.property("contentY")
    assert abs(after - before) < 2
    assert not ui.property("followOutput")
    assert not conversation.property("atYEnd")
    QMetaObject.invokeMethod(control(ui, "outputToggle", 10), "clicked")
    qtbot.wait(80)
    after_collapse = control(ui, "messageEntry", 10).y() - conversation.property("contentY")
    assert abs(after_collapse - max(0, before)) < 2
    workspace.transcript.update(10, body="updated\n" * 50)
    qtbot.wait(80)
    assert not conversation.property("atYEnd")
    assert control(ui, "messageText", 10).height() <= 144


def test_existing_bounded_display_notice_is_available_after_expansion(workspace, ui, qtbot):
    append_tool(workspace, "x" * (workspace.transcript.MAX_BODY + 100))
    qtbot.wait(60)
    expand_receipt(workspace, ui, qtbot)
    QMetaObject.invokeMethod(control(ui, "outputToggle"), "clicked")
    qtbot.wait(60)
    assert "esporta la cronologia completa" in control(ui, "messageText").property("text")
    QMetaObject.invokeMethod(control(ui, "messageCopy"), "clicked")
    assert QGuiApplication.clipboard().text() == workspace.transcript.rows[0]["body"]


def test_collapsed_error_receipt_exposes_original_status_and_actions_on_open(workspace, ui, qtbot):
    command = "python3 check_files.py"
    append_tool(workspace, "Error details\n" * 100, status="errore", command=command,
                raw='{"ok": false}', toolResult={"ok": False, "error": {
                    "code": "SYNTHETIC_ERROR", "message": "Literal recorded failure"}})
    qtbot.wait(70)
    receipt = control(ui, "toolReceipt")
    assert receipt.isVisible() and receipt.property("outcome") == "error"
    assert receipt.property("errorPreview") == "SYNTHETIC_ERROR: Literal recorded failure"
    assert not control(ui, "commandCopy").isVisible()
    expand_receipt(workspace, ui, qtbot)
    status = control(ui, "messageStatus")
    assert status.isVisible() and status.property("text") == "errore"
    copy = control(ui, "commandCopy")
    assert copy.isVisible()
    click(ui, copy)
    assert QGuiApplication.clipboard().text() == command
    assert control(ui, "outputToggle").isVisible()
    assert control(ui, "messageText").height() <= 144
