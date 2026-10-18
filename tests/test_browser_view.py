"""Synthetic viewport controls and opt-in, isolated Chromium qualification."""

import asyncio
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kira.tools.browser import MAX_BROWSER_FRAME, MAX_VIEW_IMAGE, BrowserSession, validate_browser_action
from kira.tools.browser_view import BrowserView
from kira.tools.browser_worker import BrowserEngine
from test_browser_engine import FakeHandle, FakeInput, FakePage, engine_with_page


async def eventually(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


class FakeCDP:
    def __init__(self):
        self.events = {}
        self.sent = []
        self.detached = False
        self.history = {"currentIndex": 0, "entries": [{"id": 1}]}

    async def send(self, method, args=None):
        self.sent.append((method, args))
        if method == "Page.captureScreenshot":
            return {"data": "eA=="}
        if method == "Page.getNavigationHistory":
            return self.history
        return {}

    def on(self, name, callback):
        self.events[name] = callback

    async def detach(self):
        self.detached = True


def embedded_engine():
    page = FakePage([FakeHandle()])
    page.set_viewport_size = AsyncMock()
    page.goto = AsyncMock()
    page.go_back = AsyncMock()
    page.go_forward = AsyncMock()
    page.reload = AsyncMock()
    engine = engine_with_page(page)
    cdp = FakeCDP()
    engine.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=cdp))
    events = []

    async def emit(event):
        events.append(event)

    engine.embedded = True
    engine.view = BrowserView(engine, emit)
    return engine, page, cdp, events


@pytest.mark.parametrize("action,arguments", [
    ("viewer", {"enabled": True, "width": 639}),
    ("viewer", {"enabled": True, "height": 1201}),
    ("viewer", {"enabled": "true"}),
    ("ui_mouse", {"type": "script", "x": 0, "y": 0}),
    ("ui_mouse", {"type": "move", "x": 0.5, "y": 1}),
    ("ui_mouse", {"type": "press", "x": 1, "y": 1, "button": "unknown"}),
    ("ui_key", {"type": "down", "key": "a", "modifiers": 16}),
    ("ui_key", {"type": "repeat", "key": "a"}),
    ("ui_text", {"text": "x" * 10001}),
    ("ui_navigate", {"url": "https://example.com", "script": "arbitrary"}),
    ("ui_takeover", {"selector": "password"}),
])
def test_manual_protocol_is_closed_and_bounded(action, arguments):
    with pytest.raises(ValueError):
        validate_browser_action(action, arguments)


async def test_viewer_enables_frames_only_while_visible_and_bounds_coordinates():
    engine, page, cdp, events = embedded_engine()
    assert not events
    try:
        result = await engine.call("viewer", {"enabled": True, "width": 800, "height": 500})
        assert result["ok"]
        page.set_viewport_size.assert_awaited_once_with({"width": 800, "height": 500})
        await eventually(lambda: any(event["event"] == "frame" for event in events))
        frame = next(event["data"] for event in events if event["event"] == "frame")
        assert frame["width"] == 800 and frame["height"] == 500
        assert frame["mime"] == "image/jpeg" and len(frame["data"]) <= MAX_VIEW_IMAGE
        assert (await engine.call("ui_text", {"text": "fixture"}))["error"]["code"] == "browser_not_manual"
        await engine.call("ui_takeover", {})
        with pytest.raises(ValueError, match="viewport"):
            await engine.call("ui_mouse", {"type": "move", "x": 800, "y": 1})
        await engine.call("viewer", {"enabled": False})
        assert cdp.detached
        count = len(events)
        cdp.events["Page.screencastFrame"]({"data": "eA==", "sessionId": 1})
        await asyncio.sleep(0.15)
        assert len(events) == count
    finally:
        await engine.view.close()


