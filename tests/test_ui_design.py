"""Actual Qt interactions with synthetic projects; no service or provider starts."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


@pytest.fixture
def design_ui(qapp, qtbot, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    view._project = {"id": "synthetic-project", "name": "Design fixture", "root": str(tmp_path),
                     "permission": "read_only"}
    view._sessions = [{"id": "synthetic-session", "project_id": "synthetic-project", "backend": "codex", "model": "chosen-model",
                       "settings": {"reasoning_effort": "xhigh", "tools": True}}]
    view._catalogs = {"codex": {"available": True, "models": [
        {"id": "chosen-model", "name": "Chosen model", "supported_reasoning_efforts": ["low", "xhigh"]}]}}
    requests = []
    def respond(method, params, done, failed=None, **kwargs):
        requests.append((method, params))
        if method == "session.snapshot":
            done({"session": view._sessions[0], "runs": [], "artifacts": []})
        return True
    monkeypatch.setattr(view.client, "request", respond)
    view.selectSession("synthetic-session")
    requests.clear()
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    qtbot.wait(60)
    yield view, window, requests
    window.setProperty("closeConfirmed", True)
    window.close()
    view.close()
    engine.deleteLater()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
@pytest.mark.parametrize("busy", [False, True])
def test_model_panel_toggle_gains_chat_space_without_changing_session(design_ui, qtbot, size, busy):
    view, window, requests = design_ui
    window.resize(*size)
    if busy:
        view._runs = {"synthetic-run": {"id": "synthetic-run", "state": "running"}}
        view.changed.emit()
    qtbot.wait(50)
    root = window.contentItem()
    version_label = item(root, "appVersionLabel")
    assert version_label.property("text").endswith(view.appVersion)
    assert version_label.property("contentWidth") <= version_label.width() + 0.5
    toggle = item(root, "modelControlsToggle")
    popover = window.findChild(QObject, "modelPopover")
    conversation = item(root, "conversation")
    assert not popover.property("visible")
    assert toggle.isVisible() and toggle.isEnabled()
    summary = item(root, "modelControlsSummary").property("text")
    assert view.selectedModel in summary or "Scegli un modello" in summary
    collapsed_height = conversation.height()
    assert collapsed_height > 180
    # The same always-available chip works with a keyboard while work is active.
    toggle.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(40)
    assert popover.property("visible")
    expanded = item(root, "modelControlsExpanded")
    assert expanded.isVisible()
    selectors = [item(root, name) for name in ("backendBox", "modelBox", "effortBox", "sessionBox")]
    selected = tuple(control.property("currentIndex") for control in selectors)
    assert all(control.isVisible() for control in selectors)
    assert all(control.isEnabled() is not busy for control in selectors)
    # The options float over the conversation: no reading space is lost.
    assert conversation.height() == pytest.approx(collapsed_height)
    for control in selectors:
        assert control.width() > 60 and control.height() > 20
        bounds = control.mapToScene(QPointF(control.width(), control.height()))
        assert 0 < bounds.x() <= window.width() and 0 < bounds.y() <= window.height()
    # Pointer activation uses the actual visual geometry, not the signal directly.
    center = toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
    qtbot.wait(40)
    assert not popover.property("visible")
    assert conversation.height() == pytest.approx(collapsed_height)
    assert tuple(control.property("currentIndex") for control in selectors) == selected
    assert view.sessionId == "synthetic-session"
    assert (view.backend, view.selectedModel, view.selectedEffort) == ("codex", "chosen-model", "xhigh")
    assert view.busy is busy and not requests
    assert not window.flags() & Qt.WindowType.FramelessWindowHint


def test_collapsed_summary_is_literal_and_unconfigured_model_can_be_opened(design_ui, qtbot):
    view, window, requests = design_ui
    summary = item(window.contentItem(), "modelControlsSummary")
    view._model = "<b>synthetic model</b>" + " long label" * 30
    view.changed.emit()
    qtbot.wait(20)
    assert "<b>synthetic model</b>" in summary.property("text")
    expression = QQmlExpression(QQmlEngine.contextForObject(summary), summary, "Number(textFormat)")
    assert expression.evaluate()[0] == 0 and not expression.hasError()
    view._model = ""
    view.changed.emit()
    qtbot.wait(20)
    assert "Scegli un modello" in summary.property("text")
    toggle = item(window.contentItem(), "modelControlsToggle")
    toggle.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(30)
    assert item(window.contentItem(), "modelBox").isVisible()
    assert not view.selectedModel and not requests


def test_intrinsic_team_selector_keeps_space_for_label_and_popup(design_ui, qtbot):
    view, window, _ = design_ui
    view.teams.setMode("manual")
    view.teams.setCount(1)
    window.setProperty("page", 4)
    editor = window.findChild(QObject, "teamEditor")
    QMetaObject.invokeMethod(editor, "open")
    qtbot.waitUntil(lambda: editor.property("opened"))
    access = item(window.contentItem(), "teamMemberAccess_0")
    assert access is not None
    # Unlike the main model controls, this selector has no explicit width/height.
    # A style background must preserve its intrinsic minimum so the dropdown
    # cannot collapse to the indicator and let its scrollbar cover every row.
    assert access.width() >= 100 and access.height() >= 30
    expression = QQmlExpression(QQmlEngine.contextForObject(access), access, "popup.open()")
    expression.evaluate()
    assert not expression.hasError()
    qtbot.wait(30)
    expression = QQmlExpression(QQmlEngine.contextForObject(access), access,
                               "popup.contentItem.itemAtIndex(0).width")
    assert expression.evaluate()[0] >= 100 and not expression.hasError()
    QMetaObject.invokeMethod(editor, "close")
