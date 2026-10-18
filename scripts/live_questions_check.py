#!/usr/bin/env python3
"""Real Codex async question acceptance in a fresh, controller-owned data store.

The controller (not a person) supplies a synthetic answer after independently
verifying progress while the native question is pending. No user store is opened.
Run with: uv run python scripts/live_questions_check.py
"""

import asyncio
import hashlib
import json
import secrets
import tempfile
import time
import uuid
from pathlib import Path

from kira import __version__
from kira.client import Client
from kira.domain.contracts import TERMINAL_STATES
from kira.domain.paths import Paths
from kira.service.server import Service

ROOT = Path(__file__).resolve().parents[1]


def manifest() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "src/kira").rglob("*.py"))}


async def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="kira-questions-"))
    base.chmod(0o700)
    paths = Paths(*(base / name for name in ("config", "data", "state", "runtime")))
    service = Service(paths)
    client = Client(paths)
    evidence = ROOT / "build/releases" / __version__ / "questions-live.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    report: dict = {"kind": "LIVE Codex; controller-generated answers; not a human UI test",
        "version": __version__, "model": "gpt-6-astra", "reasoning_effort": "ultra",
        "data_store": str(base), "source_sha256": manifest(), "status": "RUNNING"}
    started = time.monotonic()
    await service.start()
    try:
        await client.connect()
        root = base / "project"
        root.mkdir()
        value = secrets.randbelow(900000) + 100000
        (root / "input.json").write_text(json.dumps({"value": value}))
        expected = {"original": value, "result": value * value + 17}
        input_hash = hashlib.sha256((root / "input.json").read_bytes()).hexdigest()
        project = (await client.request("project.create", {"name": "Live structured input check",
            "root": str(root), "permission": "trusted_host",
            "instructions": "Only use this disposable project's files. Never delegate."}))["project"]
        session = (await client.request("session.create", {"project_id": project["id"],
            "backend": "codex", "model": "gpt-6-astra",
            "settings": {"reasoning_effort": "ultra"}}))["session"]
        results = []
        for iteration in range(2):
            independent = root / f"independent-{iteration}.json"
            answer_file = root / f"answer-{iteration}.txt"
            answer = "Violet " + secrets.token_hex(8)
            prompt = (
                "This is a real asynchronous question UI integration check. FIRST use the native "
                "request_user_input_async tool to ask which accent color to use, with suggested "
                "choices Blue and Green and an Other answer. Ask exactly one question. "
                "While the answer is pending, continue independent work: read input.json and "
                f"write {independent.name} with original=value and result=value squared plus 17. "
                "The external controller will send its answer ONLY after that file is correct. "
                "Then wait for the asynchronous reply using the available waiting mechanism; "
                "do not guess, do not time out the question, do not end the turn before receiving it. "
                f"Write the exact answer, no quotes or extra commentary, to {answer_file.name}. "
                "Use real tools, don't alter input.json, don't delegate, and finish concisely."
            )
            run = (await client.request("run.start", {"session_id": session["id"],
                "prompt": prompt, "request_id": str(uuid.uuid4())}))["run"]
            run_id = run["id"]
            row: dict = {"run_id": run_id, "resumed_thread": iteration == 1}
            results.append(row)
            report["runs"] = results
            deadline = time.monotonic() + 420
            submitted = False
            question_id = None
            while time.monotonic() < deadline:
                listing = await client.request("input.list", {"project_id": project["id"]})
                pending = [q for q in listing["requests"] if q["run_id"] == run_id
                           and q["status"] == "pending"]
                if pending and independent.exists() and not submitted:
                    assert json.loads(independent.read_text()) == expected
                    form = pending[0]
                    assert form["blocking"] is False, "Provider used blocking input"
                    assert len(form["questions"]) == 1
                    question_id = form["request_id"]
                    qid = form["questions"][0]["id"]
                    # A real disconnect cannot discard the pending question.
                    await client.close()
                    client = Client(paths)
                    await client.connect()
                    reconnected = await client.request("input.list", {"project_id": project["id"]})
                    assert any(q["request_id"] == question_id and q["status"] == "pending"
                               for q in reconnected["requests"])
                    params = {"run_id": run_id, "request_id": question_id,
                              "answers": {qid: {"answers": [answer]}}}
                    response = await client.request("input.answer", params)
                    assert response["accepted"] is True
                    assert (await client.request("input.answer", params))["accepted"] is True
                    submitted = True
                    row.update(independent_progress_before_answer=True, reconnect_pending=True,
                               duplicate_answer_idempotent=True, request_id=question_id)
                    print(f"Run {iteration + 1}: independent file verified; async answer submitted",
                          flush=True)
                snapshot = await client.request("project.snapshot", {"project_id": project["id"]})
                run = next(r for r in snapshot["runs"] if r["id"] == run_id)
                if run["state"] in TERMINAL_STATES:
                    break
                await asyncio.sleep(0.4)
            else:
                await client.request("run.cancel", {"run_id": run_id})
                raise TimeoutError("Live input check exceeded its controller deadline")
            events = []
            cursor = 0
            while True:
                page = await client.request("run.events", {"run_id": run_id,
                    "after_seq": cursor, "limit": 500})
                events.extend(page["events"])
                cursor = page["last_seq"] if not page["events"] else page["events"][-1]["seq"]
                if not page.get("has_more"):
                    break
            row.update(state=run["state"], error=run.get("error"), event_count=len(events),
                       input_events=[e for e in events if e["type"].startswith("input.")])
            assert run["state"] == "completed", run
            assert submitted and answer_file.read_text().strip() == answer
            assert [e["seq"] for e in events] == list(range(1, run["last_seq"] + 1))
            assert any(e["type"] == "input.delivered" and
                       e["payload"]["request_id"] == question_id for e in events)
            row.update(answer_file_verified=True, contiguous_events=True, provider_received=True)
            print(f"Run {iteration + 1}: PASS, {len(events)} durable events", flush=True)
            cleanup = service.tasks.get(run_id)
            if cleanup is not None:
                await asyncio.wait_for(asyncio.shield(cleanup), timeout=30)
        assert hashlib.sha256((root / "input.json").read_bytes()).hexdigest() == input_hash
        assert report["source_sha256"] == manifest(), "Source changed during live acceptance"
        report.update(status="PASS", input_unchanged=True, source_unchanged=True)
    except Exception as exc:
        report.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await client.close()
        await service.close()  # Only this function's private, owned service.
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Evidence: {evidence}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
