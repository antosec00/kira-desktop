"""Team presentation and IPC contracts; all service replies here are synthetic."""

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import (
    QCoreApplication, QEvent, QMetaObject, QObject, QPersistentModelIndex, QPoint, Qt,
)
from PySide6.QtNetwork import QLocalServer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from kira import __version__
from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.ipc import LocalClient
from kira.ui.team_models import TeamListModel
from test_ui_support import settled_point


def snapshot(team_id="team-a", *, state="running", seq=0, **overrides):
    return {"id": team_id, "project_id": "project-a", "goal": "Review ordinary project files",
            "mode": "manual", "state": state, "phase": "execution", "last_seq": seq,
            "round_index": 1, "round_limit": 3, "concurrency": 2, "backend": "codex",
            "model": "saved-model", "settings": {"reasoning_effort": "ultra"},
            "members": [{"id": "coordinator", "name": "Coordinator", "role": "Coordinate",
                         "instructions": "", "access": "read", "state": "waiting"},
                        {"id": "agent_1", "name": "Reviewer", "role": "Review files",
                         "instructions": "", "access": "read", "state": "running"}],
            "steps": [], "messages": [], "approvals": [], "omitted": {}, **overrides}


def team_event(seq, kind="team.state", team_id="team-a", **payload):
    return {"schema_version": 1, "team_id": team_id, "seq": seq, "type": kind,
            "timestamp": "2026-10-12T12:00:00Z", "payload": payload}


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def evaluate(item, expression):
    script = QQmlExpression(QQmlEngine.contextForObject(item), item, expression)
    result, _ = script.evaluate()
    assert not script.hasError(), script.error().toString()
    return result


class Replies:
    def __init__(self):
        self.requests = []
        self.snapshots = {}
        self.events = []
        self.delayed = set()
        self.pending = []

    def request(self, method, params, done, failed=None, **kwargs):
        self.requests.append((method, copy.deepcopy(params)))
        if method in self.delayed or method in {"team.start", "team.resume", "team.send_message",
                                               "team.cancel", "team.approve"}:
            self.pending.append((method, params, done, failed))
        elif method == "team.list":
            done({"teams": [copy.deepcopy(s) for s in self.snapshots.values()
                            if s["project_id"] == params["project_id"]]})
        elif method == "team.snapshot":
            done({"team": copy.deepcopy(self.snapshots[params["team_id"]])})
        elif method == "team.subscribe":
            events = [copy.deepcopy(e) for e in self.events if e["team_id"] == params["team_id"]
                      and e["seq"] > params["after_seq"]]
            done({"events": events[:2], "has_more": len(events) > 2,
                  "last_seq": events[-1]["seq"] if events else params["after_seq"]})
        return True

    def calls(self, method):
        return [params for name, params in self.requests if name == method]


