"""Synthetic UI/RPC checks for scoped chat-native managed Team controls."""

import copy
import hashlib
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.drafts import DraftStore
from kira.ui.assistant_controller import delegation_card


def delegation(**changes):
    return {"id": "delegation-a", "delegation_id": "delegation-a", "project_id": "project-a",
            "session_id": "chat-a", "origin_run_id": "run-a", "team_id": "team-a",
            "goal": "Review synthetic project files", "state": "running", "summary": "",
            "model": "chosen-model", "reasoning_effort": "xhigh", "members_count": 3,
            "completed_steps": 1, "total_steps": 4, "can_cancel": True, "can_message": True,
            "can_resume": False, "cleanup_confirmed": False, **changes}


class Replies:
    def __init__(self):
        self.calls = []
        self.pending = []

    def request(self, method, params, done, failed=None, **kwargs):
        self.calls.append((method, copy.deepcopy(params)))
        if method.startswith("assistant."):
            self.pending.append((method, params, done, failed))
        return True

    def snapshot(self, rows=(), **environment):
        request = next(item for item in self.pending if item[0] == "assistant.snapshot")
        self.pending.remove(request)
        request[2]({"environment": {"application": "Kira Desktop", "capabilities": {}, **environment},
                    "delegations": list(rows)})


@pytest.fixture
def assistant_workspace(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project-a", "name": "Synthetic", "permission": "project_write", "root": str(tmp_path)}
    workspace._session = {"id": "chat-a", "project_id": "project-a", "backend": "codex",
                          "model": "chosen-model", "settings": {"reasoning_effort": "xhigh"}}
    workspace._backend, workspace._model, workspace._effort = "codex", "chosen-model", "xhigh"
    workspace._connected_value = True
    replies = Replies()
    monkeypatch.setattr(workspace.client, "request", replies.request)
    workspace.changed.emit()
    workspace.assistant.refresh()
    yield workspace, workspace.assistant, replies
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_context_snapshot_never_adopts_another_chat_and_discards_stale_reply(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    stale = replies.pending.pop(0)
    workspace._session = {**workspace._session, "id": "chat-b"}
    workspace.changed.emit()
    assistant.refresh()
    stale[2]({"environment": {"secret": "old context"}, "delegations": [delegation()]})
    assert assistant.environment == {} and assistant.current == {}
    replies.snapshot([delegation(), delegation(id="d-b", delegation_id="d-b", session_id="chat-b")])
    assert assistant.current["delegation_id"] == "d-b"
    assert len(assistant.delegations) == 1
    assert workspace.sessionId == "chat-b"


def test_idle_no_poll_and_relevant_events_coalesce_with_one_inflight(assistant_workspace, qtbot):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot()
    assert not assistant._timer.isActive()
    workspace._runs = {"run-a": {"id": "run-a", "session_id": "chat-a", "state": "completed"},
                       "foreign": {"id": "foreign", "session_id": "chat-b", "state": "completed"}}
    assistant._event({"run_id": "not-this-project", "type": "team.delegation"})
    assert not assistant._coalesce.isActive()
    for _ in range(20):
        assistant._event({"run_id": "run-a", "type": "team.delegation"})
    # The coalescing timer issues one read, then nothing else is scheduled.
    qtbot.waitUntil(lambda: len(replies.pending) == 1, timeout=2000)
    qtbot.wait(150)
    assert len(replies.pending) == 1
    assistant.refresh()
    assistant.refresh()
    assert len(replies.pending) == 1
    replies.snapshot([delegation(state="pending", team_id=None)])
    qtbot.waitUntil(lambda: len(replies.pending) == 1, timeout=2000)
    qtbot.wait(150)
    assert len(replies.pending) == 1
    replies.snapshot([delegation(state="completed", can_cancel=False, can_message=False)])
    assert not assistant._timer.isActive()


def test_active_poll_stops_on_disconnect_and_cannot_mutate_offline(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="finishing")])
    assert assistant.active and assistant._timer.isActive()
    workspace._connected_value = False
    workspace.client.disconnected.emit()
    assert not assistant._timer.isActive() and not assistant._coalesce.isActive()
    before = len(replies.calls)
    assistant.cancel()
    assistant.sendMessage("coordinator", "Instructions")
    assert len(replies.calls) == before


