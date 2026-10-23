"""Native/viewer lifecycle remains independent of model tool execution."""

import asyncio
import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PySide6.QtGui import QImage
import pytest

from kira.tools.browser_desktop import BrowserDesktop
from kira.tools.browser_worker import BrowserEngine
from test_browser_engine import FakeHandle, FakePage
from test_browser_view import FakeCDP, embedded_engine, eventually


@pytest.mark.parametrize("stage", ["computer", "view", "auth", "context", "playwright", "desktop"])
async def test_close_releases_all_owned_resources_after_an_earlier_cleanup_failure(stage):
    engine = BrowserEngine()
    order = []

    def resource(name, method="close"):
        async def close():
            order.append(name)
            if name == stage:
                raise OSError("Synthetic cleanup failure")
        return SimpleNamespace(**{method: close})

    engine.computer = resource("computer", "invalidate")
    engine.view = resource("view")
    engine.auth = resource("auth")
    engine.context = resource("context")
    engine.playwright = resource("playwright", "stop")
    engine.desktop = resource("desktop")
    engine.proxy = resource("proxy")
    with pytest.raises(OSError, match="Synthetic"):
        await engine.close()
    assert order == ["computer", "view", "auth", "context", "playwright", "desktop", "proxy"]


async def test_render_failure_retains_executed_operation_without_retry():
    engine, _, _, events = embedded_engine()
    result = {"ok": True, "action_executed": True}
    engine._call = AsyncMock(return_value=result)
    engine.view.sync = AsyncMock(side_effect=OSError("Synthetic renderer failure"))
    assert await engine.call("click", {"ref": "synthetic:1"}) == result
    engine._call.assert_awaited_once_with("click", {"ref": "synthetic:1"})
    assert events[-1]["event"] == "state" and events[-1]["data"]["surface_error"]


async def test_model_manual_pause_cannot_authorize_native_authentication():
    engine, _, _, _ = embedded_engine()
    engine.auth = SimpleNamespace(set_manual=AsyncMock())
    try:
        assert (await engine.call("manual_pause", {}))["paused"]
        assert engine.paused and not engine.auth_manual_ready
        engine.auth.set_manual.assert_not_awaited()
        assert (await engine.call("ui_takeover", {}))["manual"]
        assert engine.auth_manual_ready
        engine.auth.set_manual.assert_awaited_once_with(True)
    finally:
        await engine.view.close()


class NativeDesktop:
    def __init__(self):
        self.width, self.height = 1280, 800
        self.ready = asyncio.Event()
        self.resize_error = False
        self.capture_error = False
        self.release_inputs = Mock()
        self.input = AsyncMock()

    async def resize(self, width, height):
        if self.resize_error:
            raise OSError("Synthetic display resize failure")
        self.width, self.height = width, height
        self.mark_dirty()

    def mark_dirty(self):
        self.ready.set()

    async def wait_changed(self):
        await self.ready.wait()
        self.ready.clear()

    def capture_jpeg(self):
        if self.capture_error:
            raise OSError("Synthetic capture failure")
        return {"data": "eA==", "mime": "image/jpeg", "width": self.width, "height": self.height}


class NativeCDP(FakeCDP):
    def __init__(self, failure=""):
        super().__init__()
        self.failure = failure

    async def send(self, method, args=None):
        if method == self.failure:
            raise OSError("Synthetic CDP setup failure")
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1}
        return await super().send(method, args)


def native_engine(*, manual=False):
    engine, page, _, events = embedded_engine()
    desktop = engine.desktop = NativeDesktop()
    engine.paused = manual
    sessions = []

    async def session(_page):
        cdp = NativeCDP()
        sessions.append(cdp)
        return cdp

    engine.context.new_cdp_session = AsyncMock(side_effect=session)
    return engine, page, desktop, sessions, events


def other_page(engine, identifier="other"):
    page = FakePage([FakeHandle()])
    page.set_viewport_size = AsyncMock()
    engine.pages[identifier] = page
    return page


