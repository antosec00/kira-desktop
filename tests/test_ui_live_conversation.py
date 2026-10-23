"""Synthetic durable guidance and Team conversation with real native composer input."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, Qt
from PySide6.QtTest import QTest

from kira.ui.controller import Workspace
from kira.ui.drafts import DraftStore
import test_ui_workbench as wb
import test_ui_teams as team_tests

view = wb.view
ui = wb.ui
team_workspace = team_tests.team_workspace


def runs(workspace, backend="codex", *, session="chat", lane=""):
    workspace._backend = backend
    workspace._session["backend"] = backend
    workspace._runs = {"active": {"id": "active", "project_id": "project", "session_id": session,
                                  "state": "running", "created_at": "2026-01-01T12:00:00Z"}}
    if lane:
        workspace._runs["active"].update(execution_lane=lane, effective_permission="read_only")
    workspace.changed.emit()


def emit(workspace, kind, payload, run="active"):
    workspace._event({"run_id": run, "seq": workspace._cursors.get(run, 0) + 1,
                      "type": kind, "timestamp": "2026-01-01T12:01:00Z", "payload": payload})


def operational(calls):
    return [c for c in calls if c[0] in {"run.steer", "run.start", "run.cancel", "queue.add"}]


def click(window, item):
    assert item is not None and item.isVisible() and item.isEnabled()
    rect = item.mapRectToScene(item.boundingRect())
    assert QRectF(0, 0, window.width(), window.height()).contains(rect), rect
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    QTest.qWait(20)


def reply_assistant(workspace, calls, *, active=True, available=True, followups=None):
    workspace.assistant.refresh()
    wb.request(calls, "assistant.snapshot")[2]({
        "environment": {"active_teams": [{"id": "team", "state": "running"}] if active else [],
                        "conversation": {"available": available, "read_only": active, "team_id": "team", "reason": ""}},
        "delegations": [], "followups": followups or []})


@pytest.mark.parametrize("backend", ["codex", "openai_chatgpt", "ollama"])
def test_keyboard_guidance_during_blocked_tool_is_durable_single_bubble(ui, qtbot, backend):
    workspace, calls, _, window = ui
    runs(workspace, backend)
    emit(workspace, "tool.started", {"tool_id": "held-tool", "name": "read_file", "arguments": {"path": "notes.txt"}})
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.forceActiveFocus()
    for char in "check the earlier note":
        QTest.keyClick(window, char)
    QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    call = wb.request(calls, "run.steer")
    assert [row[0] for row in operational(calls)] == ["run.steer"]
    assert workspace._active_tools["active"] == {"held-tool"}
    identity = call[1]["request_id"]
    receipt = {"run_id": "active", "request_id": identity, "accepted": False,
               "status": "pending", "delivery_state": "queued", "model_consumption_confirmed": False}
    emit(workspace, "steering.requested", {**receipt, "content": "check the earlier note"})
    call[2](receipt)
    assert workspace.draftText == "" and not workspace._pending_intents
    rows = [r for r in workspace.transcript.rows if r["kind"] == "user"]
    assert len(rows) == 1 and "attesa" in rows[0]["status"]
    assert workspace._active_tools["active"] == {"held-tool"}
    emit(workspace, "message.user", {"source": "steer", "request_id": identity,
                                    "content": "check the earlier note", "delivery_state": "submitted"})
    emit(workspace, "steering.submitted", {**receipt, "delivery_state": "submitted"})
    emit(workspace, "steering.accepted", {**receipt, "accepted": True, "delivery_state": "provider_accepted"})
    rows = [r for r in workspace.transcript.rows if r["kind"] == "user"]
    assert len(rows) == 1 and rows[0]["body"] == "check the earlier note"
    assert rows[0]["status"] == "Indicazioni ricevute dal provider"
    assert workspace._runs["active"].get("prompt", "") == ""
    assert not any(row[0] == "run.cancel" for row in calls)
    assert workspace.selectedModel == "explicit-model" and workspace.selectedEffort == "ultra"


def test_secondary_queue_is_explicit_and_does_not_steer(ui, qtbot):
    workspace, calls, _, window = ui
    runs(workspace)
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.setProperty("text", "A separate request")
    qtbot.wait(30)
    click(window, window.findChild(QObject, "queueComposerButton"))
    call = wb.request(calls, "queue.add")
    assert call[1]["prompt"] == "A separate request"
    assert [row[0] for row in operational(calls)] == ["queue.add"]
    assert workspace.draftText == "A separate request"


def test_other_chat_never_receives_guidance_and_queue_remains_explicit(ui, qtbot):
    workspace, calls, _, window = ui
    runs(workspace, session="another-chat")
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.setProperty("text", "For this chat only")
    prompt.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not operational(calls)
    assert not window.findChild(QObject, "sendButton").isEnabled()
    assert workspace.draftText == "For this chat only"
    click(window, window.findChild(QObject, "queueComposerButton"))
    assert wb.request(calls, "queue.add")[1]["session_id"] == "chat"
    assert not any(row[0] == "run.steer" for row in calls)


@pytest.mark.parametrize("size,font_size", [((1380, 900), 13), ((880, 620), 18)])
def test_active_team_conversation_is_enabled_from_scoped_service_metadata(ui, qtbot, size, font_size):
    workspace, calls, engine, window = ui
    window.resize(*size)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", font_size)
    workspace._project["permission"] = "trusted_host"
    workspace.teams._teams = [{"id": "team", "state": "running", "project_id": "project"}]
    workspace.teams.changed.emit()
    reply_assistant(workspace, calls)
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Explain the current team results")
    qtbot.wait(40)
    assert "Team al lavoro" in window.findChild(QObject, "activityLabel").property("text")
    assert window.findChild(QObject, "chatPermissionSummary").property("text") == "Conversazione in sola lettura"
    click(window, window.findChild(QObject, "sendButton"))
    start = wb.request(calls, "run.start")
    assert start[1]["session_id"] == "chat" and start[1]["prompt"] == "Explain the current team results"
    assert [row[0] for row in operational(calls)] == ["run.start"]
    assert workspace.project["permission"] == "trusted_host"
    start[2]({"run": {"id": "conversation", "session_id": "chat", "project_id": "project",
                       "state": "running", "execution_lane": "team_conversation", "effective_permission": "read_only"}})
    workspace.teams._teams = []
    reply_assistant(workspace, calls, active=False)
    assert workspace.workbench.conversationReadOnly, "An admitted turn keeps its effective permission after Team completion"
    assert workspace.selectedModel == "explicit-model" and workspace.selectedEffort == "ultra"


def test_team_conversation_requires_service_admission_not_only_local_busy_state(ui):
    workspace, calls, _, window = ui
    workspace.teams._teams = [{"id": "team", "state": "running"}]
    reply_assistant(workspace, calls, available=False)
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Wait for admission")
    assert not window.findChild(QObject, "sendButton").isEnabled()
    workspace.workbench.submit(prompt.property("text"))
    assert not operational(calls) and workspace.draftText == "Wait for admission"


def test_unknown_guidance_retry_survives_terminal_run_reopen_and_chat_switch(view, monkeypatch):
    workspace, calls = view
    runs(workspace)
    workspace.setDraftText("Preserve the same instruction")
    workspace.workbench.steer(workspace.draftText)
    first = wb.request(calls, "run.steer")
    first[3]({"code": "DISCONNECTED", "message": "Synthetic lost reply"})
    workspace._runs["active"]["state"] = "completed"
    saved = DraftStore(workspace.paths.config)
    assert saved.pending[0]["steer_run_id"] == "active"
    reopened = Workspace(workspace.paths)
    second_calls = []
    monkeypatch.setattr(reopened.client, "request", lambda *a, **k: second_calls.append(a))
    try:
        reopened._project = deepcopy(workspace._project)
        reopened._session = deepcopy(workspace._session)
        reopened._connected_value = True
        reopened._model, reopened._backend, reopened._effort = "explicit-model", "codex", "ultra"
        reopened.changed.emit()
        assert reopened.workbench.guidanceRetryAvailable
        reopened.workbench.submit(reopened.draftText)
        second = wb.request(second_calls, "run.steer")
        assert first[1] == second[1], "Retry binds the original request and run even after restart"
        reopened._session = {**reopened._session, "id": "other-chat"}
        reopened.changed.emit()
        reopened.setDraftText("A new unrelated draft")
        second[2]({"run_id": "active", "request_id": first[1]["request_id"],
                   "accepted": True, "delivery_state": "provider_accepted"})
        assert reopened.sessionId == "other-chat" and reopened.draftText == "A new unrelated draft"
        assert not reopened._pending_intents
    finally:
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_newer_draft_survives_guidance_ack_and_unknown_intent_does_not_block_new_text(view):
    workspace, calls = view
    runs(workspace)
    workspace.setDraftText("First guidance")
    workspace.workbench.steer(workspace.draftText)
    first = wb.request(calls, "run.steer")
    workspace.setDraftText("New independent message")
    first[3]({"code": "DELIVERY_UNKNOWN", "message": "Synthetic unconfirmed receipt"})
    workspace._runs["active"]["state"] = "completed"
    workspace.changed.emit()
    assert not workspace.workbench.guidanceRetryAvailable and workspace.workbench.canSubmit
    workspace.workbench.submit(workspace.draftText)
    assert wb.request(calls, "run.start")[1]["prompt"] == "New independent message"
    assert workspace._pending_intents[0]["request_id"] == first[1]["request_id"]


def test_followup_snapshot_adopts_only_actual_current_chat_run_and_keeps_draft(ui, qtbot):
    workspace, calls, _, window = ui
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Keep my unsent text")
    base = {"automatic": True, "source": "team_followup", "team_id": "team", "request_id": "followup-intent",
            "run_id": "", "state": "pending", "error": ""}
    reply_assistant(workspace, calls, active=False, followups=[base])
    assert workspace.assistant.followupPending
    assert "attende" in workspace.workbench.activityLabel
    assert not any(row[0] == "run.start" for row in calls)
    actual = {"id": "followup-run", "session_id": "chat", "request_id": "followup-intent", "state": "running",
              "source": "team_followup", "created_at": "2026-01-01T12:00:00Z", "execution_lane": "primary", "effective_permission": "project_write"}
    reply_assistant(workspace, calls, active=False, followups=[{**base, "state": "running", "run_id": "followup-run", "run": actual}])
    assert wb.request(calls, "run.subscribe")[1]["run_id"] == "followup-run"
    assert workspace._runs["followup-run"] == actual
    assert workspace.draftText == prompt.property("text") == "Keep my unsent text"
    count = len([row for row in calls if row[0] == "run.subscribe"])
    reply_assistant(workspace, calls, active=False, followups=[{**base, "state": "running", "run_id": "foreign", "run": {**actual, "id": "foreign", "session_id": "another-chat"}}])
    assert "foreign" not in workspace._runs
    assert len([row for row in calls if row[0] == "run.subscribe"]) == count


def test_manual_team_start_keeps_origin_chat(team_workspace):
    workspace, teams, replies = team_workspace
    teams.setDraftText("goal", "Review project notes")
    teams.startTeam()
    assert replies.calls("team.start")[-1]["origin_session_id"] == workspace.sessionId


def test_guidance_ack_keeps_newer_text_and_known_not_delivered_keeps_original(view):
    workspace, calls = view
    runs(workspace)
    workspace.setDraftText("Already registered")
    workspace.workbench.steer(workspace.draftText)
    first = wb.request(calls, "run.steer")
    workspace.setDraftText("Keep newer words")
    first[2]({"request_id": first[1]["request_id"], "run_id": "active", "accepted": False, "delivery_state": "queued"})
    assert workspace.draftText == "Keep newer words"
    workspace.workbench.steer(workspace.draftText)
    second = wb.request(calls, "run.steer")
    second[2]({"request_id": second[1]["request_id"], "run_id": "active", "accepted": False, "delivery_state": "not_delivered"})
    assert workspace.draftText == "Keep newer words" and not workspace._pending_intents
    assert workspace.workbench.guidanceWarning


def test_selected_run_wins_over_newer_foreign_activity_for_guidance(view):
    workspace, calls = view
    runs(workspace)
    workspace._runs["foreign"] = {"id": "foreign", "session_id": "other-chat", "state": "running"}
    workspace.setDraftText("Only my turn")
    workspace.workbench.steer(workspace.draftText)
    assert wb.request(calls, "run.steer")[1]["run_id"] == "active"


def test_followup_adoption_waits_for_replay_and_skips_older_terminal_runs(view):
    workspace, calls = view
    workspace._chat_loading = True
    base = {"automatic": True, "source": "team_followup", "team_id": "team", "request_id": "intent",
            "run_id": "response", "state": "running", "error": ""}
    run = {"id": "response", "session_id": "chat", "source": "team_followup", "state": "running",
           "created_at": "2026-01-01T12:00:00Z"}
    reply_assistant(workspace, calls, active=False, followups=[{**base, "run": run}])
    assert "response" not in workspace._runs
    assert not any(c[0] == "run.subscribe" for c in calls)
    workspace._chat_loading = False
    workspace.changed.emit()
    assert workspace._runs["response"] == run
    assert wb.request(calls, "run.subscribe")[1]["run_id"] == "response"
    old_run = {**run, "id": "old-response", "state": "completed", "created_at": "2025-01-01T12:00:00Z"}
    reply_assistant(workspace, calls, active=False, followups=[{**base, "state": "completed", "run_id": "old-response", "run": old_run}])
    assert "old-response" not in workspace._runs


@pytest.mark.parametrize("state,pending", [("armed", False), ("pending", True), ("running", True),
    ("completed", False), ("failed", False), ("interrupted", False), ("cancelled", False), ("suppressed", False)])
def test_followup_states_remain_distinct_from_team_result(view, state, pending):
    workspace, calls = view
    followup = {"automatic": True, "source": "team_followup", "team_id": "team", "request_id": "intent",
                "state": state, "run_id": "", "error": "Synthetic reason" if state == "suppressed" else ""}
    reply_assistant(workspace, calls, active=False, followups=[followup])
    assert workspace.assistant.followupPending is pending
    assert workspace.assistant.followupLabel
    assert workspace.assistant.followupError == followup["error"]
    assert not any(c[0] == "run.start" for c in calls)


def test_direct_images_are_not_dropped_into_text_guidance_and_can_be_queued(ui, qtbot, tmp_path):
    import hashlib
    import uuid

    from PySide6.QtGui import QImage

    workspace, calls, _, window = ui
    runs(workspace, "openai_chatgpt")
    identity = str(uuid.uuid4())
    directory = workspace.paths.data / "image-attachments"
    directory.mkdir(parents=True, mode=0o700)
    path = directory / (identity + ".png")
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0x334455)
    assert image.save(str(path))
    path.chmod(0o600)
    row = {"id": identity, "name": "Synthetic image", "mime": "image/png", "width": 16, "height": 16,
           "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "preview_url": path.as_uri()}
    assert workspace.addDraftImage(row, ("project", "chat"))
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Inspect the attached image")
    qtbot.wait(30)
    assert not window.findChild(QObject, "sendButton").isEnabled()
    assert "solo testo" in window.findChild(QObject, "conversationAvailabilityNotice").property("text")
    workspace.workbench.steer(prompt.property("text"))
    assert not any(c[0] == "run.steer" for c in calls)
    click(window, window.findChild(QObject, "queueComposerButton"))
    assert wb.request(calls, "queue.add")[1]["attachments"] == [identity]
    assert workspace.draft_image_ids() == [identity]


def test_manual_team_resume_retry_keeps_original_origin_chat(team_workspace):
    workspace, teams, replies = team_workspace
    team_tests.select(teams, replies, team_tests.snapshot(state="paused", phase="awaiting_user"))
    teams.resumeTeam()
    first = deepcopy(replies.calls("team.resume")[-1])
    assert first["origin_session_id"] == workspace.sessionId
    replies.pending[-1][3]({"code": "DELIVERY_UNKNOWN", "message": "Synthetic uncertain continuation"})
    workspace._session = {**workspace._session, "id": "another-chat"}
    workspace.changed.emit()
    teams.resumeTeam()
    assert replies.calls("team.resume")[-1] == first


def test_pending_followup_does_not_prevent_new_explicit_user_conversation(view):
    workspace, calls = view
    followup = {"automatic": True, "source": "team_followup", "team_id": "team", "state": "pending",
                "waiting_reason": "user_queue", "request_id": "intent", "run_id": "", "error": ""}
    reply_assistant(workspace, calls, active=False, available=False, followups=[followup])
    workspace.setDraftText("A new explicit question")
    assert workspace.workbench.canSubmit
    assert "in coda" in workspace.assistant.followupLabel
    workspace.workbench.submit(workspace.draftText)
    assert wb.request(calls, "run.start")[1]["prompt"] == "A new explicit question"


def test_active_manual_team_context_does_not_reuse_older_delegation_card(ui, qtbot):
    workspace, calls, _, window = ui
    workspace.assistant.refresh()
    wb.request(calls, "assistant.snapshot")[2]({
        "environment": {"active_teams": [{"id": "current-manual", "state": "running"}],
                        "conversation": {"available": True, "read_only": True}},
        "delegations": [{"id": "older", "project_id": "project", "session_id": "chat",
                         "team_id": "older-team", "state": "completed", "goal": "Older completed goal"}],
        "followups": [{"automatic": True, "source": "team_followup", "team_id": "current-manual", "state": "armed"}]})
    assert workspace.assistant.currentTeamId == "current-manual"
    panel = window.findChild(QObject, "assistantTeamPanel")
    assert panel.property("hasTeamContext") and not panel.property("hasDelegation")
    assert window.findChild(QObject, "assistantConfigureTeam").property("text") == "Team al lavoro"
    assert not window.findChild(QObject, "assistantResumeTeam").isVisible()


def test_late_guidance_ack_does_not_regress_recorded_provider_receipt(view):
    workspace, calls = view
    runs(workspace)
    workspace.setDraftText("Use the saved note")
    workspace.workbench.steer(workspace.draftText)
    call = wb.request(calls, "run.steer")
    identity = call[1]["request_id"]
    payload = {"request_id": identity, "run_id": "active", "delivery_state": "queued", "content": "Use the saved note"}
    emit(workspace, "steering.requested", payload)
    emit(workspace, "steering.accepted", {**payload, "delivery_state": "provider_accepted", "accepted": True})
    call[2]({**payload, "accepted": False})
    assert workspace.workbench.guidanceNotice == "Indicazioni ricevute dal provider"
    row = next(r for r in workspace.transcript.rows if r["kind"] == "user")
    assert '"queued"' in row["raw"] and '"provider_accepted"' in row["raw"]
    assert row["body"] == "Use the saved note"


def test_guidance_retranslation_preserves_literal_text_and_fits_narrow_view(ui, qtbot):
    workspace, calls, engine, window = ui
    runs(workspace)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", 18)
    prompt = wb.item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Sola lettura")
    workspace.workbench.steer(workspace.draftText)
    call = wb.request(calls, "run.steer")
    payload = {"request_id": call[1]["request_id"], "run_id": "active", "delivery_state": "queued", "content": "Sola lettura"}
    emit(workspace, "steering.requested", payload)
    call[2]({**payload, "accepted": False})
    engine.rootContext().contextProperty("localization").setLanguage("en")
    prompt.setProperty("text", "Another instruction")
    qtbot.wait(60)
    assert window.findChild(QObject, "sendButton").property("text") == "Send guidance"
    assert workspace.workbench.guidanceNotice == "Guidance waiting for the next step"
    row = next(r for r in workspace.transcript.rows if r["kind"] == "user")
    assert row["body"] == "Sola lettura" and '"Sola lettura"' in row["raw"]
    assert row["status"] == "Guidance waiting for the next step"
    for name in ("sendButton", "queueComposerButton"):
        control = window.findChild(QObject, name)
        rect = control.mapRectToScene(control.boundingRect())
        assert QRectF(0, 0, 880, 620).contains(rect), (name, rect)


def test_service_followup_notice_is_not_a_human_message_and_preserves_original_payload(view):
    workspace, _ = view
    runs(workspace)
    payload = {"source": "team_followup", "team_id": "team",
               "message": "Team result available; Kira is continuing the original conversation.",
               "content": "Recorded Team result payload"}
    emit(workspace, "backend.notice", payload)
    row = workspace.transcript.rows[-1]
    assert row["kind"] == "notice" and row["title"] == "AVVISO"
    assert row["body"] == "Kira sta preparando la risposta sui risultati del Team"
    assert '"Recorded Team result payload"' in row["raw"]
    assert not any(r["kind"] == "user" for r in workspace.transcript.rows)


@pytest.mark.parametrize("lane", ["team_conversation", "team_followup"])
def test_related_team_run_survives_project_refresh_and_gui_reopen(ui, qtbot, monkeypatch, lane):
    workspace, calls, _, _ = ui
    project, session = deepcopy(workspace._project), deepcopy(workspace._session)
    workspace.setDraftText("Preserve this draft across lane replay")
    workspace.flushDrafts()
    run = {"id": "ordinary-lane", "project_id": project["id"], "session_id": session["id"],
           "state": "running", "created_at": "2026-01-01T12:00:00Z", "request_id": "lane-request",
           "execution_lane": lane, "related_team_id": "team", "effective_permission": "read_only",
           "source": "team_followup" if lane == "team_followup" else "user"}
    member = {"id": "managed-member", "session_id": "member-chat", "team_id": "team", "state": "running"}

    def reload(target, recorded):
        target.openProject(project["id"])
        wb.request(recorded, "project.snapshot")[2]({"project": project, "sessions": [session], "runs": [member, run]})
        assert "ordinary-lane" in target._runs and "managed-member" not in target._runs
        wb.request(recorded, "session.snapshot")[2]({"session": session, "runs": [run], "artifacts": []})
        wb.request(recorded, "run.subscribe")[2]({"events": [], "has_more": False, "last_seq": 0})
        assert not target._chat_loading
        assert target.selectedActiveRunId == target.activeRunId == "ordinary-lane"
        assert target.workbench.canSteer and target.activityCanInspect
        assert target._runs["ordinary-lane"]["related_team_id"] == "team"
        assert target.draftText == "Preserve this draft across lane replay"

    reload(workspace, calls)
    reopened = Workspace(workspace.paths)
    recorded = []
    monkeypatch.setattr(reopened.client, "request", lambda *a, **k: recorded.append(a))
    reopened._connected_value = True
    reopened._catalogs = deepcopy(workspace._catalogs)
    second_engine = wb.create_engine(reopened)
    second = second_engine.rootObjects()[0]
    try:
        reload(reopened, recorded)
        qtbot.wait(40)
        assert second.findChild(QObject, "sendButton").property("text") == "Invia indicazioni"
        assert second.findChild(QObject, "sendButton").isEnabled()
        assert wb.item(second.contentItem(), "prompt").property("text") == "Preserve this draft across lane replay"
        assert (reopened.selectedModel, reopened.selectedEffort) == ("explicit-model", "ultra")
    finally:
        second.setProperty("closeConfirmed", True)
        second.close()
        second_engine.deleteLater()
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("prompt,allowed", [("a" * 32_000, True), ("a" * 32_001, False),
    ("\n" * 15_999 + "x", True), ("\n" * 16_000 + "x", False),
    ("è" * 16_000, True), ("è" * 16_001, False)])
def test_guidance_gui_bound_matches_service_json_escaping(view, prompt, allowed):
    workspace, calls = view
    runs(workspace)
    workspace.setDraftText(prompt)
    workspace.workbench.steer(prompt)
    assert bool([row for row in calls if row[0] == "run.steer"]) is allowed
    if not allowed:
        assert "32.000 byte" in workspace.workbench.error
        assert workspace.draftText == prompt and not workspace._pending_intents


def test_manual_team_start_schedules_scoped_snapshot_after_parent_followup_settled(view, qtbot):
    workspace, calls = view
    parent = {"id": "parent-delegation", "project_id": "project", "session_id": "chat",
              "team_id": "parent-team", "state": "paused", "goal": "Keep the original result",
              "cleanup_confirmed": True}
    parent_followup = {"automatic": True, "source": "team_followup", "team_id": "parent-team",
                       "state": "completed", "run_id": "", "request_id": "parent-receipt", "error": ""}
    workspace.assistant.refresh()
    wb.request(calls, "assistant.snapshot")[2]({"environment": {"active_teams": []},
        "delegations": [parent], "followups": [parent_followup]})
    assert not workspace.assistant.active and not workspace.assistant._timer.isActive()
    calls.clear()
    workspace.teams.teamStarted.emit()
    workspace.teams.teamStarted.emit()
    qtbot.waitUntil(lambda: bool([call for call in calls if call[0] == "assistant.snapshot"]), timeout=1000)
    reads = [call for call in calls if call[0] == "assistant.snapshot"]
    assert len(reads) == 1 and reads[0][1] == {"project_id": "project", "session_id": "chat"}
    child_followup = {**parent_followup, "team_id": "manual-child", "request_id": "child-receipt"}
    reads[0][2]({"environment": {"active_teams": []}, "delegations": [parent],
                 "followups": [child_followup, parent_followup]})
    assert workspace.assistant.current["delegation_id"] == "parent-delegation"
    assert workspace.assistant.current["goal"] == "Keep the original result"
    assert workspace.assistant.followup["team_id"] == "manual-child"
    assert not operational(calls)
