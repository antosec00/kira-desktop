"""Real Qt tool-group interactions preserve original output and reading position."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


@pytest.fixture
def group_ui(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "synthetic-project", "name": "Grouped tool fixture",
                          "root": str(tmp_path), "permission": "read_only"}
    workspace._preferences.update(onboarding_complete=True, resume_hint_visible=False)
    calls = []
    def request(method, params, received=None, failed=None, **kwargs):
        # A saved-goal read does not belong to the tool-presentation RPC list.
        if method == "goal.get":
            if received:
                received({"goal": None})
            return True
        if method == "soul.snapshot":
            # The identity chip reads metadata when this fixture connects a chat.
            # It cannot execute tools or select/change an identity.
            assert params == {"project_id": workspace.project["id"], "session_id": workspace.sessionId}
            if received:
                received({"snapshot": {**params, "soul": None, "epoch": 0,
                                       "selected": False, "disabled": False}})
            return True
        calls.append((method, params, received, failed))
        return True

    monkeypatch.setattr(workspace.client, "request", request)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1280, 900)
    qtbot.wait(60)
    calls.clear()
    yield workspace, window, engine.rootContext().contextProperty("localization")
    assert not calls
    assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                   or "TypeError:" in record.message or "Unable to assign" in record.message
                   for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def control(window, name, index=None):
    result = item(window.contentItem(), name if index is None else f"{name}_{index}")
    assert result is not None, (name, index)
    return result


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def click(window, button):
    assert button.isVisible() and button.isEnabled()
    center = button.mapToScene(button.boundingRect().center())
    assert 0 < center.x() < window.width() and 0 < center.y() < window.height()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(center.x()), round(center.y())))


def append_tool(workspace, identity, *, run_id="synthetic-run", status="confermato", **fields):
    return workspace.transcript.append(kind="tool", title="Synthetic tool", runId=run_id,
                                       toolId=identity, status=status, **fields)


def test_pointer_keyboard_group_toggle_preserves_individual_output_and_original_actions(
        group_ui, qtbot):
    workspace, window, _ = group_ui
    source = "<b>literal</b> **result**\n" * 36
    raw = '{"ok": true, "literal": "Impostazioni"}'
    command = "python3 check_files.py"
    append_tool(workspace, "first", body=source, raw=raw, command=command)
    append_tool(workspace, "second", body="Second original result")
    qtbot.wait(70)
    header = control(window, "toolGroupToggle", 0)
    assert header.isVisible() and header.property("operationCount") == 2
    assert not header.property("expanded")
    assert not control(window, "messageText", 0).isVisible()
    assert control(window, "messageEntry", 1).height() == 0

    click(window, header)
    qtbot.wait(60)
    assert header.property("expanded")
    receipt = control(window, "toolReceipt", 0)
    assert receipt.isVisible() and not receipt.property("expanded")
    assert not control(window, "messageText", 0).isVisible()
    click(window, receipt)
    qtbot.wait(40)
    text = control(window, "messageText", 0)
    assert text.isVisible() and text.property("text") == workspace.transcript.rows[0]["previewBody"]
    assert evaluate(text, "Number(textFormat)") == 0
    click(window, control(window, "messageCopy", 0))
    assert QGuiApplication.clipboard().text() == source
    click(window, control(window, "commandCopy", 0))
    assert QGuiApplication.clipboard().text() == command
    click(window, control(window, "messageDetails", 0))
    qtbot.wait(30)
    assert window.property("eventDetails") == raw
    assert control(window, "eventDetailsText").property("text") == raw
    evaluate(window, "eventDialog.close()")
    qtbot.wait(30)

    click(window, control(window, "outputToggle", 0))
    qtbot.wait(60)
    assert workspace.transcript.rows[0]["outputExpanded"] is True
    assert workspace.transcript.rows[0]["receiptExpanded"] is True
    assert control(window, "messageText", 0).property("text") == source
    header = control(window, "toolGroupToggle", 0)
    header.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(60)
    assert not header.property("expanded")
    assert not control(window, "messageText", 0).isVisible()
    assert workspace.transcript.rows[0]["outputExpanded"] is True
    click(window, header)
    qtbot.wait(60)
    assert header.property("expanded")
    assert control(window, "messageText", 0).property("text") == source
    assert control(window, "outputToggle", 0).property("text") == "Riduci"
    assert workspace.transcript.rows[1]["outputExpanded"] is False
    assert workspace.transcript.rows[0]["body"] == source
    assert workspace.transcript.rows[0]["raw"] == raw
    assert workspace.transcript.rows[0]["command"] == command
    assert not window.property("followOutput")


def test_new_error_opens_group_and_collapsed_header_keeps_the_problem_visible(group_ui, qtbot):
    workspace, window, _ = group_ui
    append_tool(workspace, "first", body="Confirmed result")
    append_tool(workspace, "second", status="in esecuzione", body="Pending result")
    qtbot.wait(60)
    workspace.transcript.update(1, status="errore", body="Recorded failure", raw='{"ok": false}')
    qtbot.wait(60)
    header = control(window, "toolGroupToggle", 0)
    assert header.property("expanded") and header.property("needsAttention")
    assert header.property("outcome") == "error" and header.property("destructive")
    receipt = control(window, "toolReceipt", 1)
    assert receipt.isVisible() and receipt.property("outcome") == "error"
    assert not control(window, "messageStatus", 1).isVisible()
    click(window, receipt)
    qtbot.wait(40)
    assert control(window, "messageStatus", 1).isVisible()
    assert control(window, "messageText", 1).property("text") == "Recorded failure"
    click(window, header)
    qtbot.wait(40)
    assert header.isVisible() and not header.property("expanded")
    assert header.property("needsAttention") and "errore" in header.property("text")
    assert control(window, "messageEntry", 1).height() == 0
    workspace.transcript.update(1, status="errore", body="More detail for the same failure")
    append_tool(workspace, "third", status="in esecuzione", body="Another pending result")
    qtbot.wait(40)
    assert not header.property("expanded") and header.property("needsAttention")
    workspace.transcript.update(2, status="esito non confermato", body="No confirmation received")
    qtbot.wait(60)
    assert header.property("expanded") and header.property("operationCount") == 3
    receipt = control(window, "toolReceipt", 2)
    assert receipt.isVisible() and receipt.property("outcome") == "unconfirmed"
    assert receipt.property("outcomeLabel") == "Esito non confermato"
    assert not control(window, "messageStatus", 2).isVisible()


def test_many_collapsed_tools_take_one_header_without_accumulating_empty_spacing(group_ui, qtbot):
    workspace, window, _ = group_ui
    conversation = control(window, "conversation")

    def history(count):
        workspace.transcript.begin_batch()
        workspace.transcript.clear()
        workspace.transcript.append(kind="user", title="TU", body="Read the synthetic files")
        for number in range(count):
            append_tool(workspace, f"tool-{number}", body=f"Original result {number}\n" * 30)
        workspace.transcript.append(kind="assistant", title="KIRA", body="Confirmed final reply")
        workspace.transcript.end_batch()
        qtbot.wait(70)
        evaluate(conversation, "forceLayout()")

    history(2)
    baseline = conversation.property("contentHeight")
    history(42)
    assert conversation.property("count") == 44
    assert conversation.property("contentHeight") == pytest.approx(baseline, abs=2)
    group_entry = control(window, "messageEntry", 1)
    tail = control(window, "messageEntry", 43)
    assert tail.y() - group_entry.y() == pytest.approx(group_entry.height(), abs=2)
    assert control(window, "toolGroupToggle", 1).property("operationCount") == 42
    assert workspace.transcript.find("synthetic-run", "tool", "tool-41") == 42
    assert "Original result 41" in workspace.transcript.plain_text()
    click(window, control(window, "toolGroupToggle", 1))
    qtbot.wait(80)
    assert conversation.property("contentHeight") > baseline + 1000


def test_group_toggle_keeps_reading_anchor_and_cancels_queued_follow(group_ui, qtbot):
    workspace, window, _ = group_ui
    workspace.transcript.begin_batch()
    for number in range(20):
        workspace.transcript.append(kind="assistant", title="KIRA",
                                    body=f"Synthetic explanation {number}\n" * 7)
        append_tool(workspace, f"first-{number}", run_id=f"run-{number}", body="Result\n" * 30)
        append_tool(workspace, f"second-{number}", run_id=f"run-{number}", body="Other result\n" * 30)
    workspace.transcript.end_batch()
    qtbot.wait(100)
    conversation = control(window, "conversation")
    window.setProperty("followOutput", False)
    evaluate(conversation, "positionViewAtIndex(31, ListView.Beginning); forceLayout()")
    qtbot.wait(60)
    before = control(window, "messageEntry", 31).y() - conversation.property("contentY")
    window.setProperty("followOutput", True)
    workspace.transcriptChanged.emit()
    QMetaObject.invokeMethod(control(window, "toolGroupToggle", 31), "clicked")
    qtbot.wait(80)
    after = control(window, "messageEntry", 31).y() - conversation.property("contentY")
    assert after == pytest.approx(before, abs=2)
    assert not window.property("followOutput") and not conversation.property("atYEnd")
    click(window, control(window, "toolGroupToggle", 31))
    qtbot.wait(60)
    collapsed = control(window, "messageEntry", 31).y() - conversation.property("contentY")
    assert collapsed == pytest.approx(max(0, before), abs=2)
    workspace.transcript.update(32, body="Additional original tool detail\n" * 70)
    workspace.transcript.update(57, body="Additional final explanation\n" * 25)
    workspace.transcriptChanged.emit()
    qtbot.wait(60)
    assert not conversation.property("atYEnd")
    assert control(window, "messageEntry", 31).y() - conversation.property("contentY") == pytest.approx(
        collapsed, abs=2)


def test_group_labels_retranslate_without_changing_original_tool_results(group_ui, qtbot):
    workspace, window, localization = group_ui
    source = "Impostazioni · literal tool output"
    append_tool(workspace, "first", body=source, raw='{"value": "Chiudi"}')
    append_tool(workspace, "second", body="Second result")
    qtbot.wait(60)
    header = control(window, "toolGroupToggle", 0)
    assert header.property("summary") == "Attività di Kira · 2 operazioni"
    assert header.property("outcomeLabel") == "completato"
    before = workspace.transcript.plain_text()
    localization.setLanguage("en")
    qtbot.wait(60)
    assert header.property("summary") == "Kira activity · 2 operations"
    assert header.property("outcomeLabel") == "completed"
    click(window, header)
    qtbot.wait(50)
    assert control(window, "messageText", 0).property("text") == source
    assert control(window, "messageStatus", 0).property("text") == "confirmed"
    localization.setLanguage("it")
    qtbot.wait(60)
    assert header.property("summary") == "Attività di Kira · 2 operazioni"
    assert header.property("outcomeLabel") == "completato"
    assert header.property("expanded")
    assert workspace.transcript.plain_text() == before
    assert workspace.transcript.rows[0]["raw"] == '{"value": "Chiudi"}'


def test_mixed_height_conversation_scrollbar_drag_keeps_delegates_visible_and_disjoint(group_ui, qtbot):
    workspace, window, _ = group_ui
    conversation = control(window, "conversation")
    bar = control(window, "conversationScrollBar")
    thumb = item(bar, "scrollThumb")

    workspace.transcript.begin_batch()
    workspace.transcript.append(kind="user", title="TU", body="Synthetic greeting", status="Completato")
    workspace.transcript.append(kind="assistant", title="KIRA", body="Short synthetic reply", status="Completato")
    workspace.transcript.append(kind="state", title="Completato", body="Completed.", status="completed")
    workspace.transcript.append(kind="user", title="TU", body="Inspect the synthetic repository", status="Completato")
    for index in range(14):
        append_tool(workspace, f"batch-a-{index}", run_id="run-a",
                    body="\n".join(f"line {line}: synthetic tool output" for line in range(220)))
    workspace.transcript.append(
        kind="assistant", title="KIRA",
        body="\n\n".join(f"### Section {section}\n" + "\n".join(
            f"- Item {point}: detailed synthetic explanation." for point in range(8)) for section in range(12)),
        status="Completato",
    )
    workspace.transcript.append(kind="state", title="Completato", body="Completed.", status="completed")
    workspace.transcript.append(kind="user", title="TU", body="Evaluate the synthetic architecture", status="Completato")
    for index in range(3):
        append_tool(workspace, f"batch-b-{index}", run_id="run-b",
                    body="\n".join(f"line {line}: synthetic check" for line in range(180)))
    workspace.transcript.append(
        kind="assistant", title="KIRA",
        body="\n\n".join(f"### Analysis {section}\n" + "\n".join(
            f"- Detail {point}: synthetic architectural note." for point in range(10)) for section in range(15)),
        status="Completato",
    )
    workspace.transcript.append(kind="state", title="Completato", body="Completed.", status="completed")
    workspace.transcript.end_batch()
    qtbot.wait(100)

    def assert_viewport_consistent():
        content_y = conversation.property("contentY")
        view_height = conversation.height()
        origin_y = conversation.property("originY")
        content_height = conversation.property("contentHeight")
        maximum_y = max(origin_y, origin_y + content_height - view_height)
        assert origin_y - 1 <= content_y <= maximum_y + 1
        content_item = conversation.property("contentItem")
        visible_entries = [
            child for child in content_item.childItems()
            if child.objectName().startswith("messageEntry_") and child.isVisible() and child.height() > 0
        ]
        visible_entries.sort(key=lambda entry: entry.property("index"))
        for previous, current in zip(visible_entries, visible_entries[1:]):
            assert previous.y() + previous.height() <= current.y() + 1
        in_viewport = [
            entry for entry in visible_entries
            if entry.y() + entry.height() > content_y and entry.y() < content_y + view_height
        ]
        assert in_viewport

    assert_viewport_consistent()
    settled_height = conversation.property("contentHeight")

    start = thumb.mapToScene(thumb.boundingRect().center()).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=start)
    for fraction in (0.85, 0.65, 0.45, 0.25, 0.05, 0.01):
        target = bar.mapToScene(QPointF(bar.width() / 2, bar.height() * fraction)).toPoint()
        QTest.mouseMove(window, target, delay=15)
        qtbot.wait(15)
        assert_viewport_consistent()
        assert conversation.property("contentHeight") == pytest.approx(settled_height, abs=2)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=target)
    qtbot.wait(40)

    evaluate(conversation, "positionViewAtIndex(4, ListView.Beginning); forceLayout()")
    qtbot.wait(30)
    click(window, control(window, "toolGroupToggle", 4))
    qtbot.wait(40)
    click(window, control(window, "toolReceipt", 4))
    qtbot.wait(40)
    click(window, control(window, "outputToggle", 4))
    qtbot.wait(60)
    expanded_height = conversation.property("contentHeight")
    assert expanded_height > settled_height + 2500
    assert_viewport_consistent()

    start = thumb.mapToScene(thumb.boundingRect().center()).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=start)
    for fraction in (0.15, 0.35, 0.55, 0.75, 0.95, 0.5, 0.05, 0.98):
        target = bar.mapToScene(QPointF(bar.width() / 2, bar.height() * fraction)).toPoint()
        QTest.mouseMove(window, target, delay=15)
        qtbot.wait(15)
        assert_viewport_consistent()
        assert conversation.property("contentHeight") == pytest.approx(expanded_height, abs=2)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=target)

