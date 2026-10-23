"""Run-bound continuation uses real service scheduling and synthetic providers."""

import asyncio

import pytest

from kira.domain.assistant import ASSISTANT_TOOL_NAMES, assistant_tool_specs
from kira.domain.contracts import KiraError
from kira.service.server import Service
from kira.service.storage import encode
from test_assistant_service import (
    delegation,
    env as env,
    member,
    settle,
    snapshot,
    start,
)


async def initial(env):
    service, factory, _, _ = env
    factory.delegate_ready.clear()
    run = await start(env)
    await settle(service, run["id"])
    return run, (await snapshot(env))["delegations"][0]


async def continue_turn(env, source, *, mode="execute", instruction="Inspect the additional fixture evidence"):
    _, factory, _, _ = env

    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("continue-team", "kira.team_resume",
            {"delegation_id": source["id"], "instruction": instruction}))

    factory.origin = origin
    factory.delegate_ready.clear()
    return await start(env, mode=mode, request="continuation-turn")


def count_teams(service):
    return service.store.db.execute("SELECT COUNT(*) FROM team_runs").fetchone()[0]


def raw_delegation(service, identifier):
    return tuple(service.store.db.execute("SELECT * FROM assistant_delegations WHERE id=?", (identifier,)).fetchone())


def test_resume_schema_is_small_and_has_no_execution_identity_arguments():
    assert "kira.team_resume" in ASSISTANT_TOOL_NAMES
    tool = next(tool for tool in assistant_tool_specs()[0]["tools"] if tool["name"] == "team_resume")
    assert set(tool["inputSchema"]["properties"]) == {"delegation_id", "instruction", "round_limit"}
    assert tool["inputSchema"]["required"] == ["delegation_id"]
    assert tool["inputSchema"]["additionalProperties"] is False
    assert len(encode(tool).encode()) < 650


@pytest.mark.asyncio
async def test_model_continuation_waits_for_cleanup_and_keeps_previous_card(env):
    service, factory, project, session = env
    first, source = await initial(env)
    original = raw_delegation(service, source["id"])
    old_events = service.store.one("runs", first["id"])["last_seq"]
    factory.close_gate = asyncio.Event()
    run = await continue_turn(env, source)
    accepted = factory.calls[-1]
    assert accepted["ok"] and accepted["state"] == "pending" and accepted["action"] == "resume"
    assert accepted["source_team_id"] == source["team_id"]
    assert accepted["source_delegation_id"] == source["id"]
    assert accepted["delegation_id"] != source["id"]
    assert count_teams(service) == 1
    assert not (await snapshot(env))["delegations"][1]["can_resume"]
    factory.close_gate.set()
    await settle(service, run["id"])
    result = (await snapshot(env))["delegations"][0]
    child = service.teams.data.one(result["team_id"])
    assert result["state"] == "completed" and result["cleanup_confirmed"]
    assert child["parent_team_id"] == source["team_id"]
    assert child["model"] == session["model"]
    assert child["settings"]["reasoning_effort"] == "high"
    assert child["settings"]["service_tier"] == "fixture-tier"
    assert child["goal"] == service.teams.data.one(source["team_id"])["goal"]
    assert raw_delegation(service, source["id"]) == original
    assert service.store.one("runs", first["id"])["last_seq"] == old_events
    returned = [event for event in service.store.events(run["id"])["events"] if event["type"] == "team.delegation"]
    assert returned[-1]["payload"]["team_id"] == child["id"]
    assert returned[-1]["payload"]["source_team_id"] == source["team_id"]
    assert not service.store.reserved(project["root"])


