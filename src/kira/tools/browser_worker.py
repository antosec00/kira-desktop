"""Private subprocess worker for Playwright; never a public command executor."""

import asyncio
import hashlib
import json
import os
import re
import struct
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kira.domain.contracts import Json
from kira.tools.browser import (
    ERROR_PAGE_MESSAGE,
    MAX_BROWSER_FRAME,
    MAX_BROWSER_RESULT,
    BrowserErrorPage,
    browser_error,
    is_error_page,
    validate_browser_action,
)
from kira.tools.browser_view import BrowserView
from kira.tools.computer_worker import ComputerEngine
from kira.tools.web_network import (
    NETWORK_BUDGET_MESSAGE,
    NetworkBudgetExhausted,
    PublicWebError,
    PublicWebProxy,
    validate_url,
)

if TYPE_CHECKING:
    from kira.tools.browser_auth import BrowserAuthBridge
    from kira.tools.browser_desktop import BrowserDesktop

MAX_TABS = 4
MAX_TEXT = 100000
MAX_ELEMENTS = 80
MAX_FRAMES = 8
MAX_SCREENSHOT = 2 * 1024 * 1024
ACTION_TIMEOUT = 30
# Ubuntu sets this to 1 to deny unprivileged user namespaces to programs
# without an AppArmor profile; Chromium's sandbox needs them.
USERNS_RESTRICTION = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
# A timeout in these requests cannot have changed a page or a site.
NO_EFFECT_ACTIONS = frozenset({"initialize", "set_artifacts", "viewer", "snapshot", "find",
                               "screenshot", "tabs", "prepare_interaction", "computer_observe",
                               "computer_prepare"})
# These may have acted on a site; repeating them without inspection is unsafe.
EFFECT_ACTIONS = frozenset({"click", "type", "press", "perform_interaction", "computer_perform",
                            "ui_mouse", "ui_key", "ui_text"})
ELEMENTS_SCRIPT = """() => {
  const all = document.querySelectorAll(
    'a[href],button,input,textarea,select,[role=button],[role=link],[contenteditable=true]');
  const elements = []; let i = 0;
  for (; i < all.length && i < 20000; i++) {
    const el = all[i], r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.right <= 0 ||
        r.top >= innerHeight || r.left >= innerWidth ||
        getComputedStyle(el).visibility === 'hidden') continue;
    if (elements.length >= 80) break;
    elements.push(el);
  }
  return {elements, truncated: i < all.length};
}"""
# Walk visible text nodes only. Form values and editable text never enter the
# transcript, including credentials typed manually into the isolated browser.
TEXT_SCRIPT = """() => {
  const walker = document.createTreeWalker(document.body || document.documentElement,
    NodeFilter.SHOW_TEXT);
  const parts = []; let chars = 0, nodes = 0, node;
  while ((node = walker.nextNode()) && nodes++ < 20000 && chars < 100000) {
    const p = node.parentElement;
    if (!p || p.closest('script,style,noscript,input,textarea,select,[contenteditable]')) continue;
    const s = getComputedStyle(p);
    if (!p.getClientRects().length || s.visibility === 'hidden' || s.display === 'none') continue;
    const text = node.textContent.replace(/\\s+/g, ' ').trim();
    if (text) { parts.push(text); chars += text.length + 1; }
  }
  return {text: parts.join('\\n').slice(0,100000), truncated: !!node};
}"""
ELEMENT_SCRIPT = """el => {
  const type = (el.type || el.getAttribute('type') || '').toLowerCase();
  const tag = el.tagName.toLowerCase();
  const editable = el.isContentEditable || ['input','textarea','select'].includes(tag);
  let labelText = '';
  if (!editable && !['body','html','iframe','frame'].includes(tag)) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll('input,textarea,select,[contenteditable],script,style,noscript')
      .forEach(node => node.remove());
    labelText = clone.textContent || '';
  }
  const label = el.getAttribute('aria-label') || el.getAttribute('title') ||
    (editable ? el.getAttribute('placeholder') : labelText) || '';
  const href = tag === 'a' ? (el.href || '') : '';
  const formAction = el.hasAttribute('formaction') ?
    (el.formAction || el.getAttribute('formaction') || '') : (el.form?.action || '');
  return {tag, type, role: el.getAttribute('role') || '',
    label: label.slice(0,240), label_truncated: label.length > 240,
    href: href.slice(0,2048), href_truncated: href.length > 2048,
    form_action: formAction.slice(0,2048), form_action_truncated: formAction.length > 2048,
    disabled: !!el.disabled, blocked: type === 'password' || type === 'file'};
}"""
FINGERPRINT_SCRIPT = """el => {
  const tag = el.tagName.toLowerCase(), type = (el.type || el.getAttribute('type') || '').toLowerCase();
  const editable = el.isContentEditable || ['input','textarea','select'].includes(tag);
  let labelText = '';
  if (!editable && !['body','html','iframe','frame'].includes(tag)) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll('input,textarea,select,[contenteditable],script,style,noscript')
      .forEach(node => node.remove());
    labelText = clone.textContent || '';
  }
  const facts = {tag, type, role: el.getAttribute('role') || '', disabled: !!el.disabled,
    label: el.getAttribute('aria-label') || el.getAttribute('title') ||
      (editable ? el.getAttribute('placeholder') : labelText) || '',
    href: tag === 'a' ? (el.href || '') : '',
    form_action: el.hasAttribute('formaction') ?
      (el.formAction || el.getAttribute('formaction') || '') : (el.form?.action || ''),
    form_method: el.hasAttribute('formmethod') ? el.formMethod : (el.form?.method || ''),
    form_target: el.hasAttribute('formtarget') ? el.formTarget : (el.form?.target || '')};
  return JSON.stringify(facts).length <= 131072 ? facts : null;
}"""


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Expected integer between {minimum} and {maximum}")
    return value