async def test_manual_control_invalidates_approval_and_never_returns_typed_values():
    engine, page, cdp, events = embedded_engine()
    private_value = "synthetic-manual-password"
    try:
        snapshot = await engine.call("snapshot", {})
        ref = snapshot["elements"][0]["ref"]
        prepared = await engine.call("prepare_interaction", {"action": "click", "arguments": {"ref": ref}})
        await engine.call("viewer", {"enabled": True})
        assert engine.prepared["token"] == prepared["token"]
        assert (await engine.call("manual_pause", {}))["paused"]
        assert engine.prepared is None and not engine.refs and not page.front
        typed = await engine.call("ui_text", {"text": private_value})
        assert ("Input.insertText", {"text": private_value}) in cdp.sent
        assert private_value not in json.dumps([typed, events])
        for action in ("snapshot", "screenshot", "tabs"):
            assert (await engine.call(action, {}))["error"]["code"] == "browser_paused"
        assert (await engine.call("ui_resume", {}))["manual"] is False
        assert (await engine.call("perform_interaction", {"token": prepared["token"]}))["error"]["code"] == "stale_approval"
        with pytest.raises(ValueError, match="Stale"):
            await engine.call("click", {"ref": ref})
    finally:
        await engine.view.close()


async def test_cdp_pointer_keyboard_and_scroll_are_fixed_protocol_operations():
    engine, _, cdp, _ = embedded_engine()
    try:
        await engine.call("viewer", {"enabled": True})
        await engine.call("ui_takeover", {})
        await engine.call("ui_mouse", {"type": "wheel", "x": 100, "y": 100,
                                      "delta_y": 250, "modifiers": 0})
        assert ("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 100, "y": 100,
                "button": "none", "buttons": 0, "modifiers": 0, "deltaX": 0, "deltaY": 250}) in cdp.sent
        await engine.call("ui_key", {"type": "down", "key": "a", "code": "KeyA", "text": "a", "modifiers": 2})
        key = next(args for name, args in reversed(cdp.sent) if name == "Input.dispatchKeyEvent")
        assert key["type"] == "rawKeyDown" and key["windowsVirtualKeyCode"] == 65
        assert "text" not in key
    finally:
        await engine.view.close()


async def test_frame_burst_is_coalesced_acknowledged_and_large_images_recompressed():
    engine, _, cdp, events = embedded_engine()
    try:
        await engine.call("viewer", {"enabled": True})
        await eventually(lambda: any(event["event"] == "frame" for event in events))
        frames_before = len([event for event in events if event["event"] == "frame"])
        for seq in range(1, 4):
            cdp.events["Page.screencastFrame"]({"sessionId": seq, "data": "x" * (MAX_VIEW_IMAGE + 1)})
        await eventually(lambda: len([event for event in events if event["event"] == "frame"]) > frames_before)
        assert [args["sessionId"] for method, args in cdp.sent if method == "Page.screencastFrameAck"] == [1, 2, 3]
        assert len([event for event in events if event["event"] == "frame"]) == frames_before + 1
        assert any(method == "Page.captureScreenshot" and args["quality"] == 15 for method, args in cdp.sent)
    finally:
        await engine.view.close()


async def test_metadata_updates_for_title_and_history_without_model_snapshot():
    engine, page, cdp, events = embedded_engine()
    try:
        await engine.call("viewer", {"enabled": True})
        page.title = AsyncMock(return_value="Updated fixture")
        page.url = "https://example.com/next"
        cdp.history = {"currentIndex": 1, "entries": [{"id": 1}, {"id": 2}, {"id": 3}]}
        engine.view.changed()
        await eventually(lambda: any(event["event"] == "state" and event["data"]["title"] == "Updated fixture" for event in events))
        state = [event["data"] for event in events if event["event"] == "state"][-1]
        assert state["url"] == page.url and state["can_back"] and state["can_forward"]
        assert "text" not in state and "elements" not in state
    finally:
        await engine.view.close()


