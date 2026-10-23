"""Real Team/chat admission and immutable read-only permissions; synthetic providers."""

import asyncio
import copy
import sys
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from test_assistant_service import env as env, settle, snapshot, start
from test_assistant_followups import pending_result


async def blocked_team(env, monkeypatch):
    service, factory, project, _ = env
    # Followup scheduling is covered separately. Keep its pending receipt from
    # reserving the project after the two owners exercised here have closed.
    monkeypatch.setattr(service.assistant.followups, "prepare_candidate", lambda *_: None)
    factory.member_gate = asyncio.Event()
    original = await start(env)
    await asyncio.wait_for(factory.member_started.wait(), 5)
    if original["id"] in service.tasks:
        await asyncio.wait_for(asyncio.shield(service.tasks[original["id"]]), 30)
    team = (await snapshot(env))["delegations"][0]
    assert team["state"] in {"planning", "running"}
    assert service.store.conversation_team(service.store.one("projects", project["id"])) == team["team_id"]
    return team["team_id"]


async def wait_run(service, run_id):
    if run_id in service.tasks:
        await asyncio.wait_for(asyncio.shield(service.tasks[run_id]), 30)


async def wait_team(service, factory):
    factory.member_gate.set()
    while service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)


async def start_chat(service, session, identity, prompt="Explain the recorded Team progress"):
    return (await service.dispatch("run.start", {"session_id": session["id"],
        "request_id": identity, "prompt": prompt}))["run"]


async def expect_busy(service, session, identity):
    with pytest.raises(KiraError) as error:
        await start_chat(service, session, identity)
    assert error.value.code == "BUSY"
    assert not service.store.all("SELECT id FROM runs WHERE request_id=?", (identity,))


