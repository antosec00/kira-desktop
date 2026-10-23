"""Synthetic Qt interactions for Kira's compact, accessible conversation layout."""

from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QRectF, Qt
from PySide6.QtGui import QWindow
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def evaluate(obj, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, code)
    result, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


def click(window, control):
    assert control.isVisible() and control.isEnabled()
    center = control.mapToScene(control.boundingRect().center()).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)


def bounds(control):
    return control.mapRectToScene(QRectF(0, 0, control.width(), control.height()))


@pytest.fixture
def clean_ui(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    workspace._project = {"id": "project-a", "name": "Layout fixture", "root": str(tmp_path),
                          "permission": "project_write"}
    workspace._session = {"id": "chat-a", "project_id": "project-a", "backend": "openai_chatgpt",
                          "model": "chosen-model", "settings": {"reasoning_effort": "xhigh"}}
    workspace._sessions = [workspace._session]
    workspace._backend, workspace._model, workspace._effort = "openai_chatgpt", "chosen-model", "xhigh"
    workspace._connected_value = True
    calls = []
    def request(method, params, received=None, failed=None, **kwargs):
        if method == "goal.get":
            if received:
                received({"goal": None})
            return True
        calls.append((method, params, received, failed))
        return True

    monkeypatch.setattr(workspace.client, "request", request)
    workspace.changed.emit()
    workspace.inputs._timer.stop()
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.requestActivate()
    qtbot.wait(70)
    calls.clear()
    yield workspace, window, engine, paths, calls
    assert not any("binding loop" in record.message.lower() for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def active_run(workspace, state="running"):
    run = {"id": "run-a", "project_id": "project-a", "session_id": "chat-a", "state": state,
           "created_at": (datetime.now(UTC) - timedelta(seconds=125)).isoformat(),
           "request_id": "request-a", "prompt": "Inspect this synthetic document", "outcome": {}}
    workspace._runs = {run["id"]: run}
    workspace.changed.emit()
    return run


def test_hide_hint_persists_and_restore_keeps_header_resume_and_draft(clean_ui, qtbot):
    workspace, window, engine, paths, calls = clean_ui
    root = window.contentItem()
    prompt = visual_item(root, "prompt")
    prompt.setProperty("text", "A literal unsent draft <sample>")
    before_selection = (workspace.sessionId, workspace.backend, workspace.selectedModel, workspace.selectedEffort)
    hint = visual_item(root, "resumeHint")
    conversation = visual_item(root, "conversation")
    height = conversation.height()
    assert hint.isVisible()
    click(window, visual_item(root, "hideResumeHint"))
    qtbot.wait(40)
    assert not hint.isVisible() and not workspace.resumeHintVisible
    assert conversation.height() >= height + 40
    resume = visual_item(root, "resumeWorkspaceButton")
    assert resume.isVisible() and bounds(resume).bottom() < bounds(conversation).top()
    restored = Workspace(paths)
    try:
        assert not restored.resumeHintVisible
    finally:
        restored.close()
        restored.deleteLater()
    window.setProperty("page", 3)
    qtbot.wait(30)
    choice = visual_item(root, "resumeHintChoice")
    # Keyboard activation works through the real persisted settings control.
    choice.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(30)
    assert workspace.resumeHintVisible
    window.setProperty("page", 0)
    qtbot.wait(30)
    assert hint.isVisible()
    assert prompt.property("text") == workspace.draftText == "A literal unsent draft <sample>"
    assert (workspace.sessionId, workspace.backend, workspace.selectedModel, workspace.selectedEffort) == before_selection
    assert not any(call[0] in {"run.start", "session.configure", "session.switch"} for call in calls)
    assert engine.rootObjects()[0] is window


def test_activity_lives_between_transcript_and_composer_and_details_are_literal(clean_ui, qtbot):
    workspace, window, _, _, calls = clean_ui
    active_run(workspace)
    workspace.setUiPreference("resume_hint_visible", False)
    qtbot.wait(50)
    root = window.contentItem()
    activity = visual_item(root, "activityIndicator")
    row = visual_item(root, "activityStatusRow")
    composer = visual_item(root, "composerCard")
    conversation = visual_item(root, "conversation")
    assert activity.parentItem() is row
    assert bounds(conversation).bottom() <= bounds(row).top()
    assert bounds(row).bottom() <= bounds(composer).top()
    assert activity.property("label") == "Sto pensando…"
    assert 125 <= activity.property("elapsedSeconds") < 135
    assert visual_item(root, "activityElapsed").property("text").startswith("02:")
    details = visual_item(root, "activityDetailsButton")
    stop = visual_item(root, "stopRunButton")
    assert details.isVisible() and details.isEnabled() and stop.isVisible() and stop.isEnabled()
    calls.clear()
    click(window, details)
    dialog = window.findChild(QObject, "eventDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert visual_item(root, "eventDetailsText").property("text") == workspace.activityDetails
    assert '"request_id": "request-a"' in workspace.activityDetails
    assert not calls
    QMetaObject.invokeMethod(dialog, "close")
    qtbot.wait(30)
    click(window, stop)
    assert [call[0] for call in calls] == ["run.cancel"]
    assert calls[0][1] == {"run_id": "run-a"}


def test_elapsed_clock_pauses_when_hidden_and_catches_up_without_service_calls(clean_ui, qtbot):
    workspace, window, _, _, calls = clean_ui
    active_run(workspace)
    qtbot.wait(40)
    activity = visual_item(window.contentItem(), "activityIndicator")
    window.requestActivate()
    qtbot.waitUntil(lambda: bool(activity.property("clockRunning")))
    calls.clear()
    other = QWindow()
    other.show()
    other.requestActivate()
    qtbot.waitUntil(lambda: not window.isActive())
    assert activity.property("clockRunning")
    other.close()
    window.hide()
    qtbot.wait(40)
    assert not activity.property("clockRunning")
    paused = activity.property("elapsedSeconds")
    qtbot.wait(1150)
    assert activity.property("elapsedSeconds") == paused
    window.show()
    window.requestActivate()
    qtbot.waitUntil(lambda: bool(activity.property("clockRunning")))
    assert activity.property("elapsedSeconds") > paused
    window.showMinimized()
    qtbot.wait(30)
    assert not activity.property("clockRunning")
    window.showNormal()
    window.requestActivate()
    qtbot.waitUntil(lambda: bool(activity.property("clockRunning")))
    workspace._runs["run-a"]["state"] = "completed"
    workspace.changed.emit()
    qtbot.wait(30)
    assert not activity.property("clockRunning")
    assert not visual_item(window.contentItem(), "activityElapsed").isVisible()
    assert not calls


@pytest.mark.parametrize("size,browser,font_size", [
    ((880, 620), False, 13), ((1380, 900), False, 18),
    ((1380, 900), True, 13), ((1380, 900), True, 18),
])
def test_footer_actions_and_status_fit_even_next_to_browser(clean_ui, qtbot, size, browser, font_size):
    workspace, window, engine, _, _ = clean_ui
    window.resize(*size)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", font_size)
    workspace.setUiPreference("resume_hint_visible", False)
    active_run(workspace)
    workspace.web._pane_visible = browser
    workspace.web.changed.emit()
    qtbot.wait(100)
    root = window.contentItem()
    chat = visual_item(root, "chatColumn")
    chat_bounds = bounds(chat)
    controls = [visual_item(root, name) for name in (
        "chatFilesButton", "chatUsageButton", "chatContextButton", "chatWebButton",
        "sharedMemoryButton", "chatWebSettingsButton", "stopRunButton", "activityDetailsButton",
        "chatPermissionSummary", "chatGPTPlanUsage", "prompt", "sendButton")]
    for control in controls:
        assert control.isVisible(), control.objectName()
        rect = bounds(control)
        assert rect.width() > 20 and rect.height() > 10, control.objectName()
        assert rect.left() >= chat_bounds.left(), control.objectName()
        assert rect.right() <= chat_bounds.right() + 1, control.objectName()
        assert 0 <= rect.top() < rect.bottom() <= window.height(), control.objectName()
    footer = controls[:6]
    for index, first in enumerate(footer):
        for second in footer[index + 1:]:
            assert not bounds(first).intersects(bounds(second))
    assert "comandi Kira non autorizzati" in visual_item(root, "chatPermissionSummary").property("text")
    assert visual_item(root, "conversation").height() > 120
    composer_bounds = bounds(visual_item(root, "composerCard"))
    for name in ("composerModeChoice", "projectFilesButton", "projectQueueButton", "chatToolsButton",
                 "promptAttach", "queueComposerButton", "sendButton"):
        rect = bounds(visual_item(root, name))
        assert composer_bounds.contains(rect), name
    assert visual_item(root, "sendButton").property("text") == "Invia indicazioni"
    assert visual_item(root, "queueComposerButton").property("text") == "Accoda"
    status_bounds = bounds(visual_item(root, "activityStatusRow"))
    for name in ("activityIndicator", "activityDetailsButton", "stopRunButton"):
        assert status_bounds.contains(bounds(visual_item(root, name))), name


def test_completed_questions_are_accessible_without_permanent_banner(clean_ui, qtbot):
    workspace, window, _, _, _ = clean_ui
    row = {"run_id": "run-a", "request_id": "question-a", "status": "answered", "delivered": True,
           "questions": [{"id": "choice", "header": "Fixture", "question": "Which synthetic document?",
                          "options": [{"label": "First", "description": "Sample document"}]}]}
    workspace.inputs._rows = [row]
    workspace.inputs._selected = "run-a:question-a"
    workspace.inputs.changed.emit()
    workspace.inputs.formChanged.emit()
    qtbot.wait(40)
    root = window.contentItem()
    assert not visual_item(root, "inputBanner").isVisible()
    button = visual_item(root, "inputOpen")
    click(window, button)
    dialog = window.findChild(QObject, "inputDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    QMetaObject.invokeMethod(dialog, "close")
    row["status"] = "pending"
    row["delivered"] = False
    workspace.inputs.changed.emit()
    workspace.inputs.formChanged.emit()
    qtbot.wait(40)
    assert visual_item(root, "inputBanner").isVisible()
    assert button.property("primary")
    row["status"] = "answered"
    workspace.inputs._error = "Synthetic answer delivery failed"
    workspace.inputs.changed.emit()
    qtbot.wait(30)
    assert not visual_item(root, "inputBanner").isVisible()
    assert button.property("destructive") and button.property("toolTipText") == workspace.inputs.error


@pytest.mark.parametrize("font_size", [13, 18])
def test_expanded_model_controls_use_chat_column_width_and_keep_selection(clean_ui, qtbot, font_size):
    workspace, window, engine, _, calls = clean_ui
    window.resize(1380, 900)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", font_size)
    workspace.setUiPreference("resume_hint_visible", False)
    workspace.web._pane_visible = True
    workspace.web.changed.emit()
    qtbot.wait(70)
    root = window.contentItem()
    prompt = visual_item(root, "prompt")
    prompt.setProperty("text", "Preserve this synthetic draft")
    popover = window.findChild(QObject, "modelPopover")
    toggle = visual_item(root, "modelControlsToggle")
    assert bounds(visual_item(root, "composerCard")).contains(bounds(toggle))
    evaluate(window, "modelPopover.open()")
    qtbot.wait(50)
    assert popover.property("visible")
    selectors = [visual_item(root, name) for name in ("backendBox", "modelBox", "effortBox", "sessionBox")]
    selection = tuple(selector.property("currentIndex") for selector in selectors)
    card = bounds(visual_item(root, "modelControlsExpanded"))
    assert visual_item(root, "chatColumn").width() < 650
    assert selectors[0].width() == 160 and selectors[2].width() == 122
    assert selectors[1].width() >= 120
    for selector in selectors:
        assert selector.isVisible() and selector.isEnabled()
        assert card.contains(bounds(selector))
    calls.clear()
    toggle.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(30)
    assert not popover.property("visible")
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(30)
    assert popover.property("visible")
    assert tuple(selector.property("currentIndex") for selector in selectors) == selection
    assert (workspace.backend, workspace.selectedModel, workspace.selectedEffort) == ("openai_chatgpt", "chosen-model", "xhigh")
    assert prompt.property("text") == workspace.draftText == "Preserve this synthetic draft"
    assert not calls


def test_completed_team_collapses_but_pending_and_errors_stay_visible(clean_ui, qtbot):
    workspace, window, _, _, _ = clean_ui
    assistant = workspace.assistant
    delegation = {"id": "delegation-a", "delegation_id": "delegation-a", "project_id": "project-a",
                  "session_id": "chat-a", "origin_run_id": "run-a", "team_id": "team-a",
                  "goal": "Synthetic review", "state": "completed", "summary": "Synthetic result",
                  "can_resume": True, "can_cancel": False, "can_message": False,
                  "cleanup_confirmed": True}
    assistant._delegations = [delegation]
    assistant.changed.emit()
    qtbot.wait(40)
    root = window.contentItem()
    panel = visual_item(root, "assistantTeamPanel")
    button = visual_item(root, "assistantConfigureTeam")
    notice = visual_item(root, "assistantInlineNotice")
    assert panel.height() == 0 and not notice.isVisible()
    assert "Completato" in button.property("text")
    click(window, button)
    qtbot.wait(40)
    assert panel.height() == 0 and panel.property("expanded")
    details = window.findChild(QObject, "assistantTeamDetails")
    assert details is not None and details.property("opened")
    for name in ("assistantOpenTeam", "assistantResumeTeam", "assistantConfigureAnotherTeam"):
        assert visual_item(root, name).isVisible()
    click(window, visual_item(root, "assistantTeamDetailsClose"))
    qtbot.wait(30)
    assert panel.height() == 0 and not panel.property("expanded")
    delegation["state"] = "pending"
    delegation["can_cancel"] = True
    assistant.changed.emit()
    qtbot.wait(30)
    assert panel.height() > 0 and notice.isVisible()
    assert evaluate(notice, "children.some(function(child) { return child.text === assistant.current.handoff_text; })")
    click(window, button)
    qtbot.wait(30)
    assert visual_item(root, "assistantPendingHandoff").isVisible()
    assert visual_item(root, "assistantCancelTeam").isVisible()
    click(window, visual_item(root, "assistantTeamDetailsClose"))
    assistant._command_error = "Synthetic team request failed"
    delegation["state"] = "completed"
    assistant.changed.emit()
    qtbot.wait(30)
    assert panel.height() > 0 and notice.isVisible()
    assert evaluate(notice, "children.some(function(child) { return child.text === assistant.error; })")
    click(window, button)
    qtbot.wait(30)
    assert visual_item(root, "assistantTeamError").isVisible()
    assert visual_item(root, "assistantTeamError").property("text") == assistant.error
    click(window, visual_item(root, "assistantTeamDetailsClose"))
    assistant._command_error = ""
    delegation["state"] = "failed"
    delegation["error"] = "Synthetic recorded team error"
    assistant.changed.emit()
    qtbot.wait(30)
    assert not panel.property("expanded") and notice.isVisible()
    assert evaluate(notice, "children.some(function(child) { return child.text === assistant.current.error; })")
    click(window, button)
    qtbot.wait(30)
    assert visual_item(root, "assistantTeamError").property("text") == delegation["error"]
    assert visual_item(root, "assistantTeamError").isVisible()
