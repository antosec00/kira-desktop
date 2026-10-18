"""Autonomous-goal GUI intents and real QML: synthetic RPCs, no provider calls."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest

from kira.ui.controller import Workspace
from test_ui_workbench import evaluate, item, request, ui as ui, view as view


def goal_result(status="active", **extra):
    return {"goal": {"objective": "Produce the requested deliverable", "status": "active"},
            "autonomous": {"id": "goal", "status": status, "cycle_count": 3,
                                    "max_cycles": 0, **extra}}


@pytest.mark.parametrize("alias", ["/obiettivo", "/obbiettivo", "/goal"])
def test_slash_goal_starts_durable_unlimited_service_goal_without_fake_chat(view, alias):
    workspace, calls = view
    original_session = deepcopy(workspace._session)
    text = alias + " Produce the requested deliverable"
    workspace.setDraftText(text)
    workspace.workbench.submit(text)
    command = request(calls, "goal.start")
    assert command[1]["objective"] == "Produce the requested deliverable"
    assert command[1]["max_cycles"] == 0
    assert workspace._pending_intents[0]["request_id"] == command[1]["request_id"]
    command[2](goal_result())
    assert not workspace._pending_intents and workspace.draftText == ""
    assert workspace.workbench.autonomousGoal["status"] == "active"
    assert workspace._session == original_session and not workspace._runs
    assert not any(call[0] in {"run.start", "run.steer", "queue.add"} for call in calls)


def test_uncertain_goal_retry_identity_survives_restart_and_preserves_new_draft(view):
    workspace, calls = view
    text = "/obiettivo Create a deliverable"
    workspace.setDraftText(text)
    workspace.workbench.submit(text)
    first = request(calls, "goal.start")
    first[3]({"code": "DISCONNECTED", "message": "Synthetic unknown delivery"})
    reopened = Workspace(workspace.paths)
    try:
        assert reopened._pending_intents[0]["request_id"] == first[1]["request_id"]
    finally:
        reopened.close()
        reopened.deleteLater()
    workspace.workbench.submit(text)
    retry = request(calls, "goal.start")
    assert retry[1] == first[1]
    workspace.setDraftText("New text while waiting")
    retry[2](goal_result())
    assert workspace.draftText == "New text while waiting"


def test_goal_identity_save_failure_has_no_effect(view, monkeypatch):
    workspace, calls = view
    monkeypatch.setattr(workspace, "flushDrafts", lambda: None)
    workspace.workbench.startAutonomousGoal("Deliver the result")
    assert not any(call[0] == "goal.start" for call in calls)
    assert workspace.workbench.error


@pytest.mark.parametrize("command,method", [("pausa", "goal.pause"), ("riprendi", "goal.resume")])
def test_controls_use_scoped_service_api_even_while_busy(view, command, method):
    workspace, calls = view
    workspace._runs = {"busy": {"id": "busy", "session_id": "chat", "state": "running"}}
    workspace._catalogs["codex"]["capabilities"]["steering"] = False
    workspace.changed.emit()
    workspace.setDraftText("/obiettivo " + command)
    assert workspace.workbench.goalCommandReady(workspace.draftText)
    workspace.workbench.submit(workspace.draftText)
    assert request(calls, method)[1]["session_id"] == "chat"
    assert not any(call[0] in {"run.steer", "run.cancel", "run.start"} for call in calls)


@pytest.mark.parametrize("suffix", ["", " stato"])
def test_goal_status_opens_panel_without_starting_anything(view, suffix):
    workspace, calls = view
    sections = []
    workspace.workbench.showPanel.connect(sections.append)
    workspace.setDraftText("/goal" + suffix)
    workspace.workbench.submit(workspace.draftText)
    assert sections == ["goal"] and workspace.draftText == ""
    assert not any(call[0] in {"goal.start", "goal.resume", "run.start"} for call in calls)


def test_noncommand_literal_is_ordinary_message(view):
    workspace, calls = view
    text = "Describe /obiettivo in prose"
    workspace.setDraftText(text)
    workspace.workbench.submit(text)
    assert request(calls, "run.start")[1]["prompt"] == text
    assert not any(call[0] in {"goal.start", "goal.pause", "goal.resume"} for call in calls)


def test_legacy_goal_is_never_armed_and_multiline_anchor_is_bounded(view):
    workspace, calls = view
    objective = "Paragraph\n" * 100
    workspace.workbench._set_goal({"goal": {"objective": objective, "status": "active"}})
    assert not workspace.workbench.autonomousGoal
    assert not workspace.workbench._goal_poll_timer.isActive()
    assert len(workspace.workbench.goalPreview) <= 300 and "\n" not in workspace.workbench.goalPreview
    assert not any(call[0] in {"goal.start", "goal.resume", "run.start"} for call in calls)


def test_poll_adopts_actual_goal_run_once_without_draft_or_settings_mutation(view):
    workspace, calls = view
    workspace.setDraftText("Keep this while the goal works")
    before = deepcopy(workspace._session)
    run = {"id": "cycle", "session_id": "chat", "project_id": "project", "state": "running",
           "created_at": "2026-01-02", "source": "goal_continuation"}
    result = goal_result(last_run_id="cycle", run=run)
    workspace.workbench._set_goal(result)
    workspace.workbench._set_goal(result)
    assert workspace._runs["cycle"] == run
    assert len([call for call in calls if call[0] == "run.subscribe"]) == 1
    assert workspace.draftText == "Keep this while the goal works" and workspace._session == before
    assert workspace.workbench._goal_poll_timer.isActive()
    assert not workspace.transcript.rows


def test_goal_poll_does_not_adopt_another_chat_or_unconfirmed_run(view):
    workspace, calls = view
    for run in ({"id": "wrong", "session_id": "another", "source": "goal_continuation"},
                {"id": "wrong", "session_id": "chat", "source": "ordinary"}):
        workspace.workbench._set_goal(goal_result(last_run_id="wrong", run=run))
    assert not workspace._runs
    assert not any(call[0] == "run.subscribe" for call in calls)


def test_goal_state_remains_visible_after_cycle_completed(view):
    workspace, _ = view
    workspace._runs = {"cycle": {"id": "cycle", "session_id": "chat", "state": "completed"}}
    workspace.workbench._set_goal(goal_result(summary="A literal progress note", next_step="Next action"))
    assert workspace.workbench.activityLabel == "Obiettivo autonomo attivo"
    assert workspace.workbench.activityOutcome == "running"
    assert "A literal progress note" in workspace.workbench.goalProgress
    workspace.workbench._set_goal(goal_result("interrupted", error="Recorded diagnostic"))
    assert workspace.workbench.activityOutcome == "unconfirmed"
    assert workspace.workbench.goalStopReason == "Recorded diagnostic"
    assert not workspace.workbench._goal_poll_timer.isActive()


def test_goal_command_creates_new_chat_with_configured_model(view):
    workspace, calls = view
    workspace._session = {}
    workspace.changed.emit()
    workspace.setDraftText("/obiettivo New goal")
    workspace.workbench.submit(workspace.draftText)
    create = request(calls, "session.create")
    assert create[1]["model"] == "explicit-model"
    assert create[1]["settings"]["reasoning_effort"] == "ultra"
    create[2]({"session": {"id": "new", "project_id": "project", "backend": "codex",
                           "model": "explicit-model", "settings": {"reasoning_effort": "ultra"}}})
    assert request(calls, "goal.start")[1]["session_id"] == "new"
    assert not any(call[0] in {"run.start", "queue.add"} for call in calls)


def test_real_composer_can_pause_without_provider_steering(ui):
    workspace, calls, _, window = ui
    workspace._runs = {"busy": {"id": "busy", "session_id": "chat", "state": "running"}}
    workspace._catalogs["codex"]["capabilities"]["steering"] = False
    workspace.workbench._set_goal(goal_result())
    workspace.changed.emit()
    prompt = item(window.contentItem(), "prompt")
    prompt.setProperty("text", "/obiettivo pausa")
    evaluate(prompt, "forceActiveFocus()")
    assert item(window.contentItem(), "sendButton").property("enabled")
    QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert request(calls, "goal.pause")
    assert not any(call[0] in {"run.steer", "run.cancel"} for call in calls)


def test_goal_panel_localizes_controls_and_keeps_pause_enabled_during_work(ui, qtbot):
    workspace, calls, engine, window = ui
    workspace._runs = {"busy": {"id": "busy", "session_id": "chat", "state": "running"}}
    workspace.workbench._set_goal(goal_result(summary="Literal provider summary"))
    workspace.changed.emit()
    panel = window.findChild(QObject, "workbenchDialog")
    evaluate(panel, 'openSection("goal")')
    request(calls, "goal.get")[2](goal_result(summary="Literal provider summary"))
    engine.rootContext().contextProperty("localization").setLanguage("en")
    qtbot.wait(30)
    assert window.findChild(QObject, "pauseAutonomousGoalButton").property("enabled")
    assert window.findChild(QObject, "autonomousGoalStatus").property("text") == "Autonomous goal active"
    assert "Literal provider summary" in window.findChild(QObject, "autonomousGoalProgress").property("text")
    assert not window.findChild(QObject, "continueGoalButton").property("visible")


def test_goal_ack_after_chat_switch_settles_only_original_draft(view):
    workspace, calls = view
    text = "/obiettivo Produce the requested deliverable"
    workspace.setDraftText(text)
    workspace.workbench.submit(text)
    submitted = request(calls, "goal.start")
    workspace._session = {**workspace._session, "id": "other"}
    workspace.changed.emit()
    workspace.setDraftText("Draft in another chat")
    submitted[2](goal_result())
    assert workspace.draftText == "Draft in another chat"
    assert not workspace.workbench.goal and not workspace._pending_intents
    assert ("project", "chat") not in workspace._drafts


def test_poll_failure_preserves_goal_and_does_not_restart_work(view):
    workspace, calls = view
    workbench = workspace.workbench
    workbench._set_goal(goal_result(summary="Known progress"))
    workbench._read_goal_anchor()
    pending = request(calls, "goal.get")
    pending[3]({"code": "DISCONNECTED", "message": "Synthetic disconnected read"})
    assert workbench.autonomousGoal["summary"] == "Known progress"
    assert workbench._goal_poll_timer.isActive()
    assert not any(call[0] in {"goal.start", "goal.resume", "run.start"} for call in calls)


def test_fast_cycles_are_all_subscribed_in_order_and_keep_latest_user_turn(view):
    workspace, calls = view
    workspace._runs = {"user": {"id": "user", "session_id": "chat", "state": "completed", "created_at": "2026-01-02"}}
    runs = [{"id": str(index), "session_id": "chat", "state": "completed", "goal_id": "goal",
             "source": "goal_continuation", "created_at": f"2026-01-0{index}"} for index in (4, 1, 3)]
    workspace.workbench._set_goal(goal_result(runs=runs, last_run_id="4"))
    subscriptions = [call[1]["run_id"] for call in calls if call[0] == "run.subscribe"]
    assert subscriptions == ["3", "4"]
    assert "1" not in workspace._runs


def test_large_poll_gap_reads_one_snapshot_without_resetting_composer_or_chat(view):
    workspace, calls = view
    workspace.setDraftText("An unfinished draft")
    run = {"id": "40", "session_id": "chat", "state": "completed", "goal_id": "goal",
           "source": "goal_continuation", "created_at": "2026-01-03"}
    workspace.workbench._set_goal(goal_result(runs=[run], runs_truncated=True, last_run_id="40"))
    workspace.workbench._set_goal(goal_result(runs=[run], runs_truncated=True, last_run_id="40"))
    reads = [call for call in calls if call[0] == "session.snapshot"]
    assert len(reads) == 1 and not workspace.chatLoading
    reads[0][2]({"session": {"id": "chat"}, "runs": [{**run, "id": "39", "created_at": "2026-01-02"}, run]})
    assert [call[1]["run_id"] for call in calls if call[0] == "run.subscribe"] == ["39", "40"]
    assert workspace.sessionId == "chat" and workspace.draftText == "An unfinished draft"
