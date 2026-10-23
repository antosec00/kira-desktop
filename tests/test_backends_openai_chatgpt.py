"""Synthetic OAuth/Responses wire checks; no subscription inference is performed."""

import asyncio
import json

import httpx
import pytest

from kira.backends.openai_chatgpt import ENDPOINT, OpenAIChatGPTBackend
from kira.backends.openai_responses import ResponsesError, sse
from kira.domain.assistant import assistant_tool_specs
from kira.domain.contracts import KiraError, RunContext
from kira.domain.knowledge import IDENTITY_INSTRUCTIONS, knowledge_tool_specs
from kira.domain.project_tools import project_tool_specs
from kira.domain.teams import coordinator_output_schema, mailbox_tool_specs


class FakeAuth:
    def __init__(self):
        self.active = "account-a"
        self.accounts = []

    async def status(self):
        return {"active_account_id": self.active}

    async def access_token(self, account_id=None):
        self.accounts.append(account_id)
        return "fixture-oauth-token"


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, records, width=17):
        self.data = b"".join(("event: " + record["type"] + "\r\ndata: " + json.dumps(
            record, ensure_ascii=False) + "\r\n\r\n").encode() for record in records)
        self.width = width
        self.closed = False

    async def __aiter__(self):
        for index in range(0, len(self.data), self.width):
            yield self.data[index:index + self.width]

    async def aclose(self):
        self.closed = True


def message(text="Finished."):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}]}


def call(name="read_file", args=None, call_id="call-1", namespace="files"):
    return {"type": "function_call", "call_id": call_id, "namespace": namespace,
            "name": name, "arguments": json.dumps(args or {"path": "fixture.txt"}),
            "status": "completed"}


def terminal(output=None, usage=None):
    return {"type": "response.completed", "response": {"status": "completed",
            "output": output if output is not None else [message()], "usage": usage}}


def wire(records):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                          stream=ChunkStream(records))


def context(tmp_path, permission="trusted_host", tools=True):
    events, executed = [], []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(name, args):
        executed.append((name, args))
        return {"ok": True, "result": "fixture-result"}

    async def approve(payload):
        raise AssertionError("Question is not an approval")

    return RunContext("run-fixture", {"root": str(tmp_path), "permission": permission},
        {"model": "fixture-model", "settings": {"tools": tools, "reasoning_effort": "xhigh"}},
        "Perform fixture task", "Fixture project context", emit, execute, approve,
        asyncio.Event()), events, executed


async def test_sse_split_utf8_comments_and_multiline():
    raw = b': comment\r\nevent: sample\r\ndata: {"type":"sample",\r\ndata: "value":"caf\xc3\xa8"}\r\n\r\n'

    async def chunks():
        for byte in raw:
            yield bytes([byte])
    assert [item async for item in sse(chunks())] == [{"type": "sample", "value": "cafè"}]


@pytest.mark.parametrize("raw", [b'data: []\n\n', b'data: {"value":NaN}\n\n',
    b'data: {"value":1e999}\n\n', b'data: {"x":"\xff"}\n\n', b'data: {}',
    b'event: wrong\ndata: {"type":"other"}\n\n'])
async def test_sse_rejects_invalid_or_incomplete(raw):
    async def chunks():
        yield raw
    with pytest.raises(ResponsesError):
        _ = [item async for item in sse(chunks())]


async def test_sse_event_and_stream_limits(monkeypatch):
    from kira.backends import openai_responses
    monkeypatch.setattr(openai_responses, "MAX_EVENT_BYTES", 12)

    async def chunks():
        yield b'data: {"oversized":true}\n\n'
    with pytest.raises(ResponsesError, match="event exceeded"):
        _ = [item async for item in sse(chunks())]
    monkeypatch.setattr(openai_responses, "MAX_STREAM_BYTES", 8)
    with pytest.raises(ResponsesError, match="stream exceeded"):
        _ = [item async for item in sse(chunks())]


