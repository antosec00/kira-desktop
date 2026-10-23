"""Synthetic service round trips: exact chat identity, bytes and generation ownership."""

import asyncio
import json
import tempfile

import pytest
import pytest_asyncio
from PySide6.QtGui import QImage

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode
from kira.service.portable_history import portable_history


class RecordingBackend:
    def __init__(self):
        self.contexts = []

    async def run(self, ctx):
        self.contexts.append(ctx)
        if ctx.session["backend"] == "codex":
            await ctx.emit("session.updated", {"backend_ref": "thread-" + ctx.run_id})
        await ctx.emit("message.completed", {"content": "Recorded answer " + ctx.prompt})
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def app(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-continuity-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        backend = RecordingBackend()
        service = Service(Paths.from_env(), backend_factory=lambda *args: backend)
        await service.start()

        async def prepare(config, **kwargs):
            return {**config, "openai_account_id": config.get("openai_account_id", "fixture-account")}

        monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
        project = (await service.dispatch("project.create", {"name": "Synthetic", "root": str(tmp_path)}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "codex",
                     "model": "fixture-model", "settings": {"reasoning_effort": "high"}, "title": "Keep title"}))["session"]
        yield service, backend, project, session
        await service.close()


async def switch(app, backend, request_id):
    service, _, project, session = app
    return await service.dispatch("session.switch", {"project_id": project["id"], "session_id": session["id"],
        "request_id": request_id, "backend": backend, "model": "fixture-model", "reasoning_effort": "high"})


