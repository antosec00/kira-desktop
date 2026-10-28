"""Main window review fixes, exercised in real offscreen QML with synthetic data only."""

import os
from contextlib import contextmanager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import (QCoreApplication, QDateTime, QEvent, QLocale, QMetaObject, QObject, QPoint,
                            QPointF, Qt)
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace

STARTED = "2026-10-23T10:15:30.123+00:00"


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def named(window, name):
    found = item(window.contentItem(), name) or window.findChild(QObject, name)
    assert found is not None, name
    return found


def evaluate(obj, script):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, script)
    result, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


def click(window, obj):
    assert obj.isVisible() and obj.isEnabled(), obj.objectName()
    center = obj.mapToScene(obj.boundingRect().center())
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(center.x()), round(center.y())))


def requests(calls, method):
    return [call for call in calls if call[0] == method]


def local_time(language, value):
    moment = QDateTime.fromString(value, Qt.DateFormat.ISODateWithMs).toLocalTime()
    return QLocale(language).toString(moment, "d MMM yyyy, HH:mm")


@contextmanager
def main_window(qtbot, qtlog, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "p", "name": "Synthetic project", "root": str(tmp_path / "p"),
                          "permission": "project_write", "memory": {}}
    workspace._projects = [workspace._project]
    workspace._session = {"id": "chat", "project_id": "p", "backend": "codex", "model": "explicit-model",
                          "title": "Synthetic chat", "settings": {"reasoning_effort": "ultra"}}
    workspace._sessions = [workspace._session.copy()]
    workspace._backend, workspace._model, workspace._effort = "codex", "explicit-model", "ultra"
    workspace._catalogs = {
        "codex": {"available": True, "capabilities": {"native_compaction": True, "steering": True},
                  "models": [{"id": "explicit-model", "name": "Explicit model",
                              "supported_reasoning_efforts": ["ultra"]}]},
        "ollama": {"available": True, "models": [
            {"id": "local-model", "name": "Local model", "size_bytes": int(2.5 * 1073741824),
             "tools_verified": True, "capabilities": {"tools": True}},
            {"id": "plain-model", "name": "Plain model"}]},
        "deepseek_web": {"available": True, "models": [{"id": "web-model", "name": "Web model"}]},
        "deepseek_account": {"available": True, "models": [{"id": "account-model", "name": "Account model"}]},
    }
    workspace._preferences["onboarding_complete"] = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args) or True)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
    qtbot.wait(60)
    localization = engine.rootContext().contextProperty("localization")
    yield workspace, window, calls, localization
    problems = [record.message for record in qtlog.records
                if "ReferenceError" in record.message or "TypeError" in record.message
                or "binding loop" in record.message.lower()]
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not problems, "\n".join(problems[:10])