@pytest.fixture
def team_workspace(qapp, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    workspace._project = {"id": "project-a", "name": "Synthetic project", "root": str(tmp_path),
                          "permission": "project_write", "memory": {}, "instructions": ""}
    workspace._backend = "codex"
    workspace._model = "saved-model"
    workspace._effort = "ultra"
    workspace._session = {"id": "ordinary-session", "settings": {"service_tier": "priority"}}
    workspace._connected_value = True
    replies = Replies()
    monkeypatch.setattr(workspace.client, "request", replies.request)
    workspace.changed.emit()
    yield workspace, workspace.teams, replies
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def select(teams, replies, value=None):
    value = value or snapshot()
    replies.snapshots[value["id"]] = value
    teams.activate()
    return value


@pytest.mark.parametrize("count", [1, 12])
def test_manual_exact_roster_and_preserved_provider_settings(team_workspace, count):
    workspace, teams, replies = team_workspace
    teams.setMode("manual")
    teams.setCount(count)
    teams.setConcurrency(min(count, 8))
    teams.setRoundLimit(0)
    teams.setDraftText("goal", "Read ordinary files and summarize")
    teams.setMember(0, "instructions", "Review independently")
    teams.startTeam()
    params, = replies.calls("team.start")
    assert params["max_agents"] == len(params["members"]) == count
    assert params["members"][0]["id"] == "coordinator"
    assert params["members"][0]["instructions"] == "Review independently"
    assert len({member["id"] for member in params["members"]}) == count
    assert params["settings"] == {"reasoning_effort": "ultra", "service_tier": "priority"}
    assert params["model"] == workspace.selectedModel == "saved-model"
    assert params["concurrency"] == min(count, 8) and params["round_limit"] == 0
    assert teams.busy


def test_automatic_start_has_cap_and_no_manual_roster_or_extra_confirmation(team_workspace):
    _, teams, replies = team_workspace
    teams.setCount(7)
    teams.setDraftText("goal", "Organize a project review")
    teams.startTeam()
    params, = replies.calls("team.start")
    assert params["mode"] == "automatic" and params["max_agents"] == 7
    assert "members" not in params
    value = snapshot(mode="automatic", state="planning")
    replies.snapshots[value["id"]] = value
    replies.pending.pop()[2]({"team": value})
    assert teams.selectedId == value["id"] and teams.canCancel
    assert not any("confirm" in method for method, _ in replies.requests)


def test_count_concurrency_round_bounds_and_current_config_inheritance(team_workspace):
    workspace, teams, _ = team_workspace
    teams.setCount(12)
    teams.setConcurrency(8)
    teams.setCount(1)
    assert teams.draft["concurrency"] == 1
    teams.setCount(0)
    teams.setCount(13)
    teams.setConcurrency(2)
    assert teams.draft["max_agents"] == teams.draft["concurrency"] == 1
    teams.setRoundLimit(10000)
    teams.setRoundLimit(10001)
    assert teams.draft["round_limit"] == 10000
    workspace._model, workspace._effort = "current-model", "high"
    teams.prepareDraft()
    assert teams.draft["model"] == "current-model" and teams.draft["reasoning_effort"] == "high"
    teams.setDraftText("model", "explicit-team-model")
    teams.setDraftText("reasoning_effort", "ultra")
    workspace._model, workspace._effort = "changed-again", "low"
    teams.prepareDraft()
    assert teams.draft["model"] == "explicit-team-model" and teams.draft["reasoning_effort"] == "ultra"


def test_read_only_and_missing_model_do_not_issue_start(team_workspace):
    workspace, teams, replies = team_workspace
    teams.setMode("manual")
    teams.setDraftText("goal", "Summarize project")
    teams.setMember(1, "access", "write")
    workspace._project["permission"] = "read_only"
    workspace.changed.emit()
    teams.startTeam()
    assert not replies.calls("team.start") and "sola lettura" in teams.error
    teams.setMember(1, "access", "read")
    teams.setMember(1, "access", "write")
    assert teams.draft["members"][1]["access"] == "read"
    teams.setDraftText("model", "")
    teams.startTeam()
    assert not replies.calls("team.start") and "scegli esplicitamente un modello" in teams.error


def test_drafts_per_project_and_ollama_effort_stays_empty_for_team(team_workspace):
    workspace, teams, _ = team_workspace
    teams.setDraftText("goal", "Draft A")
    workspace._project = {"id": "project-b", "permission": "read_only"}
    workspace._backend, workspace._model, workspace._effort = "ollama", "local-model", ""
    workspace.changed.emit()
    assert teams.draft["model"] == "local-model" and teams.draft["reasoning_effort"] == ""
    teams.setDraftText("goal", "Draft B")
    workspace._project = {"id": "project-a", "permission": "project_write"}
    workspace.changed.emit()
    assert teams.draft["goal"] == "Draft A" and teams.draft["model"] == "saved-model"


def test_subscription_pages_order_dedup_and_reconnect_cursor(team_workspace):
    _, teams, replies = team_workspace
    replies.events = [team_event(seq) for seq in range(1, 6)]
    select(teams, replies, snapshot(seq=5))
    assert [p["after_seq"] for p in replies.calls("team.subscribe")][:3] == [0, 2, 4]
    assert teams._cursors["team-a"] == teams.activity.rowCount() == 5
    teams._event(team_event(4))
    replies.delayed.add("team.subscribe")
    teams._event(team_event(7))
    teams._event(team_event(6))
    assert teams._cursors["team-a"] == teams.activity.rowCount() == 7
    assert replies.calls("team.subscribe")[-1]["after_seq"] == 5
    teams._disconnected()
    replies.delayed.clear()
    teams._ready()
    assert replies.calls("team.subscribe")[-1]["after_seq"] == 7
    assert teams.activity.rowCount() == 7


def test_stale_snapshot_and_project_response_cannot_replace_selection(team_workspace):
    workspace, teams, replies = team_workspace
    select(teams, replies, snapshot(seq=4))
    teams._apply_snapshot(snapshot(seq=3, state="failed"))
    assert teams.team["state"] == "running"
    replies.delayed.add("team.snapshot")
    teams.refresh()
    done = replies.pending[-1][2]
    workspace._project = {"id": "project-b", "permission": "read_only"}
    workspace.changed.emit()
    done({"team": snapshot(seq=5)})
    assert teams.selectedId == "" and not teams.team and not teams.loading


def test_start_disconnect_retries_identical_request_and_config(team_workspace):
    workspace, teams, replies = team_workspace
    teams.setDraftText("goal", "Review synthetic files")
    teams.startTeam()
    first = replies.pending.pop()
    workspace._connected_value = False
    first[3]({"code": "DISCONNECTED", "message": "Synthetic disconnect"})
    teams._disconnected()
    teams.setDraftText("model", "draft-changed-after-submit")
    workspace._connected_value = True
    teams._ready()
    a, b = replies.calls("team.start")
    assert a == b and a["request_id"] and a["model"] == "saved-model"
    assert teams.busy
    value = snapshot()
    replies.snapshots[value["id"]] = value
    replies.pending.pop()[2]({"team": value})
    assert teams.selectedId == "team-a" and not teams._pending


def test_message_error_preserves_draft_and_acceptance_keeps_newer_text(team_workspace):
    _, teams, replies = team_workspace
    select(teams, replies)
    teams.setMessageDraft("Please review section two")
    teams.setRecipientDraft("agent_1")
    teams.sendMessage("agent_1")
    assert teams.messagePending
    replies.pending.pop()[3]({"code": "INVALID_ARGUMENT", "message": "Ordinary validation error"})
    assert teams.messageDraft == "Please review section two" and not teams.messagePending
    teams.sendMessage("agent_1")
    teams.setMessageDraft("New follow-up draft")
    replies.pending.pop()[2]({"message": {"id": "message-a"}})
    assert teams.messageDraft == "New follow-up draft"
    assert replies.calls("team.send_message")[-1]["recipient_id"] == "agent_1"
    assert teams.recipientDraft == "agent_1"


def test_resume_is_new_continuation_and_cancel_uses_team_not_child_run(team_workspace):
    _, teams, replies = team_workspace
    old = select(teams, replies, snapshot(state="paused"))
    teams.setContinuationDraft("Continue with the recorded result")
    teams.resumeTeam()
    request, = replies.calls("team.resume")
    assert request["team_id"] == old["id"] and request["instruction"] == teams.continuationDraft
    assert "round_limit" not in request
    newer = snapshot("team-b", state="planning", parent_team_id=old["id"])
    replies.snapshots["team-b"] = newer
    replies.pending.pop()[2]({"team": newer})
    assert teams.selectedId == "team-b" and teams.team["parent_team_id"] == "team-a"
    teams.cancelTeam()
    assert replies.calls("team.cancel") == [{"team_id": "team-b"}]
    assert not replies.calls("run.cancel") and not replies.calls("run.start")


@pytest.mark.parametrize("initial, edited", [(1, 0), (0, 6), (3, 10000)])
def test_continuation_limit_edits_preserve_original_and_follow_team_identity(team_workspace, initial, edited):
    workspace, teams, replies = team_workspace
    original = select(teams, replies, snapshot(state="paused", round_limit=initial))
    assert teams.continuationRoundLimit == initial
    teams.setContinuationRoundLimit(edited)
    teams.setContinuationRoundLimit(-1)
    teams.setContinuationRoundLimit(10001)
    teams.setContinuationRoundLimit(True)
    teams.refresh()
    assert teams.continuationRoundLimit == edited
    assert original["round_limit"] == teams.team["round_limit"] == initial
    assert not replies.calls("team.resume")
    other = snapshot("team-b", state="paused", round_limit=4)
    replies.snapshots[other["id"]] = other
    teams.selectTeam(other["id"])
    assert teams.continuationRoundLimit == 4
    teams.selectTeam(original["id"])
    assert teams.continuationRoundLimit == edited
    teams.resumeTeam()
    request = replies.calls("team.resume")[-1]
    assert request["round_limit"] == edited
    assert workspace.selectedModel == "saved-model" and workspace.selectedEffort == "ultra"


@pytest.mark.parametrize("code", ["DISCONNECTED", "INTERRUPTED", "DELIVERY_UNKNOWN"])
def test_continuation_uncertain_retry_keeps_exact_limit_and_instruction(team_workspace, code):
    _, teams, replies = team_workspace
    select(teams, replies, snapshot(state="paused", round_limit=1))
    teams.setContinuationRoundLimit(0)
    teams.setContinuationDraft("Continue with recorded results")
    teams.resumeTeam()
    first = replies.pending.pop()
    assert not teams.canResume and not teams.canEditContinuation
    first[3]({"code": code, "message": "No confirmation"})
    assert teams.canResume and teams.continuationOutcomeUncertain and not teams.canEditContinuation
    teams.setContinuationRoundLimit(8)
    teams.setContinuationDraft("Different instruction")
    teams.refresh()
    assert teams.continuationRoundLimit == 0
    assert teams.continuationDraft == "Continue with recorded results"
    teams.resumeTeam()
    second = replies.pending.pop()
    assert second[1] == first[1]
    second[2]({"ok": False, "error": {"code": "DELIVERY_UNKNOWN", "message": "Still unknown"}})
    assert teams.continuationOutcomeUncertain
    teams.resumeTeam()
    third = replies.pending.pop()
    assert third[1] == first[1]
    newer = snapshot("team-b", state="paused", round_limit=0, parent_team_id="team-a")
    replies.snapshots[newer["id"]] = newer
    third[2]({"team": newer})
    assert teams.selectedId == "team-b" and teams.continuationRoundLimit == 0
    assert not teams.continuationOutcomeUncertain and teams.canEditContinuation
    teams.selectTeam("team-a")
    assert teams.continuationRoundLimit == 1


def test_continuation_disconnect_reconnect_keeps_submitted_zero(team_workspace):
    workspace, teams, replies = team_workspace
    select(teams, replies, snapshot(state="paused", round_limit=2))
    teams.setContinuationRoundLimit(0)
    teams.resumeTeam()
    first = replies.pending.pop()
    workspace._connected_value = False
    first[3]({"code": "DISCONNECTED", "message": "Disconnected"})
    teams._disconnected()
    teams.setContinuationRoundLimit(7)
    workspace._connected_value = True
    teams._ready()
    assert replies.calls("team.resume") == [first[1], first[1]]


def test_continuation_deterministic_failure_unlocks_limit_without_editing_parent(team_workspace):
    _, teams, replies = team_workspace
    select(teams, replies, snapshot(state="paused", round_limit=1))
    teams.setContinuationRoundLimit(0)
    teams.resumeTeam()
    first = replies.pending.pop()
    first[3]({"code": "BUSY", "message": "Project is busy"})
    assert teams.canEditContinuation and teams.continuationRoundLimit == 0
    teams.setContinuationRoundLimit(5)
    teams.resumeTeam()
    second = replies.pending.pop()
    assert second[1]["request_id"] != first[1]["request_id"]
    assert second[1]["round_limit"] == 5 and teams.team["round_limit"] == 1


def test_approvals_are_correlated_by_run_and_stable_on_refresh(team_workspace):
    _, teams, replies = team_workspace
    approvals = [{"run_id": run, "approval_id": "same-provider-id", "member_id": member,
                  "request": {"message": "Review ordinary change"}}
                 for run, member in (("run-a", "coordinator"), ("run-b", "agent_1"))]
    select(teams, replies, snapshot(approvals=approvals))
    assert teams.approvalModel.rowCount() == 2
    teams.approveRequest("foreign-run", "same-provider-id", "accept")
    assert not replies.calls("team.approve")
    teams.approveRequest("run-b", "same-provider-id", "decline")
    params, = replies.calls("team.approve")
    assert params == {"team_id": "team-a", "run_id": "run-b",
                      "approval_id": "same-provider-id", "decision": "decline"}
    teams.approveRequest("run-b", "same-provider-id", "accept")
    assert len(replies.calls("team.approve")) == 1


def test_truncated_approval_cannot_be_accepted_but_can_be_declined(team_workspace):
    _, teams, replies = team_workspace
    select(teams, replies, snapshot(approvals=[{
        "run_id": "run-a", "approval_id": "a", "member_id": "coordinator",
        "request": {"message": "Incomplete request preview", "truncated": True}}]))
    teams.approveRequest("run-a", "a", "accept")
    assert not replies.calls("team.approve") and "incompleta" in teams.error
    teams.approveRequest("run-a", "a", "decline")
    assert replies.calls("team.approve") == [{"team_id": "team-a", "run_id": "run-a",
                                              "approval_id": "a", "decision": "decline"}]


def test_keyed_rows_and_message_refresh_never_reset_models(team_workspace):
    _, teams, replies = team_workspace
    message = {"id": "m-a", "sender_name": "Reviewer", "recipient_id": "all",
               "content": "Ordinary <comparison> is literal", "created_at": "12:00"}
    select(teams, replies, snapshot(messages=[message]))
    resets = []
    for model in (teams.messages, teams.memberModel, teams.stepModel, teams.approvalModel):
        model.modelReset.connect(lambda: resets.append(True))
    teams._apply_snapshot(snapshot(seq=1, messages=[message]))
    assert not resets and teams.messages.rowCount() == 1
    owner = QObject()
    rows = TeamListModel(owner)
    rows.modelReset.connect(lambda: resets.append(True))
    rows.sync([{"id": "a"}, {"id": "b"}, {"id": "c"}])
    persistent = QPersistentModelIndex(rows.index(0))
    rows.sync([{"id": "c"}, {"id": "a", "state": "completed"}])
    assert rows.rows == [{"id": "c"}, {"id": "a", "state": "completed"}]
    assert not resets and persistent.isValid() and persistent.row() == 1


def test_team_meta_properties_keep_qml_types_and_notify(team_workspace):
    _, teams, _ = team_workspace
    for name, type_name in (("draft", "QVariantMap"), ("team", "QVariantMap"),
                            ("models", "QVariantList"), ("members", "QVariantList"),
                            ("approvals", "QVariantList")):
        prop = teams.metaObject().property(teams.metaObject().indexOfProperty(name))
        assert prop.typeName() == type_name and prop.hasNotifySignal()
    assert isinstance(teams.draft, dict) and isinstance(teams.members, list)


def test_activity_filters_stream_references_without_skipping_replay_cursor(team_workspace):
    _, teams, replies = team_workspace
    select(teams, replies)
    for seq in range(1, 790):
        teams._event(team_event(seq, "member.activity", member_id="agent_1",
                               run_id="run-a", run_seq=seq, event_type="message.delta"))
    teams._event(team_event(790, "team.result", summary="Completed ordinary review"))
    assert teams._cursors["team-a"] == 790
    assert teams.activity.rowCount() == 1 and not teams.activity.omitted
    assert "Completed ordinary review" in teams.activity.rows[0]["body"]
    assert teams.historyNotice == ""
    teams._event(team_event(791, "member.activity", mailbox_error="INVALID_ARGUMENT"))
    assert teams.activity.rowCount() == 2


def test_team_plan_feedback_and_unverified_historical_conclusion_remain_explicit(team_workspace):
    _, teams, replies = team_workspace
    select(teams, replies)
    teams._event(team_event(1, "team.plan.feedback", repair_scheduled=True,
                           summary="<literal coordinator claim>", summary_verified=False))
    assert teams.activity.rows[0]["title"] == "Correzione degli incarichi"
    assert "piano corretto" in teams.activity.rows[0]["body"]
    assert "<literal coordinator claim>" in teams.activity.rows[0]["body"]
    value = snapshot(state="completed", seq=1, phase="done", summary="Recorded <claim>",
                     completion_evidence={"coordinator_only_conclusion": True, "basis": None,
                                          "worker_assignments_completed": 0})
    teams._apply_snapshot(value)
    assert "Conclusione da verificare" in teams.stateText
    assert "Recorded <claim>" in teams.outcomeText
    assert teams.team["state"] == "completed" and teams.team["summary"] == "Recorded <claim>"


def test_coordinator_plan_preview_and_history_labels_are_explicit(team_workspace):
    _, teams, replies = team_workspace
    value = snapshot()
    value["members"][0]["last_output"] = json.dumps({"summary": "Review plan", "tasks": []})
    value["omitted"] = {"messages": 10, "steps": 4}
    select(teams, replies, value)
    assert teams.members[0]["output_preview"] == "Review plan"
    assert "Chat: 10" in teams.historyNotice and "Incarichi: 4" in teams.historyNotice
    assert "600" not in teams.historyNotice


def test_chat_history_notice_counts_rows_actually_retained_in_the_view(team_workspace):
    _, teams, replies = team_workspace
    messages = [{"id": f"m-{i}", "content": f"Message {i}"} for i in range(5)]
    select(teams, replies, snapshot(messages=messages))
    teams._apply_snapshot(snapshot(seq=1, messages=messages[-2:], omitted={"messages": 3}))
    assert teams.messages.rowCount() == 5 and not teams.historyNotice


def test_run_evidence_only_reads_linked_runs_and_keeps_its_own_cursor(team_workspace):
    _, teams, replies = team_workspace
    value = snapshot(steps=[{"id": "task-a", "round_index": 1, "member_id": "agent_1",
                            "title": "Review", "state": "completed", "run_id": "run-a"}])
    select(teams, replies, value)
    replies.delayed.add("run.events")
    teams.openRunEvidence("foreign-run")
    assert not replies.calls("run.events")
    teams.openRunEvidence("run-a")
    assert teams.evidenceLoading
    assert replies.calls("run.events") == [{"run_id": "run-a", "after_seq": 0, "limit": 250}]
    first = {"run_id": "run-a", "seq": 1, "type": "tool.completed", "timestamp": "12:00",
             "payload": {"tool_id": "tool-a", "result": "Verified synthetic file"}}
    replies.pending.pop()[2]({"events": [first], "has_more": True})
    assert teams.evidenceMore and teams.evidence.rowCount() == 1
    assert teams.activity.rowCount() == teams._cursors["team-a"] == 0
    teams.loadRunEvidence()
    assert replies.calls("run.events")[-1]["after_seq"] == 1
    done = replies.pending.pop()[2]
    teams.closeRunEvidence()
    done({"events": [{**first, "seq": 2}], "has_more": False})
    assert not teams.evidenceRunId and teams.evidence.rowCount() == 0


def test_ordinary_project_refresh_excludes_managed_team_sessions(team_workspace):
    workspace, teams, replies = team_workspace
    workspace._session = {}
    replies.delayed.add("project.snapshot")
    workspace.openProject("project-a")
    replies.pending.pop()[2]({"project": workspace.project, "sessions": [
        {"id": "internal", "team_id": "team-a", "backend": "codex", "model": "other-model",
         "settings": {"reasoning_effort": "low"}}], "runs": [
        {"id": "internal-run", "team_id": "team-a", "state": "running"}]})
    assert not workspace.sessions and not workspace._runs
    assert workspace.selectedModel == "saved-model" and workspace.selectedEffort == "ultra"
    select(teams, replies)
    workspace.send("Do more work")
    assert not replies.calls("run.start") and "team" in workspace.notice.lower()


def test_local_client_routes_team_notifications_separately(qapp, qtbot, tmp_path):
    server = QLocalServer()
    assert server.listen(str(tmp_path / "team-fixture.sock"))
    client = LocalClient(server.fullServerName())
    peers = []
    def accepted():
        peer = server.nextPendingConnection()
        peers.append(peer)
        def read():
            request = json.loads(peer.readAll().data())
            peer.write(json.dumps({"version": 1, "id": request["id"], "result": {
                "version": 1, "app_version": __version__}}).encode() + b"\n")
        peer.readyRead.connect(read)
    server.newConnection.connect(accepted)
    team_events, ordinary_events = [], []
    client.teamEvent.connect(team_events.append)
    client.eventReceived.connect(ordinary_events.append)
    try:
        client.connect_service()
        qtbot.waitUntil(lambda: client.compatible)
        event = team_event(1, content="Caffè <nota>")
        packet = json.dumps({"version": 1, "team_event": event}, ensure_ascii=False).encode() + b"\n"
        split = packet.index("è".encode()) + 1
        peers[0].write(packet[:split])
        peers[0].flush()
        qtbot.wait(10)
        assert not team_events
        peers[0].write(packet[split:])
        qtbot.waitUntil(lambda: team_events == [event])
        assert not ordinary_events
        # A frame split inside a UTF-8 character is not a protocol error.
        assert client.compatible and not client._buffer
    finally:
        client.close()
        client.deleteLater()
        server.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def team_ui(team_workspace, qtbot):
    workspace, teams, replies = team_workspace
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setProperty("page", 4)
    qtbot.waitUntil(lambda: window.findChild(QQuickItem, "teamView") is not None)
    yield workspace, teams, replies, window
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size,font_size", [((1380, 900), 13), ((880, 620), 18)])
def test_existing_team_limit_zero_keyboard_and_pointer_resume_fit_window(team_ui, qtbot, size, font_size):
    _, teams, replies, window = team_ui
    window.resize(*size)
    QQmlEngine.contextForObject(window).contextProperty("appearance").setOption("ui_size", font_size)
    original = select(teams, replies, snapshot(state="paused", round_limit=1, phase="awaiting_user"))
    qtbot.wait(40)
    control = window.findChild(QQuickItem, "teamContinuationRoundLimitInput")
    assert control.isVisible() and control.isEnabled() and control.property("value") == 1
    content = control.property("contentItem")
    center = content.mapToScene(content.boundingRect().center())
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(round(center.x()), round(center.y())))
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_0)
    teams.refresh()
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert teams.continuationRoundLimit == 0 and teams.team["round_limit"] == original["round_limit"] == 1
    assert not replies.calls("team.resume")
    button = window.findChild(QQuickItem, "resumeTeamButton")
    for item in (control, button):
        bounds = item.mapRectToItem(window.contentItem(), item.boundingRect())
        assert bounds.left() >= 0 and bounds.right() <= window.width()
        assert bounds.top() >= 0 and bounds.bottom() <= window.height()
    center = button.mapToScene(button.boundingRect().center())
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(round(center.x()), round(center.y())))
    assert replies.calls("team.resume")[-1]["round_limit"] == 0


