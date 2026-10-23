"""Public HTTP(S) boundary for the isolated browser.

DNS answers are validated as a set and the actual socket uses one of those
numeric addresses. Chromium's TLS verification still runs through CONNECT.
The proxy never follows redirects: each redirected connection is checked anew.
"""

import asyncio
import base64
import hmac
import ipaddress
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from urllib.parse import SplitResult, urlsplit

MAX_HEADERS = 32768
MAX_CONNECTIONS = 32
MAX_CONNECTION_BYTES = 32 * 1024 * 1024
# The network budget restarts with every navigation Kira or the user requests
# and otherwise rolls over per window, bounding traffic a page makes by itself.
BUDGET_WINDOW = 300.0
MAX_WINDOW_REQUESTS = 3000
MAX_WINDOW_BYTES = 128 * 1024 * 1024
# A relayed connection closes after this long without data in either direction.
IDLE_TIMEOUT = 60.0
NETWORK_BUDGET_MESSAGE = (
    "Kira's network limits for this browser blocked or cut some requests, so the page may be "
    "incomplete. The budget restarts with the next navigation and every five minutes.")
Resolver = Callable[[str, int], Awaitable[list[str]]]


class PublicWebError(ValueError):
    """A destination cannot be used by the public-web browser."""


class NetworkBudgetExhausted(PublicWebError):
    """Kira's request or transfer allowance for the browser is used up."""


