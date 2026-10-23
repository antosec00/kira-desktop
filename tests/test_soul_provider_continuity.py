"""Synthetic provider envelopes for versioned, user-level Kira identities.

These tests establish transport/context continuity, not model compliance or
reasoning quality. All providers use local scripted transports.
"""

import asyncio
import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kira.backends import codex
from kira.backends.direct_context import DirectContext
from kira.backends.ollama import NO_TOOLS_GUIDANCE, OllamaBackend
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.contracts import KiraError, RunContext
from kira.domain.paths import Paths
from kira.service.direct_context_store import DirectContextStore
from kira.service.provider_routing import ProviderRouting
from kira.service.storage import Store, encode, now


PERSONAS = (
    "Current personal profile: synthetic-studio revision 1. Prefer explicit assumptions.",
    "Current personal profile: synthetic-atelier revision 2. Prefer contrasting examples.",
)
APP_INSTRUCTIONS = "Service-owned operating instructions. User preferences are defaults."


class Auth:
    async def access_token(self, account):
        assert account == "synthetic-account"
        return "synthetic-token"

    async def status(self):
        return {"active_account_id": "synthetic-account"}


def message(text):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}]}


def response(output):
    event = {"type": "response.completed", "response": {"status": "completed",
             "output": output, "usage": {"input_tokens": 120, "output_tokens": 10}}}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content=("data: " + json.dumps(event) + "\n\n").encode())


def context(tmp_path, persona=PERSONAS[0], *, saved=None, durable=True):
    events, snapshots, effects = [], [], []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(name, arguments):
        effects.append((name, arguments))
        return {"ok": True, "content": "Synthetic verified content"}

    async def approve(_request):
        raise AssertionError("No approvals expected for synthetic continuity checks")

    async def save(payload):
        value = copy.deepcopy(payload)
        value["revision"] = (snapshots[-1]["revision"] if snapshots else
                             (saved or {}).get("revision", 0)) + 1
        snapshots.append(value)
        return value

    async def page(_params):
        return {"groups": [], "has_more": False, "next_cursor": None}

    ctx = RunContext("synthetic-run", {"root": str(tmp_path), "permission": "read_only"},
        {"id": "synthetic-session", "model": "fixture-model", "provider_generation": 0,
         "settings": {"tools": False, "reasoning_effort": "high",
                      "openai_account_id": "synthetic-account"}},
        "Continue the exact current objective; give full detail for this step.",
        "Project constraint: preserve existing work.\n" + persona,
        emit, execute, approve, asyncio.Event(), assistant_instructions=APP_INSTRUCTIONS,
        context_state=saved, context_save=save if durable else None,
        context_page=page if durable else None,
        context_catalog={"context_window": 32_000, "max_context_window": 32_000})
    return ctx, events, snapshots, effects


def is_summary(body):
    return body.get("text", {}).get("format", {}).get("name") == "kira_context_memory"


def is_fold(body):
    return body.get("context_management") == [
        {"type": "compaction", "compact_threshold": 1000}]


