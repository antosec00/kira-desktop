"""Run-specific status context, including interleaved replay and event deduplication."""

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace, run_state_presentation


def test_request_excerpt_is_bounded_but_full_details_retained():
    request = "  Prima riga\n\t" + "documento è 🐶 " * 80
    run = {"prompt": request, "created_at": "2026-10-14T05:10:00+02:00", "session_id": "session"}
    event = {"run_id": "execution-one", "seq": 8, "timestamp": "2026-10-14T03:12:00Z",
             "payload": {"state": "completed"}}
    old_run, old_event = copy.deepcopy(run), copy.deepcopy(event)
    result = run_state_presentation(run, event)
    assert result["title"] == "Completato"
    assert len(result["body"].splitlines()[0]) <= 171
    assert "…" in result["body"]
    assert "14/10/2026 03:10:00 UTC" in result["body"]
    assert "Esito: 14/10/2026 03:12:00 UTC" in result["body"]
    assert json.loads(result["raw"])["request"] == request
    assert (run, event) == (old_run, old_event)


@pytest.mark.parametrize("timestamp", [None, "bad timestamp", "2026-10-14T03:12:00"])
def test_missing_context_never_invents_a_request_or_time(timestamp):
    result = run_state_presentation({}, {"run_id": "abcdef12345", "timestamp": timestamp,
                                        "payload": {"state": "interrupted", "error": "Connection lost"}})
    assert "Testo non disponibile · esecuzione abcdef12" in result["body"]
    assert "Esito: non disponibile" in result["body"]
    assert "Errore: Connection lost" in result["body"]


@pytest.fixture
def view(qapp, tmp_path):
    workspace = Workspace(Paths(*(tmp_path / p for p in ("config", "data", "state", "runtime"))))
    workspace._runs = {name: {"id": name, "state": "completed", "prompt": f"Richiesta {name}",
                             "created_at": "2026-10-14T01:00:00Z"} for name in ("alpha", "beta")}
    yield workspace
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def event(run, seq, kind, payload):
    return {"run_id": run, "seq": seq, "type": kind,
            "timestamp": "2026-10-14T02:00:00Z", "payload": payload}


def test_interleaved_runs_keep_own_request_and_duplicate_event_is_ignored(view):
    view._session = {"id": "unrelated-current-session", "model": "unrelated-model"}
    for run in ("beta", "alpha"):
        data = event(run, 1, "run.state", {"state": "completed"})
        view._event(data)
        view._event(copy.deepcopy(data))
    assert view.transcript.rows == []
    rows = view.activity
    assert len(rows) == 2
    assert {(row["id"], json.loads(row["details"])["request"]) for row in rows} == {
        ("beta", "Richiesta beta"), ("alpha", "Richiesta alpha")}
    assert all("unrelated" not in row["description"] for row in rows)


def test_user_evidence_restores_request_after_snapshot_truncation(view):
    view._runs["alpha"].update(prompt="short snapshot", prompt_truncated=True)
    view._event(event("alpha", 1, "run.state", {"state": "queued"}))
    assert json.loads(next(row for row in view.activity if row["id"] == "alpha")["details"])["request_truncated"] is True
    request = "Full request after snapshot\n" + "detail " * 600
    view._event(event("alpha", 2, "message.user", {"content": request}))
    view._event(event("alpha", 3, "run.state", {"state": "failed", "error": "Synthetic error"}))
    final = next(row for row in view.activity if row["id"] == "alpha")
    assert json.loads(final["details"])["request"] == request
    assert json.loads(final["details"])["request_truncated"] is False
    assert final["state"] == "failed" and "Synthetic error" in final["description"]
    assert not any(row["kind"] == "state" for row in view.transcript.rows)
