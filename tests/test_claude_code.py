"""Deterministic Claude Code fixtures. Live account evidence is reported separately."""

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys

import pytest

sdk = pytest.importorskip("claude_agent_sdk")

from kira.backends import claude_code  # noqa: E402
from kira.backends.claude_code import ClaudeCodeBackend  # noqa: E402
from kira.domain.contracts import KiraError, RunContext  # noqa: E402
from kira.service.questions import DELIVERY_TRANSPORTS  # noqa: E402


def runtime(tmp_path):
    path = tmp_path / "runtime"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def context(tmp_path, permission="project_write", **extra):
    events = []
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(name, args):
        raise AssertionError("Claude Code tools must never execute through Kira")

    async def approve(request):
        raise AssertionError("Claude Code approvals are decided by the project permission")

    settings = extra.pop("settings", {"reasoning_effort": "low"})
    session = {"model": "sonnet", "settings": settings, "backend_ref": extra.pop("backend_ref", None)}
    ctx = RunContext("fixture-run", {"id": "p", "root": str(root), "permission": permission},
                     session, "Change the fixture", "fixture supplied context", emit, execute,
                     approve, asyncio.Event(), **extra)
    return ctx, events, root


def init(session="session-fixture", servers=()):
    return sdk.SystemMessage("init", {"type": "system", "subtype": "init", "session_id": session,
                                      "model": "claude-sonnet-5-5", "tools": ["Read"],
                                      "apiKeySource": "none", "mcp_servers": list(servers)})


