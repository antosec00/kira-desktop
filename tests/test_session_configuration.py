"""Synthetic session updates preserve native identity and serialize with work."""

import asyncio
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode, new_id, now


class CaptureBackend:
    def __init__(self):
        self.contexts = []

    async def run(self, ctx):
        self.contexts.append(ctx)
        await ctx.emit("message.completed", {"content": "Synthetic response"})
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def configured(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-config-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        backend = CaptureBackend()
        service = Service(Paths.from_env(), backend_factory=lambda *_: backend)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic project", "root": str(tmp_path)}))["project"]
        chat = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "codex", "model": "fixture-original",
            "title": "Keep title", "handoff": "Synthetic handoff", "settings": {
                "reasoning_effort": "high", "service_tier": "default", "tools": True,
                "temperature": 0.2, "max_output_bytes": 8192,
            },
        }))["session"]
        service.store.db.execute("UPDATE sessions SET backend_ref=? WHERE id=?", ("synthetic-native-thread", chat["id"]))
        chat = service.store.one("sessions", chat["id"])
        try:
            yield service, backend, project, chat
        finally:
            await service.close()


def params(project, chat, **overrides):
    return {"project_id": project["id"], "session_id": chat["id"], "model": "fixture-next", **overrides}


async def test_configure_preserves_identity_history_and_other_settings(configured):
    service, _, project, chat = configured
    run, _ = service.store.create_run(chat["id"], new_id(), "Synthetic earlier request")
    service.store.event(run["id"], "message.completed", {"content": "Earlier answer"})
    service.store.event(run["id"], "run.state", {"state": "completed"})
    history = service.store.history(chat["id"])
    events = service.store.events(run["id"])
    result = (await service.dispatch("session.configure", params(project, chat, reasoning_effort="medium")))["session"]
    for key in ("id", "project_id", "backend", "backend_ref", "created_at"):
        assert result[key] == chat[key]
    assert result["model"] == "fixture-next"
    assert result["settings"] == {**chat["settings"], "reasoning_effort": "medium"}
    assert result["title"] == "Keep title"
    assert service.store.history(chat["id"]) == history
    assert service.store.events(run["id"]) == events
    assert len(service.store.all("SELECT * FROM sessions")) == 1


async def test_effort_omission_preserves_and_empty_removes(configured):
    service, _, project, chat = configured
    response = await service.dispatch("session.configure", params(project, chat))
    assert response["session"]["settings"]["reasoning_effort"] == "high"
    response = await service.dispatch("session.configure", params(project, chat, reasoning_effort=""))
    assert "reasoning_effort" not in response["session"]["settings"]
    expected = {key: value for key, value in chat["settings"].items() if key != "reasoning_effort"}
    assert response["session"]["settings"] == expected


@pytest.mark.parametrize("invalid", [
    {"model": ""}, {"model": " "}, {"model": 3}, {"model": "x" * 201}, {"model": "a\0b"},
    {"reasoning_effort": None}, {"reasoning_effort": " "}, {"reasoning_effort": "x" * 81},
    {"backend": "ollama"}, {"settings": {}}, {"backend_ref": "different"},
])
async def test_invalid_updates_are_nonmutating(configured, invalid):
    service, _, project, chat = configured
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, chat, **invalid))
    assert caught.value.code == "INVALID_ARGUMENT"
    assert service.store.one("sessions", chat["id"]) == chat


async def test_foreign_and_team_sessions_cannot_be_reconfigured(configured, tmp_path):
    service, _, project, chat = configured
    other = (await service.dispatch("project.create", {"name": "Other", "root": str(tmp_path)}))["project"]
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(other, chat))
    assert caught.value.code == "NOT_FOUND"
    config, owned, roster = service.teams._config({
        "project_id": project["id"], "request_id": "synthetic-team", "goal": "Synthetic goal",
        "mode": "manual", "backend": "codex", "model": "fixture-only",
        "members": [{"id": "coordinator", "name": "Coordinator", "role": "Fixture", "instructions": "", "access": "read"}],
        "concurrency": 1, "round_limit": 1,
    })
    team = service.teams.data.create(owned, config, "fixture", roster)
    member = service.store.all("SELECT * FROM team_members WHERE team_id=?", (team["id"],))[0]
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, {"id": member["session_id"]}))
    assert caught.value.code == "NOT_FOUND"
    assert service.store.one("sessions", chat["id"]) == chat


