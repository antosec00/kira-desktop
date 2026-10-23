"""An owned X11 display for manual interaction with Chromium's native UI.

The display is private to one browser worker. Its pixels and clipboard never
touch the user's desktop, model observations, files, or durable service events.
"""

import asyncio
import base64
import contextlib
import ctypes as C
import ctypes.util
import functools
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import tempfile
from typing import Any, ClassVar

from kira.domain.contracts import Json
from kira.tools.browser import MAX_VIEW_IMAGE, validate_browser_action

MAX_WIDTH, MAX_HEIGHT = 1600, 1200
_MODIFIERS = {1: "Alt_L", 2: "Control_L", 4: "Super_L", 8: "Shift_L"}
_MODIFIER_NAMES = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
_KEY_NAMES = {"Enter": "Return", "Backspace": "BackSpace", "ArrowLeft": "Left", "ArrowRight": "Right",
              "ArrowUp": "Up", "ArrowDown": "Down", "PageUp": "Prior",
              "PageDown": "Next", " ": "space", "Space": "space",
              **{name: name for name in ("Escape", "Delete", "Tab",
                                         "Home", "End", "Insert")},
              **{name: _MODIFIERS[bit] for name, bit in _MODIFIER_NAMES.items()}}


class _Image(C.Structure):
    _fields_: ClassVar[list[Any]] = [(name, C.c_int) for name in ("width", "height", "xoffset", "format")] + [
        ("data", C.c_void_p),
        *[(name, C.c_int) for name in ("byte_order", "bitmap_unit", "bitmap_bit_order",
                                      "bitmap_pad", "depth", "bytes_per_line", "bits_per_pixel")],
        ("red_mask", C.c_ulong), ("green_mask", C.c_ulong), ("blue_mask", C.c_ulong),
    ]


class _Attributes(C.Structure):
    _fields_ = [
        *[(name, C.c_int) for name in ("x", "y", "width", "height", "border_width", "depth")],
        ("visual", C.c_void_p), ("root", C.c_ulong),
        *[(name, C.c_int) for name in ("window_class", "bit_gravity", "win_gravity", "backing_store")],
        ("backing_planes", C.c_ulong), ("backing_pixel", C.c_ulong), ("save_under", C.c_int),
        ("colormap", C.c_ulong), ("map_installed", C.c_int), ("map_state", C.c_int),
        ("all_event_masks", C.c_long), ("your_event_mask", C.c_long),
        ("do_not_propagate_mask", C.c_long), ("override_redirect", C.c_int),
        ("screen", C.c_void_p),
    ]


class _SelectionRequest(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), *[(name, C.c_ulong) for name in
                ("owner", "requestor", "selection", "target", "property", "time")]]


class _Selection(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), *[(name, C.c_ulong) for name in
                ("requestor", "selection", "target", "property", "time")]]


class _Property(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), ("window", C.c_ulong), ("atom", C.c_ulong),
                ("time", C.c_ulong), ("state", C.c_int)]


class _Event(C.Union):
    _fields_ = [("type", C.c_int), ("request", _SelectionRequest),
                ("selection", _Selection), ("property", _Property), ("pad", C.c_long * 24)]


_ERROR_HANDLER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
_IO_HANDLER = C.CFUNCTYPE(C.c_int, C.c_void_p)
_IO_EXIT_HANDLER = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)


