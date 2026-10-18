"""Settings UI interactions with a private store; no provider/service is started."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.preferences import read_preferences


def evaluate(obj, expression):
    script = QQmlExpression(QQmlEngine.contextForObject(obj), obj, expression)
    result, _ = script.evaluate()
    assert not script.hasError(), script.error().toString()
    return result


@pytest.fixture
def settings_ui(qapp, qtbot, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime")))
    view = Workspace(paths)
    view._connected_value = True
    view._project = {"id": "synthetic-project", "name": "Settings fixture", "root": str(tmp_path),
                     "permission": "read_only"}
    view.teams._project_id = "synthetic-project"
    requests = []
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: requests.append(args))
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setProperty("page", 3)
    qtbot.wait(40)
    yield view, window, requests
    window.setProperty("closeConfirmed", True)
    window.close()
    view.close()
    engine.deleteLater()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def control(window, name):
    item = window.findChild(QObject, name)
    assert item is not None, name
    return item


def reveal(window, item):
    pane = control(window, "settingsPane")
    ancestor = item
    while ancestor and ancestor.property("settingsCategory") is None:
        ancestor = ancestor.parentItem()
    if ancestor:
        index = ancestor.property("settingsCategory")
        if pane.property("selectedSection") != index:
            category = control(window, ("settingsAppearanceCategory", "settingsWorkCategory",
                                       "settingsConnectionsCategory", "settingsDataCategory")[index])
            center = category.mapToScene(QPointF(category.width() / 2, category.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
            QTest.qWait(30)
            assert pane.property("selectedSection") == index
    offset = item.mapToItem(pane, QPointF(0, 0)).y() - 20
    evaluate(pane, "contentItem.contentY += " + str(offset))
    QTest.qWait(20)
    return item


def click(window, name):
    item = reveal(window, control(window, name))
    assert item.isEnabled()
    pos = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=pos)
    QTest.qWait(20)


def choose(window, name, index):
    item = reveal(window, control(window, name))
    item.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    QTest.keyClick(window, Qt.Key.Key_Home)
    for _ in range(index):
        QTest.keyClick(window, Qt.Key.Key_Down)
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.qWait(20)
    assert item.property("currentIndex") == index


def test_preferences_edit_and_restore_without_running_work(settings_ui, qtbot):
    view, window, requests = settings_ui
    assert not requests, "Opening settings must not save or start anything"
    choose(window, "densityChoice", 1)
    choose(window, "closePolicyChoice", 1)
    choose(window, "fileOpenerChoice", 1)
    choose(window, "permissionDefaultChoice", 2)
    choose(window, "reduceMotionChoice", 1)
    click(window, "modelControlsChoice")
    click(window, "detailsChoice")
    font = reveal(window, control(window, "chatFontChoice"))
    font.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Up)
    qtbot.wait(20)
    expected = {"density": "compact", "confirm_close": "always", "file_opener": "vscode",
                "default_permission": "trusted_host", "reduce_motion": True,
                "model_controls_visible": True, "details_visible": True, "chat_font_size": 15}
    saved = read_preferences(view.paths.config)
    assert {key: saved[key] for key in expected} == expected
    assert view.project["permission"] == "read_only" and not requests
    # A separate Workspace reads the actual saved file, without changing the live instance.
    restored = Workspace(view.paths)
    second_engine = create_engine(restored)
    assert second_engine.rootObjects()
    second = second_engine.rootObjects()[0]
    second.setProperty("page", 3)
    qtbot.wait(30)
    try:
        for name, value in (("densityChoice", 1), ("closePolicyChoice", 1),
                            ("fileOpenerChoice", 1), ("permissionDefaultChoice", 2),
                            ("reduceMotionChoice", 1)):
            assert control(second, name).property("currentIndex") == value
        assert control(second, "chatFontChoice").property("value") == 15
        for name in ("modelControlsChoice", "detailsChoice"):
            assert control(second, name).property("checked")
        assert "esistenti restano invariati" in control(second, "defaultPermissionDescription").property("text")
        assert "non è una sandbox" in control(second, "defaultPermissionWarning").property("text")
    finally:
        second.setProperty("closeConfirmed", True)
        second.close()
        restored.close()
        second_engine.deleteLater()
        restored.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("busy_source", ["run", "team", "disconnected"])
def test_backend_edits_disabled_while_busy_but_gui_preferences_work(settings_ui, qtbot, busy_source):
    view, window, requests = settings_ui
    if busy_source == "run":
        view._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    elif busy_source == "team":
        view.teams._teams = [{"id": "fixture-team", "state": "running"}]
        view.teams.changed.emit()
    else:
        view._connected_value = False
    view.changed.emit()
    qtbot.wait(20)
    for name in ("settingsEndpoint", "settingsCodexBinary", "settingsToolTimeout",
                 "settingsToolRounds", "settingsOutputLimit", "settingsSaveButton"):
        assert not control(window, name).isEnabled(), name
    for name in ("densityChoice", "chatFontChoice", "reduceMotionChoice", "detailsChoice",
                 "closePolicyChoice", "fileOpenerChoice", "permissionDefaultChoice"):
        assert control(window, name).isEnabled(), name
    choose(window, "reduceMotionChoice", 1)
    assert view.uiPreferences["reduce_motion"] and not requests
    view._runs = {}
    view.teams._teams = []
    view._connected_value = True
    view.changed.emit()
    view.teams.changed.emit()
    qtbot.wait(20)
    assert control(window, "settingsSaveButton").isEnabled()


def test_backend_save_backup_and_resource_actions_are_explicit(settings_ui):
    view, window, requests = settings_ui
    assert not requests
    control(window, "settingsEndpoint").setProperty("text", "http://127.0.0.1:11435")
    control(window, "settingsCodexBinary").setProperty("text", "/fixture/codex")
    control(window, "settingsToolTimeout").setProperty("value", 90)
    control(window, "settingsToolRounds").setProperty("value", 8)
    control(window, "settingsOutputLimit").setProperty("value", 8192)
    assert not requests
    click(window, "settingsSaveButton")
    assert requests[-1][:2] == ("settings.update", {
        "ollama_url": "http://127.0.0.1:11435", "codex_binary": "/fixture/codex",
        "tool_timeout": 90, "max_tool_rounds": 8, "max_output_bytes": 8192})
    click(window, "settingsBackupButton")
    assert requests[-1][:2] == ("database.backup", {})
    click(window, "settingsResourcesButton")
    assert requests[-1][:2] == ("system.resources", {})
    assert not any(request[0] == "run.start" for request in requests)


@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
def test_settings_scroll_and_controls_fit_supported_window_sizes(settings_ui, qtbot, size):
    _, window, _ = settings_ui
    window.resize(*size)
    qtbot.wait(30)
    pane = control(window, "settingsPane")
    assert evaluate(pane, "contentItem.contentHeight > height")
    for name in ("densityChoice", "chatFontChoice", "closePolicyChoice", "fileOpenerChoice",
                 "permissionDefaultChoice", "defaultPermissionDescription", "defaultPermissionWarning",
                 "settingsEndpoint", "settingsCodexBinary", "settingsToolTimeout",
                 "settingsToolRounds", "settingsOutputLimit", "settingsSaveButton",
                 "settingsBackupButton", "settingsResourcesButton"):
        item = reveal(window, control(window, name))
        top_left = item.mapToScene(QPointF(0, 0))
        bottom_right = item.mapToScene(QPointF(item.width(), item.height()))
        assert item.width() > 60 and item.height() >= 16, name
        assert 0 <= top_left.x() < bottom_right.x() <= window.width(), name
        assert 0 <= top_left.y() < bottom_right.y() <= window.height(), name
        if name.endswith(("Description", "Warning")):
            assert item.height() >= item.property("contentHeight"), name
        if item.inherits("QQuickComboBox"):
            assert not evaluate(item, "contentItem.truncated"), name


def test_appearance_controls_persist_real_keyboard_and_pointer_edits(settings_ui, qtbot):
    view, window, requests = settings_ui
    choose(window, "appearanceModeChoice", 1)
    choose(window, "appearanceThemeChoice", 2)
    accent = reveal(window, control(window, "appearanceColor_accent"))
    accent.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for character in "#4080c0":
        QTest.keyClick(window, Qt.Key(ord(character.upper())))
    QTest.keyClick(window, Qt.Key.Key_Return)
    slider = reveal(window, control(window, "appearanceContrastSlider"))
    slider.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Right)
    click(window, "smartPunctuationChoice")
    click(window, "sidebarTranslucentChoice")
    choose(window, "diffIndicatorsChoice", 1)
    qtbot.wait(30)
    saved = read_preferences(view.paths.config)["appearance"]
    assert saved["mode"] == "light" and saved["shared"]["theme"] == "warm"
    assert saved["shared"]["accent"] == "#4080c0"
    assert saved["contrast"] == 46
    assert saved["smart_punctuation"] and saved["sidebar_translucent"]
    assert saved["diff_indicators"] == "signs"
    assert not requests and view.project["permission"] == "read_only"


def test_theme_import_dialog_rejects_invalid_json_then_applies_appearance_only(settings_ui, qtbot):
    view, window, requests = settings_ui
    appearance = QQmlEngine.contextForObject(window).contextProperty("appearance")
    original = view.uiPreferences
    theme = json.loads(appearance.exportTheme())
    theme["appearance"]["mode"] = "light"
    theme["appearance"]["code_size"] = 17
    click(window, "importThemeButton")
    dialog = control(window, "themeJsonDialog")
    editor = control(window, "themeJsonInput")
    editor.setProperty("text", '{"provider":"unexpected"}')
    evaluate(control(window, "applyImportedThemeButton"), "clicked()")
    qtbot.wait(20)
    assert dialog.property("opened") and appearance.error
    assert view.uiPreferences == original
    editor.setProperty("text", json.dumps(theme))
    evaluate(control(window, "applyImportedThemeButton"), "clicked()")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert not appearance.error and appearance.codeFont.pixelSize() == 17
    assert {k: v for k, v in view.uiPreferences.items() if k != "appearance"} == {
        k: v for k, v in original.items() if k != "appearance"}
    assert not requests