@pytest.mark.parametrize("size", [(1380, 880), (880, 620)])
def test_editor_typing_preserves_member_focus_and_model_draft(team_ui, qtbot, size):
    workspace, teams, _, window = team_ui
    window.resize(*size)
    teams.setMode("manual")
    teams.setCount(1)
    editor = window.findChild(QObject, "teamEditor")
    assert editor and QMetaObject.invokeMethod(editor, "open")
    qtbot.waitUntil(lambda: editor.property("opened"))
    role = visual_item(window.contentItem(), "teamMemberRole_0")
    role.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for character in "Reviewer with tests":
        QTest.keyClick(window, character)
        workspace.changed.emit()
        QCoreApplication.processEvents()
        assert role is visual_item(window.contentItem(), "teamMemberRole_0")
        assert role.hasActiveFocus()
    assert role.property("text") == teams.draft["members"][0]["role"] == "Reviewer with tests"
    model = window.findChild(QQuickItem, "teamModelInput")
    content = model.property("contentItem")
    assert content.property("placeholderText") == "Seleziona o scrivi il modello"
    assert model.property("editText") == "saved-model"
    content.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for character in "explicit-codex-model":
        QTest.keyClick(window, character)
    window.findChild(QQuickItem, "teamGoalInput").forceActiveFocus()
    assert teams.draft["model"] == "explicit-codex-model"
    assert window.findChild(QQuickItem, "teamRoundLimitInput").property("to") == 10000
    assert window.findChild(QQuickItem, "startTeamButton").isEnabled()