@pytest.mark.asyncio
async def test_readonly_conversation_guards_survive_team_end_and_cleanup_then_normal_write_resumes(env, monkeypatch):
    service, factory, project, origin_session = env
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "trusted_host"})
    project = service.store.one("projects", project["id"])
    original_project = copy.deepcopy(project)
    direct = (await service.dispatch("session.create", {"project_id": project["id"],
        "backend": "openai_chatgpt", "model": "synthetic-conversation-model", "settings": {
            "tools": True, "reasoning_effort": "xhigh", "openai_account_id": "synthetic-conversation-account"}}))["session"]
    original_session = service.store.one("sessions", direct["id"])
    team_id = await blocked_team(env, monkeypatch)
    entered, finished = asyncio.Event(), asyncio.Event()
    contexts, browser_dispatches = [], []

    async def conversation(ctx):
        contexts.append(ctx)
        entered.set()
        await finished.wait()

    async def no_browser_engine(*values, **options):
        browser_dispatches.append((values, options))
        raise AssertionError("A read-only conversation must reject mutations before opening a browser")

    factory.origin = conversation
    factory.origin_gate = finished
    factory.close_gate = asyncio.Event()
    monkeypatch.setattr(service.web, "_engine", no_browser_engine)
    run = await start_chat(service, direct, "concurrent-readonly-conversation")
    await asyncio.wait_for(entered.wait(), 5)
    ctx = contexts[0]
    assert run["execution_lane"] == "team_conversation" and run["effective_permission"] == "read_only"
    assert run["related_team_id"] == team_id and "team_id" not in run
    assert ctx.project["permission"] == "read_only"
    assert ctx.model == direct["model"] and ctx.settings["reasoning_effort"] == "xhigh"
    assert ctx.settings["openai_account_id"] == "synthetic-conversation-account"
    assert "read-only for its entire run" in ctx.supplied_context
    assert service.store.run_lane(run["id"])["effective_permission"] == "read_only"
    (Path(project["root"]) / "evidence.txt").write_text("Synthetic readable evidence", encoding="utf-8")

    async def guard_checks(phase):
        read = await ctx.execute_tool("read_file", {"path": "evidence.txt"})
        assert read["ok"] and "Synthetic readable evidence" in read["content"]
        write = await ctx.execute_tool("write_file", {"path": "forbidden.txt", "content": "Synthetic forbidden write",
                                                    "expected_sha256": None})
        host = await ctx.execute_tool("run_command", {"argv": [sys.executable, "-c",
            "from pathlib import Path; Path('host-canary.txt').write_text('synthetic effect')"]})
        browser = await ctx.client_tool_call("browser-" + phase, "browser.click", {"ref": "synthetic-reference"})
        computer = await ctx.client_tool_call("computer-" + phase, "computer.act", {
            "action": "click", "observation_id": "synthetic-observation", "x": 10, "y": 10})
        for result in (write, host, browser, computer):
            assert result["ok"] is False and result["error"]["code"] == "PERMISSION_DENIED"
        assert not (Path(project["root"]) / "forbidden.txt").exists()
        assert not (Path(project["root"]) / "host-canary.txt").exists()
        assert not browser_dispatches

    await guard_checks("team-running")
    assert {row["owner_id"] for row in service.store.reserved(project["root"])} == {team_id, run["id"]}
    await expect_busy(service, origin_session, "second-ordinary-while-team")
    await wait_team(service, factory)
    assert service.teams.data.one(team_id)["state"] == "completed"
    assert {row["owner_id"] for row in service.store.reserved(project["root"])} == {run["id"]}
    assert not factory.followup_contexts
    await guard_checks("team-completed")
    assert ctx.project["permission"] == "read_only"
    assert service.team_conversation_state(project, direct)["read_only"]
    await expect_busy(service, origin_session, "second-ordinary-after-team")

    finished.set()
    async with asyncio.timeout(5):
        while service.store.one("runs", run["id"])["state"] != "completed":
            await asyncio.sleep(.01)
    assert run["id"] in service.tasks  # The backend's close is intentionally held.
    assert service.store.reserved(project["root"])[0]["owner_id"] == run["id"]
    await expect_busy(service, origin_session, "ordinary-before-cleanup")
    factory.close_gate.set()
    await wait_run(service, run["id"])
    assert not service.store.reserved(project["root"])
    assert service.store.one("projects", project["id"]) == original_project
    assert service.store.one("sessions", direct["id"]) == original_session

    written = []

    async def normal(ctx):
        assert ctx.project["permission"] == "trusted_host"
        written.append(await ctx.execute_tool("write_file", {"path": "normal-result.txt", "content": "Synthetic verified write",
                                                            "expected_sha256": None}))

    factory.origin = normal
    normal_run = await start_chat(service, direct, "normal-write-after-cleanup", "Write the synthetic result")
    await wait_run(service, normal_run["id"])
    assert not service.store.run_lane(normal_run["id"])
    assert written and written[0]["ok"]
    assert (Path(project["root"]) / "normal-result.txt").read_text() == "Synthetic verified write"
    assert service.store.one("projects", project["id"]) == original_project
    assert service.store.one("sessions", direct["id"]) == original_session


