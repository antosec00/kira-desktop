"""Synthetic service/browser/Responses integration; no personal data or provider use."""

import asyncio
import base64
import json
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from PySide6.QtGui import QImage

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode
from kira.tools import browser


class Auth:
    async def status(self):
        return {"active_account_id": "synthetic-account"}

    async def access_token(self, account):
        return "synthetic-token"


class Engine:
    instances = []

    def __init__(self, profile_dir, artifacts_dir, **options):
        self.profile_dir, self.artifacts_dir = profile_dir, artifacts_dir
        self.options = options
        self.calls = []
        self.closed = False
        self.instances.append(self)

    async def start(self):
        pass

    async def call(self, action, arguments):
        self.calls.append((action, arguments))
        if action == "set_artifacts":
            self.artifacts_dir = Path(arguments["path"])
        if action == "prepare_interaction":
            return {"ok": True, "token": "synthetic-approval-token", "url": "https://example.com/",
                    "target": {"label": "Continue", "role": "button"}}
        if action == "screenshot":
            path = self.artifacts_dir / "synthetic.png"
            image = QImage(40, 30, QImage.Format.Format_RGB32)
            image.fill(0x55AA99)
            assert image.save(str(path), "PNG")
            return {"ok": True, "url": "https://example.com/", "screenshot": {"path": str(path)}}
        return {"ok": True, "title": "Synthetic page", "url": "https://example.com/", "text": "Synthetic content"}

    async def close(self):
        self.closed = True


def response(items):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
        content=("data: " + json.dumps({"type": "response.completed", "response": {
            "status": "completed", "output": items}}) + "\n\n").encode())


def call(action, args=None):
    return {"type": "function_call", "status": "completed", "namespace": "browser",
            "name": action, "call_id": "synthetic-" + action, "arguments": json.dumps(args or {})}


class Transport:
    def __init__(self):
        self.requests = []
        self.outputs = [[call("navigate", {"url": "https://example.com/"})], [call("screenshot")]]

    def __call__(self, request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"slug": "fixture", "context_window": 128000}]})
        if not request.url.path.endswith("/responses"):
            return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing"}})
        self.requests.append(json.loads(request.content))
        return response(self.outputs.pop(0) if self.outputs else [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Synthetic completion"}]}])


@pytest_asyncio.fixture
async def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "BrowserSession", Engine)
    monkeypatch.setattr(browser, "engine_available", lambda _: True)
    Engine.instances = []
    with tempfile.TemporaryDirectory(prefix="kira-web-") as directory:
        paths = Paths(*(Path(directory) / item for item in ("config", "data", "state", "runtime")))
        transport = Transport()
        service = Service(paths, backend_factory=lambda *_: OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(transport)))
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic", "root": str(tmp_path), "permission": "project_write"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
            "model": "fixture", "settings": {"tools": True, "openai_account_id": "synthetic-account"}}))["session"]
        try:
            yield service, transport, project, session
        finally:
            await service.close()


async def start(fixture):
    service, _, _, session = fixture
    return (await service.dispatch("run.start", {"session_id": session["id"], "request_id": "synthetic-run", "prompt": "Inspect the synthetic page"}))["run"]


async def finish(service, run):
    task = service.tasks.get(run["id"])
    if task:
        try:
            await asyncio.wait_for(asyncio.shield(task), 10)
        except TimeoutError as exc:
            # Preserve the actual blocked coroutine chains before fixture
            # teardown cancels them. Do not hide a stall by extending its limit.
            chains = []
            for pending in asyncio.all_tasks():
                current = pending.get_coro()
                frames = []
                while current is not None:
                    frame = getattr(current, "cr_frame", None) or getattr(current, "gi_frame", None)
                    if frame is not None:
                        frames.append(f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}:{frame.f_code.co_name}")
                    current = getattr(current, "cr_await", None) or getattr(current, "gi_yieldfrom", None)
                chains.append(" -> ".join(frames))
            event_types = [event["type"] for event in service.store.events(run["id"])["events"]]
            exc.add_note("Synthetic browser run events: " + ", ".join(event_types[-12:]))
            exc.add_note("Pending coroutine chains:\n" + "\n".join(sorted(chains)))
            raise
    stored = service.store.one("runs", run["id"])
    assert stored["state"] == "completed", stored.get("error")


