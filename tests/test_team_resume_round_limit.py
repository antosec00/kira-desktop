"""Continuation round ceilings use isolated profiles and synthetic providers."""

import asyncio

import pytest
import test_service_teams as team_fixtures

from kira.domain.assistant import assistant_tool_specs
from kira.domain.contracts import KiraError
from kira.service.server import Service
from kira.service.storage import encode
from test_assistant_resume_tool import count_teams, raw_delegation
from test_assistant_service import (
    delegation,
    env as env,
    member,
    settle,
    snapshot,
    start,
)
from test_service_teams import finished, params

team_env = team_fixtures.env
INVALID_LIMITS = (True, False, None, "0", 0.0, -1, 10001)


def persisted_team(service, team_id):
    """Capture the prior execution, including its durable evidence, verbatim."""
    db = service.store.db
    return {
        "team": tuple(db.execute("SELECT * FROM team_runs WHERE id=?", (team_id,)).fetchone()),
        **{
            table: [tuple(row) for row in db.execute(
                f"SELECT * FROM {table} WHERE team_id=? ORDER BY rowid", (team_id,))]
            for table in ("team_members", "team_steps", "team_messages", "team_events", "team_run_links")
        },
    }


async def worked_source(env):
    """A single coordinator round dispatches a real worker, then pauses."""
    service, factory, _, _ = env

    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("initial-worker", "kira.team_delegate",
            delegation(members=[member(), member("reader")], concurrency=2, round_limit=1)))

    async def work(ctx):
        link = factory.service.teams.data.link(ctx.run_id)
        team = factory.service.teams.data.one(link["team_id"])
        if not link["coordinator"]:
            output = "WORKER_VERIFIED_CONTINUATION_FIXTURE"
        else:
            first = team["round_index"] == 1
            output = encode({"decision": "continue" if first else "completed",
                "summary": "Dispatch fixture analysis" if first else "Final fixture synthesis",
                "members": [], "tasks": [{"id": "inspect", "title": "Inspect fixture",
                    "member_id": "reader", "instructions": "Inspect synthetic evidence",
                    "access": "read", "depends_on": []}] if first else []})
        await ctx.emit("message.completed", {"content": output})
        return {"state": "completed", "outcome": {"final_text": output}}

    factory.origin, factory.member = origin, work
    factory.delegate_ready.clear()
    run = await start(env)
    await settle(service, run["id"])
    source = (await snapshot(env))["delegations"][0]
    assert source["state"] == "paused" and source["completed_steps"] == 1
    return run, source


async def model_resume(env, source, *, request, **extra):
    _, factory, _, _ = env

    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("continue", "kira.team_resume",
            {"delegation_id": source["id"], **extra}))

    factory.origin = origin
    factory.delegate_ready.clear()
    return await start(env, request=request)


def ui_params(env, source, request, **extra):
    _, _, project, session = env
    return {"project_id": project["id"], "session_id": session["id"],
        "delegation_id": source["id"], "request_id": request, **extra}


def test_model_schema_exposes_optional_bounded_round_limit():
    tool = next(item for item in assistant_tool_specs()[0]["tools"] if item["name"] == "team_resume")
    schema = tool["inputSchema"]
    assert schema["required"] == ["delegation_id"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"delegation_id", "instruction", "round_limit"}
    limit = schema["properties"]["round_limit"]
    assert limit["type"] == "integer" and limit["minimum"] == 0 and limit["maximum"] == 10000


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 1, 2, 10000])
async def test_team_resume_changes_only_child_and_reaches_synthesis(team_env, limit):
    service, factory, _ = team_env
    parent = (await service.dispatch("team.start", params(team_env, round_limit=1)))["team"]
    previous = await finished(service, parent["id"])
    assert previous["state"] == "paused" and previous["round_index"] == 1
    before = persisted_team(service, parent["id"])
    request = {"team_id": parent["id"], "request_id": "resume-with-limit", "round_limit": limit}
    child = (await service.dispatch("team.resume", request))["team"]
    assert child["parent_team_id"] == parent["id"] and child["round_limit"] == limit
    result = await finished(service, child["id"])
    assert result["state"] == ("paused" if limit == 1 else "completed")
    assert result["round_index"] == (1 if limit == 1 else 2)
    assert result["settings"] == previous["settings"]
    assert result["model"] == previous["model"] and result["backend"] == previous["backend"]
    child_record = service.teams.data.one(child["id"])
    parent_record = service.teams.data.one(parent["id"])
    assert child_record["permission"] == parent_record["permission"]
    assert child_record["root"] == parent_record["root"]
    assert "worker-report:producer" in factory.contexts[-1].supplied_context
    assert persisted_team(service, parent["id"]) == before


