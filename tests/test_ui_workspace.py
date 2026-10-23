"""Synthetic UI journeys; these do not claim native application launches."""

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from shiboken6 import isValid
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from kira import __version__
from kira.domain.paths import Paths
from kira.domain.contracts import SCHEMA_VERSION
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.preferences import DEFAULTS, read_preferences
from test_ui_support import SERVICE_WAIT_MS, isolated_service_environment, start_service, stop_service


@pytest.fixture
def workspace(qapp, tmp_path):
    view = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    view._connected_value = True
    view._project = {"id": "fixture-project", "name": "Synthetic workspace", "root": str(tmp_path)}
    yield view
    view.close()
    # The application exits its Qt loop before shutdown; pytest continues that
    # loop for later tests. Dispose the native QObject graph before yielding it.
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(view)


@pytest.fixture
def ui(workspace, qtbot):
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    qtbot.wait(60)
    yield window
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def find(window, name):
    result = window.findChild(QObject, name)
    if result is not None:
        return result
    # Popup content is visually reparented to the overlay.
    def visit(item):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            if result := visit(child):
                return result
        return None
    return visit(window.contentItem())


def click(window, item):
    assert item is not None and item.property("enabled")
    point = item.mapToScene(QPoint(int(item.width() / 2), int(item.height() / 2))).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)


def sample_workspace(project="fixture-project"):
    return {"project_id": project, "tools": ["folder", "terminal"], "next_step": "Verify results",
            "resume": {"last_run": {"id": "old-run", "state": "interrupted",
                        "updated_at": "2026-10-10T12:00:00Z", "summary": "Synthetic incomplete task"},
                       "active_count": 0, "interrupted_count": 1},
            "watches": [], "captures": []}


def test_workspace_refresh_mutation_and_project_race(workspace, monkeypatch):
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace.refreshWorkspace()
    first = requests.pop()
    workspace._project = {"id": "second-project"}
    workspace.refreshWorkspace()
    second = requests.pop()
    second[2](sample_workspace("second-project"))
    first[2](sample_workspace())
    assert workspace.projectWorkspace["project_id"] == "second-project"
    assert "Interrotto" in workspace.resumeText and "incomplete task" in workspace.resumeText
    workspace.saveWorkspace(False, True, True, "Check the result explicitly")
    assert requests[-1][0] == "workspace.update"
    assert requests[-1][1] == {"project_id": "second-project", "tools": ["terminal", "editor"],
                                "next_step": "Check the result explicitly"}
    requests[-1][3]({"message": "Synthetic service unavailable"})
    assert workspace.workspaceError == "Synthetic service unavailable"
    assert not workspace.workspaceLoading
    workspace.watchFile(" src/main.py ")
    assert requests[-1][:2] == ("workspace.watch", {"project_id": "second-project", "path": "src/main.py"})
    workspace.unwatchFile("src/main.py")
    assert requests[-1][0] == "workspace.unwatch"
    workspace.refreshWorkspace()
    assert requests[-1][0] == "workspace.get"


def test_native_disconnect_requires_explicit_reconciliation_and_same_id(workspace, monkeypatch):
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace.launchAction("browser.search", "cosa è una meteora?")
    first = requests[-1]
    assert first[0] == "desktop.launch" and workspace.nativeBusy
    assert first[1]["query"] == "cosa è una meteora?"
    first[3]({"code": "DISCONNECTED", "message": "Synthetic disconnect"})
    assert workspace.nativeRetryAvailable and not workspace.nativeBusy
    workspace.launchAction("project.terminal", "")
    assert len(requests) == 1, "Another click cannot silently repeat uncertain work"
    workspace.retryNativeAction()
    second = requests[-1]
    assert first[1] == second[1]
    second[2]({"request_id": second[1]["request_id"], "status": "unknown",
               "message": "Dispatch was interrupted"})
    assert "Esito non confermato" in workspace.nativeResult
    assert not workspace.nativeRetryAvailable
    assert requests[-1][0] == "workspace.get", "Only read back project state after completion"


def test_native_requested_capture_and_workspace_results_are_distinct(workspace, monkeypatch, tmp_path, qtbot):
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace.launchWorkspace()
    assert requests[-1][0] == "workspace.launch"
    requests[-1][2]({"results": [{"status": "requested", "message": "Terminal requested"},
                                  {"status": "unavailable", "message": "Editor absent"}]})
    assert "Apertura richiesta" in workspace.nativeResult and "Non disponibile" in workspace.nativeResult
    assert "Completato" not in workspace.nativeResult
    capture = tmp_path / "fixture-capture.png"
    capture.write_bytes(b"synthetic file, not a real capture")
    workspace._workspace = {"captures": [{"path": str(capture)}]}
    opened = []
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl",
                        lambda url: opened.append(url.toLocalFile()) or True)
    workspace.openArtifact(str(capture))
    qtbot.waitUntil(lambda: opened == [str(capture)])
    workspace.openProjectFolder()
    assert requests[-1][0] == "desktop.launch"
    assert requests[-1][1]["action_id"] == "project.folder"