async def test_discovery_uses_direct_catalog_preserves_order_and_effort():
    auth = FakeAuth()

    def handler(request):
        assert str(request.url) == ENDPOINT + "/models"
        assert request.headers["Authorization"] == "Bearer fixture-oauth-token"
        return httpx.Response(200, json={"models": [
            {"slug": "z-model", "display_name": "Z model", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "xhigh"}, {"effort": "medium"}],
             "default_reasoning_level": "medium", "context_window": 100_000},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hidden"},
            {"slug": "a-model", "display_name": "A model", "visibility": "list"}]})
    backend = OpenAIChatGPTBackend(auth, transport=httpx.MockTransport(handler))
    try:
        result = await backend.discover()
        assert result["available"]
        assert [model["id"] for model in result["models"]] == ["z-model", "a-model"]
        assert result["models"][0]["supported_reasoning_efforts"] == ["xhigh", "medium"]
        assert result["models"][0]["default_reasoning_effort"] == "medium"
        assert not result["models"][0]["access_verified"]
        assert result["capabilities"]["images"]
        assert result["account"]["connected"]
        assert auth.accounts == ["account-a"]
    finally:
        await backend.close()


async def test_tool_loop_complete_only_exact_selection_opaque_reasoning_and_account(tmp_path):
    ctx, events, executed = context(tmp_path)
    ctx.history = [{"role": "user", "content": "Old task"},
                   {"role": "assistant", "content": "Old answer"}]
    ctx.assistant_instructions = "Service identity instructions"
    auth, bodies, streams = FakeAuth(), [], []
    reasoning = {"type": "reasoning", "id": "r-private", "encrypted_content": "opaque-fixture",
                 "summary": [{"type": "summary_text", "text": "public-summary-fixture"}]}

    def handler(request):
        assert str(request.url) == ENDPOINT + "/responses"
        body = json.loads(request.content)
        bodies.append(body)
        for forbidden in ("previous_response_id", "temperature", "max_output_tokens", "metadata",
                          "multi_agent", "background", "conversation", "prompt", "user"):
            assert forbidden not in body
        assert body["store"] is False and body["stream"] is True
        assert body["model"] == "fixture-model" and body["reasoning"] == {"effort": "xhigh", "summary": "auto"}
        assert all(item.get("role") != "system" for item in body["input"])
        assert "Service identity instructions" in body["instructions"]
        if len(bodies) == 1:
            assert not executed
            assert body["input"][1:3] == ctx.history
            auth.active = "account-b"
            records = [{"type": "response.output_item.done", "output_index": 1, "item": call()},
                       {"type": "response.reasoning_summary_text.delta", "delta": "private-delta"},
                       terminal([reasoning, call()])]
        else:
            assert streams[0].closed
            assert executed == [("read_file", {"path": "fixture.txt"})]
            assert reasoning in body["input"]
            assert body["input"][-1]["type"] == "function_call_output"
            records = [{"type": "response.output_text.delta", "delta": "Café", "output_index": 0},
                       terminal([message("Café")], {"input_tokens": 10, "output_tokens": 4,
                           "total_tokens": 14, "output_tokens_details": {"reasoning_tokens": 2}})]
        response = wire(records)
        streams.append(response.stream)
        return response
    backend = OpenAIChatGPTBackend(auth, transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed"
        assert result["outcome"]["tool_rounds"] == 1
        assert auth.accounts == ["account-a", "account-a"]
        serialized = json.dumps(events)
        assert "public-summary-fixture" in serialized and "private-delta" not in serialized
        assert "opaque-fixture" not in serialized
        assert ("message.delta", {"content": "Café", "item_id": "run-fixture:openai:1:0"}) in events
        assert next(p for kind, p in events if kind == "usage")["reasoning_tokens"] == 2
    finally:
        await backend.close()


@pytest.mark.parametrize("ending,expected", [
    ({"type": "response.failed", "response": {"error": {
        "code": "subscription_sharing_usage_limit_exceeded", "message": "fixture-oauth-token"}}}, "failed"),
    ({"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}, "interrupted"),
    ({"type": "response.output_item.done", "output_index": 0, "item": call()}, "interrupted"),
    ({"type": "error", "code": "subscription_sharing_usage_unavailable"}, "failed")])
async def test_no_tools_execute_before_successful_terminal(tmp_path, ending, expected):
    ctx, events, executed = context(tmp_path)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([{"type": "response.output_text.delta", "delta": "Partial", "output_index": 0},
              {"type": "response.output_item.done", "output_index": 0, "item": call()}, ending])))
    try:
        result = await backend.run(ctx)
        assert result["state"] == expected and not executed
        assert not any(kind == "message.completed" for kind, _ in events)
        assert "fixture-oauth-token" not in json.dumps([events, result])
    finally:
        await backend.close()


@pytest.mark.parametrize("permission,blocked", [("read_only", {"write_file", "run_command"}),
                                                ("project_write", {"run_command"})])
async def test_permission_tools_are_absent_and_unknown_calls_rejected(tmp_path, permission, blocked):
    ctx, _, executed = context(tmp_path, permission=permission)
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        offered = {tool["name"] for tool in body["tools"][0]["tools"]}
        assert not offered & blocked
        if len(bodies) == 1:
            return wire([terminal([call("run_command", {"argv": ["true"]})])])
        output = json.loads(body["input"][-1]["output"])
        assert not output["ok"] and output["error"]["code"] == "INVALID_TOOL"
        return wire([terminal()])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert not executed
    finally:
        await backend.close()


async def test_duplicate_call_id_never_reexecutes_and_conflicting_id_interrupts(tmp_path):
    ctx, _, executed = context(tmp_path)
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        args = {"path": "different.txt"} if count == 3 else {"path": "fixture.txt"}
        return wire([terminal([call(args=args)])])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted" and "reused" in result["error"]
        assert len(executed) == 1
    finally:
        await backend.close()


@pytest.mark.parametrize("specs", [mailbox_tool_specs, assistant_tool_specs])
async def test_service_namespace_conversion_structured_result_and_identity(tmp_path, specs):
    ctx, _, executed = context(tmp_path)
    ctx.client_tools = specs()
    ctx.disable_native_agents = True
    ctx.output_schema = coordinator_output_schema()
    delivered, bodies = [], []
    namespace = ctx.client_tools[0]["name"]
    tool = ctx.client_tools[0]["tools"][0]["name"]

    async def callback(identity, name, arguments):
        delivered.append((identity, name, arguments))
        return {"ok": True, "value": "service-result"}
    ctx.client_tool_call = callback

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        converted = next(item for item in body["tools"] if item["name"] == namespace)
        assert "parameters" in converted["tools"][0] and "inputSchema" not in converted["tools"][0]
        assert body["text"]["format"] == {"type": "json_schema", "name": "kira_result",
            "strict": True, "schema": ctx.output_schema}
        assert ("managed team member" in body["instructions"]) is (namespace == "team")
        if len(bodies) == 1:
            return wire([terminal([call(tool, {"fixture": "value"}, namespace=namespace)])])
        return wire([terminal([message('{"fixture":"final"}')])])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed"
        assert result["outcome"]["final_text"] == '{"fixture":"final"}'
        assert delivered == [("call-1", f"{namespace}.{tool}", {"fixture": "value"})]
        assert not executed
    finally:
        await backend.close()


async def test_kira_knowledge_and_project_tool_payload_survive_responses_wire(tmp_path):
    ctx, _, executed = context(tmp_path)
    ctx.assistant_instructions = IDENTITY_INSTRUCTIONS
    ctx.client_tools = [{"type": "namespace", "name": "kira", "description": "Fixture Kira",
                         "tools": knowledge_tool_specs() + project_tool_specs()}]
    delivered, bodies = [], []
    arguments = {"action": "run", "payload": {"tool_id": "fixture-tool", "revision": 1,
                                             "arguments": {"nested": [1, True, "fixture"]}}}

    async def callback(identity, name, args):
        delivered.append((identity, name, args))
        return {"ok": True, "result": {"value": 7}, "external_effects": "unverified"}
    ctx.client_tool_call = callback

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert IDENTITY_INSTRUCTIONS in body["instructions"]
        namespace = next(item for item in body["tools"] if item.get("name") == "kira")
        assert {item["name"] for item in namespace["tools"]} == {"help_search", "help_read", "project_tools"}
        gateway = next(item for item in namespace["tools"] if item["name"] == "project_tools")
        assert gateway["parameters"]["properties"]["payload"]["type"] == "object"
        if len(bodies) == 1:
            return wire([terminal([call("project_tools", arguments, namespace="kira")])])
        return wire([terminal([message("Fixture finished")])])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert delivered == [("call-1", "kira.project_tools", arguments)]
        assert not executed
    finally:
        await backend.close()


@pytest.mark.parametrize("round_limit", [None, 0, 5])
async def test_team_resume_wire_is_bound_and_duplicate_call_is_not_repeated(tmp_path, round_limit):
    ctx, _, executed = context(tmp_path)
    ctx.client_tools = assistant_tool_specs()
    calls, bodies = [], []
    arguments = {"delegation_id": "fixture-delegation", "instruction": "Review the recorded fixture outcome"}
    if round_limit is not None:
        arguments["round_limit"] = round_limit

    async def callback(call_id, name, payload):
        calls.append((call_id, name, payload))
        return {"ok": True, "action": "resume", "state": "pending",
                "source_delegation_id": payload["delegation_id"]}
    ctx.client_tool_call = callback

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        namespace = next(item for item in body["tools"] if item.get("name") == "kira")
        tool = next(item for item in namespace["tools"] if item["name"] == "team_resume")
        assert "project_id" not in tool["parameters"]["properties"]
        if len(bodies) < 3:
            return wire([terminal([call("team_resume", arguments, namespace="kira")])])
        return wire([terminal([message("Fixture continuation queued")])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert calls == [("call-1", "kira.team_resume", arguments)] and not executed
    finally:
        await backend.close()


@pytest.mark.parametrize("accepted", [True, False])
async def test_question_delivery_only_after_followup_admitted(tmp_path, accepted):
    ctx, events, _ = context(tmp_path)
    count = 0
    requests = []

    async def request_input(request):
        requests.append(request)
        return {"ok": True, "status": "answered", "request_id": "durable-question",
                "answers": {"choice": {"answers": ["One"]}}}

    async def read_input(identity, wait):
        raise AssertionError("Blocking request already answered")
    ctx.request_input, ctx.read_input = request_input, read_input

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return wire([terminal([call("request_input", {"blocking": True, "questions": [
                {"id": "choice", "question": "Pick one", "options": [{"label": "One"}]}]},
                namespace="questions")])])
        assert not any(kind == "input.delivered" for kind, _ in events)
        if not accepted:
            return httpx.Response(403, json={"detail": "fixture-oauth-token"})
        return wire([{"type": "response.created", "response": {"status": "in_progress"}}, terminal()])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert requests[0]["request_id"] == "call-1"
        assert result["state"] == ("completed" if accepted else "failed")
        kind = "input.delivered" if accepted else "input.delivery_failed"
        assert sum(event == kind for event, _ in events) == 1
        assert "fixture-oauth-token" not in json.dumps([result, events])
    finally:
        await backend.close()


@pytest.mark.parametrize("code,status,body", [
    ("subscription_sharing_usage_limit_exceeded", 429, "error"),
    ("subscription_sharing_user_not_eligible", 403, "error"),
    (None, 401, "detail"), (None, 503, "detail")])
async def test_admission_error_machine_details_no_retry_or_secret(tmp_path, code, status, body):
    ctx, events, _ = context(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        payload = {"detail": "fixture-oauth-token"} if body == "detail" else {
            "error": {"code": code, "param": "model", "message": "fixture-oauth-token"}}
        return httpx.Response(status, headers={"x-request-id": "req-fixture"}, json=payload)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and len(requests) == 1
        assert result["outcome"]["http_status"] == status
        assert result["outcome"]["body_shape"] == body
        assert result["outcome"]["request_id"] == "req-fixture"
        assert "fixture-oauth-token" not in json.dumps([result, events])
        if code:
            assert result["outcome"]["code"] == code
    finally:
        await backend.close()


async def test_cancellation_closes_stream_without_claiming_remote_termination(tmp_path):
    ctx, events, executed = context(tmp_path)
    ready = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            ready.set()
            await asyncio.Future()
            yield b""

        async def aclose(self):
            self.closed = True
    stream = WaitingStream()
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)))
    try:
        task = asyncio.create_task(backend.run(ctx))
        await asyncio.wait_for(ready.wait(), 2)
        ctx.cancel.set()
        result = await asyncio.wait_for(task, 2)
        assert result["state"] == "cancelled" and stream.closed
        assert result["outcome"]["remote_generation_termination_confirmed"] is False
        assert not [event for event in events if event[0] != "reasoning.summary.status"] and not executed
    finally:
        await backend.close()


async def test_configured_round_limit_stops_before_next_effect(tmp_path):
    ctx, _, executed = context(tmp_path)
    ctx.session["settings"]["max_tool_rounds"] = 1
    requests = []

    def handler(request):
        requests.append(request)
        return wire([terminal([call(call_id=f"call-{len(requests)}")])])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "failed"
        assert len(executed) == 1 and len(requests) == 2
    finally:
        await backend.close()


async def test_explicit_account_and_auth_failure_do_not_fallback(tmp_path):
    ctx, events, _ = context(tmp_path)
    ctx.session["settings"]["openai_account_id"] = "account-bound"
    auth = FakeAuth()

    async def denied(account):
        assert account == "account-bound"
        raise KiraError("OPENAI_PLAN_DISABLED", "Plan disabled")
    auth.access_token = denied
    backend = OpenAIChatGPTBackend(auth, transport=httpx.MockTransport(lambda request:
        pytest.fail("No inference when plan permission is missing")))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and not [
            event for event in events if event[0] != "reasoning.summary.status"]
        assert result["outcome"]["code"] == "OPENAI_PLAN_DISABLED"
    finally:
        await backend.close()


@pytest.mark.parametrize("output", [[{**call(), "status": "incomplete"}],
    [{**call(), "caller": {"type": "program", "caller_id": "unknown"}}],
    [{"type": "computer_call", "call_id": "unsupported"}]])
async def test_unregistered_execution_modes_and_incomplete_calls_do_not_execute(tmp_path, output):
    ctx, _, executed = context(tmp_path)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([terminal(output)])))
    try:
        assert (await backend.run(ctx))["state"] != "completed" and not executed
    finally:
        await backend.close()


