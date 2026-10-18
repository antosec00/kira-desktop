#!/usr/bin/env python3
"""Qualify real chat -> managed team -> same-chat continuity on synthetic files.

Five provider turns are expected: origin, coordinator, reader, coordinator,
continuation. Exact model/effort are required. No existing Kira profile, account
configuration or project is modified. Direct ChatGPT mode uses an explicitly selected
credential directory and may refresh its OAuth tokens. Generated reports are private evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kira import __version__
from kira.client import Client
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import new_id


async def qualify(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"status": "FAIL", "version": __version__, "kind": "real provider, isolated synthetic project",
        "backend": args.backend, "model": args.model, "effort": args.effort, "timestamp": datetime.now(UTC).isoformat(), "turns": []}
    previous = os.environ.get("KIRA_HOME")
    try:
        with tempfile.TemporaryDirectory(prefix="kira-assistant-live-") as directory:
            root = Path(directory)
            os.environ["KIRA_HOME"] = str(root / "profile")
            service = Service(Paths.from_env())
            await service.start()
            if args.backend == "openai_chatgpt":
                from kira.backends.openai_auth import ChatGPTAuth
                service.direct_accounts._auth = ChatGPTAuth(args.auth_directory,
                    protect_secrets=service.redactor.set_provider_values)
            client = Client(service.paths)
            try:
                await client.connect()
                project_root = root / "project"
                project_root.mkdir()
                marker = "KIRA-EVIDENCE-" + secrets.token_hex(12)
                evidence = project_root / "input.txt"
                evidence.write_text(marker + "\n")
                original_hash = hashlib.sha256(evidence.read_bytes()).hexdigest()
                project = (await client.request("project.create", {"name": "Synthetic assistant team qualification",
                    "root": str(project_root), "permission": "read_only",
                    "instructions": "This is an authorized local read-only synthetic test. No network, browser, edits or external files. Use only the project input.txt as evidence; preserve its bytes."}))["project"]
                chat = (await client.request("session.create", {"project_id": project["id"], "backend": args.backend,
                    "model": args.model, "settings": {"reasoning_effort": args.effort, "tools": True}}))["session"]

                async def turn(prompt: str) -> dict[str, Any]:
                    run = (await client.request("run.start", {"session_id": chat["id"], "request_id": new_id(),
                                                               "prompt": prompt}))["run"]
                    task = service.tasks.get(run["id"])
                    if task:
                        try:
                            await asyncio.wait_for(asyncio.shield(task), args.timeout)
                        except TimeoutError:
                            await client.request("run.cancel", {"run_id": run["id"]})
                            raise
                    actual = service.store.one("runs", run["id"])
                    if actual["state"] != "completed":
                        raise RuntimeError(f"Origin turn ended {actual['state']}: {actual.get('error')}")
                    return actual

                origin = await turn(
                    "Use your Kira environment tool, then request a Kira managed team from this chat. "
                    "Use manual composition with exactly two read-only members: coordinator (id coordinator) "
                    "and evidence reader (id reader). Concurrency 1, round_limit 3. "
                    "Goal: read input.txt and report its exact token unchanged. The reader must use the "
                    "team mailbox to send the token to coordinator and include it in its final result. "
                    "Brief: this is an authorized synthetic local check; no edits or network, and the "
                    "coordinator verifies the reader's evidence and returns that token in its final summary. "
                    "Do not read the file yourself. After the delegation is accepted, finish this turn "
                    "successfully without waiting, polling, or starting any native subagent.")
                rows = service.store.all("SELECT id,team_id,state FROM assistant_delegations WHERE origin_run_id=?", (origin["id"],))
                assert len(rows) == 1 and rows[0]["team_id"], f"Origin did not create one managed team: {rows}"
                delegation_id, team_id = rows[0]["id"], rows[0]["team_id"]
                task = service.teams.tasks.get(team_id)
                if task:
                    await asyncio.wait_for(asyncio.shield(task), args.timeout * 3)
                snapshot = await client.request("assistant.snapshot", {"project_id": project["id"], "session_id": chat["id"]})
                result = next(row for row in snapshot["delegations"] if row["id"] == delegation_id)
                assert result["state"] == "completed" and result["cleanup_confirmed"], result
                assert marker in result["summary"], "Team summary did not match independently generated evidence"
                assert result["members_count"] == 2 and result["completed_steps"] >= 1
                messages = service.store.all("SELECT sender_id,recipient_id,content FROM team_messages WHERE team_id=?", (team_id,))
                assert any(row["sender_id"] == "reader" and row["recipient_id"] in {"coordinator", "all"}
                           and marker in row["content"] for row in messages), "No durable reader -> coordinator marker message"
                assert service.store.db.execute("SELECT COUNT(*) FROM assistant_calls WHERE run_id=? AND name='kira.environment'", (origin["id"],)).fetchone()[0]
                events = service.store.all("SELECT * FROM events WHERE run_id=? AND type='team.delegation' ORDER BY seq", (origin["id"],))
                assert any(event["payload"].get("state") == "completed" and marker in event["payload"].get("summary", "") for event in events)
                native = service.store.one("sessions", chat["id"])["backend_ref"]
                continuation = await turn("Without any tools or new team, repeat only the exact token reported by the Kira team. Use the recorded team result supplied to this chat.")
                answers = service.store.all("SELECT payload FROM events WHERE run_id=? AND type='message.completed'", (continuation["id"],))
                assert marker in "\n".join(row["payload"].get("content", "") for row in answers)
                assert service.store.one("sessions", chat["id"])["backend_ref"] == native
                assert hashlib.sha256(evidence.read_bytes()).hexdigest() == original_hash
                assert list(project_root.iterdir()) == [evidence]
                for run in service.store.all("SELECT * FROM runs ORDER BY created_at"):
                    notices = service.store.all("SELECT payload FROM events WHERE run_id=? AND type='backend.notice'", (run["id"],))
                    configured = [row["payload"] for row in notices if row["payload"].get("message") == "Codex session configured"]
                    if args.backend == "codex":
                        assert configured and all(row["model"] == args.model and row["reasoning_effort"] == args.effort
                                                  and row["permission"] == "read_only" for row in configured)
                    else:
                        started = service.store.all("SELECT payload FROM events WHERE run_id=? AND type='run.state' AND json_extract(payload,'$.state')='running' ORDER BY seq", (run["id"],))
                        assert started and started[0]["payload"]["model"] == args.model
                        assert started[0]["payload"]["reasoning_effort"] == args.effort
                        assert started[0]["payload"]["backend"] == args.backend
                        assert not service.store.all("SELECT * FROM provider_processes WHERE run_id=?", (run["id"],))
                    report["turns"].append({"id": run["id"], "state": run["state"], "events": run["last_seq"]})
                assert not service.tasks and not service.teams.tasks
                assert not service.store.all("SELECT * FROM project_reservations")
                report.update(status="PASS", origin_chat_preserved=True, native_thread_preserved=args.backend == "codex",
                    actual_two_member_team=True, actual_reader_mailbox_message=True, independently_verified_token=True,
                    durable_result_in_origin_chat=True, next_turn_received_team_result=True,
                    unchanged_project_files=True, explicit_model_effort_preserved=True, owned_cleanup_confirmed=True)
            finally:
                # Preserve failed attempts as evidence before the disposable store
                # disappears; a failed assertion must not hide actual provider work.
                report["turns"] = [{"id": run["id"], "state": run["state"], "events": run["last_seq"],
                                    "error": run.get("error")} for run in service.store.all("SELECT * FROM runs ORDER BY created_at")]
                report["recorded_events"] = service.store.all("SELECT * FROM events ORDER BY run_id,seq")
                report["delegations"] = service.store.all("SELECT * FROM assistant_delegations")
                report["assistant_calls"] = service.store.all("SELECT * FROM assistant_calls")
                await client.close()
                await service.close()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"[:2000]
    finally:
        if previous is None:
            os.environ.pop("KIRA_HOME", None)
        else:
            os.environ["KIRA_HOME"] = previous
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("codex", "openai_chatgpt"), default="codex")
    parser.add_argument("--auth-directory", type=Path, help="Explicit direct ChatGPT credential directory; never copied")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/evidence/assistant/live.json"))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.timeout < 30 or not args.model.strip() or not args.effort.strip():
        parser.error("Explicit nonempty model/effort and timeout >=30 seconds are required")
    if args.backend == "openai_chatgpt" and (args.auth_directory is None or not args.auth_directory.is_absolute()):
        parser.error("Direct mode requires an explicit absolute --auth-directory")
    if args.dry_run:
        print("Configuration valid; no service or provider started")
        return
    report = asyncio.run(qualify(args))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "turns": len(report["turns"]), "report": str(args.output)}), flush=True)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
