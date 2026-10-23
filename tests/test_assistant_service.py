"""Actual service handoff tests with isolated profiles and synthetic providers."""

import asyncio
import json
import sys
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


def member(identifier="coordinator", access="read"):
    return {"id": identifier, "name": identifier, "role": "Independent fixture analysis",
            "instructions": "Inspect synthetic evidence", "access": access}


def delegation(**updates):
    return {"goal": "Verify synthetic workspace evidence", "brief": "The fixture objective is to inspect only synthetic data.",
            "mode": "manual", "members": [member()], **updates}


class Factory:
    def __init__(self):
        self.service = None
        self.origin = None
        self.member = None
        self.contexts = []
        self.calls = []
        self.origin_state = "completed"
        self.close_gate = None
        self.member_gate = None
        self.member_started = asyncio.Event()
        self.delegate_ready = asyncio.Event()
        self.origin_gate = None
        self.followup_contexts = []
        self.followup_started = asyncio.Event()
        self.followup = None

    def __call__(self, name, settings):
        factory = self

        class Backend:
            process = None
            managed = False

            async def run(self, ctx):
                if factory.service.assistant.followups.is_followup(ctx.run_id):
                    factory.followup_contexts.append(ctx)
                    factory.followup_started.set()
                    if factory.followup:
                        return await factory.followup(ctx)
                    await ctx.emit("message.completed", {"content": "Recorded Team result integrated into the original objective."})
                    return {"state": "completed", "outcome": {"final_text": "Synthetic result followup"}}
                factory.contexts.append(ctx)
                self.managed = bool(factory.service.teams.data.link(ctx.run_id))
                if self.managed:
                    self.process = await asyncio.create_subprocess_exec(sys.executable, "-c",
                        "import time; time.sleep(120)", start_new_session=True,
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL)
                    await ctx.provider_started(self.process.pid)
                    factory.member_started.set()
                    if factory.member_gate:
                        await factory.member_gate.wait()
                    if factory.member:
                        return await factory.member(ctx)
                    payload = json.dumps({"decision": "completed", "summary": "Verified synthetic evidence",
                        "members": [], "tasks": []})
                    await ctx.emit("message.completed", {"content": payload})
                    return {"state": "completed", "outcome": {"final_text": payload}}
                if factory.origin:
                    await factory.origin(ctx)
                else:
                    result = await ctx.client_tool_call("delegate-one", "kira.team_delegate", delegation())
                    factory.calls.append(result)
                factory.delegate_ready.set()
                if factory.origin_gate:
                    await factory.origin_gate.wait()
                return {"state": factory.origin_state, "outcome": {"final_text": "Managed handoff requested"}}

            async def close(self):
                if not self.managed and factory.close_gate:
                    await factory.close_gate.wait()
                if self.process and self.process.returncode is None:
                    self.process.terminate()
                    await self.process.wait()

        return Backend()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-assistant-") as profile:
        monkeypatch.setenv("KIRA_HOME", profile)
        factory = Factory()
        service = Service(Paths.from_env(), backend_factory=factory)
        factory.service = service
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Assistant fixture", "root": str(tmp_path),
            "permission": "project_write", "instructions": "Synthetic instruction marker"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex",
            "model": "fixture-selected-model", "settings": {"reasoning_effort": "high", "service_tier": "fixture-tier"}}))["session"]
        yield service, factory, project, session
        for gate in (factory.close_gate, factory.origin_gate, factory.member_gate):
            if gate:
                gate.set()
        await service.close()


async def start(env, mode="execute", request="fixture-request"):
    service, factory, project, session = env
    run = (await service.dispatch("run.start", {"session_id": session["id"], "prompt": "Please organize the synthetic analysis",
        "request_id": request, "mode": mode}))["run"]
    await asyncio.wait_for(factory.delegate_ready.wait(), 5)
    return run


async def settle(service, run_id):
    # Correctness guard, not a latency assertion: backend shutdown and identity
    # verification have their own grace periods and /proc work can be contended
    # by the simultaneously running installed-package checks.
    if run_id in service.tasks:
        await asyncio.wait_for(asyncio.shield(service.tasks[run_id]), 30)
    while service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)
    followups = [task for identifier, task in service.tasks.items() if service.assistant.followups.is_followup(identifier)]
    if followups:
        await asyncio.wait_for(asyncio.gather(*followups), 30)
    await asyncio.sleep(0)


async def snapshot(env):
    service, _, project, session = env
    return await service.dispatch("assistant.snapshot", {"project_id": project["id"], "session_id": session["id"]})