def result(**fields):
    values = {"subtype": "success", "duration_ms": 5, "duration_api_ms": 4, "is_error": False,
              "num_turns": 1, "session_id": "session-fixture", "stop_reason": "end_turn",
              "usage": {"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 11}}
    values.update(fields)
    return sdk.ResultMessage(**values)


class FakeClient:
    instances = []

    def __init__(self, options, script, info=None):
        self.options = options
        self.script = script
        self.info = {"pid": 4242, "models": []} if info is None else info
        self.queries = []
        self.interrupted = asyncio.Event()
        self.disconnected = False
        FakeClient.instances.append(self)

    async def connect(self):
        return None

    async def get_server_info(self):
        return self.info

    async def query(self, prompt):
        self.queries.append(prompt)

    async def interrupt(self):
        self.interrupted.set()

    async def disconnect(self):
        self.disconnected = True

    async def receive_messages(self):
        for item in self.script:
            if callable(item):
                item = await item(self)
            if item is not None:
                yield item

    async def receive_response(self):
        # Like the SDK: stop after the first result, whichever turn it belongs to.
        async for item in self.receive_messages():
            yield item
            if isinstance(item, sdk.ResultMessage):
                return


def backend(tmp_path, script, info=None):
    FakeClient.instances.clear()
    return ClaudeCodeBackend(runtime(tmp_path),
                             client_factory=lambda options: FakeClient(options, script, info))


def guard_of(options):
    return options.hooks["PreToolUse"][0].hooks[0]


def test_tool_set_follows_kira_permissions():
    assert claude_code.tool_set("read_only") == ["Read", "Glob", "Grep"]
    assert claude_code.tool_set("project_write") == ["Read", "Glob", "Grep", "Edit", "Write",
                                                     "NotebookEdit"]
    for permission in ("trusted_host", "full_access"):
        assert {"Bash", "WebFetch", "WebSearch"} <= set(claude_code.tool_set(permission))


def test_tool_receipts_use_kira_names_for_kira_tools():
    assert claude_code.display_name("Write") == "claude_code.Write"
    assert claude_code.display_name("mcp__kira__kira__environment") == "claude_code.kira.environment"
    assert (claude_code.display_name("mcp__kira__questions__request_input")
            == "claude_code.questions.request_input")


async def test_guard_denies_tools_and_paths_outside_the_project(tmp_path):
    ctx, _, root = context(tmp_path)
    (root / "inside.txt").write_text("fixture")
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / "link.txt").symlink_to(outside)
    guard = ClaudeCodeBackend(runtime(tmp_path))._guard(ctx, set(claude_code.tool_set("project_write")))

    def denied(output):
        return output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    assert await guard({"tool_name": "Read", "tool_input": {"file_path": str(root / "inside.txt")}},
                       "t1", {}) == {}
    assert await guard({"tool_name": "Write", "tool_input": {"file_path": "new/file.txt"}}, "t2", {}) == {}
    assert await guard({"tool_name": "Grep", "tool_input": {"pattern": "x"}}, "t3", {}) == {}
    assert denied(await guard({"tool_name": "Read", "tool_input": {"file_path": str(outside)}}, "t4", {}))
    assert denied(await guard({"tool_name": "Read", "tool_input": {"file_path": "link.txt"}}, "t5", {}))
    assert denied(await guard({"tool_name": "Edit", "tool_input": {"file_path": "../outside.txt"}}, "t6", {}))
    assert denied(await guard({"tool_name": "Glob", "tool_input": {"pattern": "/etc/*"}}, "t7", {}))
    assert denied(await guard({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "t8", {}))
    assert denied(await guard({"tool_name": "Agent", "tool_input": {}}, "t9", {}))

    full, _, _ = context(tmp_path / "full", permission="full_access")
    full_guard = ClaudeCodeBackend(runtime(tmp_path / "full"))._guard(
        full, set(claude_code.tool_set("full_access")))
    assert await full_guard({"tool_name": "Read", "tool_input": {"file_path": str(outside)}},
                            "t10", {}) == {}


def test_launcher_starts_a_new_session_with_a_reduced_environment(tmp_path):
    directory = runtime(tmp_path)
    launcher = ClaudeCodeBackend(directory)._launcher_path()
    assert launcher.parent == directory and launcher.stat().st_mode & 0o777 == 0o700
    target = tmp_path / "target"
    target.write_text(f"#!{sys.executable}\nimport json,os,sys\n"
                      "print(json.dumps({'leader': os.getsid(0) == os.getpid(), 'args': sys.argv[1:],"
                      " 'env': sorted(os.environ)}))\n")
    target.chmod(0o700)
    environment = {"HOME": str(tmp_path), "PATH": os.defpath, "SECRET_TOKEN": "fixture-secret",
                   "OPENAI_API_KEY": "fixture-key", "CLAUDE_CONFIG_DIR": str(tmp_path),
                   claude_code.TARGET_ENV: str(target)}
    output = subprocess.run([str(launcher), "--flag", "value with space"], env=environment,
                            capture_output=True, text=True, check=True, timeout=30).stdout
    observed = json.loads(output)
    assert observed["leader"] is True
    assert observed["args"] == ["--flag", "value with space"]
    assert "CLAUDE_CONFIG_DIR" in observed["env"] and "HOME" in observed["env"]
    assert not {"SECRET_TOKEN", "OPENAI_API_KEY", claude_code.TARGET_ENV} & set(observed["env"])


async def test_run_streams_text_observes_tools_and_records_the_session(tmp_path):
    ctx, events, root = context(tmp_path, backend_ref="session-before",
                                assistant_instructions="Fixture operating contract")
    registered = []

    async def started(pid):
        registered.append(pid)
    ctx.provider_started = started
    target = root / "a.txt"

    async def write_file(client):
        guard = guard_of(client.options)
        denied = await guard({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, "b", {})
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        target.write_text("written")
        return sdk.UserMessage([sdk.ToolResultBlock("tool-1", "File written", False)])

    script = [
        init(),
        sdk.StreamEvent("u1", "session-fixture", {"type": "message_start", "message": {"id": "m1"}}),
        sdk.StreamEvent("u2", "session-fixture", {"type": "content_block_start", "index": 0,
                                                   "content_block": {"type": "text", "text": ""}}),
        sdk.StreamEvent("u3", "session-fixture", {"type": "content_block_delta", "index": 0,
                                                   "delta": {"type": "text_delta", "text": "Ciao"}}),
        sdk.StreamEvent("u4", "session-fixture", {"type": "content_block_delta", "index": 0,
                                                   "delta": {"type": "text_delta", "text": " mondo"}}),
        sdk.StreamEvent("u5", "session-fixture", {"type": "content_block_stop", "index": 0}),
        sdk.AssistantMessage([sdk.TextBlock("Ciao mondo")], "claude-sonnet-5-5", message_id="m1"),
        sdk.AssistantMessage([sdk.ToolUseBlock("tool-1", "Write", {"file_path": str(target),
                                                                   "content": "written"})],
                             "claude-sonnet-5-5", message_id="m1"),
        write_file,
        result(),
    ]
    outcome = await backend(tmp_path, script).run(ctx)

    assert outcome["state"] == "completed"
    assert outcome["outcome"]["session_id"] == "session-fixture"
    assert outcome["outcome"]["model"] == "claude-sonnet-5-5"
    assert registered == [4242]
    client = FakeClient.instances[0]
    options = client.options
    assert options.setting_sources == [] and options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert options.tools == claude_code.tool_set("project_write")
    assert options.cwd == str(root) and options.model == "sonnet" and options.effort == "low"
    assert options.resume == "session-before"
    assert options.system_prompt == {"type": "preset", "preset": "claude_code",
                                     "append": "Fixture operating contract"}
    assert options.env[claude_code.TARGET_ENV] and options.cli_path.endswith(claude_code.LAUNCHER_NAME)
    assert options.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert options.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert client.queries == ["Kira-supplied project context:\nfixture supplied context\n\n"
                              "Task:\nChange the fixture"]
    assert client.disconnected

    kinds = [kind for kind, _ in events]
    assert ("session.updated", {"backend_ref": "session-fixture"}) in events
    deltas = [payload for kind, payload in events if kind == "message.delta"]
    completed = [payload for kind, payload in events if kind == "message.completed"]
    assert [delta["content"] for delta in deltas] == ["Ciao", " mondo"]
    assert completed == [{"content": "Ciao mondo", "item_id": deltas[0]["item_id"]}]
    started_tool = next(payload for kind, payload in events if kind == "tool.started")
    assert started_tool["name"] == "claude_code.Write"
    assert started_tool["arguments"]["path"] == str(target)
    finished = next(payload for kind, payload in events if kind == "tool.completed")
    assert finished["result"]["ok"] is True and finished["result"]["observed_only"] is True
    artifact = next(payload for kind, payload in events if kind == "artifact.created")
    assert artifact["sha256"] == hashlib.sha256(b"written").hexdigest()
    usage = next(payload for kind, payload in events if kind == "usage")
    # Kira's input count folds the cache read in; the uncached share stays visible.
    assert usage["input_tokens"] == 14 and usage["cached_input_tokens"] == 11
    assert usage["uncached_input_tokens"] == 3 and usage["round"] == 1 and usage["scope"] == "turn"
    assert kinds.index("tool.started") < kinds.index("tool.completed")


async def test_plan_limits_and_turn_usage_are_recorded_from_claude_code_reports(tmp_path):
    ctx, events, _ = context(tmp_path, permission="read_only")
    info = sdk.RateLimitInfo(
        status="allowed_warning", resets_at=1792567800, rate_limit_type="five_hour", utilization=0.91,
        overage_status="rejected", overage_disabled_reason="out_of_credits",
        raw={"status": "allowed_warning", "isUsingOverage": False, "unifiedWindows": {
            "five_hour": {"utilization": 0.91, "resetsAt": 1792567800},
            "seven_day": {"utilization": 0.13, "resetsAt": 1792879200},
            "unknown_window": {"utilization": 0.5, "resetsAt": 1}}})
    turn_usage = {"input_tokens": 6, "cache_read_input_tokens": 51_163, "cache_creation_input_tokens": 400,
                  "output_tokens": 2_287, "iterations": [
                      {"input_tokens": 2, "cache_read_input_tokens": 20_000, "cache_creation_input_tokens": 300,
                       "output_tokens": 87, "type": "message"},
                      {"input_tokens": 4, "cache_read_input_tokens": 31_163, "cache_creation_input_tokens": 100,
                       "output_tokens": 2_200, "type": "message"}]}
    model_usage = {"claude-sonnet-5-5": {"inputTokens": 6, "outputTokens": 2287, "cacheReadInputTokens": 51163,
                                         "cacheCreationInputTokens": 400, "webSearchRequests": 0, "costUSD": 0.2,
                                         "contextWindow": 200_000, "maxOutputTokens": 64_000}}
    script = [init(), sdk.RateLimitEvent(rate_limit_info=info, uuid="r1", session_id="session-fixture"),
              sdk.AssistantMessage([sdk.TextBlock("Ok")], "claude-sonnet-5-5", message_id="m1"),
              result(usage=turn_usage, model_usage=model_usage, total_cost_usd=0.2)]
    outcome = await backend(tmp_path, script).run(ctx)
    assert outcome["state"] == "completed"

    plan, turn = [payload for kind, payload in events if kind == "usage"]
    assert plan["measurement"] == "plan_limits" and plan["scope"] == "account" and "round" not in plan
    assert plan["status"] == "allowed_warning" and plan["limit_type"] == "five_hour"
    assert plan["utilization"] == 0.91 and plan["resets_at"] == 1792567800
    assert plan["windows"] == {"five_hour": {"utilization": 0.91, "resets_at": 1792567800},
                               "seven_day": {"utilization": 0.13, "resets_at": 1792879200}}
    assert plan["overage_status"] == "rejected" and plan["overage_disabled_reason"] == "out_of_credits"
    assert plan["using_overage"] is False
    notice = next(payload for kind, payload in events
                  if kind == "backend.notice" and payload.get("message") == "Claude plan usage limit status")
    assert notice["status"] == "allowed_warning" and notice["limit_type"] == "five_hour"

    assert turn["round"] == 1 and turn["scope"] == "turn" and turn["model"] == "claude-sonnet-5-5"
    assert turn["input_tokens"] == 6 + 51_163 + 400 and turn["cached_input_tokens"] == 51_163
    assert turn["cache_creation_input_tokens"] == 400 and turn["uncached_input_tokens"] == 6
    assert turn["output_tokens"] == 2_287 and turn["total_tokens"] == 6 + 51_163 + 400 + 2_287
    assert turn["requests"] == 2
    assert turn["last_request"] == {"input_tokens": 4 + 31_163 + 100, "cached_input_tokens": 31_163,
                                    "cache_creation_input_tokens": 100, "output_tokens": 2_200}
    assert turn["model_context_window"] == 200_000
    # The API-equivalent cost estimate is not forwarded.
    assert "cost" not in json.dumps(turn).lower()


async def test_plan_notice_with_allowed_status_is_recorded_without_a_warning(tmp_path):
    ctx, events, _ = context(tmp_path, permission="read_only")
    info = sdk.RateLimitInfo(status="allowed", resets_at=None, rate_limit_type=None, utilization=None,
                             raw={"status": "allowed", "unifiedWindows": "not-a-mapping"})
    script = [init(), sdk.RateLimitEvent(rate_limit_info=info, uuid="r1", session_id="session-fixture"),
              result(usage={"input_tokens": 0, "output_tokens": 0})]
    assert (await backend(tmp_path, script).run(ctx))["state"] == "completed"
    usage = [payload for kind, payload in events if kind == "usage"]
    assert len(usage) == 1 and usage[0]["measurement"] == "plan_limits" and usage[0]["windows"] == {}
    assert not any(payload.get("message") == "Claude plan usage limit status"
                   for kind, payload in events if kind == "backend.notice")


async def test_stop_interrupts_claude_code_and_reports_cancelled(tmp_path):
    ctx, events, _ = context(tmp_path, permission="read_only")

    async def stop(client):
        ctx.cancel.set()
        await asyncio.wait_for(client.interrupted.wait(), 5)
        return result(subtype="error_during_execution", is_error=True,
                      terminal_reason="aborted_streaming", usage={})

    outcome = await backend(tmp_path, [init(), stop]).run(ctx)
    assert outcome["state"] == "cancelled"
    assert outcome["outcome"]["interruption_confirmed"] is True
    assert FakeClient.instances[0].interrupted.is_set()
    assert not any(kind == "usage" for kind, _ in events)


async def test_turn_that_finished_before_the_interruption_keeps_its_outcome(tmp_path):
    ctx, _, _ = context(tmp_path, permission="read_only")

    async def finished(client):
        ctx.cancel.set()
        await asyncio.wait_for(client.interrupted.wait(), 5)
        return result()

    outcome = await backend(tmp_path, [init(), finished]).run(ctx)
    assert outcome["state"] == "completed"


async def test_queued_notification_result_does_not_end_the_prompt(tmp_path):
    # On resume, a background task stopped with the previous run is replayed as
    # its own zero-turn result before the prompt is answered.
    ctx, events, _ = context(tmp_path, permission="read_only")
    script = [init(), result(num_turns=0, stop_reason=None, result="", usage={},
                             origin={"kind": "task-notification"}),
              init(),
              sdk.AssistantMessage([sdk.TextBlock("Risposta")], "claude-sonnet-5-5", message_id="m1"),
              result(result="Risposta")]
    outcome = await backend(tmp_path, script).run(ctx)
    assert outcome["state"] == "completed" and outcome["outcome"]["num_turns"] == 1
    assert [payload["content"] for kind, payload in events if kind == "message.completed"] == [
        "Risposta"]
    notices = [payload["message"] for kind, payload in events if kind == "backend.notice"]
    assert notices.count("Claude Code session configured") == 1
    assert "Claude Code handled a queued notification before this prompt" in notices


async def test_kira_tools_and_questions_use_the_in_process_mcp_server(tmp_path, monkeypatch):
    monkeypatch.setattr(sdk, "create_sdk_mcp_server",
                        lambda name, version, tools: {"type": "sdk", "name": name, "tools": tools})
    calls = []

    async def client_tool_call(call_id, name, arguments):
        calls.append((call_id, name, arguments))
        return {"ok": True, "results": []}

    async def request_input(request):
        return {"ok": True, "status": "answered", "request_id": request["request_id"],
                "answers": {"q1": {"answers": ["Sì"]}}}

    async def read_input(request_id, wait):
        return {"ok": True, "status": "pending", "request_id": request_id}

    history = {"type": "namespace", "name": "kira_history", "description": "History",
               "tools": [{"type": "function", "name": "search", "description": "Search",
                          "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}}]}
    ctx, events, _ = context(tmp_path, permission="read_only", client_tools=[history],
                             client_tool_call=client_tool_call, request_input=request_input,
                             read_input=read_input)
    replies = {}

    async def use_tools(client):
        tools = {tool.name: tool for tool in client.options.mcp_servers["kira"]["tools"]}
        assert set(tools) == {"kira_history__search", "questions__request_input",
                              "questions__read_input"}
        replies["search"] = await tools["kira_history__search"].handler({"query": "fixture"})
        replies["question"] = await tools["questions__request_input"].handler({
            "blocking": True, "questions": [{"id": "q1", "question": "Procedo?"}]})
        return None

    script = [init(servers=[{"name": "kira", "status": "connected"}]), use_tools,
              sdk.AssistantMessage([sdk.TextBlock("Grazie")], "claude-sonnet-5-5", message_id="m2"),
              result()]
    outcome = await backend(tmp_path, script).run(ctx)
    assert outcome["state"] == "completed"
    options = FakeClient.instances[0].options
    assert {"mcp__kira__kira_history__search", "mcp__kira__questions__request_input"} <= set(
        options.allowed_tools)
    assert FakeClient.instances[0].queries[0].startswith(claude_code.QUESTION_GUIDANCE)
    assert calls == [("fixture-run:claude-code:1", "kira_history.search", {"query": "fixture"})]
    assert replies["search"]["is_error"] is False
    assert json.loads(replies["search"]["content"][0]["text"]) == {"ok": True, "results": []}
    assert replies["question"]["is_error"] is False
    delivered = [payload for kind, payload in events if kind == "input.delivered"]
    assert delivered == [{"request_id": "fixture-run:claude-code-question:1",
                          "transport": "claude-code-mcp"}]
    # The service rejects unknown transports; a mismatch interrupts the real run.
    assert claude_code.ANSWER_TRANSPORT in DELIVERY_TRANSPORTS
    assert not any(kind == "input.delivery_failed" for kind, _ in events)


async def test_missing_kira_tool_connection_stops_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(sdk, "create_sdk_mcp_server",
                        lambda name, version, tools: {"type": "sdk", "name": name, "tools": tools})

    async def request_input(request):
        raise AssertionError("not reached")

    async def read_input(request_id, wait):
        raise AssertionError("not reached")

    ctx, _, _ = context(tmp_path, permission="read_only", request_input=request_input,
                        read_input=read_input)
    script = [init(servers=[{"name": "kira", "status": "failed"}]), result()]
    outcome = await backend(tmp_path, script).run(ctx)
    assert outcome["state"] == "interrupted"
    assert "did not connect the Kira tools" in outcome["error"]


async def test_unsupported_inputs_fail_before_claude_code_starts(tmp_path):
    ctx, _, _ = context(tmp_path, image_inputs={"fixture-run": [{"type": "input_image"}]})
    assert (await backend(tmp_path, []).run(ctx))["state"] == "failed"
    assert not FakeClient.instances
    ctx, _, _ = context(tmp_path, settings={"reasoning_effort": "ultra"})
    outcome = await backend(tmp_path, []).run(ctx)
    assert outcome["state"] == "failed" and "reasoning effort" in outcome["error"]


async def test_provider_error_is_reported_as_failed(tmp_path):
    ctx, _, _ = context(tmp_path, permission="read_only")
    script = [init(), result(subtype="error_during_execution", is_error=True,
                             errors=["authentication_failed"], api_error_status=401)]
    outcome = await backend(tmp_path, script).run(ctx)
    assert outcome["state"] == "failed"
    assert "authentication_failed" in outcome["error"] and "HTTP 401" in outcome["error"]


async def test_discovery_lists_models_without_account_identifiers(tmp_path):
    info = {"pid": 1, "claude_code_version": "2.1.301",
            "account": {"email": "person@example.invalid", "organization": "Fixture Org",
                        "subscriptionType": "Claude Max", "apiProvider": "firstParty"},
            "models": [{"value": "opus", "displayName": "Opus 5.5", "resolvedModel": "claude-opus-5-5",
                        "supportedEffortLevels": ["low", "high", "turbo"]},
                       {"value": "default", "displayName": "Default"}, "invalid"]}
    discovered = await backend(tmp_path, [], info).discover()
    assert discovered["available"] is True and discovered["personal_use_only"] is True
    assert [model["id"] for model in discovered["models"]] == ["opus", "default"]
    assert discovered["models"][0]["supported_reasoning_efforts"] == ["low", "high"]
    assert discovered["models"][1]["default"] is True
    assert discovered["account"] == {"connected": True, "subscription": "Claude Max",
                                     "api_provider": "firstParty"}
    serialized = json.dumps(discovered)
    assert "person@example.invalid" not in serialized and "Fixture Org" not in serialized
    assert FakeClient.instances[0].options.tools == []
    assert FakeClient.instances[0].disconnected


async def test_missing_sdk_is_reported_without_starting_a_process(tmp_path, monkeypatch):
    def missing():
        raise claude_code.ClaudeCodeUnavailable("Claude Code support is not installed")
    monkeypatch.setattr(claude_code, "sdk", missing)
    discovered = await ClaudeCodeBackend(runtime(tmp_path)).discover()
    assert discovered["available"] is False and discovered["error_code"] == "CLAUDE_CODE_MISSING"
    with pytest.raises(KiraError) as error:
        claude_code.require_sdk()
    assert error.value.code == "UNAVAILABLE"


PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture pixels").decode("ascii")


async def test_browser_and_computer_tools_deliver_screenshots_as_mcp_images(tmp_path, monkeypatch):
    from kira.domain.computer import computer_tool_specs
    from kira.domain.web import browser_tool_specs
    monkeypatch.setattr(sdk, "create_sdk_mcp_server",
                        lambda name, version, tools: {"type": "sdk", "name": name, "tools": tools})
    calls, images = [], []

    async def client_tool_call(call_id, name, arguments):
        calls.append((call_id, name, arguments))
        if name == "browser.screenshot":
            return {"ok": True, "browser_image_id": "shot-1", "screenshot": {"id": "shot-1", "bytes": 24}}
        if name == "computer.observe":
            return {"ok": True, "observation_id": "obs-1", "browser_image_id": "shot-2"}
        if name == "computer.act":
            return {"ok": False, "error": {"code": "NO_PAGE", "message": "No page is open"}}
        return {"ok": True, "url": arguments.get("url")}

    async def tool_image(image_id):
        images.append(image_id)
        if image_id == "shot-2":
            raise KiraError("INVALID_ATTACHMENT", "Screenshot does not belong to this active run")
        return {"type": "input_image", "image_url": claude_code.IMAGE_PREFIX + PNG_B64, "detail": "auto"}

    ctx, events, _ = context(tmp_path, permission="read_only",
                             client_tools=[*browser_tool_specs(), *computer_tool_specs()],
                             client_tool_call=client_tool_call, tool_image=tool_image)
    replies = {}

    async def use_tools(client):
        tools = {tool.name: tool for tool in client.options.mcp_servers["kira"]["tools"]}
        assert {"browser__navigate", "browser__screenshot", "computer__observe", "computer__act"} <= set(tools)
        replies["navigate"] = await tools["browser__navigate"].handler({"url": "https://example.test"})
        replies["screenshot"] = await tools["browser__screenshot"].handler({})
        replies["observe"] = await tools["computer__observe"].handler({})
        replies["act"] = await tools["computer__act"].handler({"action": "click", "x": 1, "y": 2})
        return None

    script = [init(servers=[{"name": "kira", "status": "connected"}]), use_tools,
              sdk.AssistantMessage([sdk.TextBlock("Fatto")], "claude-sonnet-5-5", message_id="m2"),
              result()]
    outcome = await backend(tmp_path, script).run(ctx)
    assert outcome["state"] == "completed"
    options = FakeClient.instances[0].options
    assert {"mcp__kira__browser__screenshot", "mcp__kira__computer__observe"} <= set(options.allowed_tools)
    assert FakeClient.instances[0].queries[0].startswith(claude_code.BROWSER_GUIDANCE)
    assert [name for _, name, _ in calls] == ["browser.navigate", "browser.screenshot",
                                              "computer.observe", "computer.act"]
    navigate = replies["navigate"]
    assert navigate["is_error"] is False and len(navigate["content"]) == 1
    assert json.loads(navigate["content"][0]["text"]) == {"ok": True, "url": "https://example.test"}
    shot = replies["screenshot"]
    assert shot["is_error"] is False and len(shot["content"]) == 2
    assert json.loads(shot["content"][0]["text"])["browser_image_id"] == "shot-1"
    assert shot["content"][1] == {"type": "image", "data": PNG_B64, "mimeType": "image/png"}
    # A screenshot whose pixels cannot be delivered is reported, not invented;
    # the run itself keeps going.
    observe = replies["observe"]
    assert observe["is_error"] is True and "delivery failed" in observe["content"][1]["text"]
    assert json.loads(observe["content"][0]["text"])["observation_id"] == "obs-1"
    assert all(block["type"] == "text" for block in replies["act"]["content"])
    assert images == ["shot-1", "shot-2"]
    assert not any(kind == "tool.started" for kind, _ in events)


async def test_image_attachments_travel_in_one_streamed_user_message(tmp_path):
    part = {"type": "input_image", "image_url": claude_code.IMAGE_PREFIX + PNG_B64, "detail": "auto"}
    original = dict(part)
    ctx, _, _ = context(tmp_path, image_inputs={"fixture-run": [part], "older-run": [{"type": "input_image"}]})
    script = [init(), sdk.AssistantMessage([sdk.TextBlock("Vedo")], "claude-sonnet-5-5", message_id="m1"),
              result()]
    outcome = await backend(tmp_path, script).run(ctx)
    assert outcome["state"] == "completed"
    streamed = FakeClient.instances[0].queries[0]
    assert not isinstance(streamed, str)
    messages = [message async for message in streamed]
    assert len(messages) == 1
    assert messages[0]["type"] == "user" and messages[0]["parent_tool_use_id"] is None
    content = messages[0]["message"]["content"]
    assert content == [
        {"type": "text", "text": "Kira-supplied project context:\nfixture supplied context\n\nTask:\nChange the fixture"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}}]
    assert part == original
    for parts in ([{"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA", "detail": "auto"}],
                  [{"type": "input_image", "image_url": claude_code.IMAGE_PREFIX + "not base64!", "detail": "auto"}],
                  [part] * 5):
        FakeClient.instances.clear()
        ctx, _, _ = context(tmp_path, image_inputs={"fixture-run": parts})
        assert (await backend(tmp_path, []).run(ctx))["state"] == "failed"
        assert not FakeClient.instances
