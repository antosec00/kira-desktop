"""Session isolation and asynchronous controller contracts using synthetic RPC evidence."""
import copy

import pytest

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.preferences import read_preferences, valid_preference


def session(key):
    return {"id": key, "project_id": "project", "backend": "codex", "model": "model-" + key,
            "title": "Chat " + key, "settings": {"reasoning_effort": "high"}}


def run(key, chat, state="completed"):
    return {"id": key, "session_id": chat, "state": state, "prompt": "Request " + key,
            "created_at": "2026-01-01T10:00:00+00:00"}


def event(key, seq, kind, **payload):
    return {"run_id": key, "seq": seq, "type": kind, "payload": payload,
            "timestamp": "2026-01-01T11:00:00+00:00"}


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / n for n in ("config", "data", "state", "runtime"))))
    view._connected_value = True
    view._project = {"id": "project", "memory": {"keep": "original"}}
    view._sessions = [session("a"), session("b")]
    requests = []
    monkeypatch.setattr(view.client, "request", lambda *args: requests.append(args))
    yield view, requests
    view.close()


def request(requests, method, **params):
    return next(r for r in reversed(requests) if r[0] == method and all(r[1].get(k) == v for k, v in params.items()))


def load(view, requests, key, runs=()):
    view.selectSession(key)
    request(requests, "session.snapshot", session_id=key)[2](
        {"session": session(key), "runs": list(runs), "artifacts": [], "history_truncated": False})


def reply_events(requests, key, events, more=False):
    request(requests, "run.subscribe", run_id=key)[2]({"events": events, "has_more": more})


def test_chat_switch_stale_snapshot_and_usage_are_isolated(controller):
    view, calls = controller
    view.selectSession("a")
    stale = request(calls, "session.snapshot", session_id="a")
    load(view, calls, "b", [run("rb", "b")])
    stale[2]({"session": session("a"), "runs": [run("ra", "a")]})
    reply_events(calls, "rb", [event("rb", 1, "message.user", content="B request"),
                              event("rb", 2, "usage", total_tokens=123)])
    assert view.sessionId == "b"
    assert [r["body"] for r in view.transcript.rows] == ["B request"]
    assert view.usageData == {"total_tokens": 123}
    load(view, calls, "a")
    assert view.usageData == {}
    assert view.transcript.rows == []


def test_replay_is_serial_and_ignores_old_callbacks(controller):
    view, calls = controller
    load(view, calls, "a", [run("r1", "a"), run("r2", "a")])
    assert [r[1]["run_id"] for r in calls if r[0] == "run.subscribe"] == ["r1"]
    view._event(event("r2", 1, "message.user", content="Second"))
    assert not view.transcript.rows
    reply_events(calls, "r1", [event("r1", 1, "message.user", content="First")], more=True)
    assert view.sessionsLoading
    old = request(calls, "run.subscribe", run_id="r1")
    old[2]({"events": [], "has_more": False})
    reply_events(calls, "r2", [event("r2", 1, "message.user", content="Second")])
    assert [r["body"] for r in view.transcript.rows] == ["First", "Second"]
    assert not view.sessionsLoading
    load(view, calls, "b")
    old[2]({"events": [event("r1", 2, "message.user", content="Stale")], "has_more": False})
    assert not view.transcript.rows


def test_background_run_is_not_stopped_from_another_chat_and_does_not_pollute_it(controller):
    view, calls = controller
    view._runs = {"ra": run("ra", "a", "running")}
    load(view, calls, "b")
    assert view.busy
    assert view.activityLabel == "Altra chat in esecuzione"
    # Stop acts on the selected chat only; chat a's run is stopped from chat a.
    view.stop()
    assert not any(c[0] == "run.cancel" for c in calls)
    assert view.notice == "Un’altra chat sta lavorando: aprila per fermarne il turno."
    view.send("must not send")
    assert not any(c[0] == "run.start" for c in calls)
    view._event(event("ra", 1, "message.user", content="Other chat"))
    view._event(event("ra", 2, "usage", total_tokens=999))
    view._event(event("ra", 3, "run.state", state="failed", error="Failure"))
    assert not view.transcript.rows
    assert not view.usageData
    assert not view.busy
    assert view.activity[0]["label"] == "Non riuscito"
    assert "Failure" in view.activity[0]["details"]


def test_terminal_states_only_in_activity(controller):
    view, calls = controller
    load(view, calls, "a", [run("ra", "a")])
    reply_events(calls, "ra", [event("ra", 1, "run.state", state="completed")])
    assert view.transcript.rows == []
    assert view.activity[0]["requestExcerpt"] == "Request ra"
    assert view.activity[0]["startedAt"]
    assert view.activity[0]["endedAt"]