def test_refresh_keeps_message_draft_focus_and_scroll_position(team_ui, qtbot):
    _, teams, replies, window = team_ui
    messages = [{"id": f"m-{i}", "sender_name": "Reviewer", "recipient_id": "all",
                 "content": f"Ordinary message {i}\n" * 5, "created_at": "12:00"} for i in range(30)]
    value = select(teams, replies, snapshot(messages=messages))
    window.findChild(QQuickItem, "teamTabs").setProperty("currentIndex", 1)
    qtbot.wait(50)
    view = window.findChild(QQuickItem, "teamView")
    view.setProperty("followMessages", False)
    message_list = window.findChild(QQuickItem, "teamMessages")
    QMetaObject.invokeMethod(message_list, "positionViewAtBeginning")
    draft = window.findChild(QQuickItem, "teamMessageDraft")
    draft.forceActiveFocus()
    for character in "Unsent draft":
        QTest.keyClick(window, character)
    y = message_list.property("contentY")
    teams._apply_snapshot({**value, "last_seq": 1})
    qtbot.wait(20)
    assert draft.hasActiveFocus() and draft.property("text") == teams.messageDraft == "Unsent draft"
    assert message_list.property("contentY") == pytest.approx(y, abs=1)


@pytest.mark.parametrize("size", [(1380, 880), (880, 620)])
def test_long_goal_has_bounded_header_and_scrollable_literal_detail(team_ui, qtbot, size):
    _, teams, replies, window = team_ui
    window.resize(*size)
    goal = ("Ordinary <comparison> in a long review objective. " * 1400)[:64000]
    select(teams, replies, snapshot(goal=goal))
    qtbot.wait(30)
    preview = window.findChild(QQuickItem, "teamGoalPreview")
    assert preview.property("truncated") and preview.height() < 90
    read_goal = window.findChild(QQuickItem, "teamReadGoal")
    assert read_goal.isVisible()
    tabs = window.findChild(QQuickItem, "teamTabs")
    assert tabs.mapToScene(tabs.boundingRect().bottomRight()).y() < window.height() - 120
    dialog = window.findChild(QObject, "teamGoalDialog")
    QMetaObject.invokeMethod(dialog, "open")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    text = window.findChild(QQuickItem, "teamFullGoal")
    expression = QQmlExpression(QQmlEngine.contextForObject(text), text, "Number(textFormat)")
    assert text.property("text") == goal and expression.evaluate()[0] == 0
    assert dialog.property("height") <= window.height() - 32


