"""Computer tool service evidence with synthetic pages, runs and image artifacts."""

import asyncio
import base64
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode
from kira.tools import browser
from test_embedded_browser_service import command, desktop, owned
from test_service_web import Engine
import test_service_web

web_fixture = test_service_web.fixture


class ComputerEngine(Engine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observations = 0
        self.current_observation = None
        self.prepared = {}
        self.effects = 0
        self.capture_failure = False

    async def capture(self):
        self.observations += 1
        self.current_observation = f"synthetic-observation-{self.observations}"
        screenshot = await super().call("screenshot", {})
        return {**screenshot, "observation_id": self.current_observation, "tab_id": "synthetic-tab",
                "viewport": {"width": 40, "height": 30}}

    async def call(self, action, arguments):
        if action in {"ui_takeover", "ui_navigate", "navigate"}:
            self.current_observation = None
            self.prepared.clear()
        if action == "computer_observe":
            self.calls.append((action, arguments))
            return await self.capture()
        if action == "computer_prepare":
            self.calls.append((action, arguments))
            if arguments["observation_id"] != self.current_observation:
                return {"ok": False, "action_executed": False,
                        "error": {"code": "stale_observation", "message": "Synthetic viewport changed"}}
            token = f"synthetic-prepared-{self.observations}-{len(self.calls)}"
            self.prepared[token] = dict(arguments)
            return {"ok": True, "token": token, "url": "https://example.com/",
                    "target": {"role": "button", "label": "Synthetic target", "x": 10, "y": 20}}
        if action == "computer_perform":
            self.calls.append((action, arguments))
            prepared = self.prepared.pop(arguments["token"], None)
            if prepared is None or prepared["observation_id"] != self.current_observation:
                return {"ok": False, "action_executed": False,
                        "error": {"code": "stale_observation", "message": "Synthetic target changed after approval"}}
            self.effects += 1
            if self.capture_failure:
                return {"ok": False, "action_executed": True,
                        "error": {"code": "capture_failed", "message": "Synthetic post-action capture failed"}}
            return {**await self.capture(), "action_executed": True}
        return await super().call(action, arguments)


class HoldingProvider:
    def __init__(self):
        self.started = asyncio.Queue()
        self.releases = {}
        self.outcomes = {}

    async def run(self, context):
        self.releases[context.run_id] = asyncio.Event()
        await self.started.put(context)
        await self.releases[context.run_id].wait()
        return {"state": self.outcomes.get(context.run_id, "completed")}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def computer_service(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "BrowserSession", ComputerEngine)
    monkeypatch.setattr(browser, "engine_available", lambda _: True)
    Engine.instances = []
    with tempfile.TemporaryDirectory(prefix="kira-computer-test-") as directory:
        paths = Paths(*(Path(directory) / item for item in ("config", "data", "state", "runtime")))
        provider = HoldingProvider()
        service = Service(paths, backend_factory=lambda *_: provider)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic visual project",
            "root": str(tmp_path), "permission": "project_write"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
            "model": "synthetic-selected-model", "settings": {"reasoning_effort": "high", "openai_account_id": "synthetic-account"}}))["session"]
        try:
            yield service, provider, project, session
        finally:
            await service.close()


async def start(fixture, mode="execute", session=None):
    service, provider, _, original_session = fixture
    session = session or original_session
    await service.dispatch("run.start", {"session_id": session["id"], "request_id": str(uuid4()),
        "prompt": "Inspect the synthetic visual fixture", "mode": mode})
    return await asyncio.wait_for(provider.started.get(), 30)


async def finish(fixture, context, state="completed"):
    service, provider, _, _ = fixture
    provider.outcomes[context.run_id] = state
    provider.releases[context.run_id].set()
    await asyncio.wait_for(service.tasks[context.run_id], 5)
    assert service.store.one("runs", context.run_id)["state"] == state


async def invoke(context, name, arguments=None, identity=None):
    return await context.client_tool_call(identity or str(uuid4()), f"computer.{name}", arguments or {})


async def approve_next(service, context, decision="accept"):
    async with asyncio.timeout(3):
        while not service.approvals:
            await asyncio.sleep(0.01)
    (run_id, approval_id), = service.approvals
    assert run_id == context.run_id
    await service.dispatch("run.approve", {"run_id": run_id, "approval_id": approval_id, "decision": decision})