def frames(events):
    return [event["data"] for event in events if event["event"] == "frame"]


@pytest.mark.parametrize("failure", ["resize", "Page.startScreencast", "Page.captureScreenshot"])
async def test_failed_stream_start_rolls_back_and_can_retry(failure):
    engine, _, desktop, sessions, events = native_engine()
    try:
        desktop.resize_error = failure == "resize"
        failed = NativeCDP(failure=failure)
        engine.context.new_cdp_session = AsyncMock(return_value=failed)
        with pytest.raises(OSError):
            await engine.view.configure({"enabled": True, "width": 800, "height": 600})
        assert failed.detached
        assert engine.view.cdp is None and engine.view._surface_scope is None
        assert engine.view._frames is None and engine.view.surface_id == ""
        assert engine.view._surface_error
        await engine.view.state()
        assert events[-1]["data"]["surface_error"]
        desktop.resize_error = False
        recovered = NativeCDP()
        engine.context.new_cdp_session = AsyncMock(return_value=recovered)
        await engine.view.sync()
        await eventually(lambda: bool(frames(events)))
        await eventually(lambda: not engine.view._surface_error)
        assert engine.view.cdp is recovered and not recovered.detached
        assert events[-1]["data"]["surface_error"] == ""
        assert sessions == []
    finally:
        await engine.view.close()


async def test_native_capture_error_survives_state_updates_until_real_frame():
    engine, page, desktop, _, events = native_engine(manual=True)
    try:
        desktop.capture_error = True
        await engine.view.configure({"enabled": True})
        await eventually(lambda: engine.view._frames.done())
        assert engine.view._surface_error
        page.title = AsyncMock(return_value="Changed synthetic title")
        await engine.view.state()
        assert events[-1]["data"]["surface_error"]
        assert not frames(events)
        desktop.capture_error = False
        await engine.view.sync()
        # Successful startup alone does not clear the previous failure.
        assert engine.view._surface_error
        await eventually(lambda: bool(frames(events)) and not engine.view._surface_error)
        frame_index = next(i for i, event in enumerate(events) if event["event"] == "frame")
        clear_index = next(i for i, event in enumerate(events)
                           if event["event"] == "state" and event["data"].get("surface_error") == "")
        assert clear_index > frame_index
    finally:
        await engine.view.close()


async def test_dismissed_cdp_cannot_relabel_old_pixels_as_another_tab():
    engine, _, _, sessions, events = native_engine()
    try:
        await engine.view.configure({"enabled": True})
        await eventually(lambda: bool(frames(events)))
        previous_cdp, previous_id = sessions[-1], engine.view.surface_id
        other_page(engine)
        engine.current_tab = "other"
        await engine.view.sync()
        assert previous_cdp.detached and engine.view.surface_id != previous_id
        previous_cdp.events["Page.screencastFrame"]({"sessionId": 17, "data": "b2xkLXBpeGVscw=="})
        await eventually(lambda: any(frame["tab_id"] == "other" for frame in frames(events)))
        assert all(frame["data"] != "b2xkLXBpeGVscw==" for frame in frames(events))
        assert not any(name == "Page.screencastFrameAck" and values.get("sessionId") == 17
                       for name, values in sessions[-1].sent)
    finally:
        await engine.view.close()


async def test_popup_during_startup_discards_candidate_before_publishing_pixels():
    engine, page, _, sessions, events = native_engine(manual=True)
    original = engine.current_tab
    other_page(engine)

    async def popup(_viewport):
        engine.current_tab = "other"
        engine.view.changed()

    page.set_viewport_size = AsyncMock(side_effect=popup)
    try:
        await engine.view.configure({"enabled": True})
        assert sessions[0].detached and engine.view.cdp is None
        assert engine.view.surface_id == "" and engine.view._surface_scope is None
        assert not frames(events)
        await eventually(lambda: any(frame["tab_id"] == "other" for frame in frames(events)))
        assert all(frame["tab_id"] != original for frame in frames(events))
    finally:
        await engine.view.close()