def test_qml_run_evidence_opens_read_only_and_returns_recorded_payload(team_ui, qtbot):
    _, teams, replies, window = team_ui
    value = snapshot()
    value["members"][0]["run_id"] = "run-a"
    select(teams, replies, value)
    replies.delayed.add("run.events")
    teams.openRunEvidence("run-a")
    dialog = window.findChild(QObject, "teamEvidenceDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    replies.pending.pop()[2]({"events": [{"run_id": "run-a", "seq": 1,
        "type": "tool.completed", "timestamp": "12:00", "payload": {"result": "ordinary <value>"}}],
        "has_more": False})
    assert "ordinary <value>" in teams.evidence.rows[0]["body"]
    assert not replies.calls("run.start") and not replies.calls("run.cancel")
    QMetaObject.invokeMethod(dialog, "close")
    qtbot.waitUntil(lambda: not teams.evidenceRunId)


def test_readable_evidence_preserves_original_copy_and_never_sends_provider_requests(team_ui, qtbot):
    _, teams, replies, window = team_ui
    value = snapshot()
    value["members"][0]["run_id"] = "run-a"
    select(teams, replies, value)
    replies.delayed.add("run.events")
    teams.openRunEvidence("run-a")
    event = {"run_id": "run-a", "seq": 7, "type": "context.supplied",
             "timestamp": "2026-10-15T12:00:00.123456Z",
             "payload": {"content": 'First line\n{"environment":{"model":"fixture-model"}}\n<literal>',
                         "command": r"printf '\n'"}}
    before = copy.deepcopy(event)
    replies.pending.pop()[2]({"events": [event], "has_more": False})
    qtbot.waitUntil(lambda: visual_item(window.contentItem(), "evidenceBody_7") is not None)
    body = visual_item(window.contentItem(), "evidenceBody_7")
    assert "First line\n" in body.property("text")
    assert '\n      "environment": {' in body.property("text")
    assert "<literal>" in body.property("text")
    assert evaluate(body, "textFormat === TextEdit.PlainText")
    QMetaObject.invokeMethod(visual_item(window.contentItem(), "evidenceCopy_7"), "clicked")
    assert json.loads(QGuiApplication.clipboard().text()) == event["payload"]
    QMetaObject.invokeMethod(visual_item(window.contentItem(), "evidenceJsonButton"), "clicked")
    qtbot.wait(30)
    assert json.loads(body.property("text")) == event["payload"]
    assert visual_item(window.contentItem(), "evidenceTime_7").property("text") == "2026-10-15  12:00:00.123 UTC"
    QMetaObject.invokeMethod(visual_item(window.contentItem(), "evidenceToggle_7"), "clicked")
    assert not body.isVisible()
    QMetaObject.invokeMethod(visual_item(window.contentItem(), "evidenceToggle_7"), "clicked")
    assert body.isVisible()
    assert event == before
    assert replies.calls("run.events") == [{"run_id": "run-a", "after_seq": 0, "limit": 250}]
    assert not replies.calls("run.start") and not replies.calls("run.cancel")


