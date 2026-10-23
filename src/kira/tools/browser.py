"""Bounded browser-worker protocol; process ownership stays in the service."""

import asyncio
import importlib.util
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from kira.domain.computer import validate_computer_arguments
from kira.domain.contracts import Json

MAX_BROWSER_FRAME = 1024 * 1024
MAX_VIEW_IMAGE = 500 * 1024
BROWSER_TIMEOUT = 45.0
MAX_BROWSER_RESULT = 60 * 1024 - 1024
# Chromium commits this document after a navigation fails to load.
ERROR_PAGE_PREFIX = "chrome-error://"
ERROR_PAGE_MESSAGE = (
    "The tab shows Chromium's error page because a navigation failed (the address was blocked, "
    "unreachable or invalid); it has no page content. Navigate to another address, go back or "
    "select another tab.")


class BrowserErrorPage(ValueError):
    """The current tab shows Chromium's own page for a failed navigation."""


class BrowserStartError(RuntimeError):
    """The worker could not initialize; the message is fixed public text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_error_page(url: str) -> bool:
    return url.startswith(ERROR_PAGE_PREFIX)


def validate_browser_action(action: str, args: Json) -> None:
    """Closed argument contracts apply before sending and again in the worker."""
    if action in {"computer_observe", "computer_prepare"}:
        validate_computer_arguments("observe" if action == "computer_observe" else "act", args)
        return
    fields: dict[str, tuple[set[str], set[str]]] = {
        "computer_perform": ({"token"}, set()),
        "initialize": ({"profile_dir", "artifacts_dir", "headed", "persistent"}, {"embedded"}),
        "set_artifacts": ({"path"}, set()),
        "viewer": ({"enabled"}, {"width", "height"}),
        "ui_mouse": ({"type", "x", "y"}, {"button", "buttons", "delta_x", "delta_y", "modifiers", "surface_id"}),
        "ui_key": ({"type", "key"}, {"code", "text", "modifiers", "surface_id"}),
        "ui_text": ({"text"}, {"surface_id"}), "ui_copy": (set(), {"surface_id"}), "ui_navigate": ({"url"}, set()),
        "ui_back": (set(), set()), "ui_forward": (set(), set()),
        "ui_reload": (set(), set()), "ui_new_tab": (set(), {"url"}),
        "ui_select_tab": ({"tab_id"}, set()), "ui_close_tab": ({"tab_id"}, set()),
        "ui_takeover": (set(), set()), "ui_resume": (set(), set()),
        "navigate": ({"url"}, set()), "snapshot": (set(), {"offset", "limit"}),
        "find": ({"text"}, set()), "click": ({"ref"}, set()),
        "type": ({"ref", "text"}, set()), "press": ({"key"}, set()),
        "scroll": ({"direction"}, {"amount"}), "select_tab": ({"tab_id"}, set()),
        "close_tab": ({"tab_id"}, set()), "screenshot": (set(), set()),
        "tabs": (set(), set()), "back": (set(), set()), "manual_pause": (set(), set()),
        "resume": (set(), set()),
        "prepare_interaction": ({"action", "arguments"}, set()),
        "perform_interaction": ({"token"}, set()),
    }
    if not isinstance(action, str) or action not in fields or not isinstance(args, dict):
        raise ValueError("Invalid browser action")
    required, optional = fields[action]
    if not required <= args.keys() or args.keys() - required - optional:
        raise ValueError("Missing or unsupported browser arguments")
    if action == "prepare_interaction":
        nested_action = args["action"]
        if not isinstance(nested_action, str) or nested_action not in {"click", "type", "press"}:
            raise ValueError("Only supported interactions can be prepared")
        validate_browser_action(nested_action, args["arguments"])
        return
    for key, value in args.items():
        if key in {"headed", "persistent", "embedded", "enabled"}:
            if type(value) is not bool:
                raise ValueError("Browser settings must be booleans")
        elif key in {"width", "height", "x", "y", "delta_x", "delta_y", "buttons", "modifiers"}:
            bounds = {"width": (640, 1600), "height": (400, 1200), "x": (0, 1600),
                      "y": (0, 1200), "delta_x": (-2400, 2400), "delta_y": (-2400, 2400),
                      "buttons": (0, 31), "modifiers": (0, 15)}
            low, high = bounds[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Browser input is outside its bound")
        elif key in {"offset", "limit", "amount"}:
            low, high = {"offset": (0, 100000), "limit": (100, 24000), "amount": (1, 2400)}[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Browser numeric argument is outside its bound")
        else:
            maximum = {"url": 8192, "text": 10000 if action in {"type", "ui_text"} else 256,
                       "profile_dir": 4096, "artifacts_dir": 4096, "path": 4096,
                       "surface_id": 64}.get(key, 128)
            if (not isinstance(value, str) or len(value) > maximum or "\0" in value
                    or not value and not (action in {"type", "ui_text", "ui_key"} and key == "text")):
                raise ValueError("Browser string argument is outside its bound")
    if action == "ui_mouse":
        if args["type"] not in {"move", "press", "release", "wheel"}:
            raise ValueError("Unsupported pointer event")
        if args.get("button", "none") not in {"none", "left", "middle", "right", "back", "forward"}:
            raise ValueError("Unsupported pointer button")
    if action == "ui_key" and args["type"] not in {"down", "up"}:
        raise ValueError("Unsupported keyboard event")
    # Reject nonfinite values and invalid Unicode before anything reaches a tool.
    json.dumps(args, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if action == "press" and args["key"] not in {
        "Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "PageUp", "PageDown", "Home", "End", "Backspace",
    }:
        raise ValueError("Unsupported browser key")
    if action == "scroll" and args["direction"] not in {"up", "down", "left", "right"}:
        raise ValueError("Unsupported scroll direction")


def engine_available(browser_path: Path | None = None) -> bool:
    """Inspect the pinned Chromium installation without launching or downloading."""
    spec = importlib.util.find_spec("playwright")
    if spec is None or not spec.origin:
        return False
    try:
        manifest = json.loads((Path(spec.origin).parent / "driver/package/browsers.json").read_text())
        revision = next(item["revision"] for item in manifest["browsers"]
                        if item["name"] == "chromium")
        root = browser_path or Path(os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH",
            str(Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ms-playwright"),
        ))
        return any(os.access(root / f"chromium-{revision}" / relative, os.X_OK)
                   for relative in ("chrome-linux/chrome", "chrome-linux64/chrome"))
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        return False


def browser_environment(browser_path: Path | None = None) -> dict[str, str]:
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "USER", "LOGNAME",
               "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
               "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "LD_LIBRARY_PATH",
               "WSL_INTEROP", "WSL_DISTRO_NAME",
               "PLAYWRIGHT_BROWSERS_PATH"}
    result = {key: value for key, value in os.environ.items() if key in allowed}
    result["PYTHONUNBUFFERED"] = "1"
    if browser_path is not None:
        result["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_path)
    return result


def browser_error(code: str, message: str) -> Json:
    return {"ok": False, "error": {"code": code, "message": message}}


class BrowserSession:
    def __init__(self, profile_dir: Path, artifacts_dir: Path, *, headed: bool,
                 persistent: bool, started: Callable[[int], Awaitable[None]],
                 browser_path: Path | None = None, embedded: bool = False,
                 on_event: Callable[[Json], Awaitable[None]] | None = None) -> None:
        self.embedded = embedded
        self.on_event = on_event
        self._reader_task: asyncio.Task[None] | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._reader_error: Exception | None = None
        self._pending: dict[int, asyncio.Future[Json]] = {}
        self._events: dict[str, Json] = {}
        self._event_ready = asyncio.Event()
        self.profile_dir = profile_dir
        self.artifacts_dir = artifacts_dir
        self.headed = headed
        self.persistent = persistent
        self.started = started
        self.browser_path = browser_path
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Browser session is closed")
        if self._process is not None:
            return
        package_root = str(Path(__file__).resolve().parents[2])
        bootstrap = ("import runpy,sys;sys.path.insert(0," + repr(package_root)
                     + ");runpy.run_module('kira.tools.browser_worker',run_name='__main__')")
        self._process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", bootstrap, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=browser_environment(self.browser_path),
            limit=MAX_BROWSER_FRAME,
        )
        try:
            # The worker reads stdin before importing Playwright or creating any
            # descendant. Register durable ownership before allowing initialization.
            await self.started(self._process.pid)
            result = await self.call("initialize", {
                "profile_dir": str(self.profile_dir), "artifacts_dir": str(self.artifacts_dir),
                "headed": self.headed, "persistent": self.persistent,
                **({"embedded": True} if self.embedded else {}),
            })
            if not result.get("ok"):
                error = result.get("error", {})
                raise BrowserStartError(str(error.get("code", "browser_unavailable")),
                                        str(error.get("message", "Browser initialization failed")))
        except BaseException:
            await self.close()
            raise

    async def call(self, action: str, args: Json) -> Json:
        return await self._call(action, args, None)

    async def call_guarded(self, action: str, args: Json,
                           before_dispatch: Callable[[], Json | None]) -> Json:
        """Recheck service admission after queuing, immediately before dispatch.

        The synchronous callback must not await a user decision while this lock
        is held. A returned result rejects the exchange without closing the
        independently owned browser or treating the action as uncertain.
        """
        return await self._call(action, args, before_dispatch)

    async def _call(self, action: str, args: Json,
                    before_dispatch: Callable[[], Json | None] | None) -> Json:
        try:
            validate_browser_action(action, args)
        except (ValueError, TypeError, UnicodeError):
            return browser_error("invalid_argument", "Invalid browser arguments")
        if self._closed or self._process is None or self._process.returncode is not None:
            return browser_error("browser_closed", "The browser session is closed")
        async with self._lock:
            if self._closed or self._process is None or self._process.returncode is not None:
                return browser_error("browser_closed", "The browser session is closed")
            if before_dispatch is not None:
                blocked = before_dispatch()
                if blocked is not None:
                    if not isinstance(blocked, dict):
                        raise TypeError("Browser dispatch admission must return an object or None")
                    return blocked
            try:
                async with asyncio.timeout(BROWSER_TIMEOUT):
                    return await self._exchange(action, args)
            except asyncio.CancelledError:
                await self.close()
                raise
            except (TimeoutError, OSError, ValueError, asyncio.IncompleteReadError):
                await self.close()
                result = browser_error("browser_unavailable", "Browser operation failed or timed out")
                if action == "computer_perform":
                    result.update(action_outcome="unknown", retry_safe=False)
                return result

    async def _exchange(self, action: str, args: Json) -> Json:
        assert self._process is not None
        assert self._process.stdin is not None and self._process.stdout is not None
        self._request_id += 1
        frame = json.dumps({"id": self._request_id, "action": action, "args": args},
                           ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
        if len(frame) > MAX_BROWSER_FRAME:
            return browser_error("invalid_argument", "Browser request exceeds the frame limit")
        if self._reader_error is not None:
            raise self._reader_error
        future: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self._pending[self._request_id] = future
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_messages())
        try:
            self._process.stdin.write(frame)
            await self._process.stdin.drain()
            return await future
        finally:
            self._pending.pop(self._request_id, None)

    async def _read_messages(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while not self._closed:
                raw = await self._process.stdout.readline()
                if not raw.endswith(b"\n") or len(raw) > MAX_BROWSER_FRAME:
                    raise ValueError("Invalid browser response frame")
                response = json.loads(raw)
                if not isinstance(response, dict):
                    raise ValueError("Invalid browser response")
                if "event" in response:
                    self._queue_event(response)
                    continue
                request_id = response.get("id")
                if (type(request_id) is not int or request_id not in self._pending
                        or not isinstance(response.get("result"), dict)):
                    raise ValueError("Invalid browser response identity")
                result = response["result"]
                if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"),
                                  allow_nan=False).encode("utf-8")) > MAX_BROWSER_RESULT:
                    raise ValueError("Browser result exceeds the client-tool bound")
                future = self._pending[request_id]
                if future.done():
                    raise ValueError("Duplicate browser response")
                future.set_result(result)
        except (OSError, ValueError, asyncio.IncompleteReadError) as exc:
            self._reader_error = exc
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)

    def _queue_event(self, event: Json) -> None:
        kind, data = event.get("event"), event.get("data")
        if kind not in {"frame", "state"} or not isinstance(data, dict):
            raise ValueError("Invalid browser event")
        if kind == "frame":
            if (not isinstance(data.get("data"), str) or len(data["data"]) > MAX_VIEW_IMAGE
                    or data.get("mime") != "image/jpeg"
                    or type(data.get("seq")) is not int
                    or not isinstance(data.get("tab_id"), str)
                    or type(data.get("width")) is not int or not 640 <= data["width"] <= 1600
                    or type(data.get("height")) is not int or not 400 <= data["height"] <= 1200):
                raise ValueError("Invalid browser image event")
        if self.embedded and self.on_event is not None:
            self._events[kind] = event
            self._event_ready.set()
            if self._event_task is None:
                self._event_task = asyncio.create_task(self._deliver_events())

    async def _deliver_events(self) -> None:
        assert self.on_event is not None
        while not self._closed:
            await self._event_ready.wait()
            self._event_ready.clear()
            # Keep at most the latest image and state while the GUI is slow.
            pending, self._events = self._events, {}
            for event in pending.values():
                try:
                    await self.on_event(event)
                except Exception:
                    # A disconnected observer cannot corrupt tool responses.
                    pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in (self._reader_task, self._event_task):
            if task and task is not asyncio.current_task():
                task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._events.clear()
        process = self._process
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        # EOF requests orderly browser shutdown. The service's ProcessRegistry
        # owns escalation and descendant cleanup; this layer never signals PIDs.
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            pass
