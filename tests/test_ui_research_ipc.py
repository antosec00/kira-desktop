"""Real QML, Unix IPC, service and SQLite research workflow; synthetic provider only."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from test_ui_support import SERVICE_WAIT_MS, isolated_service_environment, start_service, stop_service
from kira.ui.controller import Workspace

FIXTURE = Path(__file__).parent / "fixtures/research_service.py"


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def open_desktop(paths):
    workspace = Workspace(paths)
    workspace._preferences["onboarding_complete"] = True
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1000, 760)
    workspace.client.connect_service()
    research = engine.rootContext().contextProperty("projectResearch")
    return workspace, engine, window, research


def close_desktop(desktop):
    workspace, engine, window, research = desktop
    research.flushDrafts()
    window.setProperty("closeConfirmed", True)
    window.close()
    workspace.close()
    engine.deleteLater()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def clicked(window, name):
    item = window.findChild(QObject, name)
    if item is None:
        item = visual_item(window.contentItem(), name)
    assert item is not None and item.property("enabled"), name
    assert QMetaObject.invokeMethod(item, "clicked")


def wait_research(qtbot, research):
    qtbot.waitUntil(lambda: not research.busy, timeout=SERVICE_WAIT_MS)
    assert not research.error, research.error


def test_real_gui_research_evidence_recheck_correction_and_restart(qapp, qtbot, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="kira-research-ipc-") as directory:
        home = Path(directory)
        paths = Paths(*(home / part for part in ("config", "data", "state", "runtime")))
        control, project = home / "fixture-control", home / "synthetic-project"
        control.mkdir()
        project.mkdir()
        original = "Original synthetic evidence: literal <marker> Ω and criterion=7."
        dependency = project / "input.txt"
        dependency.write_text(original, encoding="utf-8")
        environment = isolated_service_environment(monkeypatch, home, KIRA_TEST_RESEARCH_CONTROL=control)
        monkeypatch.setenv("QML_DISK_CACHE_PATH", str(home / "cache/qml"))
        with (home / "service.log").open("w+", encoding="utf-8") as log:
            process = start_service([FIXTURE], environment, log)
            desktops = []
            try:
                qtbot.waitUntil(lambda: paths.socket.exists() or process.poll() is not None, timeout=SERVICE_WAIT_MS)
                assert process.poll() is None, (home / "service.log").read_text()
                desktop = open_desktop(paths)
                desktops.append(desktop)
                workspace, _, window, research = desktop
                qtbot.waitUntil(lambda: workspace.connected and bool(workspace.models), timeout=SERVICE_WAIT_MS)
                workspace.createProject("Synthetic research IPC", str(project), "", "read_only")
                qtbot.waitUntil(lambda: bool(workspace.project) and not workspace.sessionsLoading, timeout=SERVICE_WAIT_MS)
                project_id = workspace.project["id"]
                workspace.setModel("synthetic-research-model")
                workspace.setEffort("high")
                prompt = visual_item(window.contentItem(), "prompt")
                prompt.setProperty("text", "Read the one synthetic source file")
                send = visual_item(window.contentItem(), "sendButton")
                qtbot.waitUntil(send.isEnabled, timeout=SERVICE_WAIT_MS)
                QMetaObject.invokeMethod(send, "clicked")
                calls_path = control / "provider-calls.jsonl"
                qtbot.waitUntil(lambda: calls_path.exists() and not workspace.busy, timeout=SERVICE_WAIT_MS)
                qtbot.waitUntil(lambda: any("Synthetic research source retained." in row["body"]
                                           for row in workspace.transcript.rows), timeout=SERVICE_WAIT_MS)
                calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
                assert len(calls) == 1 and calls[0]["read_result"]["ok"]
                run_id, session_id = calls[0]["run_id"], workspace.sessionId
                chat_draft = "Unsent chat draft: /plan è <literal> %2"
                prompt.setProperty("text", chat_draft)
                workspace.flushDrafts()

                clicked(window, "projectResearchButton")
                dialog = window.findChild(QObject, "researchDialog")
                qtbot.waitUntil(lambda: dialog.property("visible") and not research.busy, timeout=SERVICE_WAIT_MS)
                assert research.report["settings"]["enabled"] is False
                clicked(window, "researchNew")
                title = "Synthetic conclusion <literal> Ω"
                window.findChild(QObject, "researchTitle").setProperty("text", title)
                window.findChild(QObject, "researchBody").setProperty("text", "Interpretation of one captured result.")
                research.setField("kind", "hypothesis")

                clicked(window, "researchSources")
                wait_research(qtbot, research)
                assert any(item["ref"].get("run_id") == run_id for item in research.picker["items"])
                research.openRunSources(run_id)
                wait_research(qtbot, research)
                tool = next(item for item in research.picker["items"]
                            if item["provenance"].get("event_type") == "tool.completed")
                source = tool["ref"]
                research.readSource(source)
                dialog.setProperty("page", "source")
                wait_research(qtbot, research)
                assert original in research.source["content"]
                assert research.source["integrity"] == "durable_record"
                assert original in window.findChild(QObject, "researchSourceText").property("text")
                research.addSource(source)
                dialog.setProperty("page", "editor")
                research.captureDependencies("input.txt")
                wait_research(qtbot, research)
                assert len(research.editor["dependencies"]) == 1
                research.setField("scope", "Synthetic input revision one")
                research.setField("criteria", "The retained source contains criterion=7")
                research.setField("assessment", "supported")
                research.setField("reassess", True)
                research.setField("reason", "Explicit user assessment of this fixture only")
                clicked(window, "researchSave")
                qtbot.waitUntil(lambda: research.editor["existing"] and not research.busy, timeout=SERVICE_WAIT_MS)
                assert not research.error, research.error
                record_id = research.editor["record_id"]
                assert research.editor["expected_revision"] == 1
                assert research.editor["assessment"] == "supported"
                assert research.report["pending_count"] == 0 and workspace.draftText == chat_draft

                dependency.write_text("Changed synthetic input: criterion=9", encoding="utf-8")
                research.recheck()
                qtbot.waitUntil(lambda: research.history["detail"].get("assessment") == "needs_recheck"
                                and not research.busy, timeout=SERVICE_WAIT_MS)
                assert research.history["detail"]["revision"] == 2
                research.reloadRecord()
                wait_research(qtbot, research)
                assert research.editor["expected_revision"] == 2
                assert research.editor["assessment"] == "needs_recheck"
                corrected = "Correction: the original observation applies only to input revision one."
                research.setField("body", corrected)
                research.setField("reason", "The declared input changed; preserve the earlier interpretation")
                clicked(window, "researchSave")
                qtbot.waitUntil(lambda: research.editor["expected_revision"] == 3 and not research.busy, timeout=SERVICE_WAIT_MS)
                research.openHistory()
                wait_research(qtbot, research)
                assert [row["revision"] for row in research.history["revisions"]] == [3, 2, 1]
                research.readRevision(1)
                wait_research(qtbot, research)
                assert research.history["detail"]["assessment"] == "supported"
                assert research.history["detail"]["sources"] == [source]
                research.configure(True)
                wait_research(qtbot, research)
                assert research.report["settings"]["enabled"] is True
                research.readContext()
                wait_research(qtbot, research)
                assert corrected in research.sharedContext["content"]
                assert "needs_recheck" in research.sharedContext["content"]

                research_draft = "Unsent registry continuation: retain <literal> Ω %1."
                research.setField("body", research_draft)
                research.setField("reason", "Unsaved next interpretation")
                assert research.flushDrafts()
                workspace.flushDrafts()
                close_desktop(desktop)
                desktops.remove(desktop)

                reopened = open_desktop(paths)
                desktops.append(reopened)
                workspace, _, window, research = reopened
                qtbot.waitUntil(lambda: workspace.connected, timeout=SERVICE_WAIT_MS)
                workspace.openProject(project_id)
                qtbot.waitUntil(lambda: workspace.sessionId == session_id and not workspace.sessionsLoading, timeout=SERVICE_WAIT_MS)
                assert visual_item(window.contentItem(), "prompt").property("text") == chat_draft
                clicked(window, "projectResearchButton")
                dialog = window.findChild(QObject, "researchDialog")
                qtbot.waitUntil(lambda: dialog.property("visible") and not research.busy, timeout=SERVICE_WAIT_MS)
                assert research.editor["record_id"] == record_id
                assert research.editor["body"] == research_draft
                assert research.editor["expected_revision"] == 3
                assert research.report["settings"]["enabled"] is True
                research.openHistory()
                wait_research(qtbot, research)
                assert [row["revision"] for row in research.history["revisions"]] == [3, 2, 1]
                research.readSource(source)
                wait_research(qtbot, research)
                assert original in research.source["content"]
                assert research.editor["body"] == research_draft
                assert workspace.selectedModel == "synthetic-research-model" and workspace.selectedEffort == "high"
                assert len(calls_path.read_text().splitlines()) == 1
                with sqlite3.connect(f"file:{paths.db}?mode=ro", uri=True) as database:
                    saved = json.loads(database.execute("SELECT record FROM research_records WHERE id=?", (record_id,)).fetchone()[0])
                    assert saved["body"] == corrected and saved["assessment"] == "needs_recheck"
                    assert database.execute("SELECT COUNT(*) FROM research_revisions WHERE record_id=?", (record_id,)).fetchone()[0] == 3
                    assert database.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
                    assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert dependency.read_text() == "Changed synthetic input: criterion=9"
            finally:
                for desktop in desktops:
                    close_desktop(desktop)
                stop_service(process)