def test_search_rejects_stale_catalog_and_filters_projects(workspace, monkeypatch):
    workspace._projects = [{"id": "p1", "name": "Meteora", "root": "/fixture/space"},
                           {"id": "p2", "name": "Other", "root": "/fixture/other"}]
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace.searchActions("old")
    workspace.searchActions("meteora")
    requests[1][2]({"actions": []})
    requests[0][2]({"actions": [{"id": "stale", "label": "Must not show"}]})
    assert [item["id"] for item in workspace.desktopActions] == ["p1"]
    assert workspace.desktopActions[0]["kind"] == "project"
    workspace.searchActions("terminal")
    requests[-1][3]({"message": "Synthetic action error"})
    assert not workspace.desktopActions and workspace.actionError == "Synthetic action error"


def test_gui_preferences_private_and_separate_from_project_data(workspace):
    assert not workspace.onboardingComplete
    workspace.completeOnboarding(True)
    workspace.setDensity("compact")
    path = workspace.paths.config / "ui.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert workspace.paths.config.stat().st_mode & 0o777 == 0o700
    assert read_preferences(workspace.paths.config) == {
        **DEFAULTS,
        "onboarding_complete": True, "density": "compact", "model_controls_visible": False,
    }
    workspace.setDensity("invalid")
    assert workspace.density == "compact"
    assert not workspace.paths.db.exists()
    path.write_text("malformed")
    assert read_preferences(workspace.paths.config)["density"] == "comfortable"
    path.unlink()
    outside = workspace.paths.config.parent / "outside.json"
    outside.write_text('{"density":"compact"}')
    path.symlink_to(outside)
    assert read_preferences(workspace.paths.config)["density"] == "comfortable"
    workspace.completeOnboarding(False)
    assert not path.is_symlink() and outside.read_text() == '{"density":"compact"}'


def test_diagnostics_preview_required_and_export_feedback(workspace, monkeypatch):
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace.exportDiagnostics()
    assert not requests
    workspace.refreshDiagnostics()
    assert requests[-1][0] == "system.diagnostics" and not workspace.diagnosticsReady
    report = {"app_version": __version__, "database_schema": 2, "platform": {"session": "wayland"},
              "service": {"active_run_count": 0}, "actions": []}
    requests[-1][2](report)
    assert workspace.diagnosticsReady and json.loads(workspace.diagnosticsText) == report
    workspace.exportDiagnostics()
    assert requests[-1][:2] == ("diagnostics.export", {})
    requests[-1][2]({"path": "/private/fixture-diagnostics.json"})
    assert workspace.diagnosticsExportPath == "/private/fixture-diagnostics.json"


