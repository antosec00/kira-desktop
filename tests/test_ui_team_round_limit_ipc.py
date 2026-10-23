"""Real QML editing, IPC and continuation scheduling with synthetic providers."""

import copy
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from test_ui_assistant_ipc import close_desktop, open_desktop, open_team_details, visual_item
from test_ui_support import (
    SERVICE_WAIT_MS, isolated_service_environment, start_service, stop_service, wait_laid_out,
)
from test_ui_team_orchestration_ipc import calls, read_marker, request

FIXTURE = Path(__file__).parent / "fixtures/team_round_limit_service.py"
MODEL = "synthetic-team-round-limit-model"
ORIGINAL = "SYNTHETIC_ORIGINAL_TEAM_EVIDENCE"
VERIFIED = "SYNTHETIC_FRESH_VERIFICATION_EVIDENCE"
RESULT = "Synthetic continuation verified a fresh result and completed its second coordinator turn."


def click(window, item):
    assert item is not None and item.isVisible() and item.isEnabled()
    bounds = item.mapRectToItem(window.contentItem(), item.boundingRect())
    assert bounds.left() >= 0 and bounds.right() <= window.width()
    assert bounds.top() >= 0 and bounds.bottom() <= window.height()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                     pos=item.mapToScene(item.boundingRect().center()).toPoint())


def type_text(window, item, text):
    click(window, item)
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for character in text:
        QTest.keyClick(window, character)


