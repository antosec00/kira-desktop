"""DeepSeek account sign-in and Messages transport.

The account flow follows DeepSeek Harness' first-party PKCE integration. It
does not import Harness code or copy browser cookies; the exchanged grant is
kept in Kira's private profile and sent only to the configured DeepSeek origin.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import hashlib
import hmac
import json
import os
import platform
import secrets
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx

from kira import __version__
from kira.backends.deepseek_context import MAX_DEEPSEEK_OUTPUT_TOKENS, context_policy
from kira.backends.ollama import (
    CAPABILITIES, DISCOVERY_TIMEOUT, MAX_DISCOVERY_BYTES, MAX_MODELS, OllamaBackend, StreamError,
    _json_size, _model_name,
)
from kira.domain.contracts import Json, KiraError, RunContext

PLATFORM = "https://platform.deepseek.com"
API = "https://api.deepseek.com"
AUTH_TIMEOUT = 600.0
MAX_AUTH_BYTES = 65_536
PNG_DATA_URL = "data:image/png;base64,"
MAX_IMAGE_BASE64_CHARS = 12_000_000
DEEPSEEK_CAPABILITIES = {**CAPABILITIES, "tools": True, "context_controls": True}
BUILTIN_MODEL_CATALOG: tuple[Json, ...] = (
    {"id": "deepseek-flash", "name": "DeepSeek V4.1 Flash", "context_window": 1_000_000,
     "max_output_tokens": 256_000, "input_modalities": ["text", "image"],
     "api_capabilities": {"messages": {"tool_calling": True}},
     "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"}},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "context_window": 1_000_000,
     "max_output_tokens": 256_000, "input_modalities": ["text"],
     "api_capabilities": {"messages": {"tool_calling": True}},
     "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"}},
)


class DeepSeekAuthError(StreamError):
    """Safe, non-secret DeepSeek account failure."""


class DeepSeekContextOverflow(StreamError):
    """A bounded HTTP rejection explicitly identifying an oversized context."""

    context_overflow = True


class DeepSeekHTTPError(httpx.HTTPStatusError):
    """HTTP failure with a bounded, whitelisted provider diagnostic."""

    def __init__(self, response: httpx.Response, detail: str) -> None:
        super().__init__(f"DeepSeek account HTTP {response.status_code}",
                         request=response.request, response=response)
        self.safe_detail = detail


def _safe_error_detail(body: bytes, token: str) -> str:
    """Keep only bounded code/type/message fields; discard the rest of the body."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error", payload)
    if not isinstance(error, dict):
        return ""
    parts = []
    for key in ("code", "type", "message"):
        value = error.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        cleaned = " ".join(str(value).split())
        if token:
            cleaned = cleaned.replace(token, "[redacted]")
        cleaned = cleaned[:240]
        if cleaned:
            parts.append(f"{key}={cleaned}")
    return "; ".join(parts)[:720]


def _discovery_error_code(exc: Exception) -> str:
    """Return a bounded, stable category for a provider-catalog failure."""
    if isinstance(exc, DeepSeekAuthError):
        return "AUTH_REJECTED"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "AUTH_REJECTED"
        if status == 403:
            return "ACCESS_DENIED"
        return f"HTTP_{status}"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, httpx.HTTPError):
        return "NETWORK_ERROR"
    if isinstance(exc, (ValueError, StreamError)):
        return "INVALID_RESPONSE"
    return "PROVIDER_ERROR"


