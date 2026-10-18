import asyncio
import base64
import ctypes as C
import os
from pathlib import Path
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PySide6.QtGui import QImage
import pytest

from kira.tools import browser_desktop as module
from kira.tools.browser import MAX_VIEW_IMAGE, browser_environment
from kira.tools.browser_desktop import BrowserDesktop, _Attributes, _Event, _Image


def fake_input():
    desktop = BrowserDesktop()
    desktop._connection = 1
    desktop._process = SimpleNamespace(returncode=None)
    desktop._libs = SimpleNamespace(
        x=SimpleNamespace(XFlush=Mock(), XSetSelectionOwner=Mock()),
        test=SimpleNamespace(XTestFakeKeyEvent=Mock(return_value=1),
                             XTestFakeButtonEvent=Mock(return_value=1),
                             XTestFakeMotionEvent=Mock(return_value=1)))
    desktop._modifier_codes = {1: 64, 2: 37, 4: 133, 8: 50}
    desktop._keycode = lambda name: {"v": 55, "a": 38, "Escape": 9}[name]
    keymap = {10: (ord("1"), ord("!")), 11: (ord("2"), ord("@")),
              13: (ord("4"), ord("$")), 38: (ord("a"), ord("A"))}
    desktop._libs.x.XKeysymToKeycode = lambda _connection, symbol: next(
        (code for code, symbols in keymap.items() if symbol in symbols), 0)
    desktop._libs.x.XKeycodeToKeysym = lambda _connection, code, level: keymap[code][level]
    desktop._focus = Mock()
    desktop._read_events = Mock()
    desktop._atoms = {"CLIPBOARD": 10}
    return desktop


@pytest.mark.parametrize("key,mask", [
    ("o", 2), ("l", 2), ("n", 2), ("p", 2), ("s", 2),
    ("i", 10), ("j", 10), ("c", 10), ("Tab", 2), ("a", 10),
    ("F11", 0), ("F12", 0), ("Escape", 1), ("r", 4),
])
async def test_native_chrome_shortcuts_are_blocked_before_input(key, mask):
    desktop = fake_input()
    with pytest.raises(ValueError, match="controls"):
        await desktop.input("ui_key", {"type": "down", "key": key, "modifiers": mask})
    desktop._libs.test.XTestFakeKeyEvent.assert_not_called()


def test_native_editing_keys_remain_available():
    assert all(BrowserDesktop._allowed_key(key, mask) for key, mask in (
        ("Escape", 0), ("Enter", 0), ("Tab", 8), ("ArrowLeft", 2),
        ("Backspace", 2), ("a", 2), ("v", 2), ("z", 10), ("c", 2), ("9", 0)))


@pytest.mark.parametrize("key,text,code,shift", [
    ("@", "@", 11, True), ("!", "!", 10, True),
    ("a", "A", 38, True), ("4", "4", 13, False),
])
async def test_native_printable_key_uses_actual_text_and_matching_keyup(key, text, code, shift):
    desktop = fake_input()
    desktop._paste = AsyncMock()
    await desktop.input("ui_key", {"type": "down", "key": key, "text": text, "modifiers": 0})
    assert code in desktop._keys
    assert (50 in desktop._keys) == shift
    await desktop.input("ui_key", {"type": "up", "key": key, "modifiers": 0})
    assert not desktop._keys and not desktop._forwarded_keys
    assert (1, code, False, 0) in [call.args for call in desktop._libs.test.XTestFakeKeyEvent.call_args_list]
    desktop._paste.assert_not_awaited()


async def test_unmapped_unicode_uses_private_paste_but_shortcuts_do_not():
    desktop = fake_input()
    desktop._paste = AsyncMock()
    await desktop.input("ui_key", {"type": "down", "key": "é", "text": "é"})
    await desktop.input("ui_key", {"type": "up", "key": "é"})
    desktop._paste.assert_awaited_once_with("é")
    assert not desktop._forwarded_keys
    await desktop.input("ui_key", {"type": "down", "key": "a", "text": "@", "modifiers": 2})
    assert desktop._keys == {37, 38}
    desktop._paste.assert_awaited_once()