def test_evidence_layout_and_zoom_fit_narrow_window_without_changing_appearance(team_ui, qtbot):
    workspace, teams, replies, window = team_ui
    window.resize(880, 620)
    value = snapshot()
    value["members"][0]["run_id"] = "run-a"
    select(teams, replies, value)
    replies.delayed.add("run.events")
    preferences = copy.deepcopy(workspace.uiPreferences)
    teams.openRunEvidence("run-a")
    replies.pending.pop()[2]({"events": [{"run_id": "run-a", "seq": 1, "type": "tool.completed",
        "timestamp": "2026-10-15T12:00:00Z", "payload": {"output": "Line one\nLine two"}}], "has_more": False})
    qtbot.waitUntil(lambda: visual_item(window.contentItem(), "evidenceBody_1") is not None)
    body = visual_item(window.contentItem(), "evidenceBody_1")
    old_size = body.property("font").pixelSize()
    QMetaObject.invokeMethod(visual_item(window.contentItem(), "evidenceFontLarger"), "clicked")
    assert body.property("font").pixelSize() == old_size + 1
    for name in ("evidenceReadableButton", "evidenceJsonButton", "evidenceFontLarger", "teamLoadRunEvidence"):
        item = visual_item(window.contentItem(), name)
        corner = item.mapToScene(item.boundingRect().bottomRight())
        assert corner.x() <= window.width() and corner.y() <= window.height()
    assert workspace.uiPreferences == preferences


