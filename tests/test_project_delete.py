"""Safe project deletion without touching user files on disk."""

import asyncio
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt
from PySide6.QtTest import QTest
from shiboken6 import isValid

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import now
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def _make_paths(base: Path) -> Paths:
    return Paths(*(base / key for key in ("config", "data", "state", "runtime")))


def _find(window: QObject, name: str) -> QObject | None:
    result = window.findChild(QObject, name)
    if result is not None:
        return result

    def visit(item):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            if found := visit(child):
                return found
        return None

    return visit(window.contentItem())


def test_project_delete_purges_metadata_without_touching_disk_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = Service(_make_paths(tmp_path / "profile"))
        await svc.start()
        try:
            root_a = tmp_path / "user-repo-a"
            root_b = tmp_path / "user-repo-b"
            root_a.mkdir()
            root_b.mkdir()
            user_file = root_a / "important.py"
            user_file.write_text("VALUE = 42\n", encoding="utf-8")

            proj_a = (await svc.dispatch("project.create", {
                "name": "Alpha", "root": str(root_a), "instructions": "", "permission": "read_only",
            }))["project"]
            proj_b = (await svc.dispatch("project.create", {
                "name": "Beta", "root": str(root_b), "instructions": "", "permission": "read_only",
            }))["project"]

            sess_a = (await svc.dispatch("session.create", {
                "project_id": proj_a["id"], "backend": "ollama", "model": "qwen3",
            }))["session"]
            sess_b = (await svc.dispatch("session.create", {
                "project_id": proj_b["id"], "backend": "ollama", "model": "qwen3",
            }))["session"]

            run_a, _ = svc.store.create_run(sess_a["id"], "req-a", "inspect repo")
            svc.store.event(run_a["id"], "run.state", {"state": "completed"})
            run_b, _ = svc.store.create_run(sess_b["id"], "req-b", "inspect beta")
            svc.store.event(run_b["id"], "run.state", {"state": "completed"})

            owned_index = svc.paths.data / "code_index" / proj_a["id"]
            owned_index.mkdir(parents=True)
            (owned_index / "index.db").write_text("idx", encoding="utf-8")

            deleted = await svc.dispatch("project.delete", {"project_id": proj_a["id"]})
            assert deleted == {"deleted": True, "project_id": proj_a["id"], "purged": True}

            # User folder and file on disk remain untouched.
            assert root_a.is_dir()
            assert user_file.read_text(encoding="utf-8") == "VALUE = 42\n"
            assert not owned_index.exists()

            # Sibling project and composite-FK bindings remain intact.
            listed = (await svc.dispatch("project.list", {}))["projects"]
            assert [p["id"] for p in listed] == [proj_b["id"]]
            with pytest.raises(KiraError) as exc:
                await svc.dispatch("project.snapshot", {"project_id": proj_a["id"]})
            assert exc.value.code == "NOT_FOUND"
            assert svc.store.db.execute("SELECT COUNT(*) FROM run_provider_bindings").fetchone()[0] == 1
            assert svc.store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            await svc.close()

    asyncio.run(scenario())


def test_project_delete_rejects_active_work_and_preserves_profile_soul_memories(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = Service(_make_paths(tmp_path / "profile"))
        await svc.start()
        try:
            root = tmp_path / "repo"
            root.mkdir()
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            proj = (await svc.dispatch("project.create", {
                "name": "ActiveRepo", "root": str(root), "instructions": "", "permission": "read_only",
            }))["project"]
            sess = (await svc.dispatch("session.create", {
                "project_id": proj["id"], "backend": "ollama", "model": "qwen3",
            }))["session"]

            run, _ = svc.store.create_run(sess["id"], "req-busy", "still running")
            with pytest.raises(KiraError) as busy_exc:
                await svc.dispatch("project.delete", {"project_id": proj["id"]})
            assert busy_exc.value.code == "BUSY"

            svc.store.event(run["id"], "run.state", {"state": "completed"})

            # Attach a profile-scoped Soul memory referencing this project.
            soul = (await svc.dispatch("soul.create", {
                "request_id": "req-soul-1",
                "name": "Reviewer",
                "core": "Concise code reviewer",
            }))["soul"]
            ts = now()
            svc.store.db.execute(
                "INSERT INTO soul_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("src-1", soul["id"], proj["id"], sess["id"], run["id"], 1, 1, "turn", "msg", "dig", ts),
            )
            svc.store.db.execute(
                "INSERT INTO soul_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("prop-1", soul["id"], "src-1", proj["id"], sess["id"], "preference", "profile",
                 "Prefer concise diffs", "Always", None, None, "accepted", ts, ts),
            )
            svc.store.db.execute(
                "INSERT INTO soul_memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("mem-prof-1", soul["id"], "prop-1", "src-1", proj["id"], sess["id"],
                 "profile", "preference", "Prefer concise diffs", "Always", ts),
            )

            deleted = await svc.dispatch("project.delete", {"project_id": proj["id"]})
            assert deleted == {"deleted": True, "project_id": proj["id"], "purged": False}
            assert (await svc.dispatch("project.list", {}))["projects"] == []
            assert (root / "README.md").read_text(encoding="utf-8") == "# Demo\n"
            row = svc.store.db.execute(
                "SELECT statement FROM soul_memories WHERE id='mem-prof-1'"
            ).fetchone()
            assert row is not None and row[0] == "Prefer concise diffs"
        finally:
            await svc.close()

    asyncio.run(scenario())


def test_ui_delete_project_dialog_and_state_transition(qapp, qtbot, tmp_path: Path, monkeypatch) -> None:
    view = Workspace(_make_paths(tmp_path / "ui"))
    view._connected_value = True
    proj_a = {"id": "proj-a", "name": "Alpha UI", "root": str(tmp_path / "alpha"), "permission": "read_only"}
    proj_b = {"id": "proj-b", "name": "Beta UI", "root": str(tmp_path / "beta"), "permission": "read_only"}
    view._projects = [proj_a, proj_b]
    view._project = dict(proj_a)
    view._drafts[("proj-a", "chat-1")] = "unsent draft"

    calls: list[tuple[str, dict]] = []

    def fake_request(method, params, on_result=None, on_error=None):
        calls.append((method, dict(params)))
        if method == "project.delete" and on_result:
            on_result({"deleted": True, "project_id": params["project_id"], "purged": True})
        elif method == "project.snapshot" and on_result:
            on_result({"project": dict(proj_b), "sessions": [], "runs": [], "artifacts": [], "messages": []})
        elif method == "project.list" and on_result:
            on_result({"projects": [dict(proj_b)]})
        elif method == "session.list" and on_result:
            on_result({"sessions": [], "has_more": False})

    monkeypatch.setattr(view.client, "request", fake_request)
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    qtbot.wait(60)
    try:
        window.promptDeleteProject(proj_a)
        qtbot.wait(30)
        dialog = _find(window, "deleteProjectDialog")
        assert dialog is not None and dialog.property("opened")
        confirm = _find(window, "confirmDeleteProjectButton")
        assert confirm is not None and confirm.property("enabled")
        pt = confirm.mapToScene(QPoint(int(confirm.width() / 2), int(confirm.height() / 2))).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=pt)
        qtbot.wait(40)

        assert ("project.delete", {"project_id": "proj-a"}) in calls
        assert view._project.get("id") == "proj-b"
        assert ("proj-a", "chat-1") not in view._drafts
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(view)
