"""Live browser presentation, ephemeral IPC, manual input and chat isolation."""

import base64
import copy
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QObject, QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QInputMethodEvent, QKeyEvent
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.browser_frames import BrowserFrames, decode_frame
from kira.ui.controller import Workspace


@pytest.fixture
def browser(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace.client.compatible = True
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path)}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "openai_chatgpt",
                          "model": "synthetic-model", "provider_generation": 2,
                          "settings": {"openai_account_id": "account", "reasoning_effort": "high"}}
    workspace._backend, workspace._model, workspace._effort = "openai_chatgpt", "synthetic-model", "high"
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


def state(**updates):
    return {"browser_id": "browser-a", "active": True, "available": True, "manual": True,
            "mode": "embedded", "tab_id": "tab-a", "title": "Literal title", "url": "https://example.test/",
            "tabs": [{"tab_id": "tab-a", "title": "Literal title", "url": "https://example.test/"}], **updates}


def event(kind, payload, seq=1, **updates):
    return {"project_id": "project", "session_id": "chat", "generation": 2,
            "browser_id": "browser-a", "type": kind, "seq": seq, "payload": payload, **updates}


def snapshot(**updates):
    return {"project_id": "project", "session_id": "chat", "settings": {
        "web_search": "live", "browser_enabled": True, "browser_persistent": False},
        "browser": state(input_stream_id="stream-a", **updates)}


def frame(width=640, height=400):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x112233)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "JPEG")
    return {"data": base64.b64encode(data.data()).decode(), "mime": "image/jpeg",
            "width": width, "height": height, "tab_id": "tab-a", "seq": 1}


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def ready(controller, requests):
    controller._browser_event(event("state", state()))
    assert requests[-1][0] == "session.browser.watch"
    requests[-1][2](snapshot())


def test_browser_ipc_never_reaches_durable_chat_events(browser, monkeypatch):
    workspace, _, _ = browser
    received, durable = [], []
    workspace.client.browserEventReceived.connect(received.append)
    workspace.client.eventReceived.connect(durable.append)
    message = event("state", state())
    monkeypatch.setattr(workspace.client.socket, "readAll", lambda: QByteArray(
        json.dumps({"version": 1, "browser_event": message}).encode() + b"\n"))
    workspace.client._read()
    assert received == [message] and durable == []
    assert workspace.transcript.rowCount() == 0


def test_frame_decoding_budget_and_memory_only_provider(qapp):
    payload = frame()
    decoded = decode_frame(payload)
    assert decoded is not None and decoded.size() == QSize(640, 400)
    for updates in ({"data": "%%%"}, {"mime": "image/png"}, {"width": 100_000},
                    {"width": 1}, {"height": True}, {"data": "A" * 800_010}):
        assert decode_frame({**payload, **updates}) is None
    provider = BrowserFrames()
    provider.publish("current", decoded)
    size = QSize()
    assert not provider.requestImage("current", size, QSize()).isNull()
    assert size == QSize(640, 400)
    assert provider.requestImage("old", QSize(), QSize()).isNull()
    provider.clear()
    assert provider.requestImage("current", QSize(), QSize()).isNull()


def test_live_events_are_scoped_latest_only_and_clear_on_chat_change(browser):
    workspace, controller, requests = browser
    ready(controller, requests)
    original = frame()
    for updates in ({"session_id": "other"}, {"project_id": "other"}, {"generation": 1},
                    {"browser_id": "other"}):
        controller._browser_event(event("frame", original, seq=2, **updates))
    assert controller._pending_frame is None
    controller._browser_event(event("frame", {**original, "tab_id": "other"}, seq=2))
    assert controller._pending_frame is None
    controller._browser_event(event("frame", original, seq=2))
    controller._browser_event(event("frame", original, seq=3))
    controller._browser_event(event("frame", original, seq=2))
    assert controller._pending_frame["_seq"] == 3
    controller._publish_frame()
    assert controller.frameUrl.endswith("/browser-a/3")
    assert controller.frameWidth == 640 and controller.frameHeight == 400
    workspace._session["id"] = "other"
    workspace.changed.emit()
    assert not controller.frameUrl and not controller.paneVisible and controller._pending_frame is None
    assert requests[-1][0] == "session.browser.unwatch"
    assert requests[-1][1]["session_id"] == "chat"


