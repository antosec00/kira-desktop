"""Durable image queue behavior with isolated profiles and synthetic providers."""

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import pytest_asyncio
from PySide6.QtGui import QImage

import kira.service.server as server_module
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


class Provider:
    def __init__(self, contexts):
        self.contexts = contexts

    async def run(self, ctx):
        self.contexts.append(ctx)
        await ctx.emit("message.completed", {"content": "Synthetic image response"})
        return {"state": "completed"}

    async def close(self):
        pass


async def until(predicate):
    try:
        async with asyncio.timeout(30):
            while not predicate():
                await asyncio.sleep(.01)
    except TimeoutError:
        raise AssertionError("Synthetic queued image did not settle") from None


@pytest_asyncio.fixture
async def runtime(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-image-queue-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        contexts = []
        service = Service(paths, backend_factory=lambda *args: Provider(contexts))
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Image fixture", "root": str(tmp_path)}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"],
            "backend": "openai_chatgpt", "model": "fixture-model", "settings": {"reasoning_effort": "high"}}))["session"]
        await service.dispatch("queue.pause", {"project_id": project["id"]})
        image = QImage(5, 7, QImage.Format.Format_RGB32)
        image.fill(0xFF772244)
        source = tmp_path / "fixture.png"
        assert image.save(str(source))
        attachments = [(await service.dispatch("attachment.import", {"path": str(source)}))["attachment"]
                       for _ in range(2)]
        try:
            yield service, contexts, project, session, attachments
        finally:
            await service.close()


def params(runtime, request_id="queued-image", *, prompt="", attachments=None):
    _, _, _, session, images = runtime
    return {"session_id": session["id"], "request_id": request_id, "prompt": prompt,
            "attachments": attachments if attachments is not None else [images[0]["id"]]}


async def test_image_only_queue_persists_lists_edits_and_dispatches_exact_ids(runtime):
    service, contexts, project, session, images = runtime
    request = params(runtime)
    first = await service.dispatch("queue.add", request)
    item = first["item"]
    assert item["prompt"] == "" and item["attachments"] == [images[0]]
    duplicate = await service.dispatch("queue.add", request)
    assert duplicate["item"]["id"] == item["id"]
    assert len(first["items"]) == 1 and first["items"][0]["attachments"] == [images[0]]
    await service.dispatch("queue.edit", {"project_id": project["id"], "item_id": item["id"],
                                           "prompt": "Edited image question"})
    await service.dispatch("queue.edit", {"project_id": project["id"], "item_id": item["id"], "prompt": ""})
    assert service.image_inputs.linked("queue", item["id"]) == request["attachments"]
    await service.dispatch("queue.resume", {"project_id": project["id"]})
    await until(lambda: contexts and not service.tasks)
    run = service.store.all("SELECT * FROM runs WHERE request_id=?", (request["request_id"],))[0]
    assert run["state"] == "completed"
    assert service.image_inputs.linked("run", run["id"]) == request["attachments"]
    assert contexts[0].prompt == "" and contexts[0].model == session["model"]
    assert len(contexts[0].image_inputs[run["id"]]) == 1
    assert "data:image" not in json.dumps(service.store.events(run["id"]))


async def test_queue_retries_compare_attachment_ids_and_order(runtime):
    service, _, _, _, images = runtime
    ids = [image["id"] for image in images]
    # A text prompt keeps the image-free retry valid, so every retry reaches the attachment comparison.
    request = params(runtime, prompt="Compare the synthetic images", attachments=ids)
    await service.dispatch("queue.add", request)
    for changed in (ids[::-1], ids[:1], []):
        with pytest.raises(KiraError) as error:
            await service.dispatch("queue.add", {**request, "attachments": changed})
        assert error.value.code == "CONFLICT"
    assert len(service.store.all("SELECT * FROM prompt_queue")) == 1


async def test_removed_request_retry_remains_removed_with_its_images(runtime):
    service, _, project, _, _ = runtime
    request = params(runtime)
    item = (await service.dispatch("queue.add", request))["item"]
    await service.dispatch("queue.remove", {"project_id": project["id"], "item_id": item["id"]})
    result = await service.dispatch("queue.add", request)
    assert result["item"]["status"] == "removed" and not result["items"]
    assert service.image_inputs.linked("queue", item["id"]) == request["attachments"]