@pytest.mark.asyncio
async def test_team_resume_omission_inherits_parent_and_retries_are_exact(team_env):
    service, _, _ = team_env
    parent = (await service.dispatch("team.start", params(team_env, round_limit=1)))["team"]
    await finished(service, parent["id"])
    request = {"team_id": parent["id"], "request_id": "inherit-current-ceiling"}
    child = (await service.dispatch("team.resume", request))["team"]
    assert child["round_limit"] == 1
    await finished(service, child["id"])
    assert (await service.dispatch("team.resume", request))["team"]["id"] == child["id"]
    with pytest.raises(KiraError) as changed:
        await service.dispatch("team.resume", {**request, "round_limit": 0})
    assert changed.value.code == "CONFLICT"
    assert count_teams(service) == 2


@pytest.mark.asyncio
async def test_team_resume_strict_limits_are_rejected_before_effects(team_env):
    service, factory, _ = team_env
    parent = (await service.dispatch("team.start", params(team_env, round_limit=1)))["team"]
    await finished(service, parent["id"])
    before = persisted_team(service, parent["id"])
    calls = len(factory.contexts)
    for index, value in enumerate(INVALID_LIMITS):
        with pytest.raises(KiraError) as invalid:
            await service.dispatch("team.resume", {"team_id": parent["id"],
                "request_id": f"invalid-{index}", "round_limit": value})
        assert invalid.value.code == "INVALID_ARGUMENT", repr(value)
        assert count_teams(service) == 1 and len(factory.contexts) == calls
        assert persisted_team(service, parent["id"]) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["codex", "openai_chatgpt"])
async def test_model_zero_waits_for_origin_cleanup_and_preserves_bound_identity(env, backend):
    service, factory, project, session = env
    if backend == "openai_chatgpt":
        session = (await service.dispatch("session.create", {"project_id": project["id"],
            "backend": backend, "model": "fixture-direct-model", "settings": {
                "reasoning_effort": "high", "openai_account_id": "fixture-selected-account"}}))["session"]
        env = service, factory, project, session
    first, source = await worked_source(env)
    before = persisted_team(service, source["team_id"])
    old_delegation = raw_delegation(service, source["id"])
    old_events = service.store.one("runs", first["id"])["last_seq"]
    factory.close_gate = asyncio.Event()
    run = await model_resume(env, source, request="model-zero-limit", round_limit=0)
    accepted = factory.calls[-1]
    assert accepted["ok"] and accepted["state"] == "pending"
    assert count_teams(service) == 1
    pending = (await snapshot(env))["delegations"][0]
    assert pending["round_limit"] == 0 and pending["team_id"] is None
    factory.close_gate.set()
    await settle(service, run["id"])
    updated = (await snapshot(env))["delegations"][0]
    child = service.teams.data.one(updated["team_id"])
    assert child["parent_team_id"] == source["team_id"]
    assert child["round_limit"] == 0 and child["round_index"] == 2 and child["state"] == "completed"
    assert child["summary"] == "Final fixture synthesis"
    assert child["settings"] == service.teams.data.one(source["team_id"])["settings"]
    assert child["model"] == session["model"] and child["backend"] == backend
    assert child["permission"] == service.teams.data.one(source["team_id"])["permission"]
    assert factory.contexts[-1].permission == "read_only"
    assert "WORKER_VERIFIED_CONTINUATION_FIXTURE" in factory.contexts[-1].supplied_context
    assert raw_delegation(service, source["id"]) == old_delegation
    assert persisted_team(service, source["team_id"]) == before
    assert service.store.one("runs", first["id"])["last_seq"] == old_events


