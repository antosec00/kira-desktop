"""Subscription identity and scheduling boundaries; simulated provider only."""

import asyncio
import json
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


class Auth:
    def __init__(self):
        self.active = "account-a"
        self.login_state = "idle"
        self.calls = []

    async def status(self):
        return {"active_account_id": self.active, "login": {"state": self.login_state},
                "accounts": [{"id": name, "label": name, "connected": True,
                              "plan_enabled": True} for name in ("account-a", "account-b")]}

    async def select(self, account_id):
        self.calls.append(("select", account_id))
        self.active = account_id
        return await self.status()

    async def login(self, account_id):
        self.calls.append(("login", account_id))
        self.login_state = "awaiting_browser"
        return await self.status()

    async def cancel_login(self):
        self.login_state = "cancelled"
        return await self.status()

    async def close(self):
        pass


class Provider:
    def __init__(self):
        self.contexts = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.service = None

    async def discover(self):
        return {"available": True, "models": [], "capabilities": {}}

    async def run(self, ctx):
        self.contexts.append(ctx)
        self.started.set()
        if ctx.output_schema:
            # A one-member team genuinely completes through the service without
            # a fabricated provider subprocess identity.
            answer = json.dumps({"decision": "completed", "summary": "Fixture review complete",
                                 "members": [], "tasks": []})
        else:
            await self.release.wait()
            answer = "Fixture answer"
        await ctx.emit("message.completed", {"content": answer})
        return {"state": "completed", "outcome": {"final_text": answer}}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def direct(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-direct-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        provider = Provider()
        service = Service(Paths.from_env(), backend_factory=lambda *_: provider)
        await service.start()
        auth = Auth()
        service.direct_accounts._auth = auth
        project = (await service.dispatch("project.create", {
            "root": str(tmp_path), "name": "Direct fixture"}))["project"]
        session = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "openai_chatgpt", "model": "fixture-model",
            "settings": {"reasoning_effort": "high"}}))["session"]
        yield service, auth, provider, project, session
        provider.release.set()
        await service.close()


def account(action, **kwargs):
    return {"backend": "openai_chatgpt", "action": action, **kwargs}


async def test_chat_binding_survives_account_selection_and_model_configuration(direct):
    service, auth, provider, project, session = direct
    assert session["settings"]["openai_account_id"] == "account-a"
    await service.dispatch("backend.account", account("select", account_id="account-b"))
    updated = (await service.dispatch("session.configure", {
        "project_id": project["id"], "session_id": session["id"], "model": "other-model"}))["session"]
    assert updated["settings"] == session["settings"]
    provider.release.set()
    run = (await service.dispatch("run.start", {
        "session_id": session["id"], "prompt": "Inspect the fixture", "request_id": "bound"}))["run"]
    await asyncio.wait_for(service.tasks[run["id"]], 30)
    assert provider.contexts[0].settings["openai_account_id"] == "account-a"
    assert provider.contexts[0].model == "other-model"
    assert service.store.one("runs", run["id"])["state"] == "completed"
    assert "kira" in {tool.get("name") for tool in provider.contexts[0].client_tools}
    assert not service.store.all("SELECT * FROM provider_processes")
    assert not service.store.all("SELECT * FROM project_reservations")


async def test_authentication_rejected_while_direct_run_or_queue_exists(direct):
    service, auth, provider, project, session = direct
    run = (await service.dispatch("run.start", {
        "session_id": session["id"], "prompt": "Wait", "request_id": "waiting"}))["run"]
    await asyncio.wait_for(provider.started.wait(), 30)
    status = await service.dispatch("backend.account", account("status"))
    assert not status["mutation_allowed"]
    with pytest.raises(KiraError, match="Finish direct work"):
        await service.dispatch("backend.login", {"backend": "openai_chatgpt"})
    assert not auth.calls
    provider.release.set()
    await asyncio.wait_for(service.tasks[run["id"]], 30)
    await service.dispatch("queue.pause", {"project_id": project["id"]})
    await service.dispatch("queue.add", {
        "session_id": session["id"], "prompt": "Later", "request_id": "queued"})
    with pytest.raises(KiraError, match="Finish direct work"):
        await service.dispatch("backend.account", account("select", account_id="account-b"))