def test_qml_workspace_save_action_search_and_diagnostics(workspace, ui, monkeypatch, qtbot):
    state = sample_workspace()
    requests = []

    def rpc(method, params, done, failed=None):
        requests.append((method, copy.deepcopy(params)))
        if method == "workspace.get":
            done(copy.deepcopy(state))
        elif method == "workspace.update":
            state.update(params)
            done(copy.deepcopy(state))
        elif method == "desktop.actions":
            done({"actions": [{"id": "project.terminal", "label": "Terminale", "description": "In project",
                               "available": True, "reason": "", "requires_project": True}]})
        elif method == "desktop.launch":
            done({"status": "requested", "message": "Synthetic launch request"})
        elif method == "system.diagnostics":
            done({"app_version": __version__, "platform": {"session": "synthetic"}})
        elif method == "diagnostics.export":
            done({"path": "/private/fixture-report.json"})

    monkeypatch.setattr(workspace.client, "request", rpc)
    click(ui, find(ui, "resumeWorkspaceButton"))
    qtbot.wait(100)
    dialog = find(ui, "workspaceDialog")
    assert dialog.property("opened")
    field = find(ui, "workspaceNextStep")
    field.setProperty("text", "Next verified step")
    click(ui, find(ui, "workspaceEditor"))
    click(ui, find(ui, "saveWorkspaceButton"))
    assert state["next_step"] == "Next verified step"
    assert state["tools"] == ["folder", "terminal", "editor"]
    QMetaObject.invokeMethod(dialog, "close")
    QTest.keyClick(ui, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(100)
    assert find(ui, "actionDialog").property("opened")
    assert find(ui, "actionSearch").property("activeFocus")
    QTest.keyClick(ui, Qt.Key.Key_Down)
    QTest.keyClick(ui, Qt.Key.Key_Return)
    assert any(method == "desktop.launch" for method, _ in requests)
    web = find(ui, "browserQuery")
    web.setProperty("text", "cosa è una meteora?")
    click(ui, find(ui, "browserSearchButton"))
    assert any(params.get("query") == "cosa è una meteora?" for _, params in requests)
    QTest.keyClick(ui, Qt.Key.Key_Escape)
    ui.setProperty("page", 3)
    qtbot.wait(100)
    click(ui, find(ui, "diagnosticsButton"))
    qtbot.wait(80)
    assert find(ui, "diagnosticsDialog").property("opened")
    assert "synthetic" in find(ui, "diagnosticsReport").property("text")
    click(ui, find(ui, "exportDiagnosticsButton"))
    assert workspace.diagnosticsExportPath == "/private/fixture-report.json"


@pytest.mark.parametrize("width,height", [(880, 620), (1100, 780), (1440, 940)])
def test_dialogs_fit_supported_window_sizes(workspace, ui, qtbot, width, height):
    ui.resize(width, height)
    for name in ("workspaceDialog", "actionDialog", "diagnosticsDialog", "onboardingDialog"):
        dialog = find(ui, name)
        QMetaObject.invokeMethod(dialog, "open")
        qtbot.wait(50)
        assert dialog.property("width") <= width
        assert dialog.property("height") <= height
        QMetaObject.invokeMethod(dialog, "close")
    ui.setProperty("page", 0)
    workspace.setDensity("compact")
    qtbot.wait(60)
    for name in ("prompt", "sendButton", "resumeWorkspaceButton"):
        item = find(ui, name)
        assert isinstance(item, QQuickItem)
        rect = item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))
        assert 0 <= rect.left() <= rect.right() <= width + 1
        assert 0 <= rect.top() <= rect.bottom() <= height + 1


def test_qml_onboarding_completion_is_private(workspace, ui, qtbot):
    dialog = find(ui, "onboardingDialog")
    QMetaObject.invokeMethod(dialog, "open")
    qtbot.wait(70)
    click(ui, find(ui, "completeOnboardingButton"))
    assert workspace.onboardingComplete
    assert not dialog.property("opened")
    assert Path(workspace.paths.config / "ui.json").is_file()
    assert QGuiApplication.applicationVersion() in {"", __version__}


def test_qml_failed_save_keeps_draft_and_refresh_does_not_erase_it(workspace, ui, monkeypatch, qtbot):
    state = sample_workspace()
    requests = []

    def rpc(method, params, done, failed=None):
        if method == "workspace.get":
            done(copy.deepcopy(state))
        else:
            requests.append((method, params, done, failed))

    monkeypatch.setattr(workspace.client, "request", rpc)
    click(ui, find(ui, "resumeWorkspaceButton"))
    qtbot.wait(70)
    dialog = find(ui, "workspaceDialog")
    field = find(ui, "workspaceNextStep")
    field.setProperty("text", "Unsaved draft must survive")
    workspace.refreshWorkspace()
    assert field.property("text") == "Unsaved draft must survive"
    click(ui, find(ui, "saveWorkspaceButton"))
    assert not field.property("enabled"), "Avoid editing a draft while its save is in flight"
    requests[-1][3]({"code": "FAILED", "message": "Synthetic save failure"})
    assert field.property("text") == "Unsaved draft must survive"
    assert field.property("enabled") and dialog.property("dirty")
    assert workspace.workspaceError == "Synthetic save failure"
    click(ui, find(ui, "saveWorkspaceButton"))
    updated = {**state, "next_step": "Unsaved draft must survive"}
    requests[-1][2](updated)
    assert not dialog.property("dirty") and field.property("text") == updated["next_step"]


