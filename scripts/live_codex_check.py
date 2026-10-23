#!/usr/bin/env python3
"""Live acceptance task. Uses real Codex, costs a real turn; never a fixture.

Run: KIRA_HOME=$PWD/build/live-state uv run python scripts/live_codex_check.py
Input is generated outside the model conversation. The controller independently
checks actual file contents, required tool observations and reconnect/idempotency.
"""

import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

from kira.client import Client, ensure_service
from kira.domain.contracts import TERMINAL_STATES

ROOT = Path(__file__).resolve().parents[1]


def evidence_path() -> Path:
    return Path(os.environ.get("KIRA_LIVE_EVIDENCE", str(ROOT / "build/evidence/live-codex.json")))


async def main() -> None:
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("Set KIRA_HOME to an isolated test data store")
    evidence = evidence_path()
    evidence.parent.mkdir(parents=True, exist_ok=True)
    project_root = ROOT / "build" / f"live-project-{uuid.uuid4().hex[:8]}"
    project_root.mkdir(parents=True)
    number = secrets.randbelow(900_000) + 100_000
    input_path = project_root / "input.json"
    input_path.write_text(json.dumps({"value": number}) + "\n")
    before = hashlib.sha256(input_path.read_bytes()).hexdigest()
    await ensure_service()
    client = Client()
    await client.connect()
    project = (await client.request("project.create", {
        "name": "Verifica reale Codex", "root": str(project_root),
        "permission": "trusted_host", "instructions": "Work only within this disposable project.",
    }))["project"]
    session = (await client.request("session.create", {
        "project_id": project["id"], "backend": "codex", "model": "gpt-6-astra",
        "settings": {"reasoning_effort": "ultra"},
    }))["session"]
    prompt = (
        "Read input.json using a real file tool or command. It contains an integer value. "
        "Use your apply_patch file-edit tool to create result.json with the keys original and result, where original equals that "
        "integer and result is its square plus 17. Then run a Python command that independently "
        "loads both files and asserts that relationship. Do not change input.json. "
        "Do not just describe commands: execute them and report the actual verification. "
        "Only use files inside this project."
    )
    request_id = str(uuid.uuid4())
    run_params = {"session_id": session["id"], "prompt": prompt, "request_id": request_id}
    run = (await client.request("run.start", run_params))["run"]
    run_id = run["id"]
    started = time.monotonic()
    await client.request("run.subscribe", {"run_id": run_id, "after_seq": 0})
    # Actually disconnect during an active provider request. Service retains ownership.
    await asyncio.sleep(2)
    await client.close()
    await asyncio.sleep(1)
    client = Client()
    await client.connect()
    duplicate = (await client.request("run.start", run_params))["run"]
    assert duplicate["id"] == run_id, "Reconnect retry launched duplicate execution"
    events: dict[int, dict] = {}
    cursor = 0
    try:
        while time.monotonic() - started < 480:
            replay = await client.request("run.events", {"run_id": run_id, "after_seq": cursor,
                                                         "limit": 500})
            for event in replay["events"]:
                events[event["seq"]] = event
                cursor = event["seq"]
            snapshot = await client.request("project.snapshot", {"project_id": project["id"]})
            run = next(r for r in snapshot["runs"] if r["id"] == run_id)
            print(f"{run['state']} | events {cursor}", flush=True)
            if run["state"] in TERMINAL_STATES and not replay.get("has_more"):
                break
            await asyncio.sleep(2)
        else:
            await client.request("run.cancel", {"run_id": run_id})
            raise TimeoutError("Live acceptance turn exceeded controller's 480 second deadline")
        # Terminal event can commit after the replay query and before snapshot.
        tail = await client.request("run.events", {"run_id": run_id, "after_seq": cursor,
                                                   "limit": 500})
        for event in tail["events"]:
            events[event["seq"]] = event
        result_path = project_root / "result.json"
        actual = json.loads(result_path.read_text()) if result_path.exists() else None
        expected = {"original": number, "result": number * number + 17}
        file_verified = actual == expected and hashlib.sha256(input_path.read_bytes()).hexdigest() == before
        kinds = [e["type"] for e in events.values()]
        contiguous = sorted(events) == list(range(1, run["last_seq"] + 1))
        report = {
            "test_kind": "LIVE, real Codex App Server", "model": "gpt-6-astra",
            "reasoning_effort": "ultra", "project_root": str(project_root),
            "kira_home": os.environ["KIRA_HOME"], "project_id": project["id"],
            "session_id": session["id"], "run_id": run_id, "state": run["state"],
            "error": run.get("error"), "duration_seconds": round(time.monotonic() - started, 3),
            "controller_verified_file": file_verified, "input_sha256": before,
            "expected": expected, "actual": actual, "duplicate_request_same_run": True,
            "ipc_disconnect_reconnect": True, "contiguous_sequences": contiguous,
            "event_types": kinds,
            "events": [e for e in events.values() if e["type"] in {
                "tool.started", "tool.completed", "artifact.created", "usage", "run.state"}],
        }
        evidence.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        assert run["state"] == "completed", run
        assert file_verified, (expected, actual)
        assert contiguous, (sorted(events), run["last_seq"])
        assert "tool.started" in kinds and "tool.completed" in kinds, "No observed native tools"
        print(f"PASS: controller independently verified result; evidence {evidence}")
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"LIVE CHECK FAILED: {exc}", file=sys.stderr)
        raise
