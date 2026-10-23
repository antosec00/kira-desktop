"""Real service/Qt IPC failure presentation with a synthetic direct provider."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject
from PySide6.QtQml import QQmlEngine, QQmlExpression

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.paths import Paths
from kira.service.direct_accounts import DirectAccounts
from kira.service.server import Service
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from test_ui_support import SERVICE_WAIT_MS

MODEL = "synthetic-failure-model"
ACCOUNT = "synthetic-failure-account"
PROVIDER_REQUEST = "req_synthetic_unavailable"


class FailureAuth:
    async def status(self):
        return {"active_account_id": ACCOUNT}

    async def access_token(self, account):
        assert account == ACCOUNT
        return "synthetic-unused-token"


class FailureAccounts(DirectAccounts):
    async def status(self):
        return {"active_account_id": ACCOUNT, "accounts": [{"id": ACCOUNT,
            "label": "Synthetic account", "connected": True, "plan_enabled": True}],
            "login": {"state": "idle"}, "mutation_allowed": True}


class FailureTransport:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.inferences = []

    async def __call__(self, request):
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"models": [{"slug": MODEL, "display_name": "Synthetic model",
                "visibility": "list", "context_window": 128000, "supported_reasoning_efforts": ["high"]}]})
        if request.url.path == "/v1/responses/input_tokens":
            return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing"}})
        assert request.method == "POST" and request.url.path == "/v1/responses"
        self.inferences.append(json.loads(request.content))
        self.started.set()
        await self.release.wait()
        return httpx.Response(503, headers={"x-request-id": PROVIDER_REQUEST},
                              json={"detail": "Synthetic unavailable"})


async def spin(app, condition):
    async with asyncio.timeout(SERVICE_WAIT_MS / 1000):
        while not condition():
            app.processEvents()
            await asyncio.sleep(.01)
    app.processEvents()


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


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


async def test_failed_direct_request_is_visible_with_durable_details_and_survives_reopen(qapp, tmp_path):
    """A 503 is shown in chat without retrying, changing settings or losing the next draft."""
    with tempfile.TemporaryDirectory(prefix="kira-failure-qt-") as directory, \
            patch.dict(os.environ, {"KIRA_HOME": directory, "XDG_CACHE_HOME": str(Path(directory) / "cache")}):
        base = Path(directory)
        paths = Paths(*(base / part for part in ("config", "data", "state", "runtime")))
        transport = FailureTransport()
        service = Service(paths, backend_factory=lambda *_: OpenAIChatGPTBackend(
            FailureAuth(), transport=httpx.MockTransport(transport)))
        desktops = []
        await service.start()
        service.direct_accounts = FailureAccounts(service)
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic failure",
                "root": str(tmp_path), "permission": "project_write"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "openai_chatgpt", "model": MODEL, "settings": {
                    "tools": True, "openai_account_id": ACCOUNT, "reasoning_effort": "high"}}))["session"]
            desktop = open_desktop(paths)
            desktops.append(desktop)
            view, _, window = desktop
            await spin(qapp, lambda: view.connected)
            view.openProject(project["id"])
            await spin(qapp, lambda: view.sessionId == session["id"] and not view._chat_loading)
            prompt = visual_item(window.contentItem(), "prompt")
            send = visual_item(window.contentItem(), "sendButton")
            submitted = "Read the synthetic project instructions and reply briefly."
            prompt.setProperty("text", submitted)
            await spin(qapp, send.isEnabled)
            assert QMetaObject.invokeMethod(send, "clicked")
            await spin(qapp, lambda: transport.started.is_set() and bool(view.activeRunId)
                       and view._pending_start is None)
            run_id = view.activeRunId
            assert len(transport.inferences) == 1
            assert service.store.one("runs", run_id)["state"] == "running"
            assert not view.runFailure.get("visible")
            # Keep the provider response pending while the user writes a new
            # draft, so failure handling must preserve independently edited text.
            draft = "Unsent draft: è <literal> **still mine**"
            prompt.setProperty("text", draft)
            view.flushDrafts()
            assert view.draftText == draft
            transport.release.set()
            await spin(qapp, lambda: run_id not in service.tasks and view.runFailure.get("visible")
                       and not view.busy and not view._chat_loading)
            run = service.store.one("runs", run_id)
            assert run["state"] == "failed"
            assert run["outcome"] == {"body_shape": "detail", "http_status": 503,
                                      "request_id": PROVIDER_REQUEST}
            assert run["error"] == "OpenAI request failed (request_failed, HTTP 503)"
            events = service.store.events(run_id)["events"]
            assert not any(event["type"] in {"tool.started", "tool.completed", "message.delta", "message.completed"}
                           for event in events)
            assert any(event["type"] == "message.user" and event["payload"]["content"] == submitted
                       for event in events)
            assert not service.store.all("SELECT * FROM project_reservations")

            for reopened in (False, True):
                if reopened:
                    close_desktop(desktop)
                    desktops.remove(desktop)
                    desktop = open_desktop(paths)
                    desktops.append(desktop)
                    view, _, window = desktop
                    await spin(qapp, lambda: view.connected)
                    view.openProject(project["id"])
                    await spin(qapp, lambda: view.sessionId == session["id"]
                               and not view._chat_loading and view.runFailure.get("visible"))
                    prompt = visual_item(window.contentItem(), "prompt")
                panel = visual_item(window.contentItem(), "chatRunError")
                assert panel is not None and panel.isVisible()
                assert view.runFailure["runId"] == run_id
                assert "HTTP 503" in visual_item(window.contentItem(), "chatRunErrorSummary").property("text")
                assert visual_item(window.contentItem(), "chatRunErrorCause").property("text") == run["error"]
                details_button = visual_item(window.contentItem(), "chatRunErrorDetails")
                assert details_button.isVisible() and details_button.isEnabled()
                assert QMetaObject.invokeMethod(details_button, "clicked")
                await spin(qapp, lambda: evaluate(window, "eventDialog.visible"))
                details = visual_item(window.contentItem(), "eventDetailsText")
                raw = json.loads(details.property("text"))
                assert raw["execution_id"] == run_id and raw["session_id"] == session["id"]
                assert raw["request_id"] == run["request_id"]
                assert raw["outcome"] == run["outcome"] and raw["error"] == run["error"]
                assert raw["request"] == submitted and raw["state"] == "failed"
                assert evaluate(details, "Number(textFormat)") == 0
                evaluate(window, "eventDialog.close()")
                assert view.draftText == draft and prompt.property("text") == draft
                assert view.backend == "openai_chatgpt"
                assert view.selectedModel == MODEL and view.selectedEffort == "high"
                assert len(transport.inferences) == 1
                assert not view.activeRunId and not view.busy and window.property("page") == 0
            assert transport.inferences[0]["model"] == MODEL
            assert transport.inferences[0]["reasoning"] == {"effort": "high", "summary": "auto"}
            saved_session = service.store.one("sessions", session["id"])
            assert saved_session["backend"] == "openai_chatgpt" and saved_session["model"] == MODEL
            assert saved_session["settings"]["reasoning_effort"] == "high"
            assert saved_session["settings"]["openai_account_id"] == ACCOUNT
            assert len(service.store.all("SELECT * FROM runs")) == 1
        finally:
            transport.release.set()
            for desktop in reversed(desktops):
                close_desktop(desktop)
            await service.close()