@pytest.mark.asyncio
async def test_handoff_waits_for_cleanup_and_returns_durable_result_to_origin(env):
    service, factory, project, session = env
    factory.close_gate = asyncio.Event()
    run = await start(env)
    assert factory.calls[0]["state"] == "pending"
    assert not service.teams.tasks
    assert (await snapshot(env))["delegations"][0]["state"] == "pending"
    factory.close_gate.set()
    await settle(service, run["id"])
    result = (await snapshot(env))["delegations"][0]
    assert result["state"] == "completed" and result["cleanup_confirmed"]
    assert result["model"] == session["model"] and result["reasoning_effort"] == "high"
    assert result["summary"] == "Verified synthetic evidence"
    team = service.teams.data.one(result["team_id"])
    assert team["settings"]["service_tier"] == "fixture-tier"
    assert all(ctx.session["model"] == session["model"] for ctx in factory.contexts)
    supplied = factory.contexts[-1].supplied_context
    assert "chat_handoff" in supplied and "Synthetic instruction marker" in supplied
    assert "synthetic data" in supplied and session["id"] in supplied
    events = service.store.events(run["id"])["events"]
    returned = [event for event in events if event["type"] == "team.delegation"]
    assert returned[-1]["payload"]["state"] == "completed"
    assert returned[-1]["payload"]["source"] == "kira_service"
    assert service.store.one("runs", run["id"])["state"] == "completed"
    assert len(factory.contexts) == 2  # Original turn and Team; followups are recorded separately.
    assert len(factory.followup_contexts) == 1
    assert result["followup"]["state"] == "completed"
    context = service.assistant.context(run["id"], project, session, "execute")
    assert "Verified synthetic evidence" not in context
    assert result["delegation_id"] in context and result["team_id"] in context
    assert not service.store.reserved(project["root"])


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
async def test_unsuccessful_origin_never_starts_team(env, state):
    service, factory, _, _ = env
    factory.origin_state = state
    run = await start(env)
    await settle(service, run["id"])
    result = (await snapshot(env))["delegations"][0]
    assert result["state"] in {"cancelled", "interrupted"}
    assert not result["team_id"] and not factory.member_started.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plan", "review", "recap"])
async def test_read_only_modes_cannot_delegate_even_read_only_team(env, mode):
    service, factory, _, _ = env
    run = await start(env, mode=mode)
    await settle(service, run["id"])
    assert factory.calls[0]["error"]["code"] == "PERMISSION_DENIED"
    assert not (await snapshot(env))["delegations"]


@pytest.mark.asyncio
async def test_provider_calls_are_durable_deduplicated_and_cannot_choose_identity(env):
    service, factory, _, _ = env
    async def origin(ctx):
        for call_id, args in [("first", delegation()), ("first", delegation()),
            ("first", delegation(goal="Different effect")), ("second", delegation()),
            ("identity", delegation(project_id="foreign"))]:
            factory.calls.append(await ctx.client_tool_call(call_id, "kira.team_delegate", args))
    factory.origin = origin
    run = await start(env)
    await settle(service, run["id"])
    assert factory.calls[0] == factory.calls[1]
    assert factory.calls[2]["error"]["code"] == "CONFLICT"
    assert factory.calls[3]["error"]["code"] == "CONFLICT"
    assert factory.calls[4]["error"]["code"] == "INVALID_ARGUMENT"
    assert len((await snapshot(env))["delegations"]) == 1
    assert await service.assistant.tool_call(run["id"], "first", "kira.team_delegate", delegation()) == factory.calls[0]


@pytest.mark.asyncio
async def test_pending_cancel_does_not_cancel_origin_but_prevents_handoff(env):
    service, factory, project, session = env
    factory.origin_gate = asyncio.Event()
    run = await start(env)
    row = (await snapshot(env))["delegations"][0]
    await service.dispatch("assistant.cancel", {"project_id": project["id"], "session_id": session["id"], "delegation_id": row["id"]})
    assert service.store.one("runs", run["id"])["state"] == "running"
    factory.origin_gate.set()
    await settle(service, run["id"])
    assert (await snapshot(env))["delegations"][0]["state"] == "cancelled"
    assert not service.teams.tasks and not factory.member_started.is_set()


