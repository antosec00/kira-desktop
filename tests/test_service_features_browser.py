"""Chat browser lifecycle against an isolated service; the engine is synthetic, nothing is browsed."""

import asyncio
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kira.domain.contracts import KiraError
from kira.service.storage import encode
from kira.tools.browser import BrowserStartError
import test_service_web
from test_embedded_browser_service import desktop, opened
from test_service_web import Engine, call, finish, start

fixture = test_service_web.fixture


def scope(project, session):
    return {"project_id": project["id"], "session_id": session["id"]}


async def browse(service, transport, session, request_id):
    """One model turn that opens or reuses the chat browser."""
    transport.outputs = [[{**call("navigate", {"url": "https://example.com/"}), "call_id": "navigate-" + request_id}]]
    run = (await service.dispatch("run.start", {"session_id": session["id"], "request_id": request_id,
                                                "prompt": "Read the synthetic page"}))["run"]
    await finish(service, run)
    return service.web.actors[session["id"]]


async def select_identity(service, project, session, request_id=None):
    soul = (await service.dispatch("soul.create", {"request_id": str(uuid4()), "name": "Synthetic identity",
                                                   "core": "Prefer concrete evidence."}))["soul"]
    params = {"request_id": request_id or str(uuid4()), **scope(project, session), "soul_id": soul["id"]}
    return params, await service.dispatch("soul.bind", params)


def browser_state(service, actor):
    return service.store.db.execute("SELECT state FROM browser_sessions WHERE id=?", (actor.id,)).fetchone()[0]


async def test_identity_change_closes_the_chat_browser_with_its_provider_generation(fixture):
    service, transport, project, session = fixture
    actor = await browse(service, transport, session, "before-identity")
    generation = service.provider_routing.current_generation(session["id"])
    params, bound = await select_identity(service, project, session)
    assert bound["identity_changed"] is True
    assert service.provider_routing.current_generation(session["id"]) == generation + 1
    assert actor.engine.closed and session["id"] not in service.web.actors
    assert browser_state(service, actor) == "stopped"
    assert not service.web.shutdown_busy()
    # A repeated identity request returns its receipt and leaves a newer browser alone.
    newer = await browse(service, transport, session, "after-identity")
    await service.dispatch("soul.bind", params)
    assert service.web.actors[session["id"]] is newer and not newer.engine.closed


async def test_identity_change_waits_while_the_user_controls_the_browser(fixture):
    service, _, project, session = fixture
    client = desktop(service)
    await opened(service, project, session, client)
    actor = service.web.actors[session["id"]]
    generation = service.provider_routing.current_generation(session["id"])
    with pytest.raises(KiraError) as busy:
        await select_identity(service, project, session)
    assert busy.value.code == "BUSY"
    assert service.provider_routing.current_generation(session["id"]) == generation
    snapshot = await service.dispatch("soul.snapshot", scope(project, session))
    assert snapshot["snapshot"]["selected"] is False
    assert service.web.actors[session["id"]] is actor and not actor.engine.closed


async def test_retained_profile_is_kept_per_chat_provider_and_account(fixture):
    service, transport, project, session = fixture
    await service.dispatch("session.web.update", {**scope(project, session), "browser_persistent": True})
    first = await browse(service, transport, session, "retained-first")
    (first.profile / "Cookies").write_text("synthetic retained site data")
    await select_identity(service, project, session)
    second = await browse(service, transport, session, "retained-second")
    assert second is not first and second.profile == first.profile
    assert (second.profile / "Cookies").read_text() == "synthetic retained site data"


async def test_profile_retained_under_a_generation_key_is_adopted(fixture):
    service, transport, project, session = fixture
    await service.dispatch("session.web.update", {**scope(project, session), "browser_persistent": True})
    root = service.paths.data / "browser-profiles"
    root.mkdir(mode=0o700, exist_ok=True)
    account = session["settings"].get("openai_account_id")
    generation = service.provider_routing.current_generation(session["id"])
    legacy = root / hashlib.sha256(encode([session["id"], session["backend"], account, generation]).encode()).hexdigest()
    legacy.mkdir(mode=0o700)
    (legacy / "Cookies").write_text("synthetic retained site data")
    actor = await browse(service, transport, session, "adopt")
    expected = hashlib.sha256(encode([session["id"], session["backend"], account]).encode()).hexdigest()
    assert actor.profile == root / expected
    assert (actor.profile / "Cookies").read_text() == "synthetic retained site data"
    assert not legacy.exists()


