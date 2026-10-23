"""Explicit permission selection in real Qt, with synthetic projects and IPC replies."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt
from PySide6.QtTest import QTest

from kira.ui.controller import Workspace
from kira.ui.preferences import read_preferences
from test_ui_support import wait_laid_out, wait_opened
import test_ui_settings_pane as settings
import test_ui_teams as team_tests
import test_ui_workbench as wb

view = wb.view
ui = wb.ui
settings_ui = settings.settings_ui
team_workspace = team_tests.team_workspace


def choose(control, index):
    assert control.isVisible() and control.isEnabled()
    control.forceActiveFocus()
    QTest.keyClick(control.window(), Qt.Key.Key_Space)
    QTest.keyClick(control.window(), Qt.Key.Key_Home)
    for _ in range(index):
        QTest.keyClick(control.window(), Qt.Key.Key_Down)
    QTest.keyClick(control.window(), Qt.Key.Key_Return)
    QTest.qWait(20)
    assert control.property("currentIndex") == index


def click(window, control):
    assert control.isVisible() and control.isEnabled()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                     pos=control.mapToScene(control.boundingRect().center()).toPoint())


def test_full_access_default_persists_without_promoting_existing_project(settings_ui, qtbot):
    workspace, window, calls = settings_ui
    before = deepcopy(workspace.project)
    settings.choose(window, "permissionDefaultChoice", 3)
    assert read_preferences(workspace.paths.config)["default_permission"] == "full_access"
    description = settings.control(window, "defaultPermissionWarning").property("text")
    assert "fuori dal progetto" in description and "senza privilegi amministrativi" in description
    assert workspace.project == before and not calls
    restored = Workspace(workspace.paths)
    try:
        assert restored.uiPreferences["default_permission"] == "full_access"
    finally:
        restored.close()
        restored.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("permission,index", [
    ("read_only", 0), ("project_write", 1), ("trusted_host", 2), ("full_access", 3),
])
def test_new_project_permission_is_explicit_and_preserves_old_defaults(ui, qtbot, permission, index):
    workspace, calls, _, window = ui
    old = deepcopy(workspace.project)
    workspace.setUiPreference("default_permission", permission)
    wb.evaluate(window, "performAction('new_project')")
    choice = window.findChild(QObject, "newProjectPermission")
    wait_opened(qtbot, window.findChild(QObject, "newProjectDialog"), choice,
                window.findChild(QObject, "createProjectButton"))
    assert choice.property("count") == 4 and choice.property("currentIndex") == index
    choose(choice, index)
    window.findChild(QObject, "projectNameInput").setProperty("text", "Synthetic new project")
    window.findChild(QObject, "projectRootInput").setProperty("text", str(workspace.paths.data))
    assert not [call for call in calls if call[0] == "project.create"]
    click(window, window.findChild(QObject, "createProjectButton"))
    request = wb.request(calls, "project.create")
    assert request[1]["permission"] == permission
    assert workspace.project == old


@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
def test_existing_project_full_access_requires_save_and_preserves_other_choices(ui, qtbot, size):
    workspace, calls, _, window = ui
    window.resize(*size)
    workspace._project.update(permission="trusted_host", instructions="Keep literal instructions")
    workspace.changed.emit()
    wb.evaluate(window, "performAction('permissions')")
    dialog = window.findChild(QObject, "projectPermissionDialog")
    choice = window.findChild(QObject, "projectPermissionChoice")
    wait_opened(qtbot, dialog, choice)
    assert choice.property("currentIndex") == 2
    choose(choice, 3)
    description = window.findChild(QObject, "projectPermissionDescription")
    assert "fuori dal progetto" in description.property("text")
    wait_laid_out(qtbot, description)
    assert description.height() >= description.property("contentHeight")
    assert not [call for call in calls if call[0] == "project.update"]
    assert workspace.project["permission"] == "trusted_host"
    click(window, window.findChild(QObject, "projectPermissionSave"))
    request = wb.request(calls, "project.update")
    assert request[1] == {"project_id": "project", "instructions": "Keep literal instructions",
                          "memory": {}, "permission": "full_access"}
    request[2]({"project": {**workspace.project, "permission": "full_access"}})
    assert workspace.project["permission"] == "full_access"
    QMetaObject.invokeMethod(dialog, "close")
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    wb.evaluate(window, "performAction('permissions')")
    wait_opened(qtbot, dialog, choice)
    assert choice.property("currentIndex") == 3
    assert workspace.selectedModel == "explicit-model" and workspace.selectedEffort == "ultra"


@pytest.mark.parametrize("mode", ["plan", "review", "recap"])
def test_full_access_footer_honors_next_request_read_only_mode(ui, qtbot, mode):
    workspace, _, _, window = ui
    workspace._project["permission"] = "full_access"
    workspace.changed.emit()
    summary = window.findChild(QObject, "chatPermissionSummary")
    qtbot.wait(20)
    assert "Accesso completo" in summary.property("text")
    workspace.workbench.setMode(mode)
    qtbot.wait(20)
    assert summary.property("text") == "Modalità in sola lettura"
    assert workspace.project["permission"] == "full_access"
    workspace.workbench.setMode("execute")
    qtbot.wait(20)
    assert "file oltre il progetto" in summary.property("text")


def test_full_access_footer_uses_active_team_lane_even_after_team_ends(ui, qtbot):
    workspace, _, _, window = ui
    workspace._project["permission"] = "full_access"
    workspace._runs = {"reader": {"id": "reader", "project_id": "project", "session_id": "chat",
                                   "state": "running", "execution_lane": "team_conversation",
                                   "effective_permission": "read_only"}}
    workspace.changed.emit()
    qtbot.wait(20)
    assert window.findChild(QObject, "chatPermissionSummary").property("text") == "Conversazione in sola lettura"
    assert workspace.project["permission"] == "full_access" and not workspace.teams.busy


def test_full_access_does_not_relabel_active_turn_when_next_request_mode_changes(ui, qtbot):
    workspace, _, _, window = ui
    workspace._project["permission"] = "full_access"
    workspace._runs = {"writer": {"id": "writer", "project_id": "project", "session_id": "chat",
                                   "state": "running", "effective_permission": "full_access"}}
    workspace.changed.emit()
    workspace.workbench.setMode("review")
    qtbot.wait(20)
    assert "Accesso completo" in window.findChild(QObject, "chatPermissionSummary").property("text")


def test_full_access_team_write_is_available_but_member_roles_stay_explicit(team_workspace):
    workspace, teams, replies = team_workspace
    workspace._project["permission"] = "full_access"
    workspace.changed.emit()
    assert teams.canWrite
    teams.setMode("manual")
    teams.setDraftText("goal", "Review synthetic files")
    teams.setMember(1, "access", "write")
    assert teams.draft["members"][0]["access"] == "read"
    teams.startTeam()
    request, = replies.calls("team.start")
    assert request["members"][0]["access"] == "read"
    assert request["members"][1]["access"] == "write"
    assert request["model"] == "saved-model" and request["settings"]["reasoning_effort"] == "ultra"


def test_full_access_environment_summary_distinguishes_host_and_read_only(view):
    workspace, _ = view
    for permission, expected in (("full_access", "Accesso completo"),
                                 ("trusted_host", "strumenti file limitati al progetto"),
                                 ("read_only", "Sola lettura")):
        workspace.assistant._environment = {"effective_permission": permission}
        assert expected in workspace.assistant.environmentSummary


def test_full_access_labels_translate_without_changing_permission_or_literal_text(ui, qtbot):
    workspace, _, engine, window = ui
    workspace._project.update(permission="full_access", name="Accesso completo", instructions="Sola lettura")
    workspace.changed.emit()
    workspace.setUiPreference("language", "en")
    localization = engine.rootContext().contextProperty("localization")
    qtbot.waitUntil(lambda: localization.language == "en")
    wb.evaluate(window, "performAction('permissions')")
    qtbot.wait(30)
    choice = window.findChild(QObject, "projectPermissionChoice")
    assert choice.property("currentText") == "Full access"
    assert "outside the project" in window.findChild(QObject, "projectPermissionDescription").property("text")
    assert "Full access" in window.findChild(QObject, "chatPermissionSummary").property("text")
    assert workspace.project["name"] == "Accesso completo"
    assert workspace.project["instructions"] == "Sola lettura"
    assert workspace.project["permission"] == "full_access"


@pytest.mark.parametrize("busy_source", ["run", "team"])
def test_permission_editor_cannot_escalate_an_active_run_or_team(ui, qtbot, busy_source):
    workspace, calls, _, window = ui
    if busy_source == "run":
        workspace._runs = {"writer": {"id": "writer", "project_id": "project", "session_id": "chat",
                                       "state": "running"}}
    else:
        workspace.teams._teams = [{"id": "team", "project_id": "project", "state": "running"}]
        workspace.teams.changed.emit()
    workspace.changed.emit()
    wb.evaluate(window, "performAction('permissions')")
    qtbot.wait(30)
    choice = window.findChild(QObject, "projectPermissionChoice")
    assert choice.isVisible() and not choice.isEnabled()
    assert workspace.project["permission"] == "project_write"
    assert not [call for call in calls if call[0] == "project.update"]
