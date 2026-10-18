"""Latest-request failures remain visible, scoped and literal across live/replay UI."""

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF
from PySide6.QtQml import QQmlEngine, QQmlExpression

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace, run_state_presentation
from kira.ui.run_errors import MAX_ERROR_PREVIEW, run_error_presentation


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    view._project = {"id": "project", "name": "Synthetic failures", "root": str(tmp_path), "permission": "read_only"}
    view._session = {"id": "chat", "project_id": "project", "backend": "codex", "model": "chosen-model",
                     "settings": {"reasoning_effort": "xhigh"}}
    view._backend, view._model, view._effort = "codex", "chosen-model", "xhigh"
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
            "updated_at": "2026-10-16T10:00:00Z", **updates}


def failure_event(**updates):
    return {"run_id": "run", "seq": 1, "type": "run.state", "timestamp": "2026-10-16T10:00:01Z",
            "payload": {"state": "failed", "error": "Synthetic provider request_failed HTTP 503",
                        "outcome": {"http_status": 503, "body_shape": "detail", "request_id": "provider-request"}},
            **updates}


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    result, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


def close_engine(engine, window):
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("language,ui_size", [("it", 13), ("en", 18)])
def test_real_qml_latest_failure_is_visible_at_minimum_width_and_has_literal_details(workspace, qtbot, qtlog, language, ui_size):
    view, requests = workspace
    view.setUiPreference("language", language)
    view.setUiPreference("appearance", {**view.uiPreferences["appearance"], "ui_size": ui_size})
    engine = create_engine(view)
    window = engine.rootObjects()[0]
    window.setWidth(880)
    window.setHeight(620)
    try:
        view._runs = {"run": run()}
        view.changed.emit()
        panel = window.findChild(QObject, "chatRunError")
        assert not panel.property("visible")
        original_session = copy.deepcopy(view._session)
        view._event(failure_event())
        qtbot.wait(50)
        assert panel.property("visible")
        assert window.findChild(QObject, "chatRunErrorTitle").property("text") == (
            "Richiesta non riuscita" if language == "it" else "Request failed")
        summary = window.findChild(QObject, "chatRunErrorSummary")
        assert "HTTP 503" in summary.property("text")
        assert ("per questa richiesta" if language == "it" else "for this request") in summary.property("text")
        cause = window.findChild(QObject, "chatRunErrorCause")
        assert cause.property("text") == failure_event()["payload"]["error"]
        assert evaluate(cause, "Number(textFormat)") == 0
        details = window.findChild(QObject, "chatRunErrorDetails")
        assert details.property("enabled")
        point = details.mapToItem(window.contentItem(), QPointF(0, 0))
        assert 0 <= point.x() and point.x() + details.property("width") <= window.width()
        assert 0 <= point.y() and point.y() + details.property("height") <= window.height()
        evaluate(details, "clicked()")
        assert window.findChild(QObject, "eventDialog").property("visible")
        content = window.findChild(QObject, "eventDetailsText")
        decoded = json.loads(content.property("text"))
        assert decoded["execution_id"] == "run" and decoded["request_id"] == "client-request"
        assert decoded["outcome"] == failure_event()["payload"]["outcome"]
        assert decoded["outcome"]["request_id"] == "provider-request"
        assert decoded["started_at"] and decoded["event_timestamp"]
        assert evaluate(content, "Number(textFormat)") == 0
        assert view._session == original_session and view.selectedModel == "chosen-model" and view.selectedEffort == "xhigh"
        assert view.draftText == "Synthetic unsent draft" and not requests
        assert not any(row["kind"] == "state" for row in view.transcript.rows)
        evaluate(window.findChild(QObject, "eventDialog"), "close()")
        view._notice = "Synthetic temporary notice"
        view.dismissNotice()
        assert panel.property("visible")
        assert not any("binding loop" in message.message.lower() or "TypeError" in message.message
                       or "ReferenceError" in message.message for message in qtlog.records)
    finally:
        close_engine(engine, window)


def test_failed_run_projection_is_scoped_and_newer_selected_outcomes_hide_old_failure(workspace):
    view, _ = workspace
    view._runs = {"run": run()}
    view._event(failure_event())
    assert view.runFailure["visible"]
    view._runs["foreign"] = run(id="foreign", session_id="other", state="failed", error="Foreign secret",
                                created_at="2026-10-16T11:00:00Z")
    view._runs["foreign-project"] = run(id="foreign-project", project_id="other", state="failed", error="Other project",
                                        created_at="2026-10-16T12:00:00Z")
    view._runs["team"] = run(id="team", team_id="team", state="failed", error="Team failure",
                             created_at="2026-10-16T13:00:00Z")
    assert view.runFailure["runId"] == "run" and "Foreign secret" not in view.runFailure["details"]
    for state in ("queued", "running", "awaiting_input", "completed", "cancelled"):
        view._runs["new"] = run(id="new", state=state, created_at="2026-10-16T14:00:00Z", error="Old error ignored")
        assert not view.runFailure["visible"]
    view._runs["new"].update(state="failed", error="New failure")
    assert view.runFailure["errorPreview"] == "New failure"
    view._session = {**view._session, "id": "empty-chat"}
    assert not view.runFailure["visible"]
    view._session = {**view._session, "id": "chat", "team_id": "team"}
    assert not view.runFailure["visible"]


