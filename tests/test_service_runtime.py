import asyncio
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.redaction import Redactor
from kira.service.storage import Store


class FixtureBackend:
    def __init__(self):
        self.runs = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.contexts = []

    async def discover(self):
        return {"available": True, "models": [{"id": "fixture-only"}], "capabilities": {}}

    async def login(self):
        return {"type": "chatgpt", "authUrl": "https://example.invalid/fixture-login"}

    async def run(self, ctx):
        self.runs += 1
        self.contexts.append(ctx)
        self.started.set()
        await ctx.emit("message.delta", {"content": "fixture "})
        release = asyncio.create_task(self.release.wait())
        stop = asyncio.create_task(ctx.cancel.wait())
        try:
            await asyncio.wait({release, stop}, return_when=asyncio.FIRST_COMPLETED)
            if ctx.cancel.is_set():
                return {"state": "cancelled", "outcome": {"fixture_confirmed": True}}
            await ctx.emit("message.delta", {"content": "answer"})
            await ctx.emit("message.completed", {"content": "fixture answer"})
            return {"state": "completed", "outcome": {"fixture_confirmed": True}}
        finally:
            release.cancel()
            stop.cancel()
            await asyncio.gather(release, stop, return_exceptions=True)

    async def close(self):
        self.closed = True


