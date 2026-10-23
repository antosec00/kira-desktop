"""Shared Ollama/DeepSeek Account loop regressions, using only synthetic transports."""

import base64
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from kira.backends import ollama
from kira.backends.deepseek_account import DeepSeekAccountBackend
from kira.backends.ollama import OllamaBackend
from kira.domain.web import browser_tool_specs
from test_backends_ollama import ChunkStream, context, install_transport

OTHER_ENDPOINT = "http://127.0.0.1:11435"


class OllamaScript:
    """Synthetic Ollama server: counts tool probes and answers ordinary turns in text."""

    def __init__(self, *, probe="pass", modified_at="2026-01-01T00:00:00Z"):
        self.probe = probe
        self.modified_at = modified_at
        self.probes: list[dict] = []
        self.turns: list[dict] = []

    def __call__(self, request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture-model"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"],
                                             "modified_at": self.modified_at})
        body = json.loads(request.content)
        if body.get("tools", [{}])[0].get("function", {}).get("name") == "kira_probe":
            self.probes.append(body)
            if self.probe == "transport":
                raise httpx.ConnectError("synthetic connection refusal", request=request)
            if self.probe == "text":
                return httpx.Response(200, stream=ChunkStream([
                    {"message": {"content": "I cannot call tools."}, "done": True}]))
            token = body["messages"][0]["content"].split("value ")[1].split(".")[0]
            return httpx.Response(200, stream=ChunkStream([{"message": {"tool_calls": [
                {"function": {"name": "kira_probe", "arguments": {"value": token}}}]}, "done": True}]))
        self.turns.append(body)
        return httpx.Response(200, stream=ChunkStream([
            {"message": {"content": "Synthetic answer"}, "done": True}]))


async def ollama_run(tmp_path, script, *, endpoint=None, settings=None):
    ctx, events, _ = context(tmp_path)
    ctx.session["backend"] = "ollama"
    ctx.session["settings"].update(settings or {})
    backend = OllamaBackend(endpoint) if endpoint else OllamaBackend()
    await install_transport(backend, script)
    try:
        return await backend.run(ctx), events
    finally:
        await backend.close()


def notices(events):
    return [payload.get("message", "") for kind, payload in events if kind == "backend.notice"]


async def test_passed_probe_lasts_for_the_service_per_endpoint_and_model(tmp_path):
    script = OllamaScript()
    first, events = await ollama_run(tmp_path, script)
    assert first["state"] == "completed" and first["outcome"]["tools_verified"]
    assert len(script.probes) == 1 and any("Verifying" in item for item in notices(events))
    # The service builds a new backend for every run.
    second, events = await ollama_run(tmp_path, script)
    assert second["state"] == "completed" and second["outcome"]["tools_verified"]
    assert len(script.probes) == 1 and not any("Verifying" in item for item in notices(events))
    assert "tools" in script.turns[-1]
    elsewhere, _ = await ollama_run(tmp_path, script, endpoint=OTHER_ENDPOINT)
    assert elsewhere["outcome"]["tools_verified"] and len(script.probes) == 2


async def test_changed_model_revision_or_thinking_mode_is_probed_again(tmp_path):
    script = OllamaScript()
    await ollama_run(tmp_path, script)
    script.modified_at = "2026-02-01T00:00:00Z"
    await ollama_run(tmp_path, script)
    assert len(script.probes) == 2
    await ollama_run(tmp_path, script, settings={"think": False})
    await ollama_run(tmp_path, script, settings={"think": False})
    assert len(script.probes) == 3


async def test_probe_uses_the_chat_context_size_and_thinking_mode(tmp_path):
    script = OllamaScript()
    result, _ = await ollama_run(tmp_path, script, settings={"num_ctx": 32768, "think": False})
    assert result["state"] == "completed"
    probe = script.probes[0]
    assert probe["options"] == {"temperature": 0, "num_ctx": 32768} and probe["think"] is False
    assert script.turns[0]["options"]["num_ctx"] == 32768


async def test_failed_probe_is_remembered_and_keeps_the_chat_conversational(tmp_path):
    script = OllamaScript(probe="text")
    first, events = await ollama_run(tmp_path, script)
    assert first["state"] == "completed" and first["outcome"]["tools_verified"] is False
    assert any("Chat-only" in item for item in notices(events))
    second, _ = await ollama_run(tmp_path, script)
    assert second["outcome"]["tools_verified"] is False and len(script.probes) == 1
    assert "tools" not in script.turns[-1]


