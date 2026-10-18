"""Live browser consent must not invalidate an unchanged provider's guidance."""

import asyncio
import json

import pytest

from kira.backends.guidance import BoundaryGuidance, GuidanceError
from kira.domain.contracts import KiraError
from kira.service.storage import encode
from test_browser_action_approval import browser_call, decide, events, pending_approval, set_policy
import test_service_computer as computer

computer_service = computer.computer_service


def steer(context, identity="synthetic-guidance"):
    return {"run_id": context.run_id, "request_id": identity,
            "prompt": "Use the updated synthetic instruction after the current action"}


@pytest.mark.parametrize("delivery", ["none", "queued", "submitted"])
@pytest.mark.parametrize("visual", [False, True])
async def test_accept_always_keeps_guidance_bound_and_never_repeats_action(computer_service, delivery, visual):
    service, _, _, session = computer_service
    context = await computer.start(computer_service)
    guidance = BoundaryGuidance(context)
    assert context.guidance is not None
    binding = service.turn_controls._scope(context.run_id)
    stored_binding = dict(service.store.db.execute(
        "SELECT * FROM run_provider_bindings WHERE run_id=?", (context.run_id,)).fetchone())
    params = steer(context)
    if delivery == "submitted":
        await service.dispatch("run.steer", params)
        assert await guidance.take() == [{"role": "user", "content": params["prompt"]}]
    else:
        assert await guidance.take() == []
    if visual:
        observed = await computer.invoke(context, "observe")
        pending = asyncio.create_task(computer.invoke(context, "act", {
            "observation_id": observed["observation_id"], "action": "click", "x": 1, "y": 2}))
    else:
        pending = asyncio.create_task(browser_call(context, "click", {"ref": "synthetic-ref"}))
    approval = await pending_approval(service, context, tool="computer.act" if visual else "browser.click")
    if delivery == "queued":
        result = await service.dispatch("run.steer", params)
        assert result["delivery_state"] == "queued" and not result["accepted"]
    assert (await decide(service, context, approval, "accept_always"))["accepted"]
    result = await asyncio.wait_for(pending, 3)
    assert result["ok"]
    if visual:
        assert result["action_executed"] is True
    # These are the same safe-boundary calls the real direct adapter performs
    # after a tool result. They must work even when the user sent no guidance.
    if delivery == "submitted":
        await guidance.accepted()
    assert await guidance.pending() is (delivery == "queued")
    if delivery == "queued":
        assert await guidance.take() == [{"role": "user", "content": params["prompt"]}]
        await guidance.accepted()
    assert await guidance.take() == []
    assert await guidance.seal() is True
    assert service.turn_controls._scope(context.run_id) == binding
    assert dict(service.store.db.execute("SELECT * FROM run_provider_bindings WHERE run_id=?",
                                         (context.run_id,)).fetchone()) == stored_binding
    current = service.store.one("sessions", session["id"])
    assert current["settings"]["browser_action_approval"] == "always"
    assert current["model"] == session["model"]
    assert current["settings"]["openai_account_id"] == session["settings"]["openai_account_id"]
    assert current["settings"]["reasoning_effort"] == session["settings"]["reasoning_effort"]
    assert len(events(service, context, "approval.requested")) == 1
    assert not events(service, context, "steering.failed")
    assert len(events(service, context, "steering.accepted")) == int(delivery != "none")
    actor = service.web.actors[session["id"]]
    executed = "computer_perform" if visual else "perform_interaction"
    assert sum(action == executed for action, _ in actor.engine.calls) == 1
    await computer.finish(computer_service, context)
    assert not (await decide(service, context, approval, "accept_always"))["accepted"]