def test_qml_local_frame_rejection_keeps_editable_draft(workspace, ui, monkeypatch, qtbot):
    workspace._workspace = sample_workspace()
    workspace.workspaceChanged.emit()
    click(ui, find(ui, "resumeWorkspaceButton"))
    qtbot.wait(30)
    workspace.client.compatible = True
    monkeypatch.setattr("kira.ui.ipc.MAX_FRAME_BYTES", 512)
    writes = []
    monkeypatch.setattr(workspace.client.socket, "write", writes.append)
    dialog = find(ui, "workspaceDialog")
    field = find(ui, "workspaceNextStep")
    draft = "Ordinary unsaved note. " * 40
    field.setProperty("text", draft)
    click(ui, find(ui, "saveWorkspaceButton"))
    assert not workspace.workspaceLoading and "limite IPC" in workspace.workspaceError
    assert field.property("text") == draft
    assert field.property("enabled") and dialog.property("dirty")
    assert not writes and not workspace.client._pending
    field.setProperty("text", "Short revised note")
    click(ui, find(ui, "saveWorkspaceButton"))
    assert len(writes) == 1 and workspace.workspaceLoading
    request = json.loads(writes[0])
    assert request["params"]["next_step"] == "Short revised note"
    callback, _ = workspace.client._pending.pop(request["id"])
    callback({**sample_workspace(), "next_step": "Short revised note"})
    assert not workspace.workspaceLoading and not dialog.property("dirty")


def test_real_ipc_workspace_journey_uses_isolated_service(qapp, qtbot, monkeypatch, tmp_path):
    """Real Qt transport + service + SQLite/file hashes; no provider or native launches."""
    with tempfile.TemporaryDirectory(prefix="kira-ui-workspace-ipc-") as directory:
        home = Path(directory)
        paths = Paths(*(home / key for key in ("config", "data", "state", "runtime")))
        environment = isolated_service_environment(monkeypatch, home)
        log = (home / "service.log").open("w+")
        process = start_service(["-m", "kira.service.server"], environment, log)
        view = Workspace(paths)
        # Catalog discovery is independent of this workspace integration and would contact providers.
        monkeypatch.setattr(view, "discover", lambda _: None)
        monkeypatch.setattr(view, "refreshResources", lambda: None)
        try:
            qtbot.waitUntil(lambda: paths.socket.exists() or process.poll() is not None, timeout=SERVICE_WAIT_MS)
            assert process.poll() is None
            view.client.connect_service()
            qtbot.waitUntil(lambda: view.connected, timeout=SERVICE_WAIT_MS)
            project_dir = tmp_path / "real-project"
            project_dir.mkdir()
            reference = project_dir / "reference.txt"
            reference.write_text("initial evidence")
            view.createProject("Isolated GUI integration", str(project_dir), "", "read_only")
            qtbot.waitUntil(lambda: bool(view.projectWorkspace), timeout=SERVICE_WAIT_MS)
            view.saveWorkspace(True, False, True, "Verify changed reference")
            qtbot.waitUntil(lambda: view.projectWorkspace.get("next_step") == "Verify changed reference", timeout=SERVICE_WAIT_MS)
            assert view.projectWorkspace["tools"] == ["folder", "editor"]
            view.watchFile("reference.txt")
            qtbot.waitUntil(lambda: len(view.projectWorkspace.get("watches", [])) == 1, timeout=SERVICE_WAIT_MS)
            assert view.projectWorkspace["watches"][0]["state"] == "unchanged"
            reference.write_text("new evidence")
            view.refreshWorkspace()
            qtbot.waitUntil(lambda: not view.workspaceLoading, timeout=SERVICE_WAIT_MS)
            assert view.projectWorkspace["watches"][0]["state"] == "changed"
            view.watchFile("reference.txt")
            qtbot.waitUntil(lambda: not view.workspaceLoading, timeout=SERVICE_WAIT_MS)
            assert view.projectWorkspace["watches"][0]["state"] == "unchanged"
            reference.unlink()
            view.refreshWorkspace()
            qtbot.waitUntil(lambda: not view.workspaceLoading, timeout=SERVICE_WAIT_MS)
            assert view.projectWorkspace["watches"][0]["state"] == "missing"
            view.unwatchFile("reference.txt")
            qtbot.waitUntil(lambda: not view.workspaceLoading, timeout=SERVICE_WAIT_MS)
            assert not view.projectWorkspace["watches"]
            view.searchActions("terminal")
            qtbot.waitUntil(lambda: bool(view.desktopActions), timeout=SERVICE_WAIT_MS)
            assert any(a["id"] == "project.terminal" for a in view.desktopActions)
            view.refreshDiagnostics()
            qtbot.waitUntil(lambda: view.diagnosticsReady, timeout=SERVICE_WAIT_MS)
            assert json.loads(view.diagnosticsText)["database_schema"] == SCHEMA_VERSION
            view.exportDiagnostics()
            qtbot.waitUntil(lambda: bool(view.diagnosticsExportPath), timeout=SERVICE_WAIT_MS)
            exported = Path(view.diagnosticsExportPath)
            assert exported.is_relative_to(paths.data) and exported.is_file()
            assert "Isolated GUI integration" not in exported.read_text()
            project_id = view.project["id"]
            view.client.socket.abort()
            qtbot.waitUntil(lambda: not view.connected, timeout=SERVICE_WAIT_MS)
            view.client.connect_service()
            qtbot.waitUntil(lambda: view.connected and not view.workspaceLoading, timeout=SERVICE_WAIT_MS)
            view.openProject(project_id)
            qtbot.waitUntil(lambda: view.projectWorkspace.get("next_step") == "Verify changed reference", timeout=SERVICE_WAIT_MS)
            assert view.projectWorkspace["tools"] == ["folder", "editor"]
        finally:
            try:
                view.close()
                view.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                assert not isValid(view)
            finally:
                stop_service(process)
                log.close()


