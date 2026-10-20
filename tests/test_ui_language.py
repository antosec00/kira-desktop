"""English/Italian presentation switches in real Qt with entirely synthetic data."""

import ast
import copy
import json
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.preferences import read_preferences


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def evaluate(obj, expression):
    script = QQmlExpression(QQmlEngine.contextForObject(obj), obj, expression)
    result, _ = script.evaluate()
    assert not script.hasError(), script.error().toString()
    return result


@pytest.fixture
def language_ui(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "synthetic-project", "name": "Impostazioni", "root": str(tmp_path / "Progetti"),
                          "permission": "read_only", "instructions": "Chiudi", "memory": {}}
    workspace._backend, workspace._model, workspace._effort = "codex", "Modelli", "ultra"
    workspace._session = {"id": "synthetic-session", "project_id": "synthetic-project", "model": "Modelli",
                          "backend": "codex", "settings": {"reasoning_effort": "ultra"}}
    workspace._sessions = [workspace._session.copy()]
    workspace._catalogs = {"codex": {"available": True, "models": [{"id": "Modelli", "name": "Modelli"}]}}
    workspace._connected_value = True
    workspace._runs = {"synthetic-run": {"id": "synthetic-run", "state": "running", "session_id": "synthetic-session"}}
    workspace._preferences["onboarding_complete"] = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args))
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(880, 620)
    localization = engine.rootContext().contextProperty("localization")
    qtbot.wait(60)
    calls.clear()
    yield workspace, window, localization, calls
    assert not any("ReferenceError" in record.message or "binding loop" in record.message.lower()
                   for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    workspace.close()
    engine.deleteLater()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def choose_language(window, code, qtbot):
    window.setProperty("page", 3)
    settings = window.findChild(QObject, "settingsPane")
    evaluate(settings, "showSection(0)")
    qtbot.wait(30)
    combo = item(window.contentItem(), "languageChoice")
    center = combo.mapToScene(combo.boundingRect().center()).toPoint()
    assert 0 < center.x() < window.width() and 0 < center.y() < window.height()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
    qtbot.wait(30)
    assert evaluate(combo, "popup.visible")
    index = evaluate(combo, "localization.languages.findIndex(function(language) { return language.code === " + json.dumps(code) + " })")
    evaluate(combo, f"popup.contentItem.positionViewAtIndex({index}, ListView.Contain); popup.contentItem.forceLayout()")
    row = evaluate(combo, f"popup.contentItem.itemAtIndex({index})")
    assert row is not None
    center = row.mapToScene(row.boundingRect().center()).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
    qtbot.wait(60)
    assert evaluate(combo, "currentValue") == code


def test_mouse_language_choice_retranslates_controls_and_dialogs_and_persists(language_ui, qtbot):
    workspace, window, localization, calls = language_ui
    assert localization.language == "it"
    assert evaluate(item(window.contentItem(), "modelControlsToggle"), "Accessible.name") == "Mostra opzioni del modello"
    original = (workspace.backend, workspace.selectedModel, workspace.selectedEffort,
                workspace.sessionId, copy.deepcopy(workspace._project), copy.deepcopy(workspace._runs))
    choose_language(window, "en", qtbot)
    assert localization.language == "en"
    assert item(window.contentItem(), "languageLabel").property("text") == "Interface language"
    assert item(window.contentItem(), "diagnosticsButton").property("text") == "Local diagnostics"
    assert evaluate(item(window.contentItem(), "modelControlsToggle"), "Accessible.name") == "Show model options"
    assert window.findChild(QObject, "workspaceDialog").property("title") == "Resume work"
    evaluate(window, "newProject.open()")
    qtbot.wait(30)
    assert window.findChild(QObject, "newProjectDialog").property("title") == "A new workspace"
    assert item(window.contentItem(), "projectNameInput").property("placeholderText") == "Project name"
    assert item(window.contentItem(), "createProjectButton").property("text") == "Create project"
    evaluate(window, "newProject.close()")
    assert read_preferences(workspace.paths.config)["language"] == "en"
    choose_language(window, "it", qtbot)
    assert item(window.contentItem(), "languageLabel").property("text") == "Lingua dell’interfaccia"
    assert evaluate(item(window.contentItem(), "modelControlsToggle"), "Accessible.name") == "Mostra opzioni del modello"
    assert window.findChild(QObject, "newProjectDialog").property("title") == "Un nuovo spazio di lavoro"
    assert read_preferences(workspace.paths.config)["language"] == "it"
    assert (workspace.backend, workspace.selectedModel, workspace.selectedEffort,
            workspace.sessionId, workspace._project, workspace._runs) == original
    assert not calls


def test_language_preserves_user_provider_paths_code_drafts_and_copy(language_ui, qtbot):
    workspace, window, localization, calls = language_ui
    source = "**Impostazioni**\n\n```python\nprint('Chiudi')\n```"
    workspace.transcript.append(kind="assistant", title="Kira", body=source)
    workspace.transcript.append(kind="tool", title="Salva", body="print('Modelli')\nCompletato", status="confermato")
    prompt = item(window.contentItem(), "prompt")
    prompt.setProperty("text", "Progetti · una bozza che non deve essere tradotta")
    prompt.setProperty("attachmentContextError", True)
    prompt.setProperty("attachmentError", prompt.property("attachmentContextErrorText"))
    before = copy.deepcopy(workspace.transcript.rows)
    localization.setLanguage("en")
    qtbot.wait(70)
    assert window.title() == "Impostazioni — Kira Desktop"
    assert workspace.project["root"].endswith("/Progetti")
    assert workspace.selectedModel == "Modelli"
    assert prompt.property("text") == "Progetti · una bozza che non deve essere tradotta"
    assert prompt.property("attachmentError") == "The conversation changed: choose the attachment again."
    assert item(window.contentItem(), "messageText_1").property("text") == before[1]["body"]
    assert item(window.contentItem(), "messageStatus_1").property("text") == "confirmed"
    assert workspace.transcript.rows[1]["status"] == "confermato"
    assert workspace.transcript.rows[1]["title"] == "Salva"
    QMetaObject.invokeMethod(item(window.contentItem(), "messageSource_0"), "clicked")
    qtbot.wait(30)
    assert item(window.contentItem(), "messageText_0").property("text") == source
    QMetaObject.invokeMethod(item(window.contentItem(), "messageCopy_0"), "clicked")
    assert QGuiApplication.clipboard().text() == source
    assert [row["body"] for row in workspace.transcript.rows] == [row["body"] for row in before]
    localization.setLanguage("it")
    qtbot.wait(30)
    assert item(window.contentItem(), "messageStatus_1").property("text") == "confermato"
    assert prompt.property("text") == "Progetti · una bozza che non deve essere tradotta"
    assert prompt.property("attachmentError") == "La conversazione è cambiata: scegli di nuovo l'allegato."
    assert not calls


def test_qml_catalog_covers_every_marked_source_and_preserves_placeholders():
    directory = Path(__file__).parents[1] / "src/kira/ui"
    catalog = {}
    for path in (directory / "translations/en").glob("*.json"):
        catalog.update(json.loads(path.read_text()))
    source_pattern = re.compile(r'\bqsTr\(\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')')
    sources = {ast.literal_eval(match.group(1)) for path in (directory / "qml").glob("*.qml")
               for match in source_pattern.finditer(path.read_text())}
    statuses = {"completo", "in esecuzione", "confermato", "errore", "incompleto", "esito non confermato", "streaming"}
    assert sources | statuses <= set(catalog)
    for source, translated in catalog.items():
        assert translated.strip(), source
        assert sorted(re.findall(r"%[1-9][0-9]*", source)) == sorted(re.findall(r"%[1-9][0-9]*", translated)), source


def test_lazy_question_and_team_views_retranslate_provider_text_stays_literal(language_ui, qtbot):
    workspace, window, localization, calls = language_ui
    # Loading a team page normally requests metadata when connected. This case
    # isolates translation and literal question contents from that separate action.
    workspace._connected_value = False
    localization.setLanguage("en")
    evaluate(window, "inputDialog.active = true")
    questions = workspace.inputs
    questions._rows = [{"run_id": "synthetic-run", "request_id": "synthetic-request", "status": "pending",
                        "blocking": False, "questions": [{"id": "choice", "header": "Modelli",
                        "question": "Impostazioni", "isOther": True, "options": [
                            {"label": "Chiudi", "description": "Progetti"}]}]}]
    questions._selected = questions._key(questions._rows[0])
    questions.changed.emit()
    questions.formChanged.emit()
    dialog = window.findChild(QObject, "inputDialog")
    QMetaObject.invokeMethod(dialog, "open")
    qtbot.wait(40)
    assert dialog.property("title") == "Project questions"
    assert item(window.contentItem(), "inputOption_0_0").property("text") == "Chiudi"
    assert item(window.contentItem(), "inputLater").property("text") == "Close"
    localization.setLanguage("it")
    qtbot.wait(30)
    assert dialog.property("title") == "Domande del progetto"
    assert item(window.contentItem(), "inputOption_0_0").property("text") == "Chiudi"
    QMetaObject.invokeMethod(dialog, "close")
    localization.setLanguage("en")
    window.setProperty("page", 4)
    qtbot.wait(50)
    assert item(window.contentItem(), "configureTeamButton").property("text") == "New team"
    assert window.findChild(QObject, "teamEditor").property("title") == "Configure team"
    assert not calls


def test_saved_language_is_used_by_a_new_gui_engine(language_ui, qtbot):
    workspace, _, localization, calls = language_ui
    localization.setLanguage("en")
    restored = Workspace(workspace.paths)
    engine = create_engine(restored)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        qtbot.wait(40)
        assert engine.rootContext().contextProperty("localization").language == "en"
        assert item(window.contentItem(), "languageLabel").property("text") == "Interface language"
        assert item(window.contentItem(), "languageChoice").property("displayText") == "English"
        assert not restored.paths.db.exists()
        assert not calls
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        restored.close()
        engine.deleteLater()
        restored.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
