"""Real disposable service with synthetic Team replies; no model access."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from kira.service.server import Service

MODEL = "synthetic-team-orchestration-model"
MARKER = "SYNTHETIC_TEAM_ORCHESTRATION_EVIDENCE"
RESULT = "Synthetic team verified the recorded reader result."
CONTINUED = "Synthetic continuation reviewed the original evidence without replay."
INSTRUCTION = "Summarize the recorded reader result without repeating the file read."


def roster():
    return [{"id": identifier, "name": identifier.title(),
             "role": "Coordinate fixture review" if identifier == "coordinator" else "Read fixture evidence",
             "instructions": "Inspect only the synthetic fixture and its recorded results.",
             "access": "read"} for identifier in ("coordinator", "reader")]


class TeamOrchestrationBackend:
    def __init__(self, service, control):
        self.service, self.control = service, control
        self.process = None
        self.run_id = None
        self.is_resume_origin = False

    async def discover(self):
        return {"available": True, "fixture": True, "capabilities": {"tools": True},
                "account": {"connected": True, "type": "synthetic"},
                "models": [{"id": MODEL, "name": "Synthetic Team orchestration fixture",
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
        # Register an actual owned child so the production cleanup gate runs.
        # It is a harmless disposable sleeper, never an inference process.
        self.run_id = ctx.run_id
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(120)", start_new_session=True,
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
                "team_id": link["team_id"] if link else followup["related_team_id"] if followup else None,
                "round_index": link["round_index"] if link else None,
                "pid": self.process.pid}) + "\n")
        if followup:
            assert not link
            team = (await self.service.dispatch("team.snapshot", {"team_id": followup["related_team_id"]}))["team"]
            assert team["summary"] in {RESULT, CONTINUED} and team["summary"] in ctx.supplied_context
            text = "Synthetic automatic Team followup: " + team["summary"]
            await ctx.emit("message.completed", {"content": text})
            return {"state": "completed", "outcome": {"fixture": True, "final_text": text}}
        if not link:
            return await self.origin(ctx)
        metadata, _ = json.JSONDecoder().raw_decode(
            ctx.supplied_context.split("\nService-managed team context:\n", 1)[1])
        team = self.service.teams.data.one(link["team_id"])
        assert team["round_limit"] == 3
        if team["parent_team_id"]:
            assert role == "coordinator", "A summary-only continuation replayed a worker"
            return await self.continuation(ctx, metadata, team)
        if role == "reader":
            result = await ctx.execute_tool("read_file", {"path": "evidence.txt"})
            assert result["ok"] and MARKER in result["content"]
            self.publish("reader-executed", {"run_id": ctx.run_id, "result": result})
            await self.wait_gate("release-reader", ctx)
            text = "Synthetic reader observed " + MARKER + " through read_file."
            await ctx.emit("message.completed", {"content": text})
            return {"state": "completed", "outcome": {"fixture": True, "final_text": text}}
        if link["round_index"] == 1:
            result = await ctx.client_tool_call("fixture-mailbox", "team.send_message", {
                "recipient_id": "reader", "content": "Read the synthetic fixture when an assignment arrives."})
            assert result["ok"]
            assert not self.service.teams.data.steps(team["id"])
            plan = {"decision": "completed", "summary": "Synthetic premature conclusion is unverified.",
                    "members": [], "tasks": []}
        elif link["round_index"] == 2:
            feedback = metadata["planning_feedback"]
            assert feedback["code"] == "missing_worker_assignments"
            assert feedback["summary_verified"] is False and feedback["repair_scheduled"] is True
            assert feedback["remaining_coordinator_turns"] == 2
            assert not self.service.teams.data.steps(team["id"])
            self.publish("correction-ready", {"team_id": team["id"], "feedback": feedback})
            await self.wait_gate("release-corrected-plan", ctx)
            plan = {"decision": "continue", "summary": "Synthetic reader assignment ready.", "members": [],
                    "tasks": [{"id": "read-evidence", "title": "Read synthetic evidence", "member_id": "reader",
                               "instructions": "Read evidence.txt and report the synthetic marker.",
                               "access": "read", "depends_on": []}]}
        else:
            assert link["round_index"] == 3
            assert MARKER in json.dumps(metadata["previous_results"])
            assert metadata["completion_evidence"]["kind"] == "worker_assignments"
            assert metadata["completion_evidence"]["completed_worker_assignments"] == 1
            plan = {"decision": "completed", "summary": RESULT, "members": [], "tasks": []}
        return await self.plan(ctx, plan)

    async def origin(self, ctx):
        existing = self.control / "delegation-requested"
        if not existing.exists():
            arguments = {"goal": "Verify synthetic Team orchestration evidence",
                         "brief": "Assign the reader a real task to read evidence.txt, then inspect its result.",
                         "mode": "manual", "members": roster(), "concurrency": 2}
            result = await ctx.client_tool_call("fixture-delegate", "kira.team_delegate", arguments)
            assert result["ok"] and result["state"] == "pending" and result["team_id"] is None
            self.publish("delegation-requested", {**result, "origin_run_id": ctx.run_id})
            await self.wait_gate("release-origin", ctx)
        else:
            self.is_resume_origin = True
            source = json.loads(existing.read_text())
            result = await ctx.client_tool_call("fixture-resume", "kira.team_resume", {
                "delegation_id": source["delegation_id"], "instruction": INSTRUCTION})
            assert result["ok"] and result["state"] == "pending" and result["team_id"] is None
            assert result["action"] == "resume" and result["source_delegation_id"] == source["delegation_id"]
            assert await ctx.client_tool_call("fixture-resume", "kira.team_resume", {
                "delegation_id": source["delegation_id"], "instruction": INSTRUCTION}) == result
            assert len(self.service.store.all("SELECT id FROM team_runs")) == 1
            self.publish("resume-requested", {**result, "origin_run_id": ctx.run_id})
            await self.wait_gate("release-resume-origin", ctx)
        await ctx.emit("message.completed", {"content": "Synthetic origin queued the requested managed work."})
        return {"state": "completed", "outcome": {"fixture": True}}

    async def continuation(self, ctx, metadata, team):
        request = json.loads((self.control / "resume-requested").read_text())
        assert (self.control / (request["origin_run_id"] + ".closed")).exists()
        cleanup = self.service.store.db.execute("SELECT state FROM run_cleanup WHERE run_id=?",
                                                (request["origin_run_id"],)).fetchone()[0]
        assert cleanup == "stopped"
        assert team["parent_team_id"] == request["source_team_id"]
        assert metadata["previous_execution"]["automatic_replay"] is False
        assert MARKER in json.dumps(metadata["previous_execution"]["results"])
        assert metadata["continuation_instruction"] == INSTRUCTION
        basis = metadata["completion_evidence"]
        assert basis["kind"] == "prior_worker_assignments"
        assert basis["source_team_id"] == team["parent_team_id"]
        self.publish("continuation-started", {"team_id": team["id"], "parent_team_id": team["parent_team_id"],
                                              "origin_cleanup": cleanup, "completion_basis": basis})
        await self.wait_gate("release-continuation", ctx)
        return await self.plan(ctx, {"decision": "completed", "summary": CONTINUED, "members": [], "tasks": []})

    @staticmethod
    async def plan(ctx, value):
        text = json.dumps(value)
        await ctx.emit("message.completed", {"content": text})
        return {"state": "completed", "outcome": {"fixture": True, "final_text": text}}

    async def close(self):
        if self.is_resume_origin:
            request = json.loads((self.control / "resume-requested").read_text())
            delegation = self.service.store.db.execute(
                "SELECT state,team_id FROM assistant_delegations WHERE id=?", (request["delegation_id"],)).fetchone()
            assert delegation["state"] == "pending" and delegation["team_id"] is None
            assert len(self.service.store.all("SELECT id FROM team_runs")) == 1
            self.publish("resume-before-cleanup", {"state": delegation["state"], "team_id": delegation["team_id"]})
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        if self.run_id:
            (self.control / (self.run_id + ".closed")).touch()


async def main():
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("This fixture service only runs on an isolated KIRA_HOME profile")
    control = Path(os.environ["KIRA_TEST_TEAM_ORCHESTRATION_CONTROL"])
    service = Service(backend_factory=lambda *_: TeamOrchestrationBackend(service, control))
    await service.start()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
