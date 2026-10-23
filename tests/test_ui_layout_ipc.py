"""Compact-layout journey through actual QML/IPC with a synthetic provider."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.service.server import Service
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from test_ui_support import SERVICE_WAIT_MS

MODEL = "synthetic-layout-model"
EFFORT = "high"
OUTPUT = "Synthetic literal tool output <data> & value\n" * 24


class LayoutBackend:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def discover(self):
        return {"available": True, "capabilities": {"tools": True}, "models": [
            {"id": MODEL, "name": "Synthetic layout model", "capabilities": {"tools": True},
             "supported_reasoning_efforts": [{"reasoningEffort": EFFORT}]}]}

    async def run(self, ctx):
        self.calls.append({"model": ctx.model, "settings": dict(ctx.settings)})
        for number in range(3):
            tool_id = f"layout-tool-{number}"
            await ctx.emit("tool.started", {"tool_id": tool_id, "name": "synthetic_read",
                                            "arguments": {"path": "example.txt"}})
            await ctx.emit("tool.completed", {"tool_id": tool_id, "name": "synthetic_read",
                                              "result": {"ok": True, "text": OUTPUT}})
        self.started.set()
        await self.release.wait()
        await ctx.emit("tool.started", {"tool_id": "layout-tool-error", "name": "synthetic_check",
                                        "arguments": {}})
        await ctx.emit("tool.completed", {"tool_id": "layout-tool-error", "name": "synthetic_check",
                                          "result": {"ok": False, "error": {
                                              "code": "SYNTHETIC_FAILURE", "message": "Synthetic visible failure"}}})
        return {"state": "interrupted", "error": "Synthetic layout interruption",
                "outcome": {"fixture": True, "confirmation": "unavailable"}}

    async def close(self):
        pass


async def spin(app, condition):
    async with asyncio.timeout(SERVICE_WAIT_MS / 1000):
        while not condition():
            app.processEvents()
            await asyncio.sleep(.01)
    app.processEvents()


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def open_desktop(paths):
    view = Workspace(paths)
    view.setUiPreference("onboarding_complete", True)
    view.setUiPreference("chatgpt_plan_welcome_seen", True)
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
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


async def test_compact_layout_grouping_details_and_draft_survive_gui_reopen(qapp, tmp_path):
    """Layout choices never repeat a provider turn or alter recorded tool results."""
    with tempfile.TemporaryDirectory(prefix="kira-layout-ipc-") as directory, \
            patch.dict(os.environ, {"KIRA_HOME": directory, "XDG_CACHE_HOME": str(Path(directory) / "cache")}):
        base = Path(directory)
        paths = Paths(*(base / part for part in ("config", "data", "state", "runtime")))
        backend = LayoutBackend()
        service = Service(paths, backend_factory=lambda *_: backend)
        desktops = []
        await service.start()
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic layout",
                "root": str(tmp_path), "permission": "project_write"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "codex", "model": MODEL, "settings": {
                    "tools": True, "reasoning_effort": EFFORT}}))["session"]
            desktop = open_desktop(paths)
            desktops.append(desktop)
            view, _, window = desktop
            await spin(qapp, lambda: view.connected)
            view.openProject(project["id"])
            await spin(qapp, lambda: view.sessionId == session["id"] and not view._chat_loading)
            # Explicitly dismiss the introductory hint through the actual control.
            hide = item(window.contentItem(), "hideResumeHint")
            assert hide is not None and hide.isVisible()
            assert QMetaObject.invokeMethod(hide, "clicked")
            assert not view.resumeHintVisible
            assert item(window.contentItem(), "resumeWorkspaceButton").isVisible()
            prompt = item(window.contentItem(), "prompt")
            send = item(window.contentItem(), "sendButton")
            prompt.setProperty("text", "Perform the synthetic layout checks.")
            await spin(qapp, send.isEnabled)
            assert QMetaObject.invokeMethod(send, "clicked")
            await spin(qapp, lambda: backend.started.is_set() and view.activityCanInspect
                       and len(view.transcript.rows) == 4)
            run_id = view.activeRunId
            assert run_id and view.activityStartedAt
            recorded = service.store.events(run_id)["events"]
            original_rows = [(row["toolId"], row["body"], row["raw"]) for row in view.transcript.rows]
            assert view.transcript.rows[1]["toolGroupCount"] == 3
            assert not view.transcript.rows[1]["toolGroupExpanded"]
            toggle = item(window.contentItem(), "toolGroupToggle_1")
            await spin(qapp, lambda: toggle is not None and toggle.isVisible())
            assert QMetaObject.invokeMethod(toggle, "clicked")
            await spin(qapp, lambda: view.transcript.rows[1]["toolGroupExpanded"])
            receipt = item(window.contentItem(), "toolReceipt_1")
            assert receipt is not None and receipt.isVisible() and not receipt.property("expanded")
            assert not item(window.contentItem(), "messageCopy_1").isVisible()
            receipt.forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_Space)
            await spin(qapp, lambda: view.transcript.rows[1]["receiptExpanded"])
            copy = item(window.contentItem(), "messageCopy_1")
            assert copy is not None and copy.isVisible()
            assert QMetaObject.invokeMethod(copy, "clicked")
            assert QGuiApplication.clipboard().text() == view.transcript.rows[1]["body"]
            assert [(row["toolId"], row["body"], row["raw"]) for row in view.transcript.rows] == original_rows
            assert service.store.events(run_id)["events"] == recorded
            details = json.loads(view.activityDetails)
            assert details["execution_id"] == run_id
            detail_button = item(window.contentItem(), "activityDetailsButton")
            assert detail_button is not None and detail_button.isVisible()
            assert QMetaObject.invokeMethod(detail_button, "clicked")
            event_dialog = window.findChild(QObject, "eventDialog")
            assert event_dialog is not None
            await spin(qapp, lambda: event_dialog.property("opened"))
            assert QMetaObject.invokeMethod(event_dialog, "close")
            draft = "Unsent synthetic draft: è <literal> **unchanged**"
            prompt.setProperty("text", draft)
            view.flushDrafts()
            backend.release.set()
            await spin(qapp, lambda: run_id not in service.tasks and view.runFailure.get("visible")
                       and not view.busy)
            assert item(window.contentItem(), "chatRunError").isVisible()
            assert not view.activityStartedAt
            assert view.transcript.rows[1]["toolGroupAttention"]
            assert view.transcript.rows[1]["toolGroupExpanded"]
            outcome = service.store.one("runs", run_id)
            assert outcome["state"] == "interrupted"
            assert outcome["outcome"] == {"fixture": True, "confirmation": "unavailable"}
            assert len(backend.calls) == 1
            close_desktop(desktop)
            desktops.remove(desktop)
            desktop = open_desktop(paths)
            desktops.append(desktop)
            view, _, window = desktop
            await spin(qapp, lambda: view.connected)
            view.openProject(project["id"])
            await spin(qapp, lambda: view.sessionId == session["id"] and not view._chat_loading
                       and view.runFailure.get("visible"))
            assert not view.resumeHintVisible
            assert item(window.contentItem(), "resumeWorkspaceButton").isVisible()
            assert item(window.contentItem(), "prompt").property("text") == draft
            assert (view.selectedModel, view.selectedEffort) == (MODEL, EFFORT)
            assert item(window.contentItem(), "chatRunError").isVisible()
            assert view.transcript.rows[1]["toolGroupAttention"]
            assert len(backend.calls) == 1
            assert service.store.one("runs", run_id)["outcome"] == outcome["outcome"]
            assert all(event in service.store.events(run_id)["events"] for event in recorded)
        finally:
            backend.release.set()
            for desktop in desktops:
                close_desktop(desktop)
            await service.close()