@pytest.fixture
def ui(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    with main_window(qtbot, qtlog, tmp_path, monkeypatch) as opened:
        yield opened


def use_backend(workspace, backend, model):
    workspace._backend, workspace._model, workspace._effort = backend, model, ""
    workspace._session.update(backend=backend, model=model, settings={})
    workspace._sessions = [workspace._session.copy()]
    workspace.changed.emit()


def test_onboarding_models_button_only_navigates(ui, qtbot):
    workspace, window, calls, _ = ui
    use_backend(workspace, "ollama", "local-model")
    calls.clear()
    evaluate(window, "onboardingDialog.open()")
    qtbot.waitUntil(lambda: bool(named(window, "onboardingDialog").property("opened")))
    # The guide scrolls; the button's own signal runs the same handler as a click.
    assert QMetaObject.invokeMethod(named(window, "onboardingModelsButton"), "clicked")
    qtbot.wait(40)
    assert window.property("page") == 1
    assert (workspace.backend, workspace.selectedModel, workspace.sessionId) == ("ollama", "local-model", "chat")
    assert not named(window, "providerSwitchDialog").property("visible")
    assert not requests(calls, "backend.discover")


@pytest.mark.parametrize("backend,model,expected", [
    ("deepseek_account", "account-model", "context"),
    ("ollama", "local-model", "note"),
    ("codex", "explicit-model", "confirmation"),
])
def test_compact_action_never_confirms_a_compaction_that_cannot_start(ui, qtbot, backend, model, expected):
    workspace, window, calls, _ = ui
    use_backend(workspace, backend, model)
    evaluate(window, 'performAction("compact")')
    qtbot.wait(60)
    confirmation = named(window, "compactContextDialog")
    workbench = named(window, "workbenchDialog")
    assert bool(confirmation.property("visible")) is (expected == "confirmation")
    if expected == "context":
        assert evaluate(window, "chatContextDialog.visible")
        assert not workbench.property("visible")
    else:
        assert workbench.property("visible") and workbench.property("section") == "chat"
        assert named(window, "compactUnavailableNote").isVisible() is (expected == "note")
        assert named(window, "compactContextButton").isEnabled() is (expected == "confirmation")
    assert not requests(calls, "session.compact")


def test_busy_codex_chat_shows_the_disabled_command_instead_of_a_confirmation(ui, qtbot):
    workspace, window, calls, _ = ui
    workspace._runs = {"busy": {"id": "busy", "session_id": "chat", "state": "running"}}
    workspace.changed.emit()
    evaluate(window, 'performAction("compact")')
    qtbot.wait(40)
    assert not named(window, "compactContextDialog").property("visible")
    assert named(window, "workbenchDialog").property("section") == "chat"


def test_direct_chat_tools_open_the_context_panel_for_every_direct_provider(ui, qtbot):
    workspace, window, calls, _ = ui
    use_backend(workspace, "deepseek_account", "account-model")
    evaluate(window, 'workbenchDialog.openSection("chat")')
    qtbot.wait(40)
    button = named(window, "compactContextButton")
    assert button.property("text") == "Contesto e memoria"
    assert not named(window, "compactUnavailableNote").isVisible()
    click(window, button)
    qtbot.wait(60)
    assert evaluate(window, "chatContextDialog.visible")
    assert not named(window, "compactContextDialog").property("visible")


def test_provider_identifiers_are_shown_with_selector_labels(ui, qtbot):
    from datetime import UTC, datetime, timedelta

    workspace, window, calls, localization = ui
    five_hours_ago = (datetime.now(UTC) - timedelta(hours=5, minutes=2)).isoformat()
    workspace._sessions = [
        workspace._session.copy(),
        {"id": "web", "project_id": "p", "backend": "openai_chatgpt",
         "model": "gpt-synthetic", "title": "Direct chat"},
        {"id": "ds", "project_id": "p", "backend": "deepseek_web",
         "model": "deepseek-reasoner", "title": "Test domande", "updated_at": five_hours_ago},
    ]
    workspace.changed.emit()
    qtbot.wait(40)
    tooltip = named(window, "chatSelect_web").property("toolTipText")
    assert tooltip == "Direct chat · OpenAI diretto · ChatGPT · gpt-synthetic"
    assert "openai_chatgpt" not in tooltip
    assert named(window, "chatSelect_ds").property("toolTipText") == "Test domande · DeepSeek Web"
    assert named(window, "chatAge_ds").property("text") == "5h"
    assert evaluate(window, 'kformat.providerLabel("deepseek_account")') == "DeepSeek Account"
    assert evaluate(window, 'kformat.providerLabel("future_provider")') == "future_provider"
    localization.setLanguage("en")
    qtbot.wait(60)
    assert named(window, "chatSelect_web").property("toolTipText") == "Direct chat · Direct OpenAI · ChatGPT · gpt-synthetic"
    assert named(window, "chatSelect_ds").property("toolTipText") == "Test domande · DeepSeek Web"


def test_reasoning_level_follows_one_rule_in_composer_and_provider_switch(ui, qtbot):
    workspace, window, calls, _ = ui
    effort, summary = named(window, "effortBox"), named(window, "modelControlsSummary")
    switch = workspace.providerSwitch
    # Popup content is only effectively visible while its popup is open.
    evaluate(window, "modelPopover.open(); providerSwitchDialog.open()")
    qtbot.wait(60)
    for backend, model, shown in (("codex", "explicit-model", True), ("ollama", "local-model", False),
                                  ("deepseek_web", "web-model", False), ("deepseek_account", "account-model", True)):
        use_backend(workspace, backend, model)
        switch._backend = backend
        switch.changed.emit()
        qtbot.wait(20)
        assert evaluate(effort, "visible") is shown, backend
        assert ("Predefinito del provider" in summary.property("text")) is shown, backend
        assert evaluate(named(window, "switchProviderEffort"), "visible") is shown, backend


def test_model_cards_show_size_for_local_models_in_the_interface_language(ui, qtbot):
    workspace, window, calls, localization = ui
    window.setProperty("page", 1)
    qtbot.wait(40)
    assert not named(window, "modelSize_explicit-model").isVisible()
    use_backend(workspace, "ollama", "local-model")
    qtbot.wait(40)
    assert named(window, "modelSize_local-model").property("text") == "Dimensione modello: 2,50 GiB"
    assert named(window, "modelSize_plain-model").isVisible()
    localization.setLanguage("en")
    qtbot.wait(60)
    assert named(window, "modelSize_local-model").property("text") == "Model size: 2.50 GiB"
    use_backend(workspace, "deepseek_web", "web-model")
    qtbot.wait(40)
    assert not named(window, "modelSize_web-model").isVisible()


def test_slash_is_consumed_only_when_an_action_is_chosen(ui, qtbot):
    workspace, window, calls, _ = ui
    prompt = named(window, "prompt")
    palette = named(window, "actionPalette")
    evaluate(prompt, "forceActiveFocus()")
    QTest.keyClick(window, Qt.Key.Key_Slash)
    qtbot.waitUntil(lambda: bool(palette.property("opened")))
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not palette.property("visible"))
    assert window.property("commandStart") == -1
    workspace.workbench.invokeAction.emit("chat")
    qtbot.wait(30)
    assert prompt.property("text") == "/"
    prompt.setProperty("text", "")
    evaluate(prompt, "forceActiveFocus()")
    QTest.keyClick(window, Qt.Key.Key_Slash)
    qtbot.waitUntil(lambda: bool(palette.property("opened")))
    named(window, "quickActionSearch").setProperty("text", "/plan")
    evaluate(palette, "activate()")
    qtbot.wait(30)
    assert workspace.workbench.mode == "plan" and prompt.property("text") == ""
    prompt.setProperty("text", "Keep /")
    window.setProperty("commandStart", 5)
    workspace.chatChanged.emit()
    assert window.property("commandStart") == -1 and window.property("mentionStart") == -1


