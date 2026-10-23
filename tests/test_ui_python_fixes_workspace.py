"""Workspace regressions: synthetic RPCs and profiles only, no service or provider."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui import controller as controller_module
from kira.ui.controller import (
    Workspace,
    draft_error_text,
    launch_denied,
    preference_error_text,
)
from kira.ui.drafts import MAX_PENDING
from kira.ui.preferences import write_preferences
from kira.ui.presentation import UiText
from kira.ui.service_errors import service_error_text


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path),
                          "permission": "project_write", "memory": {}}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "codex",
                          "model": "explicit-model", "settings": {"reasoning_effort": "ultra"}}
    workspace._sessions = [workspace._session.copy()]
    workspace._model, workspace._backend, workspace._effort = "explicit-model", "codex", "ultra"
    workspace._catalogs = {"codex": {"available": True, "capabilities": {"steering": True},
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


def emit(workspace, run_id, seq, kind, payload):
    workspace._event({"run_id": run_id, "seq": seq, "type": kind, "payload": payload,
                      "timestamp": "2026-01-01T00:00:00Z"})


def use_backend(workspace, backend, model="model"):
    workspace._session = {**workspace._session, "backend": backend, "model": model}
    workspace._sessions = [workspace._session.copy()]
    workspace._backend, workspace._model = backend, model
    workspace.changed.emit()


# UP-1: Claude Code receives Kira's browser, so its chats need the browser UI.
def test_claude_code_chat_gets_browser_panel_and_model_requested_manual_login(view):
    workspace, calls = view
    use_backend(workspace, "claude_code", "opus")
    assert workspace.web.available
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running",
                               "backend": "claude_code", "model": "opus"}}
    emit(workspace, "run", 1, "approval.requested",
         {"approval_id": "login", "kind": "browser_manual_login", "tool": "browser.manual_login"})
    assert workspace.web.manualApproval
    assert request(calls, "session.browser.open")[1]["session_id"] == "chat"


def test_claude_code_browser_results_refresh_the_open_web_panel(view):
    workspace, calls = view
    use_backend(workspace, "claude_code", "opus")
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running",
                               "backend": "claude_code", "model": "opus"}}
    workspace.web.open()
    assert request(calls, "session.web.get")
    workspace.web._event({"run_id": "run", "seq": 4, "type": "tool.completed",
                          "payload": {"name": "browser.navigate", "result": {"ok": True}}})
    assert workspace.web._refresh_needed


# UP-3: the top-bar Export shares the chat Markdown export, never the project JSON.
def test_top_bar_export_saves_only_the_selected_chat_as_markdown(view, tmp_path):
    workspace, calls = view
    target = tmp_path / "chat.md"
    workspace.exportTranscript(target.as_uri())
    assert not any(call[0] == "project.export" for call in calls)
    export = request(calls, "session.export")
    assert export[1] == {"project_id": "project", "session_id": "chat"}
    calls.clear()
    workspace._session = {}
    workspace.changed.emit()
    workspace.exportTranscript(target.as_uri())
    assert not calls and workspace.notice == "Apri una chat per esportarla."


# UP-5: replaying another chat's run must not reopen an approval already decided.
def test_decided_approval_in_another_chat_is_not_reopened_on_replay(view):
    workspace, _ = view
    workspace._runs = {"other-run": {"id": "other-run", "session_id": "other", "state": "running"}}
    emit(workspace, "other-run", 1, "approval.requested",
         {"approval_id": "approval", "kind": "browser_action", "tool": "browser.click"})
    assert workspace.approval["approval_id"] == "approval"
    emit(workspace, "other-run", 2, "backend.notice",
         {"message": "Approval decision received", "approval_id": "approval", "decision": "accept"})
    assert workspace.approval == {}


# UP-6: the service offers "always" for its browser actions with every web provider.
@pytest.mark.parametrize("backend", ["claude_code", "ollama", "deepseek_account"])
def test_always_allow_follows_the_service_offer_for_every_web_provider(view, backend):
    workspace, _ = view
    use_backend(workspace, backend)
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "awaiting_input"}}
    emit(workspace, "run", 1, "approval.requested",
         {"approval_id": "approval", "kind": "browser_action", "tool": "browser.click",
          "allow_always": True})
    assert workspace.approvalCanAlways
    emit(workspace, "run", 2, "approval.requested",
         {"approval_id": "manual", "kind": "browser_action", "tool": "browser.click"})
    assert not workspace.approvalCanAlways


# UP-9: WSL drvfs/9p and Windows volumes report every document as executable.
def mountinfo(monkeypatch, tmp_path, kind):
    device = os.stat(tmp_path).st_dev
    table = tmp_path / "mountinfo"
    table.write_text(f"36 25 {os.major(device)}:{os.minor(device)} / {tmp_path} rw,noatime shared:1"
                     f" - {kind} source rw\n")
    monkeypatch.setattr(controller_module, "MOUNTINFO", table)


def test_documents_on_drvfs_open_despite_their_synthetic_execute_bit(tmp_path, monkeypatch):
    document = tmp_path / "report.txt"
    document.write_bytes(b"Plain project notes\n")
    document.chmod(0o755)
    script = tmp_path / "tool"
    script.write_bytes(b"#!/bin/sh\necho unsafe\n")
    script.chmod(0o755)
    program = tmp_path / "setup.exe"
    program.write_bytes(b"not really a program")
    mountinfo(monkeypatch, tmp_path, "9p")
    assert not launch_denied(document)
    assert launch_denied(script) and launch_denied(program)
    mountinfo(monkeypatch, tmp_path, "ext4")
    assert launch_denied(document)


# UP-10: routine acknowledgements are not chat rows; the notice title is translated.
def test_routine_acknowledgements_are_hidden_and_notices_have_a_translated_title(view):
    workspace, _ = view
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running"}}
    routine = ["Claude Code session configured", "DeepSeek Web response received",
               "Approval decision received", "Ollama tool results returned to model",
               "Verifying Ollama tool calling with a side-effect-free probe", "Ollama tool probe passed"]
    for seq, message in enumerate(routine, start=1):
        emit(workspace, "run", seq, "backend.notice", {"message": message})
    assert not workspace.transcript.rows
    emit(workspace, "run", 7, "backend.notice", {"message": "Chat-only mode: tool capability was not verified"})
    emit(workspace, "run", 8, "backend.notice", {"message": "DeepSeek Web response received", "level": "warning"})
    rows = workspace.transcript.rows
    assert [row["body"] for row in rows] == ["Chat-only mode: tool capability was not verified",
                                             "DeepSeek Web response received"]
    assert all(isinstance(row["title"], UiText) and row["title"] == "AVVISO" for row in rows)


# UP-11: known service error codes are explained; the literal message is kept.
def test_known_error_codes_are_translated_and_keep_the_literal_detail():
    busy = service_error_text({"code": "BUSY", "message": "Project already has an active run"}, "fallback")
    assert busy.startswith("Il servizio sta completando altro lavoro")
    assert busy.endswith("Dettaglio del servizio: Project already has an active run")
    assert service_error_text({"code": "QUEUE_FULL"}, "fallback").startswith("La coda del progetto è piena")
    assert service_error_text({"code": "PROVIDER_X", "message": "Literal"}, "fallback") == "Literal"
    assert service_error_text({}, "fallback") == "fallback"


def test_rejected_start_shows_the_translated_reason(view):
    workspace, calls = view
    workspace.send("Synthetic request")
    request(calls, "run.start")[3]({"code": "ARCHIVED", "message": "Restore this chat before starting work"})
    assert workspace.notice.startswith("La chat è archiviata")
    assert "Restore this chat before starting work" in workspace.notice


# UP-12: run.start keeps its identity across a restart, like queued requests.
def test_start_identity_is_saved_before_sending_and_reused_after_restart(view):
    workspace, calls = view
    workspace.setDraftText("Synthetic request")
    workspace.send("Synthetic request")
    first = request(calls, "run.start")
    saved = [row for row in workspace._pending_intents if row["method"] == "run.start"]
    assert [row["request_id"] for row in saved] == [first[1]["request_id"]]
    reopened = Workspace(workspace.paths)
    try:
        reopened._project, reopened._session = dict(workspace._project), dict(workspace._session)
        reopened._model, reopened._backend = workspace._model, workspace._backend
        reopened._connected_value = True
        resent = []
        reopened.client.request = lambda *args, **kwargs: resent.append(args)
        reopened.send("Synthetic request")
        assert request(resent, "run.start")[1] == first[1]
    finally:
        reopened.close()
        reopened.deleteLater()
    first[2]({"run": {"id": "run", "session_id": "chat", "state": "queued"}})
    assert not any(row["method"] == "run.start" for row in workspace._pending_intents)


def test_start_identity_settles_on_rejection_and_survives_an_unknown_outcome(view):
    workspace, calls = view
    workspace.send("Rejected request")
    request(calls, "run.start")[3]({"code": "BUSY", "message": "Project is busy"})
    assert not workspace._pending_intents and not workspace._pending_start
    workspace.send("Uncertain request")
    uncertain = request(calls, "run.start")
    uncertain[3]({"code": "DISCONNECTED", "message": "Connection lost"})
    assert workspace._pending_start["request_id"] == uncertain[1]["request_id"]
    assert [row["request_id"] for row in workspace._pending_intents] == [uncertain[1]["request_id"]]


def test_start_is_not_sent_when_its_identity_cannot_be_saved(view):
    workspace, calls = view
    workspace._pending_intents = [{"fingerprint": f"{index:064x}", "method": "queue.add",
                                   "request_id": f"request-{index}", "project_id": "project",
                                   "session_id": "chat", "state": "unknown"} for index in range(MAX_PENDING)]
    workspace.send("Synthetic request")
    assert not any(call[0] == "run.start" for call in calls)
    assert workspace.workbench.error.startswith("Troppe operazioni con esito incerto")


# UP-16: Stop acts on the selected chat only.
def test_stop_never_cancels_another_chats_run(view):
    workspace, calls = view
    workspace._runs = {"other-run": {"id": "other-run", "session_id": "other", "state": "running"}}
    workspace.changed.emit()
    assert workspace.activeRunId == "other-run"
    workspace.stop()
    assert not any(call[0] == "run.cancel" for call in calls)
    assert workspace.notice == "Un’altra chat sta lavorando: aprila per fermarne il turno."
    workspace._runs["own-run"] = {"id": "own-run", "session_id": "chat", "state": "running"}
    workspace.stop()
    assert request(calls, "run.cancel")[1] == {"run_id": "own-run"}


# UP-19: store diagnostics are keys the interface explains in the selected language.
def test_draft_and_preference_diagnostics_are_explained():
    invalid = "Il file delle bozze non è valido: è stato conservato e non verrà sovrascritto."
    for error in ("Invalid drafts", "Duplicate draft", "Invalid pending request identities"):
        assert draft_error_text(error) == invalid and isinstance(draft_error_text(error), UiText)
    assert draft_error_text("Pending request identity limit exceeded").startswith("Troppe operazioni")
    assert draft_error_text("[Errno 28] No space left on device") == "[Errno 28] No space left on device"
    assert preference_error_text("Preferences directory must not be a symlink") == (
        "La cartella delle preferenze non può essere un collegamento.")


def test_preference_write_failure_is_reported_with_translatable_text(view, tmp_path):
    workspace, _ = view
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "linked-config"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="Preferences directory must not be a symlink"):
        write_preferences(link, {})
    workspace.paths = Paths(link, *(tmp_path / name for name in ("data", "state", "runtime")))
    workspace.setUiPreference("density", "compact")
    assert workspace.notice == ("Preferenze non salvate: La cartella delle preferenze non può essere "
                                "un collegamento.")
    assert isinstance(workspace._notice.values["error"], UiText)


# UP-22: reasoning summaries name providers like the rest of the interface.
@pytest.mark.parametrize(("backend", "label"), [("openai_chatgpt", "OpenAI diretto · ChatGPT"),
                                                ("deepseek_account", "DeepSeek Account")])
def test_reasoning_summary_uses_the_interface_provider_label(view, backend, label):
    workspace, _ = view
    use_backend(workspace, backend)
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running",
                               "project_id": "project", "created_at": "2026-01-01T00:00:00Z"}}
    workspace._reasoning.receive("run", "reasoning.summary.status",
                                 {"source": "provider_summary", "provider": backend, "status": "requested"})
    assert workspace.reasoning.current["provider"] == label
