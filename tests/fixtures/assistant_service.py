"""Real isolated service/scheduler with deterministic providers; no model access."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from kira.service.server import Service

MODEL = "synthetic-assistant-model"
RESULT = "Synthetic team verified the fixture evidence."
FOLLOWUP = "Synthetic Kira reviewed the recorded Team evidence in the original chat."


def roster():
    return [{"id": identifier, "name": identifier.title(),
             "role": "Coordinate fixture review" if identifier == "coordinator" else "Review fixture evidence",
             "instructions": "Read only the synthetic fixture evidence and report its marker.",
             "access": "read"} for identifier in ("coordinator", "reviewer")]


class AssistantFixtureBackend:
    def __init__(self, service, control):
        self.service, self.control = service, control
        self.process = None
        self.run_id = None

    async def discover(self):
        return {"available": True, "fixture": True, "capabilities": {"tools": True},
                "account": {"connected": True, "type": "synthetic"},
                "models": [{"id": MODEL, "name": "Synthetic assistant fixture",
                            "supported_reasoning_efforts": ["high", "xhigh"],
                            "capabilities": {"tools": True}}]}

    async def wait_gate(self, name, ctx):
        while not (self.control / name).exists():
            if ctx.cancel.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(0.01)

    def record(self, value):
        with (self.control / "provider-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value) + "\n")

    async def run(self, ctx):
        # Exercise real process ownership and cleanup. This child is only a
        # disposable sleeper, never an inference backend or user process.
        self.run_id = ctx.run_id
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(120)", start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        await ctx.provider_started(self.process.pid)
        link = self.service.teams.data.link(ctx.run_id)
        events = (await self.service.dispatch("run.events", {"run_id": ctx.run_id}))["events"]
        followup = any(event["type"] == "backend.notice" and event["payload"].get("source") == "team_followup"
                       for event in events)
        role = link["member_id"] if link else "followup" if followup else "origin"
        self.record({"run_id": ctx.run_id, "role": role, "model": ctx.model,
                     "effort": ctx.settings.get("reasoning_effort"),
                     "permission": ctx.permission, "pid": self.process.pid})
        if followup:
            assert not link and RESULT in ctx.supplied_context
            environment = await ctx.client_tool_call("fixture-followup-environment", "kira.environment", {})
            assert environment["ok"] and environment["environment"]["capabilities"]["managed_team"] is False
            (self.control / "followup-started").write_text(ctx.run_id)
            await self.wait_gate("release-followup", ctx)
            await ctx.emit("message.completed", {"content": FOLLOWUP})
            return {"state": "completed", "outcome": {"fixture": True, "final_text": FOLLOWUP}}
        if not link:
            environment = await ctx.client_tool_call("fixture-environment", "kira.environment", {})
            assert environment["ok"] and environment["environment"]["chat"]["id"] == ctx.session["id"]
            arguments = {"goal": "Review the synthetic IPC fixture evidence",
                         "brief": "Read evidence.txt and report its marker to the original chat.",
                         "mode": "manual", "members": roster(), "concurrency": 2, "round_limit": 2}
            result = await ctx.client_tool_call("fixture-delegate", "kira.team_delegate", arguments)
            assert result["ok"]
            assert await ctx.client_tool_call("fixture-delegate", "kira.team_delegate", arguments) == result
            (self.control / "delegation-requested").write_text(result["delegation_id"])
            await self.wait_gate("release-origin", ctx)
            await ctx.emit("message.completed", {"content": "Synthetic origin queued a managed team."})
            return {"state": "completed", "outcome": {"fixture": True}}
        if role == "reviewer":
            assert ctx.permission == "read_only"
            result = await ctx.execute_tool("read_file", {"path": "evidence.txt"})
            assert result["ok"] and "SYNTHETIC_EVIDENCE_MARKER" in result["content"]
            (self.control / "reviewer-started").write_text(ctx.run_id)
            await self.wait_gate("release-reviewer", ctx)
            text = "Synthetic reviewer read SYNTHETIC_EVIDENCE_MARKER through the service file tool."
            await ctx.emit("message.completed", {"content": text})
            return {"state": "completed", "outcome": {"fixture": True, "final_text": text}}
        if link["round_index"] == 1:
            plan = {"decision": "continue", "summary": "Synthetic review assigned.", "members": [],
                    "tasks": [{"id": "review", "title": "Read fixture evidence", "member_id": "reviewer",
                               "instructions": "Read evidence.txt and report the synthetic marker.",
                               "access": "read", "depends_on": []}]}
        else:
            plan = {"decision": "completed", "summary": RESULT, "members": [], "tasks": []}
        text = json.dumps(plan)
        await ctx.emit("message.completed", {"content": text})
        return {"state": "completed", "outcome": {"fixture": True, "final_text": text}}

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        if self.run_id:
            (self.control / (self.run_id + ".closed")).touch()


async def main():
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("This fixture service only runs on an isolated KIRA_HOME profile")
    control = Path(os.environ["KIRA_TEST_ASSISTANT_CONTROL"])
    service = Service(backend_factory=lambda *_: AssistantFixtureBackend(service, control))
    await service.start()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
