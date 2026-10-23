"""Real IPC, SIGKILL and child cleanup with an explicitly synthetic provider."""

import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import uuid

from kira.domain.paths import Paths
from kira.domain.teams import TEAM_TERMINAL_STATES
from test_integration_recovery import wait_for_client

FIXTURE = Path(__file__).parent / "fixtures/team_crash_service.py"


def alive(identity):
    try:
        fields = Path(f"/proc/{identity['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        # Z (zombie) and X (being reaped) have both finished executing.
        return fields[0] not in {"Z", "X"} and fields[19] == identity["start_ticks"]
    except (FileNotFoundError, ProcessLookupError):
        return False


async def test_team_crash_reconciles_owned_child_and_never_replays_work(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-team-crash-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        root = tmp_path / "project"
        root.mkdir()
        marker = tmp_path / "started"
        log = tmp_path / "service.log"
        env = {**os.environ, "KIRA_HOME": directory, "KIRA_TEST_MARKER": str(marker)}

        async def spawn():
            with log.open("ab") as stream:
                return await asyncio.create_subprocess_exec(
                    sys.executable, str(FIXTURE), env=env, stdout=asyncio.subprocess.DEVNULL,
                    stderr=stream)

        process = await spawn()
        client = None
        child_identity = None
        try:
            client = await wait_for_client(paths, process, log)
            project = (await client.request("project.create", {
                "name": "FIXTURE: team crash recovery", "root": str(root),
                "permission": "project_write"}))["project"]
            params = {"project_id": project["id"], "request_id": str(uuid.uuid4()),
                      "goal": "Deterministic recovery fixture", "mode": "manual",
                      "backend": "codex", "model": "FIXTURE-team", "settings": {},
                      "concurrency": 1, "round_limit": 3, "members": [
                          {"id": "coordinator", "name": "Planner", "role": "Fixture planner",
                           "instructions": "Synthetic plan only", "access": "read"},
                          {"id": "worker", "name": "Worker", "role": "Fixture effect",
                           "instructions": "Synthetic owned child", "access": "write"}]}
            team = (await client.request("team.start", params))["team"]
            team_id = team["id"]
            # The fixture child publishes its identity atomically, so existence means complete.
            async with asyncio.timeout(30):
                while not marker.exists():
                    snapshot = (await client.request("team.snapshot", {"team_id": team_id}))["team"]
                    assert snapshot["state"] not in TEAM_TERMINAL_STATES, json.dumps(snapshot)
                    await asyncio.sleep(0.02)
            child_identity = json.loads(marker.read_text())
            assert alive(child_identity)
            assert (root / "effect.txt").is_file()
            calls = marker.with_suffix(".calls").read_text()
            assert len(calls.splitlines()) == 2
            process.send_signal(signal.SIGKILL)
            assert await process.wait() == -signal.SIGKILL
            await client.close()
            assert alive(child_identity), "Fixture child must actually outlive its service"
            process = await spawn()
            client = await wait_for_client(paths, process, log)
            recovered = (await client.request("team.snapshot", {"team_id": team_id}))["team"]
            assert recovered["state"] == "interrupted"
            assert not alive(child_identity), "Recovery must confirm owned child termination"
            heartbeat = (root / "heartbeat.txt").read_bytes()
            await asyncio.sleep(0.1)
            assert (root / "heartbeat.txt").read_bytes() == heartbeat
            assert marker.with_suffix(".calls").read_text() == calls
            duplicate = (await client.request("team.start", params))["team"]
            assert duplicate["id"] == team_id and duplicate["state"] == "interrupted"
            assert marker.with_suffix(".calls").read_text() == calls
            events = (await client.request("team.events", {"team_id": team_id}))["events"]
            assert [event["seq"] for event in events] == list(range(1, recovered["last_seq"] + 1))
            worker_step = next(s for s in recovered["steps"] if s["member_id"] == "worker")
            run_events = (await client.request("run.events", {"run_id": worker_step["run_id"]}))["events"]
            assert any(e["type"] == "tool.started" for e in run_events)
            assert not any(e["type"] == "tool.completed" for e in run_events)
            ordinary = await client.request("project.snapshot", {"project_id": project["id"]})
            assert not ordinary["sessions"], "Team-only sessions must not become ordinary chat targets"
            resumed = (await client.request("team.resume", {
                "team_id": team_id, "request_id": str(uuid.uuid4()),
                "instruction": "Inspect the existing fixture result; do not repeat the effect"}))["team"]
            assert resumed["id"] != team_id and resumed["parent_team_id"] == team_id
            async with asyncio.timeout(30):
                while resumed["state"] not in TEAM_TERMINAL_STATES:
                    await asyncio.sleep(0.02)
                    resumed = (await client.request("team.snapshot", {"team_id": resumed["id"]}))["team"]
            assert resumed["state"] == "completed", resumed
            assert len(marker.with_suffix(".calls").read_text().splitlines()) == 5
            assert len(resumed["steps"]) == 1
            assert resumed["steps"][0]["id"] == "verify" and resumed["steps"][0]["access"] == "read"
            assert resumed["completion_evidence"]["worker_assignments_completed"] == 1
            assert (root / "heartbeat.txt").read_bytes() == heartbeat
            await client.request("service.shutdown")
            assert await asyncio.wait_for(process.wait(), 30) == 0
        finally:
            if client:
                await client.close()
            if process.returncode is None:
                process.kill()
                await process.wait()
            if child_identity and alive(child_identity):
                # Cleanup the fixture's exact identity even when an assertion fails.
                # The child may exit between the check and pidfd_open.
                with contextlib.suppress(ProcessLookupError):
                    descriptor = os.pidfd_open(child_identity["pid"])
                    try:
                        if alive(child_identity):
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    finally:
                        os.close(descriptor)
