#!/usr/bin/env python3
"""Real Codex resume, GUI close/reopen and confirmed cancellation acceptance."""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from kira.client import Client, ensure_service
from kira.domain.contracts import TERMINAL_STATES

ROOT = Path(__file__).resolve().parents[1]


def source_evidence_path() -> Path:
    return Path(os.environ.get("KIRA_LIVE_EVIDENCE", str(ROOT / "build/evidence/live-codex.json")))


def evidence_directory() -> Path:
    return ROOT / "build/evidence"


def owned_process_alive(identity: dict) -> bool:
    try:
        fields = Path(f"/proc/{identity['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0] != "Z" and fields[19] == identity["start_ticks"]
    except (OSError, IndexError):
        return False


async def main() -> None:
    evidence = evidence_directory()
    evidence.mkdir(parents=True, exist_ok=True)
    previous = json.loads(source_evidence_path().read_text())
    if os.environ.get("KIRA_HOME") != previous["kira_home"]:
        raise SystemExit("Set KIRA_HOME to the data store in live-codex.json")
    await ensure_service()
    async with Client() as client:
        project_id = previous["project_id"]
        session_id = previous["session_id"]
        snapshot = await client.request("project.snapshot", {"project_id": project_id})
        backend_ref = next(s for s in snapshot["sessions"] if s["id"] == session_id)["backend_ref"]
        marker = Path(previous["project_root"]) / "cancel-process.json"
        marker.unlink(missing_ok=True)
        code = (
            "import os,json,time; from pathlib import Path; "
            "p=os.getpid(); ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]; "
            "Path('cancel-process.json').write_text(json.dumps({'pid':p,'start_ticks':ticks})); "
            "print('Kira cancellation check is ready',flush=True); time.sleep(120)"
        )
        prompt = (
            "This is a deliberate interruption acceptance test. Execute exactly this Python "
            "command in the project directory as a foreground command and wait for it: "
            + json.dumps(["python3", "-c", code]) + ". Do not change any other file. "
            "The desktop will cancel the turn after reconnecting."
        )
        run = (await client.request("run.start", {"session_id": session_id,
            "prompt": prompt, "request_id": str(uuid.uuid4())}))["run"]
        run_id = run["id"]
        started = time.monotonic()
        while not marker.exists() and time.monotonic() - started < 180:
            await asyncio.sleep(1)
            snapshot = await client.request("project.snapshot", {"project_id": project_id})
            state = next(r for r in snapshot["runs"] if r["id"] == run_id)["state"]
            if state in TERMINAL_STATES:
                raise AssertionError(f"Turn ended before long command: {state}")
        assert marker.exists(), "Native command did not create its process identity file"
        identity = json.loads(marker.read_text())
        assert owned_process_alive(identity), "Long command is not actually running"
        print(f"Live command running, opening/closing GUI for run {run_id}", flush=True)
        gui_env = {**os.environ, "QT_QUICK_BACKEND": "software"}

        async def gui(filename: str, stop: bool = False):
            command = [sys.executable, str(ROOT / "scripts/gui_evidence.py"),
                "--project-id", project_id, "--expect-active", run_id,
                "--output", str(evidence / filename)]
            if stop:
                command.append("--stop")
            process = await asyncio.create_subprocess_exec(*command, env=gui_env)
            assert await asyncio.wait_for(process.wait(), 45) == 0

        await gui("ui-active-before-close.png")
        assert owned_process_alive(identity), "Closing GUI terminated the command"
        print("GUI closed; native command still alive. Reopening and requesting Stop.", flush=True)
        await gui("ui-cancelled-after-reconnect.png", stop=True)
        snapshot = await client.request("project.snapshot", {"project_id": project_id})
        final = next(r for r in snapshot["runs"] if r["id"] == run_id)
        session = next(s for s in snapshot["sessions"] if s["id"] == session_id)
        for _ in range(50):
            if not owned_process_alive(identity):
                break
            await asyncio.sleep(0.1)
        report = {
            "kind": "LIVE Codex plus actual Qt GUI", "model": "gpt-6-astra",
            "reasoning_effort": "ultra", "run_id": run_id, "state": final["state"],
            "outcome": final.get("outcome"), "error": final.get("error"),
            "thread_resumed": backend_ref == session["backend_ref"],
            "gui_closed_during_active_command": True, "command_survived_gui_close": True,
            "gui_reopened_and_stop_requested": True,
            "process_identity": identity, "process_termination_verified": not owned_process_alive(identity),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        (evidence / "live-continuity.json").write_text(json.dumps(report, indent=2) + "\n")
        assert final["state"] == "cancelled", final
        assert report["thread_resumed"]
        assert report["process_termination_verified"], "Native command remains alive after cancellation"
        print("PASS: live GUI continuity, thread resume and native command cancellation verified")


if __name__ == "__main__":
    asyncio.run(main())