@pytest.mark.parametrize("surface", ["chat_details", "team_view"])
def test_existing_team_zero_limit_real_qml_ipc_and_second_synthesis(qapp, qtbot, monkeypatch, surface):
    with tempfile.TemporaryDirectory(prefix="kira-round-ipc-") as directory:
        home = Path(directory)
        paths = Paths(*(home / part for part in ("config", "data", "state", "runtime")))
        control, project = home / "fixture-control", home / "synthetic-project"
        control.mkdir()
        project.mkdir()
        (project / "evidence.txt").write_text(ORIGINAL + "\n", encoding="utf-8")
        (project / "verification.txt").write_text(VERIFIED + "\n", encoding="utf-8")
        environment = isolated_service_environment(
            monkeypatch, home, KIRA_TEST_TEAM_ROUND_LIMIT_CONTROL=control)
        with (home / "service.log").open("w+", encoding="utf-8") as log:
            process = start_service([FIXTURE], environment, log)
            desktops = []
            try:
                qtbot.waitUntil(lambda: paths.socket.exists() or process.poll() is not None, timeout=SERVICE_WAIT_MS)
                assert process.poll() is None
                desktop = open_desktop(paths)
                desktops.append(desktop)
                view, _, window = desktop
                window.resize(1380, 900)
                qtbot.waitUntil(lambda: view.connected, timeout=SERVICE_WAIT_MS)
                view.setBackend("codex")
                qtbot.waitUntil(lambda: bool(view.models), timeout=SERVICE_WAIT_MS)
                view.createProject("Synthetic continuation round limit", str(project), "", "read_only")
                qtbot.waitUntil(lambda: bool(view.project) and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                project_id = view.project["id"]
                view.setModel(MODEL)
                view.setEffort("xhigh")
                view.setTools(True)
                prompt = visual_item(window.contentItem(), "prompt")
                type_text(window, prompt, "Delegate a synthetic read-only review with one coordinator turn.")
                send = visual_item(window.contentItem(), "sendButton")
                qtbot.waitUntil(send.isEnabled, timeout=SERVICE_WAIT_MS)
                click(window, send)
                qtbot.waitUntil(lambda: (control / "delegation-requested").exists(), timeout=SERVICE_WAIT_MS)
                origin = read_marker(control, "delegation-requested")
                session_id = view.sessionId
                session_settings = copy.deepcopy(view._session["settings"])
                draft = "Unsent project draft preserved while continuing the Team."
                type_text(window, prompt, draft)
                view.flushDrafts()
                (control / "release-origin").touch()
                qtbot.waitUntil(lambda: view.assistant.current.get("state") == "paused"
                                and view.assistant.current.get("cleanup_confirmed") is True, timeout=SERVICE_WAIT_MS)
                parent_id = view.assistant.current["team_id"]
                qtbot.waitUntil(lambda: view.assistant.followup.get("team_id") == parent_id
                                and view.assistant.followup.get("state") == "completed"
                                and not view.busy, timeout=SERVICE_WAIT_MS)
                parent_followup = view.assistant.followup["run_id"]
                qtbot.waitUntil(lambda: any(row["kind"] == "assistant" and row["runId"] == parent_followup
                                and row["body"] == "Synthetic round-limit Team followup: paused"
                                for row in view.transcript.rows), timeout=SERVICE_WAIT_MS)
                assert not any(row["kind"] == "user" and row["runId"] == parent_followup for row in view.transcript.rows)
                parent = request(qtbot, view, "team.snapshot", {"team_id": parent_id})["team"]
                assert parent["round_limit"] == parent["round_index"] == 1
                assert parent["pause_reason"] == "round_limit" and len(parent["steps"]) == 1
                assert parent["steps"][0]["state"] == "completed"
                reader = read_marker(control, "parent-reader-executed")
                assert ORIGINAL in reader["result"]["content"]
                assert parent["steps"][0]["run_id"] == reader["run_id"]
                recorded_before = calls(control)
                assert [row["role"] for row in recorded_before] == ["origin", "coordinator", "reader", "followup"]
                assert view.assistant.current["round_limit"] == 1
                assert view.assistant.current["delegation_id"] == origin["delegation_id"]

                open_team_details(qtbot, window)
                if surface == "chat_details":
                    control_name, button_name = "assistantResumeRoundLimitInput", "assistantResumeTeam"
                else:
                    open_team = visual_item(window.contentItem(), "assistantOpenTeam")
                    wait_laid_out(qtbot, open_team)
                    click(window, open_team)
                    qtbot.waitUntil(lambda: view.teams.selectedId == parent_id and view.teams.canResume, timeout=SERVICE_WAIT_MS)
                    control_name, button_name = "teamContinuationRoundLimitInput", "resumeTeamButton"
                qtbot.waitUntil(lambda: visual_item(window.contentItem(), control_name) is not None, timeout=SERVICE_WAIT_MS)
                limit = visual_item(window.contentItem(), control_name)
                # The input is clicked at its centre: measure it only once its panel is laid out.
                wait_laid_out(qtbot, limit, limit.property("contentItem"))
                assert limit.property("value") == 1
                type_text(window, limit.property("contentItem"), "0")
                QTest.keyClick(window, Qt.Key.Key_Return)
                assert limit.property("value") == 0
                assert len(request(qtbot, view, "team.list", {"project_id": project_id})["teams"]) == 1
                assert calls(control) == recorded_before
                assert not (control / "continuation-started").exists()
                assert view.sessionId == session_id and view.draftText == draft
                click(window, visual_item(window.contentItem(), button_name))
                qtbot.waitUntil(lambda: (control / "continuation-started").exists(), timeout=SERVICE_WAIT_MS)
                continuation = read_marker(control, "continuation-started")
                child_id = continuation["team_id"]
                child = request(qtbot, view, "team.snapshot", {"team_id": child_id})["team"]
                assert child["round_limit"] == 0 and child["parent_team_id"] == parent_id
                assert child["round_index"] == 1 and child["steps"] == []
                assert child["model"] == parent["model"] == MODEL
                assert child["settings"] == parent["settings"]
                assert child["settings"]["reasoning_effort"] == "xhigh"
                assert request(qtbot, view, "team.snapshot", {"team_id": parent_id})["team"] == parent
                (control / "release-continuation").touch()

                def child_completed():
                    latest = request(qtbot, view, "team.snapshot", {"team_id": child_id})["team"]
                    return latest["state"] == "completed" and latest.get("cleanup_confirmed", True)

                qtbot.waitUntil(child_completed, timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: view.assistant.followup.get("team_id") == child_id
                                and view.assistant.followup.get("state") == "completed"
                                and not view.busy, timeout=SERVICE_WAIT_MS)
                child_followup = view.assistant.followup["run_id"]
                assert child_followup != parent_followup
                qtbot.waitUntil(lambda: any(row["kind"] == "assistant" and row["runId"] == child_followup
                                and row["body"] == "Synthetic round-limit Team followup: completed"
                                for row in view.transcript.rows), timeout=SERVICE_WAIT_MS)
                assert not any(row["kind"] == "user" and row["runId"] == child_followup for row in view.transcript.rows)
                child = request(qtbot, view, "team.snapshot", {"team_id": child_id})["team"]
                assert child["round_index"] == 2 and child["round_limit"] == 0 and child["summary"] == RESULT
                assert len(child["steps"]) == 1 and child["steps"][0]["state"] == "completed"
                fresh = read_marker(control, "child-reader-executed")
                assert VERIFIED in fresh["result"]["content"]
                assert fresh["run_id"] != reader["run_id"] and child["steps"][0]["run_id"] == fresh["run_id"]
                for run_id in (reader["run_id"], fresh["run_id"]):
                    events = request(qtbot, view, "run.events", {"run_id": run_id})["events"]
                    assert any(event["type"] == "tool.completed" and event["payload"].get("name") == "read_file"
                               and event["payload"]["result"]["ok"] for event in events)
                assert child["completion_evidence"]["basis"]["kind"] == "worker_assignments"
                assert request(qtbot, view, "team.snapshot", {"team_id": parent_id})["team"] == parent
                recorded = calls(control)
                assert [row["role"] for row in recorded] == [
                    "origin", "coordinator", "reader", "followup", "coordinator", "reader", "coordinator", "followup"]
                assert all(row["model"] == MODEL and row["effort"] == "xhigh" for row in recorded)
                assert all(row["account_id"] == recorded[0]["account_id"] for row in recorded)
                assert all(row["permission"] == "read_only" for row in recorded)
                qtbot.waitUntil(lambda: all((control / (row["run_id"] + ".closed")).exists() for row in recorded),
                                timeout=SERVICE_WAIT_MS)
                assert view.sessionId == session_id and view.draftText == draft
                assert view.selectedModel == MODEL and view.selectedEffort == "xhigh"
                assert view._session["settings"] == session_settings
                close_desktop(desktop)
                desktops.remove(desktop)
                reopened = open_desktop(paths)
                desktops.append(reopened)
                view, _, window = reopened
                qtbot.waitUntil(lambda: view.connected, timeout=SERVICE_WAIT_MS)
                view.openProject(project_id)
                qtbot.waitUntil(lambda: view.sessionId == session_id and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                assert visual_item(window.contentItem(), "prompt").property("text") == draft
                assert view.selectedModel == MODEL and view.selectedEffort == "xhigh"
                assert view._session["settings"] == session_settings
                restored = request(qtbot, view, "team.snapshot", {"team_id": child_id})["team"]
                assert restored["round_limit"] == 0 and restored["summary"] == RESULT
                assert len(request(qtbot, view, "team.list", {"project_id": project_id})["teams"]) == 2
                qtbot.wait(150)
                assert calls(control) == recorded
            except BaseException:
                if desktops:
                    failure_view = desktops[-1][0]
                    if failure_view.connected and failure_view.sessionId:
                        snapshot = request(qtbot, failure_view, "assistant.snapshot", {
                            "project_id": failure_view.project["id"], "session_id": failure_view.sessionId})
                        print("Synthetic failure followups:", snapshot.get("followups", []))
                        for followup in snapshot.get("followups", []):
                            if followup.get("run_id"):
                                evidence = request(qtbot, failure_view, "run.events", {"run_id": followup["run_id"]})
                                print("Synthetic followup evidence:", evidence["events"])
                log.flush()
                log.seek(0)
                print("Synthetic Team round-limit service log:\n" + log.read())
                raise
            finally:
                for name in ("release-origin", "release-continuation"):
                    (control / name).touch()
                for desktop in desktops:
                    close_desktop(desktop)
                stop_service(process)
