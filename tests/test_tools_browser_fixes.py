"""Browser worker regressions: timeouts, error pages, sandbox start, budgets and the private display."""

import asyncio
import base64
import json
import os
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kira.tools import browser, browser_desktop, browser_worker, web_network
from kira.tools.browser import (
    ERROR_PAGE_MESSAGE,
    BrowserErrorPage,
    BrowserSession,
    BrowserStartError,
)
from kira.tools.browser_desktop import BrowserDesktop
from kira.tools.browser_worker import BrowserEngine, launch_failure, request_failure
from kira.tools.web_network import NetworkBudget, PublicWebError, PublicWebProxy
from test_browser_engine import FakeHandle, FakePage, engine_with_page, fake_process
from test_browser_view import embedded_engine, eventually

ERROR_URL = "chrome-error://chromewebdata/"

WORKER_FIXTURE = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
import kira.tools.browser_worker as worker
from playwright.async_api import TimeoutError as PlaywrightTimeout


class FixtureEngine:
    def __init__(self, emit=None):
        pass

    async def call(self, action, args):
        if action == "click":
            raise PlaywrightTimeout("Timeout 12000ms exceeded")
        if action == "press":
            await asyncio.wait_for(asyncio.Event().wait(), 0.01)
        if action == "type":
            await asyncio.sleep(10)
        return {"ok": True, "action": action}

    async def close(self):
        pass


