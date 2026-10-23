"""Native design navigation retains settings and explicit recorded work controls."""

import copy

import pytest
from PySide6.QtCore import QObject, QPointF, QRectF, Qt
from PySide6.QtQml import QQmlEngine
from PySide6.QtTest import QTest

import test_ui_browser_authentication as auth_tests
import test_ui_settings_pane as settings_tests
import test_ui_teams as team_tests

settings_ui = settings_tests.settings_ui
team_workspace = team_tests.team_workspace
team_ui = team_tests.team_ui
browser = auth_tests.browser
auth_ui = auth_tests.auth_ui


def click_visible(window, item):
    assert item.isVisible() and item.isEnabled(), item.objectName()
    bounds = item.mapRectToScene(item.boundingRect())
    assert QRectF(0, 0, window.width(), window.height()).contains(bounds)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=bounds.center().toPoint())
    QTest.qWait(25)


@pytest.mark.parametrize("size,font_size", [((1380, 900), 13), ((880, 620), 18)])
def test_settings_categories_keep_unsaved_fields_and_do_not_send_commands(settings_ui, size, font_size):
    workspace, window, requests = settings_ui
    window.resize(*size)
    appearance = QQmlEngine.contextForObject(window).contextProperty("appearance")
    appearance.setOption("ui_size", font_size)
    QTest.qWait(30)
    original = (workspace.backend, workspace.selectedModel, workspace.selectedEffort,
                copy.deepcopy(workspace._project))
    endpoint = settings_tests.reveal(window, settings_tests.control(window, "settingsEndpoint"))
    endpoint.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for character in "http://127.0.0.1:12000":
        QTest.keyClick(window, character)
    assert not requests
    categories = ("settingsAppearanceCategory", "settingsWorkCategory",
                  "settingsConnectionsCategory", "settingsDataCategory")
    pane = settings_tests.control(window, "settingsPane")
    for index, name in enumerate(categories):
        control = settings_tests.control(window, name)
        click_visible(window, control)
        assert pane.property("selectedSection") == index
        assert control.property("checked")
        if index == 0:
            settings_tests.choose(window, "appearanceModeChoice", 1)
        assert settings_tests.control(window, "settingsEndpoint") is endpoint
        assert endpoint.property("text") == "http://127.0.0.1:12000"
        assert not requests, "Category navigation must not apply service settings or launch work"
    assert original == (workspace.backend, workspace.selectedModel, workspace.selectedEffort,
                        workspace._project)
    # The same pending edit is sent only by the explicit Save action.
    settings_tests.click(window, "settingsSaveButton")
    assert len(requests) == 1 and requests[0][0] == "settings.update"
    assert requests[0][1]["ollama_url"] == "http://127.0.0.1:12000"


def test_settings_preview_precedes_font_details_and_switching_retains_appearance(settings_ui):
    workspace, window, requests = settings_ui
    preview = settings_tests.control(window, "appearancePreview")
    font = settings_tests.control(window, "appearanceFont_ui")
    assert preview.mapToScene(QPointF()).y() < font.mapToScene(QPointF()).y()
    settings_tests.choose(window, "appearanceModeChoice", 1)
    settings_tests.choose(window, "appearanceThemeChoice", 2)
    before = copy.deepcopy(workspace.uiPreferences)
    settings_tests.reveal(window, settings_tests.control(window, "settingsToolRounds"))
    settings_tests.reveal(window, preview)
    assert workspace.uiPreferences == before and not requests
    assert settings_tests.control(window, "appearanceModeChoice").property("currentIndex") == 1
    assert settings_tests.control(window, "appearanceThemeChoice").property("currentIndex") == 2


def test_team_task_rows_link_recorded_member_names_without_changing_evidence(team_ui, qtbot):
    _, teams, replies, window = team_ui
    value = team_tests.snapshot(
        state="paused", phase="awaiting_user", round_limit=1,
        steps=[{"id": "step-review", "member_id": "agent_1", "title": "Review saved notes",
                "state": "completed", "round_index": 1, "run_id": "run-review", "depends_on": []}],
    )
    original = copy.deepcopy(value)
    team_tests.select(teams, replies, value)
    qtbot.wait(35)
    assignment = team_tests.visual_item(window.contentItem(), "teamTaskAssignment_step-review")
    assert assignment is not None
    assert assignment.property("text") == "Reviewer · agent_1 · round 1"
    assert teams.team["round_limit"] == 1 and value == original
    assert not replies.calls("team.resume") and not replies.calls("team.start")
    assert window.findChild(QObject, "teamContinuationRoundLimitInput").isVisible()


@pytest.mark.parametrize("size,font_size", [((1380, 900), 13), ((880, 620), 18)])
def test_team_editor_grouping_keeps_manual_roster_and_zero_limit_editable(team_ui, qtbot, size, font_size):
    workspace, teams, replies, window = team_ui
    window.resize(*size)
    QQmlEngine.contextForObject(window).contextProperty("appearance").setOption("ui_size", font_size)
    teams.setMode("manual")
    teams.setCount(1)
    teams.setDraftText("goal", "Read ordinary project notes")
    editor = window.findChild(QObject, "teamEditor")
    team_tests.evaluate(editor, "open()")
    qtbot.waitUntil(lambda: editor.property("opened"))
    qtbot.wait(40)
    form = window.findChild(QObject, "teamEditorForm")
    role = team_tests.visual_item(window.contentItem(), "teamMemberRole_0")
    model = window.findChild(QObject, "teamModelInput")
    limit = window.findChild(QObject, "teamRoundLimitInput")
    assert role.mapToScene(QPointF()).y() < model.mapToScene(QPointF()).y() < limit.mapToScene(QPointF()).y()
    offset = limit.mapToItem(form, QPointF()).y() - 30
    team_tests.evaluate(form, "contentItem.contentY += " + str(offset))
    qtbot.wait(20)
    content = limit.property("contentItem")
    click_visible(window, content)
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_0)
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert teams.draft["round_limit"] == 0
    assert teams.draft["model"] == workspace.selectedModel == "saved-model"
    assert teams.draft["reasoning_effort"] == workspace.selectedEffort == "ultra"
    assert not replies.calls("team.start")
    click_visible(window, window.findChild(QObject, "startTeamButton"))
    assert replies.calls("team.start")[-1]["round_limit"] == 0


@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
def test_browser_ownership_band_remains_manual_until_explicit_handoff(auth_ui, size):
    with auth_ui(size=size, large=True, manual=True, auth_pending=False,
                 auth_needs_manual=False, auth_result="completed") as ui:
        band = ui.find("browserOwnershipBand")
        assert band.isVisible()
        assert ui.find("browserActionApprovalStatus").property("text") == "Controllo tuo"
        assert not auth_tests.operational(ui.requests)
        ui.window.resize(*size)
        QTest.qWait(30)
        assert ui.controller.controlling and not auth_tests.operational(ui.requests)
        ui.click("browserTakeControl")
        calls = auth_tests.operational(ui.requests)
        assert len(calls) == 1 and calls[0][0] == "session.browser.command"
        assert calls[0][1]["action"] == "ui_resume"