async def test_live_consent_revocation_restores_approval_without_breaking_guidance(computer_service):
    service, _, project, session = computer_service
    await set_policy(service, project, session, "always")
    context = await computer.start(computer_service)
    guidance = BoundaryGuidance(context)
    assert (await browser_call(context, "click", {"ref": "synthetic-ref"}))["ok"]
    assert not events(service, context, "approval.requested")
    await service.dispatch("run.steer", steer(context))
    generation = service.provider_routing.current_generation(session["id"])
    await set_policy(service, project, session, "ask", expected_generation=generation)
    assert await guidance.pending() is True
    assert len(await guidance.take()) == 1
    await guidance.accepted()
    pending = asyncio.create_task(browser_call(context, "click", {"ref": "second-ref"}))
    approval = await pending_approval(service, context, tool="browser.click")
    await decide(service, context, approval, "decline")
    assert not (await pending)["ok"]
    assert await guidance.pending() is False
    assert await guidance.seal() is True
    actor = service.web.actors[session["id"]]
    assert sum(action == "perform_interaction" for action, _ in actor.engine.calls) == 1
    assert service.provider_routing.current_generation(session["id"]) == generation
    assert not events(service, context, "steering.failed")
    await computer.finish(computer_service, context)


@pytest.mark.parametrize("changed", [
    "backend", "model", "account", "effort", "permission", "root", "generation",
    "tools", "browser_enabled", "browser_persistent", "web_search", "handoff", "malformed_policy",
])
async def test_live_policy_exception_keeps_other_guidance_bindings_strict(computer_service, changed, tmp_path):
    service, _, project, session = computer_service
    context = await computer.start(computer_service)
    guidance = BoundaryGuidance(context)
    params = steer(context)
    await service.dispatch("run.steer", params)
    await set_policy(service, project, session, "always")
    assert await guidance.pending() is True
    current = service.store.one("sessions", session["id"])
    if changed in {"backend", "model"}:
        column = "backend" if changed == "backend" else "model"
        service.store.db.execute(f"UPDATE sessions SET {column}=? WHERE id=?",
                                 ("ollama" if changed == "backend" else "different-model", session["id"]))
    elif changed in {"permission", "root"}:
        column = "permission" if changed == "permission" else "root"
        service.store.db.execute(f"UPDATE projects SET {column}=? WHERE id=?",
                                 ("read_only" if changed == "permission" else str(tmp_path.parent), project["id"]))
    elif changed == "generation":
        service.store.db.execute("INSERT INTO session_provider_generations "
            "SELECT session_id,generation+1,backend,model,settings,backend_ref,created_at,retired_at "
            "FROM session_provider_generations WHERE session_id=?", (session["id"],))
        service.store.db.execute("UPDATE run_provider_bindings SET generation=generation+1 WHERE run_id=?",
                                 (context.run_id,))
    else:
        key, value = {
            "account": ("openai_account_id", "different-account"),
            "effort": ("reasoning_effort", "low"),
            "tools": ("tools", False),
            "browser_enabled": ("browser_enabled", False),
            "browser_persistent": ("browser_persistent", True),
            "web_search": ("web_search", "disabled"),
            "handoff": ("handoff", "changed synthetic context"),
            "malformed_policy": ("browser_action_approval", {"invalid": True}),
        }[changed]
        service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?",
                                 (encode({**current["settings"], key: value}), session["id"]))
    with pytest.raises(GuidanceError, match=r"unavailable \(CONFLICT\)"):
        await guidance.take()
    receipt = await service.dispatch("run.steer", params)
    assert receipt["delivery_state"] == "not_delivered"
    assert receipt["accepted"] is False and receipt["automatic_replay"] is False
    assert not events(service, context, "steering.submitted")
    assert not events(service, context, "steering.accepted")
    await computer.finish(computer_service, context)


async def test_invalid_or_stale_live_policy_updates_leave_guidance_unchanged(computer_service):
    service, _, project, session = computer_service
    context = await computer.start(computer_service)
    guidance = BoundaryGuidance(context)
    for value, expected_generation in (("invalid", 0), ("always", 1)):
        with pytest.raises(KiraError):
            await set_policy(service, project, session, value, expected_generation=expected_generation)
        assert await guidance.pending() is False
    assert await guidance.seal() is True
    stored = service.store.db.execute("SELECT settings FROM run_provider_bindings WHERE run_id=?",
                                      (context.run_id,)).fetchone()
    assert json.loads(stored["settings"]).get("browser_action_approval", "ask") == "ask"
    await computer.finish(computer_service, context)
