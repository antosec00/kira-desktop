"""Transient Chromium viewport and fixed manual controls; no model-visible frames."""

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from kira.domain.contracts import Json
from kira.tools.browser import MAX_VIEW_IMAGE, browser_error
from kira.tools.web_network import validate_url

if TYPE_CHECKING:
    from kira.tools.browser_worker import BrowserEngine

# DOM key names to Chromium's Windows virtual key codes (platform-independent CDP).
KEY_CODES = {
    "Backspace": 8, "Tab": 9, "Enter": 13, "Shift": 16, "Control": 17,
    "Alt": 18, "Pause": 19, "CapsLock": 20, "Escape": 27, " ": 32,
    "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36,
    "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
    "Insert": 45, "Delete": 46, "Meta": 91, "ContextMenu": 93,
    "NumLock": 144, "ScrollLock": 145,
    **{f"F{index}": 111 + index for index in range(1, 25)},
}
PHYSICAL_KEY_CODES = {
    **{f"Key{letter}": ord(letter) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{f"Digit{number}": 48 + number for number in range(10)},
    **{f"Numpad{number}": 96 + number for number in range(10)},
    "Space": 32, "Semicolon": 186, "Equal": 187, "Comma": 188, "Minus": 189,
    "Period": 190, "Slash": 191, "Backquote": 192, "BracketLeft": 219,
    "Backslash": 220, "BracketRight": 221, "Quote": 222,
    "NumpadMultiply": 106, "NumpadAdd": 107, "NumpadSubtract": 109,
    "NumpadDecimal": 110, "NumpadDivide": 111, "NumpadEnter": 13,
}
# Manual controls that leave or replace the visible page.
LEAVING_ACTIONS = frozenset({"ui_navigate", "ui_back", "ui_forward", "ui_reload", "ui_new_tab",
                             "ui_select_tab", "ui_close_tab"})
# Stable identifier of the rendering failure, for translation by the interface.
VIEW_UNAVAILABLE = "view_unavailable"


class BrowserView:
    """One visible tab, one pending image, and an explicit manual-control gate."""

    def __init__(self, engine: "BrowserEngine", emit: Callable[[Json], Awaitable[None]]) -> None:
        self.engine = engine
        self.emit = emit
        self.enabled = False
        self.width = 1280
        self.height = 800
        self.cdp: Any = None
        self.tab_id = ""
        self.surface_id = ""
        self.surface_mode = "page"
        self._surface_scope: tuple[str, int, int, str] | None = None
        self._surface_error = ""
        self._surface_error_code = ""
        self.seq = 0
        self._latest: Json | None = None
        self._ready = asyncio.Event()
        self._dirty = asyncio.Event()
        self._frames: asyncio.Task[None] | None = None
        self._metadata: asyncio.Task[None] | None = None
        self._last_state: Json | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._loading: dict[str, bool] = {}

    def changed(self, *_: Any) -> None:
        self._dirty.set()

    async def watch(self, tab_id: str, page: Any) -> None:
        changed = self.changed

        def started(request: Any) -> None:
            if request.is_navigation_request() and request.frame is page.main_frame:
                self._loading[tab_id] = True
                changed()

        def loaded(*_: Any) -> None:
            self._loading[tab_id] = False
            changed()

        def failed(request: Any) -> None:
            if request.is_navigation_request():
                loaded()

        def closed(*_: Any) -> None:
            self._loading.pop(tab_id, None)
            changed()

        page.on("request", started)
        page.on("requestfailed", failed)
        page.on("framenavigated", changed)
        page.on("domcontentloaded", loaded)
        page.on("load", loaded)
        page.on("close", closed)
        changed()
        # A fixed observer sends only a dirty signal. Title strings are read by
        # the bounded metadata path, and no page value becomes a binding arg.
        binding = "__kiraViewMetadata_" + tab_id
        script = """(() => {
          if (window !== window.top) return;
          const notify = () => { void window[BINDING]().catch(() => {}); };
          const observer = new MutationObserver(records => {
            if (records.some(record => record.target.nodeName === 'TITLE' ||
                record.target.parentNode?.nodeName === 'TITLE' ||
                [...record.addedNodes, ...record.removedNodes].some(node =>
                  node.nodeName === 'TITLE' || node.nodeName === 'HEAD'))) notify();
          });
          const watch = () => {
            observer.disconnect();
            if (document.head) observer.observe(document.head,
              {subtree:true, childList:true, characterData:true});
          };
          watch();
          if (document.readyState === 'loading')
            document.addEventListener('DOMContentLoaded', watch, {once:true});
        })()""".replace("BINDING", json.dumps(binding))
        try:
            await page.expose_binding(binding, lambda _source, *_args: changed())
            await page.add_init_script(script)
            await page.evaluate(script)
        except Exception:
            # A closing/navigating page still has its normal lifecycle signals.
            pass

    async def configure(self, args: Json) -> Json:
        enabled = args["enabled"]
        width, height = args.get("width", self.width), args.get("height", self.height)
        resize = (width, height) != (self.width, self.height)
        self.width, self.height = width, height
        self.enabled = enabled
        if not enabled:
            await self._stop_stream()
            return {"ok": True, "visible": False, "manual": self.engine.paused}
        if resize:
            await self.engine.computer.invalidate()
            await self._stop_stream()
        await self.sync()
        return {"ok": True, "visible": True, "manual": self.engine.paused,
                "width": self.width, "height": self.height}

    async def sync(self) -> None:
        if not self.enabled or self._closed:
            return
        async with self._lock:
            page = self.engine.page()
            desktop = getattr(self.engine, "desktop", None)
            mode = "native" if desktop is not None and self.engine.paused else "page"
            scope = (self.engine.current_tab, self.width, self.height, mode)
            if (self._surface_scope != scope or self.cdp is None
                    or self._frames is not None and self._frames.done()):
                await self._stop_stream()
                if not self.enabled or self._closed:
                    return
                cdp = None
                try:
                    await page.set_viewport_size({"width": scope[1], "height": scope[2]})
                    cdp = await self.engine.context.new_cdp_session(page)
                    if desktop is not None:
                        await page.bring_to_front()
                        window = await cdp.send("Browser.getWindowForTarget")
                        await cdp.send("Browser.setWindowBounds", {
                            "windowId": window["windowId"], "bounds": {"windowState": "fullscreen"},
                        })
                        # Keep native dialogs aligned after viewport emulation,
                        # and repaint when entering manual view at the same size.
                        await desktop.resize(scope[1], scope[2])
                    if (not self.enabled or self._closed or scope != self._current_scope()):
                        await cdp.detach()
                        # A popup/resize can win while startup awaits Chromium.
                        # Never label that candidate's pixels as the new tab.
                        self.changed()
                    else:
                        self.tab_id, self.surface_mode = scope[0], mode
                        self.surface_id = uuid.uuid4().hex
                        self._surface_scope, self.cdp = scope, cdp
                        surface_id = self.surface_id
                        if mode == "native":
                            assert desktop is not None
                            self._frames = asyncio.create_task(self._pump_native(surface_id, self.tab_id))
                            desktop.mark_dirty()
                        else:
                            cdp.on("Page.screencastFrame", lambda frame: self._received(frame, cdp, surface_id))
                            await cdp.send("Page.enable")
                            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60,
                                           "maxWidth": scope[1], "maxHeight": scope[2],
                                           "everyNthFrame": 1})
                            capture = await cdp.send("Page.captureScreenshot", {
                                "format": "jpeg", "quality": 55, "fromSurface": True,
                                "captureBeyondViewport": False,
                            })
                            self._frames = asyncio.create_task(self._pump_frames(cdp, self.tab_id, surface_id))
                            self._received(capture, cdp, surface_id)
                except BaseException as exc:
                    attached = self.cdp is cdp and cdp is not None
                    await self._stop_stream()
                    if cdp is not None and not attached:
                        with contextlib.suppress(Exception):
                            await cdp.detach()
                    if not isinstance(exc, asyncio.CancelledError):
                        await self.report_error()
                    raise
            if self._metadata is None:
                self._metadata = asyncio.create_task(self._update_state())

    def _current_scope(self) -> tuple[str, int, int, str]:
        mode = "native" if getattr(self.engine, "desktop", None) is not None and self.engine.paused else "page"
        return self.engine.current_tab, self.width, self.height, mode

    def _current_surface(self, surface_id: str) -> bool:
        return bool(surface_id and self.surface_id == surface_id
                    and self._surface_scope == self._current_scope())

    async def report_error(self) -> None:
        """Keep rendering failure visible without replacing a tool's outcome."""
        self._surface_error = (
            "The complete browser view is unavailable. Close and reopen the browser after preserving your work.")
        self._surface_error_code = VIEW_UNAVAILABLE
        if not self._closed:
            await self.emit({"event": "state", "data": {
                "surface_error": self._surface_error, "surface_error_code": self._surface_error_code,
                "manual": self.engine.paused,
            }})

    async def _frame_ready(self, surface_id: str) -> None:
        if self._surface_error and self._current_surface(surface_id):
            self._surface_error = ""
            self._surface_error_code = ""
            await self.emit({"event": "state", "data": {
                "surface_error": "", "surface_error_code": "", "manual": self.engine.paused,
            }})

    def _received(self, frame: Json, cdp: Any, surface_id: str) -> None:
        if (not self.enabled or self._closed or self.surface_mode != "page"
                or self.cdp is not cdp or not self._current_surface(surface_id)):
            return
        # Chromium allows only a bounded number of unacknowledged screencast
        # frames. Keep their IDs with the single newest image until the pump acks.
        previous = self._latest
        acknowledgements = previous.get("acks", []) if previous else []
        if "sessionId" in frame:
            acknowledgements = [*acknowledgements, frame["sessionId"]]
        self._latest = {"data": frame.get("data", ""), "acks": acknowledgements[-4:]}
        self._ready.set()

    async def _pump_frames(self, cdp: Any, tab_id: str, surface_id: str) -> None:
        last_sent = 0.0
        loop = asyncio.get_running_loop()
        try:
            while self.enabled and self.cdp is cdp and self._current_surface(surface_id):
                await self._ready.wait()
                await asyncio.sleep(max(0, 0.1 - (loop.time() - last_sent)))
                self._ready.clear()
                frame, self._latest = self._latest, None
                if frame is None:
                    continue
                for session_id in frame["acks"]:
                    await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                data = frame["data"]
                if not isinstance(data, str) or not data:
                    continue
                if len(data) > MAX_VIEW_IMAGE:
                    compressed = await cdp.send("Page.captureScreenshot", {
                        "format": "jpeg", "quality": 15, "fromSurface": True,
                        "captureBeyondViewport": False,
                    })
                    data = compressed.get("data", "")
                if not data or len(data) > MAX_VIEW_IMAGE:
                    continue
                if not self.enabled or self.cdp is not cdp or not self._current_surface(surface_id):
                    break
                self.seq += 1
                await self.emit({"event": "frame", "data": {
                    "seq": self.seq, "tab_id": tab_id, "data": data,
                    "mime": "image/jpeg", "width": self.width, "height": self.height,
                    "surface_id": surface_id, "surface_mode": "page",
                }})
                await self._frame_ready(surface_id)
                last_sent = loop.time()
        except Exception:
            if not self._closed and self.cdp is cdp and self._current_surface(surface_id):
                await self.report_error()

    async def _pump_native(self, surface_id: str, tab_id: str) -> None:
        """Human-only browser pixels, including native authentication dialogs."""
        desktop = self.engine.desktop
        assert desktop is not None
        loop = asyncio.get_running_loop()
        last_sent = loop.time()
        try:
            while (self.enabled and not self._closed and self.engine.paused
                   and self._current_surface(surface_id)):
                await desktop.wait_changed()
                await asyncio.sleep(max(0, .1 - (loop.time() - last_sent)))
                if (not self.enabled or not self.engine.paused or self._closed
                        or not self._current_surface(surface_id)):
                    break
                frame = desktop.capture_jpeg()
                if not frame or len(frame.get("data", "")) > MAX_VIEW_IMAGE:
                    continue
                self.seq += 1
                await self.emit({"event": "frame", "data": {
                    **frame, "seq": self.seq, "tab_id": tab_id,
                    "surface_id": surface_id, "surface_mode": "native",
                }})
                await self._frame_ready(surface_id)
                last_sent = loop.time()
        except Exception:
            # A native rendering failure is visible and must never fall back
            # silently to a page-only image during a private login ceremony.
            if not self._closed and self._current_surface(surface_id):
                await self.report_error()

    async def _update_state(self) -> None:
        try:
            while self.enabled and not self._closed:
                await self._dirty.wait()
                # Coalesce an actual burst of page events, never poll an idle page.
                await asyncio.sleep(0.1)
                self._dirty.clear()
                await self.sync()
                if self.enabled and self._surface_scope != self._current_scope():
                    # Startup discarded a superseded tab/geometry candidate.
                    # state() consumes the dirty flag, so retry before calling
                    # it or this metadata task could leave the view blank.
                    self.changed()
                    continue
                await self.state()
        except Exception:
            if not self._closed and self.enabled:
                await self.report_error()
        finally:
            if self._metadata is asyncio.current_task():
                self._metadata = None

    async def state(self) -> None:
        if not self.engine.embedded or self._closed:
            return
        self._dirty.clear()
        tabs = []
        for tab_id, page in list(self.engine.pages.items()):
            try:
                tabs.append({"tab_id": tab_id, "title": (await page.title())[:512],
                             "url": page.url[:8192]})
            except Exception:
                continue
        selected = next((tab for tab in tabs if tab["tab_id"] == self.engine.current_tab), {})
        back, forward = False, False
        if self.cdp is not None and self.tab_id == self.engine.current_tab:
            try:
                history = await self.cdp.send("Page.getNavigationHistory")
                index = history.get("currentIndex", 0)
                back = index > 0
                forward = index + 1 < len(history.get("entries", []))
            except Exception:
                pass
        state = {"tabs": tabs, "tab_id": self.engine.current_tab,
                 "url": selected.get("url", ""), "title": selected.get("title", ""),
                 "loading": self._loading.get(self.engine.current_tab, False),
                 "can_back": back, "can_forward": forward, "manual": self.engine.paused,
                 "surface_mode": self.surface_mode, "surface_id": self.surface_id,
                 "surface_error": self._surface_error, "surface_error_code": self._surface_error_code,
                 **getattr(self.engine, "auth_state", {})}
        if state != self._last_state:
            self._last_state = state
            await self.emit({"event": "state", "data": state})

    async def manual(self, action: str, args: Json) -> Json:
        if not self.enabled and action not in {"ui_takeover", "ui_resume"}:
            return browser_error("browser_hidden", "Open the embedded browser before interacting")
        if action == "ui_takeover":
            await self.engine._clear_prepared()
            await self.engine.invalidate()
            self.engine.paused = True
            self.engine.auth_manual_ready = True
            bridge = getattr(self.engine, "auth", None)
            if bridge is not None:
                await bridge.set_manual(True)
        elif action == "ui_resume":
            if getattr(self.engine, "auth_state", {}).get("auth_pending"):
                return browser_error("browser_auth_pending", "Complete or cancel the authentication request before returning control to Kira")
            await self.engine._clear_prepared()
            await self.engine.invalidate()
            self.engine.paused = False
            self.engine.auth_manual_ready = False
            bridge = getattr(self.engine, "auth", None)
            if bridge is not None:
                await bridge.set_manual(False)
        else:
            if not self.engine.paused:
                return browser_error("browser_not_manual", "Take manual control before interacting")
            desktop = getattr(self.engine, "desktop", None)
            if action in {"ui_mouse", "ui_key", "ui_text", "ui_copy"}:
                await self.sync()
                if desktop is not None and (self.surface_mode != "native"
                        or not self._current_surface(args.get("surface_id", ""))):
                    return browser_error("browser_surface_changed", "The browser view changed; wait for its current image before interacting")
            if action == "ui_copy":
                if getattr(self.engine, "auth_state", {}).get("auth_pending"):
                    return browser_error("browser_private_input", "Authentication controls cannot be copied")
                scope, surface_id = self._current_scope(), self.surface_id
                result = await self._copy_selection()
                if (scope != self._current_scope() or surface_id != self.surface_id
                        or getattr(self.engine, "auth_state", {}).get("auth_pending")):
                    return browser_error("browser_surface_changed", "The browser view changed; wait for its current image before interacting")
                return result
            await self.engine._clear_prepared()
            await self.engine.invalidate()
            await self._input(action, args)
        return {"ok": True, "manual": self.engine.paused}

    async def _copy_selection(self) -> Json:
        frame, handle = await self.engine._focused_element()
        try:
            selected = await frame.evaluate("""() => {
              const el = document.activeElement;
              if (['password','file'].includes((el?.type || '').toLowerCase()))
                return {blocked:true};
              const text = el && ['INPUT','TEXTAREA'].includes(el.tagName) &&
                typeof el.selectionStart === 'number'
                ? el.value.slice(el.selectionStart, el.selectionEnd)
                : (window.getSelection()?.toString() || '');
              return {text:text.slice(0,65536), truncated:text.length > 65536};
            }""")
        finally:
            await handle.dispose()
        if selected.get("blocked"):
            return browser_error("browser_private_input", "Password and file inputs cannot be copied")
        encoded = selected.get("text", "").encode("utf-8")
        bounded = encoded[:48 * 1024].decode("utf-8", "ignore")
        return {"ok": True, "text": bounded,
                "truncated": bool(selected.get("truncated")) or len(encoded) > 48 * 1024}

    async def _input(self, action: str, args: Json) -> None:
        await self.sync()
        page = self.engine.page()
        if action not in LEAVING_ACTIONS:
            # Leaving or replacing the page reads nothing from it; destinations
            # pass validate_url, the request route and the proxy. Input may use
            # the controls of Chromium's error page, which holds no site content.
            self.engine._check_page(page, error_page_allowed=True)
        desktop = getattr(self.engine, "desktop", None)
        if desktop is not None and action in {"ui_mouse", "ui_key", "ui_text"}:
            # Recheck after sync, which may switch tabs or resize the surface.
            if not self._current_surface(args.get("surface_id", "")) or self.surface_mode != "native":
                raise ValueError("The native browser surface changed")
            await desktop.input(action, args)
            return
        if action == "ui_mouse":
            if args["x"] >= self.width or args["y"] >= self.height:
                raise ValueError("Pointer is outside the viewport")
            event = {"type": {"move": "mouseMoved", "press": "mousePressed",
                              "release": "mouseReleased", "wheel": "mouseWheel"}[args["type"]],
                     "x": args["x"], "y": args["y"], "button": args.get("button", "none"),
                     "buttons": args.get("buttons", 0), "modifiers": args.get("modifiers", 0)}
            if args["type"] in {"press", "release"}:
                event["clickCount"] = 1
            if args["type"] == "wheel":
                event.update(deltaX=args.get("delta_x", 0), deltaY=args.get("delta_y", 0))
            await self.cdp.send("Input.dispatchMouseEvent", event)
        elif action == "ui_key":
            key, text = args["key"], args.get("text", "")
            modifiers = args.get("modifiers", 0)
            # Ctrl/Alt/Meta shortcuts must never insert the accompanying text.
            if modifiers & 7:
                text = ""
            virtual = PHYSICAL_KEY_CODES.get(args.get("code", ""), KEY_CODES.get(
                key, ord(key.upper()) if len(key) == 1 and key.isascii() and key.isalnum() else 0,
            ))
            event = {"type": "keyUp" if args["type"] == "up" else
                     ("keyDown" if text else "rawKeyDown"), "key": key,
                     "code": args.get("code", ""), "modifiers": modifiers,
                     "windowsVirtualKeyCode": virtual, "nativeVirtualKeyCode": virtual}
            if args["type"] == "down" and text:
                event.update(text=text, unmodifiedText=text)
            await self.cdp.send("Input.dispatchKeyEvent", event)
        elif action == "ui_text":
            await self.cdp.send("Input.insertText", {"text": args["text"]})
        elif action == "ui_navigate":
            validate_url(args["url"])
            self.engine.proxy.budget.reset()
            await page.goto(args["url"], wait_until="domcontentloaded")
        elif action == "ui_back":
            self.engine.proxy.budget.reset()
            await page.go_back(wait_until="domcontentloaded")
        elif action == "ui_forward":
            self.engine.proxy.budget.reset()
            await page.go_forward(wait_until="domcontentloaded")
        elif action == "ui_reload":
            self.engine.proxy.budget.reset()
            await page.reload(wait_until="domcontentloaded")
        elif action == "ui_new_tab":
            from kira.tools.browser_worker import MAX_TABS
            if len(self.engine.pages) >= MAX_TABS:
                raise ValueError("Browser tab limit reached")
            url = args.get("url")
            if url:
                validate_url(url)
            new_page = await self.engine.context.new_page()
            await self.engine._new_page(new_page)
            self.engine.current_tab = next(tab for tab, candidate in self.engine.pages.items()
                                           if candidate is new_page)
            if url:
                self.engine.proxy.budget.reset()
                await new_page.goto(url, wait_until="domcontentloaded")
        elif action in {"ui_select_tab", "ui_close_tab"}:
            tab_id = args["tab_id"]
            if tab_id not in self.engine.pages:
                raise ValueError("Unknown browser tab")
            if action == "ui_select_tab":
                self.engine.current_tab = tab_id
            else:
                if len(self.engine.pages) == 1:
                    raise ValueError("Cannot close the last browser tab")
                closing = self.engine.pages.pop(tab_id)
                await closing.close()
                self.engine.page()
        else:
            raise ValueError("Unsupported manual control")

    async def _stop_stream(self) -> None:
        desktop = getattr(self.engine, "desktop", None)
        if desktop is not None:
            desktop.release_inputs()
        for name in ("_frames", "_metadata"):
            task = getattr(self, name)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                setattr(self, name, None)
        cdp, self.cdp = self.cdp, None
        self.tab_id = ""
        self._surface_scope = None
        self.surface_id = ""
        self.surface_mode = "page"
        self._latest = None
        self._ready.clear()
        if cdp is not None:
            with contextlib.suppress(Exception):
                await cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                await cdp.detach()

    async def close(self) -> None:
        self.enabled = False
        self._closed = True
        await self._stop_stream()
