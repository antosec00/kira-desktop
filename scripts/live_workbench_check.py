#!/usr/bin/env python3
"""Isolated real-provider qualification of the chat controls (queue, guidance, compaction).

Requires explicit --model and --effort. At most three ordinary turns and one
native compaction are requested. Existing authentication is used only by the
configured Codex executable; no credentials or global configuration are edited.
Reports are private generated evidence, never fixtures or publishable source.
Use --dry-run to validate configuration without starting a service or provider.
Use --plan-only for one fresh isolated plan turn and visible-plan delivery proof.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kira import __version__
from kira.domain.contracts import TERMINAL_STATES, Json, KiraError
from kira.domain.paths import Paths
from kira.service.processes import identity, same_process
from kira.service.server import Service
from kira.service.storage import encode

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    "isolation", "provider_selection", "native_plan_readonly", "goal_context",
    "queue_admission", "active_steering", "queue_restart_recovery", "queued_execution",
    "native_fork_latest", "native_fork_boundary", "native_compaction", "chat_export",
    "filesystem_integrity", "owned_process_cleanup",
)
PLAN_CHECKS = ("isolation", "provider_selection", "native_plan_readonly", "plan_visible_final_text",
               "private_reasoning_boundary", "filesystem_integrity", "owned_process_cleanup")
UNAVAILABLE = {"UNSUPPORTED", "UNAVAILABLE", "PROVIDER_UNAVAILABLE", "NO_PROVIDER_THREAD"}


class NotRun(Exception):
    """A missing prerequisite is not a successful feature check."""


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Exact selected Codex model; no fallback")
    parser.add_argument("--effort", required=True, help="Exact selected reasoning effort; no fallback")
    parser.add_argument("--codex-binary", help="Configured Codex executable, otherwise codex from PATH")
    parser.add_argument("--output", type=Path, default=ROOT / "build/evidence/workbench",
                        help="Private evidence parent; each attempt gets a new directory")
    parser.add_argument("--turn-timeout", type=int, default=240,
                        help="Seconds per ordinary turn, between 30 and 600")
    parser.add_argument("--dry-run", action="store_true", help="No provider, service, or inference")
    parser.add_argument("--plan-only", action="store_true",
                        help="One fresh plan turn only; no queue, branches, steering or compaction")
    parser.add_argument("--resume-report", type=Path,
                        help="Continue this harness's stopped steering-evidence assertion; no completed turn is repeated")
    args = parser.parse_args(argv)
    if args.plan_only and args.resume_report:
        parser.error("--plan-only always uses a fresh profile and cannot resume a report")
    if not 30 <= args.turn_timeout <= 600:
        parser.error("--turn-timeout must be between 30 and 600")
    if any(not value.strip() or "\0" in value for value in (args.model, args.effort)):
        parser.error("--model and --effort must be nonempty text without NUL")
    if args.codex_binary is not None and (not args.codex_binary.strip() or "\0" in args.codex_binary):
        parser.error("--codex-binary must be a nonempty executable name/path")
    return args


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Qualification:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        parent = args.output.expanduser().absolute()
        if parent.is_symlink():
            raise ValueError("Evidence parent must not be a symlink")
        parent.mkdir(parents=True, exist_ok=True)
        self.output = parent / f"{stamp}-{secrets.token_hex(4)}"
        self.output.mkdir(mode=0o700)
        self.report: Json = {
            "kind": "LIVE Codex through isolated Kira service", "app_version": __version__,
            "status": "NOT_RUN", "model": args.model, "reasoning_effort": args.effort,
            "codex_binary": args.codex_binary or "codex", "started_at": datetime.now(UTC).isoformat(),
            "limits": {"ordinary_turns": 1 if args.plan_only else 3,
                       "native_compactions": 0 if args.plan_only else 1,
                       "turn_timeout_seconds": args.turn_timeout},
            "checks": {name: {"status": "NOT_RUN"} for name in (PLAN_CHECKS if args.plan_only else CHECKS)},
            "runs": [], "operations": [], "cleanup": [],
            "limitations": ["No GUI, installed-package or Ollama qualification.",
                            "Native branch history is not independently read back in this harness.",
                            "Read-only proof combines resolved sandbox evidence and independent file hashes; "
                            "the harness does not ask the model to bypass the plan's policy."],
        }
        if args.plan_only:
            self.report["kind"] = "LIVE Codex native plan delivery through isolated Kira service"
            self.report["limitations"] = [
                "One native planning turn; no GUI, queue, branching, compaction or installed-package qualification.",
                "Read-only proof combines resolved sandbox evidence and independent file hashes; "
                "the harness does not ask the model to bypass the plan's policy.",
            ]
        self.stage = "isolation"
        self.service: Service | None = None
        self.temporary: Path | None = None
        self.project: Json = {}
        self.session: Json = {}
        self.paths: Paths | None = None
        self.known_processes: list[Json] = []
        self.run_count = 0
        self.started = time.monotonic()
        self.save()

    def save(self) -> None:
        self.report["duration_seconds"] = round(time.monotonic() - self.started, 3)
        write_json(self.output / "report.json", self.report)

    def begin(self, name: str) -> None:
        self.stage = name
        print(f"CHECK {name}", flush=True)

    def passed(self, name: str, **evidence: Any) -> None:
        self.report["checks"][name] = {"status": "PASS", **evidence}
        self.save()
        print(f"PASS {name}", flush=True)

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

    async def wait(self, predicate: Callable[[], bool], description: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(description)
            await asyncio.sleep(0.2)

    def events(self, run_id: str) -> list[Json]:
        assert self.service is not None
        return self.service.store.all("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))

    def configured(self, run_id: str, permission: str, sandbox: str) -> Json:
        notices = [row["payload"] for row in self.events(run_id)
                   if row["type"] == "backend.notice"
                   and row["payload"].get("message") == "Codex session configured"]
        self.require(len(notices) == 1, "Exactly one actual configured notice is required")
        notice = notices[0]
        self.require(notice.get("model") == self.args.model, "Resolved model changed")
        self.require(notice.get("reasoning_effort") == self.args.effort, "Resolved reasoning effort changed")
        self.require(notice.get("permission") == permission, "Effective permission changed")
        self.require(notice.get("sandbox", {}).get("type") == sandbox, "Native sandbox mismatch")
        return notice

    async def start_run(self, prompt: str, mode: str) -> Json:
        assert self.service is not None
        self.require(self.run_count < self.report["limits"]["ordinary_turns"], "Ordinary-turn budget exceeded")
        self.run_count += 1
        run = (await self.service.dispatch("run.start", {
            "session_id": self.session["id"], "request_id": str(uuid.uuid4()),
            "prompt": prompt, "mode": mode}))["run"]
        self.report["runs"].append({"id": run["id"], "mode": mode, "source": "direct"})
        self.save()
        return run

    async def finish_run(self, run_id: str) -> Json:
        assert self.service is not None
        last = ""
        deadline = time.monotonic() + self.args.turn_timeout
        while True:
            run = self.service.store.one("runs", run_id)
            if run["state"] != last:
                print(f"RUN {run_id}: {run['state']}", flush=True)
                last = run["state"]
            if run["state"] in TERMINAL_STATES and run_id not in self.service.tasks:
                write_json(self.output / f"events-{run_id}.json", self.events(run_id))
                if run["state"] != "completed":
                    raise AssertionError(f"Run ended {run['state']}: {run.get('error')}")
                self.require(bool((run.get("outcome") or {}).get("turn_id")), "Missing completed native turn boundary")
                return run
            if time.monotonic() >= deadline:
                raise TimeoutError("Ordinary turn exceeded its fixed controller deadline")
            await asyncio.sleep(0.3)

    async def idle(self) -> None:
        assert self.service is not None
        service = self.service
        await self.wait(lambda: not service.tasks and not service.turn_controls.tasks
                        and not service.store.reserved(self.project["root"]),
                        "Owned project cleanup did not finish", 30)

    def collect_processes(self) -> None:
        assert self.service is not None
        for table in ("provider_process_members", "control_process_members"):
            for row in self.service.store.all(f"SELECT identity FROM {table}"):
                self.known_processes.append(json.loads(row["identity"]))
        for backend in self.service._control_backends.values():
            for rpc in getattr(backend, "_active", ()):
                process = getattr(rpc, "process", None)
                if process is not None:
                    record = identity(process.pid)
                    if record is not None:
                        self.known_processes.append(record)

    async def stop_service(self, label: str) -> None:
        if self.service is None:
            return
        service = self.service
        self.collect_processes()
        backup = service.store.backup(self.output / f"database-{label}.sqlite3")
        self.report.setdefault("database_backups", []).append({"file": backup.name, "sha256": digest(backup)})
        await asyncio.wait_for(service.close(), 45)
        self.service = None
        alive = []
        for record in self.known_processes:
            current = identity(record["pid"])
            if current is not None and same_process(record, current) and current["state"] != "Z":
                alive.append(record["pid"])
        self.report["cleanup"].append({"stage": label, "recorded_identities": len(self.known_processes),
                                       "remaining_owned_pids": sorted(set(alive))})
        self.require(not alive, "An owned provider process remains after service cleanup")
        self.save()

    async def execute(self) -> None:
        self.begin("isolation")
        self.temporary = Path(tempfile.mkdtemp(prefix="kira-wb-", dir="/tmp"))
        profile = self.temporary / "profile"
        root = self.temporary / "project"
        root.mkdir(mode=0o700)
        self.paths = Paths(*(profile / part for part in ("config", "data", "state", "runtime")))
        os.environ["KIRA_HOME"] = str(profile)
        self.service = Service(self.paths)
        await self.service.start()
        if self.args.codex_binary:
            await self.service.dispatch("settings.update", {"codex_binary": self.args.codex_binary})
        self.project = (await self.service.dispatch("project.create", {
            "name": "Disposable workbench qualification", "root": str(root),
            "permission": "project_write", "instructions": "Use only the generated files in this disposable project. "
            "Do not inspect unrelated directories, credentials or global configuration. Do not use subagents or network tools."}))["project"]
        self.session = (await self.service.dispatch("session.create", {
            "project_id": self.project["id"], "backend": "codex", "model": self.args.model,
            "settings": {"reasoning_effort": self.args.effort}, "title": "Disposable live controls"}))["session"]
        self.report.update(temporary_fixture=str(self.temporary), project_id=self.project["id"],
                           session_id=self.session["id"])
        self.passed("isolation", source_checkout_used_as_project=False, active_user_profile_opened=False,
                    existing_authentication="Codex runtime only", global_configuration_changed=False)

        self.begin("provider_selection")
        catalog = await self.service.dispatch("backend.discover", {"backend": "codex"})
        if not catalog.get("available"):
            raise NotRun("Configured Codex provider unavailable: " + str(catalog.get("error", "")))
        model = next((item for item in catalog.get("models", []) if item.get("id") == self.args.model), None)
        if model is None or self.args.effort not in model.get("supported_reasoning_efforts", []):
            raise NotRun("The explicitly selected model/effort is not advertised; no substitute is allowed")
        capabilities = catalog.get("capabilities", {})
        required = ("planning",) if self.args.plan_only else ("planning", "steering", "native_fork", "native_compaction")
        missing = [key for key in required
                   if not capabilities.get(key)]
        if missing:
            raise NotRun("Missing advertised capabilities: " + ", ".join(missing))
        self.passed("provider_selection", model=self.args.model, effort=self.args.effort,
                    capabilities={key: capabilities[key] for key in required},
                    discovery_is_not_entitlement=True)

        value = secrets.randbelow(9000) + 1000
        guard = root / "guard.json"
        guard.write_text(json.dumps({"value": value}) + "\n", encoding="utf-8")
        guard_before = digest(guard)
        if self.args.plan_only:
            await self.visible_plan(root, guard, guard_before)
            return
        marker = "KIRA_GUIDANCE_" + secrets.token_hex(8)
        goal_marker = "KIRA_GOAL_" + secrets.token_hex(8)
        scope = {"project_id": self.project["id"], "session_id": self.session["id"]}
        await self.service.dispatch("goal.update", {**scope, "objective": "Qualify the disposable project safely: " + goal_marker})
        self.require(not self.service.tasks, "Saving an objective unexpectedly started execution")

        self.begin("native_plan_readonly")
        plan = await self.start_run(
            "Read guard.json with a real tool. Provide a brief plan to create result.json containing "
            "the original integer as value and its value * 7 + 3 as result. This request is planning only: "
            "do not create, modify or delete any file, do not start implementation, and do not ask questions.", "plan")
        plan = await self.finish_run(plan["id"])
        notice = self.configured(plan["id"], "read_only", "readOnly")
        self.require(digest(guard) == guard_before and not (root / "result.json").exists(), "Planning changed fixture files")
        self.require(any(event["type"] == "tool.completed" for event in self.events(plan["id"])),
                     "Plan did not provide an observed tool read")
        self.passed("native_plan_readonly", run_id=plan["id"], configured_notice=notice,
                    guard_sha256=guard_before, result_file_absent=True)
        self.begin("goal_context")
        contexts = [event["payload"].get("content", "") for event in self.events(plan["id"])
                    if event["type"] == "context.supplied"]
        self.require(any(goal_marker in text for text in contexts), "Active objective absent from supplied context")
        await self.service.dispatch("goal.update", {**scope, "status": "paused"})
        self.require(not self.service.workbench.goal_context(self.session["id"]), "Paused objective still supplied")
        self.passed("goal_context", active_context_recorded=True, paused_context_empty=True,
                    automatic_execution_started=False)

        self.begin("active_steering")
        barrier_code = ("import time\nfrom pathlib import Path\n"
                        "print('KIRA_STEER_BARRIER', flush=True)\n"
                        "deadline = time.monotonic() + 50\n"
                        "while not Path('continue.signal').exists() and time.monotonic() < deadline:\n"
                        "    time.sleep(0.1)\n"
                        "assert Path('continue.signal').exists(), 'controller signal timed out'\n"
                        "print('controller signal received', flush=True)\n")
        argv = ["python3", "-c", barrier_code]
        running = await self.start_run(
            "Execute exactly this harmless foreground Python command in the project and wait for it. "
            "The controller will create its signal shortly. Do not write or edit any files yourself. "
            "After it finishes, follow any new user guidance and give a short final reply. "
            "Do not ask questions, start background processes or delegate. Command argv: " + json.dumps(argv), "execute")

        def barrier_started() -> bool:
            assert self.service is not None
            state = self.service.store.one("runs", running["id"])["state"]
            if state in TERMINAL_STATES:
                raise AssertionError("Turn ended before the observed steering barrier")
            return any(event["type"] == "tool.started" and "KIRA_STEER_BARRIER" in encode(event["payload"])
                       for event in self.events(running["id"])) and running["id"] in self.service._run_controls

        await self.wait(barrier_started, "Provider did not start the controlled native wait", self.args.turn_timeout)
        notice = self.configured(running["id"], "project_write", "workspaceWrite")
        self.begin("queue_admission")
        queued_params = {"session_id": self.session["id"], "request_id": str(uuid.uuid4()), "mode": "execute",
                         "prompt": "Read guard.json. Use a real file editing tool to create result.json with exactly "
                         "two integer fields: value copied from guard.json and result equal to value * 7 + 3. "
                         "Do not change guard.json or any other file. Verify the calculation with a real tool "
                         "and finish with a brief result. Do not wait for signals, ask questions or delegate."}
        queued = (await self.service.dispatch("queue.add", queued_params))["item"]
        await asyncio.sleep(0.3)
        stored = self.service.store.all("SELECT * FROM prompt_queue WHERE id=?", (queued["id"],))[0]
        self.require(stored["status"] == "pending" and stored["run_id"] is None,
                     "Queued request started while the preceding run was active")
        self.require(not self.service.store.all("SELECT id FROM runs WHERE request_id=?", (queued_params["request_id"],)),
                     "Queue admission created a conflicting run")
        await self.service.dispatch("queue.pause", {"project_id": self.project["id"]})
        self.passed("queue_admission", item_id=queued["id"], durable_pending=True, predecessor_active=True)

        self.begin("active_steering")
        guidance = {"run_id": running["id"], "request_id": str(uuid.uuid4()),
                    "prompt": "Additional explicit guidance: your final reply for the current turn must "
                    "include this exact harmless marker: " + marker + ". Do not write it to a file."}
        accepted = await self.service.dispatch("run.steer", guidance)
        self.require(accepted.get("accepted") is True, "Native steering was not acknowledged")
        repeated = await self.service.dispatch("run.steer", guidance)
        self.require(repeated.get("status") == "accepted", "Identical steering retry did not reuse its identity")
        (root / "continue.signal").write_text("controller-only release\n", encoding="utf-8")
        running = await self.finish_run(running["id"])
        replies = [event["payload"].get("content", "") for event in self.events(running["id"])
                   if event["type"] == "message.completed"]
        final_reply = replies[-1] if replies else ""
        self.require(marker in final_reply, "Acknowledged steering marker is absent from the final assistant reply")
        steering_events = [event for event in self.events(running["id"])
                           if event["type"] == "message.user" and event["payload"].get("source") == "steer"]
        self.require(len(steering_events) == 1, "Steering was duplicated or not committed in the transcript")
        self.passed("active_steering", run_id=running["id"], configured_notice=notice,
                    acknowledged=True, exact_marker_in_final_reply=True, retry_deduplicated=True,
                    acknowledgment_alone_is_not_consumption=True)

        await self.remaining(root, guard, guard_before, value, marker, goal_marker, scope,
                             plan, running, queued, queued_params)

    async def visible_plan(self, root: Path, guard: Path, guard_before: str) -> None:
        self.begin("native_plan_readonly")
        marker = "KIRA_PLAN_VISIBLE_" + secrets.token_hex(8)
        plan = await self.start_run(
            "Read guard.json with a real tool. Produce a concise final plan to create result.json with "
            "the original integer as value and value * 7 + 3 as result. Include this exact harmless "
            "verification marker in the final proposed plan: " + marker + ". This is planning only: "
            "do not create, modify or delete files, do not implement the plan, do not delegate and "
            "do not ask questions. Make the final plan visible to the user.", "plan")
        plan = await self.finish_run(plan["id"])
        notice = self.configured(plan["id"], "read_only", "readOnly")
        self.require(digest(guard) == guard_before and not (root / "result.json").exists(),
                     "Planning changed fixture files")
        events = self.events(plan["id"])
        self.require(any(event["type"] == "tool.completed" for event in events),
                     "Plan did not perform an observed fixture read")
        self.passed("native_plan_readonly", run_id=plan["id"], configured_notice=notice,
                    guard_sha256=guard_before, result_file_absent=True)

        self.begin("plan_visible_final_text")
        completed = [event for event in events if event["type"] == "message.completed"
                     and event["payload"].get("source") == "codex.plan"
                     and event["payload"].get("phase") == "final_answer"
                     and isinstance(event["payload"].get("content"), str)
                     and event["payload"]["content"].strip()]
        self.require(bool(completed), "No nonempty completed native plan reached the conversation")
        matching = [event for event in completed if marker in event["payload"]["content"]]
        self.require(bool(matching), "Generated marker is absent from the visible final native plan")
        self.passed("plan_visible_final_text", run_id=plan["id"], event_sequences=[event["seq"] for event in matching],
                    source="codex.plan", phase="final_answer", exact_generated_marker_present=True,
                    completed_native_plans=len(completed))

        self.begin("private_reasoning_boundary")
        private_fields = {"thinking", "reasoning", "analysis", "reasoning_content"}

        def has_private_field(value: Any) -> bool:
            if isinstance(value, dict):
                return any(str(key).casefold() in private_fields or has_private_field(child)
                           for key, child in value.items())
            if isinstance(value, list):
                return any(has_private_field(child) for child in value)
            return False

        self.require(not any(any(word in event["type"].casefold() for word in private_fields)
                             or has_private_field(event["payload"]) for event in events),
                     "Private reasoning fields or events were persisted")
        self.passed("private_reasoning_boundary", durable_events_checked=len(events), private_fields_found=False)

        self.begin("filesystem_integrity")
        files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        self.require(files == ["guard.json"] and digest(guard) == guard_before,
                     "Planning changed the generated project contents")
        self.passed("filesystem_integrity", files=files, guard_sha256=guard_before)
        await self.idle()

    async def resume(self) -> None:
        """Continue only the known harness assertion failure, never an unknown effect."""
        previous_path = self.args.resume_report.expanduser().resolve(strict=True)
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        self.require(previous.get("kind") == "LIVE Codex through isolated Kira service"
                     and previous.get("model") == self.args.model
                     and previous.get("reasoning_effort") == self.args.effort,
                     "Resume report does not match this harness and exact provider selection")
        self.require(previous.get("error") == "AssertionError: Acknowledged steering marker is absent from the final assistant reply",
                     "Only the known final-text harness assertion can be continued")
        self.require(previous["checks"]["owned_process_cleanup"]["status"] == "PASS"
                     and len(previous.get("runs", [])) == 2
                     and not previous.get("operations"), "Previous attempt is not a safely stopped two-turn fixture")
        temporary = Path(previous["temporary_fixture"])
        self.require(temporary.parent == Path("/tmp") and temporary.name.startswith("kira-wb-")
                     and temporary.resolve(strict=True) == temporary and temporary.stat().st_uid == os.getuid(),
                     "Resume accepts only this user's original short temporary fixture")
        self.temporary = temporary
        root, profile = temporary / "project", temporary / "profile"
        self.paths = Paths(*(profile / part for part in ("config", "data", "state", "runtime")))
        os.environ["KIRA_HOME"] = str(profile)
        self.report.update(resumes_report=str(previous_path), resumes_report_sha256=digest(previous_path),
                           temporary_fixture=str(temporary), runs=previous["runs"],
                           project_id=previous["project_id"], session_id=previous["session_id"])
        self.service = Service(self.paths)
        await self.service.start()
        self.project = self.service.store.one("projects", previous["project_id"])
        self.session = self.service.store.one("sessions", previous["session_id"])
        self.require(self.project["root"] == str(root) and self.session["project_id"] == self.project["id"]
                     and self.session["model"] == self.args.model
                     and self.session["settings"].get("reasoning_effort") == self.args.effort,
                     "Fixture ownership or selected model/effort changed")
        self.run_count = 2
        plan, running = [self.service.store.one("runs", entry["id"]) for entry in previous["runs"]]
        self.require(all(run["state"] == "completed" and run["session_id"] == self.session["id"]
                         for run in (plan, running)), "Cannot replay incomplete or foreign source turns")
        self.configured(plan["id"], "read_only", "readOnly")
        notice = self.configured(running["id"], "project_write", "workspaceWrite")
        guidance = [event for event in self.events(running["id"])
                    if event["type"] == "message.user" and event["payload"].get("source") == "steer"]
        self.require(len(guidance) == 1, "Source steering message is missing or duplicated")
        match = re.search(r"KIRA_GUIDANCE_[0-9a-f]{16}", guidance[0]["payload"]["content"])
        self.require(match is not None, "Synthetic guidance marker missing")
        assert match is not None
        marker = match.group()
        replies = [event["payload"].get("content", "") for event in self.events(running["id"])
                   if event["type"] == "message.completed"]
        self.require(bool(replies) and marker in replies[-1], "Corrected final-message evidence still fails")
        self.require(any(event["type"] == "steering.accepted" for event in self.events(running["id"])),
                     "No durable native steering acknowledgment")
        goal = self.service.store.all("SELECT * FROM session_goals WHERE session_id=?", (self.session["id"],))[0]
        match = re.search(r"KIRA_GOAL_[0-9a-f]{16}", goal["objective"])
        self.require(goal["status"] == "paused" and match is not None, "Synthetic objective not safely paused")
        assert match is not None
        goal_marker = match.group()
        guard = root / "guard.json"
        guard_before = previous["checks"]["native_plan_readonly"]["guard_sha256"]
        self.require(digest(guard) == guard_before and not (root / "result.json").exists(),
                     "Fixture files changed before bounded continuation")
        value = json.loads(guard.read_text())["value"]
        queued = self.service.store.all("SELECT * FROM prompt_queue WHERE id=?",
                                       (previous["checks"]["queue_admission"]["item_id"],))[0]
        self.require(queued["session_id"] == self.session["id"] and queued["status"] == "pending"
                     and queued["run_id"] is None, "Queue item has already dispatched; it cannot be repeated")
        for name in ("isolation", "provider_selection", "native_plan_readonly", "goal_context", "queue_admission"):
            self.report["checks"][name] = {**previous["checks"][name], "evidence_origin": str(previous_path)}
        self.passed("active_steering", run_id=running["id"], configured_notice=notice,
                    acknowledged=True, exact_marker_in_final_reply=True, ordinary_turn_repeated=False,
                    correction="Read final message.completed; ordinary outcomes do not contain final_text")
        for run in (plan, running):
            write_json(self.output / f"events-{run['id']}.json", self.events(run["id"]))
        scope = {"project_id": self.project["id"], "session_id": self.session["id"]}
        await self.remaining(root, guard, guard_before, value, marker, goal_marker, scope,
                             plan, running, queued, queued)

    async def remaining(self, root: Path, guard: Path, guard_before: str, value: int,
                        marker: str, goal_marker: str, scope: Json, plan: Json,
                        running: Json, queued: Json, queued_params: Json) -> None:
        assert self.service is not None

        self.begin("queue_restart_recovery")
        await self.idle()
        await self.stop_service("before-restart")
        assert self.paths is not None
        self.service = Service(self.paths)
        await self.service.start()
        listing = await self.service.dispatch("queue.list", {"project_id": self.project["id"]})
        restored = next(row for row in listing["items"] if row["id"] == queued["id"])
        self.require(listing["paused"] and restored["status"] == "pending" and restored["run_id"] is None,
                     "Restart failed to preserve the pending queue in paused state")
        self.require(not self.service.tasks, "Restart silently dispatched queued work")
        self.passed("queue_restart_recovery", same_item_id=True, pending_preserved=True,
                    restart_paused=True, automatic_replay=False)

        self.begin("queued_execution")
        self.run_count += 1
        self.require(self.run_count == 3, "Unexpected ordinary-turn count")
        await self.service.dispatch("queue.resume", {"project_id": self.project["id"]})

        def queue_has_run() -> bool:
            assert self.service is not None
            return bool(self.service.store.all("SELECT id FROM runs WHERE request_id=?", (queued_params["request_id"],)))

        await self.wait(queue_has_run, "Explicitly resumed queue did not start", 30)
        queued_run = self.service.store.all("SELECT * FROM runs WHERE request_id=?", (queued_params["request_id"],))[0]
        self.report["runs"].append({"id": queued_run["id"], "mode": "execute", "source": "queue"})
        queued_run = await self.finish_run(queued_run["id"])
        await self.idle()
        notice = self.configured(queued_run["id"], "project_write", "workspaceWrite")
        result_file = root / "result.json"
        expected = {"value": value, "result": value * 7 + 3}
        self.require(result_file.is_file() and json.loads(result_file.read_text()) == expected,
                     "Independent file calculation check failed")
        self.require(queued_run["created_at"] >= running["updated_at"], "Queued run predates predecessor completion")
        self.require(digest(guard) == guard_before, "Queued execution changed the immutable input")
        contexts = [event["payload"].get("content", "") for event in self.events(queued_run["id"])
                    if event["type"] == "context.supplied"]
        self.require(all(goal_marker not in text for text in contexts), "Paused goal was newly supplied after restart")
        stored = self.service.store.all("SELECT * FROM prompt_queue WHERE id=?", (queued["id"],))[0]
        self.require(stored["status"] == "completed", "Queue did not record successful completion")
        self.passed("queued_execution", run_id=queued_run["id"], configured_notice=notice,
                    independently_verified=expected, result_sha256=digest(result_file),
                    starts_after_completed=True, paused_goal_not_supplied=True)

        source = self.service.store.one("sessions", self.session["id"])
        source_events = self.service.store.all("SELECT e.* FROM events e JOIN runs r ON r.id=e.run_id "
                                               "WHERE r.session_id=? ORDER BY r.created_at,r.rowid,e.seq",
                                               (self.session["id"],))
        source_hash = hashlib.sha256(encode(source_events).encode()).hexdigest()
        for name, boundary in (("native_fork_latest", None), ("native_fork_boundary", plan["id"])):
            self.begin(name)
            request = {**scope, "request_id": str(uuid.uuid4()), "title": "Disposable " + name}
            if boundary is not None:
                request["through_run_id"] = boundary
            forked = await self.service.dispatch("session.fork", request)
            branch = forked.get("session", {})
            self.require(forked.get("mode") == "native" and bool(branch.get("backend_ref")), "Native branch was not confirmed")
            self.require(branch["backend_ref"] != source["backend_ref"], "Native branch reused the source reference")
            self.require(branch.get("model") == self.args.model and
                         branch.get("settings", {}).get("reasoning_effort") == self.args.effort,
                         "Branch changed selected model or effort")
            self.require(await self.service.dispatch("session.fork", request) == forked, "Branch retry created a second effect")
            self.require(self.service.store.one("sessions", self.session["id"]) == source, "Branch altered original session")
            current = self.service.store.all("SELECT e.* FROM events e JOIN runs r ON r.id=e.run_id "
                                              "WHERE r.session_id=? ORDER BY r.created_at,r.rowid,e.seq", (self.session["id"],))
            self.require(hashlib.sha256(encode(current).encode()).hexdigest() == source_hash, "Branch rewrote source events")
            self.report["operations"].append({"kind": name, "request_id": request["request_id"],
                                               "source_run_id": boundary, "result": forked})
            self.passed(name, session_id=branch["id"], source_unchanged=True,
                        boundary_turn_id=(plan.get("outcome") or {}).get("turn_id") if boundary else None,
                        retry_deduplicated=True)

        self.begin("native_compaction")
        request = {**scope, "request_id": str(uuid.uuid4())}
        compaction = await self.service.dispatch("session.compact", request)
        deadline = time.monotonic() + 110
        while compaction.get("status") == "pending":
            if time.monotonic() > deadline:
                raise TimeoutError("Native compaction did not reach a terminal result")
            await asyncio.sleep(0.5)
            compaction = await self.service.dispatch("session.compact", request)
        self.require(compaction.get("status") == "completed" and compaction.get("compacted") is True
                     and bool(compaction.get("turn_id")), "Compaction lacks successful correlated terminal evidence: " + encode(compaction))
        self.require(self.service.store.one("sessions", self.session["id"])["backend_ref"] == source["backend_ref"],
                     "Compaction replaced the native thread")
        self.report["operations"].append({"kind": "native_compaction", "result": compaction})
        self.passed("native_compaction", result=compaction, evidence="Adapter observed matching contextCompaction and successful turn completion")

        self.begin("chat_export")
        exported = await self.service.dispatch("session.export", scope)
        markdown = exported["text"] if "text" in exported else Path(exported["path"]).read_text(encoding="utf-8")
        self.require(marker in markdown and "Retained activity" in markdown and "steering.accepted" in markdown,
                     "Full export lost accepted guidance or retained activity")
        target = self.output / "chat-export.md"
        target.write_text(markdown, encoding="utf-8")
        target.chmod(0o600)
        self.passed("chat_export", bytes=len(markdown.encode()), sha256=digest(target),
                    marker_preserved_after_compaction=True)

        self.begin("filesystem_integrity")
        actual_paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        self.require(actual_paths == ["continue.signal", "guard.json", "result.json"], "Unexpected fixture project files: " + repr(actual_paths))
        self.require(digest(guard) == guard_before, "Immutable input changed")
        self.passed("filesystem_integrity", files=actual_paths, guard_sha256=guard_before)
        await self.idle()

    async def run(self) -> int:
        prior_home = os.environ.get("KIRA_HOME")
        if self.args.dry_run:
            self.report.update(kind="Harness dry run; no service or provider invoked", status="NOT_RUN",
                               reason="Explicit dry-run mode")
            self.save()
            print(f"NOT_RUN: configuration validated; report {self.output / 'report.json'}", flush=True)
            return 0
        try:
            async with asyncio.timeout(self.args.turn_timeout * 3 + 360):
                if self.args.resume_report:
                    await self.resume()
                else:
                    await self.execute()
        except (Exception, asyncio.CancelledError) as exc:
            unavailable = isinstance(exc, NotRun) or isinstance(exc, KiraError) and exc.code in UNAVAILABLE
            self.report["checks"][self.stage] = {"status": "NOT_RUN" if unavailable else "FAIL",
                                                   "error": f"{type(exc).__name__}: {exc}"}
            self.report["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{'NOT_RUN' if unavailable else 'FAIL'} {self.stage}: {exc}", flush=True)
        finally:
            try:
                if self.service is not None:
                    if self.project:
                        with contextlib.suppress(Exception):
                            await self.service.dispatch("queue.pause", {"project_id": self.project["id"]})
                    for row in self.service.store.all("SELECT id FROM runs WHERE state NOT IN "
                                                      "('completed','failed','cancelled','interrupted')"):
                        with contextlib.suppress(Exception):
                            await self.service.dispatch("run.cancel", {"run_id": row["id"]})
                    for run in self.report["runs"]:
                        write_json(self.output / f"events-{run['id']}.json", self.events(run["id"]))
                    await self.stop_service("final")
                self.passed("owned_process_cleanup", cleanup=self.report["cleanup"],
                            authority="Only this temporary service's recorded children")
            except Exception as exc:
                self.report["checks"]["owned_process_cleanup"] = {"status": "FAIL", "error": str(exc)}
                print(f"FAIL owned_process_cleanup: {exc}", flush=True)
            if prior_home is None:
                os.environ.pop("KIRA_HOME", None)
            else:
                os.environ["KIRA_HOME"] = prior_home
            if self.temporary is not None:
                # Keep the original short-lived profile intact for audit/recovery.
                # Copy only our generated project, never a user's selected project.
                with contextlib.suppress(OSError):
                    shutil.copytree(self.temporary / "project", self.output / "project")
            statuses = [check["status"] for check in self.report["checks"].values()]
            self.report["status"] = "FAIL" if "FAIL" in statuses else "NOT_RUN" if "NOT_RUN" in statuses else "PASS"
            self.report["finished_at"] = datetime.now(UTC).isoformat()
            self.save()
        print(f"{self.report['status']}: report {self.output / 'report.json'}", flush=True)
        return {"PASS": 0, "FAIL": 1, "NOT_RUN": 2}[self.report["status"]]


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(Qualification(arguments()).run()))
    except KeyboardInterrupt:
        sys.exit(130)
