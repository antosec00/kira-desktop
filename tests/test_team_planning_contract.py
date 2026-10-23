"""Real scheduler/cleanup receipts with scripted providers, never model inference."""

import asyncio
import json
import sys
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


def member(identifier):
    return {"id": identifier, "name": identifier, "role": "Inspect fixture evidence",
            "instructions": "Use the recorded assignment", "access": "read"}


def assignment(identifier="worker"):
    return {"id": "inspect", "member_id": identifier, "title": "Inspect fixture evidence",
            "instructions": "Read fixture.txt and report the actual content",
            "access": "read", "depends_on": []}


class PlanningProvider:
    def __init__(self):
        self.service = None
        self.actions = ["work", "completed"]
        self.contexts = []
        self.coordinators = []
        self.workers = []
        self.hook = None
        self.worker_state = "completed"
        self.close_run_id = None
        self.close_entered = asyncio.Event()
        self.close_gate = asyncio.Event()

    def __call__(self, _backend, _settings):
        factory = self

        class Backend:
            process = None
            run_id = None

            async def run(self, ctx):
                self.run_id = ctx.run_id
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", "import sys; sys.stdin.buffer.read()",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                await ctx.provider_started(self.process.pid)
                factory.contexts.append(ctx)
                if ctx.output_schema is None:
                    factory.workers.append(ctx)
                    read = await ctx.execute_tool("read_file", {"path": "fixture.txt"})
                    assert read["ok"] and read["content"] == "Synthetic project evidence"
                    await ctx.emit("message.completed", {"content": "Inspected synthetic project evidence"})
                    return {"state": factory.worker_state,
                            "error": None if factory.worker_state == "completed" else "Synthetic worker failure"}
                index = len(factory.coordinators)
                factory.coordinators.append(ctx)
                action = factory.actions[min(index, len(factory.actions) - 1)]
                if factory.hook is not None:
                    await factory.hook(ctx, index)
                if action == "provider_failure":
                    return {"state": "failed", "error": "Synthetic provider no-progress stop"}
                link = factory.service.teams.data.link(ctx.run_id)
                team = factory.service.teams.data.one(link["team_id"])
                tasks = [assignment("coordinator" if action == "coordinator_work" else "worker")]
                plan = {
                    "decision": "continue" if action in {"work", "coordinator_work", "empty"} else action,
                    "summary": "Synthetic coordinator conclusion",
                    "members": [member("coordinator"), member("worker")] if not team["roster_frozen"] else [],
                    "tasks": tasks if action in {"work", "coordinator_work"} else []}
                output = json.dumps(plan)
                await ctx.emit("message.completed", {"content": output})
                return {"state": "completed", "outcome": {"final_text": output}}

            async def close(self):
                if factory.close_run_id is not None and self.run_id == factory.close_run_id:
                    factory.close_entered.set()
                    await factory.close_gate.wait()
                if self.process is not None and self.process.returncode is None:
                    self.process.terminate()
                    await self.process.wait()

        return Backend()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-team-plan-") as profile:
        monkeypatch.setenv("KIRA_HOME", profile)
        (tmp_path / "fixture.txt").write_text("Synthetic project evidence")
        factory = PlanningProvider()
        service = Service(Paths.from_env(), backend_factory=factory)
        factory.service = service
        await service.start()
        project = (await service.dispatch("project.create", {
            "name": "Synthetic scheduler contract", "root": str(tmp_path),
            "permission": "project_write"}))["project"]
        try:
            yield service, factory, project
        finally:
            factory.close_gate.set()
            await service.close()


def params(env, **changes):
    return {"project_id": env[2]["id"], "request_id": "scheduler-contract",
            "goal": "Inspect the synthetic project evidence as a team", "mode": "manual",
            "backend": "codex", "model": "FIXTURE-contract", "settings": {"reasoning_effort": "high"},
            "members": [member("coordinator"), member("worker")],
            "round_limit": 3, "concurrency": 2, **changes}


