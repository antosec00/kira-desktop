"""Chat-scoped browser consent with synthetic providers and owned mock actors."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from kira.domain.contracts import KiraError
from kira.domain.web import WEB_DEFAULTS, validate_web_settings
from kira.service.server import Service
from kira.tools.browser import BrowserSession
from test_embedded_browser_service import command, desktop, owned
import test_service_computer as computer

computer_service = computer.computer_service


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def policy(service, session):
    current = service.store.one("sessions", session["id"])
    return service.web.snapshot(current)["settings"]["browser_action_approval"]


async def set_policy(service, project, session, value, **extra):
    return await service.dispatch("session.web.update", {
        **owned(project, session), "browser_action_approval": value, **extra})


@pytest.mark.parametrize("expected", [True, -1, "0", 1])
async def test_policy_rejects_invalid_or_retired_expected_generation(computer_service, expected):
    service, _, project, session = computer_service
    before = service.store.one("sessions", session["id"])
    with pytest.raises(KiraError) as failure:
        await set_policy(service, project, session, "always", expected_generation=expected)
    assert failure.value.code == ("CONFLICT" if type(expected) is int and expected >= 0 else "INVALID_ARGUMENT")
    assert service.store.one("sessions", session["id"]) == before
    assert policy(service, session) == "ask"


async def test_current_generation_policy_update_remains_live(computer_service):
    service, _, project, session = computer_service
    context = await computer.start(computer_service)
    generation = service.provider_routing.current_generation(session["id"])
    result = await set_policy(service, project, session, "always", expected_generation=generation)
    assert result["settings"]["browser_action_approval"] == "always"
    assert service.provider_routing.current_generation(session["id"]) == generation
    await computer.finish(computer_service, context)


async def browser_call(context, action, arguments=None):
    return await context.client_tool_call(str(uuid4()), "browser." + action, arguments or {})


def events(service, context, kind):
    return [event["payload"] for event in service.store.events(context.run_id)["events"]
            if event["type"] == kind]


async def pending_approval(service, context, *, tool=None, kind=None):
    def current():
        for request in reversed(events(service, context, "approval.requested")):
            future = service.approvals.get((context.run_id, request["approval_id"]))
            if (future is not None and not future.done() and (tool is None or request.get("tool") == tool)
                    and (kind is None or request.get("kind") == kind)):
                return request
        return None

    await until(current)
    return current()


async def decide(service, context, approval, decision):
    return await service.dispatch("run.approve", {"run_id": context.run_id,
        "approval_id": approval["approval_id"], "decision": decision})


async def watch(service, project, session):
    actor = service.web.actors[session["id"]]
    client = desktop(service)
    params = {**owned(project, session), "browser_id": actor.id}
    result = await service.dispatch("session.browser.watch", params, client)
    return actor, client, params, result["browser"]["input_stream_id"]


@pytest.mark.parametrize("value", [None, True, 1, [], {}, "ALWAYS", "always ", "accept_always"])
def test_policy_values_are_explicit_and_closed(value):
    assert WEB_DEFAULTS["browser_action_approval"] == "ask"
    with pytest.raises(KiraError):
        validate_web_settings({"browser_action_approval": value})


async def test_default_asks_and_accept_once_does_not_change_policy(computer_service):
    service, _, _, session = computer_service
    context = await computer.start(computer_service)
    assert policy(service, session) == "ask"
    first = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic-ref"}))
    approval = await pending_approval(service, context, tool="browser.click")
    assert approval.get("allow_always") is True
    actor = service.web.actors[session["id"]]
    assert not any(action == "perform_interaction" for action, _ in actor.engine.calls)
    await decide(service, context, approval, "accept")
    assert (await first)["ok"] and policy(service, session) == "ask"
    second = asyncio.create_task(browser_call(context, "press", {"key": "Enter"}))
    approval = await pending_approval(service, context, tool="browser.press")
    await decide(service, context, approval, "decline")
    assert not (await second)["ok"]
    assert sum(action == "perform_interaction" for action, _ in actor.engine.calls) == 1
    await computer.finish(computer_service, context)


async def test_accept_always_persists_live_policy_audits_and_never_replays_grant(computer_service):
    service, _, project, session = computer_service
    context = await computer.start(computer_service)
    first = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic-ref"}))
    approval = await pending_approval(service, context, tool="browser.click")
    actor = service.web.actors[session["id"]]
    generation = service.provider_routing.current_generation(session["id"])
    assert (await decide(service, context, approval, "accept_always"))["accepted"]
    assert (await first)["ok"] and policy(service, session) == "always"
    assert service.web.actors[session["id"]] is actor and not actor.closed
    assert service.provider_routing.current_generation(session["id"]) == generation
    assert len(events(service, context, "approval.requested")) == 1
    notices = events(service, context, "backend.notice")
    assert any(notice.get("decision") == "accept_always" and notice.get("scope") == "chat"
               and notice.get("browser_action_approval") == "always" for notice in notices)
    for action, arguments in (("type", {"ref": "synthetic-ref", "text": "Synthetic"}),
                              ("press", {"key": "Enter"})):
        assert (await browser_call(context, action, arguments))["ok"]
    assert len(events(service, context, "approval.requested")) == 1
    assert sum(action == "perform_interaction" for action, _ in actor.engine.calls) == 3
    await set_policy(service, project, session, "ask")
    assert not (await decide(service, context, approval, "accept_always"))["accepted"]
    assert policy(service, session) == "ask"
    current = service.store.one("sessions", session["id"])
    assert current["model"] == session["model"] and current["settings"]["reasoning_effort"] == "high"
    await computer.finish(computer_service, context)


async def test_always_applies_to_every_computer_mutation_without_fabricated_approvals(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    current = await computer.invoke(context, "observe")
    for action, arguments in (("click", {"x": 1, "y": 2}), ("double_click", {"x": 1, "y": 2}),
                             ("type", {"text": "Synthetic"}), ("key", {"key": "Enter"}),
                             ("drag", {"path": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]})):
        current = await computer.invoke(context, "act", {
            "observation_id": current["observation_id"], "action": action, **arguments})
        assert current["ok"] and current["action_executed"]
    assert service.web.actors[session["id"]].engine.effects == 5
    assert not events(service, context, "approval.requested") and not service.approvals
    assert sum(event.get("name") == "computer.act" for event in events(service, context, "tool.completed")) == 5
    await computer.finish(computer_service, context)


async def test_computer_approval_can_grant_always_for_later_browser_actions(computer_service):
    service, _, _, session = computer_service
    context = await computer.start(computer_service)
    observed = await computer.invoke(context, "observe")
    pending = asyncio.create_task(computer.invoke(context, "act", {
        "observation_id": observed["observation_id"], "action": "click", "x": 1, "y": 2}))
    approval = await pending_approval(service, context, tool="computer.act")
    assert approval.get("allow_always") is True
    await decide(service, context, approval, "accept_always")
    assert (await pending)["action_executed"] and policy(service, session) == "always"
    assert (await browser_call(context, "press", {"key": "Enter"}))["ok"]
    assert len(events(service, context, "approval.requested")) == 1
    await computer.finish(computer_service, context)


async def test_live_policy_update_resolves_only_owned_browser_approval(computer_service, tmp_path_factory):
    service, _, project, session = computer_service
    root = tmp_path_factory.mktemp("browser-consent-other-project")
    other_project = (await service.dispatch("project.create", {"name": "Other synthetic project",
        "root": str(root), "permission": "project_write"}))["project"]
    other_session = (await service.dispatch("session.create", {"project_id": other_project["id"],
        "backend": "openai_chatgpt", "model": session["model"], "settings": session["settings"]}))["session"]
    first_context = await computer.start(computer_service)
    second_context = await computer.start(computer_service, session=other_session)
    first = asyncio.create_task(browser_call(first_context, "click", {"ref": "first"}))
    second = asyncio.create_task(browser_call(second_context, "click", {"ref": "second"}))
    first_approval = await pending_approval(service, first_context, tool="browser.click")
    second_approval = await pending_approval(service, second_context, tool="browser.click")
    provider = asyncio.create_task(first_context.approve({"kind": "provider_action", "message": "Synthetic provider decision"}))
    provider_approval = await pending_approval(service, first_context, kind="provider_action")
    actor = service.web.actors[session["id"]]
    generation = actor.generation
    result = await set_policy(service, project, session, "always")
    assert result["settings"]["browser_action_approval"] == "always"
    assert (await first)["ok"] and not second.done() and not provider.done()
    assert policy(service, other_session) == "ask"
    assert service.web.actors[session["id"]] is actor and not actor.engine.closed
    assert service.provider_routing.current_generation(session["id"]) == generation
    assert any(notice.get("approval_id") == first_approval["approval_id"]
               and notice.get("decision") == "accept_always" for notice in events(service, first_context, "backend.notice"))
    await decide(service, second_context, second_approval, "decline")
    await decide(service, first_context, provider_approval, "decline")
    assert not (await second)["ok"] and (await provider)["decision"] == "decline"
    await computer.finish(computer_service, first_context)
    await computer.finish(computer_service, second_context)


@pytest.mark.parametrize("approval_request", [
    {"kind": "browser_action", "tool": "browser.click", "arguments": {"ref": "synthetic"}, "allow_always": True},
    {"kind": "browser_manual_login", "tool": "browser.manual_login", "allow_always": True},
    {"kind": "command_execution", "command": "synthetic", "allow_always": True},
    {"kind": "provider_action", "allow_always": True},
])
async def test_untrusted_provider_approval_cannot_offer_or_grant_browser_policy(computer_service, approval_request):
    service, _, _, session = computer_service
    context = await computer.start(computer_service)
    pending = asyncio.create_task(context.approve(approval_request))
    approval = await pending_approval(service, context)
    assert approval.get("allow_always") is not True
    with pytest.raises(KiraError) as failure:
        await decide(service, context, approval, "accept_always")
    # A provider request has no trusted Kira browser-action binding.
    assert failure.value.code == "INVALID_ARGUMENT"
    assert policy(service, session) == "ask" and not pending.done()
    await decide(service, context, approval, "decline")
    assert (await pending)["decision"] == "decline"
    await computer.finish(computer_service, context)


async def test_always_never_confirms_manual_login(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    pending = asyncio.create_task(browser_call(context, "manual_login"))
    approval = await pending_approval(service, context, kind="browser_manual_login")
    assert approval.get("allow_always") is not True
    await set_policy(service, project, session, "always")
    assert not pending.done()
    with pytest.raises(KiraError) as failure:
        await decide(service, context, approval, "accept_always")
    # Manual login is never an eligible browser action for a persistent grant.
    assert failure.value.code == "INVALID_ARGUMENT"
    await decide(service, context, approval, "decline")
    assert not (await pending)["ok"]
    assert service.web.actors[session["id"]].state["manual"]
    await computer.finish(computer_service, context)


@pytest.mark.parametrize("field,value", [("generation", 999), ("browser_id", "other-actor")])
async def test_stale_trusted_binding_cannot_persist_always(computer_service, field, value):
    service, _, _, session = computer_service
    context = await computer.start(computer_service)
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"}))
    approval = await pending_approval(service, context, tool="browser.click")
    binding = service.browser_action_approvals[(context.run_id, approval["approval_id"])]
    binding[field] = value
    with pytest.raises(KiraError) as failure:
        await decide(service, context, approval, "accept_always")
    assert failure.value.code == "PERMISSION_DENIED"
    assert policy(service, session) == "ask" and not pending.done()
    await decide(service, context, approval, "decline")
    assert not (await pending)["ok"]
    await computer.finish(computer_service, context)


async def test_cancelled_request_cannot_later_grant_always(computer_service):
    service, _, _, session = computer_service
    context = await computer.start(computer_service)
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"}))
    approval = await pending_approval(service, context, tool="browser.click")
    context.cancel.set()
    assert not (await pending)["ok"]
    assert not (await decide(service, context, approval, "accept_always"))["accepted"]
    assert policy(service, session) == "ask" and not service.browser_action_approvals
    assert not any(action == "perform_interaction" for action, _ in service.web.actors[session["id"]].engine.calls)
    await computer.finish(computer_service, context, "cancelled")


async def test_revoke_during_manual_wait_requires_new_consent_before_dispatch(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    await browser_call(context, "navigate", {"url": "https://example.com/"})
    actor, client, params, stream = await watch(service, project, session)
    await command(service, client, params, stream, 1, "ui_takeover")
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"}))
    await until(lambda: context.run_id in actor.waiting_runs)
    before = list(actor.engine.calls)
    await set_policy(service, project, session, "ask")
    assert not pending.done() and actor.engine.calls == before and actor.state["manual"]
    await command(service, client, params, stream, 2, "ui_resume")
    approval = await pending_approval(service, context, tool="browser.click")
    assert not any(action == "perform_interaction" for action, _ in actor.engine.calls)
    await decide(service, context, approval, "accept")
    assert (await pending)["ok"] and policy(service, session) == "ask"
    await computer.finish(computer_service, context)


@pytest.mark.parametrize("kind", ["browser", "computer"])
async def test_revoke_after_preparation_is_checked_again_before_effect(computer_service, monkeypatch, kind):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    observed = await computer.invoke(context, "observe")
    actor, client, params, stream = await watch(service, project, session)
    original = actor.engine.call
    prepared = "prepare_interaction" if kind == "browser" else "computer_prepare"

    async def take_control_after_prepare(action, arguments):
        result = await original(action, arguments)
        if action == prepared:
            await service.web._event(actor, {"event": "state", "data": {"manual": True}})
        return result

    monkeypatch.setattr(actor.engine, "call", take_control_after_prepare)
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"})
        if kind == "browser" else computer.invoke(context, "act", {
            "observation_id": observed["observation_id"], "action": "click", "x": 1, "y": 2}))
    await until(lambda: context.run_id in actor.waiting_runs)
    await set_policy(service, project, session, "ask")
    await command(service, client, params, stream, 1, "ui_resume")
    approval = await pending_approval(service, context, tool="browser.click" if kind == "browser" else "computer.act")
    assert not any(action in {"perform_interaction", "computer_perform"} for action, _ in actor.engine.calls)
    await decide(service, context, approval, "accept")
    assert (await pending)["ok"]
    await computer.finish(computer_service, context)


async def test_revoked_persistent_grant_is_not_cached_as_one_time_consent(computer_service):
    service, _, project, session = computer_service
    context = await computer.start(computer_service)
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"}))
    first = await pending_approval(service, context, tool="browser.click")
    actor, client, params, stream = await watch(service, project, session)
    await command(service, client, params, stream, 1, "ui_takeover")
    await set_policy(service, project, session, "always")
    await until(lambda: context.run_id in actor.waiting_runs)
    await set_policy(service, project, session, "ask")
    await command(service, client, params, stream, 2, "ui_resume")
    second = await pending_approval(service, context, tool="browser.click")
    assert second["approval_id"] != first["approval_id"]
    assert not any(action == "perform_interaction" for action, _ in actor.engine.calls)
    await decide(service, context, second, "accept")
    assert (await pending)["ok"]
    await computer.finish(computer_service, context)


async def test_already_accepted_once_survives_policy_revoke_during_manual_wait(computer_service):
    service, _, project, session = computer_service
    context = await computer.start(computer_service)
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"}))
    approval = await pending_approval(service, context, tool="browser.click")
    actor, client, params, stream = await watch(service, project, session)
    await command(service, client, params, stream, 1, "ui_takeover")
    await decide(service, context, approval, "accept")
    await until(lambda: context.run_id in actor.waiting_runs)
    await set_policy(service, project, session, "always")
    await set_policy(service, project, session, "ask")
    await command(service, client, params, stream, 2, "ui_resume")
    assert (await pending)["ok"]
    assert len(events(service, context, "approval.requested")) == 1
    await computer.finish(computer_service, context)


@pytest.mark.parametrize("mode,permission", [("plan", "project_write"), ("review", "project_write"),
                                           ("execute", "read_only")])
async def test_always_does_not_expand_execute_or_project_permissions(computer_service, mode, permission):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    if permission == "read_only":
        await service.dispatch("project.update", {"project_id": project["id"], "permission": permission})
    context = await computer.start(computer_service, mode)
    result = await browser_call(context, "click", {"ref": "synthetic"})
    assert result["error"]["code"] == "PERMISSION_DENIED"
    observed = await computer.invoke(context, "observe")
    result = await computer.invoke(context, "act", {"observation_id": observed["observation_id"],
        "action": "click", "x": 1, "y": 2})
    assert result["error"]["code"] == "PERMISSION_DENIED" and not result["action_executed"]
    assert not events(service, context, "approval.requested")
    assert service.web.actors[session["id"]].engine.effects == 0
    await computer.finish(computer_service, context)


async def test_always_preserves_stale_observation_and_screenshot_limits(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    observed = await computer.invoke(context, "observe")
    actor = service.web.actors[session["id"]]
    actor.engine.current_observation = None
    result = await computer.invoke(context, "act", {"observation_id": observed["observation_id"],
        "action": "click", "x": 1, "y": 2})
    assert not result["ok"] and not result["action_executed"] and actor.engine.effects == 0
    service.web.browser_captures[context.run_id] = 16
    result = await browser_call(context, "screenshot")
    assert result["error"]["code"] == "OUTPUT_LIMIT"
    assert not events(service, context, "approval.requested")
    await computer.finish(computer_service, context)


async def test_policy_only_update_is_live_but_other_settings_keep_idle_guard(computer_service):
    service, _, project, session = computer_service
    context = await computer.start(computer_service)
    generation = service.provider_routing.current_generation(session["id"])
    before = service.store.one("sessions", session["id"])
    # Any setting besides the consent policy is refused by the active-work guard.
    for extra in ({"browser_persistent": True}, {"web_search": "cached"}, {"browser_enabled": False}):
        with pytest.raises(KiraError) as failure:
            await set_policy(service, project, session, "always", **extra)
        assert failure.value.code == "BUSY"
    with pytest.raises(KiraError) as failure:
        await set_policy(service, project, session, None)
    assert failure.value.code == "INVALID_ARGUMENT"
    assert service.store.one("sessions", session["id"]) == before
    await set_policy(service, project, session, "always")
    assert policy(service, session) == "always"
    assert service.provider_routing.current_generation(session["id"]) == generation
    await computer.finish(computer_service, context)


async def test_policy_survives_service_restart_without_reopening_browser(computer_service):
    service, provider, project, session = computer_service
    await set_policy(service, project, session, "always")
    before = service.store.one("sessions", session["id"])
    paths = service.paths
    await service.close()
    restarted = Service(paths, backend_factory=lambda *_: provider)
    try:
        await restarted.start()
        assert restarted.store.one("sessions", session["id"]) == before
        assert policy(restarted, session) == "always" and not restarted.web.actors
    finally:
        await restarted.close()


async def test_fork_resets_consent_without_changing_original_chat(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    await computer.finish(computer_service, context)
    result = await service.dispatch("session.fork", {**owned(project, session),
        "request_id": str(uuid4()), "through_run_id": context.run_id})
    fork = result["session"]
    assert policy(service, fork) == "ask" and policy(service, session) == "always"
    assert fork["model"] == session["model"]
    assert fork["settings"]["openai_account_id"] == session["settings"]["openai_account_id"]


@pytest.mark.parametrize("transition", ["account", "provider"])
async def test_provider_or_account_switch_never_restores_retired_always(computer_service, transition):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    target = {"backend": "openai_chatgpt", "model": session["model"], "reasoning_effort": "high",
              "account_id": "different-synthetic-account"}
    if transition == "provider":
        target = {"backend": "codex", "model": "synthetic-other-provider", "reasoning_effort": "high"}
    await service.dispatch("session.switch", {**owned(project, session), "request_id": str(uuid4()), **target})
    current = service.store.one("sessions", session["id"])
    assert current["settings"].get("browser_action_approval", "ask") == "ask"
    await service.dispatch("session.switch", {**owned(project, session), "request_id": str(uuid4()),
        "backend": "openai_chatgpt", "model": session["model"], "reasoning_effort": "high",
        "account_id": session["settings"]["openai_account_id"]})
    assert policy(service, session) == "ask"


async def test_model_only_change_keeps_chat_account_consent(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    await service.dispatch("session.configure", {**owned(project, session), "model": "synthetic-other-model",
                                                "reasoning_effort": "high"})
    assert policy(service, session) == "always"


async def test_model_guidance_describes_user_policy_without_claiming_blanket_authority(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    guidance = context.assistant_instructions.lower()
    assert "individual user confirmation" not in guidance
    assert "browser consent policy" in guidance
    assert "require execute" in guidance and "manual" in guidance
    assert "never" in guidance and "uncertain" in guidance
    await computer.finish(computer_service, context)


def queued_dispatch(monkeypatch, actor):
    """Use the production IPC lock/admission path without starting a process."""
    wire = BrowserSession(actor.profile, actor.engine.artifacts_dir, headed=True,
                          persistent=False, started=AsyncMock())
    wire._process = SimpleNamespace(returncode=None)
    wire.close = AsyncMock()
    entered, effects = asyncio.Event(), []
    original = actor.engine.call

    async def exchange(action, arguments):
        effects.append(action)
        return await original(action, arguments)

    async def guarded(action, arguments, before_dispatch):
        if action in {"perform_interaction", "computer_perform"}:
            entered.set()
            return await wire.call_guarded(action, arguments, before_dispatch)
        rejected = before_dispatch()
        return rejected if rejected is not None else await original(action, arguments)

    monkeypatch.setattr(wire, "_exchange", exchange)
    monkeypatch.setattr(actor.engine, "call_guarded", guarded, raising=False)
    return wire, entered, effects


@pytest.mark.parametrize("kind", ["browser", "computer"])
async def test_revoke_while_queued_on_worker_lock_prevents_exchange(computer_service, monkeypatch, kind):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    observed = await computer.invoke(context, "observe")
    actor = service.web.actors[session["id"]]
    wire, entered, effects = queued_dispatch(monkeypatch, actor)
    await wire._lock.acquire()
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"})
        if kind == "browser" else computer.invoke(context, "act", {
            "observation_id": observed["observation_id"], "action": "click", "x": 1, "y": 2}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await set_policy(service, project, session, "ask")
        assert not effects
        wire._lock.release()
        approval = await pending_approval(service, context, tool="browser.click" if kind == "browser" else "computer.act")
        assert not effects
        await decide(service, context, approval, "accept")
        assert (await pending)["ok"] and len(effects) == 1
        wire.close.assert_not_awaited()
        await computer.finish(computer_service, context)
    finally:
        if wire._lock.locked():
            wire._lock.release()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("kind", ["browser", "computer"])
async def test_cancel_while_queued_has_no_uncertain_effect_or_browser_cleanup(computer_service, monkeypatch, kind):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    observed = await computer.invoke(context, "observe")
    actor = service.web.actors[session["id"]]
    wire, entered, effects = queued_dispatch(monkeypatch, actor)
    await wire._lock.acquire()
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"})
        if kind == "browser" else computer.invoke(context, "act", {
            "observation_id": observed["observation_id"], "action": "click", "x": 1, "y": 2}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        # A takeover ahead of the queued action can publish manual ownership
        # before its IPC exchange releases the lock. Preserve that user's page.
        await service.web._event(actor, {"event": "state", "data": {"manual": True}})
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not effects and not actor.uncertain_runs and not actor.closed
        wire.close.assert_not_awaited()
        if kind == "computer":
            result = events(service, context, "tool.completed")[-1]["result"]
            assert result["action_executed"] is False and "action_outcome" not in result
        wire._lock.release()
        await computer.finish(computer_service, context, "cancelled")
        assert not actor.closed and actor.profile.exists()
    finally:
        if wire._lock.locked():
            wire._lock.release()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_closed_actor_is_rechecked_after_waiting_on_worker_lock(computer_service, monkeypatch):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    await browser_call(context, "navigate", {"url": "https://example.com/"})
    actor, client, params, _ = await watch(service, project, session)
    wire, entered, effects = queued_dispatch(monkeypatch, actor)
    await wire._lock.acquire()
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic"}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await service.dispatch("session.browser.close", {**params, "request_id": str(uuid4())}, client)
        wire._lock.release()
        result = await pending
        assert not result["ok"] and not effects and not actor.uncertain_runs
        assert session["id"] not in service.web.actors
        wire.close.assert_not_awaited()
        await computer.finish(computer_service, context)
    finally:
        if wire._lock.locked():
            wire._lock.release()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
