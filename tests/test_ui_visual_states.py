"""Status colour metadata uses observed evidence without changing execution."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.models import TranscriptModel


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "root": str(tmp_path), "permission": "read_only"}
    workspace._session = {"id": "chat", "project_id": "project"}
    workspace._sessions = [workspace._session]
    workspace._connected_value = True
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: requests.append(args))
    yield workspace, requests
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def run(**changes):
    return {"id": "run", "session_id": "chat", "project_id": "project", "state": "running",
            "created_at": "2026-01-01T10:00:00Z", **changes}


def emit(view, kind, payload, run_id="run"):
    view._event({"run_id": run_id, "seq": view._cursors.get(run_id, 0) + 1,
                 "type": kind, "timestamp": "2026-01-01T10:00:10Z", "payload": payload})


@pytest.mark.parametrize("state,expected", [
    ("queued", "queued"), ("running", "running"), ("awaiting_input", "waiting"),
    ("cancelling", "waiting"), ("cancelled", "cancelled"), ("completed", "completed"),
    ("failed", "error"), ("interrupted", "unconfirmed"), ("", "unknown"),
    ("unrecognized", "unknown"), (None, "unknown"),
])
def test_recorded_run_state_drives_visual_outcome_without_mutations_or_rpc(view, state, expected):
    workspace, requests = view
    workspace._runs = {"run": run(state=state)}
    before = deepcopy((workspace._runs, workspace._session, workspace._project))
    assert workspace.activityOutcome == expected
    assert workspace.property("activityOutcome") == expected
    assert workspace.activityAnimating is (state == "running")
    assert (workspace._runs, workspace._session, workspace._project) == before
    assert not requests


@pytest.mark.parametrize("context", ["disconnected", "loading", "replay-failed"])
def test_unavailable_status_is_not_running_or_completed(view, context):
    workspace, requests = view
    workspace._runs = {"run": run()}
    if context == "disconnected":
        workspace._connected_value = False
    elif context == "loading":
        workspace._chat_loading = True
    else:
        workspace._chat_failed = True
    assert workspace.activityOutcome == "unknown"
    assert not workspace.activityAnimating and not workspace.chatStatusIndicators
    assert not requests


def test_local_send_is_queued_without_inventing_recorded_execution(view):
    workspace, requests = view
    assert workspace.activityOutcome == "idle"
    workspace._creating_session = True
    assert workspace.activityOutcome == "queued" and not workspace.chatStatusIndicators
    workspace._creating_session = False
    workspace._pending_start = {"session_id": "chat", "prompt": "Synthetic pending message"}
    assert workspace.activityOutcome == "queued" and not workspace.chatStatusIndicators
    assert not workspace._runs and not requests


def test_tool_failure_and_missing_receipt_override_completed_request_colour_only(view):
    workspace, requests = view
    workspace._runs = {"run": run()}
    emit(workspace, "tool.started", {"tool_id": "missing", "name": "read_file", "arguments": {}})
    emit(workspace, "run.state", {"state": "completed"})
    assert workspace.activityOutcome == "unconfirmed"
    assert workspace.transcript.rows[0]["receiptState"] == "unconfirmed"
    emit(workspace, "tool.completed", {"tool_id": "error", "name": "read_file", "result": {"ok": False}})
    assert workspace.activityOutcome == "error" and workspace.activityLabel == "Richiesta terminata"
    assert workspace._runs["run"]["state"] == "completed"
    assert workspace.chatStatusIndicators["chat"]["state"] == "error"
    emit(workspace, "tool.completed", {"tool_id": "error", "result": {"ok": True}})
    assert workspace.activityOutcome == "unconfirmed"
    emit(workspace, "tool.completed", {"tool_id": "missing", "result": {"ok": True}})
    assert workspace.activityOutcome == "completed" and not workspace.chatStatusIndicators
    assert not requests


def test_cancelled_request_keeps_unconfirmed_tool_amber_and_request_grey(view):
    workspace, requests = view
    workspace._runs = {"run": run()}
    emit(workspace, "tool.started", {"tool_id": "tool", "name": "write_file", "arguments": {}})
    emit(workspace, "run.state", {"state": "cancelled"})
    assert workspace.activityOutcome == "cancelled" and not workspace.activityAnimating
    assert workspace.transcript.rows[0]["receiptState"] == "unconfirmed"
    assert not workspace.chatStatusIndicators and not requests


def test_browser_and_approval_wait_are_observed_and_stop_activity_pulse(view):
    workspace, requests = view
    workspace._runs = {"run": run()}
    emit(workspace, "approval.requested", {"approval_id": "approval"})
    assert workspace.activityOutcome == "waiting" and not workspace.activityAnimating
    assert workspace.chatStatusIndicators["chat"] == {
        "state": "waiting", "label": "In attesa di autorizzazione"}
    emit(workspace, "backend.notice", {"approval_id": "approval", "decision": "allow", "message": "Approved"})
    emit(workspace, "tool.started", {"tool_id": "tool", "name": "browser.tabs", "arguments": {}})
    emit(workspace, "tool.progress", {"tool_id": "tool", "name": "browser.tabs",
        "phase": "waiting_for_browser_control", "reason": "manual_control"})
    assert workspace.activityOutcome == "waiting" and not workspace.activityAnimating
    assert workspace.chatStatusIndicators["chat"]["label"] == "In attesa del browser · controllo tuo"
    emit(workspace, "tool.progress", {"tool_id": "tool", "name": "browser.tabs",
        "phase": "resuming", "reason": "manual_control"})
    assert workspace.activityOutcome == "running" and workspace.activityAnimating
    assert workspace.chatStatusIndicators["chat"]["state"] == "running" and not requests


def test_other_chat_activity_is_in_sidebar_without_animating_selected_completion(view):
    workspace, requests = view
    workspace._sessions.append({"id": "other", "project_id": "project"})
    workspace._runs = {"run": run(state="completed"), "other-run": run(id="other-run", session_id="other")}
    assert workspace.activityLabel == "Altra chat in esecuzione"
    assert workspace.activityOutcome == "unknown" and not workspace.activityAnimating
    assert workspace.chatStatusIndicators == {"other": {"state": "running", "label": "In esecuzione"}}
    workspace._session = workspace._sessions[-1]
    assert workspace.activityOutcome == "running" and workspace.activityAnimating
    assert not requests


def test_sidebar_ignores_foreign_managed_unknown_archived_and_unbound_metadata(view):
    workspace, requests = view
    workspace._sessions.extend([
        {"id": "foreign", "project_id": "other-project"},
        {"id": "managed", "project_id": "project", "team_id": "team"},
        {"id": "archived", "project_id": "project", "archived": True},
        {"id": "unbound"}, {"id": "empty", "project_id": "project"},
    ])
    workspace._runs = {name: run(id=name, session_id=name) for name in
                       ("foreign", "managed", "archived", "unbound", "absent")}
    workspace._runs.update({"run": run(state="unknown"),
        "foreign-run": run(id="foreign-run", project_id="other-project"),
        "managed-run": run(id="managed-run", team_id="team")})
    assert not workspace.chatStatusIndicators
    assert workspace.activityOutcome == "unknown" and not workspace.activityAnimating
    workspace._runs = {"run": run(related_team_id="team")}
    assert workspace.chatStatusIndicators["chat"]["state"] == "running"
    assert workspace.activityOutcome == "running" and not requests


def test_latest_run_replaces_old_attention_and_selected_replay_stays_unknown(view):
    workspace, requests = view
    workspace._runs = {"run": run(state="failed")}
    assert workspace.chatStatusIndicators["chat"]["state"] == "error"
    workspace._runs["new"] = run(id="new", state="completed", created_at="2026-01-01T11:00:00Z")
    assert not workspace.chatStatusIndicators and workspace.activityOutcome == "completed"
    workspace._reset_chat()
    workspace._chat_loading = True
    workspace._replay_queue = ["new"]
    workspace.transcript.begin_batch()
    emit(workspace, "tool.completed", {"tool_id": "error", "result": {"ok": False}}, "new")
    assert not workspace.chatStatusIndicators and workspace.activityOutcome == "unknown"
    workspace._replay_queue.clear()
    workspace._finish_replay()
    assert workspace.chatStatusIndicators["chat"]["state"] == "error"
    assert workspace.activityOutcome == "error" and not requests


def test_stream_deltas_do_not_add_status_notifications_or_requests(view):
    workspace, requests = view
    workspace._runs = {"run": run()}
    changes = []
    workspace.changed.connect(lambda: changes.append(workspace.chatStatusIndicators))
    for _ in range(50):
        emit(workspace, "message.delta", {"item_id": "reply", "content": "Synthetic "})
    assert len(changes) == 1
    assert changes[0] == {"chat": {"state": "running", "label": "In esecuzione"}}
    assert not requests


@pytest.mark.parametrize("status,expected", [
    ("queued", "queued"), ("pending", "queued"), ("cancelled", "cancelled"),
    ("paused", "paused"), ("running", "running"), ("confermato", "completed"),
    ("errore", "error"), ("esito non confermato", "unconfirmed"), ("", "unknown"),
])
def test_receipt_and_group_keep_queued_cancelled_uncertain_and_success_distinct(qapp, status, expected):
    model = TranscriptModel()
    model.append(kind="tool", title="read_file", runId="run", toolId="tool", status=status)
    assert model.rows[0]["receiptState"] == expected
    assert model.rows[0]["toolGroupStatus"] == expected
    assert model.rows[0]["status"] == status