async def test_parent_dispatches_interleaved_frames_and_correlated_responses_without_waiting_for_gui(tmp_path):
    stream = asyncio.StreamReader(limit=MAX_BROWSER_FRAME)
    process = SimpleNamespace(pid=12345, returncode=None, stdin=FakeInput(), stdout=stream,
                              wait=AsyncMock(return_value=0))
    entered = asyncio.Event()
    release = asyncio.Event()
    events = []

    async def observer(event):
        events.append(event)
        entered.set()
        await release.wait()

    session = BrowserSession(tmp_path, tmp_path, headed=False, persistent=False,
                             started=AsyncMock(), embedded=True, on_event=observer)
    session._process = process
    task = asyncio.create_task(session.call("snapshot", {}))
    try:
        await eventually(lambda: bool(process.stdin.writes))
        frame = {"event": "frame", "data": {"seq": 1, "tab_id": "tab", "data": "eA==",
                 "mime": "image/jpeg", "width": 800, "height": 500}}
        stream.feed_data((json.dumps(frame) + '\n{"id":1,"result":{"ok":true}}\n').encode())
        assert (await asyncio.wait_for(task, 1))["ok"]
        await entered.wait()
        second = asyncio.create_task(session.call("tabs", {}))
        await eventually(lambda: len(process.stdin.writes) == 2)
        for seq in range(2, 15):
            frame["data"]["seq"] = seq
            stream.feed_data((json.dumps(frame) + "\n").encode())
        stream.feed_data(b'{"id":2,"result":{"ok":true,"tabs":[]}}\n')
        assert (await asyncio.wait_for(second, 1))["tabs"] == []
        release.set()
        await eventually(lambda: len(events) == 2)
        assert events[-1]["data"]["seq"] == 14
    finally:
        release.set()
        await session.close()


async def test_parent_mismatched_response_cannot_be_satisfied_by_an_event(tmp_path):
    stream = asyncio.StreamReader(limit=MAX_BROWSER_FRAME)
    process = SimpleNamespace(pid=12345, returncode=None, stdin=FakeInput(), stdout=stream,
                              wait=AsyncMock(return_value=0))
    session = BrowserSession(tmp_path, tmp_path, headed=False, persistent=False,
                             started=AsyncMock(), embedded=True, on_event=AsyncMock())
    session._process = process
    task = asyncio.create_task(session.call("snapshot", {}))
    await eventually(lambda: bool(process.stdin.writes))
    stream.feed_data(b'{"event":"state","data":{"manual":false}}\n{"id":9,"result":{"ok":true}}\n')
    assert (await task)["error"]["code"] == "browser_unavailable"
    assert process.stdin.closed