def test_pending_new_request_hides_failure_until_its_admission_is_known(workspace):
    view, _ = workspace
    view._runs = {"run": run()}
    view._event(failure_event())
    view._pending_start = {"session_id": "chat", "prompt": "New explicit request"}
    assert not view.runFailure["visible"]
    view._pending_start = None
    assert view.runFailure["visible"]


def test_snapshot_and_replay_restore_full_error_and_outcome_without_extra_chat_cards(workspace):
    view, requests = workspace
    literal = '<img src="https://invalid.test/fetch"> **literal** ' + "synthetic detail " * 400
    outcome = {"http_status": 503, "request_id": "provider-request", "detail": {"literal": "<b>raw</b>"},
               "more": ["synthetic" * 1000]}
    view._runs = {"run": run(state="failed", error=literal[:100], error_truncated=True,
                            outcome={"truncated": True, "source": "run.events"})}
    view._chat_loading = True
    view._replay_queue = ["run"]
    assert not view.runFailure["visible"]
    view._subscribe("run")
    requests[-1][2]({"events": [failure_event(payload={"state": "failed", "error": literal, "outcome": outcome})],
                    "last_seq": 1, "has_more": False})
    projection = view.runFailure
    assert projection["visible"] and len(projection["errorPreview"]) <= MAX_ERROR_PREVIEW + 1
    assert projection["errorPreview"].endswith("…")
    details = json.loads(projection["details"])
    assert details["error"] == literal and details["outcome"] == outcome and not details["error_truncated"]
    assert json.loads(view.activity[0]["details"])["outcome"] == outcome
    assert view.transcript.rows == []
    # Reconstructed snapshots after reopening produce the same visible data.
    recorded = copy.deepcopy(view._runs)
    view._runs = {}
    assert not view.runFailure["visible"]
    view._runs = recorded
    assert view.runFailure == projection


def test_long_untrusted_error_is_plain_text_in_panel_and_full_details(workspace, qtbot):
    view, _ = workspace
    literal = '<img src="https://invalid.test/fetch"><b>literal</b> ' + "\nsynthetic " * 400
    view._runs = {"run": run(state="failed", error=literal, outcome={"detail": "<script>literal</script>"})}
    engine = create_engine(view)
    window = engine.rootObjects()[0]
    try:
        qtbot.wait(25)
        cause = window.findChild(QObject, "chatRunErrorCause")
        assert cause.property("text") == literal[:MAX_ERROR_PREVIEW] + "…"
        assert evaluate(cause, "Number(textFormat)") == 0
        assert cause.property("height") <= evaluate(cause, "font.pixelSize * 3")
        evaluate(window.findChild(QObject, "chatRunErrorDetails"), "clicked()")
        details = window.findChild(QObject, "eventDetailsText")
        assert evaluate(details, "Number(textFormat)") == 0
        assert json.loads(details.property("text"))["error"] == literal
    finally:
        close_engine(engine, window)


@pytest.mark.parametrize("state", ["failed", "interrupted"])
def test_empty_errors_have_a_visible_fallback_without_invented_cause(workspace, state):
    view, _ = workspace
    view._runs = {"run": run(state=state, error=None, outcome=None)}
    assert view.runFailure["visible"] and view.runFailure["summary"] and not view.runFailure["errorPreview"]
    assert "503" not in view.runFailure["summary"]
    details = json.loads(view.runFailure["details"])
    assert details["error"] is None and details["outcome"] is None


@pytest.mark.parametrize("outcome", [None, {}, {"http_status": "503"}, {"http_status": 500}, {"http_status": True}])
def test_provider_text_never_drives_http_classification(outcome):
    value = run(state="failed", error="Literal HTTP 503 provider-overload words", outcome=outcome)
    presentation = run_error_presentation(value, "details")
    assert presentation["summary"] == ""
    assert presentation["errorPreview"] == value["error"]


def test_run_state_details_preserve_raw_outcome_and_both_request_identifiers():
    value = run()
    event = failure_event()
    original = copy.deepcopy((value, event))
    details = json.loads(run_state_presentation(value, event)["raw"])
    assert details["request_id"] == "client-request"
    assert details["outcome"] == event["payload"]["outcome"]
    assert (value, event) == original