def playwright_timeout(exc: BaseException) -> bool:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeout
    except ImportError:
        return False
    return isinstance(exc, PlaywrightTimeout)


def _uncertain(result: Json, action: str) -> Json:
    if action not in NO_EFFECT_ACTIONS:
        result["action_outcome"] = "unknown"
        if action in EFFECT_ACTIONS:
            result["retry_safe"] = False
    return result


def request_failure(exc: Exception, action: str, *, deadline_expired: bool) -> tuple[Json, bool]:
    """Public result for a failed worker request and whether the worker must stop.

    Playwright and other bounded waits leave the browser usable. Only Kira's
    whole-request deadline stops the worker: it expires when an operation
    without a deadline of its own (for example a script in a hung renderer)
    stalls, and the browser is then closed instead of queueing work behind it.
    Playwright messages can contain form values or request data, so only
    fixed text leaves the worker.
    """
    if isinstance(exc, BrowserErrorPage):
        return browser_error("browser_error_page", str(exc)), False
    if isinstance(exc, PublicWebError):
        return browser_error("public_web_only", "Only public HTTP port 80 and HTTPS port 443 "
                             "destinations without URL credentials are supported"), False
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return browser_error("invalid_argument", "Invalid browser request or stale element reference"), False
    if isinstance(exc, TimeoutError) and deadline_expired:
        return _uncertain(browser_error(
            "browser_unavailable", "The browser stopped responding within Kira's time limit and "
            "was closed; open the page again and inspect it before continuing"), action), True
    if isinstance(exc, TimeoutError) or playwright_timeout(exc):
        return _uncertain(browser_error(
            "browser_timeout", "The browser operation timed out; take a new snapshot to inspect "
            "the page before retrying"), action), False
    return browser_error("browser_failed", "Browser operation failed; take a new snapshot before retrying"), False


def launch_failure(exc: BaseException, restriction: Path | None = None) -> Json:
    """Describe a failed Chromium start without echoing Playwright's text.

    That text includes the browser log, command line and profile paths. The
    sandbox stays enabled; a sandbox failure is named with its likely cause.
    """
    detail = str(exc).casefold()
    if "sandbox" not in detail and "new namespace" not in detail:
        return browser_error("browser_unavailable", "Chromium could not start; install the browser "
                             "engine for this profile and check the desktop environment")
    try:
        restricted = (restriction or USERNS_RESTRICTION).read_text(encoding="ascii").strip() == "1"
    except (OSError, ValueError):
        restricted = False
    if restricted:
        return browser_error(
            "browser_sandbox_unavailable", "Chromium could not start its sandbox because this "
            "system restricts unprivileged user namespaces through AppArmor "
            "(kernel.apparmor_restrict_unprivileged_userns = 1). Kira keeps the sandbox enabled: "
            "allow user namespaces for Kira's browser engine as described in the installation "
            "guide's troubleshooting, then open the browser again")
    return browser_error(
        "browser_sandbox_unavailable", "Chromium could not start its sandbox because unprivileged "
        "user namespaces are unavailable on this system. Kira keeps the sandbox enabled; see the "
        "installation guide's troubleshooting, then open the browser again")