@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit browser test opt-in")
async def test_real_embedded_live_view_manual_iframe_input_and_hidden_stream(tmp_path):
    events = []

    async def emit(event):
        events.append(event)

    engine = BrowserEngine(emit=emit)
    try:
        initialized = await engine.call("initialize", {
            "profile_dir": str(tmp_path / "profile"), "artifacts_dir": str(tmp_path / "artifacts"),
            "headed": True, "persistent": False, "embedded": True,
        })
        assert initialized["ok"] and initialized["headed"]
        page = engine.page()
        await engine.context.route("https://embedded-fixture.example/**", lambda route: route.fulfill(
            content_type="text/html", body="""<title>Embedded fixture</title>
            <p>Synthetic page</p><iframe style='width:500px;height:150px' srcdoc=
            "<input placeholder='Private fixture field'><input type=password>"></iframe>
            <div style='height:1600px'></div><button>Footer fixture</button>"""))
        await engine.call("navigate", {"url": "https://embedded-fixture.example/"})
        assert not any(event["event"] == "frame" for event in events)
        await engine.call("viewer", {"enabled": True, "width": 900, "height": 600})
        await eventually(lambda: any(event["event"] == "frame" for event in events), 5)
        first = next(event["data"] for event in events if event["event"] == "frame")
        assert base64.b64decode(first["data"]).startswith(b"\xff\xd8")
        assert len(json.dumps(first)) < MAX_BROWSER_FRAME and len(first["data"]) <= MAX_VIEW_IMAGE
        assert first["width"] == 900 and page.viewport_size == {"width": 900, "height": 600}
        snapshot = await engine.call("snapshot", {})
        old_ref = snapshot["elements"][0]["ref"]
        await engine.call("ui_takeover", {})
        field = page.frames[1].locator("input").first
        box = await field.bounding_box()
        x, y = int(box["x"] + 10), int(box["y"] + 10)
        await engine.call("ui_mouse", {"surface_id": engine.view.surface_id, "type": "press", "x": x, "y": y, "button": "left", "buttons": 1})
        await engine.call("ui_mouse", {"surface_id": engine.view.surface_id, "type": "release", "x": x, "y": y, "button": "left", "buttons": 0})
        await engine.call("ui_text", {"surface_id": engine.view.surface_id, "text": "synthetic-private-entry"})
        assert await field.input_value() == "synthetic-private-entry"
        await field.evaluate("el => el.select()")
        copied = await engine.call("ui_copy", {"surface_id": engine.view.surface_id})
        assert copied == {"ok": True, "text": "synthetic-private-entry", "truncated": False}
        await engine.call("ui_key", {"surface_id": engine.view.surface_id, "type": "down", "key": "a", "code": "KeyA", "modifiers": 2})
        await engine.call("ui_key", {"surface_id": engine.view.surface_id, "type": "up", "key": "a", "code": "KeyA", "modifiers": 2})
        await engine.call("ui_key", {"surface_id": engine.view.surface_id, "type": "down", "key": "Backspace"})
        await engine.call("ui_key", {"surface_id": engine.view.surface_id, "type": "up", "key": "Backspace"})
        assert await field.input_value() == ""
        await engine.call("ui_key", {"surface_id": engine.view.surface_id, "type": "down", "key": "a", "code": "KeyA", "text": "a"})
        await engine.call("ui_key", {"surface_id": engine.view.surface_id, "type": "up", "key": "a", "code": "KeyA"})
        await engine.call("ui_text", {"surface_id": engine.view.surface_id, "text": " synthetic-private-entry"})
        assert await field.input_value() == "a synthetic-private-entry"
        password = page.frames[1].locator("input[type=password]")
        box = await password.bounding_box()
        x, y = int(box["x"] + 8), int(box["y"] + 8)
        await engine.call("ui_mouse", {"surface_id": engine.view.surface_id, "type": "press", "x": x, "y": y, "button": "left", "buttons": 1})
        await engine.call("ui_mouse", {"surface_id": engine.view.surface_id, "type": "release", "x": x, "y": y, "button": "left", "buttons": 0})
        await engine.call("ui_text", {"surface_id": engine.view.surface_id, "text": "synthetic-private-password"})
        assert await password.input_value() == "synthetic-private-password"
        assert (await engine.call("ui_copy", {"surface_id": engine.view.surface_id}))["error"]["code"] == "browser_private_input"
        assert (await engine.call("screenshot", {}))["error"]["code"] == "browser_paused"
        assert (await engine.call("snapshot", {}))["error"]["code"] == "browser_paused"
        await engine.call("ui_resume", {})
        with pytest.raises(ValueError, match="Stale"):
            await engine.call("click", {"ref": old_ref})
        result = await engine.call("snapshot", {})
        assert "synthetic-private-entry" not in json.dumps(result)
        assert "synthetic-private-password" not in json.dumps(result)
        assert "synthetic-private-password" not in json.dumps([event for event in events if event["event"] == "state"])
        assert "synthetic-private-entry" not in json.dumps([event for event in events if event["event"] == "state"])
        screenshot = await engine.call("screenshot", {})
        assert Path(screenshot["screenshot"]["path"]).is_file()
        await page.evaluate("() => {document.title = 'Updated live fixture';}")
        await eventually(lambda: any(event["event"] == "state" and event["data"]["title"] == "Updated live fixture" for event in events), 5)
        await page.evaluate("() => {history.pushState({}, '', '/next');}")
        await eventually(lambda: any(event["event"] == "state" and event["data"]["url"].endswith('/next') and event["data"]["can_back"] for event in events), 5)
        await engine.call("viewer", {"enabled": False})
        frames_before = len([event for event in events if event["event"] == "frame"])
        await page.evaluate("() => {document.body.style.background = 'red'}")
        await asyncio.sleep(0.3)
        assert len([event for event in events if event["event"] == "frame"]) == frames_before
        assert len(list((tmp_path / "artifacts").iterdir())) == 1
    finally:
        await engine.close()