def test_resume_requires_clean_settled_team_and_explicit_user_command(assistant_workspace):
    _, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="interrupted", can_resume=True)])
    assert not assistant.canResume
    assistant.refresh()
    replies.snapshot([delegation(state="interrupted", can_resume=True, cleanup_confirmed=True)])
    assert assistant.canResume
    assert not any(name == "assistant.resume" for name, _ in replies.calls)
    assistant.resume()
    name, params = replies.calls[-1]
    assert name == "assistant.resume"
    assert params == {"project_id": "project-a", "session_id": "chat-a", "delegation_id": "delegation-a",
                      "request_id": params["request_id"]}
    assert assistant.commandPending


def test_message_retry_reuses_request_and_preserves_edited_draft(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation()])
    assistant.setMessageDraft("Read the existing evidence")
    assistant.sendMessage("coordinator", assistant.messageDraft)
    first = replies.pending.pop()
    first[3]({"code": "DISCONNECTED", "message": "Unconfirmed"})
    assistant.sendMessage("coordinator", assistant.messageDraft)
    second = replies.pending.pop()
    assert first[1] == second[1]
    assert set(second[1]) == {"project_id", "session_id", "delegation_id", "request_id", "recipient_id", "content"}
    assistant.setMessageDraft("An additional instruction")
    second[2]({"accepted": True})
    assert assistant.messageDraft == "An additional instruction"
    assert workspace.sessionId == "chat-a" and workspace.selectedModel == "chosen-model"
    assert not any(name in {"run.start", "session.create", "session.configure"} for name, _ in replies.calls)


def test_message_draft_and_callback_remain_owned_by_origin_context(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation()])
    assistant.setMessageDraft("First chat message")
    assistant.sendMessage("coordinator", assistant.messageDraft)
    command = replies.pending.pop()
    workspace._session = {**workspace._session, "id": "chat-b"}
    workspace.changed.emit()
    assistant.refresh()
    replies.snapshot([delegation(session_id="chat-b", delegation_id="delegation-b")])
    assistant.setMessageDraft("Second chat message")
    command[2]({"accepted": True})
    assert assistant.messageDraft == "Second chat message"
    assert workspace.sessionId == "chat-b"


def test_team_durable_events_update_one_literal_card_without_navigation(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot()
    workspace._runs = {"run-a": {"id": "run-a", "session_id": "chat-a", "state": "completed"}}
    for seq, state in enumerate(("pending", "running", "completed"), 1):
        workspace._event({"run_id": "run-a", "seq": seq, "type": "team.delegation",
                          "payload": delegation(state=state, summary="<b>Literal result</b>")})
    rows = workspace.transcript.rows
    assert len(rows) == 1
    assert rows[0]["kind"] == "team" and rows[0]["command"] == "team-a"
    assert "Literal result" in rows[0]["body"] and "<b>" in rows[0]["body"]
    assert rows[0]["title"].startswith("TEAM")
    assert workspace.sessionId == "chat-a" and workspace.teams.selectedId == ""
    assistant.openTeam("unrelated-team")
    assert workspace.teams.selectedId == ""
    assistant.openTeam("team-a")
    assert workspace.teams.selectedId == "team-a" and workspace.sessionId == "chat-a"


def test_long_result_remains_bounded_and_full_payload_inspectable(assistant_workspace):
    workspace, _, replies = assistant_workspace
    replies.snapshot()
    workspace._runs = {"run-a": {"id": "run-a", "session_id": "chat-a", "state": "completed"}}
    workspace._event({"run_id": "run-a", "seq": 1, "type": "team.delegation",
                      "payload": delegation(state="completed", summary="x" * 16000)})
    row = workspace.transcript.rows[0]
    assert len(row["body"]) <= 1801 and len(row["raw"]) > 16000


def test_environment_reads_actual_capabilities_and_does_not_claim_native_agents(assistant_workspace):
    _, assistant, replies = assistant_workspace
    replies.snapshot(capabilities={"managed_team": False, "team_status": True, "team_message": False},
                     effective_permission="read_only", active_runs=[{"id": "run"}], active_teams=[])
    summary = assistant.environmentSummary
    assert "Sola lettura" in summary and "Organizzazione di Team: Non disponibile" in summary
    assert "Stato dei Team: Disponibile" in summary and "1 turni, 0 team" in summary


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if result := visual_item(child, name):
            return result
    return None


def evaluate(item, expression):
    script = QQmlExpression(QQmlEngine.contextForObject(item), item, expression)
    result, _ = script.evaluate()
    assert not script.hasError(), script.error().toString()
    return result


def activate(window, item, *, keyboard=False):
    assert item is not None and item.isVisible() and item.isEnabled()
    bounds = item.mapRectToItem(window.contentItem(), item.boundingRect())
    assert bounds.left() >= 0 and bounds.right() <= window.width()
    assert bounds.top() >= 0 and bounds.bottom() <= window.height()
    if keyboard:
        item.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Space)
    else:
        QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                         pos=item.mapToScene(item.boundingRect().center()).toPoint())
    QTest.qWait(40)