def test_drafts_and_preferences_restore_per_session(controller):
    view, calls = controller
    load(view, calls, "a")
    view.setDraftText("Draft A")
    load(view, calls, "b")
    assert view.draftText == ""
    view.setDraftText("Draft B")
    load(view, calls, "a")
    assert view.draftText == "Draft A"
    assert read_preferences(view.paths.config)["selected_sessions"] == {"project": "a"}
    assert not valid_preference("selected_sessions", {"project": 7})
    assert not valid_preference("selected_sessions", {str(i): "s" for i in range(33)})


def test_new_chat_retains_model_and_does_not_create_until_send(controller):
    view, calls = controller
    load(view, calls, "a")
    view.newChat()
    view.setDraftText("New draft")
    view.newChat()
    assert view.sessionId == ""
    assert view.selectedModel == "model-a"
    assert view.selectedEffort == "high"
    assert view.draftText == "New draft"
    assert not any(c[0] == "session.create" for c in calls)
    view.send("New draft")
    assert request(calls, "session.create")[1]["model"] == "model-a"
    view.newChat()
    request(calls, "session.create")[2]({"session": session("created")})
    assert request(calls, "run.start")[1]["session_id"] == "created"
    assert view.draftText == "New draft"


def test_shared_memory_updates_only_memory_and_ignores_stale_result(controller):
    view, calls = controller
    load(view, calls, "a")
    view.refreshSharedContext()
    old = request(calls, "project.context")
    load(view, calls, "b")
    old[2]({"content": "Old content", "enabled": True})
    assert view.sharedContextText == ""
    view.setSharedSessionMemory(False)
    update = request(calls, "project.update")
    assert update[1] == {"project_id": "project", "share_session_summaries": False}
    project = copy.deepcopy(view.project)
    project["memory"] = {"keep": "latest service notes", "share_session_summaries": False}
    update[2]({"project": project})
    assert not view.sharedSessionMemoryEnabled
    request(calls, "project.context")[2]({"content": "", "enabled": False})
    assert "disattivata" in view.sharedContextText


def test_session_list_pagination_dedup_and_stale_failure(controller):
    view, calls = controller
    view.refreshSessions()
    first = request(calls, "session.list")
    first[2]({"sessions": [session("a")], "has_more": True})
    view.loadMoreSessions()
    page = request(calls, "session.list")
    assert page[1]["offset"] == 1
    page[2]({"sessions": [session("a"), session("b")], "has_more": False})
    assert [s["id"] for s in view.sessions] == ["a", "b"]
    assert not view.sessionsHasMore
    view.refreshSessions()
    first[3]({"message": "Stale failure"})
    assert view.sessionsLoading
    request(calls, "session.list")[3]({"message": "Current failure"})
    assert not view.sessionsLoading
    assert view.notice == "Current failure"


def test_snapshot_failure_releases_loading_and_does_not_send_old_chat(controller):
    view, calls = controller
    view.selectSession("b")
    assert view.busy
    request(calls, "session.snapshot")[3]({"message": "Unavailable"})
    assert not view.sessionsLoading
    assert view.sessionId == ""
    assert view.notice == "Unavailable"


def test_model_cannot_change_while_background_run_active(controller):
    view, calls = controller
    load(view, calls, "a")
    view._runs = {"rb": run("rb", "b", "running")}
    view.setBackend("ollama")
    view.setModel("changed")
    view.setEffort("low")
    assert (view.backend, view.selectedModel, view.selectedEffort) == ("codex", "model-a", "high")


def test_project_open_restores_older_remembered_session_via_snapshot(controller):
    view, calls = controller
    view._preferences["selected_sessions"] = {"project": "older"}
    view.openProject("project")
    request(calls, "project.snapshot")[2]({"project": view.project, "sessions": [session("a")],
                                         "runs": [], "artifacts": []})
    assert request(calls, "session.snapshot")[1]["session_id"] == "older"
    request(calls, "session.snapshot")[2]({"session": session("older"), "runs": [], "artifacts": []})
    assert view.sessionId == "older"
    assert view.selectedModel == "model-older"


def test_failed_replay_requires_reload_and_same_selection_can_retry(controller):
    view, calls = controller
    load(view, calls, "a", [run("ra", "a")])
    request(calls, "run.subscribe")[3]({"message": "Read failed"})
    assert not view.sessionsLoading
    view.send("Do not run against unread history")
    assert not any(c[0] == "run.start" for c in calls)
    before = len(calls)
    view.selectSession("a")
    assert len(calls) == before + 1
    assert calls[-1][0] == "session.snapshot"