async def test_real_service_images_are_tool_evidence_not_user_attachments(fixture):
    service, transport, project, session = fixture
    run = await start(fixture)
    await finish(service, run)
    assert not Engine.instances[0].closed
    assert Engine.instances[0].profile_dir.exists()
    outputs = [item for item in transport.requests[-1]["input"] if item.get("type") == "function_call_output"]
    image = outputs[-1]["output"][1]
    assert base64.b64decode(image["image_url"].partition(",")[2]).startswith(b"\x89PNG")
    assert service.image_inputs.linked("run", run["id"]) == []
    events = service.store.events(run["id"])["events"]
    assert any(e["type"] == "artifact.created" and e["payload"]["kind"] == "browser_screenshot" for e in events)
    assert "data:image" not in encode(events)
    raw = service.store.db.execute("SELECT items FROM direct_context_states").fetchone()
    # The private checkpoint exists and contains identifiers, not pixel blobs.
    assert raw is not None
    assert "data:image" not in str(raw[0])
    owned = service.contexts.data._assets(session["id"])
    assert len(owned) == 1
    snapshot = await service.dispatch("session.web.get", {"project_id": project["id"], "session_id": session["id"]})
    assert snapshot["browser"]["preview_url"].startswith("file:")
    assert snapshot["browser"]["active"] is True
    assert snapshot["browser"]["mode"] == "embedded"
    assert Engine.instances[0].options["headed"] is True
    assert Engine.instances[0].options["embedded"] is True


async def test_settings_preserve_session_and_provider_generation(fixture):
    service, _, project, session = fixture
    ids = {"project_id": project["id"], "session_id": session["id"]}
    snapshot = await service.dispatch("session.web.update", {**ids, "web_search": "cached", "browser_persistent": True})
    assert snapshot["settings"] == {"web_search": "cached", "browser_enabled": True,
                                    "browser_persistent": True, "browser_action_approval": "ask"}
    saved = service.store.one("sessions", session["id"])
    assert saved["model"] == session["model"] and saved["settings"]["openai_account_id"] == "synthetic-account"
    with pytest.raises(KiraError, match="boolean"):
        await service.dispatch("session.web.update", {**ids, "browser_enabled": "yes"})
    with pytest.raises(KiraError, match="web_search"):
        await service.dispatch("session.web.update", {**ids, "web_search": "automatic"})
    run = await start(fixture)
    with pytest.raises(KiraError):
        await service.dispatch("session.web.update", {**ids, "browser_enabled": False})
    await finish(service, run)
    assert Engine.instances[0].profile_dir.is_dir()


async def test_read_only_browser_does_not_dispatch_interactions(fixture):
    service, transport, project, _ = fixture
    transport.outputs = [[call("click", {"ref": "synthetic"})]]
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "read_only"})
    run = await start(fixture)
    await finish(service, run)
    assert not Engine.instances
    output = transport.requests[-1]["input"][-1]
    assert json.loads(output["output"])["error"]["code"] == "PERMISSION_DENIED"


async def test_interaction_waits_for_explicit_approval_and_decline_has_no_effect(fixture):
    service, transport, _, _ = fixture
    transport.outputs = [[call("click", {"ref": "synthetic"})]]
    run = await start(fixture)
    async with asyncio.timeout(5):
        while not service.approvals:
            await asyncio.sleep(0.01)
    assert [item[0] for item in Engine.instances[0].calls if item[0] != "set_artifacts"] == ["prepare_interaction"]
    _, approval_id = next(iter(service.approvals))
    await service.dispatch("run.approve", {"run_id": run["id"], "approval_id": approval_id, "decision": "decline"})
    await finish(service, run)
    assert [item[0] for item in Engine.instances[0].calls if item[0] != "set_artifacts"] == ["prepare_interaction"]


async def test_approved_interaction_executes_only_prepared_target(fixture):
    service, transport, _, _ = fixture
    transport.outputs = [[call("click", {"ref": "synthetic"})]]
    run = await start(fixture)
    async with asyncio.timeout(5):
        while not service.approvals:
            await asyncio.sleep(0.01)
    events = service.store.events(run["id"])["events"]
    approval = next(item["payload"] for item in events if item["type"] == "approval.requested")
    assert approval["target"] == {"label": "Continue", "role": "button"}
    await service.dispatch("run.approve", {"run_id": run["id"], "approval_id": approval["approval_id"], "decision": "accept"})
    await finish(service, run)
    assert Engine.instances[0].calls[-1] == ("perform_interaction", {"token": "synthetic-approval-token"})


