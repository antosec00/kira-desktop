"""The startup loading screen follows synthetic service answers; no real service runs."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from shiboken6 import isValid
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui import controller as controller_module
from kira.ui.app import close_launch_splash, create_engine, show_launch_splash
from kira.ui.controller import Workspace


def visual(root, name):
    if root.objectName() == name and root.isVisible():
        return root
    for child in root.childItems():
        if found := visual(child, name):
            return found
    return None


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    instance = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "ipc"))))
    requests = []

    def request(method, params, done, failed=None, **kwargs):
        requests.append((method, params, done))
        return True

    monkeypatch.setattr(instance.client, "request", request)
    yield instance, requests
    instance.close()
    instance.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def answer(requests, method, result, **match):
    for name, params, done in requests:
        if name == method and all(params.get(key) == value for key, value in match.items()):
            done(result)
            return
    raise AssertionError(f"no {method} request {match}")


def test_tests_and_embedders_never_see_the_screen(workspace):
    instance, _ = workspace
    assert instance.startupStage == "ready"
    instance._connected()
    assert instance.startupStage == "ready"


def test_stages_follow_service_projects_and_the_selected_models(workspace):
    instance, requests = workspace
    instance._backend = "ollama"
    instance.beginStartupScreen()
    assert instance.startupStage == "service"
    instance._connected()
    assert instance.startupStage == "projects" and instance._startup_timer.isActive()
    answer(requests, "backend.discover", {"available": True, "models": []}, backend="codex")
    answer(requests, "project.list", {"projects": []})
    assert instance.startupStage == "models"
    answer(requests, "backend.discover", {"available": True, "models": []}, backend="ollama")
    assert instance.startupStage == "ready" and not instance._startup_timer.isActive()


def test_models_that_arrive_first_do_not_hold_the_screen(workspace):
    instance, requests = workspace
    instance._backend = "ollama"
    instance.beginStartupScreen()
    instance._connected()
    answer(requests, "backend.discover", {"available": False, "models": [], "error": "x"}, backend="ollama")
    answer(requests, "project.list", {"projects": []})
    assert instance.startupStage == "ready"


def test_a_provider_not_queried_at_startup_is_not_awaited(workspace):
    instance, requests = workspace
    instance._backend = "deepseek_web"
    instance.beginStartupScreen()
    instance._connected()
    answer(requests, "project.list", {"projects": []})
    assert instance.startupStage == "ready"


def test_the_screen_has_a_time_limit_and_can_be_skipped(workspace, qtbot, monkeypatch):
    instance, requests = workspace
    instance._backend = "ollama"
    instance._startup_timer.setInterval(50)
    instance.beginStartupScreen()
    instance._connected()
    answer(requests, "project.list", {"projects": []})
    assert instance.startupStage == "models"
    qtbot.waitUntil(lambda: instance.startupStage == "ready", timeout=2000)
    instance.beginStartupScreen()
    instance._connected_value = False
    instance.beginStartupScreen()
    assert instance.startupStage == "service"
    instance.finishStartupScreen()
    assert instance.startupStage == "ready"
    assert controller_module.STARTUP_SCREEN_LIMIT_MS == 8000


def test_the_screen_covers_the_window_and_fades_when_ready(workspace, qtbot, qtlog):
    instance, requests = workspace
    instance.beginStartupScreen()
    engine = create_engine(instance)
    window = engine.rootObjects()[0]
    window.resize(1100, 760)
    try:
        screen = window.findChild(QObject, "kiraLoadingScreen")
        qtbot.waitUntil(lambda: screen.property("opened"))
        qtbot.wait(100)
        assert screen.property("opacity") == 1
        assert visual(window.contentItem(), "loadingStatus").property("text") == "Avvio del servizio locale…"
        # The screen takes the clicks meant for the window behind it.
        header_button = visual(window.contentItem(), "headerNewProjectButton")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         header_button.mapToScene(QPointF(header_button.width() / 2, header_button.height() / 2)).toPoint())
        QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        assert not window.findChild(QObject, "newProjectDialog").property("visible")
        instance._connected()
        assert visual(window.contentItem(), "loadingStatus").property("text") == "Carico progetti e chat…"
        screen.setProperty("slow", True)
        qtbot.waitUntil(lambda: visual(window.contentItem(), "loadingContinue") is not None)
        answer(requests, "project.list", {"projects": []})
        instance.finishStartupScreen()
        qtbot.waitUntil(lambda: not screen.property("visible"), timeout=3000)
        click_point = header_button.mapToScene(QPointF(header_button.width() / 2, header_button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, click_point)
        qtbot.waitUntil(lambda: window.findChild(QObject, "newProjectDialog").property("visible"))
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any("TypeError" in record.message or "ReferenceError" in record.message
                   or "Unable to assign" in record.message or "KiraLoading" in record.message
                   for record in qtlog.records), [record.message for record in qtlog.records]


def test_launch_splash_hands_over_after_the_first_frame(workspace, qtbot):
    instance, _ = workspace
    splash = show_launch_splash(instance)
    assert splash is not None
    window = splash.rootObjects()[0]
    assert window.objectName() == "kiraSplashWindow" and window.isVisible()
    assert window.property("version")
    engine = create_engine(instance)
    try:
        close_launch_splash(splash, engine)
        qtbot.waitUntil(lambda: not isValid(window) or not window.isVisible(), timeout=5000)
    finally:
        main = engine.rootObjects()[0]
        main.setProperty("closeConfirmed", True)
        main.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
