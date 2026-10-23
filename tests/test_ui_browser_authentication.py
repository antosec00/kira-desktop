"""Synthetic Qt authentication handoffs; no real profile or native authenticator."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, Qt
from PySide6.QtTest import QTest

from kira.ui.app import create_engine
from test_ui_embedded_browser import evaluate, event, snapshot, state
import test_ui_embedded_browser
from test_ui_native_browser_surface import pixels

browser = test_ui_embedded_browser.browser


@pytest.mark.parametrize("result,word", [
    ("unsupported", "supportate"), ("cancelled", "annullata"), ("failed", "confermata"),
    ("unconfirmed", "confermata"), ("completed", ""),
])
def test_native_authentication_outcome_is_visible_only_on_its_site(auth_ui, result, word):
    with auth_ui(manual=True, auth_pending=False, auth_needs_manual=False,
                 auth_result=result, url="https://passkey.example.test/login") as ui:
        error = ui.find("browserError")
        assert word in error.property("text")
        assert error.isVisible() == bool(word)
        ui.update(url="https://another.example.test/")
        assert not error.isVisible()


def descendants(item):
    yield item
    for child in item.childItems():
        yield from descendants(child)


def bounds(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def literal_texts(item):
    return [child for child in descendants(item)
            if child.property("readOnly") is True and isinstance(child.property("text"), str)]


def operational(requests):
    return [request for request in requests if request[0] == "run.approve"
            or (request[0] == "session.browser.command" and request[1]["action"] != "viewer")]


@pytest.fixture
def auth_ui(browser, qtbot, qtlog, monkeypatch):
    workspace, controller, requests = browser

    @contextmanager
    def opened(*, language="it", size=(1380, 900), large=False, approval=False, **updates):
        workspace.setUiPreference("language", language)
        workspace._preferences.update(onboarding_complete=True, resume_hint_visible=False)
        workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "awaiting_input" if approval else "running"}}
        workspace._approval = ({"kind": "browser_manual_login", "approval_id": "approval", "run_id": "run"}
                               if approval else {})
        current = {"manual": False, "headed": True, "auth_pending": True, "auth_needs_manual": True,
                   "auth_provider": "windows", "auth_origin": "https://passkey.example.test",
                   "auth_operation": "get", "auth_result": "waiting", "surface_error": "", **updates}
        requests.clear()

        def request(method, params, done, failed=None):
            requests.append((method, params, done, failed))
            if method == "session.browser.watch":
                done(snapshot(**current))
            elif method == "session.browser.command" and params.get("action") == "viewer":
                done(snapshot(**current))
            return True

        monkeypatch.setattr(workspace.client, "request", request)
        engine = create_engine(workspace)
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        window.resize(*size)
        if large:
            appearance = engine.rootContext().contextProperty("appearance")
            for name, value in (("ui_size", 18), ("content_size", 20), ("code_size", 20)):
                appearance.setOption(name, value)

        def update(**changes):
            current.update(changes)
            controller._browser_event(event("state", state(**current), seq=controller._browser_cursor + 1))
            sequence = controller._browser_cursor + 1
            controller._browser_event(event("frame", pixels(f"auth-surface-{sequence}",
                                      mode="native" if current["manual"] else "page"), seq=sequence))
            controller._publish_frame()

        def find(name):
            result = window.findChild(QObject, name)
            assert result is not None, name
            return result

        def click(name):
            control = find(name)
            assert control.isVisible() and control.isEnabled(), name
            point = control.mapToScene(control.boundingRect().center()).toPoint()
            assert QRectF(0, 0, window.width(), window.height()).contains(point), name
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)

        try:
            update()
            workspace.changed.emit()
            qtbot.wait(80)
            yield SimpleNamespace(workspace=workspace, controller=controller, requests=requests,
                                  window=window, engine=engine, find=find, update=update,
                                  click=click, current=current)
            assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                           or "TypeError" in record.message or "Unable to assign" in record.message
                           for record in qtlog.records)
        finally:
            window.setProperty("closeConfirmed", True)
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    return opened


@pytest.mark.parametrize("language", ["it", "en"])
def test_agent_authentication_waits_for_one_explicit_takeover_without_approval(auth_ui, qtbot, language):
    with auth_ui(language=language) as ui:
        assert ui.controller.browser["auth_pending"] and ui.controller.browser["auth_needs_manual"]
        assert not ui.controller.controlling and not operational(ui.requests)
        banner = ui.find("browserAuthenticationBanner")
        assert banner.isVisible() and ui.find("browserAuthenticationContinue").isVisible()
        assert not ui.find("browserTakeControl").isEnabled()
        expected = "Continua autenticazione" if language == "it" else "Continue authentication"
        assert ui.find("browserAuthenticationContinue").property("text") == expected
        ui.click("browserAuthenticationContinue")
        calls = operational(ui.requests)
        assert len(calls) == 1 and calls[0][0] == "session.browser.command"
        assert calls[0][1]["action"] == "ui_takeover" and calls[0][1]["arguments"] == {}
        assert not ui.find("browserAuthenticationContinue").isEnabled()
        assert not any(method == "run.approve" for method, *_ in ui.requests)
        # A successful ownership receipt shows the secure Windows instruction;
        # neither ownership nor opening the dialog confirms authentication.
        ui.current.update(manual=True, auth_needs_manual=False)
        calls[0][2](snapshot(**ui.current))
        qtbot.wait(30)
        assert ui.controller.controlling and ui.controller.browser["auth_pending"]
        assert not ui.find("browserAuthenticationContinue").isVisible()
        text = "\n".join(child.property("text") for child in literal_texts(banner))
        assert "Windows" in text and "PIN" in text
        assert len(operational(ui.requests)) == 1


@pytest.mark.parametrize("provider", ["windows", "native"])
def test_authentication_origin_is_literal_and_provider_instructions_are_distinct(auth_ui, provider):
    origin = "https://passkey.example.test/<b>literal</b>?label=PIN&ok=true"
    with auth_ui(manual=True, auth_needs_manual=False, auth_provider=provider, auth_origin=origin) as ui:
        banner = ui.find("browserAuthenticationBanner")
        assert banner.isVisible()
        texts = literal_texts(banner)
        origin_item = next(child for child in texts if origin in child.property("text"))
        assert evaluate(origin_item, "Number(textFormat)") == 0
        assert origin_item.property("text") == "Autenticazione per " + origin
        assert "<b>literal</b>" in origin_item.property("text")
        instruction = "\n".join(child.property("text") for child in texts if child is not origin_item)
        if provider == "windows":
            assert "finestra sicura di Windows" in instruction and "PIN" in instruction
        else:
            assert "finestra del browser" in instruction and "Windows" not in instruction
        assert not operational(ui.requests)


@pytest.mark.parametrize("result", ["completed", "cancelled"])
@pytest.mark.parametrize("approval", [False, True])
def test_authentication_result_reenables_only_explicit_return_or_login_confirmation(auth_ui, qtbot, result, approval):
    with auth_ui(manual=True, auth_needs_manual=False, approval=approval) as ui:
        assert ui.find("browserAuthenticationBanner").isVisible()
        assert not ui.find("browserTakeControl").isEnabled()
        if approval:
            assert not ui.find("browserLoginBanner").isVisible()
            assert ui.find("browserAuthenticationKeepControl").isVisible()
            assert ui.find("browserAuthenticationKeepControl").isEnabled()
            assert not ui.find("browserLoginDone").isEnabled()
        ui.update(auth_pending=False, auth_result=result)
        qtbot.wait(40)
        assert not ui.find("browserAuthenticationBanner").isVisible()
        assert ui.controller.controlling and not operational(ui.requests)
        if approval:
            assert ui.find("browserLoginBanner").isVisible()
            assert not ui.find("browserAuthenticationKeepControl").isVisible()
            assert ui.find("browserLoginDone").isVisible()
            assert ui.find("browserLoginDone").isEnabled()
            assert not ui.find("browserTakeControl").isEnabled()
            assert ui.workspace.approval["approval_id"] == "approval"
            ui.click("browserLoginDone")
            calls = operational(ui.requests)
            assert len(calls) == 1 and calls[0][0] == "run.approve"
            assert calls[0][1] == {"run_id": "run", "approval_id": "approval", "decision": "accept"}
        else:
            assert ui.find("browserTakeControl").isEnabled()
            ui.click("browserTakeControl")
            calls = operational(ui.requests)
            assert len(calls) == 1 and calls[0][0] == "session.browser.command"
            assert calls[0][1]["action"] == "ui_resume"


def test_keep_control_during_authentication_does_not_resume_browser(auth_ui):
    with auth_ui(manual=True, auth_needs_manual=False, approval=True) as ui:
        ui.click("browserAuthenticationKeepControl")
        calls = operational(ui.requests)
        assert len(calls) == 1 and calls[0][0] == "run.approve"
        assert calls[0][1]["decision"] == "decline"
        calls[0][2]({"accepted": True})
        assert ui.controller.controlling and ui.controller.browser["auth_pending"]
        assert not any(request[0] == "session.browser.command" and request[1]["action"] == "ui_resume"
                       for request in ui.requests)


def test_surface_failure_is_visible_literal_and_never_retried(auth_ui):
    failure = "Synthetic native display unavailable <b>detail</b>"
    with auth_ui(manual=True, auth_needs_manual=False, surface_error=failure) as ui:
        error = ui.find("browserError")
        assert error.isVisible() and error.property("text") == failure
        assert evaluate(error, "Number(textFormat)") == 0
        assert ui.find("browserStop").isEnabled()
        assert not operational(ui.requests)


@pytest.mark.parametrize("language", ["it", "en"])
@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
@pytest.mark.parametrize("pending_login,capture_failure,needs_manual", [
    (False, False, True), (True, False, False), (True, True, False), (True, True, True),
])
def test_authentication_controls_and_viewport_fit_at_maximum_fonts(auth_ui, qtbot, language, size,
                                                                  pending_login, capture_failure, needs_manual):
    with auth_ui(language=language, size=size, large=True, approval=pending_login,
                 manual=not needs_manual, auth_needs_manual=needs_manual,
                 surface_error=("The complete browser view is unavailable. Close and reopen the browser after preserving your work."
                                if capture_failure else ""),
                 auth_origin="https://authentication-long-hostname-for-layout.example.test") as ui:
        assert (ui.window.width(), ui.window.height()) == size
        assert ui.engine.rootContext().contextProperty("appearance").uiFont.pixelSize() == 18
        pane = ui.find("browserPane")
        pane_bounds = bounds(pane)
        window_bounds = QRectF(0, 0, ui.window.width(), ui.window.height())
        assert pane.isVisible() and window_bounds.adjusted(-1, -1, 1, 1).contains(pane_bounds)
        for pending in (True, False):
            if not pending:
                ui.update(auth_pending=False, auth_result="completed", auth_needs_manual=False, manual=True)
                qtbot.wait(40)
            assert ui.find("browserAuthenticationBanner").isVisible() == pending
            assert ui.find("browserLoginBanner").isVisible() == (pending_login and not pending)
            assert ui.find("browserAuthenticationKeepControl").isVisible() == (pending_login and pending)
            if pending_login and pending:
                assert ui.find("browserAuthenticationKeepControl").isEnabled()
            bands = [ui.find("browserSettings").parentItem(), ui.find("browserNewTab").parentItem(),
                     ui.find("browserAddress").parentItem()]
            bands.extend(ui.find(name) for name in ("browserAuthenticationBanner", "browserLoginBanner")
                         if ui.find(name).isVisible())
            bands.append(ui.find("browserTakeControl").parentItem())
            if capture_failure:
                bands.append(ui.find("browserError"))
                assert ui.find("browserError").property("contentHeight") <= ui.find("browserError").height() + 1
            bands.append(ui.find("browserViewport"))
            for band in bands:
                rect = bounds(band)
                assert band.isVisible() and rect.width() > 20 and rect.height() > 10, band.objectName()
                assert pane_bounds.adjusted(-1, -1, 1, 1).contains(rect), (band.objectName(), rect, pane_bounds)
                assert window_bounds.adjusted(-1, -1, 1, 1).contains(rect), (band.objectName(), rect, window_bounds)
            for first, second in zip(bands, bands[1:], strict=False):
                assert bounds(first).bottom() <= bounds(second).top() + 1, (
                    first.objectName(), bounds(first), second.objectName(), bounds(second))
            assert ui.find("browserViewport").height() >= 80

            for name in ("browserSettings", "browserExpand", "browserHide", "browserNewTab", "browserBack",
                         "browserForward", "browserReload", "browserAddress", "browserTakeControl", "browserStop"):
                control = ui.find(name)
                assert pane_bounds.adjusted(-1, -1, 1, 1).contains(bounds(control)), name
            for names in (("browserSettings", "browserExpand", "browserHide"),
                          ("browserBack", "browserForward", "browserReload", "browserAddress"),
                          ("browserTakeControl", "browserStop")):
                for left, right in zip(names, names[1:], strict=False):
                    assert not bounds(ui.find(left)).intersects(bounds(ui.find(right))), (left, right)
            for banner_name in ("browserAuthenticationBanner", "browserLoginBanner"):
                banner = ui.find(banner_name)
                if not banner.isVisible():
                    continue
                for text in literal_texts(banner):
                    if text.isVisible():
                        assert bounds(banner).adjusted(-1, -1, 1, 1).contains(bounds(text)), (banner_name, text.property("text"))
                        assert text.property("contentHeight") <= text.height() + 1, (banner_name, text.property("text"))
                buttons = ([ui.find("browserLoginCancel"), ui.find("browserLoginDone")]
                           if banner_name == "browserLoginBanner" else
                           [ui.find("browserAuthenticationContinue"), ui.find("browserAuthenticationKeepControl")])
                visible_buttons = [button for button in buttons if button.isVisible()]
                for button in visible_buttons:
                    assert bounds(banner).adjusted(-1, -1, 1, 1).contains(bounds(button)), button.objectName()
                    assert window_bounds.contains(bounds(button)), button.objectName()
                if len(visible_buttons) == 2:
                    assert not bounds(visible_buttons[0]).intersects(bounds(visible_buttons[1]))
        assert not operational(ui.requests)
