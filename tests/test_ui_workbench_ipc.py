"""Real QML → Qt IPC → isolated service journey with a synthetic provider only."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QUrl, Qt
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace

FIXTURE = Path(__file__).parent / "fixtures/workbench_service.py"


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def open_desktop(paths):
    view = Workspace(paths)
    view._preferences["onboarding_complete"] = True
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(880, 620)
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


def test_real_gui_ipc_queue_restart_plan_and_chat_management(qapp, qtbot, monkeypatch):
    """A paused queue and a literal draft survive GUI restart while work continues."""
    with tempfile.TemporaryDirectory(prefix="kira-workbench-ipc-") as directory:
        home = Path(directory)
        paths = Paths(*(home / part for part in ("config", "data", "state", "runtime")))
        control = home / "fixture-control"
        control.mkdir()
        project = home / "synthetic-project"
        project.mkdir()
        monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
        environment = {**os.environ, "KIRA_HOME": str(home), "CODEX_HOME": str(home / "codex"),
                       "KIRA_TEST_WORKBENCH_CONTROL": str(control)}
        with (home / "service.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-I", str(FIXTURE)], env=environment,
                                       stdout=log, stderr=log)
            desktops = []
            try:
                qtbot.waitUntil(lambda: paths.socket.exists() or process.poll() is not None, timeout=5000)
                assert process.poll() is None
                desktop = open_desktop(paths)
                desktops.append(desktop)
                view, _, window = desktop
                qtbot.waitUntil(lambda: view.connected and bool(view.models), timeout=5000)
                view.createProject("Synthetic IPC workbench", str(project), "", "project_write")
                qtbot.waitUntil(lambda: bool(view.project) and not view.sessionsLoading, timeout=5000)
                project_id = view.project["id"]
                view.setModel("synthetic-workbench-model")
                # A saved provider setting must remain literal throughout mode/queue changes.
                view.setEffort("ultra")
                prompt = visual_item(window.contentItem(), "prompt")
                send = visual_item(window.contentItem(), "sendButton")
                prompt.setProperty("text", "Hold the first synthetic turn")
                qtbot.waitUntil(send.isEnabled)
                QMetaObject.invokeMethod(send, "clicked")
                qtbot.waitUntil(lambda: bool(view.activeRunId) and (control / "provider-calls.jsonl").exists(), timeout=5000)
                original_run = view.activeRunId
                original_session = view.sessionId
                qtbot.waitUntil(lambda: view.workbench.composerReady)
                view.workbench.setMode("plan")
                prompt.setProperty("text", "Queued read-only plan")
                queue = visual_item(window.contentItem(), "queueComposerButton")
                qtbot.waitUntil(lambda: queue.isVisible() and queue.isEnabled())
                assert queue.property("text") == "Accoda" and send.property("text") == "Invia indicazioni"
                bounds = queue.mapRectToItem(window.contentItem(), queue.boundingRect())
                assert bounds.left() >= 0 and bounds.right() <= window.width()
                assert bounds.top() >= 0 and bounds.bottom() <= window.height()
                QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                                 pos=queue.mapToScene(queue.boundingRect().center()).toPoint())
                qtbot.waitUntil(lambda: len(view.workbench.queueItems) == 1, timeout=5000)
                queued = view.workbench.queueItems[0]
                assert queued["status"] == "pending" and queued["mode"] == "plan"
                assert queued["session_id"] == original_session
                assert not view.workbench.queuePaused
                view.workbench.setQueuePaused(True)
                qtbot.waitUntil(lambda: view.workbench.queuePaused and not view.workbench.loading)
                draft = "Unsent literal draft: /plan @file è <text>"
                prompt.setProperty("text", draft)
                view.flushDrafts()
                close_desktop(desktop)
                desktops.remove(desktop)

                reopened = open_desktop(paths)
                desktops.append(reopened)
                view, _, window = reopened
                qtbot.waitUntil(lambda: view.connected, timeout=5000)
                view.openProject(project_id)
                qtbot.waitUntil(lambda: view.sessionId == original_session and not view.sessionsLoading, timeout=5000)
                qtbot.waitUntil(lambda: len(view.workbench.queueItems) == 1, timeout=5000)
                assert view.workbench.queuePaused and view.workbench.queueItems[0]["status"] == "pending"
                assert view.activeRunId == original_run
                assert visual_item(window.contentItem(), "prompt").property("text") == draft
                assert view.selectedModel == "synthetic-workbench-model" and view.selectedEffort == "ultra"
                (control / "release-first").write_text("release synthetic fixture\n")
                qtbot.waitUntil(lambda: not view.busy, timeout=5000)
                view.workbench.refreshQueue()
                qtbot.waitUntil(lambda: not view.workbench._queue_loading)
                assert view.workbench.queuePaused and view.workbench.queueItems[0]["status"] == "pending"
                assert len((control / "provider-calls.jsonl").read_text().splitlines()) == 1
                view.workbench.setQueuePaused(False)
                qtbot.waitUntil(lambda: view.workbench.queueItems[0]["status"] == "completed", timeout=7000)
                qtbot.waitUntil(lambda: not view.busy and not view.workbench.loading)
                calls = [json.loads(line) for line in (control / "provider-calls.jsonl").read_text().splitlines()]
                assert len(calls) == 2
                assert calls[1]["mode"] == "plan" and calls[1]["permission"] == "read_only"
                assert calls[1]["model"] == "synthetic-workbench-model"
                assert calls[1]["settings"]["reasoning_effort"] == "ultra"
                assert calls[1]["write_result"]["error"]["code"] == "PERMISSION_DENIED"
                assert not (project / "should-not-exist.txt").exists()
                assert view.project["permission"] == "project_write"
                assert view.draftText == draft and not view._pending_intents
                qtbot.waitUntil(lambda: any("Synthetic confirmed response: Queued read-only plan" in row["body"]
                                           for row in view.transcript.rows), timeout=5000)

                view.workbench.archive(original_session, True)
                qtbot.waitUntil(lambda: view.workbench.sessionArchived and not view.workbench.loading)
                assert original_session not in {row["id"] for row in view.sessions}
                view.workbench.archive(original_session, False)
                qtbot.waitUntil(lambda: not view.workbench.sessionArchived and not view.workbench.loading)
                qtbot.waitUntil(lambda: original_session in {row["id"] for row in view.sessions})
                export = home / "conversation.md"
                view.workbench.exportChat(QUrl.fromLocalFile(str(export)).toString())
                qtbot.waitUntil(lambda: export.exists() and export.stat().st_size > 0, timeout=5000)
                exported = export.read_text()
                assert "Hold the first synthetic turn" in exported and "Queued read-only plan" in exported
                assert "Synthetic confirmed response" in exported
                view.workbench.fork("")
                qtbot.waitUntil(lambda: view.sessionId != original_session and not view.sessionsLoading, timeout=5000)
                assert view.workbench.parentSessionId == original_session
                assert view._session["fork_mode"] == "copied_context"
                assert view.selectedModel == "synthetic-workbench-model" and view.selectedEffort == "ultra"
                assert view.transcript.rowCount() == 0
                assert view._drafts[(project_id, original_session)] == draft
            finally:
                (control / "release-first").touch()
                for desktop in desktops:
                    close_desktop(desktop)
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
