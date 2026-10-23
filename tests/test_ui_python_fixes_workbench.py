"""Workbench intent regressions: synthetic RPCs and profiles only, no provider."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.drafts import MAX_PENDING, DraftStore


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path),
                          "permission": "project_write", "memory": {}}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "codex",
                          "model": "explicit-model", "settings": {"reasoning_effort": "ultra"}}
    workspace._sessions = [workspace._session.copy()]
    workspace._model, workspace._backend, workspace._effort = "explicit-model", "codex", "ultra"
    workspace._catalogs = {"codex": {"available": True, "capabilities": {
        "steering": True, "native_compaction": True, "native_fork": True, "planning": True},
        "models": [{"id": "explicit-model", "name": "Explicit model"}]}}
    workspace._connected_value = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args))
    workspace.changed.emit()
    yield workspace, calls
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def request(calls, method):
    return next(call for call in reversed(calls) if call[0] == method)


def goal_result():
    return {"goal": {"objective": "Synthetic objective", "status": "active"},
            "autonomous": {"id": "goal", "status": "active", "cycle_count": 1, "max_cycles": 0}}


# UP-2: English goal subcommands never become the objective of a new goal.
@pytest.mark.parametrize("word", ["/obiettivo", "/obbiettivo", "/goal"])
@pytest.mark.parametrize(("subcommand", "method"), [("pause", "goal.pause"), ("PAUSE", "goal.pause"),
                                                     ("resume", "goal.resume"), ("pausa", "goal.pause"),
                                                     ("riprendi", "goal.resume")])
def test_goal_subcommands_accept_english_for_every_command_word(view, word, subcommand, method):
    workspace, calls = view
    workspace.workbench.submit(f"{word} {subcommand}")
    assert request(calls, method)[1]["session_id"] == "chat"
    assert not any(call[0] == "goal.start" for call in calls)


@pytest.mark.parametrize("prompt", ["/goal status", "/obiettivo status", "/obbiettivo stato"])
def test_goal_status_opens_the_controls_without_starting_a_goal(view, prompt):
    workspace, calls = view
    panels = []
    workspace.workbench.showPanel.connect(panels.append)
    workspace.workbench.submit(prompt)
    assert panels == ["goal"]
    assert not any(call[0].startswith("goal.") and call[0] != "goal.get" for call in calls)


# UP-7: a definitive rejection settles the saved identity; an unknown outcome keeps it.
@pytest.mark.parametrize("code", ["QUEUE_FULL", "BUSY", "INVALID_ARGUMENT", "ARCHIVED"])
def test_rejected_queue_request_does_not_stay_pending(view, code):
    workspace, calls = view
    workspace.workbench.enqueue("Queued task")
    added = request(calls, "queue.add")
    assert [row["request_id"] for row in workspace._pending_intents] == [added[1]["request_id"]]
    added[3]({"code": code, "message": "Synthetic rejection"})
    assert not workspace._pending_intents and not workspace.workbench.hasUncertainRequests
    assert DraftStore(workspace.paths.config).pending == []
    assert workspace.draftText == ""  # Nothing was acknowledged as sent.


def test_unknown_queue_outcome_keeps_its_identity_for_an_exact_retry(view):
    workspace, calls = view
    workspace.workbench.enqueue("Queued task")
    added = request(calls, "queue.add")
    added[3]({"code": "DISCONNECTED", "message": "Connection lost"})
    assert [(row["request_id"], row["state"]) for row in workspace._pending_intents] == [
        (added[1]["request_id"], "unknown")]
    workspace.workbench.enqueue("Queued task")
    assert request(calls, "queue.add")[1] == added[1]


def test_rejected_fork_goal_and_compaction_identities_are_settled(view):
    workspace, calls = view
    workspace.workbench.fork("")
    request(calls, "session.fork")[3]({"code": "BUSY", "message": "Synthetic"})
    workspace.workbench.startAutonomousGoal("Synthetic objective")
    request(calls, "goal.start")[3]({"code": "INVALID_ARGUMENT", "message": "Synthetic"})
    workspace.workbench.compact()
    request(calls, "session.compact")[3]({"code": "UNSUPPORTED", "message": "Synthetic"})
    assert not workspace._pending_intents
    assert workspace.workbench.error.startswith("Operazione non supportata")
    workspace.workbench.startAutonomousGoal("Synthetic objective")
    request(calls, "goal.start")[2](goal_result())
    assert not workspace._pending_intents


def test_identity_limit_is_explained_before_sending_and_drafts_keep_saving(view):
    workspace, calls = view
    workspace._pending_intents = [{"fingerprint": f"{index:064x}", "method": "queue.add",
                                   "request_id": f"request-{index}", "project_id": "project",
                                   "session_id": "other", "state": "unknown"} for index in range(MAX_PENDING)]
    workspace._draft_dirty = True
    workspace.flushDrafts()
    workspace.workbench.enqueue("One more task")
    assert not any(call[0] == "queue.add" for call in calls)
    assert len(workspace._pending_intents) == MAX_PENDING
    assert workspace.workbench.error == ("Troppe operazioni con esito incerto: verifica le attività "
                                         "prima di inviarne altre.")
    workspace.setDraftText("Draft still saved")
    workspace.flushDrafts()
    assert not workspace._draft_error
    assert DraftStore(workspace.paths.config).drafts[("project", "chat")] == "Draft still saved"


# UP-8: a chat created to queue a request is created like a chat created to send one.
class SoulSelection:
    newChatSoulId = "soul"

    def __init__(self):
        self.consumed = []

    def consumeNewChatSelection(self, project_id, soul_id):
        self.consumed.append((project_id, soul_id))


def test_queued_new_chat_uses_the_same_creation_settings_and_soul(view):
    workspace, calls = view
    souls = SoulSelection()
    workspace._soul_controller = souls
    workspace._session = {}
    workspace._backend, workspace._model, workspace._effort = "ollama", "local-model", ""
    workspace._tools, workspace._num_ctx, workspace._think = True, 32768, True
    workspace.changed.emit()
    workspace.send("Sent task")
    sent = request(calls, "session.create")[1]
    calls.clear()
    workspace._creating_session = False
    workspace.workbench.enqueue("Queued task")
    queued = request(calls, "session.create")
    assert queued[1] == sent
    assert queued[1]["soul_id"] == "soul" and queued[1]["settings"] == {
        "tools": True, "num_ctx": 32768, "think": True}
    queued[2]({"session": {"id": "new", "project_id": "project", "backend": "ollama",
                           "model": "local-model", "settings": queued[1]["settings"]}})
    assert souls.consumed == [("project", "soul")]
    assert request(calls, "queue.add")[1]["session_id"] == "new"