@pytest_asyncio.fixture
async def runtime(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-runtime-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        backend = FixtureBackend()
        service = Service(Paths.from_env(), backend_factory=lambda name, settings: backend)
        await service.start()
        project = (await service.dispatch("project.create", {"root": str(tmp_path), "name": "Fixture project"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "ollama", "model": "fixture-only"}))["session"]
        yield service, backend, project, session
        await service.close()


async def start(runtime, request_id="stable-request"):
    service, backend, project, session = runtime
    run = (await service.dispatch("run.start", {"session_id": session["id"], "prompt": "fixture prompt", "request_id": request_id}))["run"]
    await asyncio.wait_for(backend.started.wait(), 30)
    return run


async def settle(service, run_id):
    task = service.tasks.get(run_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 30)
    return service.store.one("runs", run_id)


async def test_durable_idempotency_and_active_project_exclusion(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    duplicate = await service.dispatch("run.start", {"session_id": session["id"], "prompt": "fixture prompt", "request_id": "stable-request"})
    assert duplicate["run"]["id"] == run["id"] and backend.runs == 1
    with pytest.raises(KiraError, match="already has an active"):
        await service.dispatch("run.start", {"session_id": session["id"], "prompt": "other", "request_id": "another-request"})
    with pytest.raises(KiraError, match="different input"):
        await service.dispatch("run.start", {"session_id": session["id"], "prompt": "changed", "request_id": "stable-request"})
    backend.release.set()
    final = await settle(service, run["id"])
    assert final["state"] == "completed"
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    assert snapshot["messages"][-1]["content"] == "fixture answer"
    assert snapshot["runs"][0]["last_seq"] == final["last_seq"]


async def test_permission_changes_and_shutdown_rejected_during_run(runtime):
    service, backend, project, session = runtime
    await start(runtime)
    for method, params in (("project.update", {"project_id": project["id"], "permission": "trusted_host"}),
                           ("service.shutdown", {}), ("settings.update", {"tool_timeout": 10})):
        with pytest.raises(KiraError) as failure:
            await service.dispatch(method, params)
        assert failure.value.code == "BUSY"
    assert not service.stopping.is_set()


async def test_cancellation_is_requested_then_confirmed(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    response = await service.dispatch("run.cancel", {"run_id": run["id"]})
    assert response["run"]["state"] == "cancelling"
    final = await settle(service, run["id"])
    assert final["state"] == "cancelled"
    states = [event["payload"]["state"] for event in service.store.events(run["id"])["events"] if event["type"] == "run.state"]
    assert states == ["queued", "running", "cancelling", "cancelled"]


async def test_tool_permissions_are_enforced_in_service(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    denied_write = await service._execute_tool(run["id"], project, asyncio.Event(), "write_file", {"path": "must-not-exist", "content": "denied"})
    denied_exec = await service._execute_tool(run["id"], project, asyncio.Event(), "run_command", {"argv": ["true"]})
    assert denied_write["error"]["code"] == "PERMISSION_DENIED"
    assert denied_exec["error"]["code"] == "PERMISSION_DENIED"
    assert not (Path(project["root"]) / "must-not-exist").exists()


async def test_write_emits_confirmed_tool_and_artifact_evidence(runtime):
    service, backend, project, session = runtime
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "project_write"})
    project = service.store.one("projects", project["id"])
    run = await start(runtime)
    result = await service._execute_tool(run["id"], project, asyncio.Event(), "write_file", {"path": "result.txt", "content": "confirmed fixture"})
    assert result["ok"] and (Path(project["root"]) / "result.txt").read_text() == "confirmed fixture"
    events = service.store.events(run["id"])["events"]
    assert any(event["type"] == "artifact.created" and event["payload"]["sha256"] == result["sha256"] for event in events)
    assert events[-1]["type"] == "tool.completed"


async def test_memory_context_export_and_backup_use_committed_data(runtime):
    service, backend, project, session = runtime
    memory = {"objective": "Fixture objective", "decisions": [{"text": "verified", "evidence": "fixture://1"}], "open_questions": ["unknown"]}
    await service.dispatch("project.update", {"project_id": project["id"], "memory": memory, "instructions": "Fixture instructions"})
    run = await start(runtime)
    supplied = backend.contexts[-1].supplied_context
    assert "Fixture instructions" in supplied and "project:memory" in supplied
    assert "Fixture objective" in service.knowledge.read(project["id"], session["id"], "project:memory")["content"]
    backup = await service.dispatch("database.backup", {})
    with sqlite3.connect(backup["path"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 0
        assert connection.execute("SELECT state FROM runs WHERE id=?", (run["id"],)).fetchone()[0] == "running"
    assert os.stat(backup["path"]).st_mode & 0o777 == 0o600
    exported = await service.dispatch("project.export", {"project_id": project["id"]})
    assert json.loads(exported["text"])["project"]["memory"] == memory


async def test_inspectable_context_includes_current_prompt_and_supplied_conversation(runtime):
    service, backend, project, session = runtime
    first = await start(runtime)
    backend.release.set()
    await settle(service, first["id"])
    second = (await service.dispatch("run.start", {"session_id": session["id"], "prompt": "Current second request", "request_id": "second-context"}))["run"]
    await settle(service, second["id"])
    context = next(event["payload"]["content"] for event in service.store.events(second["id"])["events"] if event["type"] == "context.supplied")
    assert "Current user request:\nCurrent second request" in context
    assert "Conversation supplied:" in context and "fixture answer" in context
    assert '"request":{"ref":"chat:request:' in backend.contexts[-1].supplied_context
    assert "Current second request" in backend.contexts[-1].supplied_context


async def test_unicode_instruction_limits_apply_to_encoded_bytes(runtime):
    service, backend, project, session = runtime
    with pytest.raises(KiraError) as failure:
        await service.dispatch("project.update", {"project_id": project["id"], "instructions": "😀" * 100_000})
    assert failure.value.code == "INVALID_ARGUMENT"
    with pytest.raises(KiraError) as failure:
        await service.dispatch("project.update", {"project_id": project["id"], "memory": {"content": "😀" * 30_000}})
    assert failure.value.code == "INVALID_ARGUMENT"


async def test_large_export_is_complete_private_file_and_snapshot_is_bounded(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    for number in range(12):
        await service.emit(run["id"], "message.completed", {"content": f"message-{number}:" + "x" * 100_000})
    exported = await service.dispatch("project.export", {"project_id": project["id"]})
    path = Path(exported["path"])
    assert path.parent == service.paths.data / "exports"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert exported["bytes"] == path.stat().st_size > 1_048_576
    contents = json.loads(path.read_text())
    assert len([message for message in contents["messages"] if message["content"].startswith("message-")]) == 12
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    assert snapshot["history_truncated"] and len(json.dumps(snapshot).encode()) < 1_048_576


async def test_snapshot_bounds_utf8_run_metadata_without_losing_run_cursors(runtime):
    service, backend, project, session = runtime
    identifiers = []
    for number in range(8):
        run, _ = service.store.create_run(session["id"], f"large-{number}", "😀" * 37_000)
        service.store.event(run["id"], "run.state", {"state": "completed", "outcome": {"fixture": "😀" * 37_000}})
        identifiers.append(run["id"])
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    assert len(json.dumps(snapshot, ensure_ascii=False).encode()) < 710_000
    assert snapshot["history_truncated"]
    assert [run["id"] for run in snapshot["runs"]] == identifiers
    assert all(run["last_seq"] == 3 and run["prompt_truncated"] for run in snapshot["runs"])
    export = await service.dispatch("project.export", {"project_id": project["id"]})
    contents = json.loads(Path(export["path"]).read_text())
    assert contents["runs"][0]["prompt"] == "😀" * 37_000
    assert contents["runs"][0]["outcome"]["fixture"] == "😀" * 37_000


@pytest.mark.parametrize("settings", [{"tools": "true"}, {"num_ctx": True}, {"reasoning_effort": 9},
                                       {"max_tool_rounds": -1}, {"sandbox": "danger-full-access"}])
async def test_session_settings_reject_invalid_or_unrecognized_controls(runtime, settings):
    service, backend, project, session = runtime
    with pytest.raises(KiraError) as failure:
        await service.dispatch("session.create", {"project_id": project["id"], "backend": "ollama", "model": "fixture-only", "settings": settings})
    assert failure.value.code == "INVALID_ARGUMENT"


async def test_backend_login_keeps_oauth_process_alive(runtime):
    service, backend, project, session = runtime
    result = await service.dispatch("backend.login", {"backend": "codex"})
    assert result["authUrl"].startswith("https://example.invalid/")
    assert not backend.closed
    await service.dispatch("backend.discover", {"backend": "codex"})
    assert not backend.closed


async def test_private_socket_and_single_service(runtime):
    service, backend, project, session = runtime
    assert service.paths.socket.stat().st_mode & 0o777 == 0o600
    assert service.paths.runtime.stat().st_mode & 0o777 == 0o700
    other = Service(service.paths)
    with pytest.raises(KiraError) as failure:
        await other.start()
    assert failure.value.code == "ALREADY_RUNNING"
    assert service.paths.socket.exists()


async def test_different_data_store_cannot_replace_live_socket(runtime):
    service, backend, project, session = runtime
    paths = Paths(service.paths.config, service.paths.data / "different-store", service.paths.state, service.paths.runtime)
    other = Service(paths)
    with pytest.raises(KiraError) as failure:
        await other.start()
    assert failure.value.code == "ALREADY_RUNNING"
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    assert (await rpc(reader, writer, "hello", {}))["result"]["version"] == 1
    writer.close()
    await writer.wait_closed()


async def rpc(reader, writer, method, params, identifier="request", version=1):
    writer.write(json.dumps({"version": version, "id": identifier, "method": method, "params": params}).encode() + b"\n")
    await writer.drain()
    while True:
        packet = json.loads(await asyncio.wait_for(reader.readline(), 3))
        if packet.get("id") == identifier:
            return packet


async def test_invalid_version_rejected_before_mutation_and_types_are_strict(runtime):
    service, backend, project, session = runtime
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    response = await rpc(reader, writer, "project.create", {"name": "never", "root": project["root"]}, version=2)
    assert response["error"]["code"] == "INCOMPATIBLE_VERSION"
    response = await rpc(reader, writer, "run.events", {"run_id": "missing", "after_seq": True})
    assert response["error"]["code"] == "INVALID_ARGUMENT"
    assert len((await service.dispatch("project.list", {}))["projects"]) == 1
    writer.close()
    await writer.wait_closed()


async def test_socket_disconnect_replay_and_live_subscription_have_no_gaps(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    response = await rpc(reader, writer, "run.subscribe", {"run_id": run["id"], "after_seq": 0})
    cursor = response["result"]["events"][-1]["seq"]
    writer.close()
    await writer.wait_closed()
    await service.emit(run["id"], "backend.notice", {"message": "while disconnected"})
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    response = await rpc(reader, writer, "run.subscribe", {"run_id": run["id"], "after_seq": cursor})
    replay = response["result"]["events"]
    assert replay[0]["seq"] == cursor + 1
    backend.release.set()
    received = list(replay)
    async with asyncio.timeout(5):
        while not any(event["type"] == "run.state" and event["payload"]["state"] == "completed" for event in received):
            packet = json.loads(await reader.readline())
            received.append(packet["event"])
    assert [event["seq"] for event in received] == list(range(cursor + 1, received[-1]["seq"] + 1))
    assert backend.runs == 1
    writer.close()
    await writer.wait_closed()


async def test_bounded_replay_is_contiguous(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    for number in range(12):
        await service.emit(run["id"], "backend.notice", {"message": f"fixture-{number}"})
    cursor = 0
    seen = []
    while True:
        page = await service.dispatch("run.events", {"run_id": run["id"], "after_seq": cursor, "limit": 3})
        seen.extend(page["events"])
        cursor = seen[-1]["seq"]
        if not page["has_more"]:
            break
    assert [event["seq"] for event in seen] == list(range(1, cursor + 1))


async def test_service_close_marks_unconfirmed_runs_interrupted(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    path = service.paths.db
    await service.close()
    store = Store(path)
    assert store.one("runs", run["id"])["state"] == "interrupted"
    store.close()


async def test_storage_recovery_never_replays_side_effect(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    backend.release.set()
    await settle(service, run["id"])
    # An unfinished persisted state represents a crash boundary fixture, not a live backend.
    service.store.db.execute("UPDATE runs SET state='running' WHERE id=?", (run["id"],))
    recovered = service.store.recover()
    assert recovered[-1]["payload"]["state"] == "interrupted"
    assert recovered[-1]["payload"]["outcome"]["automatic_replay"] is False
    assert service.store.recover() == []
    assert backend.runs == 1


async def test_correlated_approval_has_durable_request_and_decision(runtime):
    service, backend, project, session = runtime
    run = await start(runtime)
    work = asyncio.create_task(service._approve(run["id"], {"backend": "fixture", "message": "fixture approval"}, asyncio.Event()))
    await asyncio.sleep(0)
    event = service.store.events(run["id"])["events"][-1]
    assert event["type"] == "approval.requested"
    approval_id = event["payload"]["approval_id"]
    assert not (await service.dispatch("run.approve", {"run_id": "other", "approval_id": approval_id, "decision": "accept"}))["accepted"]
    assert (await service.dispatch("run.approve", {"run_id": run["id"], "approval_id": approval_id, "decision": "accept"}))["accepted"]
    assert await work == {"decision": "accept"}
    assert service.store.one("runs", run["id"])["state"] == "running"


async def test_canary_redacted_before_events_export_artifacts_and_database(tmp_path, monkeypatch):
    canary = "fake-canary-never-real-credential-18371"
    monkeypatch.setenv("KIRA_TEST_API_KEY", canary)
    with tempfile.TemporaryDirectory(prefix="kira-canary-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        backend = FixtureBackend()
        service = Service(backend_factory=lambda name, settings: backend)
        await service.start()
        try:
            project = (await service.dispatch("project.create", {"name": "Canary fixture", "root": str(tmp_path), "instructions": canary}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "ollama", "model": "fixture-only"}))["session"]
            run = (await service.dispatch("run.start", {"session_id": session["id"], "prompt": canary, "request_id": "canary-fixture"}))["run"]
            await asyncio.wait_for(backend.started.wait(), 30)
            await service.emit(run["id"], "tool.completed", {"tool_id": "fixture", "name": "fixture", "result": {"stdout": canary, "access_token": "unregistered-fake-canary-value"}})
            artifact = service._save_artifact(run["id"], "canary", canary)
            assert canary not in Path(artifact["path"]).read_text()
            exported = await service.dispatch("project.export", {"project_id": project["id"]})
            assert canary not in exported["text"] and "unregistered-fake-canary-value" not in exported["text"]
            assert "[REDACTED]" in exported["text"]
            assert canary not in " ".join(service.store.db.iterdump())
        finally:
            await service.close()


STREAM_CANARY = "fixture-stream-sensitive-value-123456"


@pytest_asyncio.fixture
async def sanitized_runtime(monkeypatch, tmp_path):
    # Do not inspect host credentials to construct this test's redaction policy.
    monkeypatch.setattr("kira.service.server.Redactor",
                        lambda: Redactor(values=(STREAM_CANARY,)))
    with tempfile.TemporaryDirectory(prefix="kira-sanitize-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        backend = FixtureBackend()
        service = Service(paths, backend_factory=lambda name, settings: backend)
        await service.start()
        project = (await service.dispatch("project.create", {
            "root": str(tmp_path), "name": "Sanitation fixture"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"],
                   "backend": "ollama", "model": "fixture-only"}))["session"]
        try:
            yield service, backend, project, session
        finally:
            await service.close()


async def test_stream_sanitation_survives_client_reconnect_and_completion(sanitized_runtime):
    service, backend, project, session = sanitized_runtime
    run = await start(sanitized_runtime)
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    response = await rpc(reader, writer, "run.subscribe", {"run_id": run["id"], "after_seq": 0})
    cursor = response["result"]["events"][-1]["seq"]
    await service.emit(run["id"], "message.delta", {"item_id": "a", "content": "First: " + STREAM_CANARY[:15]})
    writer.close()
    await writer.wait_closed()
    await service.emit(run["id"], "usage", {"source": "fixture", "output_tokens": 1})
    await service.emit(run["id"], "backend.notice", {"message": "Still connected to fixture provider"})
    assert service._stream_redactors[run["id"]]["a"].pending == STREAM_CANARY[:15]
    # An interleaved item has its own prefix state.
    await service.emit(run["id"], "message.delta", {"item_id": "b", "content": "Second: " + STREAM_CANARY[:9]})
    await service.emit(run["id"], "message.delta", {"item_id": "a", "content": STREAM_CANARY[15:] + "."})
    await service.emit(run["id"], "message.completed", {"item_id": "a"})
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    try:
        replay = (await rpc(reader, writer, "run.subscribe", {"run_id": run["id"], "after_seq": cursor}))["result"]["events"]
        await service.emit(run["id"], "message.delta", {"item_id": "b", "content": STREAM_CANARY[9:] + "."})
        await service.emit(run["id"], "message.completed", {"item_id": "b", "content": "Second: " + STREAM_CANARY + "."})
        received = replay[:]
        async with asyncio.timeout(3):
            while not any(event["type"] == "message.completed" and event["payload"].get("item_id") == "b" for event in received):
                received.append(json.loads(await reader.readline())["event"])
        assert [event["seq"] for event in received] == list(range(cursor + 1, received[-1]["seq"] + 1))
        for item, expected in (("a", "First: [REDACTED]."), ("b", "Second: [REDACTED].")):
            assert "".join(event["payload"]["content"] for event in received
                           if event["type"] == "message.delta" and event["payload"].get("item_id") == item) == expected
        completed = [event["payload"] for event in received if event["type"] == "message.completed"]
        assert "content" not in completed[0]
        assert completed[-1]["content"] == "Second: [REDACTED]."
        assert STREAM_CANARY[:9] not in json.dumps(received)
        exported = await service.dispatch("project.export", {"project_id": project["id"]})
        assert STREAM_CANARY[:9] not in exported["text"]
        assert STREAM_CANARY[:9] not in " ".join(service.store.db.iterdump())
    finally:
        writer.close()
        await writer.wait_closed()
        backend.release.set()
        await settle(service, run["id"])
    assert run["id"] not in service._stream_redactors


@pytest.mark.parametrize("terminal", ["completed", "failed", "interrupted", "cancelled", "exception"])
async def test_terminal_stream_prefix_is_suppressed_and_state_released(sanitized_runtime, terminal):
    service, backend, project, session = sanitized_runtime

    async def partial(ctx):
        backend.started.set()
        await ctx.emit("message.delta", {"content": "Public: " + STREAM_CANARY[:12]})
        if terminal == "exception":
            raise RuntimeError("Fixture provider ended")
        return {"state": terminal}

    backend.run = partial
    run = await start(sanitized_runtime)
    final = await settle(service, run["id"])
    events = service.store.events(run["id"])["events"]
    deltas = [event for event in events if event["type"] == "message.delta"]
    assert "".join(event["payload"]["content"] for event in deltas) == "Public: [REDACTED]"
    assert all("item_id" not in event["payload"] for event in deltas)
    assert events[-1]["type"] == "run.state"
    assert final["state"] == ("failed" if terminal == "exception" else terminal)
    assert run["id"] not in service._stream_redactors
    assert STREAM_CANARY[:12] not in " ".join(service.store.db.iterdump())


async def test_sanitation_precedes_large_artifact_and_nested_provider_persistence(sanitized_runtime):
    service, backend, project, session = sanitized_runtime
    run = await start(sanitized_runtime)
    payload = {"item_id": "large", "content": "Public " + STREAM_CANARY,
               "provider_messages": [{"role": "assistant", "content": STREAM_CANARY,
                   "thinking": "fixture-private-thought", "extra": {
                       "reasoning": "fixture-private-thought",
                       "nested": [{"analysis": "fixture-private-thought"}],
                       "public_padding": "x" * 181_000}}]}
    await service.emit(run["id"], "message.completed", payload)
    artifacts = service.store.all("SELECT * FROM artifacts WHERE run_id=?", (run["id"],))
    assert artifacts
    for artifact in artifacts:
        text = Path(artifact["path"]).read_text()
        assert STREAM_CANARY not in text and "fixture-private-thought" not in text
        assert "[REDACTED]" in text
    database = " ".join(service.store.db.iterdump())
    assert STREAM_CANARY not in database and "fixture-private-thought" not in database
    backend.release.set()
    await settle(service, run["id"])


@pytest.mark.parametrize("force_task", [False, True])
async def test_cancellation_never_publishes_unfinished_sensitive_prefix(sanitized_runtime, force_task):
    service, backend, project, session = sanitized_runtime
    run = await start(sanitized_runtime)
    await service.emit(run["id"], "message.delta", {"item_id": "cancelled-partial",
                        "content": "Visible: " + STREAM_CANARY[:12]})
    if force_task:
        service.tasks[run["id"]].cancel()
    else:
        await service.dispatch("run.cancel", {"run_id": run["id"]})
    final = await settle(service, run["id"])
    events = service.store.events(run["id"])["events"]
    assert final["state"] == ("interrupted" if force_task else "cancelled")
    assert "".join(event["payload"]["content"] for event in events
                   if event["type"] == "message.delta"
                   and event["payload"].get("item_id") == "cancelled-partial") == "Visible: [REDACTED]"
    assert run["id"] not in service._stream_redactors
    assert STREAM_CANARY[:12] not in " ".join(service.store.db.iterdump())


async def test_stream_item_and_pending_memory_limits_are_explicit(sanitized_runtime, monkeypatch):
    service, backend, project, session = sanitized_runtime
    run = await start(sanitized_runtime)
    with pytest.raises(KiraError, match="item ID"):
        await service.emit(run["id"], "message.delta", {"item_id": "x" * 257, "content": "ordinary"})
    # The fixture backend owns one item without an explicit ID.
    for number in range(63):
        await service.emit(run["id"], "message.delta", {"item_id": str(number), "content": "ordinary."})
    with pytest.raises(KiraError, match="unfinished"):
        await service.emit(run["id"], "message.delta", {"item_id": "overflow", "content": "ordinary."})
    monkeypatch.setattr("kira.service.server.MAX_PENDING_CHARS", 10)
    await service.emit(run["id"], "message.delta", {"item_id": "0", "content": STREAM_CANARY[:6]})
    with pytest.raises(KiraError, match="redaction memory limit"):
        await service.emit(run["id"], "message.delta", {"item_id": "1", "content": STREAM_CANARY[:6]})
    assert run["id"] not in service._stream_redactors


@pytest.mark.parametrize("complete_message", [False, True])
async def test_synthetic_tail_metadata_has_the_same_public_sanitation(
    sanitized_runtime, monkeypatch, complete_message
):
    service, backend, project, session = sanitized_runtime
    published = []
    original_publish = service._publish

    def record_publication(run_id, event):
        published.append(event)
        original_publish(run_id, event)

    monkeypatch.setattr(service, "_publish", record_publication)
    internal_id = "item-" + STREAM_CANARY

    async def partial(ctx):
        backend.started.set()
        await ctx.emit("message.delta", {"item_id": internal_id,
                                        "content": "Visible: " + STREAM_CANARY[:12]})
        assert internal_id in service._stream_redactors[ctx.run_id]
        if complete_message:
            await ctx.emit("message.completed", {"item_id": internal_id})
        return {"state": "completed" if complete_message else "interrupted"}

    backend.run = partial
    run = await start(sanitized_runtime)
    # The provider's own outcome: a failed assertion inside it would only fail the run.
    assert (await settle(service, run["id"]))["state"] == ("completed" if complete_message else "interrupted")
    events = service.store.events(run["id"])["events"]
    deltas = [event["payload"] for event in events if event["type"] == "message.delta"]
    assert [payload["content"] for payload in deltas] == ["Visible: ", "[REDACTED]"]
    assert all(payload["item_id"] == "item-[REDACTED]" for payload in deltas)
    assert STREAM_CANARY not in json.dumps(events)
    assert STREAM_CANARY not in json.dumps(published)
    assert STREAM_CANARY not in " ".join(service.store.db.iterdump())
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    exported = await service.dispatch("project.export", {"project_id": project["id"]})
    assert STREAM_CANARY not in json.dumps(snapshot)
    assert STREAM_CANARY not in exported["text"]
    assert snapshot["messages"][-1] == {
        "run_id": run["id"], "role": "assistant", "item_id": "item-[REDACTED]",
        "content": "Visible: [REDACTED]", "complete": complete_message}


async def test_snapshot_and_export_keep_interleaved_items_in_first_event_order(sanitized_runtime):
    service, backend, project, session = sanitized_runtime

    async def interleaved(ctx):
        backend.started.set()
        await ctx.emit("message.delta", {"item_id": "first", "content": "First "})
        await ctx.emit("message.delta", {"item_id": "second", "content": "Second draft."})
        # Completion order differs from first appearance; full bodies replace deltas.
        await ctx.emit("message.completed", {"item_id": "second", "content": "Second final."})
        await ctx.emit("message.delta", {"item_id": "first", "content": "complete."})
        await ctx.emit("message.completed", {"item_id": "first"})
        return {"state": "completed"}

    backend.run = interleaved
    run = await start(sanitized_runtime)
    await settle(service, run["id"])
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    exported = json.loads((await service.dispatch("project.export", {"project_id": project["id"]}))["text"])
    expected = [
        {"run_id": run["id"], "role": "user", "content": "fixture prompt"},
        {"run_id": run["id"], "role": "assistant", "item_id": "first",
         "content": "First complete.", "complete": True},
        {"run_id": run["id"], "role": "assistant", "item_id": "second",
         "content": "Second final.", "complete": True},
    ]
    assert snapshot["messages"] == exported["messages"] == expected
    assert not snapshot["history_truncated"] and not exported["history_truncated"]


async def test_interrupted_snapshot_retains_both_unfinished_item_ids(sanitized_runtime):
    service, backend, project, session = sanitized_runtime

    async def interrupted(ctx):
        backend.started.set()
        await ctx.emit("message.delta", {"item_id": "first", "content": "First "})
        await ctx.emit("message.delta", {"item_id": "second", "content": "Second pending."})
        await ctx.emit("message.delta", {"item_id": "first", "content": "pending."})
        return {"state": "interrupted"}

    backend.run = interrupted
    run = await start(sanitized_runtime)
    await settle(service, run["id"])
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    exported = json.loads((await service.dispatch("project.export", {"project_id": project["id"]}))["text"])
    expected = [
        {"run_id": run["id"], "role": "assistant", "item_id": "first",
         "content": "First pending.", "complete": False},
        {"run_id": run["id"], "role": "assistant", "item_id": "second",
         "content": "Second pending.", "complete": False},
    ]
    assert snapshot["messages"][1:] == exported["messages"][1:] == expected


async def test_legacy_absent_and_null_item_ids_share_one_pending_message(sanitized_runtime):
    service, backend, project, session = sanitized_runtime

    async def legacy(ctx):
        backend.started.set()
        await ctx.emit("message.delta", {"content": "First "})
        await ctx.emit("message.delta", {"item_id": None, "content": "complete."})
        await ctx.emit("message.completed", {})
        await ctx.emit("message.delta", {"content": "Next pending."})
        return {"state": "interrupted"}

    backend.run = legacy
    run = await start(sanitized_runtime)
    await settle(service, run["id"])
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    exported = json.loads((await service.dispatch("project.export", {"project_id": project["id"]}))["text"])
    expected = [
        {"run_id": run["id"], "role": "assistant", "item_id": None,
         "content": "First complete.", "complete": True},
        {"run_id": run["id"], "role": "assistant", "item_id": None,
         "content": "Next pending.", "complete": False},
    ]
    assert snapshot["messages"][1:] == exported["messages"][1:] == expected


async def test_per_item_preview_limits_preserve_full_export(sanitized_runtime):
    service, backend, project, session = sanitized_runtime
    first = "First " + "é" * 17_000 + " end."
    second = "Second " + "界" * 11_000 + " end."

    async def large_items(ctx):
        backend.started.set()
        await ctx.emit("message.delta", {"item_id": "first", "content": "First draft."})
        await ctx.emit("message.delta", {"item_id": "second", "content": second})
        await ctx.emit("message.completed", {"item_id": "first", "content": first})
        await ctx.emit("message.completed", {"item_id": "second"})
        return {"state": "completed"}

    backend.run = large_items
    run = await start(sanitized_runtime)
    await settle(service, run["id"])
    snapshot = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    exported = json.loads((await service.dispatch("project.export", {"project_id": project["id"]}))["text"])
    assert snapshot["history_truncated"] and not exported["history_truncated"]
    assert snapshot["messages"][1]["content"] == first.encode()[:32_000].decode("utf-8", "ignore")
    assert snapshot["messages"][2]["content"] == second.encode()[-32_000:].decode("utf-8", "ignore")
    assert [message["item_id"] for message in exported["messages"][1:]] == ["first", "second"]
    assert [message["content"] for message in exported["messages"][1:]] == [first, second]
    assert all(message["complete"] for message in exported["messages"][1:])


def test_future_database_schema_is_refused(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA user_version=99")
    with pytest.raises(KiraError, match="newer"):
        Store(path)
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 99
