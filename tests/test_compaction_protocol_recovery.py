"""Real service and synthetic HTTP coverage for unusable auxiliary compaction.

These fixtures never contact an account. A completed file write precedes context
pressure, so recovery must preserve its evidence without repeating its effect.
"""

import asyncio
import copy
import json
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode


class SyntheticAuth:
    async def access_token(self, account):
        assert account == "compaction-fixture-account"
        return "compaction-fixture-token"

    async def status(self):
        return {"active_account_id": "compaction-fixture-account"}


def message(text):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}]}


def stream(*events):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(event) + "\n\n" for event in events).encode())


def completed(items):
    return stream({"type": "response.completed", "response": {
        "status": "completed", "output": items,
        "usage": {"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010}}})


class RecoveryTransport:
    def __init__(self):
        self.calls = []
        self.pressure = False
        self.seed_requested = False
        self.bad_index = 64
        self.bad_item = {"type": "reasoning", "encrypted_content": "unusable-private-auxiliary"}
        self.prefix = []
        self.native_failure = None
        self.summary_failure = None
        self.main_failure = False
        self.hold_summary = False
        self.summary_started = asyncio.Event()
        self.release_summary = asyncio.Event()
        self.native_started = asyncio.Event()
        self.native_observer = lambda: None

    def malformed(self):
        return stream(*self.prefix, {"type": "response.output_item.done", "output_index": self.bad_index,
                       "item": self.bad_item})

    async def __call__(self, request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"slug": "compaction-fixture-model",
                "display_name": "Synthetic model", "visibility": "list", "context_window": 100000}]})
        body = json.loads(request.content)
        if request.url.path.endswith("input_tokens"):
            compacted = "Kira generated historical summary." in encode(body["input"])
            return httpx.Response(200, json={"input_tokens": 90000
                if self.pressure and not compacted else 1000})
        assert request.url.path.endswith("/responses")
        if body.get("text", {}).get("format", {}).get("name") == "kira_context_memory":
            phase = "summary"
        elif body.get("context_management") == [{"type": "compaction", "compact_threshold": 1000}]:
            phase = "native"
        else:
            phase = "ordinary"
        self.calls.append((phase, copy.deepcopy(body)))
        assert body["model"] == "compaction-fixture-model"
        assert body["reasoning"] == {"effort": "high", **(
            {"summary": "auto"} if phase == "ordinary" else {})}
        if phase == "native":
            assert "tools" not in body
            assert "Create the historical handoff now" not in encode(body)
            self.native_observer()
            self.native_started.set()
            if self.native_failure in {401, 429}:
                return httpx.Response(self.native_failure, json={"error": {
                    "code": "invalid_api_key" if self.native_failure == 401 else "insufficient_quota"}})
            if self.native_failure == "transport":
                raise httpx.ReadError("Synthetic disconnected stream", request=request)
            if self.native_failure == "cancel":
                await asyncio.Event().wait()
            return self.malformed()
        if phase == "summary":
            assert "tools" not in body and "context_management" not in body
            assert "unusable-private-auxiliary" not in encode(body)
            self.summary_started.set()
            if self.hold_summary:
                await self.release_summary.wait()
            if self.summary_failure == "protocol":
                return self.malformed()
            if self.summary_failure == "ungrounded":
                return completed([message(json.dumps({"summary": "An unsupported claim", "memory": [
                    {"kind": "constraint", "text": "Unsupported", "source_ids": ["h:not-a-source"],
                     "status": "claim"}]}))])
            envelope = json.loads(body["input"][-1]["content"].split("\n", 1)[1])
            sources = envelope["source_ids"]
            memory = [{"kind": "constraint", "text": "Preserve the completed receipt file",
                       "source_ids": [sources[0]], "status": "claim"}] if sources else []
            return completed([message(json.dumps({
                "summary": "The receipt file was created once; retain it.", "memory": memory}))])
        if self.main_failure:
            return self.malformed()
        if not self.seed_requested:
            self.seed_requested = True
            return completed([{"type": "function_call", "call_id": "write-receipt-once",
                "namespace": "files", "name": "write_file", "arguments": json.dumps({
                    "path": "receipt.txt", "content": "confirmed-once", "expected_sha256": None})}])
        return completed([message("The original work is preserved; continue from its result.")])


@pytest_asyncio.fixture
async def recovery_service(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-compaction-recovery-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        transport = RecoveryTransport()

        def factory(*_args):
            return OpenAIChatGPTBackend(SyntheticAuth(), transport=httpx.MockTransport(transport))

        service = Service(paths, backend_factory=factory)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Recovery fixture", "root": str(tmp_path),
            "permission": "project_write"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
            "model": "compaction-fixture-model", "settings": {
                "reasoning_effort": "high", "openai_account_id": "compaction-fixture-account"}}))["session"]
        try:
            yield service, transport, project, session
        finally:
            transport.release_summary.set()
            await service.close()


