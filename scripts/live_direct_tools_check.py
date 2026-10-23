#!/usr/bin/env python3
"""Explicit real direct-provider file task in an isolated disposable project.

Requires a previously authorized private OAuth directory; does not copy tokens.
The test may refresh credentials and consumes the selected account's allowance.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path

from kira import __version__
from kira.backends.openai_auth import ChatGPTAuth
from kira.domain.paths import Paths
from kira.service.server import Service


async def qualify(args: argparse.Namespace) -> dict:
    report: dict = {"status": "FAIL", "version": __version__, "backend": "openai_chatgpt",
                    "model": args.model, "effort": args.effort,
                    "kind": "real provider with independent synthetic file verification"}
    with tempfile.TemporaryDirectory(prefix="kira-direct-tools-") as directory:
        base = Path(directory)
        profile = base / "profile"
        paths = Paths(*(profile / part for part in ("config", "data", "state", "runtime")))
        service = Service(paths)
        try:
            await service.start()
            service.direct_accounts._auth = ChatGPTAuth(args.auth_directory,
                protect_secrets=service.redactor.set_provider_values)
            root = base / "project"
            root.mkdir()
            original = "kira-direct-probe-" + secrets.token_hex(16) + "\n"
            (root / "input.txt").write_text(original)
            project = (await service.dispatch("project.create", {
                "name": "Synthetic direct tool qualification", "root": str(root),
                "permission": "trusted_host", "instructions":
                "Authorized synthetic local test. Use only this temporary project directory. "
                "No network, external files, packages or background processes."}))["project"]
            chat = (await service.dispatch("session.create", {
                "project_id": project["id"], "backend": "openai_chatgpt", "model": args.model,
                "settings": {"reasoning_effort": args.effort, "tools": True}}))["session"]
            run = (await service.dispatch("run.start", {"session_id": chat["id"],
                "request_id": "direct-file-qualification", "prompt":
                "Use files.read_file to read input.txt. Then use files.write_file to create "
                "result.txt containing that exact text converted to uppercase, preserving its "
                "trailing newline. Use files.run_command to run a Python assertion that reads "
                "both files and verifies result.txt equals input.txt uppercased. Check the actual "
                "exit code and briefly report the outcome. Do not start a Team or other agents."}))["run"]
            await asyncio.wait_for(asyncio.shield(service.tasks[run["id"]]), args.timeout)
            result = service.store.one("runs", run["id"])
            report["outcome"] = result.get("outcome")
            if result["state"] != "completed":
                raise RuntimeError(f"Run ended {result['state']}: {result.get('error')}")
            assert (root / "input.txt").read_text() == original
            assert (root / "result.txt").read_text() == original.upper()
            tools = service.store.all("SELECT payload FROM events WHERE type='tool.completed'")
            names = {row["payload"]["name"] for row in tools}
            assert {"read_file", "write_file", "run_command"} <= names
            assert any(row["payload"]["name"] == "run_command" and
                       row["payload"]["result"].get("exit_code") == 0 for row in tools)
            assert not service.store.all("SELECT * FROM project_reservations")
            report.update(status="PASS", independently_verified_result=True,
                          input_preserved=True, actual_tool_names=sorted(names),
                          result_sha256=hashlib.sha256((root / "result.txt").read_bytes()).hexdigest(),
                          cleanup_confirmed=True)
        except Exception as exc:
            report["error"] = str(exc)[:2000]
        finally:
            if hasattr(service, "store"):
                report["events"] = service.store.all("SELECT * FROM events ORDER BY run_id,seq")
                report["runs"] = service.store.all("SELECT * FROM runs ORDER BY created_at")
            await service.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-directory", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output", type=Path, default=Path("build/direct-openai/live-tools.json"))
    args = parser.parse_args()
    if not args.auth_directory.is_absolute() or args.timeout < 30:
        parser.error("Use an absolute private auth directory and timeout >=30 seconds")
    report = asyncio.run(qualify(args))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({"status": report["status"], "report": str(args.output)}), flush=True)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