def test_chat_panel_renders_pending_handoff_and_environment_without_session_switch(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="pending", team_id=None, can_message=False)])
    engine = create_engine(workspace)
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    try:
        QTest.qWait(80)
        panel = visual_item(root.contentItem(), "assistantTeamPanel")
        assert panel is not None
        assert visual_item(root.contentItem(), "assistantInlineNotice").isVisible()
        activate(root, visual_item(root.contentItem(), "assistantConfigureTeam"), keyboard=True)
        assert visual_item(root.contentItem(), "assistantPendingHandoff").isVisible()
        assert not visual_item(root.contentItem(), "assistantOpenTeam").isVisible()
        assert visual_item(root.contentItem(), "assistantCancelTeam").isVisible()
        message = visual_item(root.contentItem(), "assistantTeamMessage")
        activate(root, visual_item(root.contentItem(), "assistantEnvironmentButton"))
        assert workspace.sessionId == "chat-a"
        assert assistant.loading
        message.setProperty("text", "Old delegation draft")
        assistant.setMessageDraft("Old delegation draft")
        replies.snapshot([delegation(id="delegation-b", delegation_id="delegation-b")])
        QTest.qWait(30)
        assert message.property("text") == ""
    finally:
        root.setProperty("closeConfirmed", True)
        root.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_continuation_card_explains_deferred_resume_without_rewriting_prior_result(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    previous = delegation(state="completed", summary="Original <literal> result")
    saved = copy.deepcopy(previous)
    continuation = delegation(id="continued", delegation_id="continued", action="resume",
        source_delegation_id=previous["id"], source_team_id=previous["team_id"],
        state="pending", team_id=None, total_steps=0, completed_steps=0)
    replies.snapshot([continuation, previous])
    assert assistant.current["label"] == "Ripresa in coda"
    assert "conservando i risultati precedenti" in assistant.current["handoff_text"]
    card = delegation_card(continuation)
    assert card["status"] == "Ripresa in coda" and card["command"] == ""
    assert previous == saved and workspace.sessionId == "chat-a"
    assert not any(method in {"run.start", "team.resume", "assistant.resume"} for method, _ in replies.calls)


def test_legacy_conclusion_without_worker_evidence_is_visible_and_literal(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    value = delegation(state="completed", summary="Original <b>coordinator claim</b>",
        total_steps=0, completed_steps=0, can_cancel=False, can_resume=True, cleanup_confirmed=True,
        completion_evidence={"worker_assignments_completed": 0, "basis": None,
                             "coordinator_only_conclusion": True})
    replies.snapshot([value])
    engine = create_engine(workspace)
    root = engine.rootObjects()[0]
    try:
        QTest.qWait(40)
        assert assistant.current["state"] == "completed"
        assert assistant.current["label"] == "Conclusione da verificare"
        panel = visual_item(root.contentItem(), "assistantTeamPanel")
        assert not panel.property("completedCollapsed")
        notice = visual_item(root.contentItem(), "assistantInlineNotice")
        assert notice.isVisible()
        assert evaluate(notice, "children.some(function(child) { return child.text === assistant.current.completion_warning; })")
        activate(root, visual_item(root.contentItem(), "assistantConfigureTeam"))
        assert visual_item(root.contentItem(), "assistantCompletionWarning").isVisible()
        card = delegation_card(value)
        assert "Original <b>coordinator claim</b>" in card["body"]
        assert card["status"] == "Conclusione da verificare"
        assert value["state"] == "completed" and value["summary"] == "Original <b>coordinator claim</b>"
    finally:
        root.setProperty("closeConfirmed", True)
        root.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_other_chat_team_reserves_project_without_mixing_results(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot(active_teams=[{"id": "other-team", "state": "running"}])
    assert assistant.active and assistant.current == {}
    assert not assistant.canMessage and not assistant.canCancel
    assert not workspace.workbench.canFork and not workspace.workbench.canCompact
    workspace.sendMode("New ordinary turn", "execute")
    assert not any(name == "run.start" for name, _ in replies.calls)
    assert assistant._timer.isActive()
    assistant._team_event({"team_id": "other-team", "type": "team.state"})
    assert assistant._coalesce.isActive()
    assert workspace.sessionId == "chat-a" and workspace.transcript.rows == []


def test_interrupted_pending_handoff_can_resume_without_created_team(assistant_workspace):
    _, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="interrupted", team_id=None, can_resume=True, cleanup_confirmed=True)])
    assert assistant.canResume
    assistant.resume()
    assert replies.calls[-1][0] == "assistant.resume"


