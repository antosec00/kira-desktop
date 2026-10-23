import asyncio
import base64

import pytest

from kira.tools import web_network
from kira.tools.web_network import PublicWebError, PublicWebProxy, resolve_public, validate_url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/a", "javascript:alert(1)",
    "http://localhost/", "http://127.0.0.1/", "http://169.254.169.254/latest/",
    "http://10.1.2.3/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[64:ff9b::7f00:1]/", "http://[2002:7f00:1::]/", "http://192.0.0.9/",
    "https://example.com:8443/", "https://user:secret@example.com/",
    "https://example.com\\@127.0.0.1/", "https://example.com/\nHost: localhost",
    "http://2130706433/", "http://0x7f000001/", "http://host.local/",
    "https://%65xample.com/", "https://example.com./", "http://[ff02::1]/",
])
def test_url_rejects_non_public_destinations(url):
    with pytest.raises(PublicWebError):
        validate_url(url)


@pytest.mark.parametrize("url", [
    "https://example.com/article?q=value#heading", "http://example.com:80/",
    "https://93.184.216.34:443/", "https://[2606:4700:4700::1111]/",
    "https://münchen.example/article",
])
def test_public_url_shapes(url):
    assert validate_url(url).scheme in {"http", "https"}


@pytest.mark.parametrize("addresses", [[], ["127.0.0.1"], ["93.184.216.34", "10.0.0.1"],
                                       ["2606:4700:4700::1111", "fe80::1"], ["invalid"]])
async def test_dns_rejects_entire_mixed_result(addresses):
    async def resolver(host, port):
        return addresses

    with pytest.raises(PublicWebError):
        await resolve_public("example.com", 443, resolver)


def authorization(proxy):
    value = base64.b64encode(f"{proxy.username}:{proxy.password}".encode()).decode()
    return f"Proxy-Authorization: Basic {value}\r\n"


async def proxy_request(proxy, target, *, authenticated=True, method="GET"):
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    auth = authorization(proxy) if authenticated else ""
    writer.write(f"{method} {target} HTTP/1.1\r\nHost: example.com\r\n{auth}\r\n".encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), 2)
    writer.close()
    await writer.wait_closed()
    return response


async def proxy_client_only(original_connect, proxy, refused, host, port, **kwargs):
    """Let the test client reach the proxy; record and refuse every other destination.

    Used by fakes patched over asyncio.open_connection, so a regression in the
    destination checks fails the test instead of opening a real connection.
    """
    if (host, port) == ("127.0.0.1", proxy.port):
        return await original_connect(host, port, **kwargs)
    refused.append((host, port))
    raise AssertionError(f"Unexpected outbound connection to {host}:{port}")


async def test_proxy_authentication_precedes_dns(monkeypatch):
    calls = []
    refused = []
    original_connect = asyncio.open_connection

    async def connect(host, port, **kwargs):
        return await proxy_client_only(original_connect, proxy, refused, host, port, **kwargs)

    async def resolver(host, port):
        calls.append((host, port))
        return ["93.184.216.34"]

    monkeypatch.setattr(web_network.asyncio, "open_connection", connect)
    proxy = PublicWebProxy(resolver=resolver)
    await proxy.start()
    try:
        response = await proxy_request(proxy, "http://example.com/", authenticated=False)
        assert b"407 Proxy Authentication Required" in response
        assert not calls
        response = await proxy_request(proxy, "http://127.0.0.1/")
        assert b"403 Forbidden" in response
        assert not calls and not refused
    finally:
        await proxy.close()


async def test_proxy_connects_pinned_ip_and_never_forwards_proxy_credentials(monkeypatch):
    requests = []
    connections = []
    resolutions = []

    async def fixture(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fixture, "127.0.0.1", 0)
    fixture_port = server.sockets[0].getsockname()[1]
    original_connect = asyncio.open_connection
    refused = []

    async def connect(host, port, **kwargs):
        if host == "93.184.216.34":
            connections.append((host, port))
            return await original_connect("127.0.0.1", fixture_port, **kwargs)
        return await proxy_client_only(original_connect, proxy, refused, host, port, **kwargs)

    async def resolver(host, port):
        resolutions.append((host, port))
        return ["93.184.216.34"] if len(resolutions) == 1 else ["127.0.0.1"]

    monkeypatch.setattr(web_network.asyncio, "open_connection", connect)
    proxy = PublicWebProxy(resolver=resolver)
    await proxy.start()
    try:
        response = await proxy_request(proxy, "http://example.com/article?q=1")
        assert response.endswith(b"OK")
        assert connections == [("93.184.216.34", 80)]
        assert len(resolutions) == 1  # No second DNS resolution while opening the socket.
        assert b"GET /article?q=1 HTTP/1.1" in requests[0]
        assert b"host: example.com" in requests[0]
        assert b"proxy-authorization" not in requests[0].lower()
        # A later DNS rebind is rejected on a new connection.
        assert b"403 Forbidden" in await proxy_request(proxy, "http://example.com/again")
        assert len(connections) == 1 and not refused
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()


