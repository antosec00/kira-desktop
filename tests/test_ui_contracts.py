"""Qt contracts and ordinary presentation/error regressions, using isolated fixtures."""

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from shiboken6 import isValid
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QObject, QPersistentModelIndex, QPointF, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.appearance import Appearance
from kira.ui.controller import Workspace
from kira.ui.ipc import LocalClient
from kira.ui.models import TranscriptModel


@pytest.fixture
def workspace(qapp, tmp_path):
    view = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    yield view
    view.close()
    # The application exits its Qt loop before shutdown; pytest continues that
    # loop for later tests. Dispose the native QObject graph before yielding it.
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(view)


def evaluate(item, expression, fallback_context=None):
    context = QQmlEngine.contextForObject(item) or fallback_context
    assert context is not None, "Qt-created control labels need the engine's context"
    script = QQmlExpression(context, item, expression)
    value, _ = script.evaluate()
    assert not script.hasError(), script.error().toString()
    return value


def visual_items(item):
    yield item
    for child in item.childItems():
        yield from visual_items(child)


def test_workspace_properties_keep_qt_metatypes_and_python_values(workspace):
    expected = {
        "appVersion": "QString", "connected": "bool", "requestedContext": "int",
        "projectWorkspace": "QVariantMap", "desktopActions": "QVariantList",
        "projects": "QVariantList", "project": "QVariantMap", "sessions": "QVariantList",
        "models": "QVariantList", "efforts": "QVariantList", "activity": "QVariantList",
        "artifacts": "QVariantList", "settings": "QVariantMap", "approval": "QVariantMap",
    }
    meta = workspace.metaObject()
    python_types = {"QString": str, "bool": bool, "int": int,
                    "QVariantList": list, "QVariantMap": dict}
    for name, qt_type in expected.items():
        index = meta.indexOfProperty(name)
        assert index >= 0, name
        prop = meta.property(index)
        assert prop.typeName() == qt_type, name
        assert type(getattr(workspace, name)) is python_types[qt_type], name
        assert prop.isReadable() and not prop.isWritable(), name
        assert prop.isConstant() if name == "appVersion" else prop.hasNotifySignal(), name
    # Computed properties consume typed descriptor values as normal Python values.
    workspace._model = "ordinary-model"
    workspace._catalogs = {"ollama": {"available": True, "models": [
        {"id": "ordinary-model", "supported_reasoning_efforts": ["high", "ultra"]}]}}
    assert workspace.efforts == ["high", "ultra"]
    assert workspace.providerText.startswith("1 modelli")


def test_transcript_supports_qbytearray_roles_and_persistent_indexes(qapp):
    model = TranscriptModel()
    names = model.roleNames()
    assert all(isinstance(name, QByteArray) for name in names.values())
    role = next(role for role, name in names.items() if name.data() == b"body")
    model.append(kind="assistant", body="Ordinary first line")
    index = QPersistentModelIndex(model.index(0))
    assert model.data(index, role) == "Ordinary first line"
    assert model.rowCount(index) == 0
    model.update(0, body="Updated line")
    assert model.data(index, role) == "Updated line"
    model.clear()
    assert not index.isValid() and model.data(index, role) is None
    assert model.rowCount(QPersistentModelIndex()) == 0


def test_local_request_size_boundary_calls_one_error_handler(qapp, tmp_path, monkeypatch):
    client = LocalClient(str(tmp_path / "unused.sock"))
    client.compatible = True
    monkeypatch.setattr("kira.ui.ipc.MAX_FRAME_BYTES", 256)
    monkeypatch.setattr("kira.ui.ipc.uuid.uuid4", lambda: "ordinary-id")
    writes, errors, problems, responses = [], [], [], []
    monkeypatch.setattr(client.socket, "write", writes.append)
    client.problem.connect(problems.append)
    envelope = {"version": 1, "id": "ordinary-id", "method": "fixture.echo",
                "params": {"text": ""}}
    remaining = 256 - len(json.dumps(envelope, ensure_ascii=False).encode() + b"\n")
    try:
        assert client.request("fixture.echo", {"text": "a" * remaining}, responses.append,
                              errors.append)
        assert len(writes[0]) == 256
        assert json.loads(writes[0])["params"]["text"] == "a" * remaining
        client._pending.clear()
        assert not client.request("fixture.echo", {"text": "a" * (remaining + 1)},
                                  responses.append, errors.append)
        assert len(errors) == 1 and errors[0]["code"] == "FRAME_TOO_LARGE"
        assert problems == [] and not client._pending and len(writes) == 1
        assert not client.request("fixture.echo", {"text": "a" * (remaining + 1)},
                                  responses.append)
        assert problems == [errors[0]["message"]] and len(errors) == 1
        assert client.request("fixture.echo", {"text": "ordinary retry"}, responses.append)
        assert len(writes) == 2 and responses == []
    finally:
        client.close()


