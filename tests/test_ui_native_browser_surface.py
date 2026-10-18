"""Synthetic native-browser pixels keep manual input bound to their visible scope."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication, QInputMethodEvent
from PySide6.QtTest import QTest

from kira.ui.app import create_engine
from test_ui_embedded_browser import evaluate, event, frame, ready, snapshot, state
import test_ui_embedded_browser

browser = test_ui_embedded_browser.browser


def pixels(identifier="surface-a", *, mode="native", width=640, height=400):
    return {**frame(width, height), "surface_id": identifier, "surface_mode": mode}


def publish(controller, identifier="surface-a", *, seq=2, mode="native", width=640, height=400):
    controller._browser_event(event("frame", pixels(identifier, mode=mode, width=width, height=height), seq=seq))
    controller._publish_frame()


def input_requests(requests):
    return [request for request in requests if request[0] == "session.browser.command"
            and request[1]["action"] != "viewer"]


def test_only_the_published_frame_can_label_new_input(browser):
    workspace, controller, requests = browser
    ready(controller, requests)
    original = deepcopy((workspace._session, workspace._drafts))
    count = len(requests)
    publish(controller)
    assert controller.surfaceId == "surface-a" and controller.nativeSurface
    first_revision = controller.surfaceRevision
    controller._browser_event(event("frame", pixels("surface-b"), seq=3))
    assert controller.surfaceId == "surface-a" and controller.surfaceRevision == first_revision
    assert len(requests) == count
    controller.sendText("Synthetic first field")
    first = requests[-1]
    assert first[1]["arguments"] == {"text": "Synthetic first field", "surface_id": "surface-a"}
    controller.sendKey("Enter", "Enter", 0, True)
    assert controller._command_queue[-1][1]["surface_id"] == "surface-a"
    controller._publish_frame()
    assert controller.surfaceId == "surface-b" and controller.surfaceRevision > first_revision
    assert not controller._command_queue
    first[2](snapshot())
    assert requests[-1] is first
    controller.sendText("Synthetic second field")
    assert requests[-1][1]["arguments"]["surface_id"] == "surface-b"
    assert first[1]["arguments"]["surface_id"] == "surface-a"
    assert (workspace._session, workspace._drafts) == original
    assert not workspace.transcript.rows and not workspace.paths.db.exists()


@pytest.mark.parametrize("updates", [
    {"surface_id": None}, {"surface_id": "x" * 65}, {"surface_id": "bad\0id"},
    {"surface_mode": "desktop"}, {"surface_mode": []}, {"surface_mode": None},
    {"surface_id": ""}, {"data": "invalid-jpeg", "surface_id": "surface-b"},
])
def test_invalid_surface_or_bitmap_does_not_relabel_last_valid_pixels(browser, updates):
    _, controller, requests = browser
    ready(controller, requests)
    publish(controller)
    original = controller.frameUrl, controller.surfaceId, controller.nativeSurface, controller.surfaceRevision
    controller._browser_event(event("frame", {**pixels("surface-b"), **updates}, seq=3))
    controller._publish_frame()
    assert (controller.frameUrl, controller.surfaceId, controller.nativeSurface, controller.surfaceRevision) == original
    controller.sendText("Synthetic field")
    assert requests[-1][1]["arguments"]["surface_id"] == "surface-a"


def test_surface_less_page_fixture_keeps_backward_compatible_arguments(browser):
    _, controller, requests = browser
    ready(controller, requests)
    controller._browser_event(event("frame", frame(), seq=2))
    controller._publish_frame()
    assert not controller.nativeSurface and not controller.surfaceId
    controller.sendText("Synthetic legacy input")
    assert requests[-1][1]["arguments"] == {"text": "Synthetic legacy input"}


def test_pixel_refresh_keeps_pending_input_but_resize_discards_it(browser):
    _, controller, requests = browser
    ready(controller, requests)
    publish(controller)
    controller.sendText("First")
    first = requests[-1]
    controller.sendKey("Tab", "", 0, True)
    revision = controller.surfaceRevision
    publish(controller, seq=3)
    assert controller.surfaceRevision == revision
    assert controller._command_queue == [("ui_key", {"type": "down", "key": "Tab", "modifiers": 0, "surface_id": "surface-a"})]
    # Even a malformed producer reusing an identifier for new dimensions must
    # not carry old coordinate/key input across the local pixel boundary.
    publish(controller, seq=4, width=800, height=600)
    assert controller.surfaceRevision > revision and not controller._command_queue
    first[2](snapshot())
    assert requests[-1] is first


def test_surface_switch_discards_old_keyups_and_preserves_explicit_handoff(browser):
    _, controller, requests = browser
    ready(controller, requests)
    publish(controller)
    controller.sendKey("Enter", "", 0, True)
    first = requests[-1]
    controller.sendKey("Enter", "", 0, False)
    controller.sendText("Queued text")
    controller.command("ui_copy", {})
    controller.command("ui_resume", {})
    assert controller.lifecycleBusy
    publish(controller, "surface-b", seq=3)
    assert controller._command_queue == [("ui_resume", {})]
    first[2](snapshot())
    assert requests[-1][1]["action"] == "ui_resume"
    assert requests[-1][1]["arguments"] == {}
    assert [request[1]["action"] for request in input_requests(requests)] == ["ui_key", "ui_resume"]


def test_key_release_from_old_surface_is_not_sent_to_new_target(browser):
    _, controller, requests = browser
    ready(controller, requests)
    publish(controller)
    controller.sendKey("Tab", "", 0, True)
    first = requests[-1]
    publish(controller, "surface-b", seq=3)
    controller.sendKey("Tab", "", 0, False)
    assert not controller._command_queue and requests[-1] is first
    first[2](snapshot())
    controller.sendKey("Enter", "", 0, True)
    second = requests[-1]
    controller.sendKey("Enter", "", 0, False)
    second[2](snapshot())
    assert requests[-1][1]["arguments"] == {"type": "up", "key": "Enter", "modifiers": 0, "surface_id": "surface-b"}


def test_mouse_coalescing_keeps_original_identity_and_letterbox_mapping(browser):
    _, controller, requests = browser
    ready(controller, requests)
    publish(controller, width=800, height=600)
    controller.sendMouse("move", 300, 400, 800, 800, "none", 0, 0, 0, 0)
    controller.sendMouse("move", 400, 400, 800, 800, "none", 0, 0, 0, 0)
    controller._browser_event(event("frame", pixels("surface-b", width=800, height=600), seq=3))
    controller._flush_mouse()
    request = requests[-1]
    assert request[1]["arguments"]["surface_id"] == "surface-a"
    assert (request[1]["arguments"]["x"], request[1]["arguments"]["y"]) == (400, 300)
    controller.sendMouse("move", 450, 400, 800, 800, "none", 0, 0, 0, 0)
    controller._publish_frame()
    assert controller._mouse_move is None and not controller._mouse_timer.isActive()
    request[2](snapshot())
    assert requests[-1] is request
    controller._flush_mouse()
    assert requests[-1] is request
    # An out-of-viewport letterbox coordinate never becomes a native click.
    controller.sendMouse("press", 400, 20, 800, 800, "left", 1, 0, 0, 0)
    assert requests[-1] is request
    controller.sendMouse("press", 400, 400, 800, 800, "left", 1, 0, 0, 0)
    pressed = requests[-1]
    publish(controller, "surface-c", seq=4, width=800, height=600)
    controller.sendMouse("move", 450, 400, 800, 800, "none", 1, 0, 0, 0)
    controller.sendMouse("release", 450, 400, 800, 800, "left", 0, 0, 0, 0)
    assert not controller._command_queue and controller._mouse_move is None
    pressed[2](snapshot())
    assert requests[-1] is pressed


@pytest.mark.parametrize("change", ["hide", "window", "disconnect", "chat", "tab", "control"])
def test_scope_reset_clears_surface_and_queued_manual_input(browser, change):
    workspace, controller, requests = browser
    ready(controller, requests)
    publish(controller)
    controller.sendText("In flight")
    controller.sendText("Do not move to the next surface")
    controller.sendMouse("move", 300, 200, 640, 400, "none", 0, 0, 0, 0)
    count = len(input_requests(requests))
    if change == "hide":
        controller.hideBrowser()
    elif change == "window":
        controller.setWindowActive(False)
    elif change == "disconnect":
        workspace._connected_value = False
        workspace.changed.emit()
    elif change == "chat":
        workspace._session["id"] = "other-chat"
        workspace.changed.emit()
    elif change == "tab":
        controller._set_browser(state(tab_id="other-tab"))
    else:
        controller._set_browser(state(manual=False))
    assert not controller.surfaceId and not controller.nativeSurface and not controller.frameUrl
    assert not controller._command_queue and controller._mouse_move is None
    assert len(input_requests(requests)) == count


def test_copy_stays_ephemeral_and_discards_reply_from_replaced_surface(browser):
    workspace, controller, requests = browser
    clipboard = QGuiApplication.clipboard()
    clipboard.setText("Existing clipboard")
    ready(controller, requests)
    publish(controller)
    controller.command("ui_copy", {"surface_id": "caller-cannot-relabel-the-frame"})
    request = requests[-1]
    assert request[1]["arguments"] == {"surface_id": "surface-a"}
    request[2]({**snapshot(), "clipboard_text": "Synthetic current selection"})
    assert clipboard.text() == "Synthetic current selection"
    controller.command("ui_copy", {})
    request = requests[-1]
    publish(controller, "surface-b", seq=3)
    request[2]({**snapshot(), "clipboard_text": "Synthetic stale selection"})
    assert clipboard.text() == "Synthetic current selection"
    assert "selection" not in str(controller._data) and not workspace.transcript.rows
    assert not workspace.paths.db.exists()


@pytest.mark.parametrize("manual", [False, True])
def test_headed_surface_after_handoff_rejects_late_frames_from_previous_owner(browser, manual):
    _, controller, requests = browser
    ready(controller, requests)
    previous_mode, current_mode = ("page", "native") if manual else ("native", "page")
    controller._set_browser(state(headed=True, manual=not manual))
    publish(controller, mode=previous_mode)
    assert controller.frameUrl
    old_key = controller.frameUrl.removeprefix("image://kira-browser/")
    controller._browser_event(event("state", state(headed=True, manual=manual,
        surface_error="Synthetic capture unavailable"), seq=3))
    assert not controller.frameUrl and not controller.surfaceId
    assert controller.frames.image(old_key).isNull()
    count = len(requests)
    # The transport may deliver an old coalesced frame after the state change.
    # A failed native capture must leave an empty view plus the explicit error,
    # never republish page pixels that omit the authentication dialog.
    publish(controller, "old-owner-frame", seq=4, mode=previous_mode)
    assert not controller.frameUrl and not controller.surfaceId and not controller.nativeSurface
    assert controller.browser["surface_error"] == "Synthetic capture unavailable"
    assert len(requests) == count
    publish(controller, "current-owner-frame", seq=5, mode=current_mode)
    assert controller.frameUrl and controller.surfaceId == "current-owner-frame"
    assert controller.nativeSurface == manual
    current = controller.frameUrl
    publish(controller, "another-old-frame", seq=6, mode=previous_mode)
    assert controller.frameUrl == current and controller.surfaceId == "current-owner-frame"


def test_enabling_headed_capture_invalidates_previous_headless_page(browser):
    _, controller, requests = browser
    ready(controller, requests)
    publish(controller, mode="page")
    assert controller.frameUrl and controller.controlling
    controller._set_browser(state(headed=True))
    assert not controller.frameUrl and not controller.surfaceId
    publish(controller, "late-headless-page", seq=3, mode="page")
    assert not controller.frameUrl
    publish(controller, "native-owner-frame", seq=4)
    assert controller.nativeSurface and controller.surfaceId == "native-owner-frame"


def test_real_qml_native_badge_wait_message_keys_and_ime(browser, qtbot, qtlog):
    workspace, controller, requests = browser
    workspace._preferences["onboarding_complete"] = True
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.resize(1400, 900)
    try:
        ready(controller, requests)
        publish(controller)
        workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running"}}
        workspace._apply_event({"run_id": "run", "type": "tool.started", "payload": {
            "tool_id": "call", "name": "browser.tabs", "arguments": {}}})
        workspace._apply_event({"run_id": "run", "type": "tool.progress", "payload": {
            "tool_id": "call", "name": "browser.tabs", "phase": "waiting_for_browser_control", "reason": "manual_control"}})
        qtbot.wait(70)
        badge = window.findChild(QObject, "browserNativeBadge")
        wait = window.findChild(QObject, "browserControlWait")
        handoff = window.findChild(QObject, "browserTakeControl")
        keyboard = window.findChild(QObject, "browserKeyboard")
        assert badge.property("visible") and badge.property("text") == "Browser completo"
        assert wait.property("visible") and wait.property("text") == "Kira attende che tu restituisca il controllo del browser."
        assert handoff.property("primary") and handoff.property("enabled")
        assert not workspace.activityAnimating and not input_requests(requests)

        evaluate(keyboard, "forceActiveFocus()")
        QTest.keyPress(window, Qt.Key.Key_Tab)
        assert evaluate(keyboard, "Boolean(forwardedKeys.Tab)")
        revision = controller.surfaceRevision
        publish(controller, seq=3)
        assert controller.surfaceRevision == revision
        assert evaluate(keyboard, "Boolean(forwardedKeys.Tab)")
        publish(controller, "surface-b", seq=4)
        assert not evaluate(keyboard, "Boolean(forwardedKeys.Tab)")
        before = len(input_requests(requests)), len(controller._command_queue)
        QTest.keyRelease(window, Qt.Key.Key_Tab)
        assert (len(input_requests(requests)), len(controller._command_queue)) == before

        # A composition is sent only when committed; ordinary pixel refreshes
        # preserve it and bind the commit to the same visible native surface.
        QGuiApplication.sendEvent(keyboard, QInputMethodEvent("ni", []))
        assert (len(input_requests(requests)), len(controller._command_queue)) == before
        publish(controller, "surface-b", seq=5)
        commit = QInputMethodEvent()
        commit.setCommitString("日本語")
        QGuiApplication.sendEvent(keyboard, commit)
        sent = [request[1] for request in input_requests(requests)]
        sent.extend({"action": action, "arguments": arguments} for action, arguments in controller._command_queue)
        assert any(item["action"] == "ui_text" and item["arguments"] == {"text": "日本語", "surface_id": "surface-b"}
                   for item in sent)
        assert not keyboard.property("text")
        controller._set_browser(state(manual=False))
        assert not badge.property("visible") and not keyboard.property("enabled")
        assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                       or "TypeError" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
