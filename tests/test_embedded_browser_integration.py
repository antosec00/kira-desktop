"""Opt-in real Chromium/service/QML transport check with a synthetic provider.

No OpenAI account, model request or personal profile is used. The one public
example page is used only when KIRA_TEST_BROWSER=1 explicitly enables this test.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.service.direct_accounts import DirectAccounts
from kira.service.server import Service
from kira.service.web import Web
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


class FixtureAccounts(DirectAccounts):
    async def status(self):
        return {"active_account_id": "fixture-account", "accounts": [{"id": "fixture-account",
            "label": "Synthetic account", "connected": True, "plan_enabled": True}],
            "login": {"state": "idle"}, "mutation_allowed": True}


class FixtureBackend:
    async def discover(self, **_):
        return {"available": True, "models": [{"id": "fixture-model", "name": "Synthetic model",
            "capabilities": {"tools": True}, "reasoning_efforts": ["high"]}], "capabilities": {"tools": True}}

    async def run(self, ctx):
        name = "browser.find" if ctx.prompt == "Synthetic second turn" else "browser.navigate"
        arguments = {"text": "documentation"} if name == "browser.find" else {"url": "https://example.com/"}
        assert ctx.client_tool_call
        result = await ctx.client_tool_call("fixture-" + ctx.run_id, name, arguments)
        return {"state": "completed" if result.get("ok") else "failed", "error": None if result.get("ok") else str(result)}

    async def close(self):
        pass


async def spin(qapp, condition, timeout=40):
    async with asyncio.timeout(timeout):
        while not condition():
            qapp.processEvents()
            await asyncio.sleep(0.01)
    qapp.processEvents()


@pytest.mark.skipif(not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                    reason="PLAYWRIGHT_BROWSERS_PATH must name the installed browser engine")
@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit live-browser integration opt-in")
async def test_real_embedded_page_survives_turns_and_manual_handoff(qapp, tmp_path):
    engine_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])

    class FixtureWeb(Web):
        @property
        def engine_path(self):
            return engine_root

    with tempfile.TemporaryDirectory(prefix="kira-embedded-qt-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: FixtureBackend())
        workspace = qml = window = None
        await service.start()
        await service.web.close()
        service.web = FixtureWeb(service)
        service.direct_accounts = FixtureAccounts(service)
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic embedded browser",
                "root": str(tmp_path), "permission": "read_only"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
                "model": "fixture-model", "settings": {"tools": True, "openai_account_id": "fixture-account", "reasoning_effort": "high"}}))["session"]
            workspace = Workspace(paths)
            workspace.setUiPreference("chatgpt_plan_welcome_seen", True)
            qml = create_engine(workspace)
            assert qml.rootObjects()
            window = qml.rootObjects()[0]
            window.setWidth(1400)
            window.setHeight(900)
            workspace.start()
            await spin(qapp, lambda: workspace.connected)
            workspace.openProject(project["id"])
            await spin(qapp, lambda: workspace.sessionId == session["id"] and not workspace._chat_loading)
            run = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "embedded-first", "prompt": "Synthetic first turn"}))["run"]
            await spin(qapp, lambda: run["id"] not in service.tasks)
            assert service.store.one("runs", run["id"])["state"] == "completed"
            await spin(qapp, lambda: workspace.web.paneVisible and bool(workspace.web.frameUrl))
            pane = window.findChild(QObject, "browserPane")
            assert pane is not None and pane.property("visible")
            assert workspace.web.browser["mode"] == "embedded"
            assert workspace.web.browser["headed"] is True
            assert workspace.web.browser["url"] == "https://example.com/"
            viewport = window.findChild(QObject, "browserViewport")
            assert viewport is not None
            await spin(qapp, lambda: not workspace.web.commandBusy and workspace.web.frameWidth ==
                max(640, min(int(viewport.property("width")), 1600)) and workspace.web.frameHeight ==
                max(400, min(int(viewport.property("height")), 1200)))
            actor = service.web.actors[session["id"]]
            browser_id, pid = actor.id, actor.engine.pid
            assert not service.store.all("SELECT * FROM project_reservations")
            assert service.store.db.execute("SELECT state FROM browser_processes WHERE run_id=?", (browser_id,)).fetchone()[0] == "active"
            # Captures only synthetic chat/public page, never a personal session.
            screenshot = tmp_path / "embedded-browser-qt.png"
            assert window.grabWindow().save(str(screenshot))
            workspace.web.command("ui_takeover", {})
            await spin(qapp, lambda: workspace.web.controlling and not workspace.web.commandBusy)
            address = window.findChild(QObject, "browserAddress")
            assert address is not None
            address.forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            entered = ""
            for character in "example.com/#embedded":
                qapp.sendEvent(window, QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, character))
                qapp.sendEvent(window, QKeyEvent(QEvent.Type.KeyRelease, 0, Qt.KeyboardModifier.NoModifier, character))
                entered += character
                # Frames and unchanged page state use this same notification.
                # They must not replace an address that the user is editing.
                workspace.web.changed.emit()
                assert address.property("text") == entered
            QTest.keyClick(window, Qt.Key.Key_Return)
            await spin(qapp, lambda: workspace.web.browser.get("url", "").endswith("#embedded")
                       and not workspace.web.commandBusy and not workspace.web._command_queue)
            assert workspace.web.browser["url"] == "https://example.com/#embedded"
            assert workspace.web.error == ""
            workspace.web.command("ui_resume", {})
            await spin(qapp, lambda: not workspace.web.controlling and not workspace.web.commandBusy)
            second = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "embedded-second", "prompt": "Synthetic second turn"}))["run"]
            await spin(qapp, lambda: second["id"] not in service.tasks)
            assert service.store.one("runs", second["id"])["state"] == "completed"
            assert service.web.actors[session["id"]].id == browser_id
            assert service.web.actors[session["id"]].engine.pid == pid
            rows = service.store.all("SELECT * FROM events")
            assert "image/jpeg" not in json.dumps(rows) and "ui_navigate" not in json.dumps(rows)
            workspace.web.closeBrowser()
            await spin(qapp, lambda: not workspace.web.browser.get("active") and session["id"] not in service.web.actors)
            assert service.store.db.execute("SELECT state FROM browser_processes WHERE run_id=?", (browser_id,)).fetchone()[0] == "stopped"
            assert not (paths.data / "browser-temporary" / browser_id).exists()
        finally:
            if workspace:
                workspace.close()
            if window:
                window.setProperty("closeConfirmed", True)
                window.close()
            if qml:
                qml.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            await service.close()
