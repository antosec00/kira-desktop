"""Scheduler fixtures with real owned harmless child processes; no model inference."""

import asyncio
import json
import sys
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


def member(mid, access="read"):
    return {
        "id": mid,
        "name": mid,
        "role": "Fixture role " + mid,
        "instructions": "",
        "access": access,
    }


def assignment(tid, mid, access="read", depends=()):
    return {
        "id": tid,
        "member_id": mid,
        "title": tid,
        "instructions": "Fixture " + tid,
        "access": access,
        "depends_on": list(depends),
    }


class Factory:
    def __init__(self):
        self.service = None
        self.contexts = []
        self.roster = [member("coordinator"), member("producer", "write"), member("verifier")]
        self.plan_tasks = [
            assignment("produce", "producer", "write"),
            assignment("verify", "verifier", depends=["produce"]),
        ]
        self.worker = None
        self.close_gate = {}
        self.started = {}
        self.close_started = {}
        self.timeline = []
        self.finish_decision = "completed"

    def __call__(self, name, settings):
        factory = self

        class Backend:
            process = None
            mid = None

            async def run(self, ctx):
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    "import time; time.sleep(120)",
                    start_new_session=True,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await ctx.provider_started(self.process.pid)
                link = factory.service.teams.data.link(ctx.run_id)
                team = factory.service.teams.data.one(link["team_id"])
                self.mid = link["member_id"]
                factory.contexts.append(ctx)
                factory.timeline.append(("start", self.mid, bool(ctx.output_schema)))
                if ctx.output_schema:
                    first = team["round_index"] == 1
                    output = json.dumps(
                        {
                            "decision": "continue" if first else factory.finish_decision,
                            "summary": "actual fixture review",
                            "members": factory.roster if not team["roster_frozen"] else [],
                            "tasks": factory.plan_tasks if first else [],
                        }
                    )
                    await ctx.emit("message.completed", {"content": output})
                    return {"state": "completed", "outcome": {"final_text": output}}
                factory.started.setdefault(self.mid, asyncio.Event()).set()
                if factory.worker:
                    result = await factory.worker(self.mid, ctx)
                    if result:
                        return result
                elif self.mid == "producer":
                    assert ctx.permission == "project_write"
                    (ctx.root / "result.txt").write_text("written by real fixture process owner")
                    result = await ctx.client_tool_call(
                        "message-1",
                        "team.send_message",
                        {"recipient_id": "verifier", "content": "Inspect result.txt"},
                    )
                    assert result["ok"]
                elif self.mid == "verifier":
                    assert ctx.permission == "read_only"
                    result = await ctx.client_tool_call("read-1", "team.read_messages", {})
                    assert result["ok"] and any(
                        m["content"] == "Inspect result.txt" for m in result["messages"]
                    )
                    assert (
                        ctx.root / "result.txt"
                    ).read_text() == "written by real fixture process owner"
                await ctx.emit("message.completed", {"content": "worker-report:" + self.mid})
                # Like ordinary Codex workers, final text is in durable assistant events.
                return {"state": "completed", "outcome": {"fixture": True}}

            async def close(self):
                if self.mid is not None:
                    factory.close_started.setdefault(self.mid, asyncio.Event()).set()
                    if self.mid in factory.close_gate:
                        await factory.close_gate[self.mid].wait()
                if self.process and self.process.returncode is None:
                    self.process.terminate()
                    await self.process.wait()
                factory.timeline.append(("closed", self.mid, False))

        return Backend()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-team-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        factory = Factory()
        service = Service(Paths.from_env(), backend_factory=factory)
        factory.service = service
        await service.start()
        project = (
            await service.dispatch(
                "project.create",
                {"name": "Team fixture", "root": str(tmp_path), "permission": "project_write"},
            )
        )["project"]
        yield service, factory, project
        for gate in factory.close_gate.values():
            gate.set()
        await service.close()