class _X11:
    """Small typed Xlib surface; connections remain on the asyncio owner thread."""

    def __init__(self) -> None:
        self.x = C.CDLL(self._library("X11"))
        self.test = C.CDLL(self._library("Xtst"))
        self.damage = C.CDLL(self._library("Xdamage"))
        pointer, window = C.c_void_p, C.c_ulong
        self._bind(self.x, "XOpenDisplay", [C.c_char_p], pointer)
        self._bind(self.x, "XCloseDisplay", [pointer], C.c_int)
        self._bind(self.x, "XDefaultRootWindow", [pointer], window)
        self._bind(self.x, "XConnectionNumber", [pointer], C.c_int)
        self._bind(self.x, "XPending", [pointer], C.c_int)
        self._bind(self.x, "XNextEvent", [pointer, C.POINTER(_Event)], C.c_int)
        self._bind(self.x, "XFlush", [pointer], C.c_int)
        self._bind(self.x, "XSync", [pointer, C.c_int], C.c_int)
        self._bind(self.x, "XSelectInput", [pointer, window, C.c_long], C.c_int)
        self._bind(self.x, "XGetImage", [pointer, window, C.c_int, C.c_int, C.c_uint,
                                       C.c_uint, C.c_ulong, C.c_int], C.POINTER(_Image))
        self._bind(self.x, "XDestroyImage", [C.POINTER(_Image)], C.c_int)
        self._bind(self.x, "XQueryTree", [pointer, window, C.POINTER(window), C.POINTER(window),
                                        C.POINTER(C.POINTER(window)), C.POINTER(C.c_uint)], C.c_int)
        self._bind(self.x, "XGetWindowAttributes", [pointer, window, C.POINTER(_Attributes)], C.c_int)
        self._bind(self.x, "XMoveResizeWindow", [pointer, window, C.c_int, C.c_int,
                                               C.c_uint, C.c_uint], C.c_int)
        self._bind(self.x, "XClearArea", [pointer, window, C.c_int, C.c_int, C.c_uint,
                                        C.c_uint, C.c_int], C.c_int)
        self._bind(self.x, "XGetInputFocus", [pointer, C.POINTER(window), C.POINTER(C.c_int)], C.c_int)
        self._bind(self.x, "XSetInputFocus", [pointer, window, C.c_int, C.c_ulong], C.c_int)
        self._bind(self.x, "XStringToKeysym", [C.c_char_p], C.c_ulong)
        self._bind(self.x, "XKeysymToKeycode", [pointer, C.c_ulong], C.c_ubyte)
        self._bind(self.x, "XKeycodeToKeysym", [pointer, C.c_ubyte, C.c_int], C.c_ulong)
        self._bind(self.x, "XAutoRepeatOff", [pointer], C.c_int)
        self._bind(self.x, "XInternAtom", [pointer, C.c_char_p, C.c_int], C.c_ulong)
        self._bind(self.x, "XGetWindowProperty", [pointer, window, window, C.c_long, C.c_long,
            C.c_int, window, C.POINTER(window), C.POINTER(C.c_int), C.POINTER(C.c_ulong),
            C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)], C.c_int)
        self._bind(self.x, "XFree", [C.c_void_p], C.c_int)
        self._bind(self.x, "XCreateSimpleWindow", [pointer, window, C.c_int, C.c_int,
            C.c_uint, C.c_uint, C.c_uint, C.c_ulong, C.c_ulong], window)
        self._bind(self.x, "XDestroyWindow", [pointer, window], C.c_int)
        self._bind(self.x, "XSetSelectionOwner", [pointer, window, window, C.c_ulong], C.c_int)
        self._bind(self.x, "XGetSelectionOwner", [pointer, window], window)
        self._bind(self.x, "XChangeProperty", [pointer, window, window, window, C.c_int,
                                              C.c_int, C.c_void_p, C.c_int], C.c_int)
        self._bind(self.x, "XDeleteProperty", [pointer, window, window], C.c_int)
        self._bind(self.x, "XSendEvent", [pointer, window, C.c_int, C.c_long,
                                         C.POINTER(_Event)], C.c_int)
        self._bind(self.x, "XSetErrorHandler", [C.c_void_p], C.c_void_p)
        self._bind(self.x, "XSetIOErrorHandler", [C.c_void_p], C.c_void_p)
        self._bind(self.x, "XSetIOErrorExitHandler", [pointer, C.c_void_p, C.c_void_p], None)
        self._bind(self.test, "XTestQueryExtension", [pointer, *[C.POINTER(C.c_int)] * 4], C.c_int)
        self._bind(self.test, "XTestFakeKeyEvent", [pointer, C.c_uint, C.c_int, C.c_ulong], C.c_int)
        self._bind(self.test, "XTestFakeButtonEvent", [pointer, C.c_uint, C.c_int, C.c_ulong], C.c_int)
        self._bind(self.test, "XTestFakeMotionEvent", [pointer, C.c_int, C.c_int,
                                                     C.c_int, C.c_ulong], C.c_int)
        self._bind(self.damage, "XDamageQueryExtension", [pointer, *[C.POINTER(C.c_int)] * 2], C.c_int)
        self._bind(self.damage, "XDamageCreate", [pointer, window, C.c_int], window)
        self._bind(self.damage, "XDamageSubtract", [pointer, window, window, window], None)
        self._bind(self.damage, "XDamageDestroy", [pointer, window], None)
        self.connections: set[int] = set()
        self._previous_handler: Any = None
        self._previous_io_handler: Any = None

        @_ERROR_HANDLER
        def error(display: int, event: Any) -> int:
            # A window can disappear between QueryTree and inspection. Do not
            # let that race terminate the worker or print private window data.
            if display not in self.connections and self._previous_handler:
                return int(self._previous_handler(display, event))
            return 0

        self._error_handler = error
        previous = self.x.XSetErrorHandler(C.cast(error, C.c_void_p))
        if previous:
            self._previous_handler = _ERROR_HANDLER(previous)

        @_IO_HANDLER
        def io_error(display: int) -> int:
            if display not in self.connections and self._previous_io_handler:
                return int(self._previous_io_handler(display))
            return 0

        self._io_handler = io_error
        previous_io = self.x.XSetIOErrorHandler(C.cast(io_error, C.c_void_p))
        if previous_io:
            self._previous_io_handler = _IO_HANDLER(previous_io)

    @staticmethod
    def _library(name: str) -> str:
        path = ctypes.util.find_library(name)
        if path is None:
            raise OSError("Native browser display libraries are unavailable")
        return path

    @staticmethod
    def _bind(library: Any, name: str, arguments: list[Any], result: Any) -> None:
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result