async def test_nonblocking_question_can_be_polled_without_implicit_answer(tmp_path):
    ctx, events, _ = context(tmp_path)
    count, question_id = 0, "durable-question"

    async def request_input(request):
        assert request["blocking"] is False
        return {"ok": True, "status": "pending", "request_id": question_id}

    async def read_input(identity, wait):
        assert identity == question_id and wait is True
        return {"ok": True, "status": "answered", "request_id": identity,
                "answers": {"choice": {"answers": ["An explicit answer"]}}}
    ctx.request_input, ctx.read_input = request_input, read_input

    def handler(request):
        nonlocal count
        count += 1
        body = json.loads(request.content)
        if count == 1:
            return wire([terminal([call("request_input", {"blocking": False, "questions": [
                {"id": "choice", "question": "What should we choose?"}]}, namespace="questions")])])
        if count == 2:
            assert json.loads(body["input"][-1]["output"])["status"] == "pending"
            assert not any(kind == "input.delivered" for kind, _ in events)
            return wire([terminal([call("read_input", {"request_id": question_id, "wait": True},
                call_id="call-2", namespace="questions")])])
        assert json.loads(body["input"][-1]["output"])["answers"] == {
            "choice": {"answers": ["An explicit answer"]}}
        return wire([terminal()])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert sum(kind == "input.delivered" for kind, _ in events) == 1
    finally:
        await backend.close()


