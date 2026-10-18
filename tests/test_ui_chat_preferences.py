"""Private UI settings and event-derived activity; all inputs are synthetic."""

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.preferences import DEFAULTS, read_preferences


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    view._project = {"id": "project", "root": str(tmp_path), "permission": "read_only"}
    view._connected_value = True
    monkeypatch.setattr(view.client, "request", lambda *args: None)
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("key,value", [
    ("details_visible", True), ("chat_font_size", 20), ("reduce_motion", True),
    ("confirm_close", "always"), ("file_opener", "vscode"),
    ("default_permission", "trusted_host"), ("resume_hint_visible", False),
])
def test_settings_persist_without_changing_project_or_provider(workspace, key, value):
    workspace._session = {"id": "session", "model": "fixture", "settings": {"reasoning_effort": "xhigh"}}
    workspace._runs = {"run": {"id": "run", "state": "running"}}
    fields = ("_project", "_session", "_runs", "_settings")
    before = {field: copy.deepcopy(getattr(workspace, field)) for field in fields}
    changes = []
    workspace.preferencesChanged.connect(lambda: changes.append(True))
    workspace.setUiPreference(key, value)
    assert workspace.uiPreferences[key] == value
    assert read_preferences(workspace.paths.config) == {**DEFAULTS, key: value}
    assert len(changes) == 1
    workspace.setUiPreference(key, value)
    assert len(changes) == 1
    snapshot = workspace.uiPreferences
    snapshot[key] = "not persisted"
    assert workspace.uiPreferences[key] == value
    assert (workspace.paths.config / "ui.json").stat().st_mode & 0o777 == 0o600
    assert workspace.paths.config.stat().st_mode & 0o777 == 0o700
    assert {field: getattr(workspace, field) for field in fields} == before
    assert not workspace.paths.db.exists()


@pytest.mark.parametrize("key,value", [
    ("chat_font_size", True), ("chat_font_size", 13.5), ("chat_font_size", 11),
    ("chat_font_size", 21), ("chat_font_size", "14"), ("details_visible", 1),
    ("reduce_motion", "false"), ("file_opener", "shell"), ("confirm_close", []),
    ("default_permission", "full"), ("api_key", "synthetic"),
    ("resume_hint_visible", 0), ("resume_hint_visible", 1), ("resume_hint_visible", "false"),
    ("resume_hint_visible", None),
])
def test_invalid_values_rejected_on_read_and_write(workspace, key, value):
    workspace.setUiPreference(key, value)
    assert workspace.uiPreferences == DEFAULTS
    assert "non valida" in workspace.notice
    assert not (workspace.paths.config / "ui.json").exists()
    workspace.paths.config.mkdir()
    (workspace.paths.config / "ui.json").write_text(json.dumps({key: value}))
    assert read_preferences(workspace.paths.config) == DEFAULTS


def test_failed_write_keeps_ui_state(workspace, monkeypatch):
    def fail(*args):
        raise OSError("synthetic full disk")
    monkeypatch.setattr("kira.ui.controller.write_preferences", fail)
    workspace.setUiPreference("chat_font_size", 18)
    assert workspace.uiPreferences["chat_font_size"] == 14
    assert "non salvate" in workspace.notice


def test_resume_hint_dismissal_and_restore_survive_restart_without_requests(workspace, monkeypatch):
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    assert workspace.resumeHintVisible
    workspace.setUiPreference("resume_hint_visible", False)
    assert not workspace.resumeHintVisible
    reopened = Workspace(workspace.paths)
    try:
        monkeypatch.setattr(reopened.client, "request", lambda *args: requests.append(args))
        assert not reopened.resumeHintVisible
        reopened.setUiPreference("resume_hint_visible", True)
        assert reopened.resumeHintVisible
        assert read_preferences(workspace.paths.config)["resume_hint_visible"] is True
        assert not requests and not workspace.paths.db.exists()
    finally:
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def emit(view, kind, payload=None, run="run"):
    view._apply_event({"run_id": run, "type": kind, "payload": payload or {}})