def params(env, **extra):
    service, factory, project = env
    return {
        "project_id": project["id"],
        "request_id": "start-fixture",
        "goal": "Produce and independently inspect a fixture",
        "mode": "manual",
        "backend": "codex",
        "model": "fixture-model",
        "settings": {"reasoning_effort": "high"},
        "members": factory.roster,
        "concurrency": 2,
        "round_limit": 3,
        **extra,
    }


async def finished(service, team_id):
    task = service.teams.tasks.get(team_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 30)
    return (await service.dispatch("team.snapshot", {"team_id": team_id}))["team"]


async def wait_event(factory, mapping, mid):
    # Member runs start and stop real child processes; allow for a contended host.
    try:
        async with asyncio.timeout(30):
            while mid not in getattr(factory, mapping):
                await asyncio.sleep(0.01)
            await getattr(factory, mapping)[mid].wait()
    except TimeoutError:
        raise AssertionError("fixture member did not reach " + mapping) from None


async def test_dependency_handoff_reaches_assignee_without_other_members_private_mail(env):
    service, factory, _ = env
    verified = []

    async def worker(mid, ctx):
        if mid == "producer":
            result = await ctx.execute_tool("write_file", {
                "path": "handoff.txt", "content": "actual dependency output", "expected_sha256": None})
            assert result["ok"]
            await ctx.client_tool_call("private-note", "team.send_message", {
                "recipient_id": "coordinator", "content": "PRIVATE-COORDINATOR-NOTE"})
        else:
            context = ctx.supplied_context.split("Service-managed team context:\n", 1)[1]
            metadata = json.loads(context.split("\nMessages recorded", 1)[0])
            assert metadata["assignment"]["id"] == "verify"
            dependency, = metadata["dependency_results"]
            assert dependency["id"] == "produce" and dependency["cleanup_confirmed"]
            assert dependency["state"] == "completed"
            assert dependency["final_text"] == "worker-report:producer"
            assert dependency["evidence"]["event_counts"]["tool.completed"] == 1
            assert "PRIVATE-COORDINATOR-NOTE" not in ctx.supplied_context
            assert (ctx.root / "handoff.txt").read_text() == "actual dependency output"
            verified.append(dependency["run_id"])

    factory.worker = worker
    team = (await service.dispatch("team.start", params(env)))["team"]
    final = await finished(service, team["id"])
    assert final["state"] == "completed" and len(verified) == 1


@pytest.mark.parametrize("mode", ["manual", "automatic"])
async def test_complete_roster_real_mailbox_results_and_persistence(env, mode):
    service, factory, project = env
    cfg = params(env, mode=mode)
    if mode == "automatic":
        cfg.pop("members")
        cfg["max_agents"] = 3
    team = (await service.dispatch("team.start", cfg))["team"]
    retry = (await service.dispatch("team.start", cfg))["team"]
    assert retry["id"] == team["id"]
    with pytest.raises(KiraError, match="different"):
        await service.dispatch("team.start", {**cfg, "goal": "changed"})
    final = await finished(service, team["id"])
    assert final["state"] == "completed", final
    assert all(s["state"] == "completed" for s in final["steps"])
    assert (
        next(m for m in final["members"] if m["id"] == "producer")["last_output"]
        == "worker-report:producer"
    )
    planner = [c for c in factory.contexts if c.output_schema][-1]
    assert (
        "worker-report:producer" in planner.supplied_context
        and "tool_failures" in planner.supplied_context
    )
    assert all(
        c.model == "fixture-model"
        and c.settings["reasoning_effort"] == "high"
        and c.disable_native_agents
        for c in factory.contexts
    )
    events = (await service.dispatch("team.events", {"team_id": team["id"], "limit": 250}))[
        "events"
    ]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(
        e["type"] == "team.message.supplied" and e["payload"].get("via") == "read_messages"
        for e in events
    )
    assert not service.store.all("SELECT * FROM project_reservations")
    assert (await service.dispatch("project.snapshot", {"project_id": project["id"]}))[
        "sessions"
    ] == []
    sessions = service.store.all("SELECT * FROM team_members WHERE team_id=?", (team["id"],))
    with pytest.raises(KiraError, match="scheduled"):
        await service.dispatch(
            "run.start",
            {"session_id": sessions[0]["session_id"], "request_id": "outside", "prompt": "outside"},
        )