def test_another_owned_chat_handoff_refreshes_guard_without_showing_its_result(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot()
    workspace._runs["other-run"] = {"id": "other-run", "session_id": "chat-b", "state": "completed"}
    workspace._event({"run_id": "other-run", "seq": 1, "type": "team.delegation",
                      "payload": delegation(session_id="chat-b")})
    assistant._event({"run_id": "other-run", "type": "team.delegation"})
    assert assistant._coalesce.isActive()
    assert workspace.transcript.rows == [] and assistant.current == {}


@pytest.mark.parametrize("state", ["paused", "completed", "failed", "cancelled", "interrupted"])
def test_settled_team_message_records_context_without_automatic_resume(assistant_workspace, state):
    _, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state=state, can_message=True, can_cancel=False,
                                 can_resume=True, cleanup_confirmed=True)])
    assert assistant.canResume
    assistant.setMessageDraft("Continue with a second review when resumed")
    assistant.sendMessage("coordinator", assistant.messageDraft)
    replies.pending.pop()[2]({"delivery": "recorded_only", "consumed": False})
    assert "lettura non ancora confermata" in assistant.deliveryNotice
    assert assistant.messageDraft == ""
    assert not any(method == "assistant.resume" for method, _ in replies.calls)


def test_token_activity_does_not_trigger_snapshot_storm(assistant_workspace):
    _, assistant, replies = assistant_workspace
    replies.snapshot([delegation()])
    for seq in range(200):
        assistant._team_event({"team_id": "team-a", "type": "member.activity", "seq": seq})
    assert not assistant._coalesce.isActive()
    assert assistant._timer.isActive()