async def test_observations_are_real_owned_images_and_preserve_selected_provider(computer_service):
    service, _, project, session = computer_service
    context = await start(computer_service)
    assert {item["name"] for item in context.client_tools} >= {"computer", "browser"}
    result = await invoke(context, "observe")
    assert result["ok"] and result["observation_id"]
    image = await context.tool_image(result["browser_image_id"])
    assert base64.b64decode(image["image_url"].partition(",")[2]).startswith(b"\x89PNG")
    assert service.image_inputs.linked("run", context.run_id) == []
    events = service.store.events(context.run_id)["events"]
    assert any(event["type"] == "artifact.created" and event["payload"]["kind"] == "browser_screenshot" for event in events)
    assert "data:image" not in encode(events)
    saved = service.store.one("sessions", session["id"])
    assert saved["backend"] == "openai_chatgpt" and saved["model"] == session["model"]
    assert saved["settings"]["reasoning_effort"] == "high"
    await finish(computer_service, context)
    assert result["browser_image_id"] in service.contexts.data._assets(session["id"])
    other = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
        "model": session["model"], "settings": session["settings"]}))["session"]
    assert not service.contexts.data._assets(other["id"])
    next_context = await start(computer_service)
    with pytest.raises(KiraError) as previous_image:
        await next_context.tool_image(result["browser_image_id"])
    assert previous_image.value.code == "INVALID_ATTACHMENT"
    foreign_observation = await invoke(next_context, "act", {"observation_id": result["observation_id"], "action": "move", "x": 1, "y": 2})
    assert foreign_observation["error"]["code"] == "INVALID_OBSERVATION"
    assert Engine.instances[0].effects == 0
    await finish(computer_service, next_context)


@pytest.mark.parametrize("mode,permission", [("review", "project_write"), ("plan", "project_write"), ("execute", "read_only")])
async def test_read_only_can_observe_move_scroll_wait_but_not_mutate(computer_service, mode, permission):
    service, _, project, _ = computer_service
    if permission == "read_only":
        await service.dispatch("project.update", {"project_id": project["id"], "permission": permission})
    context = await start(computer_service, mode)
    current = await invoke(context, "observe")
    for action, arguments in (("move", {"x": 1, "y": 2}), ("scroll", {"direction": "down"}), ("wait", {"duration_ms": 0})):
        current = await invoke(context, "act", {"observation_id": current["observation_id"], "action": action, **arguments})
        assert current["ok"] and current["action_executed"] is True
    for action, arguments in (("click", {"x": 1, "y": 2}), ("double_click", {"x": 1, "y": 2}),
                             ("type", {"text": "Synthetic text"}), ("key", {"key": "Enter"}),
                             ("drag", {"path": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]})):
        rejected = await invoke(context, "act", {"observation_id": current["observation_id"], "action": action, **arguments})
        assert rejected["error"]["code"] == "PERMISSION_DENIED" and rejected["action_executed"] is False
    assert not service.approvals
    assert Engine.instances[0].effects == 3
    await finish(computer_service, context)


async def test_visual_mutation_waits_for_exact_target_approval_and_deduplicates(computer_service):
    service, _, _, _ = computer_service
    context = await start(computer_service)
    observed = await invoke(context, "observe")
    arguments = {"observation_id": observed["observation_id"], "action": "click", "x": 10, "y": 20}
    identity = str(uuid4())
    pending = asyncio.create_task(invoke(context, "act", arguments, identity))
    async with asyncio.timeout(3):
        while not service.approvals:
            await asyncio.sleep(0.01)
    assert Engine.instances[0].effects == 0
    approval = next(event["payload"] for event in service.store.events(context.run_id)["events"] if event["type"] == "approval.requested")
    assert approval["kind"] == "browser_action" and approval["tool"] == "computer.act"
    assert approval["target"] == {"role": "button", "label": "Synthetic target", "x": 10, "y": 20}
    await approve_next(service, context)
    result = await pending
    assert result["ok"] and result["action_executed"] is True
    assert result["observation_id"] != observed["observation_id"]
    assert await invoke(context, "act", arguments, identity) == result
    assert Engine.instances[0].effects == 1
    conflicting = await invoke(context, "act", {**arguments, "x": 11}, identity)
    assert conflicting["error"]["code"] == "CONFLICT"
    assert Engine.instances[0].effects == 1
    await finish(computer_service, context)