@pytest.mark.asyncio
async def test_continuation_inherits_real_worker_results_and_model_instruction_provenance(env):
    service, factory, _, _ = env

    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("delegate-with-reader", "kira.team_delegate",
            delegation(members=[member(), member("reader")], concurrency=2)))

    async def work(ctx):
        link = service.teams.data.link(ctx.run_id)
        team = service.teams.data.one(link["team_id"])
        if not link["coordinator"]:
            output = "WORKER_VERIFIED_SYNTHETIC_RESULT"
        elif team["round_index"] == 1 and not team["parent_team_id"]:
            output = encode({"decision": "continue", "summary": "Dispatch the reader", "members": [],
                "tasks": [{"id": "inspect", "title": "Inspect fixture", "member_id": "reader",
                    "instructions": "Read synthetic evidence", "access": "read", "depends_on": []}]})
        else:
            output = encode({"decision": "completed", "summary": "Verified worker result reviewed",
                "members": [], "tasks": []})
        await ctx.emit("message.completed", {"content": output})
        return {"state": "completed", "outcome": {"final_text": output}}

    factory.origin, factory.member = origin, work
    _, source = await initial(env)
    assert source["completed_steps"] == 1
    run = await continue_turn(env, source, instruction="REVIEW_NEW_SYNTHETIC_EVIDENCE")
    await settle(service, run["id"])
    result = (await snapshot(env))["delegations"][0]
    assert result["state"] == "completed"
    supplied = factory.contexts[-1].supplied_context
    assert "WORKER_VERIFIED_SYNTHETIC_RESULT" in supplied
    assert "REVIEW_NEW_SYNTHETIC_EVIDENCE" in supplied
    assert "synthetic data" in supplied and "continuation_context" in supplied
    messages = service.teams.snapshot(result["team_id"])["messages"]
    instruction = next(message for message in messages if "REVIEW_NEW_SYNTHETIC_EVIDENCE" in message["content"])
    assert instruction["sender_id"] == "origin_assistant:" + run["id"]
    assert instruction["sender_name"] == "Kira assistant"


@pytest.mark.asyncio
async def test_duplicate_call_conflict_and_ui_cannot_fork_pending_model_continuation(env):
    service, factory, project, session = env
    _, source = await initial(env)
    factory.origin_gate = asyncio.Event()

    async def origin(ctx):
        args = {"delegation_id": source["id"]}
        first = await ctx.client_tool_call("resume", "kira.team_resume", args)
        factory.calls.append(first)
        assert await ctx.client_tool_call("resume", "kira.team_resume", args) == first
        reused = await ctx.client_tool_call("resume", "kira.team_resume", {**args, "instruction": "different"})
        another = await ctx.client_tool_call("second-resume", "kira.team_resume", args)
        delegated = await ctx.client_tool_call("also-delegate", "kira.team_delegate", delegation())
        assert all(result["error"]["code"] == "CONFLICT" for result in (reused, another, delegated))

    factory.origin = origin
    factory.delegate_ready.clear()
    run = await start(env, request="deduplicated-continuation")
    with pytest.raises(KiraError, match="confirmed cleanup"):
        await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
            "delegation_id": source["id"], "request_id": "competing-ui"})
    assert count_teams(service) == 1
    factory.origin_gate.set()
    await settle(service, run["id"])
    assert count_teams(service) == 2
    assert service.store.db.execute("SELECT COUNT(*) FROM assistant_delegations WHERE origin_run_id=?",
        (run["id"],)).fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plan", "review", "recap"])
async def test_read_only_turn_cannot_continue_team(env, mode):
    service, factory, _, _ = env
    _, source = await initial(env)
    run = await continue_turn(env, source, mode=mode)
    await settle(service, run["id"])
    assert factory.calls[-1]["error"]["code"] == "PERMISSION_DENIED"
    assert count_teams(service) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
async def test_unsuccessful_origin_does_not_launch_continuation(env, state):
    service, factory, _, _ = env
    _, source = await initial(env)
    factory.origin_state = state
    run = await continue_turn(env, source)
    await settle(service, run["id"])
    assert factory.calls[-1]["ok"]
    result = (await snapshot(env))["delegations"][0]
    assert result["state"] in {"cancelled", "interrupted"} and result["team_id"] is None
    assert count_teams(service) == 1
    assert (await snapshot(env))["delegations"][1]["can_resume"]


@pytest.mark.asyncio
async def test_cancel_pending_continuation_releases_source_claim(env):
    service, factory, project, session = env
    _, source = await initial(env)
    factory.origin_gate = asyncio.Event()
    run = await continue_turn(env, source)
    await service.dispatch("assistant.cancel", {"project_id": project["id"], "session_id": session["id"],
        "delegation_id": factory.calls[-1]["delegation_id"]})
    factory.origin_gate.set()
    await settle(service, run["id"])
    assert count_teams(service) == 1
    assert (await snapshot(env))["delegations"][1]["can_resume"]