@pytest.mark.asyncio
async def test_scope_rejects_foreign_chat_and_team_member_session(env):
    service, factory, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    foreign = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex", "model": "fixture"}))["session"]
    with pytest.raises(KiraError, match="does not belong"):
        await service.dispatch("assistant.cancel", {"project_id": project["id"], "session_id": foreign["id"], "delegation_id": row["id"]})
    team_session = service.teams.data.members(row["team_id"])[0]["session_id"]
    with pytest.raises(KiraError, match="Ordinary chat"):
        await service.dispatch("assistant.snapshot", {"project_id": project["id"], "session_id": team_session})
    assert "Verified synthetic evidence" not in service.assistant.context(run["id"], project, foreign, "execute")


@pytest.mark.asyncio
async def test_environment_does_not_leak_other_project_and_disabled_backend_is_honest(env, tmp_path_factory):
    service, _, project, session = env
    sibling = tmp_path_factory.mktemp("different-project")
    other = (await service.dispatch("project.create", {"name": "foreign-private-marker", "root": str(sibling)}))["project"]
    data = (await snapshot(env))["environment"]
    assert other["id"] not in json.dumps(data) and "foreign-private-marker" not in json.dumps(data)
    assert data["capabilities"]["managed_team"]
    # Ollama now joins the managed-team providers; DeepSeek Web does not.
    assert not service.assistant.environment(project, {**session, "backend": "deepseek_web"})["capabilities"]["managed_team"]
    local = service.assistant.options("unused", project, {**session, "backend": "deepseek_web"}, "execute")
    assert "You are Kira" in local["assistant_instructions"]
    assert [tool["name"] for tool in local["client_tools"][0]["tools"]] == ["environment"]
    disabled = service.assistant.options("unused", project, {**session, "settings": {"tools": False}}, "execute")
    assert "You are Kira" in disabled["assistant_instructions"] and not disabled.get("client_tools")


@pytest.mark.asyncio
async def test_ui_continuation_is_explicit_deduplicated_and_keeps_origin_context(env):
    service, factory, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    params = {"project_id": project["id"], "session_id": session["id"], "delegation_id": row["id"], "request_id": "resume-once"}
    result = await service.dispatch("assistant.resume", params)
    assert result["ok"]
    assert await service.dispatch("assistant.resume", params) == result
    await settle(service, run["id"])
    updated = (await snapshot(env))["delegations"][0]
    assert updated["team_id"] != row["team_id"]
    assert service.teams.data.one(updated["team_id"])["parent_team_id"] == row["team_id"]
    assert "synthetic data" in factory.contexts[-1].supplied_context
    assert len(factory.contexts) == 3


@pytest.mark.asyncio
async def test_ui_message_has_user_provenance_and_retry_is_exactly_once(env):
    service, factory, project, session = env
    factory.member_gate = asyncio.Event()
    run = await start(env)
    await asyncio.wait_for(factory.member_started.wait(), 5)
    row = (await snapshot(env))["delegations"][0]
    params = {"project_id": project["id"], "session_id": session["id"], "delegation_id": row["id"],
        "request_id": "message-once", "recipient_id": "coordinator", "content": "Check the fixture annotation"}
    result = await service.dispatch("assistant.message", params)
    assert await service.dispatch("assistant.message", params) == result
    assert result["message"]["sender_id"] == "user"
    assert len(service.teams.snapshot(row["team_id"])["messages"]) == 1
    with pytest.raises(KiraError, match="reused"):
        await service.dispatch("assistant.message", {**params, "content": "Different content"})
    factory.member_gate.set()
    await settle(service, run["id"])


@pytest.mark.asyncio
async def test_progress_coalesces_without_per_token_origin_events(env):
    service, factory, _, _ = env
    factory.member_gate = asyncio.Event()
    run = await start(env)
    await asyncio.wait_for(factory.member_started.wait(), 5)
    row = (await snapshot(env))["delegations"][0]
    await asyncio.sleep(0)
    before = service.store.one("runs", run["id"])["last_seq"]
    for _ in range(100):
        service.assistant.on_team_event({"type": "member.activity", "team_id": row["team_id"]})
        service.assistant.on_team_event({"type": "member.state", "team_id": row["team_id"]})
    await asyncio.sleep(0)
    assert service.store.one("runs", run["id"])["last_seq"] == before + 1
    factory.member_gate.set()
    await settle(service, run["id"])


@pytest.mark.asyncio
async def test_restart_marks_pending_interrupted_without_replay(env):
    service, factory, _, _ = env
    factory.origin_gate = asyncio.Event()
    run = await start(env)
    await service.assistant.recover()
    row = (await snapshot(env))["delegations"][0]
    assert row["state"] == "interrupted" and row["team_id"] is None
    factory.origin_gate.set()
    await settle(service, run["id"])
    assert not factory.member_started.is_set()


