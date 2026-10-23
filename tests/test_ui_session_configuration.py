"""Configuration changes keep a chat and apply only after durable acknowledgement."""
from copy import deepcopy

import pytest

from kira.domain.paths import Paths
from kira.ui.controller import Workspace


@pytest.fixture
def configured_chat(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    view._connected_value = True
    view._project = {"id": "project"}
    session = {"id": "chat", "project_id": "project", "backend": "codex", "model": "old",
               "provider_ref": "existing-thread", "settings": {"reasoning_effort": "high", "tools": True}}
    view._adopt_session(session)
    view._sessions = [session]
    view._catalogs = {"codex": {"available": True, "models": [
        {"id": "new", "supported_reasoning_efforts": [{"reasoningEffort": "high"}]},
        {"id": "default-only", "supported_reasoning_efforts": []}]}}
    view.transcript.append(kind="assistant", body="Existing evidence", runId="old-run")
    view.setDraftText("Unsent draft")
    calls = []
    monkeypatch.setattr(view.client, "request", lambda *args: calls.append(args))
    yield view, calls
    view.close()


def test_model_change_keeps_chat_draft_evidence_and_supported_effort(configured_chat):
    view, calls = configured_chat
    original_rows = deepcopy(view.transcript.rows)
    view.setModel("new")
    request = calls[-1]
    assert request[:2] == ("session.configure", {"project_id": "project", "session_id": "chat",
                                               "model": "new", "reasoning_effort": "high"})
    assert view.busy and not view.workbench.composerReady
    assert view.selectedModel == "old"
    view.send("must wait")
    view.workbench.enqueue("must also wait")
    assert len(calls) == 1
    updated = {**view._session, "model": "new"}
    request[2]({"session": updated})
    assert view.sessionId == "chat" and view.selectedModel == "new"
    assert view.selectedEffort == "high" and view._session["provider_ref"] == "existing-thread"
    assert view.draftText == "Unsent draft" and view.transcript.rows == original_rows
    assert not view.busy
    view.send("Next request")
    assert calls[-1][0] == "run.start" and calls[-1][1]["session_id"] == "chat"
    assert not any(call[0] == "session.create" for call in calls)


def test_failed_configuration_retains_selection_and_can_retry(configured_chat):
    view, calls = configured_chat
    view.setModel("default-only")
    assert calls[-1][1]["reasoning_effort"] == ""
    calls[-1][3]({"message": "Model unavailable"})
    assert view.selectedModel == "old" and view.selectedEffort == "high"
    assert view.sessionId == "chat" and view.draftText == "Unsent draft"
    assert not view.busy and view.notice == "Model unavailable"
    view.setEffort("")
    assert calls[-1][1] == {"project_id": "project", "session_id": "chat",
                             "model": "old", "reasoning_effort": ""}
    updated = deepcopy(view._session)
    updated["settings"].pop("reasoning_effort")
    calls[-1][2]({"session": updated})
    assert view.selectedEffort == "" and view.sessionId == "chat"


def test_configuration_callback_after_chat_switch_cannot_overwrite_new_chat(configured_chat):
    view, calls = configured_chat
    view.setModel("new")
    stale = calls[-1]
    other = {**view._session, "id": "other", "model": "other-model"}
    view._sessions.append(other)
    view.selectSession("other")
    snapshot = calls[-1]
    stale[2]({"session": {**view._session, "id": "chat", "model": "new"}})
    snapshot[2]({"session": other, "runs": []})
    stale[3]({"message": "Stale error"})
    assert view.sessionId == "other" and view.selectedModel == "other-model"
    assert view.notice != "Stale error" and not view.busy


def test_disconnect_invalidates_configuration_and_releases_busy(configured_chat):
    view, calls = configured_chat
    view.setEffort("low")
    pending = calls[-1]
    view._disconnected()
    pending[2]({"session": {**view._session, "model": "unexpected"}})
    assert not view.busy and view.selectedModel == "old" and view.selectedEffort == "high"


def test_stale_session_listing_cannot_replace_configured_session(configured_chat):
    view, calls = configured_chat
    view.refreshSessions()
    stale = calls[-1]
    original = deepcopy(view._session)
    view.setModel("new")
    calls[-1][2]({"session": {**original, "model": "new"}})
    stale[2]({"sessions": [original]})
    assert view._session["model"] == "new"
    assert view.sessions[0]["model"] == "new"