@pytest.mark.asyncio
async def test_completed_continuation_must_be_resumed_from_latest_delegation(env):
    service, factory, _, _ = env
    _, source = await initial(env)
    second = await continue_turn(env, source)
    await settle(service, second["id"])
    latest = (await snapshot(env))["delegations"][0]

    async def origin(ctx):
        rejected = await ctx.client_tool_call("obsolete-source", "kira.team_resume", {"delegation_id": source["id"]})
        assert rejected["error"]["code"] == "CONFLICT"
        factory.calls.append(await ctx.client_tool_call("latest-source", "kira.team_resume", {"delegation_id": latest["id"]}))

    factory.origin = origin
    factory.delegate_ready.clear()
    third = await start(env, request="latest-continuation")
    await settle(service, third["id"])
    result = (await snapshot(env))["delegations"][0]
    assert factory.calls[-1]["ok"]
    assert service.teams.data.one(result["team_id"])["parent_team_id"] == latest["team_id"]
    assert count_teams(service) == 3
    assert service.store.db.execute("SELECT COUNT(*) FROM team_runs WHERE parent_team_id=?",
        (source["team_id"],)).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_uncertain_origin_cleanup_never_dispatches_child(env, monkeypatch):
    service, factory, _, _ = env
    _, source = await initial(env)
    factory.close_gate = asyncio.Event()
    run = await continue_turn(env, source)
    cleanup = service.processes.cleanup

    async def uncertain(run_id):
        return False if run_id == run["id"] else await cleanup(run_id)

    monkeypatch.setattr(service.processes, "cleanup", uncertain)
    factory.close_gate.set()
    await settle(service, run["id"])
    pending = (await snapshot(env))["delegations"][0]
    assert pending["state"] == "interrupted" and pending["team_id"] is None
    assert not pending["cleanup_confirmed"]
    assert count_teams(service) == 1


@pytest.mark.asyncio
async def test_reject_foreign_chat_source_and_team_identifier(env):
    service, factory, project, _ = env
    _, source = await initial(env)
    other = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex",
        "model": "fixture-selected-model", "settings": {"reasoning_effort": "high"}}))["session"]

    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("foreign", "kira.team_resume", {"delegation_id": source["id"]}))
        factory.calls.append(await ctx.client_tool_call("team-id", "kira.team_resume", {"delegation_id": source["team_id"]}))

    factory.origin = origin
    factory.delegate_ready.clear()
    run = await start((service, factory, project, other), request="other-chat")
    await settle(service, run["id"])
    assert all(call["error"]["code"] == "NOT_FOUND" for call in factory.calls[-2:])
    assert count_teams(service) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["model", "effort", "generation", "permission", "root", "account"])
async def test_source_execution_identity_must_match_current_chat(env, change, tmp_path):
    service, factory, project, session = env
    _, source = await initial(env)
    if change in {"model", "effort"}:
        await service.dispatch("session.configure", {"project_id": project["id"], "session_id": session["id"],
            "model": "changed-model" if change == "model" else session["model"],
            "reasoning_effort": "low" if change == "effort" else "high"})
    elif change == "generation":
        service.store.db.execute("INSERT INTO session_provider_generations SELECT session_id,generation+1,backend,"
            "model,settings,backend_ref,created_at,NULL FROM session_provider_generations WHERE session_id=?",
            (session["id"],))
    elif change == "permission":
        await service.dispatch("project.update", {"project_id": project["id"], "permission": "read_only"})
    elif change == "root":
        sibling = tmp_path / "another-root"
        sibling.mkdir()
        service.store.db.execute("UPDATE projects SET root=? WHERE id=?", (str(sibling), project["id"]))
    else:
        service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?",
            (encode({**session["settings"], "openai_account_id": "other-fixture-account"}), session["id"]))
    unavailable = (await snapshot(env))["delegations"][0]
    assert unavailable["cleanup_confirmed"] and not unavailable["can_resume"]
    assert "changed" in unavailable["resume_unavailable_reason"]
    assert count_teams(service) == 1
    run = await continue_turn(env, source)
    await settle(service, run["id"])
    assert factory.calls[-1]["error"]["code"] == "CONFLICT"
    assert count_teams(service) == 1


