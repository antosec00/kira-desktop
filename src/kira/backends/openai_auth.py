"""Independent Sign in with ChatGPT public-client credentials.

Only the service owns this manager. Tokens never cross IPC or enter event history.
The lifetime file lock also excludes a second process from rotating a token.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from kira.domain.contracts import Json, KiraError

ISSUER = "https://auth.openai.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
PENDING_STATES = frozenset({"awaiting_browser", "exchanging"})
LOGIN_TIMEOUT = 600.0
AUTH_REQUEST_TIMEOUT = 20.0
MAX_STORE = 2_097_152
MAX_RESPONSE = 1_048_576
MAX_ACCOUNTS = 32
# Tolerated difference between this machine's clock and the issuer's when
# validating ID-token times; a clock running behind would otherwise reject a
# freshly issued token and discard the tokens rotated with it.
CLOCK_SKEW_SECONDS = 120
TERMINAL_REFRESH_ERRORS = frozenset({
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
})


def _error(code: str, message: str) -> KiraError:
    return KiraError(code, message)


def _text(value: Any, maximum: int = 65_536) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise _error("OPENAI_AUTH_INVALID", "OpenAI returned invalid authentication data.")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error("OPENAI_AUTH_INVALID", "OpenAI returned invalid token expiry data.")
    return float(value)


class ChatGPTAuth:
    """Private multi-registration OAuth state, independent of Codex and API keys."""

    def __init__(self, directory: Path, *, client: httpx.AsyncClient | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 protect_secrets: Callable[[tuple[str, ...]], None] | None = None):
        if client is not None and transport is not None:
            raise ValueError("Supply either client or transport")
        self._directory_fd = -1
        self._lock_fd = -1
        self._closed = False
        self._protect_secrets = protect_secrets
        self._lock = asyncio.Lock()
        self._listener: asyncio.Server | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        self._exchange_task: asyncio.Task[Any] | None = None
        self._handlers: set[asyncio.Task[Any]] = set()
        self._pending: Json | None = None
        self._login: Json = {"state": "idle"}
        self._discovery: Json | None = None
        self._owns_client = client is None
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._check_private(self._directory_fd, directory=True)
            self._lock_fd = self._open_private("credentials.lock", os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _error("OPENAI_AUTH_BUSY", "Another Kira service owns the ChatGPT account store.") from None
            self._data = self._load()
            self._protect_credentials(self._data)
        except (OSError, ValueError, KiraError) as exc:
            self._close_files()
            if isinstance(exc, KiraError):
                raise
            raise _error("OPENAI_AUTH_STORE", "The private ChatGPT account store cannot be opened safely.") from None
        self._client = client or httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(15.0), follow_redirects=False, trust_env=False,
        )

    @staticmethod
    def _check_private(fd: int, *, directory: bool = False) -> None:
        info = os.fstat(fd)
        correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not correct_type or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise _error("OPENAI_AUTH_STORE", "ChatGPT account storage must be owned by you and private.")
        if not directory and info.st_nlink != 1:
            raise _error("OPENAI_AUTH_STORE", "ChatGPT account files must not have additional hard links.")

    def _open_private(self, name: str, flags: int) -> int:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self._directory_fd)
        try:
            self._check_private(fd)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _load(self) -> Json:
        try:
            fd = self._open_private("accounts.json", os.O_RDONLY)
        except FileNotFoundError:
            result: Json = {"version": 1, "host_id": f"urn:uuid:{uuid.uuid4()}",
                            "active_account_id": None, "accounts": {}}
            self._save(result)
            return result
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(MAX_STORE + 1)
        try:
            result = json.loads(raw)
            if (len(raw) > MAX_STORE or not isinstance(result, dict) or result.get("version") != 1
                    or not re.fullmatch(r"urn:uuid:[0-9a-f-]{36}", result.get("host_id", ""))
                    or not isinstance(result.get("accounts"), dict)
                    or len(result["accounts"]) > MAX_ACCOUNTS):
                raise ValueError
            for key, row in result["accounts"].items():
                if (not isinstance(key, str) or not re.fullmatch(r"[0-9a-f-]{36}", key)
                        or not isinstance(row, dict) or row.get("id") != key
                        or row.get("issuer") != ISSUER or not isinstance(row.get("client_id"), str)
                        or not row["client_id"] or row["client_id"] == "dynamic_agent_client"
                        or len(row["client_id"]) > 256 or not isinstance(row.get("label"), str)
                        or not isinstance(row.get("scopes"), list)
                        or any(not isinstance(s, str) for s in row["scopes"])):
                    raise ValueError
                for field in ("subject", "email", "access_token", "refresh_token", "id_token", "nonce"):
                    if row.get(field) is not None and not isinstance(row[field], str):
                        raise ValueError
                for field in ("expires_at", "earliest_refresh_at"):
                    _number(row.get(field, 0))
            if result.get("active_account_id") is not None and result["active_account_id"] not in result["accounts"]:
                raise ValueError
            return dict(result)
        except (ValueError, TypeError, RecursionError, KiraError):
            raise _error("OPENAI_AUTH_STORE", "The ChatGPT account store is invalid; restore a private backup.") from None

    def _save(self, data: Json | None = None) -> None:
        content = json.dumps(data if data is not None else self._data, allow_nan=False).encode()
        if len(content) > MAX_STORE:
            raise _error("OPENAI_AUTH_STORE", "ChatGPT account storage exceeds its size limit.")
        name = f".accounts-{secrets.token_hex(16)}.tmp"
        try:
            # Refuse an unexpectedly replaced destination, including a symlink.
            try:
                old_fd = self._open_private("accounts.json", os.O_RDONLY)
            except FileNotFoundError:
                pass
            else:
                os.close(old_fd)
            fd = self._open_private(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, "accounts.json", src_dir_fd=self._directory_fd, dst_dir_fd=self._directory_fd)
            os.fsync(self._directory_fd)
        except OSError:
            raise _error("OPENAI_AUTH_STORE", "ChatGPT credentials could not be saved privately.") from None
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=self._directory_fd)

    def _commit(self, data: Json) -> None:
        # Protect a rotated value before it could be used or accidentally echoed
        # by a trusted-host command, including when persistence fails afterward.
        self._protect_credentials(data)
        self._save(data)
        self._data = data

    def _protect_credentials(self, data: Json) -> None:
        if self._protect_secrets is not None:
            self._protect_secrets(tuple(value for row in data["accounts"].values()
                for key in ("access_token", "refresh_token", "id_token")
                if isinstance(value := row.get(key), str) and value))

    def _store_account(self, row: Json, *, activate: bool = False) -> None:
        data = {**self._data, "accounts": {**self._data["accounts"], row["id"]: row}}
        if activate:
            data["active_account_id"] = row["id"]
        self._commit(data)

    def _ensure_open(self) -> None:
        if self._closed:
            raise _error("OPENAI_AUTH_CLOSED", "The ChatGPT account manager is closed.")

    def _ensure_idle(self) -> None:
        self._ensure_open()
        if self._login["state"] in PENDING_STATES:
            raise _error("OPENAI_AUTH_BUSY", "Finish or cancel the current ChatGPT sign-in first.")

    def _account(self, account_id: str | None) -> Json:
        selected = self._data["active_account_id"] if account_id is None else account_id
        row = self._data["accounts"].get(selected)
        if row is None:
            raise _error("OPENAI_SIGN_IN_REQUIRED", "Select an account and continue with ChatGPT first.")
        return dict(row)

    def _status(self) -> Json:
        accounts = []
        for row in self._data["accounts"].values():
            connected = bool(row.get("access_token") and (
                row.get("refresh_token") or row.get("expires_at", 0) > time.time()))
            accounts.append({"id": row["id"], "label": row["label"], "connected": connected,
                             "plan_enabled": connected and PLAN_SCOPE in row["scopes"]})
        return {"accounts": accounts, "active_account_id": self._data["active_account_id"],
                "login": dict(self._login)}

    async def status(self) -> Json:
        # An exchange can wait on the network; polling must still show its state.
        self._ensure_open()
        return self._status()

    async def select(self, account_id: str) -> Json:
        async with self._lock:
            self._ensure_idle()
            row = self._account(account_id)
            self._commit({**self._data, "active_account_id": row["id"]})
            return self._status()

    async def login(self, account_id: str | None = None) -> Json:
        async with self._lock:
            self._ensure_idle()
            row = self._account(account_id) if account_id is not None else None
            if row is None and len(self._data["accounts"]) >= MAX_ACCOUNTS:
                raise _error("OPENAI_AUTH_LIMIT", "The limit of saved ChatGPT registrations has been reached.")
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            pending: Json = {"account_id": account_id, "state": secrets.token_urlsafe(32),
                             "nonce": secrets.token_urlsafe(32), "verifier": verifier,
                             "start_key": secrets.token_urlsafe(32), "started": False,
                             "client_id": row["client_id"] if row else "dynamic_agent_client"}
            self._listener = await asyncio.start_server(self._handle_client, "127.0.0.1", 0, limit=16_384)
            port = self._listener.sockets[0].getsockname()[1]
            redirect = f"http://127.0.0.1:{port}/auth/callback"
            pending["redirect_uri"] = redirect
            pending["host"] = f"127.0.0.1:{port}"
            query = {"client_id": pending["client_id"], "ext_agent_host_id": self._data["host_id"],
                     "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
                     "resource": RESOURCE, "state": pending["state"], "nonce": pending["nonce"],
                     "code_challenge_method": "S256", "code_challenge": challenge}
            if row is None:
                query["agent_name_hint"] = "Kira Desktop"
            else:
                if row.get("id_token"):
                    query["id_token_hint"] = row["id_token"]
                if row.get("email"):
                    query["login_hint"] = row["email"]
                if PLAN_SCOPE not in row["scopes"]:
                    query["prompt"] = "consent"
            pending["authorization_url"] = f"{AUTHORIZE_URL}?{urlencode(query)}"
            self._pending = pending
            local_url = f"http://127.0.0.1:{port}/auth/start/{pending['start_key']}"
            self._login = {"state": "awaiting_browser", "account_id": account_id,
                           "expires_at": time.time() + LOGIN_TIMEOUT, "authorization_url": local_url}
            self._expiry_task = asyncio.create_task(self._expire(pending))
            return {**self._status(), "authorization_url": local_url}

    async def _expire(self, pending: Json) -> None:
        await asyncio.sleep(LOGIN_TIMEOUT)
        if self._pending is pending:
            await self._cancel_attempt("failed", {
                "code": "OPENAI_AUTH_TIMEOUT", "message": "ChatGPT sign-in expired. Please try again.",
            })

    def _finish(self, state: str, *, error: Json | None = None) -> None:
        self._login = {"state": state, "account_id": self._login.get("account_id")}
        if error:
            self._login["error"] = error
        self._pending = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._expiry_task is not None and self._expiry_task is not asyncio.current_task():
            self._expiry_task.cancel()
        self._expiry_task = None

    async def _cancel_attempt(self, state: str, error: Json | None = None) -> Json:
        pending = self._pending
        exchange = self._exchange_task
        if exchange is not None and exchange is not asyncio.current_task():
            exchange.cancel()
            await asyncio.gather(exchange, return_exceptions=True)
        async with self._lock:
            if self._pending is pending and self._login["state"] in PENDING_STATES:
                self._finish(state, error=error)
            return self._status()

    async def cancel_login(self) -> Json:
        self._ensure_open()
        return await self._cancel_attempt("cancelled")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        if len(self._handlers) >= 16:
            writer.close()
            return
        self._handlers.add(task)
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
            if len(header) > 16_384:
                await self._reply(writer, 400)
                return
            lines = header.decode("ascii").split("\r\n")
            method, target, version = lines[0].split(" ")
            hosts = [line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("host:")]
            pending = self._pending
            parsed = urlsplit(target)
            if (method != "GET" or version not in {"HTTP/1.0", "HTTP/1.1"} or pending is None
                    or hosts != [pending["host"]] or parsed.scheme or parsed.netloc or parsed.fragment):
                await self._reply(writer, 400)
                return
            if parsed.path == f"/auth/start/{pending['start_key']}" and not parsed.query:
                if pending["started"] or self._login["state"] != "awaiting_browser":
                    await self._reply(writer, 410)
                else:
                    pending["started"] = True
                    await self._reply(writer, 302, location=pending["authorization_url"])
                return
            if parsed.path != "/auth/callback":
                await self._reply(writer, 404)
                return
            values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
            if any(len(items) != 1 for items in values.values()):
                await self._reply(writer, 400)
                return
            params = {key: items[0] for key, items in values.items()}
            if not secrets.compare_digest(params.get("state", "").encode(), pending["state"].encode()):
                await self._reply(writer, 400)
                return
            async with self._lock:
                if self._pending is not pending or self._login["state"] != "awaiting_browser":
                    await self._reply(writer, 409)
                    return
                self._login.pop("authorization_url", None)
                self._login["state"] = "exchanging"
                self._exchange_task = task
                try:
                    await self._callback(pending, params)
                except KiraError as exc:
                    self._finish("failed", error={"code": exc.code, "message": str(exc)})
                except (OSError, ValueError, TypeError, RecursionError):
                    self._finish("failed", error={"code": "OPENAI_AUTH_INVALID",
                                                 "message": "ChatGPT sign-in could not be validated."})
                finally:
                    self._exchange_task = None
            await self._reply(writer, 200, success=self._login["state"] == "completed")
        except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.LimitOverrunError,
                asyncio.IncompleteReadError, ConnectionError):
            with contextlib.suppress(ConnectionError):
                await self._reply(writer, 400)
        finally:
            self._handlers.discard(task)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, *, location: str | None = None,
                     success: bool = False) -> None:
        message = ("Sign-in completed. Return to Kira Desktop." if success else
                   "Return to Kira Desktop to check sign-in status.")
        body = (f"<!doctype html><html lang='en'><meta charset='utf-8'><title>Kira Desktop</title>"
                f"<h1>Kira Desktop</h1><p>{message}</p></html>").encode()
        headers = [f"HTTP/1.1 {status} Response", "Connection: close", "Cache-Control: no-store",
                   "Referrer-Policy: no-referrer", "Content-Security-Policy: default-src 'none'; frame-ancestors 'none'",
                   "X-Content-Type-Options: nosniff", "Content-Type: text/html; charset=utf-8",
                   f"Content-Length: {len(body)}"]
        if location:
            headers.append(f"Location: {location}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
        await writer.drain()

    async def _request(self, method: str, url: str, **kwargs: Any) -> tuple[int, Json]:
        try:
            async with asyncio.timeout(AUTH_REQUEST_TIMEOUT):
                async with self._client.stream(method, url, follow_redirects=False,
                                               headers={"Accept-Encoding": "identity"}, **kwargs) as response:
                    if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
                        raise _error("OPENAI_AUTH_INVALID", "OpenAI authentication returned unsupported content encoding.")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > MAX_RESPONSE:
                            raise _error("OPENAI_AUTH_INVALID", "OpenAI authentication response exceeds its size limit.")
                        content.extend(chunk)
                    if not content and response.status_code == 200:
                        return response.status_code, {}
                    try:
                        value = json.loads(content)
                    except (ValueError, UnicodeError, RecursionError):
                        value = {}
                    return response.status_code, value if isinstance(value, dict) else {}
        except (httpx.HTTPError, TimeoutError):
            raise _error("OPENAI_AUTH_UNAVAILABLE", "OpenAI authentication is temporarily unavailable; try again.") from None

    async def _metadata(self) -> Json:
        if self._discovery is None:
            status, data = await self._request("GET", DISCOVERY_URL)
            if status != 200 or data.get("issuer") != ISSUER:
                raise _error("OPENAI_AUTH_INVALID", "OpenAI identity metadata could not be validated.")
            for key in ("jwks_uri", "revocation_endpoint"):
                value = urlsplit(_text(data.get(key), 2048))
                if (value.scheme != "https" or value.netloc != "auth.openai.com"
                        or not value.path.startswith("/") or value.query or value.fragment):
                    raise _error("OPENAI_AUTH_INVALID", "OpenAI identity endpoints could not be validated.")
            self._discovery = data
        return self._discovery

    async def _identity(self, token: str, client_id: str, nonce: str | None) -> Json:
        try:
            metadata = await self._metadata()
            status, keys = await self._request("GET", metadata["jwks_uri"])
            header = jwt.get_unverified_header(token)
            if status != 200 or header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError
            matches = [key for key in keys.get("keys", []) if isinstance(key, dict)
                       and key.get("kid") == header["kid"] and key.get("kty") == "RSA"
                       and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"]
            if len(matches) != 1:
                raise ValueError
            key = jwt.PyJWK.from_dict(matches[0], algorithm="RS256")
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                                leeway=CLOCK_SKEW_SECONDS,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            _text(claims.get("sub"), 512)
            _number(claims["exp"])
            _number(claims["iat"])
            audiences = claims["aud"]
            if ((isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != client_id)
                    or ("azp" in claims and claims["azp"] != client_id)):
                raise ValueError
            if nonce is not None and (not isinstance(claims.get("nonce"), str)
                                      or not secrets.compare_digest(claims["nonce"], nonce)):
                raise ValueError
            return dict(claims)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise _error("OPENAI_AUTH_INVALID", "OpenAI account identity could not be validated.") from None

    async def _callback(self, pending: Json, params: dict[str, str]) -> None:
        if "error" in params:
            raise _error("OPENAI_AUTH_DECLINED", "ChatGPT sign-in was declined or could not be completed.")
        code = _text(params.get("code"), 16_384)
        client_id = _text(params.get("client_id", pending["client_id"]), 256)
        if client_id == "dynamic_agent_client":
            raise _error("OPENAI_AUTH_INVALID", "OpenAI did not return an issued client registration.")
        if pending["client_id"] != "dynamic_agent_client" and client_id != pending["client_id"]:
            raise _error("OPENAI_AUTH_INVALID", "OpenAI returned a different account registration.")
        if pending["account_id"] is None:
            if any(row["client_id"] == client_id for row in self._data["accounts"].values()):
                raise _error("OPENAI_AUTH_INVALID", "This ChatGPT registration already exists; select its saved account.")
            account_id = str(uuid.uuid4())
            row: Json = {"id": account_id, "label": f"ChatGPT · {account_id[:8]}", "client_id": client_id,
                         "issuer": ISSUER, "subject": None, "email": None, "scopes": [],
                         "expires_at": 0, "earliest_refresh_at": 0}
            # Retain the issued registration even when the one-use code expires.
            self._store_account(row)
            pending["account_id"] = account_id
            self._login["account_id"] = account_id
        else:
            row = self._account(pending["account_id"])
        status, result = await self._request("POST", TOKEN_URL, data={
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"], "resource": RESOURCE,
        })
        if status != 200:
            raise _error("OPENAI_AUTH_EXCHANGE_FAILED", "ChatGPT sign-in could not exchange its code; sign in again with this saved account.")
        claims = await self._identity(_text(result.get("id_token")), client_id, pending["nonce"])
        if row.get("subject") is not None and row["subject"] != claims["sub"]:
            raise _error("OPENAI_AUTH_INVALID", "The returned ChatGPT identity differs from the selected account.")
        saved = self._token_record(row, result)
        email = claims.get("email")
        email = email if isinstance(email, str) and 0 < len(email) <= 254 and not any(ord(c) < 32 for c in email) else None
        saved.update({"subject": claims["sub"], "email": email, "nonce": pending["nonce"],
                      "label": f"{email or 'ChatGPT'} · {row['id'][:8]}"})
        self._store_account(saved, activate=True)
        self._finish("completed")

    @staticmethod
    def _token_record(previous: Json, result: Json, *, refresh: bool = False) -> Json:
        access = _text(result.get("access_token"))
        if not isinstance(result.get("token_type"), str) or result["token_type"].lower() != "bearer":
            raise _error("OPENAI_AUTH_INVALID", "OpenAI returned an unsupported token type.")
        expiry = _number(result.get("expires_in"))
        if not 0 < expiry <= 604_800:
            raise _error("OPENAI_AUTH_INVALID", "OpenAI returned invalid token expiry data.")
        scope = result.get("scope")
        if scope is None and refresh:
            scopes = list(previous["scopes"])
        else:
            scopes = _text(scope, 8192).split()
        replacement = result.get("refresh_token")
        if "offline_access" in scopes or refresh:
            replacement = _text(replacement)
        elif replacement is not None:
            replacement = _text(replacement)
        earliest_value = result.get("earliest_refresh_at", 0)
        if isinstance(earliest_value, str):
            try:
                date = datetime.fromisoformat(earliest_value.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    raise ValueError
                earliest_value = date.timestamp()
            except ValueError:
                raise _error("OPENAI_AUTH_INVALID", "OpenAI returned invalid token refresh data.") from None
        earliest = _number(earliest_value)
        if earliest < 0:
            raise _error("OPENAI_AUTH_INVALID", "OpenAI returned invalid token refresh data.")
        saved = dict(previous)
        saved.update({"access_token": access, "refresh_token": replacement, "token_type": "Bearer",
                      "scopes": list(dict.fromkeys(scopes)), "expires_at": time.time() + expiry,
                      "earliest_refresh_at": earliest, "saved_at": time.time()})
        if "id_token" in result:
            saved["id_token"] = _text(result["id_token"])
        return saved

    @staticmethod
    def _clear_tokens(row: Json) -> Json:
        saved = {key: value for key, value in row.items()
                 if key not in {"access_token", "refresh_token", "id_token", "nonce", "token_type", "saved_at"}}
        saved.update({"scopes": [], "expires_at": 0, "earliest_refresh_at": 0})
        return saved

    async def access_token(self, account_id: str | None = None) -> str:
        async with self._lock:
            self._ensure_idle()
            row = self._account(account_id)
            if not row.get("access_token"):
                raise _error("OPENAI_SIGN_IN_REQUIRED", "Continue with ChatGPT again for this saved account.")
            if PLAN_SCOPE not in row["scopes"]:
                raise _error("OPENAI_PLAN_DISABLED", "Allow Kira to use your ChatGPT plan before starting a request.")
            now = time.time()
            if row["expires_at"] > now + 60 or (now < row["earliest_refresh_at"] and row["expires_at"] > now):
                return str(row["access_token"])
            if now < row["earliest_refresh_at"]:
                raise _error("OPENAI_AUTH_REFRESH_FAILED", "OpenAI has not yet allowed token refresh; try again later.")
            if not row.get("refresh_token"):
                raise _error("OPENAI_SIGN_IN_REQUIRED", "This ChatGPT session expired; sign in again.")
            status, result = await self._request("POST", TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": row["client_id"],
                "refresh_token": row["refresh_token"], "resource": RESOURCE,
            })
            if status != 200:
                code = result.get("error")
                if isinstance(code, dict):
                    code = code.get("code")
                if isinstance(code, str) and code in TERMINAL_REFRESH_ERRORS:
                    self._store_account(self._clear_tokens(row))
                    raise _error("OPENAI_SIGN_IN_REQUIRED", "The ChatGPT session ended; sign in again with this saved account.")
                if code == "invalid_client":
                    raise _error("OPENAI_AUTH_INVALID", "OpenAI rejected this saved client registration.")
                raise _error("OPENAI_AUTH_REFRESH_FAILED", "ChatGPT token renewal failed temporarily; credentials were retained.")
            if result.get("id_token"):
                claims = await self._identity(_text(result["id_token"]), row["client_id"], None)
                if (claims["sub"] != row["subject"]
                        or ("nonce" in claims and claims["nonce"] != row.get("nonce"))):
                    raise _error("OPENAI_AUTH_INVALID", "The refreshed ChatGPT identity differs from the selected account.")
            saved = self._token_record(row, result, refresh=True)
            self._store_account(saved)
            if PLAN_SCOPE not in saved["scopes"]:
                raise _error("OPENAI_PLAN_DISABLED", "Allow Kira to use your ChatGPT plan before starting a request.")
            return str(saved["access_token"])

    async def logout(self, account_id: str | None = None) -> Json:
        async with self._lock:
            self._ensure_idle()
            row = self._account(account_id)
            revoked = not bool(row.get("refresh_token"))
            if row.get("refresh_token"):
                for attempt in range(3):
                    try:
                        metadata = await self._metadata()
                        status, _ = await self._request("POST", metadata["revocation_endpoint"], data={
                            "token": row["refresh_token"], "token_type_hint": "refresh_token",
                            "client_id": row["client_id"],
                        })
                        revoked = status == 200
                        if revoked or status < 500:
                            break
                    except KiraError:
                        pass
                    if attempt < 2:
                        await asyncio.sleep(0.25 * 2**attempt)
            self._store_account(self._clear_tokens(row))
            result = self._status()
            result["remote_revocation_confirmed"] = revoked
            if not revoked:
                result["warning"] = "Signed out locally. Remote revocation was not confirmed; disconnect Kira in ChatGPT Settings."
            return result

    def _close_files(self) -> None:
        for name in ("_lock_fd", "_directory_fd"):
            fd = getattr(self, name)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)

    async def close(self) -> None:
        if self._closed:
            return
        await self._cancel_attempt("cancelled")
        for task in tuple(self._handlers):
            task.cancel()
        if self._handlers:
            await asyncio.gather(*self._handlers, return_exceptions=True)
        async with self._lock:
            self._closed = True
            self._close_files()
            if self._owns_client:
                await self._client.aclose()