def test_activity_tracks_current_observed_run_and_honest_waits(workspace):
    view = workspace
    view._project = {"id": "project"}
    view._session = {"id": "session", "project_id": "project"}
    assert view.activityLabel == "Pronto" and not view.activityAnimating
    view._creating_session = True
    assert view.activityLabel == "Preparo la sessione…" and view.activityAnimating
    view._creating_session = False
    view._pending_start = {"request_id": "request"}
    assert view.activityLabel == "Invio della richiesta…" and view.activityAnimating
    view._pending_start = None
    view._runs = {"old": {"id": "old", "session_id": "session", "state": "completed"},
                  "run": {"id": "run", "session_id": "session", "state": "queued"}}
    assert view.activityLabel == "In coda" and not view.activityAnimating
    emit(view, "run.state", {"state": "running"})
    assert view.activityLabel == "Sto pensando…" and view.activityAnimating
    emit(view, "tool.started", {"tool_id": "stale"}, run="old")
    assert view.activityLabel == "Sto pensando…"
    emit(view, "message.delta", {"item_id": "message", "content": "Synthetic"})
    assert view.activityLabel == "Sto rispondendo…"
    emit(view, "tool.started", {"tool_id": "tool"})
    assert view.activityLabel == "Eseguo strumenti…"
    emit(view, "tool.completed", {"tool_id": "tool", "result": {"ok": True}})
    assert view.activityLabel == "Sto rispondendo…"
    emit(view, "message.completed", {"item_id": "message", "content": "Synthetic"})
    assert view.activityLabel == "Sto pensando…"
    # Pending nonblocking questions are not an execution pause.
    view.inputs._rows = [{"run_id": "run", "status": "pending", "blocking": False}]
    assert view.activityLabel == "Sto pensando…" and view.activityAnimating
    emit(view, "approval.requested", {"approval_id": "approval"})
    assert view.activityLabel == "In attesa di autorizzazione" and not view.activityAnimating
    emit(view, "backend.notice", {"approval_id": "approval", "decision": "allow", "message": "Allowed"})
    assert view.activityLabel == "Sto pensando…"
    emit(view, "run.state", {"state": "awaiting_input"})
    assert view.activityLabel == "Serve una decisione" and not view.activityAnimating
    emit(view, "run.state", {"state": "cancelling"})
    assert view.activityLabel == "Arresto richiesto" and not view.activityAnimating
    emit(view, "run.state", {"state": "completed"})
    assert view.activityLabel == "Completato" and not view.activityAnimating
    assert "run" not in view._active_tools and "run" not in view._streaming_items
    view._disconnected()
    assert view.activityLabel == "Connessione al servizio…" and not view.activityAnimating


def test_notice_filter_preserves_event_sequence_and_warnings(workspace):
    workspace._runs = {"run": {"id": "run", "state": "running"}}
    payloads = [{"message": "Codex session configured"}, {"message": "Codex warning"},
                {"message": "Codex session configured", "level": "error"},
                {"message": "Codex session configured", "severity": "warning"}]
    events = [{"run_id": "run", "seq": i + 1, "type": "backend.notice", "payload": p}
              for i, p in enumerate(payloads)]
    original = copy.deepcopy(events)
    for event in events:
        workspace._event(event)
    assert events == original and workspace._cursors["run"] == 4
    assert len(workspace.transcript.rows) == 3
    assert workspace.transcript.rows[0]["body"] == "Codex warning"
    # Replaying after a fresh project view applies the identical GUI-only filter.
    workspace.transcript.clear()
    workspace._cursors.clear()
    for event in events:
        workspace._event(event)
    assert len(workspace.transcript.rows) == 3