async def test_popup_during_metadata_restart_does_not_lose_retry_signal():
    engine, _, _, _, events = native_engine(manual=True)
    middle = other_page(engine, "middle")
    other_page(engine, "final")

    async def popup(_viewport):
        engine.current_tab = "final"
        engine.view.changed()

    middle.set_viewport_size = AsyncMock(side_effect=popup)
    try:
        await engine.view.configure({"enabled": True})
        await eventually(lambda: bool(frames(events)))
        engine.current_tab = "middle"
        engine.view.changed()
        await eventually(lambda: any(frame["tab_id"] == "final" for frame in frames(events)))
        assert all(frame["tab_id"] != "middle" for frame in frames(events))
    finally:
        await engine.view.close()


async def test_stale_copy_and_authentication_copy_never_read_page_selection():
    engine, _, _, _, _ = native_engine(manual=True)
    engine.view._copy_selection = AsyncMock(return_value={"ok": True, "text": "Synthetic selection"})
    try:
        await engine.view.configure({"enabled": True})
        old_surface = engine.view.surface_id
        other_page(engine)
        engine.current_tab = "other"
        result = await engine.view.manual("ui_copy", {"surface_id": old_surface})
        assert result["error"]["code"] == "browser_surface_changed"
        engine.view._copy_selection.assert_not_awaited()
        engine.auth_state["auth_pending"] = True
        result = await engine.view.manual("ui_copy", {"surface_id": engine.view.surface_id})
        assert result["error"]["code"] == "browser_private_input"
        engine.view._copy_selection.assert_not_awaited()
    finally:
        await engine.view.close()


async def test_copy_discards_selection_if_scope_changes_during_read():
    engine, _, _, _, _ = native_engine(manual=True)
    other_page(engine)

    async def copied():
        engine.current_tab = "other"
        return {"ok": True, "text": "Synthetic result from a stale view"}

    engine.view._copy_selection = AsyncMock(side_effect=copied)
    try:
        await engine.view.configure({"enabled": True})
        result = await engine.view.manual("ui_copy", {"surface_id": engine.view.surface_id})
        assert result["error"]["code"] == "browser_surface_changed" and "text" not in result
    finally:
        await engine.view.close()


async def test_manual_page_transitions_preserve_pages_and_invalidate_old_input():
    engine, page, desktop, sessions, events = native_engine()
    tab, context = engine.current_tab, engine.context
    try:
        await engine.call("viewer", {"enabled": True})
        await eventually(lambda: any(frame["surface_mode"] == "page" for frame in frames(events)))
        page_surface, page_cdp = engine.view.surface_id, sessions[-1]
        await engine.call("ui_takeover", {})
        await eventually(lambda: any(frame["surface_mode"] == "native" for frame in frames(events)))
        native_surface = engine.view.surface_id
        assert native_surface != page_surface and page_cdp.detached
        result = await engine.call("ui_text", {"text": "Synthetic manual text", "surface_id": page_surface})
        assert result["error"]["code"] == "browser_surface_changed"
        desktop.input.assert_not_awaited()
        assert (await engine.call("ui_text", {"text": "Synthetic manual text", "surface_id": native_surface}))["ok"]
        desktop.input.assert_awaited_once()
        assert (await engine.call("computer_observe", {}))["error"]["code"] == "browser_paused"
        await engine.call("ui_resume", {})
        await eventually(lambda: frames(events)[-1]["surface_mode"] == "page")
        assert engine.page() is page and engine.current_tab == tab and engine.context is context
        assert engine.view.surface_id not in {native_surface, page_surface}
        released = desktop.release_inputs.call_count
        await engine.call("viewer", {"enabled": False})
        count = len(frames(events))
        desktop.mark_dirty()
        await asyncio.sleep(0.15)
        assert len(frames(events)) == count and desktop.release_inputs.call_count > released
        assert engine.page() is page and engine.view.cdp is None and engine.view.surface_id == ""
    finally:
        await engine.view.close()


