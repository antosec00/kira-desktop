"""Synthetic actor/IPC checks; no provider requests, credentials or browsing."""

import asyncio
import json
from uuid import uuid4

import pytest

from kira.domain.contracts import KiraError
from kira.service.server import Client
from kira.service.storage import encode
import test_service_web
from test_service_web import Engine, call, finish, start

fixture = test_service_web.fixture


class Writer:
    def __init__(self):
        self.closed = False
        self.packets = []

    def close(self):
        self.closed = True

    def write(self, packet):
        self.packets.append(packet)

    async def drain(self):
        pass


def desktop(service):
    client = Client(Writer())
    service.clients.add(client)
    return client


def owned(project, session):
    return {"project_id": project["id"], "session_id": session["id"]}


async def opened(service, project, session, client):
    result = await service.dispatch("session.browser.open", {**owned(project, session), "request_id": str(uuid4())}, client)
    params = {**owned(project, session), "browser_id": result["browser"]["browser_id"]}
    watched = await service.dispatch("session.browser.watch", params, client)
    return params, watched["browser"]["input_stream_id"]


async def command(service, client, params, stream, sequence, action, arguments=None, request_id=None):
    return await service.dispatch("session.browser.command", {**params, "request_id": request_id or str(uuid4()),
        "stream_id": stream, "sequence": sequence, "action": action, "arguments": arguments or {}}, client)


async def test_actor_and_profile_continue_across_successful_turns(fixture):
    service, transport, _, session = fixture
    first = await start(fixture)
    await finish(service, first)
    actor = service.web.actors[session["id"]]
    transport.outputs = [[{**call("screenshot"), "call_id": "synthetic-second-screenshot"}]]
    second = (await service.dispatch("run.start", {"session_id": session["id"], "request_id": "synthetic-second",
        "prompt": "Inspect the same page"}))["run"]
    await finish(service, second)
    assert len(Engine.instances) == 1
    assert service.web.actors[session["id"]] is actor
    assert not actor.engine.closed and actor.profile.exists()
    assert actor.engine.artifacts_dir.name == second["id"]
    for run in (first, second):
        assert service.store.db.execute("SELECT state FROM run_cleanup WHERE run_id=?", (run["id"],)).fetchone()[0] == "stopped"
    assert actor.last_run_id == second["id"]


async def test_manual_browser_inputs_never_enter_database_or_run_events(fixture):
    service, _, project, session = fixture
    client = desktop(service)
    params, stream = await opened(service, project, session, client)
    marker = "synthetic-private-input-that-is-never-a-fixture-password"
    await command(service, client, params, stream, 1, "ui_text", {"text": marker})
    stored = "\n".join(service.store.db.iterdump())
    assert marker not in stored
    assert marker not in encode(client.browser_requests)
    actor = service.web.actors[session["id"]]
    assert marker not in str(actor.streams[stream].receipts)
    assert not service.store.all("SELECT * FROM runs")
    assert actor.state["manual"]
    with pytest.raises(KiraError, match="manually controlled"):
        await service.dispatch("service.shutdown", {})
    await service.dispatch("session.browser.close", {**params, "request_id": str(uuid4())}, client)
    assert actor.engine.closed and not actor.profile.exists()
    assert not service.web.shutdown_busy()


async def test_high_water_input_remains_bounded_and_old_inputs_never_replay(fixture):
    service, _, project, session = fixture
    client = desktop(service)
    params, stream = await opened(service, project, session, client)
    original = str(uuid4())
    await command(service, client, params, stream, 1, "ui_text", {"text": "synthetic"}, original)
    actor = service.web.actors[session["id"]]
    for sequence in range(2, 2102):
        # State notifications are consumed by a normal desktop pump. Here discard
        # synthetic notifications to isolate the input high-water contract.
        while not client.queue.empty():
            client.queue.get_nowait()
        await command(service, client, params, stream, sequence, "ui_mouse", {"type": "move", "x": 40, "y": 20})
    assert len(actor.streams[stream].receipts) == 128
    assert actor.streams[stream].high_water == 2101
    before = len(actor.engine.calls)
    with pytest.raises(KiraError) as rejected:
        await command(service, client, params, stream, 1, "ui_text", {"text": "synthetic"}, original)
    assert rejected.value.code == "STALE_INPUT"
    assert len(actor.engine.calls) == before
    last = str(uuid4())
    await command(service, client, params, stream, 2102, "ui_resume", request_id=last)
    before = len(actor.engine.calls)
    await command(service, client, params, stream, 2102, "ui_resume", request_id=last)
    assert len(actor.engine.calls) == before
    with pytest.raises(KiraError) as reused:
        await command(service, client, params, stream, 2102, "ui_takeover", request_id=last)
    assert reused.value.code == "CONFLICT"


