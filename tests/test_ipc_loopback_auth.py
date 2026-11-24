"""Loopback TCP transport (the Windows one), forced on this platform: no secret, no command."""

import asyncio
import json
import stat
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira import __version__
from kira.client import Client
from kira.domain import ipc
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.ui.ipc import LocalClient


@pytest.fixture
def paths(tmp_path):
    value = Paths(*(tmp_path / part for part in ("config", "data", "state", "run")))
    value.ensure()
    return value


@pytest.fixture
def tcp(monkeypatch):
    """Service, command-line client and interface all choose loopback TCP."""
    monkeypatch.setattr(ipc, "tcp_transport", lambda: True)


def frame(method, params=None, identifier="request"):
    return json.dumps({"version": 1, "id": identifier, "method": method,
                       "params": params or {}}).encode() + b"\n"


async def exchange(port, payload):
    """Send bytes to the service; return its first reply and whatever follows until it closes."""
    reader, writer = await asyncio.open_connection(ipc.LOOPBACK, port)
    try:
        writer.write(payload)
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), 5))
        rest = await asyncio.wait_for(reader.read(), 5)
        return reply, rest
    finally:
        writer.close()
        await writer.wait_closed()


def test_endpoint_file_carries_port_and_secret_together(tmp_path):
    endpoint = tmp_path / "service.sock"
    token = ipc.new_token()
    ipc.write_endpoint(endpoint, 54321, token)
    assert stat.S_IMODE(endpoint.stat().st_mode) == 0o600
    assert ipc.read_endpoint(endpoint) == (ipc.LOOPBACK, 54321, token)
    assert list(tmp_path.iterdir()) == [endpoint]
    assert len(token) == ipc.TOKEN_CHARACTERS and token != ipc.new_token()
    assert ipc.token_matches(token, token)
    assert not ipc.token_matches(token, token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert not ipc.token_matches(token, None) and not ipc.token_matches("", "")
    # A service that predates authentication wrote only the address.
    endpoint.write_text("127.0.0.1:54321\n", encoding="utf-8")
    assert ipc.read_endpoint(endpoint) == (ipc.LOOPBACK, 54321, "")
    for invalid in ("", "127.0.0.1\n", "example.org:80\n" + token + "\n", "127.0.0.1:0\n",
                    "127.0.0.1:99999\n", "127.0.0.1:80\nnot-a-secret\n",
                    "127.0.0.1:80\n" + token + "\nextra\n"):
        endpoint.write_text(invalid, encoding="utf-8")
        with pytest.raises(ValueError):
            ipc.read_endpoint(endpoint)


async def test_loopback_service_reads_no_command_before_the_secret(tcp, paths, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    create = frame("project.create", {"name": "Injected", "root": str(project_root)})
    service = Service(paths)
    await service.start()
    try:
        assert paths.socket.is_file() and stat.S_IMODE(paths.socket.stat().st_mode) == 0o600
        host, port, token = ipc.read_endpoint(paths.socket)
        assert host == ipc.LOOPBACK and len(token) == ipc.TOKEN_CHARACTERS

        # No secret: the handshake itself is refused and the connection closed.
        reply, rest = await exchange(port, frame("hello") + create)
        assert reply["error"]["code"] == "UNAUTHENTICATED" and reply["id"] == "request" and rest == b""

        # A wrong secret, a secret among other parameters, a secret for another protocol version.
        wrong = token[:-1] + ("0" if token[-1] != "0" else "1")
        for attempt in (frame(ipc.AUTH_METHOD, {"token": wrong}),
                        frame(ipc.AUTH_METHOD, {"token": token, "extra": True}),
                        frame(ipc.AUTH_METHOD, {"token": [token]}),
                        json.dumps({"version": 2, "id": "request", "method": ipc.AUTH_METHOD,
                                    "params": {"token": token}}).encode() + b"\n"):
            reply, rest = await exchange(port, attempt + create)
            assert reply["error"]["code"] == "UNAUTHENTICATED" and rest == b""

        # A web page posting to the port: the request line is not the secret, so
        # the command in the body is never read.
        reply, rest = await exchange(
            port, b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\n\r\n"
            + frame(ipc.AUTH_METHOD, {"token": token}) + create)
        assert reply["error"]["code"] == "UNAUTHENTICATED" and reply["id"] is None and rest == b""

        # An oversized first frame is not parsed at all.
        reply, rest = await exchange(port, frame(ipc.AUTH_METHOD, {"token": token + " " * ipc.AUTH_FRAME_BYTES}))
        assert reply["error"]["code"] == "UNAUTHENTICATED" and rest == b""

        assert (await service.dispatch("project.list", {}))["projects"] == []
        assert not service.clients

        # The right secret: the same bytes are now accepted and executed.
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(frame(ipc.AUTH_METHOD, {"token": token}, "first") + frame("hello", identifier="second"))
        await writer.drain()
        assert json.loads(await reader.readline()) == {
            "version": 1, "id": "first", "result": {"authenticated": True}}
        hello = json.loads(await reader.readline())
        assert hello["id"] == "second" and hello["result"]["app_version"] == __version__
        writer.close()
        await writer.wait_closed()

        async with Client(paths) as client:
            assert (await client.request("project.list"))["projects"] == []
    finally:
        await service.close()
    assert not paths.socket.exists()


async def test_silent_stranger_is_dropped_and_never_registered(tcp, paths, monkeypatch):
    monkeypatch.setattr(ipc, "AUTH_TIMEOUT", 0.3)
    service = Service(paths)
    await service.start()
    try:
        _, port, _ = ipc.read_endpoint(paths.socket)
        reader, writer = await asyncio.open_connection(ipc.LOOPBACK, port)
        await asyncio.sleep(0.05)
        # Waiting for the secret, the peer is not a client: it gets no broadcast.
        assert not service.clients
        assert await asyncio.wait_for(reader.read(), 5) == b""
        writer.close()
        await writer.wait_closed()
        assert not service.clients
    finally:
        await service.close()


async def test_client_reports_a_stale_secret_as_an_unreachable_service(tcp, paths):
    service = Service(paths)
    await service.start()
    try:
        _, port, token = ipc.read_endpoint(paths.socket)
        ipc.write_endpoint(paths.socket, port, ipc.new_token())
        with pytest.raises(ConnectionError, match="secret"):
            await Client(paths).connect()
        ipc.write_endpoint(paths.socket, port, token)
        async with Client(paths) as client:
            assert (await client.request("hello"))["app_version"] == __version__
    finally:
        await service.close()


async def test_client_still_reaches_a_service_that_predates_authentication(tcp, paths):
    """An update must be able to ask the previous version's idle service to stop."""
    received = []

    async def old_service(reader, writer):
        while line := await reader.readline():
            request = json.loads(line)
            received.append(request["method"])
            writer.write(json.dumps({"version": 1, "id": request["id"],
                                     "result": {"version": 1, "app_version": "0.1.0"}}).encode() + b"\n")
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(old_service, ipc.LOOPBACK, 0)
    paths.socket.write_text(f"127.0.0.1:{server.sockets[0].getsockname()[1]}\n", encoding="utf-8")
    async with server:
        client = Client(paths)
        hello = await client.connect(same_version=False)
        assert hello["app_version"] == "0.1.0"
        await client.request("service.shutdown")
        await client.close()
    assert received == ["hello", "service.shutdown"]


class ServiceThread:
    """A real service on its own event loop, as it runs next to the interface."""

    def __init__(self, paths):
        self.paths = paths
        self.started = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)
        self.thread.start()
        assert self.started.wait(20)
        if self.failure is not None:
            raise self.failure

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        service = Service(self.paths)
        try:
            await service.start()
        except BaseException as exc:
            self.failure = exc
            self.started.set()
            return
        self.started.set()
        await self.stop.wait()
        await service.close()

    def close(self):
        if self.failure is None and self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(20)
        assert not self.thread.is_alive()


def test_interface_finds_the_service_again_after_it_restarts(tcp, paths, qapp, qtbot):
    running = ServiceThread(paths)
    client = LocalClient(str(paths.socket))
    client._retry.setInterval(20)
    problems = []
    client.problem.connect(problems.append)
    try:
        first = ipc.read_endpoint(paths.socket)
        client.connect_service()
        qtbot.waitUntil(lambda: client.compatible, timeout=10000)
        answers = []
        client.request("project.list", {}, answers.append)
        qtbot.waitUntil(lambda: bool(answers), timeout=10000)
        assert answers == [{"projects": []}]

        running.close()
        qtbot.waitUntil(lambda: not client.compatible, timeout=10000)
        assert not client.incompatible

        running = ServiceThread(paths)
        second = ipc.read_endpoint(paths.socket)
        # Another start: another secret, and the interface must read it again.
        assert second[2] != first[2]
        qtbot.waitUntil(lambda: client.compatible, timeout=10000)
        client.request("project.list", {}, answers.append)
        qtbot.waitUntil(lambda: len(answers) == 2, timeout=10000)
        assert not client.incompatible

        # A secret that is not the running service's: refused, retried, never
        # mistaken for another version; the right one is picked up by itself.
        client.socket.abort()
        ipc.write_endpoint(paths.socket, second[1], ipc.new_token())
        qtbot.waitUntil(lambda: bool(problems), timeout=10000)
        qtbot.wait(200)
        assert not client.compatible and not client.incompatible and client._retry.isActive()
        refused = []
        assert not client.request("project.list", {}, answers.append, refused.append)
        assert refused[0]["code"] == "DISCONNECTED" and len(answers) == 2
        ipc.write_endpoint(paths.socket, second[1], second[2])
        qtbot.waitUntil(lambda: client.compatible, timeout=10000)
    finally:
        client.close()
        client.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        running.close()