async def test_declined_or_stale_approval_never_executes(computer_service):
    service, _, _, _ = computer_service
    context = await start(computer_service)
    observed = await invoke(context, "observe")
    arguments = {"observation_id": observed["observation_id"], "action": "click", "x": 10, "y": 20}
    declined = asyncio.create_task(invoke(context, "act", arguments))
    await approve_next(service, context, "decline")
    assert (await declined)["action_executed"] is False
    stale = asyncio.create_task(invoke(context, "act", arguments))
    async with asyncio.timeout(3):
        while not service.approvals:
            await asyncio.sleep(0.01)
    Engine.instances[0].current_observation = None
    await approve_next(service, context)
    result = await stale
    assert result["error"]["code"] == "stale_observation" and result["action_executed"] is False
    assert Engine.instances[0].effects == 0
    await finish(computer_service, context)


async def test_manual_handoff_waits_and_does_not_reuse_changed_visual_evidence(computer_service):
    service, _, project, session = computer_service
    context = await start(computer_service)
    observed = await invoke(context, "observe")
    actor = service.web.actors[session["id"]]
    client = desktop(service)
    params = {**owned(project, session), "browser_id": actor.id}
    watched = await service.dispatch("session.browser.watch", params, client)
    stream = watched["browser"]["input_stream_id"]
    await command(service, client, params, stream, 1, "ui_takeover")
    before = len(actor.engine.calls)
    pending = asyncio.create_task(invoke(context, "act", {"observation_id": observed["observation_id"], "action": "move", "x": 1, "y": 2}))
    async with asyncio.timeout(3):
        while context.run_id not in actor.waiting_runs:
            await asyncio.sleep(0.01)
    assert len(actor.engine.calls) == before
    await command(service, client, params, stream, 2, "ui_text", {"text": "Synthetic manual text"})
    await command(service, client, params, stream, 3, "ui_resume")
    result = await pending
    assert result["error"]["code"] == "stale_observation" and actor.engine.effects == 0
    assert not actor.closed
    await finish(computer_service, context)


@pytest.mark.parametrize("failure", ["capture", "import"])
async def test_executed_action_remains_confirmed_when_new_image_fails(computer_service, monkeypatch, failure):
    service, _, _, _ = computer_service
    context = await start(computer_service)
    observed = await invoke(context, "observe")
    if failure == "capture":
        Engine.instances[0].capture_failure = True
    else:
        def failed_import(_path):
            raise KiraError("INVALID_ATTACHMENT", "Synthetic image import rejected")
        monkeypatch.setattr(service.image_inputs.images, "import_file", failed_import)
    identity = str(uuid4())
    arguments = {"observation_id": observed["observation_id"], "action": "scroll", "direction": "down"}
    result = await invoke(context, "act", arguments, identity)
    assert not result["ok"] and result["action_executed"] is True
    assert "browser_image_id" not in result
    assert await invoke(context, "act", arguments, identity) == result
    assert Engine.instances[0].effects == 1
    completed = [event["payload"]["result"] for event in service.store.events(context.run_id)["events"] if event["type"] == "tool.completed"]
    assert completed[-1]["action_executed"] is True
    await finish(computer_service, context)


async def test_cancelled_unknown_action_is_not_replayed_and_worker_is_cleaned(computer_service, monkeypatch):
    service, _, _, session = computer_service
    context = await start(computer_service)
    observed = await invoke(context, "observe")
    actor = service.web.actors[session["id"]]
    original = actor.engine.call
    entered = asyncio.Event()

    async def interrupted(action, arguments):
        if action == "computer_perform":
            actor.engine.effects += 1
            entered.set()
            await asyncio.Event().wait()
        return await original(action, arguments)

    monkeypatch.setattr(actor.engine, "call", interrupted)
    arguments = {"observation_id": observed["observation_id"], "action": "scroll", "direction": "down"}
    identity = str(uuid4())
    pending = asyncio.create_task(invoke(context, "act", arguments, identity))
    await asyncio.wait_for(entered.wait(), 3)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    replay = await invoke(context, "act", arguments, identity)
    assert replay["action_outcome"] == "unknown" and replay["retry_safe"] is False
    assert "action_executed" not in replay
    assert actor.engine.effects == 1
    await finish(computer_service, context, "cancelled")
    assert actor.closed


async def test_visual_budget_is_reserved_before_effects_and_browser_budget_is_independent(computer_service):
    service, _, _, _ = computer_service
    context = await start(computer_service)
    for _ in range(32):
        current = await invoke(context, "observe")
        assert current["ok"]
    before = list(Engine.instances[0].calls)
    exhausted = await invoke(context, "act", {"observation_id": current["observation_id"], "action": "scroll", "direction": "down"})
    assert exhausted["error"]["code"] == "OUTPUT_LIMIT" and exhausted["action_executed"] is False
    assert Engine.instances[0].calls == before and Engine.instances[0].effects == 0
    for _ in range(16):
        shot = await context.client_tool_call(str(uuid4()), "browser.screenshot", {})
        assert shot["ok"]
    rejected = await context.client_tool_call(str(uuid4()), "browser.screenshot", {})
    assert rejected["error"]["code"] == "OUTPUT_LIMIT"
    assert len(service.web.images[context.run_id]) == 48
    await finish(computer_service, context)