@pytest.mark.asyncio
async def test_summary_reenables_resume_when_original_selection_is_restored(env):
    service, _, project, session = env
    _, source = await initial(env)
    assert source["can_resume"] and "resume_unavailable_reason" not in source
    params = {"project_id": project["id"], "session_id": session["id"]}
    await service.dispatch("session.configure", {**params, "model": "other-selected-model", "reasoning_effort": "low"})
    changed = (await snapshot(env))["delegations"][0]
    assert not changed["can_resume"] and "changed" in changed["resume_unavailable_reason"]
    assert count_teams(service) == 1
    await service.dispatch("session.configure", {**params, "model": session["model"], "reasoning_effort": "high"})
    restored = (await snapshot(env))["delegations"][0]
    assert restored["can_resume"] and "resume_unavailable_reason" not in restored
    continued = await service.dispatch("assistant.resume", {**params, "delegation_id": source["id"],
        "request_id": "resume-restored-selection"})
    assert continued["ok"]
    await settle(service, source["origin_run_id"])
    assert service.teams.data.one(continued["delegation"]["team_id"])["parent_team_id"] == source["team_id"]


@pytest.mark.asyncio
async def test_summary_keeps_no_team_interrupted_gui_recovery_available(env):
    service, factory, project, session = env
    factory.origin_gate = asyncio.Event()
    run = await start(env)
    await service.assistant.recover()
    blocked = (await snapshot(env))["delegations"][0]
    assert not blocked["can_resume"] and "cleanup" in blocked["resume_unavailable_reason"]
    factory.origin_gate.set()
    await settle(service, run["id"])
    available = (await snapshot(env))["delegations"][0]
    assert available["state"] == "interrupted" and available["team_id"] is None
    assert available["can_resume"] and "resume_unavailable_reason" not in available
    result = await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
        "delegation_id": available["id"], "request_id": "recover-unstarted-original"})
    assert result["ok"]
    await settle(service, run["id"])
    assert count_teams(service) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["archive", "current_binding", "source_cleanup"])
async def test_run_bound_admission_checks_latest_state(env, change):
    service, factory, _, session = env
    _, source = await initial(env)

    async def origin(ctx):
        if change == "archive":
            service.store.db.execute("INSERT INTO session_workbench(session_id,archived,created_at,updated_at) "
                "VALUES (?,1,?,?) ON CONFLICT(session_id) DO UPDATE SET archived=1",
                (session["id"], session["created_at"], session["created_at"]))
        elif change == "current_binding":
            service.store.db.execute("UPDATE sessions SET model='changed-during-turn' WHERE id=?", (session["id"],))
        else:
            service.store.db.execute("UPDATE run_cleanup SET state='unknown' WHERE run_id IN "
                "(SELECT run_id FROM team_run_links WHERE team_id=?)", (source["team_id"],))
        factory.calls.append(await ctx.client_tool_call("stale", "kira.team_resume", {"delegation_id": source["id"]}))

    factory.origin = origin
    factory.delegate_ready.clear()
    run = await start(env, request="changed-admission")
    await settle(service, run["id"])
    expected = {"archive": "ARCHIVED", "current_binding": "CONFLICT", "source_cleanup": "BUSY"}[change]
    assert factory.calls[-1]["error"]["code"] == expected
    assert count_teams(service) == 1


@pytest.mark.asyncio
async def test_final_resume_guard_rejects_scope_change_across_cleanup_await(env, monkeypatch):
    service, factory, _, session = env
    _, source = await initial(env)
    release = service.teams.release_if_clean

    async def changed(team_id):
        result = await release(team_id)
        if team_id == source["team_id"]:
            service.store.db.execute("UPDATE sessions SET model='changed-after-validation' WHERE id=?", (session["id"],))
        return result

    monkeypatch.setattr(service.teams, "release_if_clean", changed)
    run = await continue_turn(env, source)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    assert factory.calls[-1]["ok"] and row["state"] == "failed" and not row["team_id"]
    assert "changed" in row["error"]
    assert count_teams(service) == 1


@pytest.mark.asyncio
async def test_late_pending_cancel_prevents_dispatch_after_await(env, monkeypatch):
    service, factory, project, session = env
    _, source = await initial(env)
    release = service.teams.release_if_clean

    async def cancelled(team_id):
        result = await release(team_id)
        if team_id == source["team_id"]:
            await service.dispatch("assistant.cancel", {"project_id": project["id"], "session_id": session["id"],
                "delegation_id": factory.calls[-1]["delegation_id"]})
        return result

    monkeypatch.setattr(service.teams, "release_if_clean", cancelled)
    run = await continue_turn(env, source)
    await settle(service, run["id"])
    assert count_teams(service) == 1
    result = (await snapshot(env))["delegations"][0]
    assert result["team_id"] is None and result["state"] == "cancelled"