async def test_archived_session_requires_restore(configured):
    service, _, project, chat = configured
    await service.dispatch("session.archive", {"project_id": project["id"], "session_id": chat["id"], "archived": True})
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, chat))
    assert caught.value.code == "ARCHIVED"


@pytest.mark.parametrize("status", ["pending", "starting", "running"])
async def test_unfinished_queue_even_paused_blocks_configuration(configured, status):
    service, _, project, chat = configured
    service.prompt_queue.set_paused(project["id"], True)
    item = (await service.dispatch("queue.add", {"session_id": chat["id"], "request_id": "queued", "prompt": "Synthetic queued request"}))["item"]
    service.store.db.execute("UPDATE prompt_queue SET status=? WHERE id=?", (status, item["id"]))
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, chat))
    assert caught.value.code == "BUSY"
    assert service.store.one("sessions", chat["id"]) == chat


@pytest.mark.parametrize("pending,blocked", [("pending", 0), ("unknown", 1)])
async def test_native_control_reservation_blocks_configuration(configured, pending, blocked):
    service, _, project, chat = configured
    service.store.db.execute(
        "INSERT INTO session_forks(request_id,fingerprint,project_id,source_session_id,root,status,result,created_at,updated_at,cleanup_blocked) "
        "VALUES (?,?,?,?,?,?,'{}',?,?,?)",
        ("synthetic-control", "synthetic", project["id"], chat["id"], project["root"], pending, now(), now(), blocked),
    )
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, chat))
    assert caught.value.code == "BUSY"


async def test_overlapping_project_reservation_blocks_configuration(configured, tmp_path):
    service, _, project, chat = configured
    nested = tmp_path / "nested"
    nested.mkdir()
    other = (await service.dispatch("project.create", {"name": "Nested", "root": str(nested)}))["project"]
    service.store.reserve(other, "run", "synthetic-reservation")
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, chat))
    assert caught.value.code == "BUSY"


@pytest.mark.parametrize("gate,code", [("_closing", "SHUTTING_DOWN"), ("_settings_changing", "BUSY")])
async def test_service_transition_gates_configuration(configured, gate, code):
    service, _, project, chat = configured
    setattr(service, gate, True)
    try:
        with pytest.raises(KiraError) as caught:
            await service.dispatch("session.configure", params(project, chat))
        assert caught.value.code == code
    finally:
        setattr(service, gate, False)


async def test_running_admission_snapshot_and_next_turn_metadata(configured):
    service, backend, project, chat = configured
    first = (await service.dispatch("run.start", {"session_id": chat["id"], "request_id": "first", "prompt": "First synthetic turn"}))["run"]
    # run.start reserves before its coroutine gets any CPU time.
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, chat))
    assert caught.value.code == "BUSY"
    await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 30)
    assert backend.contexts[0].session["model"] == "fixture-original"
    await service.dispatch("session.configure", params(project, chat, reasoning_effort=""))
    second = (await service.dispatch("run.start", {"session_id": chat["id"], "request_id": "second", "prompt": "Second synthetic turn"}))["run"]
    await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 30)
    assert backend.contexts[1].session["id"] == chat["id"]
    assert backend.contexts[1].session["backend_ref"] == "synthetic-native-thread"
    assert backend.contexts[1].session["model"] == "fixture-next"
    assert "reasoning_effort" not in backend.contexts[1].session["settings"]
    assert backend.contexts[0].session["model"] == "fixture-original"
    for run, model, effort in ((first, "fixture-original", "high"), (second, "fixture-next", None)):
        event = next(row for row in service.store.events(run["id"])["events"]
                     if row["type"] == "run.state" and row["payload"]["state"] == "running")
        assert event["payload"] == {"state": "running", "backend": "codex", "model": model, "reasoning_effort": effort}


async def test_existing_provider_settings_validated_without_mutation(configured):
    service, _, project, chat = configured
    invalid = {**chat["settings"], "tools": "invalid"}
    service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?", (encode(invalid), chat["id"]))
    with pytest.raises(KiraError) as caught:
        await service.dispatch("session.configure", params(project, chat))
    assert caught.value.code == "INVALID_ARGUMENT"
    assert service.store.one("sessions", chat["id"])["model"] == chat["model"]
