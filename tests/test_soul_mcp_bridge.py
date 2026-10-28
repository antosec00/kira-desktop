"""Real local relay coverage; callbacks are synthetic and never call a provider."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from kira.backends import codex, kira_mcp
from kira.backends.client_tools import ClientToolError, validate_tools
from kira.domain.assistant import ASSISTANT_TOOL_NAMES
from kira.domain.canvas import CANVAS_TOOL_NAMES, canvas_tool_specs
from kira.domain.code_index import CODE_INDEX_TOOL_NAMES, code_index_tool_specs
from kira.domain.knowledge import KNOWLEDGE_TOOL_NAMES, knowledge_tool_specs
from kira.domain.mcp_servers import MCP_TOOL_NAMES, mcp_tool_specs
from kira.domain.project_tools import PROJECT_TOOL_NAMES, project_tool_specs
from kira.domain.research import RESEARCH_TOOL_NAMES, research_tool_specs
from kira.domain.souls import SOUL_TOOL_NAMES, soul_tool_specs
from kira.domain.studio import STUDIO_TOOL_NAMES, studio_tool_specs
from kira.domain.teams import mailbox_tool_specs
from test_backends_assistant import AssistantServer, context


ALL_KIRA_TOOLS = (ASSISTANT_TOOL_NAMES | RESEARCH_TOOL_NAMES | KNOWLEDGE_TOOL_NAMES
                  | PROJECT_TOOL_NAMES | SOUL_TOOL_NAMES | STUDIO_TOOL_NAMES | MCP_TOOL_NAMES
                  | CODE_INDEX_TOOL_NAMES | CANVAS_TOOL_NAMES)
PROPOSAL_ARGUMENTS = {
    "statement": "Prefer a concise result before the supporting details.",
    "applicability": "When reporting the result of a completed task.",
    "source_run_id": "fixture-source-run",
    "source_seq": 2,
    "kind": "preference",
    "scope": "chat",
    "key": "detail",
    "value": "Result first, followed by useful evidence.",
}
PENDING_RESULT = {
    "ok": True,
    "proposal": {"id": "fixture-proposal", "status": "pending"},
    "applied": False,
    "review_required": True,
}


def soul_context(tmp_path, *, full=False):
    ctx, calls, events = context(tmp_path)
    if full:
        ctx.client_tools[0]["tools"] += (research_tool_specs() + knowledge_tool_specs()
                                         + project_tool_specs() + soul_tool_specs()
                                         + studio_tool_specs() + mcp_tool_specs()
                                         + code_index_tool_specs() + canvas_tool_specs())
    else:
        ctx.client_tools[0]["tools"] = soul_tool_specs()

    async def callback(*args):
        calls.append(copy.deepcopy(args))
        return copy.deepcopy(PENDING_RESULT)

    ctx.client_tool_call = callback
    return ctx, calls, events


def test_all_kira_families_register_together_including_profile_learning(tmp_path):
    ctx, calls, _ = soul_context(tmp_path, full=True)
    assert validate_tools(ctx) == ALL_KIRA_TOOLS
    assert len(ctx.client_tools[0]["tools"]) == len(ALL_KIRA_TOOLS)
    assert not calls


@pytest.mark.parametrize("name", ["soul_feedback", "soul_archive", "soul_edit", "arbitrary_tool"])
def test_shared_validator_does_not_expand_to_unregistered_names(tmp_path, name):
    ctx, _, _ = soul_context(tmp_path, full=True)
    # Replace instead of append so rejection proves the allowlist, not the count bound.
    ctx.client_tools[0]["tools"][-1]["name"] = name
    with pytest.raises(ClientToolError, match="unsupported Kira client-tool definition"):
        validate_tools(ctx)
    with pytest.raises(ClientToolError, match="registered Kira tools"):
        kira_mcp.KiraMcpRelay(ctx, {"kira." + name})


def test_shared_validator_still_rejects_duplicate_and_overbound_definitions(tmp_path):
    ctx, _, _ = soul_context(tmp_path, full=True)
    ctx.client_tools[0]["tools"].append(copy.deepcopy(soul_tool_specs()[0]))
    with pytest.raises(ClientToolError, match="bounded set"):
        validate_tools(ctx)
    ctx.client_tools[0]["tools"].pop(0)
    with pytest.raises(ClientToolError, match="unsupported Kira client-tool definition"):
        validate_tools(ctx)


def test_profile_learning_does_not_mix_managed_team_and_chat_scopes(tmp_path):
    ctx, _, _ = soul_context(tmp_path)
    ctx.client_tools += mailbox_tool_specs()
    with pytest.raises(ClientToolError, match="cannot share the same scope"):
        validate_tools(ctx)


@pytest.mark.parametrize("name", ["soul_feedback", "soul_archive", "soul_edit"])
async def test_private_descriptor_rejects_unregistered_profile_operations(tmp_path, name):
    ctx, _, _ = soul_context(tmp_path)
    relay = kira_mcp.KiraMcpRelay(ctx, validate_tools(ctx))
    try:
        await relay.start()
        path = relay.directory / "bridge.json"
        descriptor = kira_mcp._descriptor(path)
        assert descriptor["tools"] == sorted(SOUL_TOOL_NAMES)
        descriptor["tools"].append("kira." + name)
        path.write_text(json.dumps(descriptor))
        with pytest.raises(ValueError, match="Invalid descriptor"):
            kira_mcp._descriptor(path)
    finally:
        await relay.close()


async def test_real_stdio_lists_exact_schema_and_delivers_one_pending_proposal(tmp_path):
    ctx, calls, _ = soul_context(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def callback(*args):
        calls.append(copy.deepcopy(args))
        entered.set()
        await release.wait()
        return copy.deepcopy(PENDING_RESULT)

    ctx.client_tool_call = callback
    relay = kira_mcp.KiraMcpRelay(ctx, validate_tools(ctx))
    process = None
    pending = None
    try:
        overlay = await relay.start()
        descriptor = kira_mcp._descriptor(relay.directory / "bridge.json")
        assert descriptor["tools"] == ["kira.soul_propose"]
        assert overlay["enabled_tools"] == ["soul_propose"]
        assert overlay["tools"] == {"soul_propose": {"approval_mode": "approve"}}
        process = await asyncio.create_subprocess_exec(
            overlay["command"], *overlay["args"], stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        async def request(request_id, method, params):
            process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id,
                                            "method": method, "params": params}) + "\n").encode())
            await process.stdin.drain()
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
            assert response["id"] == request_id and "error" not in response
            return response["result"]

        initialized = await request(1, "initialize", {"protocolVersion": "2025-06-18"})
        assert initialized["serverInfo"]["name"] == "Kira Desktop" and relay.ready
        listed = await request(2, "tools/list", {})
        assert listed["tools"] == [{key: value for key, value in soul_tool_specs()[0].items()
                                    if key != "type"}]
        arguments = copy.deepcopy(PROPOSAL_ARGUMENTS)
        pending = asyncio.create_task(request(3, "tools/call", {
            "name": "soul_propose", "arguments": arguments}))
        await asyncio.wait_for(entered.wait(), 30)
        assert not pending.done()
        assert calls == [("mcp-3", "kira.soul_propose", arguments)]
        release.set()
        result = await pending
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"]) == PENDING_RESULT
        repeat = await request(3, "tools/call", {"name": "soul_propose", "arguments": arguments})
        assert repeat == result and len(calls) == 1
        changed = {**arguments, "statement": "A different suggestion with a reused request ID."}
        conflicting = await request(3, "tools/call", {"name": "soul_propose", "arguments": changed})
        assert conflicting["isError"] and len(calls) == 1
        for request_id, name in enumerate(("soul_feedback", "soul_archive", "soul_edit", "environment"), 4):
            rejected = await request(request_id, "tools/call", {"name": name, "arguments": {}})
            assert rejected["isError"] and len(calls) == 1
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0 and await process.stderr.read() == b""
    finally:
        release.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await relay.close()
    assert not relay.directory.exists() and not relay.tasks


@pytest.mark.parametrize("resume", [False, True])
async def test_codex_new_and_resumed_threads_attach_profile_tool_with_complete_family(
        tmp_path, monkeypatch, resume):
    ctx, calls, _ = soul_context(tmp_path, full=True)
    if resume:
        ctx.session["backend_ref"] = "fixture-thread"

    class SoulServer(AssistantServer):
        instances = []
        valid_tools = True

        async def request(self, method, params, timeout=30):
            if method != "turn/start":
                return await super().request(method, params, timeout)
            self.requests.append((method, copy.deepcopy(params)))
            result = await kira_mcp._relay_call(self.config, {
                "call_id": "fixture-soul-call", "name": "kira.soul_propose",
                "arguments": PROPOSAL_ARGUMENTS})
            assert not result["isError"]
            assert json.loads(result["content"][0]["text"]) == PENDING_RESULT
            await self.notifications.put({"method": "turn/completed", "params": {
                "threadId": "fixture-thread", "turn": {"id": "fixture-turn", "status": "completed"}}})
            return {"turn": {"id": "fixture-turn"}}

    monkeypatch.setattr(codex, "AppServer", SoulServer)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed"
    assert calls == [("fixture-soul-call", "kira.soul_propose", PROPOSAL_ARGUMENTS)]
    rpc = SoulServer.instances[0]
    requests = dict(rpc.requests)
    thread = requests["thread/resume" if resume else "thread/start"]
    overlay = thread["config"]["mcp_servers"]["kira"]
    assert set(rpc.config["tools"]) == ALL_KIRA_TOOLS
    assert set(overlay["enabled_tools"]) == {name.split(".", 1)[1] for name in ALL_KIRA_TOOLS}
    assert "dynamicTools" not in thread
    assert thread["model"] == "fixture-model"
    assert requests["turn/start"]["effort"] == "xhigh"
    assert requests["turn/start"]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert rpc.closed and not Path(overlay["args"][-1]).exists()
