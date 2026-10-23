"""Browser ownership pauses are visible, replayable and isolated to their chat."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.ui.controller import Workspace
from test_ui_activity_details import run
import test_ui_activity_details
from test_ui_tool_group_layout import control
import test_ui_tool_group_layout

workspace = test_ui_activity_details.workspace
group_ui = test_ui_tool_group_layout.group_ui


def event(seq, kind, payload, run_id="run"):
    return {"schema_version": 1, "run_id": run_id, "seq": seq, "type": kind,
            "timestamp": "2026-10-16T10:00:05Z", "payload": payload}


def history(name="browser.tabs", run_id="run"):
    return [event(1, "run.state", {"state": "running"}, run_id),
            event(2, "tool.started", {"tool_id": "call", "name": name, "arguments": {}}, run_id),
            event(3, "tool.progress", {"tool_id": "call", "name": name,
                  "phase": "waiting_for_browser_control", "reason": "manual_control"}, run_id)]


def apply(view, events):
    for item in events:
        view._event(item)


@pytest.mark.parametrize("name", ["browser.tabs", "computer.observe", "computer.act"])
def test_wait_label_stops_work_animation_and_preserves_request_draft_and_result(workspace, name):
    view, requests = workspace
    view._runs = {"run": run()}
    before = deepcopy((view._session, view._project, view._drafts))
    original = history(name)
    apply(view, original)
    assert view.browserControlWaiting
    assert view.activityLabel == "In attesa del browser · controllo tuo"
    assert not view.activityAnimating and view.activeRunId == "run" and view.busy
    assert view._runs["run"]["state"] == "running"
    assert "Restituisci il browser a Kira" in view.activityDetails
    assert '"waiting_tools": [\n    "call"' in view.activityDetails
    row = view.transcript.rows[0]
    assert row["status"] == "in attesa del browser" and row["toolGroupStatus"] == "waiting"
    assert not row["toolGroupAttention"] and row["body"] == "{}"
    assert "waiting_for_browser_control" in row["raw"]
    assert (view._session, view._project, view._drafts) == before
    assert original == history(name) and not requests

    resume = {**original[-1]["payload"], "phase": "resuming"}
    view._event(event(4, "tool.progress", resume))
    assert not view.browserControlWaiting and view.activityAnimating
    assert view.activityLabel == "Eseguo strumenti…"
    assert row["status"] == "in esecuzione" and row["toolGroupStatus"] == "running"
    assert "waiting_tools" not in view.activityDetails
    view._event(event(5, "tool.completed", {"tool_id": "call", "name": name, "result": {"ok": True, "text": "Synthetic result"}}))
    assert view.activityLabel == "Sto pensando…"
    assert row["status"] == "confermato" and row["toolGroupStatus"] == "completed"
    assert "Synthetic result" in row["body"]
    assert (view._session, view._project, view._drafts) == before and not requests


@pytest.mark.parametrize("ending", ["completed", "failed", "cancelled", "interrupted"])
def test_terminal_state_clears_pause_and_retains_unconfirmed_tool_outcome(workspace, ending):
    view, requests = workspace
    view._runs = {"run": run()}
    apply(view, history())
    view._event(event(4, "run.state", {"state": ending}))
    assert not view.browserControlWaiting and not view.activityAnimating
    assert "run" not in view._waiting_browser_tools and "run" not in view._active_tools
    assert view.transcript.rows[0]["status"] == "esito non confermato"
    assert view.transcript.rows[0]["toolGroupStatus"] == "unconfirmed"
    assert "waiting_tools" not in view.activityDetails and not requests


def test_tool_completion_clears_wait_even_without_resume_event(workspace):
    view, _ = workspace
    view._runs = {"run": run()}
    apply(view, history())
    view._event(event(4, "tool.completed", {"tool_id": "call", "name": "browser.tabs",
        "result": {"ok": False, "error": {"code": "BROWSER_USER_CLOSED", "message": "Synthetic browser closed"}}}))
    assert not view.browserControlWaiting
    assert view.transcript.rows[0]["toolGroupStatus"] == "error"
    assert view.activityLabel == "Sto pensando…"
    # A stale progress packet cannot turn a settled tool into a pending action.
    view._event(event(5, "tool.progress", history()[-1]["payload"]))
    assert not view.browserControlWaiting and view.transcript.rows[0]["status"] == "errore"


@pytest.mark.parametrize("changes", [
    {"tool_id": "unknown"}, {"name": "computer.observe"}, {"name": "run_command"},
    {"phase": "running"}, {"phase": []}, {"phase": {}},
    {"reason": "provider_reasoning"}, {"tool_id": None},
])
def test_unknown_or_mismatched_progress_cannot_pause_the_visible_tool(workspace, changes):
    view, _ = workspace
    view._runs = {"run": run()}
    rows = history()
    apply(view, rows[:2])
    view._event(event(3, "tool.progress", {**rows[-1]["payload"], **changes}))
    assert not view.browserControlWaiting and view.activityAnimating
    assert view.activityLabel == "Eseguo strumenti…"
    assert view.transcript.rows[0]["toolGroupStatus"] == "running"


def test_foreign_chat_progress_is_not_displayed_or_inherited_after_switch(workspace):
    view, _ = workspace
    view._runs = {"run": run(), "foreign": run(id="foreign", session_id="other-chat")}
    apply(view, history(run_id="foreign"))
    assert not view.browserControlWaiting and not view._waiting_browser_tools
    assert not view.transcript.rows
    assert "manual_control" not in view.activityDetails
    view._runs["foreign"]["state"] = "completed"
    apply(view, history())
    assert view.browserControlWaiting
    view._session = {**view._session, "id": "other-chat"}
    view._reset_chat()
    assert not view.browserControlWaiting and not view._waiting_browser_tools
    assert not view.transcript.rows
    view._session = {**view._session, "id": "chat"}
    view._reset_chat()
    apply(view, history())
    assert view.browserControlWaiting and view.transcript.rows[0]["toolGroupStatus"] == "waiting"


def test_reopening_and_ordered_replay_restore_wait_without_duplicate_rows(workspace, monkeypatch):
    view, requests = workspace
    view._runs = {"run": run()}
    rows = history()
    apply(view, rows)
    original = deepcopy(view.transcript.rows)
    reopened = Workspace(view.paths)
    try:
        monkeypatch.setattr(reopened.client, "request", lambda *args, **kwargs: requests.append(args))
        reopened._project = deepcopy(view._project)
        reopened._session = deepcopy(view._session)
        reopened._runs = {"run": run()}
        reopened._connected_value = True
        reopened.transcript.begin_batch()
        apply(reopened, rows)
        reopened.transcript.end_batch()
        assert reopened.browserControlWaiting and not reopened.activityAnimating
        assert reopened.transcript.rows == original
        apply(reopened, rows)
        assert reopened.transcript.rows == original and reopened._cursors["run"] == 3
        assert not requests
    finally:
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_multiple_tools_keep_wait_until_the_last_waiting_call_resumes(workspace):
    view, _ = workspace
    view._runs = {"run": run()}
    apply(view, history())
    other = {"tool_id": "second", "name": "computer.observe"}
    view._event(event(4, "tool.started", {**other, "arguments": {}}))
    view._event(event(5, "tool.progress", {**other, "reason": "manual_control", "phase": "waiting_for_browser_control"}))
    view._event(event(6, "tool.progress", {**history()[-1]["payload"], "phase": "resuming"}))
    assert view.browserControlWaiting and not view.activityAnimating
    assert all(row["toolGroupStatus"] == "waiting" for row in view.transcript.rows)
    view._event(event(7, "tool.progress", {**other, "reason": "manual_control", "phase": "resuming"}))
    assert not view.browserControlWaiting and view.activityAnimating
    assert all(row["toolGroupStatus"] == "running" for row in view.transcript.rows)


def test_qml_collapsed_group_shows_wait_and_keeps_original_data_available(group_ui, qtbot):
    view, window, _ = group_ui
    view._session = {"id": "chat", "project_id": view._project["id"]}
    view._runs = {"run": run(project_id=view._project["id"])}
    view._connected_value = True
    apply(view, history())
    # This fixture does not connect a service; suppress its unrelated initial
    # assistant snapshot scheduled when the synthetic chat becomes connected.
    view.assistant._coalesce.stop()
    qtbot.wait(70)
    header = control(window, "toolGroupToggle", 0)
    assert header.isVisible() and not header.property("expanded")
    assert header.property("outcome") == "waiting"
    assert "in attesa del browser" in header.property("text")
    assert "in corso" not in header.property("text")
    assert view.browserControlWaiting and not view.activityAnimating
    view.transcript.setToolGroupExpanded(0, True)
    qtbot.wait(40)
    assert control(window, "messageStatus", 0).property("text") == "in attesa del browser"
    assert control(window, "messageText", 0).property("text") == "{}"
    view._event(event(4, "tool.progress", {**history()[-1]["payload"], "phase": "resuming"}))
    qtbot.wait(40)
    assert header.property("outcome") == "running" and "in corso" in header.property("text")