def test_visibility_watch_unwatch_without_idle_polling_or_restart(browser):
    _, controller, requests = browser
    ready(controller, requests)
    assert controller.paneVisible
    controller.hideBrowser()
    assert requests[-1][0] == "session.browser.unwatch"
    controller._browser_event(event("state", state(title="Changed"), seq=2))
    assert not controller.paneVisible
    count = len(requests)
    controller._browser_event(event("frame", frame(), seq=3))
    assert len(requests) == count and not controller._frame_timer.isActive()
    controller.showBrowser()
    assert requests[-1][0] == "session.browser.watch"
    controller.setWindowActive(False)
    assert requests[-1][0] == "session.browser.unwatch"
    count = len(requests)
    controller.sendText("Hidden input")
    assert len(requests) == count and not controller.controlling
    controller.setWindowActive(True)
    assert requests[-1][0] == "session.browser.watch"
    assert not controller._refresh_timer.isActive()


def test_manual_commands_are_single_flight_and_unknown_result_never_retried(browser):
    workspace, controller, requests = browser
    workspace._drafts["chat"] = "Synthetic draft"
    original_session = copy.deepcopy(workspace._session)
    ready(controller, requests)
    controller._set_browser(state(manual=False))
    controller.sendText("Not allowed")
    controller.command("ui_navigate", {"url": "https://example.test/other"})
    assert len(requests) == 1
    controller.command("ui_takeover", {})
    assert requests[-1][1]["action"] == "ui_takeover"
    requests[-1][2](snapshot())
    controller.sendText("Synthetic Unicode: è 日本語")
    first = requests[-1]
    controller.sendKey("Enter", "Enter", 0, True)
    controller.sendKey("Enter", "Enter", 0, False)
    assert requests[-1] is first
    assert first[1]["arguments"] == {"text": "Synthetic Unicode: è 日本語"}
    assert first[1]["stream_id"] == "stream-a" and first[1]["sequence"] == 2
    first[3]({"code": "DISCONNECTED", "message": "Synthetic unknown outcome"})
    assert not controller._command_queue and not controller.commandBusy
    assert requests[-1] is first and controller.error == "Synthetic unknown outcome"
    assert workspace._session == original_session and workspace._drafts["chat"] == "Synthetic draft"
    assert workspace.transcript.rowCount() == 0


def test_mouse_maps_letterboxed_viewport_and_coalesces_moves(browser):
    _, controller, requests = browser
    ready(controller, requests)
    controller._browser_event(event("frame", frame(), seq=2))
    controller._publish_frame()
    controller.sendMouse("press", 200, 10, 640, 800, "left", 1, 0, 0, 0)
    assert len(requests) == 1
    controller.sendMouse("move", 300, 400, 640, 800, "none", 0, 0, 0, 0)
    controller.sendMouse("move", 320, 400, 640, 800, "none", 0, 0, 0, 0)
    assert len(requests) == 1 and controller._mouse_timer.isActive()
    controller._flush_mouse()
    assert requests[-1][1]["arguments"]["x"] == 320
    assert requests[-1][1]["arguments"]["y"] == 200
    assert requests[-1][1]["arguments"]["type"] == "move"
    requests[-1][2](snapshot())
    controller.sendMouse("press", 320, 400, 640, 800, "left", 1, 0, 0, 0)
    assert requests[-1][1]["arguments"]["type"] == "press"
    controller.sendMouse("release", 320, 400, 640, 800, "left", 0, 0, 0, 0)
    requests[-1][2](snapshot())
    assert requests[-1][1]["arguments"]["type"] == "release"
    assert requests[-1][1]["sequence"] == 3


