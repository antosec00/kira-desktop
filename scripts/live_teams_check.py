#!/usr/bin/env python3
"""Exercise real manual/automatic Codex teams against disposable project files.

Uses the configured provider account and real inference. The controller verifies
files, member mailbox calls, durable events and reconnect independently of prose.
KIRA_HOME must point to an isolated directory beneath this checkout's build/.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from kira import __version__
from kira.client import Client, ensure_service
from kira.domain.teams import TEAM_TERMINAL_STATES

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-6-astra"
SETTINGS = {"reasoning_effort": "ultra"}


def runtime_manifest() -> dict[str, str]:
    files = [ROOT / "src/kira/__init__.py", ROOT / "src/kira/client.py"]
    for component in ("domain", "service", "tools", "backends"):
        files.extend((ROOT / "src/kira" / component).rglob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


async def all_events(client: Client, team_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = await client.request("team.events", {
            "team_id": team_id, "after_seq": cursor, "limit": 250})
        result.extend(page["events"])
        if not page.get("has_more"):
            return result
        assert page["events"], "Replay has_more made no progress"
        cursor = page["events"][-1]["seq"]


async def stop_after_failure(team_id: str) -> dict[str, Any]:
    """The failed request's transport must not determine whether cancellation runs."""
    async with asyncio.timeout(45):
        await ensure_service()
        async with Client() as cleanup:
            await cleanup.request("team.cancel", {"team_id": team_id})
            while True:
                team = (await cleanup.request("team.snapshot", {"team_id": team_id}))["team"]
                if team["state"] in TEAM_TERMINAL_STATES:
                    return {"cancellation_requested": True, "observed_state": team["state"],
                            "snapshot": team, "events": await all_events(cleanup, team_id)}
                await asyncio.sleep(0.2)


