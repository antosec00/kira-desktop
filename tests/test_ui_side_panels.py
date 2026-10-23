"""Synthetic native panel journeys; no provider calls or user profiles."""

import copy
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, Qt, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from test_ui_support import wait_laid_out, wait_opened


def evaluate(obj, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, code)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def bounds(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def click(window, item):
    assert item is not None and item.isVisible() and item.isEnabled()
    point = item.mapToScene(item.boundingRect().center()).toPoint()
    assert QRectF(0, 0, window.width(), window.height()).contains(point)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)


@pytest.fixture
def panels(qapp, qtbot, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path), "permission": "project_write"}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "codex", "model": "synthetic-model", "settings": {}}
    workspace._backend, workspace._model = "codex", "synthetic-model"
    workspace._connected_value = True
    workspace._preferences["onboarding_complete"] = True
    workspace._preferences["resume_hint_visible"] = False
    calls = []

    def request(method, params, done, failed=None, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "workspace.get":
            done({"project_id": params["project_id"], "tools": ["folder"], "next_step": "Saved next step", "watches": [], "captures": []})
        elif method == "research.list":
            done({"project_id": params["project_id"], "records": [], "settings": {"enabled": False, "revision": 0}, "has_more": False})
        elif method == "knowledge.snapshot":
            done({"identity": {"name": "Kira", "model": "synthetic-model", "backend": "codex"},
                  "anchor": {}, "sources": [], "capabilities": []})
        elif method == "session.context":
            done({"project_id": params["project_id"], "session_id": params["session_id"],
                  "memory": {"pins": [], "summary": {}, "sources": []}, "status": "idle"})
        elif method == "backend.usage":
            done({"available": False, "backend": "codex", "windows": []})
        return True

    monkeypatch.setattr(workspace.client, "request", request)
    workspace.changed.emit()
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1480, 900)
    qtbot.wait(40)
    yield workspace, engine, window, calls
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size,font_size", [((1480, 900), 13), ((880, 620), 18)])
def test_panels_are_nonmodal_one_at_a_time_and_fit_workspace(panels, qtbot, qtlog, size, font_size):
    workspace, engine, window, calls = panels
    window.resize(*size)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", font_size)
    previous = None
    for name in ("workspaceDialog", "researchDialog", "assistantEnvironmentDialog", "contextDialog", "usageDialog"):
        panel = window.findChild(QObject, name)
        evaluate(panel, "open()")
        close = window.findChild(QObject, name + "Close")
        wait_opened(qtbot, panel, close)
        assert panel.property("opened") and panel.property("sidePanel")
        assert not panel.property("modal")
        assert evaluate(panel, "panelHost.activePanel === this")
        if previous is not None:
            assert not previous.property("visible")
        content = panel.property("contentItem")
        assert QRectF(0, 0, *size).adjusted(-1, -1, 1, 1).contains(bounds(content))
        assert content.height() > 100
        assert QRectF(0, 0, *size).contains(bounds(close))
        previous = panel
    host = window.findChild(QObject, "kiraPanelHost")
    assert (host.property("reservedWidth") > 0) == (size[0] == 1480)
    click(window, window.findChild(QObject, "usageDialogClose"))
    qtbot.wait(30)
    assert host.property("reservedWidth") == 0
    assert not any(method in {"run.start", "research.save", "workspace.update", "session.context.pin"} for method, _ in calls)
    assert not any(any(text in record.message.lower() for text in ("binding loop", "referenceerror", "typeerror", "unable to assign"))
                   for record in qtlog.records)


def test_chat_remains_editable_panel_expands_and_escape_closes(panels, qtbot):
    workspace, _, window, calls = panels
    panel = window.findChild(QObject, "researchDialog")
    evaluate(panel, "open()")
    prompt = window.findChild(QObject, "prompt")
    wait_opened(qtbot, panel, prompt)
    click(window, prompt)
    for letter in "draft":
        QTest.keyClick(window, ord(letter.upper()))
    assert workspace.draftText == "DRAFT"
    assert panel.property("opened")
    click(window, window.findChild(QObject, "researchDialogExpand"))
    qtbot.waitUntil(lambda: panel.property("expanded") is True)
    wait_laid_out(qtbot, panel.property("contentItem"))
    assert window.findChild(QObject, "kiraPanelHost").property("reservedWidth") == 0
    assert QRectF(0, 0, window.width(), window.height()).contains(bounds(panel.property("contentItem")))
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.wait(30)
    assert not panel.property("visible") and workspace.draftText == "DRAFT"
    assert not any(method == "run.start" for method, _ in calls)


