"""Synthetic workbench intents and real Qt interaction; no provider is started."""

import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def evaluate(obj, script):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, script)
    result, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path), "permission": "project_write", "memory": {}}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "codex", "model": "explicit-model", "settings": {"reasoning_effort": "ultra"}}
    workspace._sessions = [workspace._session.copy()]
    workspace._model, workspace._backend, workspace._effort = "explicit-model", "codex", "ultra"
    workspace._catalogs = {"codex": {"available": True, "capabilities": {"steering": True, "native_compaction": True, "native_fork": True, "planning": True}, "models": [{"id": "explicit-model", "name": "Explicit model"}]}}
    workspace._connected_value = True
    workspace._preferences["onboarding_complete"] = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args))
    workspace.changed.emit()
    yield workspace, calls
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def request(calls, method):
    return next(call for call in reversed(calls) if call[0] == method)


def test_draft_restart_literal_and_pending_ack_does_not_erase_new_text(view):
    workspace, calls = view
    workspace.setDraftText("Original draft")
    workspace.workbench.setMode("plan")
    workspace.workbench.send(workspace.draftText)
    start = request(calls, "run.start")
    assert start[1]["mode"] == "plan"
    assert workspace.selectedEffort == "ultra" and workspace.project["permission"] == "project_write"
    workspace.setDraftText("New draft typed while waiting")
    start[2]({"run": {"id": "run", "session_id": "chat", "state": "running"}})
    workspace.flushDrafts()
    reopened = Workspace(workspace.paths)
    try:
        reopened._project, reopened._session = workspace.project.copy(), workspace._session.copy()
        assert reopened.draftText == "New draft typed while waiting"
        assert reopened._prompt_history[-1]["text"] == "Original draft"
    finally:
        reopened.close()
        reopened.deleteLater()


def test_queue_retry_identity_and_ack_is_not_evidence_of_execution(view):
    workspace, calls = view
    workspace.setDraftText("Queued task")
    workspace.workbench.enqueue("Queued task")
    first = request(calls, "queue.add")
    first[3]({"code": "DISCONNECTED", "message": "Disconnected"})
    workspace.workbench.enqueue("Queued task")
    second = request(calls, "queue.add")
    assert first[1] == second[1]
    second[2]({"item": {"id": "item", "status": "pending"}})
    assert workspace.draftText == "" and not workspace._runs
    assert not any(call[0] == "run.start" for call in calls)


def test_queue_operations_use_only_pending_order_and_distinct_steer(view):
    workspace, calls = view
    workbench = workspace.workbench
    workbench._queue = [{"id": "old", "status": "completed"}, {"id": "a", "status": "pending"}, {"id": "b", "status": "pending"}]
    workbench.moveQueue("b", -1)
    assert request(calls, "queue.reorder")[1]["item_ids"] == ["b", "a"]
    request(calls, "queue.reorder")[2]({})
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running", "prompt": "Original"}}
    workspace.setDraftText("New guidance")
    workbench.steer("New guidance")
    assert request(calls, "run.steer")[1]["run_id"] == "run"
    assert not any(call[0] == "run.start" for call in calls)
    workspace._apply_event({"run_id": "run", "type": "message.user", "payload": {"source": "steer", "content": "New guidance"}})
    assert workspace._runs["run"]["prompt"] == "Original"
    assert workspace.transcript.rows[-1]["body"] == "New guidance"


def test_compaction_polls_same_identity_and_waits_for_confirmed_completion(view):
    workspace, calls = view
    workbench = workspace.workbench
    workbench.compact()
    first = request(calls, "session.compact")
    first[2]({"status": "pending"})
    assert not workbench.canCompact and "corso" in workbench.error
    workbench._poll_compact()
    second = request(calls, "session.compact")
    assert first[1] == second[1]
    second[2]({"status": "completed", "compacted": True})
    assert workbench.canCompact and "Contesto ridotto" in workbench.error
    assert not any(call[0] == "run.start" for call in calls)


def test_stale_file_search_does_not_cross_project_and_paths_stay_literal(view):
    workspace, calls = view
    workbench = workspace.workbench
    workbench.searchFiles("old")
    old = request(calls, "project.files")
    workbench.searchFiles("new")
    latest = request(calls, "project.files")
    latest[2]({"files": [{"path": "folder/literal name.py", "bytes": 10}]})
    old[2]({"files": [{"path": "old.py"}]})
    assert workbench.files[0]["path"] == "folder/literal name.py"
    inserted = []
    workbench.insertFile.connect(inserted.append)
    workbench.chooseFile("foreign.py")
    workbench.chooseFile("folder/literal name.py")
    assert inserted == ['"folder/literal name.py"']
    assert not any(call[0] == "run.start" for call in calls)


