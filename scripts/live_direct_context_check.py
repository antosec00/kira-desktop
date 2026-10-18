#!/usr/bin/env python3
"""Qualify direct context with synthetic facts and an explicitly reduced test window.

This runs real requests with the exact selected model/effort and an existing
private OAuth store. It can refresh credentials and consume allowance; no tokens
are copied and no account/model fallback is allowed. Another service must not
own that OAuth store. The isolated profile and a whitelisted report are retained
privately beside --output, including after failure. Opaque provider state stays
in the private database and never appears in the report or terminal output.

The reduced catalog window is a harness-only stress setting. This verifies
compaction behavior, not acceptance of the model's full advertised capacity.
Background archive text is compressible synthetic project discussion; only the
four independently generated FACT identifiers are random, including a correction.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kira import __version__
from kira.backends.openai_auth import ChatGPTAuth
from kira.domain.contracts import Json
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode, new_id

FACT_KEYS = ("early", "middle", "late", "corrected")
CATALOG_FIELDS = ("id", "context_window", "max_context_window", "max_output_tokens")


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-directory", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New private report; never overwritten")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds per real turn or maintenance")
    parser.add_argument("--test-context-window", type=int, default=10000,
                        help="Explicit TEST ONLY window, 6000..64000; not a provider entitlement")
    args = parser.parse_args(argv)
    if not args.auth_directory.is_absolute() or not args.auth_directory.is_dir():
        parser.error("Use an existing absolute private OAuth directory")
    if any(not value.strip() or "\0" in value for value in (args.model, args.effort)):
        parser.error("Preserve explicit nonempty model and effort values")
    if not 30 <= args.timeout <= 1800 or not 6000 <= args.test_context_window <= 64000:
        parser.error("Use timeout 30..1800 and test context window 6000..64000")
    if args.output.exists() or args.output.is_symlink():
        parser.error("Choose a new report filename; earlier reports are preserved")
    return args


def _write_report(path: Path, report: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _records(service: Service, run_ids: list[str]) -> Json:
    return {run_id: {"run": service.store.one("runs", run_id), "events": [dict(row) for row in
            service.store.db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))]}
            for run_id in run_ids}


def _seed(service: Service, session_id: str, test_window: int) -> tuple[Json, str, list[str]]:
    expected = {key: "fixture-" + secrets.token_hex(10) for key in FACT_KEYS}
    obsolete = "superseded-" + secrets.token_hex(10)
    ids = []
    modules = ("settings editor", "conversation list", "file preview", "search view", "progress messages")
    for index in range(40):
        # Compressible project discussion stresses history length without asking
        # compaction to preserve irrelevant high-entropy strings as identifiers.
        paragraph = (f"Review the synthetic {modules[index % len(modules)]} layout. Keep labels clear, "
            "preserve keyboard navigation, and check the empty and loading states. This is repetitive "
            "background discussion, not an identifier or a required fact for the final recall. ")
        filler = paragraph * max(1, (test_window // 24 + len(paragraph) - 1) // len(paragraph))
        content = f"ARCHIVE_RECORD {index:02d}. Synthetic milestone notes: {filler} "
        if index == 0:
            content += f"FACT early = {expected['early']}. Preserve this exact original identifier. "
        if index == 8:
            content += f"FACT corrected = {obsolete}. This is the original provisional value. "
        if index == 19:
            content += f"FACT middle = {expected['middle']}. This exact identifier is required later. "
        if index == 35:
            content += f"FACT late = {expected['late']}. Keep its exact spelling. "
        if index == 39:
            content += (f"CORRECTION: FACT corrected = {expected['corrected']}. This explicitly supersedes "
                        f"{obsolete}; the previous value must never be used as the current value. ")
        run, _ = service.store.create_run(session_id, new_id(), content)
        service.store.event(run["id"], "message.completed", {"content":
            f"Recorded synthetic milestone {index:02d}; user corrections override earlier provisional values."})
        service.store.event(run["id"], "run.state", {"state": "completed", "outcome": {"fixture_seed": True}})
        ids.append(run["id"])
    return expected, obsolete, ids


def _answer(service: Service, run_id: str) -> Json:
    for row in service.store.all("SELECT payload FROM events WHERE run_id=? AND type='message.completed' ORDER BY seq DESC", (run_id,)):
        content = row["payload"].get("content", "").strip()
        if content.startswith("```") and content.endswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise AssertionError("Provider did not return the requested complete JSON fact object")


async def _turn(service: Service, session: Json, request_id: str, prompt: str,
                args: argparse.Namespace, expected: Json) -> Json:
    run = (await service.dispatch("run.start", {"session_id": session["id"],
        "request_id": request_id, "prompt": prompt}))["run"]
    task = service.tasks.get(run["id"])
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), args.timeout)
        except TimeoutError:
            await service.dispatch("run.cancel", {"run_id": run["id"]})
            raise
    result = service.store.one("runs", run["id"])
    if result["state"] != "completed":
        raise AssertionError(f"Qualification turn ended {result['state']}; inspect its private profile")
    if result["prompt"] != prompt:
        raise AssertionError("The current request was rewritten")
    binding = service.store.all("SELECT * FROM run_provider_bindings WHERE run_id=?", (run["id"],))[0]
    if binding["backend"] != "openai_chatgpt" or binding["model"] != args.model \
            or binding["settings"].get("reasoning_effort") != args.effort:
        raise AssertionError("Immutable provider selection changed")
    outcome = result.get("outcome") or {}
    if outcome.get("model") != args.model or outcome.get("reasoning_effort") != args.effort:
        raise AssertionError("Completed provider outcome did not preserve the selected model/effort")
    answer = _answer(service, run["id"])
    if answer != expected:
        raise AssertionError("Recalled early/middle/late facts or the superseding correction disagree with ground truth")
    names = [row["payload"].get("name") for row in service.store.all(
        "SELECT payload FROM events WHERE run_id=? AND type='tool.completed'", (run["id"],))]
    if any(name not in {"kira_history.search", "kira_history.read"} for name in names):
        raise AssertionError("Qualification executed a tool outside chat history retrieval")
    return {"run_id": run["id"], "state": result["state"], "answer_sha256": _digest(answer),
            "exact_ground_truth_match": True, "tools": names,
            "model": binding["model"], "effort": binding["settings"]["reasoning_effort"],
            "current_prompt_sha256": _digest(prompt)}


def _evidence(service: Service, session: Json) -> tuple[Json, list[str]]:
    identity = service.contexts.data.identity(session)
    state = service.contexts.data._raw_load(identity)
    if state is None:
        raise AssertionError("No durable context was recorded")
    checkpoints = [json.loads(row[0]) for row in service.store.db.execute(
        "SELECT state FROM direct_context_checkpoints WHERE session_id=? ORDER BY created_at,id", (session["id"],))]
    items = [item for record in [*checkpoints, state] for item in record["items"]]
    opaque = [item["encrypted_content"] for item in items if item.get("type") in {"compaction", "reasoning"}
              and isinstance(item.get("encrypted_content"), str)]
    native = [item for item in items if item.get("type") == "compaction"]
    return {"revision": state["revision"], "compactions": state.get("compactions", 0),
            "checkpoints": len(checkpoints), "native_compaction_items": len(native),
            "native_item_sha256": sorted({_digest(item) for item in native}),
            "output_types": sorted({str(item.get("type", "message")) for item in items}),
            "state_sha256": _digest(state), "source_cursor_sha256": _digest(state["cursor"]),
            "memory_entries": len(state["memory"])}, opaque


def _idle(service: Service) -> bool:
    return not (service.tasks or service.contexts.tasks or service.store.all("SELECT * FROM project_reservations")
                or service.store.all("SELECT * FROM direct_context_reservations")
                or service.store.all("SELECT * FROM run_cleanup WHERE state!='stopped'")
                or service.store.all("SELECT * FROM control_processes WHERE state!='stopped'"))


async def qualify(args: argparse.Namespace, *, service_factory: Callable[..., Service] = Service,
                  auth_factory: Callable[..., Any] = ChatGPTAuth,
                  evidence_kind: str = "live provider with synthetic archive and independent ground truth",
                  progress: Callable[[str], None] | None = None) -> Json:
    """Factories support offline harness tests; the command uses actual providers."""
    report: Json = {"status": "FAIL", "version": __version__, "kind": evidence_kind,
        "selection": {"backend": "openai_chatgpt", "model": args.model, "effort": args.effort},
        "test_only_context_window": args.test_context_window,
        "full_provider_window_qualification": False, "turns": [], "seeded_requests": 40,
        "archive_background": "compressible synthetic project discussion; only FACT identifiers are random",
        "outbound_requests_checked": 0, "literal_current_prompts_checked": 0}
    profile = args.output.parent / (args.output.stem + "-profile")
    service: Service | None = None
    opaque: list[str] = []
    checked_runs: set[str] = set()
    phase = "create_isolated_profile"

    def advance(value: str) -> None:
        nonlocal phase
        phase = value
        if progress:
            progress(value)

    with tempfile.TemporaryDirectory(prefix="kira-context-runtime-") as runtime:
        try:
            if args.output.exists() or args.output.is_symlink():
                raise FileExistsError("The qualification report already exists")
            profile.mkdir(parents=True, mode=0o700)
            paths = Paths(profile / "config", profile / "data", profile / "state", Path(runtime))
            project_root = profile / "project"
            project_root.mkdir(mode=0o700)

            async def start() -> Service:
                instance = service_factory(paths)
                await instance.start()
                try:
                    instance.direct_accounts._auth = auth_factory(args.auth_directory,
                        protect_secrets=instance.redactor.set_provider_values)
                    backend_factory = instance._backend

                    def observed_backend(backend: str) -> Any:
                        provider: Any = backend_factory(backend)
                        stream = provider._stream

                        async def observed_stream(body: Json, context: Any, *values: Any, **options: Any) -> Json:
                            if body.get("model") != args.model or body.get("reasoning", {}).get("effort") != args.effort:
                                raise AssertionError("An outbound request changed the explicit model or reasoning effort")
                            report["outbound_requests_checked"] += 1
                            round_number = values[1] if len(values) > 1 else options.get("round_")
                            if (type(round_number) is int and round_number >= 0
                                    and context.run_id not in checked_runs):
                                prompt = instance.store.one("runs", context.run_id)["prompt"]
                                literal = any(item.get("role") == "user" and (
                                    item.get("content") == prompt or isinstance(item.get("content"), list)
                                    and any(part.get("type") == "input_text" and part.get("text") == prompt
                                            for part in item["content"])) for item in body.get("input", []))
                                if not literal:
                                    raise AssertionError("The current user request was not retained literally in the outbound input")
                                checked_runs.add(context.run_id)
                                report["literal_current_prompts_checked"] += 1
                            return dict(await stream(body, context, *values, **options))

                        provider._stream = observed_stream
                        return provider

                    instance._backend = observed_backend  # type: ignore[method-assign]
                    catalog = instance.contexts.catalog

                    async def test_catalog(session: Json, provider: Any = None) -> Json:
                        original = await catalog(session, provider)
                        if not original or original.get("id") != args.model:
                            raise AssertionError("Exact selected model was not found in the connected account catalog")
                        observed = {key: original[key] for key in CATALOG_FIELDS if key in original}
                        report.setdefault("observed_catalog", observed)
                        return {**original, "context_window": args.test_context_window,
                                "max_context_window": args.test_context_window,
                                "max_output_tokens": None}

                    instance.contexts.catalog = test_catalog  # type: ignore[method-assign]
                    attach = instance.contexts.attach_history

                    def history_only(session: Json, run_id: str, options: Json) -> Json:
                        value = attach(session, run_id, options)
                        value["client_tools"] = [item for item in value["client_tools"] if item["name"] == "kira_history"]
                        return value

                    instance.contexts.attach_history = history_only  # type: ignore[method-assign]
                    return instance
                except BaseException:
                    await instance.close()
                    raise

            service = await start()
            status = await service.direct_accounts.auth.status()
            account_keys = sorted(row["id"] for row in status.get("accounts", []))
            account_id = status.get("active_account_id")
            if not account_id or account_id not in account_keys:
                raise AssertionError("The existing OAuth store has no active connected account")
            report["account_identity_sha256"] = _digest(account_id)
            project = (await service.dispatch("project.create", {"name": "Synthetic direct context qualification",
                "root": str(project_root), "permission": "read_only", "instructions":
                "This is a synthetic context-retention qualification. Use kira_history.search/kira_history.read "
                "if exact facts are missing; never guess. Do not read files, execute commands, ask "
                "questions, start agents or teams, or use network tools. Return only the requested JSON."}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "openai_chatgpt", "model": args.model,
                "settings": {"reasoning_effort": args.effort, "tools": False,
                             "openai_account_id": account_id}}))["session"]
            selection = service.store.one("sessions", session["id"])
            expected, obsolete, seeds = _seed(service, session["id"], args.test_context_window)
            report["ground_truth_sha256"] = _digest(expected)
            report["superseded_value_sha256"] = _digest(obsolete)
            originals = _digest(_records(service, seeds))
            report["original_seed_rows_sha256"] = originals
            advance("automatic_compaction_and_first_recall")
            first_prompt = ("Recall the exact synthetic FACT values from this chat's original history. "
                "Return only JSON with keys early, middle, late, corrected. Apply the latest explicit "
                "CORRECTION to corrected, never its provisional predecessor. Use kira_history.search/kira_history.read "
                "if needed. Do not guess or add keys.")
            report["turns"].append(await _turn(service, session, "context-first-recall", first_prompt, args, expected))
            first, opaque = _evidence(service, session)
            report["after_automatic"] = first
            automatic = service.store.all("SELECT payload FROM events WHERE type='context.compaction'")
            if not any(row["payload"].get("phase") == "completed" and row["payload"].get("kind") == "native"
                       and row["payload"].get("reason") != "manual" for row in automatic):
                raise AssertionError("The reduced test window did not produce confirmed automatic native compaction")
            advance("manual_native_compaction")
            before_run_count = service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            request = {"project_id": project["id"], "session_id": session["id"], "request_id": "context-manual-qualification"}
            await service.dispatch("session.context.compact", request)
            task = service.contexts.tasks.get(request["request_id"])
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), args.timeout)
            receipt = (await service.dispatch("session.context.compact", request))["operation"]
            if receipt["status"] != "completed" or not receipt.get("compacted") or receipt.get("method") != "native":
                raise AssertionError("Manual native compaction was not confirmed")
            if service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] != before_run_count:
                raise AssertionError("Manual maintenance unexpectedly created an ordinary chat run")
            report["manual_operation"] = {key: receipt[key] for key in ("status", "compacted", "method")}
            before_restart, new_opaque = _evidence(service, session)
            opaque.extend(new_opaque)
            report["before_restart"] = before_restart
            if before_restart["compactions"] < 2 or len(before_restart["native_item_sha256"]) < 2:
                raise AssertionError("Fewer than two native compaction boundaries were durably recorded")
            if not _idle(service):
                raise AssertionError("Provider work did not become idle before restart")
            advance("restart_service_and_auth_transport")
            await service.close()
            service = await start()
            restored, new_opaque = _evidence(service, service.store.one("sessions", session["id"]))
            opaque.extend(new_opaque)
            if restored["state_sha256"] != before_restart["state_sha256"]:
                raise AssertionError("Restart changed the confirmed context checkpoint")
            report["restart_preserved_state"] = True
            advance("post_restart_exact_recall")
            followup = ("After the restart, recover the same four exact FACT identifiers. Return only JSON "
                "with early, middle, late, corrected. Keep the latest correction; use chat history tools "
                "for exact originals when needed. Do not guess or add other keys.")
            report["turns"].append(await _turn(service, session, "context-restarted-recall", followup, args, expected))
            if _digest(_records(service, seeds)) != originals:
                raise AssertionError("Original archived user/assistant rows changed")
            if service.store.one("sessions", session["id"]) != selection:
                raise AssertionError("Session identity, model, reasoning effort or account binding changed")
            final_status = await service.direct_accounts.auth.status()
            if sorted(row["id"] for row in final_status.get("accounts", [])) != account_keys \
                    or final_status.get("active_account_id") != account_id:
                raise AssertionError("The existing account registrations or active account changed")
            evidence, new_opaque = _evidence(service, session)
            opaque.extend(new_opaque)
            report["final_context"] = evidence
            sources = service.store.db.execute("SELECT COUNT(DISTINCT run_id) FROM chat_history_sources WHERE session_id=?",
                                               (session["id"],)).fetchone()[0]
            if sources <= 30:
                raise AssertionError("The source archive unexpectedly retained only 30 requests")
            public_events = encode(service.store.all("SELECT * FROM events"))
            exported = await service.dispatch("project.export", {"project_id": project["id"]})
            public_export = exported.get("text") or Path(exported["path"]).read_text()
            if any(value in public_events or value in public_export for value in opaque if value):
                raise AssertionError("Opaque private context leaked into events or project export")
            if (paths.config / "openai-chatgpt" / "accounts.json").exists():
                raise AssertionError("The qualification copied credentials into its isolated profile")
            if not _idle(service):
                raise AssertionError("Qualification left active reservations or provider processes")
            if report["literal_current_prompts_checked"] != 2:
                raise AssertionError("Both initial outbound turn prompts were not independently checked")
            report.update(status="PASS", source_runs=sources, originals_preserved=True,
                exact_selection_preserved=True, account_registrations_preserved=True,
                private_state_absent_from_events_and_export=True, credentials_not_copied=True,
                cleanup_confirmed=True)
        except Exception as error:
            report["failure_phase"] = phase
            report["error_type"] = type(error).__name__
            message = str(error)
            if service is not None:
                message = service.redactor.text(message)
            for private in opaque:
                message = message.replace(private, "[private provider state]")
            report["error"] = message[:2000]
        finally:
            if service is not None:
                try:
                    await service.close()
                except Exception as error:
                    report.update(status="FAIL", cleanup_confirmed=False,
                                  cleanup_error_type=type(error).__name__)
            report["profile_directory"] = str(profile)
            report["profile_retained"] = profile.exists()
            if any(value in encode(report) for value in opaque if value):
                raise AssertionError("Private provider state must not appear in the qualification report")
            _write_report(args.output, report)
    return report


def main() -> None:
    args = arguments()
    report = asyncio.run(qualify(args, progress=lambda phase: print(json.dumps({"phase": phase}), flush=True)))
    print(json.dumps({"status": report["status"], "report": str(args.output)}), flush=True)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