async def test_browser_opened_during_a_slow_close_stays_registered(fixture):
    service, transport, _, session = fixture
    old = await browse(service, transport, session, "slow-close")
    release = asyncio.Event()

    async def slow_close():
        await release.wait()
        old.engine.closed = True

    old.engine.close = slow_close
    closing = asyncio.create_task(service.web._close_actor(old))
    await asyncio.sleep(0)
    assert old.closed
    await service.web._engine(service.store.one("sessions", session["id"]), None)
    replacement = service.web.actors[session["id"]]
    assert replacement is not old
    release.set()
    await closing
    assert service.web.actors[session["id"]] is replacement
    assert service.web.sessions[replacement.id] is replacement.engine
    assert browser_state(service, old) == "stopped" and browser_state(service, replacement) == "active"


@pytest.mark.parametrize("code,exited,retired", [
    ("browser_timeout", False, False),
    ("browser_failed", False, False),
    ("browser_failed", True, True),
    ("browser_timeout", True, True),
])
async def test_failed_action_retires_the_browser_only_when_its_worker_exited(fixture, monkeypatch, code, exited, retired):
    service, transport, _, session = fixture
    original = Engine.call

    async def failing(self, action, arguments):
        if action == "navigate":
            if exited:
                self._process = SimpleNamespace(returncode=-9)
            return {"ok": False, "error": {"code": code, "message": "Synthetic browser failure"}}
        return await original(self, action, arguments)

    monkeypatch.setattr(Engine, "call", failing)
    transport.outputs = [[call("navigate", {"url": "https://example.com/"})]]
    run = await start(fixture)
    await finish(service, run)
    engine = Engine.instances[0]
    assert engine.closed is retired
    assert (session["id"] in service.web.actors) is not retired


SANDBOX = ("Chromium could not start its sandbox because this system restricts unprivileged user "
           "namespaces through AppArmor. Synthetic explanation.")


async def test_worker_start_failure_reaches_the_model_and_the_panel(fixture, monkeypatch):
    service, transport, _, session = fixture

    async def restricted(self):
        raise BrowserStartError("browser_sandbox_unavailable", SANDBOX)

    monkeypatch.setattr(Engine, "start", restricted)
    transport.outputs = [[call("navigate", {"url": "https://example.com/"})]]
    run = await start(fixture)
    await finish(service, run)
    completed = next(event["payload"] for event in service.store.events(run["id"])["events"]
                     if event["type"] == "tool.completed")
    assert completed["result"]["error"] == {"code": "browser_sandbox_unavailable", "message": SANDBOX}
    assert service.web.snapshot(session)["browser"]["error"] == SANDBOX
    assert not service.web.actors and not service.web.sessions


async def test_worker_start_failure_reaches_the_desktop_that_opened_the_browser(fixture, monkeypatch):
    service, _, project, session = fixture

    async def restricted(self):
        raise BrowserStartError("browser_sandbox_unavailable", SANDBOX)

    monkeypatch.setattr(Engine, "start", restricted)
    with pytest.raises(KiraError) as failed:
        await service.dispatch("session.browser.open", {**scope(project, session), "request_id": str(uuid4())},
                               desktop(service))
    assert (failed.value.code, str(failed.value)) == ("browser_sandbox_unavailable", SANDBOX)


async def test_surface_error_code_reaches_the_browser_state(fixture):
    service, transport, project, session = fixture
    actor = await browse(service, transport, session, "surface-error")
    client = desktop(service)
    while not client.queue.empty():
        client.queue.get_nowait()
    await service.web._event(actor, {"event": "state", "data": {
        "surface_error": "The complete browser view is unavailable.", "surface_error_code": "view_unavailable"}})
    browser = (await service.dispatch("session.web.get", scope(project, session)))["browser"]
    assert browser["surface_error_code"] == "view_unavailable"
    published = []
    while not client.queue.empty():
        message = client.queue.get_nowait()
        if isinstance(message, dict) and "browser_event" in message:
            published.append(message["browser_event"])
    assert published and published[-1]["payload"]["surface_error_code"] == "view_unavailable"