def test_structured_resume_rejection_is_visible_and_not_hidden_by_snapshot(assistant_workspace):
    _, assistant, replies = assistant_workspace
    value = delegation(state="paused", can_resume=True, cleanup_confirmed=True)
    replies.snapshot([value])
    assistant.resume()
    first = replies.pending.pop()
    first[2]({"ok": False, "error": {"code": "BUSY", "message": "Project reservation is busy"}})
    assert assistant.error == "Project reservation is busy"
    assert not assistant.commandPending
    assistant.refresh()
    replies.snapshot([value])
    assert assistant.error == "Project reservation is busy"
    assistant.resume()
    second = replies.pending.pop()
    assert second[1]["request_id"] != first[1]["request_id"]


@pytest.mark.parametrize("code", ["INTERRUPTED", "DELIVERY_UNKNOWN"])
def test_lost_resume_ack_then_uncertain_result_retains_same_request_id(assistant_workspace, code):
    workspace, assistant, replies = assistant_workspace
    value = delegation(state="interrupted", can_resume=True, cleanup_confirmed=True)
    replies.snapshot([value])
    assistant.resume()
    first = replies.pending.pop()
    first[3]({"code": "DISCONNECTED", "message": "Connection lost before acknowledgment"})
    workspace._connected_value = False
    workspace.changed.emit()
    workspace.client.disconnected.emit()
    workspace._connected_value = True
    workspace.changed.emit()
    assistant.refresh()
    replies.snapshot([value])
    assert assistant.error == "Connection lost before acknowledgment"
    assistant.resume()
    second = replies.pending.pop()
    assert second[1]["request_id"] == first[1]["request_id"]
    second[2]({"ok": False, "error": {"code": code, "message": "Outcome remains uncertain"}})
    assistant.refresh()
    replies.snapshot([value])
    assert assistant.error == "Outcome remains uncertain"
    mutations_before = sum(name == "assistant.resume" for name, _ in replies.calls)
    QTest.qWait(160)
    assert sum(name == "assistant.resume" for name, _ in replies.calls) == mutations_before
    assistant.resume()
    third = replies.pending.pop()
    assert third[1]["request_id"] == first[1]["request_id"]


def test_structured_message_failure_preserves_draft_and_never_acknowledges(assistant_workspace):
    _, assistant, replies = assistant_workspace
    replies.snapshot([delegation()])
    accepted = []
    assistant.messageAccepted.connect(lambda: accepted.append(True))
    assistant.setMessageDraft("Review the draft")
    assistant.sendMessage("coordinator", assistant.messageDraft)
    first = replies.pending.pop()
    first[2]({"ok": False, "error": {"code": "DELIVERY_UNKNOWN", "message": "Not confirmed"}})
    assert assistant.messageDraft == "Review the draft"
    assert not accepted and not assistant.deliveryNotice
    assert assistant.error == "Not confirmed"
    assistant.sendMessage("coordinator", assistant.messageDraft)
    assert replies.pending.pop()[1]["request_id"] == first[1]["request_id"]



@pytest.mark.parametrize("font_size", [13, 18])
def test_idle_assistant_keeps_header_height_and_chat_space_at_minimum_window(assistant_workspace, font_size):
    workspace, _, replies = assistant_workspace
    replies.snapshot()
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        window.resize(880, 620)
        engine.rootContext().contextProperty("appearance").setOption("ui_size", font_size)
        QTest.qWait(60)
        content = window.contentItem()
        panel = visual_item(content, "assistantTeamPanel")
        assert panel.height() == 0
        toggle = visual_item(content, "modelControlsToggle")
        for name in ("assistantConfigureTeam",):
            button = visual_item(content, name)
            assert button.height() == toggle.height()
            bounds = button.mapRectToItem(content, button.boundingRect())
            assert bounds.left() >= 0 and bounds.right() <= window.width()
        knowledge = visual_item(content, "assistantEnvironmentButton")
        assert knowledge.isVisible() and knowledge.property("text") == "Conoscenza e strumenti"
        assert knowledge.property("wrapText")
        bounds = knowledge.mapRectToItem(content, knowledge.boundingRect())
        assert bounds.left() >= 0 and bounds.right() <= visual_item(content, "sidebar").width()
        assert visual_item(content, "conversation").height() > 160
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)



