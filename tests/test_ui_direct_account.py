"""Synthetic account IPC and real Qt presentation; no login or inference is performed."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlEngine, QQmlExpression

from kira.domain.paths import Paths
from kira.ui import external_links
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def status(state="idle", active="account-a", **extra):
    return {"accounts": [{"id": "account-a", "label": "First account", "connected": True,
                           "plan_enabled": True},
                          {"id": "account-b", "label": "Second account", "connected": True,
                           "plan_enabled": False}],
            "active_account_id": active, "login": {"state": state}, **extra}


@pytest.fixture
def direct(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "project", "root": str(tmp_path), "name": "Synthetic project",
                          "permission": "read_only"}
    requests, opened = [], []
    def request(*args, **kwargs):
        if args[0] == "goal.get":
            # The independently tested header read does not participate in this
            # feature's command queue. Its request must still match this chat.
            assert args[1] == {"project_id": workspace.project["id"],
                               "session_id": workspace.sessionId}
            args[2]({"goal": None})
            return True
        return requests.append(args)

    monkeypatch.setattr(workspace.client, "request", request)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    # Synthetic Qt callback, not a real WSL->Windows browser launch.
    monkeypatch.setattr(external_links, "_is_wsl", lambda: False)
    workspace.changed.emit()
    yield workspace, workspace.directAccount, requests, opened
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_login_browser_polling_completion_preserves_chat_and_model(direct):
    workspace, account, requests, opened = direct
    workspace._backend, workspace._model, workspace._effort = "codex", "configured-model", "high"
    workspace._session = {"id": "codex-chat", "backend": "codex", "backend_ref": "native-thread",
                          "settings": {"reasoning_effort": "high"}}
    original = copy.deepcopy(workspace._session)
    account.login("")
    assert requests[-1][:2] == ("backend.login", {"backend": "openai_chatgpt"})
    assert not opened and account.busy and not account.editable
    url = "http://127.0.0.1:1455/auth/start/abcdefghijklmnop"
    requests[-1][2](status("awaiting_browser", authorization_url=url))
    assert opened == [url] and account.pending and account._timer.isActive()
    assert url not in str(vars(account))
    account.refresh()
    assert requests[-1][:2] == ("backend.account", {"backend": "openai_chatgpt", "action": "status"})
    previous = len(requests)
    account.refresh()
    assert len(requests) == previous, "Only one status request may be in flight"
    requests[-1][2](status("completed"))
    assert not account.pending and not account._timer.isActive()
    assert requests[-1][:2] == ("backend.discover", {"backend": "openai_chatgpt", "account_id": "account-a"})
    assert workspace._session == original
    assert (workspace.backend, workspace.selectedModel, workspace.selectedEffort) == ("codex", "configured-model", "high")
    assert not any(row[0] in {"session.create", "session.configure", "run.start"} for row in requests)


@pytest.mark.parametrize("url", ["https://example.test/authorize", "http://auth.openai.com/authorize",
    "http://localhost:1455/auth/start/abcdefghijklmnop", "http://127.0.0.1:1455/other/abcdefghijklmnop",
    "http://127.0.0.1:1455/auth/start/abcdefghijklmnop?unexpected=value",
    "https://user@auth.openai.com/authorize", "file:///tmp/login"])
def test_login_never_opens_untrusted_urls(direct, url):
    _, account, requests, opened = direct
    account.login("")
    requests[-1][2](status("awaiting_browser", authorization_url=url))
    assert not opened and account.error


def test_cancel_invalidates_delayed_status_and_stops_polling(direct):
    _, account, requests, _ = direct
    account._apply(status("awaiting_browser"))
    account.refresh()
    delayed_status = requests[-1][2]
    account.cancel()
    assert requests[-1][:2] == ("backend.account", {"backend": "openai_chatgpt", "action": "cancel"})
    delayed_status(status("awaiting_browser"))
    requests[-1][2](status("cancelled"))
    assert not account.pending and not account._timer.isActive()
    assert account.editable


@pytest.mark.parametrize("source", ["run", "team", "remote", "disconnected"])
def test_account_mutations_blocked_by_active_work(direct, source):
    workspace, account, requests, opened = direct
    account._apply(status())
    if source == "run":
        workspace._runs = {"run": {"id": "run", "state": "running"}}
    elif source == "team":
        workspace.teams._teams = [{"id": "team", "state": "running"}]
    elif source == "remote":
        account._apply(status(mutation_allowed=False, busy_reason="Other project is active"))
    else:
        workspace._connected_value = False
    assert not account.editable
    account.login("")
    account.select("account-b")
    account.logout("account-a")
    assert not requests and not opened


def test_select_account_preserves_bound_chat_label_and_no_automatic_login(direct):
    workspace, account, requests, opened = direct
    workspace._backend = "openai_chatgpt"
    workspace._session = {"id": "bound-chat", "backend": "openai_chatgpt",
                          "settings": {"openai_account_id": "account-a"}}
    account._apply(status())
    assert workspace.accountLabel == "First account"
    account.select("account-b")
    assert requests[-1][:2] == ("backend.account", {"backend": "openai_chatgpt", "action": "select",
                                                  "account_id": "account-b"})
    requests[-1][2](status(active="account-b"))
    assert account.activeLabel == "Second account"
    assert workspace.accountLabel == "First account" and workspace.sessionId == "bound-chat"
    assert not opened and "non risulta autorizzato" in account.statusText
    assert [row[0] for row in requests] == ["backend.account", "backend.discover", "backend.discover"]
    assert {row[1]["account_id"] for row in requests[1:]} == {"account-a", "account-b"}


def test_transport_failure_and_disconnect_do_not_poll_or_replay_mutations(direct):
    workspace, account, requests, opened = direct
    account._apply(status("awaiting_browser"))
    account.refresh()
    requests[-1][3]({"code": "DISCONNECTED", "message": "Disconnected"})
    assert account.error == "Disconnected" and not account._timer.isActive()
    account.cancel()
    delayed = requests[-1][2]
    workspace._connected_value = False
    account.close()
    delayed(status("awaiting_browser"))
    assert not account.busy and not account._timer.isActive() and not opened


def test_backend_switch_opens_provisional_choice_without_mutating_codex(direct):
    workspace, _, requests, _ = direct
    session = {"id": "codex-chat", "project_id": "project", "backend": "codex",
               "backend_ref": "native-thread", "model": "saved-model", "settings": {"reasoning_effort": "high"}}
    workspace._backend, workspace._model, workspace._effort = "codex", "saved-model", "high"
    workspace._session, workspace._sessions = session, [copy.deepcopy(session)]
    workspace.setBackend("openai_chatgpt")
    assert workspace.backend == "codex" and workspace.sessionId == "codex-chat"
    assert workspace.selectedModel == "saved-model" and workspace.selectedEffort == "high"
    assert workspace._sessions == [session]
    assert [row[0] for row in requests] == ["session.providers"]
    workspace.providerSwitch.cancel()
    # Explicitly opening a new chat still allows normal provider/model selection.
    workspace.newChat()
    workspace.setBackend("openai_chatgpt")
    assert workspace.backend == "openai_chatgpt" and not workspace.sessionId
    workspace._catalogs["openai_chatgpt"] = {"models": [
        {"id": "direct-model", "supported_reasoning_efforts": ["low", "high"]}]}
    workspace.setModel("direct-model")
    workspace.setEffort("high")
    assert workspace.efforts == ["low", "high"] and workspace.selectedEffort == "high"
    assert not any(row[0] in {"session.create", "session.switch"} for row in requests)


def test_direct_team_uses_selected_backend_model_effort_without_codex_tier(direct):
    workspace, _, requests, _ = direct
    workspace._backend, workspace._model, workspace._effort = "openai_chatgpt", "direct-model", "high"
    workspace._session = {"id": "direct-chat", "settings": {"service_tier": "legacy", "openai_account_id": "account-a"}}
    workspace.teams.prepareDraft()
    workspace.teams.setDraftText("goal", "Review synthetic local documents")
    workspace.teams.startTeam()
    params = requests[-1][1]
    assert requests[-1][0] == "team.start" and params["backend"] == "openai_chatgpt"
    assert params["model"] == "direct-model" and params["settings"] == {"reasoning_effort": "high"}
    assert "openai_account_id" not in params["settings"], "The service owns account binding"


def test_catalogs_are_scoped_to_bound_chat_and_active_team_account(direct):
    workspace, account, requests, _ = direct
    account._apply(status(active="account-b"))
    workspace._backend = "openai_chatgpt"
    workspace._session = {"id": "bound-chat", "backend": "openai_chatgpt",
                          "settings": {"openai_account_id": "account-a"}}
    workspace.teams._draft["backend"] = "openai_chatgpt"
    workspace.discover("openai_chatgpt")
    workspace.discover("openai_chatgpt")
    assert len(requests) == 1
    response_a = requests[-1][2]
    assert requests[-1][1]["account_id"] == "account-a"
    workspace.discoverForTeam("openai_chatgpt")
    response_b = requests[-1][2]
    assert requests[-1][1]["account_id"] == "account-b"
    response_b({"available": True, "account": {"id": "account-b", "connected": True},
                "models": [{"id": "model-b", "name": "Model B"}]})
    assert workspace.models == [] and workspace.teams.models[0]["id"] == "model-b"
    response_a({"available": True, "account": {"id": "account-a", "connected": True},
                "models": [{"id": "model-a", "name": "Model A"}]})
    assert workspace.models[0]["id"] == "model-a"
    assert workspace.teams.models[0]["id"] == "model-b"
    assert "First account" in workspace.providerText
    workspace.newChat()
    assert not workspace.sessionId and workspace.models[0]["id"] == "model-b"
    assert len(requests) == 2, "The new chat can reuse its own cached catalog"


def test_catalog_mismatch_and_stale_connection_cannot_replace_current_models(direct):
    workspace, account, requests, _ = direct
    account._apply(status())
    workspace._backend = "openai_chatgpt"
    workspace.discover("openai_chatgpt")
    old_response = requests[-1][2]
    workspace._disconnected()
    workspace._connected_value = True
    workspace.discover("openai_chatgpt")
    fresh_response = requests[-1][2]
    old_response({"available": True, "account": {"id": "account-a"}, "models": [{"id": "stale"}]})
    assert not workspace.models
    fresh_response({"available": True, "account": {"id": "account-b"}, "models": [{"id": "wrong-account"}]})
    assert not workspace.models and "altro account" in workspace.providerText


def test_unconfirmed_remote_logout_warning_survives_status_refresh(direct, qtbot):
    workspace, account, requests, _ = direct
    account._apply(status())
    account.logout("account-a")
    reply = status(active="", remote_revocation_confirmed=False)
    reply["accounts"] = []
    requests[-1][2](reply)
    assert "revoca remota" in account.warning and "impostazioni di ChatGPT" in account.warning
    account.refresh()
    requests[-1][2](status(active="", accounts=[]))
    assert "revoca remota" in account.warning
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.setProperty("page", 3)
    window.findChild(QObject, "settingsPane").setProperty("selectedSection", 2)
    qtbot.wait(20)
    try:
        warning = window.findChild(QObject, "settingsChatGPTWarning")
        assert warning is not None and warning.property("visible")
        assert warning.property("text") == account.warning
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_real_qml_account_controls_are_explicit_and_guarded(direct, qtbot):
    workspace, account, requests, opened = direct
    account._apply(status())
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setProperty("page", 3)
    window.findChild(QObject, "settingsPane").setProperty("selectedSection", 2)
    qtbot.wait(30)
    try:
        login = window.findChild(QObject, "settingsChatGPTLogin")
        assert login is not None and login.property("text") == "Continua con ChatGPT"
        choice = window.findChild(QObject, "settingsChatGPTChoice")
        assert choice is not None and choice.property("currentIndex") == 0
        assert choice.property("displayText") == "First account"
        assert login.property("enabled") and not requests and not opened
        expression = QQmlExpression(QQmlEngine.contextForObject(login), login, "clicked()")
        expression.evaluate()
        assert not expression.hasError()
        assert requests[-1][:2] == ("backend.login", {"backend": "openai_chatgpt", "account_id": "account-a"})
        requests[-1][2](status("awaiting_browser", authorization_url="https://auth.openai.com/authorize"))
        qtbot.wait(20)
        assert not login.property("enabled")
        cancel = window.findChild(QObject, "settingsChatGPTCancel")
        assert cancel is not None and cancel.property("visible") and cancel.property("enabled")
        assert not any(row[0] == "run.start" for row in requests)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_plan_welcome_is_dismissible_once_and_private(direct, qtbot):
    workspace, account, requests, opened = direct
    account._apply(status(active="account-b"))
    assert not account.welcomePending, "Identity-only access does not authorize plan use"
    account._apply(status("completed"))
    assert account.welcomePending
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    qtbot.wait(30)
    try:
        dialog = window.findChild(QObject, "chatGPTWelcomeDialog")
        assert dialog is not None and dialog.property("visible")
        button = window.findChild(QObject, "chatGPTWelcomeUsage")
        expression = QQmlExpression(QQmlEngine.contextForObject(button), button, "clicked()")
        expression.evaluate()
        assert not expression.hasError() and opened == ["https://chatgpt.com/settings/usage"]
        button = window.findChild(QObject, "chatGPTWelcomeDismiss")
        expression = QQmlExpression(QQmlEngine.contextForObject(button), button, "clicked()")
        expression.evaluate()
        assert not expression.hasError()
        qtbot.wait(20)
        assert not dialog.property("visible") and not account.welcomePending
        path = workspace.paths.config / "ui.json"
        assert path.stat().st_mode & 0o777 == 0o600
        restored = Workspace(workspace.paths)
        try:
            restored.directAccount._apply(status("completed"))
            assert not restored.directAccount.welcomePending
        finally:
            restored.close()
            restored.deleteLater()
        assert not requests, "Acknowledging plan usage is only a local UI preference"
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_quota_cta_uses_recorded_code_for_current_direct_chat(direct, qtbot):
    workspace, account, requests, opened = direct
    account._apply(status())
    account.dismissWelcome()
    workspace._backend = "openai_chatgpt"
    workspace._project = {"id": "project-a", "name": "Synthetic project"}
    workspace._session = {"id": "chat-a", "settings": {"openai_account_id": "account-a"}}
    workspace._runs = {"r": {"id": "r", "session_id": "chat-a", "state": "failed",
                             "created_at": "2026-01-01T12:00:00Z",
                             "outcome": {"code": "subscription_sharing_usage_limit_exceeded"}}}
    assert workspace.planQuotaLimited
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    qtbot.wait(30)
    try:
        button = window.findChild(QObject, "chatGPTPlanUsage")
        label = window.findChild(QObject, "chatGPTPlanIndicator")
        assert button is not None and button.property("primary") and button.property("visible")
        assert label is not None and label.property("text") == "Limite di utilizzo raggiunto"
        expression = QQmlExpression(QQmlEngine.contextForObject(button), button, "clicked()")
        expression.evaluate()
        assert not expression.hasError() and not opened
        assert ("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"}) in [row[:2] for row in requests]
        assert ("session.context", {"project_id": "project-a", "session_id": "chat-a"}) in [row[:2] for row in requests]
        usage = window.findChild(QObject, "usageDialog")
        expression = QQmlExpression(QQmlEngine.contextForObject(usage), usage, "open()")
        expression.evaluate()
        qtbot.wait(20)
        usage_button = window.findChild(QObject, "usageManageChatGPT")
        assert usage_button is not None and usage_button.property("visible")
        workspace._session = {"id": "chat-b", "settings": {"openai_account_id": "account-a"}}
        workspace.changed.emit()
        assert not workspace.planQuotaLimited
        assert {row[0] for row in requests} <= {"assistant.snapshot", "backend.usage", "session.context"}
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
