"""Real QML evidence for motion visibility and compact, persistent editing."""

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from test_ui_clean_layout import active_run, bounds, clean_ui, evaluate, visual_item

__all__ = ["clean_ui"]


def assert_renders(window):
    image = window.grabWindow()
    ratio = window.devicePixelRatio()
    assert not image.isNull()
    assert (image.width(), image.height()) == (round(window.width() * ratio), round(window.height() * ratio))


def emit(workspace, kind, payload):
    workspace._event({"run_id": "run-a", "seq": workspace._cursors.get("run-a", 0) + 1,
                      "type": kind, "timestamp": "2026-01-01T12:01:00Z", "payload": payload})


@pytest.mark.parametrize("size,font_size,browser", [
    ((1380, 900), 13, False), ((880, 620), 18, False), ((1380, 900), 18, True),
])
def test_compact_editor_grows_without_replacing_draft_or_selection(clean_ui, qtbot, size, font_size, browser):
    workspace, window, engine, _, calls = clean_ui
    window.resize(*size)
    appearance = engine.rootContext().contextProperty("appearance")
    appearance.setOption("ui_size", font_size)
    appearance.setOption("content_size", font_size)
    workspace.setUiPreference("resume_hint_visible", False)
    workspace.web._pane_visible = browser
    workspace.web.changed.emit()
    qtbot.wait(70)
    root = window.contentItem()
    editor = visual_item(root, "prompt")
    scroll = visual_item(root, "composerScroll")
    card = visual_item(root, "composerCard")
    short_height = scroll.height()
    # Two text lines plus padding; font metrics differ by a pixel or two
    # between the local and CI font sets (DejaVu versus Noto).
    assert short_height <= 2 * font_size + 24
    assert card.height() < 170
    source = "A synthetic draft to keep while the layout changes.\n" * 36
    editor.setProperty("text", source)
    editor.forceActiveFocus()
    evaluate(editor, "select(4, 24)")
    qtbot.wait(50)
    assert scroll.height() > short_height + 15
    assert scroll.height() <= scroll.property("maximumInputHeight") + 1
    assert editor.height() > scroll.height() * 2
    assert editor.property("selectedText") == source[4:24]
    appearance.setOption("full_width", False)
    qtbot.wait(40)
    assert visual_item(root, "prompt") is editor
    assert editor.property("text") == workspace.draftText == source
    assert editor.property("selectedText") == source[4:24]
    QTest.keyClick(window, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_Exclam)
    qtbot.wait(30)
    assert editor.property("text") == workspace.draftText == source + "!"
    cursor = editor.property("cursorRectangle")
    cursor_top = editor.mapToScene(cursor.topLeft()).y()
    assert bounds(scroll).top() <= cursor_top < bounds(scroll).bottom()
    assert card.width() <= 900
    for name in ("sendButton", "promptAttach", "projectQueueButton"):
        control = visual_item(root, name)
        assert control.isVisible() and bounds(card).contains(bounds(control))
    assert not [call for call in calls if call[0] in {"run.start", "run.cancel", "run.steer", "queue.add"}]
    editor.setProperty("text", "")
    qtbot.wait(40)
    assert scroll.height() == pytest.approx(short_height, abs=1)