async def finish(service, team_id):
    task = service.teams.tasks.get(team_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 30)
    return (await service.dispatch("team.snapshot", {"team_id": team_id}))["team"]


async def start(env, **changes):
    team = (await env[0].dispatch("team.start", params(env, **changes)))["team"]
    return await finish(env[0], team["id"])


def feedback(service, team_id):
    return [row["payload"] for row in service.store.all(
        "SELECT payload FROM team_events WHERE team_id=? AND type='team.plan.feedback' ORDER BY seq",
        (team_id,))]


def metadata(ctx):
    return json.loads(ctx.supplied_context.split("Service-managed team context:\n", 1)[1]
                      .split("\nMessages recorded", 1)[0])


@pytest.mark.parametrize("automatic", [False, True])
async def test_missing_assignments_get_one_corrective_plan_then_real_worker_review(env, automatic):
    service, factory, _ = env
    factory.actions = ["completed", "work", "completed"]
    changes = {"mode": "automatic", "max_agents": 2} if automatic else {}
    options = params(env, **changes)
    if automatic:
        options.pop("members")
    team = (await service.dispatch("team.start", options))["team"]
    final = await finish(service, team["id"])
    assert final["state"] == "completed" and final["round_index"] == 3
    assert len(factory.workers) == 1 and len(factory.coordinators) == 3
    first, = feedback(service, team["id"])
    assert first["code"] == "missing_worker_assignments" and first["repair_scheduled"]
    assert first["summary_verified"] is False and first["remaining_coordinator_turns"] == 2
    assert metadata(factory.coordinators[1])["planning_feedback"] == first
    assert metadata(factory.coordinators[2])["completion_evidence"]["kind"] == "worker_assignments"
    assert final["completion_evidence"]["worker_assignments_completed"] == 1
    assert final["completion_evidence"]["basis"]["source_team_id"] == team["id"]
    assert all(ctx.model == "FIXTURE-contract" and ctx.settings["reasoning_effort"] == "high"
               and ctx.permission == "read_only" for ctx in factory.contexts)
    assert all("does not start working until you RETURN" in ctx.assistant_instructions
               for ctx in factory.coordinators)
    assert not service.store.all("SELECT * FROM project_reservations")


@pytest.mark.parametrize("action", ["completed", "empty"])
@pytest.mark.parametrize("limit,expected_turns,repair_count", [(1, 1, 0), (2, 2, 1), (0, 2, 1)])
async def test_missing_work_never_claims_completion_or_exceeds_round_or_repair_budget(
        env, action, limit, expected_turns, repair_count):
    service, factory, _ = env
    factory.actions = [action]
    final = await start(env, round_limit=limit)
    assert final["state"] == "paused" and final["pause_reason"] == "no_progress"
    assert final["round_index"] == expected_turns == len(factory.coordinators)
    assert len(factory.workers) == 0 and final["steps"] == []
    assert sum(row["repair_scheduled"] for row in feedback(service, final["id"])) == repair_count
    assert final["summary"].startswith("Unverified coordinator summary:")
    assert final["error"] and final["completion_evidence"]["basis"] is None
    assert not service.store.all("SELECT * FROM project_reservations")
    assert not any(row["payload"].get("state") == "completed" for row in service.store.all(
        "SELECT payload FROM team_events WHERE team_id=? AND type IN ('team.state','team.result')",
        (final["id"],)))
    before = len(factory.contexts)
    await service.teams.recover()
    duplicate = (await service.dispatch("team.start", params(env, round_limit=limit)))["team"]
    assert duplicate["id"] == final["id"] and duplicate["state"] == "paused"
    assert len(factory.contexts) == before and not service.teams.tasks