async def test_takeover_is_required_and_stream_cannot_cross_clients(fixture):
    service, _, project, session = fixture
    first = await start(fixture)
    await finish(service, first)
    client, other = desktop(service), desktop(service)
    actor = service.web.actors[session["id"]]
    params = {**owned(project, session), "browser_id": actor.id}
    result = await service.dispatch("session.browser.watch", params, client)
    stream = result["browser"]["input_stream_id"]
    with pytest.raises(KiraError) as rejected:
        await command(service, client, params, stream, 1, "ui_text", {"text": "synthetic"})
    assert rejected.value.code == "BROWSER_AGENT_CONTROL"
    with pytest.raises(KiraError) as foreign:
        await command(service, other, params, stream, 2, "ui_takeover")
    assert foreign.value.code == "STALE_INPUT"
    await command(service, client, params, stream, 2, "ui_takeover")
    assert actor.state["manual"]
    await service.web.disconnect(client)
    assert actor.streams[stream].client is None
    assert actor.streams[stream].high_water == 2
    # A browser under manual control cannot continue without a desktop viewer:
    # the disconnect closes it, so later input finds no current browser.
    assert actor.closed
    with pytest.raises(KiraError) as stale:
        await command(service, client, params, stream, 3, "ui_resume")
    assert stale.value.code == "STALE_BROWSER"
    assert ("viewer", {"enabled": False}) in actor.engine.calls


async def test_cancelled_input_has_unknown_receipt_and_is_not_replayed(fixture, monkeypatch):
    service, _, project, session = fixture
    client = desktop(service)
    params, stream = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    entered = asyncio.Event()
    original = actor.engine.call

    async def interrupted(action, arguments):
        if action == "ui_text":
            entered.set()
            await asyncio.Event().wait()
        return await original(action, arguments)

    monkeypatch.setattr(actor.engine, "call", interrupted)
    request_id = str(uuid4())
    task = asyncio.create_task(command(service, client, params, stream, 1, "ui_text", {"text": "synthetic"}, request_id))
    await asyncio.wait_for(entered.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 30)
    with pytest.raises(KiraError) as rejected:
        await command(service, client, params, stream, 1, "ui_text", {"text": "synthetic"}, request_id)
    assert rejected.value.code == "UNKNOWN_OUTCOME"


async def test_frames_are_latest_only_and_never_enter_durable_queue(fixture):
    service, _, project, session = fixture
    watched, unwatched = desktop(service), desktop(service)
    params, _ = await opened(service, project, session, watched)
    actor = service.web.actors[session["id"]]
    before = watched.queue.qsize()
    for sequence in range(500):
        await actor.engine.options["on_event"]({"event": "frame", "data": {
            "seq": sequence, "tab_id": "t1", "data": "synthetic-frame", "mime": "image/jpeg", "width": 800, "height": 600}})
    assert watched.queue.qsize() == before
    assert len(watched.browser_frames) == 1
    assert not watched.closed and not unwatched.browser_frames
    message = watched.browser_frames[actor.id]
    assert message["browser_event"]["payload"]["seq"] == 499
    assert "synthetic-frame" not in "\n".join(service.store.db.iterdump())
    await service.dispatch("session.browser.unwatch", params, watched)
    assert not watched.browser_frames
    assert actor.engine.calls[-1] == ("viewer", {"enabled": False})