async def test_held_control_cannot_hide_a_browser_shortcut():
    desktop = fake_input()
    await desktop.input("ui_key", {"type": "down", "key": "Control"})
    assert desktop._keys == {37}
    with pytest.raises(ValueError):
        await desktop.input("ui_key", {"type": "down", "key": "o", "modifiers": 0})
    assert not desktop._keys
    assert desktop._libs.test.XTestFakeKeyEvent.call_args_list[-1].args == (1, 37, False, 0)


async def test_release_inputs_releases_native_keys_and_pointer():
    desktop = fake_input()
    await desktop.input("ui_key", {"type": "down", "key": "Shift"})
    await desktop.input("ui_mouse", {"type": "press", "x": 10, "y": 20,
                                     "button": "left", "modifiers": 8})
    assert desktop._keys == {50} and desktop._buttons == {1}
    desktop.release_inputs()
    assert not desktop._keys and not desktop._buttons
    assert desktop._libs.test.XTestFakeKeyEvent.call_args_list[-1].args == (1, 50, False, 0)
    assert desktop._libs.test.XTestFakeButtonEvent.call_args_list[-1].args == (1, 1, False, 0)


@pytest.mark.parametrize("arguments", [
    {"type": "press", "x": 0, "y": 0, "button": "right"},
    {"type": "press", "x": 0, "y": 0, "button": "middle"},
    {"type": "move", "x": 1280, "y": 0},
    {"type": "move", "x": 0, "y": 800},
    {"type": "wheel", "x": 0, "y": 0, "modifiers": 2, "delta_y": 120},
])
async def test_unsafe_pointer_commands_have_no_native_effect(arguments):
    desktop = fake_input()
    with pytest.raises(ValueError):
        await desktop.input("ui_mouse", arguments)
    desktop._libs.test.XTestFakeMotionEvent.assert_not_called()
    desktop._libs.test.XTestFakeButtonEvent.assert_not_called()


async def test_rejected_pointer_combination_releases_existing_drag():
    desktop = fake_input()
    await desktop.input("ui_mouse", {"type": "press", "x": 10, "y": 10,
                                     "button": "left", "modifiers": 8})
    with pytest.raises(ValueError):
        await desktop.input("ui_mouse", {"type": "press", "x": 10, "y": 10, "button": "right"})
    assert not desktop._keys and not desktop._buttons
    assert desktop._libs.test.XTestFakeButtonEvent.call_args_list[-1].args == (1, 1, False, 0)


