"""Provisional provider selection and durable same-chat acknowledgements."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.drafts import DraftStore


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "project"}
    workspace._adopt_session({"id": "chat", "project_id": "project", "title": "Original title",
        "backend": "codex", "model": "explicit-codex", "provider_generation": 0,
        "settings": {"reasoning_effort": "max", "tools": True}})
    workspace._sessions = [deepcopy(workspace._session)]
    workspace.directAccount._active_id = "account"
    workspace.directAccount._accounts = [{"id": "account", "label": "Test account", "connected": True,
                                          "plan_enabled": True}]
    workspace.setDraftText("Literal unfinished draft")
    workspace.transcript.append(kind="assistant", body="Preserved transcript")
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: calls.append(args))
    yield workspace, calls
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def prepare(view, *, previous=False):
    workspace, calls = view
    workspace.setBackend("openai_chatgpt")
    assert calls[-1][0] == "session.providers"
    calls[-1][2]({"providers": [{"backend": "openai_chatgpt", "model": "vision-model" if previous else "",
        "reasoning_effort": "high" if previous else "", "account_id": "account"}]})
    assert calls[-1][:2] == ("backend.discover", {"backend": "openai_chatgpt", "account_id": "account"})
    calls[-1][2]({"available": True, "account": {"id": "account"}, "models": [
        {"id": "vision-model", "name": "Vision model", "supported_reasoning_efforts": ["high", "low"]}]})
    return workspace.providerSwitch


def test_provider_switch_is_provisional_and_keeps_chat_until_ack(view):
    workspace, calls = view
    rows = deepcopy(workspace.transcript.rows)
    switch = prepare(view)
    assert not switch.canApply and switch.model == ""
    assert workspace.backend == "codex" and workspace.selectedModel == "explicit-codex"
    switch.setModel("vision-model")
    switch.setEffort("high")
    assert switch.canApply
    switch.apply()
    submitted = calls[-1]
    assert submitted[0] == "session.switch"
    assert DraftStore(workspace.paths.config).pending[0]["request_id"] == submitted[1]["request_id"]
    assert workspace.busy and not workspace.workbench.composerReady
    assert workspace.sessionId == "chat" and workspace.sessionTitle == "Original title"
    updated = {**workspace._session, "backend": "openai_chatgpt", "model": "vision-model",
               "provider_generation": 1, "settings": {"reasoning_effort": "high", "openai_account_id": "account"}}
    submitted[2]({"session": updated})
    assert workspace.sessionId == "chat" and workspace.backend == "openai_chatgpt"
    assert workspace.selectedEffort == "high" and workspace.draftText == "Literal unfinished draft"
    assert workspace.transcript.rows == rows and not workspace._pending_intents
    assert not any(call[0] in {"session.create", "run.start"} for call in calls)


def test_saved_target_choices_are_restored_without_substitution_and_cancel_is_harmless(view):
    workspace, calls = view
    switch = prepare(view, previous=True)
    assert switch.model == "vision-model" and switch.effort == "high" and switch.canApply
    switch.setEffort("unsupported")
    switch.setModel("unavailable-model")
    assert not switch.canApply
    switch.cancel()
    assert workspace.backend == "codex" and workspace.sessionId == "chat"
    assert not any(call[0] == "session.switch" for call in calls)


def test_lost_ack_reuses_request_after_restart_and_does_not_drop_draft(view):
    workspace, calls = view
    switch = prepare(view, previous=True)
    switch.apply()
    first = calls[-1]
    first[3]({"code": "DISCONNECTED", "message": "Synthetic disconnect"})
    assert workspace.backend == "codex" and workspace.sessionId == "chat" and not workspace.busy
    assert DraftStore(workspace.paths.config).pending[0]["state"] == "unknown"
    switch.apply()
    assert calls[-1][1] == first[1]
    calls[-1][3]({"code": "INVALID", "message": "Unavailable model"})
    assert workspace.draftText == "Literal unfinished draft" and not workspace._pending_intents
    assert switch.error == "Unavailable model"


def test_late_switch_response_cannot_overwrite_another_chat(view):
    workspace, calls = view
    switch = prepare(view, previous=True)
    switch.apply()
    pending = calls[-1]
    original = deepcopy(workspace._session)
    workspace._session = {**original, "id": "other"}
    workspace._reset_chat()
    pending[2]({"session": {**original, "backend": "openai_chatgpt"}})
    assert workspace.sessionId == "other" and workspace.backend == "codex"
    assert not workspace._pending_intents


def test_provider_intent_is_not_sent_when_identity_cannot_be_saved(view):
    workspace, calls = view
    switch = prepare(view, previous=True)
    workspace._draft_store.blocked = True
    switch.apply()
    assert not any(call[0] == "session.switch" for call in calls)
    assert workspace.backend == "codex" and not switch.applying


def test_native_operation_identity_changes_with_provider_generation(view):
    workspace, _ = view
    first = workspace.workbench._intent("session.compact", {"session_id": "chat"})
    workspace._session["provider_generation"] = 2
    second = workspace.workbench._intent("session.compact", {"session_id": "chat"})
    assert first["request_id"] != second["request_id"]


def test_real_provider_dialog_keeps_current_selector_and_localizes_at_minimum_size(view, qtbot, qtlog):
    from PySide6.QtCore import QObject
    from kira.ui.app import create_engine

    workspace, calls = view
    workspace._preferences["onboarding_complete"] = True
    workspace._preferences["model_controls_visible"] = True
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        window.resize(880, 620)
        qtbot.wait(40)
        switch = prepare(view, previous=True)
        qtbot.wait(40)
        dialog = window.findChild(QObject, "providerSwitchDialog")
        assert dialog.property("visible") and dialog.property("width") <= 840
        assert window.findChild(QObject, "backendBox").property("currentIndex") == 1
        assert window.findChild(QObject, "switchProviderModel").property("currentValue") == "vision-model"
        assert window.findChild(QObject, "switchProviderApply").property("enabled")
        engine.rootContext().contextProperty("localization").setLanguage("en")
        qtbot.wait(30)
        assert dialog.property("title") == "Continue this chat with another provider"
        assert window.findChild(QObject, "switchProviderApply").property("text") == "Continue in this chat"
        assert workspace.backend == "codex" and switch.model == "vision-model"
        assert not any("ReferenceError" in r.message or "binding loop" in r.message.lower() for r in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_direct_provider_can_reopen_account_choice_without_new_chat(view):
    workspace, calls = view
    workspace._backend = "openai_chatgpt"
    workspace._session["backend"] = "openai_chatgpt"
    workspace.setBackend("openai_chatgpt")
    assert calls[-1][0] == "session.providers"
    assert workspace.sessionId == "chat" and workspace.draftText == "Literal unfinished draft"


def models_window(view):
    from kira.ui.app import create_engine

    workspace, _ = view
    workspace._preferences["onboarding_complete"] = True
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.resize(1200, 800)
    window.setProperty("page", 1)
    return engine, window


def close_window(engine, window):
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def visual(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if (found := visual(child, name)) is not None:
            return found
    return None


def click(window, name):
    from PySide6.QtCore import QMetaObject, QObject

    button = window.findChild(QObject, name) or visual(window.contentItem(), name)
    assert button is not None and button.property("visible"), name
    QMetaObject.invokeMethod(button, "clicked")


def test_models_page_shows_another_provider_without_switching_the_chat(view, qtbot, qtlog):
    from PySide6.QtCore import QObject

    workspace, calls = view
    engine, window = models_window(view)
    try:
        qtbot.wait(40)
        click(window, "deepseekWebBackendButton")
        qtbot.wait(40)
        # The setup pane is reachable and the chat keeps its provider: no switch dialog.
        assert window.findChild(QObject, "deepseekWebPane").property("visible")
        assert not window.findChild(QObject, "providerSwitchDialog").property("visible")
        assert workspace.backend == "codex"
        discover = [call for call in calls if call[:2] == ("backend.discover", {"backend": "deepseek_web"})]
        discover[-1][2]({"available": True, "models": [{"id": "deepseek-reasoner", "name": "DeepSeek Reasoner"}]})
        qtbot.wait(40)
        assert visual(window.contentItem(), "modelCard_deepseek-reasoner") is not None
        assert "Codex" in window.findChild(QObject, "providerStatusText").property("text")
        # "Usa modello" continues the chat with that model, after the usual confirmation.
        click(window, "useModel_deepseek-reasoner")
        qtbot.wait(40)
        assert window.findChild(QObject, "providerSwitchDialog").property("visible")
        calls[-1][2]({"providers": [{"backend": "deepseek_web", "model": "", "reasoning_effort": ""}]})
        calls[-1][2]({"available": True, "models": [{"id": "deepseek-reasoner", "name": "DeepSeek Reasoner"}]})
        qtbot.wait(40)
        assert workspace.providerSwitch.model == "deepseek-reasoner" and workspace.providerSwitch.canApply
        assert workspace.backend == "codex"
        assert not any("ReferenceError" in r.message or "TypeError" in r.message for r in qtlog.records)
    finally:
        close_window(engine, window)


def test_switch_dialog_leads_to_the_setup_of_a_provider_without_access(view, qtbot):
    from PySide6.QtCore import QObject

    workspace, calls = view
    engine, window = models_window(view)
    try:
        window.setProperty("page", 0)
        workspace.setBackend("deepseek_web")
        calls[-1][2]({"providers": []})
        calls[-1][2]({"available": False, "models": [], "error": "No DeepSeek web token configured"})
        qtbot.wait(40)
        dialog = window.findChild(QObject, "providerSwitchDialog")
        assert dialog.property("visible")
        click(window, "switchProviderConfigure")
        qtbot.waitUntil(lambda: not dialog.property("visible"))
        assert window.property("page") == 1 and workspace.backend == "codex"
        assert window.findChild(QObject, "deepseekWebPane").property("visible")
    finally:
        close_window(engine, window)