async def test_parallel_readers_and_writer_wait_for_actual_close(env):
    service, factory, _ = env
    factory.roster = [member("coordinator"), member("a"), member("b"), member("writer", "write")]
    factory.plan_tasks = [
        assignment("a", "a"),
        assignment("b", "b"),
        assignment("writer", "writer", "write"),
    ]
    release = asyncio.Event()
    factory.close_gate["a"] = asyncio.Event()

    async def worker(mid, ctx):
        if mid in {"a", "b"}:
            await release.wait()

    factory.worker = worker
    team = (await service.dispatch("team.start", params(env, concurrency=3)))["team"]
    await wait_event(factory, "started", "a")
    await wait_event(factory, "started", "b")
    assert "writer" not in factory.started
    release.set()
    await wait_event(factory, "close_started", "a")
    await asyncio.sleep(0.05)
    assert "writer" not in factory.started
    factory.close_gate["a"].set()
    final = await finished(service, team["id"])
    assert final["state"] == "completed"
    assert factory.timeline.index(("closed", "a", False)) < factory.timeline.index(
        ("start", "writer", False)
    )


async def test_overlap_reservation_held_through_writer_cleanup(env, tmp_path):
    service, factory, project = env
    factory.close_gate["producer"] = asyncio.Event()
    team = (await service.dispatch("team.start", params(env)))["team"]
    await wait_event(factory, "close_started", "producer")
    sub = tmp_path / "nested"
    sub.mkdir()
    other = (await service.dispatch("project.create", {"name": "Overlap", "root": str(sub)}))[
        "project"
    ]
    session = (
        await service.dispatch(
            "session.create", {"project_id": other["id"], "backend": "ollama", "model": "fixture"}
        )
    )["session"]
    with pytest.raises(KiraError) as failure:
        await service.dispatch(
            "run.start", {"session_id": session["id"], "request_id": "ordinary", "prompt": "x"}
        )
    assert failure.value.code == "BUSY"
    with pytest.raises(KiraError) as second:
        await service.dispatch("team.start", params(env, request_id="second"))
    assert second.value.code == "BUSY"
    factory.close_gate["producer"].set()
    await finished(service, team["id"])


async def test_mailbox_routing_dedup_empty_reads_and_validation_errors(env):
    service, factory, _ = env
    factory.roster = [member("coordinator"), member("a"), member("b")]
    factory.plan_tasks = [assignment("a", "a"), assignment("b", "b", depends=["a"])]

    async def worker(mid, ctx):
        if mid == "a":
            args = {"recipient_id": "coordinator", "content": "coordinator-only"}
            first = await ctx.client_tool_call("same", "team.send_message", args)
            again = await ctx.client_tool_call("same", "team.send_message", args)
            assert first == again
            conflict = await ctx.client_tool_call(
                "same", "team.send_message", {"recipient_id": "all", "content": "different"}
            )
            assert conflict["error"]["code"] == "CONFLICT"
            invalid = await ctx.client_tool_call(
                "bad", "team.send_message", {"recipient_id": "missing", "content": "x"}
            )
            assert not invalid["ok"]
            retry = await ctx.client_tool_call(
                "bad", "team.send_message", {"recipient_id": "all", "content": "x"}
            )
            assert retry["error"]["code"] == "CONFLICT"
        else:
            result = await ctx.client_tool_call("empty", "team.read_messages", {})
            assert {key: result[key] for key in ("ok", "messages", "last_seq", "has_more")} == {
                "ok": True, "messages": [], "last_seq": 0, "has_more": False}
            assert result["scheduling"]["messages_start_work"] is False
            assert result["scheduling"]["next_action"] == "continue_assigned_work"
            assert "coordinator-only" not in ctx.supplied_context

    factory.worker = worker
    team = (await service.dispatch("team.start", params(env)))["team"]
    final = await finished(service, team["id"])
    assert final["state"] == "completed", final
    assert len(final["messages"]) == 1


