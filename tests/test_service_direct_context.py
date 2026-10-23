"""Actual service/SQLite/HTTP fixtures for durable context, tools and maintenance."""

import asyncio
import copy
import json
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode, new_id


class Auth:
    async def access_token(self, account):
        assert account == "synthetic-account"
        return "synthetic-token"

    async def status(self):
        return {"active_account_id": "synthetic-account"}


def message(text):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}]}


def response(items, tokens=100):
    event = {"type": "response.completed", "response": {"status": "completed", "output": items,
              "usage": {"input_tokens": tokens, "output_tokens": 10, "total_tokens": tokens + 10}}}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=("data: " + json.dumps(event) + "\n\n").encode())


class Transport:
    def __init__(self):
        self.requests = []
        self.compactions = 0
        self.tool_requested = False
        self.wrote = False

    def __call__(self, request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"slug": "fixture-model", "display_name": "Fixture",
                "visibility": "list", "context_window": 272000, "max_context_window": 872000}]})
        body = json.loads(request.content)
        self.requests.append((request.url.path, copy.deepcopy(body)))
        if not request.url.path.endswith("/responses"):
            return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing"}})
        if body.get("text", {}).get("format", {}).get("name") == "kira_context_memory":
            assert "context_management" not in body
            tail = body["input"][-1]["content"].split("\n", 1)[1]
            sources = json.loads(tail)["source_ids"]
            memory = [{"kind": "constraint", "text": "Keep the original synthetic constraint",
                       "source_ids": [sources[0]], "status": "claim"}] if sources else []
            return response([message(json.dumps({"summary": "Synthetic retained project context", "memory": memory}))])
        if not body.get("tools") and body.get("context_management", [{}])[0].get("compact_threshold") == 1000:
            self.compactions += 1
            assert "Produce a compact factual handoff" not in body["instructions"]
            assert "Create the historical handoff now" not in encode(body["input"])
            assert body["text"]["format"] == {"type": "text"}
            return response([{"type": "compaction", "id": "compact-" + str(self.compactions),
                              "encrypted_content": "private-opaque-fixture"},
                             message("discarded-native-fold-continuation-fixture")])
        assert "discarded-native-fold-continuation-fixture" not in encode(body)
        assert "Create the historical handoff now" not in encode(body)
        if self.tool_requested and not self.wrote:
            self.wrote = True
            return response([{"type": "function_call", "call_id": "write-exact-once", "namespace": "files",
                "name": "write_file", "arguments": json.dumps({"path": "result.txt", "content": "verified", "expected_sha256": None})}])
        return response([message("Synthetic completed response")])


@pytest_asyncio.fixture
async def context_service(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-context-service-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        transport = Transport()
        def factory(*_args):
            return OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(transport))
        service = Service(paths, backend_factory=factory)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic project", "root": str(tmp_path),
            "permission": "project_write"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
            "model": "fixture-model", "settings": {"reasoning_effort": "high", "openai_account_id": "synthetic-account"}}))["session"]
        yield service, transport, project, session, factory
        await service.close()


