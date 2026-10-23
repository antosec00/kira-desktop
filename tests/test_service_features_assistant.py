"""Assistant tools offered per provider, against an isolated service and a synthetic provider."""

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

import kira.service.server as server_module
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


class HoldingProvider:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Event()

    async def run(self, context):
        await self.started.put(context)
        await self.release.wait()
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def assistant_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "require_claude_code", lambda: None)
    with tempfile.TemporaryDirectory(prefix="kira-assistant-tools-") as directory:
        paths = Paths(*(Path(directory) / name for name in ("config", "data", "state", "runtime")))
        provider = HoldingProvider()
        service = Service(paths, backend_factory=lambda *_: provider)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic assistant tools",
            "root": str(tmp_path), "permission": "project_write"}))["project"]
        try:
            yield service, provider, project
        finally:
            provider.release.set()
            await service.close()


async def turn(env, backend, model):
    service, provider, project = env
    settings = {} if backend == "ollama" else {"reasoning_effort": "high"}
    session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": backend,
        "model": model, "settings": settings}))["session"]
    await service.dispatch("run.start", {"session_id": session["id"], "request_id": "turn-" + backend,
                                         "prompt": "Organize a synthetic team"})
    return session, await asyncio.wait_for(provider.started.get(), 5)


def assistant_tools(context):
    kira = next(namespace for namespace in context.client_tools if namespace["name"] == "kira")
    return {tool["name"] for tool in kira["tools"]}


DELEGATION = {"goal": "Inspect the synthetic fixture", "brief": "Read only.", "mode": "automatic", "max_agents": 2}


async def test_claude_code_keeps_assistant_tools_without_team_delegation(assistant_env):
    service, _, _ = assistant_env
    session, context = await turn(assistant_env, "claude_code", "synthetic-claude-model")
    names = assistant_tools(context)
    assert {"environment", "team_status", "team_message"} <= names
    assert not names & {"team_delegate", "team_resume"}
    assert "team_delegate" not in context.assistant_instructions
    environment = await context.client_tool_call("environment", "kira.environment", {})
    capabilities = environment["environment"]["capabilities"]
    assert capabilities["managed_team"] is False and capabilities["team_resume"] is False
    assert capabilities["team_status"] is True and capabilities["team_message"] is True
    denied = await context.client_tool_call("delegate", "kira.team_delegate", DELEGATION)
    assert not denied["ok"] and denied["error"]["code"] == "UNSUPPORTED"
    assert not service.store.all("SELECT * FROM assistant_delegations WHERE session_id=?", (session["id"],))


@pytest.mark.parametrize("backend", ["codex", "openai_chatgpt", "ollama", "deepseek_account"])
async def test_team_providers_are_offered_delegation(assistant_env, monkeypatch, backend):
    service, _, _ = assistant_env
    if backend == "openai_chatgpt":
        async def prepare(settings, **_):
            return {**settings, "openai_account_id": "synthetic-account"}
        monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    elif backend == "deepseek_account":
        async def ready():
            return None
        monkeypatch.setattr(service.deepseek_accounts, "prepare", ready)
    _, context = await turn(assistant_env, backend, "synthetic-model")
    assert {"team_delegate", "team_resume"} <= assistant_tools(context)
    environment = await context.client_tool_call("environment", "kira.environment", {})
    assert environment["environment"]["capabilities"]["managed_team"] is True


async def test_team_start_rejects_providers_that_cannot_run_members(assistant_env):
    service, _, project = assistant_env
    with pytest.raises(KiraError) as rejected:
        await service.dispatch("team.start", {"project_id": project["id"], "request_id": "claude-team",
            "goal": "Synthetic goal", "mode": "automatic", "backend": "claude_code",
            "model": "synthetic-claude-model", "settings": {}})
    assert rejected.value.code == "INVALID_ARGUMENT"
