"""Panel and dialog review fixes in real offscreen QML with synthetic data only."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject

import kira.ui.i18n as i18n
from test_ui_qml_fixes_main import STARTED, click, evaluate, item, local_time, named, requests, ui, use_backend

__all__ = ["ui"]


def visual_items(root):
    yield root
    for child in root.childItems():
        yield from visual_items(child)


def test_deeperseeker_can_be_switched_off_and_the_choice_survives_refreshes(ui, qtbot):
    workspace, window, calls, _ = ui
    account = workspace.deepseekAccount
    account._proxy_enabled, account._proxy_configured = True, True
    account._proxy_url = "http://127.0.0.1:4000"
    use_backend(workspace, "deepseek_account", "account-model")
    account.changed.emit()
    window.setProperty("page", 1)
    qtbot.wait(40)
    proxy, disable = named(window, "deepseekUseProxy"), named(window, "deepseekProxyDisable")
    login = named(window, "deepseekAccountLogin")
    assert proxy.property("checked") and not disable.isVisible() and not login.isVisible()
    click(window, proxy)
    qtbot.wait(20)
    account.changed.emit()  # an unrelated status refresh keeps the unsaved choice
    qtbot.wait(20)
    assert not proxy.property("checked") and disable.isVisible()
    calls.clear()
    click(window, disable)
    request = requests(calls, "backend.account")[-1]
    assert request[1] == {"backend": "deepseek_account", "action": "configure_proxy",
                          "url": "http://127.0.0.1:4000", "api_key": "", "enabled": False}
    request[2]({"connected": False, "mode": "account", "url": "http://127.0.0.1:4000"})
    qtbot.wait(30)
    assert not account.proxyEnabled
    assert not disable.isVisible() and login.isVisible() and not proxy.property("checked")
    # Once saved, the checkbox follows the stored mode again.
    account._proxy_enabled = True
    account.changed.emit()
    qtbot.wait(20)
    assert proxy.property("checked") and not disable.isVisible()


def test_refused_language_keeps_the_selector_on_the_active_language(ui, qtbot, monkeypatch):
    workspace, window, calls, localization = ui
    original = i18n._native_translator

    def refuse(code, parent, *, strict=False):
        if strict:
            raise RuntimeError("synthetic catalog failure")
        return original(code, parent, strict=strict)

    monkeypatch.setattr(i18n, "_native_translator", refuse)
    window.setProperty("page", 3)
    evaluate(named(window, "settingsPane"), "showSection(0)")
    qtbot.wait(30)
    combo = named(window, "languageChoice")
    english = evaluate(combo, 'indexOfValue("en")')
    combo.setProperty("currentIndex", english)
    assert QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, english))
    qtbot.wait(30)
    assert localization.language == "it"
    assert combo.property("currentValue") == "it" and combo.property("displayText") == "Italiano"
    assert workspace.notice == "Impossibile caricare la lingua selezionata."


def test_team_views_name_the_deepseek_account_and_count_singulars(ui, qtbot):
    workspace, window, calls, _ = ui
    teams = workspace.teams
    window.setProperty("page", 4)
    qtbot.wait(80)
    teams._team = {"id": "team", "state": "running", "backend": "deepseek_account", "model": "account-model",
                   "settings": {}, "concurrency": 2, "goal": "Synthetic goal",
                   "steps": [{"id": "one", "state": "completed", "title": "First"},
                             {"id": "two", "state": "running", "title": "Second"}]}
    teams.changed.emit()
    qtbot.wait(40)
    configuration = named(window, "teamConfigurationText").property("text")
    assert configuration.startswith("DeepSeek Account · account-model · ")
    assert named(window, "teamStepsHeading").property("text") == "INCARICHI · 1 completato su 2"
    teams._team["steps"][1]["state"] = "completed"
    teams.changed.emit()
    assert named(window, "teamStepsHeading").property("text") == "INCARICHI · 2 completati su 2"
    editor = window.findChild(QObject, "teamBackendInput")
    assert editor is not None and evaluate(editor, "textAt(3)") == "DeepSeek Account"
    window.setProperty("page", 0)
    assistant = workspace.assistant
    assistant._delegations = [{"id": "delegation", "state": "running", "goal": "Synthetic goal",
                               "members_count": 1, "completed_steps": 0, "total_steps": 2}]
    assistant.changed.emit()
    qtbot.wait(30)
    progress = named(window, "assistantTeamProgress")
    assert progress.property("text") == "1 membro · 0/2 incarichi completati"
    assistant._delegations[0]["members_count"] = 3
    assistant.changed.emit()
    assert progress.property("text") == "3 membri · 0/2 incarichi completati"


def test_service_times_in_dialogs_are_local_and_follow_the_language(ui, qtbot):
    workspace, window, calls, localization = ui
    workspace._prompt_history = [{"project_id": "p", "session_id": "chat", "text": "Earlier request",
                                  "timestamp": STARTED}]
    workspace.workbench.historyChanged.emit()
    evaluate(window, 'workbenchDialog.openSection("history")')
    # Opening the section refreshes the list, which recreates its rows.
    def history_time():
        row = item(window.contentItem(), "promptHistoryTime")
        return row.property("text") if row is not None else None

    qtbot.waitUntil(lambda: history_time() == local_time("it", STARTED))
    evaluate(window, "workbenchDialog.close()")
    workspace._runs = {"active": {"id": "active", "project_id": "p", "session_id": "chat", "state": "running",
                                  "created_at": STARTED}}
    workspace.changed.emit()
    qtbot.wait(30)
    assert named(window, "activityIndicator").property("startedAt") == STARTED
    # Evaluated inside the component, where the tooltip's id lives.
    assert evaluate(named(window, "activityElapsed"), "elapsedTip.text").endswith("\n" + local_time("it", STARTED))
    localization.setLanguage("en")
    qtbot.wait(60)
    expected = "Time elapsed since the request started\n" + local_time("en", STARTED)
    assert evaluate(named(window, "activityElapsed"), "elapsedTip.text") == expected


@pytest.mark.parametrize("language", ["it", "en"])
def test_onboarding_and_welcome_copy_name_no_single_provider_pair(ui, qtbot, language):
    workspace, window, calls, localization = ui
    localization.setLanguage(language)
    qtbot.wait(60)
    texts = [obj.property("text") or "" for obj in named(window, "onboardingDialog").findChildren(QObject)
             if obj.property("text")]
    copy = " ".join(str(text) for text in texts)
    for stale in ("Codex e Ollama", "Codex and Ollama", "flusso ufficiale Codex", "official Codex flow"):
        assert stale not in copy
    research = window.findChild(QObject, "researchNew")
    assert research.property("text") == ("Nuova scheda di ricerca" if language == "it" else "New record")
    tagline = "Modelli locali o in cloud  ·  Dati sul tuo computer" if language == "it" \
        else "Local or cloud models  ·  Data on your computer"
    labels = [obj.property("text") for obj in visual_items(window.contentItem())
              if isinstance(obj.property("text"), str)]
    assert tagline in labels and not any("Codex App Server" in label for label in labels)
