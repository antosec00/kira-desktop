"""Synthetic permission contracts across providers; no live account calls."""

import copy
import hashlib
import json

import httpx
import pytest

from kira.backends import codex
from kira.backends.ollama import OllamaBackend, TOOL_CALL_GUIDANCE, TOOL_SCHEMAS, local_tool_schemas
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.contracts import KiraError
from test_backends_codex import FakeServer, context as codex_context, event, terminal
from test_backends_ollama import ChunkStream, context as ollama_context, install_transport
from test_backends_openai_chatgpt import (
    FakeAuth,
    call,
    context as direct_context,
    terminal as direct_terminal,
    wire,
)
from test_backends_workbench import WorkbenchServer, compaction_events, context as native_context


@pytest.mark.parametrize("permission,expected", [
    ("read_only", {"list_files", "search_files", "read_file"}),
    ("project_write", {"list_files", "search_files", "read_file", "write_file"}),
    ("trusted_host", {"list_files", "search_files", "read_file", "write_file", "run_command"}),
    ("full_access", {"list_files", "search_files", "read_file", "write_file", "run_command"}),
    ("unknown", {"list_files", "search_files", "read_file"}),
])
def test_file_tool_catalog_uses_explicit_effective_permissions(permission, expected):
    before = copy.deepcopy(TOOL_SCHEMAS)
    schemas = local_tool_schemas(permission)
    assert {tool["function"]["name"] for tool in schemas} == expected
    file_tools = [tool for tool in schemas if tool["function"]["name"] != "run_command"]
    if permission == "full_access":
        assert all("external paths allowed as current OS user" in tool["function"]["description"]
                   for tool in file_tools)
    else:
        assert all("Project paths only." in tool["function"]["description"]
                   for tool in file_tools)
    assert TOOL_SCHEMAS == before


@pytest.mark.parametrize("permission", ["trusted_host", "full_access"])
async def test_direct_host_tools_preserve_model_and_send_real_service_calls(tmp_path, permission):
    ctx, _, executed = direct_context(tmp_path, permission)
    ctx.assistant_instructions = "Kira service identity fixture"
    external = str(tmp_path.parent / "outside-fixture.txt")
    requested = [call("write_file", {"path": external, "content": "fixture",
                                    "expected_sha256": None}),
                 call("run_command", {"argv": ["fixture-command"]}, call_id="call-2")]
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["model"] == ctx.model
        assert body["reasoning"]["effort"] == "xhigh"
        assert ctx.assistant_instructions in body["instructions"]
        file_tools = next(namespace["tools"] for namespace in body["tools"]
                          if namespace.get("name") == "files")
        assert {"write_file", "run_command"} <= {tool["name"] for tool in file_tools}
        scope = next(tool["description"] for tool in file_tools if tool["name"] == "write_file")
        assert ("external paths allowed as current OS user" in scope) == (permission == "full_access")
        return wire([direct_terminal(requested if len(bodies) == 1 else None)])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert executed == [("write_file", {"path": external, "content": "fixture",
                                            "expected_sha256": None}),
                            ("run_command", {"argv": ["fixture-command"]})]
    finally:
        await backend.close()


@pytest.mark.parametrize("permission", ["trusted_host", "full_access"])
async def test_ollama_host_tools_are_verified_and_keep_path_scope(tmp_path, permission):
    ctx, _, executed = ollama_context(tmp_path, permission=permission)
    ctx.assistant_instructions = "Kira service identity fixture"
    external = str(tmp_path.parent / "outside-fixture.txt")
    bodies = []

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        body = json.loads(request.content)
        if body.get("tools", [{}])[0].get("function", {}).get("name") == "kira_probe":
            token = body["messages"][0]["content"].split("value ")[1].split(".")[0]
            return httpx.Response(200, stream=ChunkStream([
                {"message": {"tool_calls": [{"function": {"name": "kira_probe",
                              "arguments": {"value": token}}}]}, "done": True}]))
        bodies.append(body)
        assert body["model"] == ctx.model
        # Verified tools add Ollama's tool-call guidance to the system instructions.
        assert body["messages"][0] == {"role": "system", "content": ctx.assistant_instructions + TOOL_CALL_GUIDANCE}
        schemas = {tool["function"]["name"]: tool["function"] for tool in body["tools"]}
        assert {"write_file", "run_command"} <= schemas.keys()
        scope = schemas["write_file"]["description"]
        assert ("external paths allowed as current OS user" in scope) == (permission == "full_access")
        if len(bodies) == 1:
            return httpx.Response(200, stream=ChunkStream([{
                "message": {"tool_calls": [
                    {"function": {"name": "write_file", "arguments": {
                        "path": external, "content": "fixture", "expected_sha256": None}}},
                    {"function": {"name": "run_command", "arguments": {
                        "argv": ["fixture-command"]}}}]}, "done": True}]))
        return httpx.Response(200, stream=ChunkStream([
            {"message": {"content": "Fixture complete"}, "done": True}]))

    backend = OllamaBackend()
    await install_transport(backend, handler)
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed" and result["outcome"]["tools_verified"]
        assert executed == [("write_file", {"path": external, "content": "fixture",
                                            "expected_sha256": None}),
                            ("run_command", {"argv": ["fixture-command"]})]
    finally:
        await backend.close()


