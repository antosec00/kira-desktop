"""Deterministic App Server fixtures. Live account evidence is reported separately."""

import asyncio
import json
import shlex
import stat
import sys
from pathlib import Path

import pytest

from kira.backends import codex
from kira.domain.contracts import RunContext


def context(tmp_path, permission="project_write"):
    events = []
    approvals = []
    executions = []
    async def emit(kind, payload):
        events.append((kind, payload))
    async def execute(name, args):
        executions.append((name, args))
        raise AssertionError("Codex tools must never execute through Kira")
    async def approve(request):
        approvals.append(request)
        return {"decision": "accept"}
    ctx = RunContext("fixture-run", {"root": str(tmp_path), "permission": permission},
        {"model": "fixture-model", "settings": {"reasoning_effort": "ultra"}},
        "Read and change fixture", "fixture supplied context", emit, execute, approve,
        asyncio.Event())
    return ctx, events, approvals, executions


class FakeServer:
    instances = []
    queued = []
    resolved_model = "fixture-model"
    resolved_effort = "ultra"
    def __init__(self, binary):
        self.notifications = asyncio.Queue()
        self.requests = []
        self.responses = []
        self.closed = False
        self.instances.append(self)
    async def start(self, *, experimental_api=True):  # Same signature as codex.AppServer.
        pass
    async def close(self):
        self.closed = True
    async def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        if method == "thread/goal/get":
            return {"goal": None}
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "thread-fixture"}, "model": self.resolved_model,
                    "reasoningEffort": self.resolved_effort, "sandbox": {"type": {"read-only": "readOnly", "workspace-write": "workspaceWrite",
                    "danger-full-access": "dangerFullAccess"}[params["sandbox"]]}}
        if method == "turn/start":
            for event in self.queued:
                await self.notifications.put(event)
            return {"turn": {"id": "turn-fixture", "status": "inProgress"}}
        if method == "turn/interrupt":
            await self.notifications.put(terminal("interrupted"))
            await asyncio.sleep(0)  # Notification can precede interrupt RPC acknowledgement.
            return {}
        raise AssertionError(method)
    async def respond(self, request_id, result):
        self.responses.append((request_id, result))
    async def send(self, value):
        self.responses.append(value)


def event(method, **params):
    return {"method": method, "params": {"threadId": "thread-fixture",
            "turnId": "turn-fixture", **params}}


def terminal(status):
    return event("turn/completed", turn={"id": "turn-fixture", "status": status,
                                        "error": {"message": "fixture failure"}})


@pytest.fixture
def fake(monkeypatch):
    # FakeServer subclasses in other modules inherit this class state, so every
    # change goes through monkeypatch and is restored when the test ends.
    for name, value in {"instances": [], "queued": [], "resolved_model": "fixture-model",
                        "resolved_effort": "ultra"}.items():
        monkeypatch.setattr(FakeServer, name, value)
    monkeypatch.setattr(codex, "AppServer", FakeServer)
    return FakeServer


async def test_run_observes_native_tools_once_preserves_policy_and_writes_evidence(tmp_path, fake):
    output = tmp_path / "result.txt"
    output.write_text("fixture artifact")
    command = {"id": "tool-1", "type": "commandExecution", "command": "fixture read",
               "cwd": str(tmp_path), "status": "inProgress"}
    done = {**command, "status": "completed", "exitCode": 0, "aggregatedOutput": "fixture output"}
    patch = {"id": "tool-2", "type": "fileChange", "status": "completed",
             "changes": [{"path": str(output), "kind": {"type": "add"}}]}
    fake.queued = [event("item/started", item=command), event("item/started", item=command),
        event("item/completed", item=done), event("item/completed", item=done),
        event("item/completed", item=patch),
        event("item/agentMessage/delta", itemId="answer", delta="Verified"),
        event("item/completed", item={"id": "answer", "type": "agentMessage", "text": "Verified"}),
        event("thread/tokenUsage/updated", tokenUsage={"last": {"inputTokens": 12,
              "outputTokens": 3}, "total": {"totalTokens": 15}, "modelContextWindow": 32000}),
        terminal("completed")]
    ctx, events, _, executions = context(tmp_path)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "completed"
    assert executions == []
    assert sum(k == "tool.started" for k, _ in events) == 1
    assert sum(k == "tool.completed" for k, _ in events) == 2
    assert sum(k == "message.delta" for k, _ in events) == 1
    artifact = next(p for k, p in events if k == "artifact.created")
    assert artifact["sha256"] and Path(artifact["path"]).read_text() == "fixture artifact"
    calls = dict(fake.instances[0].requests)
    assert calls["thread/start"]["model"] == "fixture-model"
    assert calls["thread/start"]["config"]["model_reasoning_effort"] == "ultra"
    assert calls["turn/start"]["effort"] == "ultra"
    assert calls["turn/start"]["sandboxPolicy"]["writableRoots"] == [str(tmp_path)]
    assert calls["turn/start"]["sandboxPolicy"]["excludeSlashTmp"]
    assert calls["turn/start"]["approvalPolicy"] == "never"
    assert ctx.supplied_context in calls["turn/start"]["input"][0]["text"]
    assert fake.instances[0].closed