async def test_mailbox_records_context_reports_idle_workers_and_preserves_no_progress_signatures(env):
    service, factory, _ = env
    observed = []

    async def communicate(ctx, index):
        if index:
            return
        send = await ctx.client_tool_call("send", "team.send_message", {
            "recipient_id": "worker", "content": "Inspect the synthetic evidence after assignment"})
        assert send["ok"] and send["scheduling"]["messages_start_work"] is False
        assert send["scheduling"]["next_action"] == "return_continue_plan_with_tasks"
        assert send["scheduling"]["active_member_ids"] == ["coordinator"]
        assert send["scheduling"]["idle_member_ids"] == ["worker"]
        assert not factory.workers
        assert not service.store.all("SELECT * FROM team_steps")
        reads = [await ctx.client_tool_call("read-" + str(n), "team.read_messages", {
            "after_seq": send["message"]["seq"]}) for n in range(3)]
        assert reads[0] == reads[1] == reads[2], "Metadata must not defeat identical-result loop detection"
        assert reads[0]["messages"] == [] and not factory.workers
        observed.extend([send, *reads])

    factory.hook = communicate
    final = await start(env)
    assert final["state"] == "completed" and len(factory.workers) == 1
    assert "Inspect the synthetic evidence after assignment" in factory.workers[0].supplied_context
    events = service.store.all("SELECT payload FROM team_events WHERE team_id=? "
        "AND type='team.message.supplied' AND json_extract(payload,'$.via')='read_messages'", (final["id"],))
    assert len(events) == 3
    assert all(row["payload"]["scheduling"] == observed[1]["scheduling"] for row in events)


async def test_terminal_run_cannot_send_or_read_during_held_cleanup_but_cached_retries_survive(env):
    service, factory, _ = env
    receipts = {}
    message = {"recipient_id": "worker", "content": "Recorded during the live coordinator turn"}

    async def record_while_live(ctx, index):
        if index:
            return
        factory.close_run_id = ctx.run_id
        receipts["send"] = await ctx.client_tool_call("live-send", "team.send_message", message)
        receipts["read"] = await ctx.client_tool_call("live-read", "team.read_messages", {})
        assert receipts["send"]["ok"] and receipts["read"]["ok"]

    factory.hook = record_while_live
    team = (await service.dispatch("team.start", params(env)))["team"]
    try:
        await asyncio.wait_for(factory.close_entered.wait(), 5)
        ctx = factory.coordinators[0]
        assert service.store.one("runs", ctx.run_id)["state"] == "completed"
        assert ctx.run_id in service.tasks and ctx.run_id in service.teams.children[team["id"]]
        assert service.teams.data.member(team["id"], "coordinator")["state"] == "running"
        messages_before = service.store.all("SELECT * FROM team_messages WHERE team_id=?", (team["id"],))
        supplied_before = service.store.all("SELECT seq FROM team_events WHERE team_id=? "
            "AND type='team.message.supplied'", (team["id"],))
        for name, arguments in (("send_message", message), ("read_messages", {})):
            rejected = await ctx.client_tool_call("late-" + name, "team." + name, arguments)
            assert not rejected["ok"] and rejected["error"]["code"] == "CANCELLED"
        assert service.store.all("SELECT * FROM team_messages WHERE team_id=?", (team["id"],)) == messages_before
        assert service.store.all("SELECT seq FROM team_events WHERE team_id=? "
            "AND type='team.message.supplied'", (team["id"],)) == supplied_before
        assert await ctx.client_tool_call("live-send", "team.send_message", message) == receipts["send"]
        assert await ctx.client_tool_call("live-read", "team.read_messages", {}) == receipts["read"]
        assert not factory.workers
    finally:
        factory.close_gate.set()
    final = await finish(service, team["id"])
    assert final["state"] == "completed" and len(factory.workers) == 1
    assert await ctx.client_tool_call("live-send", "team.send_message", message) == receipts["send"]
    assert await ctx.client_tool_call("live-read", "team.read_messages", {}) == receipts["read"]
    explicit = await service.dispatch("team.send_message", {
        "team_id": team["id"], "request_id": "human-note-after-cleanup",
        "recipient_id": "coordinator", "content": "Explicit user note on the settled team"})
    assert explicit["message"]["sender_id"] == "user"
    assert not service.teams.tasks and len(factory.workers) == 1