@pytest.mark.asyncio
async def test_model_override_retry_same_value_is_idempotent_changed_value_conflicts(env):
    service, factory, _, _ = env
    _, source = await worked_source(env)

    async def origin(ctx):
        args = {"delegation_id": source["id"], "round_limit": 0}
        accepted = await ctx.client_tool_call("same-identity", "kira.team_resume", args)
        factory.calls.append(accepted)
        assert accepted["ok"]
        assert await ctx.client_tool_call("same-identity", "kira.team_resume", args) == accepted
        rejected = await ctx.client_tool_call("same-identity", "kira.team_resume", {**args, "round_limit": 2})
        assert rejected["error"]["code"] == "CONFLICT"

    factory.origin = origin
    factory.delegate_ready.clear()
    run = await start(env, request="retry-model-limit")
    await settle(service, run["id"])
    assert count_teams(service) == 2
    assert (await snapshot(env))["delegations"][0]["round_limit"] == 0


@pytest.mark.asyncio
async def test_invalid_model_and_ui_limits_do_not_mutate_or_launch_team(env):
    service, factory, _, _ = env
    _, source = await worked_source(env)
    before = persisted_team(service, source["team_id"])
    old_delegation = raw_delegation(service, source["id"])
    calls = len(factory.contexts)
    for index, value in enumerate(INVALID_LIMITS):
        with pytest.raises(KiraError) as invalid:
            await service.dispatch("assistant.resume", ui_params(env, source,
                f"invalid-ui-{index}", round_limit=value))
        assert invalid.value.code == "INVALID_ARGUMENT", repr(value)
        assert len(factory.contexts) == calls and count_teams(service) == 1

    async def origin(ctx):
        for index, value in enumerate(INVALID_LIMITS):
            rejected = await ctx.client_tool_call(f"invalid-model-{index}", "kira.team_resume",
                {"delegation_id": source["id"], "round_limit": value})
            assert rejected["error"]["code"] == "INVALID_ARGUMENT", repr(value)
        factory.calls.append(await ctx.client_tool_call("valid-after-invalid", "kira.team_resume",
            {"delegation_id": source["id"], "round_limit": 2}))

    factory.origin = origin
    factory.close_gate = asyncio.Event()
    factory.delegate_ready.clear()
    run = await start(env, request="invalid-model-limits")
    assert factory.calls[-1]["ok"] and count_teams(service) == 1
    assert raw_delegation(service, source["id"]) == old_delegation
    assert persisted_team(service, source["team_id"]) == before
    factory.close_gate.set()
    await settle(service, run["id"])
    assert (await snapshot(env))["delegations"][0]["round_limit"] == 2


@pytest.mark.asyncio
async def test_ui_zero_then_positive_override_and_omission_follow_actual_child(env):
    service, _, _, _ = env
    run, source = await worked_source(env)
    before = persisted_team(service, source["team_id"])
    request = ui_params(env, source, "ui-zero", round_limit=0)
    result = await service.dispatch("assistant.resume", request)
    assert result["ok"] and result["delegation"]["round_limit"] == 0
    assert await service.dispatch("assistant.resume", request) == result
    with pytest.raises(KiraError) as changed:
        await service.dispatch("assistant.resume", {**request, "round_limit": 2})
    assert changed.value.code == "CONFLICT"
    await settle(service, run["id"])
    latest = (await snapshot(env))["delegations"][0]
    assert latest["state"] == "completed" and latest["round_limit"] == 0
    assert persisted_team(service, source["team_id"]) == before
    next_result = await service.dispatch("assistant.resume", ui_params(env, latest, "ui-finite", round_limit=2))
    await settle(service, run["id"])
    finite_id = next_result["delegation"]["team_id"]
    assert service.teams.data.one(finite_id)["round_limit"] == 2
    inherited = await service.dispatch("assistant.resume", ui_params(env, latest, "ui-inherited"))
    await settle(service, run["id"])
    child = service.teams.data.one(inherited["delegation"]["team_id"])
    assert child["parent_team_id"] == finite_id and child["round_limit"] == 2


