"""Real service/provider adapters with synthetic HTTP, never authenticated inference.

The service assembles all tools and owns profile snapshots/callback persistence.
These regressions intentionally do not replace backend.run with a capture stub.
"""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile

import httpx
import pytest

from kira.backends.ollama import OllamaBackend
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.paths import Paths
from kira.service.server import Service


MODEL = "synthetic-bridge-model"
CORE = "Prefer concrete examples; keep the user's current objective explicit."
ANSWER = "Synthetic greeting completed."


class SyntheticAuth:
    async def access_token(self, account):
        assert account == "synthetic-account"
        return "synthetic-token"

    async def status(self):
        return {"active_account_id": "synthetic-account"}


def direct_response(output):
    event = {"type": "response.completed", "response": {"status": "completed",
        "output": output, "usage": {"input_tokens": 120, "output_tokens": 10}}}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=("data: " + json.dumps(event) + "\n\n").encode())


def ollama_response(message):
    return httpx.Response(200, content=(json.dumps({"message": message, "done": True,
        "done_reason": "stop", "prompt_eval_count": 120, "eval_count": 10}) + "\n").encode())


class ScriptedTransport:
    def __init__(self, provider, propose):
        self.provider, self.propose = provider, propose
        self.service = None
        self.requests = []
        self.results = []
        self.probes = 0

    def proposal_arguments(self):
        run = self.service.store.all("SELECT id FROM runs ORDER BY created_at DESC LIMIT 1")[0]
        event = next(item for item in self.service.store.events(run["id"])["events"]
                     if item["type"] == "message.user")
        return {"statement": "The fixture benefits from concrete examples.",
                "applicability": "When explaining a fixture", "source_run_id": run["id"],
                "source_seq": event["seq"], "kind": "lesson", "scope": "chat"}

    def __call__(self, request):
        assert request.url.host in {"api.openai.com", "127.0.0.1"}
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": MODEL,
                "display_name": MODEL, "visibility": "list",
                "supported_reasoning_levels": ["high"], "context_window": 32000}]})
        body = json.loads(request.content)
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 120})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        if self.provider == "ollama" and body.get("tools", [{}])[0].get(
                "function", {}).get("name") == "kira_probe":
            self.probes += 1
            token = body["messages"][0]["content"].split("value ", 1)[1].split(".", 1)[0]
            return ollama_response({"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "kira_probe", "arguments": {"value": token}}}]})
        self.requests.append(body)
        assert body["model"] == MODEL
        if self.provider == "openai_chatgpt":
            assert body["reasoning"] == {"effort": "high", "summary": "auto"}
            assert CORE not in body["instructions"]
            self.results.extend(json.loads(item["output"]) for item in body["input"]
                                if item.get("type") == "function_call_output")
            if self.propose and len(self.requests) == 1:
                return direct_response([{"type": "function_call", "id": "synthetic-tool",
                    "call_id": "synthetic-proposal", "namespace": "kira", "name": "soul_propose",
                    "arguments": json.dumps(self.proposal_arguments()), "status": "completed"}])
            return direct_response([{"type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": ANSWER}]}])
        assert CORE not in "\n".join(item["content"] for item in body["messages"]
                                     if item["role"] == "system")
        assert body["think"] == "high"
        self.results.extend(json.loads(item["content"]) for item in body["messages"]
                            if item["role"] == "tool")
        if self.propose and len(self.requests) == 1:
            return ollama_response({"role": "assistant", "content": "", "tool_calls": [{
                "function": {"name": "kira__soul_propose", "arguments": self.proposal_arguments()}}]})
        return ollama_response({"role": "assistant", "content": ANSWER})

    def names(self):
        body = self.requests[0]
        if self.provider == "ollama":
            return {tool["function"]["name"].replace("__", ".") for tool in body.get("tools", [])}
        return {namespace["name"] + "." + tool["name"]
                for namespace in body.get("tools", []) if namespace["type"] == "namespace"
                for tool in namespace["tools"]}


@asynccontextmanager
async def environment(tmp_path, provider, mode, *, tools=True, propose=False):
    script = ScriptedTransport(provider, propose)
    if provider == "openai_chatgpt":
        backend = OpenAIChatGPTBackend(SyntheticAuth(), transport=httpx.MockTransport(script))
    else:
        backend = OllamaBackend()
        await backend.client.aclose()
        backend.client = httpx.AsyncClient(base_url="http://127.0.0.1:11434",
                                          transport=httpx.MockTransport(script), trust_env=False)
    with tempfile.TemporaryDirectory(prefix="kira-soul-bridge-") as directory:
        paths = Paths(*(Path(directory) / name for name in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: backend)
        script.service = service
        await service.start()
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic bridge fixture",
                "root": str(tmp_path), "permission": "read_only"}))["project"]
            soul = None if mode is None else (await service.dispatch("soul.create", {
                "request_id": "create-profile", "name": "Synthetic Studio", "core": CORE,
                "mode": mode}))["soul"]
            settings = {"tools": tools, "reasoning_effort": "high"}
            if provider == "ollama":
                settings["think"] = "high"
            else:
                settings["openai_account_id"] = "synthetic-account"
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": provider, "model": MODEL, "settings": settings,
                **({"soul_id": soul["id"]} if soul else {})}))["session"]
            yield service, script, project, session, soul
        finally:
            await service.close()
            await backend.close()


