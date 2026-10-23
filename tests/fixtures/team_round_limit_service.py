"""Disposable production scheduler with synthetic replies and registered children."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from kira.service.server import Service

MODEL = "synthetic-team-round-limit-model"
ORIGINAL = "SYNTHETIC_ORIGINAL_TEAM_EVIDENCE"
VERIFIED = "SYNTHETIC_FRESH_VERIFICATION_EVIDENCE"
RESULT = "Synthetic continuation verified a fresh result and completed its second coordinator turn."


class RoundLimitBackend:
    def __init__(self, service, control):
        self.service, self.control = service, control
        self.process = None
        self.run_id = None

    async def discover(self):
        return {"available": True, "fixture": True, "capabilities": {"tools": True},
                "account": {"connected": True, "type": "synthetic", "id": "synthetic-account"},
                "models": [{"id": MODEL, "name": "Synthetic Team round-limit fixture",
                            "supported_reasoning_efforts": ["high", "xhigh"],
                            "capabilities": {"tools": True}}]}

    def publish(self, name, value):
        temporary = self.control / (name + ".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(self.control / name)

    async def wait_gate(self, name, ctx):
        while not (self.control / name).exists():
            if ctx.cancel.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(0.01)

    async def run(self, ctx):
        self.run_id = ctx.run_id
        # The production process registry and cleanup gates observe a real child;
        # this isolated sleeper does not run a model or read account credentials.
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", "import time; time.sleep(120)", start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        await ctx.provider_started(self.process.pid)
        link = self.service.teams.data.link(ctx.run_id)
        records = (await self.service.dispatch("run.events", {"run_id": ctx.run_id}))["events"]
        followup = next((event["payload"] for event in records if event["type"] == "backend.notice"
                         and event["payload"].get("source") == "team_followup"), None)
        role = link["member_id"] if link else "followup" if followup else "origin"
        with (self.control / "provider-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"run_id": ctx.run_id, "role": role, "model": ctx.model,
                "effort": ctx.settings.get("reasoning_effort"), "permission": ctx.permission,
                "account_id": ctx.settings.get("account_id"),
                "team_id": link["team_id"] if link else followup["related_team_id"] if followup else None,
                "round_index": link["round_index"] if link else None,
                "pid": self.process.pid}) + "\n")
        if followup:
            assert not link
            team = (await self.service.dispatch("team.snapshot", {"team_id": followup["related_team_id"]}))["team"]
            assert team["state"] in {"paused", "completed"} and team["id"] in ctx.supplied_context
            text = "Synthetic round-limit Team followup: " + team["state"]
            await ctx.emit("message.completed", {"content": text})
            return {"state": "completed", "outcome": {"fixture": True, "final_text": text}}
        if not link:
            assert not (self.control / "delegation-requested").exists(), "Ordinary turn replayed"
            members = [{"id": member, "name": member.title(), "role": "Synthetic project review",
                        "instructions": "Read the requested synthetic project file.", "access": "read"}
                       for member in ("coordinator", "reader")]
            result = await ctx.client_tool_call("fixture-delegate", "kira.team_delegate", {
                "goal": "Verify synthetic evidence and synthesize the result",
                "brief": "Assign a read-only worker task, then verify its result.",
                "mode": "manual", "members": members, "concurrency": 2, "round_limit": 1})
            assert result["ok"] and result["state"] == "pending"
            self.publish("delegation-requested", {**result, "origin_run_id": ctx.run_id})
            await self.wait_gate("release-origin", ctx)
            await ctx.emit("message.completed", {"content": "Synthetic Team work queued with one coordinator turn."})
            return {"state": "completed", "outcome": {"fixture": True}}

        team = self.service.teams.data.one(link["team_id"])
        continuation = bool(team["parent_team_id"])
        assert team["round_limit"] == (0 if continuation else 1)
        metadata, _ = json.JSONDecoder().raw_decode(
            ctx.supplied_context.split("\nService-managed team context:\n", 1)[1])
        if role == "reader":
            filename, marker = ("verification.txt", VERIFIED) if continuation else ("evidence.txt", ORIGINAL)
            result = await ctx.execute_tool("read_file", {"path": filename})
            assert result["ok"] and marker in result["content"]
            self.publish("child-reader-executed" if continuation else "parent-reader-executed",
                         {"run_id": ctx.run_id, "team_id": team["id"], "result": result})
            text = "Synthetic reader observed " + marker + " through read_file."
            await ctx.emit("message.completed", {"content": text})
            return {"state": "completed", "outcome": {"fixture": True, "final_text": text}}

        assert role == "coordinator"
        if link["round_index"] == 1:
            if continuation:
                assert metadata["previous_execution"]["automatic_replay"] is False
                assert ORIGINAL in json.dumps(metadata["previous_execution"]["results"])
                self.publish("continuation-started", {"team_id": team["id"],
                    "parent_team_id": team["parent_team_id"], "round_limit": team["round_limit"]})
                await self.wait_gate("release-continuation", ctx)
            task_id = "verify-fresh-evidence" if continuation else "read-original-evidence"
            filename = "verification.txt" if continuation else "evidence.txt"
            plan = {"decision": "continue", "summary": "Read-only worker verification scheduled.", "members": [],
                    "tasks": [{"id": task_id, "title": "Read synthetic evidence", "member_id": "reader",
                               "instructions": f"Read {filename} and report its synthetic marker.",
                               "access": "read", "depends_on": []}]}
        else:
            assert continuation and link["round_index"] == 2
            assert VERIFIED in json.dumps(metadata["previous_results"])
            assert ORIGINAL in json.dumps(metadata["previous_execution"]["results"])
            assert metadata["completion_evidence"]["kind"] == "worker_assignments"
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
    control = Path(os.environ["KIRA_TEST_TEAM_ROUND_LIMIT_CONTROL"])
    service = Service(backend_factory=lambda *_: RoundLimitBackend(service, control))
    await service.start()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
