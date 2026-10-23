"""Hard process death around actual local file effects, with a fixture provider."""

import asyncio
import os
import signal
import sys
import uuid
from pathlib import Path

import pytest

from kira.client import Client
from kira.domain.paths import Paths

FIXTURE = Path(__file__).parent / "fixtures/crash_service.py"


def log_tail(log):
    try:
        return log.read_bytes()[-20_000:].decode("utf-8", "replace")
    except OSError:
        return "(no service log)"


async def wait_for_client(paths, process, log, timeout=30):
    """Connect to a fixture service process, reporting its log if it exits first.

    The fixture's stderr goes to a file so a talkative service can never block on
    an unread pipe. Three seconds was flaky during package builds; this is only an
    upper bound.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if process.returncode is not None:
            raise RuntimeError(f"Fixture service exited ({process.returncode}): {log_tail(log)}")
        client = Client(paths)
        try:
            await client.connect()
            return client
        except (OSError, ConnectionError):
            await client.close()
            await asyncio.sleep(0.02)
    raise TimeoutError(f"Fixture service did not start within {timeout} s: {log_tail(log)}")


async def read_marker(path, timeout=30):
    """Return a fixture marker once it holds content; fixtures publish it atomically."""
    async with asyncio.timeout(timeout):
        while True:
            try:
                if content := path.read_text():
                    return content
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("phase", ["before", "after", "after_uncommitted"])
async def test_sigkill_recovery_never_replays_uncertain_effects(tmp_path, phase):
    # Short runtime path respects Linux sockaddr_un limits under pytest temp roots.
    base = tmp_path / "k"
    paths = Paths(*(base / p for p in ("config", "data", "state", "runtime")))
    project_root = tmp_path / "project"
    project_root.mkdir()
    marker = tmp_path / "marker"
    log = tmp_path / "service.log"
    env = {**os.environ, "KIRA_HOME": str(base), "KIRA_TEST_MARKER": str(marker)}

    async def spawn():
        with log.open("ab") as stream:
            return await asyncio.create_subprocess_exec(
                sys.executable, str(FIXTURE), env=env, stdout=asyncio.subprocess.DEVNULL,
                stderr=stream,
            )

    process = await spawn()
    client = None
    try:
        client = await wait_for_client(paths, process, log)
        project = (await client.request("project.create", {
            "name": "FIXTURE: crash recovery", "root": str(project_root),
            "permission": "project_write",
        }))["project"]
        session = (await client.request("session.create", {
            "project_id": project["id"], "backend": "ollama", "model": "FIXTURE-crash",
        }))["session"]
        params = {"session_id": session["id"], "prompt": phase, "request_id": str(uuid.uuid4())}
        run = (await client.request("run.start", params))["run"]
        assert await read_marker(marker) == phase
        assert (project_root / "effect.txt").exists() == (phase != "before")
        # Kill the exact child held by asyncio; no persisted/reused PID is consulted.
        process.send_signal(signal.SIGKILL)
        assert await process.wait() == -signal.SIGKILL
        await client.close()
        process = await spawn()
        client = await wait_for_client(paths, process, log)
        snapshot = await client.request("project.snapshot", {"project_id": project["id"]})
        assert len(snapshot["runs"]) == 1
        recovered = snapshot["runs"][0]
        assert recovered["state"] == "interrupted"
        assert recovered["outcome"] == {"effects": "unknown", "automatic_replay": False}
        duplicate = (await client.request("run.start", params))["run"]
        assert duplicate["id"] == run["id"] and duplicate["state"] == "interrupted"
        assert marker.with_suffix(".calls").read_text().splitlines() == [run["id"]]
        assert (project_root / "effect.txt").exists() == (phase != "before")
        events = (await client.request("run.events", {"run_id": run["id"]}))["events"]
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        completed_tools = [e for e in events if e["type"] == "tool.completed"]
        assert len(completed_tools) == (1 if phase == "after" else 0)
        if completed_tools:
            assert completed_tools[0]["payload"]["result"]["ok"] is True
        if phase == "after_uncommitted":
            assert any(e["type"] == "tool.started" for e in events)
            assert not completed_tools, "Recovery must not fabricate an uncommitted tool result"
        await client.request("service.shutdown")
        assert await asyncio.wait_for(process.wait(), 30) == 0
    finally:
        if client:
            await client.close()
        if process.returncode is None:
            process.kill()
            await process.wait()
