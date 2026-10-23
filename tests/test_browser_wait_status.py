"""Synthetic durable browser handoffs; no credentials, websites or model requests."""

import asyncio
import json
from uuid import uuid4

import pytest

from test_embedded_browser_service import command, desktop, opened, owned
from test_service_computer import finish, start
import test_service_computer
from kira.domain.contracts import KiraError

computer_service = test_service_computer.computer_service


@pytest.mark.parametrize("changes", [
    {"phase": []}, {"phase": "complete"}, {"tool_id": ""}, {"tool_id": "x" * 257},
    {"name": "command.run"}, {"reason": "other"}, {"text": "private fixture"},
])
async def test_progress_rejects_unrecognized_metadata_without_persisting(computer_service, changes):
    service, _, _, _ = computer_service
    context = await start(computer_service)
    payload = {"tool_id": "synthetic", "name": "browser.tabs",
               "phase": "waiting_for_browser_control", "reason": "manual_control", **changes}
    before = service.store.events(context.run_id)
    with pytest.raises(KiraError) as error:
        await service.emit(context.run_id, "tool.progress", payload)
    assert error.value.code == "INVALID_EVENT"
    assert service.store.events(context.run_id) == before
    await finish(computer_service, context)


def progress(service, run_id):
    return [event for event in service.store.events(run_id)["events"]
            if event["type"] == "tool.progress"]


async def waiting(service, context, actor):
    async with asyncio.timeout(3):
        while context.run_id not in actor.waiting_runs:
            await asyncio.sleep(0.005)
    assert service.store.one("runs", context.run_id)["state"] == "running"


async def paused_tool(fixture, name):
    service, _, project, session = fixture
    client = desktop(service)
    params, stream = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    context = await start(fixture)
    before = list(actor.engine.calls)
    call_id = str(uuid4())
    task = asyncio.create_task(context.client_tool_call(call_id, name, {}))
    await waiting(service, context, actor)
    assert actor.engine.calls == before
    return context, actor, client, params, stream, call_id, task, before


@pytest.mark.parametrize("name", ["browser.tabs", "computer.observe"])
async def test_manual_wait_is_durable_specific_private_and_resumes_once(computer_service, name):
    service, _, _, session = computer_service
    original_session = service.store.one("sessions", session["id"])
    context, actor, client, params, stream, call_id, task, before = await paused_tool(computer_service, name)
    expected = {"tool_id": call_id, "name": name, "phase": "waiting_for_browser_control", "reason": "manual_control"}
    assert [event["payload"] for event in progress(service, context.run_id)] == [expected]
    # Repeated unchanged manual state and elapsed time do not produce progress
    # spam, automatic continuation or any new engine request.
    await service.web._event(actor, {"event": "state", "data": {"manual": True}})
    await asyncio.sleep(0.03)
    assert len(progress(service, context.run_id)) == 1
    assert actor.engine.calls == before and not task.done()

    marker = "synthetic-private-manual-field"
    await command(service, client, params, stream, 1, "ui_text", {"text": marker})
    assert marker not in "\n".join(service.store.db.iterdump())
    # A fresh subscription receives exactly the same committed wait evidence.
    reopened = desktop(service)
    replay = await service.dispatch("run.subscribe", {"run_id": context.run_id, "after_seq": 0}, reopened)
    assert [event["payload"] for event in replay["events"] if event["type"] == "tool.progress"] == [expected]
    assert service.store.one("sessions", session["id"]) == original_session

    await command(service, client, params, stream, 2, "ui_resume")
    result = await asyncio.wait_for(task, 3)
    assert result["ok"]
    assert [event["payload"] for event in progress(service, context.run_id)] == [
        expected, {**expected, "phase": "resuming"}]
    assert not actor.waiting_runs and not actor.closed
    events = service.store.events(context.run_id)["events"]
    completed = next(event for event in events if event["type"] == "tool.completed")
    assert completed["payload"]["tool_id"] == call_id and completed["payload"]["name"] == name
    assert progress(service, context.run_id)[-1]["seq"] < completed["seq"]
    assert all(set(event["payload"]) == {"tool_id", "name", "phase", "reason"}
               for event in progress(service, context.run_id))
    await finish(computer_service, context)


@pytest.mark.parametrize("name", ["browser.tabs", "computer.observe"])
@pytest.mark.parametrize("cancel_task", [False, True])
async def test_cancel_during_human_wait_never_resumes_or_closes_manual_page(computer_service, name, cancel_task):
    service, _, _, session = computer_service
    context, actor, _, _, _, _, task, before = await paused_tool(computer_service, name)
    if cancel_task:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
    else:
        context.cancel.set()
        result = await asyncio.wait_for(task, 10)
        assert result["error"]["code"] == "CANCELLED"
    assert [event["payload"]["phase"] for event in progress(service, context.run_id)] == ["waiting_for_browser_control"]
    assert not actor.waiting_runs and context.run_id not in actor.uncertain_runs
    assert actor.engine.calls == before
    await finish(computer_service, context, "cancelled")
    assert service.web.actors[session["id"]] is actor
    assert actor.state["manual"] and not actor.closed and actor.profile.is_dir()