async def test_cancelling_run_rejects_new_mailbox_effects_and_keeps_prior_receipts(env):
    service, factory, _ = env
    entered, release = asyncio.Event(), asyncio.Event()
    receipts = {}
    message = {"recipient_id": "worker", "content": "Live planning context before cancellation"}

    async def wait_for_cancel(ctx, index):
        assert index == 0
        receipts["send"] = await ctx.client_tool_call("before-send", "team.send_message", message)
        receipts["read"] = await ctx.client_tool_call("before-read", "team.read_messages", {})
        entered.set()
        await release.wait()

    factory.hook = wait_for_cancel
    team = (await service.dispatch("team.start", params(env)))["team"]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        ctx = factory.coordinators[0]
        assert service.store.one("runs", ctx.run_id)["state"] == "running"
        await service.dispatch("team.cancel", {"team_id": team["id"]})
        assert service.cancellations[ctx.run_id].is_set() and ctx.run_id in service.tasks
        before = service.store.all("SELECT * FROM team_messages WHERE team_id=?", (team["id"],))
        for name, arguments in (("send_message", message), ("read_messages", {})):
            rejected = await ctx.client_tool_call("cancelled-" + name, "team." + name, arguments)
            assert not rejected["ok"] and rejected["error"]["code"] == "CANCELLED"
        assert service.store.all("SELECT * FROM team_messages WHERE team_id=?", (team["id"],)) == before
        assert await ctx.client_tool_call("before-send", "team.send_message", message) == receipts["send"]
        assert await ctx.client_tool_call("before-read", "team.read_messages", {}) == receipts["read"]
    finally:
        release.set()
    final = await finish(service, team["id"])
    assert final["state"] == "cancelled" and not factory.workers


async def test_actual_coordinator_only_roster_can_finish_without_fabricated_workers(env):
    service, factory, _ = env
    factory.actions = ["completed"]
    final = await start(env, members=[member("coordinator")], concurrency=1)
    assert final["state"] == "completed" and len(factory.coordinators) == 1
    assert not factory.workers and not feedback(service, final["id"])
    assert final["completion_evidence"]["basis"]["kind"] == "coordinator_only"


async def test_coordinator_assigned_work_does_not_replace_worker_participation(env):
    service, factory, _ = env
    factory.actions = ["coordinator_work", "completed", "completed"]
    final = await start(env)
    assert final["state"] == "paused" and final["pause_reason"] == "no_progress"
    assert final["steps"][0]["member_id"] == "coordinator"
    assert final["steps"][0]["state"] == "completed"
    assert final["completion_evidence"]["worker_assignments_completed"] == 0
    assert len(feedback(service, final["id"])) == 2


async def test_repaired_plan_still_pauses_when_coordinator_round_budget_is_exhausted(env):
    _, factory, _ = env
    factory.actions = ["completed", "work", "completed"]
    final = await start(env, round_limit=2)
    assert final["state"] == "paused" and final["pause_reason"] == "round_limit"
    assert len(factory.coordinators) == 2 and len(factory.workers) == 1
    assert final["steps"][0]["state"] == "completed"


async def test_single_round_runs_planned_workers_then_pauses_for_explicit_review(env):
    _, factory, _ = env
    final = await start(env, round_limit=1)
    assert final["state"] == "paused" and final["pause_reason"] == "round_limit"
    assert len(factory.coordinators) == 1 and len(factory.workers) == 1
    assert metadata(factory.coordinators[0])["remaining_coordinator_turns_after_this"] == 0


async def test_provider_failure_is_not_hidden_or_retried_as_a_planning_repair(env):
    service, factory, _ = env
    factory.actions = ["provider_failure"]
    final = await start(env)
    assert final["state"] == "failed" and "no-progress" in final["error"]
    assert len(factory.coordinators) == 1 and not feedback(service, final["id"])


