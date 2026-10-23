"""Real QML/Qt IPC/service handoff journey; provider replies are synthetic only."""

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, Qt
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from test_ui_support import SERVICE_WAIT_MS, isolated_service_environment, start_service, stop_service

FIXTURE = Path(__file__).parent / "fixtures/assistant_service.py"
MODEL = "synthetic-assistant-model"
RESULT = "Synthetic team verified the fixture evidence."
FOLLOWUP = "Synthetic Kira reviewed the recorded Team evidence in the original chat."


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def click_visible(window, name):
    control = visual_item(window.contentItem(), name)
    assert control is not None and control.isVisible() and control.isEnabled(), name
    bounds = control.mapRectToItem(window.contentItem(), control.boundingRect())
    assert bounds.left() >= 0 and bounds.right() <= window.width()
    assert bounds.top() >= 0 and bounds.bottom() <= window.height()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                     pos=control.mapToScene(control.boundingRect().center()).toPoint())


def open_team_details(qtbot, window):
    click_visible(window, "assistantConfigureTeam")
    qtbot.waitUntil(lambda: (control := visual_item(window.contentItem(), "assistantTeamDetailsClose"))
                    is not None and control.isVisible())


def open_desktop(paths):
    view = Workspace(paths)
    view._preferences["onboarding_complete"] = True
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1100, 800)
    view.client.connect_service()
    return view, engine, window


def close_desktop(desktop):
    view, engine, window = desktop
    window.setProperty("closeConfirmed", True)
    window.close()
    view.close()
    engine.deleteLater()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def team_cards(view):
    return [row for row in view.transcript.rows if row["kind"] == "team"]


