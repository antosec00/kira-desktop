"""Actual Desktop/IPC/service/database identity journey with a synthetic backend."""

import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QMetaObject

from kira.domain.paths import Paths
from test_ui_support import SERVICE_WAIT_MS, isolated_service_environment, start_service, stop_service
from test_ui_workbench_ipc import close_desktop, open_desktop, visual_item

FIXTURE = Path(__file__).parent / "fixtures" / "souls_service.py"


def test_first_message_identity_edit_disable_and_draft_restart(qapp, qtbot, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-soul-ipc-") as directory:
        home = Path(directory)
        project = home / "project"
        project.mkdir()
        control = home / "control"
        control.mkdir()
        paths = Paths(*(home / part for part in ("config", "data", "state", "runtime")))
        environment = isolated_service_environment(monkeypatch, home, KIRA_TEST_SOUL_CONTROL=control)
        with (home / "service.log").open("w+", encoding="utf-8") as log:
            process = start_service([FIXTURE], environment, log)
            desktop = None
            try:
                qtbot.waitUntil(lambda: paths.socket.exists() or process.poll() is not None, timeout=SERVICE_WAIT_MS)
                assert process.poll() is None
                desktop = open_desktop(paths)
                view, engine, window = desktop
                qtbot.waitUntil(lambda: view.connected and bool(view.models), timeout=SERVICE_WAIT_MS)
                view.createProject("Synthetic identities", str(project), "", "read_only")
                qtbot.waitUntil(lambda: bool(view.project) and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                view.setModel("synthetic-soul-model")
                view.setEffort("high")
                souls = engine.rootContext().contextProperty("souls")
                chip = visual_item(window.contentItem(), "chatSoulButton")
                assert chip is not None and chip.isEnabled()
                QMetaObject.invokeMethod(chip, "clicked")
                qtbot.waitUntil(lambda: souls.visible and not souls.busy, timeout=SERVICE_WAIT_MS)
                souls.edit("Synthetic Studio", "FIRST_TURN_CORE_CANARY", "suggest")
                souls.save()
                qtbot.waitUntil(lambda: bool(souls.selected["id"]) and not souls.busy and not souls.pending, timeout=SERVICE_WAIT_MS)
                soul_id = souls.selected["id"]
                assert not view.sessionId
                assert souls.canBind
                souls.applyToChat()
                qtbot.waitUntil(lambda: souls.newChatSoulId == soul_id and not souls.busy, timeout=SERVICE_WAIT_MS)
                assert not (control / "requests.jsonl").exists()
                view.send("First synthetic request")
                qtbot.waitUntil(lambda: (control / "requests.jsonl").exists() and not view.busy, timeout=SERVICE_WAIT_MS)
                first = json.loads((control / "requests.jsonl").read_text().splitlines()[0])
                assert "FIRST_TURN_CORE_CANARY" in first["context"]
                assert first["model"] == "synthetic-soul-model" and first["settings"]["reasoning_effort"] == "high"
                assert not souls.newChatSoulId and view.sessionId
                session_id, project_id = view.sessionId, view.project["id"]

                souls.open()
                qtbot.waitUntil(lambda: not souls.busy, timeout=SERVICE_WAIT_MS)
                souls.select(soul_id)
                qtbot.waitUntil(lambda: souls.selected["id"] == soul_id and not souls.busy, timeout=SERVICE_WAIT_MS)
                souls.edit("Synthetic Studio", "SECOND_TURN_CORE_CANARY", "suggest")
                souls.save()
                qtbot.waitUntil(lambda: souls.selected["revision"] == 2 and not souls.busy and not souls.pending, timeout=SERVICE_WAIT_MS)
                view.send("Second synthetic request")
                qtbot.waitUntil(lambda: len((control / "requests.jsonl").read_text().splitlines()) == 2
                                and not view.busy, timeout=SERVICE_WAIT_MS)
                second = json.loads((control / "requests.jsonl").read_text().splitlines()[1])
                assert "SECOND_TURN_CORE_CANARY" in second["context"]
                assert "FIRST_TURN_CORE_CANARY" not in second["context"]
                souls.refreshSnapshot()
                qtbot.waitUntil(lambda: not souls.busy and souls.canBind, timeout=SERVICE_WAIT_MS)
                souls.disableForChat()
                qtbot.waitUntil(lambda: not souls.busy and not souls.pending and souls.snapshot.get("disabled") is True, timeout=SERVICE_WAIT_MS)
                view.send("Third synthetic request")
                qtbot.waitUntil(lambda: len((control / "requests.jsonl").read_text().splitlines()) == 3
                                and not view.busy, timeout=SERVICE_WAIT_MS)
                third = json.loads((control / "requests.jsonl").read_text().splitlines()[2])
                assert "earlier profile defaults no longer apply" in third["context"]
                assert "SECOND_TURN_CORE_CANARY" not in third["context"]

                prompt = visual_item(window.contentItem(), "prompt")
                prompt.setProperty("text", "Unsent chat draft <fixture>")
                view.flushDrafts()
                souls.edit("Synthetic Studio", "Unsent identity draft <fixture>", "suggest")
                close_desktop(desktop)
                desktop = None
                desktop = open_desktop(paths)
                view, engine, _ = desktop
                qtbot.waitUntil(lambda: view.connected, timeout=SERVICE_WAIT_MS)
                view.openProject(project_id)
                qtbot.waitUntil(lambda: view.sessionId == session_id and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                assert view.draftText == "Unsent chat draft <fixture>"
                souls = engine.rootContext().contextProperty("souls")
                souls.open()
                qtbot.waitUntil(lambda: not souls.busy, timeout=SERVICE_WAIT_MS)
                souls.select(soul_id)
                qtbot.waitUntil(lambda: souls.selected["id"] == soul_id and not souls.busy, timeout=SERVICE_WAIT_MS)
                assert souls.draft["core"] == "Unsent identity draft <fixture>"
                assert len((control / "requests.jsonl").read_text().splitlines()) == 3
            finally:
                if desktop is not None:
                    close_desktop(desktop)
                stop_service(process)