async def test_model_change_closes_browser_and_rejects_old_callbacks(fixture):
    service, _, project, session = fixture
    run = await start(fixture)
    await finish(service, run)
    actor = service.web.actors[session["id"]]
    client = desktop(service)
    params = {**owned(project, session), "browser_id": actor.id}
    await service.dispatch("session.browser.watch", params, client)
    await service.dispatch("session.configure", {**owned(project, session), "model": "synthetic-other"})
    assert actor.closed and not service.web.actors
    before = client.queue.qsize()
    await actor.engine.options["on_event"]({"event": "state", "data": {"title": "stale"}})
    assert client.queue.qsize() == before
    with pytest.raises(KiraError) as rejected:
        await service.dispatch("session.browser.watch", params, client)
    assert rejected.value.code == "STALE_BROWSER"


async def test_ipc_watch_delivers_frames_only_to_subscriber_and_disconnect_stops_viewer(fixture):
    service, _, project, session = fixture
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    reader2, writer2 = await asyncio.open_unix_connection(service.paths.socket)

    async def rpc(method, params):
        identifier = str(uuid4())
        writer.write((encode({"version": 1, "id": identifier, "method": method, "params": params}) + "\n").encode())
        await writer.drain()
        while True:
            message = json.loads(await asyncio.wait_for(reader.readline(), 2))
            if message.get("id") == identifier:
                assert "error" not in message, message
                return message["result"]

    try:
        result = await rpc("session.browser.open", {**owned(project, session), "request_id": str(uuid4())})
        actor = service.web.actors[session["id"]]
        params = {**owned(project, session), "browser_id": result["browser"]["browser_id"]}
        watched = await rpc("session.browser.watch", params)
        assert watched["browser"]["input_stream_id"]
        await actor.engine.options["on_event"]({"event": "frame", "data": {
            "tab_id": "t1", "data": "synthetic-pixels", "width": 800, "height": 600, "mime": "image/jpeg"}})
        frame = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert frame["browser_event"]["type"] == "frame"
        assert frame["browser_event"]["browser_id"] == actor.id
        # Other connected desktops can discover this chat's state, never pixels.
        while True:
            try:
                message = json.loads(await asyncio.wait_for(reader2.readline(), 0.05))
                assert message.get("browser_event", {}).get("type") != "frame"
            except TimeoutError:
                break
        writer.close()
        await writer.wait_closed()
        async with asyncio.timeout(2):
            while actor.viewers:
                await asyncio.sleep(0.01)
        assert actor.engine.calls[-1] == ("viewer", {"enabled": False})
    finally:
        writer.close()
        writer2.close()
        await writer2.wait_closed()


async def test_unknown_worker_recovery_remains_blocked_without_process_authority(fixture, monkeypatch):
    service, _, project, session = fixture
    client = desktop(service)
    params, _ = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]

    async def failed_close():
        raise RuntimeError("Synthetic process close uncertainty")

    monkeypatch.setattr(actor.engine, "close", failed_close)
    with pytest.raises(KiraError) as rejected:
        await service.dispatch("session.browser.close", {**params, "request_id": str(uuid4())}, client)
    assert rejected.value.code == "BROWSER_CLEANUP"
    assert not service.web.actors
    assert actor.profile.exists()
    await service.web.recover()
    assert service.store.db.execute("SELECT state FROM browser_sessions WHERE id=?", (actor.id,)).fetchone()[0] == "unknown"
    with pytest.raises(KiraError) as blocked:
        await service.dispatch("session.browser.open", {**owned(project, session), "request_id": str(uuid4())}, client)
    assert blocked.value.code == "BROWSER_CLEANUP"


async def test_browser_capacity_never_evicts_another_chat(fixture):
    service, _, project, session = fixture
    client = desktop(service)
    await opened(service, project, session, client)
    sessions = [session]
    for _ in range(2):
        sessions.append((await service.dispatch("session.create", {"project_id": project["id"],
            "backend": "openai_chatgpt", "model": "fixture", "settings": {"openai_account_id": "synthetic-account"}}))["session"])
    await opened(service, project, sessions[1], client)
    with pytest.raises(KiraError) as full:
        await opened(service, project, sessions[2], client)
    assert full.value.code == "BROWSER_BUSY"
    assert len(Engine.instances) == 2
    assert all(not engine.closed for engine in Engine.instances)
    assert set(service.web.actors) == {item["id"] for item in sessions[:2]}