@pytest.mark.parametrize("resume", [False, True])
async def test_codex_full_access_preserves_exact_boundary_and_model(tmp_path, monkeypatch, resume):
    class NetworkServer(FakeServer):
        instances = []
        queued = [terminal("completed")]
        resolved_model = "fixture-model"
        resolved_effort = "ultra"

        async def request(self, method, params, timeout=30):
            result = await super().request(method, params, timeout)
            if method in {"thread/start", "thread/resume"}:
                result["sandbox"]["networkAccess"] = True
            return result

    monkeypatch.setattr(codex, "AppServer", NetworkServer)
    ctx, _, _, executed = codex_context(tmp_path, "full_access")
    if resume:
        ctx.session["backend_ref"] = "thread-fixture"
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    requests = dict(NetworkServer.instances[-1].requests)
    start = requests["thread/resume" if resume else "thread/start"]
    assert start["sandbox"] == "danger-full-access"
    assert start["cwd"] == str(tmp_path) and start["model"] == "fixture-model"
    assert start["config"]["model_reasoning_effort"] == "ultra"
    assert requests["turn/start"]["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert requests["turn/start"]["effort"] == "ultra" and not executed


@pytest.mark.parametrize("mode", ["plan", "review", "recap"])
async def test_codex_restrictive_modes_cannot_inherit_full_access(tmp_path, monkeypatch, mode):
    class Server(FakeServer):
        instances = []

    monkeypatch.setattr(codex, "AppServer", Server)
    ctx, _, _, _ = codex_context(tmp_path, "full_access")
    ctx.mode = mode
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed" and "read-only" in result["error"]
    assert not Server.instances[-1].requests


@pytest.mark.parametrize("method", ["item/commandExecution/requestApproval",
                                    "item/fileChange/requestApproval"])
@pytest.mark.parametrize("decision,expected", [("accept", "accept"),
                                               ("acceptForSession", "decline")])
async def test_codex_full_access_approval_does_not_accept_persistent_amendments(
        tmp_path, monkeypatch, method, decision, expected):
    request = event(method, command="fixture command")
    request["id"] = 98

    class Server(FakeServer):
        instances = []
        queued = [request, terminal("completed")]
        # Pinned here rather than inherited from FakeServer's shared class state.
        resolved_model = "fixture-model"
        resolved_effort = "ultra"

    monkeypatch.setattr(codex, "AppServer", Server)
    ctx, _, _, _ = codex_context(tmp_path, "full_access")
    approvals = []

    async def approve(value):
        approvals.append(value)
        return {"decision": decision}

    ctx.approve = approve
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    assert len(approvals) == 1
    assert Server.instances[-1].responses == [(98, {"decision": expected})]


@pytest.mark.parametrize("operation", ["fork", "compact"])
async def test_codex_thread_management_preserves_full_access(tmp_path, monkeypatch, operation):
    class Server(WorkbenchServer):
        instances = []
        compact_events = compaction_events()

        async def request(self, method, params, timeout=30):
            result = await super().request(method, params, timeout)
            if method in {"thread/fork", "thread/resume"}:
                result["sandbox"]["networkAccess"] = True
            return result

    monkeypatch.setattr(codex, "AppServer", Server)
    ctx, _ = native_context(tmp_path, permission="full_access")
    before = copy.deepcopy(ctx.session)
    result = await codex.CodexBackend().manage_thread(operation, ctx.session, ctx.project, {})
    expected = "fork-thread" if operation == "fork" else "source-thread"
    assert result["backend_ref"] == expected
    request = dict(Server.instances[-1].requests)["thread/fork" if operation == "fork"
                                                else "thread/resume"]
    assert request["sandbox"] == "danger-full-access" and request["model"] == ctx.model
    assert request["config"]["model_reasoning_effort"] == "ultra"
    assert request["serviceTier"] == "priority" and ctx.session == before


@pytest.mark.parametrize("operation", ["fork", "compact"])
async def test_codex_full_access_mismatched_boundary_still_rejected(tmp_path, monkeypatch, operation):
    class Server(WorkbenchServer):
        instances = []

        async def request(self, method, params, timeout=30):
            result = await super().request(method, params, timeout)
            if method in {"thread/fork", "thread/resume"}:
                result["sandbox"] = {"type": "workspaceWrite", "networkAccess": False}
            return result

    monkeypatch.setattr(codex, "AppServer", Server)
    ctx, _ = native_context(tmp_path, permission="full_access")
    with pytest.raises(KiraError, match="different permission boundary"):
        await codex.CodexBackend().manage_thread(operation, ctx.session, ctx.project, {})
    assert not any(method == "thread/compact/start" for method, _ in Server.instances[-1].requests)


@pytest.mark.parametrize("permission,published", [("read_only", False), ("project_write", False),
                                                 ("trusted_host", False), ("full_access", True)])
async def test_codex_records_observed_external_file_only_in_full_access(tmp_path, permission, published):
    root = tmp_path / "project"
    root.mkdir()
    external = tmp_path / "outside.txt"
    external.write_text("fixture artifact")
    ctx, events, _, executed = codex_context(root, permission)
    await codex.CodexBackend()._artifact(ctx, str(external), "native-file-change")
    assert bool(events) is published
    if published:
        assert events == [("artifact.created", {
            "path": str(external), "kind": "file", "tool_id": "native-file-change",
            "sha256": hashlib.sha256(external.read_bytes()).hexdigest(), "source": "codex"})]
    assert not executed