async def exercise(mode: str, permission: str, output: Path, deadline: float) -> dict[str, Any]:
    project_root = output / f"{mode}-project-{uuid.uuid4().hex[:8]}"
    project_root.mkdir(mode=0o700)
    number = secrets.randbelow(900_000) + 100_000
    source = project_root / "input.json"
    source.write_text(json.dumps({"value": number}) + "\n")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    success_marker = "KIRA_INDEPENDENT_VERIFIER_OK_" + secrets.token_hex(12)
    verifier_file = project_root / "verify.py"
    verifier_file.write_text(
        "import json\nfrom pathlib import Path\n"
        "root = Path(__file__).resolve().parent\n"
        "source = json.loads((root / 'input.json').read_text())\n"
        "actual = json.loads((root / 'result.json').read_text())\n"
        "assert actual == {'original': source['value'], 'result': source['value'] ** 2 + 17}\n"
        f"print({success_marker!r})\n")
    verifier_hash = hashlib.sha256(verifier_file.read_bytes()).hexdigest()
    client = Client()
    await client.connect()
    team_id = None
    started = time.monotonic()
    report: dict[str, Any] = {"kind": "LIVE Codex project team", "mode": mode,
                             "app_version": __version__, "model": MODEL, "settings": SETTINGS,
                             "permission": permission, "project_root": str(project_root),
                             "runtime_source_sha256": runtime_manifest(),
                             "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        project = (await client.request("project.create", {
            "name": f"Live {mode} team acceptance", "root": str(project_root),
            "permission": permission,
            "instructions": "Use only this disposable directory. No network, installation, "
                            "account changes or changes to other files or projects.",
        }))["project"]
        goal = (
            "Work as a small team on this disposable project. Read input.json with a native tool. "
            "A producer must create result.json with exactly original and result, where original "
            "is the input value and result is its square plus 17. A different member must then "
            "verify both files by executing the provided checker exactly as python3 verify.py. "
            "Preserve input.json and verify.py. The producer must not run the verifier on behalf "
            "of the second member; the verification command belongs to the read-only verifier. "
            "Use separate producer and verifier assignments with an explicit dependency. "
            "Each assigned member must call team.read_messages and send a concrete result to "
            "coordinator through team.send_message. Coordinate using the actual mailbox. "
            "After verification, complete the objective with a brief evidence summary. "
            "No internet, package installation or unrelated commands. In automatic mode choose "
            "a coordinator, one producer and one independent verifier."
        )
        params: dict[str, Any] = {
            "project_id": project["id"], "request_id": str(uuid.uuid4()), "goal": goal,
            "mode": mode, "backend": "codex", "model": MODEL, "settings": SETTINGS,
            "max_agents": 3, "concurrency": 2, "round_limit": 3,
        }
        if mode == "manual":
            params["members"] = [
                {"id": "coordinator", "name": "Coordinator", "role": "Plan and review evidence",
                 "instructions": "Make a producer task and a dependent verifier task, then review.",
                 "access": "read"},
                {"id": "producer", "name": "Producer", "role": "Implement the requested file",
                 "instructions": "Read your mailbox, write result.json, send evidence to coordinator.",
                 "access": "write"},
                {"id": "verifier", "name": "Verifier", "role": "Independent read-only verification",
                 "instructions": "Read mailbox, independently check both files and send evidence.",
                 "access": "read"},
            ]
        team = (await client.request("team.start", params))["team"]
        team_id = team["id"]
        report.update({"project_id": project["id"], "team_id": team_id})
        replay = await client.request("team.subscribe", {"team_id": team_id, "after_seq": 0})
        assert isinstance(replay["events"], list)
        await asyncio.sleep(1)
        await client.close()
        client = Client()
        await client.connect()
        duplicate = (await client.request("team.start", params))["team"]
        assert duplicate["id"] == team_id, "A reconnect retry created another team"
        report["disconnect_reconnect_idempotent"] = True
        last_status = None
        while time.monotonic() - started < deadline:
            team = (await client.request("team.snapshot", {"team_id": team_id}))["team"]
            status = (team["state"], team.get("round_index"), team.get("last_seq"))
            if status != last_status:
                print(f"{mode}: {status}", flush=True)
                last_status = status
            if team["state"] in TEAM_TERMINAL_STATES:
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError(f"Controller's explicit {deadline}s live-team deadline reached")
        events = await all_events(client, team_id)
        target = project_root / "result.json"
        actual = json.loads(target.read_text()) if target.exists() else None
        messages = [e for e in events if e["type"] == "team.message"]
        steps = team.get("steps", [])
        members = team.get("members", [])
        participant_ids = {step["member_id"] for step in steps if step["state"] == "completed"}
        senders = {e["payload"].get("sender_id", e["payload"].get("message", {}).get("sender_id"))
                   for e in messages}
        checks = {
            "team_completed": team["state"] == "completed",
            "file_matches_independent_calculation": actual == {
                "original": number, "result": number * number + 17},
            "input_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == source_hash,
            "controller_checker_unchanged": hashlib.sha256(verifier_file.read_bytes()).hexdigest()
            == verifier_hash,
            "contiguous_team_events": [e["seq"] for e in events] == list(
                range(1, team["last_seq"] + 1)),
            "member_count_bounded": 2 <= len(members) <= 3,
            "distinct_assignees_executed": len(participant_ids) >= 2,
            "assigned_members_sent_messages": participant_ids <= senders,
            "model_preserved": team["model"] == MODEL and all(
                team["settings"].get(key) == value for key, value in SETTINGS.items()),
        }
        if mode == "manual":
            checks["exact_manual_roster"] = {m["id"] for m in members} == {
                "coordinator", "producer", "verifier"}
        child_runs: dict[str, list[dict[str, Any]]] = {}
        for step in steps:
            run_id = step.get("run_id")
            if not run_id:
                continue
            child: list[dict[str, Any]] = []
            cursor = 0
            while True:
                page = await client.request("run.events", {
                    "run_id": run_id, "after_seq": cursor, "limit": 500})
                child.extend(page["events"])
                if not page.get("has_more"):
                    break
                assert page["events"]
                cursor = page["events"][-1]["seq"]
            child_runs[run_id] = child
        checks["actual_native_tool_results"] = sum(
            any(e["type"] == "tool.completed" and
                e["payload"].get("name", "").startswith("codex.") and
                e["payload"].get("result", {}).get("ok") is True for e in rows)
            for rows in child_runs.values()) >= 2
        pairs = [(producer, verifier) for producer in steps for verifier in steps
                 if producer["state"] == verifier["state"] == "completed"
                 and producer["access"] == "write" and verifier["access"] == "read"
                 and producer["member_id"] != verifier["member_id"]
                 and producer["round_index"] == verifier["round_index"]
                 and producer["id"] in verifier["depends_on"]]
        checks["producer_to_independent_verifier_dependency"] = bool(pairs)
        checks["verifier_executed_controller_checker"] = any(
            event["type"] == "tool.completed" and
            event["payload"].get("name") == "codex.commandExecution" and
            event["payload"].get("result", {}).get("ok") is True and
            "python3 verify.py" in str(event["payload"].get("result", {}).get("command", "")) and
            success_marker in event["payload"].get("result", {}).get("aggregatedOutput", "")
            for _, verifier in pairs for event in child_runs.get(verifier.get("run_id"), []))
        readers = {event["payload"].get("member_id") for event in events
                   if event["type"] == "team.message.supplied"
                   and event["payload"].get("via") == "read_messages"
                   and event["payload"].get("call_id")}
        checks["assigned_members_read_mailbox"] = participant_ids <= readers
        checks["runtime_source_unchanged"] = report["runtime_source_sha256"] == runtime_manifest()
        if checks["controller_checker_unchanged"]:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", str(verifier_file), cwd=project_root,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(process.communicate(), 10)
            checks["controller_reran_checker"] = process.returncode == 0 and (
                stdout.decode().strip() == success_marker)
        else:
            checks["controller_reran_checker"] = False
        report.update({"checks": checks, "state": team["state"], "snapshot": team,
                       "events": events, "child_events": child_runs,
                       "input_sha256": source_hash, "checker_sha256": verifier_hash,
                       "checker_success_marker": success_marker, "actual": actual})
        assert all(checks.values()), checks
        return report
    except BaseException as exc:
        report["controller_error"] = f"{type(exc).__name__}: {str(exc)[:1000]}"
        if team_id:
            try:
                report["failure_cleanup"] = await stop_after_failure(team_id)
            except Exception as cleanup_error:
                report["cleanup_error"] = type(cleanup_error).__name__
        raise
    finally:
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        destination = output / f"live-team-{mode}.json"
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        destination.chmod(0o600)
        print(f"Evidence: {destination}", flush=True)
        await client.close()


async def main(args: argparse.Namespace) -> None:
    state = os.environ.get("KIRA_HOME")
    if not state or not Path(state).resolve().is_relative_to((ROOT / "build").resolve()):
        raise SystemExit("Set KIRA_HOME to a private test store beneath this checkout's build/")
    output = ROOT / "build/releases" / __version__ / f"teams-{uuid.uuid4().hex[:8]}"
    output.mkdir(parents=True, mode=0o700)
    await ensure_service()
    modes = ("manual", "automatic") if args.mode == "both" else (args.mode,)
    for mode in modes:
        await exercise(mode, args.permission, output, args.deadline)
    print("PASS: real team operation, actual mailbox and independently verified files", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("manual", "automatic", "both"), default="both")
    parser.add_argument("--permission", choices=("project_write", "trusted_host"),
                        default="project_write")
    parser.add_argument("--deadline", type=float, default=1200,
                        help="Explicit controller timeout per team in seconds (default 1200)")
    asyncio.run(main(parser.parse_args()))