def test_reconnect_reloads_only_selected_transcript_and_keeps_draft(controller):
    view, calls = controller
    load(view, calls, "a", [run("ra", "a")])
    reply_events(calls, "ra", [event("ra", 1, "message.user", content="Only once")])
    view.setDraftText("Unsent")
    view.openProject("project")
    request(calls, "project.snapshot")[2]({"project": view.project,
        "sessions": [session("a"), session("b")], "runs": [run("ra", "a"), run("rb", "b")]})
    request(calls, "session.snapshot")[2]({"session": session("a"), "runs": [run("ra", "a")]})
    reply_events(calls, "ra", [event("ra", 1, "message.user", content="Only once")])
    assert [r["body"] for r in view.transcript.rows] == ["Only once"]
    assert view.draftText == "Unsent"


def test_context_failure_releases_loading(controller):
    view, calls = controller
    view.refreshSharedContext()
    request(calls, "project.context")[3]({"message": "Unavailable"})
    assert not view.sharedContextLoading
    assert view.notice == "Unavailable"


def test_failed_foreign_selection_cannot_send_to_unvalidated_session(controller):
    view, calls = controller
    load(view, calls, "a")
    view.selectSession("foreign")
    view.send("Blocked while loading")
    request(calls, "session.snapshot")[3]({"code": "NOT_FOUND", "message": "Unknown chat"})
    view.send("Blocked after failure")
    assert view.sessionId == ""
    assert not any(c[0] == "run.start" for c in calls)
    view.newChat()
    view.send("Valid new draft")
    assert request(calls, "session.create")[1]["project_id"] == "project"


def test_foreign_snapshot_is_not_adopted(controller):
    view, calls = controller
    view.selectSession("foreign")
    request(calls, "session.snapshot")[2]({"session": {**session("foreign"), "project_id": "other"}})
    assert view.sessionId == ""
    view.send("Blocked")
    assert not any(c[0] == "run.start" for c in calls)


def test_live_event_during_snapshot_wait_is_buffered_until_ordered_replay(controller):
    view, calls = controller
    view._runs = {"r1": run("r1", "a"), "r2": run("r2", "a", "running")}
    view.selectSession("a")
    view._event(event("r2", 1, "message.user", content="Second"))
    assert not view.transcript.rows
    request(calls, "session.snapshot")[2]({"session": session("a"),
        "runs": [run("r1", "a"), run("r2", "a", "running")]})
    reply_events(calls, "r1", [event("r1", 1, "message.user", content="First")])
    reply_events(calls, "r2", [event("r2", 1, "message.user", content="Second")])
    assert [r["body"] for r in view.transcript.rows] == ["First", "Second"]


def test_session_title_refresh_and_rename_preserve_draft(controller):
    view, calls = controller
    load(view, calls, "a")
    view.setDraftText("Do not discard this draft")
    changes = []
    view.chatChanged.connect(lambda: changes.append(view.sessionId))
    view.refreshSessions()
    request(calls, "session.list")[2]({"sessions": [{**session("a"), "title": "Derived title"}], "has_more": False})
    assert view.sessionTitle == "Derived title"
    view.renameSession("a", "Manual title")
    request(calls, "session.rename")[2]({"session": {**session("a"), "title": "Manual title"}})
    assert view.sessionTitle == "Manual title"
    assert view.draftText == "Do not discard this draft"
    assert changes == []


@pytest.mark.parametrize("snapshot_pending", [False, True])
def test_live_overflow_waits_for_replay_head_then_recovers_durable_events(controller, snapshot_pending):
    view, calls = controller
    runs = [run("r1", "a"), run("r2", "a", "running")]
    if snapshot_pending:
        view._runs = {r["id"]: r for r in runs}
        view.selectSession("a")
    else:
        load(view, calls, "a", runs)
    for seq in range(1, 2051):
        view._event(event("r2", seq, "usage", total_tokens=seq))
    assert not any(c[0] == "run.subscribe" and c[1]["run_id"] == "r2" for c in calls)
    assert len(view._buffered_events["r2"]) <= 2048
    if snapshot_pending:
        request(calls, "session.snapshot")[2]({"session": session("a"), "runs": runs})
    reply_events(calls, "r1", [event("r1", 1, "message.user", content="First")])
    assert request(calls, "run.subscribe", run_id="r2")[1]["after_seq"] == 0
    reply_events(calls, "r2", [event("r2", seq, "usage", total_tokens=seq)
                               for seq in range(1, 2051)])
    assert view._cursors["r2"] == 2050
    assert view.usageData == {"total_tokens": 2050}
    assert [r["body"] for r in view.transcript.rows] == ["First"]
    assert not view.sessionsLoading