def test_keyboard_shortcuts_reach_the_chat_and_keypad_enter_sends(ui, qtbot):
    workspace, window, calls, _ = ui
    workspace._runs = {"busy": {"id": "busy", "session_id": "chat", "state": "running"}}
    workspace.changed.emit()
    prompt = named(window, "prompt")
    prompt.setProperty("text", "Guidance from the keypad")
    evaluate(prompt, "forceActiveFocus()")
    QTest.keyClick(window, Qt.Key.Key_Enter, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier)
    assert requests(calls, "run.steer")[-1][1]["prompt"] == "Guidance from the keypad"
    workspace._runs = {}
    workspace.changed.emit()
    window.setProperty("page", 3)
    QTest.keyClick(window, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(30)
    assert window.property("page") == 0 and prompt.property("activeFocus")
    window.setProperty("page", 3)
    QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    qtbot.wait(30)
    assert window.property("page") == 0 and workspace.sessionId == ""


def test_rename_dialog_focuses_the_name_and_accepts_with_enter(ui, qtbot):
    workspace, window, calls, _ = ui
    click(window, named(window, "chatRename_chat"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "renameChatDialog.opened")))
    field = named(window, "chatNameInput")
    assert field.property("activeFocus") and field.property("selectedText") == "Synthetic chat"
    field.setProperty("text", "Renamed with the keyboard")
    QTest.keyClick(window, Qt.Key.Key_Return)
    rename = requests(calls, "session.rename")[-1]
    assert rename[1] == {"project_id": "p", "session_id": "chat", "title": "Renamed with the keyboard"}
    qtbot.waitUntil(lambda: not evaluate(window, "renameChatDialog.visible"))


def test_settings_shortcut_list_matches_the_guide(ui, qtbot):
    workspace, window, calls, _ = ui
    window.setProperty("page", 3)
    evaluate(named(window, "settingsPane"), "showSection(3)")
    qtbot.wait(30)
    shortcuts = named(window, "settingsShortcuts").property("text")
    for keys in ("Ctrl+Invio", "Ctrl+B", "Ctrl+K", "Ctrl+N", "Ctrl+L", "Ctrl+Shift+N", "Ctrl+Shift+P",
                 "Ctrl+Shift+R", "Ctrl+Shift+Q", "Ctrl+Shift+C"):
        assert keys + " · " in shortcuts