@pytest.mark.parametrize("status,expected", [("failed", "failed"), ("interrupted", "interrupted")])
async def test_terminal_states_are_not_false_success(tmp_path, fake, status, expected):
    fake.queued = [terminal(status)]
    ctx, _, _, _ = context(tmp_path)
    assert (await codex.CodexBackend().run(ctx))["state"] == expected


async def test_disconnect_after_turn_start_is_uncertain(tmp_path, fake):
    fake.queued = [{"method": "kira/disconnected", "params": {"error": "fixture EOF"}}]
    ctx, _, _, _ = context(tmp_path)
    assert (await codex.CodexBackend().run(ctx))["state"] == "interrupted"


@pytest.mark.parametrize("field,value", [("resolved_model", "other"), ("resolved_effort", "low")])
async def test_no_silent_model_or_effort_change(tmp_path, fake, monkeypatch, field, value):
    monkeypatch.setattr(fake, field, value)
    ctx, _, _, _ = context(tmp_path)
    result = await codex.CodexBackend().run(ctx)
    assert result["state"] == "failed"
    assert not any(m == "turn/start" for m, _ in fake.instances[0].requests)


async def test_resume_uses_backend_ref_and_same_configuration(tmp_path, fake):
    fake.queued = [terminal("completed")]
    ctx, _, _, _ = context(tmp_path)
    ctx.session["backend_ref"] = "thread-fixture"
    assert (await codex.CodexBackend().run(ctx))["state"] == "completed"
    assert fake.instances[0].requests[0] == ("thread/goal/get", {"threadId": "thread-fixture"})
    assert fake.instances[0].requests[1][0] == "thread/resume"
    assert fake.instances[0].requests[1][1]["threadId"] == "thread-fixture"


async def test_cancellation_requires_codex_terminal_confirmation(tmp_path, fake):
    ctx, _, _, _ = context(tmp_path)
    task = asyncio.create_task(codex.CodexBackend().run(ctx))
    async with asyncio.timeout(10):
        while not fake.instances or not any(m == "turn/start"
                                            for m, _ in fake.instances[0].requests):
            assert not task.done(), f"The run ended before turn/start: {task.result()}"
            await asyncio.sleep(0)
    ctx.cancel.set()
    result = await asyncio.wait_for(task, 2)
    assert result["state"] == "cancelled"
    assert result["outcome"]["interruption_confirmed"]
    assert any(m == "turn/interrupt" for m, _ in fake.instances[0].requests)


@pytest.mark.parametrize("permission,decision", [("read_only", "decline"),
                  ("project_write", "decline"), ("trusted_host", "accept")])
async def test_approval_cannot_enlarge_project_permissions(tmp_path, fake, permission, decision):
    request = event("item/commandExecution/requestApproval", command="fixture command")
    request["id"] = 98
    fake.queued = [request, terminal("completed")]
    ctx, _, approvals, _ = context(tmp_path, permission)
    await codex.CodexBackend().run(ctx)
    assert fake.instances[0].responses == [(98, {"decision": decision})]
    assert bool(approvals) == (permission == "trusted_host")


async def test_protocol_drains_large_stderr_and_correlates_out_of_order_responses(tmp_path):
    script = tmp_path / "fake-app-server"
    script.write_text('''#!/usr/bin/env python3
import json, sys
pending = []
for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        sys.stderr.write("x" * 1048576)
        sys.stderr.flush()
        print(json.dumps({"id": req["id"], "result": {"fixture": True}}), flush=True)
    elif req.get("method") == "echo":
        pending.append(req)
        if len(pending) == 2:
            for entry in reversed(pending):
                print(json.dumps({"id": entry["id"], "result": entry["params"]}), flush=True)
''')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    rpc = codex.AppServer(str(script))
    try:
        await asyncio.wait_for(rpc.start(), 30)
        a, b = await asyncio.gather(rpc.request("echo", {"n": 1}), rpc.request("echo", {"n": 2}))
        assert (a, b) == ({"n": 1}, {"n": 2})
        assert rpc.stderr_bytes == 1048576
    finally:
        await rpc.close()
    assert rpc.process.returncode is not None


async def test_malformed_stdout_unblocks_pending_request(tmp_path):
    script = tmp_path / "fake-malformed-app-server"
    script.write_text('''#!/usr/bin/env python3
import sys
sys.stdin.readline()
print("{malformed}", flush=True)
''')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    rpc = codex.AppServer(str(script))
    try:
        with pytest.raises(codex.ProtocolError):
            await asyncio.wait_for(rpc.start(), 30)
    finally:
        await rpc.close()


def test_child_environment_drops_unrelated_credentials(monkeypatch):
    monkeypatch.setenv("KIRA_FAKE_CREDENTIAL", "canary_not_real")
    assert "KIRA_FAKE_CREDENTIAL" not in codex.child_environment()