def _confirmed_context_overflow(body: bytes) -> bool:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    strings: list[str] = []
    pending: list[Any] = [payload]
    while pending and len(strings) < 32:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(item for key, item in value.items()
                           if key in {"error", "message", "type", "code", "details"})
        elif isinstance(value, list):
            pending.extend(value[:32])
        elif isinstance(value, str):
            strings.append(value[:2048].casefold())
    terms = ("context length exceeded", "context window exceeded", "maximum context length",
             "context limit exceeded", "input tokens exceed", "prompt is too long",
             "maximum prompt length", "prompt token count exceeds", "too many input tokens")
    return any(term in value for value in strings for term in terms)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _validate_proxy_url(value: str) -> None:
    try:
        bounded = isinstance(value, str) and len(value.encode("utf-8")) <= 4096
    except UnicodeError:
        bounded = False
    if not bounded or "\0" in value:
        raise KiraError("INVALID_ARGUMENT", "DeeperSeeker URL must be bounded text")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise KiraError("INVALID_ARGUMENT", "DeeperSeeker URL is invalid") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise KiraError("INVALID_ARGUMENT", "DeeperSeeker URL requires http(s), no credentials, query or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise KiraError("INVALID_ARGUMENT", "DeeperSeeker URL has an invalid port") from exc
    # The API key and every prompt/tool result travel to this URL: plain HTTP
    # is limited to a proxy on this machine.
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in {"127.0.0.1", "::1", "localhost"}:
        raise KiraError("INVALID_ARGUMENT", "DeeperSeeker URL requires https unless it is on this machine")


class DeepSeekAccount:
    """Own one DeepSeek Harness account grant and its loopback PKCE callback."""

    def __init__(self, directory: Path,
                 protect_secrets: Callable[[tuple[str, ...]], None] | None = None) -> None:
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise KiraError("UNSAFE_PATH", "DeepSeek credential storage cannot be a symlink")
        self.directory.chmod(0o700)
        info = self.directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise KiraError("UNSAFE_PATH", "DeepSeek credential storage must be private and user-owned")
        self.path = self.directory / "account.json"
        self.proxy_path = self.directory / "deeperseeker.json"
        self.protect_secrets = protect_secrets
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8),
                                        follow_redirects=False, trust_env=False)
        self.token = ""
        self.proxy_url = "http://127.0.0.1:4000"
        self.proxy_api_key = ""
        self.proxy_enabled = False
        self.device_id = str(uuid.uuid4())
        self.state: Json = {"phase": "idle", "connected": False}
        self.listener: asyncio.AbstractServer | None = None
        self.pending: Json | None = None
        self.finish_task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()
        self._load()
        self._load_proxy()

    def _load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise KiraError("UNSAFE_PATH", "DeepSeek credential file must be private and user-owned")
            # The finally clause owns the descriptor; it is closed exactly once.
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_AUTH_BYTES + 1)
            value = json.loads(raw)
            if (len(raw) > MAX_AUTH_BYTES or not isinstance(value, dict)
                    or value.get("version") != 1 or not isinstance(value.get("token"), str)
                    or not isinstance(value.get("device_id"), str)):
                raise ValueError
            uuid.UUID(value["device_id"])
            if value["token"] and any(ord(c) < 33 or ord(c) > 126 for c in value["token"]):
                raise ValueError
            self.token = value["token"]
            self.device_id = value["device_id"]
            if self.token and self.protect_secrets:
                self.protect_secrets((self.token,))
            self.state = {"phase": "signed_in" if self.token else "idle",
                          "connected": bool(self.token)}
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise KiraError("CREDENTIAL_STORE", "DeepSeek account storage is invalid") from exc
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _load_proxy(self) -> None:
        try:
            fd = os.open(self.proxy_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise KiraError("UNSAFE_PATH", "DeeperSeeker credentials must be private and user-owned")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_AUTH_BYTES + 1)
            value = json.loads(raw)
            if (len(raw) > MAX_AUTH_BYTES or not isinstance(value, dict)
                    or value.get("version") != 1 or not isinstance(value.get("url"), str)
                    or not isinstance(value.get("api_key"), str)
                    or not isinstance(value.get("enabled"), bool)):
                raise ValueError
            _validate_proxy_url(value["url"])
            if len(value["api_key"]) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value["api_key"]):
                raise ValueError
            if value["enabled"] and not value["api_key"]:
                raise ValueError
            self.proxy_url = value["url"].rstrip("/")
            self.proxy_api_key = value["api_key"]
            self.proxy_enabled = value["enabled"]
            if self.protect_secrets:
                self.protect_secrets(tuple(x for x in (self.token, self.proxy_api_key) if x))
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise KiraError("CREDENTIAL_STORE", "DeeperSeeker credential record is invalid") from exc
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def configure_proxy(self, url: str, api_key: str, enabled: bool) -> Json:
        _validate_proxy_url(url)
        try:
            key_bounded = isinstance(api_key, str) and len(api_key.encode("utf-8")) <= 4096
        except UnicodeError:
            key_bounded = False
        if (type(enabled) is not bool or not key_bounded
                or any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
            raise KiraError("INVALID_ARGUMENT", "DeeperSeeker API key must be bounded printable text")
        if not api_key and self.proxy_api_key:
            api_key = self.proxy_api_key
        if enabled and not api_key:
            raise KiraError("AUTH_REQUIRED", "Enter the DeeperSeeker API key in Kira settings")
        value = {"version": 1, "url": url.rstrip("/"), "api_key": api_key, "enabled": enabled}
        data = json.dumps(value, separators=(",", ":")).encode("utf-8")
        temporary = self.directory / f".deeperseeker-{secrets.token_hex(16)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.proxy_path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        self.proxy_url = url.rstrip("/")
        self.proxy_api_key = api_key
        self.proxy_enabled = enabled
        if self.protect_secrets:
            self.protect_secrets(tuple(x for x in (self.token, api_key) if x))
        return {"connected": bool(enabled and api_key), "mode": "deeperseeker" if enabled else "account",
                "url": self.proxy_url}

    def _save(self, token: str) -> None:
        if self.protect_secrets:
            self.protect_secrets(tuple(value for value in (token, self.proxy_api_key) if value))
        data = json.dumps({"version": 1, "token": token, "device_id": self.device_id},
                          separators=(",", ":")).encode()
        if len(data) > MAX_AUTH_BYTES:
            raise KiraError("CREDENTIAL_STORE", "DeepSeek credential record is too large")
        temporary = self.directory / f".account-{secrets.token_hex(16)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def status(self) -> Json:
        return {"connected": bool(self.token), "login": dict(self.state)}

    async def expire(self, rejected_token: str) -> None:
        if self.token != rejected_token:
            return
        self._save("")
        self.token = ""
        self.state = {"phase": "expired", "connected": False}

    async def login(self) -> Json:
        async with self._lock:
            if self.pending is not None:
                raise KiraError("BUSY", "DeepSeek sign-in is already in progress")
            verifier = _b64(secrets.token_bytes(32))
            state = _b64(secrets.token_bytes(32))
            challenge = _b64(hashlib.sha256(verifier.encode()).digest())
            self.listener = await asyncio.start_server(self._callback, "127.0.0.1", 0,
                                                       limit=16_384)
            port = self.listener.sockets[0].getsockname()[1]
            redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"
            pending: Json = {"state": state, "verifier": verifier,
                             "redirect_uri": redirect_uri, "authorize_id": "",
                             "code": "", "expires_at": time.time() + AUTH_TIMEOUT,
                             "callback_event": asyncio.Event(), "callback_writer": None}
            self.pending = pending
            self.state = {"phase": "initializing", "connected": bool(self.token)}
            headers = self._client_headers()
            try:
                payload = await self._platform_post("auth_init", {
                    "code_challenge": challenge, "code_challenge_method": "S256",
                    "state": state, "redirect_uri": redirect_uri,
                    "locale": "en_US", "login_source": "desktop",
                }, headers)
            except (httpx.HTTPError, DeepSeekAuthError):
                await self.cancel()
                raise
            data = payload.get("data", {}).get("biz_data", {}) if isinstance(payload.get("data"), dict) else {}
            authorize_url = data.get("authorize_url")
            authorize_id = data.get("authorize_id")
            expires_in = data.get("expires_in")
            if (not isinstance(authorize_url, str) or not isinstance(authorize_id, str)
                    or not authorize_id or not isinstance(expires_in, (int, float))
                    or isinstance(expires_in, bool) or expires_in <= 0):
                await self.cancel()
                raise DeepSeekAuthError("DeepSeek sign-in initialization returned an invalid response")
            target = urlsplit(authorize_url)
            if (target.scheme != "https" or target.netloc != "platform.deepseek.com"
                    or target.path != "/dsh/authorize" or target.username or target.password
                    or target.fragment):
                await self.cancel()
                raise DeepSeekAuthError("DeepSeek returned an untrusted sign-in destination")
            pending["authorize_id"] = authorize_id
            pending["expires_at"] = min(pending["expires_at"], time.time() + float(expires_in))
            self.state = {"phase": "waiting_browser", "connected": bool(self.token),
                          "expires_at": pending["expires_at"]}
            self.finish_task = asyncio.create_task(self._finish_login(pending))
            return {**self.status(), "authorization_url": authorize_url}

    @staticmethod
    def _client_headers() -> dict[str, str]:
        return {
            "x-client-bundle-id": "", "x-client-platform": _platform_id(),
            "x-client-version": __version__, "x-client-locale": "en_US",
            "x-client-timezone-offset": "0",
        }

    async def _platform_post(self, action: str, body: Json,
                             headers: dict[str, str] | None = None) -> Json:
        response = await self.client.post(f"{PLATFORM}/auth-api/v0/dsh/{action}", json=body,
                                          headers={"Content-Type": "application/json", **(headers or {})})
        if response.status_code != 200 or len(response.content) > MAX_AUTH_BYTES:
            raise DeepSeekAuthError(f"DeepSeek sign-in request failed (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeepSeekAuthError("DeepSeek sign-in returned invalid data") from exc
        if (not isinstance(payload, dict) or payload.get("code") != 0
                or not isinstance(payload.get("data"), dict)
                or payload["data"].get("biz_code") != 0
                or not isinstance(payload["data"].get("biz_data"), dict)):
            raise DeepSeekAuthError("DeepSeek sign-in was not accepted")
        return payload

    async def _callback(self, reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            if len(raw) > 16_384:
                raise ValueError
            lines = raw.decode("ascii").split("\r\n")
            method, target, version = lines[0].split(" ")
            hosts = [line.split(":", 1)[1].strip() for line in lines[1:]
                     if line.lower().startswith("host:")]
            pending = self.pending
            parsed = urlsplit(target)
            if (method != "GET" or version not in {"HTTP/1.0", "HTTP/1.1"}
                    or pending is None or hosts != [urlsplit(pending["redirect_uri"]).netloc]
                    or parsed.path != "/oauth/callback" or parsed.fragment):
                raise ValueError
            query = parse_qs(parsed.query, strict_parsing=True)
            states = query.get("state", [])
            codes = query.get("code", [])
            if (len(states) != 1 or len(codes) != 1 or not codes[0]
                    or not hmac.compare_digest(states[0], pending["state"])):
                raise ValueError
            pending["code"] = codes[0]
            pending["callback_writer"] = writer
            event = pending.get("callback_event")
            if not isinstance(event, asyncio.Event):
                raise ValueError
            await asyncio.wait_for(event.wait(), max(0.1, float(pending["expires_at"]) - time.time()))
            completion = pending.get("completion_url")
            if not isinstance(completion, str):
                raise ValueError
            body = b"Access complete. You can return to Kira."
            writer.write((f"HTTP/1.1 302 Found\r\nLocation: {completion}\r\n"
                          f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                          "Connection: close\r\n\r\n").encode("ascii") + body)
            await writer.drain()
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                TimeoutError):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 13\r\nConnection: close\r\n\r\nInvalid login")
            with contextlib.suppress(Exception):
                await writer.drain()
        finally:
            pending = self.pending
            if pending is not None and pending.get("callback_writer") is writer:
                pending["callback_writer"] = None
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _finish_login(self, pending: Json) -> None:
        try:
            deadline = float(pending["expires_at"])
            while self.pending is pending and time.time() < deadline and not pending.get("code"):
                await asyncio.sleep(0.1)
            if self.pending is not pending:
                return
            if not pending.get("code"):
                self.state = {"phase": "expired", "connected": bool(self.token)}
                return
            self.state = {"phase": "exchanging", "connected": bool(self.token)}
            payload = await self._platform_post("auth_exchange", {
                "code": pending["code"], "code_verifier": pending["verifier"],
                "redirect_uri": pending["redirect_uri"], "device_id": self.device_id,
                "device_model": f"{platform.system()}-{platform.machine()}",
                "os_version": platform.release(),
            }, self._client_headers())
            data = payload["data"]["biz_data"]
            token = data.get("token")
            authorized_url = data.get("authorized_url")
            if (not isinstance(token, str) or not token or len(token) > 8192
                    or not isinstance(authorized_url, str)):
                raise DeepSeekAuthError("DeepSeek account exchange returned invalid data")
            target = urlsplit(authorized_url)
            if target.scheme != "https" or target.netloc != "platform.deepseek.com" or target.path != "/dsh/authorized":
                raise DeepSeekAuthError("DeepSeek returned an untrusted completion destination")
            self._save(token)
            self.token = token
            self.state = {"phase": "signed_in", "connected": True}
            pending["completion_url"] = authorized_url
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, DeepSeekAuthError, KiraError, OSError):
            self.state = {"phase": "failed", "connected": bool(self.token),
                          "error": "DeepSeek sign-in failed; retry from Kira."}
        finally:
            callback_event = pending.get("callback_event")
            if isinstance(callback_event, asyncio.Event):
                callback_event.set()
            if self.pending is pending:
                self.pending = None
            if self.listener is not None:
                self.listener.close()
                await self.listener.wait_closed()
                self.listener = None

    async def cancel(self) -> Json:
        pending, self.pending = self.pending, None
        if self.finish_task is not None and self.finish_task is not asyncio.current_task():
            self.finish_task.cancel()
            await asyncio.gather(self.finish_task, return_exceptions=True)
        if self.listener is not None:
            self.listener.close()
            await self.listener.wait_closed()
            self.listener = None
        if pending and pending.get("authorize_id"):
            with contextlib.suppress(Exception):
                await self._platform_post("auth_cancel", {
                    "authorize_id": pending["authorize_id"], "code_verifier": pending["verifier"]
                }, self._client_headers())
        self.state = {"phase": "cancelled", "connected": bool(self.token)}
        return self.status()

    async def logout(self) -> Json:
        token = self.token
        self._save("")
        self.token = ""
        self.state = {"phase": "idle", "connected": False}
        if token:
            with contextlib.suppress(Exception):
                await self.client.post(f"{PLATFORM}/auth-api/v0/users/logout",
                                       headers={"x-dsh-auth-token": token})
        return self.status()

    async def close(self) -> None:
        if self.pending is not None:
            await self.cancel()
        await self.client.aclose()


def _platform_id() -> str:
    if os.name == "nt":
        return "desktop-win"
    if platform.system() == "Darwin":
        return "desktop-mac"
    return "web"


class DeepSeekAccounts:
    """Service lifecycle and admission for the DeepSeek account credential."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self._auth: DeepSeekAccount | None = None
        self.changing = False

    @property
    def auth(self) -> DeepSeekAccount:
        if self._auth is None:
            self._auth = DeepSeekAccount(
                self.service.paths.config / "deepseek-account",
                protect_secrets=functools.partial(self.service.redactor.set_provider_values,
                                                  owner="deepseek_account"))
        return self._auth

    def busy(self) -> bool:
        db = self.service.store.db
        checks = (
            "SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id WHERE s.backend=? "
            "AND (r.state NOT IN ('completed','failed','cancelled','interrupted') "
            "OR EXISTS (SELECT 1 FROM project_reservations p WHERE p.owner_kind='run' AND p.owner_id=r.id)) LIMIT 1",
            "SELECT 1 FROM team_runs t WHERE t.backend=? AND t.state NOT IN "
            "('completed','failed','cancelled','interrupted','paused') LIMIT 1",
            "SELECT 1 FROM prompt_queue q JOIN sessions s ON s.id=q.session_id WHERE s.backend=? "
            "AND q.status IN ('pending','starting','running') LIMIT 1",
        )
        return self.changing or any(db.execute(query, ("deepseek_account",)).fetchone() for query in checks)

    async def dispatch(self, action: str, params: Json | None = None) -> Json:
        params = params or {}
        if self.service.backend_factory is not None and self._auth is None:
            if action == "status":
                return {"connected": False, "login": {"phase": "idle"}}
            raise KiraError("UNAVAILABLE", "DeepSeek account sign-in is unavailable in the test backend")
        if action == "status":
            return {**self.auth.status(), "proxy": {"enabled": self.auth.proxy_enabled,
                    "url": self.auth.proxy_url, "configured": bool(self.auth.proxy_api_key)}}
        if self.busy():
            raise KiraError("BUSY", "Finish DeepSeek requests before changing account access")
        self.changing = True
        try:
            if action == "login":
                return await self.auth.login()
            if action == "cancel":
                return await self.auth.cancel()
            if action == "logout":
                return await self.auth.logout()
            if action == "configure_proxy":
                result = self.auth.configure_proxy(params.get("url", self.auth.proxy_url),
                    params.get("api_key", ""), params.get("enabled", False))
                provider = self.service._control_backends.pop("deepseek_account", None)
                if provider:
                    await provider.close()
                return result
            raise KiraError("INVALID_ARGUMENT", "Unsupported DeepSeek account action")
        finally:
            self.changing = False

    async def prepare(self) -> None:
        """Admit DeepSeek work unless account access is changing.

        Active and queued DeepSeek work never blocks admission (the queue calls
        this for its own 'starting' item); busy() guards only account mutations.
        """
        if self.changing:
            raise KiraError("BUSY", "DeepSeek account settings are changing")
        if self.service.backend_factory is not None and self._auth is None:
            return
        if self.auth.pending is not None:
            raise KiraError("BUSY", "Finish or cancel DeepSeek sign-in before starting DeepSeek work")
        if self.auth.proxy_enabled:
            if not self.auth.proxy_api_key:
                raise KiraError("AUTH_REQUIRED", "Configure the DeeperSeeker API key in Kira settings")
        elif not self.auth.token:
            raise KiraError("AUTH_REQUIRED", "Sign in with your DeepSeek account before using this provider")

    async def close(self) -> None:
        if self._auth is not None:
            await self._auth.close()
            self._auth = None


class DeepSeekAccountBackend(OllamaBackend):
    """Use DeepSeek's account grant with its Anthropic-compatible Messages API.

    The inherited Kira tool loop supplies the same service callbacks, permission
    checks, browser images, questions, and history tools as Ollama. This class
    translates only the provider wire format.
    """

    def __init__(self, account: DeepSeekAccount) -> None:
        super().__init__(account.proxy_url if account.proxy_enabled else API)
        self.account = account
        self._details = {}
        self._probed = {}

    supports_durable_context = True

    async def compact_context(self, ctx: RunContext) -> Json:
        from kira.backends.deepseek_context import DeepSeekContext
        return await DeepSeekContext(self, ctx).compact_now()

    async def _show(self, model: str, *, refresh: bool = False) -> Json:
        model = _model_name(model)
        if not refresh and model in self._details:
            return self._details[model]
        await self._load_catalog()
        if model not in self._details:
            raise StreamError("Selected model is not available in the DeepSeek account catalog")
        return self._details[model]

    async def discover(self) -> Json:
        try:
            proxy = self.account.proxy_enabled
            if proxy and not self.account.proxy_api_key:
                return {"available": False, "models": [], "capabilities": {
                    **DEEPSEEK_CAPABILITIES, "images": False},
                    "error_code": "AUTH_REQUIRED",
                    "error": "Configure the DeeperSeeker API key in Kira settings"}
            if not proxy and not self.account.token:
                return {"available": False, "models": [], "capabilities": {
                    **DEEPSEEK_CAPABILITIES, "images": False},
                    "error_code": "AUTH_REQUIRED",
                    "error": "Sign in to DeepSeek to list account models"}
            async with asyncio.timeout(DISCOVERY_TIMEOUT):
                token = self.account.proxy_api_key if proxy else self.account.token
                if self._details:
                    await self._show(next(iter(self._details)), refresh=True)
                else:
                    await self._load_catalog()
                current = self.account.proxy_api_key if proxy else self.account.token
                if token != current:
                    raise DeepSeekAuthError("DeepSeek provider credentials changed during discovery")
            models = []
            for name, detail in self._details.items():
                probe = self._probe_outcome("deepseek_account", name, detail)
                verified = probe == "passed"
                info = detail.get("model_info", {})
                effort = info.get("effort", {}) if isinstance(info, dict) else {}
                levels = effort.get("supported_levels", []) if isinstance(effort, dict) else []
                if isinstance(levels, list) and "off" not in levels:
                    levels = ["off", *levels]
                models.append({"id": name, "name": detail["name"],
                    "capabilities": {**DEEPSEEK_CAPABILITIES, "tools": verified,
                                     "images": "vision" in detail["capabilities"]},
                    "tools_metadata": "tools" in detail["capabilities"],
                    "tools_verified": verified, "tool_probe": probe,
                    "context_window": detail.get("context_window"),
                    "max_output_tokens": detail.get("max_output_tokens"),
                    "supported_reasoning_efforts": levels if isinstance(levels, list) else [],
                    "catalog_fallback": detail.get("catalog_fallback") is True})
            result = {"available": True, "models": models,
                      "capabilities": {**DEEPSEEK_CAPABILITIES,
                                       "images": any("vision" in item.get("capabilities", [])
                                                     for item in self._details.values())},
                      "account": ({"connected": True, "mode": "deeperseeker", "url": self.account.proxy_url}
                                  if proxy else self.account.status())}
            if _json_size(result) > MAX_DISCOVERY_BYTES:
                raise StreamError("DeepSeek model catalog exceeds its response limit")
            return result
        except (httpx.HTTPError, ValueError, StreamError, TimeoutError, DeepSeekAuthError) as exc:
            return {"available": False, "models": [], "capabilities": {
                    **DEEPSEEK_CAPABILITIES, "images": False},
                    "error_code": _discovery_error_code(exc),
                    "error": f"DeepSeek provider unavailable: {str(exc)[:200]}"}

    async def _load_catalog(self) -> None:
        proxy = self.account.proxy_enabled
        token = self.account.proxy_api_key if proxy else self.account.token
        if not token:
            raise DeepSeekAuthError("Configure provider credentials before listing models")
        try:
            response = await self.client.get("/v1/models" if proxy else "/models",
                headers={"Authorization": f"Bearer {token}"} if proxy else {"x-dsh-auth-token": token}, timeout=20)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401 and not proxy:
                await self.account.expire(token)
            raise
        if len(response.content) > 512_000:
            raise StreamError("DeepSeek model catalog exceeds its size limit")
        try:
            value = response.json()
        except ValueError as exc:
            raise StreamError("DeepSeek returned an invalid model catalog") from exc
        entries = value.get("data") if isinstance(value, dict) else None
        if not isinstance(entries, list) or len(entries) > MAX_MODELS:
            raise StreamError("DeepSeek model catalog is invalid")
        if not entries:
            entries = [{**item, "catalog_fallback": True} for item in BUILTIN_MODEL_CATALOG]
        self._details = {}
        for row in entries:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            modalities = row.get("input_modalities", [])
            caps = row.get("api_capabilities", {})
            if proxy:
                provider_caps = row.get("capabilities", {})
                image_supported = (isinstance(provider_caps, dict)
                    and isinstance(provider_caps.get("image_input"), dict)
                    and provider_caps["image_input"].get("supported") is True)
                modalities = ["image"] if image_supported else []
                caps = {"messages": {"tool_calling": True}}
                effort_meta = provider_caps.get("effort", {}) if isinstance(provider_caps, dict) else {}
                if isinstance(effort_meta, dict) and effort_meta.get("supported"):
                    row["effort"] = {"supported_levels": [level for level in ("low", "medium", "high")
                        if isinstance(effort_meta.get(level), dict) and effort_meta[level].get("supported")],
                        "default_level": "medium"}
            message_caps = caps.get("messages", {}) if isinstance(caps, dict) else {}
            supports_tools = message_caps.get("tool_calling", True) if isinstance(message_caps, dict) else True
            model_caps = [*( ["tools"] if supports_tools else []),
                          *( ["vision"] if isinstance(modalities, list) and "image" in modalities else [])]
            self._details[row["id"]] = {"name": row.get("name", row["id"]),
                "context_window": row.get("context_window"), "max_output_tokens": row.get("max_output_tokens"),
                "capabilities": model_caps, "model_info": row,
                "catalog_fallback": row.get("catalog_fallback") is True}
        if not self._details:
            for row in BUILTIN_MODEL_CATALOG:
                modalities = row["input_modalities"]
                self._details[row["id"]] = {
                    "name": row["name"], "context_window": row["context_window"],
                    "max_output_tokens": row["max_output_tokens"],
                    "capabilities": ["tools", *( ["vision"] if "image" in modalities else [])],
                    "model_info": row, "catalog_fallback": True,
                }

    async def _stream(self, body: Json, ctx: RunContext | None = None,
                      item_id: str | None = None) -> tuple[Json, Json]:
        proxy = self.account.proxy_enabled
        token = self.account.proxy_api_key if proxy else self.account.token
        if not token:
            raise DeepSeekAuthError("Configure provider credentials before using this provider")
        source = body.get("messages", [])
        if ctx is not None and ctx.image_inputs.get(ctx.run_id):
            model_detail = self._details.get(ctx.model, {})
            if "vision" not in model_detail.get("capabilities", []):
                raise StreamError("The selected DeepSeek model does not accept image inputs")
            source = _bind_active_prompt(source, ctx)
        messages, system = _anthropic_messages(source, ctx.image_inputs if ctx else {})
        if body.get("format"):
            system += "\nReturn one JSON object matching this schema, with no surrounding text: " + json.dumps(body["format"])
        requested_output = body.get("max_tokens", MAX_DEEPSEEK_OUTPUT_TOKENS)
        if type(requested_output) is not int or requested_output <= 0:
            raise StreamError("DeepSeek max_tokens must be a positive integer")
        request_cap = min(requested_output, MAX_DEEPSEEK_OUTPUT_TOKENS)
        # The context policy admits input only below the window minus its
        # reserve, so output capped at that reserve always fits beside it.
        policy = context_policy(self._details.get(body["model"], {}))
        if policy["context_window"]:
            request_cap = min(request_cap, max(1, policy["reserved_tokens"]))
        request: Json = {"model": body["model"], "max_tokens": request_cap,
                         "messages": messages, "stream": True}
        if ctx:
            detail = self._details.get(ctx.model, {})
            maximum = detail.get("max_output_tokens")
            if isinstance(maximum, int) and maximum > 0:
                request["max_tokens"] = min(request_cap, maximum)
            byte_limit = ctx.settings.get("max_output_bytes")
            if type(byte_limit) is int and byte_limit > 0:
                request["max_tokens"] = min(request["max_tokens"], max(1, byte_limit // 4))
            effort = ctx.settings.get("reasoning_effort")
            if isinstance(effort, str) and effort in {"low", "medium", "high", "max"}:
                request["thinking"] = {"type": "enabled"}
                request["output_config"] = {"effort": effort}
        if system:
            request["system"] = system
        tools = body.get("tools", [])
        if isinstance(tools, list) and tools:
            request["tools"] = [{"name": item["function"]["name"],
                "description": item["function"].get("description", ""),
                "input_schema": item["function"].get("parameters", {"type": "object"})}
                for item in tools if isinstance(item, dict) and isinstance(item.get("function"), dict)]
        think = body.get("think")
        effort = ctx.settings.get("reasoning_effort") if ctx else None
        model_info = self._details.get(body["model"], {}).get("model_info", {})
        effort_info = model_info.get("effort", {}) if isinstance(model_info, dict) else {}
        levels = effort_info.get("supported_levels", []) if isinstance(effort_info, dict) else []
        default_effort = effort_info.get("default_level") if isinstance(effort_info, dict) else None
        selected_effort = effort if isinstance(effort, str) and effort else default_effort
        if levels and (not isinstance(selected_effort, str) or selected_effort not in levels):
            selected_effort = "high" if "high" in levels else str(levels[0])
        if think is False or think in {"off", "false", "disabled"} or selected_effort == "off":
            request["thinking"] = {"type": "disabled"}
        elif think is True:
            request["thinking"] = {"type": "enabled"}
        elif isinstance(selected_effort, str):
            request["thinking"] = {"type": "enabled"}
            if selected_effort in {"low", "high", "max"}:
                request["output_config"] = {"effort": selected_effort}
            elif selected_effort == "medium":
                request["output_config"] = {"effort": "high"}
        if not request.get("thinking", {}).get("type") == "enabled" and isinstance(body.get("options"), dict):
            temperature = body["options"].get("temperature")
            if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
                request["temperature"] = temperature
        text = ""
        blocks: dict[int, Json] = {}
        tool_calls: list[Json] = []
        input_tokens = output_tokens = None
        stop_reason = ""
        message_stopped = False
        incomplete_tool_input = False
        total = 0
        limit = int(ctx.settings.get("max_output_bytes", 4 * 1024 * 1024)) if ctx else 65_536
        async with self.client.stream("POST", "/v1/messages" if proxy else "/anthropic/v1/messages", json=request,
            headers=({"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"}
                     if proxy else {"x-dsh-auth-token": token, "anthropic-version": "2023-06-01"}) | {
                     "user-agent": f"Kira-Desktop/{__version__}"}, timeout=180) as response:
            if response.status_code == 401 and not proxy:
                await self.account.expire(token)
            if response.status_code >= 400:
                error_body = bytearray()
                async for chunk in response.aiter_bytes():
                    error_body.extend(chunk[:max(0, 65_537 - len(error_body))])
                    if len(error_body) > 65_536:
                        break
                body_bytes = bytes(error_body[:65_536])
                if response.status_code in {400, 413} and _confirmed_context_overflow(body_bytes):
                    raise DeepSeekContextOverflow("DeepSeek rejected the request for context capacity")
                raise DeepSeekHTTPError(response, _safe_error_detail(body_bytes, token))
            response.raise_for_status()
            event = ""
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if len(line.encode("utf-8")) > 1_048_576:
                    raise StreamError("DeepSeek stream record exceeds its size limit")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line and data_lines:
                    raw = "\n".join(data_lines)
                    data_lines.clear()
                    total += len(raw.encode("utf-8"))
                    if total > limit * 2:
                        raise StreamError("DeepSeek response exceeded configured output limit")
                    try:
                        record = json.loads(raw)
                    except (ValueError, UnicodeError) as exc:
                        raise StreamError("DeepSeek emitted invalid Messages data") from exc
                    if not isinstance(record, dict):
                        raise StreamError("DeepSeek emitted an invalid Messages event")
                    if event == "message_start":
                        usage = record.get("message", {}).get("usage", {})
                        input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
                    elif event == "content_block_start":
                        index = record.get("index")
                        block = record.get("content_block")
                        if type(index) is not int or not isinstance(block, dict):
                            raise StreamError("DeepSeek emitted an invalid content block")
                        blocks[index] = dict(block)
                        if block.get("type") == "text" and isinstance(block.get("text"), str):
                            text += block["text"]
                            if ctx and block["text"]:
                                await ctx.emit("message.delta", {"content": block["text"], "item_id": item_id})
                    elif event == "content_block_delta":
                        index = record.get("index")
                        delta = record.get("delta")
                        if type(index) is not int or not isinstance(delta, dict):
                            raise StreamError("DeepSeek emitted an invalid content delta")
                        if delta.get("type") == "text_delta":
                            value = delta.get("text")
                            if not isinstance(value, str):
                                raise StreamError("DeepSeek emitted invalid text")
                            text += value
                            total += len(value.encode("utf-8"))
                            if total > limit:
                                raise StreamError("DeepSeek response exceeded configured output limit")
                            if ctx and value:
                                await ctx.emit("message.delta", {"content": value, "item_id": item_id})
                        elif delta.get("type") == "input_json_delta":
                            block = blocks.get(index)
                            if block is None or block.get("type") != "tool_use":
                                raise StreamError("DeepSeek emitted tool input without a tool call")
                            block["partial_json"] = block.get("partial_json", "") + str(delta.get("partial_json", ""))
                    elif event == "message_delta":
                        delta = record.get("delta", {})
                        usage = record.get("usage", {})
                        stop_reason = str(delta.get("stop_reason", "")) if isinstance(delta, dict) else ""
                        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
                    elif event == "error":
                        raise StreamError("DeepSeek Messages request failed")
                    elif event == "message_stop":
                        message_stopped = True
                        break
                    event = ""
        if not message_stopped:
            raise StreamError("DeepSeek Messages stream ended without message_stop; outcome is unknown")
        for index, block in sorted(blocks.items()):
            if block.get("type") != "tool_use":
                continue
            if stop_reason == "max_tokens":
                # A response cut at its output cap may end inside any call, and
                # a call without input deltas would otherwise run with {}.
                incomplete_tool_input = True
                continue
            arguments = block.get("input", {})
            if block.get("partial_json"):
                try:
                    arguments = json.loads(block["partial_json"])
                except ValueError:
                    # Never pass partial provider JSON to a Kira tool. Treat
                    # the whole response as incomplete so complete sibling
                    # calls from the same truncated response cannot run either.
                    incomplete_tool_input = True
                    continue
            if not isinstance(arguments, dict) or not isinstance(block.get("name"), str):
                raise StreamError("DeepSeek returned invalid tool arguments")
            tool_calls.append({"id": block.get("id", f"deepseek-{index}"),
                "type": "function", "function": {"name": block["name"], "arguments": arguments}})
        if incomplete_tool_input:
            tool_calls.clear()
        message: Json = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        terminal: Json = {"done": True, "done_reason": "tool_use" if tool_calls else
                          "length" if stop_reason == "max_tokens" else
                          "tool_input_incomplete" if incomplete_tool_input else stop_reason,
                          "prompt_eval_count": input_tokens, "eval_count": output_tokens,
                          "max_output_tokens": request["max_tokens"],
                          "tool_input_incomplete": incomplete_tool_input}
        return message, terminal


def _png_block(data: str) -> Json:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _attachment_image_block(image: Any) -> Json:
    """Convert one Kira-normalized PNG attachment into a Messages image block."""
    if (not isinstance(image, dict) or image.get("type") != "input_image"
            or image.get("detail") != "auto" or not isinstance(image.get("image_url"), str)
            or not image["image_url"].startswith(PNG_DATA_URL)):
        raise StreamError("DeepSeek received an invalid Kira image attachment")
    data = image["image_url"][len(PNG_DATA_URL):]
    if len(data) > MAX_IMAGE_BASE64_CHARS:
        raise StreamError("DeepSeek image attachment exceeds its request limit")
    return _png_block(data)


def _tool_image_block(image: Any) -> Json:
    """Convert a browser/Computer tool image (bare base64 PNG or data URL) into a block."""
    data = image[len(PNG_DATA_URL):] if isinstance(image, str) and image.startswith(PNG_DATA_URL) else image
    if not isinstance(data, str) or not data or len(data) > MAX_IMAGE_BASE64_CHARS:
        raise StreamError("DeepSeek received an invalid or oversized Kira tool image")
    try:
        base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise StreamError("DeepSeek received an invalid Kira tool image") from exc
    return _png_block(data)


def _bind_active_prompt(source: Any, ctx: RunContext) -> list[Json]:
    """Attach this run's images to its own request, never to later tool results.

    The active request is the latest user message without a recorded run
    identity whose text is this run's prompt; history items carry their run.
    """
    if not isinstance(source, list):
        raise StreamError("Invalid provider message history")
    for index in range(len(source) - 1, -1, -1):
        message = source[index]
        if (isinstance(message, dict) and message.get("role") == "user"
                and "run_id" not in message and message.get("content") == ctx.prompt):
            return [*source[:index], {**message, "run_id": ctx.run_id}, *source[index + 1:]]
    raise StreamError("DeepSeek image inputs have no matching user turn")


def _anthropic_messages(source: Any, image_inputs: dict[str, list[Json]]) -> tuple[list[Json], str]:
    if not isinstance(source, list):
        raise StreamError("Invalid provider message history")
    system: list[str] = []
    result: list[Json] = []
    pending_ids: dict[str, list[str]] = {}

    def append(role: str, blocks: list[Json]) -> None:
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": blocks})

    for message in source:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str):
                system.append(content)
            continue
        if role == "assistant":
            assistant_blocks: list[Json] = []
            if isinstance(content, str) and content:
                assistant_blocks.append({"type": "text", "text": content})
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    continue
                function = call["function"]
                name, args = function.get("name"), function.get("arguments")
                identity = call.get("id")
                if not isinstance(identity, str) or not identity:
                    identity = f"kira-tool-{len(pending_ids.get(str(name), []))}"
                if not isinstance(name, str) or not isinstance(args, dict):
                    continue
                assistant_blocks.append({"type": "tool_use", "id": identity, "name": name, "input": args})
                pending_ids.setdefault(name, []).append(identity)
            if assistant_blocks:
                append("assistant", assistant_blocks)
        elif role == "tool":
            name = message.get("tool_name")
            if not isinstance(name, str):
                continue
            identity = message.get("tool_call_id")
            candidates = pending_ids.get(name, [])
            if not isinstance(identity, str) or not identity:
                identity = candidates.pop(0) if candidates else f"kira-tool-result-{len(result)}"
            elif identity in candidates:
                candidates.remove(identity)
            value = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            images = message.get("images", [])
            image_blocks = [_tool_image_block(image) for image in images] if isinstance(images, list) else []
            result_content: str | list[Json] = (
                [{"type": "text", "text": value}, *image_blocks] if image_blocks else value)
            append("user", [{"type": "tool_result", "tool_use_id": identity, "content": result_content}])
        elif role == "user":
            if isinstance(content, str):
                user_blocks: list[Json] = [{"type": "text", "text": content}] if content else []
                identity = message.get("run_id")
                images = image_inputs.get(identity, []) if isinstance(identity, str) else []
                user_blocks.extend(_attachment_image_block(image) for image in images)
                if user_blocks:
                    append("user", user_blocks)
    if not result or result[-1]["role"] != "user":
        result.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return result, "\n\n".join(system)