async def test_manual_takeover_can_precede_viewer_subscription_without_allowing_hidden_input():
    engine, _, _, events = embedded_engine()
    try:
        assert (await engine.call("ui_takeover", {}))["manual"]
        assert events[-1]["data"]["manual"]
        assert (await engine.call("ui_text", {"text": "fixture"}))["error"]["code"] == "browser_hidden"
        assert (await engine.call("snapshot", {}))["error"]["code"] == "browser_paused"
        assert not (await engine.call("ui_resume", {}))["manual"]
        assert (await engine.call("manual_pause", {}))["paused"]
    finally:
        await engine.view.close()


@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit browser test opt-in")
async def test_real_worker_streams_interleaved_frames_and_tabs_without_external_window(tmp_path):
    events = []

    async def observe(event):
        events.append(event)

    session = BrowserSession(tmp_path / "profile", tmp_path / "artifacts", headed=True,
                             persistent=False, started=AsyncMock(), embedded=True, on_event=observe)
    try:
        await session.start()
        assert not any(event["event"] == "frame" for event in events)
        assert (await session.call("ui_takeover", {}))["manual"]
        assert (await session.call("viewer", {"enabled": True, "width": 700, "height": 450}))["ok"]
        await eventually(lambda: any(event["event"] == "frame" for event in events), 5)
        initial_tab = next(event["data"]["tab_id"] for event in events if event["event"] == "frame")
        assert (await session.call("ui_new_tab", {}))["ok"]
        await eventually(lambda: any(event["event"] == "state" and len(event["data"]["tabs"]) == 2 for event in events), 5)
        second = [event["data"] for event in events if event["event"] == "state"][-1]["tab_id"]
        assert second != initial_tab
        assert (await session.call("ui_select_tab", {"tab_id": initial_tab}))["ok"]
        assert (await session.call("ui_close_tab", {"tab_id": second}))["ok"]
        assert (await session.call("viewer", {"enabled": False}))["ok"]
        assert not (await session.call("ui_resume", {}))["manual"]
        assert len((await session.call("tabs", {}))["tabs"]) == 1
        assert not list((tmp_path / "artifacts").iterdir())
    finally:
        await session.close()
        assert session._process.returncode == 0


async def test_static_visible_page_has_no_metadata_polling_or_repeated_screenshots():
    engine, page, cdp, events = embedded_engine()
    page.title = AsyncMock(return_value="Static fixture")
    try:
        await engine.call("viewer", {"enabled": True})
        await eventually(lambda: any(event["event"] == "frame" for event in events))
        await asyncio.sleep(0.15)
        title_reads = page.title.await_count
        cdp_calls = list(cdp.sent)
        event_count = len(events)
        await asyncio.sleep(0.7)
        assert page.title.await_count == title_reads
        assert cdp.sent == cdp_calls and len(events) == event_count
        # Even image updates need no duplicate metadata retrieval.
        cdp.events["Page.screencastFrame"]({"sessionId": 1, "data": "eA=="})
        await eventually(lambda: len(events) > event_count)
        assert page.title.await_count == title_reads
        assert len([method for method, _ in cdp.sent if method == "Page.captureScreenshot"]) == 1
    finally:
        await engine.view.close()


async def test_manual_copy_is_private_bounded_and_does_not_publish_selection():
    engine, page, _, events = embedded_engine()
    handle = FakeHandle()
    frame = SimpleNamespace(evaluate=AsyncMock(return_value={"text": "界" * 30000}))
    engine._focused_element = AsyncMock(return_value=(frame, handle))
    try:
        await engine.call("viewer", {"enabled": True})
        assert (await engine.call("ui_copy", {}))["error"]["code"] == "browser_not_manual"
        await engine.call("ui_takeover", {})
        copied = await engine.call("ui_copy", {})
        assert len(copied["text"].encode("utf-8")) <= 48 * 1024
        assert copied["truncated"] and handle.disposed
        assert "界" not in json.dumps(events, ensure_ascii=False)
        frame.evaluate.return_value = {"blocked": True}
        assert (await engine.call("ui_copy", {}))["error"]["code"] == "browser_private_input"
        assert "text" not in (await engine.call("ui_copy", {}))
    finally:
        await engine.view.close()
