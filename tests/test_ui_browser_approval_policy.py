"""Synthetic chat-scoped browser consent; no browsing or provider execution."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, Qt
from PySide6.QtTest import QTest

from kira.ui.app import create_engine
from test_ui_embedded_browser import event as browser_event, state as browser_state
from test_ui_support import wait_laid_out, wait_opened
from test_ui_web import evaluate, snapshot
import test_ui_web

web = test_ui_web.web


def policy_snapshot(policy="ask", **updates):
    return snapshot(settings={"web_search": "live", "browser_enabled": True,
                              "browser_persistent": False, "browser_action_approval": policy}, **updates)


def loaded(fixture, policy="ask"):
    workspace, controller, requests = fixture
    controller.refresh()
    requests[-1][2](policy_snapshot(policy))
    return workspace, controller, requests


def active(workspace, *, session_id="chat"):
    workspace._runs = {"run": {"id": "run", "session_id": session_id, "state": "running"}}


def approval(workspace, **updates):
    active(workspace)
    payload = {"approval_id": "approval", "kind": "browser_action", "tool": "browser.click",
               "allow_always": True, "arguments": {"ref": "synthetic-ref"},
               "url": "https://example.test/", "target": {"label": "<literal target>"}, **updates}
    workspace._apply_event({"run_id": "run", "type": "approval.requested", "payload": payload})
    return payload


def policy_requests(requests):
    return [request for request in requests if request[0] == "session.web.update"]


def approval_requests(requests):
    return [request for request in requests if request[0] == "run.approve"]


def bounds(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def click(window, control):
    assert control.isVisible() and control.isEnabled()
    point = control.mapToScene(control.boundingRect().center()).toPoint()
    assert QRectF(0, 0, window.width(), window.height()).contains(point)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)


def test_live_policy_saves_only_its_field_and_keeps_pending_edits_and_session(web):
    workspace, controller, requests = loaded(web)
    workspace._drafts[("project", "chat")] = "Synthetic unsent draft"
    before = deepcopy((workspace._session, workspace._drafts))
    controller.setSearchMode("cached")
    controller.setPersistentEnabled(True)
    active(workspace)
    assert not controller.canEdit and controller.canChangeActionApproval
    controller.setActionApprovalAlways(True)
    request = policy_requests(requests)[-1]
    assert request[:2] == ("session.web.update", {"project_id": "project", "session_id": "chat",
                                                "browser_action_approval": "always", "expected_generation": 2})
    assert controller.busy and not controller.canChangeActionApproval
    assert not controller.actionApprovalAlways
    controller.setActionApprovalAlways(True)
    controller.setActionApprovalAlways(False)
    assert len(policy_requests(requests)) == 1
    request[2](policy_snapshot("always"))
    assert controller.actionApprovalAlways and controller.canChangeActionApproval
    assert controller.dirty and controller.settings["web_search"] == "cached"
    assert controller.settings["browser_persistent"] is True
    assert (workspace._session, workspace._drafts) == before
    assert not any(method.startswith("session.browser.") for method, *_ in requests)
    controller.setActionApprovalAlways(False)
    request = policy_requests(requests)[-1]
    assert request[1] == {"project_id": "project", "session_id": "chat",
                          "browser_action_approval": "ask", "expected_generation": 2}
    request[2](policy_snapshot())
    assert not controller.actionApprovalAlways and controller.dirty
    assert (workspace._session, workspace._drafts) == before


def test_failed_revocation_preserves_saved_policy_and_never_retries(web):
    _, controller, requests = loaded(web, "always")
    controller.setActionApprovalAlways(False)
    request = policy_requests(requests)[-1]
    request[3]({"code": "SYNTHETIC_FAILURE", "message": "Synthetic policy was not saved"})
    assert controller.actionApprovalAlways and controller.canChangeActionApproval
    assert controller.error == "Synthetic policy was not saved"
    assert len(policy_requests(requests)) == 1
    controller.setActionApprovalAlways(False)
    assert len(policy_requests(requests)) == 2 and not controller.error
    policy_requests(requests)[-1][2](policy_snapshot())
    assert not controller.actionApprovalAlways


@pytest.mark.parametrize("scope", ["chat", "account", "generation", "model", "offline"])
def test_late_policy_callbacks_never_apply_to_a_changed_context(web, scope):
    workspace, controller, requests = loaded(web)
    controller.setActionApprovalAlways(True)
    request = policy_requests(requests)[-1]
    assert request[1]["expected_generation"] == 2
    if scope == "chat":
        workspace._session["id"] = "other-chat"
    elif scope == "account":
        workspace._session["settings"]["openai_account_id"] = "other-account"
    elif scope == "generation":
        workspace._session["provider_generation"] = 3
    elif scope == "model":
        workspace._model = "other-model"
    else:
        workspace._connected_value = False
    workspace.changed.emit()
    request[2](policy_snapshot("always"))
    request[3]({"message": "Late unrelated failure"})
    assert not controller.loaded and not controller.actionApprovalAlways and not controller.error
    assert len(policy_requests(requests)) == 1
    assert request[1]["expected_generation"] == 2


def test_policy_guard_stays_with_original_choice_and_refresh_uses_no_guard(web):
    workspace, controller, requests = loaded(web)
    controller.setActionApprovalAlways(True)
    original = policy_requests(requests)[-1]
    original_params = deepcopy(original[1])
    workspace._session["provider_generation"] = 3
    workspace._session["settings"]["openai_account_id"] = "account-b"
    workspace.changed.emit()
    # A late response cannot adopt the original account's preference, and the
    # already dispatched request keeps its generation for server-side rejection.
    original[2](policy_snapshot("always"))
    original[3]({"code": "CONFLICT", "message": "Synthetic generation changed"})
    assert original[1] == original_params and original_params["expected_generation"] == 2
    assert not controller.loaded and not controller.actionApprovalAlways and not controller.error
    controller.refresh()
    assert requests[-1][:2] == ("session.web.get", {"project_id": "project", "session_id": "chat"})
    requests[-1][2](policy_snapshot(provider_generation=3, account_id="account-b"))
    controller.setActionApprovalAlways(True)
    assert policy_requests(requests)[-1][1] == {"project_id": "project", "session_id": "chat",
        "browser_action_approval": "always", "expected_generation": 3}


@pytest.mark.parametrize("blocked", ["archived", "team", "configuring", "loading", "offline", "codex", "unloaded"])
def test_live_policy_requires_an_owned_loaded_available_chat(web, blocked):
    workspace, controller, requests = loaded(web)
    if blocked == "archived":
        workspace._session["archived"] = True
    elif blocked == "team":
        workspace._session["team_id"] = "team"
    elif blocked == "configuring":
        workspace._configuring_session = True
    elif blocked == "loading":
        workspace._chat_loading = True
    elif blocked == "offline":
        workspace._connected_value = False
    elif blocked == "codex":
        workspace._backend = "codex"
    else:
        controller._data = {}
    controller.setActionApprovalAlways(True)
    assert not controller.canChangeActionApproval and not policy_requests(requests)


@pytest.mark.parametrize("value", [None, True, 1, [], {}, "sometimes"])
def test_malformed_policy_snapshot_cannot_grant_consent(web, value):
    _, controller, requests = loaded(web)
    controller.setActionApprovalAlways(True)
    policy_requests(requests)[-1][2](policy_snapshot(value))
    assert controller.error and not controller.actionApprovalAlways and not controller.busy


@pytest.mark.parametrize("tool", ["browser.click", "browser.type", "browser.press", "computer.act"])
def test_quick_option_requires_trusted_local_action_and_selected_chat(web, tool):
    workspace, _, requests = loaded(web)
    payload = approval(workspace, tool=tool)
    original = deepcopy(payload)
    assert workspace.approvalCanAlways
    assert '"label": "<literal target>"' in workspace.approval["description"]
    workspace.approve("accept_always")
    assert approval_requests(requests)[-1][1] == {
        "run_id": "run", "approval_id": "approval", "decision": "accept_always"}
    assert workspace.approvalBusy and payload == original


@pytest.mark.parametrize("updates", [
    {"allow_always": False}, {"allow_always": None}, {"allow_always": 1},
    {"kind": "browser_manual_login"}, {"kind": "provider_native"},
    {"tool": "run_command"}, {"tool": "browser.manual_login"}, {"tool": "browser.tabs"},
    {"tool": []}, {"tool": {}},
])
def test_quick_option_never_promotes_other_approval_kinds_or_untrusted_metadata(web, updates):
    workspace, _, requests = loaded(web)
    approval(workspace, **updates)
    assert not workspace.approvalCanAlways
    workspace.approve("accept_always")
    assert not approval_requests(requests)


@pytest.mark.parametrize("scope", ["foreign", "team", "provider", "loading"])
def test_quick_option_does_not_claim_another_scope_as_this_chat(web, scope):
    workspace, _, requests = loaded(web)
    approval(workspace)
    if scope == "foreign":
        workspace._runs["run"]["session_id"] = "other-chat"
    elif scope == "team":
        workspace._runs["run"]["team_id"] = "team"
    elif scope == "provider":
        workspace._backend = "codex"
    else:
        workspace._chat_loading = True
    assert not workspace.approvalCanAlways
    workspace.approve("accept_always")
    assert not approval_requests(requests)


def test_quick_decision_is_single_flight_and_failure_preserves_pending_approval(web):
    workspace, controller, requests = loaded(web)
    approval(workspace)
    workspace.approve("accept_always")
    first = approval_requests(requests)[-1]
    for value in ("accept_always", "accept", "decline"):
        workspace.approve(value)
    controller.setActionApprovalAlways(True)
    assert len(approval_requests(requests)) == 1 and not policy_requests(requests)
    assert workspace.approvalBusy and not controller.canChangeActionApproval
    first[3]({"code": "SYNTHETIC_FAILURE", "message": "Synthetic approval not sent"})
    assert not workspace.approvalBusy and workspace.approval["approval_id"] == "approval"
    assert "Synthetic approval not sent" in workspace.notice
    workspace.approve("accept_always")
    assert len(approval_requests(requests)) == 2
    approval_requests(requests)[-1][2]({"accepted": True})
    assert not workspace.approval and not workspace.approvalBusy
    assert requests[-1][0] == "session.web.get"
    requests[-1][2](policy_snapshot("always"))
    assert controller.actionApprovalAlways


def test_late_approval_reply_cannot_clear_a_new_decision_or_refresh_foreign_chat(web):
    workspace, _, requests = loaded(web)
    approval(workspace)
    workspace.approve("accept_always")
    first = approval_requests(requests)[-1]
    approval(workspace, approval_id="next-approval")
    workspace._session["id"] = "other-chat"
    workspace.changed.emit()
    count = len(requests)
    first[2]({"accepted": True})
    first[3]({"message": "Old unrelated failure"})
    assert workspace.approval["approval_id"] == "next-approval"
    assert "Old unrelated failure" not in workspace.notice
    assert len(requests) == count and not workspace.approvalBusy


def test_already_settled_approval_refreshes_saved_policy_without_replaying_decision(web):
    workspace, controller, requests = loaded(web)
    approval(workspace)
    workspace.approve("accept_always")
    approval_requests(requests)[-1][2]({"accepted": False})
    assert not workspace.approval and requests[-1][0] == "session.web.get"
    requests[-1][2](policy_snapshot("always"))
    assert controller.actionApprovalAlways and len(approval_requests(requests)) == 1


def test_accept_always_notice_closes_only_its_correlated_popup(web):
    workspace, _, _ = loaded(web)
    approval(workspace)
    workspace._apply_event({"run_id": "run", "type": "backend.notice", "payload": {
        "message": "Synthetic decision", "approval_id": "different", "decision": "accept_always"}})
    assert workspace.approval
    workspace._apply_event({"run_id": "run", "type": "backend.notice", "payload": {
        "message": "Synthetic decision", "approval_id": "approval", "decision": "accept_always"}})
    assert not workspace.approval


@pytest.mark.parametrize("language", ["it", "en"])
def test_live_checkbox_saves_and_revokes_with_visible_error_in_real_qml(web, qtbot, qtlog, language):
    workspace, controller, requests = web
    workspace.setUiPreference("language", language)
    workspace._preferences["onboarding_complete"] = True
    active(workspace)
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.resize(880, 620)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", 18)
    try:
        dialog = window.findChild(QObject, "webDialog")
        evaluate(dialog, "open()")
        requests[-1][2](policy_snapshot())
        checkbox = window.findChild(QObject, "webAlwaysAllowActions")
        scroll = window.findChild(QObject, "webScroll")
        wait_opened(qtbot, dialog, scroll, checkbox)
        assert checkbox.isEnabled() and not checkbox.property("checked")
        assert checkbox.property("text") == ("Consenti sempre le azioni dell’agente in questa chat" if language == "it"
                                               else "Always allow the agent’s actions in this chat")
        assert not window.findChild(QObject, "webSave").property("enabled")
        offset = bounds(checkbox).top() - bounds(scroll).top() + evaluate(scroll, "contentItem.contentY")
        evaluate(scroll, f"contentItem.contentY = Math.max(0, Math.min({offset}, contentItem.contentHeight - height))")
        wait_laid_out(qtbot, checkbox)
        assert bounds(scroll).adjusted(-1, -1, 1, 1).contains(bounds(checkbox))
        click(window, checkbox)
        first = policy_requests(requests)[-1]
        assert set(first[1]) == {"project_id", "session_id", "browser_action_approval", "expected_generation"}
        assert first[1]["expected_generation"] == 2
        assert first[1]["browser_action_approval"] == "always" and not checkbox.isEnabled()
        first[2](policy_snapshot("always"))
        assert checkbox.property("checked") and checkbox.isEnabled()
        click(window, checkbox)
        revoke = policy_requests(requests)[-1]
        assert revoke[1]["browser_action_approval"] == "ask"
        revoke[3]({"message": "Synthetic save failure"})
        qtbot.wait(30)
        assert checkbox.property("checked") and checkbox.isEnabled()
        error = window.findChild(QObject, "webError")
        assert error.property("visible") and error.property("text") == "Synthetic save failure"
        assert not approval_requests(requests)
        assert not any("binding loop" in record.message.lower() or "TypeError" in record.message
                       or "ReferenceError" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("language", ["it", "en"])
@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
def test_quick_buttons_fit_at_large_fonts_and_dispatch_once(web, qtbot, qtlog, language, size):
    workspace, controller, requests = loaded(web)
    workspace.setUiPreference("language", language)
    workspace._preferences["onboarding_complete"] = True
    original = approval(workspace)
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.resize(*size)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", 18)
    try:
        popup = window.findChild(QObject, "approvalDialog")
        action_row = window.findChild(QObject, "approvalActions")
        buttons = [window.findChild(QObject, name) for name in
                   ("approvalAllowOnce", "approvalAllowAlways", "approvalDecline")]
        hint = window.findChild(QObject, "approvalAlwaysHint")
        wait_opened(qtbot, popup, action_row, hint, *buttons)
        assert popup.property("visible")
        content = popup.property("contentItem")
        assert buttons[1].property("text") == ("Consenti sempre in questa chat" if language == "it" else "Always allow in this chat")
        for button in buttons:
            assert button.isVisible() and button.isEnabled()
            assert bounds(content).adjusted(-1, -1, 1, 1).contains(bounds(button))
            assert QRectF(0, 0, *size).contains(bounds(button))
            assert bounds(action_row).adjusted(-1, -1, 1, 1).contains(bounds(button))
        for index, first in enumerate(buttons):
            for second in buttons[index + 1:]:
                assert not bounds(first).intersects(bounds(second))
        assert bounds(hint).bottom() <= bounds(action_row).top()
        assert '<literal target>' in workspace.approval["description"]
        click(window, buttons[1])
        assert len(approval_requests(requests)) == 1
        assert all(not button.isEnabled() for button in buttons)
        workspace.approve("accept_always")
        assert len(approval_requests(requests)) == 1
        approval_requests(requests)[-1][2]({"accepted": True})
        requests[-1][2](policy_snapshot("always"))
        assert controller.actionApprovalAlways and not workspace.approval
        assert original["target"] == {"label": "<literal target>"}
        controller._browser_event(browser_event("state", browser_state(manual=False)))
        status = window.findChild(QObject, "browserActionApprovalStatus")
        assert ("azioni consentite" if language == "it" else "actions allowed") in status.property("text")
        assert not any("binding loop" in record.message.lower() or "TypeError" in record.message
                       or "ReferenceError" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