@pytest.mark.asyncio
async def test_context_and_snapshot_remain_bounded_with_large_brief(env):
    service, factory, project, session = env
    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("bounded", "kira.team_delegate", delegation(brief="x" * 16000, goal="g" * 16000)))
    factory.origin = origin
    run = await start(env)
    await settle(service, run["id"])
    assert factory.calls[0]["ok"]
    assert len(json.dumps(await snapshot(env)).encode()) < 700_000
    assert len(service.assistant.context(run["id"], project, session, "execute").encode()) < 32_000
    assert len(json.dumps(service.assistant.team_context((await snapshot(env))["delegations"][0]["team_id"])).encode()) < 64_000


@pytest.mark.asyncio
async def test_static_identity_never_promotes_project_or_user_data(env):
    service, factory, project, _ = env
    await service.dispatch("project.update", {"project_id": project["id"], "instructions": "UNTRUSTED_PROJECT_CANARY"})
    run = await start(env)
    await settle(service, run["id"])
    ctx = factory.contexts[0]
    assert "UNTRUSTED_PROJECT_CANARY" in ctx.supplied_context
    assert "UNTRUSTED_PROJECT_CANARY" not in ctx.assistant_instructions
    assert project["id"] not in ctx.assistant_instructions
    assert "Kira Desktop" in ctx.assistant_instructions


@pytest.mark.asyncio
async def test_member_environment_cannot_claim_ordinary_assistant_tools(env):
    service, factory, project, _ = env
    run = await start(env)
    await settle(service, run["id"])
    member_context = factory.contexts[-1]
    data = service.assistant.environment(project, member_context.session)
    assert data["role"] == "team_member" and data["capabilities"]["mailbox"]
    assert not data["capabilities"]["managed_team"]
    assert not data["capabilities"]["team_message"]


@pytest.mark.asyncio
async def test_recovered_pending_handoff_requires_explicit_resume(env):
    service, factory, project, session = env
    factory.origin_gate = asyncio.Event()
    run = await start(env)
    await service.assistant.recover()
    factory.origin_gate.set()
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    assert row["state"] == "interrupted" and row["can_resume"] and row["team_id"] is None
    result = await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
        "delegation_id": row["id"], "request_id": "explicit-after-recovery"})
    assert result["ok"]
    await settle(service, run["id"])
    assert (await snapshot(env))["delegations"][0]["state"] == "completed"


@pytest.mark.asyncio
async def test_recovery_links_committed_team_without_replaying_it(env):
    service, factory, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    original_team = row["team_id"]
    service.store.db.execute("UPDATE assistant_delegations SET state='pending',team_id=NULL WHERE id=?", (row["id"],))
    await service.assistant.recover()
    restored = (await snapshot(env))["delegations"][0]
    assert restored["team_id"] == original_team and restored["state"] == "completed"
    assert len(factory.contexts) == 2 and not service.teams.tasks


@pytest.mark.asyncio
async def test_archived_chat_cannot_resume_team(env):
    service, factory, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    await service.dispatch("session.archive", {"project_id": project["id"], "session_id": session["id"], "archived": True})
    with pytest.raises(KiraError, match="Restore this chat"):
        await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
            "delegation_id": row["id"], "request_id": "archived-resume"})
    assert len(factory.contexts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("round_limit", [0, 99])
async def test_delegation_preserves_existing_team_round_limits(env, round_limit):
    service, factory, _, _ = env
    async def origin(ctx):
        factory.calls.append(await ctx.client_tool_call("rounds", "kira.team_delegate", delegation(round_limit=round_limit)))
    factory.origin = origin
    run = await start(env)
    await settle(service, run["id"])
    assert factory.calls[0]["ok"]
    row = (await snapshot(env))["delegations"][0]
    assert service.teams.data.one(row["team_id"])["round_limit"] == round_limit


@pytest.mark.asyncio
async def test_uncertain_origin_cleanup_cannot_dispatch_or_resume(env, monkeypatch):
    service, factory, project, session = env
    factory.close_gate = asyncio.Event()
    run = await start(env)
    original_cleanup = service.processes.cleanup
    async def uncertain(identifier):
        return False if identifier == run["id"] else await original_cleanup(identifier)
    monkeypatch.setattr(service.processes, "cleanup", uncertain)
    factory.close_gate.set()
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    assert row["state"] == "interrupted" and not row["can_resume"]
    assert row["team_id"] is None and not factory.member_started.is_set()
    with pytest.raises(KiraError, match="confirmed cleanup"):
        await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
            "delegation_id": row["id"], "request_id": "uncertain-resume"})