async def test_remembered_probe_outcomes_stay_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama, "MAX_TOOL_PROBES", 2)
    script = OllamaScript()
    for revision in range(4):
        script.modified_at = f"2026-01-0{revision + 1}T00:00:00Z"
        await ollama_run(tmp_path, script)
    assert len(script.probes) == 4 and len(ollama._tool_probes()) == 2


async def test_probe_transport_failure_is_reported_and_not_remembered(tmp_path):
    script = OllamaScript(probe="transport")
    first, events = await ollama_run(tmp_path, script)
    assert first["state"] == "interrupted" and "ConnectError" in first["error"], first
    assert not script.turns and not any("Chat-only" in item for item in notices(events))
    script.probe = "pass"
    second, _ = await ollama_run(tmp_path, script)
    assert second["state"] == "completed" and second["outcome"]["tools_verified"]
    assert len(script.probes) == 2


@pytest.mark.parametrize(("path", "status", "error"), [
    ("/api/chat", 500, "model requires more system memory (12.0 GiB) than is available (8.0 GiB)"),
    ("/api/show", 404, "model 'fixture-model' not found, try pulling it first"),
    ("/api/chat", 400, {"message": "registry.ollama.ai/library/fixture does not support tools"}),
])
async def test_provider_error_text_reaches_the_run_error(tmp_path, path, status, error):
    def handler(request):
        if request.url.path == path:
            return httpx.Response(status, json={"error": error})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        raise AssertionError(request.url.path)

    result, _ = await ollama_run(tmp_path, handler, settings={"tools": False})
    text = error if isinstance(error, str) else error["message"]
    assert result == {"state": "failed", "error": f"ollama HTTP {status} — {text}"}


async def test_provider_error_text_is_bounded_and_single_line(tmp_path):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        return httpx.Response(500, json={"error": "first line\nsecond\tline " + "x" * 5000})

    result, _ = await ollama_run(tmp_path, handler, settings={"tools": False})
    assert result["error"].startswith("ollama HTTP 500 — first line second line x")
    assert "\n" not in result["error"] and len(result["error"]) < 600


# DeepSeek Account reaches the same loop through its own Messages wire format.

CATALOG = {"data": [{"id": "deepseek-fixture", "name": "DeepSeek fixture", "context_window": 1_000_000,
                     "max_output_tokens": 65_536, "input_modalities": ["text"],
                     "api_capabilities": {"messages": {"tool_calling": True}}}]}


def sse(*events):
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


class DeepSeekScript:
    def __init__(self, tool_rounds=0, *, rounds=None, catalog=CATALOG):
        self.probes = 0
        self.turns = 0
        self.rounds = rounds if rounds is not None else [[("read_file", {"path": "fixture.txt"})]] * tool_rounds
        self.catalog = catalog
        self.bodies: list[dict] = []

    def __call__(self, request):
        if request.url.path == "/models":
            return httpx.Response(200, json=self.catalog)
        assert request.url.path == "/anthropic/v1/messages"
        body = json.loads(request.content)
        blocks: list[tuple[str, dict]]
        if any(tool["name"] == "kira_probe" for tool in body.get("tools", [])):
            self.probes += 1
            token = re.search(r"value ([0-9a-f]{32})", request.content.decode()).group(1)
            blocks = [("content_block_start", {"index": 0, "content_block": {
                          "type": "tool_use", "id": "toolu_probe", "name": "kira_probe", "input": {}}}),
                      ("content_block_delta", {"index": 0, "delta": {
                          "type": "input_json_delta", "partial_json": json.dumps({"value": token})}})]
            stop = "tool_use"
        elif self.turns < len(self.rounds):
            calls = self.rounds[self.turns]
            self.bodies.append(body)
            self.turns += 1
            blocks = [("content_block_start", {"index": index, "content_block": {
                          "type": "tool_use", "id": f"toolu_{self.turns}_{index}", "name": name,
                          "input": arguments}}) for index, (name, arguments) in enumerate(calls)]
            stop = "tool_use"
        else:
            self.bodies.append(body)
            self.turns += 1
            blocks = [("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                      ("content_block_delta", {"index": 0, "delta": {"type": "text_delta",
                                                                     "text": "Synthetic answer"}})]
            stop = "end_turn"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(
            ("message_start", {"message": {"usage": {"input_tokens": 40}}}), *blocks,
            ("message_delta", {"delta": {"stop_reason": stop}, "usage": {"output_tokens": 4}}),
            ("message_stop", {})))


