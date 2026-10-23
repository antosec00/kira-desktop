"""Opt-in real service/QML/Chromium journey; the provider is synthetic."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.service.direct_accounts import DirectAccounts
from kira.service.server import Service
from kira.service.web import Web
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


class ComputerAccounts(DirectAccounts):
    async def status(self):
        return {"active_account_id": "fixture-account", "accounts": [{"id": "fixture-account",
            "label": "Synthetic account", "connected": True, "plan_enabled": True}],
            "login": {"state": "idle"}, "mutation_allowed": True}


class ComputerProvider:
    def __init__(self):
        self.view_ready = asyncio.Event()
        self.results = []

    async def discover(self, **_):
        return {"available": True, "models": [{"id": "fixture-model", "name": "Synthetic model",
            "capabilities": {"tools": True, "images": True}, "reasoning_efforts": ["high"]}],
            "capabilities": {"tools": True, "images": True}}

    async def run(self, ctx):
        assert any(namespace["name"] == "computer" for namespace in ctx.client_tools)
        assert ctx.client_tool_call and ctx.tool_image
        navigated = await ctx.client_tool_call("computer-nav", "browser.navigate", {"url": "https://example.com/"})
        assert navigated["ok"]
        await self.view_ready.wait()
        observed = await ctx.client_tool_call("computer-observe", "computer.observe", {})
        assert observed["ok"], observed
        self.results.append(observed)
        image = await ctx.tool_image(observed["browser_image_id"])
        assert image["type"] == "input_image" and image["image_url"].startswith("data:image/png;base64,")
        # A real click in the blank outer margin is harmless and still requires
        # the same explicit GUI decision as a consequential visual interaction.
        acted = await ctx.client_tool_call("computer-click", "computer.act", {
            "observation_id": observed["observation_id"], "action": "click", "x": 12, "y": 12})
        assert acted["ok"] and acted["action_executed"], acted
        assert acted["observation_id"] != observed["observation_id"]
        self.results.append(acted)
        verified = await ctx.tool_image(acted["browser_image_id"])
        assert verified["image_url"].startswith("data:image/png;base64,")
        return {"state": "completed"}

    async def close(self):
        pass


async def spin(app, condition, timeout=50):
    async with asyncio.timeout(timeout):
        while not condition():
            app.processEvents()
            await asyncio.sleep(.01)
    app.processEvents()


@pytest.mark.skipif(not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                    reason="PLAYWRIGHT_BROWSERS_PATH must name the installed browser engine")
@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit real-browser integration opt-in")
@pytest.mark.parametrize("decision", ["accept", "accept_always"])
async def test_computer_observation_approval_action_and_live_view(qapp, tmp_path, decision):
    engine_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])

    class ComputerWeb(Web):
        @property
        def engine_path(self):
            return engine_root

    with tempfile.TemporaryDirectory(prefix="kira-computer-qt-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        provider = ComputerProvider()
        service = Service(paths, backend_factory=lambda *_: provider)
        workspace = qml = window = None
        await service.start()
        await service.web.close()
        service.web = ComputerWeb(service)
        service.direct_accounts = ComputerAccounts(service)
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic Kira Computer",
                "root": str(tmp_path), "permission": "project_write"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
                "model": "fixture-model", "settings": {"tools": True, "openai_account_id": "fixture-account",
                "reasoning_effort": "high"}}))["session"]
            workspace = Workspace(paths)
            workspace.setUiPreference("chatgpt_plan_welcome_seen", True)
            qml = create_engine(workspace)
            window = qml.rootObjects()[0]
            window.setWidth(1400)
            window.setHeight(900)
            workspace.start()
            await spin(qapp, lambda: workspace.connected)
            workspace.openProject(project["id"])
            await spin(qapp, lambda: workspace.sessionId == session["id"] and not workspace._chat_loading)
            workspace.send("Synthetic visual-control journey")
            await spin(qapp, lambda: bool(workspace.activeRunId))
            run = service.store.one("runs", workspace.activeRunId)
            viewport = window.findChild(QObject, "browserViewport")
            try:
                await spin(qapp, lambda: workspace.web.paneVisible and bool(workspace.web.frameUrl)
                    and not workspace.web.commandBusy and not workspace.web._command_queue
                    and workspace.web.frameWidth == max(640, min(int(viewport.property("width")), 1600)))
            except TimeoutError as error:
                # Retain a bounded synthetic diagnostic instead of hiding a
                # browser/provider failure behind a longer rendering timeout.
                stored = service.store.one("runs", run["id"])
                error.add_note(json.dumps({"state": stored["state"], "error": stored.get("error"),
                    "pane_visible": workspace.web.paneVisible, "web_error": workspace.web.error,
                    "browser": workspace.web.browser, "frame_width": workspace.web.frameWidth,
                    "viewport_width": viewport.property("width"),
                    "command_busy": workspace.web.commandBusy,
                    "window_active": workspace.web._window_active,
                    "queued_commands": len(workspace.web._command_queue)}))
                raise
            provider.view_ready.set()
            await spin(qapp, lambda: workspace.approval.get("tool") == "computer.act" or run["id"] not in service.tasks)
            assert run["id"] in service.tasks, service.store.one("runs", run["id"]).get("error")
            assert workspace.approval["kind"] == "browser_action"
            assert "Computer di Kira" in workspace.approval["description"]
            if decision == "accept_always":
                button = window.findChild(QObject, "approvalAllowAlways")
                await spin(qapp, lambda: button is not None and button.isVisible() and button.isEnabled())
                QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                    pos=button.mapToScene(button.boundingRect().center()).toPoint())
            else:
                workspace.approve("accept")
            await spin(qapp, lambda: run["id"] not in service.tasks)
            assert service.store.one("runs", run["id"])["state"] == "completed"
            assert len(provider.results) == 2
            assert not service.store.all("SELECT * FROM project_reservations")
            events = service.store.events(run["id"])["events"]
            completed = [event["payload"] for event in events if event["type"] == "tool.completed"]
            assert [row["name"] for row in completed] == ["browser.navigate", "computer.observe", "computer.act"]
            assert all(row["result"]["ok"] for row in completed)
            assert "data:image/" not in json.dumps(events) and "image/jpeg" not in json.dumps(events)
            assert service.image_inputs.linked("run", run["id"]) == []
            assert len(service.contexts.data._assets(session["id"])) == 2
            if decision == "accept_always":
                await spin(qapp, lambda: workspace.web.actionApprovalAlways and not workspace.web.busy)
                await spin(qapp, lambda: not workspace.busy)
                workspace.send("Synthetic automatic consent journey")
                await spin(qapp, lambda: bool(workspace.activeRunId) and workspace.activeRunId != run["id"])
                second = service.store.one("runs", workspace.activeRunId)
                await spin(qapp, lambda: second["id"] not in service.tasks)
                assert service.store.one("runs", second["id"])["state"] == "completed"
                assert len(provider.results) == 4
                assert not any(event["type"] == "approval.requested"
                               for event in service.store.events(second["id"])["events"])
                # Revoke through the real GUI controller/IPC while retaining the
                # same browser. The following action must request consent again.
                browser_id = service.web.actors[session["id"]].id
                workspace.web.setActionApprovalAlways(False)
                await spin(qapp, lambda: not workspace.web.actionApprovalAlways and not workspace.web.busy)
                assert service.web.actors[session["id"]].id == browser_id
                await spin(qapp, lambda: not workspace.busy)
                workspace.send("Synthetic revoked consent journey")
                await spin(qapp, lambda: bool(workspace.activeRunId) and workspace.activeRunId != second["id"])
                third = service.store.one("runs", workspace.activeRunId)
                await spin(qapp, lambda: workspace.approval.get("run_id") == third["id"]
                           or third["id"] not in service.tasks)
                assert workspace.approval.get("run_id") == third["id"]
                assert len(provider.results) == 5  # Observation, with no click yet.
                workspace.approve("accept")
                await spin(qapp, lambda: third["id"] not in service.tasks)
                assert service.store.one("runs", third["id"])["state"] == "completed"
                assert len(provider.results) == 6
                assert service.store.one("sessions", session["id"])["settings"]["browser_action_approval"] == "ask"
            screenshot = tmp_path / "computer-integration.png"
            assert window.grabWindow().save(str(screenshot))
            workspace.web.closeBrowser()
            await spin(qapp, lambda: session["id"] not in service.web.actors and not workspace.web.browser["active"])
            assert not service.store.all("SELECT * FROM browser_processes WHERE state!='stopped'")
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