def test_only_visible_active_receipts_pulse_and_terminal_rows_stop(clean_ui, qtbot):
    workspace, window, engine, _, _ = clean_ui
    workspace.setUiPreference("resume_hint_visible", False)
    active_run(workspace)
    for index in range(15):
        emit(workspace, "tool.started", {"tool_id": f"tool-{index}", "name": "read_file",
                                         "arguments": {"path": f"notes/document-{index}.md"}})
    workspace.transcript.setToolGroupExpanded(0, True)
    qtbot.wait(80)
    root = window.contentItem()
    conversation = visual_item(root, "conversation")
    window.setProperty("followOutput", False)
    evaluate(conversation, "positionViewAtBeginning()")
    qtbot.wait(40)
    first = visual_item(root, "toolReceipt_0")
    assert first is not None
    marker = visual_item(first, "toolReceiptStatus")
    assert marker.property("animating")
    # A delegate can remain cached while its own receipt has left the viewport.
    conversation.setProperty("contentY", conversation.property("contentY") + first.mapToItem(conversation, QPointF(0, 0)).y() + first.height() + 10)
    qtbot.wait(40)
    assert not marker.property("animating")
    evaluate(conversation, "positionViewAtBeginning()")
    qtbot.wait(40)
    assert marker.property("animating")
    appearance = engine.rootContext().contextProperty("appearance")
    appearance.setOption("motion", "on")
    qtbot.wait(30)
    assert not marker.property("animating")
    appearance.setOption("motion", "off")
    window.hide()
    qtbot.wait(30)
    assert not marker.property("animating")
    window.show()
    qtbot.wait(30)
    assert marker.property("animating")
    window.setProperty("page", 2)
    qtbot.wait(30)
    assert not marker.property("animating")
    window.setProperty("page", 0)
    workspace._runs["run-a"]["state"] = "interrupted"
    workspace.changed.emit()
    qtbot.wait(30)
    assert not marker.property("animating"), "An old running receipt is not evidence of current execution"


def test_paused_team_and_running_chat_have_distinct_status_and_sidebar_accessibility(clean_ui, qtbot):
    workspace, window, engine, _, _ = clean_ui
    workspace._projects = [workspace.project]
    workspace._sessions = [{**workspace._session, "title": "Synthetic conversation with a long title"},
                           {**workspace._session, "id": "unobserved", "title": "Unobserved chat"}]
    workspace.assistant._delegations = [{"id": "delegation", "delegation_id": "delegation", "team_id": "team-paused", "state": "paused"}]
    workspace.assistant.changed.emit()
    active_run(workspace)
    qtbot.wait(60)
    root = window.contentItem()
    team = visual_item(root, "assistantConfigureTeam")
    paused = visual_item(team, "teamStatusIndicator")
    activity = visual_item(root, "activityIndicator")
    sidebar = visual_item(root, "chatStatus_chat-a")
    assert team.property("paused") and "pausa" in team.property("text")
    assert paused.isVisible() and paused.property("state") == "paused" and not paused.property("animating")
    assert activity.property("outcome") == "running" and activity.property("animating")
    assert sidebar.isVisible() and sidebar.property("state") == "running"
    assert paused.property("statusColor") != sidebar.property("statusColor")
    select = visual_item(root, "chatSelect_chat-a")
    assert evaluate(select, "Accessible.name").endswith(workspace.chatStatusIndicators["chat-a"]["label"])
    assert workspace.chatStatusIndicators["chat-a"]["label"] in select.property("toolTipText")
    assert not visual_item(root, "chatStatus_unobserved").isVisible()
    before = (workspace.sessionId, workspace.selectedModel, workspace.selectedEffort)
    select.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert (workspace.sessionId, workspace.selectedModel, workspace.selectedEffort) == before
    emit(workspace, "message.user", {"content": "Controlla la documentazione e prepara un riepilogo."})
    emit(workspace, "message.completed", {"content": "Sto verificando i documenti disponibili. Il Team resta in pausa; puoi aggiungere indicazioni mentre continuo."})
    for index, name in enumerate(["overview", "missing", "changes"]):
        emit(workspace, "tool.started", {"tool_id": f"preview-{index}", "name": "read_file",
                                         "arguments": {"path": f"docs/{name}.md"}})
        if index < 2:
            result = {"ok": True, "content": "Synthetic document."} if index == 0 else {
                "ok": False, "error": {"code": "NOT_FOUND", "message": "Synthetic document unavailable."}}
            emit(workspace, "tool.completed", {"tool_id": f"preview-{index}", "name": "read_file", "result": result})
    workspace.transcript.setToolGroupExpanded(2, True)
    workspace.setUiPreference("resume_hint_visible", False)
    qtbot.wait(70)
    # The wide and the narrow, large-font presentations both render; nothing is written to disk.
    assert_renders(window)
    window.resize(880, 620)
    appearance = engine.rootContext().contextProperty("appearance")
    appearance.setOption("ui_size", 18)
    appearance.setOption("content_size", 18)
    workspace.setUiPreference("resume_hint_visible", False)
    qtbot.wait(70)
    assert_renders(window)