@pytest.mark.asyncio
async def test_settled_model_messages_keep_provenance_and_recipient_isolation_on_resume(env):
    service, factory, project, session = env
    async def initial(ctx):
        factory.calls.append(await ctx.client_tool_call("initial", "kira.team_delegate",
            delegation(members=[member(), member("reader")], concurrency=2)))
    factory.origin = initial
    first = await start(env)
    await settle(service, first["id"])
    row = (await snapshot(env))["delegations"][0]
    async def message_turn(ctx):
        for recipient, marker in [("coordinator", "COORDINATOR_PRIVATE_MARKER"), ("reader", "READER_PRIVATE_CANARY")]:
            factory.calls.append(await ctx.client_tool_call("send-" + recipient, "kira.team_message",
                {"delegation_id": row["id"], "recipient_id": recipient, "content": marker}))
    factory.origin = message_turn
    second = await start(env, request="message-turn")
    await settle(service, second["id"])
    assert all(result["ok"] and result["delivery"] == "recorded_only" and not result["consumed"] for result in factory.calls[1:])
    recorded = service.teams.snapshot(row["team_id"])["messages"]
    assert all(message["sender_id"] == "origin_assistant:" + second["id"] for message in recorded)
    assert all(message["sender_id"] != "user" for message in recorded)
    result = await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
        "delegation_id": row["id"], "request_id": "resume-with-messages"})
    await settle(service, first["id"])
    supplied = factory.contexts[-1].supplied_context
    assert "COORDINATOR_PRIVATE_MARKER" in supplied and "READER_PRIVATE_CANARY" not in supplied
    continuation = service.assistant.team_context(result["delegation"]["team_id"], "reader")
    assert "READER_PRIVATE_CANARY" in json.dumps(continuation)
    assert "COORDINATOR_PRIVATE_MARKER" not in json.dumps(continuation)
    events = service.teams.data.events(result["delegation"]["team_id"], 0, 250)["events"]
    evidence = [event["payload"] for event in events if event["type"] == "team.message.supplied" and event["payload"].get("via") == "continuation_context"]
    assert evidence and evidence[0]["source_team_id"] == row["team_id"]
    assert evidence[0]["understood"] == "unknown"


@pytest.mark.asyncio
async def test_automatic_composition_uses_selected_model_and_bound_context(env):
    service, factory, _, session = env
    async def origin(ctx):
        args = delegation(mode="automatic", max_agents=3)
        args.pop("members")
        factory.calls.append(await ctx.client_tool_call("auto", "kira.team_delegate", args))
    async def coordinate(ctx):
        output = json.dumps({"decision": "completed", "summary": "Automatic synthetic review", "members": [member()], "tasks": []})
        return {"state": "completed", "outcome": {"final_text": output}}
    factory.origin = origin
    factory.member = coordinate
    run = await start(env)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    assert row["state"] == "completed" and row["model"] == session["model"]
    assert service.teams.data.one(row["team_id"])["mode"] == "automatic"
    assert "chat_handoff" in factory.contexts[-1].supplied_context


@pytest.mark.asyncio
async def test_pending_prompt_waits_for_team_reservation_then_receives_result_context(env):
    service, factory, _, session = env
    factory.member_gate = asyncio.Event()
    factory.origin_gate = asyncio.Event()
    first = await start(env)
    await service.dispatch("queue.add", {"session_id": session["id"], "request_id": "queued-after-team", "prompt": "Summarize the recorded team result"})
    observed = []
    async def followup(ctx):
        # Collected for the test body: an assertion here would only fail the queued run.
        observed.append((ctx.supplied_context, await ctx.client_tool_call("inspect-result", "kira.team_status", {})))
    factory.origin = followup
    factory.origin_gate.set()
    await asyncio.wait_for(factory.member_started.wait(), 5)
    assert service.store.db.execute("SELECT status FROM prompt_queue WHERE request_id='queued-after-team'").fetchone()[0] == "pending"
    assert len(factory.contexts) == 2
    factory.member_gate.set()
    await settle(service, first["id"])
    # The queue's one-second poller dispatches the request after the Team releases the project.
    async with asyncio.timeout(30):
        while (row := service.store.db.execute("SELECT status,run_id FROM prompt_queue "
                "WHERE request_id='queued-after-team'").fetchone())[0] in {"pending", "starting", "running"}:
            await asyncio.sleep(.01)
    assert row[0] == "completed"
    assert len(factory.contexts) == 3
    (supplied, result), = observed
    assert "Verified synthetic evidence" not in supplied
    assert result["ok"] and result["delegations"][0]["summary"] == "Verified synthetic evidence"