@pytest.mark.asyncio
async def test_model_resume_omission_uses_linked_team_not_old_saved_override(env):
    service, _, _, _ = env
    _, source = await worked_source(env)
    first_resume = await model_resume(env, source, request="model-zero", round_limit=0)
    await settle(service, first_resume["id"])
    model_delegation = (await snapshot(env))["delegations"][0]
    finite = await service.dispatch("assistant.resume", ui_params(env, model_delegation,
        "ui-changes-linked-ceiling", round_limit=2))
    await settle(service, first_resume["id"])
    linked_id = finite["delegation"]["team_id"]
    third = await model_resume(env, model_delegation, request="model-inherits-current")
    await settle(service, third["id"])
    final = (await snapshot(env))["delegations"][0]
    child = service.teams.data.one(final["team_id"])
    assert child["parent_team_id"] == linked_id and child["round_limit"] == 2


@pytest.mark.asyncio
async def test_zero_continuation_keeps_bounded_no_progress_stop(env):
    service, factory, _, _ = env
    _, source = await worked_source(env)

    async def no_progress(ctx):
        output = encode({"decision": "continue", "summary": "No new assignments",
            "members": [], "tasks": []})
        return {"state": "completed", "outcome": {"final_text": output}}

    factory.member = no_progress
    run = await model_resume(env, source, request="unlimited-without-progress", round_limit=0)
    await settle(service, run["id"])
    latest = (await snapshot(env))["delegations"][0]
    team = service.teams.data.one(latest["team_id"])
    assert team["round_limit"] == 0 and team["state"] == "paused"
    assert team["pause_reason"] == "no_progress" and team["round_index"] == 2


@pytest.mark.asyncio
async def test_recovered_original_handoff_can_change_limit_before_first_team(env):
    service, factory, _, _ = env

    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("first-handoff", "kira.team_delegate",
            delegation(round_limit=1)))

    factory.origin = origin
    factory.origin_gate = asyncio.Event()
    run = await start(env)
    await service.assistant.recover()
    factory.origin_gate.set()
    await settle(service, run["id"])
    source = (await snapshot(env))["delegations"][0]
    assert source["state"] == "interrupted" and source["team_id"] is None
    assert source["round_limit"] == 1 and count_teams(service) == 0
    result = await service.dispatch("assistant.resume", ui_params(env, source,
        "recover-original-with-zero", round_limit=0))
    assert result["ok"]
    await settle(service, run["id"])
    team = service.teams.data.one(result["delegation"]["team_id"])
    assert team["parent_team_id"] is None and team["round_limit"] == 0
    assert team["state"] == "completed" and count_teams(service) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_limit", [None, 2])
async def test_pending_model_override_survives_restart_and_explicit_recovery(env, monkeypatch, recovery_limit):
    service, factory, project, session = env
    _, source = await worked_source(env)
    before = persisted_team(service, source["team_id"])

    async def hold(run_id, cleaned):
        return None

    monkeypatch.setattr(service.assistant, "after_run", hold)
    run = await model_resume(env, source, request="saved-pending-limit", round_limit=0)
    await settle(service, run["id"])
    pending = (await snapshot(env))["delegations"][0]
    assert pending["state"] == "pending" and pending["round_limit"] == 0
    calls = len(factory.contexts)
    paths = service.paths
    await service.close()
    restored = Service(paths, backend_factory=factory)
    factory.service = restored
    await restored.start()
    try:
        restored_env = restored, factory, project, session
        latest = (await snapshot(restored_env))["delegations"][0]
        assert latest["state"] == "interrupted" and latest["team_id"] is None
        assert latest["round_limit"] == 0 and latest["can_resume"]
        assert count_teams(restored) == 1 and len(factory.contexts) == calls
        extra = {} if recovery_limit is None else {"round_limit": recovery_limit}
        result = await restored.dispatch("assistant.resume", ui_params(restored_env,
            latest, "explicit-limit-recovery", **extra))
        assert result["ok"]
        await settle(restored, run["id"])
        child = restored.teams.data.one(result["delegation"]["team_id"])
        assert child["parent_team_id"] == source["team_id"]
        assert child["round_limit"] == (0 if recovery_limit is None else recovery_limit)
        assert child["round_index"] == 2 and child["state"] == "completed"
        assert persisted_team(restored, source["team_id"]) == before
        assert count_teams(restored) == 2
    finally:
        await restored.close()
