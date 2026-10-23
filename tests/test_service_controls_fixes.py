"""Queue, archive, token storage and migration fixes with synthetic profiles."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service import storage
from kira.service.deepseek_web_accounts import DeepSeekWebAccounts
from kira.service.server import Service
from kira.service.storage import Store


class Provider:
    async def discover(self):
        return {"available": True, "models": [], "capabilities": {}}

    async def run(self, ctx):
        await ctx.emit("message.completed", {"content": "Synthetic answer"})
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def env():
    with tempfile.TemporaryDirectory(prefix="kira-controls-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: Provider())
        await service.start()
        root = Path(directory) / "project"
        root.mkdir()
        project = (await service.dispatch("project.create", {"name": "Synthetic controls", "root": str(root)}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "ollama",
                                                             "model": "synthetic-model"}))["session"]
        try:
            yield service, project, session
        finally:
            await service.close()


async def queue(service, session, request_id="queued-request"):
    return await service.dispatch("queue.add", {"session_id": session["id"], "request_id": request_id,
                                                "prompt": "Queued synthetic request"})


async def dispatched(service, project):
    async with asyncio.timeout(5):
        while project["id"] in service.prompt_queue.tasks:
            await asyncio.sleep(0.01)
    return await service.dispatch("queue.list", {"project_id": project["id"]})


# SC-12: a refusal that depends on the current state keeps the request waiting.
@pytest.mark.parametrize("code,status", [("BUSY", "pending"), ("ARCHIVED", "pending"),
                                         ("SHUTTING_DOWN", "pending"), ("INVALID_ARGUMENT", "failed"),
                                         ("AUTH_REQUIRED", "failed")])
async def test_queue_keeps_transiently_refused_requests_waiting(env, monkeypatch, code, status):
    service, project, session = env
    original = service.dispatch

    async def refuse(method, params, subscriber=None):
        if method == "run.start":
            raise KiraError(code, "Synthetic refusal")
        return await original(method, params, subscriber)

    monkeypatch.setattr(service, "dispatch", refuse)
    await queue(service, session)
    listing = await dispatched(service, project)
    item = listing["items"][0]
    assert item["status"] == status and listing["paused"]
    assert item["error"] == "Synthetic refusal"
    assert not service.store.all("SELECT * FROM runs")


# SC-13: an archived chat keeps no waiting requests.
async def test_archiving_waits_until_queued_requests_are_removed(env):
    service, project, session = env
    await service.dispatch("queue.pause", {"project_id": project["id"]})
    item = (await queue(service, session))["item"]
    params = {"project_id": project["id"], "session_id": session["id"], "archived": True}
    with pytest.raises(KiraError, match="queued requests") as error:
        await service.dispatch("session.archive", params)
    assert error.value.code == "BUSY" and not service.workbench.metadata(session["id"])["archived"]
    await service.dispatch("queue.remove", {"project_id": project["id"], "item_id": item["id"]})
    assert (await service.dispatch("session.archive", params))["session"]["archived"]


# SC-16: the token file descriptor is closed exactly once, by its file object.
def test_token_file_descriptor_is_not_closed_twice(tmp_path, monkeypatch):
    directory = tmp_path / "deepseek-web"
    directory.mkdir(mode=0o700)
    stored = directory / "token.json"
    stored.write_text(json.dumps({"version": 1, "token": "synthetic-token"}))
    stored.chmod(0o600)
    redactor = SimpleNamespace(set_provider_values=lambda values, owner: None)
    owner = SimpleNamespace(paths=SimpleNamespace(config=tmp_path), redactor=redactor)
    explicit = []
    real_close = os.close

    def close(descriptor):
        explicit.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(os, "close", close)
    assert DeepSeekWebAccounts(owner).token == "synthetic-token"
    assert explicit == []


# SC-18: a migration records the schema version the service declares.
def test_migration_records_the_declared_schema_version(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "SCHEMA_VERSION", storage.SCHEMA_VERSION + 1)
    store = Store(tmp_path / "fresh.sqlite3")
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
    finally:
        store.close()