async def test_replayed_close_does_not_reopen_or_repeat_effect(fixture):
    service, _, project, session = fixture
    client = desktop(service)
    params, _ = await opened(service, project, session, client)
    request = {**params, "request_id": str(uuid4())}
    result = await service.dispatch("session.browser.close", request, client)
    assert not result["browser"]["active"]
    assert await service.dispatch("session.browser.close", request, client) == result
    assert len(Engine.instances) == 1


async def test_client_pump_prioritizes_durable_events_over_latest_pixels():
    client = Client(Writer())
    client.browser_ids.add("synthetic-browser")
    for sequence in range(500):
        client.send_browser_frame("synthetic-browser", {"version": 1, "browser_event": {"seq": sequence}})
    client.send({"version": 1, "event": {"type": "run.state", "payload": {"state": "completed"}}})
    pump = asyncio.create_task(client.pump())
    async with asyncio.timeout(2):
        while len(client.writer.packets) < 2:
            await asyncio.sleep(0)
    pump.cancel()
    await asyncio.gather(pump, return_exceptions=True)
    assert json.loads(client.writer.packets[0])["event"]["type"] == "run.state"
    assert json.loads(client.writer.packets[1])["browser_event"]["seq"] == 499
    assert not client.closed


async def test_manual_copy_delivers_selection_once_only_to_requesting_desktop(fixture, monkeypatch):
    service, _, project, session = fixture
    client, other = desktop(service), desktop(service)
    params, stream = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    original = actor.engine.call
    copied = "synthetic selection for the clipboard only"

    async def copy(action, arguments):
        if action == "ui_copy":
            return {"ok": True, "text": copied}
        return await original(action, arguments)

    monkeypatch.setattr(actor.engine, "call", copy)
    request_id = str(uuid4())
    result = await command(service, client, params, stream, 1, "ui_copy", request_id=request_id)
    assert result["clipboard_text"] == copied
    replay = await command(service, client, params, stream, 1, "ui_copy", request_id=request_id)
    assert "clipboard_text" not in replay
    assert copied not in "\n".join(service.store.db.iterdump())
    assert copied not in str(actor.streams[stream].receipts)
    for queued in (client, other):
        while not queued.queue.empty():
            assert copied not in encode(queued.queue.get_nowait())


async def test_worker_failure_closes_actor_even_when_model_handles_error_and_completes(fixture, monkeypatch):
    service, transport, _, session = fixture
    transport.outputs = [[call("navigate", {"url": "https://example.com/"})]]
    original = Engine.call

    async def failed(self, action, arguments):
        if action == "navigate":
            return {"ok": False, "error": {"code": "browser_unavailable", "message": "Synthetic worker failure"}}
        return await original(self, action, arguments)

    monkeypatch.setattr(Engine, "call", failed)
    run = await start(fixture)
    await finish(service, run)
    assert not service.web.actors
    assert Engine.instances[0].closed
    assert service.web.snapshot(session)["browser"]["active"] is False
    assert service.web.snapshot(session)["browser"]["error"] == "Synthetic worker failure"


async def test_manual_takeover_suspends_tool_and_resumes_original_action(fixture):
    service, transport, project, session = fixture
    client = desktop(service)
    params, stream = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    before = len(actor.engine.calls)
    transport.outputs = [[call("navigate", {"url": "https://example.com/after-handoff"})]]
    run = await start(fixture)
    async with asyncio.timeout(3):
        while run["id"] not in actor.waiting_runs:
            await asyncio.sleep(0.01)
    assert len(actor.engine.calls) == before
    await command(service, client, params, stream, 1, "ui_text", {"text": "synthetic manual input"})
    assert not any(action == "navigate" for action, _ in actor.engine.calls)
    assert service.store.one("runs", run["id"])["state"] == "running"
    await command(service, client, params, stream, 2, "ui_resume")
    await finish(service, run)
    assert ("navigate", {"url": "https://example.com/after-handoff"}) in actor.engine.calls
    assert not actor.waiting_runs and actor.automation_ready.is_set()
    assert not actor.closed


