"""A waiting Team/result must not inherit a previous request's green outcome."""

import pytest

from test_ui_workbench import view as view


def completed(workspace):
    workspace._runs = {"old": {"id": "old", "session_id": "chat", "project_id": "project",
                              "state": "completed", "created_at": "2026-01-01T12:00:00Z"}}


@pytest.mark.parametrize("source", ["team", "followup"])
def test_team_result_waiting_has_its_own_outcome_without_inference(view, source):
    workspace, calls = view
    completed(workspace)
    if source == "team":
        workspace.assistant._environment = {"active_teams": [{"id": "team", "state": "running"}]}
    else:
        workspace.assistant._followups = [{"team_id": "team", "automatic": True, "state": "pending"}]
    calls.clear()
    assert workspace.activityOutcome == "completed"
    assert workspace.workbench.activityOutcome == "waiting"
    assert not calls


def test_selected_conversation_work_remains_independent_of_a_paused_team(view):
    workspace, calls = view
    workspace._runs = {"active": {"id": "active", "session_id": "chat", "project_id": "project",
                                 "state": "running", "created_at": "2026-01-01T12:00:00Z"}}
    workspace.assistant._delegations = [{"id": "delegation", "team_id": "team", "state": "paused"}]
    calls.clear()
    assert workspace.workbench.activityOutcome == "running"
    assert workspace.assistant.current["state"] == "paused"
    assert not calls


def test_disconnected_or_other_chat_activity_does_not_reuse_selected_success(view):
    workspace, calls = view
    completed(workspace)
    workspace._runs["other"] = {"id": "other", "session_id": "other-chat", "project_id": "project",
                                 "state": "running", "created_at": "2026-01-01T13:00:00Z"}
    calls.clear()
    assert workspace.workbench.activityOutcome == "unknown"
    workspace._connected_value = False
    assert workspace.workbench.activityOutcome == "unknown"
    assert not calls