def test_fork_current_end_works_without_historical_boundary_and_goal_explicit(view):
    workspace, calls = view
    workbench = workspace.workbench
    workspace._runs = {"old": {"id": "old", "session_id": "chat", "state": "completed"}}
    assert workbench.completedRuns == []
    workbench.fork("")
    call = request(calls, "session.fork")
    assert "through_run_id" not in call[1]
    call[3]({"message": "Not supported"})
    workbench.saveGoal("Literal objective", "active")
    request(calls, "goal.update")[2]({"goal": {"objective": "Literal objective", "status": "active"}})
    assert not any(call[0] == "run.start" for call in calls)
    workbench.continueGoal()
    assert request(calls, "run.start")[1]["prompt"] == "Literal objective"


def test_copy_completed_output_skips_incomplete_and_history_never_overwrites_draft(view):
    workspace, _ = view
    workspace._runs = {"done": {"id": "done", "state": "completed"}, "busy": {"id": "busy", "state": "running"}}
    workspace.transcript.append(kind="assistant", runId="done", body="```literal code```", status="completo")
    workspace.transcript.append(kind="assistant", runId="busy", body="partial", status="streaming")
    workspace.workbench.copyLastOutput()
    assert QGuiApplication.clipboard().text() == "```literal code```"
    workspace.setDraftText("keep draft")
    inserted = []
    workspace.workbench.replaceComposer.connect(inserted.append)
    workspace.workbench.useHistory("previous prompt")
    assert inserted == ["previous prompt"] and workspace.draftText == "keep draft"


@pytest.fixture
def ui(view, qtbot, qtlog):
    workspace, calls = view
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(880, 620)
    qtbot.wait(60)
    yield workspace, calls, engine, window
    # Capture the actual warning text before cleanup. Even when the assertion
    # fails, destroy the QML engine before the dependent Workspace fixture;
    # otherwise Qt emits hundreds of unrelated null-context binding errors.
    warnings = [r.message for r in qtlog.records
                if "ReferenceError" in r.message or "binding loop" in r.message.lower()]
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not warnings, "\n".join(warnings[:10])