@pytest.mark.asyncio
async def test_delegation_and_call_rollback_when_durable_event_fails(env):
    import sqlite3
    service, factory, _, _ = env
    async def origin(ctx):
        before = service.store.one("runs", ctx.run_id)["last_seq"]
        service.store.db.execute("CREATE TEMP TRIGGER reject_assistant_event BEFORE INSERT ON events "
            "WHEN NEW.type='team.delegation' BEGIN SELECT RAISE(ABORT, 'fixture event failure'); END")
        try:
            with pytest.raises(sqlite3.IntegrityError, match="fixture event failure"):
                await ctx.client_tool_call("atomic-failure", "kira.team_delegate", delegation())
            assert service.store.one("runs", ctx.run_id)["last_seq"] == before
            assert service.store.db.execute("SELECT COUNT(*) FROM assistant_calls WHERE run_id=?", (ctx.run_id,)).fetchone()[0] == 0
            assert service.store.db.execute("SELECT COUNT(*) FROM assistant_delegations WHERE origin_run_id=?", (ctx.run_id,)).fetchone()[0] == 0
        finally:
            service.store.db.execute("DROP TRIGGER reject_assistant_event")
    factory.origin = origin
    run = await start(env)
    await settle(service, run["id"])
    # The checks above run inside the provider; a failed one would only fail the run.
    assert service.store.one("runs", run["id"])["state"] == "completed"
    assert not (await snapshot(env))["delegations"]


@pytest.mark.asyncio
async def test_result_update_rolls_back_with_event_failure(env):
    import sqlite3
    service, factory, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    summary = (await snapshot(env))["delegations"][0]
    row = service.assistant._one(summary["id"], project["id"], session["id"])
    before = service.store.one("runs", run["id"])["last_seq"]
    service.store.db.execute("CREATE TEMP TRIGGER reject_result_event BEFORE INSERT ON events "
        "WHEN NEW.type='team.delegation' BEGIN SELECT RAISE(ABORT, 'fixture update failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="fixture update failure"):
            service.assistant._update(row, "interrupted", error="must roll back")
        assert service.assistant._one(row["id"], project["id"], session["id"])["state"] == "completed"
        assert service.store.one("runs", run["id"])["last_seq"] == before
    finally:
        service.store.db.execute("DROP TRIGGER reject_result_event")


@pytest.mark.asyncio
async def test_same_ui_request_name_is_scoped_to_origin_delegation(env):
    service, factory, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    first = (await snapshot(env))["delegations"][0]
    params = {"project_id": project["id"], "session_id": session["id"], "delegation_id": first["id"], "request_id": "shared-name"}
    first_resume = await service.dispatch("assistant.resume", params)
    await settle(service, run["id"])
    second_run = await start(env, request="second-origin")
    await settle(service, second_run["id"])
    second = (await snapshot(env))["delegations"][0]
    second_resume = await service.dispatch("assistant.resume", {**params, "delegation_id": second["id"]})
    await settle(service, second_run["id"])
    assert first_resume["ok"] and second_resume["ok"]
    assert first_resume["delegation"]["team_id"] != second_resume["delegation"]["team_id"]


@pytest.mark.asyncio
async def test_snapshot_reports_effective_active_plan_permissions_and_idle_state(env):
    service, factory, project, _ = env
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "trusted_host"})
    factory.origin_gate = asyncio.Event()
    run = await start(env, mode="plan")
    current = (await snapshot(env))["environment"]
    assert current["project"]["permission"] == "trusted_host"
    assert current["effective_permission"] == "read_only"
    assert current["run_id"] == run["id"] and current["mode"] == "plan"
    assert current["execution_state"] == "running"
    assert not current["capabilities"]["managed_team"]
    assert not current["capabilities"]["team_message"]
    factory.origin_gate.set()
    await settle(service, run["id"])
    idle = (await snapshot(env))["environment"]
    assert idle["execution_state"] == "idle" and idle["run_id"] is None
    assert idle["effective_permission"] == "trusted_host" and idle["mode"] == "execute"


