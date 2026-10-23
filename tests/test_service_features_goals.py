"""Autonomous-goal progress, evidence and checkpoint bounds against an isolated service.

Provider receipts are synthetic copies of the shapes the Codex and Claude Code
adapters record (observed-only items); no provider, account or profile is used.
"""

import itertools
from pathlib import Path

import pytest

import kira.service.server as server_module
import test_autonomous_goals
from test_autonomous_goals import OBJECTIVE, checkpoint, until

goal_env = test_autonomous_goals.goal_env
_identities = itertools.count(1)


def recorded(service, run_id, tool_id):
    return next(event for event in service.store.events(run_id)["events"]
                if event["type"] == "tool.completed" and event["payload"]["tool_id"] == tool_id)


async def observed(service, ctx, name, result):
    """Record a provider-observed tool exchange the way the adapters do."""
    tool_id = f"provider-item-{next(_identities)}"
    await ctx.emit("tool.started", {"tool_id": tool_id, "name": name, "arguments": {}})
    await ctx.emit("tool.completed", {"tool_id": tool_id, "name": name, "result": result})
    return recorded(service, ctx.run_id, tool_id)


def codex_item(item_type, **fields):
    # Every Codex item carries a fresh identity and timing; commands also a process.
    return {"id": f"item-{next(_identities)}", "type": item_type, "status": "completed",
            "durationMs": next(_identities), **fields, "source": "codex", "observed_only": True, "ok": True}


async def codex_cycle(service, ctx):
    await observed(service, ctx, "codex.commandExecution", codex_item(
        "commandExecution", command="cat unchanged.txt", cwd=str(ctx.root), exitCode=0,
        processId=str(next(_identities)), aggregatedOutput="Unchanging synthetic information."))
    await observed(service, ctx, "codex.mcpToolCall", codex_item(
        "mcpToolCall", server="kira", tool="help_search", arguments={"query": "goal"},
        result={"content": [{"type": "text", "text": "{\"ok\":true}"}]}))
    await observed(service, ctx, "codex.dynamicToolCall", codex_item(
        "dynamicToolCall", namespace="goal", tool="checkpoint", arguments={}, success=True))


async def claude_code_cycle(service, ctx):
    native = {"ok": True, "source": "claude_code", "observed_only": True,
              "content": "Unchanging synthetic information."}
    await observed(service, ctx, "claude_code.Read", native)
    for mirrored in ("claude_code.kira.help_search", "claude_code.goal.checkpoint"):
        # Claude Code results carry Kira's own JSON reply, including its per-call receipt.
        await observed(service, ctx, mirrored, {**native, "content": f"{{\"ok\":true,\"call\":{next(_identities)}}}"})


async def claude_code_session(env, monkeypatch):
    service, _, project, _ = env
    monkeypatch.setattr(server_module, "require_claude_code", lambda: None)
    return (await service.dispatch("session.create", {"project_id": project["id"], "backend": "claude_code",
        "model": "synthetic-claude-model", "settings": {"reasoning_effort": "high"}}))["session"]


async def settled(service, session_id):
    await until(lambda: not service.tasks and service.store.db.execute(
        "SELECT 1 FROM autonomous_goals WHERE session_id=? AND status IN ('paused','blocked','completed','interrupted')",
        (session_id,)).fetchone())
    return (await service.dispatch("goal.get", {"project_id": service.store.one("sessions", session_id)["project_id"],
                                                "session_id": session_id}))["autonomous"]


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
async def test_provider_copies_of_kira_calls_and_repeated_reads_are_not_progress(goal_env, monkeypatch, provider):
    service, factory, project, session = goal_env
    (Path(project["root"]) / "unchanged.txt").write_text("Unchanging synthetic information.")
    if provider == "claude_code":
        session = await claude_code_session(goal_env, monkeypatch)

    async def cycle(ctx, number):
        await (codex_cycle if provider == "codex" else claude_code_cycle)(service, ctx)
        assert (await checkpoint(ctx, summary=f"Read the same synthetic fact in cycle {number}."))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await service.dispatch("goal.start", {"project_id": project["id"], "session_id": session["id"],
        "request_id": "provider-copies", "objective": OBJECTIVE, "max_cycles": 6})
    state = await settled(service, session["id"])
    # The first native read is new; three more identical cycles stop the goal.
    assert state["status"] == "blocked" and state["cycle_count"] == 4, state
    assert "progress" in state["error"].lower()


