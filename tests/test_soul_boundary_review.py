"""Independent isolated regressions for identity admission and chat sharing."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest_asyncio

from kira.domain.paths import Paths
from kira.service.project_context import ProjectChats
from kira.service.server import Service


@pytest_asyncio.fixture
async def soul_service(tmp_path):
    received = []

    class CaptureProvider:
        async def run(self, ctx):
            received.append(ctx)
            await ctx.emit("message.completed", {"content": "Recorded outcome for " + ctx.prompt})
            return {"state": "completed"}

        async def close(self):
            pass

    with tempfile.TemporaryDirectory(prefix="kira-soul-boundary-") as directory:
        paths = Paths(*(Path(directory) / name for name in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: CaptureProvider())
        await service.start()
        try:
            project = (await service.dispatch("project.create", {
                "name": "Synthetic boundary project", "root": str(tmp_path)}))["project"]
            chats = [(await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "ollama", "model": "fixture-model", "settings": {"tools": False}}))["session"]
                for _ in range(2)]
            identities = [(await service.dispatch("soul.create", {
                "request_id": "create-" + label, "name": label, "core": "Core for " + label}))["soul"]
                for label in ("Studio", "Atelier")]
            yield service, project, chats, identities, received
        finally:
            await service.close()


async def bind(service, project, chat, soul, request_id):
    return await service.dispatch("soul.bind", {"request_id": request_id, "project_id": project["id"],
        "session_id": chat["id"], "soul_id": soul["id"]})


async def run(service, chat, prompt, request_id):
    result = await service.dispatch("run.start", {"session_id": chat["id"],
        "request_id": request_id, "prompt": prompt})
    task = service.tasks.get(result["run"]["id"])
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 5)
    saved = service.store.one("runs", result["run"]["id"])
    assert saved["state"] == "completed", saved
    return saved


async def test_rebinding_source_chat_does_not_reshare_previous_identity_run(soul_service):
    service, project, (source, target), (studio, atelier), _ = soul_service
    await bind(service, project, source, studio, "source-studio")
    await bind(service, project, target, atelier, "target-atelier")
    original = await run(service, source, "STUDIO-PRIVATE original task", "studio-task")
    assert ProjectChats(service.store).context(project["id"], target["id"])["sources"] == []
    await bind(service, project, source, atelier, "source-atelier")
    shared = ProjectChats(service.store).context(project["id"], target["id"])
    assert shared["sources"] == []
    assert "STUDIO-PRIVATE" not in shared["content"]
    # Same-chat history is deliberately retained, rather than erased to obtain isolation.
    assert service.store.one("runs", original["id"])["prompt"] == "STUDIO-PRIVATE original task"
    # A new eligible run can be shared; its title must not copy an older identity's prompt.
    current = await run(service, source, "ATELIER-CURRENT new task", "atelier-task")
    shared = ProjectChats(service.store).context(project["id"], target["id"])
    assert [item["run_id"] for item in shared["sources"]] == [current["id"]]
    assert "ATELIER-CURRENT" in shared["content"]
    assert "STUDIO-PRIVATE" not in shared["content"]


async def test_rebinding_away_and_back_does_not_reuse_prior_binding_epoch(soul_service):
    service, project, (source, target), (studio, atelier), _ = soul_service
    await bind(service, project, source, studio, "source-studio")
    await bind(service, project, target, studio, "target-studio")
    await run(service, source, "First studio task", "studio-task")
    assert ProjectChats(service.store).context(project["id"], target["id"])["sources"]
    await bind(service, project, source, atelier, "source-atelier")
    await bind(service, project, source, studio, "source-studio-again")
    assert ProjectChats(service.store).context(project["id"], target["id"])["sources"] == []


async def test_profile_edit_after_run_admission_waits_for_next_run(soul_service, monkeypatch):
    service, project, (chat, _), (studio, _), received = soul_service
    await bind(service, project, chat, studio, "source-studio")
    entered, release = asyncio.Event(), asyncio.Event()
    emit = service.emit

    async def delayed_emit(run_id, kind, payload):
        await emit(run_id, kind, payload)
        if kind == "run.state" and payload.get("state") == "running":
            entered.set()
            await release.wait()

    monkeypatch.setattr(service, "emit", delayed_emit)
    request = asyncio.create_task(run(service, chat, "Exact user objective", "admitted-turn"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        snapshot = service.store.db.execute("SELECT snapshot FROM soul_run_snapshots").fetchone()
        assert snapshot is not None
        assert json.loads(snapshot[0])["soul"]["revision"] == 1
        await service.dispatch("soul.update", {"request_id": "edit-during-turn", "soul_id": studio["id"],
            "expected_revision": 1, "core": "NEW-CORE only for subsequent turns"})
        release.set()
        await asyncio.wait_for(request, 5)
        assert "Core for Studio" in received[-1].supplied_context
        assert "NEW-CORE" not in received[-1].supplied_context
        assert received[-1].prompt == "Exact user objective"
        await run(service, chat, "Continue same objective", "subsequent-turn")
        assert "NEW-CORE only for subsequent turns" in received[-1].supplied_context
        assert "Core for Studio" not in received[-1].supplied_context
        assert received[-1].model == "fixture-model"
        assert received[-1].settings["tools"] is False
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
