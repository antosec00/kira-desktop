"""DeepSeek Account context policy and compaction through the real service and archive.

The DeepSeek API is simulated with a mock transport; the account is synthetic.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from kira.backends.deepseek_account import API, DeepSeekAccountBackend
from kira.backends.deepseek_context import DeepSeekContext, context_policy
from kira.domain.contracts import RunContext
from kira.domain.paths import Paths
from kira.service.server import Service

MODEL = "deepseek-fixture"
SOURCES_MARKER = "Available source IDs: "
OVERFLOW_MARKER = "SYNTHETIC-OVERFLOW-ONCE"


@pytest.mark.parametrize("window", [65_536, 131_072])
def test_small_windows_keep_a_usable_input_budget(window):
    policy = context_policy({"context_window": window})

    assert policy["compact_threshold"] >= window // 2
    assert policy["compact_threshold"] + policy["reserved_tokens"] <= window


def test_large_windows_keep_output_and_headroom_reserve():
    assert context_policy({"context_window": 1_000_000})["compact_threshold"] == 800_000
    assert context_policy({"context_window": 262_144})["compact_threshold"] == 131_072
    fallback = context_policy({"context_window": 1_000_000, "catalog_fallback": True})
    assert fallback["context_window"] == 262_144 and fallback["compact_threshold"] == 131_072


async def test_first_turn_on_a_128k_model_is_not_rejected_as_too_large():
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def unused(*_args):
        raise AssertionError("not used")

    ctx = RunContext(run_id="run-first", project={"id": "project", "root": "/nonexistent"},
                     session={"backend": "deepseek_account", "model": MODEL, "settings": {}},
                     prompt="Hello", supplied_context="", emit=emit, execute_tool=unused,
                     approve=unused, cancel=asyncio.Event(),
                     context_catalog={"context_window": 131_072, "max_output_tokens": 65_536})
    context = DeepSeekContext(None, ctx)

    messages = await context.before_request([{"role": "user", "content": "Hello"}])

    assert messages == [{"role": "user", "content": "Hello"}]
    assert [kind for kind, _ in events] == ["context.status"]


class Account:
    """The account surface read by the backend and the service; no real credential."""

    token = "synthetic-deepseek-token"
    proxy_enabled = False
    proxy_api_key = ""
    proxy_url = "http://127.0.0.1:4000"
    pending = None

    def status(self):
        return {"connected": True, "login": {"phase": "signed_in"}}

    async def expire(self, token):
        raise AssertionError("the synthetic token must not expire")

    async def close(self):
        pass


def reply(text):
    events = (("message_start", {"message": {"usage": {}}}),
              ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
              ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": text}}),
              ("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {}}),
              ("message_stop", {}))
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode())


class Transport:
    def __init__(self, window):
        self.window = window
        self.summaries: list[list[str]] = []
        self.overflowed = False

    def __call__(self, request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": MODEL, "name": "Synthetic DeepSeek", "context_window": self.window,
                "max_output_tokens": 8192, "input_modalities": ["text"],
                "api_capabilities": {"messages": {"tool_calling": True}}}]})
        body = json.loads(request.content)
        texts = [block.get("text", "") for message in body["messages"] if message["role"] == "user"
                 for block in message["content"] if isinstance(block, dict)]
        summary = next((text for text in texts if SOURCES_MARKER in text), None)
        if summary is not None:
            sources = json.loads(summary.split(SOURCES_MARKER, 1)[1].split("\n", 1)[0])
            self.summaries.append(sources)
            return reply(json.dumps({"summary": "Synthetic summary of the oldest turns", "memory": [
                {"kind": "result", "text": "The oldest synthetic turns were summarized",
                 "source_ids": [sources[0]], "status": "claim"}]}))
        if OVERFLOW_MARKER in texts[-1] and not self.overflowed:
            self.overflowed = True
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                                                       "message": "maximum context length exceeded"}})
        return reply("Synthetic answer")


async def _service(folder, root, window):
    paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
    transport = Transport(window)
    account = Account()

    def factory(_name, _config):
        backend = DeepSeekAccountBackend(account)
        backend.client = httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(transport))
        return backend

    service = Service(paths, backend_factory=factory)
    await service.start()
    service.deepseek_accounts._auth = account
    project = (await service.dispatch("project.create", {
        "name": "Synthetic project", "root": str(root)}))["project"]
    session = (await service.dispatch("session.create", {
        "project_id": project["id"], "backend": "deepseek_account", "model": MODEL}))["session"]
    return service, transport, project, session


@pytest_asyncio.fixture
async def deepseek(tmp_path):
    # A short private profile keeps the service socket path within its limit.
    with tempfile.TemporaryDirectory(prefix="kira-deepseek-context-") as folder:
        services = []

        async def start(window):
            result = await _service(folder, tmp_path, window)
            services.append(result[0])
            return result

        yield start
        for service in services:
            await service.close()


async def run(service, session, index, *, size, marker=""):
    prompt = f"Synthetic request {index}: " + marker + "x" * size
    result = await service.dispatch("run.start", {
        "session_id": session["id"], "request_id": f"turn-{index}", "prompt": prompt})
    task = service.tasks.get(result["run"]["id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 10)
    completed = service.store.one("runs", result["run"]["id"])
    assert completed["state"] == "completed", completed["error"]
    return completed


def sources_by_run(service, session):
    return {f"h:{row[0]}": row[1] for row in service.store.db.execute(
        "SELECT id,run_id FROM chat_history_sources WHERE session_id=?", (session["id"],))}


async def test_manual_compaction_after_completed_turns_cites_their_sources(deepseek):
    service, transport, project, session = await deepseek(262_144)
    runs = [await run(service, session, index, size=40_000) for index in range(4)]
    params = {"project_id": project["id"], "session_id": session["id"], "request_id": "compact-manual"}
    await service.dispatch("session.context.compact", params)
    if task := service.contexts.tasks.get(params["request_id"]):
        await asyncio.wait_for(asyncio.shield(task), 10)

    operation = (await service.dispatch("session.context.compact", params))["operation"]
    assert operation["status"] == "completed", operation["error"]
    assert operation["compacted"] is True
    owners = sources_by_run(service, session)
    assert len(transport.summaries) == 1
    assert {owners[source] for source in transport.summaries[0]} == {runs[0]["id"], runs[1]["id"]}
    state = service.contexts.data.load(service.store.one("sessions", session["id"]))
    assert state["compactions"] == 1
    assert [entry["source_ids"] for entry in state["memory"]] == [[transport.summaries[0][0]]]


async def test_overflow_compaction_retries_a_turn_after_completed_turns(deepseek):
    service, transport, _project, session = await deepseek(262_144)
    runs = [await run(service, session, index, size=40_000) for index in range(4)]

    await run(service, session, 4, size=1_000, marker=OVERFLOW_MARKER)

    assert transport.overflowed and len(transport.summaries) == 1
    owners = sources_by_run(service, session)
    assert {owners[source] for source in transport.summaries[0]} <= {run["id"] for run in runs}


async def test_automatic_compaction_on_a_64k_model_after_completed_turns(deepseek):
    service, transport, _project, session = await deepseek(65_536)
    runs = [await run(service, session, index, size=16_000) for index in range(6)]

    assert len(transport.summaries) == 1
    owners = sources_by_run(service, session)
    assert {owners[source] for source in transport.summaries[0]} <= {run["id"] for run in runs[:5]}
    state = service.contexts.data.load(service.store.one("sessions", session["id"]))
    assert state["compactions"] == 1