async def deepseek_backend(script):
    async def expire(_token):
        raise AssertionError("Synthetic credentials are never rejected")

    account = SimpleNamespace(proxy_enabled=False, proxy_url="http://127.0.0.1:4000", proxy_api_key="",
                              token="synthetic-account-token", expire=expire,
                              status=lambda: {"connected": True})
    backend = DeepSeekAccountBackend(account)
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(base_url=backend.endpoint, transport=httpx.MockTransport(script))
    return backend


async def deepseek_run(tmp_path, script, *, durable=False, guidance=None, prepare=None):
    ctx, events, executed = context(tmp_path)
    ctx.guidance = guidance
    ctx.session.update({"backend": "deepseek_account", "model": "deepseek-fixture"})
    if prepare is not None:
        prepare(ctx)
    saves = []
    if durable:
        async def save(payload):
            saves.append(payload)
            return payload

        async def page(_params):
            return {"groups": [], "has_more": False, "next_cursor": None}

        ctx.context_save, ctx.context_page = save, page
        ctx.context_catalog = {"context_window": 1_000_000, "max_output_tokens": 65_536}

    backend = await deepseek_backend(script)
    try:
        return await backend.run(ctx), events, executed, saves
    finally:
        await backend.close()


async def test_deepseek_account_probe_is_not_repeated_for_every_turn(tmp_path):
    script = DeepSeekScript()
    for _ in range(3):
        result, events, _, _ = await deepseek_run(tmp_path, script)
        assert result["state"] == "completed" and result["outcome"]["tools_verified"], result
    assert script.probes == 1 and script.turns == 3


@pytest.mark.parametrize("tool_rounds", [0, 1])
async def test_durable_context_is_checked_once_per_request(tmp_path, tool_rounds):
    script = DeepSeekScript(tool_rounds)
    result, events, executed, saves = await deepseek_run(tmp_path, script, durable=True)
    assert result["state"] == "completed", result
    assert result["outcome"]["history_mode"] == "durable_compacted_context"
    assert len(executed) == tool_rounds and script.turns == tool_rounds + 1
    statuses = [payload for kind, payload in events if kind == "context.status"]
    assert len(statuses) == script.turns
    assert saves[-1]["boundary"] == "response_complete"


async def test_guidance_at_the_first_boundary_is_checked_and_sent(tmp_path):
    batches = [{"messages": [{"request_id": "guidance-1", "content": "Synthetic correction"}],
                "delivery_id": "delivery-1"}]

    async def guidance(action, _values):
        if action == "take":
            return batches.pop(0) if batches else {"messages": [], "delivery_id": ""}
        if action == "accepted":
            return {"accepted": True, "delivery_id": "delivery-1"}
        return {"pending": False} if action == "pending" else {"sealed": True}

    script = DeepSeekScript()
    result, events, _, _ = await deepseek_run(tmp_path, script, durable=True, guidance=guidance)
    assert result["state"] == "completed", result
    assert script.turns == 1 and "Synthetic correction" in json.dumps(script.bodies[0])
    # The turn changed after the first check, so it is checked again before sending.
    assert len([kind for kind, _ in events if kind == "context.status"]) == 2


VISION_CATALOG = {"data": [{**CATALOG["data"][0], "input_modalities": ["text", "image"]}]}


def png(label):
    return "data:image/png;base64," + base64.b64encode(b"synthetic-" + label.encode()).decode()


def image_blocks(body):
    """Image blocks per user message of one DeepSeek request, tool results included."""
    found = []
    for message in body["messages"]:
        for block in message["content"] if isinstance(message["content"], list) else []:
            inner = block.get("content") if block.get("type") == "tool_result" else [block]
            found += [item["source"]["data"] for item in inner if isinstance(item, dict)
                      and item.get("type") == "image"] if isinstance(inner, list) else []
    return found


