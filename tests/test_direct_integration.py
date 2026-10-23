"""Real service/file/HTTP-adapter loop with synthetic Responses transport."""

import asyncio
import hashlib
import json
import secrets
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.paths import Paths
from kira.service.server import Service
from test_backends_openai_auth import IdentityServer, connected


class Account:
    async def status(self):
        return {"active_account_id": "fixture-account", "login": {"state": "idle"},
                "accounts": [{"id": "fixture-account", "label": "Fixture",
                              "connected": True, "plan_enabled": True}]}

    async def access_token(self, account_id):
        assert account_id == "fixture-account"
        return "synthetic-credential-not-an-api-key"

    async def close(self):
        pass


def call(name, arguments, number, namespace="files"):
    return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
            "namespace": namespace, "name": name, "arguments": json.dumps(arguments),
            "status": "completed"}


def message(text):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def completion(output):
    # Subscription streams may carry complete items before a terminal event
    # whose output array is empty. Effects still require that terminal event.
    events = [
        {"type": "response.created", "response": {"id": "resp_fixture", "status": "in_progress"}},
        *[{"type": "response.output_item.done", "output_index": index,
           "item": {"id": f"item_fixture_{index}", **item}}
          for index, item in enumerate(output)],
        {"type": "response.completed", "response": {"id": "resp_fixture", "status": "completed",
            "output": [], "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(event) + "\n\n" for event in events))


def context_metadata(request):
    """Model discovery and capability-only requests do not count as inference."""
    assert request.headers["authorization"] == "Bearer synthetic-credential-not-an-api-key"
    if request.method == "GET" and request.url.path == "/v1/models":
        return httpx.Response(200, json={"models": [
            {"slug": name, "display_name": name, "visibility": "list",
             "context_window": 32_000, "max_context_window": 128_000}
            for name in ("fixture-model", "fixture")]})
    if request.url.path == "/v1/responses/input_tokens":
        return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing"}})
    return None


def visible_text(item):
    content = item["content"]
    return content if isinstance(content, str) else "".join(part.get("text", "") for part in content)


def results(body):
    return [json.loads(item["output"]) for item in body["input"]
            if item.get("type") == "function_call_output"]


@pytest.mark.asyncio
async def test_http_adapter_executes_real_file_task_and_preserves_portable_history(tmp_path, monkeypatch):
    token = secrets.token_hex(12)
    (tmp_path / "input.txt").write_text(token)
    expected = hashlib.sha256(token.encode()).hexdigest()
    requests = []

    def respond(request):
        if metadata := context_metadata(request):
            return metadata
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer synthetic-credential-not-an-api-key"
        body = json.loads(request.content)
        requests.append(body)
        assert body["store"] is False and body["stream"] is True
        assert body["model"] == "fixture-model" and body["reasoning"] == {"effort": "high", "summary": "auto"}
        outputs = results(body)
        if body["input"][-1].get("content") == "Recall the result":
            previous = [visible_text(item) for item in body["input"] if item.get("role") == "assistant"]
            assert expected in "\n".join(previous)
            return completion([message(expected)])
        if not outputs:
            return completion([call("read_file", {"path": "input.txt"}, 1)])
        if len(outputs) == 1:
            digest = hashlib.sha256(outputs[0]["content"].encode()).hexdigest()
            return completion([call("write_file", {"path": "result.txt", "content": digest,
                                                   "expected_sha256": None}, 2)])
        if len(outputs) == 2:
            check = ("from pathlib import Path; import hashlib; "
                     "assert Path('result.txt').read_text() == "
                     "hashlib.sha256(Path('input.txt').read_bytes()).hexdigest()")
            return completion([call("run_command", {"argv": [sys.executable, "-c", check]}, 3)])
        assert outputs[-1]["exit_code"] == 0
        return completion([message(expected)])

    with tempfile.TemporaryDirectory(prefix="kira-direct-http-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        auth = Account()
        service = Service(Paths.from_env(), backend_factory=lambda *_: OpenAIChatGPTBackend(
            auth, transport=httpx.MockTransport(respond)))
        await service.start()
        service.direct_accounts._auth = auth
        try:
            project = (await service.dispatch("project.create", {
                "name": "Synthetic file task", "root": str(tmp_path), "permission": "trusted_host"}))["project"]
            session = (await service.dispatch("session.create", {
                "project_id": project["id"], "backend": "openai_chatgpt", "model": "fixture-model",
                "settings": {"reasoning_effort": "high"}}))["session"]
            for index, prompt in enumerate(("Read, derive, write and verify", "Recall the result")):
                run = (await service.dispatch("run.start", {
                    "session_id": session["id"], "request_id": f"fixture-{index}", "prompt": prompt}))["run"]
                await asyncio.wait_for(service.tasks[run["id"]], 10)
                actual = service.store.one("runs", run["id"])
                assert actual["state"] == "completed", actual
            assert (tmp_path / "result.txt").read_text() == expected
            assert (tmp_path / "input.txt").read_text() == token
            events = service.store.all("SELECT * FROM events ORDER BY rowid")
            assert [e["payload"]["name"] for e in events if e["type"] == "tool.completed"] == [
                "read_file", "write_file", "run_command"]
            assert len([e for e in events if e["type"] == "usage"]) == 5
            assert "synthetic-credential" not in json.dumps(events)
            assert not service.store.all("SELECT * FROM project_reservations")
        finally:
            await service.close()


@pytest.mark.asyncio
async def test_http_question_waits_for_answer_and_records_response_acceptance(tmp_path, monkeypatch):
    request_count = 0

    def respond(request):
        nonlocal request_count
        if metadata := context_metadata(request):
            return metadata
        request_count += 1
        body = json.loads(request.content)
        if request_count == 1:
            return completion([call("request_input", {"blocking": True, "questions": [
                {"id": "color", "question": "Choose the fixture color", "options": [
                    {"label": "Blue"}, {"label": "Red"}]}]}, "question", "questions")])
        answer = results(body)[0]
        assert answer["status"] == "answered", answer
        return completion([message("Recorded fixture choice")])

    with tempfile.TemporaryDirectory(prefix="kira-direct-question-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        auth = Account()
        service = Service(Paths.from_env(), backend_factory=lambda *_: OpenAIChatGPTBackend(
            auth, transport=httpx.MockTransport(respond)))
        await service.start()
        service.direct_accounts._auth = auth
        try:
            project = (await service.dispatch("project.create", {
                "name": "Synthetic question", "root": str(tmp_path)}))["project"]
            session = (await service.dispatch("session.create", {
                "project_id": project["id"], "backend": "openai_chatgpt", "model": "fixture"}))["session"]
            run = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "question-turn", "prompt": "Ask a choice"}))["run"]
            for _ in range(100):
                questions = (await service.dispatch("input.list", {"project_id": project["id"]}))["requests"]
                if questions:
                    break
                await asyncio.sleep(0.01)
            assert questions
            question = questions[0]
            await service.dispatch("input.answer", {"run_id": run["id"],
                "request_id": question["request_id"], "answers": {"color": {"answers": ["Blue"]}}})
            await asyncio.wait_for(service.tasks[run["id"]], 5)
            actual = service.store.one("runs", run["id"])
            assert actual["state"] == "completed", actual
            delivered = service.store.all("SELECT payload FROM events WHERE type='input.delivered'")
            assert delivered == [{"payload": {"request_id": question["request_id"], "transport": "responses-http"}}]
        finally:
            await service.close()


@pytest.mark.asyncio
async def test_http_chat_delegates_real_scheduler_mailbox_and_returns_same_chat(tmp_path, monkeypatch):
    marker = secrets.token_hex(12)
    (tmp_path / "input.txt").write_text(marker)
    roster = [{"id": name, "name": name.title(), "role": "Fixture reviewer",
               "instructions": "Read synthetic evidence", "access": "read"}
              for name in ("coordinator", "reader")]
    round_bodies = []
    providers = 0
    auth = Account()

    def factory(*_):
        nonlocal providers
        index = providers
        providers += 1

        def respond(request):
            if metadata := context_metadata(request):
                return metadata
            body = json.loads(request.content)
            round_bodies.append(body)
            output = results(body)
            assert body["model"] == "fixture-model"
            assert body["reasoning"] == {"effort": "high", "summary": "auto"}
            if index == 0:
                if not output:
                    return completion([call("team_delegate", {"goal": "Read input.txt",
                        "brief": "Read the evidence and send it to the coordinator",
                        "mode": "manual", "members": roster, "concurrency": 1,
                        "round_limit": 3}, "delegate", "kira")])
                assert output[0]["ok"], output
                return completion([message("Team handoff recorded")])
            if index == 1:
                assert body["text"]["format"]["type"] == "json_schema"
                answer = {"decision": "continue", "summary": "Reader needs evidence",
                          "members": [], "tasks": [{"id": "inspect", "member_id": "reader",
                              "title": "Read evidence", "instructions": "Read input.txt; send the token to coordinator",
                              "access": "read", "depends_on": []}]}
                return completion([message(json.dumps(answer))])
            if index == 2:
                if not output:
                    return completion([call("read_file", {"path": "input.txt"}, "read")])
                evidence = output[0]["content"]
                if len(output) == 1:
                    return completion([call("send_message", {"recipient_id": "coordinator",
                        "content": evidence}, "mailbox", "team")])
                assert output[-1]["ok"], output[-1]
                return completion([message(evidence)])
            if index == 3:
                assert marker in json.dumps(body["input"])
                return completion([message(json.dumps({"decision": "completed",
                    "summary": "Verified reader evidence: " + marker, "members": [], "tasks": []}))])
            assert index in {4, 5}  # Automatic result response, then explicit recall.
            if not any(item.get("delegations") for item in output):
                return completion([call("team_status", {}, "inspect-recorded-result", "kira")])
            assert marker in json.dumps(body["input"])
            return completion([message(marker)])

        return OpenAIChatGPTBackend(auth, transport=httpx.MockTransport(respond))

    with tempfile.TemporaryDirectory(prefix="kira-direct-team-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        service = Service(Paths.from_env(), backend_factory=factory)
        await service.start()
        service.direct_accounts._auth = auth
        try:
            project = (await service.dispatch("project.create", {
                "name": "Synthetic direct team", "root": str(tmp_path)}))["project"]
            session = (await service.dispatch("session.create", {
                "project_id": project["id"], "backend": "openai_chatgpt", "model": "fixture-model",
                "settings": {"reasoning_effort": "high"}}))["session"]
            run = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "team-origin", "prompt": "Delegate a fixture read"}))["run"]
            await asyncio.wait_for(service.tasks[run["id"]], 5)
            actual = service.store.one("runs", run["id"])
            assert actual["state"] == "completed", actual
            rows = service.store.all("SELECT * FROM assistant_delegations")
            assert len(rows) == 1 and rows[0]["team_id"], rows
            if task := service.teams.tasks.get(rows[0]["team_id"]):
                await asyncio.wait_for(task, 10)
            state = await service.dispatch("assistant.snapshot", {"project_id": project["id"],
                "session_id": session["id"]})
            assert state["delegations"][0]["state"] == "completed", state
            assert marker in state["delegations"][0]["summary"]
            assert service.store.db.execute("SELECT COUNT(*) FROM team_messages WHERE sender_id='reader'").fetchone()[0] == 1
            followup = state["delegations"][0]["followup"]
            assert followup["run_id"] and followup["source"] == "team_followup"
            if task := service.tasks.get(followup["run_id"]):
                await asyncio.wait_for(task, 5)
            assert service.store.one("runs", followup["run_id"])["state"] == "completed"
            events = service.store.events(followup["run_id"])["events"]
            assert not any(event["type"] == "message.user" for event in events)
            assert any(event["type"] == "message.completed" and marker in event["payload"].get("content", "")
                       for event in events)
            again = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "team-continuation", "prompt": "Recall the Team result"}))["run"]
            await asyncio.wait_for(service.tasks[again["id"]], 5)
            assert service.store.one("runs", again["id"])["state"] == "completed"
            assert (tmp_path / "input.txt").read_text() == marker
            assert not service.store.all("SELECT * FROM project_reservations")
            assert not service.store.all("SELECT * FROM provider_processes")
            assert providers == 6
        finally:
            await service.close()