def test_unconfirmed_identity_bound_rejects_new_commands_but_preserves_retries(assistant_workspace):
    _, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="paused", can_resume=True, cleanup_confirmed=True)])
    assistant.resume()
    original = replies.pending.pop()
    original[3]({"code": "INTERRUPTED", "message": "Unknown continuation"})
    for index in range(127):
        workspace = assistant.workspace
        workspace._pending_intents.append({"fingerprint": f"{index:064x}", "request_id": f"request-{index}",
            "method": "queue.add", "project_id": "another-project", "session_id": "chat", "state": "unknown"})
    workspace._draft_dirty = True
    workspace.flushDrafts()
    before = len(replies.calls)
    assistant.sendMessage("coordinator", "A new operation")
    assert len(replies.calls) == before
    assert "Troppe operazioni" in assistant.error
    assert len(workspace._pending_intents) == 128
    assistant.resume()
    retry = replies.pending.pop()
    assert retry[1]["request_id"] == original[1]["request_id"]
    retry[2]({"ok": True})
    assert len(workspace._pending_intents) == 127


@pytest.mark.parametrize("method", ["assistant.message", "assistant.resume"])
def test_request_identity_survives_real_workspace_close_and_restart(assistant_workspace, monkeypatch, method):
    workspace, assistant, replies = assistant_workspace
    value = delegation(state="paused", can_resume=True, cleanup_confirmed=True)
    replies.snapshot([value])

    def invoke(controller):
        if method == "assistant.message":
            controller.sendMessage("coordinator", "Same literal instructions after restart")
        else:
            controller.resume()

    invoke(assistant)
    original = replies.pending.pop()
    pending_on_disk = DraftStore(workspace.paths.config).pending
    assert pending_on_disk[0]["request_id"] == original[1]["request_id"]
    assert pending_on_disk[0]["state"] == "pending"
    original[3]({"code": "DISCONNECTED", "message": "Acknowledgment lost"})
    assert DraftStore(workspace.paths.config).pending[0]["state"] == "unknown"
    workspace.close()

    reopened = Workspace(workspace.paths)
    try:
        reopened._project = workspace._project.copy()
        reopened._session = workspace._session.copy()
        reopened._connected_value = True
        fresh = Replies()
        monkeypatch.setattr(reopened.client, "request", fresh.request)
        reopened.changed.emit()
        reopened.assistant.refresh()
        fresh.snapshot([value])
        QTest.qWait(160)
        assert not any(name in {"assistant.message", "assistant.resume"} for name, _ in fresh.calls)
        assert reopened._pending_intents[0]["state"] == "unknown"
        assert not reopened.assistant._retry_ids
        invoke(reopened.assistant)
        retry = fresh.pending.pop()
        assert retry[0] == method and retry[1] == original[1]
        retry[2]({"ok": True})
        assert DraftStore(reopened.paths.config).pending == []
    finally:
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("method", ["assistant.message", "assistant.resume"])
def test_identity_save_failure_blocks_effect_until_persisted(assistant_workspace, monkeypatch, method):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="paused", can_resume=True, cleanup_confirmed=True)])
    original_save = workspace._draft_store.save

    def no_space(*_args):
        raise OSError("Synthetic disk full")

    monkeypatch.setattr(workspace._draft_store, "save", no_space)
    def invoke():
        if method == "assistant.message":
            assistant.sendMessage("coordinator", "Persist before effects")
        else:
            assistant.resume()
    invoke()
    assert not any(name == method for name, _ in replies.calls)
    assert "Nessuna nuova operazione" in assistant.error
    assert workspace._draft_dirty
    identity = workspace._pending_intents[0]["request_id"]
    monkeypatch.setattr(workspace._draft_store, "save", original_save)
    invoke()
    call = replies.pending.pop()
    assert call[1]["request_id"] == identity
    assert DraftStore(workspace.paths.config).pending[0]["request_id"] == identity