class NetworkBudget:
    """Request and transfer allowance shared by one browser's route and proxy.

    ``reset`` starts a fresh allowance for an explicit navigation; otherwise the
    counters roll over every BUDGET_WINDOW seconds. ``limited`` records that a
    limit blocked or cut traffic since the last explicit navigation.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._window = clock()
        self.requests = 0
        self.bytes = 0
        self.limited = False

    def reset(self) -> None:
        self._window = self._clock()
        self.requests = 0
        self.bytes = 0
        self.limited = False

    def _roll(self) -> None:
        if self._clock() - self._window >= BUDGET_WINDOW:
            self._window = self._clock()
            self.requests = 0
            self.bytes = 0

    def request(self) -> bool:
        self._roll()
        if self.requests >= MAX_WINDOW_REQUESTS:
            self.limited = True
            return False
        self.requests += 1
        return True

    def transfer(self, size: int) -> bool:
        self._roll()
        self.bytes += size
        if self.bytes > MAX_WINDOW_BYTES:
            self.limited = True
            return False
        return True

    def accepts_connection(self) -> bool:
        self._roll()
        if self.bytes >= MAX_WINDOW_BYTES:
            self.limited = True
            return False
        return True


def public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Exclude address translation/tunnelling and protocol assignments as well
        # as local addresses, including mapped private IPv4 addresses.
        return (
            address in ipaddress.ip_network("2000::/3")
            and address not in ipaddress.ip_network("2001::/23")
            and address not in ipaddress.ip_network("2002::/16")
        )
    return address not in ipaddress.ip_network("192.0.0.0/24")


def validate_url(value: str) -> SplitResult:
    if (not isinstance(value, str) or len(value) > 8192 or not value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or "\\" in value):
        raise PublicWebError("A bounded absolute HTTP(S) URL is required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise PublicWebError("Invalid web URL") from exc
    if (parsed.scheme not in {"http", "https"} or not hostname
            or parsed.username is not None or parsed.password is not None
            or "%" in hostname or hostname.endswith(".")):
        raise PublicWebError("Only public HTTP(S) URLs without credentials are supported")
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        raise PublicWebError("Only HTTP port 80 and HTTPS port 443 are supported")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ascii_name = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise PublicWebError("Invalid hostname") from exc
        if (len(ascii_name) > 253 or "." not in ascii_name
                or any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
                       or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in part)
                       for part in ascii_name.lower().split("."))
                or ascii_name.lower().endswith((".localhost", ".local", ".internal", ".home"))):
            raise PublicWebError("A public hostname is required")
    else:
        if not public_address(hostname):
            raise PublicWebError("Local, private and special-use addresses are blocked")
    return parsed


async def resolve_addresses(host: str, port: int) -> list[str]:
    answers = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    return list(dict.fromkeys(str(item[4][0]) for item in answers))


async def resolve_public(host: str, port: int, resolver: Resolver = resolve_addresses) -> list[str]:
    try:
        addresses = await asyncio.wait_for(resolver(host.encode("idna").decode("ascii"), port), 8)
    except (OSError, UnicodeError, TimeoutError) as exc:
        raise PublicWebError("The public hostname could not be resolved") from exc
    if not addresses or len(addresses) > 64 or any(not public_address(ip) for ip in addresses):
        raise PublicWebError("DNS returned a local, private or special-use address")
    return addresses


class PublicWebProxy:
    """Loopback-only authenticated proxy with pinned public destinations."""

    def __init__(self, *, resolver: Resolver = resolve_addresses) -> None:
        self.username = "kira"
        self.password = secrets.token_urlsafe(32)
        self._authorization = "Basic " + base64.b64encode(
            f"{self.username}:{self.password}".encode("ascii")
        ).decode("ascii")
        self._resolver = resolver
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.budget = NetworkBudget()
        self.denied_requests = 0
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=MAX_HEADERS)
        self.port = int(self._server.sockets[0].getsockname()[1])

    @property
    def configuration(self) -> dict[str, str]:
        return {"server": f"http://127.0.0.1:{self.port}", "username": self.username,
                "password": self.password, "bypass": "<-loopback>"}

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._tasks) >= MAX_CONNECTIONS or not self.budget.accepts_connection():
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        connected = False
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            if len(raw) > MAX_HEADERS:
                raise PublicWebError("Proxy request headers exceed the limit")
            lines = raw.decode("iso-8859-1").split("\r\n")
            method, target, version = lines[0].split(" ")
            if version not in {"HTTP/1.0", "HTTP/1.1"} or not method.isalpha():
                raise PublicWebError("Invalid HTTP request")
            headers: dict[str, str] = {}
            for line in lines[1:-2]:
                key, value = line.split(":", 1)
                if not key or key != key.strip() or key.lower() in headers:
                    raise PublicWebError("Ambiguous HTTP headers")
                headers[key.lower()] = value.strip()
            if not hmac.compare_digest(headers.get("proxy-authorization", "").encode("iso-8859-1"),
                                       self._authorization.encode("ascii")):
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\n'
                             b'Proxy-Authenticate: Basic realm="Kira"\r\n'
                             b'Connection: close\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            if method == "CONNECT":
                parsed = validate_url("https://" + target)
                if parsed.path or parsed.query or parsed.fragment or parsed.port != 443:
                    raise PublicWebError("Invalid CONNECT authority")
                port = 443
            else:
                parsed = validate_url(target)
                if parsed.scheme != "http":
                    raise PublicWebError("HTTPS requires CONNECT")
                port = 80
            assert parsed.hostname is not None
            addresses = await resolve_public(parsed.hostname, port, self._resolver)
            # Keep DNS pins unchanged while allowing an unreachable first address
            # (commonly IPv6 on an IPv4-only host) to fall back within one budget.
            candidates = [addresses[0]]
            other_family = next((ip for ip in addresses if (":" in ip) != (":" in addresses[0])), None)
            if other_family:
                candidates.append(other_family)
            candidates.extend(ip for ip in addresses if ip not in candidates)
            async with asyncio.timeout(10):
                for address in candidates[:4]:
                    try:
                        remote_reader, upstream = await asyncio.wait_for(
                            asyncio.open_connection(address, port, limit=MAX_HEADERS), 3,
                        )
                        break
                    except (OSError, TimeoutError):
                        continue
                else:
                    raise PublicWebError("No validated public address could be reached")
            assert upstream is not None
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                headers.pop("proxy-authorization", None)
                headers.pop("proxy-connection", None)
                headers["connection"] = "close"
                headers["host"] = parsed.netloc
                request = f"{method} {path} {version}\r\n" + "".join(
                    f"{key}: {value}\r\n" for key, value in headers.items()
                ) + "\r\n"
                upstream.write(request.encode("iso-8859-1"))
                await upstream.drain()
            connected = True
            await self._relay(reader, writer, remote_reader, upstream)
        except (ValueError, OSError, TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError) as exc:
            # A relayed connection that resets or times out was not denied;
            # only refusals and Kira's own transfer limits count.
            if not connected or isinstance(exc, NetworkBudgetExhausted):
                self.denied_requests += 1
            if not connected and not writer.is_closing():
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n"
                             b"Content-Length: 0\r\n\r\n")
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            writer.close()
            if upstream:
                upstream.close()

    async def _relay(self, client: asyncio.StreamReader, client_out: asyncio.StreamWriter,
                     remote: asyncio.StreamReader, remote_out: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        transferred = 0
        active = loop.time()

        async def copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
            nonlocal transferred, active
            while data := await source.read(65536):
                active = loop.time()
                transferred += len(data)
                if transferred > MAX_CONNECTION_BYTES:
                    self.budget.limited = True
                    raise NetworkBudgetExhausted("Browser connection transfer limit reached")
                if not self.budget.transfer(len(data)):
                    raise NetworkBudgetExhausted("Browser network budget exhausted")
                destination.write(data)
                await destination.drain()
                active = loop.time()

        tasks = [asyncio.create_task(copy(client, remote_out)),
                 asyncio.create_task(copy(remote, client_out))]
        try:
            # Streams such as WebSockets and server-sent events stay open while
            # either direction transfers data; an idle connection closes quietly.
            while (remaining := active + IDLE_TIMEOUT - loop.time()) > 0:
                done, _ = await asyncio.wait(tasks, timeout=remaining,
                                             return_when=asyncio.FIRST_COMPLETED)
                if done:
                    for task in done:
                        task.result()
                    return
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
