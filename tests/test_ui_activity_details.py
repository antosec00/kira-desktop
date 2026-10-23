"""Compact activity reads only the selected chat's durable, literal run metadata."""

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import MAX_ACTIVITY_DETAILS_CHARS, Workspace, run_state_presentation


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    view._project = {"id": "project", "name": "Synthetic activity", "root": str(tmp_path)}
    view._session = {"id": "chat", "project_id": "project", "backend": "codex", "model": "chosen-model",
                     "settings": {"reasoning_effort": "xhigh"}}
    view._backend, view._model, view._effort = "codex", "chosen-model", "xhigh"
    view._connected_value = True
    view._drafts[("project", "chat")] = "Synthetic unsent draft"
    requests = []
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: requests.append(args))
    yield view, requests
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def run(**updates):
    return {"id": "run", "project_id": "project", "session_id": "chat", "request_id": "client-request",
            "prompt": "Synthetic request", "state": "running", "created_at": "2026-10-16T10:00:00Z",
            "updated_at": "2026-10-16T10:00:05Z", "error": None, "outcome": {}, **updates}


def test_selected_activity_prefers_own_active_run_and_never_exposes_foreign_details(workspace):
    view, requests = workspace
    assert not view.activityCanInspect and not view.activityDetails and not view.activityStartedAt
    view._runs = {"run": run(),
                  "old": run(id="old", state="completed", created_at="2026-10-16T09:00:00Z"),
                  "foreign": run(id="foreign", session_id="foreign-chat", prompt="Foreign chat text",
                                 created_at="2026-10-16T11:00:00Z"),
                  "foreign-project": run(id="foreign-project", project_id="foreign-project",
                                         prompt="Foreign project text", created_at="2026-10-16T12:00:00Z"),
                  "team": run(id="team", team_id="team", prompt="Team text", created_at="2026-10-16T13:00:00Z")}
    original = copy.deepcopy(view._runs)
    assert view.activityCanInspect
    details = json.loads(view.activityDetails)
    assert details["execution_id"] == "run" and details["request"] == "Synthetic request"
    assert "Foreign" not in view.activityDetails and "Team text" not in view.activityDetails
    assert view.activityStartedAt == "2026-10-16T10:00:00.000+00:00"
    assert view._runs == original and not requests
    view._runs["run"]["state"] = "completed"
    assert view.activityCanInspect and not view.activityStartedAt
    assert json.loads(view.activityDetails)["state"] == "completed"
    view._session["id"] = "empty-chat"
    assert not view.activityCanInspect and not view.activityDetails and not view.activityStartedAt


def test_active_execution_wins_over_terminal_insertion_or_timestamp_order(workspace):
    view, _ = workspace
    view._runs = {"run": run(),
                  "terminal": run(id="terminal", state="completed", created_at="2026-10-16T11:00:00Z")}
    assert json.loads(view.activityDetails)["execution_id"] == "run"
    assert view.activityStartedAt == "2026-10-16T10:00:00.000+00:00"
    view._runs["run"]["state"] = "completed"
    assert json.loads(view.activityDetails)["execution_id"] == "terminal"
    assert not view.activityStartedAt


@pytest.mark.parametrize("context", ["no-project", "no-chat", "foreign-project", "team-chat", "loading", "pending"])
def test_unselected_or_changing_context_has_no_stale_activity_details(workspace, context):
    view, requests = workspace
    view._runs = {"run": run()}
    if context == "no-project":
        view._project = {}
    elif context == "no-chat":
        view._session = {}
    elif context == "foreign-project":
        view._session["project_id"] = "other-project"
    elif context == "team-chat":
        view._session["team_id"] = "team"
    elif context == "loading":
        view._chat_loading = True
    elif context == "pending":
        view._pending_start = {"session_id": "chat", "prompt": "Not admitted yet"}
    assert not view.activityCanInspect and not view.activityDetails and not view.activityStartedAt
    assert not requests