def test_switching_panels_preserves_research_page_and_all_unsent_notes(panels, qtbot):
    workspace, engine, window, calls = panels
    research = window.findChild(QObject, "researchDialog")
    resume = window.findChild(QObject, "workspaceDialog")
    context = window.findChild(QObject, "contextDialog")
    usage = window.findChild(QObject, "usageDialog")
    evaluate(research, 'open(); projectResearch.newRecord(); page = "editor"; syncEditor()')
    window.findChild(QObject, "researchTitle").setProperty("text", "Unsent <research> %1")
    window.findChild(QObject, "researchBody").setProperty("text", "Original body")
    evaluate(resume, "open()")
    window.findChild(QObject, "workspaceNextStep").setProperty("text", "Unsent next step")
    evaluate(context, "open()")
    window.findChild(QObject, "contextPinInput").setProperty("text", "Unsent <note> %2")
    evaluate(usage, "open()")
    evaluate(research, "open()")
    assert research.property("page") == "editor"
    assert window.findChild(QObject, "researchTitle").property("text") == "Unsent <research> %1"
    assert window.findChild(QObject, "researchBody").property("text") == "Original body"
    evaluate(resume, "open()")
    assert resume.property("dirty")
    assert window.findChild(QObject, "workspaceNextStep").property("text") == "Unsent next step"
    evaluate(context, "open()")
    assert window.findChild(QObject, "contextPinInput").property("text") == "Unsent <note> %2"
    assert engine.rootContext().contextProperty("chatContext").noteDraft == "Unsent <note> %2"
    assert not any(method in {"research.save", "workspace.update", "session.context.pin", "run.start"} for method, _ in calls)


def test_project_switch_closes_panel_and_restores_only_matching_project_drafts(panels, qtbot):
    workspace, _, window, _ = panels
    resume = window.findChild(QObject, "workspaceDialog")
    research = window.findChild(QObject, "researchDialog")
    evaluate(resume, "open()")
    window.findChild(QObject, "workspaceNextStep").setProperty("text", "Project A draft")
    window.findChild(QObject, "watchPath").setProperty("text", "docs/project-a.md")
    evaluate(research, 'open(); projectResearch.newRecord(); page = "editor"; syncEditor()')
    window.findChild(QObject, "researchTitle").setProperty("text", "Project A research")
    workspace._project = {**workspace._project, "id": "other-project"}
    workspace._session = {**workspace._session, "id": "other-chat", "project_id": "other-project"}
    workspace.changed.emit()
    qtbot.wait(30)
    assert not research.property("visible") and research.property("page") == "list"
    evaluate(resume, "open()")
    assert window.findChild(QObject, "workspaceNextStep").property("text") == "Saved next step"
    assert window.findChild(QObject, "watchPath").property("text") == ""
    window.findChild(QObject, "workspaceNextStep").setProperty("text", "Project B draft")
    workspace._project = {**workspace._project, "id": "project"}
    workspace._session = {**workspace._session, "id": "chat", "project_id": "project"}
    workspace.changed.emit()
    qtbot.wait(30)
    assert not resume.property("visible")
    evaluate(resume, "open()")
    assert window.findChild(QObject, "workspaceNextStep").property("text") == "Project A draft"
    assert window.findChild(QObject, "watchPath").property("text") == "docs/project-a.md"
    evaluate(research, "open()")
    assert evaluate(research, "projectResearch.editor.title") == "Project A research"


def test_resume_keeps_current_tab_after_panel_switch_and_metadata_refresh(panels, qtbot):
    workspace, _, window, _ = panels
    resume = window.findChild(QObject, "workspaceDialog")
    usage = window.findChild(QObject, "usageDialog")
    tabs = window.findChild(QObject, "workspaceTabs")
    evaluate(resume, "open()")
    tabs.setProperty("currentIndex", 1)
    evaluate(usage, "open()")
    evaluate(resume, "open()")
    assert tabs.property("currentIndex") == 1
    tabs.setProperty("currentIndex", 2)
    workspace.refreshWorkspace()
    assert tabs.property("currentIndex") == 2


def test_panel_without_workspace_host_retains_centered_dialog_fallback(panels, qtbot):
    _, engine, _, _ = panels
    import kira.ui.app

    directory = Path(kira.ui.app.__file__).parent / "qml"
    component = QQmlComponent(engine)
    component.setData(b'''
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            width: 880; height: 620; visible: true
            KiraPanel {
                id: panel; objectName: "standalonePanel"
                title: "Synthetic panel"; fallbackWidth: 500; fallbackHeight: 400
                contentItem: Label { text: "Synthetic content" }
            }
            Component.onCompleted: panel.open()
        }
    ''', QUrl.fromLocalFile(str(directory / "SyntheticPanelHarness.qml")))
    standalone = component.create()
    assert standalone is not None, [error.toString() for error in component.errors()]
    try:
        qtbot.wait(40)
        panel = standalone.findChild(QObject, "standalonePanel")
        assert panel.property("opened") and panel.property("modal") and not panel.property("sidePanel")
        assert panel.property("width") == 500 and panel.property("height") == 400
        assert panel.property("x") == 190 and panel.property("y") == 110
        QTest.keyClick(standalone, Qt.Key.Key_Escape)
        qtbot.wait(30)
        assert not panel.property("visible")
    finally:
        standalone.close()
        standalone.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