async def run(app, text, request_id, attachments=None):
    service, _, _, session = app
    params = {"session_id": session["id"], "prompt": text, "request_id": request_id}
    if attachments is not None:
        params["attachments"] = attachments
    result = await service.dispatch("run.start", params)
    task = service.tasks.get(result["run"]["id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 3)
    assert service.store.one("runs", result["run"]["id"])["state"] == "completed"
    return result["run"]


async def image(app, tmp_path):
    service = app[0]
    path = tmp_path / "synthetic.png"
    pixels = QImage(32, 24, QImage.Format.Format_RGB32)
    pixels.fill(0xFF2266AA)
    assert pixels.save(str(path))
    return (await service.dispatch("attachment.import", {"path": str(path)}))["attachment"]


async def test_same_chat_round_trip_bootstraps_new_codex_thread_once(app):
    service, backend, project, session = app
    first = await run(app, "Remember synthetic marker", "first")
    old_ref = service.store.one("sessions", session["id"])["backend_ref"]
    result = await switch(app, "openai_chatgpt", "to-direct")
    assert result["session"]["id"] == session["id"]
    assert result["session"]["title"] == "Keep title"
    assert result["session"]["backend_ref"] is None
    await run(app, "Use marker", "second")
    direct = backend.contexts[-1]
    assert any(m["content"] == "Remember synthetic marker" for m in direct.history)
    await switch(app, "codex", "to-codex")
    await run(app, "Continue", "third")
    resumed = backend.contexts[-1]
    assert resumed.session["backend_ref"] is None
    assert "Remember synthetic marker" in resumed.supplied_context and "Use marker" in resumed.supplied_context
    assert "Portable conversation history" in resumed.supplied_context
    current = service.store.one("sessions", session["id"])
    assert current["backend_ref"] != old_ref
    retired = service.store.all("SELECT * FROM session_provider_generations WHERE session_id=? AND generation=0", (session["id"],))[0]
    service.store.event(first["id"], "session.updated", {"backend_ref": "delayed-old-ref"})
    assert service.store.one("sessions", session["id"])["backend_ref"] == current["backend_ref"]
    assert service.store.all("SELECT * FROM session_provider_generations WHERE session_id=? AND generation=0", (session["id"],))[0] == retired
    await run(app, "Again", "fourth")
    assert "Portable conversation history" not in backend.contexts[-1].supplied_context
    assert service.store.db.execute("SELECT COUNT(*) FROM sessions WHERE project_id=?", (project["id"],)).fetchone()[0] == 1
    assert [r["generation"] for r in service.store.all("SELECT generation FROM run_provider_bindings ORDER BY rowid")] == [0, 1, 2, 2]


async def test_image_only_send_history_replay_and_export_without_base64(app, tmp_path):
    service, backend, project, _ = app
    attachment = await image(app, tmp_path)
    await switch(app, "openai_chatgpt", "images")
    first = await run(app, "", "image-only", [attachment["id"]])
    assert backend.contexts[-1].image_inputs[first["id"]][0]["type"] == "input_image"
    replay = await service.dispatch("run.start", {"session_id": app[3]["id"], "prompt": "", "request_id": "image-only", "attachments": [attachment["id"]]})
    assert replay["run"]["id"] == first["id"] and len(backend.contexts) == 1
    await run(app, "Describe the previous image again", "next")
    assert first["id"] in backend.contexts[-1].image_inputs
    snapshot = service._snapshot(project["id"], bounded=False)
    assert snapshot["messages"][0]["attachments"][0]["sha256"] == attachment["sha256"]
    for row in service.store.all("SELECT payload FROM events"):
        assert "base64," not in encode(row)
    assert "base64," not in encode(snapshot)
    # An empty prompt is admissible only with attachments, even for a known request identity.
    with pytest.raises(KiraError, match="prompt") as empty:
        await service.dispatch("run.start", {"session_id": app[3]["id"], "prompt": "", "request_id": "image-only", "attachments": []})
    assert empty.value.code == "INVALID_ARGUMENT"
    # The selected images are part of the durable request identity.
    other = await image(app, tmp_path)
    with pytest.raises(KiraError) as changed:
        await service.dispatch("run.start", {"session_id": app[3]["id"], "prompt": "", "request_id": "image-only", "attachments": [other["id"]]})
    assert changed.value.code == "CONFLICT" and len(backend.contexts) == 2


async def test_selected_image_modified_before_send_fails_without_creating_run(app, tmp_path):
    service, backend, _, _ = app
    attachment = await image(app, tmp_path)
    await switch(app, "openai_chatgpt", "images")
    path = service.paths.data / "image-attachments" / (attachment["id"] + ".png")
    path.chmod(0o600)
    path.write_bytes(b"changed")
    with pytest.raises(KiraError, match="changed") as error:
        await run(app, "Look", "tampered", [attachment["id"]])
    assert error.value.code == "INVALID_ATTACHMENT"
    assert backend.contexts == [] and service.store.all("SELECT * FROM runs") == []


async def test_direct_auth_await_cannot_admit_stale_provider(app, monkeypatch):
    service = app[0]
    await switch(app, "openai_chatgpt", "initial")
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(config, **kwargs):
        entered.set()
        await release.wait()
        return config

    monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    pending = asyncio.create_task(run(app, "Must not execute on wrong provider", "race"))
    await asyncio.wait_for(entered.wait(), 30)
    await switch(app, "codex", "race-switch")
    release.set()
    with pytest.raises(KiraError, match="provider changed"):
        await asyncio.wait_for(pending, 30)
    assert not service.store.all("SELECT * FROM runs")


async def test_portable_history_never_replays_native_tools_or_reasoning(app):
    service, _, _, session = app
    first = await run(app, "Synthetic user", "first")
    service.store.event(first["id"], "message.completed", {"provider_messages": [
        {"role": "assistant", "tool_calls": [{"id": "must-not-replay"}], "reasoning": "opaque"}]})
    service.store.event(first["id"], "tool.completed", {"name": "read_file", "result": {"ok": True, "content": "synthetic result"}})
    history, receipt = portable_history(service.store, session["id"], "next")
    assert "synthetic result" in json.dumps(history)
    assert "must-not-replay" not in json.dumps(history) and "opaque" not in json.dumps(history)
    assert receipt["native_state_transferred"] is False