@pytest.mark.parametrize("operation", ["run", "session", "workspace", "native"])
def test_local_request_rejection_releases_pending_state(workspace, monkeypatch, operation):
    monkeypatch.setattr("kira.ui.ipc.MAX_FRAME_BYTES", 512)
    workspace.client.compatible = workspace._connected_value = True
    workspace._project = {"id": "fixture-project"}
    workspace._model = "ordinary-model"
    draft = "Ordinary draft. " * 100
    accepted = []
    workspace.promptAccepted.connect(lambda: accepted.append(True))
    if operation == "run":
        workspace._session = {"id": "fixture-session"}
        workspace.send(draft)
        assert not workspace.busy and workspace._pending_start is None
        assert "limite IPC" in workspace.notice
    elif operation == "session":
        workspace._model = draft
        workspace.send("Ordinary task")
        assert not workspace.busy and not workspace._creating_session
        assert "limite IPC" in workspace.notice
    elif operation == "workspace":
        workspace.saveWorkspace(True, False, False, draft)
        assert not workspace.workspaceLoading and "limite IPC" in workspace.workspaceError
    else:
        workspace.launchAction("browser.search", draft)
        assert not workspace.nativeBusy and not workspace.nativeRetryAvailable
        assert "limite IPC" in workspace.nativeResult
    assert not workspace.client._pending and accepted == []


