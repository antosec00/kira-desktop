"""Synthetic chat fixtures: ownership, bounds and actual supplied-context plumbing."""

import asyncio
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.project_context import CONTEXT_CHARS
from kira.service.server import Service
from kira.service.storage import encode, new_id


class CaptureBackend:
    def __init__(self):
        self.contexts = []

    async def run(self, ctx):
        self.contexts.append(ctx)
        await ctx.emit("message.completed", {"content": "Synthetic provider answer"})
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def chats(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-chats-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        backend = CaptureBackend()
        service = Service(Paths.from_env(), backend_factory=lambda *_: backend)
        await service.start()
        projects = []
        for name in ("P", "Q"):
            root = tmp_path / name
            root.mkdir()
            projects.append((await service.dispatch("project.create", {"name": name, "root": str(root)}))["project"])
        try:
            yield service, backend, projects
        finally:
            await service.close()


async def session(service, project, **params):
    return (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "codex", "model": "fixture-only", **params}))["session"]


def recorded(service, chat, prompt, answer="Recorded synthetic answer", state="completed"):
    run, _ = service.store.create_run(chat["id"], new_id(), prompt)
    service.store.event(run["id"], "message.completed", {"content": answer})
    service.store.event(run["id"], "run.state", {"state": state})
    return run


def managed_chat(service, project):
    params = {"project_id": project["id"], "request_id": "fixture-team", "goal": "Synthetic team",
              "mode": "manual", "backend": "codex", "model": "fixture-only",
              "members": [{"id": "coordinator", "name": "Coordinator", "role": "Fixture",
                           "instructions": "", "access": "read"}],
              "concurrency": 1, "round_limit": 1}
    config, owned, roster = service.teams._config(params)
    team = service.teams.data.create(owned, config, "fixture", roster)
    member = service.store.all("SELECT * FROM team_members WHERE team_id=?", (team["id"],))[0]
    return service.store.one("sessions", member["session_id"])


async def test_chat_titles_create_derive_rename_and_keep_provider_state(chats):
    service, _, (project, _) = chats
    chat = await session(service, project)
    assert chat["title"] == "Nuova chat"
    recorded(service, chat, "  First\n   request   with spaces " + "x" * 100)
    listed = await service.dispatch("session.list", {"project_id": project["id"]})
    assert listed["sessions"][0]["title"].startswith("First request with spaces")
    assert len(listed["sessions"][0]["title"]) == 80
    named = (await service.dispatch("session.rename", {"project_id": project["id"],
              "session_id": chat["id"], "title": "  Design\nreview  "}))["session"]
    assert named["title"] == "Design review"
    assert named["backend_ref"] == chat["backend_ref"]
    assert (await service.dispatch("project.snapshot", {"project_id": project["id"]}))["sessions"][0]["title"] == "Design review"
    assert service.store.db.execute("PRAGMA user_version").fetchone()[0] == 16


@pytest.mark.parametrize("title", ["", " \n ", "x" * 121, 5, None, "a\0b"])
async def test_invalid_chat_titles_rejected(chats, title):
    service, _, (project, _) = chats
    with pytest.raises(KiraError) as error:
        await session(service, project, title=title)
    assert error.value.code == "INVALID_ARGUMENT"


async def test_internal_chat_title_cannot_be_injected_through_settings(chats):
    service, _, (project, _) = chats
    with pytest.raises(KiraError) as error:
        await session(service, project, settings={"chat_title": "Bypass"})
    assert error.value.code == "INVALID_ARGUMENT"


async def test_project_ownership_and_team_exclusion(chats):
    service, _, (project, other) = chats
    a = await session(service, project)
    foreign = await session(service, other, title="OTHER-PROJECT-SENTINEL")
    managed = managed_chat(service, project)
    for method in ("session.snapshot", "session.rename", "project.context"):
        for chat in (foreign, managed):
            params = {"project_id": project["id"], "session_id": chat["id"]}
            if method == "session.rename":
                params["title"] = "Wrong owner"
            with pytest.raises(KiraError) as caught:
                await service.dispatch(method, params)
            assert caught.value.code == "NOT_FOUND"
    result = await service.dispatch("session.list", {"project_id": project["id"]})
    assert [item["id"] for item in result["sessions"]] == [a["id"]]
    assert "OTHER-PROJECT-SENTINEL" not in encode(result)


async def test_chat_list_pagination_and_bounds(chats):
    service, _, (project, _) = chats
    expected = [(await session(service, project))["id"] for _ in range(5)]
    offset, found = 0, []
    while True:
        result = await service.dispatch("session.list", {"project_id": project["id"], "offset": offset, "limit": 2})
        found += [item["id"] for item in result["sessions"]]
        if not result["has_more"]:
            break
        offset = result["next_offset"]
    assert found == list(reversed(expected))
    for params in ({"limit": 201}, {"offset": -1}, {"limit": True}):
        with pytest.raises(KiraError) as error:
            await service.dispatch("session.list", {"project_id": project["id"], **params})
        assert error.value.code == "INVALID_ARGUMENT"