async def start(service, session, identity, prompt="Continue from the confirmed result"):
    result = await service.dispatch("run.start", {"session_id": session["id"],
        "request_id": identity, "prompt": prompt})
    return result["run"]["id"]


async def settled(service, run_id):
    if task := service.tasks.get(run_id):
        await asyncio.wait_for(asyncio.shield(task), 10)
    return service.store.one("runs", run_id)


def state(service, session):
    return service.contexts.data.load(service.store.one("sessions", session["id"]))


def checkpoints(service):
    return service.store.all("SELECT * FROM direct_context_checkpoints ORDER BY rowid")


def events(service, run_id):
    return service.store.events(run_id)["events"]


def assert_one_write(service, project):
    writes = service.store.db.execute("SELECT payload FROM events WHERE type='tool.completed' "
        "AND json_extract(payload,'$.name')='write_file'").fetchall()
    assert len(writes) == 1
    assert json.loads(writes[0][0])["result"]["ok"] is True
    assert (Path(project["root"]) / "receipt.txt").read_text() == "confirmed-once"


async def seed(service, transport, session):
    seed_id = await start(service, session, "seed", "Create the receipt once and preserve it")
    assert (await settled(service, seed_id))["state"] == "completed"
    transport.pressure = True
    transport.calls.clear()
    return seed_id


@pytest.mark.parametrize(("index", "item"), [
    (None, {"type": "reasoning"}), ("0", {"type": "reasoning"}),
    (True, {"type": "reasoning"}), (-1, {"type": "reasoning"}),
    (64, {"type": "reasoning"}), (4096, {"type": "reasoning"}),
    (0, "unusable-private-auxiliary"), (0, None),
])
async def test_unusable_auxiliary_recovers_with_readable_memory_without_replaying_work(
        recovery_service, index, item):
    service, transport, project, session = recovery_service
    seed_id = await seed(service, transport, session)
    seed_events = events(service, seed_id)
    settings = copy.deepcopy(service.store.one("sessions", session["id"])["settings"])
    transport.bad_index, transport.bad_item = index, item
    transport.hold_summary = True
    before = {}

    def remember_boundary():
        before.update(context=state(service, session), checkpoints=checkpoints(service))

    transport.native_observer = remember_boundary
    run_id = await start(service, session, "recover", "Continue after maintenance: KEEP-CURRENT-EXACT")
    await asyncio.wait_for(transport.summary_started.wait(), 5)
    assert state(service, session) == before["context"]
    assert checkpoints(service) == before["checkpoints"]
    assert events(service, seed_id) == seed_events
    assert_one_write(service, project)
    transport.release_summary.set()
    result = await settled(service, run_id)
    assert result["state"] == "completed", result
    assert [phase for phase, _ in transport.calls] == ["native", "summary", "ordinary"]
    native, summary, ordinary = [body for _, body in transport.calls]
    assert "KEEP-CURRENT-EXACT" not in encode(native["input"])
    assert "KEEP-CURRENT-EXACT" not in encode(summary["input"])
    assert "KEEP-CURRENT-EXACT" in encode(ordinary["input"])
    assert "Kira generated historical summary." in encode(ordinary["input"])
    assert "context_management" not in ordinary
    saved = state(service, session)
    assert saved["capabilities"]["server_compaction"] is False
    assert saved["compactions"] == before["context"]["compactions"] + 1
    assert saved["memory"][0]["text"] == "Preserve the completed receipt file"
    recorded = events(service, run_id)
    notices = [event["payload"] for event in recorded if event["type"] == "backend.notice"
               and event["payload"].get("code") == "native_compaction_response_unusable"]
    assert len(notices) == 1
    assert notices[0]["validation"]["maintenance_phase"] == "native_fold"
    assert "unusable-private-auxiliary" not in encode(recorded)
    assert any(event["type"] == "context.compaction" and event["payload"].get("kind") == "structured"
               and event["payload"].get("phase") == "completed" for event in recorded)
    assert events(service, seed_id) == seed_events
    assert service.store.one("sessions", session["id"])["settings"] == settings
    assert_one_write(service, project)
    # A later ordinary turn loads the persisted capability decision and readable context.
    transport.calls.clear()
    next_id = await start(service, session, "next-turn")
    assert (await settled(service, next_id))["state"] == "completed"
    assert [phase for phase, _ in transport.calls] == ["ordinary"]
    assert "context_management" not in transport.calls[0][1]
    assert_one_write(service, project)