async def test_invalid_observation_and_extra_fields_fail_before_engine_launch(computer_service):
    service, _, _, _ = computer_service
    context = await start(computer_service)
    for arguments in ({"observation_id": "unowned", "action": "move", "x": 1, "y": 2},
                      {"observation_id": "unowned", "action": "move", "x": 1, "y": 2, "profile": "foreign"}):
        result = await invoke(context, "act", arguments)
        assert result["error"]["code"] in {"INVALID_OBSERVATION", "INVALID_ARGUMENT"}
    assert not Engine.instances and context.run_id not in service.web.computer_cycles
    await finish(computer_service, context)


async def test_provider_receives_computer_pixels_from_service_artifact_callback(web_fixture, monkeypatch):
    monkeypatch.setattr(browser, "BrowserSession", ComputerEngine)
    service, transport, _, session = web_fixture
    transport.outputs = [[{"type": "function_call", "status": "completed", "namespace": "computer", "name": "observe",
        "call_id": "synthetic-computer-observe", "arguments": "{}"}]]
    run = await test_service_web.start(web_fixture)
    await test_service_web.finish(service, run)
    outputs = [item for item in transport.requests[-1]["input"] if item.get("type") == "function_call_output"]
    image = outputs[-1]["output"][1]
    assert image["type"] == "input_image" and image["detail"] == "original"
    assert base64.b64decode(image["image_url"].partition(",")[2]).startswith(b"\x89PNG")
    assert len(service.contexts.data._assets(session["id"])) == 1
    assert "data:image" not in encode(service.store.events(run["id"])["events"])
    checkpoint = service.store.db.execute("SELECT items FROM direct_context_states WHERE session_id=?", (session["id"],)).fetchone()
    assert checkpoint is not None and "data:image" not in checkpoint[0]
    following = (await service.dispatch("run.start", {"session_id": session["id"], "request_id": str(uuid4()),
        "prompt": "Continue from the synthetic visual observation"}))["run"]
    await test_service_web.finish(service, following)
    restored = [part for item in transport.requests[-1]["input"] if item.get("type") == "function_call_output"
                and isinstance(item.get("output"), list) for part in item["output"] if part.get("type") == "input_image"]
    assert len(restored) == 1 and restored[0] == image


async def test_cancel_during_manual_wait_preserves_user_browser_and_records_no_effect(computer_service):
    service, _, project, session = computer_service
    context = await start(computer_service)
    observed = await invoke(context, "observe")
    actor = service.web.actors[session["id"]]
    client = desktop(service)
    params = {**owned(project, session), "browser_id": actor.id}
    watched = await service.dispatch("session.browser.watch", params, client)
    stream = watched["browser"]["input_stream_id"]
    await command(service, client, params, stream, 1, "ui_takeover")
    before = list(actor.engine.calls)
    arguments = {"observation_id": observed["observation_id"], "action": "scroll", "direction": "down"}
    identity = str(uuid4())
    pending = asyncio.create_task(invoke(context, "act", arguments, identity))
    async with asyncio.timeout(3):
        while context.run_id not in actor.waiting_runs:
            await asyncio.sleep(0.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    replay = await invoke(context, "act", arguments, identity)
    assert replay["action_executed"] is False and "action_outcome" not in replay
    assert actor.engine.calls == before and service.web.computer_cycles[context.run_id] == 1
    await finish(computer_service, context, "cancelled")
    assert not actor.closed and actor.profile.exists() and actor.state["manual"]


async def test_observation_rejects_coordinate_viewport_that_does_not_match_pixels(computer_service, monkeypatch):
    service, _, _, _ = computer_service
    original = ComputerEngine.capture

    async def mismatched(self):
        result = await original(self)
        result["viewport"]["width"] += 1
        return result

    monkeypatch.setattr(ComputerEngine, "capture", mismatched)
    context = await start(computer_service)
    result = await invoke(context, "observe")
    assert not result["ok"] and result["error"]["code"] == "INVALID_OBSERVATION"
    assert "browser_image_id" not in result
    assert not service.web.computer_observations.get(context.run_id)
    assert not any(event["type"] == "artifact.created" for event in service.store.events(context.run_id)["events"])
    await finish(computer_service, context)