async def test_session_snapshot_isolated_ordered_bounded_and_artifact_owned(chats):
    service, _, (project, other) = chats
    a, b, q = [await session(service, p) for p in (project, project, other)]
    runs = [recorded(service, a, f"Request {i}") for i in range(65)]
    recorded(service, b, "SIBLING-HISTORY-SENTINEL")
    recorded(service, q, "OTHER-HISTORY-SENTINEL")
    for chat in (a, b, q):
        run = recorded(service, chat, "Artifact owner")
        service.store.event(run["id"], "artifact.created", {"path": f"/{chat['id']}/file.txt"})
    result = await service.dispatch("session.snapshot", {"project_id": project["id"], "session_id": a["id"]})
    assert result["history_truncated"] and len(result["runs"]) == 60
    assert result["runs"][0]["id"] == runs[6]["id"]
    assert all(run["session_id"] == a["id"] for run in result["runs"])
    assert len(result["artifacts"]) == 1 and a["id"] in result["artifacts"][0]["path"]
    assert "SENTINEL" not in encode(result)


async def test_snapshot_unicode_and_outcome_are_byte_bounded(chats):
    service, _, (project, _) = chats
    a = await session(service, project)
    run = recorded(service, a, "😀" * 3000)
    service.store.event(run["id"], "run.state", {"state": "completed", "outcome": {"result": "😀" * 3000}})
    result = await service.dispatch("session.snapshot", {"project_id": project["id"], "session_id": a["id"]})
    assert len(result["runs"][0]["prompt"].encode()) <= 2000
    assert result["runs"][0]["outcome"]["truncated"]
    assert result["history_truncated"]


async def test_context_excludes_target_other_project_teams_and_failed_claims(chats):
    service, _, (project, other) = chats
    a, b, q = [await session(service, p) for p in (project, project, other)]
    recorded(service, a, "TARGET-PRIVATE")
    prior = recorded(service, b, "Same project request", "SIBLING-CLAIM")
    recorded(service, b, "FAILED-REQUEST", "FAILED-CLAIM", "failed")
    recorded(service, q, "OTHER-PROJECT-PRIVATE")
    managed = managed_chat(service, project)
    recorded(service, managed, "TEAM-PRIVATE")
    context = await service.dispatch("project.context", {"project_id": project["id"], "session_id": a["id"]})
    assert "SIBLING-CLAIM" in context["content"]
    assert all(token not in context["content"] for token in ("TARGET-PRIVATE", "FAILED-", "OTHER-PROJECT-PRIVATE", "TEAM-PRIVATE"))
    assert context["sources"] == [{"session_id": b["id"], "run_id": prior["id"], "title": "Same project request"}]
    assert "not independently verified" in context["content"]
    assert context["chars"] == len(context["content"]) and not context["truncated"]


async def test_context_budget_clips_excerpts_and_limits_sources(chats):
    service, _, (project, _) = chats
    target = await session(service, project)
    for i in range(8):
        chat = await session(service, project, title=f"Chat {i}")
        recorded(service, chat, "p" * 8000, "answer" * 8000)
    context = await service.dispatch("project.context", {"project_id": project["id"], "session_id": target["id"]})
    assert context["truncated"] and 0 < context["chars"] <= CONTEXT_CHARS
    assert 0 < len(context["sources"]) <= 5
    assert all(source["run_id"] in context["content"] for source in context["sources"])