async def test_limit_pause_and_unicode_explicit_continuation_atomic(env):
    service, factory, _ = env
    team = (await service.dispatch("team.start", params(env, round_limit=1)))["team"]
    original = await finished(service, team["id"])
    assert original["state"] == "paused" and original["pause_reason"] == "round_limit"
    instruction = "😀" * 4000
    resumed = (
        await service.dispatch(
            "team.resume",
            {"team_id": team["id"], "request_id": "resume", "instruction": instruction},
        )
    )["team"]
    assert resumed["parent_team_id"] == team["id"] and resumed["id"] in service.teams.tasks
    assert len(resumed["messages"][0]["content"].encode()) <= 8000
    events = service.teams.data.events(resumed["id"], 0, 100)["events"]
    assert any(e["payload"].get("continuation_instruction") == instruction for e in events)
    await finished(service, resumed["id"])


@pytest.mark.parametrize("bad", [True, 3.0])
async def test_manual_count_rejects_boolean_float(env, bad):
    service, _, _ = env
    with pytest.raises(KiraError) as error:
        await service.dispatch("team.start", params(env, max_agents=bad))
    assert error.value.code == "INVALID_ARGUMENT"
    assert not service.store.all("SELECT * FROM project_reservations")


@pytest.mark.parametrize("mid", ["all", "user", "human", "system"])
async def test_reserved_member_ids(env, mid):
    service, factory, _ = env
    with pytest.raises(KiraError) as error:
        await service.dispatch(
            "team.start", params(env, members=[member("coordinator"), member(mid)])
        )
    assert error.value.code == "INVALID_ARGUMENT"


async def test_shutdown_admission_gate(env):
    service, _, _ = env
    service._closing = True
    try:
        with pytest.raises(KiraError) as failure:
            await service.dispatch("team.start", params(env))
        assert failure.value.code == "SHUTTING_DOWN"
    finally:
        service._closing = False


async def test_cancel_during_finalizer_keeps_drain_and_reconciles_children(env):
    service, factory, _ = env
    factory.close_gate["producer"] = asyncio.Event()
    team = (await service.dispatch("team.start", params(env)))["team"]
    await wait_event(factory, "close_started", "producer")
    run_id = service.teams.data.member(team["id"], "producer")["run_id"]
    task = service.tasks[run_id]
    # Cancellation arrives after inference, while provider.close is still awaited.
    await service.dispatch("team.cancel", {"team_id": team["id"]})
    task.cancel()
    await asyncio.sleep(0.03)
    assert run_id in service.tasks and not task.done()
    assert service.store.reserved(env[2]["root"])
    factory.close_gate["producer"].set()
    final = await finished(service, team["id"])
    assert final["state"] == "cancelled"
    assert "verifier" not in factory.started
    assert all(step["state"] not in {"running", "queued"} for step in final["steps"])
    assert all(m["state"] != "running" for m in final["members"])
    assert not service.tasks and not service.cancellations
    assert not service.store.all("SELECT * FROM project_reservations")


async def test_unknown_cleanup_stops_all_further_admission(env, monkeypatch):
    service, factory, _ = env
    original = service.processes.cleanup

    async def uncertain(run_id):
        clean = await original(run_id)
        link = service.teams.data.link(run_id)
        return False if link and link["member_id"] == "producer" else clean

    monkeypatch.setattr(service.processes, "cleanup", uncertain)
    factory.plan_tasks = [assignment("first", "producer", "write"), assignment("next", "verifier")]
    team = (await service.dispatch("team.start", params(env)))["team"]
    final = await finished(service, team["id"])
    assert final["state"] == "interrupted" and final["phase"] == "cleanup_blocked"
    assert "verifier" not in factory.started
    assert service.store.reserved(env[2]["root"])
    assert next(s for s in final["steps"] if s["id"] == "first")["state"] == "interrupted"
    assert next(s for s in final["steps"] if s["id"] == "next")["state"] == "cancelled"
    monkeypatch.setattr(service.processes, "cleanup", original)
    assert await service.teams.release_if_clean(team["id"])


