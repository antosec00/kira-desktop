"""The top bar keeps the chat title readable: what is beside it gives way in stages, never over it."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtQml import QQmlEngine, QQmlExpression

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace

ACTIONS = ("projectResearchButton", "resumeWorkspaceButton", "quickActionsButton",
           "projectExportButton", "detailsToggle", "headerNewProjectButton")


def bounds(item):
    return item.mapRectToScene(item.boundingRect())


def evaluate(item, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(item), item, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


@pytest.fixture
def bar(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "p", "name": "Guida di viaggio in montagna", "root": str(tmp_path / "p"),
                          "permission": "project_write", "memory": {}}
    workspace._projects = [workspace._project]
    workspace._session = {"id": "s", "project_id": "p", "backend": "ollama", "model": "m", "settings": {},
                          "title": "Capitolo sulle Dolomiti e sui rifugi"}
    workspace._sessions = [workspace._session]
    workspace._preferences["onboarding_complete"] = True
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: True)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    yield workspace, window
    assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                   or "TypeError:" in record.message for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("connected", [False, True])
def test_nothing_in_the_top_bar_covers_the_chat_title_at_any_width(bar, qtbot, connected):
    workspace, window = bar
    if connected:
        # A connected account shows a longer label: name and available credit.
        workspace.kiraCloudAccount._status = {"connected": True, "name": "Giulia Bianchi", "plan_label": "Kira Start",
                                              "plan_active": True, "currency": "EUR", "total_remaining": 14.2}
        workspace.kiraCloudAccount.changed.emit()
    header = window.findChild(QObject, "projectHeader")
    title = window.findChild(QObject, "projectHeaderTitle")
    account = window.findChild(QObject, "topBarKiraAccountButton")
    theme = window.findChild(QObject, "themeToggle")
    actions = [window.findChild(QObject, name) for name in ACTIONS]
    stages = []
    for width in (2000, 1700, 1500, 1380, 1280, 1180, 1000, 880):
        window.resize(width, 800)
        qtbot.wait(80)
        title_bounds = bounds(title)
        assert title.property("text") == "Capitolo sulle Dolomiti e sui rifugi"
        assert title_bounds.width() >= 150, width
        beside = [*actions, account, theme]
        for index, control in enumerate(beside):
            rect = bounds(control)
            assert control.isVisible(), (width, control.objectName())
            assert not rect.intersects(title_bounds), (width, control.objectName())
            assert bounds(header).adjusted(-1, -1, 1, 1).contains(rect), (width, control.objectName())
            assert all(not rect.intersects(bounds(other)) for other in beside[index + 1:]), (width, control.objectName())
        stages.append((evaluate(header, "compactAccount"), evaluate(header, "compactKicker"),
                       evaluate(header, "compactActions")))
        # The account button always carries its symbol; its label only while there is room.
        assert account.property("iconName") == "user"
        assert bool(account.property("text")) is not stages[-1][0]
        kicker = window.findChild(QObject, "projectHeaderKicker")
        assert kicker.isVisible() is not stages[-1][1]
    # Wide: everything. Then the account label goes, then the project name, then the action labels;
    # a stage, once reached, holds for every narrower window.
    assert stages[0] == (False, False, False) and stages[-1] == (True, True, True)
    assert stages == sorted(stages)
    assert all(account <= 1 and kicker <= account and compact <= kicker for account, kicker, compact in stages)
    assert {(True, False, False), (True, True, False)} & set(stages)