async def test_summary_only_continuations_retain_real_clean_ancestor_receipts_without_replay(env):
    service, factory, _ = env
    original = await start(env, round_limit=1)
    factory.actions = ["completed"]
    parent = original
    for depth in (1, 2, 3):
        child = (await service.dispatch("team.resume", {
            "team_id": parent["id"], "request_id": "summary-" + str(depth),
            "instruction": "Summarize the recorded evidence without repeating effects"}))["team"]
        child = await finish(service, child["id"])
        assert child["state"] == "completed" and child["steps"] == []
        basis = child["completion_evidence"]["basis"]
        assert basis["kind"] == "prior_worker_assignments"
        assert basis["source_team_id"] == original["id"] and basis["ancestor_depth"] == depth
        if depth > 1:
            source = metadata(factory.coordinators[-1])["recorded_worker_results"]
            assert source["source_team_id"] == original["id"]
            receipt, = source["results"]
            assert receipt["cleanup_confirmed"] and receipt["run_id"] == original["steps"][0]["run_id"]
            assert "Inspected synthetic project evidence" in receipt["outcome"]
        parent = child
    assert len(factory.workers) == 1 and len(factory.coordinators) == 4


async def test_failed_worker_parent_cannot_be_certified_by_summary_only_continuation(env):
    service, factory, _ = env
    factory.worker_state = "failed"
    parent = await start(env)
    assert parent["pause_reason"] == "unresolved_work"
    factory.actions = ["completed"]
    child = (await service.dispatch("team.resume", {
        "team_id": parent["id"], "request_id": "inspect-failure", "instruction": "Review evidence"}))["team"]
    final = await finish(service, child["id"])
    assert final["state"] == "paused" and final["pause_reason"] == "no_progress"
    assert final["completion_evidence"]["basis"] is None and len(factory.workers) == 1


async def test_resume_is_idempotent_and_rejects_another_branch_from_the_same_parent(env):
    service, factory, _ = env
    parent = await start(env)
    factory.actions = ["completed"]
    arguments = {"team_id": parent["id"], "request_id": "resume-once", "instruction": "Review recorded work"}
    child = (await service.dispatch("team.resume", arguments))["team"]
    await finish(service, child["id"])
    again = (await service.dispatch("team.resume", arguments))["team"]
    assert again["id"] == child["id"]
    with pytest.raises(KiraError, match="already has a continuation"):
        await service.dispatch("team.resume", {**arguments, "request_id": "another-branch"})


async def test_final_resume_scope_guard_runs_after_cleanup_before_any_continuation_effect(env):
    service, _, _ = env
    parent = await start(env)
    calls = []

    def reject():
        calls.append(True)
        assert not service.store.all("SELECT id FROM team_runs WHERE parent_team_id=?", (parent["id"],))
        raise KiraError("CONFLICT", "Synthetic chat generation changed")

    with pytest.raises(KiraError, match="generation changed"):
        await service.teams.dispatch("team.resume", {
            "team_id": parent["id"], "request_id": "rejected-scope"}, before_resume=reject)
    assert calls == [True]
    assert not service.store.all("SELECT id FROM team_runs WHERE parent_team_id=?", (parent["id"],))
    assert not service.store.all("SELECT * FROM project_reservations")