@pytest.mark.parametrize("name,result", [
    ("claude_code.goal.status", {"ok": True, "source": "claude_code", "observed_only": True,
                                 "content": "{\"ok\":true,\"goal\":{}}"}),
    ("codex.dynamicToolCall", codex_item("dynamicToolCall", namespace="goal", tool="status",
                                         arguments={}, success=True)),
])
async def test_provider_copy_of_a_goal_receipt_is_not_completion_evidence(goal_env, name, result):
    service, factory, _, _ = goal_env
    outcomes = []

    async def cycle(ctx, number):
        copy = await observed(service, ctx, name, result)
        status = await ctx.client_tool_call("status", "goal.status", {})
        references = {(item["run_id"], item["seq"]) for item in status["recent_evidence"]}
        outcomes.append((ctx.run_id, copy["seq"]) in references)
        outcomes.append(await checkpoint(ctx, "completed", next_step="", call_id="cite-copy",
                                         evidence=[{"run_id": ctx.run_id, "seq": copy["seq"]}]))
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await test_autonomous_goals.start_goal(goal_env, max_cycles=1)
    state = await test_autonomous_goals.terminal(goal_env)
    listed, cited = outcomes
    assert listed is False
    assert not cited["ok"] and cited["error"]["code"] == "INVALID_ARGUMENT", cited
    assert state["status"] != "completed"


async def test_native_provider_execution_remains_valid_completion_evidence(goal_env):
    service, factory, _, _ = goal_env
    outcomes = []

    async def cycle(ctx, number):
        command = await observed(service, ctx, "codex.commandExecution", codex_item(
            "commandExecution", command="pytest -q", exitCode=0, aggregatedOutput="1 passed"))
        outcomes.append(await checkpoint(ctx, "completed", next_step="",
                                         evidence=[{"run_id": ctx.run_id, "seq": command["seq"]}]))
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await test_autonomous_goals.start_goal(goal_env)
    state = await test_autonomous_goals.terminal(goal_env)
    assert outcomes[0]["ok"], outcomes
    assert state["status"] == "completed"
    assert state["completion_basis"] == "model_assessed_with_execution_evidence"


async def test_checkpoint_limits_count_characters_like_the_tool_schema(goal_env):
    _, factory, _, _ = goal_env
    outcomes = []
    # Two UTF-8 bytes per character: within the advertised character limits.
    summary, next_step, deliverable = "è" * 6000, "à" * 3000, "ù" * 12000

    async def cycle(ctx, number):
        outcomes.append(await checkpoint(ctx, "completed", summary=summary, next_step=next_step,
                                         deliverable=deliverable))
        outcomes.append(await checkpoint(ctx, "completed", summary=summary + "è", next_step="",
                                         deliverable=deliverable, call_id="over-limit"))
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await test_autonomous_goals.start_goal(goal_env, max_cycles=1)
    state = await test_autonomous_goals.terminal(goal_env)
    within, beyond = outcomes
    assert within["ok"], within
    assert not beyond["ok"] and beyond["error"]["code"] == "INVALID_ARGUMENT"
    assert "6000 characters" in beyond["error"]["message"]
    assert state["status"] == "completed" and state["summary"] == summary


async def test_claude_code_goal_survives_live_browser_consent_changes(goal_env, monkeypatch):
    service, factory, project, _ = goal_env
    session = await claude_code_session(goal_env, monkeypatch)
    policies = []

    async def cycle(ctx, number):
        policies.append(ctx.settings.get("browser_action_approval", "ask"))
        if number in {1, 2}:
            await service.dispatch("session.web.update", {"project_id": project["id"], "session_id": session["id"],
                "browser_action_approval": "always" if number == 1 else "ask"})
        evidence = await test_autonomous_goals.write_receipt(service, ctx, f"claude-policy-{number}.txt")
        assert (await checkpoint(ctx, "completed" if number == 3 else "continue",
            next_step="" if number == 3 else "Continue the synthetic objective.", evidence=[evidence]))["ok"]
        return {"state": "completed", "outcome": {"synthetic": True}}

    factory.handler = cycle
    await service.dispatch("goal.start", {"project_id": project["id"], "session_id": session["id"],
        "request_id": "claude-policy", "objective": OBJECTIVE})
    state = await settled(service, session["id"])
    assert state["status"] == "completed" and state["cycle_count"] == 3, state
    assert policies == ["ask", "always", "ask"]