async def test_text_limit_counts_unstreamed_messages_across_rounds(tmp_path):
    ctx, _, executed = context(tmp_path)
    ctx.session["settings"]["max_output_bytes"] = 8
    requests = []

    def handler(request):
        requests.append(request)
        return wire([terminal([message("12345"), call(call_id=f"call-{len(requests)}")])])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted" and "byte limit" in result["error"]
        assert len(executed) == 1
    finally:
        await backend.close()


async def test_tool_callback_error_does_not_expose_exception_contents(tmp_path):
    ctx, events, _ = context(tmp_path)

    async def broken(name, arguments):
        raise RuntimeError("fixture-oauth-token or confidential callback content")
    ctx.execute_tool = broken
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([terminal([call()])])))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted"
        assert "fixture-oauth-token" not in json.dumps([result, events])
        assert "inspect durable events" in result["error"]
    finally:
        await backend.close()


async def test_chat_only_tools_disabled_cannot_execute_provider_invented_call(tmp_path):
    ctx, _, executed = context(tmp_path, tools=False)
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        body = json.loads(request.content)
        assert "tools" not in body
        return wire([terminal([call()])]) if count == 1 else wire([terminal()])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed" and not executed
    finally:
        await backend.close()


async def test_redirect_is_not_followed_or_authorized_elsewhere(tmp_path):
    ctx, _, _ = context(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/collect"})
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed"
        assert len(requests) == 1 and requests[0].url.host == "api.openai.com"
    finally:
        await backend.close()


async def test_no_progress_detected_even_when_provider_changes_call_ids(tmp_path):
    ctx, _, executed = context(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return wire([terminal([call(call_id=f"call-{len(requests)}")])])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "three times" in result["error"]
        assert len(executed) == 3 and len(requests) == 3
    finally:
        await backend.close()


async def test_discovery_explicit_account_does_not_use_active_account():
    auth = FakeAuth()
    backend = OpenAIChatGPTBackend(auth, transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={"models": []})))
    try:
        result = await backend.discover("account-bound")
        assert result["available"] and result["account"]["id"] == "account-bound"
        assert auth.accounts == ["account-bound"]
    finally:
        await backend.close()