@pytest.mark.asyncio
@pytest.mark.parametrize("overlap", ["same_root", "nested_root"])
async def test_conversation_exception_never_admits_foreign_overlapping_project_or_second_chat(env, monkeypatch, overlap):
    service, factory, project, session = env
    root = Path(project["root"])
    if overlap == "nested_root":
        root = root / "nested-fixture"
        root.mkdir()
    other = (await service.dispatch("project.create", {"name": "Synthetic overlapping project",
        "root": str(root), "permission": "project_write"}))["project"]
    other_session = (await service.dispatch("session.create", {"project_id": other["id"],
        "backend": "codex", "model": "synthetic-overlapping-model"}))["session"]
    same_project_chat = (await service.dispatch("session.create", {"project_id": project["id"],
        "backend": "codex", "model": "synthetic-other-chat"}))["session"]
    team_id = await blocked_team(env, monkeypatch)
    assert service.store.conversation_team(other) is None
    await expect_busy(service, other_session, "overlap-before-conversation")
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []

    async def conversation(ctx):
        contexts.append(ctx)
        entered.set()
        await release.wait()

    factory.origin = conversation
    factory.origin_gate = release
    request_id = "one-admitted-conversation"
    run = await start_chat(service, session, request_id)
    await asyncio.wait_for(entered.wait(), 5)
    assert contexts[0].project["permission"] == "read_only"
    assert contexts[0].model == session["model"] and contexts[0].settings["reasoning_effort"] == "high"
    assert contexts[0].settings["service_tier"] == "fixture-tier"
    duplicate = await start_chat(service, session, request_id)
    assert duplicate["id"] == run["id"] and duplicate["execution_lane"] == "team_conversation"
    assert len(contexts) == 1
    await expect_busy(service, same_project_chat, "another-chat-during-conversation")
    await expect_busy(service, other_session, "overlap-during-conversation")
    await wait_team(service, factory)
    assert service.teams.data.one(team_id)["state"] == "completed"
    await expect_busy(service, other_session, "overlap-after-team-before-conversation-end")
    assert {row["owner_id"] for row in service.store.reserved(str(root))} == {run["id"]}
    release.set()
    await wait_run(service, run["id"])
    assert not service.store.reserved(str(root))
    admitted = await start_chat(service, other_session, "overlap-after-all-cleanup")
    await wait_run(service, admitted["id"])
    assert not service.store.run_lane(admitted["id"])
    assert contexts[-1].project["permission"] == "project_write"
    assert len(contexts) == 2


@pytest.mark.asyncio
async def test_pending_team_result_wakes_after_native_compaction_releases_scope(env, monkeypatch):
    service, factory, project, session = env
    _, row, schedule = await pending_result(env, monkeypatch)
    monkeypatch.setattr(service.chat_lanes, "schedule_followup", schedule)
    monkeypatch.setattr(service, "schedule_assistant_followup", schedule)
    service.store.db.execute("UPDATE sessions SET backend_ref='synthetic-thread' WHERE id=?", (session["id"],))
    entered, release = asyncio.Event(), asyncio.Event()
    original_backend = service._backend

    class Compactor:
        process = None

        async def manage_thread(self, action, saved, scope, params, **callbacks):
            assert action == "compact" and scope["permission"] == "read_only"
            self.process = await asyncio.create_subprocess_exec(sys.executable, "-c",
                "import time; time.sleep(60)", start_new_session=True,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await callbacks["provider_started"](self.process.pid)
            entered.set()
            await release.wait()
            return {"compacted": True, "backend_ref": saved["backend_ref"]}

        async def close(self):
            if self.process and self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()

    monkeypatch.setattr(service, "_backend", lambda *_: Compactor())
    try:
        params = {"project_id": project["id"], "session_id": session["id"], "request_id": "synthetic-compaction"}
        assert (await service.dispatch("session.compact", params))["status"] == "pending"
        await asyncio.wait_for(entered.wait(), 5)
        monkeypatch.setattr(service, "_backend", original_backend)
        await schedule(row["team_id"])
        assert not factory.followup_contexts
        assert service.assistant.followups._one(row["team_id"])["state"] == "pending"
        release.set()
        async with asyncio.timeout(30):
            while service.turn_controls.tasks:
                await asyncio.sleep(.01)
        assert (await service.dispatch("session.compact", params))["status"] == "completed"
        await asyncio.wait_for(factory.followup_started.wait(), 5)
        assert len(factory.followup_contexts) == 1
        await settle(service, factory.followup_contexts[0].run_id)
        assert service.assistant.followups._one(row["team_id"])["state"] == "completed"
        assert not service.store.reserved(project["root"])
    finally:
        release.set()
        if service.turn_controls.tasks:
            await asyncio.gather(*list(service.turn_controls.tasks), return_exceptions=True)