def test_chat_ipc_managed_team_result_draft_and_reopen(qapp, qtbot, monkeypatch):
    """One durable origin card, real scheduling/cleanup, and explicit Team navigation."""
    with tempfile.TemporaryDirectory(prefix="kira-assistant-ipc-") as directory:
        home = Path(directory)
        paths = Paths(*(home / part for part in ("config", "data", "state", "runtime")))
        control = home / "fixture-control"
        control.mkdir()
        project = home / "synthetic-project"
        project.mkdir()
        (project / "evidence.txt").write_text("SYNTHETIC_EVIDENCE_MARKER\n")
        environment = isolated_service_environment(monkeypatch, home, KIRA_TEST_ASSISTANT_CONTROL=control)
        with (home / "service.log").open("w+", encoding="utf-8") as log:
            process = start_service([FIXTURE], environment, log)
            desktops = []
            try:
                qtbot.waitUntil(lambda: paths.socket.exists() or process.poll() is not None, timeout=SERVICE_WAIT_MS)
                assert process.poll() is None
                desktop = open_desktop(paths)
                desktops.append(desktop)
                view, _, window = desktop
                qtbot.waitUntil(lambda: view.connected, timeout=SERVICE_WAIT_MS)
                view.setBackend("codex")
                qtbot.waitUntil(lambda: bool(view.models), timeout=SERVICE_WAIT_MS)
                view.createProject("Synthetic assistant IPC", str(project), "", "read_only")
                qtbot.waitUntil(lambda: bool(view.project) and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                project_id = view.project["id"]
                view.setModel(MODEL)
                view.setEffort("xhigh")
                view.setTools(True)
                prompt = visual_item(window.contentItem(), "prompt")
                send = visual_item(window.contentItem(), "sendButton")
                prompt.setProperty("text", "Organize a synthetic review with a coordinator and reviewer")
                qtbot.waitUntil(send.isEnabled, timeout=SERVICE_WAIT_MS)
                QMetaObject.invokeMethod(send, "clicked")
                qtbot.waitUntil(lambda: (control / "delegation-requested").exists(), timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: len(team_cards(view)) == 1, timeout=SERVICE_WAIT_MS)
                original_session, original_run = view.sessionId, view.activeRunId
                delegation_id = (control / "delegation-requested").read_text()
                assert original_session and original_run
                assert window.property("page") == 0
                assert team_cards(view)[0]["toolId"] == delegation_id
                assert not team_cards(view)[0]["command"]
                qtbot.waitUntil(lambda: view.assistant.current.get("state") == "pending", timeout=SERVICE_WAIT_MS)
                assert visual_item(window.contentItem(), "assistantInlineNotice").isVisible()
                open_team_details(qtbot, window)
                assert visual_item(window.contentItem(), "assistantPendingHandoff").isVisible()
                assert visual_item(window.contentItem(), "assistantCancelTeam").isVisible()
                click_visible(window, "assistantTeamDetailsClose")
                draft = "Unsent original-chat draft: è <literal> @evidence.txt"
                prompt.setProperty("text", draft)
                view.flushDrafts()

                (control / "release-origin").touch()
                qtbot.waitUntil(lambda: (control / "reviewer-started").exists(), timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: bool(view.assistant.current.get("team_id")), timeout=SERVICE_WAIT_MS)
                team_id = view.assistant.current["team_id"]
                assert (control / (original_run + ".closed")).exists()
                assert view.sessionId == original_session and view.draftText == draft
                assert window.property("page") == 0 and len(team_cards(view)) == 1
                assert view.selectedModel == MODEL and view.selectedEffort == "xhigh"

                (control / "release-reviewer").touch()
                qtbot.waitUntil(lambda: view.assistant.current.get("state") == "completed", timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: len(team_cards(view)) == 1 and RESULT in team_cards(view)[0]["body"], timeout=SERVICE_WAIT_MS)
                assert view.assistant.current["cleanup_confirmed"]
                # The completed Team produces a distinct, real ordinary response.
                # It is discovered through the service snapshot and subscribed by Qt IPC.
                qtbot.waitUntil(lambda: (control / "followup-started").exists(), timeout=SERVICE_WAIT_MS)
                followup_id = (control / "followup-started").read_text()
                qtbot.waitUntil(lambda: view.assistant.followup.get("state") == "running"
                                and followup_id in view._runs, timeout=SERVICE_WAIT_MS)
                assert view.assistant.current["state"] == "completed"
                assert view._runs[followup_id]["source"] == "team_followup"
                assert view._runs[followup_id]["related_team_id"] == team_id
                assert "team_id" not in view._runs[followup_id]
                assert view.selectedActiveRunId == followup_id
                assert view.draftText == prompt.property("text") == draft
                received = {}
                assert view.client.request("run.events", {"run_id": followup_id}, received.update)
                qtbot.waitUntil(lambda: bool(received), timeout=SERVICE_WAIT_MS)
                assert any(event["type"] == "backend.notice"
                           and event["payload"].get("source") == "team_followup" for event in received["events"])
                assert not any(event["type"] == "message.user" for event in received["events"])
                assert not any(row["kind"] == "user" and row["runId"] == followup_id for row in view.transcript.rows)
                open_team_details(qtbot, window)
                assert visual_item(window.contentItem(), "assistantFollowupState").isVisible()
                click_visible(window, "assistantTeamDetailsClose")
                (control / "release-followup").touch()
                qtbot.waitUntil(lambda: view.assistant.followup.get("state") == "completed"
                                and not view.busy, timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: any(row["kind"] == "assistant" and row["runId"] == followup_id
                                           and row["body"] == FOLLOWUP for row in view.transcript.rows), timeout=SERVICE_WAIT_MS)
                assert not view.assistant.active and not view.busy
                assert view.sessionId == original_session and prompt.property("text") == draft
                assert window.property("page") == 0
                calls = [json.loads(line) for line in (control / "provider-calls.jsonl").read_text().splitlines()]
                assert [call["role"] for call in calls] == ["origin", "coordinator", "reviewer", "coordinator", "followup"]
                assert all(call["model"] == MODEL and call["effort"] == "xhigh" for call in calls)
                assert all(call["permission"] == "read_only" for call in calls)
                assert all((control / (call["run_id"] + ".closed")).exists() for call in calls)
                # Closing and reopening the real QML engine replays persisted
                # service evidence; no provider turn or team is recreated.
                close_desktop(desktop)
                desktops.remove(desktop)
                reopened = open_desktop(paths)
                desktops.append(reopened)
                view, _, window = reopened
                qtbot.waitUntil(lambda: view.connected, timeout=SERVICE_WAIT_MS)
                view.openProject(project_id)
                qtbot.waitUntil(lambda: view.sessionId == original_session and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: view.assistant.current.get("state") == "completed", timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: len(team_cards(view)) == 1 and RESULT in team_cards(view)[0]["body"], timeout=SERVICE_WAIT_MS)
                assert view.assistant.current["delegation_id"] == delegation_id
                assert team_cards(view)[0]["command"] == team_id
                assert view.draftText == draft
                qtbot.waitUntil(lambda: view.assistant.followup.get("state") == "completed", timeout=SERVICE_WAIT_MS)
                assert view.assistant.followup["run_id"] == followup_id
                qtbot.waitUntil(lambda: any(row["kind"] == "assistant" and row["runId"] == followup_id
                                           and row["body"] == FOLLOWUP for row in view.transcript.rows), timeout=SERVICE_WAIT_MS)
                assert not any(row["kind"] == "user" and row["runId"] == followup_id for row in view.transcript.rows)
                assert visual_item(window.contentItem(), "prompt").property("text") == draft
                assert window.property("page") == 0
                assert len((control / "provider-calls.jsonl").read_text().splitlines()) == 5
                # Completed teams reopen from the persistent compact summary;
                # expanding presentation must not schedule another team turn.
                open_team = visual_item(window.contentItem(), "assistantOpenTeam")
                assert open_team is None or not open_team.isVisible()
                summary = visual_item(window.contentItem(), "assistantConfigureTeam")
                assert summary.isVisible() and summary.isEnabled()
                assert "Completato" in summary.property("text")
                open_team_details(qtbot, window)
                open_team = visual_item(window.contentItem(), "assistantOpenTeam")
                assert open_team.isVisible() and open_team.isEnabled()
                click_visible(window, "assistantOpenTeam")
                qtbot.waitUntil(lambda: window.property("page") == 4 and view.teams.selectedId == team_id)
                assert view.sessionId == original_session and view.draftText == draft
                assert len(team_cards(view)) == 1
                assert len((control / "provider-calls.jsonl").read_text().splitlines()) == 5
            finally:
                (control / "release-origin").touch()
                (control / "release-reviewer").touch()
                (control / "release-followup").touch()
                for desktop in desktops:
                    close_desktop(desktop)
                stop_service(process)


def test_delegation_replayed_after_empty_snapshot_refreshes_assistant(qapp, qtbot, tmp_path, monkeypatch):
    """A fast provider can commit handoff before the GUI registers run.subscribe."""
    paths = Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime")))
    view = Workspace(paths)
    calls = []
    pending = []

    def request(method, params, received, failed=None, **kwargs):
        # Saved-goal metadata is independent of the assistant replay under test.
        if method == "goal.get":
            received({"goal": None})
            return True
        calls.append((method, params))
        pending.append((method, received))
        return True

    def respond(method, result):
        index = next(i for i, item in enumerate(pending) if item[0] == method)
        pending.pop(index)[1](result)

    monkeypatch.setattr(view.client, "request", request)
    try:
        view._connected_value = True
        view._project = {"id": "project", "name": "Synthetic replay", "root": str(tmp_path)}
        view._session = {"id": "chat", "project_id": "project", "backend": "codex", "model": MODEL,
                         "settings": {"reasoning_effort": "xhigh"}}
        view._backend, view._model, view._effort = "codex", MODEL, "xhigh"
        view.changed.emit()
        view.assistant.refresh()
        respond("assistant.snapshot", {"environment": {}, "delegations": []})
        assert not view.assistant.current and not view.assistant._timer.isActive()
        view._runs = {"run": {"id": "run", "project_id": "project", "session_id": "chat", "state": "running"}}
        delegation = {"id": "delegation", "delegation_id": "delegation", "project_id": "project",
                      "session_id": "chat", "origin_run_id": "run", "state": "pending", "team_id": None,
                      "goal": "Synthetic replay handoff"}
        replayed = {"run_id": "run", "seq": 1, "type": "team.delegation", "payload": delegation}
        view._subscribe("run")
        respond("run.subscribe", {"events": [replayed], "last_seq": 1, "has_more": False})
        assert len(team_cards(view)) == 1
        qtbot.waitUntil(lambda: any(method == "assistant.snapshot" for method, _ in pending), timeout=1000)
        respond("assistant.snapshot", {"environment": {}, "delegations": [delegation]})
        assert view.assistant.current["state"] == "pending" and view.assistant.active
        assert view.assistant.current["delegation_id"] == "delegation"
        # A duplicate arriving later on the live connection must not schedule
        # another fetch after the replay already refreshed this read model.
        count = len(calls)
        view.client.eventReceived.emit(replayed)
        assert not view.assistant._coalesce.isActive() and len(calls) == count
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
