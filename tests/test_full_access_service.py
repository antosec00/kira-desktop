"""Explicit filesystem scope through actual run-owned service callbacks."""

import asyncio
import sys
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from test_assistant_service import env as env, settle as team_settle, start as team_start
from test_project_tools_service import (call, manifest, project_tools as project_tools,
                                        ref, SOURCE, start as tool_start)
from test_service_runtime import runtime as runtime


def folders(service, project):
    base = Path(project["root"])
    workspace, outside = base / "workspace", base / "sibling"
    workspace.mkdir()
    outside.mkdir()
    service.store.db.execute("UPDATE projects SET root=? WHERE id=?", (str(workspace), project["id"]))
    return workspace, outside


@pytest.mark.parametrize("permission", ["read_only", "project_write", "trusted_host", "full_access"])
async def test_only_explicit_full_access_enables_external_files_with_durable_receipts(runtime, permission):
    service, backend, project, session = runtime
    workspace, outside = folders(service, project)
    (outside / "input.txt").write_text("Synthetic external evidence")
    await service.dispatch("project.update", {"project_id": project["id"], "permission": permission})
    run = (await service.dispatch("run.start", {"session_id": session["id"],
        "request_id": "file-scope", "prompt": "Inspect and write the explicitly selected sibling folder."}))["run"]
    await asyncio.wait_for(backend.started.wait(), 30)
    context = backend.contexts[0]
    read = await context.execute_tool("read_file", {"path": str(outside / "input.txt")})
    write = await context.execute_tool("write_file", {"path": "../sibling/output.txt", "content": "Confirmed synthetic output"})
    assert read["ok"] is write["ok"] is (permission == "full_access")
    if permission == "full_access":
        assert (outside / "output.txt").read_text() == "Confirmed synthetic output"
        assert write["kind"] == "host_file" and write["path"] == str(outside / "output.txt")
        listed = await context.execute_tool("list_files", {"path": str(outside)})
        assert {entry["name"] for entry in listed["entries"]} == {"input.txt", "output.txt"}
        search = await context.execute_tool("search_files", {"path": str(outside), "query": "Synthetic"})
        assert search["matches"][0]["path"] == str(outside / "input.txt")
        assert "Explicit full_access is enabled" in context.supplied_context
        assert '"filesystem_scope":"user_accessible_paths"' in context.supplied_context
        artifact = service.store.all("SELECT path,kind FROM artifacts WHERE run_id=?", (run["id"],))
        assert artifact == [{"path": str(outside / "output.txt"), "kind": "host_file"}]
    else:
        assert not (outside / "output.txt").exists()
        assert read["error"]["code"] == "PATH_DENIED"
    command = await context.execute_tool("run_command", {"argv": [sys.executable, "-c", "print('synthetic-host-command')"]})
    assert command["ok"] is (permission in {"trusted_host", "full_access"})
    assert context.root == workspace and context.model == session["model"]
    backend.release.set()
    await asyncio.wait_for(service.tasks[run["id"]], 30)
    assert service.store.one("projects", project["id"])["permission"] == permission
    assert not service.store.all("SELECT * FROM project_reservations")


@pytest.mark.parametrize("mode", ["plan", "review", "recap"])
async def test_full_access_still_downscopes_restricted_modes(runtime, mode):
    service, backend, project, session = runtime
    _, outside = folders(service, project)
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "full_access"})
    run = (await service.dispatch("run.start", {"session_id": session["id"], "request_id": mode,
        "prompt": "Discuss the synthetic change", "mode": mode}))["run"]
    await asyncio.wait_for(backend.started.wait(), 30)
    context = backend.contexts[0]
    assert context.permission == "read_only"
    for name, arguments in (("write_file", {"path": str(outside / "no-write.txt"), "content": "no"}),
                            ("run_command", {"argv": [sys.executable, "-c", "raise AssertionError('must not execute')"]})):
        result = await context.execute_tool(name, arguments)
        assert result["error"]["code"] == "PERMISSION_DENIED"
    assert not (outside / "no-write.txt").exists()
    backend.release.set()
    await asyncio.wait_for(service.tasks[run["id"]], 30)
    assert service.store.one("projects", project["id"])["permission"] == "full_access"


async def test_full_access_needs_explicit_idle_permission_selection(runtime):
    service, backend, project, session = runtime
    assert project["permission"] == "read_only"
    run = (await service.dispatch("run.start", {"session_id": session["id"], "request_id": "hold",
        "prompt": "Hold a synthetic conversation"}))["run"]
    await asyncio.wait_for(backend.started.wait(), 30)
    with pytest.raises(KiraError, match="active work"):
        await service.dispatch("project.update", {"project_id": project["id"], "permission": "full_access"})
    backend.release.set()
    await asyncio.wait_for(service.tasks[run["id"]], 30)
    result = await service.dispatch("project.update", {"project_id": project["id"], "permission": "full_access"})
    assert result["project"]["permission"] == "full_access"


async def test_full_access_keeps_registered_project_tools_runnable(project_tools):
    service, _, project, _ = project_tools
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "full_access"})
    context = await tool_start(project_tools)
    written = await context.execute_tool("write_file", {"path": "created.py", "content": SOURCE})
    registered = await call(context, "register", {"source_path": "created.py", "expected_sha256": written["sha256"], "manifest": manifest()})
    assert registered["ok"]
    tool = ref(registered)
    checked = await call(context, "test", tool)
    assert checked["ok"] and checked["test"]["cases_passed"] == 1
    result = await call(context, "run", {**tool, "arguments": {"a": 7, "b": 11}})
    assert result["ok"] and result["result"] == {"sum": 18}
    assert service.project_tools.data.get(project["id"], tool["tool_id"])["tool"]["runnable"]


async def test_full_access_is_preserved_by_automatic_team_result_lane(env):
    service, factory, project, session = env
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "full_access"})
    run = await team_start(env)
    await team_settle(service, run["id"])
    await asyncio.wait_for(factory.followup_started.wait(), 5)
    followup = factory.followup_contexts[0]
    await team_settle(service, followup.run_id)
    assert followup.permission == "full_access"
    assert service.store.run_lane(followup.run_id)["effective_permission"] == "full_access"
    assert followup.session["id"] == session["id"]