async def test_discovery_unknown_explicit_account_never_falls_back():
    auth = FakeAuth()
    attempted = []

    async def denied(account):
        attempted.append(account)
        raise KiraError("OPENAI_SIGN_IN_REQUIRED", "Unknown selected account")
    auth.access_token = denied
    backend = OpenAIChatGPTBackend(auth, transport=httpx.MockTransport(lambda request:
        pytest.fail("Unknown account must not reach the API")))
    try:
        result = await backend.discover("unknown-account")
        assert not result["available"] and result["models"] == []
        assert attempted == ["unknown-account"]
        assert result["error_code"] == "OPENAI_SIGN_IN_REQUIRED"
    finally:
        await backend.close()


@pytest.mark.parametrize("setting,value", [("service_tier", "priority"), ("temperature", 0.3),
    ("seed", 42), ("think", True), ("num_ctx", 1024)])
async def test_unsupported_explicit_settings_fail_before_inference(tmp_path, setting, value):
    ctx, events, _ = context(tmp_path)
    ctx.session["settings"][setting] = value
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        pytest.fail("Unsupported explicit setting must not silently disappear")))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and not [
            event for event in events if event[0] != "reasoning.summary.status"]
        assert "provider settings" in result["error"]
    finally:
        await backend.close()


@pytest.mark.parametrize("reported", [None, "fixture-model-2026-01-01"])
async def test_reported_model_distinct_from_selected_alias(tmp_path, reported):
    ctx, _, _ = context(tmp_path)
    record = terminal()
    if reported is not None:
        record["response"]["model"] = reported
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([record])))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed"
        assert result["outcome"]["requested_model"] == "fixture-model"
        assert result["outcome"]["reported_model"] == reported
    finally:
        await backend.close()


