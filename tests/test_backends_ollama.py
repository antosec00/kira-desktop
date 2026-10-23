"""Deterministic native Ollama wire tests; these are not live model evidence."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from kira.backends.ollama import NO_TOOLS_GUIDANCE, TOOL_CALL_GUIDANCE, OllamaBackend, StreamError, ndjson
from kira.domain.contracts import RunContext
from kira.domain.knowledge import IDENTITY_INSTRUCTIONS, knowledge_tool_specs
from kira.domain.project_tools import project_tool_specs


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, records: list[dict[str, Any]], width: int = 3):
        self.data = b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode() for r in records)
        self.width = width
        self.closed = False

    async def __aiter__(self):
        for i in range(0, len(self.data), self.width):
            yield self.data[i:i + self.width]

    async def aclose(self):
        self.closed = True


def context(tmp_path, *, tools=True, permission="trusted_host"):
    events = []
    executed = []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(name, args):
        executed.append((name, args))
        return {"ok": True, "value": "fixture-result"}

    async def approve(request):
        return {"decision": "decline"}

    ctx = RunContext("run-fixture", {"root": str(tmp_path), "permission": permission},
                     {"model": "fixture-model", "settings": {"tools": tools}},
                     "Perform fixture task", "fixture context", emit, execute, approve,
                     asyncio.Event())
    return ctx, events, executed


async def install_transport(backend, handler):
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(base_url=backend.endpoint,
                                     transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("data", [b'{"content":"caf\xc3\xa8"}\n', b'{"content":"caf\xc3\xa8"}'])
async def test_ndjson_split_utf8_and_final_record(data):
    async def chunks():
        for byte in data:
            yield bytes([byte])
    assert [r async for r in ndjson(chunks())] == [{"content": "cafè"}]


@pytest.mark.parametrize("data", [b'[]\n', b'{broken}\n', b'{"x": "\xff"}\n', b'{"x":'])
async def test_ndjson_rejects_malformed_records(data):
    async def chunks():
        yield data
    with pytest.raises(StreamError):
        _ = [r async for r in ndjson(chunks())]


async def test_native_tool_loop_requires_complete_response_and_preserves_fields(tmp_path):
    ctx, events, executed = context(tmp_path)
    bodies = []
    pending = []
    counts = {"main": 0}

    async def handler(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        bodies.append(body)
        if body.get("tools", [{}])[0].get("function", {}).get("name") == "kira_probe":
            token = body["messages"][0]["content"].split("value ")[1].split(".")[0]
            return httpx.Response(200, stream=ChunkStream([
                {"message": {"tool_calls": [{"function": {"name": "kira_probe",
                  "arguments": {"value": token}}}]}, "done": True}]))
        counts["main"] += 1
        step = counts["main"]
        if step == 1:
            # Tool calls arrive in distinct records; neither executes until done=true.
            stream = ChunkStream([
                {"message": {"thinking": "private fixture reasoning", "content": "Leggo café "}},
                {"message": {"tool_calls": [{"function": {"name": "read_file",
                     "arguments": {"path": "input.txt"}}}]}},
                {"message": {"tool_calls": [{"function": {"name": "write_file", "arguments": {
                     "path": "result.txt", "content": "ok", "expected_sha256": None}}}]}},
                {"done": True, "prompt_eval_count": 12, "eval_count": 5}])
            pending.append(stream)
            return httpx.Response(200, stream=stream)
        if step == 2:
            assert pending[0].closed
            assert [n for n, _ in executed] == ["read_file", "write_file"]
            assert body["messages"][-3]["thinking"] == "private fixture reasoning"
            assert body["messages"][-2]["tool_name"] == "read_file"
            return httpx.Response(200, stream=ChunkStream([
                {"message": {"tool_calls": [{"function": {"name": "run_command", "arguments": {
                     "argv": ["python3", "-c", "assert True"]}}}]}}, {"done": True}]))
        assert [n for n, _ in executed] == ["read_file", "write_file", "run_command"]
        return httpx.Response(200, stream=ChunkStream([
            {"message": {"content": "Verificato."}, "done": True}]))

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed"
        assert result["outcome"]["tools_verified"]
        assert "private fixture reasoning" not in json.dumps(events)
        assert any(p.get("input_tokens") == 12 for k, p in events if k == "usage")
        assert all(b["model"] == ctx.model for b in bodies)
        completed_ids = {p["item_id"] for k, p in events if k == "message.completed"}
        assert all(p["item_id"] in completed_ids for k, p in events if k == "message.delta")
    finally:
        await backend.close()


async def test_tool_fragments_are_assembled_before_execution(tmp_path):
    ctx, events, executed = context(tmp_path)
    calls = 0

    async def handler(request):
        nonlocal calls
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"]})
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=ChunkStream([
                {"message": {"tool_calls": [{"function": {"index": 0, "name": "read_file",
                    "arguments": '{"path":"in'}}]}},
                {"message": {"tool_calls": [{"function": {"index": 0,
                    "arguments": 'put.txt"}'}}]}}, {"done": True}]))
        return httpx.Response(200, stream=ChunkStream([{"done": True}]))

    backend = OllamaBackend()
    backend._probed[ctx.model] = True
    await install_transport(backend, handler)
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert executed == [("read_file", {"path": "input.txt"})]
    finally:
        await backend.close()


async def test_midstream_disconnect_cannot_execute_tool_or_succeed(tmp_path):
    ctx, _, executed = context(tmp_path)

    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"]})
        return httpx.Response(200, stream=ChunkStream([{"message": {"tool_calls": [{"function": {
            "name": "write_file", "arguments": {"path": "x", "content": "x",
                                                  "expected_sha256": None}}}]}}]))
    backend = OllamaBackend()
    backend._probed[ctx.model] = True
    await install_transport(backend, handler)
    try:
        assert (await backend.run(ctx))["state"] == "interrupted"
        assert executed == []
    finally:
        await backend.close()


async def test_unverified_model_chat_fallback_and_metrics(tmp_path):
    ctx, events, executed = context(tmp_path)
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        assert "tools" not in json.loads(request.content)
        return httpx.Response(200, stream=ChunkStream([{"message": {"content": "Hello"},
                "done": True, "eval_count": 7}]))
    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert not executed
        usage = next(p for k, p in events if k == "usage")
        assert usage["output_tokens"] == 7
        assert "input_tokens" not in usage
        assert any("Chat-only" in p.get("message", "") for _, p in events)
    finally:
        await backend.close()


async def test_invalid_tool_request_returns_structured_error_without_execution(tmp_path):
    ctx, _, executed = context(tmp_path)
    requests = []
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"]})
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, stream=ChunkStream([{"message": {"tool_calls": [
                {"function": {"name": "write_file", "arguments": {"path": "x", "content": "x"}}}]},
                "done": True}]))
        assert json.loads(requests[-1]["messages"][-1]["content"])["error"]["code"] == "INVALID_TOOL"
        return httpx.Response(200, stream=ChunkStream([{"done": True}]))
    backend = OllamaBackend()
    backend._probed[ctx.model] = True
    await install_transport(backend, handler)
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert not executed
    finally:
        await backend.close()


async def test_cancel_closes_active_http_stream(tmp_path):
    ctx, _, _ = context(tmp_path, tools=False)
    started = asyncio.Event()
    closed = asyncio.Event()
    class HangingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            yield b'{"message":{"content":"started"}}\n'
            await asyncio.Event().wait()
        async def aclose(self):
            closed.set()
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={})
        return httpx.Response(200, stream=HangingStream())
    backend = OllamaBackend()
    await install_transport(backend, handler)
    task = asyncio.create_task(backend.run(ctx))
    await asyncio.wait_for(started.wait(), 2)
    ctx.cancel.set()
    try:
        result = await asyncio.wait_for(task, 2)
        assert result["state"] == "cancelled"
        assert not result["outcome"]["remote_generation_termination_confirmed"]
        assert closed.is_set()
    finally:
        await backend.close()


async def test_catalog_preserves_loaded_and_model_context_as_distinct(tmp_path):
    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture", "size": 42}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "fixture", "size_vram": 12,
                                                        "context_length": 4096}]})
        return httpx.Response(200, json={"capabilities": ["tools"],
                              "model_info": {"fixture.context_length": 32768}})
    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        data = await backend.discover()
        model = data["models"][0]
        assert data["available"]
        assert model["loaded"]["context_length"] == 4096
        assert model["model_info"]["fixture.context_length"] == 32768
        assert model["tools_metadata"] and not model["capabilities"]["tools"]
        assert data["device_memory_used_bytes"] is None
    finally:
        await backend.close()


async def test_two_runs_reopened_store_preserves_tool_result_messages(tmp_path):
    from kira.service.storage import Store, encode

    database = tmp_path / "history.sqlite3"
    store = Store(database)
    store.db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                     ("p", "Fixture", str(tmp_path), "", "trusted_host", "{}", "0", "0"))
    store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                     ("s", "p", "ollama", "fixture-model", "{}", None, "0"))
    store.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("run-fixture", "s", "request-fixture", "First fixture task", "running",
                      "0", "0", None, None, 0))
    ctx, _, _ = context(tmp_path)
    ctx.prompt = "First fixture task"
    async def emit(kind, payload):
        store.event(ctx.run_id, kind, payload)
    ctx.emit = emit
    chat_calls = 0
    async def handler(request):
        nonlocal chat_calls
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"]})
        chat_calls += 1
        body = json.loads(request.content)
        if chat_calls == 1:
            return httpx.Response(200, stream=ChunkStream([{"message": {"tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "fixture.txt"}}}]},
                "done": True}]))
        if chat_calls == 3:
            messages = body["messages"]
            call_index = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
            assert messages[call_index + 1]["role"] == "tool"
            assert messages[call_index + 1]["tool_name"] == "read_file"
            assert json.loads(messages[call_index + 1]["content"])["value"] == "fixture-result"
            assert sum(m.get("content") == "First fixture task" for m in messages) == 1
        return httpx.Response(200, stream=ChunkStream([{"message": {"content": "fixture answer"},
                                                        "done": True}]))
    backend = OllamaBackend()
    backend._probed[ctx.model] = True
    await install_transport(backend, handler)
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        store.db.execute("UPDATE runs SET state='completed' WHERE id=?", (ctx.run_id,))
        store.close()
        store = Store(database)
        second, _, _ = context(tmp_path)
        second.prompt = "Continue fixture task"
        second.history = store.history("s")
        assert "fixture-result" in encode(second.history)
        assert (await backend.run(second))["state"] == "completed"
        assert chat_calls == 3
    finally:
        await backend.close()
        store.close()


async def test_interrupted_historical_tool_request_is_unknown_and_not_reexecuted(tmp_path):
    ctx, events, executed = context(tmp_path, tools=False)
    ctx.history = [
        {"role": "user", "content": "Previous interrupted task"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {
            "name": "write_file", "arguments": {"path": "fixture.txt", "content": "unknown",
                                                  "expected_sha256": None}}}]},
    ]
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={})
        messages = json.loads(request.content)["messages"]
        missing = next(m for m in messages if m.get("role") == "tool")
        assert missing["tool_name"] == "write_file"
        assert json.loads(missing["content"])["error"]["code"] == "UNKNOWN_OUTCOME"
        return httpx.Response(200, stream=ChunkStream([{"done": True,
                "message": {"content": "The previous outcome needs verification."}}]))
    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert executed == []
        assert any(p.get("unknown_tool_outcomes") == 1 for _, p in events)
    finally:
        await backend.close()


class MetadataStream(httpx.AsyncByteStream):
    """Small bounded wire fixture; optionally pause until cancelled."""

    def __init__(self, chunks, *, pause=False):
        self.chunks = chunks
        self.pause = pause
        self.started = asyncio.Event()
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.pause:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def test_metadata_stream_requests_identity_and_accepts_bounded_json():
    stream = MetadataStream([b'{"model_', b'info":{"context":4096}}'])

    async def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, stream=stream)

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        assert await backend._json("POST", "/api/show", {"model": "fixture"}) == {
            "model_info": {"context": 4096}}
        assert stream.closed
    finally:
        await backend.close()


@pytest.mark.parametrize("headers, chunks, error", [
    ({"Content-Encoding": "gzip"}, [b"unused fixture"], "identity content encoding"),
    ({"Content-Length": "65"}, [b"unused fixture"], "byte limit"),
    ({"Content-Length": "invalid"}, [b"unused fixture"], "Content-Length"),
    ({}, [b'{"value":"', b"x" * 32, b"x" * 32, b'"}'], "byte limit"),
])
async def test_metadata_limits_close_response_before_parsing(monkeypatch, headers, chunks, error):
    monkeypatch.setattr("kira.backends.ollama.MAX_METADATA_BYTES", 64)
    stream = MetadataStream(chunks)

    async def handler(request):
        return httpx.Response(200, headers=headers, stream=stream)

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        with pytest.raises(StreamError, match=error):
            await backend._json("GET", "/api/tags")
        assert stream.closed
        if headers:
            assert stream.reads == 0
    finally:
        await backend.close()


@pytest.mark.parametrize("body", [b"[]", b'{"value":NaN}', b'{"value":1e999}',
                                 b'{"value":"\xff"}', b"{unfinished"])
async def test_metadata_requires_finite_object_json(body):
    backend = OllamaBackend()
    await install_transport(backend, lambda request: httpx.Response(200, stream=MetadataStream([body])))
    try:
        with pytest.raises(StreamError):
            await backend._json("GET", "/api/tags")
    finally:
        await backend.close()


async def test_metadata_total_deadline_closes_incomplete_response(monkeypatch):
    monkeypatch.setattr("kira.backends.ollama.METADATA_TIMEOUT", 0.02)
    stream = MetadataStream([b"{"], pause=True)
    backend = OllamaBackend()
    await install_transport(backend, lambda request: httpx.Response(200, stream=stream))
    try:
        with pytest.raises(StreamError, match="total request deadline"):
            await asyncio.wait_for(backend._json("GET", "/api/tags"), 2)
        assert stream.closed
    finally:
        await backend.close()


@pytest.mark.parametrize("models", [None, {}, ["fixture"], [{"name": 12}],
    [{"name": "fixture", "size": True}], [{"name": "fixture", "details": []}],
    [{"name": "fixture", "digest": {}}], [{"name": "same"}, {"name": "same"}]])
async def test_catalog_shapes_fail_without_model_work(models):
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"models": models})

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await backend.discover()
        assert not result["available"] and result["models"] == []
        assert result["error"]
        assert requests == ["/api/tags"]
        assert not backend._details
    finally:
        await backend.close()


@pytest.mark.parametrize("detail", [{"capabilities": "tools"}, {"capabilities": [{}]},
                                  {"model_info": []}, {"details": "fixture"}])
async def test_run_show_uses_validated_metadata_path(tmp_path, detail):
    ctx, events, executed = context(tmp_path)
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=detail)

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted"
        assert "Ollama" in result["error"]
        assert requests == ["/api/show"]
        assert not backend._details and not executed
    finally:
        await backend.close()


async def test_invalid_optional_metadata_is_visible_and_not_cached():
    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [None]})
        return httpx.Response(200, json={"capabilities": "tools"})

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await backend.discover()
        assert result["available"] and result["running_models_error"]
        model = result["models"][0]
        assert model["metadata_error"]
        assert not model["tools_metadata"] and not model["tools_verified"]
        assert not backend._details
    finally:
        await backend.close()


async def test_catalog_count_bounds_network_work(monkeypatch):
    monkeypatch.setattr("kira.backends.ollama.MAX_MODELS", 2)
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": f"fixture-{i}"} for i in range(3)]})

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await backend.discover()
        assert not result["available"] and "catalog limit" in result["error"]
        assert requests == ["/api/tags"]
    finally:
        await backend.close()


async def test_discovery_total_deadline_closes_model_metadata(monkeypatch):
    monkeypatch.setattr("kira.backends.ollama.DISCOVERY_TIMEOUT", 0.02)
    stream = MetadataStream([b"{"], pause=True)
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        if request.url.path in {"/api/tags", "/api/ps"}:
            return httpx.Response(200, json={"models": [{"name": "fixture"}]})
        return httpx.Response(200, stream=stream)

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await asyncio.wait_for(backend.discover(), 2)
        assert not result["available"]
        assert "TimeoutError" in result["error"]
        assert requests == ["/api/tags", "/api/ps", "/api/show"]
        assert stream.closed and not backend._details
    finally:
        await backend.close()


async def test_discovery_serialized_response_limit_stops_more_detail_requests(monkeypatch):
    # Includes the advertised boundary-steering capability in each model.
    monkeypatch.setattr("kira.backends.ollama.MAX_DISCOVERY_BYTES", 700)
    details = []

    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": f"fixture-{i}"} for i in range(3)]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        details.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"model_info": {"fixture": "bounded"}})

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await backend.discover()
        assert not result["available"] and "serialized response limit" in result["error"]
        assert details == ["fixture-0", "fixture-1"]
    finally:
        await backend.close()


async def test_repeated_catalogs_and_probes_keep_caches_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("kira.backends.ollama.MAX_MODELS", 2)
    monkeypatch.setattr("kira.backends.ollama.MAX_CACHE_BYTES", 210)
    generation = 0

    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": f"fixture-{generation}"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"],
                "model_info": {"label": "x" * 70}, "template": "not retained", "modelfile": "not retained"})
        return httpx.Response(200, stream=ChunkStream([{"done": True}]))

    ctx, _, _ = context(tmp_path)
    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        for generation in range(6):
            result = await backend.discover()
            assert result["available"]
            await backend._probe_tools(f"fixture-{generation}", ctx)
            assert len(backend._details) <= 2 and len(backend._probed) <= 2
            size = sum(len(json.dumps(detail, separators=(",", ":")).encode()) + len(model.encode())
                       for model, detail in backend._details.items())
            assert size <= 210
            assert "not retained" not in json.dumps(backend._details)
        assert "fixture-0" not in backend._details
    finally:
        await backend.close()


async def test_run_cancellation_closes_initial_metadata_request(tmp_path):
    ctx, _, executed = context(tmp_path)
    stream = MetadataStream([b"{"], pause=True)
    backend = OllamaBackend()
    await install_transport(backend, lambda request: httpx.Response(200, stream=stream))
    task = asyncio.create_task(backend.run(ctx))
    try:
        await asyncio.wait_for(stream.started.wait(), 2)
        ctx.cancel.set()
        result = await asyncio.wait_for(task, 2)
        assert result["state"] == "cancelled"
        assert stream.closed and not backend._details and not executed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.close()


@pytest.mark.parametrize("verified", [False, True])
async def test_common_kira_identity_and_json_gateway_use_verified_ollama_transport(tmp_path, verified):
    ctx, events, executed = context(tmp_path)
    ctx.assistant_instructions = IDENTITY_INSTRUCTIONS
    ctx.client_tools = [{"type": "namespace", "name": "kira", "description": "Fixture Kira",
                         "tools": knowledge_tool_specs() + project_tool_specs()}]
    calls, bodies = [], []

    async def callback(call_id, name, arguments):
        calls.append((call_id, name, arguments))
        return {"ok": True, "content": "Observed fixture source"}
    ctx.client_tool_call = callback

    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        body = json.loads(request.content)
        bodies.append(body)
        guidance = TOOL_CALL_GUIDANCE if verified else NO_TOOLS_GUIDANCE
        assert body["messages"][0] == {"role": "system", "content": IDENTITY_INSTRUCTIONS + guidance}
        assert body["messages"][1]["role"] == "user"
        assert "fixture context" in body["messages"][1]["content"]
        if not verified:
            assert "tools" not in body
        elif len(bodies) == 1:
            names = {item["function"]["name"]: item["function"]["parameters"] for item in body["tools"]}
            assert names["kira__project_tools"]["properties"]["payload"]["type"] == "object"
            return httpx.Response(200, stream=ChunkStream([{"message": {"tool_calls": [
                {"function": {"name": "kira__help_read", "arguments": {"ref": "app:identity", "offset": 0}}},
                {"function": {"name": "kira__project_tools", "arguments": {
                    "action": "describe", "payload": {"action": "register"}}}}
            ]}, "done": True}]))
        return httpx.Response(200, stream=ChunkStream([
            {"message": {"content": "Fixture finished"}, "done": True}]))
    backend = OllamaBackend()
    backend._probed[ctx.model] = verified
    await install_transport(backend, handler)
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed"
        assert result["outcome"]["tools_verified"] is verified
        assert not executed
        if verified:
            assert [name for _, name, _ in calls] == ["kira.help_read", "kira.project_tools"]
            assert len({identifier for identifier, _, _ in calls}) == 2
            assert calls[-1][2]["payload"] == {"action": "register"}
            tool_results = [item for item in bodies[-1]["messages"] if item["role"] == "tool"]
            assert len(tool_results) == 2 and all("Observed fixture source" in item["content"] for item in tool_results)
        else:
            assert not calls
            assert any(payload.get("tools_verified") is False for _, payload in events)
    finally:
        await backend.close()


@pytest.mark.parametrize("name", [[], {}, None, 7])
async def test_malformed_tool_name_returns_invalid_tool_without_alias_lookup_crash(tmp_path, name):
    ctx, _, executed = context(tmp_path)
    bodies = []

    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"]})
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, stream=ChunkStream([{"message": {"tool_calls": [
                {"function": {"name": name, "arguments": {}}}]}, "done": True}]))
        assert json.loads(body["messages"][-1]["content"])["error"]["code"] == "INVALID_TOOL"
        return httpx.Response(200, stream=ChunkStream([{"message": {"content": "Corrected fixture"}, "done": True}]))
    backend = OllamaBackend()
    backend._probed[ctx.model] = True
    await install_transport(backend, handler)
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert not executed
    finally:
        await backend.close()


def test_missing_user_query_error_detection_and_stream_timeout_constant():
    from kira.backends.ollama import OLLAMA_CHAT_READ_TIMEOUT, _is_missing_user_query_error
    assert OLLAMA_CHAT_READ_TIMEOUT == 600.0
    assert _is_missing_user_query_error(b'{"error": "No user query found in messages"}')
    assert _is_missing_user_query_error(b'{"error": {"message": "no  USER query\\nfound in messages"}}')
    assert not _is_missing_user_query_error(b'{"error": "model not found"}')
    assert not _is_missing_user_query_error(b'not json')
    assert not _is_missing_user_query_error(b'[]')