@pytest.mark.parametrize("name", ["browser.tabs", "computer.observe"])
async def test_close_during_human_wait_does_not_report_resume_or_reopen(computer_service, name):
    service, _, _, _ = computer_service
    context, actor, client, params, _, _, task, before = await paused_tool(computer_service, name)
    await service.dispatch("session.browser.close", {**params, "request_id": str(uuid4())}, client)
    result = await asyncio.wait_for(task, 10)
    assert result["error"]["code"] == "BROWSER_USER_CLOSED"
    assert [event["payload"]["phase"] for event in progress(service, context.run_id)] == ["waiting_for_browser_control"]
    assert actor.closed and actor.engine.calls == before and not actor.waiting_runs
    assert not service.web.actors
    again = await context.client_tool_call(str(uuid4()), name, {})
    assert again["error"]["code"] == "BROWSER_USER_CLOSED"
    assert not service.web.actors
    await finish(computer_service, context)


@pytest.mark.parametrize("name,action", [("browser.tabs", "tabs"), ("computer.observe", "computer_observe")])
async def test_takeover_winning_dispatch_queue_reports_wait_for_original_call(computer_service, monkeypatch, name, action):
    service, _, project, session = computer_service
    client = desktop(service)
    params, stream = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    await command(service, client, params, stream, 1, "ui_resume")
    original = actor.engine.call
    paused = False

    async def raced(operation, arguments):
        nonlocal paused
        if operation == action and not paused:
            paused = True
            return {"ok": False, "error": {"code": "browser_paused", "message": "Synthetic ownership race"}}
        return await original(operation, arguments)

    monkeypatch.setattr(actor.engine, "call", raced)
    context = await start(computer_service)
    call_id = str(uuid4())
    task = asyncio.create_task(context.client_tool_call(call_id, name, {}))
    await waiting(service, context, actor)
    assert not any(operation == action for operation, _ in actor.engine.calls)
    assert progress(service, context.run_id)[0]["payload"]["name"] == name
    await command(service, client, params, stream, 2, "ui_resume")
    assert (await asyncio.wait_for(task, 3))["ok"]
    assert sum(operation == action for operation, _ in actor.engine.calls) == 1
    assert [event["payload"]["phase"] for event in progress(service, context.run_id)] == ["waiting_for_browser_control", "resuming"]
    await finish(computer_service, context)


async def test_cancel_wins_simultaneous_return_of_control(computer_service):
    service, _, _, _ = computer_service
    context, actor, client, params, stream, _, task, before = await paused_tool(computer_service, "browser.tabs")
    context.cancel.set()
    await command(service, client, params, stream, 1, "ui_resume")
    assert (await asyncio.wait_for(task, 10))["error"]["code"] == "CANCELLED"
    assert [event["payload"]["phase"] for event in progress(service, context.run_id)] == ["waiting_for_browser_control"]
    assert actor.engine.calls == [*before, ("ui_resume", {})]
    await finish(computer_service, context, "cancelled")


@pytest.mark.parametrize("name", ["browser.tabs", "computer.observe"])
async def test_normal_automation_has_no_wait_progress(computer_service, name):
    service, _, _, _ = computer_service
    context = await start(computer_service)
    assert (await context.client_tool_call(str(uuid4()), name, {}))["ok"]
    assert not progress(service, context.run_id)
    assert "waiting_for_browser_control" not in json.dumps(service.store.events(context.run_id))
    await finish(computer_service, context)


@pytest.mark.parametrize("resume", [False, True])
async def test_visual_action_wait_does_not_reuse_pixels_changed_by_manual_control(computer_service, resume):
    service, _, project, session = computer_service
    context = await start(computer_service)
    observation = await context.client_tool_call(str(uuid4()), "computer.observe", {})
    assert observation["ok"]
    actor = service.web.actors[session["id"]]
    client = desktop(service)
    params = {**owned(project, session), "browser_id": actor.id}
    watched = await service.dispatch("session.browser.watch", params, client)
    stream = watched["browser"]["input_stream_id"]
    await command(service, client, params, stream, 1, "ui_takeover")
    before = list(actor.engine.calls)
    task = asyncio.create_task(context.client_tool_call(str(uuid4()), "computer.act", {
        "observation_id": observation["observation_id"], "action": "click", "x": 10, "y": 20}))
    await waiting(service, context, actor)
    assert actor.engine.calls == before and not service.approvals
    assert progress(service, context.run_id)[0]["payload"]["name"] == "computer.act"
    if resume:
        await command(service, client, params, stream, 2, "ui_resume")
    else:
        context.cancel.set()
    result = await asyncio.wait_for(task, 10)
    assert not result["ok"] and result["action_executed"] is False
    assert result["error"]["code"] == ("stale_observation" if resume else "CANCELLED")
    assert actor.engine.effects == 0 and not service.approvals
    assert [event["payload"]["phase"] for event in progress(service, context.run_id)] == (
        ["waiting_for_browser_control", "resuming"] if resume else ["waiting_for_browser_control"])
    await finish(computer_service, context, "completed" if resume else "cancelled")