async def run(service, session, prompt, request_id):
    result = await service.dispatch("run.start", {"session_id": session["id"], "request_id": request_id, "prompt": prompt})
    task = service.tasks.get(result["run"]["id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 5)
    completed = service.store.one("runs", result["run"]["id"])
    assert completed["state"] == "completed", (completed.get("error"), completed.get("outcome"), service.store.events(completed["id"])["events"])
    return completed


async def test_full_old_history_current_pins_and_maximum_survive_service_restart(context_service):
    service, transport, project, session, factory = context_service
    for index in range(45):
        item, _ = service.store.create_run(session["id"], new_id(), f"Original synthetic constraint {index}")
        service.store.event(item["id"], "message.completed", {"content": f"Original result {index}"})
        service.store.event(item["id"], "run.state", {"state": "completed"})
    await service.dispatch("session.context.pin", {"project_id": project["id"], "session_id": session["id"],
        "request_id": "pin-one", "content": "Never remove synthetic acceptance criteria"})
    first = await run(service, session, "Next step", "first-durable")
    main = [body for path, body in transport.requests if path.endswith("/responses")][-1]
    assert "Original synthetic constraint 0" in encode(main) and "Original result 44" in encode(main)
    assert "Never remove synthetic acceptance criteria" in encode(main)
    assert main["context_management"][0]["compact_threshold"] > 272000
    state = service.contexts.data.load(service.store.one("sessions", session["id"]))
    assert state["completed_run_id"] == first["id"]
    paths = service.paths
    await service.close()
    restored = Service(paths, backend_factory=factory)
    await restored.start()
    try:
        await run(restored, session, "Continue after restart", "second-durable")
        main = [body for path, body in transport.requests if path.endswith("/responses")][-1]
        assert encode(main).count("Original synthetic constraint 0") == 1
        assert encode(main).count("Next step") == 1
        snapshot = await restored.dispatch("session.context", {"project_id": project["id"], "session_id": session["id"]})
        assert snapshot["policy"]["context_window"] == 872000
        assert snapshot["metrics"]["input_tokens"] == 100
        assert snapshot["metrics"]["measurement"] == "estimate"
        assert snapshot["metrics"]["estimated_tokens"] > 0
    finally:
        await restored.close()


async def test_native_manual_compaction_is_durable_idempotent_and_not_a_chat_run(context_service):
    service, transport, project, session, _ = context_service
    await run(service, session, "Remember original constraint", "seed")
    count = service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    params = {"project_id": project["id"], "session_id": session["id"], "request_id": "compact-one"}
    initial = await service.dispatch("session.context.compact", params)
    task = service.contexts.tasks.get(params["request_id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 5)
    result = await service.dispatch("session.context.compact", params)
    assert initial["operation"]["request_id"] == params["request_id"]
    assert result["operation"]["status"] == "completed", result["operation"]
    assert result["operation"]["compacted"] is True and result["operation"]["method"] == "native"
    assert result["operation"]["usage"]["requests"] == 2
    assert result["operation"]["usage"]["total_tokens"] == 220
    assert transport.compactions == 1 and service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == count
    assert not service.store.all("SELECT * FROM direct_context_reservations")
    assert result["context"]["memory"]["summary"]["entries"]
    assert "private-opaque-fixture" not in encode(result)
    assert "private-opaque-fixture" not in encode(service._snapshot(project["id"], bounded=False))
    await run(service, session, "Continue with compacted state", "follow-up")
    assert any(item.get("type") == "compaction" for path, body in transport.requests
               if path.endswith("/responses") for item in body["input"])


async def test_compaction_and_restart_do_not_execute_completed_tool_twice(context_service):
    service, transport, project, session, factory = context_service
    transport.tool_requested = True
    await run(service, session, "Write one synthetic file", "write-once")
    assert (Path(project["root"]) / "result.txt").read_text() == "verified"
    writes = "SELECT COUNT(*) FROM events WHERE type='tool.completed' AND json_extract(payload,'$.name')='write_file'"
    before = service.store.db.execute(writes).fetchone()[0]
    params = {"project_id": project["id"], "session_id": session["id"], "request_id": "compact-after-write"}
    await service.dispatch("session.context.compact", params)
    if task := service.contexts.tasks.get(params["request_id"]):
        await asyncio.wait_for(asyncio.shield(task), 30)
    compacted = await service.dispatch("session.context.compact", params)
    assert compacted["operation"]["status"] == "completed", compacted["operation"]
    assert compacted["operation"]["compacted"] is True and transport.compactions == 1
    fold = next(body for path, body in transport.requests if path.endswith("/responses") and not body.get("tools")
                and body.get("context_management", [{}])[0].get("compact_threshold") == 1000)
    assert any(item.get("name") == "write_file" for item in fold["input"])
    paths = service.paths
    await service.close()
    restored = Service(paths, backend_factory=factory)
    await restored.start()
    try:
        await run(restored, session, "Continue without repeating writes", "second")
        # The restarted turn resumes from the compacted durable state.
        main = [body for path, body in transport.requests if path.endswith("/responses")][-1]
        assert any(item.get("type") == "compaction" for item in main["input"])
        after = restored.store.db.execute(writes).fetchone()[0]
    finally:
        await restored.close()
    assert before == after == 1
    assert (Path(project["root"]) / "result.txt").read_text() == "verified"


async def test_history_tools_are_registered_and_own_chat_scoped(context_service):
    service, transport, project, session, _ = context_service
    await run(service, session, "Exact original source", "seed")
    body = [body for path, body in transport.requests if path.endswith("/responses")][-1]
    assert any(namespace["name"] == "kira_history" for namespace in body["tools"])
    found = await service.dispatch("session.history.search", {"project_id": project["id"], "session_id": session["id"], "query": "Exact original"})
    assert found["sources"]
    source = found["sources"][0]["source_id"]
    text = await service.dispatch("session.history.read", {"project_id": project["id"], "session_id": session["id"], "source_id": source})
    assert text["content"] == "Exact original source"
    other = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex", "model": "fixture-model"}))["session"]
    with pytest.raises(KiraError, match="does not belong to this chat") as error:
        await service.dispatch("session.history.read", {"project_id": project["id"], "session_id": other["id"], "source_id": source})
    assert error.value.code == "NOT_FOUND"


async def test_empty_manual_compaction_has_no_inference_or_fake_success(context_service):
    service, transport, project, session, _ = context_service
    params = {"project_id": project["id"], "session_id": session["id"], "request_id": "empty"}
    await service.dispatch("session.context.compact", params)
    if task := service.contexts.tasks.get("empty"):
        await asyncio.wait_for(asyncio.shield(task), 5)
    result = await service.dispatch("session.context.compact", params)
    assert result["operation"]["status"] == "completed" and result["operation"]["compacted"] is False
    assert not transport.requests


async def test_provider_safe_history_namespace_executes_search_and_read_in_current_chat(context_service, monkeypatch):
    service, transport, project, session, _ = context_service
    original = "Retain the archived verification marker: ORIGINAL-SEARCH-7728"
    prior, _ = service.store.create_run(session["id"], new_id(), original)
    service.store.event(prior["id"], "run.state", {"state": "completed"})
    requests = []

    def handler(request):
        if request.method == "GET" or not request.url.path.endswith("/responses"):
            return transport(request)
        body = json.loads(request.content)
        requests.append(body)
        namespaces = {item["name"] for item in body.get("tools", [])
                      if item.get("type") == "namespace"}
        assert "kira_history" in namespaces and "history" not in namespaces
        outputs = [json.loads(item["output"]) for item in body["input"]
                   if item.get("type") == "function_call_output"]
        if not outputs:
            return response([{"type": "function_call", "call_id": "history-search-fixture",
                "namespace": "kira_history", "name": "search", "arguments": json.dumps({"query": "archived verification"})}])
        if len(outputs) == 1:
            source = next(item for item in outputs[0]["sources"] if item["run_id"] == prior["id"])
            return response([{"type": "function_call", "call_id": "history-read-fixture",
                "namespace": "kira_history", "name": "read", "arguments": json.dumps({"source_id": source["source_id"]})}])
        assert outputs[-1]["ok"] is True and outputs[-1]["content"] == original
        return response([message("ORIGINAL-SEARCH-7728")])

    monkeypatch.setattr(service, "_backend", lambda _backend: OpenAIChatGPTBackend(
        Auth(), transport=httpx.MockTransport(handler)))
    completed = await run(service, session, "Retrieve the original archived marker", "namespaced-history-turn")
    assert len(requests) == 3
    tools = service.store.db.execute("SELECT payload FROM events WHERE run_id=? AND type='tool.completed' ORDER BY seq",
                                     (completed["id"],)).fetchall()
    assert [json.loads(row[0])["name"] for row in tools] == ["kira_history.search", "kira_history.read"]