async def greeting(service, session):
    run = (await service.dispatch("run.start", {"session_id": session["id"],
        "request_id": "synthetic-greeting", "prompt": "Hello, introduce yourself with one concrete example."}))["run"]
    await asyncio.wait_for(service.tasks[run["id"]], 10)
    saved = service.store.one("runs", run["id"])
    assert saved["state"] == "completed", (saved["error"], saved["outcome"])
    assert any(event["type"] == "message.completed" and event["payload"]["content"] == ANSWER
               for event in service.store.events(run["id"])["events"])
    current = service.store.one("sessions", session["id"])
    assert current["model"] == MODEL and current["settings"] == session["settings"]
    return saved


@pytest.mark.parametrize("provider", ["openai_chatgpt", "ollama"])
@pytest.mark.parametrize("mode", [None, "manual", "suggest", "adaptive"])
async def test_service_greeting_with_complete_registered_profile_tools(tmp_path, provider, mode):
    async with environment(tmp_path, provider, mode) as (service, script, _, session, soul):
        await greeting(service, session)
        assert len(script.requests) == 1
        assert ("kira.soul_propose" in script.names()) is (mode in {"suggest", "adaptive"})
        assert {"kira.environment", "kira.help_search", "kira.help_read"} <= script.names()
        if soul:
            assert CORE in json.dumps(script.requests[0])
        assert not service.store.all("SELECT * FROM soul_proposals")
        if provider == "ollama":
            assert script.probes == 1


@pytest.mark.parametrize("provider", ["openai_chatgpt", "ollama"])
async def test_service_tools_disabled_keeps_profile_without_proposal_registration(tmp_path, provider):
    async with environment(tmp_path, provider, "adaptive", tools=False) as (
            service, script, _, session, _):
        await greeting(service, session)
        # Direct chat retains its questions/history helpers even when project
        # tools are disabled; profile learning must not be offered in that mode.
        assert not any(name.startswith("kira.") for name in script.names())
        assert CORE in json.dumps(script.requests[0])
        # Ollama keeps its browser-only tools when project tools are off, so the
        # transport probe still runs once; no profile learning is offered.
        assert script.probes == (1 if provider == "ollama" else 0)
        assert not service.store.all("SELECT * FROM soul_proposals")


@pytest.mark.parametrize("provider", ["openai_chatgpt", "ollama"])
@pytest.mark.parametrize("mode", ["suggest", "adaptive"])
async def test_real_provider_callback_records_pending_proposal_and_durable_receipts(tmp_path, provider, mode):
    async with environment(tmp_path, provider, mode, propose=True) as (
            service, script, _, session, soul):
        run = await greeting(service, session)
        assert len(script.requests) == 2
        assert len(script.results) == 1 and script.results[0]["ok"]
        proposals = (await service.dispatch("soul.proposals", {"soul_id": soul["id"]}))["proposals"]
        assert len(proposals) == 1 and proposals[0]["status"] == "pending"
        assert proposals[0]["id"] == script.results[0]["proposal"]["id"]
        current = (await service.dispatch("soul.get", {"soul_id": soul["id"]}))["soul"]
        assert current["revision"] == 1 and not current["preferences"]
        assert not (await service.dispatch("soul.memories", {"soul_id": soul["id"]}))["memories"]
        receipts = [event for event in service.store.events(run["id"])["events"]
                    if event["type"].startswith("tool.")
                    and event["payload"].get("name") == "kira.soul_propose"]
        assert [event["type"] for event in receipts] == ["tool.started", "tool.completed"]
        assert receipts[0]["payload"]["tool_id"] == receipts[1]["payload"]["tool_id"]
        assert receipts[1]["payload"]["result"]["ok"]


async def test_adaptive_profile_coexists_with_service_assembled_goal_namespace(tmp_path):
    async with environment(tmp_path, "openai_chatgpt", "adaptive") as (
            service, script, project, session, _):
        scope = {"project_id": project["id"], "session_id": session["id"]}
        await service.dispatch("goal.start", {**scope, "request_id": "synthetic-goal",
            "objective": "Explain this synthetic fixture once.", "max_cycles": 1})
        # Pause synchronously before the scheduler can admit a cycle. Ordinary
        # chat still receives the saved goal's read-only namespace.
        await service.dispatch("goal.pause", {**scope, "request_id": "pause-fixture-goal"})
        await greeting(service, session)
        assert {"kira.soul_propose", "goal.status"} <= script.names()
        assert "goal.checkpoint" not in script.names()
        assert (await service.dispatch("goal.get", scope))["autonomous"]["status"] == "paused"