async def test_redirect_target_is_checked_as_new_destination(monkeypatch):
    async def fixture(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/\r\n"
                     b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fixture, "127.0.0.1", 0)
    fixture_port = server.sockets[0].getsockname()[1]
    original_connect = asyncio.open_connection
    sockets = []
    refused = []

    async def connect(host, port, **kwargs):
        if host == "93.184.216.34":
            sockets.append(host)
            return await original_connect("127.0.0.1", fixture_port, **kwargs)
        return await proxy_client_only(original_connect, proxy, refused, host, port, **kwargs)

    async def resolver(host, port):
        return ["93.184.216.34"]

    monkeypatch.setattr(web_network.asyncio, "open_connection", connect)
    proxy = PublicWebProxy(resolver=resolver)
    await proxy.start()
    try:
        first = await proxy_request(proxy, "http://example.com/")
        assert b"302 Found" in first
        second = await proxy_request(proxy, "http://169.254.169.254/latest/")
        assert b"403 Forbidden" in second
        assert len(sockets) == 1 and not refused
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()


async def test_authenticated_connect_pins_https_and_tunnels_bytes(monkeypatch):
    pinned = []

    async def fixture(reader, writer):
        writer.write(await reader.readexactly(4))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fixture, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    original_connect = asyncio.open_connection
    refused = []

    async def connect(host, target_port, **kwargs):
        if host == "93.184.216.34":
            pinned.append((host, target_port))
            return await original_connect("127.0.0.1", port, **kwargs)
        return await proxy_client_only(original_connect, proxy, refused, host, target_port,
                                       **kwargs)

    async def resolver(host, port):
        return ["93.184.216.34"]

    monkeypatch.setattr(web_network.asyncio, "open_connection", connect)
    proxy = PublicWebProxy(resolver=resolver)
    await proxy.start()
    try:
        reader, writer = await original_connect("127.0.0.1", proxy.port)
        writer.write(("CONNECT example.com:443 HTTP/1.1\r\n"
                      + authorization(proxy) + "\r\n").encode())
        await writer.drain()
        assert b"200 Connection Established" in await reader.readuntil(b"\r\n\r\n")
        writer.write(b"data")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(4), 2) == b"data"
        assert pinned == [("93.184.216.34", 443)]
        writer.close()
        await writer.wait_closed()
        assert b"403 Forbidden" in await proxy_request(proxy, "example.com:80", method="CONNECT")
        assert not refused
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()


async def test_proxy_enforces_transfer_budget(monkeypatch):
    monkeypatch.setattr(web_network, "MAX_WINDOW_BYTES", 3)
    proxy = PublicWebProxy()
    proxy.budget.bytes = 3
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
        assert proxy.budget.limited
    finally:
        await proxy.close()


async def test_proxy_fails_over_between_validated_addresses_without_resolving_again(monkeypatch):
    connections = []
    resolutions = []

    async def fixture(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fixture, "127.0.0.1", 0)
    fixture_port = server.sockets[0].getsockname()[1]
    original_connect = asyncio.open_connection
    refused = []

    async def connect(host, port, **kwargs):
        if host == "2606:4700:4700::1111":
            connections.append((host, port))
            raise OSError("Synthetic unreachable IPv6 route")
        if host == "93.184.216.34":
            connections.append((host, port))
            return await original_connect("127.0.0.1", fixture_port, **kwargs)
        return await proxy_client_only(original_connect, proxy, refused, host, port, **kwargs)

    async def resolver(host, port):
        resolutions.append((host, port))
        return ["2606:4700:4700::1111", "93.184.216.34"]

    monkeypatch.setattr(web_network.asyncio, "open_connection", connect)
    proxy = PublicWebProxy(resolver=resolver)
    await proxy.start()
    try:
        assert (await proxy_request(proxy, "http://example.com/")).endswith(b"OK")
        assert connections == [("2606:4700:4700::1111", 80), ("93.184.216.34", 80)]
        assert resolutions == [("example.com", 80)] and not refused
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()