def test_team_menus_have_literal_labels_visible_indicators_and_effective_contrast(team_ui, qtbot):
    workspace, teams, replies, window = team_ui
    workspace._catalogs = {"codex": {"available": True, "account": {"connected": True}, "models": [
        {"id": "saved-model", "name": "Saved <model>", "supported_reasoning_efforts": ["ultra"]}]}}
    workspace.changed.emit()
    select(teams, replies)
    teams.setMode("manual")
    teams.setCount(1)
    editor = window.findChild(QObject, "teamEditor")
    expected = {"teamHistory": "In esecuzione", "teamMessageRecipient": "Tutto il team",
                "teamModelInput": "Saved <model>", "teamEffortInput": "Predefinito del provider",
                "teamMemberAccess_0": "Lettura"}
    def luminance(rgb):
        values = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(values, (0.2126, 0.7152, 0.0722), strict=True))

    def row_center(combo):
        return json.loads(evaluate(combo, """
            (function() {
                const item = popup.opened ? popup.contentItem.itemAtIndex(0) : null;
                if (!item || item.width <= 0 || item.height <= 0) return "null";
                const p = item.mapToItem(null, item.width / 2, item.height / 2);
                return JSON.stringify([p.x, p.y]);
            })()
        """))

    for name, label in expected.items():
        if name == "teamModelInput":
            QMetaObject.invokeMethod(editor, "open")
            qtbot.waitUntil(lambda: editor.property("opened"))
        if name == "teamMessageRecipient":
            window.findChild(QQuickItem, "teamTabs").setProperty("currentIndex", 1)
        combo = visual_item(window.contentItem(), name)
        assert combo is not None
        indicator = json.loads(evaluate(combo, """
            JSON.stringify([[indicator.color.r, indicator.color.g, indicator.color.b],
                            [palette.button.r, palette.button.g, palette.button.b]])
        """))
        light, dark = sorted(map(luminance, indicator), reverse=True)
        assert (light + 0.05) / (dark + 0.05) >= 4.5
        evaluate(combo, "popup.open()")
        # The popup sizes itself over a few frames: use its first row once it stopped moving.
        settled_point(qtbot, lambda: row_center(combo))
        for state in ("normal", "highlighted", "focus", "pressed"):
            evaluate(combo, """
                (function() {
                    const item = popup.contentItem.itemAtIndex(0);
                    item.focus = false;
                    item.forceActiveFocus(Qt.%s);
                    item.highlighted = %s;
                    item.down = undefined;
                })()
            """ % ("TabFocusReason" if state == "focus" else "MouseFocusReason",
                   "true" if state == "highlighted" else "false"))
            if state == "pressed":
                QTest.mousePress(window, Qt.MouseButton.LeftButton,
                                 pos=settled_point(qtbot, lambda: row_center(combo)))
            values = json.loads(evaluate(combo, """
                (function() {
                    const item = popup.contentItem.itemAtIndex(0);
                    const fg = item.contentItem.color;
                    const bg = item.background.color;
                    return JSON.stringify({text: item.contentItem.text, format: item.contentItem.textFormat,
                        fg: [fg.r, fg.g, fg.b], bg: [bg.r, bg.g, bg.b], pressed: item.pressed,
                        focus: item.visualFocus, opacity: item.background.opacity, alpha: bg.a});
                })()
            """))
            assert label in values["text"] and values["format"] == 0
            assert values["pressed"] == (state == "pressed"), (name, state, values)
            assert values["focus"] == (state == "focus")
            assert values["opacity"] == values["alpha"] == 1
            light, dark = sorted((luminance(values["fg"]), luminance(values["bg"])), reverse=True)
            assert (light + 0.05) / (dark + 0.05) >= 4.5, (name, state, values)
            if state == "pressed":
                QTest.mouseMove(window, QPoint(1, 1))
                QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        evaluate(combo, "popup.close()")
    assert teams.draft["model"] == "saved-model" and teams.draft["reasoning_effort"] == "ultra"