async def test_missing_content_type_accepts_valid_sse_and_reports_only_known_program(tmp_path):
    """Synthetic reconstruction of the plan route's headerless HTTP 200 SSE."""
    ctx, events, executed = context(tmp_path)
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert "access_programs" not in body
        if len(bodies) == 1:
            records = [{"type": "response.created", "response": {"status": "in_progress"}},
                       terminal([call()])]
        else:
            completed = terminal()
            completed["response"]["access_programs"] = {
                "cyber": "daybreak_blue", "opaque_unexpected": "private-fixture"}
            records = [{"type": "response.created", "response": {"status": "in_progress"}}, completed]
        return httpx.Response(200, stream=ChunkStream(records))
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed"
        assert len(executed) == 1
        assert result["outcome"]["reported_access_programs"] == {"cyber": "daybreak_blue"}
        assert "private-fixture" not in json.dumps([result, events])
    finally:
        await backend.close()


@pytest.mark.parametrize("payload", [b'{"status":"completed","output":[]}',
    b'<html>Not an event stream</html>\n',
    b'data: {"type":"response.output_item.done","item":{"type":"function_call"}}\n\n',
    b'event: response.completed\ndata: {"type":"response.completed"}\n\n'])
async def test_missing_content_type_does_not_accept_non_sse_or_unconfirmed_tools(tmp_path, payload):
    ctx, events, executed = context(tmp_path)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        httpx.Response(200, content=payload)))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted"
        assert not executed and not any(kind == "message.completed" for kind, _ in events)
    finally:
        await backend.close()


@pytest.mark.parametrize("content_type", ["application/json", "text/html", ""])
async def test_explicit_incompatible_content_type_still_rejected(tmp_path, content_type):
    ctx, events, executed = context(tmp_path)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        httpx.Response(200, headers={"content-type": content_type}, stream=ChunkStream([terminal()]))))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted"
        assert not [event for event in events if event[0] != "reasoning.summary.status"] and not executed
    finally:
        await backend.close()


