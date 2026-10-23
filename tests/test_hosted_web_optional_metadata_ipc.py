"""Real service/QML continuity with synthetic hosted-web optional metadata."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.paths import Paths
from kira.service.direct_accounts import DirectAccounts
from kira.service.server import Service
from kira.ui.app import create_engine
from kira.ui.controller import Workspace

MODEL = "synthetic-web-metadata-model"
ACCOUNT = "synthetic-web-metadata-account"


class MetadataAuth:
    async def status(self):
        return {"active_account_id": ACCOUNT}

    async def access_token(self, account):
        assert account == ACCOUNT
        return "synthetic-unused-token"


class MetadataAccounts(DirectAccounts):
    async def status(self):
        return {"active_account_id": ACCOUNT, "accounts": [{"id": ACCOUNT,
            "label": "Synthetic account", "connected": True, "plan_enabled": True}],
            "login": {"state": "idle"}, "mutation_allowed": True}


class MetadataStream(httpx.AsyncByteStream):
    """Pause after real streamed text so the test observes unfinished GUI output."""

    def __init__(self, initial, final):
        self.initial, self.final = initial, final
        self.release = asyncio.Event()

    async def __aiter__(self):
        for record in self.initial:
            yield ("data: " + json.dumps(record) + "\n\n").encode()
        await self.release.wait()
        for record in self.final:
            yield ("data: " + json.dumps(record) + "\n\n").encode()


class MetadataTransport:
    def __init__(self):
        self.cases = [(shape, envelope) for shape in ("omitted_url", "null_url", "omitted_action")
                      for envelope in ("done_empty_terminal", "terminal_only")]
        self.inferences = []
        self.streams = []
        self.native_items = []
        self.answers = []

    async def __call__(self, request):
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"models": [{"slug": MODEL, "display_name": "Synthetic model",
                "visibility": "list", "context_window": 128000, "supported_reasoning_efforts": ["xhigh"]}]})
        if request.url.path == "/v1/responses/input_tokens":
            return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing"}})
        assert request.method == "POST" and request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["model"] == MODEL and body["reasoning"] == {"effort": "xhigh", "summary": "auto"}
        number = len(self.inferences)
        assert number <= len(self.cases), "No automatic provider retry is allowed"
        # Subsequent turns must carry native provider evidence exactly as sent,
        # including absent versus null fields. Tool-display markers stay separate.
        assert all(item in body["input"] for item in self.native_items)
        self.inferences.append(body)
        answer = f"Synthetic hosted-web answer {number + 1}."
        self.answers.append(answer)
        message = {"type": "message", "id": f"message-{number}", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": answer, "annotations": []}]}
        output = [message]
        envelope = "terminal_only"
        if number < len(self.cases):
            shape, envelope = self.cases[number]
            item = {"type": "web_search_call", "id": f"synthetic-web-{number}", "status": "completed"}
            if shape != "omitted_action":
                item["action"] = {"type": "open_page"}
                if shape == "null_url":
                    item["action"]["url"] = None
            self.native_items.append(item)
            output = [item, message]
            message["content"][0]["annotations"] = [{"type": "url_citation",
                "url": f"https://example.com/source-{number}", "title": f"Synthetic source {number}",
                "start_index": 0, "end_index": len(answer)}]
        initial = [{"type": "response.output_text.delta", "output_index": len(output) - 1,
                    "delta": answer[:18]}]
        final = []
        if envelope == "done_empty_terminal":
            initial.insert(0, {"type": "response.output_item.added", "output_index": 0,
                "item": {"type": "web_search_call", "id": output[0]["id"], "status": "in_progress"}})
            final.extend({"type": "response.output_item.done", "output_index": index, "item": item}
                         for index, item in enumerate(output))
        final.append({"type": "response.completed", "response": {"status": "completed",
            "output": [] if envelope == "done_empty_terminal" else output}})
        stream = MetadataStream(initial, final)
        self.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)


async def spin(app, condition):
    async with asyncio.timeout(5):
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


async def test_optional_hosted_web_metadata_completes_gui_and_preserves_next_turn(qapp, tmp_path):
    """Omitted/null web metadata cannot strand a streamed answer or its next turn."""
    with tempfile.TemporaryDirectory(prefix="kira-web-metadata-") as directory, \
            patch.dict(os.environ, {"XDG_CACHE_HOME": str(Path(directory) / "cache")}):
        base = Path(directory)
        paths = Paths(*(base / part for part in ("config", "data", "state", "runtime")))
        transport = MetadataTransport()
        service = Service(paths, backend_factory=lambda *_: OpenAIChatGPTBackend(
            MetadataAuth(), transport=httpx.MockTransport(transport)))
        view = engine = window = None
        await service.start()
        service.direct_accounts = MetadataAccounts(service)
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic optional web metadata",
                "root": str(tmp_path), "permission": "project_write"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "openai_chatgpt", "model": MODEL, "settings": {
                    "tools": True, "openai_account_id": ACCOUNT, "reasoning_effort": "xhigh"}}))["session"]
            view = Workspace(paths)
            view.setUiPreference("onboarding_complete", True)
            view.setUiPreference("chatgpt_plan_welcome_seen", True)
            engine = create_engine(view)
            assert engine.rootObjects()
            window = engine.rootObjects()[0]
            window.resize(1380, 900)
            view.client.connect_service()
            await spin(qapp, lambda: view.connected)
            view.openProject(project["id"])
            await spin(qapp, lambda: view.sessionId == session["id"] and not view._chat_loading)
            prompt = visual_item(window.contentItem(), "prompt")
            send = visual_item(window.contentItem(), "sendButton")
            run_ids = []
            for number in range(len(transport.cases) + 1):
                prompt.setProperty("text", f"Perform synthetic web check {number + 1}.")
                await spin(qapp, send.isEnabled)
                assert QMetaObject.invokeMethod(send, "clicked")
                await spin(qapp, lambda: len(transport.streams) == number + 1 and bool(view.activeRunId)
                           and view._pending_start is None and any(row["kind"] == "assistant"
                           and row["runId"] == view.activeRunId and row["status"] == "streaming"
                           for row in view.transcript.rows))
                run_id = view.activeRunId
                run_ids.append(run_id)
                transport.streams[number].release.set()
                await spin(qapp, lambda: run_id not in service.tasks and not view.busy
                           and not view._chat_loading and view._runs.get(run_id, {}).get("state")
                           in {"completed", "failed", "interrupted"})
                run = service.store.one("runs", run_id)
                assert run["state"] == "completed", run.get("error")
                assert not view.runFailure.get("visible")
                answers = [row for row in view.transcript.rows if row["kind"] == "assistant"
                           and row["runId"] == run_id]
                assert len(answers) == 1 and answers[0]["status"] == "completo"
                assert answers[0]["body"].startswith(transport.answers[number])
                events = service.store.events(run_id)["events"]
                completed = [event["payload"] for event in events if event["type"] == "tool.completed"]
                if number < len(transport.cases):
                    assert f"https://example.com/source-{number}" in answers[0]["body"]
                    assert len(completed) == 1 and completed[0]["name"] == "web_search"
                    result = completed[0]["result"]
                    assert result["ok"] and result["status"] == "completed"
                    assert result["execution"] == "provider_hosted" and result["evidence"] == "provider_stream"
                    assert "url" not in result
                    # The application must label unavailable metadata rather
                    # than inventing a destination or treating it as failure.
                    if transport.cases[number][0] == "omitted_action":
                        assert result["action_details_available"] is False
                        assert "action" not in result
                    else:
                        assert result["destination_available"] is False
                        assert result["action"] == "open_page"
                else:
                    assert completed == []
                assert all(event["payload"]["name"] == "web_search" for event in events
                           if event["type"] in {"tool.started", "tool.completed"})
                assert len([event for event in events if event["type"] == "message.completed"]) == 1
                assert not service.web.actors and not service.store.all("SELECT * FROM project_reservations")
                assert len(transport.inferences) == number + 1
                state = service.contexts.data.load(service.store.one("sessions", session["id"]))
                assert state is not None and all(item in state["items"] for item in transport.native_items)
                assert state["model"] == MODEL and state["account_id"] == ACCOUNT
                assert view.backend == "openai_chatgpt" and view.selectedModel == MODEL
                assert view.selectedEffort == "xhigh" and not view.activeRunId
            assert len(run_ids) == len(set(run_ids)) == 7
            assert len(service.store.all("SELECT * FROM runs")) == 7
            current = service.store.one("sessions", session["id"])
            assert current["backend"] == "openai_chatgpt" and current["model"] == MODEL
            assert current["settings"]["reasoning_effort"] == "xhigh"
            assert current["settings"]["openai_account_id"] == ACCOUNT
        finally:
            for stream in transport.streams:
                stream.release.set()
            if window:
                window.setProperty("closeConfirmed", True)
                window.close()
            if view:
                view.close()
            if engine:
                engine.deleteLater()
            if view:
                view.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            await service.close()