@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("durable", [False, True])
async def test_direct_no_tools_receives_only_user_level_persona(tmp_path, persona, durable):
    ctx, _, snapshots, effects = context(tmp_path, persona, durable=durable)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("input_tokens"):
            assert persona in json.dumps(body["input"])
            return httpx.Response(200, json={"input_tokens": 120})
        requests.append(body)
        assert body["model"] == ctx.model
        assert body["reasoning"] == {"effort": "high", "summary": "auto"}
        assert not body.get("tools")
        assert persona not in body["instructions"]
        entries = [item for item in body["input"] if persona in str(item.get("content", ""))]
        assert len(entries) == 1 and entries[0]["role"] == "user"
        assert body["input"][-1]["content"] == ctx.prompt
        return response([message("Synthetic completed answer")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert len(requests) == 1 and not effects
    if durable:
        assert snapshots and persona not in json.dumps(snapshots[-1]["items"])
    else:
        assert not snapshots


async def test_direct_freezes_admitted_capsule_across_tool_rounds(tmp_path):
    ctx, _, snapshots, effects = context(tmp_path)
    ctx.session["settings"]["tools"] = True
    ordinary = []

    async def execute(name, arguments):
        effects.append((name, arguments))
        # A settings refresh cannot alter the identity of an admitted run.
        ctx.supplied_context = PERSONAS[1]
        return {"ok": True, "content": "Synthetic verified content"}

    ctx.execute_tool = execute

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 120})
        ordinary.append(body)
        assert PERSONAS[0] in json.dumps(body["input"])
        assert PERSONAS[1] not in json.dumps(body)
        if len(ordinary) == 1:
            return response([{"type": "function_call", "id": "synthetic-tool",
                "call_id": "synthetic-call", "namespace": "files", "name": "read_file",
                "arguments": json.dumps({"path": "fixture.txt"}), "status": "completed"}])
        call = next(item for item in body["input"] if item.get("type") == "function_call")
        output = next(item for item in body["input"] if item.get("type") == "function_call_output")
        assert call["call_id"] == output["call_id"] == "synthetic-call"
        return response([message("Synthetic verified answer")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert effects == [("read_file", {"path": "fixture.txt"})]
    assert len(ordinary) == 2
    assert PERSONAS[0] not in json.dumps(snapshots[-1]["items"])


async def test_manual_native_compaction_preserves_opaque_state_then_injects_fresh_capsule(tmp_path):
    opaque = {"type": "compaction", "id": "synthetic-native",
              "encrypted_content": "synthetic-opaque-content-not-for-display"}
    ctx, events, snapshots, effects = context(tmp_path)
    ctx.context_page = None
    ctx.history = [{"role": "user", "content": "Original objective and exact acceptance criterion"}]
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((request.url.path, copy.deepcopy(body)))
        assert body["model"] == "fixture-model"
        assert body["reasoning"]["effort"] == "high"
        if request.url.path.endswith("input_tokens"):
            assert any(persona in json.dumps(body["input"]) for persona in PERSONAS)
            return httpx.Response(200, json={"input_tokens": 120})
        if is_fold(body):
            assert "Original objective and exact acceptance criterion" in json.dumps(body["input"])
            assert not any(persona in json.dumps(body) for persona in PERSONAS)
            assert "tools" not in body
            return response([opaque])
        if is_summary(body):
            assert not any(persona in json.dumps(body) for persona in PERSONAS)
            return response([message(json.dumps({"summary": "Retain original objective", "memory": []}))])
        assert body["input"][0]["role"] == "user"
        assert PERSONAS[1] in body["input"][0]["content"]
        assert PERSONAS[0] not in json.dumps(body)
        assert opaque in body["input"]
        assert body["input"][-1]["content"] == ctx.prompt
        return response([message("Continue the same objective")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.compact_context(ctx))["compacted"] is True
        assert snapshots[-1]["items"] == [opaque]
        resumed, _, resumed_snapshots, _ = context(tmp_path, PERSONAS[1], saved=snapshots[-1])
        assert (await backend.run(resumed))["state"] == "completed"
        assert resumed_snapshots[-1]["items"][0] == opaque
    finally:
        await backend.close()
    assert not effects
    assert opaque["encrypted_content"] not in json.dumps(events)
    assert len([body for _, body in requests if is_fold(body)]) == 1


async def test_automatic_structured_compaction_keeps_current_goal_and_capsule_literal(tmp_path):
    ctx, _, snapshots, _ = context(tmp_path)
    folded = False

    def handler(request):
        nonlocal folded
        body = json.loads(request.content)
        if request.url.path.endswith("input_tokens"):
            assert PERSONAS[0] in json.dumps(body["input"])
            return httpx.Response(200, json={"input_tokens": 100 if folded else 31_000})
        if is_fold(body):
            return httpx.Response(400, json={"error": {"code": "unsupported_parameter",
                "param": "context_management", "message": "Synthetic unavailable capability"}})
        if is_summary(body):
            folded = True
            assert PERSONAS[0] not in json.dumps(body)
            assert ctx.prompt not in json.dumps(body)
            return response([message(json.dumps({"summary": "Original verified task checkpoint", "memory": []}))])
        raise AssertionError("This check runs context compaction, never a model work turn")

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        manager = DirectContext(backend, ctx, "synthetic-account", APP_INSTRUCTIONS, [])
        manager.items = [{"role": "user", "content": "Historical task work"}]
        manager.append_current([{"role": "user", "content": ctx.prompt}])
        wire = await manager.before_request()
        assert wire[0]["role"] == "user" and PERSONAS[0] in wire[0]["content"]
        assert wire[-1] == {"role": "user", "content": ctx.prompt}
        assert "Original verified task checkpoint" in wire[1]["content"]
        assert PERSONAS[0] not in json.dumps(snapshots[-1]["items"])
        assert snapshots[-1]["compaction"]["kind"] == "structured"
    finally:
        await backend.close()


@pytest.mark.parametrize("persona", PERSONAS)
async def test_ollama_user_persona_does_not_change_model_or_options(tmp_path, persona):
    ctx, _, _, effects = context(tmp_path, persona, durable=False)
    ctx.session["settings"] = {"tools": False, "num_ctx": 8192, "temperature": 0.2, "think": True}
    requests = []

    def handler(request):
        body = json.loads(request.content)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        requests.append(body)
        assert body["model"] == "fixture-model"
        assert body["options"] == {"num_ctx": 8192, "temperature": 0.2}
        assert body["think"] is True
        assert "tools" not in body
        assert body["messages"][0] == {"role": "system", "content": APP_INSTRUCTIONS + NO_TOOLS_GUIDANCE}
        assert body["messages"][1]["role"] == "user"
        assert persona in body["messages"][1]["content"]
        assert body["messages"][-1] == {"role": "user", "content": ctx.prompt}
        return httpx.Response(200, content=(json.dumps({"message": {"role": "assistant",
            "content": "Synthetic answer"}, "done": True}) + "\n").encode())

    backend = OllamaBackend()
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(base_url="http://synthetic.invalid", transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert len(requests) == 1 and not effects


@pytest.mark.parametrize("persona", PERSONAS)
async def test_codex_user_persona_does_not_change_developer_instructions(tmp_path, monkeypatch, persona):
    ctx, _, _, effects = context(tmp_path, persona, durable=False)
    calls = []

    class AppServer:
        def __init__(self, _binary):
            self.notifications = asyncio.Queue()

        async def start(self):
            pass

        async def close(self):
            pass

        async def request(self, method, params, **_kwargs):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "synthetic-thread"}, "model": "fixture-model",
                    "reasoningEffort": "high", "sandbox": {"type": "readOnly"}}
            if method == "turn/start":
                await self.notifications.put({"method": "turn/completed", "params": {
                    "threadId": "synthetic-thread", "turnId": "synthetic-turn",
                    "turn": {"id": "synthetic-turn", "status": "completed"}}})
                return {"turn": {"id": "synthetic-turn", "status": "inProgress"}}
            raise AssertionError(method)

    monkeypatch.setattr(codex, "AppServer", AppServer)
    backend = codex.CodexBackend()
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    params = dict(calls)
    assert params["thread/start"]["model"] == "fixture-model"
    assert params["thread/start"]["config"]["model_reasoning_effort"] == "high"
    assert params["thread/start"]["developerInstructions"] == APP_INSTRUCTIONS
    assert params["turn/start"]["effort"] == "high"
    assert persona in params["turn/start"]["input"][0]["text"]
    assert ctx.prompt in params["turn/start"]["input"][0]["text"]
    assert not effects


@pytest.mark.parametrize("provider", ["openai_chatgpt", "codex", "ollama"])
def test_identity_rebind_retires_native_generation_without_changing_task_or_model(tmp_path, provider):
    store = Store(tmp_path / "synthetic-continuity.sqlite3")
    stamp = now()
    settings = {"reasoning_effort": "high", "tools": False, "openai_account_id": "synthetic-account"}
    store.db.execute("INSERT INTO projects VALUES (?,?,?,'','read_only','{}',?,?)",
        ("project", "Synthetic project", str(tmp_path), stamp, stamp))
    store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
        ("chat", "project", provider, "fixture-model", encode(settings), "old-native-thread", stamp))
    routing = ProviderRouting(SimpleNamespace(store=store))
    session = store.one("sessions", "chat")
    run, _ = store.create_run("chat", "original-task", "Keep the exact original objective")
    store.event(run["id"], "context.supplied", {"content": PERSONAS[0]})
    store.event(run["id"], "message.completed", {"content": "Original verified task result"})
    store.event(run["id"], "run.state", {"state": "completed"})
    originals = store.events(run["id"])
    data = DirectContextStore(store)
    opaque = {"type": "compaction", "encrypted_content": "synthetic-old-private-state"}
    try:
        if provider == "openai_chatgpt":
            data.save(session, {"items": [opaque], "boundary": "bootstrap"})
            assert data.load(session)["items"] == [opaque]
        with pytest.raises(KiraError, match="atomic transaction"):
            routing.reset_native(session)
        store.db.execute("BEGIN IMMEDIATE")
        generation = routing.reset_native(session)
        store.db.execute("COMMIT")
        current = store.one("sessions", "chat")
        assert current == {**session, "backend_ref": None}
        assert generation == 1
        old = store.db.execute("SELECT * FROM session_provider_generations WHERE session_id='chat' "
                               "AND generation=0").fetchone()
        assert old["retired_at"] and old["backend_ref"] == "old-native-thread"
        assert old["model"] == current["model"] == "fixture-model"
        assert json.loads(old["settings"]) == current["settings"] == settings
        assert store.events(run["id"]) == originals
        if provider == "openai_chatgpt":
            assert data.load(current) is None
            old_items = store.db.execute("SELECT items FROM direct_context_states WHERE generation=0").fetchone()[0]
            assert json.loads(old_items) == [opaque]
            page = data.page("chat", None, limit_bytes=64_000)
            assert "Keep the exact original objective" in encode(page)
            assert "Original verified task result" in encode(page)
            assert PERSONAS[0] not in encode(page)
            assert opaque["encrypted_content"] not in encode(page)
            with pytest.raises(KiraError, match="generation changed"):
                data.save({**session, "provider_generation": 0},
                          {"items": [opaque], "boundary": "bootstrap"})
    finally:
        store.close()


async def test_manual_maintenance_freezes_current_capsule_without_learning_or_fake_run(tmp_path, monkeypatch):
    from kira.service.server import Service

    entered, release = asyncio.Event(), asyncio.Event()
    contexts, reads = [], []
    selected = [PERSONAS[0]]

    class Maintenance:
        supports_durable_context = True

        async def discover(self, **_kwargs):
            entered.set()
            await release.wait()
            return {"models": [{"id": "fixture-model", "context_window": 32_000}]}

        async def compact_context(self, ctx):
            contexts.append(ctx)
            assert PERSONAS[0] in ctx.supplied_context and PERSONAS[1] not in ctx.supplied_context
            assert ctx.model == "fixture-model" and ctx.settings["reasoning_effort"] == "high"
            assert ctx.assistant_instructions == ""
            with pytest.raises(KiraError, match="cannot execute tools"):
                await ctx.execute_tool("write_file", {})
            return {"compacted": True, "method": "synthetic"}

        async def close(self):
            pass

    with tempfile.TemporaryDirectory(prefix="kira-soul-context-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: Maintenance())
        await service.start()
        try:
            project = (await service.dispatch("project.create", {
                "name": "Synthetic identity maintenance", "root": str(tmp_path)}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "openai_chatgpt", "model": "fixture-model", "settings": {
                    "openai_account_id": "synthetic-account", "reasoning_effort": "high"}}))["session"]

            async def prepare(settings, **_kwargs):
                return dict(settings)

            def soul_context(value):
                reads.append(value["id"])
                return "\n" + selected[0]

            monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
            monkeypatch.setattr(service.souls, "context", soul_context)
            params = {"project_id": project["id"], "session_id": session["id"],
                      "request_id": "synthetic-maintenance"}
            operation = asyncio.create_task(service.dispatch("session.context.compact", params))
            await asyncio.wait_for(entered.wait(), 3)
            selected[0] = PERSONAS[1]
            release.set()
            await asyncio.wait_for(operation, 3)
            tasks = list(service.contexts.tasks.values())
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
            assert reads == [session["id"]] and len(contexts) == 1
            receipt = await service.dispatch("session.context.compact", params)
            assert receipt["operation"]["status"] == "completed"
            assert not service.store.all("SELECT * FROM runs")
            assert not service.store.all("SELECT * FROM soul_run_snapshots")
            assert not service.store.all("SELECT * FROM soul_proposals")
            assert not service.store.all("SELECT * FROM direct_context_reservations")
        finally:
            release.set()
            await service.close()