async def test_cancelling_turn_waiting_for_manual_control_preserves_user_browser(fixture):
    service, transport, project, session = fixture
    client = desktop(service)
    params, stream = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    before = len(actor.engine.calls)
    transport.outputs = [[call("navigate", {"url": "https://example.com/after-handoff"})]]
    run = await start(fixture)
    async with asyncio.timeout(3):
        while run["id"] not in actor.waiting_runs:
            await asyncio.sleep(0.01)
    await service.dispatch("run.cancel", {"run_id": run["id"]})
    task = service.tasks.get(run["id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 5)
    assert service.store.one("runs", run["id"])["state"] == "cancelled"
    assert len(actor.engine.calls) == before
    assert not actor.engine.closed and not actor.closed and actor.profile.exists()
    assert service.web.actors[session["id"]] is actor
    assert actor.state["manual"] and not actor.uncertain_runs
    assert not actor.waiting_runs
    await command(service, client, params, stream, 1, "ui_text", {"text": "manual work continues"})
    assert actor.engine.calls[-1][0] == "ui_text"


async def test_closing_manual_browser_wakes_tool_without_reopening_in_same_turn(fixture):
    service, transport, project, session = fixture
    client = desktop(service)
    params, _ = await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    transport.outputs = [[call("navigate", {"url": "https://example.com/first"})],
        [{**call("navigate", {"url": "https://example.com/second"}), "call_id": "synthetic-retry-after-close"}]]
    run = await start(fixture)
    async with asyncio.timeout(3):
        while run["id"] not in actor.waiting_runs:
            await asyncio.sleep(0.01)
    await service.dispatch("session.browser.close", {**params, "request_id": str(uuid4())}, client)
    await finish(service, run)
    assert len(Engine.instances) == 1 and actor.closed
    assert not any(action == "navigate" for action, _ in actor.engine.calls)
    results = [event["payload"]["result"] for event in service.store.events(run["id"])["events"]
               if event["type"] == "tool.completed" and event["payload"]["name"] == "browser.navigate"]
    assert len(results) == 2
    assert all(result["error"]["code"] == "BROWSER_USER_CLOSED" for result in results)
    assert not service.web.closed_runs


async def test_closing_during_manual_login_rejects_approval_and_finishes_turn(fixture):
    service, transport, project, session = fixture
    transport.outputs = [[call("manual_login")]]
    client = desktop(service)
    run = await start(fixture)
    async with asyncio.timeout(3):
        while not service.approvals:
            await asyncio.sleep(0.01)
    actor = service.web.actors[session["id"]]
    params = {**owned(project, session), "browser_id": actor.id, "request_id": str(uuid4())}
    await service.dispatch("session.browser.close", params, client)
    await finish(service, run)
    assert actor.closed and not service.approvals
    events = service.store.events(run["id"])["events"]
    assert any(event["type"] == "backend.notice" and event["payload"].get("decision") == "decline" for event in events)
    result = next(event["payload"]["result"] for event in events if event["type"] == "tool.completed")
    assert result["error"]["code"] == "BROWSER_USER_CLOSED"
    assert not any(action == "resume" for action, _ in actor.engine.calls)


async def test_cancelling_uncertain_agent_exchange_still_cleans_manual_actor(fixture, monkeypatch):
    service, transport, project, session = fixture
    original = Engine.call
    entered = asyncio.Event()

    async def blocked(self, action, arguments):
        if action == "navigate":
            entered.set()
            await asyncio.Event().wait()
        return await original(self, action, arguments)

    monkeypatch.setattr(Engine, "call", blocked)
    transport.outputs = [[call("navigate", {"url": "https://example.com/"})]]
    run = await start(fixture)
    await asyncio.wait_for(entered.wait(), 3)
    actor = service.web.actors[session["id"]]
    # A real worker serializes this transition; force the observed state here to
    # test that manual mode never suppresses uncertain exchange cleanup.
    await service.web._event(actor, {"event": "state", "data": {"manual": True}})
    service.cancellations[run["id"]].set()
    task = service.tasks[run["id"]]
    task.cancel()
    await asyncio.wait_for(asyncio.shield(task), 5)
    assert run["id"] in actor.uncertain_runs
    assert actor.closed and actor.engine.closed and not actor.profile.exists()
    assert not service.web.actors