@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1" or not BrowserDesktop.available(),
                    reason="Explicit isolated Chromium/Xvfb test opt-in")
async def test_real_native_view_preserves_model_observations_tabs_and_manual_input(tmp_path, monkeypatch):
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        pytest.skip("Select an installed browser engine explicitly")
    from kira.tools.browser_auth import BrowserAuthBridge

    # This checks Linux display integration, without invoking any Windows
    # authenticator or introducing an account/credential into the fixture.
    monkeypatch.setattr(BrowserAuthBridge, "prepare", AsyncMock(return_value=False))
    events = []

    async def emit(event):
        events.append(event)

    async def current_frame(engine):
        surface_id = engine.view.surface_id
        await eventually(lambda: any(frame["surface_id"] == surface_id for frame in frames(events)), 5)
        return next(frame for frame in reversed(frames(events)) if frame["surface_id"] == surface_id)

    engine = BrowserEngine(emit=emit)
    try:
        initialized = await engine.call("initialize", {
            "profile_dir": str(tmp_path / "profile"), "artifacts_dir": str(tmp_path / "artifacts"),
            "headed": False, "persistent": False, "embedded": True})
        assert initialized["ok"] and initialized["headed"]
        await engine.context.route("https://native-fixture.example/**", lambda route: route.fulfill(
            content_type="text/html", body='<title>Native fixture</title><input id="entry">'
            '<p>Public synthetic page</p><div style="height:1500px;background:white"></div>'))
        await engine.call("navigate", {"url": "https://native-fixture.example/"})
        await engine.call("viewer", {"enabled": True, "width": 800, "height": 600})
        assert (await current_frame(engine))["surface_mode"] == "page"
        tab, page = engine.current_tab, engine.page()
        await engine.call("ui_takeover", {})
        native_id = engine.view.surface_id
        assert (await current_frame(engine))["surface_mode"] == "native"
        box = await page.locator("#entry").bounding_box()
        for kind in ("press", "release"):
            assert (await engine.call("ui_mouse", {"type": kind,
                "x": int(box["x"] + 10), "y": int(box["y"] + 10),
                "button": "left", "surface_id": native_id}))["ok"]
        assert (await engine.call("ui_text", {"text": "Synthetic è 🦊", "surface_id": native_id}))["ok"]
        assert await page.locator("#entry").input_value() == "Synthetic è 🦊"
        await engine.call("ui_resume", {})
        assert (await current_frame(engine))["surface_mode"] == "page"
        observation = await engine.call("computer_observe", {})
        assert observation["ok"] and observation["viewport"] == {"width": 800, "height": 600}
        await engine.call("ui_takeover", {})
        frame = await current_frame(engine)
        bitmap = QImage.fromData(base64.b64decode(frame["data"]))
        assert bitmap.pixelColor(400, 300).lightness() > 200
        await engine.call("ui_new_tab", {"url": "https://native-fixture.example/second"})
        await current_frame(engine)
        assert engine.current_tab != tab
        await engine.call("ui_select_tab", {"tab_id": tab})
        await current_frame(engine)
        assert engine.page() is page
        await engine.call("viewer", {"enabled": True, "width": 640, "height": 400})
        frame = await current_frame(engine)
        assert (frame["width"], frame["height"]) == (640, 400)
        await engine.call("viewer", {"enabled": False})
        before = len(frames(events))
        await asyncio.sleep(0.15)
        assert len(frames(events)) == before
        await engine.call("viewer", {"enabled": True})
        await current_frame(engine)
        assert len(engine.pages) == 2 and engine.page() is page
        assert not engine.view._surface_error
    finally:
        await engine.close()
