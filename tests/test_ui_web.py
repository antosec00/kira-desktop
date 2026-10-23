"""Synthetic web settings, isolated local previews and GUI callback boundaries."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlEngine, QQmlExpression

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.i18n import Localization
from kira.ui.web_controller import browser_approval_description
from test_ui_support import wait_opened


def snapshot(**updates):
    return {"project_id": "project", "session_id": "chat", "backend": "openai_chatgpt",
            "model": "synthetic-model", "provider_generation": 2, "account_id": "account-a",
            "settings": {"web_search": "live", "browser_enabled": True, "browser_persistent": False,
                         "browser_action_approval": "ask"},
            "browser": {"available": True, "active": False, "headed": False, "manual": False,
                        "title": "<literal title>", "url": "https://example.test/page", "preview_url": ""},
            **updates}


@pytest.fixture
def web(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path)}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "openai_chatgpt",
                          "model": "synthetic-model", "provider_generation": 2,
                          "settings": {"openai_account_id": "account-a", "reasoning_effort": "high"}}
    workspace._backend, workspace._model, workspace._effort = "openai_chatgpt", "synthetic-model", "high"
    workspace._tools = True
    workspace.changed.emit()
    requests = []
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
    yield workspace, workspace.web, requests
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def test_settings_are_read_only_until_saved_and_preserve_draft_model_and_effort(web):
    workspace, controller, requests = web
    workspace._drafts["chat"] = "Synthetic unsent draft"
    before = copy.deepcopy(workspace._session)
    controller.open()
    controller.refresh()
    assert len(requests) == 1
    assert requests[0][:2] == ("session.web.get", {"project_id": "project", "session_id": "chat"})
    requests[-1][2](snapshot())
    controller.setSearchMode("cached")
    controller.setPersistentEnabled(True)
    assert len(requests) == 1
    assert controller.dirty
    controller.apply()
    assert requests[-1][:2] == ("session.web.update", {
        "project_id": "project", "session_id": "chat", "web_search": "cached",
        "browser_enabled": True, "browser_persistent": True, "browser_action_approval": "ask",
        "expected_generation": 2})
    assert controller.busy
    requests[-1][2](snapshot(settings={"web_search": "cached", "browser_enabled": True,
                                      "browser_persistent": True}))
    assert not controller.dirty and not controller.busy
    assert controller.settings["browser_persistent"]
    assert workspace._session == before
    assert workspace._drafts["chat"] == "Synthetic unsent draft"
    assert workspace.selectedModel == "synthetic-model" and workspace.selectedEffort == "high"


@pytest.mark.parametrize("blocked", ["archived", "team", "busy", "configuring", "loading", "offline", "codex"])
def test_preferences_never_update_an_unavailable_or_busy_chat(web, blocked):
    workspace, controller, requests = web
    controller.refresh()
    requests[-1][2](snapshot())
    controller.setPersistentEnabled(True)
    if blocked == "archived":
        workspace._session["archived"] = True
    elif blocked == "team":
        workspace._session["team_id"] = "team"
    elif blocked == "busy":
        workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running"}}
    elif blocked == "configuring":
        workspace._configuring_session = True
    elif blocked == "loading":
        workspace._chat_loading = True
    elif blocked == "offline":
        workspace._connected_value = False
    else:
        workspace._backend = "codex"
    controller.apply()
    controller.setBrowserEnabled(False)
    assert len(requests) == 1 and not controller.canEdit


def test_deepseek_web_enables_browser_panel_and_accepts_browser_events(web):
    workspace, controller, _ = web
    workspace._backend = "deepseek_web"
    workspace._session["backend"] = "deepseek_web"
    assert controller.available

    controller._visible = False
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running",
                                "backend": "deepseek_web", "model": "synthetic-model"}}
    controller._event({"run_id": "run", "seq": 1, "type": "tool.completed",
                       "payload": {"name": "browser.navigate", "result": {"ok": True}}})
    assert len(web[2]) == 1 and web[2][0][0] == "session.web.get"
    web[2][-1][2](snapshot(backend="deepseek_web", account_id="", browser={
        "browser_id": "browser-deepseek", "active": True, "available": True,
        "headed": True, "manual": False, "tabs": [], "tab_id": "tab", "url": "https://example.test/",
    }))
    assert controller.paneVisible


def test_deepseek_web_vision_models_accept_image_input(web):
    workspace, _, _ = web
    workspace._backend = "deepseek_web"
    workspace._session.update(backend="deepseek_web", model="deepseek-v4-flash")
    workspace._model = "deepseek-v4-flash"
    workspace._catalogs["deepseek_web"] = {"models": [
        {"id": "deepseek-v4-flash", "capabilities": {"images": True}},
        {"id": "deepseek-reasoner", "capabilities": {"images": True}},
    ]}
    assert workspace.imageInputsEnabled
    workspace._model = "deepseek-reasoner"
    assert workspace.imageInputsEnabled


@pytest.mark.parametrize("change", ["chat", "generation", "account", "model", "offline"])
def test_late_callbacks_cannot_change_the_selected_chat(web, change):
    workspace, controller, requests = web
    controller.refresh()
    old = requests[-1]
    if change == "chat":
        workspace._session["id"] = "other-chat"
    elif change == "generation":
        workspace._session["provider_generation"] = 3
    elif change == "account":
        workspace._session["settings"]["openai_account_id"] = "account-b"
    elif change == "model":
        workspace._model = "other-model"
    else:
        workspace._connected_value = False
    workspace.changed.emit()
    old[2](snapshot())
    old[3]({"message": "Late failure"})
    assert not controller.loaded and not controller.busy and not controller.error
    assert not controller._visible and len(requests) == 1


def test_invalid_snapshot_and_scope_leave_data_unloaded(web):
    _, controller, requests = web
    controller.refresh()
    requests[-1][2](snapshot(session_id="foreign-chat"))
    assert not controller.loaded and controller.error
    controller.refresh()
    requests[-1][2](snapshot(settings={"web_search": []}))
    assert not controller.loaded and controller.error


def test_refresh_preserves_unsaved_settings_and_failed_save_can_be_retried(web):
    _, controller, requests = web
    controller.refresh()
    requests[-1][2](snapshot())
    controller.setPersistentEnabled(True)
    controller.refresh()
    requests[-1][2](snapshot())
    assert controller.dirty and controller.settings["browser_persistent"]
    controller.apply()
    requests[-1][3]({"code": "BUSY", "message": "Synthetic service rejection"})
    assert controller.dirty and controller.canEdit and controller.error == "Synthetic service rejection"
    controller.apply()
    assert requests[-1][1]["browser_persistent"]


def test_preview_only_accepts_local_attachment_files_and_preserves_literal_text(web, tmp_path):
    workspace, controller, requests = web
    root = workspace.paths.data / "image-attachments"
    root.mkdir(parents=True)
    path = root / "preview.png"
    path.write_bytes(b"synthetic-preview")
    for url, expected in [(path.as_uri(), path.as_uri()), ("https://example.test/image.png", ""),
                          ((tmp_path / "outside.png").as_uri(), ""),
                          (path.as_uri() + "?redirect=1", ""), ("file://foreign/preview.png", "")]:
        controller.refresh()
        requests[-1][2](snapshot(browser={"available": True, "preview_url": url,
                                          "title": "<literal title>", "url": "https://example.test/%2"}))
        assert controller.browser["preview_url"] == expected
        assert controller.browser["title"] == "<literal title>"
        assert controller.browser["url"] == "https://example.test/%2"
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"synthetic-outside")
    path.unlink()
    path.symlink_to(outside)
    controller.refresh()
    requests[-1][2](snapshot(browser={"available": True, "preview_url": path.as_uri()}))
    assert not controller.browser["preview_url"]


def test_browser_events_coalesce_refresh_only_for_visible_current_chat(web):
    workspace, controller, requests = web
    workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running"},
                       "other": {"id": "other", "session_id": "other-chat", "state": "running"}}
    # Other workspace controllers may refresh independently; count this API only.
    def browser_requests():
        return [request for request in requests if request[0] == "session.web.get"]

    controller.open()
    browser_requests()[-1][2](snapshot())
    event = {"type": "tool.completed", "run_id": "run", "seq": 1,
             "payload": {"name": "browser.navigate"}}
    controller._event({**event, "run_id": "other"})
    controller._event({**event, "payload": {"name": "read_file"}})
    assert not controller._refresh_timer.isActive()
    controller._event(event)
    controller._event(event)
    assert controller._refresh_timer.isActive() and len(browser_requests()) == 1
    controller._refresh_after_event()
    assert browser_requests()[-1][0] == "session.web.get" and len(browser_requests()) == 2
    controller._event({**event, "seq": 2})
    browser_requests()[-1][2](snapshot())
    controller._refresh_after_event()
    assert len(browser_requests()) == 3
    browser_requests()[-1][2](snapshot())
    controller.hide()
    controller._event({**event, "seq": 3})
    assert not controller._refresh_timer.isActive() and len(browser_requests()) == 3


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("kind", ["browser_manual_login", "browser_action"])
def test_browser_approval_guidance_retranslates_without_changing_literal_request(web, selected, kind):
    workspace, _, requests = web
    engine = QQmlEngine()
    localization = Localization(workspace, engine, engine)
    payload = {"kind": kind, "approval_id": "approval", "tool": "browser.click",
               "url": "https://example.test/%2", "arguments": {"ref": "<literal ref>", "text": "Impostazioni"},
               "target": {"label": "Invia", "role": "button", "href": "https://example.test/submit"}}
    original = copy.deepcopy(payload)
    workspace._runs = {"run": {"id": "run", "session_id": "chat" if selected else "other-chat",
                               "state": "running"}}
    try:
        workspace._apply_event({"type": "approval.requested", "run_id": "run", "payload": payload})
        description = workspace.approval["description"]
        assert ("Completa l’accesso" if kind == "browser_manual_login" else "Kira chiede") in description
        workspace.setUiPreference("language", "en")
        translated = workspace.approval["description"]
        assert ("Complete sign-in" if kind == "browser_manual_login" else "Kira is asking") in translated
        assert '"text": "Impostazioni"' in translated and "<literal ref>" in translated
        assert '"label": "Invia"' in translated and '"href": "https://example.test/submit"' in translated
        assert payload == original and workspace.approval["approval_id"] == "approval"
        if selected and kind == "browser_manual_login":
            assert [request[0] for request in requests] == ["session.browser.open"]
        else:
            assert not requests
        assert localization.language == "en"
        assert browser_approval_description({"kind": "provider_native", "text": "Impostazioni"}) is None
    finally:
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("language,ui_size", [("it", 13), ("en", 18)])
def test_real_web_dialog_translates_and_keeps_literal_preview_at_minimum_window(web, qtbot, qtlog, language, ui_size):
    workspace, controller, requests = web
    workspace.setUiPreference("language", language)
    workspace.setUiPreference("appearance", {**workspace.uiPreferences["appearance"], "ui_size": ui_size})
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.setWidth(880)
    window.setHeight(620)
    try:
        dialog = window.findChild(QObject, "webDialog")
        assert dialog is not None
        assert window.findChild(QObject, "chatWebButton").property("enabled")
        evaluate(dialog, "open()")
        path = workspace.paths.data / "image-attachments" / "preview.png"
        path.parent.mkdir(parents=True)
        bitmap = QImage(64, 32, QImage.Format.Format_RGB32)
        bitmap.fill(0x112233)
        assert bitmap.save(str(path))
        requests[-1][2](snapshot(browser={"available": False, "title": "<literal title>",
                                         "url": "https://example.test/%2", "preview_url": path.as_uri()}))
        scroll = window.findChild(QObject, "webScroll")
        wait_opened(qtbot, dialog, scroll)
        assert dialog.property("title") == ("Chat web and browser" if language == "en" else "Web e browser della chat")
        assert window.findChild(QObject, "webComputerTitle").property("text") == ("Kira's Computer" if language == "en" else "Computer di Kira")
        assert window.findChild(QObject, "webPageTitle").property("text") == "<literal title>"
        assert window.findChild(QObject, "webPageUrl").property("text") == "https://example.test/%2"
        assert window.findChild(QObject, "webInstallCommand").property("text") == "kira-desktop browser-install"
        assert window.findChild(QObject, "webPreview").property("source").toString() == path.as_uri()
        assert dialog.property("height") <= 560
        assert scroll.property("contentHeight") > scroll.property("height")
        assert not window.findChild(QObject, "webSave").property("enabled")
        controller.setPersistentEnabled(True)
        assert window.findChild(QObject, "webSave").property("enabled")
        assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                       or "TypeError" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_computer_approval_explains_observed_page_and_preserves_literal_target(web):
    workspace, _, _ = web
    payload = {"kind": "browser_action", "tool": "computer.act",
               "arguments": {"action": "click", "x": 10, "y": 20, "observation_id": "synthetic-frame"},
               "target": {"label": "<literal target>"}, "url": "https://example.test/"}
    original = copy.deepcopy(payload)
    description = browser_approval_description(payload)
    assert "Computer di Kira" in description and "schermata" in description
    assert '"observation_id": "synthetic-frame"' in description and "<literal target>" in description
    assert payload == original
    assert workspace.transcript.rowCount() == 0