async def test_paste_timeout_clears_private_selection_and_modifiers(monkeypatch):
    desktop = fake_input()

    async def timeout(awaitable, _timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(module.asyncio, "wait_for", timeout)
    with pytest.raises(TimeoutError):
        await desktop.input("ui_text", {"text": "Synthetic PIN 1234"})
    assert desktop._clipboard == b"" and not desktop._keys
    assert desktop._libs.x.XSetSelectionOwner.call_args_list[-1].args == (1, 10, 0, 0)


def fake_image(width=1600, height=1200, *, bpp=32):
    desktop = fake_input()
    desktop.width, desktop.height = width, height
    pixels = C.create_string_buffer(random.Random(12).randbytes(width * height * 4))
    image = _Image(width=width, height=height, data=C.cast(pixels, C.c_void_p),
                   byte_order=0, bits_per_pixel=bpp, bytes_per_line=width * 4,
                   red_mask=0xff0000, green_mask=0xff00, blue_mask=0xff)
    desktop._libs.x.XGetImage = Mock(return_value=C.pointer(image))
    desktop._libs.x.XDestroyImage = Mock()
    desktop._libs.damage = SimpleNamespace(XDamageSubtract=Mock())
    return desktop, pixels


def test_native_frame_compresses_to_existing_wire_budget():
    desktop, pixels = fake_image()
    result = desktop.capture_jpeg()
    assert pixels and len(result["data"]) <= MAX_VIEW_IMAGE
    decoded = QImage.fromData(base64.b64decode(result["data"]))
    assert (decoded.width(), decoded.height()) == (1600, 1200)
    desktop._libs.x.XDestroyImage.assert_called_once()


def test_native_capture_releases_ximage_on_bad_format_and_frame_limit(monkeypatch):
    desktop, pixels = fake_image(640, 400, bpp=16)
    with pytest.raises(OSError, match="pixel format"):
        desktop.capture_jpeg()
    assert pixels
    desktop._libs.x.XDestroyImage.assert_called_once()
    desktop, pixels = fake_image(640, 400)
    monkeypatch.setattr(module, "MAX_VIEW_IMAGE", 1)
    with pytest.raises(ValueError, match="frame limit"):
        desktop.capture_jpeg()
    assert pixels
    desktop._libs.x.XDestroyImage.assert_called_once()


async def test_start_validation_and_library_failure_leave_no_private_data(tmp_path, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/Xvfb")
    for width, height in ((True, 400), (639, 400), (1601, 1200), (640, 1201)):
        with pytest.raises(ValueError):
            await BrowserDesktop().start(tmp_path, width, height)
    with pytest.raises(ValueError):
        await BrowserDesktop().start(Path("relative"), 800, 600)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        await BrowserDesktop().start(link, 800, 600)
    monkeypatch.setattr(module, "_libraries", Mock(side_effect=OSError("Unavailable library")))
    with pytest.raises(OSError):
        await BrowserDesktop().start(tmp_path, 800, 600)
    assert list(tmp_path.iterdir()) == [link]


async def test_cancelled_start_stops_owned_child_and_removes_cookie(tmp_path, monkeypatch):
    class Process:
        returncode = None
        terminated = False

        def terminate(self):
            self.terminated = True

        async def wait(self):
            self.returncode = -15
            return self.returncode

    process = Process()
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/Xvfb")
    monkeypatch.setattr(module, "_libraries", lambda: SimpleNamespace())
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    desktop = BrowserDesktop()
    monkeypatch.setattr(desktop, "_connect", lambda: None)
    task = asyncio.create_task(desktop.start(tmp_path, 800, 600))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated and not list(tmp_path.iterdir())
    arguments = spawn.call_args
    assert "-nolisten" in arguments.args and "tcp" in arguments.args
    assert "-ac" not in arguments.args and not arguments.kwargs.get("start_new_session")
    assert not any("TOKEN" in key or "KEY" in key for key in arguments.kwargs["env"])


real_display = pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1" or not BrowserDesktop.available(),
                                  reason="Opt in with KIRA_TEST_BROWSER=1 and installed Xvfb/X11 libraries")


@real_display
async def test_real_displays_are_separate_private_idle_and_cleaned(tmp_path):
    original = {key: os.environ.get(key) for key in ("DISPLAY", "XAUTHORITY")}
    first, second = BrowserDesktop(), BrowserDesktop()
    processes = []
    try:
        environments = [await first.start(tmp_path, 800, 600), await second.start(tmp_path, 640, 400)]
        assert environments[0]["DISPLAY"] != environments[1]["DISPLAY"]
        assert {key: os.environ.get(key) for key in original} == original
        for desktop, environment in zip((first, second), environments, strict=True):
            from kira.tools.browser_desktop import _KEY_NAMES

            assert all(desktop._keycode(name) for name in _KEY_NAMES.values())
            authority = Path(environment["XAUTHORITY"])
            assert authority.stat().st_mode & 0o777 == 0o600
            assert authority.parent.stat().st_mode & 0o777 == 0o700
            process = desktop._process
            processes.append(process)
            assert os.getsid(process.pid) == os.getsid(0)
            command = Path(f"/proc/{process.pid}/cmdline").read_bytes()
            assert b"-nolisten\0tcp" in command and b"-ac\0" not in command
            assert authority.read_bytes()[-16:].hex().encode() not in command
            for _ in range(3):
                desktop.mark_dirty()
                await desktop.wait_changed()
                image = desktop.capture_jpeg()
                await asyncio.sleep(0.02)
            assert len(image["data"]) <= MAX_VIEW_IMAGE
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(desktop.wait_changed(), 0.15)
    finally:
        await first.close()
        await second.close()
    assert all(process.returncode is not None for process in processes)
    assert not list(tmp_path.iterdir())


@real_display
async def test_real_display_loss_wakes_waiters_and_refuses_capture(tmp_path):
    desktop = BrowserDesktop()
    try:
        await desktop.start(tmp_path, 800, 600)
        await desktop.wait_changed()
        desktop.capture_jpeg()
        await asyncio.sleep(0.05)
        desktop._process.kill()
        await desktop._process.wait()
        with pytest.raises(OSError):
            await asyncio.wait_for(desktop.wait_changed(), 1)
        with pytest.raises(OSError):
            desktop.capture_jpeg()
    finally:
        await desktop.close()
    assert not list(tmp_path.iterdir())


@real_display
async def test_real_chromium_native_unicode_input_resize_and_held_keys(tmp_path):
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        pytest.skip("Select an installed browser engine explicitly")
    from playwright.async_api import async_playwright

    desktop, context, playwright = BrowserDesktop(), None, None
    try:
        environment = {**browser_environment(), **await desktop.start(tmp_path / "display", 800, 600)}
        environment.pop("WAYLAND_DISPLAY", None)
        playwright = await async_playwright().start()
        context = await playwright.chromium.launch_persistent_context(
            str(tmp_path / "profile"), headless=False, channel="chromium", chromium_sandbox=True,
            viewport={"width": 800, "height": 600}, env=environment,
            args=["--kiosk", "--ozone-platform=x11"])
        page = context.pages[0]
        await page.set_content('<input id="entry"><textarea id="multi"></textarea><button>Sample</button>')
        await desktop.resize(800, 600)
        await page.locator("#entry").click()
        await desktop.input("ui_text", {"text": "Kira è pronta 🦊 1234"})
        assert await page.locator("#entry").input_value() == "Kira è pronta 🦊 1234"
        await desktop.input("ui_key", {"type": "down", "key": "a", "modifiers": 2})
        await desktop.input("ui_key", {"type": "up", "key": "a", "modifiers": 2})
        await desktop.input("ui_text", {"text": "replacement"})
        assert await page.locator("#entry").input_value() == "replacement"
        await page.locator("#multi").click()
        await desktop.input("ui_text", {"text": "first line\n日本語 Ω"})
        assert await page.locator("#multi").input_value() == "first line\n日本語 Ω"
        assert desktop._clipboard == b"" and desktop._clipboard_target is None
        await page.locator("#entry").fill("")
        await page.locator("#entry").click()
        for key, text in (("@", "@"), ("!", "!"), ("a", "A"), ("4", "4"), ("é", "é"), ("🦊", "🦊")):
            await desktop.input("ui_key", {"type": "down", "key": key, "text": text})
            await desktop.input("ui_key", {"type": "up", "key": key})
        assert await page.locator("#entry").input_value() == "@!A4é🦊"
        await page.locator("#entry").fill("")
        desktop._paste = AsyncMock(wraps=desktop._paste)
        for digit in "1234":
            await desktop.input("ui_key", {"type": "down", "key": digit, "text": digit})
            await desktop.input("ui_key", {"type": "up", "key": digit})
        assert await page.locator("#entry").input_value() == "1234"
        desktop._paste.assert_not_awaited()
        await page.locator("#entry").fill("")
        await desktop.input("ui_key", {"type": "down", "key": "a", "text": "a"})
        await desktop.input("ui_key", {"type": "down", "key": "a", "text": "a"})
        await desktop.input("ui_key", {"type": "up", "key": "a"})
        assert await page.locator("#entry").input_value() == "aa"
        await desktop.input("ui_key", {"type": "down", "key": "Shift"})
        await desktop.input("ui_mouse", {"type": "press", "x": 500, "y": 350,
                                         "button": "left", "modifiers": 8})
        await page.set_viewport_size({"width": 640, "height": 400})
        await desktop.resize(640, 400)
        assert not desktop._keys and not desktop._buttons
        attributes = _Attributes()
        windows = desktop._windows()
        assert windows
        desktop._libs.x.XGetWindowAttributes(desktop._connection, windows[-1], C.byref(attributes))
        assert (attributes.x, attributes.y, attributes.width, attributes.height) == (0, 0, 640, 400)
        # A model-side CDP capture may invalidate the headed backing surface.
        # Re-entering manual view must restore real pixels, even without resize.
        await page.screenshot(type="png")
        await desktop.resize(640, 400)
        await asyncio.sleep(0.1)
        image = desktop.capture_jpeg()
        assert (image["width"], image["height"]) == (640, 400)
        assert len(image["data"]) <= MAX_VIEW_IMAGE
        bitmap = QImage.fromData(base64.b64decode(image["data"]))
        assert bitmap.pixelColor(300, 250).lightness() > 200
    finally:
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
        await desktop.close()


def test_x11_event_union_is_large_enough_for_native_events():
    # This is an ABI boundary, not a serialized protocol owned by Kira.
    assert C.sizeof(_Event) == C.sizeof(C.c_long) * 24
