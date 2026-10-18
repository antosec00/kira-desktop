#!/usr/bin/env python3
"""Live Ollama chat/file-task gates. Never downloads weights or selects a model.

Requires --model and an isolated KIRA_HOME. Exit 2 means external prerequisites
are unavailable; fixtures must never be substituted for this controller.
"""

import argparse
import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from kira.client import Client, ensure_service
from kira.domain.contracts import TERMINAL_STATES

ROOT = Path(__file__).resolve().parents[1]


def evidence_path() -> Path:
    return ROOT / "build/evidence/live-ollama.json"


def save_failure(exc: Exception) -> None:
    evidence = evidence_path()
    evidence.parent.mkdir(parents=True, exist_ok=True)
    report = json.loads(evidence.read_text()) if evidence.exists() else {}
    report["tools" if report.get("chat") == "PASS" else "chat"] = "FAIL"
    report["error"] = f"{type(exc).__name__}: {exc}"
    evidence.write_text(json.dumps(report, indent=2) + "\n")


async def run(args) -> int:
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("Set KIRA_HOME to an isolated test directory")
    evidence = evidence_path()
    evidence.parent.mkdir(parents=True, exist_ok=True)
    report = {"kind": "LIVE Ollama (never a fixture)", "endpoint": args.endpoint,
              "model": args.model, "chat": "NOT RUN", "tools": "NOT RUN"}

    def save():
        evidence.write_text(json.dumps(report, indent=2) + "\n")

    await ensure_service()
    async with Client() as client:
        await client.request("settings.update", {"ollama_url": args.endpoint})
        catalog = await client.request("backend.discover", {"backend": "ollama"})
        models = [m["id"] for m in catalog.get("models", [])]
        if not catalog["available"] or args.model not in models:
            report["reason"] = catalog.get("error") or "Explicitly selected model is not installed"
            save()
            print(json.dumps(report, indent=2))
            return 2
        root = ROOT / "build" / f"ollama-project-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        number = secrets.randbelow(900000) + 100000
        (root / "input.json").write_text(json.dumps({"value": number}) + "\n")
        project = (await client.request("project.create", {
            "name": "Verifica reale Ollama", "root": str(root), "permission": "trusted_host",
        }))["project"]

        async def turn(prompt, tools):
            session = (await client.request("session.create", {
                "project_id": project["id"], "backend": "ollama", "model": args.model,
                "settings": {"tools": tools},
            }))["session"]
            run = (await client.request("run.start", {"session_id": session["id"],
                "prompt": prompt, "request_id": str(uuid.uuid4())}))["run"]
            started = time.monotonic()
            events = []
            cursor = 0
            while time.monotonic() - started < args.timeout:
                page = await client.request("run.events", {"run_id": run["id"], "after_seq": cursor})
                events.extend(page["events"])
                if events:
                    cursor = events[-1]["seq"]
                snapshot = await client.request("project.snapshot", {"project_id": project["id"]})
                current = next(r for r in snapshot["runs"] if r["id"] == run["id"])
                if current["state"] in TERMINAL_STATES:
                    while True:
                        tail = await client.request("run.events", {"run_id": run["id"], "after_seq": cursor})
                        events.extend(tail["events"])
                        if events:
                            cursor = events[-1]["seq"]
                        if not tail.get("has_more"):
                            break
                    return current, events
                await asyncio.sleep(1)
            await client.request("run.cancel", {"run_id": run["id"]})
            raise TimeoutError("Live controller deadline; run cancellation requested")

        marker = "KIRA_CHAT_" + uuid.uuid4().hex[:12]
        chat, events = await turn("Reply briefly with this exact marker: " + marker, False)
        text = "".join(e["payload"].get("content", "") for e in events if e["type"] == "message.delta")
        assert chat["state"] == "completed" and marker in text, chat
        report.update(chat="PASS", chat_run_id=chat["id"], project_root=str(root))
        save()
        if not args.chat_only:
            task, events = await turn(
                "Use real tools to read input.json. Create result.json with original equal to "
                "its value and result equal to value squared plus 17. Run a python3 command "
                "that reads both files and independently asserts that relationship. "
                "Do not modify input.json. Execute the tools; narration alone is insufficient.", True)
            actual = json.loads((root / "result.json").read_text())
            assert actual == {"original": number, "result": number * number + 17}
            assert json.loads((root / "input.json").read_text()) == {"value": number}
            names = {e["payload"].get("name") for e in events if e["type"] == "tool.completed"
                     and e["payload"].get("result", {}).get("ok")}
            assert {"read_file", "write_file", "run_command"} <= names, names
            assert task["state"] == "completed", task
            report.update(tools="PASS", tool_run_id=task["id"], controller_verified_file=True)
        snapshot = await client.request("project.snapshot", {"project_id": project["id"]})
        assert any(marker in m["content"] for m in snapshot["messages"] if m["role"] == "assistant")
        report["history_reopened"] = True
        save()
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--chat-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=480)
    try:
        raise SystemExit(asyncio.run(run(parser.parse_args())))
    except Exception as exc:
        save_failure(exc)
        raise