def test_symbol_buttons_and_queue_counts_have_spoken_names(ui, qtbot):
    workspace, window, calls, _ = ui
    workspace._problem("Synthetic notice")
    qtbot.wait(30)
    dismiss = named(window, "dismissNoticeButton")
    assert evaluate(dismiss, "Accessible.name") == "Chiudi avviso"
    assert dismiss.property("toolTipText") == "Chiudi avviso"
    names = {"studioExpand": "Espandi Studio", "studioHide": "Nascondi Studio senza eliminare il lavoro",
             "studioMoveLeft": "Sposta il testo a sinistra", "studioMoveRight": "Sposta il testo a destra",
             "studioMoveUp": "Sposta il testo in alto", "studioMoveDown": "Sposta il testo in basso"}
    for name, spoken in names.items():
        assert evaluate(named(window, name), "Accessible.name") == spoken
    assert evaluate(named(window, "studioError"), "Number(textFormat)") == 0
    assert evaluate(named(window, "studioExportPath"), "Number(textFormat)") == 0
    queue = named(window, "projectQueueButton")
    workspace.workbench._queue = [{"id": "a", "status": "pending", "session_id": "chat"}]
    workspace.workbench.changed.emit()
    assert evaluate(queue, "Accessible.name") == "Coda del progetto: 1 richiesta"
    workspace.workbench._queue.append({"id": "b", "status": "pending", "session_id": "chat"})
    workspace.workbench.changed.emit()
    assert evaluate(queue, "Accessible.name") == "Coda del progetto: 2 richieste"
    assert evaluate(named(window, "backendBox"), "Accessible.name") == "Provider"
    assert named(window, "approvalDialog").property("title") == "Decisione richiesta"


def test_activity_times_use_the_interface_language_and_local_time(ui, qtbot):
    workspace, window, calls, localization = ui
    workspace._runs = {"run-one": {"id": "run-one", "project_id": "p", "session_id": "chat", "state": "completed",
                                   "created_at": STARTED, "updated_at": STARTED}}
    workspace.changed.emit()
    window.setProperty("page", 2)
    qtbot.wait(60)
    label = named(window, "activityStarted_run-one")
    assert label.property("text") == local_time("it", STARTED)
    assert "T10:15" not in label.property("text")
    assert evaluate(window, 'kformat.timestamp("not a timestamp")') == "not a timestamp"
    assert evaluate(window, 'kformat.timestamp("")') == ""
    localization.setLanguage("en")
    qtbot.wait(60)
    # Retranslation refreshes the run list, so look the row up again.
    assert named(window, "activityStarted_run-one").property("text") == local_time("en", STARTED)


def test_ollama_tools_switch_is_locked_in_a_saved_chat(ui, qtbot):
    workspace, window, calls, _ = ui
    use_backend(workspace, "ollama", "local-model")
    evaluate(window, "modelPopover.open()")
    qtbot.wait(60)
    tools = named(window, "ollamaToolsChoice")
    assert tools.isVisible() and not tools.isEnabled()
    assert tools.property("hint") == "Gli strumenti si scelgono per le nuove chat: apri una nuova chat per cambiarli."
    center = tools.mapToScene(QPointF(tools.width() / 2, tools.height() / 2)).toPoint()
    QTest.mouseMove(window, center)
    qtbot.wait(40)
    QTest.mouseMove(window, center + QPoint(1, 0))
    qtbot.waitUntil(lambda: any(tip.property("visible") and tip.property("text") == tools.property("hint")
                                for tip in window.findChildren(QObject, "literalToolTip")))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    qtbot.wait(30)
    assert workspace.sessionId == "chat"
    workspace.newChat()
    qtbot.wait(30)
    assert tools.isEnabled()


def test_header_export_saves_the_chat_as_markdown_and_memory_as_json(ui, qtbot):
    workspace, window, calls, _ = ui
    dialog = named(window, "projectExportDialog")
    assert dialog.property("title") == "Esporta chat"
    assert list(dialog.property("nameFilters")) == ["Markdown (*.md)"]
    assert dialog.property("defaultSuffix") == "md"
    window.setProperty("memoryExport", True)
    assert dialog.property("title") == "Esporta memoria"
    assert list(dialog.property("nameFilters"))[0] == "JSON (*.json)"
    assert dialog.property("defaultSuffix") == "json"
    assert named(window, "projectExportButton").property("toolTipText") == "Esporta chat in Markdown"