@pytest.mark.asyncio
async def test_ui_message_crash_gap_never_replays_after_delegation_changes_team(env, monkeypatch):
    service, _, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    original = (await snapshot(env))["delegations"][0]
    params = {"project_id": project["id"], "session_id": session["id"], "delegation_id": original["id"],
        "request_id": "crash-message", "recipient_id": "coordinator", "content": "ONE_COMMITTED_UI_MESSAGE"}
    finish = service.assistant._finish_call
    def crash_after_message(run_id, call_id, result):
        if call_id == "ui:crash-message":
            raise RuntimeError("Synthetic crash after mailbox commit")
        return finish(run_id, call_id, result)
    monkeypatch.setattr(service.assistant, "_finish_call", crash_after_message)
    with pytest.raises(RuntimeError, match="after mailbox commit"):
        await service.dispatch("assistant.message", params)
    monkeypatch.setattr(service.assistant, "_finish_call", finish)
    assert service.store.db.execute("SELECT COUNT(*) FROM team_messages WHERE content=?", (params["content"],)).fetchone()[0] == 1
    await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
        "delegation_id": original["id"], "request_id": "resume-after-message-crash"})
    await settle(service, run["id"])
    assert (await snapshot(env))["delegations"][0]["team_id"] != original["team_id"]
    retried = await service.dispatch("assistant.message", params)
    assert not retried["ok"] and retried["delivery"] == "unknown"
    assert retried["target_team_id"] == original["team_id"]
    recorded = service.store.all("SELECT team_id FROM team_messages WHERE content=?", (params["content"],))
    assert recorded == [{"team_id": original["team_id"]}]
    with pytest.raises(KiraError, match="reused"):
        await service.dispatch("assistant.message", {**params, "content": "DIFFERENT_MESSAGE"})


@pytest.mark.asyncio
async def test_model_message_crash_gap_retains_target_and_never_replays(env, monkeypatch):
    service, factory, project, session = env
    first = await start(env)
    await settle(service, first["id"])
    original = (await snapshot(env))["delegations"][0]
    args = {"delegation_id": original["id"], "recipient_id": "coordinator", "content": "ONE_COMMITTED_MODEL_MESSAGE"}
    finish = service.assistant._finish_call
    async def origin(ctx):
        def crash_after_message(run_id, call_id, result):
            if call_id == "model-crash":
                raise RuntimeError("Synthetic model post-commit crash")
            return finish(run_id, call_id, result)
        monkeypatch.setattr(service.assistant, "_finish_call", crash_after_message)
        with pytest.raises(RuntimeError, match="post-commit crash"):
            await ctx.client_tool_call("model-crash", "kira.team_message", args)
        monkeypatch.setattr(service.assistant, "_finish_call", finish)
        factory.calls.append(await ctx.client_tool_call("model-crash", "kira.team_message", args))
    factory.origin = origin
    second = await start(env, request="model-message-crash-turn")
    await settle(service, second["id"])
    assert service.store.one("runs", second["id"])["state"] == "completed"
    assert factory.calls[-1]["delivery"] == "unknown"
    await service.dispatch("assistant.resume", {"project_id": project["id"], "session_id": session["id"],
        "delegation_id": original["id"], "request_id": "resume-after-model-message-crash"})
    await settle(service, first["id"])
    retried = await service.assistant.tool_call(second["id"], "model-crash", "kira.team_message", args)
    assert retried["target_team_id"] == original["team_id"]
    recorded = service.store.all("SELECT team_id FROM team_messages WHERE content=?", (args["content"],))
    assert recorded == [{"team_id": original["team_id"]}]