def test_editor_uses_argument_vector_and_browser_keeps_system(workspace, tmp_path, monkeypatch, qtbot):
    launches, urls = [], []
    monkeypatch.setattr("kira.ui.controller.shutil.which", lambda binary: "/usr/bin/code")
    monkeypatch.setattr("kira.ui.controller.QProcess.startDetached",
                        lambda *args: launches.append(args) or (True, 123))
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl",
                        lambda url: urls.append(url) or True)
    document = tmp_path / "--notes $(synthetic).md"
    document.write_text("Synthetic")
    workspace.setUiPreference("file_opener", "vscode")
    workspace.openMessageLink(document.as_uri())
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert launches == [("/usr/bin/code", ["--", str(document)])]
    workspace.openProjectFolder()
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert launches[-1] == ("/usr/bin/code", ["--", str(tmp_path)])
    workspace.openMessageLink("https://example.invalid/help")
    assert len(urls) == 1
    workspace._artifacts = [{"path": str(document)}]
    workspace.openArtifact(str(document))
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert launches[-1] == ("/usr/bin/code", ["--", str(document)])


def test_editor_unavailable_reports_without_fallback(workspace, tmp_path, monkeypatch, qtbot):
    launches = []
    monkeypatch.setattr("kira.ui.controller.shutil.which", lambda binary: None)
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl", lambda url: launches.append(url))
    document = tmp_path / "notes.md"
    document.write_text("Synthetic")
    workspace.setUiPreference("file_opener", "vscode")
    workspace.openMessageLink(str(document))
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert not launches and "VS Code non disponibile" in workspace.notice


@pytest.mark.parametrize("target", ["../outside.md", "script.py", "launch.desktop", "shortcut.md"])
def test_editor_preserves_chat_link_safety(workspace, tmp_path, monkeypatch, qtbot, target):
    launches = []
    monkeypatch.setattr("kira.ui.controller.shutil.which", lambda binary: "/usr/bin/code")
    monkeypatch.setattr("kira.ui.controller.QProcess.startDetached",
                        lambda *args: launches.append(args) or (True, 123))
    (tmp_path / "script.py").write_text("synthetic")
    (tmp_path / "launch.desktop").write_text("[Desktop Entry]")
    outside = tmp_path.parent / "outside.md"
    outside.write_text("synthetic")
    (tmp_path / "shortcut.md").symlink_to(outside)
    workspace.setUiPreference("file_opener", "vscode")
    workspace.openMessageLink(target)
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert not launches and workspace.notice


def test_editor_start_failure_reports_without_system_fallback(workspace, monkeypatch, qtbot):
    opened = []
    monkeypatch.setattr("kira.ui.controller.shutil.which", lambda binary: "/usr/bin/code")
    monkeypatch.setattr("kira.ui.controller.QProcess.startDetached", lambda *args: (False, 0))
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl", lambda url: opened.append(url))
    workspace.setUiPreference("file_opener", "vscode")
    workspace.openProjectFolder()
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert not opened and "non è stato avviato" in workspace.notice


def test_project_switch_discards_pending_editor_folder_open(workspace, monkeypatch):
    pending, opened = [], []
    monkeypatch.setattr(workspace, "_background", lambda work, done: pending.append((work, done)))
    monkeypatch.setattr(workspace, "_open_local_path", lambda path: opened.append(path))
    workspace.setUiPreference("file_opener", "vscode")
    workspace.openProjectFolder()
    workspace.openProject("next-project")
    work, done = pending[0]
    done(work())
    assert not opened


def test_streaming_notifies_activity_once_per_item_not_per_token(workspace):
    workspace._runs = {"run": {"id": "run", "state": "running"}}
    signals = []
    workspace.changed.connect(lambda: signals.append(workspace.activityLabel))
    for token in ("A", " synthetic", " reply"):
        emit(workspace, "message.delta", {"item_id": "message", "content": token})
    assert signals == ["Sto rispondendo…"]
    workspace._flush()
    assert signals == ["Sto rispondendo…"]
    assert workspace.transcript.rows[-1]["body"] == "A synthetic reply"
    emit(workspace, "message.completed", {"item_id": "message"})
    assert signals[-1] == "Sto pensando…"
    emit(workspace, "message.delta", {"item_id": "message", "content": " continued"})
    assert signals[-1] == "Sto rispondendo…" and len(signals) == 3