def test_saved_identity_is_authoritative_over_transient_cache(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="paused", can_resume=True, cleanup_confirmed=True)])
    assistant.resume()
    original = replies.pending.pop()
    original[3]({"code": "INTERRUPTED", "message": "Unknown result"})
    record = workspace._pending_intents[0]
    assistant._retry_ids[record["fingerprint"]] = "synthetic-stale-cache"
    assistant.resume()
    assert replies.pending.pop()[1]["request_id"] == original[1]["request_id"]


def test_deterministic_rejection_settles_saved_identity(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="paused", can_resume=True, cleanup_confirmed=True)])
    assistant.resume()
    replies.pending.pop()[2]({"ok": False, "error": {"code": "BUSY", "message": "Reserved project"}})
    assert not workspace._pending_intents
    assert DraftStore(workspace.paths.config).pending == []


@pytest.mark.parametrize("initial, edited", [(1, 0), (0, 4), (3, 10000)])
def test_resume_limit_edit_is_scoped_and_does_not_mutate_original(assistant_workspace, initial, edited):
    workspace, assistant, replies = assistant_workspace
    value = delegation(state="paused", can_resume=True, cleanup_confirmed=True, round_limit=initial)
    replies.snapshot([value])
    assert assistant.resumeRoundLimit == initial
    assistant.setResumeRoundLimit(edited)
    assistant.setResumeRoundLimit(-1)
    assistant.setResumeRoundLimit(10001)
    assistant.setResumeRoundLimit(True)
    assert assistant.resumeRoundLimit == edited
    assert assistant.current["round_limit"] == value["round_limit"] == initial
    assert not any(name == "assistant.resume" for name, _ in replies.calls)
    assistant.refresh()
    replies.snapshot([value])
    assert assistant.resumeRoundLimit == edited
    assistant.refresh()
    replies.snapshot([{**value, "team_id": "continued-team", "round_limit": 7}])
    assert assistant.resumeRoundLimit == 7
    assistant.refresh()
    replies.snapshot([value])
    assert assistant.resumeRoundLimit == edited
    workspace._session = {**workspace._session, "id": "chat-b"}
    workspace.changed.emit()
    assistant.refresh()
    replies.snapshot([{**value, "session_id": "chat-b", "round_limit": 2}])
    assert assistant.resumeRoundLimit == 2
    workspace._session = {**workspace._session, "id": "chat-a"}
    workspace.changed.emit()
    assistant.refresh()
    replies.snapshot([value])
    assert assistant.resumeRoundLimit == edited
    assistant.resume()
    assert replies.pending[-1][1]["round_limit"] == edited
    assert workspace.selectedModel == "chosen-model" and workspace.selectedEffort == "xhigh"


