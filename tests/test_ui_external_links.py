"""Synthetic browser dispatches: no real browser, login or network request."""

import base64
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QProcess, QUrl

from kira.ui import external_links
from kira.ui.external_links import BrowserLauncher, BrowserOpenResult


@pytest.fixture
def browser(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(external_links.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or False)
    monkeypatch.setattr(external_links, "_is_wsl", lambda: False)
    launcher = BrowserLauncher()
    yield launcher, opened
    launcher.close()


def test_native_success_does_not_invoke_windows_fallback(browser, monkeypatch):
    launcher, _ = browser
    monkeypatch.setattr(external_links.QDesktopServices, "openUrl", lambda url: True)
    monkeypatch.setattr(external_links, "_is_wsl", lambda: pytest.fail("Unexpected fallback"))
    results = []
    launcher.open("https://example.test/usage", results.append)
    assert results == [BrowserOpenResult(True, "native")]


def test_native_failure_is_observable_outside_wsl(browser):
    launcher, opened = browser
    results = []
    launcher.open("https://example.test/usage", results.append)
    assert opened == ["https://example.test/usage"]
    assert results == [BrowserOpenResult(False, error="browser_unavailable")]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_wsl_oauth_loopback_uses_windows_even_when_native_handler_would_say_yes(
        browser, monkeypatch, qtbot, exit_code):
    launcher, opened = browser
    # xdg-open/Qt may acknowledge a link but launch nothing in Windows.
    # The OAuth start URL is single-use: do not dispatch it twice.
    monkeypatch.setattr(external_links.QDesktopServices, "openUrl",
                        lambda _: pytest.fail("WSL OAuth must not use the misleading native handler"))
    windows_fake(monkeypatch, (sys.executable, ["-c", f"raise SystemExit({exit_code})"]))
    results = []
    launcher.open("http://127.0.0.1:1455/auth/start/abcdefghijklmnop",
                  results.append, allow_loopback=True)
    qtbot.waitUntil(lambda: len(results) == 1)
    assert opened == []
    assert results == [BrowserOpenResult(exit_code == 0, "windows",
                                         "" if exit_code == 0 else "launch_failed")]
    assert not launcher._pending


def test_wsl_oauth_uses_native_handler_only_if_windows_launcher_unavailable(
        browser, monkeypatch):
    launcher, opened = browser
    windows_fake(monkeypatch, None)
    results = []
    launcher.open("http://127.0.0.1:1455/auth/start/abcdefghijklmnop",
                  results.append, allow_loopback=True)
    assert opened == ["http://127.0.0.1:1455/auth/start/abcdefghijklmnop"]
    assert results == [BrowserOpenResult(False, error="browser_unavailable")]


@pytest.mark.parametrize("url", [
    "file:///tmp/example", "javascript:alert(1)", "mailto:user@example.test", "https://",
    "https://user:secret@example.test", "https://user@example.test", "//example.test",
    " https://example.test", "https://example.test\n", "https://example.test/\x00value",
    "https://example.test:99999", "https://example.test:0", "https://example.test\\other",
    "http://example.test", "http://127.0.0.1:1455/auth/start/example",
])
def test_unacceptable_urls_never_reach_an_opener(browser, url):
    launcher, opened = browser
    results = []
    launcher.open(url, results.append)
    assert opened == []
    assert results == [BrowserOpenResult(False, error="invalid_url")]


def test_http_and_loopback_are_separate_explicit_options(browser):
    launcher, opened = browser
    results = []
    launcher.open("http://127.0.0.1:1455/auth/start/example", results.append, allow_loopback=True)
    launcher.open("http://example.test", results.append, allow_loopback=True)
    launcher.open("http://example.test", results.append, allow_http=True)
    assert opened == ["http://127.0.0.1:1455/auth/start/example", "http://example.test"]
    assert results[1].error == "invalid_url"


def test_windows_command_is_constant_and_reads_url_as_data(monkeypatch):
    monkeypatch.setattr(external_links, "_powershell_binary", lambda: "/synthetic/powershell.exe")
    executable, arguments = external_links._windows_browser_command()
    assert executable == "/synthetic/powershell.exe"
    assert arguments[:-1] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(arguments[-1]).decode("utf-16-le")
    assert "[Console]::In.ReadToEnd()" in script
    assert "[System.Text.UTF8Encoding]::new($false)" in script
    assert "-FilePath $url -ErrorAction Stop" in script
    assert script.count("Start-Process") == 1
    assert "Invoke-Expression" not in script and "cmd.exe" not in script


def windows_fake(monkeypatch, command):
    monkeypatch.setattr(external_links, "_is_wsl", lambda: True)
    monkeypatch.setattr(external_links, "_windows_browser_command", lambda: command)


def test_url_with_special_characters_and_unicode_is_passed_only_in_stdin(
        browser, monkeypatch, qtbot, tmp_path):
    launcher, _ = browser
    captured = tmp_path / "stdin.txt"
    script = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
    windows_fake(monkeypatch, (sys.executable, ["-c", script, str(captured)]))
    value = "https://example.test/a'b?x=$('side-effect');&y=%60z%60&pct=%25&name=città#fragment"
    results = []
    launcher.open(value, results.append)
    process = next(iter(launcher._pending))
    assert "example.test" not in repr(process.arguments())
    qtbot.waitUntil(lambda: len(results) == 1)
    assert results[0].opened
    assert captured.read_bytes() == QUrl(value).toString(QUrl.FullyEncoded).encode("utf-8")


@pytest.mark.parametrize("exit_code", [0, 7])
def test_windows_dispatch_runs_asynchronously_and_reports_exit(browser, monkeypatch, qtbot, exit_code):
    launcher, opened = browser
    windows_fake(monkeypatch, (sys.executable, ["-c", f"raise SystemExit({exit_code})"]))
    results = []
    launcher.open("https://example.test/usage", results.append)
    assert opened == ["https://example.test/usage"]
    assert not results
    qtbot.waitUntil(lambda: len(results) == 1)
    assert results[0].opened is (exit_code == 0)
    assert results[0].method == "windows"
    assert results[0].error == ("" if exit_code == 0 else "launch_failed")
    assert not launcher._pending


def test_windows_failed_start_and_missing_launcher_are_reported(browser, monkeypatch, qtbot):
    launcher, _ = browser
    windows_fake(monkeypatch, ("/no/such/browser-launcher", []))
    results = []
    launcher.open("https://example.test", results.append)
    qtbot.waitUntil(lambda: len(results) == 1)
    assert results[0] == BrowserOpenResult(False, "windows", "failed_to_start")
    windows_fake(monkeypatch, None)
    launcher.open("https://example.test", results.append)
    assert results[1].error == "browser_unavailable"


def test_windows_timeout_is_bounded_and_reported_once(browser, monkeypatch, qtbot):
    launcher, _ = browser
    launcher._timeout_ms = 30
    windows_fake(monkeypatch, (sys.executable, ["-c", "import time; time.sleep(30)"]))
    results = []
    launcher.open("https://example.test", results.append)
    qtbot.waitUntil(lambda: bool(results))
    assert results == [BrowserOpenResult(False, "windows", "timeout")]
    qtbot.wait(50)
    assert len(results) == 1 and not launcher._pending


def test_close_cancels_pending_process_without_calling_stale_callback(browser, monkeypatch, qtbot):
    launcher, _ = browser
    windows_fake(monkeypatch, (sys.executable, ["-c", "import time; time.sleep(30)"]))
    results = []
    launcher.open("https://example.test", results.append)
    process = next(iter(launcher._pending))
    qtbot.waitUntil(lambda: process.state() == QProcess.ProcessState.Running)
    launcher.close()
    qtbot.wait(50)
    assert not results and not launcher._pending
    launcher.open("https://example.test", results.append)
    assert results == [BrowserOpenResult(False, error="closed")]