async def test_manual_login_pauses_and_waits_for_user_confirmation(fixture, monkeypatch):
    service, transport, project, session = fixture
    monkeypatch.setattr(service.web, "headed", lambda: True)
    await service.dispatch("session.web.update", {"project_id": project["id"], "session_id": session["id"], "browser_persistent": True})
    transport.outputs = [[call("navigate", {"url": "https://example.com/"})], [call("manual_login")]]
    run = await start(fixture)
    try:
        async with asyncio.timeout(5):
            while not service.approvals:
                await asyncio.sleep(0.01)
    except TimeoutError:
        # The full suite can expose scheduling-sensitive hangs. Preserve the
        # synthetic event sequence rather than guessing or increasing timeouts.
        kinds = [event["type"] for event in service.store.events(run["id"])["events"]]
        calls = [action for engine in Engine.instances for action, _ in engine.calls]
        pytest.fail(f"Manual login approval absent: events={kinds[-20:]}; "
                    f"browser_actions={calls}; provider_requests={len(transport.requests)}")
    assert Engine.instances[0].calls[-1][0] == "manual_pause"
    assert run["id"] in service.web.manual
    _, approval_id = next(iter(service.approvals))
    await service.dispatch("run.approve", {"run_id": run["id"], "approval_id": approval_id, "decision": "accept"})
    await finish(service, run)
    assert Engine.instances[0].calls[-1][0] == "resume"
    assert not service.web.manual


async def test_screenshot_assets_never_cross_chats(fixture):
    service, _, project, session = fixture
    run = await start(fixture)
    await finish(service, run)
    other = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
        "model": "fixture", "settings": {"openai_account_id": "synthetic-account"}}))["session"]
    assert service.contexts.data._assets(other["id"]) == {}
    assert service.contexts.data._assets(session["id"])
    assert service.web.snapshot(other)["browser"]["preview_url"] == ""


async def test_successful_run_cleanup_keeps_browser_capacity_owned_until_close(fixture):
    service, _, _, session = fixture
    run = await start(fixture)
    await finish(service, run)
    actor = service.web.actors[session["id"]]
    assert await service.ensure_run_cleanup(run["id"])
    assert actor.id in service.web.sessions and actor.profile.exists()
    await service.web._close_actor(actor)
    assert not service.web.sessions and not actor.profile.exists()


async def test_extra_tool_fields_rejected_before_browser_launch(fixture):
    service, transport, _, _ = fixture
    transport.outputs = [[call("navigate", {"url": "https://example.com/", "profile_dir": "/untrusted"})]]
    run = await start(fixture)
    await finish(service, run)
    assert not Engine.instances
    output = json.loads(transport.requests[-1]["input"][-1]["output"])
    assert output["error"]["code"] == "INVALID_ARGUMENT"


async def test_browser_start_failure_has_a_recorded_outcome_and_visible_error(fixture, monkeypatch):
    service, transport, _, session = fixture
    transport.outputs = [[call("navigate", {"url": "https://example.com/"})]]

    async def failed_start(self):
        raise RuntimeError("Synthetic sandbox failure")

    monkeypatch.setattr(Engine, "start", failed_start)
    run = await start(fixture)
    await finish(service, run)
    events = service.store.events(run["id"])["events"]
    completed = next(e["payload"] for e in events if e["type"] == "tool.completed")
    assert completed["result"]["error"]["code"] == "BROWSER_UNAVAILABLE"
    assert service.web.snapshot(session)["browser"]["error"]
    assert not service.web.sessions


async def test_new_page_removes_previous_screenshot_preview(fixture):
    service, transport, _, session = fixture
    transport.outputs = [[call("navigate", {"url": "https://example.com/"})], [call("screenshot")],
        [{**call("navigate", {"url": "https://example.com/next"}), "call_id": "synthetic-next-page"}]]
    run = await start(fixture)
    await finish(service, run)
    assert service.contexts.data._assets(session["id"])
    assert service.web.snapshot(session)["browser"]["preview_url"] == ""