class BrowserEngine:
    def __init__(self, emit: Callable[[Json], Awaitable[None]] | None = None) -> None:
        self.embedded = False
        self.view: BrowserView | None = None
        self._emit = emit
        self.context: Any = None
        self.playwright: Any = None
        self.proxy = PublicWebProxy()
        self.artifacts_dir = Path()
        self.headed = False
        self.desktop: BrowserDesktop | None = None
        self.auth: BrowserAuthBridge | None = None
        self.auth_manual_ready = False
        self.auth_state: Json = {"auth_pending": False, "auth_needs_manual": False,
                                 "auth_provider": "native", "auth_operation": "",
                                 "auth_origin": "", "auth_result": ""}
        self.paused = False
        self.pages: dict[str, Any] = {}
        self.current_tab = ""
        self.refs: dict[str, Any] = {}
        self.snapshot_id = ""
        self.blocked_requests = 0
        self.prepared: Json | None = None
        self.computer = ComputerEngine(self)

    async def initialize(self, args: Json) -> Json:
        if self.context is not None:
            raise ValueError("Browser already initialized")
        profile = Path(args["profile_dir"])
        self.artifacts_dir = Path(args["artifacts_dir"])
        if not profile.is_absolute() or not self.artifacts_dir.is_absolute():
            raise ValueError("Browser directories must be absolute")
        for directory in (profile, self.artifacts_dir):
            if directory.is_symlink():
                raise ValueError("Browser directory cannot be a symlink")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        self.embedded = args.get("embedded") is True
        self.headed = args.get("headed") is True or self.embedded
        if self.embedded:
            async def discard(event: Json) -> None:
                pass
            self.view = BrowserView(self, self._emit or discard)
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return browser_error("browser_unavailable", "Playwright is not installed")
        await self.proxy.start()
        self.playwright = await async_playwright().start()
        try:
            environment = dict(os.environ)
            launch_args = ["--disable-quic", "--disable-background-networking",
                           "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                           "--proxy-bypass-list=<-loopback>",
                           "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]
            ignored_args: list[str] = []
            if self.embedded:
                from kira.tools.browser_auth import BrowserAuthBridge
                from kira.tools.browser_desktop import BrowserDesktop

                self.desktop = BrowserDesktop()
                native_env = await self.desktop.start(profile / "KiraNative", 1280, 800)
                # Browser UI belongs solely to this private X display. It must
                # not contact the user's Wayland compositor or desktop bus.
                for key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET",
                            "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
                    environment.pop(key, None)
                environment.update(native_env)
                environment["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(profile / "KiraNative/no-host-bus")
                launch_args.extend(["--ozone-platform=x11", "--kiosk",
                                    "--window-position=0,0", "--window-size=1280,800"])
                bridge = BrowserAuthBridge(self._authentication_changed,
                    lambda: self.paused and self.auth_manual_ready)
                if await bridge.prepare(profile):
                    self.auth = bridge
                    launch_args.extend(bridge.launch_args)
                    ignored_args = list(bridge.ignore_default_args)
                else:
                    await bridge.close()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(profile), headless=not self.headed, channel="chromium", chromium_sandbox=True,
                proxy=self.proxy.configuration, accept_downloads=False,
                service_workers="block", ignore_https_errors=False,
                viewport={"width": 1280, "height": 800}, device_scale_factor=1,
                permissions=[], env=environment, args=launch_args,
                ignore_default_args=ignored_args,
            )
            if self.auth is not None:
                if await self.auth.attach(self.context):
                    self.auth_state["auth_provider"] = "windows"
                else:
                    await self.auth.close()
                    self.auth = None
                    self.auth_state["auth_result"] = "unavailable"
        except Exception as exc:
            return launch_failure(exc)
        self.context.set_default_timeout(12000)
        self.context.set_default_navigation_timeout(25000)
        await self.context.route("**/*", self._route)
        self.context.on("page", self._new_page)
        for page in self.context.pages:
            await self._new_page(page)
        if not self.pages:
            await self._new_page(await self.context.new_page())
        return {"ok": True, "headed": self.headed, "max_tabs": MAX_TABS}

    async def _authentication_changed(self, metadata: Json) -> None:
        """Expose only human-facing ceremony state, never WebAuthn material."""
        pending = metadata.get("pending") is True
        operation = metadata.get("operation", "")
        origin = metadata.get("origin", "")
        result = metadata.get("result", "")
        self.auth_state.update(auth_pending=pending,
            auth_needs_manual=pending and not self.auth_manual_ready,
            auth_operation=operation if isinstance(operation, str) and operation in {"create", "get"} else "",
            auth_origin=origin[:512] if isinstance(origin, str) else "",
            auth_result=result if isinstance(result, str) and len(result) <= 64 else "")
        if pending and not self.paused:
            await self._clear_prepared()
            await self.invalidate()
            self.paused = True
        if self._emit is not None:
            await self._emit({"event": "state", "data": {
                "manual": self.paused, **self.auth_state,
            }})
        if self.view is not None:
            self.view.changed()

    async def _route(self, route: Any) -> None:
        try:
            if not self.proxy.budget.request():
                raise NetworkBudgetExhausted("Browser request budget exhausted")
            validate_url(route.request.url)
            previous = route.request.redirected_from
            redirects = 0
            while previous is not None:
                redirects += 1
                if redirects > 10:
                    raise PublicWebError("Too many redirects")
                previous = previous.redirected_from
            await route.continue_()
        except (ValueError, OSError):
            self.blocked_requests += 1
            await route.abort("blockedbyclient")

    async def _new_page(self, page: Any) -> None:
        if any(existing is page for existing in self.pages.values()):
            return
        if len(self.pages) >= MAX_TABS:
            await page.close()
            return
        tab_id = uuid.uuid4().hex[:12]
        self.pages[tab_id] = page
        if not self.current_tab or self.embedded:
            self.current_tab = tab_id
        page.on("download", lambda download: asyncio.create_task(download.cancel()))
        page.on("filechooser", lambda chooser: asyncio.create_task(chooser.set_files([])))
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        page.on("close", lambda: self.pages.pop(tab_id, None))
        await self.computer.attach(page, tab_id)
        if self.view is not None:
            await self.view.watch(tab_id, page)

    def page(self) -> Any:
        if self.current_tab not in self.pages:
            self.current_tab = next(iter(self.pages), "")
        if not self.current_tab:
            raise ValueError("No browser tab remains")
        return self.pages[self.current_tab]

    async def invalidate(self) -> None:
        await self.computer.invalidate()
        old = self.refs
        self.refs = {}
        self.snapshot_id = ""
        for handle in old.values():
            try:
                await handle.dispose()
            except Exception:
                pass

    async def snapshot(self, args: Json) -> Json:
        page = self.page()
        self._check_page(page)
        offset = bounded_int(args.get("offset"), 0, 0, MAX_TEXT)
        limit = bounded_int(args.get("limit"), 16000, 100, 24000)
        await self.invalidate()
        self.snapshot_id = uuid.uuid4().hex[:12]
        extracted = await self._extract_frames(collect_refs=True)
        text = extracted["text"]
        return {"ok": True, "url": page.url, "title": (await page.title())[:512],
                "tab_id": self.current_tab, "snapshot_id": self.snapshot_id,
                "text": text[offset:offset + limit], "offset": offset,
                "next_offset": offset + limit if offset + limit < len(text) else None,
                "truncated": extracted["truncated"] or offset + limit < len(text),
                "elements": extracted["elements"],
                "elements_truncated": extracted["elements_truncated"],
                "frames": extracted["frames"], "frames_omitted": extracted["frames_omitted"],
                "blocked_requests": self.blocked_requests + self.proxy.denied_requests,
                **self._network_notice(), "untrusted_content": True}

    def _network_notice(self) -> Json:
        if not self.proxy.budget.limited:
            return {}
        return {"network_budget": {"code": "network_budget_exhausted",
                                   "message": NETWORK_BUDGET_MESSAGE}}

    async def _extract_frames(self, *, collect_refs: bool) -> Json:
        page = self.page()
        documents = list(page.frames)
        frames: list[Json] = []
        elements: list[Json] = []
        text = ""
        omitted = max(0, len(documents) - MAX_FRAMES)
        truncated = bool(omitted)
        elements_truncated = bool(omitted)
        for index, frame in enumerate(documents[:MAX_FRAMES]):
            frame_id = "main" if index == 0 else f"frame-{index}"
            metadata: Json = {"frame_id": frame_id, "url": frame.url[:2048]}
            try:
                self._check_frame(frame)
                extracted = await frame.evaluate(TEXT_SCRIPT)
                segment = str(extracted["text"])
                if index:
                    segment = f"\n[Frame {frame_id}]\n" + segment
                available = MAX_TEXT - len(text)
                metadata["text_offset"] = len(text)
                text += segment[:available]
                truncated = truncated or bool(extracted["truncated"]) or len(segment) > available
                metadata["text_truncated"] = bool(extracted["truncated"]) or len(segment) > available
                if collect_refs:
                    visible = index == 0 or await self._frame_visible(frame, page)
                    if visible:
                        elements_truncated = await self._frame_elements(
                            frame, frame_id, elements,
                        ) or elements_truncated
                metadata["status"] = "read"
            except Exception:
                if index == 0:
                    raise
                metadata["status"] = "omitted"
                metadata["reason"] = "Frame was blocked, detached or unavailable"
                omitted += 1
                truncated = True
                elements_truncated = True
            frames.append(metadata)
        return {"text": text, "truncated": truncated, "elements": elements,
                "elements_truncated": elements_truncated, "frames": frames,
                "frames_omitted": omitted}

    async def _frame_elements(self, frame: Any, frame_id: str, elements: list[Json]) -> bool:
        element_bytes = 0
        for element in elements:
            element_bytes += len(json.dumps(element, ensure_ascii=True))
        # Fixed script returns bounded actual nodes in the current viewport.
        # Scrolling therefore exposes controls further down long documents.
        extracted_handles = await frame.evaluate_handle(ELEMENTS_SCRIPT)
        array = await extracted_handles.get_property("elements")
        truncation = await extracted_handles.get_property("truncated")
        elements_truncated = await truncation.json_value()
        try:
            for value in (await array.get_properties()).values():
                handle = value.as_element()
                if handle is None:
                    await value.dispose()
                    continue
                if len(elements) >= MAX_ELEMENTS or element_bytes >= 64000:
                    elements_truncated = True
                    await handle.dispose()
                    continue
                metadata = await handle.evaluate(ELEMENT_SCRIPT)
                ref = f"{self.snapshot_id}:{len(elements) + 1}"
                self.refs[ref] = handle
                elements.append({"ref": ref, "frame_id": frame_id, **metadata})
                element_bytes += len(json.dumps(elements[-1], ensure_ascii=True))
        finally:
            await array.dispose()
            await truncation.dispose()
            await extracted_handles.dispose()
        return bool(elements_truncated)

    @staticmethod
    async def _frame_visible(frame: Any, page: Any) -> bool:
        owner = await frame.frame_element()
        try:
            box = await owner.bounding_box()
            viewport = page.viewport_size or {"width": 1280, "height": 800}
            return bool(box and box["width"] > 0 and box["height"] > 0
                        and box["x"] + box["width"] > 0 and box["y"] + box["height"] > 0
                        and box["x"] < viewport["width"] and box["y"] < viewport["height"])
        finally:
            await owner.dispose()

    def _check_page(self, page: Any, *, error_page_allowed: bool = False) -> None:
        """Admit only a public page, or the initial blank page, for reading and input.

        Chromium's error page holds no site content: it is named as such rather
        than as a public-web violation, and manual input may use its controls.
        """
        if page.url == "about:blank":
            return
        if is_error_page(page.url):
            if error_page_allowed:
                return
            notice = self._network_notice().get("network_budget", {}).get("message", "")
            raise BrowserErrorPage(f"{ERROR_PAGE_MESSAGE} {notice}".strip())
        validate_url(page.url)

    @staticmethod
    def _check_frame(frame: Any) -> None:
        # about:blank/srcdoc inherit their embedding page's controlled origin.
        # No local-file, data or blob origin is opened through this exception.
        for _ in range(64):
            if frame.url not in {"about:blank", "about:srcdoc"}:
                if frame.parent_frame is None and is_error_page(frame.url):
                    raise BrowserErrorPage(ERROR_PAGE_MESSAGE)
                validate_url(frame.url)
                return
            if frame.parent_frame is None:
                if frame.url == "about:blank":
                    return
                break
            frame = frame.parent_frame
        raise PublicWebError("Frame does not have a supported public-web origin")

    async def element(self, ref: Any) -> Any:
        if not isinstance(ref, str) or ref not in self.refs:
            raise ValueError("Stale or unknown element reference; take a new snapshot")
        handle = self.refs[ref]
        if not await handle.evaluate("el => el.isConnected && el.ownerDocument === document"):
            raise ValueError("Stale element reference; take a new snapshot")
        return handle

    async def call(self, action: str, args: Json) -> Json:
        validate_browser_action(action, args)
        result = await self._bound_result(await self._call(action, args))
        if (self.view is not None and self.context is not None
                and action not in {"ui_mouse", "ui_key", "ui_text", "ui_copy"}):
            try:
                await self.view.sync()
                await self.view.state()
            except Exception:
                # Rendering follows the operation. Its failure cannot erase an
                # already executed action or invite a duplicate side effect.
                try:
                    await self.view.report_error()
                except Exception:
                    pass
        return result

    async def _bound_result(self, result: Json) -> Json:
        """Keep the complete tool result below the client bridge's 64 KiB limit."""
        def size(value: Any) -> int:
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                                  allow_nan=False).encode("utf-8"))

        while size(result) > MAX_BROWSER_RESULT:
            result["truncated"] = True
            elements = result.get("elements", [])
            text = result.get("text", "")
            if elements and (size(elements) > 24000 or len(text) <= 1024):
                element = elements.pop()
                result["elements_truncated"] = True
                handle = self.refs.pop(element["ref"], None)
                if handle:
                    await handle.dispose()
            elif text and len(text) > 1024:
                excess = size(result) - MAX_BROWSER_RESULT
                target_bytes = max(1024, len(text.encode("utf-8")) - excess - 256)
                reduced = text.encode("utf-8")[:target_bytes].decode("utf-8", "ignore")
                if len(reduced) == len(text):
                    reduced = text[:max(1, len(text) // 2)]
                result["text"] = reduced
                result["next_offset"] = result.get("offset", 0) + len(reduced)
            elif result.get("matches"):
                result["matches"].pop()
            elif result.get("frames"):
                removed = result["frames"].pop()
                result["frames_omitted"] = result.get("frames_omitted", 0) + 1
                for element in list(elements):
                    if element.get("frame_id") == removed["frame_id"]:
                        elements.remove(element)
                        result["elements_truncated"] = True
                        handle = self.refs.pop(element["ref"], None)
                        if handle:
                            await handle.dispose()
            else:
                await self.invalidate()
                return browser_error("browser_limit", "Browser output exceeded the supported size")
        return result

    async def _call(self, action: str, args: Json) -> Json:
        if not action.startswith("computer_") and action not in {"viewer", "set_artifacts"}:
            await self.computer.invalidate()
        if action not in {"perform_interaction", "viewer", "set_artifacts"}:
            await self._clear_prepared()
        if action == "initialize":
            return await self.initialize(args)
        if self.context is None:
            return browser_error("browser_unavailable", "Browser is not initialized")
        if action == "set_artifacts":
            directory = Path(args["path"])
            if not directory.is_absolute() or directory.is_symlink():
                raise ValueError("Invalid browser artifact directory")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
            self.artifacts_dir = directory
            return {"ok": True}
        if action == "viewer" or action.startswith("ui_"):
            if self.view is None:
                return browser_error("browser_unavailable", "Embedded browser is unavailable")
            if action == "viewer":
                return await self.view.configure(args)
            return await self.view.manual(action, args)
        if action == "manual_pause":
            if not self.headed and self.view is None:
                return browser_error("browser_hidden", "Manual login requires a visible browser")
            await self._clear_prepared()
            await self.invalidate()
            self.paused = True
            # A model-requested handoff pauses automation; only an explicit
            # desktop takeover may authorize a native authenticator prompt.
            if self.headed:
                await self.page().bring_to_front()
            return {"ok": True, "paused": True}
        if action == "resume":
            if self.auth_state.get("auth_pending"):
                return browser_error("browser_auth_pending", "Complete or cancel the authentication request before returning control to Kira")
            self.paused = False
            self.auth_manual_ready = False
            if self.auth is not None:
                await self.auth.set_manual(False)
            await self.invalidate()
            try:
                return {**await self.snapshot({}), "paused": False}
            except BrowserErrorPage as exc:
                # Control returned to Kira even though the tab has no readable page.
                return {"ok": True, "paused": False, "tab_id": self.current_tab,
                        "url": self.page().url[:2048],
                        "page_error": {"code": "browser_error_page", "message": str(exc)}}
        if self.paused:
            return {**browser_error("browser_paused", "Browser awaits manual login completion"),
                    **({"action_executed": False} if action.startswith("computer_") else {})}
        if action == "computer_observe":
            return await self.computer.observe()
        if action == "computer_prepare":
            return await self.computer.prepare(args)
        if action == "computer_perform":
            return await self.computer.perform(args["token"])
        if action == "prepare_interaction":
            return await self._prepare_interaction(args)
        if action == "perform_interaction":
            return await self._perform_interaction(args)
        if action not in {"navigate", "back", "tabs", "select_tab", "close_tab"}:
            # Leaving or replacing the page reads nothing from it; destinations
            # pass validate_url, the request route and the proxy.
            self._check_page(self.page())
        if action == "snapshot":
            return await self.snapshot(args)
        if action == "navigate":
            url = args.get("url")
            if not isinstance(url, str):
                raise ValueError("Navigation requires a URL")
            validate_url(url)
            await self.invalidate()
            self.proxy.budget.reset()
            response = await self.page().goto(url, wait_until="domcontentloaded")
            result = await self.snapshot({})
            result["status"] = response.status if response else None
            return result
        if action == "find":
            needle = args.get("text")
            if not isinstance(needle, str) or not needle or len(needle) > 256:
                raise ValueError("Find text must contain 1 to 256 characters")
            extracted = await self._extract_frames(collect_refs=False)
            text = str(extracted["text"])
            matches = []
            for match in re.finditer(re.escape(needle), text, re.IGNORECASE):
                matches.append({"offset": match.start(),
                                "text": text[max(0, match.start() - 120):match.end() + 120]})
                if len(matches) >= 30:
                    break
            return {"ok": True, "url": self.page().url, "matches": matches,
                    "truncated": bool(extracted["truncated"]) or len(matches) == 30,
                    "frames": extracted["frames"], "frames_omitted": extracted["frames_omitted"],
                    "untrusted_content": True}
        if action in {"click", "type"}:
            handle = await self.element(args.get("ref"))
            metadata = await handle.evaluate(ELEMENT_SCRIPT)
            if metadata["blocked"]:
                raise ValueError("Password and file inputs require manual user interaction")
            if action == "type":
                typed_text = args.get("text")
                if not isinstance(typed_text, str) or len(typed_text) > 10000 or "\0" in typed_text:
                    raise ValueError("Typed text must be a bounded string")
                await handle.fill(typed_text)
            else:
                if metadata["href"]:
                    validate_url(metadata["href"])
                await handle.click()
            return await self.snapshot({})
        if action == "press":
            key = args.get("key")
            if key not in {"Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft",
                           "ArrowRight", "PageUp", "PageDown", "Home", "End", "Backspace"}:
                raise ValueError("Unsupported browser key")
            frames = list(self.page().frames)
            if len(frames) > 64:
                return browser_error("browser_limit", "Too many frames to check keyboard focus safely")
            for frame in frames:
                self._check_frame(frame)
                blocked = await frame.evaluate(
                    "() => ['password','file'].includes((document.activeElement?.type || '').toLowerCase())"
                )
                if blocked:
                    raise ValueError("Password and file inputs require manual user interaction")
            await self.page().keyboard.press(key)
            return await self.snapshot({})
        if action == "scroll":
            direction = args.get("direction", "down")
            if direction not in {"up", "down", "left", "right"}:
                raise ValueError("Invalid scroll direction")
            amount = bounded_int(args.get("amount"), 600, 1, 2400)
            x = amount * (1 if direction == "right" else -1) if direction in {"left", "right"} else 0
            y = amount * (1 if direction == "down" else -1) if direction in {"up", "down"} else 0
            await self.page().mouse.wheel(x, y)
            await self.page().evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
            return await self.snapshot({})
        if action == "screenshot":
            page = self.page()
            frames = list(page.frames)
            if len(frames) > 64:
                return browser_error("browser_limit", "Screenshot cannot safely mask more than 64 frames")
            for frame in frames:
                self._check_frame(frame)
            data = await page.screenshot(type="png", full_page=False, timeout=12000,
                                         mask=[frame.locator("input,textarea,select,[contenteditable]")
                                               for frame in frames])
            if len(data) > MAX_SCREENSHOT:
                return browser_error("browser_limit", "Viewport PNG exceeds the 2 MiB image limit")
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("Invalid browser screenshot")
            width, height = struct.unpack(">II", data[16:24])
            path = self.artifacts_dir / f"browser-{uuid.uuid4().hex}.png"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
            return {"ok": True, "url": page.url, "screenshot": {"path": str(path),
                    "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                    "mime": "image/png", "width": width, "height": height}}
        if action == "tabs":
            return {"ok": True, "tabs": [{"tab_id": tab_id, "url": page.url,
                    "title": (await page.title())[:512], "active": tab_id == self.current_tab}
                    for tab_id, page in list(self.pages.items())]}
        if action in {"select_tab", "close_tab"}:
            tab_id = args.get("tab_id")
            if not isinstance(tab_id, str) or tab_id not in self.pages:
                raise ValueError("Unknown browser tab")
            await self.invalidate()
            if action == "select_tab":
                self.current_tab = tab_id
                await self.page().bring_to_front()
                return await self.snapshot({})
            if len(self.pages) == 1:
                raise ValueError("Cannot close the last browser tab")
            page = self.pages.pop(tab_id)
            await page.close()
            self.page()
            return {"ok": True, "closed_tab": tab_id}
        if action == "back":
            await self.invalidate()
            self.proxy.budget.reset()
            await self.page().go_back(wait_until="domcontentloaded")
            return await self.snapshot({})
        raise ValueError("Unknown browser action")

    async def _clear_prepared(self) -> None:
        prepared, self.prepared = self.prepared, None
        if prepared and prepared["action"] == "press":
            try:
                await prepared["handle"].dispose()
            except Exception:
                pass

    async def _focused_element(self) -> tuple[Any, Any]:
        frame = self.page().main_frame
        for _ in range(MAX_FRAMES):
            self._check_frame(frame)
            value = await frame.evaluate_handle("() => document.activeElement")
            handle = value.as_element()
            if handle is None:
                await value.dispose()
                raise ValueError("No focused browser element")
            metadata = await handle.evaluate(ELEMENT_SCRIPT)
            if metadata["tag"] not in {"iframe", "frame"}:
                return frame, handle
            focused_frame = None
            for child in list(frame.child_frames)[:64]:
                owner = await child.frame_element()
                try:
                    if await owner.evaluate("el => el === document.activeElement"):
                        focused_frame = child
                        break
                finally:
                    await owner.dispose()
            await handle.dispose()
            if focused_frame is None:
                raise ValueError("Focused frame is unavailable")
            frame = focused_frame
        raise ValueError("Browser focus exceeds the supported frame depth")

    async def _prepare_interaction(self, args: Json) -> Json:
        self._check_page(self.page())
        action, arguments = args["action"], args["arguments"]
        if action == "press":
            _, handle = await self._focused_element()
        else:
            handle = await self.element(arguments["ref"])
        metadata = await handle.evaluate(ELEMENT_SCRIPT)
        if metadata["blocked"]:
            if action == "press":
                await handle.dispose()
            raise ValueError("Password and file inputs require manual user interaction")
        if action == "click" and metadata["href"]:
            validate_url(metadata["href"])
        try:
            fingerprint = await self._target_fingerprint(handle)
        except Exception:
            if action == "press":
                await handle.dispose()
            raise
        token = uuid.uuid4().hex
        self.prepared = {"token": token, "action": action, "args": dict(arguments),
                         "tab_id": self.current_tab, "url": self.page().url,
                         "target": metadata, "handle": handle,
                         "fingerprint": fingerprint}
        return {"ok": True, "token": token, "target": metadata, "url": self.page().url}

    @staticmethod
    async def _target_fingerprint(handle: Any) -> str:
        facts = await handle.evaluate(FINGERPRINT_SCRIPT)
        if not isinstance(facts, dict):
            raise ValueError("Browser target cannot be safely described within the supported bounds")
        return hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode("utf-8")).hexdigest()

    async def _perform_interaction(self, args: Json) -> Json:
        prepared = self.prepared
        if not prepared or args["token"] != prepared["token"]:
            await self._clear_prepared()
            return browser_error("stale_approval", "Browser approval expired; prepare the action again")
        self.prepared = None
        executing = False
        try:
            if self.current_tab != prepared["tab_id"] or self.page().url != prepared["url"]:
                raise ValueError("Browser tab changed during approval")
            self._check_page(self.page())
            handle = prepared["handle"]
            if not await handle.evaluate("el => el.isConnected && el.ownerDocument === document"):
                raise ValueError("Browser target changed during approval")
            if await handle.evaluate(ELEMENT_SCRIPT) != prepared["target"]:
                raise ValueError("Browser target changed during approval")
            if await self._target_fingerprint(handle) != prepared["fingerprint"]:
                raise ValueError("Browser target changed during approval")
            if prepared["action"] == "press":
                _, current = await self._focused_element()
                try:
                    if not await handle.evaluate("(el, current) => el === current", current):
                        raise ValueError("Browser keyboard focus changed during approval")
                finally:
                    await current.dispose()
            executing = True
            return await self._call(prepared["action"], prepared["args"])
        except Exception:
            if executing:
                raise
            return browser_error("stale_approval", "Browser target changed; inspect it and request approval again")
        finally:
            if prepared["action"] == "press":
                try:
                    await prepared["handle"].dispose()
                except Exception:
                    pass

    async def close(self) -> None:
        try:
            await self.computer.invalidate()
        finally:
            try:
                if self.view is not None:
                    await self.view.close()
            finally:
                try:
                    if self.auth is not None:
                        await self.auth.close()
                finally:
                    try:
                        if self.context:
                            await self.context.close()
                    finally:
                        try:
                            if self.playwright:
                                await self.playwright.stop()
                        finally:
                            try:
                                if self.desktop is not None:
                                    await self.desktop.close()
                            finally:
                                await self.proxy.close()


async def serve() -> None:
    reader = asyncio.StreamReader(limit=MAX_BROWSER_FRAME)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer,
    )
    loop = asyncio.get_running_loop()
    output_transport, output_protocol = await loop.connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(loop=loop), sys.stdout.buffer,
    )
    writer = asyncio.StreamWriter(output_transport, output_protocol, None, loop)
    output_lock = asyncio.Lock()

    async def send(message: Json) -> None:
        frame = json.dumps(message, ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
        if len(frame) > MAX_BROWSER_FRAME:
            raise ValueError("Browser output exceeds its frame limit")
        async with output_lock:
            writer.write(frame)
            await writer.drain()

    engine = BrowserEngine(emit=send)
    initialized = False
    try:
        while True:
            try:
                raw = await reader.readline()
            except ValueError:
                break
            if not raw or not raw.endswith(b"\n") or len(raw) > MAX_BROWSER_FRAME:
                break
            request: Json = {}
            fatal = False
            deadline: asyncio.Timeout | None = None
            try:
                request = json.loads(raw)
                if (not isinstance(request, dict) or type(request.get("id")) is not int
                        or not isinstance(request.get("action"), str)
                        or not isinstance(request.get("args"), dict)):
                    break
                action = request["action"]
                if not initialized and action != "initialize":
                    break
                if initialized and action == "initialize":
                    break
                initialized = True
                deadline = asyncio.timeout(ACTION_TIMEOUT)
                async with deadline:
                    result = await engine.call(action, request["args"])
            except Exception as exc:
                result, fatal = request_failure(exc, str(request.get("action", "")),
                                                deadline_expired=deadline is not None and deadline.expired())
            await send({"id": request.get("id"), "result": result})
            if fatal:
                break
    finally:
        transport.close()
        try:
            await asyncio.wait_for(engine.close(), 3)
        except Exception:
            pass
        writer.close()


if __name__ == "__main__":
    asyncio.run(serve())