@pytest.mark.parametrize("state", ["queued", "running", "awaiting_input", "cancelling"])
def test_elapsed_start_uses_the_actual_active_run_not_connection_or_event_time(workspace, state):
    view, _ = workspace
    view._runs = {"run": run(state=state, created_at="2026-10-16T12:00:00.123456+02:00")}
    assert view.activityStartedAt == "2026-10-16T10:00:00.123+00:00"
    view._runs["run"]["updated_at"] = "2026-10-16T19:00:00Z"
    assert view.activityStartedAt == "2026-10-16T10:00:00.123+00:00"


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled", "interrupted", "unknown", ""])
def test_terminal_or_unknown_state_has_details_without_running_elapsed_time(workspace, state):
    view, _ = workspace
    view._runs = {"run": run(state=state)}
    assert view.activityCanInspect and not view.activityStartedAt
    assert json.loads(view.activityDetails)["state"] == state


@pytest.mark.parametrize("timestamp", [None, "", 42, True, [], {}, "synthetic", "2026-10-16",
                                       "2026-10-16T10:00:00", "2026-02-30T10:00:00Z",
                                       "2026-10-16T10:00:00+25:00", "x" * 1000])
def test_unavailable_or_invalid_start_never_falls_back_to_a_later_timestamp(workspace, timestamp):
    view, _ = workspace
    view._runs = {"run": run(created_at=timestamp)}
    assert not view.activityStartedAt
    assert json.loads(view.activityDetails)["started_at"] == timestamp


def test_details_preserve_literal_outcome_and_prompt_without_data_or_provider_changes(workspace):
    view, requests = workspace
    literal = '<img src="https://example.invalid/pixel"> **literal**\nà %1 {name}'
    outcome = {"http_status": 503, "request_id": "provider-request", "code": "synthetic-code",
               "content": literal, "confirmed": False, "nested": [None, 0, True]}
    view._runs = {"run": run(state="interrupted", prompt=literal, error=literal, outcome=outcome)}
    original = copy.deepcopy((view._runs, view._session, view._drafts, view._settings))
    details = view.activityDetails
    reference = run_state_presentation(view._runs["run"], {
        "run_id": "run", "timestamp": view._runs["run"]["updated_at"],
        "payload": {"state": "interrupted", "error": literal, "outcome": outcome}})["raw"]
    assert details == reference
    parsed = json.loads(details)
    assert parsed["outcome"] == outcome and parsed["request"] == literal and parsed["error"] == literal
    assert parsed["execution_id"] == "run" and parsed["request_id"] == "client-request"
    assert (view._runs, view._session, view._drafts, view._settings) == original
    assert not requests and not view.paths.db.exists()


def test_oversized_details_are_explicitly_bounded_without_changing_stored_metadata(workspace):
    view, requests = workspace
    prompt = 'Synthetic literal <b>text</b> "quoted"\n' * 5000
    view._runs = {"run": run(prompt=prompt)}
    original = copy.deepcopy(view._runs)
    details = view.activityDetails
    assert len(details) == MAX_ACTIVITY_DETAILS_CHARS
    assert details.startswith('{\n  "execution_id": "run",')
    assert 'Synthetic literal <b>text</b> \\"quoted\\"\\n' in details
    assert details.endswith("Esporta la chat per consultare la cronologia completa.")
    assert view._runs == original and not requests


def test_live_and_replayed_state_keep_exact_event_payload_and_sequence(workspace):
    view, requests = workspace
    view._runs = {"run": run()}
    event = {"run_id": "run", "seq": 1, "type": "run.state", "timestamp": "2026-10-16T10:01:00Z",
             "payload": {"state": "interrupted", "error": "Synthetic failure",
                         "outcome": {"state": "unconfirmed", "request_id": "provider-request"}}}
    original = copy.deepcopy(event)
    observed = []
    view.eventApplied.connect(lambda value: observed.append(copy.deepcopy(value)))
    view._event(event)
    details = view.activityDetails
    parsed = json.loads(details)
    assert parsed["outcome"] == event["payload"]["outcome"]
    assert parsed["error"] == event["payload"]["error"]
    assert parsed["event_timestamp"] == event["timestamp"]
    assert not view.activityStartedAt and view.activityCanInspect
    assert view.runFailure["visible"] and view.runFailure["details"] == details
    view._event(copy.deepcopy(event))
    assert event == original and observed == [original] and view._cursors == {"run": 1}
    assert view.activityDetails == details and view.transcript.rows == [] and not requests
