"""DeepSeek Account admission: running or queued work never blocks new DeepSeek requests.

Real service and SQLite with a simulated provider and a synthetic account; no network.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


class Account:
    """The account surface the service reads; holds no real credential."""

    def __init__(self):
        self.token = "synthetic-deepseek-token"
        self.proxy_enabled = False
        self.proxy_api_key = ""
        self.proxy_url = "http://127.0.0.1:4000"
        self.pending = None

    def status(self):
        return {"connected": bool(self.token), "login": {"phase": "signed_in"}}

    async def close(self):
        pass


class Provider:
    def __init__(self):
        self.prompts = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def discover(self):
        return {"available": True, "models": [], "capabilities": {}}

    async def run(self, ctx):
        self.prompts.append(ctx.prompt)
        self.started.set()
        await self.release.wait()
        await ctx.emit("message.completed", {"content": "Synthetic answer"})
        return {"state": "completed", "outcome": {}}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def deepseek(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-deepseek-admission-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        provider = Provider()
        service = Service(paths, backend_factory=lambda *_: provider)
        await service.start()
        account = Account()
        service.deepseek_accounts._auth = account
        try:
            project = (await service.dispatch("project.create", {
                "name": "Synthetic project", "root": str(tmp_path)}))["project"]
            session = (await service.dispatch("session.create", {
                "project_id": project["id"], "backend": "deepseek_account",
                "model": "deepseek-fixture"}))["session"]
            yield service, provider, account, project, session
        finally:
            provider.release.set()
            await service.close()


async def _settle(service, project_id):
    task = service.prompt_queue.tasks.get(project_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 5)


async def test_queued_deepseek_request_starts_instead_of_failing_busy(deepseek):
    service, provider, _account, project, session = deepseek
    provider.release.set()
    item = (await service.dispatch("queue.add", {
        "session_id": session["id"], "request_id": "queued-deepseek", "prompt": "Queued work"}))["item"]
    await _settle(service, project["id"])
    row = service.store.all("SELECT * FROM prompt_queue WHERE id=?", (item["id"],))[0]
    assert row["status"] in {"running", "completed"}, row["error"]
    assert row["run_id"]
    task = service.tasks.get(row["run_id"])
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 5)
    assert service.store.one("runs", row["run_id"])["state"] == "completed"
    assert provider.prompts == ["Queued work"]
    assert not (await service.dispatch("queue.list", {"project_id": project["id"]}))["paused"]


async def test_active_deepseek_run_does_not_block_another_deepseek_chat(deepseek, tmp_path):
    service, provider, _account, _project, session = deepseek
    run = (await service.dispatch("run.start", {
        "session_id": session["id"], "request_id": "active-deepseek", "prompt": "Long work"}))["run"]
    await asyncio.wait_for(provider.started.wait(), 5)
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = (await service.dispatch("project.create", {
        "name": "Other project", "root": str(other_root)}))["project"]
    created = (await service.dispatch("session.create", {
        "project_id": other["id"], "backend": "deepseek_account", "model": "deepseek-fixture"}))["session"]
    assert created["backend"] == "deepseek_account"
    # Account changes stay blocked while DeepSeek work is active.
    with pytest.raises(KiraError, match="Finish DeepSeek requests"):
        await service.dispatch("backend.account", {"backend": "deepseek_account", "action": "logout"})
    provider.release.set()
    await asyncio.wait_for(asyncio.shield(service.tasks[run["id"]]), 5)


@pytest.mark.parametrize("blocker", ["changing", "pending_login"])
async def test_account_change_or_pending_sign_in_still_blocks_admission(deepseek, blocker):
    service, _provider, account, _project, session = deepseek
    if blocker == "changing":
        service.deepseek_accounts.changing = True
        expected = "settings are changing"
    else:
        account.pending = {"state": "synthetic"}
        expected = "Finish or cancel DeepSeek sign-in"
    with pytest.raises(KiraError, match=expected) as error:
        await service.dispatch("run.start", {
            "session_id": session["id"], "request_id": "blocked", "prompt": "Wait"})
    assert error.value.code == "BUSY"
    assert not service.store.all("SELECT * FROM runs")
