#!/usr/bin/env python3
"""Two benign real Codex turns qualify project chat continuity over private IPC.

Uses a fresh temporary KIRA_HOME and synthetic project roots. Existing user data,
provider configuration and authentication are not edited. Run only when real
provider use is authorized; both turns consume actual provider resources.
"""

import asyncio
import hashlib
import json
import os
import secrets
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from kira import __version__
from kira.client import Client
from kira.domain.contracts import TERMINAL_STATES
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import new_id

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "build/evidence/project-chats/live-context.json"
MODEL = "gpt-6.1-sol"
EFFORT = "xhigh"


async def real_turn(client, service, chat, prompt, report):
    run = (await client.request("run.start", {
        "session_id": chat["id"], "prompt": prompt, "request_id": new_id(),
    }))["run"]
    started = time.monotonic()
    task = service.tasks.get(run["id"])
    try:
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 180)
    except TimeoutError:
        await client.request("run.cancel", {"run_id": run["id"]})
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), 10)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        raise TimeoutError("Owned live fixture turn exceeded 180 seconds") from None
    events, cursor = [], 0
    while True:
        replay = await client.request("run.events", {"run_id": run["id"], "after_seq": cursor, "limit": 500})
        events.extend(replay["events"])
        if not replay["has_more"]:
            break
        cursor = events[-1]["seq"]
    actual = service.store.one("runs", run["id"])
    public = {"run_id": run["id"], "session_id": chat["id"], "state": actual["state"],
              "duration_seconds": round(time.monotonic() - started, 3),
              "event_count": len(events), "usage": [e["payload"] for e in events if e["type"] == "usage"],
              "tool_calls": sum(e["type"] == "tool.started" for e in events)}
    if actual.get("error"):
        public["error"] = actual["error"][:1000]
    report["turns"].append(public)
    print(f"Real fixture turn {len(report['turns'])}: {actual['state']}, {len(events)} durable events", flush=True)
    if actual["state"] not in TERMINAL_STATES or actual["state"] != "completed":
        raise RuntimeError(f"Real fixture turn ended {actual['state']}: {actual.get('error')}")
    assert public["tool_calls"] == 0, "Unexpected tool call in no-tools probe"
    answer = "\n".join(e["payload"].get("content", "") for e in events if e["type"] == "message.completed")
    supplied = "\n".join(e["payload"].get("content", "") for e in events if e["type"] == "context.supplied")
    return answer, supplied


async def main():
    report = {"status": "NOT_RUN", "test_kind": "LIVE: two real Codex App Server turns over private IPC",
              "kira_version": __version__, "timestamp_utc": datetime.now(UTC).isoformat(),
              "model": MODEL, "reasoning_effort": EFFORT, "permission": "read_only",
              "turn_timeout_seconds": 180, "turns": [],
              "unrelated_project_canary": "Synthetic stored history only; no provider turn in project Q",
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    previous_home = os.environ.get("KIRA_HOME")
    try:
        with tempfile.TemporaryDirectory(prefix="kira-chat-live-") as directory:
            isolated = Path(directory)
            os.environ["KIRA_HOME"] = str(isolated / "state")
            paths = Paths.from_env()
            service = Service(paths)
            await service.start()
            client = Client(paths)
            try:
                await client.connect()
                projects = []
                for name in ("P", "Q"):
                    root = isolated / name
                    root.mkdir()
                    projects.append((await client.request("project.create", {
                        "name": f"Live chat qualification {name}", "root": str(root),
                        "permission": "read_only",
                        "instructions": "Benign continuity check. Do not invoke tools, read files or use a browser. Reply only to the text request.",
                    }))["project"])
                chats = []
                for project, title in ((projects[0], "Marker origin"), (projects[0], "Marker recall"), (projects[1], "Synthetic isolation canary")):
                    chats.append((await client.request("session.create", {
                        "project_id": project["id"], "backend": "codex", "model": MODEL,
                        "settings": {"reasoning_effort": EFFORT}, "title": title,
                    }))["session"])
                marker = "KIRA-MARKER-" + secrets.token_hex(12)
                canary = "SYNTHETIC-OTHER-PROJECT-" + secrets.token_hex(12)
                synthetic, _ = service.store.create_run(chats[2]["id"], new_id(), canary)
                service.store.event(synthetic["id"], "message.completed", {"content": canary})
                service.store.event(synthetic["id"], "run.state", {"state": "completed"})
                report["status"] = "FAIL"
                first, _ = await real_turn(client, service, chats[0],
                    f"The project marker is {marker}. Acknowledge by replying only with that exact marker. No tools, files or browser use.", report)
                assert marker in first, "First real turn did not acknowledge the generated marker"
                preview = await client.request("project.context", {"project_id": projects[0]["id"], "session_id": chats[1]["id"]})
                second, supplied = await real_turn(client, service, chats[1],
                    "What project marker was recorded in the other chat of this project? Reply only with the exact marker. No tools, files or browser use.", report)
                refs = [service.store.one("sessions", chat["id"])["backend_ref"] for chat in chats[:2]]
                checks = {
                    "first_turn_acknowledged_marker": marker in first,
                    "second_turn_recalled_marker_without_repeating_it_in_prompt": marker in second,
                    "context_api_contains_source_marker": marker in preview["content"],
                    "context_api_excludes_other_project_canary": canary not in json.dumps(preview),
                    "actual_supplied_context_contains_exact_preview": preview["content"] in supplied,
                    "actual_supplied_context_references_source_session": chats[0]["id"] in supplied,
                    "actual_supplied_context_excludes_other_project_canary": canary not in supplied,
                    "distinct_nonempty_provider_threads": bool(refs[0] and refs[1] and refs[0] != refs[1]),
                    "context_chars_within_budget": preview["chars"] <= 6000,
                }
                report["checks"] = checks
                report["source_count"] = len(preview["sources"])
                report["context_chars"] = preview["chars"]
                assert all(checks.values()), checks
                report["status"] = "PASS"
            finally:
                await client.close()
                await service.close()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"[:1500]
    finally:
        if previous_home is None:
            os.environ.pop("KIRA_HOME", None)
        else:
            os.environ["KIRA_HOME"] = previous_home
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"{report['status']}: {EVIDENCE}", flush=True)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
