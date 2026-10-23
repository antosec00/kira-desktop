"""Workspace/native-dispatch contract fixtures; native GUI qualification is separate."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError, SCHEMA_VERSION
from kira.domain.paths import Paths
from kira.service import desktop
from kira.service.desktop import ACTIONS, Desktop, native_environment
from kira.service.server import Service
from kira.service.workspace import MAX_WATCH_BYTES

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFqsAAAAASUVORK5CYII=")


class FixtureDesktop:
    def __init__(self):
        self.calls = []
        self.fail_on = None
        self.crash_on = None
        self.capture = PNG
        self.block = None

    def catalog(self, project=None, query=""):
        return [{"id": item.id, "label": item.label, "description": item.description,
                 "available": not item.requires_project or project is not None,
                 "reason": "" if not item.requires_project or project else "Select a project",
                 "requires_project": item.requires_project} for item in ACTIONS
                if query.casefold() in (item.id + " " + item.label).casefold()]

    async def launch(self, action_id, project, query, capture_path=None):
        self.calls.append((action_id, project, query))
        if self.block:
            await self.block.wait()
        if action_id == self.crash_on:
            raise asyncio.CancelledError
        if action_id == self.fail_on:
            raise KiraError("NATIVE_FAILED", "Fixture launch failure")
        if capture_path:
            capture_path.write_bytes(self.capture)


@pytest_asyncio.fixture
async def workspace(monkeypatch, tmp_path):
    # Every service start builds its launcher; patch the class so no start or
    # restart in these tests can reach the real native desktop launcher.
    fixture = FixtureDesktop()
    monkeypatch.setattr("kira.service.workspace.Desktop", lambda: fixture)
    with tempfile.TemporaryDirectory(prefix="kira-workspace-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        service = Service(Paths.from_env())
        await service.start()
        assert service.workspaces.desktop is fixture
        project = (await service.dispatch("project.create", {"root": str(tmp_path), "name": "Workspace fixture"}))["project"]
        try:
            yield service, fixture, project
        finally:
            await service.close()


async def test_preferences_resume_and_file_freshness_survive_restart(workspace):
    service, fixture, project = workspace
    params = {"project_id": project["id"]}
    path = Path(project["root"]) / "evidence.txt"
    path.write_text("initial evidence")
    state = await service.dispatch("workspace.update", {**params, "tools": ["terminal"], "next_step": "Review evidence"})
    assert state["tools"] == ["terminal"] and state["resume"]["last_run"] is None
    state = await service.dispatch("workspace.watch", {**params, "path": "evidence.txt"})
    assert state["watches"][0]["state"] == "unchanged"
    original_hash = state["watches"][0]["sha256"]
    session = (await service.dispatch("session.create", {**params, "backend": "codex", "model": "fixture-model"}))["session"]
    run, _ = service.store.create_run(session["id"], "unfinished-fixture", "fixture prompt")
    await service.close()
    await service.start()
    assert service.workspaces.desktop is fixture
    state = await service.dispatch("workspace.get", params)
    assert state["tools"] == ["terminal"] and state["next_step"] == "Review evidence"
    assert state["resume"]["last_run"]["id"] == run["id"]
    assert state["resume"]["last_run"]["state"] == "interrupted"
    assert state["resume"]["interrupted_count"] == 1 and state["resume"]["active_count"] == 0
    assert fixture.calls == []
    path.write_text("changed evidence")
    state = await service.dispatch("workspace.get", params)
    assert state["watches"][0]["state"] == "changed"
    assert state["watches"][0]["sha256"] == original_hash
    assert state["watches"][0]["current_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    await service.dispatch("workspace.watch", {**params, "path": "evidence.txt"})
    assert (await service.dispatch("workspace.get", params))["watches"][0]["state"] == "unchanged"
    path.unlink()
    assert (await service.dispatch("workspace.get", params))["watches"][0]["state"] == "missing"
    path.write_text("keep this file")
    assert not (await service.dispatch("workspace.unwatch", {**params, "path": "evidence.txt"}))["watches"]
    assert path.read_text() == "keep this file"


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape", ".", "a/../../escape", "\x00", 42])
async def test_watch_requires_relative_contained_regular_file(workspace, path):
    service, _, project = workspace
    with pytest.raises(KiraError):
        await service.dispatch("workspace.watch", {"project_id": project["id"], "path": path})


async def test_symlinks_fifos_large_and_replaced_files_report_unavailable(workspace):
    service, _, project = workspace
    root = Path(project["root"])
    params = {"project_id": project["id"]}
    (root / "link").symlink_to("/etc/passwd")
    os.mkfifo(root / "pipe")
    with (root / "large").open("wb") as stream:
        stream.truncate(MAX_WATCH_BYTES + 1)
    for name in ("link", "pipe", "large"):
        with pytest.raises(KiraError):
            await asyncio.wait_for(service.dispatch("workspace.watch", {**params, "path": name}), 3)
    path = root / "watched"
    path.write_text("regular")
    await service.dispatch("workspace.watch", {**params, "path": "watched"})
    path.unlink()
    os.mkfifo(path)
    state = await asyncio.wait_for(service.dispatch("workspace.get", params), 3)
    assert state["watches"][0]["state"] == "unavailable"
    assert state["watches"][0]["current_sha256"] is None
    assert state["watches"][0]["reason"]


async def test_native_request_is_durable_idempotent_and_conflicting_reuse_rejected(workspace):
    service, fixture, project = workspace
    params = {"action_id": "project.folder", "project_id": project["id"], "request_id": "one-click"}
    first = await service.dispatch("desktop.launch", params)
    assert first["status"] == "requested" and len(fixture.calls) == 1
    assert await service.dispatch("desktop.launch", params) == first
    with pytest.raises(KiraError, match="different input"):
        await service.dispatch("desktop.launch", {**params, "action_id": "project.terminal"})
    await service.close()
    await service.start()
    assert service.workspaces.desktop is fixture
    assert await service.dispatch("desktop.launch", params) == first
    assert len(fixture.calls) == 1


async def test_concurrent_retry_does_not_duplicate_inflight_effect(workspace):
    service, fixture, project = workspace
    fixture.block = asyncio.Event()
    params = {"action_id": "project.folder", "project_id": project["id"], "request_id": "inflight"}
    first = asyncio.create_task(service.dispatch("desktop.launch", params))
    await asyncio.sleep(0)
    duplicate = await service.dispatch("desktop.launch", params)
    assert duplicate["status"] == "unknown" and len(fixture.calls) == 1
    fixture.block.set()
    assert (await first)["status"] == "requested"


async def test_partial_workspace_crash_preserves_confirmed_and_unknown_without_replay(workspace):
    service, fixture, project = workspace
    params = {"project_id": project["id"], "request_id": "workspace-click"}
    fixture.crash_on = "project.terminal"
    with pytest.raises(asyncio.CancelledError):
        await service.dispatch("workspace.launch", params)
    await service.close()
    await service.start()
    assert service.workspaces.desktop is fixture
    await service.dispatch("workspace.update", {"project_id": project["id"], "tools": ["editor"]})
    duplicate = await service.dispatch("workspace.launch", params)
    assert [result["status"] for result in duplicate["results"]] == ["requested", "unknown", "unknown"]
    assert [call[0] for call in fixture.calls] == ["project.folder", "project.terminal"]
    assert service.store.db.execute("SELECT status FROM desktop_requests").fetchone()[0] == "unknown"


async def test_launch_failures_are_visible_and_not_retried(workspace):
    service, fixture, project = workspace
    fixture.fail_on = "project.terminal"
    params = {"project_id": project["id"], "action_id": "project.terminal", "request_id": "failed-click"}
    result = await service.dispatch("desktop.launch", params)
    assert result["status"] == "failed" and "Fixture" in result["message"]
    assert await service.dispatch("desktop.launch", params) == result
    assert len(fixture.calls) == 1
    unavailable = await service.dispatch("desktop.launch", {"action_id": "project.folder", "request_id": "missing-project"})
    assert unavailable["status"] == "unavailable" and len(fixture.calls) == 1


async def test_capture_requires_completed_png_and_keeps_private_project_reference(workspace):
    service, fixture, project = workspace
    params = {"project_id": project["id"], "action_id": "screenshot.capture", "request_id": "capture-click"}
    result = await service.dispatch("desktop.launch", params)
    assert result["status"] == "completed"
    artifact = result["artifact"]
    capture = Path(artifact["path"])
    assert capture.read_bytes() == PNG and capture.stat().st_mode & 0o777 == 0o600
    assert capture.parent.stat().st_mode & 0o777 == 0o700
    assert artifact["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert (await service.dispatch("workspace.get", {"project_id": project["id"]}))["captures"] == [artifact]
    exported = json.loads((await service.dispatch("project.export", {"project_id": project["id"]}))["text"])
    assert exported["workspace"]["captures"] == [artifact]
    assert exported["workspace"]["captures_truncated"] is False
    assert await service.dispatch("desktop.launch", params) == result
    fixture.capture = PNG[:-12]
    failed = await service.dispatch("desktop.launch", {**params, "request_id": "partial-capture"})
    assert failed["status"] == "failed" and "artifact" not in failed
    assert len(list(capture.parent.iterdir())) == 1


@pytest.mark.parametrize("method,extra", [
    ("workspace.update", {"tools": ["shell"]}),
    ("workspace.update", {"tools": ["folder", "folder"]}),
    ("workspace.update", {"next_step": []}),
    ("desktop.launch", {"action_id": "run.shell", "request_id": "invalid"}),
    ("desktop.launch", {"action_id": "browser.search", "request_id": "invalid", "query": ""}),
    ("desktop.launch", {"action_id": "browser.open", "request_id": "invalid", "argv": ["sh"]}),
])
async def test_native_and_workspace_inputs_are_constrained(workspace, method, extra):
    service, fixture, project = workspace
    with pytest.raises(KiraError):
        await service.dispatch(method, {"project_id": project["id"], **extra})
    assert not fixture.calls


async def test_diagnostics_export_whitelists_values_and_excludes_content(workspace, monkeypatch):
    service, fixture, project = workspace
    canary = "fake-private-canary-account-and-token"
    monkeypatch.setenv("UNRELATED_ENV", canary)
    monkeypatch.setenv("XDG_SESSION_TYPE", canary)
    await service.dispatch("project.update", {"project_id": project["id"], "name": canary,
                                             "instructions": canary, "memory": {"private": canary}})
    await service.dispatch("workspace.update", {"project_id": project["id"], "next_step": canary})
    report = await service.dispatch("system.diagnostics", {})
    exported = await service.dispatch("diagnostics.export", {})
    data = Path(exported["path"]).read_text()
    assert json.loads(data) == report
    assert report["database_schema"] == SCHEMA_VERSION and report["event_schema"] == 1
    assert report["platform"]["session"] == "unknown"
    assert set(report) == {"app_version", "database_schema", "event_schema", "platform", "service", "actions"}
    assert canary not in data and project["root"] not in data and project["id"] not in data
    assert Path(exported["path"]).stat().st_mode & 0o777 == 0o600


def test_fixed_native_argv_and_clean_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "executable", lambda names: "/usr/bin/" + names[0])
    monkeypatch.setenv("QT_PLUGIN_PATH", "/bundled/qt/plugins")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundled/lib")
    monkeypatch.setenv("PYTHONHOME", "/bundled/python")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.5")
    environment = native_environment()
    assert not {"QT_PLUGIN_PATH", "LD_LIBRARY_PATH", "PYTHONHOME"} & environment.keys()
    assert environment["WAYLAND_DISPLAY"] == "wayland-0"
    assert environment["QT_SCALE_FACTOR"] == "1.5"
    launcher = Desktop()
    project = {"root": str(tmp_path)}
    argv, cwd = launcher.argv("project.terminal", project, "")
    assert argv == ["/usr/bin/konsole", "--workdir", str(tmp_path)] and cwd == tmp_path
    argv, cwd = launcher.argv("project.editor", project, "")
    assert argv == ["/usr/bin/kate", "--startanon"] and cwd == tmp_path
    assert not (tmp_path / ".kateproject").exists()
    argv, _ = launcher.argv("browser.search", None, '$(touch /tmp/never); "meteor"')
    assert len(argv) == 3 and argv[2].startswith("https://www.google.com/search?q=%24%28")
    assert " " not in argv[2]


def test_catalog_has_search_and_honest_unavailable_state(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(desktop, "executable", lambda names: None)
    actions = Desktop().catalog(query="backup")
    assert len(actions) == 1 and actions[0]["id"] == "system.backup"
    assert not actions[0]["available"] and "deja-dup" in actions[0]["reason"]
