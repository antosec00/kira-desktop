"""Actual QML/IPC/scheduler regressions with disposable synthetic providers."""

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMetaObject

from kira.domain.paths import Paths
from test_ui_assistant_ipc import (
    click_visible, close_desktop, open_desktop, open_team_details, team_cards, visual_item,
)
from test_ui_support import SERVICE_WAIT_MS, isolated_service_environment, start_service, stop_service

FIXTURE = Path(__file__).parent / "fixtures/team_orchestration_service.py"
MODEL = "synthetic-team-orchestration-model"
MARKER = "SYNTHETIC_TEAM_ORCHESTRATION_EVIDENCE"
RESULT = "Synthetic team verified the recorded reader result."
CONTINUED = "Synthetic continuation reviewed the original evidence without replay."


def request(qtbot, view, method, params):
    received = {}
    assert view.client.request(method, params, lambda value: received.update(result=value),
                               lambda value: received.update(error=value))
    qtbot.waitUntil(lambda: bool(received), timeout=SERVICE_WAIT_MS)
    assert "error" not in received, received
    return received["result"]


def calls(control):
    return [json.loads(line) for line in (control / "provider-calls.jsonl").read_text().splitlines()]


def read_marker(control, name):
    return json.loads((control / name).read_text())


def test_corrective_team_plan_and_model_continuation_over_real_qml_ipc(qapp, qtbot, monkeypatch):
    """A mailbox is not work; continuation retains clean evidence without replay."""
    with tempfile.TemporaryDirectory(prefix="kira-team-ipc-") as directory:
        home = Path(directory)
        paths = Paths(*(home / part for part in ("config", "data", "state", "runtime")))
        control = home / "fixture-control"
        control.mkdir()
        project = home / "synthetic-project"
        project.mkdir()
        (project / "evidence.txt").write_text(MARKER + "\n", encoding="utf-8")
        environment = isolated_service_environment(
            monkeypatch, home, KIRA_TEST_TEAM_ORCHESTRATION_CONTROL=control)
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
                view.createProject("Synthetic Team orchestration", str(project), "", "read_only")
                qtbot.waitUntil(lambda: bool(view.project) and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                project_id = view.project["id"]
                view.setModel(MODEL)
                view.setEffort("xhigh")
                view.setTools(True)
                prompt = visual_item(window.contentItem(), "prompt")
                send = visual_item(window.contentItem(), "sendButton")
                states_before_reader = []

                def observe_current():
                    if not (control / "reader-executed").exists() and view.assistant.current:
                        states_before_reader.append(view.assistant.current.get("state"))

                def observe_event(event):
                    if event.get("type") == "team.delegation" and not (control / "reader-executed").exists():
                        states_before_reader.append(event.get("payload", {}).get("state"))

                view.assistant.changed.connect(observe_current)
                view.eventApplied.connect(observe_event)
                prompt.setProperty("text", "Delegate a synthetic review to a coordinator and reader.")
                qtbot.waitUntil(send.isEnabled, timeout=SERVICE_WAIT_MS)
                QMetaObject.invokeMethod(send, "clicked")
                qtbot.waitUntil(lambda: (control / "delegation-requested").exists(), timeout=SERVICE_WAIT_MS)
                initial = read_marker(control, "delegation-requested")
                session_id = view.sessionId
                qtbot.waitUntil(lambda: view.assistant.current.get("state") == "pending", timeout=SERVICE_WAIT_MS)
                assert visual_item(window.contentItem(), "assistantInlineNotice").isVisible()
                open_team_details(qtbot, window)
                assert visual_item(window.contentItem(), "assistantPendingHandoff").isVisible()
                assert visual_item(window.contentItem(), "assistantCancelTeam").isVisible()
                click_visible(window, "assistantTeamDetailsClose")
                draft = "Unsent fixture draft: città <literal> @evidence.txt"
                prompt.setProperty("text", draft)
                view.flushDrafts()
                (control / "release-origin").touch()

                qtbot.waitUntil(lambda: (control / "correction-ready").exists(), timeout=SERVICE_WAIT_MS)
                correction = read_marker(control, "correction-ready")
                team_id = correction["team_id"]
                qtbot.waitUntil(lambda: view.assistant.current.get("team_id") == team_id, timeout=SERVICE_WAIT_MS)
                assert (control / (initial["origin_run_id"] + ".closed")).exists()
                snapshot = request(qtbot, view, "team.snapshot", {"team_id": team_id})["team"]
                assert snapshot["state"] == "planning" and snapshot["round_index"] == 2
                assert snapshot["round_limit"] == 3 and snapshot["steps"] == []
                assert next(member for member in snapshot["members"] if member["id"] == "reader")["run_id"] is None
                assert not (control / "reader-executed").exists()
                events = request(qtbot, view, "team.events", {"team_id": team_id})["events"]
                feedback = [event["payload"] for event in events if event["type"] == "team.plan.feedback"]
                assert len(feedback) == 1 and feedback[0]["code"] == "missing_worker_assignments"
                assert feedback[0]["summary_verified"] is False and feedback[0]["repair_scheduled"] is True
                assert any(event["type"] == "team.message" for event in events)
                assert not any(event["type"] == "team.state" and event["payload"].get("state") == "completed"
                               for event in events)
                assert states_before_reader and "completed" not in states_before_reader
                assert view.assistant.current["state"] != "completed"
                assert len(team_cards(view)) == 1 and team_cards(view)[0]["status"] != "Completato"
                assert view.sessionId == session_id and prompt.property("text") == draft
                assert view.selectedModel == MODEL and view.selectedEffort == "xhigh"

                (control / "release-corrected-plan").touch()
                qtbot.waitUntil(lambda: (control / "reader-executed").exists(), timeout=SERVICE_WAIT_MS)
                reader = read_marker(control, "reader-executed")
                assert MARKER in reader["result"]["content"]
                read_events = request(qtbot, view, "run.events", {"run_id": reader["run_id"]})["events"]
                assert any(event["type"] == "tool.completed" and event["payload"].get("name") == "read_file"
                           and event["payload"]["result"]["ok"] for event in read_events)
                assert view.assistant.current["state"] != "completed"
                (control / "release-reader").touch()
                qtbot.waitUntil(lambda: view.assistant.current.get("state") == "completed"
                                and view.assistant.current.get("cleanup_confirmed") is True, timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: len(team_cards(view)) == 1 and RESULT in team_cards(view)[0]["body"], timeout=SERVICE_WAIT_MS)
                original = request(qtbot, view, "team.snapshot", {"team_id": team_id})["team"]
                assert original["round_index"] == 3 and len(original["steps"]) == 1
                assert original["steps"][0]["run_id"] == reader["run_id"]
                assert original["steps"][0]["state"] == "completed"
                assert original["completion_evidence"]["basis"]["kind"] == "worker_assignments"
                qtbot.waitUntil(lambda: view.assistant.followup.get("team_id") == team_id
                                and view.assistant.followup.get("state") == "completed"
                                and not view.busy, timeout=SERVICE_WAIT_MS)
                first_followup = view.assistant.followup["run_id"]
                qtbot.waitUntil(lambda: any(row["kind"] == "assistant" and row["runId"] == first_followup
                                and row["body"] == "Synthetic automatic Team followup: " + RESULT
                                for row in view.transcript.rows), timeout=SERVICE_WAIT_MS)
                assert not any(row["kind"] == "user" and row["runId"] == first_followup for row in view.transcript.rows)
                original_card = dict(team_cards(view)[0])
                assert not view.assistant.active and not view.busy
                assert view.sessionId == session_id and view.draftText == draft
                assert window.property("page") == 0

                # A deliberate new QML send replaces the previous unsent draft.
                # This ordinary model turn uses the new service-bound tool.
                prompt.setProperty("text", "Continue the team by summarizing its recorded result, without replay.")
                qtbot.waitUntil(send.isEnabled, timeout=SERVICE_WAIT_MS)
                QMetaObject.invokeMethod(send, "clicked")
                qtbot.waitUntil(lambda: (control / "resume-requested").exists(), timeout=SERVICE_WAIT_MS)
                resumed = read_marker(control, "resume-requested")
                assert resumed["delegation_id"] != initial["delegation_id"]
                qtbot.waitUntil(lambda: view.assistant.current.get("delegation_id") == resumed["delegation_id"]
                                and view.assistant.current.get("state") == "pending", timeout=SERVICE_WAIT_MS)
                assert visual_item(window.contentItem(), "assistantInlineNotice").isVisible()
                open_team_details(qtbot, window)
                pending = visual_item(window.contentItem(), "assistantPendingHandoff")
                assert pending.isVisible()
                assert pending.property("text") == (
                    "La ripresa del team partirà dopo la conclusione del turno corrente, conservando i risultati precedenti.")
                assert visual_item(window.contentItem(), "assistantCancelTeam").isVisible()
                click_visible(window, "assistantTeamDetailsClose")
                assert view.assistant.current["label"] == "Ripresa in coda"
                assert view.assistant.current["team_id"] is None
                assert len(request(qtbot, view, "team.list", {"project_id": project_id})["teams"]) == 1
                assert not (control / "continuation-started").exists()
                qtbot.waitUntil(lambda: len(team_cards(view)) == 2, timeout=SERVICE_WAIT_MS)
                old_card = next(card for card in team_cards(view) if card["toolId"] == initial["delegation_id"])
                assert old_card["body"] == original_card["body"] and old_card["command"] == team_id
                continuation_draft = "Draft preserved during continuation: è <second fixture>"
                prompt.setProperty("text", continuation_draft)
                view.flushDrafts()
                (control / "release-resume-origin").touch()

                qtbot.waitUntil(lambda: (control / "continuation-started").exists(), timeout=SERVICE_WAIT_MS)
                continued = read_marker(control, "continuation-started")
                assert read_marker(control, "resume-before-cleanup") == {"state": "pending", "team_id": None}
                assert continued["origin_cleanup"] == "stopped" and continued["parent_team_id"] == team_id
                child_id = continued["team_id"]
                child = request(qtbot, view, "team.snapshot", {"team_id": child_id})["team"]
                assert child["parent_team_id"] == team_id and child["steps"] == []
                assert continued["completion_basis"]["kind"] == "prior_worker_assignments"
                assert len([call for call in calls(control) if call["role"] == "reader"]) == 1
                (control / "release-continuation").touch()
                qtbot.waitUntil(lambda: view.assistant.current.get("delegation_id") == resumed["delegation_id"]
                                and view.assistant.current.get("state") == "completed"
                                and view.assistant.current.get("cleanup_confirmed") is True, timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: any(CONTINUED in card["body"] for card in team_cards(view)), timeout=SERVICE_WAIT_MS)
                child = request(qtbot, view, "team.snapshot", {"team_id": child_id})["team"]
                assert child["round_index"] == 1 and child["steps"] == []
                assert child["completion_evidence"]["basis"]["kind"] == "prior_worker_assignments"
                qtbot.waitUntil(lambda: view.assistant.followup.get("team_id") == child_id
                                and view.assistant.followup.get("state") == "completed"
                                and not view.busy, timeout=SERVICE_WAIT_MS)
                second_followup = view.assistant.followup["run_id"]
                assert second_followup != first_followup
                qtbot.waitUntil(lambda: any(row["kind"] == "assistant" and row["runId"] == second_followup
                                and row["body"] == "Synthetic automatic Team followup: " + CONTINUED
                                for row in view.transcript.rows), timeout=SERVICE_WAIT_MS)
                assert not any(row["kind"] == "user" and row["runId"] == second_followup for row in view.transcript.rows)
                unchanged = request(qtbot, view, "team.snapshot", {"team_id": team_id})["team"]
                assert unchanged["steps"] == original["steps"] and unchanged["summary"] == original["summary"]
                recorded = calls(control)
                assert [call["role"] for call in recorded] == [
                    "origin", "coordinator", "coordinator", "reader", "coordinator", "followup",
                    "origin", "coordinator", "followup"]
                assert all(call["model"] == MODEL and call["effort"] == "xhigh" for call in recorded)
                assert all(call["permission"] == "read_only" for call in recorded)
                assert all((control / (call["run_id"] + ".closed")).exists() for call in recorded)
                assert view.sessionId == session_id and prompt.property("text") == continuation_draft
                assert view.selectedModel == MODEL and view.selectedEffort == "xhigh"

                # Reopening reconstructs both service-authored cards; no work starts.
                close_desktop(desktop)
                desktops.remove(desktop)
                reopened = open_desktop(paths)
                desktops.append(reopened)
                view, _, window = reopened
                qtbot.waitUntil(lambda: view.connected, timeout=SERVICE_WAIT_MS)
                view.openProject(project_id)
                qtbot.waitUntil(lambda: view.sessionId == session_id and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: len(team_cards(view)) == 2, timeout=SERVICE_WAIT_MS)
                assert {card["command"] for card in team_cards(view)} == {team_id, child_id}
                assert any(RESULT in card["body"] for card in team_cards(view))
                assert any(CONTINUED in card["body"] for card in team_cards(view))
                assert visual_item(window.contentItem(), "prompt").property("text") == continuation_draft
                assert view.selectedModel == MODEL and view.selectedEffort == "xhigh"
                assert len(calls(control)) == len(recorded)
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
                print("Synthetic Team service log:\n" + log.read())
                raise
            finally:
                for name in ("release-origin", "release-corrected-plan", "release-reader",
                             "release-resume-origin", "release-continuation"):
                    (control / name).touch()
                for desktop in desktops:
                    close_desktop(desktop)
                stop_service(process)