async def test_queue_links_and_item_are_atomic(runtime):
    service, _, _, _, _ = runtime
    service.store.db.execute("CREATE TEMP TRIGGER fail_image_link BEFORE INSERT ON queue_image_attachments "
                             "BEGIN SELECT RAISE(ABORT, 'synthetic link failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic link failure"):
        await service.dispatch("queue.add", params(runtime))
    assert not service.store.all("SELECT * FROM prompt_queue")
    assert not service.store.all("SELECT * FROM queue_image_attachments")


async def test_queue_rejects_changed_provider_after_account_preparation(runtime, monkeypatch):
    service, _, _, session, _ = runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(settings):
        entered.set()
        await release.wait()
        return settings

    monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    pending = asyncio.create_task(service.dispatch("queue.add", params(runtime)))
    await asyncio.wait_for(entered.wait(), 30)
    service.store.db.execute("UPDATE sessions SET backend='codex' WHERE id=?", (session["id"],))
    release.set()
    with pytest.raises(KiraError, match="provider changed"):
        await pending
    assert not service.store.all("SELECT * FROM prompt_queue")


async def test_queue_concurrent_same_retry_is_idempotent_after_prepare(runtime, monkeypatch):
    service, _, _, _, _ = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def prepare(settings):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
        await release.wait()
        return settings

    monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    first = asyncio.create_task(service.dispatch("queue.add", params(runtime)))
    second = asyncio.create_task(service.dispatch("queue.add", params(runtime)))
    await asyncio.wait_for(entered.wait(), 30)
    release.set()
    results = await asyncio.gather(first, second)
    assert results[0]["item"]["id"] == results[1]["item"]["id"]
    assert len(service.store.all("SELECT * FROM prompt_queue")) == 1


async def test_bad_current_image_is_rejected_and_queued_substitution_pauses(runtime):
    service, contexts, project, _, images = runtime
    request = params(runtime)
    item = (await service.dispatch("queue.add", request))["item"]
    saved = Path(unquote(urlsplit(images[0]["preview_url"]).path))
    saved.chmod(0o600)
    saved.write_bytes(b"modified after queue preview")
    with pytest.raises(KiraError) as error:
        await service.dispatch("queue.add", params(runtime, request_id="another-image"))
    assert error.value.code == "INVALID_ATTACHMENT"
    await service.dispatch("queue.resume", {"project_id": project["id"]})
    await until(lambda: service.prompt_queue.paused(project["id"]))
    row = service.store.all("SELECT * FROM prompt_queue WHERE id=?", (item["id"],))[0]
    assert row["status"] == "failed" and not contexts
    assert not service.store.all("SELECT * FROM runs")


async def test_other_backend_rejects_image_queue_without_mutation(runtime):
    service, _, project, _, _ = runtime
    session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "codex", "model": "fixture-codex"}))["session"]
    with pytest.raises(KiraError) as error:
        await service.dispatch("queue.add", {**params(runtime), "session_id": session["id"]})
    assert error.value.code == "UNSUPPORTED_IMAGES"
    assert not service.store.all("SELECT * FROM prompt_queue")


async def test_recovery_keeps_saved_queue_images_but_requires_resume(runtime):
    service, contexts, project, _, images = runtime
    await service.dispatch("queue.add", params(runtime))
    await service.close()
    restored = Service(service.paths, backend_factory=lambda *args: Provider(contexts))
    await restored.start()
    try:
        listing = await restored.dispatch("queue.list", {"project_id": project["id"]})
        assert listing["paused"] and listing["items"][0]["attachments"] == [images[0]]
        assert not contexts
        await restored.dispatch("queue.resume", {"project_id": project["id"]})
        await until(lambda: contexts and not restored.tasks)
        assert contexts[0].image_inputs
    finally:
        await restored.close()


async def test_claude_code_accepts_image_queue_like_the_direct_providers(runtime, monkeypatch):
    # Queue admission never loads the optional Claude Code SDK; only chat creation checks it.
    monkeypatch.setattr(server_module, "require_claude_code", lambda: None)
    service, _, project, _, _ = runtime
    session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "claude_code", "model": "sonnet"}))["session"]
    added = await service.dispatch("queue.add", {**params(runtime), "session_id": session["id"]})
    rows = service.store.all("SELECT * FROM prompt_queue")
    assert len(rows) == 1 and rows[0]["session_id"] == session["id"]
    assert service.image_inputs.linked("queue", rows[0]["id"]) == params(runtime)["attachments"]
    assert added