@pytest.mark.asyncio
async def test_explicit_status_exposes_automatic_roster_assignments_and_addressable_members(env):
    service, factory, _, _ = env
    async def delegate_auto(ctx):
        args = delegation(mode="automatic", max_agents=3)
        args.pop("members")
        factory.calls.append(await ctx.client_tool_call("automatic-team", "kira.team_delegate", args))
    async def managed(ctx):
        link = service.teams.data.link(ctx.run_id)
        team = service.teams.data.one(link["team_id"])
        if ctx.output_schema:
            first = team["round_index"] == 1
            output = json.dumps({"decision": "continue" if first else "completed", "summary": "Automatic evidence review",
                "members": [member(), {**member("evidence_analyst"), "role": "Analyze fixture evidence"}] if first else [],
                "tasks": [{"id": "inspect_evidence", "member_id": "evidence_analyst", "title": "Inspect fixture evidence",
                    "instructions": "Read the synthetic evidence", "access": "read", "depends_on": []}] if first else []})
            return {"state": "completed", "outcome": {"final_text": output}}
        sent = await ctx.client_tool_call("worker-evidence", "team.send_message",
            {"recipient_id": "coordinator", "content": "Verified synthetic evidence marker"})
        assert sent["ok"]
        return {"state": "completed", "outcome": {"final_text": "Independent evidence result"}}
    factory.origin = delegate_auto
    factory.member = managed
    first = await start(env)
    await settle(service, first["id"])
    delegation_id = (await snapshot(env))["delegations"][0]["id"]
    results = []
    async def inspect(ctx):
        detailed = await ctx.client_tool_call("details", "kira.team_status", {"delegation_id": delegation_id})
        results.append(detailed)
        assert detailed["ok"]
        entry = detailed["delegations"][0]
        analyst = next(item for item in entry["members"] if item["role"] == "Analyze fixture evidence")
        assert analyst["id"] == "evidence_analyst" and analyst["state"] == "completed"
        assert analyst["last_output"] == "Independent evidence result"
        assert entry["steps"][0]["title"] == "Inspect fixture evidence"
        assert entry["steps"][0]["state"] == "completed"
        assert entry["messages"][0]["content"] == "Verified synthetic evidence marker"
        message = await ctx.client_tool_call("address-known-member", "kira.team_message",
            {"delegation_id": delegation_id, "recipient_id": analyst["id"], "content": "Please preserve the evidence references"})
        assert message["ok"] and message["message"]["recipient_id"] == analyst["id"]
        cheap = await ctx.client_tool_call("cheap-status", "kira.team_status", {})
        assert not any(key in cheap["delegations"][0] for key in ("members", "steps", "messages"))
    factory.origin = inspect
    second = await start(env, request="inspect-automatic-roster")
    await settle(service, second["id"])
    assert results[0]["ok"]
    assert service.store.one("runs", second["id"])["state"] == "completed"


@pytest.mark.asyncio
async def test_detailed_status_rejects_delegation_from_another_chat(env):
    service, factory, project, _ = env
    first = await start(env)
    await settle(service, first["id"])
    delegation_id = (await snapshot(env))["delegations"][0]["id"]
    foreign = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex", "model": "fixture-model"}))["session"]
    results = []
    async def inspect(ctx):
        results.append(await ctx.client_tool_call("foreign-details", "kira.team_status", {"delegation_id": delegation_id}))
    factory.origin = inspect
    second = await start((service, factory, project, foreign), request="foreign-status")
    await settle(service, second["id"])
    assert results[0] == {"ok": False, "error": {"code": "NOT_FOUND", "message": "Delegation does not belong to this chat"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("expanding", ["\x01", "😀"])
async def test_explicit_status_bounds_serialized_json_and_reports_omissions(env, expanding):
    from kira.service.storage import encode, new_id, now
    service, factory, _, _ = env
    async def roster(ctx):
        factory.calls.append(await ctx.client_tool_call("large-roster", "kira.team_delegate",
            delegation(members=[member()] + [member("reader" + str(index)) for index in range(11)])))
    factory.origin = roster
    first = await start(env)
    await settle(service, first["id"])
    original = (await snapshot(env))["delegations"][0]
    team_id = original["team_id"]
    service.store.db.execute("UPDATE team_members SET name=?,role=?,last_output=? WHERE team_id=?",
        (expanding * 25, expanding * 100, expanding * 16000, team_id))
    service.store.db.execute("UPDATE team_runs SET summary=?,error=? WHERE id=?", (expanding * 16000, expanding * 16000, team_id))
    for index in range(30):
        service.store.db.execute("INSERT INTO team_steps(team_id,round_index,id,member_id,title,instructions,access,depends_on,state,error,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (team_id, 1, "step" + str(index), "coordinator", expanding * 100,
            "Fixture task", "read", "[]", "failed", expanding * 16000, now(), now()))
    for index in range(12):
        service.store.db.execute("INSERT INTO team_messages VALUES (?,?,?,?,?,?,?,?)",
            (new_id(), team_id, 1000 + index, "user", "User", "coordinator", expanding * 2000, now()))
    results = []
    async def inspect(ctx):
        results.append(await ctx.client_tool_call("bounded-details", "kira.team_status", {"delegation_id": original["id"]}))
    factory.origin = inspect
    second = await start(env, request="bounded-status")
    await settle(service, second["id"])
    payload = results[0]
    assert payload["ok"] and len(encode(payload).encode()) <= 64_000
    details = payload["delegations"][0]
    assert len(details["members"]) == 12 and len(details["steps"]) == 24 and len(details["messages"]) == 8
    assert details["omitted"] == {"members": 0, "steps": 6, "messages": 4}
    assert details["has_more"] and details["truncated"]
    assert details["members"][0]["last_output_truncated"]
    assert details["steps"][0]["error_truncated"]
    assert details["messages"][0]["content_truncated"]
    assert service.store.one("runs", second["id"])["state"] == "completed"