@functools.lru_cache(maxsize=1)
def _libraries() -> _X11:
    return _X11()


def _dimensions(width: int, height: int) -> None:
    if (type(width) is not int or type(height) is not int
            or not 640 <= width <= MAX_WIDTH or not 400 <= height <= MAX_HEIGHT):
        raise ValueError("Native browser dimensions are outside their bounds")


class BrowserDesktop:
    """Headed browser surface; only the worker's manual-control path may input."""

    def __init__(self) -> None:
        self.width, self.height = 1280, 800
        self.display = ""
        self._directory: Path | None = None
        self._authority: Path | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._libs: _X11 | None = None
        self._connection: int | None = None
        self._root = self._damage = self._owner = 0
        self._fd = -1
        self._damage_event = 0
        self._atoms: dict[str, int] = {}
        self._dirty = asyncio.Event()
        self._closed = False
        self._failed = False
        self._keys: set[int] = set()
        self._forwarded_keys: dict[str, int] = {}
        self._buttons: set[int] = set()
        self._modifier_codes: dict[int, int] = {}
        self._clipboard = b""
        self._clipboard_read = asyncio.Event()
        self._clipboard_delivered = False
        self._clipboard_target: tuple[int, int, int] | None = None
        self._clipboard_requested_at = 0.0
        self._input_lock = asyncio.Lock()

        @_IO_EXIT_HANDLER
        def disconnected(_display: Any, _data: Any) -> None:
            # Supported libX11 versions let an owned display fail without exiting Python.
            # Never call Xlib from this callback, including XCloseDisplay.
            self._failed = True
            if self._fd >= 0:
                asyncio.get_running_loop().remove_reader(self._fd)
            self._dirty.set()
            self._clipboard_read.set()

        self._io_exit = disconnected

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("Xvfb") and all(ctypes.util.find_library(name)
                                               for name in ("X11", "Xtst", "Xdamage")))

    async def start(self, private_dir: Path, width: int, height: int) -> dict[str, str]:
        _dimensions(width, height)
        if self._closed or self._directory is not None:
            raise ValueError("Native browser display cannot be started again")
        if not private_dir.is_absolute() or private_dir.is_symlink():
            raise ValueError("Native browser display requires a private absolute directory")
        executable = shutil.which("Xvfb")
        if executable is None:
            raise OSError("Install Xvfb to use the native browser display")
        private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if private_dir.stat().st_uid != os.getuid():
            raise ValueError("Native browser display directory has another owner")
        self.width, self.height = width, height
        self._directory = Path(tempfile.mkdtemp(prefix="display-", dir=private_dir))
        self._authority = self._directory / "Xauthority"
        try:
            self._libs = _libraries()
            for _ in range(8):
                number = str(1000 + secrets.randbelow(29000))
                self.display = ":" + number
                # A FamilyWild record still matches only this display number.
                # Write the cookie directly: it must never enter process argv.
                fields = (b"", number.encode("ascii"), b"MIT-MAGIC-COOKIE-1", secrets.token_bytes(16))
                data = struct.pack(">H", 65535) + b"".join(struct.pack(">H", len(v)) + v for v in fields)
                descriptor = os.open(self._authority, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
                # Xvfb writes the display number to -displayfd once it listens,
                # and exits without writing when another server holds it. Kira
                # connects only after that report, never to whichever server
                # happens to answer on the number.
                report, report_writer = os.pipe()
                try:
                    self._process = await asyncio.create_subprocess_exec(
                        executable, self.display, "-displayfd", str(report_writer),
                        "-screen", "0", f"{MAX_WIDTH}x{MAX_HEIGHT}x24",
                        "-nolisten", "tcp", "-auth", str(self._authority), "-noreset",
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL, pass_fds=(report_writer,),
                        env={key: value for key, value in os.environ.items()
                             if key in {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR"}},
                    )
                except BaseException:
                    os.close(report)
                    raise
                finally:
                    os.close(report_writer)
                if await self._reported_display(report) == number:
                    self._connection = self._connect()
                    if self._connection and self._served_by_display_process():
                        break
                    self._disconnect()
                await self._stop_process()
            if not self._connection:
                raise OSError("The private browser display could not start")
            self._initialize_connection()
            self._watcher = asyncio.create_task(self._watch_process())
            self.mark_dirty()
            return {"DISPLAY": self.display, "XAUTHORITY": str(self._authority)}
        except BaseException:
            await self.close()
            raise

    async def _reported_display(self, descriptor: int) -> str | None:
        """Read the number Xvfb reports when ready; None if it exits or stays silent."""
        loop = asyncio.get_running_loop()
        readable = asyncio.Event()
        data = b""
        try:
            os.set_blocking(descriptor, False)
            loop.add_reader(descriptor, readable.set)
            try:
                async with asyncio.timeout(3):
                    while b"\n" not in data and len(data) <= 16:
                        await readable.wait()
                        readable.clear()
                        try:
                            chunk = os.read(descriptor, 32)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            return None
                        data += chunk
            except TimeoutError:
                return None
            finally:
                loop.remove_reader(descriptor)
        finally:
            os.close(descriptor)
        line = data.split(b"\n", 1)[0]
        return line.decode("ascii") if line.isdigit() and len(line) <= 5 else None

    def _served_by_display_process(self) -> bool:
        """Whether the peer of the X connection is the Xvfb process started here."""
        assert self._libs is not None and self._connection is not None
        if self._process is None:
            return False
        try:
            descriptor = self._libs.x.XConnectionNumber(self._connection)
            # fromfd duplicates the descriptor; Xlib keeps its own.
            with socket.fromfd(descriptor, socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                              struct.calcsize("3i"))
        except OSError:
            return False
        pid, uid, _ = struct.unpack("3i", credentials)
        return pid == self._process.pid and uid == os.getuid()

    def _disconnect(self) -> None:
        connection, self._connection = self._connection, None
        if connection and self._libs is not None:
            self._libs.x.XCloseDisplay(connection)

    def _connect(self) -> int | None:
        assert self._libs is not None and self._authority is not None
        previous = os.environ.get("XAUTHORITY")
        try:
            os.environ["XAUTHORITY"] = str(self._authority)
            # No await while temporarily selecting the private authority file.
            return self._libs.x.XOpenDisplay(self.display.encode("ascii")) or None
        finally:
            if previous is None:
                os.environ.pop("XAUTHORITY", None)
            else:
                os.environ["XAUTHORITY"] = previous

    def _initialize_connection(self) -> None:
        assert self._libs is not None and self._connection is not None
        libraries, connection = self._libs, self._connection
        libraries.connections.add(connection)
        libraries.x.XSetIOErrorExitHandler(connection, C.cast(self._io_exit, C.c_void_p), None)
        event, error, major, minor = (C.c_int() for _ in range(4))
        if not libraries.test.XTestQueryExtension(connection, C.byref(event), C.byref(error),
                                                  C.byref(major), C.byref(minor)):
            raise OSError("The private browser display lacks native input support")
        if not libraries.damage.XDamageQueryExtension(connection, C.byref(event), C.byref(error)):
            raise OSError("The private browser display lacks damage notifications")
        self._damage_event = event.value
        self._root = libraries.x.XDefaultRootWindow(connection)
        self._damage = libraries.damage.XDamageCreate(connection, self._root, 3)
        libraries.x.XSelectInput(connection, self._root, (1 << 19) | (1 << 17) | (1 << 15))
        self._owner = libraries.x.XCreateSimpleWindow(connection, self._root, 0, 0, 1, 1, 0, 0, 0)
        self._atoms = {name: libraries.x.XInternAtom(connection, name.encode("ascii"), False)
                       for name in ("CLIPBOARD", "UTF8_STRING", "TARGETS", "ATOM", "WM_CLASS")}
        self._modifier_codes = {bit: self._keycode(name) for bit, name in _MODIFIERS.items()}
        # Qt already forwards auto-repeat. A second X server repeat timer would
        # duplicate held characters and could outlive a queued key release.
        libraries.x.XAutoRepeatOff(connection)
        self._fd = libraries.x.XConnectionNumber(connection)
        os.set_inheritable(self._fd, False)
        asyncio.get_running_loop().add_reader(self._fd, self._read_events)
        libraries.x.XFlush(connection)

    def _live(self) -> bool:
        return bool(self._connection and self._process and self._process.returncode is None
                    and not self._closed and not self._failed)

    def _require_live(self) -> tuple[_X11, int]:
        if not self._live() or self._libs is None or self._connection is None:
            raise OSError("The private browser display is unavailable")
        return self._libs, self._connection

    async def _watch_process(self) -> None:
        assert self._process is not None
        await self._process.wait()
        if not self._closed:
            self._failed = True
            if self._fd >= 0:
                asyncio.get_running_loop().remove_reader(self._fd)
            self._dirty.set()
            self._clipboard_read.set()

    def mark_dirty(self) -> None:
        self._dirty.set()

    async def wait_changed(self) -> None:
        self._require_live()
        self._read_events()
        await self._dirty.wait()
        self._dirty.clear()
        self._require_live()

    def _read_events(self) -> None:
        if not self._live() or self._libs is None:
            return
        for _ in range(256):
            pending = self._libs.x.XPending(self._connection)
            if not self._live() or pending <= 0:
                return
            event = _Event()
            self._libs.x.XNextEvent(self._connection, C.byref(event))
            if event.type == 30:
                self._selection_requested(event.request)
            elif event.type == 29:
                # A SelectionClear from the preceding paste can already be
                # queued when a new character takes ownership. Only the
                # current server owner can invalidate this paste operation.
                if self._libs.x.XGetSelectionOwner(self._connection, self._atoms["CLIPBOARD"]) != self._owner:
                    self._clipboard = b""
                    self._clipboard_read.set()
            elif (event.type == 28 and event.property.state == 1 and self._clipboard_target
                  and (event.property.window, event.property.atom) == self._clipboard_target[:2]):
                # The receiver has read and deleted the property. Dropping
                # ownership on SelectionNotify alone races Chromium's read.
                self._clipboard_delivered = True
                self._clipboard_read.set()
            elif event.type == self._damage_event or event.type in {12, 16, 17, 18, 19, 21, 22}:
                self.mark_dirty()
        asyncio.get_running_loop().call_soon(self._read_events)

    def _windows(self) -> list[int]:
        libraries, connection = self._require_live()
        root, parent, children, count = C.c_ulong(), C.c_ulong(), C.POINTER(C.c_ulong)(), C.c_uint()
        if not libraries.x.XQueryTree(connection, self._root, C.byref(root), C.byref(parent),
                                      C.byref(children), C.byref(count)):
            return []
        try:
            candidates = list(children[:min(count.value, 128)])
        finally:
            if children:
                libraries.x.XFree(children)
        result = []
        for window in candidates:
            attributes = _Attributes()
            if (not libraries.x.XGetWindowAttributes(connection, window, C.byref(attributes))
                    or attributes.map_state != 2 or attributes.override_redirect):
                continue
            actual, format_, count_, remaining, value = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong(), C.c_void_p()
            libraries.x.XGetWindowProperty(connection, window, self._atoms["WM_CLASS"], 0, 128,
                False, 0, C.byref(actual), C.byref(format_), C.byref(count_), C.byref(remaining), C.byref(value))
            try:
                name = C.string_at(value, min(count_.value, 512)).lower() if value and format_.value == 8 else b""
            finally:
                if value:
                    libraries.x.XFree(value)
            if any(part in name for part in (b"chromium", b"google-chrome")):
                result.append(window)
        return result

    async def resize(self, width: int, height: int) -> None:
        _dimensions(width, height)
        libraries, connection = self._require_live()
        self.release_inputs()
        self.width, self.height = width, height
        # Keep Chromium's fullscreen/kiosk state. A CDP transition to normal
        # would restore the browser's own address bar and menu controls.
        for window in self._windows():
            libraries.x.XMoveResizeWindow(connection, window, 0, 0, width, height)
            libraries.x.XClearArea(connection, window, 0, 0, 0, 0, True)
        libraries.x.XFlush(connection)
        self._require_live()
        self.mark_dirty()
        await asyncio.sleep(0)

    def capture_jpeg(self) -> Json:
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        libraries, connection = self._require_live()
        # Rearm damage before reading, so updates concurrent with capture remain
        # pending for the next frame. There is no periodic screenshot timer.
        libraries.damage.XDamageSubtract(connection, self._damage, 0, 0)
        image = libraries.x.XGetImage(connection, self._root, 0, 0, self.width, self.height,
                                      C.c_ulong(-1).value, 2)
        if not image:
            raise OSError("The native browser surface could not be captured")
        try:
            self._require_live()
            pixels = image.contents
            if (pixels.width != self.width or pixels.height != self.height or not pixels.data
                    or pixels.bits_per_pixel != 32 or pixels.byte_order != 0
                    or (pixels.red_mask, pixels.green_mask, pixels.blue_mask) != (0xff0000, 0xff00, 0xff)
                    or not self.width * 4 <= pixels.bytes_per_line <= MAX_WIDTH * 4):
                raise OSError("The native browser surface has an unsupported pixel format")
            raw = C.string_at(pixels.data, pixels.bytes_per_line * self.height)
            bitmap = QImage(raw, self.width, self.height, pixels.bytes_per_line,
                            QImage.Format.Format_RGB32).copy()
        finally:
            libraries.x.XDestroyImage(image)
        for quality in (55, 35, 15, 5):
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            # Qt's runtime accepts str here; its bundled stub currently says bytes.
            if not bitmap.save(buffer, "JPEG", quality):  # type: ignore[call-overload]
                raise OSError("The native browser image could not be encoded")
            encoded = base64.b64encode(buffer.data().data()).decode("ascii")
            if len(encoded) <= MAX_VIEW_IMAGE:
                return {"data": encoded, "mime": "image/jpeg", "width": self.width, "height": self.height}
        raise ValueError("The native browser image exceeds its frame limit")

    def _keycode(self, name: str) -> int:
        assert self._libs is not None and self._connection is not None
        symbol = self._libs.x.XStringToKeysym(name.encode("ascii"))
        code = int(self._libs.x.XKeysymToKeycode(self._connection, symbol)) if symbol else 0
        if not code:
            raise ValueError("The native browser key is unavailable")
        return code

    def _key(self, code: int, down: bool) -> None:
        libraries, connection = self._require_live()
        if down and code in self._keys:
            # Forward Qt's repeat once while X server auto-repeat is disabled.
            if not libraries.test.XTestFakeKeyEvent(connection, code, False, 0):
                raise OSError("Native browser keyboard input failed")
        if not libraries.test.XTestFakeKeyEvent(connection, code, down, 0):
            raise OSError("Native browser keyboard input failed")
        if down:
            self._keys.add(code)
        else:
            self._keys.discard(code)

    def _modifiers(self, mask: int) -> None:
        for bit, code in self._modifier_codes.items():
            down = bool(mask & bit)
            if down != (code in self._keys):
                self._key(code, down)

    @staticmethod
    def _allowed_key(key: str, modifiers: int) -> bool:
        if key in _MODIFIER_NAMES:
            return True
        if modifiers & 5:
            return False
        if modifiers & 2:
            if modifiers & 8 and key.lower() not in {"v", "z"}:
                return False
            return key.lower() in {"a", "c", "v", "x", "y", "z"} or key in {
                "Backspace", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"}
        return key in _KEY_NAMES or len(key) == 1 and key.isprintable()

    def _text_key(self, text: str) -> tuple[int, int] | None:
        """Map the committed character to this private display's keyboard."""
        if len(text) != 1 or not text.isprintable():
            return None
        libraries, connection = self._require_live()
        character = ord(text)
        symbol = character if character <= 255 else 0x1000000 | character
        code = int(libraries.x.XKeysymToKeycode(connection, symbol))
        if code:
            for level, shift in ((0, 0), (1, 8)):
                if libraries.x.XKeycodeToKeysym(connection, code, level) == symbol:
                    return code, shift
        return None

    def _focus(self) -> None:
        libraries, connection = self._require_live()
        window, revert = C.c_ulong(), C.c_int()
        libraries.x.XGetInputFocus(connection, C.byref(window), C.byref(revert))
        if window.value in {0, 1, self._root}:
            windows = self._windows()
            if windows:
                libraries.x.XSetInputFocus(connection, windows[-1], 2, 0)

    async def input(self, action: str, args: Json) -> None:
        if action not in {"ui_mouse", "ui_key", "ui_text"}:
            self.release_inputs()
            raise ValueError("Unsupported native browser input")
        try:
            validate_browser_action(action, args)
        except (ValueError, TypeError, UnicodeError):
            self.release_inputs()
            raise
        async with self._input_lock:
            libraries, connection = self._require_live()
            if action == "ui_mouse":
                self._mouse(args)
            elif action == "ui_key":
                key, down = args["key"], args["type"] == "down"
                mask = args.get("modifiers", 0)
                held = sum(bit for bit, code in self._modifier_codes.items() if code in self._keys)
                if not self._allowed_key(key, mask | held):
                    self.release_inputs()
                    raise ValueError("Use Kira's controls instead of browser shortcuts")
                if key in _MODIFIER_NAMES:
                    bit = _MODIFIER_NAMES[key]
                    mask = mask | bit if down else mask & ~bit
                    self._modifiers(mask)
                else:
                    self._focus()
                    identity = args.get("code") or str(key).casefold()
                    if not down:
                        code = self._forwarded_keys.pop(identity, 0)
                        if code:
                            self._key(code, False)
                        self._modifiers(mask)
                    elif len(key) == 1 and not mask & 7:
                        # Qt's actual text includes layout/CapsLock/compose
                        # semantics; key alone does not. Native ASCII PINs stay
                        # on XTest, and unmapped Unicode uses the private paste.
                        text = args.get("text") or key
                        mapped = self._text_key(text)
                        if mapped is None:
                            await self._paste(text)
                            self._modifiers(mask)
                            self._forwarded_keys[identity] = 0
                        else:
                            code, shift = mapped
                            self._modifiers((mask & ~8) | shift)
                            self._key(code, True)
                            self._forwarded_keys[identity] = code
                    else:
                        self._modifiers(mask)
                        code = self._keycode(_KEY_NAMES.get(str(key), str(key)))
                        self._key(code, True)
                        self._forwarded_keys[identity] = code
            else:
                await self._paste(args["text"])
            libraries.x.XFlush(connection)
            self._require_live()
            self.mark_dirty()

    def _mouse(self, args: Json) -> None:
        libraries, connection = self._require_live()
        if args["x"] >= self.width or args["y"] >= self.height:
            self.release_inputs()
            raise ValueError("Pointer is outside the native browser surface")
        if args.get("button", "none") not in {"none", "left"}:
            self.release_inputs()
            raise ValueError("Native browser context menus are unavailable")
        mask = args.get("modifiers", 0)
        if mask & 5 or args["type"] == "wheel" and mask & 2:
            self.release_inputs()
            raise ValueError("Unsupported native browser pointer shortcut")
        self._modifiers(mask)
        libraries.test.XTestFakeMotionEvent(connection, 0, args["x"], args["y"], 0)
        kind = args["type"]
        if kind in {"press", "release"}:
            if args.get("button") != "left":
                self.release_inputs()
                raise ValueError("Native pointer input requires the left button")
            down = kind == "press"
            libraries.test.XTestFakeButtonEvent(connection, 1, down, 0)
            if down:
                self._buttons.add(1)
            else:
                self._buttons.discard(1)
        elif kind == "wheel":
            for axis, negative, positive in (("delta_y", 4, 5), ("delta_x", 6, 7)):
                value = args.get(axis, 0)
                for _ in range(min(20, (abs(value) + 119) // 120)):
                    button = negative if value < 0 else positive
                    libraries.test.XTestFakeButtonEvent(connection, button, True, 0)
                    libraries.test.XTestFakeButtonEvent(connection, button, False, 0)

    async def _paste(self, text: str) -> None:
        if not text:
            return
        data = text.encode("utf-8")
        if len(data) > 40000:
            raise ValueError("Native browser text exceeds its limit")
        libraries, connection = self._require_live()
        self.release_inputs()
        self._focus()
        self._clipboard, self._clipboard_read = data, asyncio.Event()
        self._clipboard_delivered = False
        self._clipboard_target = None
        self._clipboard_requested_at = asyncio.get_running_loop().time()
        libraries.x.XSetSelectionOwner(connection, self._atoms["CLIPBOARD"], self._owner, 0)
        try:
            self._modifiers(2)
            key = self._keycode("v")
            self._key(key, True)
            self._key(key, False)
            self._modifiers(0)
            libraries.x.XFlush(connection)
            self._read_events()
            await asyncio.wait_for(self._clipboard_read.wait(), 2)
            self._require_live()
            if not self._clipboard_delivered:
                raise OSError("Native browser text delivery was not confirmed")
            # Chromium can read once for its clipboard cache, then again for
            # the focused control. Serve the bounded burst before clearing it.
            deadline = asyncio.get_running_loop().time() + 1
            while True:
                remaining = 0.1 - (asyncio.get_running_loop().time() - self._clipboard_requested_at)
                if remaining <= 0 or asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(remaining)
        finally:
            self._clipboard = b""
            if self._live():
                if self._clipboard_target:
                    window, property_, mask = self._clipboard_target
                    libraries.x.XDeleteProperty(connection, window, property_)
                    libraries.x.XSelectInput(connection, window, mask)
                libraries.x.XSetSelectionOwner(connection, self._atoms["CLIPBOARD"], 0, 0)
                self.release_inputs()
                libraries.x.XFlush(connection)
            self._clipboard_target = None

    def _selection_requested(self, request: _SelectionRequest) -> None:
        libraries, connection = self._require_live()
        self._clipboard_requested_at = asyncio.get_running_loop().time()
        property_ = request.property or request.target
        if request.selection != self._atoms["CLIPBOARD"] or not self._clipboard:
            property_ = 0
        elif request.target == self._atoms["TARGETS"]:
            targets = (C.c_ulong * 2)(self._atoms["TARGETS"], self._atoms["UTF8_STRING"])
            libraries.x.XChangeProperty(connection, request.requestor, property_, self._atoms["ATOM"],
                                        32, 0, C.cast(targets, C.c_void_p), 2)
        elif request.target == self._atoms["UTF8_STRING"]:
            attributes = _Attributes()
            libraries.x.XGetWindowAttributes(connection, request.requestor, C.byref(attributes))
            mask = (self._clipboard_target[2] if self._clipboard_target
                    and self._clipboard_target[0] == request.requestor else attributes.your_event_mask)
            self._clipboard_target = (request.requestor, property_, mask)
            libraries.x.XSelectInput(connection, request.requestor, attributes.your_event_mask | (1 << 22))
            data = C.create_string_buffer(self._clipboard)
            libraries.x.XChangeProperty(connection, request.requestor, property_, self._atoms["UTF8_STRING"],
                                        8, 0, C.cast(data, C.c_void_p), len(self._clipboard))
        else:
            property_ = 0
        event = _Event()
        event.selection = _Selection(31, 0, True, connection, request.requestor, request.selection,
                                     request.target, property_, request.time)
        libraries.x.XSendEvent(connection, request.requestor, False, 0, C.byref(event))
        libraries.x.XFlush(connection)

    def release_inputs(self) -> None:
        if self._live() and self._libs is not None:
            for code in tuple(self._keys):
                self._libs.test.XTestFakeKeyEvent(self._connection, code, False, 0)
            for button in tuple(self._buttons):
                self._libs.test.XTestFakeButtonEvent(self._connection, button, False, 0)
            self._libs.x.XFlush(self._connection)
        self._keys.clear()
        self._forwarded_keys.clear()
        self._buttons.clear()

    async def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await asyncio.wait_for(process.wait(), 1)

    async def close(self) -> None:
        if self._closed:
            return
        self.release_inputs()
        self._clipboard = b""
        self._closed = True
        self._dirty.set()
        self._clipboard_read.set()
        if self._fd >= 0:
            asyncio.get_running_loop().remove_reader(self._fd)
        if self._libs is not None and self._connection:
            if not self._failed:
                if self._damage:
                    self._libs.damage.XDamageDestroy(self._connection, self._damage)
                if self._owner:
                    self._libs.x.XDestroyWindow(self._connection, self._owner)
            # The per-display I/O exit handler also makes teardown safe after
            # server loss; Xlib releases its own descriptor and allocations.
            self._libs.x.XCloseDisplay(self._connection)
            self._libs.connections.discard(self._connection)
            self._connection = None
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
        await self._stop_process()
        if self._authority is not None:
            self._authority.unlink(missing_ok=True)
        if self._directory is not None:
            with contextlib.suppress(OSError):
                self._directory.rmdir()
