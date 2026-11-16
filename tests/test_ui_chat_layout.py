"""Real Qt chat interactions with synthetic projects, never a provider run."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
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


def evaluate(obj, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, code)
    result, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


def click(window, control):
    QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                     pos=control.mapToScene(control.boundingRect().center()).toPoint())


@pytest.fixture
def chat_ui(qapp, tmp_path, qtbot, qtlog, monkeypatch):
    paths = Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime")))
    view = Workspace(paths)
    view._project = {"id": "synthetic", "name": "Chat layout fixture", "root": str(tmp_path),
                     "permission": "read_only"}
    requests = []
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: requests.append(args))
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    qtbot.wait(60)
    yield view, window, paths, requests
    assert not any("binding loop" in record.message.lower() for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_sidebar_default_toggle_persists_without_provider_calls(chat_ui, qtbot):
    view, window, paths, requests = chat_ui
    root = window.contentItem()
    panel = item(root, "detailsPanel")
    toggle = item(root, "detailsToggle")
    conversation = item(root, "conversation")
    assert not panel.isVisible() and toggle.isVisible()
    wide = conversation.width()
    click(window, toggle)
    qtbot.wait(50)
    assert panel.isVisible() and view.uiPreferences["details_visible"]
    assert conversation.width() < wide - 200
    restored = Workspace(paths)
    try:
        assert restored.uiPreferences["details_visible"]
    finally:
        restored.close()
        restored.deleteLater()
    toggle.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(40)
    assert not panel.isVisible()
    assert not requests


def test_narrow_details_overlay_has_real_content_and_close(chat_ui, qtbot):
    view, window, _, _ = chat_ui
    window.resize(880, 620)
    qtbot.wait(40)
    toggle = item(window.contentItem(), "detailsToggle")
    assert toggle.isVisible()
    click(window, toggle)
    drawer = window.findChild(QObject, "detailsDrawer")
    qtbot.waitUntil(lambda: drawer.property("opened"))
    close = item(window.contentItem(), "detailsDrawerClose")
    assert view.uiPreferences["details_visible"]
    assert close.isVisible() and close.width() > 80
    assert any(c.property("text") == "NEL PROGETTO" for c in window.findChildren(QObject)
               if c.metaObject().indexOfProperty("text") >= 0)
    click(window, close)
    qtbot.waitUntil(lambda: not drawer.property("visible"))
    qtbot.waitUntil(lambda: not view.uiPreferences["details_visible"])
    click(window, toggle)
    qtbot.waitUntil(lambda: drawer.property("opened"))
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not view.uiPreferences["details_visible"])


def test_chat_scrollbar_drag_and_new_output_keep_reading_position(chat_ui, qtbot):
    view, window, _, _ = chat_ui
    for index in range(65):
        view.transcript.append(kind="assistant", title="KIRA", body=f"Result {index}\n" * 8)
    qtbot.wait(120)
    root = window.contentItem()
    conversation = item(root, "conversation")
    bar = item(root, "conversationScrollBar")
    thumb = item(bar, "scrollThumb")
    assert bar.width() >= 16 and thumb.height() >= 35
    assert bar.isVisible()
    before = conversation.property("contentY")
    center = thumb.mapToScene(thumb.boundingRect().center()).toPoint()
    destination = bar.mapToScene(QPointF(bar.width() / 2, bar.height() / 3)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(window, destination, delay=80)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=destination)
    qtbot.wait(100)
    assert conversation.property("contentY") < before - 100
    assert not window.property("followOutput")
    position = conversation.property("contentY")
    view.transcript.append(kind="assistant", title="KIRA", body="A new result\n" * 20)
    view.transcriptChanged.emit()
    qtbot.wait(100)
    assert not window.property("followOutput")
    assert conversation.property("contentY") == pytest.approx(position, abs=2)
    # Every visible card reserves the whole scrollbar hit area plus a gutter.
    for child in conversation.childItems():
        for entry in child.childItems():
            if entry.objectName().startswith("messageEntry_"):
                assert entry.width() <= conversation.width() - bar.width() - 8


def test_chat_font_sizes_update_message_and_composer_independently_of_code(chat_ui, qtbot):
    view, window, _, _ = chat_ui
    view.transcript.append(kind="assistant", title="KIRA", body="Synthetic **message**")
    view.transcript.append(kind="tool", title="Check", body="synthetic result")
    view.setUiPreference("chat_font_size", 19)
    qtbot.wait(50)
    root = window.contentItem()
    for name, size in (("messageText_0", 19), ("messageText_1", 12), ("prompt", 19)):
        assert evaluate(item(root, name), "font.pixelSize") == size


@pytest.mark.parametrize("policy,busy,confirm", [
    ("active", False, False), ("active", True, True),
    ("always", False, True), ("always", True, True),
    ("never", False, False), ("never", True, False),
])
def test_close_policy_only_closes_gui(chat_ui, qtbot, policy, busy, confirm):
    view, window, _, requests = chat_ui
    view.setUiPreference("confirm_close", policy)
    if busy:
        view._runs = {"synthetic": {"id": "synthetic", "state": "running"}}
        view.changed.emit()
    window.close()
    qtbot.wait(40)
    dialog = window.findChild(QObject, "closeConfirmation")
    assert bool(dialog.property("visible")) is confirm
    assert window.isVisible() is confirm
    assert view.busy is busy and not requests


def test_new_project_default_permission_does_not_mutate_current(chat_ui, qtbot):
    view, window, _, requests = chat_ui
    view.setUiPreference("default_permission", "project_write")
    QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(50)
    permission = item(window.contentItem(), "newProjectPermission")
    assert permission.isVisible() and permission.property("currentIndex") == 1
    assert view.project["permission"] == "read_only" and not requests


def test_details_preference_follows_window_size_without_changing_selection(chat_ui, qtbot):
    view, window, _, _ = chat_ui
    view.setUiPreference("details_visible", True)
    qtbot.wait(40)
    assert item(window.contentItem(), "detailsPanel").isVisible()
    window.resize(880, 620)
    drawer = window.findChild(QObject, "detailsDrawer")
    qtbot.waitUntil(lambda: drawer.property("opened"))
    assert view.uiPreferences["details_visible"]
    window.resize(1380, 900)
    qtbot.waitUntil(lambda: not drawer.property("visible"))
    qtbot.wait(50)
    assert view.uiPreferences["details_visible"]
    assert item(window.contentItem(), "detailsPanel").isVisible()


def test_activity_dots_pause_for_reduced_motion_waits_and_hidden_window(chat_ui, qtbot):
    view, window, _, _ = chat_ui
    view._connected_value = True
    view._session = {"id": "activity-chat", "project_id": "synthetic", "backend": "ollama",
                     "model": "fixture-model", "settings": {}}
    view._sessions = [view._session]
    view._runs = {"synthetic": {"id": "synthetic", "state": "running",
                                "session_id": "activity-chat", "project_id": "synthetic"}}
    view.changed.emit()
    qtbot.wait(50)
    activity = item(window.contentItem(), "activityIndicator")
    assert activity.property("label") == "Sto pensando…"
    assert activity.property("animating")
    view.setUiPreference("reduce_motion", True)
    qtbot.wait(30)
    assert not activity.property("animating")
    assert activity.property("label") == "Sto pensando…"
    view.setUiPreference("reduce_motion", False)
    qtbot.wait(30)
    assert activity.property("animating")
    window.showMinimized()
    qtbot.wait(30)
    assert not activity.property("animating")
    window.showNormal()
    qtbot.wait(30)
    assert activity.property("animating")
    window.hide()
    qtbot.wait(30)
    assert not activity.property("animating")
    window.show()
    view._runs["synthetic"]["state"] = "awaiting_input"
    view.changed.emit()
    qtbot.wait(40)
    assert not activity.property("animating")
    assert activity.property("label") != "Sto pensando…"
    view._runs["synthetic"]["state"] = "running"
    view._connected_value = False
    view.changed.emit()
    qtbot.wait(40)
    assert not activity.property("animating")


def test_chat_defaults_to_full_width_and_jump_button_stays_clear_of_scrollbar(chat_ui, qtbot):
    view, window, _, _ = chat_ui
    window.resize(1680, 900)
    for index in range(45):
        view.transcript.append(kind="assistant", title="KIRA", body=f"Wide layout item {index}\n" + "Content line. " * 18)
    qtbot.wait(100)
    root = window.contentItem()
    conversation = item(root, "conversation")
    bar = item(root, "conversationScrollBar")
    composer = item(root, "composerCard")
    jump = item(root, "jumpToLatestButton")
    assert composer.width() > 1100
    entry = next(
        entry for child in conversation.childItems() for entry in child.childItems()
        if entry.objectName().startswith("messageEntry_") and entry.width() > 0
    )
    assert entry.width() == pytest.approx(conversation.width() - bar.width() - 8, abs=2)
    assert not jump.isVisible()

    # Drag thumb partway up: jump button appears cleanly centered and never touches the scrollbar.
    thumb = item(bar, "scrollThumb")
    center = thumb.mapToScene(thumb.boundingRect().center()).toPoint()
    destination = bar.mapToScene(QPointF(bar.width() / 2, bar.height() / 3)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(window, destination, delay=60)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=destination)
    qtbot.wait(80)
    assert jump.isVisible()
    for width, height in ((1680, 900), (880, 620)):
        window.resize(width, height)
        qtbot.wait(50)
        jump_rect = jump.mapRectToScene(jump.boundingRect())
        bar_rect = bar.mapRectToScene(bar.boundingRect())
        conv_rect = conversation.mapRectToScene(conversation.boundingRect())
        assert jump_rect.left() >= conv_rect.left()
        assert jump_rect.right() <= bar_rect.left() - 8

    click(window, jump)
    qtbot.wait(80)
    assert window.property("followOutput")
    assert not jump.isVisible()


def test_projects_sidebar_button_shows_projects_sorted_by_latest_activity(chat_ui, qtbot):
    view, window, paths, _ = chat_ui
    view._projects = [
        {"id": "older", "name": "Older Project", "root": str(paths.data / "older"),
         "permission": "read_only", "created_at": "2026-01-01T10:00:00Z", "updated_at": "2026-01-01T10:00:00Z"},
        {"id": "synthetic", "name": "Chat layout fixture", "root": str(paths.data),
         "permission": "project_write", "created_at": "2026-01-01T08:00:00Z", "updated_at": "2026-01-01T08:00:00Z"},
        {"id": "middle", "name": "Middle Project", "root": str(paths.data / "middle"),
         "permission": "trusted_host", "created_at": "2026-01-02T12:00:00Z", "updated_at": "2026-01-02T12:00:00Z"},
    ]
    # Live run activity in the currently open project bumps it to the top of workspace.projects.
    view._runs = {"r1": {"id": "r1", "session_id": "s1", "state": "completed",
                         "created_at": "2026-01-03T15:00:00Z", "updated_at": "2026-01-03T15:00:00Z"}}
    view.changed.emit()
    qtbot.wait(50)
    assert [p["id"] for p in view.projects] == ["synthetic", "middle", "older"]

    root = window.contentItem()
    projects_nav = item(root, "projectsNavButton")
    overview = item(root, "projectsOverviewPage")
    split = item(root, "browserSplit")
    assert split.isVisible() and not overview.isVisible()

    click(window, projects_nav)
    qtbot.wait(60)
    assert window.property("projectsOverview")
    assert overview.isVisible() and not split.isVisible()
    card_top = item(root, "projectOverviewCard_synthetic")
    card_mid = item(root, "projectOverviewCard_middle")
    card_old = item(root, "projectOverviewCard_older")
    assert card_top.isVisible() and card_mid.isVisible() and card_old.isVisible()
    assert card_top.y() < card_mid.y() < card_old.y()

    click(window, item(root, "projectOverviewOpen_synthetic"))
    qtbot.wait(60)
    assert not window.property("projectsOverview")
    assert split.isVisible() and not overview.isVisible()
