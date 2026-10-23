"""Models page: segmented providers, provider status, model cards and folded raw data."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def walk(root):
    yield root
    for child in root.childItems():
        yield from walk(child)


def named(window, name):
    return next(obj for obj in walk(window.contentItem()) if obj.objectName() == name)


def click(window, obj):
    assert obj.isVisible() and obj.isEnabled()
    center = obj.mapToScene(obj.boundingRect().center())
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(center.x()), round(center.y())))


@pytest.fixture
def models_ui(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "p", "name": "Documenti", "root": str(tmp_path / "p"),
                          "permission": "read_only", "memory": {}}
    workspace._projects = [workspace._project]
    workspace._backend, workspace._model, workspace._effort = "ollama", "qwen", ""
    workspace._catalogs = {
        "ollama": {"available": True, "models": [
            {"id": "qwen", "name": "Qwen", "size_bytes": 2 * 1073741824, "tools_verified": True},
            {"id": "gemma", "name": "Gemma", "tools_metadata": True}]},
        "codex": {"available": False, "error": "Accesso non confermato.", "models": []},
        "claude_code": {"available": True, "models": [
            {"id": "opus", "name": "Opus", "supported_reasoning_efforts": ["low", "high"]}]},
    }
    workspace._preferences["onboarding_complete"] = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args) or True)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
    window.setProperty("page", 1)
    qtbot.wait(60)
    yield workspace, window, calls
    assert not any("ReferenceError" in record.message or "binding loop" in record.message.lower()
                   for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_provider_state_colours_the_status_dot_from_catalog_and_account(models_ui, qtbot):
    workspace, window, calls = models_ui
    dot = named(window, "providerStatusDot")
    assert workspace.providerState == "ok"
    assert named(window, "ollamaBackendButton").property("checked")
    assert not named(window, "codexLoginButton").isVisible()
    ok_color = dot.property("color").name()
    workspace._backend = "codex"
    workspace.changed.emit()
    qtbot.wait(30)
    assert workspace.providerState == "error"
    assert named(window, "providerStatusText").property("text") == "Accesso non confermato."
    assert named(window, "codexLoginButton").isVisible()
    error_color = dot.property("color").name()
    workspace._backend = "claude_code"
    workspace.changed.emit()
    qtbot.wait(30)
    assert workspace.providerState == "warning"
    assert "Claude Code non risulta collegato" in named(window, "providerStatusText").property("text")
    warning_color = dot.property("color").name()
    workspace._catalogs["claude_code"]["account"] = {"connected": True}
    workspace.changed.emit()
    qtbot.wait(30)
    assert workspace.providerState == "ok" and dot.property("color").name() == ok_color
    workspace._backend = "deepseek_web"
    workspace.changed.emit()
    assert workspace.providerState == "pending"
    assert len({ok_color, error_color, warning_color}) == 3
    assert not calls


def test_model_cards_select_a_model_and_raw_catalog_stays_folded(models_ui, qtbot):
    workspace, window, calls = models_ui
    assert named(window, "modelCard_qwen").property("selected")
    assert not named(window, "catalogText").isVisible()
    click(window, named(window, "catalogToggle"))
    qtbot.wait(30)
    raw = named(window, "catalogText")
    assert raw.isVisible() and '"size_bytes": 2147483648' in raw.property("text")
    click(window, named(window, "catalogToggle"))
    qtbot.wait(30)
    assert not named(window, "catalogText").isVisible()
    click(window, named(window, "useModel_gemma"))
    qtbot.wait(30)
    assert workspace.selectedModel == "gemma"
    assert window.property("page") == 0
    window.setProperty("page", 1)
    qtbot.wait(30)
    assert named(window, "modelCard_gemma").property("selected") and not named(window, "modelCard_qwen").property("selected")
    assert not any(call[0] == "run.start" for call in calls)


def test_model_cards_flow_in_two_columns_only_when_wide(models_ui, qtbot):
    workspace, window, calls = models_ui
    first, second = named(window, "modelCard_qwen"), named(window, "modelCard_gemma")
    assert first.mapToScene(QPoint(0, 0)).y() == second.mapToScene(QPoint(0, 0)).y()
    assert first.height() == second.height()
    assert first.mapToScene(QPoint(0, 0)).x() < second.mapToScene(QPoint(0, 0)).x()
    window.resize(880, 620)
    qtbot.wait(60)
    assert first.mapToScene(QPoint(0, 0)).y() < second.mapToScene(QPoint(0, 0)).y()
    assert first.width() == second.width()
    assert first.mapToScene(QPoint(0, 0)).x() + first.width() <= window.width()