async def test_context_opt_out_and_provider_provenance(chats):
    service, backend, (project, other) = chats
    target = await session(service, project, title="Target")
    sibling = await session(service, project)
    foreign = await session(service, other)
    recorded(service, sibling, "Sibling request", "SIBLING-EVIDENCE")
    recorded(service, foreign, "FOREIGN-CONTEXT")
    await service.dispatch("project.update", {"project_id": project["id"], "memory": {"note": "MANUAL-PRIORITY"}})
    inspected = await service.dispatch("project.context", {"project_id": project["id"], "session_id": target["id"]})
    run = (await service.dispatch("run.start", {"session_id": target["id"], "prompt": "Fixture request", "request_id": new_id()}))["run"]
    await asyncio.wait_for(service.tasks[run["id"]], 30)
    ctx = backend.contexts[-1]
    assert inspected["content"] in ctx.supplied_context and "project:memory" in ctx.supplied_context
    assert "MANUAL-PRIORITY" not in ctx.supplied_context
    assert "MANUAL-PRIORITY" in service.knowledge.read(project["id"], target["id"], "project:memory")["content"]
    assert "FOREIGN-CONTEXT" not in ctx.supplied_context
    assert ctx.history == [] and "chat_title" not in ctx.session["settings"]
    supplied_event = next(e for e in service.store.events(run["id"])["events"] if e["type"] == "context.supplied")
    assert inspected["content"] in supplied_event["payload"]["content"]
    await service.dispatch("project.update", {"project_id": project["id"], "memory": {"share_session_summaries": False}})
    disabled = await service.dispatch("project.context", {"project_id": project["id"], "session_id": target["id"]})
    assert disabled == {"enabled": False, "content": "", "sources": [], "truncated": False, "chars": 0}
    next_run = (await service.dispatch("run.start", {"session_id": target["id"], "prompt": "Second fixture", "request_id": new_id()}))["run"]
    await asyncio.wait_for(service.tasks[next_run["id"]], 30)
    assert "SIBLING-EVIDENCE" not in backend.contexts[-1].supplied_context


async def test_turn_reads_current_memory_and_shared_context_setting(chats):
    service, backend, (project, _) = chats
    target = await session(service, project)
    sibling = await session(service, project)
    recorded(service, sibling, "Sibling request", "STALE-SHARING-SENTINEL")
    run = (await service.dispatch("run.start", {"session_id": target["id"],
               "prompt": "Fixture", "request_id": new_id()}))["run"]
    # Dispatch is synchronous up to returning the admitted run; its scheduled task
    # must read the latest memory rather than the admission-time project object.
    await service.dispatch("project.update", {"project_id": project["id"],
               "memory": {"note": "CURRENT-MEMORY", "share_session_summaries": False}})
    await asyncio.wait_for(service.tasks[run["id"]], 30)
    assert "project:memory" in backend.contexts[-1].supplied_context
    assert "CURRENT-MEMORY" in service.knowledge.read(project["id"], target["id"], "project:memory")["content"]
    assert "STALE-SHARING-SENTINEL" not in backend.contexts[-1].supplied_context


async def test_session_page_large_handoffs_is_byte_bounded_without_omitting_cursor(chats):
    service, _, (project, _) = chats
    for _ in range(10):
        await session(service, project, handoff="h" * 99000)
    first = await service.dispatch("session.list", {"project_id": project["id"]})
    assert first["has_more"] and len(encode(first).encode()) < 710000
    second = await service.dispatch("session.list", {"project_id": project["id"], "offset": first["next_offset"]})
    ids = [item["id"] for page in (first, second) for item in page["sessions"]]
    assert len(ids) == len(set(ids)) == 10
    assert not second["has_more"]


async def test_unicode_manual_title_and_large_error_preserve_sources(chats):
    service, _, (project, _) = chats
    chat = await session(service, project, title="😀" * 120)
    run = recorded(service, chat, "Request", state="failed")
    service.store.event(run["id"], "run.state", {"state": "failed", "error": "x" * 5000})
    result = await service.dispatch("session.snapshot", {"project_id": project["id"], "session_id": chat["id"]})
    assert result["session"]["title"] == "😀" * 120
    assert result["history_truncated"] and result["runs"][0]["error_truncated"]
    assert len(result["runs"][0]["error"]) == 2000
    assert len(service.store.one("runs", run["id"])["error"]) == 5000


async def test_sharing_flag_update_preserves_current_notes_after_stale_gui_snapshot(chats):
    service, _, (project, _) = chats
    stale = await service.dispatch("project.snapshot", {"project_id": project["id"]})
    assert stale["project"]["memory"] == {}
    notes = {"note": "Newer user-maintained note", "open_questions": ["Review fixture"]}
    await service.dispatch("project.update", {"project_id": project["id"], "memory": notes})
    updated = await service.dispatch("project.update", {"project_id": project["id"], "share_session_summaries": False})
    assert updated["project"]["memory"] == {**notes, "share_session_summaries": False}
    assert not (await service.dispatch("project.context", {"project_id": project["id"]}))["enabled"]
    enabled = await service.dispatch("project.update", {"project_id": project["id"], "share_session_summaries": True})
    assert enabled["project"]["memory"] == {**notes, "share_session_summaries": True}


@pytest.mark.parametrize("flag", [None, 0, 1, "false", [], {}])
async def test_sharing_flag_update_rejects_non_boolean_without_changing_memory(chats, flag):
    service, _, (project, _) = chats
    with pytest.raises(KiraError) as error:
        await service.dispatch("project.update", {"project_id": project["id"], "share_session_summaries": flag})
    assert error.value.code == "INVALID_ARGUMENT"
    assert service.store.one("projects", project["id"])["memory"] == {}