@pytest.mark.asyncio
async def test_recovery_reconciles_committed_child_without_replay(env, monkeypatch):
    service, factory, _, _ = env
    _, source = await initial(env)
    dispatch = service.teams.dispatch

    async def interrupted(method, params, client=None, **kwargs):
        result = await dispatch(method, params, client, **kwargs)
        if method == "team.resume":
            raise RuntimeError("Synthetic crash after the child transaction")
        return result

    monkeypatch.setattr(service.teams, "dispatch", interrupted)
    run = await continue_turn(env, source)
    await settle(service, run["id"])
    before = (await snapshot(env))["delegations"][0]
    assert before["state"] == "interrupted" and before["team_id"] is None
    assert count_teams(service) == 2
    count = len(factory.contexts)
    await service.assistant.recover()
    after = (await snapshot(env))["delegations"][0]
    assert after["team_id"] and after["state"] == "completed"
    assert service.teams.data.one(after["team_id"])["parent_team_id"] == source["team_id"]
    await service.assistant.recover()
    assert count_teams(service) == 2 and len(factory.contexts) == count


@pytest.mark.asyncio
async def test_unknown_dispatch_without_effect_never_replays_or_allows_duplicate(env, monkeypatch):
    service, factory, project, session = env
    _, source = await initial(env)
    dispatch = service.teams.dispatch

    async def interrupted(method, params, client=None, **kwargs):
        if method == "team.resume":
            raise RuntimeError("Synthetic missing dispatch confirmation")
        return await dispatch(method, params, client, **kwargs)

    monkeypatch.setattr(service.teams, "dispatch", interrupted)
    run = await continue_turn(env, source)
    await settle(service, run["id"])
    await service.assistant.recover()
    assert count_teams(service) == 1
    rows = (await snapshot(env))["delegations"]
    assert rows[0]["state"] == "interrupted" and not rows[0]["can_resume"]
    assert not rows[1]["can_resume"]
    with pytest.raises(KiraError, match="confirmed cleanup"):
        await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
            "delegation_id": source["id"], "request_id": "do-not-replay-unknown"})


@pytest.mark.asyncio
async def test_service_restart_does_not_replay_pending_continuation(env, monkeypatch):
    service, factory, project, session = env
    _, source = await initial(env)
    after_run = service.assistant.after_run

    async def hold(run_id, cleaned):
        return None

    monkeypatch.setattr(service.assistant, "after_run", hold)
    run = await continue_turn(env, source)
    await settle(service, run["id"])
    assert (await snapshot(env))["delegations"][0]["state"] == "pending"
    monkeypatch.setattr(service.assistant, "after_run", after_run)
    paths = service.paths
    await service.close()
    restored = Service(paths, backend_factory=factory)
    factory.service = restored
    await restored.start()
    try:
        rows = (await restored.dispatch("assistant.snapshot", {"project_id": project["id"], "session_id": session["id"]}))["delegations"]
        assert rows[0]["state"] == "interrupted" and rows[0]["team_id"] is None
        assert count_teams(restored) == 1
        result = await restored.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
            "delegation_id": rows[0]["id"], "request_id": "explicit-after-restart"})
        assert result["ok"]
        await settle(restored, run["id"])
        assert restored.teams.data.one(result["delegation"]["team_id"])["parent_team_id"] == source["team_id"]
        assert count_teams(restored) == 2
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_direct_continuation_keeps_bound_account(env):
    service, factory, project, _ = env
    session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
        "model": "fixture-direct-model", "settings": {"reasoning_effort": "high",
            "openai_account_id": "fixture-selected-account"}}))["session"]
    direct_env = service, factory, project, session
    _, source = await initial(direct_env)
    run = await continue_turn(direct_env, source)
    await settle(service, run["id"])
    row = (await snapshot(direct_env))["delegations"][0]
    child = service.teams.data.one(row["team_id"])
    assert child["parent_team_id"] == source["team_id"]
    assert child["backend"] == "openai_chatgpt" and child["model"] == "fixture-direct-model"
    assert child["settings"]["reasoning_effort"] == "high"
    assert child["settings"]["openai_account_id"] == "fixture-selected-account"