@pytest.mark.parametrize("label", ["Ordinary label", "Literal <draft> & notes"])
def test_dynamic_qml_labels_and_tooltips_are_plain_text(workspace, qtbot, monkeypatch, label):
    project = {"id": "fixture-project", "name": label, "root": "/fixture/" + label}
    workspace._project, workspace._projects = project, [project]
    workspace._model = label
    workspace._status = workspace._notice = label
    workspace._catalogs = {"ollama": {"available": False, "error": label, "models": [
        {"id": label, "name": label}]}}
    workspace._artifacts = [{"path": "/fixture/artifact/" + label}]
    workspace._workspace = {"tools": [], "next_step": label, "watches": [
        {"path": label, "state": label, "checked_at": label}], "captures": [
        {"path": "/fixture/capture/" + label, "created_at": label}]}
    workspace.transcript.append(kind="tool", title=label, status=label, body=label)
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: False)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        def assert_labels():
            items = list(visual_items(window.contentItem()))
            matches = [item for item in items if label in str(item.property("text"))
                       and item.metaObject().indexOfProperty("textFormat") >= 0]
            assert matches
            for item in matches:
                assert evaluate(item, "Number(textFormat)", engine.rootContext()) == 0, (
                    item.metaObject().className(), item.property("text"))
            return {item.property("text") for item in matches}

        qtbot.wait(60)
        assert label in assert_labels()
        # Open real ComboBox delegates as well as the model catalog cards.
        model_box = window.findChild(QObject, "modelBox")
        evaluate(model_box, "popup.open()")
        qtbot.wait(30)
        assert_labels()
        evaluate(model_box, "popup.close()")
        window.setProperty("page", 1)
        qtbot.wait(30)
        assert label in assert_labels()
        window.setProperty("page", 0)
        action = window.findChild(QObject, "actionDialog")
        evaluate(action, "open()")
        qtbot.wait(30)
        assert "◇  " + label in assert_labels()
        evaluate(action, "close()")
        resume = window.findChild(QObject, "workspaceDialog")
        evaluate(resume, "open()")
        tabs = next(item for item in resume.findChildren(QObject) if item.inherits("QQuickTabBar"))
        tabs.setProperty("currentIndex", 1)
        qtbot.wait(30)
        assert "Controllato: " + label in assert_labels()
        tabs.setProperty("currentIndex", 2)
        qtbot.wait(30)
        assert "↗  " + label in assert_labels()
        evaluate(resume, "close()")
        items = list(visual_items(window.contentItem()))
        assert {item.property("toolTipText") for item in items} >= {
            project["root"], "/fixture/artifact/" + label}
        # Notify signals must continue to update the real QML bindings.
        workspace._project = {**project, "name": "Updated " + label}
        workspace.changed.emit()
        qtbot.wait(30)
        assert "Esecuzioni di Updated " + label in assert_labels()
        kicker = window.findChild(QObject, "projectHeaderKicker")
        assert kicker.property("text") == "Updated " + label
        assert evaluate(kicker, "Number(textFormat)", engine.rootContext()) == 0
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("label", ["Ordinary label", "Literal <draft> & notes"])
def test_shared_button_tooltip_preserves_literal_path(workspace, label):
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("appearance", Appearance(workspace, engine))
    source = Path(__import__("kira.ui.app", fromlist=["__file__"]).__file__).parent / "qml/KButton.qml"
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(source)))
    button = component.create()
    assert button is not None, [error.toString() for error in component.errors()]
    try:
        path = "/fixture/" + label
        button.setProperty("text", label)
        button.setProperty("toolTipText", path)
        tip = button.findChild(QObject, "literalToolTip")
        assert tip is not None and tip.property("text") == path
        assert evaluate(tip, "contentItem.text === text && Number(contentItem.textFormat) === 0")
    finally:
        button.deleteLater()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_all_combo_popups_show_string_and_object_model_labels(workspace, qtbot):
    workspace._connected_value = True
    workspace._project = {"id": "fixture-project", "name": "Ordinary project", "permission": "read_only"}
    workspace._backend = "codex"
    workspace._model = "chosen-model"
    workspace._catalogs = {"codex": {"available": True, "models": [
        {"id": "chosen-model", "name": "Chosen model",
         "supported_reasoning_efforts": ["high", "ultra"]},
        {"id": "other-model", "name": "Other model"}]}}
    workspace._sessions = [{"id": "session-one", "backend": "codex", "model": "chosen-model"}]
    workspace._session = workspace._sessions[0]
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    observed = set()
    try:
        combos = [item for item in window.findChildren(QQuickItem)
                  if item.inherits("QQuickComboBox") and item.property("count") > 0]
        assert len(combos) == 40
        for combo in combos:
            evaluate(window.findChild(QObject, "assistantEnvironmentDialog"), "close()")
            if combo.objectName() == "knowledgeScope":
                evaluate(window.findChild(QObject, "assistantEnvironmentDialog"), "open()")
            evaluate(window, "newProject.close(); memoryDialog.close(); workbenchDialog.close(); providerSwitchDialog.close(); metricsDialog.close(); webDialog.close(); researchDialog.close(); soulDialog.close(); modelPopover.close()")
            if combo.objectName() == "webSearchMode":
                evaluate(window, "webDialog.open()")
            if combo.objectName() == "usageProviderChoice":
                evaluate(window, "metricsDialog.open()")
            if combo.objectName() in {"backendBox", "modelBox", "effortBox", "sessionBox"}:
                evaluate(window, "modelPopover.open()")
            if combo.objectName().startswith("switchProvider"):
                evaluate(window, "providerSwitchDialog.open()")
            section = {"workbenchSection": "queue", "forkRunChoice": "chat", "changedFileChoice": "changes"}.get(combo.objectName())
            if section:
                evaluate(window, 'workbenchDialog.openSection("' + section + '")')
            settings_pane = combo.parentItem()
            while settings_pane and settings_pane.objectName() != "settingsPane":
                settings_pane = settings_pane.parentItem()
            window.setProperty("page", 3 if settings_pane else 0)
            if settings_pane:
                category = combo.parentItem()
                while category and category.property("settingsCategory") is None:
                    category = category.parentItem()
                if category:
                    evaluate(settings_pane, "showSection(" + str(category.property("settingsCategory")) + ")")
                    qtbot.wait(20)
                offset = combo.mapToItem(settings_pane, QPointF(0, 0)).y() - 20
                evaluate(settings_pane, "contentItem.contentY += " + str(offset))
            if combo.objectName() in {"newProjectPermission", "projectPermissionChoice"} or (not combo.objectName() and not combo.property("textRole")):
                dialog = "memoryDialog" if "nessuna sandbox" in evaluate(combo, "textAt(2)") else "newProject"
                evaluate(window, dialog + ".open()")
                assert combo.isVisible() and combo.isEnabled()
            if combo.objectName().startswith("research"):
                page = "links" if combo.objectName() == "researchRelation" else "editor"
                evaluate(window, 'researchDialog.open(); researchDialog.page = "' + page + '"; researchDialog.syncEditor()')
                qtbot.wait(20)
                pane = window.findChild(QObject, "researchScroll")
                offset = combo.mapToItem(pane, QPointF(0, 0)).y() - 80
                evaluate(pane, "contentItem.contentY += " + str(offset))
                assert combo.isVisible() and combo.isEnabled()
            if combo.objectName() == "soulMode":
                evaluate(window, "soulDialog.open(); soulDialog.expanded = true")
                qtbot.wait(20)
                pane = window.findChild(QObject, "soulEditorScroll")
                offset = combo.mapToItem(pane, QPointF(0, 0)).y() - 80
                evaluate(pane, "contentItem.contentY += " + str(offset))
                assert combo.isVisible() and combo.isEnabled()
            if combo.objectName() in {"ollamaContextPreset", "ollamaThinkMode"}:
                # Ollama-only rows live in the details panel and in the narrow-window drawer.
                workspace._backend = "ollama"
                workspace.changed.emit()
                host = combo.parentItem()
                while host and host.objectName() != "detailsPanel":
                    host = host.parentItem()
                if host is not None:
                    workspace.setUiPreference("details_visible", True)
                else:
                    evaluate(window, "detailsDrawer.open()")
                    qtbot.waitUntil(lambda: evaluate(window, "detailsDrawer.position") >= 1)
                qtbot.wait(40)
                assert combo.isVisible() and combo.isEnabled()
            evaluate(combo, "popup.open()")
            qtbot.wait(30)
            values = []
            for index in range(combo.property("count")):
                # Font lists are virtualized: realize each row through the real
                # popup viewport before checking its rendered/accessibility text.
                value = json.loads(evaluate(combo, """
                    (function(index) {
                        popup.contentItem.positionViewAtIndex(index, ListView.Contain);
                        popup.contentItem.forceLayout();
                        const item = popup.contentItem.itemAtIndex(index);
                        return JSON.stringify(item ? [item.contentItem.text,
                            item.contentItem.textFormat, item.Accessible.name, textAt(index)] : null);
                    })(%d)
                """ % index))
                assert value is not None, (combo.objectName(), index)
                assert value[0] and value[1] == 0 and value[0] == value[2] == value[3], value
                values.append(value[0])
            assert values, combo.objectName()
            observed.add(tuple(values))
            evaluate(combo, "popup.close()")
            if combo.objectName() in {"ollamaContextPreset", "ollamaThinkMode"}:
                evaluate(window, "detailsDrawer.close()")
                workspace.setUiPreference("details_visible", False)
                workspace._backend = "codex"
                workspace.changed.emit()
        appearance = engine.rootContext().contextProperty("appearance")
        font_lists = {
            ("Come l’interfaccia", *appearance.fontFamilies),
            ("Monospazio di sistema", *appearance.fontFamilies),
        }
        interface_fonts = ["Carattere di sistema", *appearance.fontFamilies]
        if appearance.style["ui_family"] not in appearance.fontFamilies:
            interface_fonts.insert(1, appearance.style["ui_family"] + " · non installato")
        font_lists.add(tuple(interface_fonts))
        assert {combo.objectName() for combo in combos} >= {
            "appearanceModeChoice", "appearanceThemeChoice", "reduceMotionChoice", "diffIndicatorsChoice", "soulMode",
            *("appearanceFont_" + role for role in ("ui", "content", "code")),
            *("appearanceFontStyle_" + role for role in ("ui", "content", "code")),
        }
        assert observed == font_lists | {
            ("Tutto", "Guida di Kira", "Capacità", "Chat", "Progetto", "Strumenti", "Anima di Kira"),
            ("Italiano", "English"), ("Manuale", "Suggerimenti", "Adattiva"),
            ("Esegui", "Pianifica", "Revisione", "Riepiloga"),
            ("Coda del progetto", "Cronologia dei messaggi inviati", "Gestisci chat", "Chat archiviate", "Modifiche", "Indice semantico AST", "Obiettivo della chat"),
            ("stdio · comando locale", "HTTP / SSE · endpoint remoto"),
            ("Fine della chat corrente",), ("Tutte le modifiche",),
            ("Segui il sistema", "Chiara", "Scura"), ("Kira", "Neutro", "Caldo"),
            ("Segui il sistema", "Sempre", "Mai"), ("Colore e segni", "Solo segni"),
            ("Normale", "Medio", "Semigrassetto", "Grassetto", "Corsivo"),
            ("Ollama", "Codex", "OpenAI diretto · ChatGPT", "DeepSeek Account", "DeepSeek Web · sperimentale", "Claude Code · uso personale"),
            ("Codex", "OpenAI diretto · ChatGPT", "Ollama", "Claude Code"),
            ("Intera", "Solo icone", "Nascosta"),
            ("Predefinito Ollama", "32K", "128K", "256K"),
            ("Predefinito del modello", "Ragionamento interno disattivato", "Ragionamento interno attivato"),
            ("Chosen model", "Other model"),
            ("Predefinito del provider", "high", "ultra"), ("Predefinito del provider",), ("Nuova chat",),
            ("Ricerca disattivata", "Contenuti indicizzati", "Accesso live"),
            ("Obiettivo", "Ipotesi", "Esperimento", "Osservazione", "Decisione", "Questione aperta"),
            ("Non valutato", "Supportato dalle fonti", "Contraddetto dalle fonti", "Inconcludente", "Da riverificare"),
            ("Aperto", "Chiuso"), ("Collegato a", "Supporta", "Contraddice", "Sostituisce"),
            ("Comoda", "Compatta"),
            ("Quando c’è lavoro in corso", "Sempre", "Mai"),
            ("Applicazione predefinita del sistema", "Visual Studio Code"),
            ("Sola lettura", "Scrittura nel progetto", "Host fidato · comandi come il tuo utente", "Accesso completo"),
            ("Sola lettura", "Scrittura nel progetto", "Host fidato · nessuna sandbox per i comandi Kira", "Accesso completo"),
        }
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_combo_popup_contrast_with_native_dark_palette(tmp_path):
    # A fresh process selects the production Basic style before any QML import.
    script = r'''
import json, sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QObject, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
native = QPalette(app.palette())
for name, color in {"Window": "#161a1d", "Base": "#191e21", "Text": "#e7ece9",
                    "Button": "#242c30", "ButtonText": "#e7ece9", "Light": "#242c30",
                    "Midlight": "#20262b", "Dark": "#101416", "BrightText": "#000000",
                    "Highlight": "#8ab4f8", "HighlightedText": "#11243e"}.items():
    native.setColor(getattr(QPalette.ColorRole, name), QColor(color))
app.setPalette(native)
base = Path(sys.argv[1])
workspace = Workspace(Paths(*(base / key for key in ("config", "data", "state", "runtime"))))
workspace._connected_value = True
workspace._project = {"id": "fixture-project", "name": "Ordinary project", "permission": "read_only"}
workspace._backend, workspace._model, workspace._effort = "codex", "chosen-model", "ultra"
workspace._catalogs = {"codex": {"available": True, "models": [
    {"id": "chosen-model", "name": "Chosen model", "supported_reasoning_efforts": ["high", "ultra"]},
    {"id": "other-model", "name": "Other model"}]}}
workspace._sessions = [{"id": "session-one", "backend": "codex", "model": "chosen-model"}]
workspace._session = workspace._sessions[0]
engine = create_engine(workspace)
assert engine.rootObjects()
window = engine.rootObjects()[0]
QTest.qWait(40)
def evaluate(item, expression):
    context = QQmlEngine.contextForObject(item) or engine.rootContext()
    script = QQmlExpression(context, item, expression)
    result, _ = script.evaluate()
    assert not script.hasError(), script.error().toString()
    return result
def luminance(rgb):
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
def contrast(foreground, background):
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)
def settled_row_center(combo):
    # Hosts such as the model popover size themselves from their content, so a
    # row can still move for a few frames after opening; press it once stable.
    readings = []
    for _ in range(150):
        readings.append(evaluate(combo, """
            (function() {
                const item = popup.opened ? popup.contentItem.itemAtIndex(0) : null;
                if (!item || item.width <= 0 || item.height <= 0) return "";
                const point = item.mapToItem(null, item.width / 2, item.height / 2);
                return JSON.stringify([Math.round(point.x), Math.round(point.y)]);
            })()
        """))
        if len(readings) >= 3 and readings[-1] and readings[-1] == readings[-2] == readings[-3]:
            return json.loads(readings[-1])
        QTest.qWait(20)
    raise AssertionError(("popup row did not settle", combo.objectName(), readings[-3:]))
measurements = []
try:
    combos = [item for item in window.findChildren(QQuickItem) if item.inherits("QQuickComboBox") and item.property("count") > 0]
    assert len(combos) == 40
    for number, combo in enumerate(combos):
        evaluate(window.findChild(QObject, "assistantEnvironmentDialog"), "close()")
        if combo.objectName() == "knowledgeScope":
            evaluate(window.findChild(QObject, "assistantEnvironmentDialog"), "open()")
        evaluate(window, "newProject.close(); memoryDialog.close(); workbenchDialog.close(); providerSwitchDialog.close(); metricsDialog.close(); webDialog.close(); researchDialog.close(); soulDialog.close(); modelPopover.close()")
        if combo.objectName() == "webSearchMode":
            evaluate(window, "webDialog.open()")
        if combo.objectName() == "usageProviderChoice":
            evaluate(window, "metricsDialog.open()")
        if combo.objectName() in {"backendBox", "modelBox", "effortBox", "sessionBox"}:
            evaluate(window, "modelPopover.open()")
        if combo.objectName().startswith("switchProvider"):
            evaluate(window, "providerSwitchDialog.open()")
        section = {"workbenchSection": "queue", "forkRunChoice": "chat", "changedFileChoice": "changes"}.get(combo.objectName())
        if section:
            evaluate(window, 'workbenchDialog.openSection("' + section + '")')
        settings_pane = combo.parentItem()
        while settings_pane and settings_pane.objectName() != "settingsPane":
            settings_pane = settings_pane.parentItem()
        window.setProperty("page", 3 if settings_pane else 0)
        if settings_pane:
            category = combo.parentItem()
            while category and category.property("settingsCategory") is None:
                category = category.parentItem()
            if category:
                evaluate(settings_pane, "showSection(" + str(category.property("settingsCategory")) + ")")
                QTest.qWait(20)
            offset = combo.mapToItem(settings_pane, QPointF(0, 0)).y() - 20
            evaluate(settings_pane, "contentItem.contentY += " + str(offset))
        if combo.objectName() in {"newProjectPermission", "projectPermissionChoice"} or (not combo.objectName() and not combo.property("textRole")):
            dialog = "memoryDialog" if "nessuna sandbox" in evaluate(combo, "textAt(2)") else "newProject"
            evaluate(window, dialog + ".open()")
            assert combo.isVisible() and combo.isEnabled()
        if combo.objectName().startswith("research"):
            page = "links" if combo.objectName() == "researchRelation" else "editor"
            evaluate(window, 'researchDialog.open(); researchDialog.page = "' + page + '"; researchDialog.syncEditor()')
            QTest.qWait(20)
            pane = window.findChild(QObject, "researchScroll")
            offset = combo.mapToItem(pane, QPointF(0, 0)).y() - 80
            evaluate(pane, "contentItem.contentY += " + str(offset))
            assert combo.isVisible() and combo.isEnabled()
        if combo.objectName() == "soulMode":
            evaluate(window, "soulDialog.open(); soulDialog.expanded = true")
            QTest.qWait(20)
            pane = window.findChild(QObject, "soulEditorScroll")
            offset = combo.mapToItem(pane, QPointF(0, 0)).y() - 80
            evaluate(pane, "contentItem.contentY += " + str(offset))
            assert combo.isVisible() and combo.isEnabled()
        if combo.objectName() in {"ollamaContextPreset", "ollamaThinkMode"}:
            # Ollama-only rows live in the details panel and in the narrow-window drawer.
            workspace._backend = "ollama"
            workspace.changed.emit()
            host = combo.parentItem()
            while host and host.objectName() != "detailsPanel":
                host = host.parentItem()
            if host is not None:
                workspace.setUiPreference("details_visible", True)
            else:
                evaluate(window, "detailsDrawer.open()")
                for _ in range(30):
                    if evaluate(window, "detailsDrawer.position") >= 1:
                        break
                    QTest.qWait(30)
            QTest.qWait(40)
            assert combo.isVisible() and combo.isEnabled()
        evaluate(combo, "popup.open()")
        QTest.qWait(35)
        evaluate(combo, "popup.contentItem.positionViewAtIndex(0, ListView.Beginning); popup.contentItem.forceLayout()")
        for state in ("normal", "highlighted", "focus", "pressed"):
            reason = "TabFocusReason" if state == "focus" else "MouseFocusReason"
            highlight = "true" if state == "highlighted" else "false"
            evaluate(combo, """
                (function() {
                    const item = popup.contentItem.itemAtIndex(0);
                    item.focus = false;
                    item.forceActiveFocus(Qt.%s);
                    item.highlighted = %s;
                    item.down = undefined;
                })()
            """ % (reason, highlight))
            if state == "pressed":
                x, y = settled_row_center(combo)
                QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
            values = json.loads(evaluate(combo, """
                (function() {
                    const item = popup.contentItem.itemAtIndex(0);
                    const foreground = item.contentItem.color;
                    const background = item.background;
                    const underneath = popup.background.color;
                    const alpha = background.visible ? background.color.a * background.opacity : 0;
                    const rgb = ["r", "g", "b"].map(function(channel) {
                        return background.color[channel] * alpha + underneath[channel] * (1 - alpha);
                    });
                    return JSON.stringify({foreground: [foreground.r, foreground.g, foreground.b],
                        background: rgb, textFormat: item.contentItem.textFormat,
                        text: item.contentItem.text, name: item.Accessible.name,
                        focus: item.visualFocus, pressed: item.pressed, down: item.down});
                })()
            """))
            ratio = contrast(values["foreground"], values["background"])
            assert values["textFormat"] == 0
            assert values["name"] == values["text"] and values["text"]
            assert values["focus"] == (state == "focus"), (state, values)
            assert values["pressed"] == values["down"] == (state == "pressed"), (state, values)
            measurements.append({"menu": number, "label": values["text"], "state": state,
                                 "contrast": ratio, **values})
            assert ratio >= 4.5, measurements[-1]
            if state == "pressed":
                # Release outside the row to cancel, preserving the user's selected backend/model.
                QTest.mouseMove(window, QPoint(1, 1))
                QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        evaluate(combo, "popup.close()")
        if combo.objectName() in {"ollamaContextPreset", "ollamaThinkMode"}:
            evaluate(window, "detailsDrawer.close()")
            workspace.setUiPreference("details_visible", False)
            workspace._backend = "codex"
            workspace.changed.emit()
    evaluate(window, "newProject.close(); memoryDialog.close(); workbenchDialog.close(); metricsDialog.close(); webDialog.close(); researchDialog.close(); soulDialog.close()")
    assert workspace.backend == "codex" and workspace.selectedModel == "chosen-model"
    assert workspace.selectedEffort == "ultra"
    density = window.findChild(QObject, "densityChoice")
    window.setProperty("page", 3)
    pane = window.findChild(QObject, "settingsPane")
    evaluate(pane, "showSection(1)")
    QTest.qWait(20)
    offset = density.mapToItem(pane, QPointF(0, 0)).y() - 80
    evaluate(pane, "contentItem.contentY += " + str(offset))
    QTest.qWait(30)
    density.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(window, Qt.Key.Key_Space)
    QTest.qWait(30)
    assert evaluate(density, "popup.visible")
    QTest.keyClick(window, Qt.Key.Key_Down)
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert workspace.density == "compact"
    assert len(measurements) == len(combos) * 4
    print(json.dumps({"style": "Basic", "measurements": measurements,
                      "combo_count": len(combos), "keyboard_selection": True, "accessible_labels": True}))
finally:
    window.setProperty("closeConfirmed", True)
    window.close()
    workspace.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
             "QT_QUICK_CONTROLS_STYLE": "Basic"}, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert len(report["measurements"]) == report["combo_count"] * 4 == 160
    (tmp_path / "combo-contrast.json").write_text(json.dumps(report, indent=2) + "\n")
