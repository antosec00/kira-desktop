"""Connected Kira Studio tool contracts: no real provider/account or external network.

These fixtures exercise the real Service attach chain, shared tool catalog,
per-chat revocation, revision provenance and isolated provider substitutions.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio

from kira.backends.client_tools import validate_tools
from kira.backends.kira_mcp import KIRA_RELAY_TOOLS
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.domain.studio import STUDIO_TOOL_NAMES, studio_tool_specs
from kira.service.server import Service


@pytest.fixture(autouse=True)
def studio_available(monkeypatch):
    # Studio is not offered in this release; these tests keep its dormant code working.
    monkeypatch.setattr("kira.domain.studio.STUDIO_AVAILABLE", True)



class HoldingProvider:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Event()

    async def run(self, ctx):
        await self.started.put(ctx)
        await self.release.wait()
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def fixture(tmp_path):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    provider = HoldingProvider()
    service = Service(paths, backend_factory=lambda *_: provider)
    await service.start()
    project = (await service.dispatch("project.create", {
        "name": "Synthetic Studio provider project", "root": str(tmp_path),
        "permission": "project_write",
    }))["project"]
    try:
        yield service, provider, project
    finally:
        provider.release.set()
        await service.close()


async def chat(service, project, backend):
    return (await service.dispatch("session.create", {
        "project_id": project["id"], "backend": backend,
        "model": "synthetic-provider", "settings": {"tools": True},
    }))["session"]


async def turn(service, provider, session, *, mode="execute"):
    result = await service.dispatch("run.start", {
        "session_id": session["id"], "request_id": str(uuid.uuid4()),
        "prompt": "Create a blue brush poster reading Synthetic poster caption",
        "mode": mode,
    })
    context = await asyncio.wait_for(provider.started.get(), 4)
    assert context.run_id == result["run"]["id"]
    return context


async def tool(ctx, action, payload=None):
    return await ctx.client_tool_call(str(uuid.uuid4()), "kira.studio", {
        "action": action, "payload": payload or {},
    })


async def enable_project(service, project):
    return await service.dispatch("studio.enable", {
        "project_id": project["id"], "enabled": True,
        "expected_enabled": False, "request_id": str(uuid.uuid4()),
    })


async def grant(service, project, session, enabled, expected):
    return await service.dispatch("studio.grant", {
        "project_id": project["id"], "session_id": session["id"],
        "enabled": enabled, "expected_enabled": expected,
        "request_id": str(uuid.uuid4()),
    })


async def finish(service, provider, ctx):
    provider.release.set()
    await asyncio.wait_for(service.tasks[ctx.run_id], 8)


@pytest.mark.parametrize("backend", ["codex", "openai_chatgpt", "ollama"])
@pytest.mark.asyncio
async def test_opt_in_provider_gateway_attributed_edits_and_immediate_revoke(
        fixture, backend):
    service, provider, project = fixture
    session = await chat(service, project, backend)
    initial = await service.dispatch("studio.status", {
        "project_id": project["id"], "session_id": session["id"],
    })
    assert initial["agent_enabled"] is False
    assert initial["enabled"] is False
    assert not service.studio._renders
    await enable_project(service, project)
    await grant(service, project, session, True, False)

    ctx = await turn(service, provider, session)
    assert STUDIO_TOOL_NAMES <= validate_tools(ctx)
    assert STUDIO_TOOL_NAMES <= KIRA_RELAY_TOOLS
    assert studio_tool_specs()[0]["name"] == "studio"

    info = await tool(ctx, "inspect")
    assert info["ok"] and info["revision"] == 0
    result = await tool(ctx, "poster", {
        "text": "Synthetic poster caption", "expected_revision": 0,
        "text_color": "#2467c9", "background": "#f0e7f3",
    })
    assert result["ok"] and result["revision"] == 1
    assert result["scene"]["objects"][-1]["text"] == "Synthetic poster caption"
    row = service.store.db.execute(
        "SELECT origin FROM studio_revisions WHERE project_id=? AND revision=1",
        (project["id"],),
    ).fetchone()
    assert row["origin"] == "agent"
    author = service.store.db.execute(
        "SELECT run_id,session_id FROM studio_agent_commits WHERE project_id=? AND revision=1",
        (project["id"],),
    ).fetchone()
    assert author["run_id"] == ctx.run_id and author["session_id"] == session["id"]

    patch = await tool(ctx, "patch", {
        "object_id": "message", "values": {"color": "#182b68"}, "expected_revision": 1,
    })
    assert patch["ok"] and patch["revision"] == 2
    old_rev = await tool(ctx, "patch", {
        "object_id": "message", "values": {"text": "stale"}, "expected_revision": 1,
    })
    assert not old_rev["ok"] and old_rev["error"]["code"] == "CONFLICT"

    await grant(service, project, session, False, True)
    rejected = await tool(ctx, "patch", {
        "object_id": "message", "values": {"text": "should not change"},
        "expected_revision": 2,
    })
    assert not rejected["ok"] and rejected["error"]["code"] == "PERMISSION_DENIED"
    assert (await service.dispatch("studio.status", {
        "project_id": project["id"],
    }))["revision"] == 2
    await finish(service, provider, ctx)


@pytest.mark.asyncio
async def test_disabled_or_ungranted_chat_does_not_receive_studio_tool(fixture):
    service, provider, project = fixture
    session = await chat(service, project, "ollama")
    ctx = await turn(service, provider, session)
    assert "kira.studio" not in validate_tools(ctx)
    await finish(service, provider, ctx)
    await enable_project(service, project)
    session2 = await chat(service, project, "ollama")
    provider.release = asyncio.Event()
    ctx2 = await turn(service, provider, session2)
    assert "kira.studio" not in validate_tools(ctx2)
    await finish(service, provider, ctx2)


@pytest.mark.asyncio
async def test_disabling_world_retracts_chat_grants_and_reenable_does_not_restore(fixture):
    service, _, project = fixture
    session = await chat(service, project, "codex")
    await enable_project(service, project)
    await grant(service, project, session, True, False)
    await service.dispatch("studio.enable", {
        "project_id": project["id"], "enabled": False,
        "expected_enabled": True, "request_id": str(uuid.uuid4()),
    })
    state = await service.dispatch("studio.status", {
        "project_id": project["id"], "session_id": session["id"],
    })
    assert not state["enabled"] and not state["agent_enabled"]
    await enable_project(service, project)
    state = await service.dispatch("studio.status", {
        "project_id": project["id"], "session_id": session["id"],
    })
    assert state["enabled"] and not state["agent_enabled"]


@pytest.mark.asyncio
async def test_grants_are_chat_scoped_idempotent_and_foreign_sessions_denied(fixture):
    service, _, project = fixture
    await enable_project(service, project)
    session_a = await chat(service, project, "openai_chatgpt")
    session_b = await chat(service, project, "openai_chatgpt")
    rid = str(uuid.uuid4())
    params = {"project_id": project["id"], "session_id": session_a["id"],
              "enabled": True, "expected_enabled": False, "request_id": rid}
    first = await service.dispatch("studio.grant", params)
    assert first["agent_enabled"]
    assert await service.dispatch("studio.grant", params) == first
    with pytest.raises(KiraError):
        await service.dispatch("studio.grant", {**params, "session_id": session_b["id"]})
    other = await service.dispatch("studio.status", {
        "project_id": project["id"], "session_id": session_b["id"],
    })
    assert not other["agent_enabled"]
    await grant(service, project, session_a, False, True)
    assert not (await service.dispatch("studio.status", {
        "project_id": project["id"], "session_id": session_a["id"],
    }))["agent_enabled"]


@pytest.mark.asyncio
async def test_read_only_projects_cannot_grant_agent_writes(fixture):
    service, _, project = fixture
    session = await chat(service, project, "codex")
    await enable_project(service, project)
    service.store.db.execute(
        "UPDATE projects SET permission='read_only' WHERE id=?", (project["id"],)
    )
    with pytest.raises(KiraError, match="project write"):
        await grant(service, project, session, True, False)
    assert not service.studio.grant_state(project["id"], session["id"])["agent_enabled"]



@pytest.mark.asyncio
async def test_model_change_invalidates_previous_grant_even_if_reverted(fixture):
    service, _, project = fixture
    session = await chat(service, project, "codex")
    await enable_project(service, project)
    await grant(service, project, session, True, False)
    sid = session["id"]
    assert service.studio.grant_state(project["id"], sid)["agent_enabled"]
    # Real configure path clears the grant, not merely an ephemeral model check.
    await service.dispatch("session.configure", {
        "project_id": project["id"], "session_id": sid, "model": "another-model",
    })
    assert not service.studio.grant_state(project["id"], sid)["agent_enabled"]
    await service.dispatch("session.configure", {
        "project_id": project["id"], "session_id": sid, "model": "synthetic-provider",
    })
    assert not service.studio.grant_state(project["id"], sid)["agent_enabled"]


@pytest.mark.asyncio
async def test_project_permission_transition_retracts_grant(fixture):
    service, _, project = fixture
    session = await chat(service, project, "codex")
    await enable_project(service, project)
    await grant(service, project, session, True, False)
    await service.dispatch("project.update", {
        "project_id": project["id"], "permission": "read_only",
    })
    assert not service.studio.grant_state(project["id"], session["id"])["agent_enabled"]
    await service.dispatch("project.update", {
        "project_id": project["id"], "permission": "project_write",
    })
    assert not service.studio.grant_state(project["id"], session["id"])["agent_enabled"]


@pytest.mark.asyncio
async def test_archived_chat_grant_does_not_reactivate_when_restored(fixture):
    service, _, project = fixture
    session = await chat(service, project, "ollama")
    await enable_project(service, project)
    await grant(service, project, session, True, False)
    await service.dispatch("session.archive", {
        "project_id": project["id"], "session_id": session["id"],
        "archived": True,
    })
    await service.dispatch("session.archive", {
        "project_id": project["id"], "session_id": session["id"],
        "archived": False,
    })
    assert not service.studio.grant_state(project["id"], session["id"])["agent_enabled"]
