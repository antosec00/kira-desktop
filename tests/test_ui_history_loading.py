"""Replay batches preserve durable event order without rendering each event."""
import time

import pytest

from kira.domain.paths import Paths
from kira.ui.controller import Workspace


@pytest.fixture
def history(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    view._connected_value = True
    view._project = {"id": "project"}
    view._sessions = [{"id": "chat", "project_id": "project", "backend": "codex", "model": "model",
                       "settings": {}}]
    calls = []
    monkeypatch.setattr(view.client, "request", lambda *args: calls.append(args))
    yield view, calls
    view.close()


def event(seq, kind, **payload):
    return {"run_id": "run", "seq": seq, "type": kind, "payload": payload}


def start_replay(view, calls, state="completed"):
    view.selectSession("chat")
    calls[-1][2]({"session": view._sessions[0], "runs": [
        {"id": "run", "session_id": "chat", "state": state}]})


def test_large_replay_publishes_once_and_keeps_latest_evidence(history):
    view, calls = history
    start_replay(view, calls)
    changes, transcript_changes, resets, inserts = [], [], [], []
    view.changed.connect(lambda: changes.append(True))
    view.transcriptChanged.connect(lambda: transcript_changes.append(True))
    view.transcript.modelReset.connect(lambda: resets.append(True))
    view.transcript.rowsInserted.connect(lambda: inserts.append(True))
    events = [event(1, "message.user", content="Synthetic request")]
    for index in range(5000):
        events.append(event(len(events) + 1, "message.delta", item_id="answer", content="chunk "))
    events.append(event(len(events) + 1, "message.completed", item_id="answer", content="Complete answer"))
    for index in range(700):
        events.append(event(len(events) + 1, "tool.started", tool_id=str(index), name="test",
                            arguments={"command": "synthetic"}))
        events.append(event(len(events) + 1, "tool.completed", tool_id=str(index),
                            result={"stdout": "Synthetic output", "ok": True}))
    events.append(event(len(events) + 1, "usage", total_tokens=123))
    events.append(event(len(events) + 1, "run.state", state="completed"))
    began = time.perf_counter()
    for offset in range(0, len(events), 200):
        page = events[offset:offset + 200]
        more = offset + 200 < len(events)
        calls[-1][2]({"events": page, "has_more": more})
        if more:
            assert view.chatLoading and view.transcript.rowCount() == 0
            assert not changes and not transcript_changes and not resets and not inserts
    elapsed = time.perf_counter() - began
    print(f"Synthetic replay: {len(events)} events in {elapsed:.3f}s; single model reset")
    assert not view.chatLoading and not view.busy
    assert len(resets) == 1 and len(changes) == 1 and len(transcript_changes) == 1
    assert not inserts and view.transcript.rowCount() == 600
    assert view.transcript.omitted == 102
    assert view.transcript.rows[-1]["toolId"] == "699"
    assert view.transcript.rows[-1]["status"] == "confermato"
    assert view.usageData == {"total_tokens": 123} and view._cursors["run"] == len(events)


def test_replay_publishes_active_partial_then_continues_live(history):
    view, calls = history
    start_replay(view, calls, state="running")
    calls[-1][2]({"events": [event(1, "message.delta", item_id="a", content="Partial ")], "has_more": False})
    assert not view.chatLoading and view.busy
    assert view.transcript.rows[0]["body"] == "Partial "
    view._event(event(2, "message.delta", item_id="a", content="live"))
    view._flush()
    assert view.transcript.rows[0]["body"] == "Partial live"
    view._event(event(3, "message.completed", item_id="a"))
    assert view.transcript.rows[0]["status"] == "completo"


@pytest.mark.parametrize("disconnect", [False, True])
def test_failed_replay_clears_loading_and_requires_retry(history, disconnect):
    view, calls = history
    start_replay(view, calls)
    calls[-1][2]({"events": [event(1, "message.user", content="Read evidence")], "has_more": True})
    stale = calls[-1]
    if disconnect:
        view._disconnected()
    else:
        stale[3]({"message": "Read failed"})
    assert not view.chatLoading and view._chat_failed
    assert view.transcript.rowCount() == 1
    view.send("must not send")
    assert not any(call[0] == "run.start" for call in calls)
    view.selectSession("chat")
    assert view.chatLoading and view.transcript.rowCount() == 0
    stale[2]({"events": [event(2, "message.user", content="Stale")], "has_more": False})
    assert view.chatLoading and view.transcript.rowCount() == 0
    calls[-1][2]({"session": view._sessions[0], "runs": []})
    assert not view.chatLoading and not view._chat_failed


def test_session_list_loading_does_not_hide_loaded_chat(history):
    view, calls = history
    start_replay(view, calls)
    calls[-1][2]({"events": [event(1, "message.user", content="Visible")], "has_more": False})
    view.refreshSessions()
    assert view.sessionsLoading and not view.chatLoading
    assert view.transcript.rowCount() == 1


def test_completed_run_replay_never_regresses_state_to_running_mid_replay(history):
    view, calls = history
    view._sessions.append({"id": "other", "project_id": "project", "backend": "codex", "model": "model", "settings": {}})
    view.selectSession("chat")
    calls[-1][2]({
        "session": view._sessions[0],
        "runs": [{"id": "run", "session_id": "chat", "state": "completed", "last_seq": 3}],
    })
    # Mid-replay page contains early queued/running state events for a run already known to be completed.
    calls[-1][2]({
        "events": [
            event(1, "run.state", state="queued"),
            event(2, "run.state", state="running"),
        ],
        "has_more": True,
    })
    assert view._runs["run"]["state"] == "completed"
    assert view.activeRunId == ""
    # Switching to another chat mid-replay must not leave the completed run marked as running ("busy").
    view.selectSession("other")
    calls[-1][2]({"session": view._sessions[1], "runs": []})
    assert view._runs["run"]["state"] == "completed"
    assert view.activeRunId == ""
    assert not view.busy


def test_session_cache_restores_previously_loaded_chat_immediately(history):
    view, calls = history
    second = {"id": "second", "project_id": "project", "backend": "codex", "model": "model", "settings": {}}
    view._sessions.append(second)
    view.selectSession("chat")
    calls[-1][2]({
        "session": view._sessions[0],
        "runs": [{"id": "run", "session_id": "chat", "state": "completed", "last_seq": 2}],
    })
    calls[-1][2]({
        "events": [
            event(1, "message.user", content="First chat question"),
            event(2, "message.completed", item_id="a", content="First chat answer"),
        ],
        "has_more": False,
    })
    assert not view.chatLoading and view.transcript.rowCount() == 2

    # Switch to second chat and load it
    view.selectSession("second")
    assert "chat" in view._session_cache
    calls[-1][2]({
        "session": second,
        "runs": [{"id": "run-2", "session_id": "second", "state": "completed", "last_seq": 1}],
    })
    calls[-1][2]({
        "events": [{"run_id": "run-2", "seq": 1, "type": "message.user", "payload": {"content": "Second chat"}}],
        "has_more": False,
    })
    assert not view.chatLoading and view.transcript.rowCount() == 1

    # Switch back to "chat": must restore immediately from cache without chatLoading=True
    view.selectSession("chat")
    assert not view.chatLoading
    assert view.transcript.rowCount() == 2
    assert view.transcript.rows[0]["body"] == "First chat question"
    assert view.transcript.rows[1]["body"] == "First chat answer"