async def test_durable_turn_keeps_only_the_two_latest_screenshots(tmp_path):
    shot = ("browser__screenshot", {})
    script = DeepSeekScript(rounds=[[shot], [shot], [shot, shot], [shot]], catalog=VISION_CATALOG)
    taken = []

    def prepare(ctx):
        ctx.client_tools = browser_tool_specs()

        async def call(_call_id, _name, _arguments):
            taken.append(f"shot-{len(taken) + 1}")
            return {"ok": True, "browser_image_id": taken[-1]}

        async def image(image_id):
            return {"type": "input_image", "image_url": png(image_id), "detail": "auto"}

        ctx.client_tool_call, ctx.tool_image = call, image

    result, _, _, _ = await deepseek_run(tmp_path, script, durable=True, prepare=prepare)
    assert result["state"] == "completed", result
    sent = [image_blocks(body) for body in script.bodies]
    assert [len(images) for images in sent] == [0, 1, 2, 2, 2]
    assert sent[-1] == [png(name).split(",", 1)[1] for name in ("shot-4", "shot-5")]


@pytest.mark.parametrize("catalog", [VISION_CATALOG, CATALOG])
async def test_history_attachments_reach_a_vision_model_without_durable_context(tmp_path, catalog):
    earlier = {"type": "input_image", "image_url": png("earlier"), "detail": "auto"}

    def prepare(ctx):
        ctx.history = [{"role": "user", "content": "Earlier request with a picture", "run_id": "run-earlier",
                        "run_state": "completed"},
                       {"role": "assistant", "content": "Earlier answer"}]
        ctx.image_inputs = {"run-earlier": [earlier]}

    script = DeepSeekScript(catalog=catalog)
    result, _, _, _ = await deepseek_run(tmp_path, script, prepare=prepare)
    assert result["state"] == "completed", result
    expected = [earlier["image_url"].split(",", 1)[1]] if catalog is VISION_CATALOG else []
    assert image_blocks(script.bodies[0]) == expected


async def test_ollama_history_wire_carries_no_run_identity(tmp_path):
    script = OllamaScript()
    ctx, _, _ = context(tmp_path)
    ctx.session["backend"] = "ollama"
    ctx.history = [{"role": "user", "content": "Earlier request", "run_id": "run-earlier", "run_state": "completed"},
                   {"role": "assistant", "content": "Earlier answer"}]
    backend = OllamaBackend()
    await install_transport(backend, script)
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()
    assert not any("run_id" in message for message in script.turns[0]["messages"])


async def ollama_discovery(script, endpoint=None):
    backend = OllamaBackend(endpoint) if endpoint else OllamaBackend()
    await install_transport(backend, script)
    try:
        result = await backend.discover()
    finally:
        await backend.close()
    assert result["available"], result
    return next(model for model in result["models"] if model["id"] == "fixture-model")


def badge(model):
    return model["tools_verified"], model["tool_probe"], model["capabilities"]["tools"]


async def test_ollama_discovery_reports_probes_from_runs_without_probing(tmp_path):
    script = OllamaScript()
    assert badge(await ollama_discovery(script)) == (False, "not_run", False)
    await ollama_run(tmp_path, script)
    assert badge(await ollama_discovery(script)) == (True, "passed", True)
    assert len(script.probes) == 1 and len(script.turns) == 1
    assert badge(await ollama_discovery(script, OTHER_ENDPOINT)) == (False, "not_run", False)
    script.modified_at = "2026-03-01T00:00:00Z"
    assert badge(await ollama_discovery(script)) == (False, "not_run", False)
    assert len(script.probes) == 1


async def test_ollama_discovery_reports_a_failed_probe_and_any_passing_thinking_mode(tmp_path):
    script = OllamaScript(probe="text")
    await ollama_run(tmp_path, script)
    assert badge(await ollama_discovery(script)) == (False, "failed", False)
    script.probe = "pass"
    await ollama_run(tmp_path, script, settings={"think": False})
    assert badge(await ollama_discovery(script)) == (True, "passed", True)
    assert len(script.probes) == 2


async def test_deepseek_account_discovery_reports_probes_from_runs_without_probing(tmp_path):
    script = DeepSeekScript()

    async def discovery():
        backend = await deepseek_backend(script)
        try:
            result = await backend.discover()
        finally:
            await backend.close()
        assert result["available"], result
        return next(model for model in result["models"] if model["id"] == "deepseek-fixture")

    assert badge(await discovery()) == (False, "not_run", False)
    await deepseek_run(tmp_path, script)
    assert badge(await discovery()) == (True, "passed", True)
    assert script.probes == 1 and script.turns == 1