@pytest.mark.asyncio
async def test_owned_oauth_credentials_are_masked_if_host_tool_echoes_them(tmp_path, monkeypatch):
    """A hostile file/tool cannot persist Kira's known OAuth values in the chat."""
    from kira.backends import openai_auth
    identity = IdentityServer()
    original_auth = openai_auth.ChatGPTAuth
    monkeypatch.setattr(openai_auth, "ChatGPTAuth", lambda directory, **kwargs: original_auth(
        directory, transport=httpx.MockTransport(identity.request), **kwargs))
    with tempfile.TemporaryDirectory(prefix="kira-direct-mask-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        credential_file = Paths.from_env().config / "openai-chatgpt/accounts.json"
        tool_results = []

        class EchoProvider:
            async def run(self, ctx):
                command = "from pathlib import Path; print(Path(" + repr(str(credential_file)) + ").read_text())"
                tool_results.append(await ctx.execute_tool("run_command", {"argv": [sys.executable, "-c", command]}))
                await ctx.emit("message.delta", {"content": "Visible: synthetic-access-"})
                await ctx.emit("message.delta", {"content": "0; confirmed"})
                await ctx.emit("message.completed", {"content": "Visible: synthetic-access-0; confirmed"})
                return {"state": "completed"}

            async def close(self):
                pass

        def assert_echo_masked(service, run_id):
            # The credential file must really be echoed: a failed tool would make
            # the absence checks below vacuous.
            assert len(tool_results) == 1
            returned = tool_results.pop()
            assert returned["ok"] is True and returned["exit_code"] == 0, returned
            completed = [event["payload"]["result"] for event in service.store.events(run_id)["events"]
                         if event["type"] == "tool.completed"]
            assert len(completed) == 1 and completed[0]["exit_code"] == 0
            for output in (returned["stdout"], completed[0]["stdout"]):
                assert account_id in output
                for field in ("access_token", "refresh_token", "id_token"):
                    assert f'"{field}": "[REDACTED]"' in output
            artifact = Path(completed[0]["artifact"]).read_text()
            assert account_id in artifact and "[REDACTED]" in artifact

        service = Service(Paths.from_env(), backend_factory=lambda *_: EchoProvider())
        await service.start()
        try:
            account_id = await connected(service.direct_accounts.auth, identity)
            credentials = json.loads(credential_file.read_text())["accounts"][account_id]
            project = (await service.dispatch("project.create", {"name": "Credential canary fixture",
                "root": str(tmp_path), "permission": "trusted_host"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "openai_chatgpt", "model": "fixture"}))["session"]
            run = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "mask-canaries", "prompt": "Test synthetic secret masking"}))["run"]
            await asyncio.wait_for(service.tasks[run["id"]], 5)
            assert service.store.one("runs", run["id"])["state"] == "completed"
            assert_echo_masked(service, run["id"])
            recorded = json.dumps(service.store.all("SELECT * FROM events"))
            recorded += "".join(path.read_text() for path in service.paths.artifacts.rglob("*.txt"))
            for field in ("access_token", "refresh_token", "id_token"):
                assert credentials[field] not in recorded
            assert "[REDACTED]" in recorded and "Visible:" in recorded
        finally:
            await service.close()
        # Restart before any direct discovery/login. Another backend may execute
        # first; existing OAuth values must already be protected at admission.
        restarted = Service(Paths.from_env(), backend_factory=lambda *_: EchoProvider())
        await restarted.start()
        try:
            assert restarted.direct_accounts._auth is not None
            other = (await restarted.dispatch("session.create", {"project_id": project["id"],
                "backend": "ollama", "model": "fixture"}))["session"]
            run = (await restarted.dispatch("run.start", {"session_id": other["id"],
                "request_id": "after-restart-canaries", "prompt": "Echo synthetic credential file"}))["run"]
            await asyncio.wait_for(restarted.tasks[run["id"]], 5)
            assert restarted.store.one("runs", run["id"])["state"] == "completed"
            assert_echo_masked(restarted, run["id"])
            recorded = json.dumps(restarted.store.all("SELECT * FROM events"))
            recorded += "".join(path.read_text() for path in restarted.paths.artifacts.rglob("*.txt"))
            for field in ("access_token", "refresh_token", "id_token"):
                assert credentials[field] not in recorded
        finally:
            await restarted.close()