def test_saved_effort_visible_without_catalog_and_next_session_preserves_it(workspace, ui, monkeypatch, qtbot):
    workspace._sessions = [{"id": "saved-session", "project_id": workspace.project["id"], "backend": "codex", "model": "chosen-model",
                            "settings": {"reasoning_effort": "ultra", "tools": True}}]
    workspace._catalogs = {"codex": {"available": False, "models": [], "error": "Unavailable"}}
    def respond(method, params, done, failed=None, **kwargs):
        if method == "session.snapshot":
            done({"session": workspace._sessions[0], "runs": [], "artifacts": []})
        return True
    monkeypatch.setattr(workspace.client, "request", respond)
    workspace.selectSession("saved-session")
    qtbot.wait(50)
    effort = find(ui, "effortBox")
    assert effort.property("displayText") == "ultra · salvato"
    assert workspace.selectedEffort == "ultra"
    assert find(ui, "modelBox").property("displayText") == "chosen-model"
    # A new session may be necessary without changing the explicit model/effort choice.
    workspace.newChat()
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace.send("Synthetic pending session request; no provider execution")
    assert requests[-1][0] == "session.create"
    assert requests[-1][1]["model"] == "chosen-model"
    assert requests[-1][1]["settings"]["reasoning_effort"] == "ultra"
    assert workspace.selectedEffort == "ultra"



def test_dark_controls_contrast_survives_native_black_bright_text(tmp_path):
    # A fresh process guarantees the production Basic style, independent of the
    # styles already imported by other Qt tests in this pytest process.
    script = r"""
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
native = QPalette(app.palette())
native.setColor(QPalette.ColorRole.BrightText, QColor("#000000"))
native.setColor(QPalette.ColorRole.Dark, QColor("#000000"))
app.setPalette(native)
base = Path(sys.argv[1])
workspace = Workspace(Paths(*(base / key for key in ("config", "data", "state", "runtime"))))
engine = create_engine(workspace)
assert engine.rootObjects()
window = engine.rootObjects()[0]
appearance = engine.rootContext().contextProperty("appearance")
window.setProperty("closeConfirmed", True)
dialog = window.findChild(QObject, "workspaceDialog")
QMetaObject.invokeMethod(dialog, "open")
QTest.qWait(100)
def luminance(color):
    rgb = color.getRgbF()[:3]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
def contrast(foreground, background):
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)
tabs = [item for item in window.findChildren(QQuickItem)
        if item.property("text") in {"Ripresa", "File di riferimento", "Schermate"}
        and isinstance(item.property("checked"), bool)]
assert len(tabs) == 3
assert {tab.property("checked") for tab in tabs} == {False, True}
for tab in tabs:
    foreground = tab.property("contentItem").property("color")
    background = tab.property("background").property("color")
    assert foreground.name() == appearance.colors["text"]
    assert background.name() in {appearance.colors["background"], appearance.colors["sidebar"]}
    assert contrast(foreground, background) >= 4.5
combos = [item for item in window.findChildren(QQuickItem)
          if item.property("indicator") is not None
          and isinstance(item.property("currentIndex"), int)]
assert len(combos) == 45, len(combos)
assert {combo.objectName() for combo in combos} >= {"soulPicker", "soulMode", "reasoningSummaryHistory"}
for combo in combos:
    foreground = combo.property("indicator").property("color")
    background = combo.property("background").property("color")
    assert foreground.name() == appearance.colors["muted"]
    assert contrast(foreground, background) >= 4.5
QMetaObject.invokeMethod(dialog, "close")
window.close()
workspace.close()
engine.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
print("Production Basic style: checked and unchecked tabs plus all ComboBox indicators pass")
"""
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
                   "QT_QUICK_CONTROLS_STYLE": "Basic"}
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=environment,
                               capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