async def test_resume_rechecks_existing_child_after_async_cleanup_admission(env, monkeypatch):
    service, factory, _ = env
    parent = await start(env)
    factory.actions = ["completed"]
    entered, release = asyncio.Event(), asyncio.Event()
    cleanup = service.teams.release_if_clean
    count = 0

    async def delay_first(team_id):
        nonlocal count
        count += 1
        if team_id == parent["id"] and count == 1:
            entered.set()
            await release.wait()
        return await cleanup(team_id)

    monkeypatch.setattr(service.teams, "release_if_clean", delay_first)
    first = asyncio.create_task(service.teams.dispatch("team.resume", {
        "team_id": parent["id"], "request_id": "delayed-continuation"}))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        winner = (await service.teams.dispatch("team.resume", {
            "team_id": parent["id"], "request_id": "concurrent-continuation"}))["team"]
        release.set()
        with pytest.raises(KiraError, match="already has a continuation"):
            await first
        assert (await finish(service, winner["id"]))["state"] == "completed"
        assert len(service.store.all("SELECT id FROM team_runs WHERE parent_team_id=?", (parent["id"],))) == 1
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


async def test_unknown_parent_cleanup_blocks_explicit_resume_without_reservation(env, monkeypatch):
    service, _, _ = env
    parent = await start(env)

    async def unknown(_team_id):
        return False

    monkeypatch.setattr(service.teams, "release_if_clean", unknown)
    with pytest.raises(KiraError, match="confirmed team provider cleanup"):
        await service.teams.dispatch("team.resume", {
            "team_id": parent["id"], "request_id": "unclean-continuation"})
    assert not service.store.all("SELECT id FROM team_runs WHERE parent_team_id=?", (parent["id"],))
    assert not service.store.all("SELECT * FROM project_reservations")


async def test_model_continuation_preserves_derived_sender_in_messages_and_events(env):
    service, factory, _ = env
    parent = await start(env)
    factory.actions = ["completed"]
    child = (await service.teams.dispatch("team.resume", {
        "team_id": parent["id"], "request_id": "model-continuation",
        "instruction": "Inspect the retained evidence without repeating work"},
        continuation_sender=("origin_assistant:fixture-origin-run", "Kira assistant")))["team"]
    final = await finish(service, child["id"])
    assert final["state"] == "completed"
    message, = final["messages"]
    assert message["sender_id"] == "origin_assistant:fixture-origin-run"
    assert message["sender_name"] == "Kira assistant"
    event, = service.store.all("SELECT payload FROM team_events WHERE team_id=? "
        "AND type='team.result' AND json_extract(payload,'$.continuation_instruction') IS NOT NULL",
        (child["id"],))
    assert event["payload"]["continuation_sender"] == {
        "id": "origin_assistant:fixture-origin-run", "name": "Kira assistant"}
    assert metadata(factory.coordinators[-1])["continuation_sender"] == event["payload"]["continuation_sender"]


async def test_completion_ancestor_search_is_bounded_and_never_trusts_intermediate_summary(env, monkeypatch):
    service, factory, _ = env
    parent = await start(env)
    factory.actions = ["completed"]
    monkeypatch.setattr("kira.service.teams.MAX_COMPLETION_ANCESTORS", 1)
    child = (await service.teams.dispatch("team.resume", {
        "team_id": parent["id"], "request_id": "first-summary"}))["team"]
    child = await finish(service, child["id"])
    assert child["state"] == "completed" and child["steps"] == []
    grandchild = (await service.teams.dispatch("team.resume", {
        "team_id": child["id"], "request_id": "too-distant-summary"}))["team"]
    final = await finish(service, grandchild["id"])
    assert final["state"] == "paused" and final["pause_reason"] == "no_progress"
    assert len(factory.workers) == 1 and final["completion_evidence"]["basis"] is None


async def test_cancellation_after_feedback_does_not_start_a_repair_or_worker(env, monkeypatch):
    service, factory, _ = env
    factory.actions = ["completed", "work"]
    publish = service.teams.publish

    def stop_after_feedback(event):
        publish(event)
        if event["type"] == "team.plan.feedback":
            service.teams.stops[event["team_id"]].set()

    monkeypatch.setattr(service.teams.data, "publish", stop_after_feedback)
    final = await start(env)
    assert final["state"] == "cancelled" and len(factory.coordinators) == 1
    assert not factory.workers and not service.store.all("SELECT * FROM project_reservations")