@pytest.mark.parametrize("count", [0, 8, 10000])
def test_edited_resume_limit_survives_uncertain_restart_and_locks_changes(assistant_workspace, monkeypatch, count):
    workspace, assistant, replies = assistant_workspace
    value = delegation(state="paused", can_resume=True, cleanup_confirmed=True, round_limit=1)
    replies.snapshot([value])
    assistant.setResumeRoundLimit(count)
    assistant.resume()
    first = replies.pending.pop()
    first[3]({"code": "DELIVERY_UNKNOWN", "message": "No confirmation"})
    assert assistant.resumeOutcomeUncertain and not assistant.canEditResumeRoundLimit
    assistant.setResumeRoundLimit(9)
    assert assistant.resumeRoundLimit == count
    workspace.close()
    reopened = Workspace(workspace.paths)
    try:
        reopened._project = workspace._project.copy()
        reopened._session = workspace._session.copy()
        reopened._connected_value = True
        fresh = Replies()
        monkeypatch.setattr(reopened.client, "request", fresh.request)
        reopened.changed.emit()
        reopened.assistant.refresh()
        fresh.snapshot([value])
        assert reopened.assistant.resumeRoundLimit == count
        assert not reopened.assistant.canEditResumeRoundLimit
        assert not any(name == "assistant.resume" for name, _ in fresh.calls)
        reopened.assistant.resume()
        retry = fresh.pending.pop()
        assert retry[1] == first[1]
        retry[2]({"ok": True})
        assert not reopened._pending_intents
    finally:
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_legacy_inherited_resume_identity_prevents_changed_limit_retry(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    replies.snapshot([delegation(state="paused", can_resume=True, cleanup_confirmed=True, round_limit=0)])
    assistant.resume()
    first = replies.pending.pop()
    first[3]({"code": "INTERRUPTED", "message": "Unknown result"})
    record = workspace._pending_intents[0]
    record.pop("delegation_id")
    record.pop("team_id")
    params = {key: value for key, value in first[1].items() if key != "request_id"}
    record["fingerprint"] = hashlib.sha256(json.dumps(["assistant.resume", params],
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert DraftStore._pending_valid([record])
    assistant.setResumeRoundLimit(6)
    assert assistant.resumeRoundLimit == 0 and not assistant.canEditResumeRoundLimit
    assistant.resume()
    assert replies.pending.pop()[1] == first[1]
    assert "round_limit" not in first[1]


def test_same_delegation_new_concrete_team_never_reuses_old_uncertain_identity(assistant_workspace):
    workspace, assistant, replies = assistant_workspace
    value = delegation(state="paused", can_resume=True, cleanup_confirmed=True, round_limit=1)
    replies.snapshot([value])
    assistant.setResumeRoundLimit(0)
    assistant.resume()
    first = replies.pending.pop()
    first[3]({"code": "DISCONNECTED", "message": "Unknown acknowledgment"})
    assistant.refresh()
    replies.snapshot([{**value, "team_id": "continued-team", "round_limit": 0}])
    assert assistant.canEditResumeRoundLimit
    assistant.setResumeRoundLimit(0)
    assistant.resume()
    second = replies.pending.pop()
    assert second[1]["request_id"] != first[1]["request_id"]
    assert second[1]["round_limit"] == first[1]["round_limit"] == 0
    assert len(workspace._pending_intents) == 2
    assert len({row["fingerprint"] for row in workspace._pending_intents}) == 2
    assert DraftStore._pending_valid(workspace._pending_intents)


@pytest.mark.parametrize("size,font_size", [((1380, 900), 13), ((880, 620), 18)])
def test_qml_resume_limit_zero_uses_keyboard_and_explicit_resume(assistant_workspace, size, font_size):
    workspace, assistant, replies = assistant_workspace
    value = delegation(state="paused", can_resume=True, cleanup_confirmed=True, round_limit=1,
                       can_message=False, goal="Review synthetic files")
    replies.snapshot([value])
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        window.resize(*size)
        engine.rootContext().contextProperty("appearance").setOption("ui_size", font_size)
        QTest.qWait(40)
        activate(window, visual_item(window.contentItem(), "assistantConfigureTeam"), keyboard=True)
        control = visual_item(window.contentItem(), "assistantResumeRoundLimitInput")
        assert control.isVisible() and control.isEnabled() and control.property("value") == 1
        content = control.property("contentItem")
        center = content.mapToScene(content.boundingRect().center())
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(round(center.x()), round(center.y())))
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_0)
        assistant.refresh()
        replies.snapshot([value])
        QTest.keyClick(window, Qt.Key.Key_Return)
        assert assistant.resumeRoundLimit == 0
        assert assistant.current["round_limit"] == 1
        assert not any(name == "assistant.resume" for name, _ in replies.calls)
        button = visual_item(window.contentItem(), "assistantResumeTeam")
        for item in (control, button):
            bounds = item.mapRectToItem(window.contentItem(), item.boundingRect())
            assert bounds.left() >= 0 and bounds.right() <= window.width()
            assert bounds.top() >= 0 and bounds.bottom() <= window.height()
        center = button.mapToScene(button.boundingRect().center())
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(round(center.x()), round(center.y())))
        assert replies.calls[-1][0] == "assistant.resume" and replies.calls[-1][1]["round_limit"] == 0
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