@pytest.mark.parametrize("state", ["awaiting_browser", "exchanging"])
async def test_pending_login_blocks_admission_without_recording_work(direct, state):
    service, auth, provider, project, session = direct
    auth.login_state = state
    for method, params in [
        ("run.start", {"session_id": session["id"], "prompt": "Wait", "request_id": "not-admitted"}),
        ("queue.add", {"session_id": session["id"], "prompt": "Wait", "request_id": "not-queued"}),
        ("session.create", {"project_id": project["id"], "backend": "openai_chatgpt", "model": "fixture"}),
    ]:
        with pytest.raises(KiraError, match="Finish or cancel"):
            await service.dispatch(method, params)
    assert not service.tasks
    assert not service.store.all("SELECT * FROM runs")
    assert not service.store.all("SELECT * FROM prompt_queue")
    await service.dispatch("backend.account", account("cancel"))
    assert (await service.dispatch("backend.account", account("status")))["mutation_allowed"]


async def test_direct_team_has_account_and_http_cleanup_without_provider_process(direct):
    service, auth, provider, project, session = direct
    team = (await service.dispatch("team.start", {
        "project_id": project["id"], "request_id": "direct-team", "goal": "Review fixture",
        "mode": "manual", "backend": "openai_chatgpt", "model": "fixture-model",
        "settings": {"reasoning_effort": "high"}, "members": [{"id": "coordinator",
            "name": "Coordinator", "role": "Reviewer", "instructions": "", "access": "read"}],
    }))["team"]
    await asyncio.wait_for(service.teams.tasks[team["id"]], 5)
    final = service.teams.snapshot(team["id"])
    assert final["state"] == "completed", final
    assert provider.contexts[0].settings["openai_account_id"] == "account-a"
    assert provider.contexts[0].settings["reasoning_effort"] == "high"
    assert not service.store.all("SELECT * FROM project_reservations")
    assert not service.store.all("SELECT * FROM provider_processes")
    assert service.store.all("SELECT state FROM run_cleanup") == [{"state": "stopped"}]


async def test_existing_codex_creation_does_not_require_or_change_direct_account(direct):
    service, auth, provider, project, session = direct
    auth.login_state = "awaiting_browser"
    codex = (await service.dispatch("session.create", {
        "project_id": project["id"], "backend": "codex", "model": "fixture-model"}))["session"]
    assert "openai_account_id" not in codex["settings"]
    assert auth.active == "account-a"
    with pytest.raises(KiraError, match="Unsupported backend operation") as error:
        await service.dispatch("backend.account", {"backend": "codex", "action": "status"})
    assert error.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("setting,value", [("service_tier", "priority"), ("temperature", 0.5),
    ("seed", 123), ("think", True), ("num_ctx", 8192)])
async def test_unsupported_settings_do_not_create_a_direct_chat(direct, setting, value):
    service, _, _, project, _ = direct
    before = service.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    with pytest.raises(KiraError, match="does not support"):
        await service.dispatch("session.create", {"project_id": project["id"],
            "backend": "openai_chatgpt", "model": "fixture", "settings": {setting: value}})
    assert service.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before


async def test_explicit_catalog_account_routed_only_to_direct_provider(direct):
    service, auth, provider, _, _ = direct
    observed = []

    async def discover(account_id=None):
        observed.append(account_id)
        return {"available": True, "models": [], "account": {"id": account_id}}

    provider.discover = discover
    result = await service.dispatch("backend.discover", {
        "backend": "openai_chatgpt", "account_id": "account-b"})
    assert result["account"]["id"] == "account-b"
    assert observed == ["account-b"] and auth.active == "account-a"
    with pytest.raises(KiraError, match="Unsupported backend operation"):
        await service.dispatch("backend.discover", {"backend": "codex", "account_id": "account-b"})