@pytest.mark.parametrize("language,width", [("it", 880), ("en", 1380)])
def test_real_embedded_pane_and_inline_login_preserve_chat(browser, qtbot, qtlog, language, width):
    workspace, controller, requests = browser
    workspace.setUiPreference("language", language)
    workspace._drafts["chat"] = "Synthetic unsent draft"
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.setWidth(width)
    window.setHeight(680)
    try:
        ready(controller, requests)
        controller._browser_event(event("frame", frame(), seq=2))
        controller._publish_frame()
        workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "awaiting_input"}}
        workspace._approval = {"kind": "browser_manual_login", "approval_id": "approval", "run_id": "run"}
        workspace.changed.emit()
        qtbot.wait(100)
        pane = window.findChild(QObject, "browserPane")
        assert pane.property("visible")
        assert pane.property("width") >= 340
        assert window.findChild(QObject, "browserLoginBanner").property("visible")
        assert not window.findChild(QObject, "approvalDialog").property("visible")
        assert window.findChild(QObject, "browserLoginDone").property("text") == ("Ho finito" if language == "it" else "Done")
        assert window.findChild(QObject, "browserAddress").property("text") == "https://example.test/"
        assert window.findChild(QObject, "browserLiveFrame").property("source").toString().startswith("image://kira-browser/")
        chat = window.findChild(QObject, "chatColumn")
        assert bool(chat.property("visible")) == (width >= 1120)
        keyboard = window.findChild(QObject, "browserKeyboard")
        QGuiApplication.clipboard().setText("Synthetic clipboard 日本語")
        # Explicit paste remains only in the ephemeral browser command path.
        evaluate(keyboard, "paste()")
        qtbot.wait(20)
        queued = [r[1] for r in requests if r[0] == "session.browser.command"]
        queued.extend({"action": action, "arguments": args} for action, args in controller._command_queue)
        assert any(r["action"] == "ui_text" and r["arguments"]["text"] == "Synthetic clipboard 日本語" for r in queued)
        assert workspace._drafts["chat"] == "Synthetic unsent draft"
        evaluate(keyboard, "forceActiveFocus()")
        count = len(controller._command_queue)
        QGuiApplication.sendEvent(keyboard, QInputMethodEvent("ni", []))
        assert len(controller._command_queue) == count
        commit = QInputMethodEvent()
        commit.setCommitString("日本語")
        QGuiApplication.sendEvent(keyboard, commit)
        assert any(action == "ui_text" and args["text"] == "日本語"
                   for action, args in controller._command_queue)
        assert not keyboard.property("text")
        QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert any(action == "ui_copy" for action, _ in controller._command_queue)
        controller.hideBrowser()
        qtbot.wait(20)
        assert chat.property("visible") and not pane.property("visible")
        assert not any("binding loop" in r.message.lower() or "ReferenceError" in r.message or "TypeError" in r.message for r in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_qml_frame_source_never_loads_web_resources():
    source = (Path(__file__).parents[1] / "src/kira/ui/qml/BrowserPane.qml").read_text()
    assert "source: pane.web.frameUrl" in source
    assert "Qt.openUrlExternally" not in source and "WebEngineView" not in source


def test_engine_error_response_is_visible_and_not_replayed(browser):
    _, controller, requests = browser
    ready(controller, requests)
    controller.sendText("Synthetic text")
    first = requests[-1]
    controller.sendKey("Enter", "Enter", 0, True)
    first[2]({"ok": False, "error": {"code": "browser_hidden", "message": "Synthetic engine rejection"}})
    assert controller.error == "Synthetic engine rejection"
    assert not controller._command_queue and not controller.commandBusy
    assert requests[-1] is first


def test_explicit_copy_only_updates_clipboard_for_current_browser_reply(browser):
    workspace, controller, requests = browser
    clipboard = QGuiApplication.clipboard()
    clipboard.setText("Existing clipboard")
    ready(controller, requests)
    controller.command("ui_copy", {})
    request = requests[-1]
    assert request[1]["action"] == "ui_copy" and request[1]["arguments"] == {}
    assert clipboard.text() == "Existing clipboard"
    request[2]({**snapshot(), "clipboard_text": "Literal selection 日本語"})
    assert clipboard.text() == "Literal selection 日本語"
    assert workspace.transcript.rowCount() == 0
    controller.command("ui_copy", {})
    requests[-1][2]({**snapshot(), "session_id": "foreign", "clipboard_text": "Foreign selection"})
    assert clipboard.text() == "Literal selection 日本語"
    controller.command("ui_copy", {})
    stale = requests[-1]
    workspace._session["provider_generation"] = 3
    workspace.changed.emit()
    stale[2]({**snapshot(), "clipboard_text": "Stale selection"})
    assert clipboard.text() == "Literal selection 日本語"


def test_engine_recreation_uses_new_provider_with_same_live_image_store(browser, qtbot):
    workspace, controller, requests = browser
    ready(controller, requests)
    controller._browser_event(event("frame", frame(), seq=2))
    controller._publish_frame()
    for _ in range(2):
        engine = create_engine(workspace)
        window = engine.rootObjects()[0]
        try:
            qtbot.wait(30)
            provider = engine.imageProvider("kira-browser")
            size = QSize()
            assert not provider.requestImage(controller.frameUrl.removeprefix("image://kira-browser/"), size, QSize()).isNull()
            assert size == QSize(640, 400)
        finally:
            window.setProperty("closeConfirmed", True)
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        # Closing a window stops display but leaves the pure Python store usable.
        bitmap = decode_frame(frame())
        controller.frames.publish("recreated", bitmap)
        controller._frame_url = "image://kira-browser/recreated"
    controller.frames.clear()


@pytest.mark.parametrize("language", ["it", "en"])
def test_address_keyboard_draft_survives_frames_and_submits_bare_host(browser, qtbot, qtlog, language):
    workspace, controller, requests = browser
    workspace.setUiPreference("language", language)
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    try:
        ready(controller, requests)
        controller._set_browser(state(url="about:blank"))
        address = window.findChild(QObject, "browserAddress")
        address.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        payload = frame()
        typed = ""
        for index, character in enumerate("example.com", start=1):
            QGuiApplication.sendEvent(window, QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, character))
            QGuiApplication.sendEvent(window, QKeyEvent(QEvent.Type.KeyRelease, 0, Qt.KeyboardModifier.NoModifier, character))
            typed += character
            controller._browser_event(event("frame", payload, seq=index * 2))
            controller._publish_frame()
            controller._browser_event(event("state", state(url="about:blank"), seq=index * 2 + 1))
            assert address.property("text") == typed
        QTest.keyClick(window, Qt.Key.Key_Return)
        navigation = [request for request in requests if request[0] == "session.browser.command"
                      and request[1]["action"] == "ui_navigate"]
        assert len(navigation) == 1
        assert navigation[0][1]["arguments"] == {"url": "https://example.com"}
        assert not any(action == "ui_key" for action, _ in controller._command_queue)
        navigation[0][2](snapshot(url="https://example.com/"))
        assert address.property("text") == "https://example.com/"
        # A different tab replaces the editable address with that tab's URL.
        address.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QGuiApplication.sendEvent(window, QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, "x"))
        controller._set_browser(state(tab_id="tab-b", url="https://example.org/"))
        assert address.property("text") == "https://example.org/"
        assert not any("ReferenceError" in record.message or "TypeError" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("entered,submitted", [
    (" example.com ", "https://example.com"),
    ("example.com:443/path?q=one#two", "https://example.com:443/path?q=one#two"),
    ("//example.com/path", "https://example.com/path"),
    ("http://example.com/path", "http://example.com/path"),
    ("file:///tmp/example.txt", "file:///tmp/example.txt"),
    ("javascript:alert(1)", "javascript:alert(1)"),
])
def test_address_normalization_keeps_explicit_schemes_for_service_validation(browser, entered, submitted):
    _, controller, requests = browser
    ready(controller, requests)
    assert controller.navigateAddress(entered)
    assert requests[-1][1]["arguments"] == {"url": submitted}


def test_navigation_error_survives_viewport_maintenance(browser):
    _, controller, requests = browser
    ready(controller, requests)
    assert controller.navigateAddress("about:blank")
    requests[-1][3]({"code": "INVALID_ARGUMENT", "message": "Synthetic invalid address"})
    assert controller.error == "Synthetic invalid address"
    controller._viewport = (800, 600)
    controller._resize_viewer()
    assert requests[-1][1]["action"] == "viewer"
    assert controller.error == "Synthetic invalid address"
    requests[-1][2](snapshot())
    assert controller.error == "Synthetic invalid address"


def test_gui_key_without_physical_code_conforms_to_worker_protocol(browser):
    from kira.tools.browser import validate_browser_action

    _, controller, requests = browser
    ready(controller, requests)
    controller.sendKey("Enter", "", 0, True)
    arguments = requests[-1][1]["arguments"]
    assert arguments == {"type": "down", "key": "Enter", "modifiers": 0}
    validate_browser_action("ui_key", arguments)


@pytest.mark.parametrize("action,arguments", [
    ("ui_mouse", {"type": "move", "x": 10, "y": 10}),
    ("ui_text", {"text": "Synthetic input"}),
    ("ui_key", {"type": "down", "key": "Tab"}),
    ("viewer", {"enabled": True, "width": 800, "height": 600}),
])
def test_qml_lifecycle_buttons_stay_enabled_during_transient_input(browser, action, arguments):
    workspace, controller, requests = browser
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    try:
        ready(controller, requests)
        buttons = [window.findChild(QObject, name) for name in ("browserTakeControl", "browserStop")]
        assert all(button.property("enabled") for button in buttons)
        colors = [button.property("foreground") for button in buttons]
        controller._enqueue(action, arguments)
        controller._flush_mouse()
        assert controller.commandBusy and not controller.lifecycleBusy
        assert all(button.property("enabled") for button in buttons)
        assert [button.property("foreground") for button in buttons] == colors
        requests[-1][2](snapshot())
        assert not controller.commandBusy and not controller.lifecycleBusy
        assert all(button.property("enabled") for button in buttons)
        assert [button.property("foreground") for button in buttons] == colors
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_resume_queues_once_after_substantive_input_and_discards_obsolete_moves(browser):
    _, controller, requests = browser
    ready(controller, requests)
    controller._enqueue("ui_mouse", {"type": "move", "x": 10, "y": 10})
    controller._flush_mouse()
    first = requests[-1]
    controller._enqueue("ui_mouse", {"type": "move", "x": 20, "y": 20})
    controller.sendText("Already typed")
    controller.sendKey("Tab", "", 0, True)
    controller._enqueue("viewer", {"enabled": True, "width": 800, "height": 600})
    controller.command("ui_resume", {})
    assert controller.lifecycleBusy
    assert [action for action, _ in controller._command_queue] == ["ui_text", "ui_key", "ui_resume"]
    controller.command("ui_resume", {})
    controller.sendText("Too late")
    controller._enqueue("ui_mouse", {"type": "move", "x": 30, "y": 30})
    assert requests[-1] is first and len(controller._command_queue) == 3
    first[2](snapshot())
    for expected in ("ui_text", "ui_key", "ui_resume"):
        assert requests[-1][1]["action"] == expected
        assert controller.lifecycleBusy
        requests[-1][2](snapshot(manual=expected != "ui_resume"))
    assert not controller.lifecycleBusy and not controller.commandBusy and not controller.controlling
    dispatched = [request[1]["action"] for request in requests if request[0] == "session.browser.command"]
    assert dispatched == ["ui_mouse", "ui_text", "ui_key", "ui_resume"]


def test_close_clicked_during_mouse_request_is_queued_once_and_not_lost(browser):
    workspace, controller, requests = browser
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    try:
        ready(controller, requests)
        controller._enqueue("ui_mouse", {"type": "move", "x": 10, "y": 10})
        controller._flush_mouse()
        first = requests[-1]
        controller.sendText("Not dispatched before close")
        close = window.findChild(QObject, "browserStop")
        assert close.property("enabled")
        evaluate(close, "clicked()")
        assert controller.lifecycleBusy and not close.property("enabled")
        assert [action for action, _ in controller._command_queue] == ["close"]
        controller.closeBrowser()
        assert requests[-1] is first and len(controller._command_queue) == 1
        first[2](snapshot())
        assert requests[-1][0] == "session.browser.close" and controller.lifecycleBusy
        controller.closeBrowser()
        assert sum(request[0] == "session.browser.close" for request in requests) == 1
        requests[-1][2](snapshot(active=False, browser_id=""))
        assert not controller.lifecycleBusy and not controller.browser["active"]
        assert not any(request[0] == "session.browser.command" and request[1]["action"] == "ui_text"
                       for request in requests)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("lifecycle", ["close", "ui_resume"])
def test_unknown_input_outcome_drops_queued_lifecycle_without_retry(browser, lifecycle):
    _, controller, requests = browser
    ready(controller, requests)
    controller.sendText("Synthetic uncertain input")
    first = requests[-1]
    if lifecycle == "close":
        controller.closeBrowser()
    else:
        controller.command(lifecycle, {})
    assert controller.lifecycleBusy
    first[3]({"code": "DISCONNECTED", "message": "Synthetic unknown outcome"})
    assert not controller.lifecycleBusy and not controller.commandBusy and not controller._command_queue
    assert requests[-1] is first
    assert controller.error == "Synthetic unknown outcome"


def test_incidental_hover_preserves_existing_action_error(browser):
    _, controller, requests = browser
    ready(controller, requests)
    controller._error = "Synthetic navigation failure"
    controller._enqueue("ui_mouse", {"type": "move", "x": 10, "y": 10})
    controller._flush_mouse()
    assert controller.error == "Synthetic navigation failure"
    requests[-1][2](snapshot())
    assert controller.error == "Synthetic navigation failure"