def test_keyboard_palette_is_searchable_localized_and_preserves_draft(ui, qtbot):
    workspace, calls, engine, window = ui
    prompt = item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Keep this draft")
    QTest.keyClick(window, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    qtbot.wait(60)
    palette = window.findChild(QObject, "actionPalette")
    assert palette.property("visible")
    search = window.findChild(QObject, "quickActionSearch")
    search.setProperty("text", "/plan")
    choices = window.findChild(QObject, "actionChoices")
    assert choices.property("count") == 1
    evaluate(palette, "activate()")
    assert workspace.workbench.mode == "plan" and prompt.property("text") == "Keep this draft"
    previous = deepcopy(workspace._session)
    calls.clear()
    engine.rootContext().contextProperty("localization").setLanguage("en")
    qtbot.wait(40)
    assert item(window.contentItem(), "composerModeChoice").property("displayText") == "Plan"
    assert workspace._session == previous and not calls


def test_busy_ctrl_enter_sends_guidance_without_stopping_or_queueing(ui, qtbot):
    workspace, calls, _, window = ui
    workspace._runs = {"busy": {"id": "busy", "session_id": "chat", "state": "running"}}
    workspace.changed.emit()
    prompt = item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Next request")
    evaluate(prompt, "forceActiveFocus()")
    QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert request(calls, "run.steer")[1]["prompt"] == "Next request"
    assert request(calls, "run.steer")[1]["run_id"] == "busy"
    assert not any(call[0] in {"queue.add", "run.start", "run.cancel"} for call in calls)


def test_every_workbench_section_opens_at_minimum_size(ui, qtbot):
    workspace, calls, engine, window = ui
    panel = window.findChild(QObject, "workbenchDialog")
    for language in ("it", "en"):
        engine.rootContext().contextProperty("localization").setLanguage(language)
        for name in ("queue", "history", "chat", "archives", "changes", "goal"):
            evaluate(panel, f'openSection("{name}")')
            qtbot.wait(20)
            assert panel.property("visible")
            assert panel.property("width") <= window.width() and panel.property("height") <= window.height()
            evaluate(panel, "close()")
    assert not any(call[0] in {"run.start", "queue.add", "run.steer", "goal.update"} for call in calls)


def test_slash_and_at_shortcuts_open_pickers_without_sending(ui, qtbot):
    workspace, calls, _, window = ui
    prompt = item(window.contentItem(), "prompt")
    evaluate(prompt, "forceActiveFocus()")
    QTest.keyClick(window, Qt.Key.Key_Slash)
    qtbot.wait(40)
    palette = window.findChild(QObject, "actionPalette")
    assert palette.property("visible")
    evaluate(palette, "close()")
    prompt.setProperty("text", "")
    evaluate(prompt, "forceActiveFocus()")
    QTest.keyClick(window, Qt.Key.Key_At)
    qtbot.wait(40)
    picker = window.findChild(QObject, "projectFileDialog")
    assert picker.property("visible")
    request(calls, "project.files")[2]({"files": [{"path": "source/file.py", "bytes": 3}]})
    workspace.workbench.chooseFile("source/file.py")
    assert prompt.property("text") == '"source/file.py" '
    assert not any(call[0] in {"run.start", "queue.add", "run.steer"} for call in calls)


def test_new_chat_can_queue_while_another_chat_runs_and_preserves_model(view):
    workspace, calls = view
    workspace._runs = {"other": {"id": "other", "session_id": "other-chat", "state": "running"}}
    workspace.newChat()
    workspace.setDraftText("Task for new chat")
    workspace.workbench.enqueue("Task for new chat")
    create = request(calls, "session.create")
    assert create[1]["model"] == "explicit-model" and create[1]["settings"]["reasoning_effort"] == "ultra"
    create[2]({"session": {"id": "new-chat", "project_id": "project", "backend": "codex", "model": "explicit-model", "settings": {"reasoning_effort": "ultra"}}})
    assert request(calls, "queue.add")[1]["session_id"] == "new-chat"
    assert workspace.draftText == "Task for new chat"
    assert not any(call[0] in {"run.start", "run.steer"} for call in calls)


def test_provider_controls_require_advertised_capabilities(view):
    workspace, calls = view
    workspace._catalogs["codex"]["capabilities"] = {}
    workspace._runs = {"active": {"id": "active", "session_id": "chat", "state": "running"}}
    assert not workspace.workbench.canSteer
    workspace.workbench.steer("No steering")
    workspace._runs.clear()
    assert not workspace.workbench.canCompact and not workspace.workbench.canFork
    workspace.workbench.compact()
    workspace.workbench.fork("")
    workspace.workbench.setMode("plan")
    assert not workspace.workbench.modeAvailable
    workspace.workbench.send("No native plan")
    assert all(call[0] == "goal.get" for call in calls)


def test_queue_poll_replays_a_run_completed_between_polls(view):
    workspace, calls = view
    workspace.workbench.refreshQueue()
    request(calls, "queue.list")[2]({"paused": False, "items": [{"id": "item", "session_id": "chat", "run_id": "fast-run", "status": "completed", "prompt": "Quick task", "mode": "execute"}]})
    assert workspace._runs["fast-run"]["state"] == "completed"
    assert request(calls, "run.subscribe")[1]["run_id"] == "fast-run"


def test_pending_send_cannot_be_duplicated_into_queue(view):
    workspace, calls = view
    workspace.setDraftText("First request")
    workspace.workbench.send("First request")
    assert not workspace.workbench.composerReady
    workspace.workbench.enqueue("First request")
    assert not any(call[0] == "queue.add" for call in calls)


def test_late_queue_ack_does_not_erase_a_new_draft(view):
    workspace, calls = view
    workspace.setDraftText("Queue this")
    workspace.workbench.enqueue("Queue this")
    add = request(calls, "queue.add")
    workspace.setDraftText("Keep this new draft")
    add[2]({"item": {"id": "queued", "status": "pending"}})
    assert workspace.draftText == "Keep this new draft"


def test_queue_editor_keeps_failed_text_until_service_confirmation(ui, qtbot):
    workspace, calls, _, window = ui
    panel = window.findChild(QObject, "workbenchDialog")
    evaluate(panel, 'openSection("queue")')
    editor = window.findChild(QObject, "queueEditor")
    editor.setProperty("itemId", "item")
    editor.setProperty("editable", True)
    window.findChild(QObject, "queuePromptEditor").setProperty("text", "Changed queue text")
    evaluate(editor, "open()")
    workspace.workbench.editQueue("item", "Changed queue text")
    edit = request(calls, "queue.edit")
    edit[3]({"code": "BUSY", "message": "Item already started"})
    assert editor.property("visible")
    assert window.findChild(QObject, "queuePromptEditor").property("text") == "Changed queue text"
    workspace.workbench.editQueue("item", "Changed queue text")
    request(calls, "queue.edit")[2]({"item": {"id": "item", "status": "pending"}})
    assert not editor.property("visible")


@pytest.mark.parametrize("method", ["queue.add", "run.steer", "session.fork"])
def test_unknown_intents_keep_identity_across_context_switch_and_restart(view, monkeypatch, method):
    workspace, calls = view
    if method == "run.steer":
        workspace._runs = {"active": {"id": "active", "session_id": "chat", "state": "awaiting_input"}}
    def invoke(view):
        {"queue.add": lambda: view.workbench.enqueue("Same literal request"),
         "run.steer": lambda: view.workbench.steer("Same literal request"),
         "session.fork": lambda: view.workbench.fork("")}[method]()
    invoke(workspace)
    original = request(calls, method)
    original[3]({"code": "DISCONNECTED", "message": "Unknown acknowledgement"})
    session = workspace._session.copy()
    workspace._session = {**session, "id": "other-chat"}
    workspace.changed.emit()
    workspace._session = session
    workspace.changed.emit()
    invoke(workspace)
    retried = request(calls, method)
    assert original[1]["request_id"] == retried[1]["request_id"]
    retried[3]({"code": "DISCONNECTED", "message": "Unknown acknowledgement"})
    reopened = Workspace(workspace.paths)
    try:
        reopened._project, reopened._session = workspace.project.copy(), session
        reopened._model, reopened._backend = workspace.selectedModel, workspace.backend
        reopened._catalogs = deepcopy(workspace._catalogs)
        reopened._runs = deepcopy(workspace._runs)
        reopened._connected_value = True
        resumed = []
        monkeypatch.setattr(reopened.client, "request", lambda *args, **kwargs: resumed.append(args))
        reopened.changed.emit()
        invoke(reopened)
        assert request(resumed, method)[1]["request_id"] == original[1]["request_id"]
    finally:
        reopened.close()
        reopened.deleteLater()


def test_request_identity_must_save_before_any_mutation(view, monkeypatch):
    workspace, calls = view
    monkeypatch.setattr(workspace._draft_store, "save", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    workspace.workbench.enqueue("Keep safe")
    assert not any(call[0] == "queue.add" for call in calls)
    assert "Nessuna nuova operazione" in workspace.workbench.error


def test_unknown_fork_retry_stays_same_until_explicit_separate_intent(view):
    workspace, calls = view
    workspace.workbench.fork("")
    first = request(calls, "session.fork")
    first[2]({"status": "unknown"})
    workspace.workbench.fork("")
    second = request(calls, "session.fork")
    assert first[1]["request_id"] == second[1]["request_id"]
    second[2]({"status": "unknown"})
    before = len(calls)
    workspace.workbench.allowNewRequests()
    assert len(calls) == before
    workspace.workbench.fork("")
    assert request(calls, "session.fork")[1]["request_id"] != first[1]["request_id"]


def test_fork_banner_exposes_original_without_fabricating_messages(ui, qtbot):
    workspace, calls, _, window = ui
    workspace._session.update(parent_session_id="source-chat", fork_mode="native")
    workspace.changed.emit()
    qtbot.wait(20)
    banner = item(window.contentItem(), "branchContextBanner")
    assert banner.isVisible()
    assert workspace.transcript.rowCount() == 0
    evaluate(window, 'workspace.selectSession(workbench.parentSessionId)')
    assert request(calls, "session.snapshot")[1]["session_id"] == "source-chat"


@pytest.mark.parametrize("method", ["queue.add", "run.steer", "session.fork"])
def test_ack_after_switch_settles_identity_without_replacing_current_chat(view, method):
    workspace, calls = view
    workspace.setDraftText("Confirmed elsewhere")
    if method == "run.steer":
        workspace._runs = {"active": {"id": "active", "session_id": "chat", "state": "running"}}
    {"queue.add": lambda: workspace.workbench.enqueue("Confirmed elsewhere"),
     "run.steer": lambda: workspace.workbench.steer("Confirmed elsewhere"),
     "session.fork": lambda: workspace.workbench.fork("")}[method]()
    call = request(calls, method)
    workspace._session = {**workspace._session, "id": "another"}
    workspace.changed.emit()
    workspace.setDraftText("Keep current draft")
    call[2]({"queue.add": {"item": {"status": "pending"}}, "run.steer": {"accepted": True},
             "session.fork": {"session": {"id": "new-branch"}}}[method])
    assert not workspace._pending_intents
    assert workspace.sessionId == "another" and workspace.draftText == "Keep current draft"
    if method != "session.fork":
        assert ("project", "chat") not in workspace._drafts
        assert workspace._prompt_history[-1]["text"] == "Confirmed elsewhere"


def test_removed_queue_retry_preserves_draft_and_does_not_claim_new_execution(view):
    workspace, calls = view
    workspace.setDraftText("Previously removed")
    workspace.workbench.enqueue("Previously removed")
    request(calls, "queue.add")[2]({"item": {"id": "removed-item", "status": "removed"}})
    assert workspace.draftText == "Previously removed"
    assert not workspace._pending_intents and not workspace._prompt_history
    assert "rimossa" in workspace.workbench.error


def test_saved_goal_anchor_is_once_per_chat_and_does_not_reserve_composer(view, qtbot):
    workspace, calls = view
    qtbot.wait(10)
    assert len([call for call in calls if call[0] == "goal.get"]) == 1
    for _ in range(30):
        workspace.changed.emit()
    qtbot.wait(10)
    assert len([call for call in calls if call[0] == "goal.get"]) == 1
    assert not workspace.workbench.loading
    workspace.setDraftText("Keep this draft while reading the saved goal")
    workspace.workbench.enqueue("Explicit queued task")
    assert request(calls, "queue.add")[1]["prompt"] == "Explicit queued task"
    request(calls, "goal.get")[2]({"goal": {"objective": "Saved objective", "status": "active"}})
    assert workspace.workbench.goal["objective"] == "Saved objective"
    assert workspace.draftText == "Keep this draft while reading the saved goal"
    assert not any(call[0] == "run.start" for call in calls)


def test_goal_anchor_discards_old_chat_and_pre_save_replies(view, qtbot):
    workspace, calls = view
    qtbot.wait(10)
    old_read = request(calls, "goal.get")
    workspace._session = {**workspace._session, "id": "second-chat"}
    workspace.changed.emit()
    qtbot.wait(10)
    new_read = request(calls, "goal.get")
    assert new_read[1]["session_id"] == "second-chat"
    old_read[2]({"goal": {"objective": "Other chat objective", "status": "active"}})
    assert not workspace.workbench.goal
    workspace.workbench.saveGoal("Explicit new objective", "active")
    request(calls, "goal.update")[2]({"goal": {"objective": "Explicit new objective", "status": "active"}})
    new_read[2]({"goal": {"objective": "Outdated objective", "status": "active"}})
    assert workspace.workbench.goal["objective"] == "Explicit new objective"
    assert (workspace.selectedModel, workspace.selectedEffort) == ("explicit-model", "ultra")


def test_failed_goal_anchor_does_not_disable_or_replay_work(view, qtbot):
    workspace, calls = view
    qtbot.wait(10)
    request(calls, "goal.get")[3]({"code": "DISCONNECTED", "message": "Synthetic interrupted read"})
    assert not workspace.workbench.loading
    assert not workspace.workbench.error
    assert not workspace.workbench.goal
    for _ in range(10):
        workspace.changed.emit()
    qtbot.wait(10)
    assert len([call for call in calls if call[0] == "goal.get"]) == 1
    assert not any(call[0] in {"goal.update", "run.start", "queue.add"} for call in calls)


def test_delayed_saved_goal_reply_updates_native_anchor_and_scope_immediately(view, qtbot):
    workspace, calls = view
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        qtbot.wait(30)
        anchor = item(window.contentItem(), 'chatGoalAnchor')
        label = item(window.contentItem(), 'chatGoalText')
        assert not anchor.isVisible()
        request(calls, 'goal.get')[2]({'goal': {'objective': 'Explicit saved objective', 'status': 'active'}})
        qtbot.waitUntil(anchor.isVisible)
        assert label.property('text') == 'Explicit saved objective'
        workspace._session = {**workspace._session, 'id': 'another-chat'}
        workspace.changed.emit()
        qtbot.waitUntil(lambda: not anchor.isVisible())
        assert label.property('text') == ''
        assert not any(call[0] == 'run.start' for call in calls)
    finally:
        window.setProperty('closeConfirmed', True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