@pytest.mark.parametrize("failure", ["protocol", "ungrounded"])
async def test_failed_summary_retains_prior_checkpoint_and_original_evidence(recovery_service, failure):
    service, transport, project, session = recovery_service
    seed_id = await seed(service, transport, session)
    seed_events = events(service, seed_id)
    before = {}
    transport.native_observer = lambda: before.update(
        context=state(service, session), checkpoints=checkpoints(service))
    transport.summary_failure = failure
    run_id = await start(service, session, "unsuccessful-recovery")
    result = await settled(service, run_id)
    # Each case must end for its own cause, never for an unrelated fixture
    # assertion that the service records as an ordinary failed run.
    if failure == "protocol":
        assert result["state"] == "interrupted", result
        assert result["outcome"]["maintenance_phase"] == "memory_summary"
        assert result["outcome"]["source"] == "kira_output_validation"
    else:
        assert result["state"] == "failed", result
        assert result["error"] == "Context memory contains an ungrounded or invalid source claim"
    assert [phase for phase, _ in transport.calls] == ["native", "summary"]
    assert state(service, session) == before["context"]
    assert checkpoints(service) == before["checkpoints"]
    assert events(service, seed_id) == seed_events
    assert not any(event["type"] == "context.compaction" and event["payload"].get("phase") == "completed"
                   for event in events(service, run_id))
    assert_one_write(service, project)


@pytest.mark.parametrize("failure", [401, 429, "transport", "cancel"])
async def test_auth_quota_transport_and_cancel_do_not_start_fallback(recovery_service, failure):
    service, transport, project, session = recovery_service
    await seed(service, transport, session)
    transport.native_failure = failure
    before = {}
    transport.native_observer = lambda: before.update(context=state(service, session))
    run_id = await start(service, session, "no-retry")
    if failure == "cancel":
        await asyncio.wait_for(transport.native_started.wait(), 5)
        await service.dispatch("run.cancel", {"run_id": run_id})
    result = await settled(service, run_id)
    expected = {401: "failed", 429: "failed", "transport": "interrupted", "cancel": "cancelled"}
    assert result["state"] == expected[failure], result
    if failure in {401, 429}:
        assert result["outcome"]["maintenance_phase"] == "native_fold"
    elif failure == "transport":
        assert "connection interrupted" in result["error"]
    assert [phase for phase, _ in transport.calls] == ["native"]
    assert state(service, session) == before["context"]
    assert not transport.summary_started.is_set()
    assert not any(event["payload"].get("code") == "native_compaction_response_unusable"
                   for event in events(service, run_id))
    assert_one_write(service, project)


async def test_same_protocol_error_during_ordinary_inference_stays_interrupted(recovery_service):
    service, transport, project, session = recovery_service
    await seed(service, transport, session)
    transport.pressure = False
    transport.main_failure = True
    run_id = await start(service, session, "ordinary-protocol-error")
    result = await settled(service, run_id)
    assert result["state"] == "interrupted"
    assert "output item" in result["error"]
    assert [phase for phase, _ in transport.calls] == ["ordinary"]
    assert not transport.summary_started.is_set()
    assert not any(event["payload"].get("code") == "native_compaction_response_unusable"
                   for event in events(service, run_id))
    assert_one_write(service, project)


async def test_rejected_native_prefix_and_tool_like_output_are_never_adopted_or_executed(recovery_service):
    service, transport, project, session = recovery_service
    await seed(service, transport, session)
    transport.prefix = [{"type": "response.output_item.done", "output_index": 0,
        "item": {"type": "compaction", "encrypted_content": "unusable-private-auxiliary"}}]
    transport.bad_index = 64
    transport.bad_item = {"type": "function_call", "call_id": "untrusted-auxiliary-tool",
        "name": "write_file", "arguments": json.dumps({"path": "must-not-exist.txt",
            "content": "auxiliary continuation", "expected_sha256": None})}
    run_id = await start(service, session, "discard-partial-native-state")
    result = await settled(service, run_id)
    assert result["state"] == "completed", result
    assert [phase for phase, _ in transport.calls] == ["native", "summary", "ordinary"]
    assert "unusable-private-auxiliary" not in encode(state(service, session))
    assert "untrusted-auxiliary-tool" not in encode(events(service, run_id))
    assert not (Path(project["root"]) / "must-not-exist.txt").exists()
    assert_one_write(service, project)