@pytest.mark.parametrize("programs", [None, {}, {"cyber": "unknown-future-program"},
                                      {"cyber": "fixture-secret-value"}, {"cyber": []}])
async def test_reported_access_programs_are_known_metadata_only(tmp_path, programs):
    ctx, events, _ = context(tmp_path)
    record = terminal()
    record["response"]["access_programs"] = programs
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request:
        wire([record])))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed"
        assert result["outcome"]["reported_access_programs"] == {}
        assert "fixture-secret-value" not in json.dumps([result, events])
    finally:
        await backend.close()


async def test_item_done_reconstructs_empty_terminal_tool_loop_and_structured_final(tmp_path):
    ctx, events, executed = context(tmp_path)
    ctx.output_schema = {"type": "object", "properties": {"result": {"type": "string"}},
                         "required": ["result"], "additionalProperties": False}
    bodies = []
    opaque = {"type": "reasoning", "id": "reasoning-fixture", "summary": [],
              "encrypted_content": "opaque-synthetic-value"}
    local_call = {**call(), "id": "function-fixture"}
    final_message = {**message('{"result":"verified"}'), "id": "message-fixture"}

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            assert not executed
            output = [opaque, local_call]
        else:
            assert executed == [("read_file", {"path": "fixture.txt"})]
            assert opaque in body["input"] and local_call in body["input"]
            assert body["input"][-1]["type"] == "function_call_output"
            output = [opaque, final_message]
        records = [{"type": "response.created", "response": {"status": "in_progress"}}]
        for index, item in enumerate(output):
            records.append({"type": "response.output_item.added", "output_index": index,
                            "item": {"type": item["type"], "id": item["id"], "status": "in_progress"}})
            if item["type"] == "message":
                records.append({"type": "response.output_text.delta", "output_index": index,
                                "delta": '{"result":"verified"}'})
            records.append({"type": "response.output_item.done", "output_index": index, "item": item})
        records.append(terminal([]))
        return httpx.Response(200, stream=ChunkStream(records))
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed" and len(bodies) == 2
        assert result["outcome"]["final_text"] == '{"result":"verified"}'
        assert len(executed) == 1
        completions = [payload for kind, payload in events if kind == "message.completed"]
        assert completions == [{"content": '{"result":"verified"}', "item_id": "run-fixture:openai:1:1"}]
        assert "opaque-synthetic-value" not in json.dumps([events, result])
    finally:
        await backend.close()


@pytest.mark.parametrize("records", [
    [{"type": "response.output_item.done", "output_index": -1, "item": call()}, terminal([])],
    [{"type": "response.output_item.done", "output_index": True, "item": call()}, terminal([])],
    [{"type": "response.output_item.done", "output_index": 64, "item": call()}, terminal([])],
    [{"type": "response.output_item.done", "output_index": 1, "item": call()}, terminal([])],
    [{"type": "response.output_item.added", "output_index": 0, "item": call()}, terminal([])],
    [{"type": "response.output_item.done", "output_index": 0, "item": call()},
     {"type": "response.output_item.done", "output_index": 0, "item": call(args={"path": "other"})}, terminal([])],
    [{"type": "response.output_item.done", "output_index": 0, "item": call()},
     terminal([call(args={"path": "different"})])],
    [{"type": "response.output_item.added", "output_index": 0, "item": {**call(), "id": "first"}},
     {"type": "response.output_item.done", "output_index": 0, "item": {**call(), "id": "other"}}, terminal([])],
    [{"type": "response.output_text.delta", "output_index": 0, "delta": "unfinished"}, terminal([])],
])
async def test_item_accumulator_rejects_conflicts_and_missing_final_items(tmp_path, records):
    ctx, events, executed = context(tmp_path)
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(lambda request: wire(records)))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted"
        assert not executed and not any(kind == "message.completed" for kind, _ in events)
    finally:
        await backend.close()


@pytest.mark.parametrize("populated_terminal", [True, False])
async def test_duplicate_identical_done_items_do_not_duplicate_effects(tmp_path, populated_terminal):
    ctx, _, executed = context(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            done = {"type": "response.output_item.done", "output_index": 0, "item": call()}
            return wire([done, done, terminal([call()] if populated_terminal else [])])
        return wire([terminal()])
    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed" and len(executed) == 1
    finally:
        await backend.close()