async def test_reverse_dependency_order_propagates_blocked_and_reviews_failure(env):
    service, factory, _ = env
    factory.plan_tasks = [
        assignment("third", "verifier", depends=["second"]),
        assignment("second", "verifier", depends=["first"]),
        assignment("first", "producer", "write"),
    ]

    async def failed(mid, ctx):
        return {"state": "failed", "error": "Fixture task failure"}

    factory.worker = failed
    team = (await service.dispatch("team.start", params(env)))["team"]
    final = await finished(service, team["id"])
    assert final["state"] == "paused" and final["pause_reason"] == "unresolved_work"
    assert {s["id"]: s["state"] for s in final["steps"]} == {
        "first": "failed",
        "second": "blocked",
        "third": "blocked",
    }
    assert len([c for c in factory.contexts if c.output_schema]) == 2


async def test_approval_queue_correlates_member_run_and_rejects_wrong_team(env):
    service, factory, _ = env
    requested = asyncio.Event()
    decision = None
    factory.plan_tasks = [assignment("one", "producer", "write")]

    async def worker(mid, ctx):
        nonlocal decision
        requested.set()
        decision = await ctx.approve(
            {"type": "fixture", "message": "Approve only the harmless fixture"}
        )

    factory.worker = worker
    team = (await service.dispatch("team.start", params(env)))["team"]
    await asyncio.wait_for(requested.wait(), 5)
    snapshot = service.teams.snapshot(team["id"])
    approval = snapshot["approvals"][0]
    assert approval["member_id"] == "producer"
    with pytest.raises(KiraError) as wrong:
        await service.dispatch(
            "team.approve",
            {
                "team_id": team["id"],
                "run_id": "unrelated",
                "approval_id": approval["approval_id"],
                "decision": "accept",
            },
        )
    assert wrong.value.code == "INVALID_ARGUMENT"
    await service.dispatch(
        "team.approve",
        {
            "team_id": team["id"],
            "run_id": approval["run_id"],
            "approval_id": approval["approval_id"],
            "decision": "accept",
        },
    )
    final = await finished(service, team["id"])
    assert decision == {"decision": "accept"} and final["approvals"] == []


async def test_invalid_cyclic_plan_never_dispatches_workers(env):
    service, factory, _ = env
    factory.plan_tasks = [
        assignment("one", "producer", "write", depends=["two"]),
        assignment("two", "verifier", depends=["one"]),
    ]
    team = (await service.dispatch("team.start", params(env)))["team"]
    final = await finished(service, team["id"])
    assert final["state"] == "failed" and "cycle" in final["error"].lower()
    assert not factory.started and not service.store.all("SELECT * FROM project_reservations")


async def test_recovery_reconciles_planning_member_without_assignment(env):
    service, factory, _ = env
    cfg, project, roster = service.teams._config(params(env))
    team = service.teams.data.create(project, cfg, "fixture", roster)
    service.store.db.execute(
        "UPDATE team_members SET state='running' WHERE team_id=? AND id='coordinator'",
        (team["id"],),
    )
    await service.teams.recover()
    result = service.teams.snapshot(team["id"])
    assert result["state"] == "interrupted"
    assert result["members"][0]["state"] == "interrupted"
    assert not factory.contexts and not service.store.all("SELECT * FROM project_reservations")