worker.BrowserEngine = FixtureEngine
worker.ACTION_TIMEOUT = 0.5
asyncio.run(worker.serve())
"""


async def test_worker_survives_bounded_timeouts_and_stops_only_at_its_own_deadline(tmp_path):
    package_root = str(Path(browser.__file__).resolve().parents[2])
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", WORKER_FIXTURE, package_root, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=tmp_path)
    requests = [(1, "initialize"), (2, "click"), (3, "press"), (4, "tabs"), (5, "type"), (6, "tabs")]
    payload = b"".join(json.dumps({"id": identifier, "action": action, "args": {}}).encode() + b"\n"
                       for identifier, action in requests)
    stdout, stderr = await asyncio.wait_for(process.communicate(payload), 20)
    responses = {item["id"]: item["result"] for item in map(json.loads, stdout.splitlines())}
    assert responses[1] == {"ok": True, "action": "initialize"}, stderr
    # A Playwright timeout (an element covered by a banner, a slow navigation)
    # leaves the browser and its tabs alive, with an explicitly unknown outcome.
    assert responses[2]["error"]["code"] == "browser_timeout"
    assert responses[2]["action_outcome"] == "unknown" and responses[2]["retry_safe"] is False
    assert "12000ms" not in json.dumps(responses[2])
    # So does an inner bounded wait such as the native paste confirmation.
    assert responses[3]["error"]["code"] == "browser_timeout"
    assert responses[4] == {"ok": True, "action": "tabs"}
    # Kira's whole-request deadline closes the worker and says so.
    assert responses[5]["error"]["code"] == "browser_unavailable"
    assert "closed" in responses[5]["error"]["message"] and responses[5]["action_outcome"] == "unknown"
    assert 6 not in responses and process.returncode == 0


def test_read_only_timeouts_do_not_claim_an_unknown_effect():
    result, fatal = request_failure(TimeoutError(), "snapshot", deadline_expired=False)
    assert result["error"]["code"] == "browser_timeout" and not fatal
    assert "action_outcome" not in result and "retry_safe" not in result
    result, fatal = request_failure(TimeoutError(), "navigate", deadline_expired=False)
    assert result["action_outcome"] == "unknown" and "retry_safe" not in result and not fatal


async def test_error_page_reads_are_named_and_leaving_actions_still_work():
    page = FakePage([FakeHandle()])
    page.url = ERROR_URL

    async def go_back(**_options):
        page.url = "https://example.com/previous"

    page.go_back = go_back
    engine = engine_with_page(page)
    for action, arguments in (("snapshot", {}), ("find", {"text": "fixture"}), ("screenshot", {}),
                              ("scroll", {"direction": "down"}),
                              ("prepare_interaction", {"action": "press", "arguments": {"key": "Enter"}})):
        with pytest.raises(BrowserErrorPage):
            await engine.call(action, arguments)
    failure, fatal = request_failure(BrowserErrorPage(ERROR_PAGE_MESSAGE), "snapshot", deadline_expired=False)
    assert failure == {"ok": False, "error": {"code": "browser_error_page", "message": ERROR_PAGE_MESSAGE}}
    assert not fatal
    assert (await engine.call("tabs", {}))["tabs"][0]["url"] == ERROR_URL
    returned = await engine.call("back", {})
    assert returned["ok"] and returned["url"] == "https://example.com/previous"


async def test_error_page_never_relaxes_other_non_public_documents():
    page = FakePage([FakeHandle()])
    page.url = "file:///private/fixture.txt"
    engine = engine_with_page(page)
    with pytest.raises(PublicWebError):
        await engine.call("snapshot", {})
    with pytest.raises(PublicWebError):
        engine._check_page(page, error_page_allowed=True)


async def test_resume_on_error_page_returns_control_without_reading_it():
    page = FakePage([FakeHandle()])
    engine = engine_with_page(page)
    engine.headed = True
    assert (await engine.call("manual_pause", {}))["paused"]
    page.url = ERROR_URL
    resumed = await engine.call("resume", {})
    assert resumed["ok"] and resumed["paused"] is False and not engine.paused
    assert resumed["page_error"]["code"] == "browser_error_page" and "text" not in resumed


async def test_computer_observation_names_error_page():
    page = FakePage()
    page.url = ERROR_URL
    result = await engine_with_page(page).call("computer_observe", {})
    assert result["error"]["code"] == "browser_error_page" and result["action_executed"] is False


async def test_manual_control_can_leave_an_error_page_and_use_its_controls():
    engine, page, cdp, _ = embedded_engine()
    other = FakePage([FakeHandle()])
    other.set_viewport_size = AsyncMock()
    engine.pages["other"] = other
    page.url = ERROR_URL
    try:
        await engine.call("viewer", {"enabled": True})
        await engine.call("ui_takeover", {})
        assert (await engine.call("ui_text", {"text": "fixture"}))["ok"]
        assert ("Input.insertText", {"text": "fixture"}) in cdp.sent
        for action, arguments in (("ui_reload", {}), ("ui_back", {}), ("ui_forward", {}),
                                  ("ui_navigate", {"url": "https://example.com/next"}),
                                  ("ui_select_tab", {"tab_id": "other"}),
                                  ("ui_select_tab", {"tab_id": "tab"}),
                                  ("ui_close_tab", {"tab_id": "other"})):
            assert (await engine.call(action, arguments))["ok"], action
        page.reload.assert_awaited_once()
        page.go_back.assert_awaited_once()
        page.go_forward.assert_awaited_once()
        page.goto.assert_awaited_once_with("https://example.com/next", wait_until="domcontentloaded")
        assert other.closed and list(engine.pages) == ["tab"]
        # Copying reads page content and stays refused, explicitly.
        with pytest.raises(BrowserErrorPage):
            await engine.call("ui_copy", {})
        # The destination of a manual navigation is still validated.
        with pytest.raises(PublicWebError):
            await engine.call("ui_navigate", {"url": "http://127.0.0.1/private"})
        page.url = "file:///private/fixture.txt"
        with pytest.raises(PublicWebError):
            await engine.call("ui_text", {"text": "fixture"})
    finally:
        await engine.view.close()


async def test_surface_error_has_a_stable_code_for_translation():
    engine, _, _, events = embedded_engine()
    try:
        await engine.view.report_error()
        assert events[-1]["data"]["surface_error_code"] == "view_unavailable"
        await engine.view.state()
        assert events[-1]["data"]["surface_error_code"] == "view_unavailable"
        await engine.call("viewer", {"enabled": True})
        await eventually(lambda: any(event["event"] == "state"
                                     and event["data"].get("surface_error_code") == ""
                                     and event["data"].get("surface_error") == "" for event in events))
    finally:
        await engine.view.close()


async def test_sandbox_start_failure_names_the_user_namespace_restriction(tmp_path, monkeypatch):
    restriction = tmp_path / "restriction"
    restriction.write_text("1\n")
    monkeypatch.setattr(browser_worker, "USERNS_RESTRICTION", restriction)
    log = ("BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
           "[pid=4242][err] [FATAL:zygote_host_impl_linux.cc(128)] No usable sandbox! "
           "--user-data-dir=/synthetic/profile")

    class Chromium:
        async def launch_persistent_context(self, *_args, **_options):
            raise RuntimeError(log)

    playwright = SimpleNamespace(chromium=Chromium(), stop=AsyncMock())
    monkeypatch.setattr("playwright.async_api.async_playwright",
                        lambda: SimpleNamespace(start=AsyncMock(return_value=playwright)))
    engine = BrowserEngine()
    try:
        result = await engine.initialize({"profile_dir": str(tmp_path / "profile"),
                                          "artifacts_dir": str(tmp_path / "artifacts"),
                                          "headed": False, "persistent": False})
    finally:
        await engine.close()
    assert result["error"]["code"] == "browser_sandbox_unavailable"
    message = result["error"]["message"]
    assert "kernel.apparmor_restrict_unprivileged_userns = 1" in message
    assert "synthetic" not in message and "4242" not in message and "no-sandbox" not in message
    restriction.write_text("0\n")
    assert "AppArmor" not in launch_failure(RuntimeError(log))["error"]["message"]
    restriction.unlink()
    assert launch_failure(RuntimeError(log))["error"]["code"] == "browser_sandbox_unavailable"
    other = launch_failure(RuntimeError("Executable doesn't exist at /synthetic/chrome"))
    assert other["error"]["code"] == "browser_unavailable"
    assert "sandbox" not in other["error"]["message"] and "synthetic" not in other["error"]["message"]


async def test_session_start_keeps_the_worker_error_code(tmp_path, monkeypatch):
    process = fake_process(b'{"id":1,"result":{"ok":false,"error":{"code":"browser_sandbox_unavailable",'
                           b'"message":"Synthetic sandbox explanation"}}}\n')
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    session = BrowserSession(tmp_path / "profile", tmp_path / "artifacts", headed=False,
                             persistent=False, started=AsyncMock())
    with pytest.raises(BrowserStartError) as raised:
        await session.start()
    assert isinstance(raised.value, RuntimeError)
    assert raised.value.code == "browser_sandbox_unavailable"
    assert str(raised.value) == "Synthetic sandbox explanation"


def test_network_budget_rolls_over_and_restarts_on_explicit_navigation(monkeypatch):
    monkeypatch.setattr(web_network, "MAX_WINDOW_REQUESTS", 2)
    monkeypatch.setattr(web_network, "MAX_WINDOW_BYTES", 10)
    now = [0.0]
    budget = NetworkBudget(clock=lambda: now[0])
    assert budget.request() and budget.request() and not budget.request()
    assert budget.limited and not budget.transfer(11)
    now[0] += web_network.BUDGET_WINDOW
    # The allowance rolls over; the page may still be incomplete until a navigation.
    assert budget.request() and budget.transfer(10) and budget.limited
    budget.reset()
    assert not budget.limited and budget.requests == 0 and budget.bytes == 0


async def test_request_budget_is_reported_and_restarts_with_explicit_navigation(monkeypatch):
    monkeypatch.setattr(web_network, "MAX_WINDOW_REQUESTS", 2)
    page = FakePage([FakeHandle()])
    page.goto = AsyncMock(return_value=None)
    engine = engine_with_page(page)
    route = SimpleNamespace(request=SimpleNamespace(url="https://example.com/app.js", redirected_from=None),
                            abort=AsyncMock(), continue_=AsyncMock())
    for _ in range(3):
        await engine._route(route)
    assert route.continue_.await_count == 2
    route.abort.assert_awaited_once_with("blockedbyclient")
    snapshot = await engine.call("snapshot", {})
    assert snapshot["network_budget"]["code"] == "network_budget_exhausted"
    navigated = await engine.call("navigate", {"url": "https://example.com/next"})
    assert navigated["ok"] and "network_budget" not in navigated
    await engine._route(route)
    assert route.continue_.await_count == 3


def proxy_headers(proxy):
    value = base64.b64encode(f"{proxy.username}:{proxy.password}".encode()).decode()
    return f"Proxy-Authorization: Basic {value}\r\n".encode()


async def relay_fixture(monkeypatch, handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    original = asyncio.open_connection

    async def connect(host, target_port, **options):
        if host == "93.184.216.34":
            return await original("127.0.0.1", port, **options)
        return await original(host, target_port, **options)

    async def resolver(host, target_port):
        return ["93.184.216.34"]

    monkeypatch.setattr(web_network.asyncio, "open_connection", connect)
    proxy = PublicWebProxy(resolver=resolver)
    await proxy.start()
    reader, writer = await original("127.0.0.1", proxy.port)
    writer.write(b"GET http://example.com/events HTTP/1.1\r\nHost: example.com\r\n"
                 + proxy_headers(proxy) + b"\r\n")
    await writer.drain()
    return server, proxy, reader, writer


async def close_fixture(server, proxy, writer):
    writer.close()
    await proxy.close()
    server.close()
    await server.wait_closed()


async def stream_events(reader, writer):
    await reader.readuntil(b"\r\n\r\n")
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
    for index in range(10):
        writer.write(f"data: {index}\n\n".encode())
        await writer.drain()
        await asyncio.sleep(0.1)
    writer.close()


async def test_active_stream_outlives_the_idle_timeout(monkeypatch):
    monkeypatch.setattr(web_network, "IDLE_TIMEOUT", 0.3)
    server, proxy, reader, writer = await relay_fixture(monkeypatch, stream_events)
    try:
        # The browser sends nothing after its request, as with server-sent events.
        body = await asyncio.wait_for(reader.read(), 5)
        assert b"data: 9" in body and proxy.denied_requests == 0
    finally:
        await close_fixture(server, proxy, writer)


async def test_idle_connection_closes_quietly_without_counting_a_denial(monkeypatch):
    monkeypatch.setattr(web_network, "IDLE_TIMEOUT", 0.3)

    async def silent(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n")
        await writer.drain()
        await reader.read()
        writer.close()

    server, proxy, reader, writer = await relay_fixture(monkeypatch, silent)
    try:
        started = time.monotonic()
        body = await asyncio.wait_for(reader.read(), 5)
        assert body.startswith(b"HTTP/1.1 200 OK") and time.monotonic() - started < 3
        await asyncio.sleep(0.05)
        assert proxy.denied_requests == 0 and not proxy.budget.limited
    finally:
        await close_fixture(server, proxy, writer)


async def test_budget_cut_is_counted_and_reported(monkeypatch):
    monkeypatch.setattr(web_network, "MAX_WINDOW_BYTES", 100)
    server, proxy, reader, writer = await relay_fixture(monkeypatch, stream_events)
    try:
        body = await asyncio.wait_for(reader.read(), 5)
        assert b"data: 9" not in body
        await asyncio.sleep(0.05)
        assert proxy.denied_requests == 1 and proxy.budget.limited
    finally:
        await close_fixture(server, proxy, writer)


class FakeXvfb:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self._exited = asyncio.Event()

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._exited.set()

    kill = terminate

    async def wait(self):
        await self._exited.wait()
        return self.returncode


def fake_display(monkeypatch, peer, outcomes):
    """Spawn fake Xvfb processes; each outcome is (reports_display, peer_pid)."""
    spawned = []

    async def spawn(*arguments, **options):
        descriptor = int(arguments[arguments.index("-displayfd") + 1])
        assert options["pass_fds"] == (descriptor,)
        reports, pid = outcomes[len(spawned)]
        if reports:
            os.write(descriptor, arguments[1].removeprefix(":").encode() + b"\n")
        spawned.append((arguments, FakeXvfb(pid)))
        return spawned[-1][1]

    libraries = SimpleNamespace(
        x=SimpleNamespace(XConnectionNumber=Mock(return_value=peer.fileno()), XCloseDisplay=Mock(),
                          XFlush=Mock()),
        test=SimpleNamespace(XTestFakeKeyEvent=Mock(), XTestFakeButtonEvent=Mock()),
        connections=set())
    monkeypatch.setattr(browser_desktop.shutil, "which", lambda _: "/usr/bin/Xvfb")
    monkeypatch.setattr(browser_desktop, "_libraries", lambda: libraries)
    monkeypatch.setattr(browser_desktop.asyncio, "create_subprocess_exec", spawn)
    desktop = BrowserDesktop()
    monkeypatch.setattr(desktop, "_connect", lambda: 7)
    monkeypatch.setattr(desktop, "_initialize_connection", lambda: None)
    return desktop, spawned, libraries


async def test_display_waits_for_xvfb_report_and_verifies_its_peer(tmp_path, monkeypatch):
    local, peer = socket.socketpair()
    try:
        # A socket pair's peer is this process; a matching pid stands for the owned Xvfb.
        desktop, spawned, libraries = fake_display(monkeypatch, local, [(False, os.getpid()),
                                                                       (True, os.getpid())])
        environment = await desktop.start(tmp_path, 800, 600)
        try:
            arguments, process = spawned[-1]
            assert environment["DISPLAY"] == arguments[1]
            assert "-nolisten" in arguments and "tcp" in arguments and "-auth" in arguments
            assert spawned[0][1].terminated and not process.terminated
            libraries.x.XCloseDisplay.assert_not_called()
        finally:
            await desktop.close()
        assert process.terminated and not list(tmp_path.iterdir())
    finally:
        local.close()
        peer.close()


async def test_display_served_by_another_process_is_refused(tmp_path, monkeypatch):
    local, peer = socket.socketpair()
    try:
        desktop, spawned, libraries = fake_display(monkeypatch, local, [(True, os.getpid() + 1)] * 8)
        with pytest.raises(OSError, match="could not start"):
            await desktop.start(tmp_path, 800, 600)
        assert len(spawned) == 8 and all(process.terminated for _, process in spawned)
        assert libraries.x.XCloseDisplay.call_count == 8
        assert not list(tmp_path.iterdir())
    finally:
        local.close()
        peer.close()
