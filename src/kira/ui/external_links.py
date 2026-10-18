"""Explicit browser launches with bounded, observable WSL interoperability.

Opening a URL reports whether the operating system accepted the dispatch; it
cannot establish that a browser window became visible. Kira does not log URLs
or launcher output and does not include them in results. Native platform
handlers may emit their own diagnostics.
"""

from __future__ import annotations

import base64
import os
import platform
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

MAX_URL_LENGTH = 16_384


@dataclass(frozen=True)
class BrowserOpenResult:
    opened: bool
    method: str = ""
    error: str = ""


def _browser_url(value: str, *, allow_http: bool, allow_loopback: bool) -> QUrl | None:
    """Reject non-web handlers, credentials and control characters before dispatch.

    HTTP is an explicit compatibility option for user-clicked web links. The
    separate loopback option supports the service-validated OAuth start URL; it
    is not a replacement for the account controller's authority/path checks.
    """
    if (not value or len(value) > MAX_URL_LENGTH or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "\\" in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port  # Invalid/out-of-range ports must also be rejected.
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or (port is not None and port == 0)):
            return None
        if parsed.scheme != "https":
            if parsed.scheme != "http":
                return None
            if not allow_http and not (allow_loopback and parsed.hostname == "127.0.0.1"):
                return None
        url = QUrl(value, QUrl.ParsingMode.StrictMode)
        return url if url.isValid() and url.host() else None
    except ValueError:
        return None


def _is_wsl() -> bool:
    return bool(os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME")
                or "microsoft" in platform.release().lower())


def _powershell_binary() -> str | None:
    found = shutil.which("powershell.exe")
    if found:
        return found
    candidate = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def _windows_browser_command() -> tuple[str, list[str]] | None:
    executable = _powershell_binary()
    if not executable:
        return None
    # The script is constant: URLs never enter command-line arguments or code.
    # Read UTF-8 from a private pipe and pass it as data to the default handler.
    script = ("[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false); "
              "$url = [Console]::In.ReadToEnd(); "
              "try { Microsoft.PowerShell.Management\\Start-Process -FilePath $url "
              "-ErrorAction Stop | Out-Null; exit 0 } catch { exit 1 }")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return executable, ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


class BrowserLauncher(QObject):
    """Use on the GUI thread; callbacks never contain the requested URL.

    In WSL, OAuth loopback URLs prefer the Windows browser directly: Qt may
    report successful native dispatch while opening no usable browser at all.
    Other URLs use the native desktop handler first, then the Windows fallback.
    No browser associations are changed and no shell evaluates the URL. Call
    close() when the owning workspace closes.
    """

    def __init__(self, parent: QObject | None = None, *, timeout_ms: int = 10_000):
        super().__init__(parent)
        self._timeout_ms = max(1, timeout_ms)
        self._pending: dict[QProcess, Callable[[BrowserOpenResult], None]] = {}
        self._closed = False

    def open(self, value: str, callback: Callable[[BrowserOpenResult], None], *,
             allow_http: bool = False, allow_loopback: bool = False) -> None:
        if self._closed:
            callback(BrowserOpenResult(False, error="closed"))
            return
        url = _browser_url(value, allow_http=allow_http, allow_loopback=allow_loopback)
        if url is None:
            callback(BrowserOpenResult(False, error="invalid_url"))
            return
        # Windows needs to open the browser for WSL-hosted OAuth callbacks.
        # QDesktopServices.openUrl() can return True merely because xdg-open
        # accepted the URL, even when the Windows browser never appears.
        # Only the explicitly authorized WSL loopback flow overrides native
        # dispatch; ordinary HTTPS links retain their previous behavior.
        wsl_loopback = (allow_loopback and url.scheme() == "http"
                        and url.host() == "127.0.0.1" and _is_wsl())
        command = _windows_browser_command() if wsl_loopback else None
        if command is None:
            if QDesktopServices.openUrl(url):
                callback(BrowserOpenResult(True, method="native"))
                return
            command = _windows_browser_command() if _is_wsl() else None
        if command is None:
            callback(BrowserOpenResult(False, error="browser_unavailable"))
            return
        process = QProcess(self)
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(QProcess.nullDevice())
        timer = QTimer(process)
        timer.setSingleShot(True)
        self._pending[process] = callback
        payload = url.toString(QUrl.ComponentFormattingOption.FullyEncoded).encode("utf-8")

        def finish(result: BrowserOpenResult) -> None:
            finished = self._pending.pop(process, None)
            if finished is None:
                return
            timer.stop()
            if process.state() == QProcess.ProcessState.NotRunning:
                process.deleteLater()
            else:
                process.kill()
            finished(result)

        def exited(code: int, status: QProcess.ExitStatus) -> None:
            accepted = code == 0 and status == QProcess.ExitStatus.NormalExit
            finish(BrowserOpenResult(accepted, "windows", "" if accepted else "launch_failed"))
            process.deleteLater()

        def failed(error: QProcess.ProcessError) -> None:
            if error == QProcess.ProcessError.FailedToStart:
                finish(BrowserOpenResult(False, "windows", "failed_to_start"))

        def started() -> None:
            nonlocal payload
            if process not in self._pending:
                payload = b""
                return
            if process.write(payload) != len(payload):
                payload = b""
                finish(BrowserOpenResult(False, "windows", "launch_failed"))
                return
            payload = b""
            process.closeWriteChannel()

        process.started.connect(started)
        process.finished.connect(exited)
        process.errorOccurred.connect(failed)
        timer.timeout.connect(lambda: finish(BrowserOpenResult(False, "windows", "timeout")))
        process.start(*command)
        timer.start(self._timeout_ms)

    def close(self) -> None:
        self._closed = True
        pending, self._pending = self._pending, {}
        for process in pending:
            for timer in process.findChildren(QTimer):
                timer.stop()
            if process.state() == QProcess.ProcessState.NotRunning:
                process.deleteLater()
            else:
                process.kill()