async def test_snapshot_byte_bound_keeps_correlation_and_reports_omissions(env):
    service, factory, _ = env
    factory.roster = [member("coordinator")] + [member("member" + str(i)) for i in range(11)]
    cfg, project, roster = service.teams._config(params(env, goal="😀" * 16000))
    team = service.teams.data.create(project, cfg, "bounded", roster)
    service.store.db.execute(
        "UPDATE team_members SET instructions=?,last_output=? WHERE team_id=?",
        ("\x01" * 8000, "😀" * 16000, team["id"]),
    )
    for i in range(120):
        service.store.db.execute(
            "INSERT INTO team_steps (team_id,round_index,id,member_id,title,instructions,access,depends_on,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                team["id"],
                i,
                "task",
                "coordinator",
                "Task",
                "😀" * 2000,
                "read",
                "[]",
                "completed",
                "then",
                "then",
            ),
        )
    approvals = [
        {
            "run_id": "run-" + str(i),
            "approval_id": "approval-" + str(i),
            "member_id": "coordinator",
            "request": {"message": "😀" * 5000},
        }
        for i in range(70)
    ]
    snapshot = service.teams.data.snapshot(team["id"], approvals)
    assert len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()) <= 650000
    assert snapshot["omitted"]["steps"] >= 24 and snapshot["omitted"]["approvals"] == 6
    assert len(snapshot["approvals"]) == 64
    assert all(
        a["run_id"] == "run-" + str(i) and a["approval_id"] == "approval-" + str(i)
        for i, a in enumerate(snapshot["approvals"])
    )
    assert all(
        a["request"]["truncated"] and a["request"]["full_request"] == "run.events"
        for a in snapshot["approvals"]
    )
    assert all(
        m["instructions_truncated"] and m["last_output_truncated"] for m in snapshot["members"]
    )
    service.store.db.execute("DELETE FROM project_reservations WHERE owner_id=?", (team["id"],))
    service.teams.data.state(team["id"], "paused")


async def test_read_only_project_rejects_write_roster_before_reserving(env):
    service, _, project = env
    service.store.db.execute(
        "UPDATE projects SET permission='read_only' WHERE id=?", (project["id"],)
    )
    with pytest.raises(KiraError) as error:
        await service.dispatch("team.start", params(env))
    assert error.value.code == "PERMISSION_DENIED"
    assert not service.store.all("SELECT * FROM project_reservations")


async def test_graceful_team_close_drains_running_members_and_persists_states(env):
    service, factory, _ = env

    async def worker(mid, ctx):
        await ctx.cancel.wait()
        return {"state": "cancelled", "outcome": {"stopped": True}}

    factory.worker = worker
    team = (await service.dispatch("team.start", params(env)))["team"]
    await wait_event(factory, "started", "producer")
    await asyncio.wait_for(service.teams.close(), 8)
    final = service.teams.snapshot(team["id"])
    assert final["state"] == "interrupted"
    assert all(s["state"] not in {"running", "queued"} for s in final["steps"])
    assert all(m["state"] != "running" for m in final["members"])
    assert not service.tasks and not service.store.all("SELECT * FROM project_reservations")
    with pytest.raises(KiraError, match="draining"):
        await service.dispatch("team.start", params(env, request_id="after-close"))


async def test_terminal_human_message_is_durable_without_starting_work(env):
    service, factory, _ = env
    team = (await service.dispatch("team.start", params(env)))["team"]
    await finished(service, team["id"])
    calls = len(factory.contexts)
    parameters = {
        "team_id": team["id"],
        "request_id": "human-message",
        "recipient_id": "all",
        "content": "Record for the next explicit continuation.",
    }
    first = await service.dispatch("team.send_message", parameters)
    second = await service.dispatch("team.send_message", parameters)
    assert first == second and first["message"]["sender_id"] == "user"
    await asyncio.sleep(0.02)
    assert not service.teams.tasks and len(factory.contexts) == calls
    events = service.teams.data.events(team["id"], first["message"]["seq"] - 1, 10)["events"]
    assert events[0]["payload"]["message"] == first["message"]
