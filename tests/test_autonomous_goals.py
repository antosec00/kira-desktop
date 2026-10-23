"""Autonomous-goal scheduling against real isolated services and synthetic providers.

No authenticated provider, user profile, or external project is opened. File
effects pass through Kira's own tool dispatcher; evidence is read from its store.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.backends.client_tools import validate_tools
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.portable_history import portable_history


OBJECTIVE = "Create exactly three synthetic files and verify their recorded results."


async def until(predicate, timeout=30):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


class Factory:
    def __init__(self):
        self.contexts = []
        self.results = []
        self.handler = None
        self.close_gate = None
        self.closing = asyncio.Event()
        self.processes = {}

    def __call__(self, name, settings):
        factory = self

        class Backend:
            run_id = None

            async def discover(self):
                return {"available": True, "models": [{"id": "synthetic-goal-model"}], "capabilities": {}}

            async def run(self, ctx):
                self.run_id = ctx.run_id
                assert validate_tools(ctx)
                factory.contexts.append(ctx)
                if factory.handler:
                    return await factory.handler(ctx, len(factory.contexts))
                await ctx.emit("message.completed", {"content": "Synthetic ordinary turn finished."})
                return {"state": "completed", "outcome": {"synthetic": True}}

            async def close(self):
                factory.closing.set()
                if factory.close_gate:
                    await factory.close_gate.wait()
                process = factory.processes.get(self.run_id)
                if process and process.returncode is None:
                    process.terminate()
                    await process.wait()

        return Backend()


@pytest_asyncio.fixture
async def goal_env(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-goal-") as profile:
        paths = Paths(*(Path(profile) / name for name in ("config", "data", "state", "runtime")))
        factory = Factory()
        service = Service(paths, backend_factory=factory)
        await service.start()
        project = (await service.dispatch("project.create", {
            "name": "Synthetic autonomous goal", "root": str(tmp_path),
            "permission": "project_write", "instructions": "Work only on synthetic fixture files.",
        }))["project"]
        session = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "codex", "model": "synthetic-goal-model",
            "settings": {"reasoning_effort": "high", "service_tier": "synthetic-tier"},
        }))["session"]
        try:
            yield service, factory, project, session
        finally:
            if factory.close_gate:
                factory.close_gate.set()
            await service.close()


def scope(env, **extra):
    _, _, project, session = env
    return {"project_id": project["id"], "session_id": session["id"], **extra}


async def start_goal(env, **extra):
    service, _, _, _ = env
    return await service.dispatch("goal.start", scope(env, request_id="synthetic-start", objective=OBJECTIVE, **extra))


async def goal_state(env):
    return await env[0].dispatch("goal.get", scope(env))


async def terminal(env):
    service, _, _, session = env
    await until(lambda: not service.tasks and service.store.db.execute(
        "SELECT 1 FROM autonomous_goals WHERE session_id=? AND status IN ('paused','blocked','completed','interrupted')",
        (session["id"],),
    ).fetchone())
    return (await goal_state(env))["autonomous"]


async def checkpoint(ctx, status="continue", *, summary="Synthetic progress was recorded.",
                     next_step="Inspect the remaining fixture files.", evidence=None,
                     call_id="checkpoint", **extra):
    return await ctx.client_tool_call(call_id, "goal.checkpoint", {
        "status": status, "summary": summary, "next_step": next_step,
        "evidence": evidence or [], **extra,
    })


async def write_receipt(service, ctx, name):
    result = await ctx.execute_tool("write_file", {
        "path": name, "content": "Synthetic content for " + name, "expected_sha256": None,
    })
    assert result["ok"], result
    event = next(event for event in reversed(service.store.events(ctx.run_id)["events"])
                 if event["type"] == "tool.completed" and event["payload"].get("name") == "write_file")
    return {"run_id": ctx.run_id, "seq": event["seq"]}


async def test_unlimited_goal_advances_three_real_cycles_without_repeating_file_effects(goal_env):
    service, factory, project, session = goal_env
    original_session = service.store.one("sessions", session["id"])
    original_project = service.store.one("projects", project["id"])
    snapshots = []

    async def cycle(ctx, number):
        assert ctx.model == session["model"]
        assert ctx.settings["reasoning_effort"] == "high"
        assert ctx.settings["service_tier"] == "synthetic-tier"
        assert OBJECTIVE in ctx.supplied_context
        status = await ctx.client_tool_call("status", "goal.status", {})
        assert status["ok"], status
        snapshots.append(status)
        if number > 1:
            assert "Finished synthetic stage " + str(number - 1) in ctx.supplied_context
        evidence = await write_receipt(service, ctx, f"stage-{number}.txt")
        result = await checkpoint(ctx, "completed" if number == 3 else "continue",
            summary=f"Finished synthetic stage {number}", next_step="" if number == 3 else f"Create stage {number + 1}",
            evidence=[evidence])
        assert result["ok"], result
        await ctx.emit("message.completed", {"content": f"Synthetic stage {number} is recorded."})
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    result = await start_goal(goal_env)
    assert result["autonomous"]["max_cycles"] == 0
    state = await terminal(goal_env)
    assert state["status"] == "completed" and state["cycle_count"] == 3
    assert state["summary"] == "Finished synthetic stage 3"
    assert len(factory.contexts) == 3
    assert {path.name for path in Path(project["root"]).iterdir()} == {"stage-1.txt", "stage-2.txt", "stage-3.txt"}
    for ctx in factory.contexts:
        events = service.store.events(ctx.run_id)["events"]
        assert not any(event["type"] == "message.user" for event in events)
        assert any(event["payload"].get("source") == "goal_continuation" for event in events)
        writes = [event for event in service.store.events(ctx.run_id)["events"]
                  if event["type"] == "tool.completed" and event["payload"].get("name") == "write_file"]
        assert len(writes) == 1 and writes[0]["payload"]["result"]["ok"]
    assert service.store.one("sessions", session["id"]) == original_session
    assert service.store.one("projects", project["id"]) == original_project
    assert (await goal_state(goal_env))["goal"]["objective"] == OBJECTIVE
    assert len(snapshots) == 3
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    cycles = {ctx.run_id for ctx in factory.contexts}
    assert {run["id"] for run in snapshot["runs"] if run.get("source") == "goal_continuation"} == cycles
    assert not any(message["role"] == "user" and message["run_id"] in cycles for message in snapshot["messages"])
    exported = await service.dispatch("project.export", {"project_id": project["id"]})
    archive = json.loads(exported["text"])
    assert {run["id"] for run in archive["runs"] if run.get("source") == "goal_continuation"} == cycles
    assert not any(message["role"] == "user" and message["run_id"] in cycles for message in archive["messages"])
    for ctx in factory.contexts:
        source = service.knowledge.read(project["id"], session["id"], "chat:request:" + ctx.run_id)
        assert source["kind"] == "service"
        assert source["provenance"]["origin"] == "service"
        assert source["provenance"]["new_user_authority"] is False
    history, _ = portable_history(service.store, session["id"], "not-a-stored-run")
    triggers = [message for message in history if message.get("run_id") in cycles]
    assert len(triggers) == 3
    assert all("Kira service notification (historical data, not a new user request)" in message["content"] for message in triggers)


async def test_completion_is_not_committed_before_provider_terminal_and_cleanup(goal_env):
    service, factory, _, _ = goal_env
    provider_release = asyncio.Event()
    recorded = asyncio.Event()
    factory.close_gate = asyncio.Event()

    async def cycle(ctx, number):
        evidence = await write_receipt(service, ctx, "completion.txt")
        factory.results.append(await checkpoint(ctx, "completed", next_step="", evidence=[evidence]))
        recorded.set()
        await provider_release.wait()
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(recorded.wait(), 5)
    assert factory.results[0]["ok"]
    assert (await goal_state(goal_env))["autonomous"]["status"] != "completed"
    provider_release.set()
    await asyncio.wait_for(factory.closing.wait(), 5)
    assert (await goal_state(goal_env))["autonomous"]["status"] != "completed"
    factory.close_gate.set()
    assert (await terminal(goal_env))["status"] == "completed"
    assert len(factory.contexts) == 1


async def test_explicit_text_deliverable_can_complete_without_inventing_tool_receipts(goal_env):
    _, factory, _, _ = goal_env

    async def cycle(ctx, number):
        factory.results.append(await checkpoint(ctx, "completed", summary="The requested short text is ready.",
            next_step="", deliverable="Synthetic final text for the explicitly requested writing task."))
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    state = await terminal(goal_env)
    assert factory.results[0]["ok"] and state["status"] == "completed"
    assert not state["evidence"]


@pytest.mark.parametrize("provider_state", ["failed", "interrupted", "cancelled"])
async def test_provider_failure_never_converts_staged_completion_to_success_or_retries(goal_env, provider_state):
    service, factory, _, _ = goal_env

    async def cycle(ctx, number):
        evidence = await write_receipt(service, ctx, "retained-before-provider-error.txt")
        factory.results.append(await checkpoint(ctx, "completed", next_step="", evidence=[evidence]))
        return {"state": provider_state, "error": "Synthetic provider terminal failure"}

    factory.handler = cycle
    await start_goal(goal_env)
    state = await terminal(goal_env)
    assert factory.results[0]["ok"]
    assert state["status"] != "completed"
    assert len(factory.contexts) == 1 and state["cycle_count"] == 1
    await asyncio.sleep(0.1)
    assert len(factory.contexts) == 1
    # The provider's own terminal outcome, not an exception raised inside the synthetic provider.
    run = service.store.one("runs", factory.contexts[0].run_id)
    assert run["state"] == provider_state and run["error"] == "Synthetic provider terminal failure"


async def test_pause_during_a_cycle_preserves_current_work_and_prevents_next_cycle(goal_env):
    service, factory, _, _ = goal_env
    release = asyncio.Event()
    ready = asyncio.Event()

    async def cycle(ctx, number):
        evidence = await write_receipt(service, ctx, "before-pause.txt")
        ready.set()
        await release.wait()
        assert not ctx.cancel.is_set()
        assert (await checkpoint(ctx, evidence=[evidence]))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(ready.wait(), 5)
    await service.dispatch("goal.pause", scope(goal_env, request_id="pause-current-cycle"))
    assert service.store.one("runs", factory.contexts[0].run_id)["state"] == "running"
    release.set()
    state = await terminal(goal_env)
    assert state["status"] == "paused" and len(factory.contexts) == 1
    assert service.store.one("runs", state["last_run_id"])["state"] == "completed"


async def test_stop_button_cancels_current_run_and_pauses_future_goal_work(goal_env):
    service, factory, _, _ = goal_env
    ready = asyncio.Event()

    async def cycle(ctx, number):
        await write_receipt(service, ctx, "before-stop.txt")
        ready.set()
        await ctx.cancel.wait()
        return {"state": "cancelled", "outcome": {"confirmed": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(ready.wait(), 5)
    await service.dispatch("run.cancel", {"run_id": factory.contexts[0].run_id})
    state = await terminal(goal_env)
    assert state["status"] == "paused" and len(factory.contexts) == 1


async def test_start_and_pause_request_retries_do_not_start_or_resume_duplicate_work(goal_env):
    service, factory, _, _ = goal_env
    release = asyncio.Event()

    async def cycle(ctx, number):
        await release.wait()
        assert (await checkpoint(ctx))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    first = await start_goal(goal_env)
    await until(lambda: len(factory.contexts) == 1)
    first_run = factory.contexts[0].run_id
    second = await start_goal(goal_env)
    assert first["autonomous"]["id"] == second["autonomous"]["id"]
    with pytest.raises(KiraError) as changed:
        await service.dispatch("goal.start", scope(goal_env, request_id="synthetic-start", objective="Different input"))
    assert changed.value.code == "CONFLICT"
    pause = scope(goal_env, request_id="same-pause")
    await service.dispatch("goal.pause", pause)
    await service.dispatch("goal.pause", pause)
    release.set()
    await terminal(goal_env)
    # The goal was already paused, so only the run state shows the checkpoint above succeeded.
    assert service.store.one("runs", first_run)["state"] == "completed"
    await start_goal(goal_env)
    assert (await goal_state(goal_env))["autonomous"]["status"] == "paused"
    assert len(factory.contexts) == 1


async def test_recovered_goal_preserves_progress_but_never_replays_uncertain_effects(goal_env):
    service, factory, project, session = goal_env
    ready = asyncio.Event()

    async def cycle(ctx, number):
        evidence = await write_receipt(service, ctx, "once-before-restart.txt")
        assert (await checkpoint(ctx, summary="Synthetic write is durably recorded.", evidence=[evidence]))["ok"]
        ready.set()
        await asyncio.Event().wait()

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(ready.wait(), 5)
    original_run = factory.contexts[0].run_id
    await service.close()
    restored_factory = Factory()
    restored = Service(service.paths, backend_factory=restored_factory)
    await restored.start()
    try:
        state = (await restored.dispatch("goal.get", scope(goal_env)))["autonomous"]
        assert state["status"] in {"paused", "interrupted"}
        assert state["last_run_id"] == original_run and state["cycle_count"] == 1
        assert (Path(project["root"]) / "once-before-restart.txt").read_text() == "Synthetic content for once-before-restart.txt"
        await asyncio.sleep(0.1)
        assert not restored_factory.contexts
        assert restored.store.db.execute("SELECT COUNT(*) FROM runs WHERE session_id=?", (session["id"],)).fetchone()[0] == 1
    finally:
        await restored.close()


async def test_existing_manual_objective_is_not_automatically_activated(goal_env):
    service, factory, _, _ = goal_env
    await service.dispatch("goal.update", scope(goal_env, objective="A saved manual objective"))
    before = await goal_state(goal_env)
    assert before["goal"]["status"] == "active" and before["autonomous"] is None
    await service.close()
    restored = Service(service.paths, backend_factory=factory)
    await restored.start()
    try:
        current = await restored.dispatch("goal.get", scope(goal_env))
        assert current["goal"]["objective"] == "A saved manual objective"
        assert current["autonomous"] is None and not factory.contexts
    finally:
        await restored.close()


@pytest.mark.parametrize("foreign", [False, True])
async def test_completion_rejects_missing_or_other_project_receipts(goal_env, tmp_path, foreign):
    service, factory, _, session = goal_env
    evidence = []
    if foreign:
        root = tmp_path / "other"
        root.mkdir()
        project = (await service.dispatch("project.create", {"name": "Other synthetic scope", "root": str(root)}))["project"]
        other = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex", "model": "fixture"}))["session"]
        run, _ = service.store.create_run(other["id"], "foreign-receipt", "Synthetic unrelated work")
        event = service.store.event(run["id"], "tool.completed", {"tool_id": "fixture", "name": "read_file", "result": {"ok": True}})
        service.store.event(run["id"], "run.state", {"state": "completed"})
        evidence = [{"run_id": run["id"], "seq": event["seq"]}]

    async def cycle(ctx, number):
        factory.results.append(await checkpoint(ctx, "completed", next_step="", evidence=evidence))
        factory.results.append(await checkpoint(ctx, "blocked", call_id="blocked",
            summary="Completion evidence is unavailable.", next_step="Request valid evidence."))
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    state = await terminal(goal_env)
    rejected, blocked = factory.results
    assert not rejected["ok"] and rejected["error"]["code"] == "INVALID_ARGUMENT"
    assert blocked["ok"]
    # A failed cycle also blocks the goal; the error shows the recorded checkpoint blocked it.
    assert state["status"] == "blocked" and state["error"] == "Request valid evidence."
    assert len(factory.contexts) == 1
    assert service.store.one("runs", factory.contexts[0].run_id)["state"] == "completed"
    assert service.store.one("sessions", session["id"])["model"] == "synthetic-goal-model"


async def test_queued_user_message_runs_before_next_automatic_cycle(goal_env):
    service, factory, _, session = goal_env
    release = asyncio.Event()

    async def cycle(ctx, number):
        if number == 1:
            await release.wait()
            assert (await checkpoint(ctx, summary="First autonomous stage finished."))["ok"]
        elif number == 2:
            assert ctx.prompt == "Inspect this queued user correction first."
        else:
            assert "First autonomous stage finished." in ctx.supplied_context
            assert (await checkpoint(ctx, "completed", next_step="", deliverable="Final synthetic response."))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await until(lambda: len(factory.contexts) == 1)
    await service.dispatch("queue.add", {"session_id": session["id"], "request_id": "user-priority",
        "prompt": "Inspect this queued user correction first."})
    release.set()
    state = await terminal(goal_env)
    assert state["status"] == "completed" and state["cycle_count"] == 2
    assert len(factory.contexts) == 3


@pytest.mark.parametrize("field,value", [("model", "changed-model"), ("permission", "read_only"), ("root", "other-root")])
async def test_binding_change_between_cycles_stops_automatic_dispatch(goal_env, tmp_path, field, value):
    service, factory, project, session = goal_env
    factory.close_gate = asyncio.Event()

    async def cycle(ctx, number):
        factory.results.append(await checkpoint(ctx, summary="Recorded before binding changed."))
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(factory.closing.wait(), 5)
    if field == "model":
        service.store.db.execute("UPDATE sessions SET model=? WHERE id=?", (value, session["id"]))
    elif field == "permission":
        service.store.db.execute("UPDATE projects SET permission=? WHERE id=?", (value, project["id"]))
    else:
        changed = tmp_path / value
        changed.mkdir()
        service.store.db.execute("UPDATE projects SET root=? WHERE id=?", (str(changed), project["id"]))
    factory.close_gate.set()
    state = await terminal(goal_env)
    # The first cycle finished normally; admission of the next cycle refused the changed binding.
    assert factory.results[0]["ok"]
    assert service.store.one("runs", factory.contexts[0].run_id)["state"] == "completed"
    assert state["status"] == "blocked" and "execution scope changed; explicitly resume" in state["error"]
    assert len(factory.contexts) == 1


async def test_missing_checkpoints_never_turn_model_narration_into_goal_completion(goal_env):
    _, factory, _, _ = goal_env
    await start_goal(goal_env)
    state = await terminal(goal_env)
    assert state["status"] == "blocked" and state["cycle_count"] == 3
    assert len(factory.contexts) == 3
    assert "progress" in state["error"].lower()


async def test_explicit_cycle_limit_pauses_without_claiming_completion(goal_env):
    service, factory, _, _ = goal_env

    async def cycle(ctx, number):
        assert (await checkpoint(ctx, summary=f"Meaningful synthetic stage {number}"))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env, max_cycles=2)
    state = await terminal(goal_env)
    assert state["status"] == "paused" and state["cycle_count"] == 2
    assert state["max_cycles"] == 2 and len(factory.contexts) == 2
    with pytest.raises(KiraError) as limited:
        await service.dispatch("goal.resume", scope(goal_env, request_id="limit-resume"))
    assert limited.value.code == "LIMIT_REACHED"


@pytest.mark.parametrize("max_cycles", [True, -1, "0", 1.5])
async def test_invalid_limits_are_rejected_before_creating_runs(goal_env, max_cycles):
    service, factory, _, _ = goal_env
    with pytest.raises(KiraError) as invalid:
        await start_goal(goal_env, max_cycles=max_cycles)
    assert invalid.value.code == "INVALID_ARGUMENT"
    assert not factory.contexts
    assert service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


async def test_explicit_resume_uses_latest_checkpoint_and_duplicate_resume_is_idempotent(goal_env):
    service, factory, _, _ = goal_env
    release = asyncio.Event()
    ready = asyncio.Event()

    async def cycle(ctx, number):
        if number == 1:
            assert (await checkpoint(ctx, summary="Keep this synthetic progress across pause."))["ok"]
            ready.set()
            await release.wait()
        else:
            assert OBJECTIVE in ctx.supplied_context
            assert "Keep this synthetic progress across pause." in ctx.supplied_context
            assert (await checkpoint(ctx, "completed", next_step="", deliverable="Synthetic completed deliverable."))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    initial = await start_goal(goal_env)
    await asyncio.wait_for(ready.wait(), 5)
    await service.dispatch("goal.pause", scope(goal_env, request_id="pause-before-resume"))
    release.set()
    assert (await terminal(goal_env))["status"] == "paused"
    params = scope(goal_env, request_id="explicit-resume")
    await service.dispatch("goal.resume", params)
    await service.dispatch("goal.resume", params)
    state = await terminal(goal_env)
    assert state["id"] == initial["autonomous"]["id"]
    assert state["status"] == "completed" and state["cycle_count"] == 2
    await service.dispatch("goal.resume", params)
    assert (await goal_state(goal_env))["autonomous"]["status"] == "completed"
    assert len(factory.contexts) == 2


async def test_checkpoint_tool_retry_is_durable_and_changed_reuse_is_rejected(goal_env):
    _, factory, _, _ = goal_env

    async def cycle(ctx, number):
        first = await checkpoint(ctx, "completed", next_step="", deliverable="Synthetic final text.")
        same = await checkpoint(ctx, "completed", next_step="", deliverable="Synthetic final text.")
        changed = await checkpoint(ctx, "blocked", summary="Changed checkpoint input")
        factory.results.extend([first, same, changed])
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    state = await terminal(goal_env)
    assert factory.results[0]["ok"] and factory.results[0] == factory.results[1]
    assert not factory.results[2]["ok"] and factory.results[2]["error"]["code"] == "CONFLICT"
    assert state["status"] == "completed" and len(factory.contexts) == 1


@pytest.mark.parametrize("stop_team", [False, True])
async def test_goal_waits_for_delegated_team_result_then_continues_original_objective(goal_env, stop_team):
    service, factory, _, _ = goal_env
    team_ready = asyncio.Event()
    team_release = asyncio.Event()
    cycles = []
    followups = []

    async def cycle(ctx, number):
        if service.teams.data.link(ctx.run_id):
            process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
                start_new_session=True, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            factory.processes[ctx.run_id] = process
            await ctx.provider_started(process.pid)
            team_ready.set()
            await team_release.wait()
            output = json.dumps({"decision": "completed", "summary": "Synthetic Team outcome is recorded.",
                "members": [], "tasks": []})
            await ctx.emit("message.completed", {"content": output})
            return {"state": "completed", "outcome": {"final_text": output}}
        if service.assistant.followups.is_followup(ctx.run_id):
            followups.append(ctx)
            await ctx.emit("message.completed", {"content": "Synthetic Team outcome is available to the original goal."})
            return {"state": "completed", "outcome": {"synthetic": True}}
        cycles.append(ctx)
        assert OBJECTIVE in ctx.supplied_context
        if len(cycles) == 1:
            result = await ctx.client_tool_call("delegate", "kira.team_delegate", {
                "goal": "Inspect the synthetic fixture before finishing.",
                "brief": "Return the recorded synthetic outcome to the originating goal.",
                "mode": "manual", "members": [{"id": "coordinator", "name": "Synthetic inspector",
                    "role": "Inspect synthetic evidence", "instructions": "Read only.", "access": "read"}],
            })
            assert result["ok"], result
            assert (await checkpoint(ctx, summary="Delegated the independent synthetic inspection.",
                next_step="Incorporate the Team result before completion."))["ok"]
        else:
            assert len(followups) == 1
            assert "Synthetic Team outcome" in ctx.supplied_context + str(ctx.history)
            assert (await checkpoint(ctx, "completed", next_step="", deliverable="Synthetic consolidated Team result."))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(team_ready.wait(), 10)
    state = (await goal_state(goal_env))["autonomous"]
    assert state["status"] == "active" and state["cycle_count"] == 1
    await asyncio.sleep(0.1)
    assert len(cycles) == 1 and not followups
    if stop_team:
        linked = [service.teams.data.link(ctx.run_id) for ctx in factory.contexts]
        team_id = next(link["team_id"] for link in linked if link)
        await service.dispatch("team.cancel", {"team_id": team_id})
    team_release.set()
    state = await terminal(goal_env)
    if stop_team:
        await until(lambda: not service.teams.tasks)
        assert state["status"] in {"paused", "interrupted"} and state["cycle_count"] == 1
        assert len(cycles) == 1 and not followups
    else:
        assert state["status"] == "completed" and state["cycle_count"] == 2
        assert len(cycles) == 2 and len(followups) == 1


async def test_later_team_delegation_invalidates_an_earlier_completion_checkpoint(goal_env):
    service, factory, _, _ = goal_env
    team_ready = asyncio.Event()
    team_release = asyncio.Event()
    cycles = []

    async def cycle(ctx, number):
        if service.teams.data.link(ctx.run_id):
            process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
                start_new_session=True, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            factory.processes[ctx.run_id] = process
            await ctx.provider_started(process.pid)
            team_ready.set()
            await team_release.wait()
            output = json.dumps({"decision": "completed", "summary": "Synthetic late inspection finished.",
                "members": [], "tasks": []})
            await ctx.emit("message.completed", {"content": output})
            return {"state": "completed", "outcome": {"final_text": output}}
        if service.assistant.followups.is_followup(ctx.run_id):
            return {"state": "completed", "outcome": {"synthetic": True}}
        cycles.append(ctx)
        assert (await checkpoint(ctx, "completed", next_step="", deliverable="Synthetic provisional result."))["ok"]
        if len(cycles) == 1:
            result = await ctx.client_tool_call("delegate-after-checkpoint", "kira.team_delegate", {
                "goal": "Complete the remaining independent synthetic verification.",
                "brief": "This work was added after a provisional completion checkpoint.",
                "mode": "manual", "members": [{"id": "coordinator", "name": "Synthetic inspector",
                    "role": "Inspect fixture", "instructions": "Synthetic read only.", "access": "read"}],
            })
            assert result["ok"], result
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    try:
        await asyncio.wait_for(team_ready.wait(), 10)
        assert (await goal_state(goal_env))["autonomous"]["status"] == "active"
        assert len(cycles) == 1
    finally:
        team_release.set()
    state = await terminal(goal_env)
    assert state["status"] == "completed" and len(cycles) == 2


async def test_different_narration_each_cycle_does_not_establish_recorded_progress(goal_env):
    service, factory, _, _ = goal_env

    async def cycle(ctx, number):
        assert (await checkpoint(ctx, summary=f"Synthetic unverified progress claim {number}."))["ok"]
        await ctx.emit("message.completed", {"content": f"I claim stage {number} has advanced."})
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    state = await terminal(goal_env)
    assert state["status"] == "blocked" and state["cycle_count"] == 3
    assert len(factory.contexts) == 3 and "progress" in state["error"].lower()
    assert all(service.store.one("runs", ctx.run_id)["state"] == "completed" for ctx in factory.contexts)


async def test_uncertain_tool_effect_blocks_continuation_even_after_provider_completed(goal_env):
    _, factory, _, _ = goal_env

    async def cycle(ctx, number):
        assert (await checkpoint(ctx, "completed", next_step="", deliverable="Provisional synthetic output."))["ok"]
        await ctx.emit("tool.started", {"tool_id": "uncertain-effect", "name": "synthetic_external_action", "arguments": {}})
        await ctx.emit("tool.completed", {"tool_id": "uncertain-effect", "name": "synthetic_external_action", "result": {
            "ok": False, "action_outcome": "unknown", "retry_safe": False,
            "error": {"code": "DELIVERY_UNKNOWN", "message": "The synthetic action has no confirmed outcome."},
        }})
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    state = await terminal(goal_env)
    assert state["status"] == "interrupted" and len(factory.contexts) == 1
    assert any(word in state["error"].lower() for word in ("unknown", "uncertain", "unconfirmed"))


async def test_ordinary_chat_can_read_paused_goal_but_cannot_write_checkpoint(goal_env):
    service, factory, _, session = goal_env

    async def first(ctx, number):
        assert (await checkpoint(ctx, "blocked", summary="Synthetic task needs user guidance.", next_step="Ask for clarification."))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = first
    await start_goal(goal_env)
    blocked = await terminal(goal_env)
    # A failed cycle also blocks the goal; the error shows the recorded checkpoint blocked it.
    assert blocked["status"] == "blocked" and blocked["error"] == "Ask for clarification."

    async def ordinary(ctx, number):
        names = {tool["name"] for group in ctx.client_tools if group.get("name") == "goal" for tool in group["tools"]}
        assert names == {"status"}
        assert "call goal.checkpoint" not in ctx.assistant_instructions
        status = await ctx.client_tool_call("read-status", "goal.status", {})
        assert status["ok"] and status["goal"]["status"] == "blocked"
        denied = await checkpoint(ctx, "completed", next_step="", deliverable="Unauthorized ordinary completion.")
        assert not denied["ok"] and denied["error"]["code"] == "PERMISSION_DENIED"
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = ordinary
    run = (await service.dispatch("run.start", {"session_id": session["id"], "request_id": "ordinary-status",
        "prompt": "Tell me the current synthetic goal status."}))["run"]
    await until(lambda: run["id"] not in service.tasks)
    assert service.store.one("runs", run["id"])["state"] == "completed"
    assert (await goal_state(goal_env))["autonomous"]["status"] == "blocked"


async def test_identical_reads_with_new_receipt_ids_do_not_fake_progress(goal_env):
    service, factory, project, _ = goal_env
    (Path(project["root"]) / "unchanged.txt").write_text("Unchanging synthetic information.")

    async def cycle(ctx, number):
        assert (await ctx.execute_tool("read_file", {"path": "unchanged.txt"}))["ok"]
        event = next(event for event in reversed(service.store.events(ctx.run_id)["events"])
                     if event["type"] == "tool.completed" and event["payload"].get("name") == "read_file")
        assert (await checkpoint(ctx, summary=f"Read the same synthetic fact in cycle {number}.",
            evidence=[{"run_id": ctx.run_id, "seq": event["seq"]}]))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env, max_cycles=6)
    state = await terminal(goal_env)
    assert state["status"] == "blocked" and state["cycle_count"] == 4
    assert len(factory.contexts) == 4
    assert all(service.store.one("runs", ctx.run_id)["state"] == "completed" for ctx in factory.contexts)


async def test_late_user_guidance_prevents_old_checkpoint_from_completing_goal(goal_env):
    service, factory, _, _ = goal_env
    recorded = asyncio.Event()
    release = asyncio.Event()

    async def cycle(ctx, number):
        if number == 1:
            async def control(action, params):
                assert action == "steer"
                return {"accepted": True, "turn_id": "synthetic-goal-turn", "request_id": params["request_id"]}

            ctx.register_control(control)
            assert (await checkpoint(ctx, "completed", next_step="", deliverable="Synthetic provisional result."))["ok"]
            recorded.set()
            await release.wait()
            ctx.register_control(None)
        else:
            assert OBJECTIVE in ctx.supplied_context
            assert "Include the additional synthetic acceptance check." in str(ctx.history) + ctx.supplied_context
            evidence = await write_receipt(service, ctx, "added-user-check.txt")
            assert (await checkpoint(ctx, "completed", next_step="", evidence=[evidence]))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(recorded.wait(), 5)
    steered = await service.dispatch("run.steer", {"run_id": factory.contexts[0].run_id,
        "request_id": "late-goal-guidance", "prompt": "Include the additional synthetic acceptance check."})
    assert steered["accepted"]
    release.set()
    state = await terminal(goal_env)
    assert state["status"] == "completed" and state["cycle_count"] == 2
    assert len(factory.contexts) == 2


@pytest.mark.parametrize("delivery", ["rejected", "transport_unknown", "invalid_acknowledgment"])
async def test_failed_native_steering_prevents_goal_completion_or_continuation(goal_env, delivery):
    service, factory, _, _ = goal_env
    recorded = asyncio.Event()
    release = asyncio.Event()
    control_calls = []

    async def cycle(ctx, number):
        async def control(action, params):
            assert action == "steer"
            control_calls.append(params)
            if delivery == "rejected":
                return {"accepted": False}
            if delivery == "transport_unknown":
                raise ConnectionError("Synthetic steering transport lost its acknowledgment.")
            return {"accepted": True, "turn_id": None}

        ctx.register_control(control)
        assert (await checkpoint(ctx, "completed", next_step="", deliverable="Synthetic provisional output."))["ok"]
        recorded.set()
        await release.wait()
        ctx.register_control(None)
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(goal_env)
    await asyncio.wait_for(recorded.wait(), 5)
    run_id = factory.contexts[0].run_id
    params = {"run_id": run_id, "request_id": "unconfirmed-native-guidance",
        "prompt": "The acceptance criterion changed; inspect the extra synthetic case before finishing."}
    with pytest.raises(KiraError) as failure:
        await service.dispatch("run.steer", params)
    assert failure.value.code == ("STEERING_REJECTED" if delivery == "rejected" else "DELIVERY_UNKNOWN")
    retry = await service.dispatch("run.steer", params)
    assert not retry["accepted"] and not retry["automatic_replay"]
    assert retry["status"] == ("rejected" if delivery == "rejected" else "unknown")
    assert len(control_calls) == 1
    events = service.store.events(run_id)["events"]
    assert any(event["type"] == "steering.failed" for event in events)
    assert not any(event["type"] == "message.user" for event in events)
    release.set()
    state = await terminal(goal_env)
    assert service.store.one("runs", run_id)["state"] == "completed"
    assert state["status"] in {"interrupted", "paused"}
    assert state["cycle_count"] == 1 and len(factory.contexts) == 1
    assert state["objective_achievement"] == "not_established"


async def test_pause_while_direct_account_admission_waits_never_dispatches_a_cycle(goal_env, monkeypatch):
    service, factory, project, _ = goal_env

    async def prepare(settings, **kwargs):
        return {**settings, "openai_account_id": "synthetic-goal-account"}

    monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    session = (await service.dispatch("session.create", {"project_id": project["id"],
        "backend": "openai_chatgpt", "model": "synthetic-direct-model", "settings": {"reasoning_effort": "high"}}))["session"]
    direct_env = service, factory, project, session
    admitting = asyncio.Event()
    release = asyncio.Event()

    async def delayed(settings, **kwargs):
        admitting.set()
        await release.wait()
        return await prepare(settings)

    monkeypatch.setattr(service.direct_accounts, "prepare", delayed)
    await start_goal(direct_env)
    await asyncio.wait_for(admitting.wait(), 5)
    assert session["id"] in service.goals.admissions
    await service.dispatch("goal.pause", scope(direct_env, request_id="pause-during-admission"))
    release.set()
    await until(lambda: not service.goals.admissions)
    assert (await goal_state(direct_env))["autonomous"]["status"] == "paused"
    assert not factory.contexts
    assert service.store.db.execute("SELECT COUNT(*) FROM runs WHERE session_id=?", (session["id"],)).fetchone()[0] == 0


async def test_explicit_browser_consent_changes_do_not_break_goal_or_restore_old_grant(goal_env, monkeypatch):
    service, factory, project, _ = goal_env

    async def prepare(settings, **kwargs):
        return {**settings, "openai_account_id": "synthetic-browser-goal-account"}

    monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    session = (await service.dispatch("session.create", {"project_id": project["id"],
        "backend": "openai_chatgpt", "model": "synthetic-direct-model", "settings": {"reasoning_effort": "high"}}))["session"]
    direct_env = service, factory, project, session
    policies = []

    async def cycle(ctx, number):
        policies.append(ctx.settings.get("browser_action_approval", "ask"))
        if number in {1, 2}:
            await service.dispatch("session.web.update", scope(direct_env,
                browser_action_approval="always" if number == 1 else "ask"))
        evidence = await write_receipt(service, ctx, f"policy-stage-{number}.txt")
        assert (await checkpoint(ctx, "completed" if number == 3 else "continue",
            next_step="" if number == 3 else "Continue the synthetic objective.", evidence=[evidence]))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await start_goal(direct_env)
    state = await terminal(direct_env)
    assert state["status"] == "completed" and state["cycle_count"] == 3
    assert policies == ["ask", "always", "ask"]
    assert service.store.one("sessions", session["id"])["settings"]["browser_action_approval"] == "ask"


async def test_accepted_shutdown_never_dispatches_an_authorized_pending_goal(goal_env):
    service, factory, _, session = goal_env
    await start_goal(goal_env)
    assert not factory.contexts
    reply = await service.dispatch("service.shutdown", {})
    assert reply["stopping"]
    await asyncio.wait_for(service.stopping.wait(), 5)
    assert not factory.contexts and not service.tasks and not service.goals.admissions
    assert service.store.db.execute("SELECT COUNT(*) FROM runs WHERE session_id=?", (session["id"],)).fetchone()[0] == 0