def test_policy_values_match_generated_official_schema(tmp_path):
    schema_root = Path(__file__).resolve().parents[1] / "protocol-schema/codex-app-server-0.162.1"
    definitions = json.loads((schema_root / "v2/TurnStartParams.json").read_text())["definitions"]
    supported = {entry["properties"]["type"]["enum"][0]
                 for entry in definitions["SandboxPolicy"]["oneOf"]}
    for permission in ("read_only", "project_write", "trusted_host"):
        ctx, _, _, _ = context(tmp_path, permission)
        _, policy = codex.permission_overlay(ctx)
        assert policy["type"] in supported


async def test_cancel_during_approval_replies_cancel_and_confirms_interrupt(tmp_path, fake):
    request = event("item/commandExecution/requestApproval", command="fixture approval")
    request["id"] = 88
    fake.queued = [request]
    ctx, _, _, _ = context(tmp_path, "trusted_host")
    waiting = asyncio.Event()
    async def approve(_):
        waiting.set()
        await asyncio.Event().wait()
    ctx.approve = approve
    task = asyncio.create_task(codex.CodexBackend().run(ctx))
    await asyncio.wait_for(waiting.wait(), 2)
    ctx.cancel.set()
    assert (await asyncio.wait_for(task, 2))["state"] == "cancelled"
    assert fake.instances[0].responses == [(88, {"decision": "cancel"})]


async def test_login_wait_is_correlated_and_never_returns_auth_payload(tmp_path, fake):
    rpc = fake("fixture")
    backend = codex.CodexBackend()
    backend._control = rpc
    backend._login_id = "expected-login"
    await rpc.notifications.put({"method": "account/login/completed", "params": {
        "loginId": "unrelated-login", "success": True}})
    await rpc.notifications.put({"method": "account/login/completed", "params": {
        "loginId": "expected-login", "success": True, "fakeToken": "fixture-canary"}})
    assert await backend.wait_login(timeout=1) == {"success": True}
    assert not rpc.closed


async def test_absolute_wrapper_finds_sibling_interpreter_with_desktop_path(tmp_path, monkeypatch):
    binary_dir = tmp_path / "configured-bin"
    binary_dir.mkdir()
    interpreter = binary_dir / "fixture-node"
    # The sibling stands in for node; it runs the current interpreter, not a host one.
    interpreter.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    interpreter.chmod(0o700)
    wrapper = binary_dir / "codex"
    wrapper.write_text('''#!/usr/bin/env fixture-node
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get("method") == "initialize":
        print(json.dumps({"id": request["id"], "result": {"fixture": True}}), flush=True)
''')
    wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    environment = codex.child_environment(str(wrapper))
    assert environment["PATH"] == f"{binary_dir}:/usr/bin:/bin"
    assert codex.child_environment("codex")["PATH"] == "/usr/bin:/bin"
    rpc = codex.AppServer(str(wrapper))
    try:
        await asyncio.wait_for(rpc.start(), 30)
        assert rpc.initialize_result == {"fixture": True}
    finally:
        await rpc.close()


async def test_explicit_model_change_resumes_same_thread_with_unchanged_permissions(tmp_path, fake, monkeypatch):
    fake.queued = [terminal('completed')]
    ctx, events, _, _ = context(tmp_path)
    assert (await codex.CodexBackend().run(ctx))['state'] == 'completed'
    reference = next(payload['backend_ref'] for kind, payload in events if kind == 'session.updated')
    ctx.session['backend_ref'] = reference
    ctx.session['model'] = 'fixture-other-model'
    ctx.session['settings']['reasoning_effort'] = 'high'
    monkeypatch.setattr(fake, 'resolved_model', 'fixture-other-model')
    monkeypatch.setattr(fake, 'resolved_effort', 'high')
    assert (await codex.CodexBackend().run(ctx))['state'] == 'completed'
    first, second = (dict(server.requests) for server in fake.instances)
    assert 'thread/start' not in second
    assert second['thread/resume']['threadId'] == reference
    assert second['turn/start']['threadId'] == reference
    for method in ('thread/resume', 'turn/start'):
        assert second[method]['model'] == 'fixture-other-model'
    assert second['thread/resume']['config']['model_reasoning_effort'] == 'high'
    assert second['turn/start']['effort'] == 'high'
    assert first['turn/start']['sandboxPolicy'] == second['turn/start']['sandboxPolicy']
    assert first['turn/start']['approvalPolicy'] == second['turn/start']['approvalPolicy']
    assert 'access_programs' not in second['thread/resume']['config']


async def test_resumed_thread_rejects_provider_model_substitution(tmp_path, fake, monkeypatch):
    ctx, _, _, _ = context(tmp_path)
    ctx.session['backend_ref'] = 'thread-fixture'
    monkeypatch.setattr(fake, 'resolved_model', 'unrequested-model')
    assert (await codex.CodexBackend().run(ctx))['state'] == 'failed'
    assert not any(method == 'turn/start' for method, _ in fake.instances[0].requests)
    assert ctx.session['backend_ref'] == 'thread-fixture'
