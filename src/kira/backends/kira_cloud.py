"""Kira Cloud backend: the models included with a Kira account.

Kira Desktop talks to two Kira services only:

- the account portal, for sign-in, plan and balance;
- the Kira Cloud gateway, for the model catalog and chat completions.

The desktop holds a single credential, the Kira Desktop token issued by the portal
after a browser sign-in. Plan limits, credit and billing are decided by the service;
this module never computes or reports costs on the user's behalf.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

import httpx

from kira import __version__
from kira.backends.ollama import (
    CAPABILITIES,
    DISCOVERY_TIMEOUT,
    MAX_DISCOVERY_BYTES,
    MAX_MODELS,
    OllamaBackend,
    StreamError,
    _json_size,
    _model_name,
)
from kira.domain.contracts import Json, KiraError, RunContext
from kira.domain.pricing import ModelPricing, register_dynamic_model_pricing

if TYPE_CHECKING:
    from kira.service.server import Service

BACKEND = "kira_cloud"
DEFAULT_PORTAL_URL = "https://kiradesktop.com"
TRUSTED_DOMAIN = "kiradesktop.com"
#: Developer overrides. Only the Kira domain over HTTPS or a loopback address is accepted.
PORTAL_ENV = "KIRA_CLOUD_PORTAL_URL"
GATEWAY_ENV = "KIRA_CLOUD_GATEWAY_URL"
MAX_AUTH_BYTES = 65_536
PNG_DATA_URL = "data:image/png;base64,"
MAX_IMAGE_BASE64_CHARS = 12_000_000
DEFAULT_REQUEST_MAX_TOKENS = 16_384
# Cost bounds. The context window says what a model can read, not what a request should cost:
# with a 1M-token window an unbounded tool result is resent, and paid, at every step.
MAX_TOOL_RESULT_CHARS = 32_000
MAX_CONTEXT_CHARS = 180_000
RETRY_BACKOFF_DELAYS: tuple[float, ...] = (1.5, 3.5, 7.0)
DEFAULT_FIRST_TOKEN_TIMEOUT = 90.0
CATALOG_TTL = 120.0
BALANCE_REFRESH_INTERVAL = 20.0
_TOKEN_RE = re.compile(r"kira_usr_[0-9a-f]{20,96}")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: Service answers worth retrying after a short pause.
_RETRYABLE_CODES = frozenset({"overloaded", "rate_limited", "timeout", "too_many_concurrent"})

KIRA_CLOUD_CAPABILITIES: Json = {
    **CAPABILITIES,
    "tools": True,
    "images": True,
    "context_controls": True,
}


class KiraCloudAuthError(StreamError):
    """The Kira account is not connected or its session is no longer valid."""


class KiraCloudContextOverflow(StreamError):
    """The service rejected the request because the conversation exceeds the model context."""

    context_overflow = True


class KiraCloudHTTPError(httpx.HTTPStatusError):
    """A service rejection carrying the user-facing message chosen by Kira Cloud."""

    def __init__(self, response: httpx.Response, code: str, message: str) -> None:
        super().__init__(
            f"Kira Cloud HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
        self.code = code
        self.safe_detail = message


class _FirstTokenStall(StreamError):
    """No content arrived within the first-token deadline."""


def _is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in _LOOPBACK_HOSTS


def _trusted_url(url: str, *, allow_loopback: bool) -> str:
    """Return ``url`` without a trailing slash when Kira may send credentials to it."""
    cleaned = url.strip()
    parts = urlparse(cleaned)
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        return ""
    if parts.scheme == "https" and (host == TRUSTED_DOMAIN or host.endswith("." + TRUSTED_DOMAIN)):
        return cleaned.rstrip("/")
    if allow_loopback and parts.scheme in {"http", "https"} and host in _LOOPBACK_HOSTS:
        return cleaned.rstrip("/")
    return ""


def portal_url() -> str:
    """The account portal: the official site unless a developer override is set."""
    override = os.environ.get(PORTAL_ENV, "")
    return (_trusted_url(override, allow_loopback=True) if override else "") or DEFAULT_PORTAL_URL


def _portal_page(url: str) -> str:
    """A page Kira may open in the browser: on the contacted portal or on the official site."""
    base = portal_url()
    if url.startswith(base + "/"):
        return url
    return url if _trusted_url(url.split("?", 1)[0], allow_loopback=False) else ""


def _mapping(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def _amount(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(float(value), 4) if value == value and abs(value) < 1e9 else 0.0


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _service_error(response: httpx.Response, body: bytes) -> tuple[str, str]:
    """Extract Kira Cloud's stable error code and its user-facing message."""
    code, message = "", ""
    with contextlib.suppress(ValueError, UnicodeError):
        error = _mapping(_mapping(json.loads(body)).get("error"))
        if isinstance(error.get("code"), str):
            code = error["code"][:64]
        if isinstance(error.get("message"), str):
            message = " ".join(error["message"].split())[:400]
    if not message:
        message = "Kira Cloud non è riuscito a completare la richiesta. Riprova tra poco."
    return code or f"http_{response.status_code}", message


async def _cancelable_sleep(seconds: float, ctx: RunContext | None) -> bool:
    if seconds <= 0:
        return True
    deadline = time.monotonic() + seconds
    while True:
        if ctx is not None and ctx.cancel.is_set():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(0.25, remaining))


class KiraCloudAccount:
    """The Kira account stored in the private profile: one token and the last known plan."""

    def __init__(
        self,
        directory: Path,
        protect_secrets: Callable[[tuple[str, ...]], None] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise KiraError("UNSAFE_PATH", "Kira Cloud credential storage cannot be a symlink")
        self.directory.chmod(0o700)
        info = self.directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise KiraError("UNSAFE_PATH", "Kira Cloud credential storage must be private and user-owned")
        self.path = self.directory / "account.json"
        self.protect_secrets = protect_secrets
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=False,
            trust_env=False,
        )
        self.token: str = ""
        self.config: Json = {}
        self.session_expired = False
        self._refreshed_at = 0.0
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        migrate = False
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise KiraError("UNSAFE_PATH", "Kira Cloud credential file must be private and user-owned")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_AUTH_BYTES + 1)
            if len(raw) > MAX_AUTH_BYTES:
                raise ValueError("Kira Cloud credential file exceeds size limit")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Invalid Kira Cloud credential file")
            if data.get("version") == 2:
                token = data.get("token", "")
                self.config = dict(_mapping(data.get("config")))
            else:
                # An earlier layout kept other material next to the token. Only the token is
                # carried over; the file is rewritten below so nothing else stays on disk.
                token = data.get("kira_pro_token", "")
                migrate = True
            self.token = token if isinstance(token, str) and _TOKEN_RE.fullmatch(token) else ""
            if self.token and self.protect_secrets:
                self.protect_secrets((self.token,))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise KiraError("CREDENTIAL_STORE", "Kira Cloud account storage is invalid") from exc
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
        if migrate:
            self._save()

    def _save(self) -> None:
        payload: Json = {"version": 2, "token": self.token, "config": self.config}
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        tmp_path = self.directory / f"account.json.tmp.{os.getpid()}"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(fd)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
        os.replace(tmp_path, self.path)
        if self.token and self.protect_secrets:
            self.protect_secrets((self.token,))

    # ------------------------------------------------------------------ state
    @property
    def connected(self) -> bool:
        return bool(self.token)

    def gateway_url(self) -> str:
        """Where chat requests go: announced by the portal, always on a trusted host."""
        override = os.environ.get(GATEWAY_ENV, "")
        if override:
            trusted = _trusted_url(override, allow_loopback=True)
            if trusted:
                return trusted
        announced = _mapping(self.config.get("gateway")).get("base_url")
        if not isinstance(announced, str):
            return ""
        return _trusted_url(announced, allow_loopback=_is_loopback(portal_url()))

    def headers(self, *, auth: bool = True) -> dict[str, str]:
        headers = {"User-Agent": f"Kira-Desktop/{__version__}", "Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def status(self) -> Json:
        """Account summary for the interface. It never contains the token."""
        user = _mapping(self.config.get("user"))
        plan = _mapping(self.config.get("plan"))
        balance = _mapping(self.config.get("balance"))
        limits = _mapping(self.config.get("limits"))
        connected = self.connected
        return {
            "connected": connected,
            "session_expired": self.session_expired and not connected,
            "service_available": connected and bool(self.gateway_url()),
            "email": str(user.get("email") or "")[:190] if connected else "",
            "name": str(user.get("name") or "")[:120] if connected else "",
            "plan_tier": str(plan.get("tier") or "free")[:40] if connected else "free",
            "plan_label": str(plan.get("label") or "")[:60] if connected else "",
            "plan_active": connected and plan.get("active") is True,
            "currency": "USD" if balance.get("currency") == "USD" else "EUR",
            "monthly_limit": _amount(balance.get("monthly_limit")),
            "monthly_used": _amount(balance.get("monthly_used")),
            "monthly_remaining": _amount(balance.get("monthly_remaining")),
            "monthly_period_ends_at": str(balance.get("monthly_period_ends_at") or "")[:40],
            "credits_limit": _amount(balance.get("credits_limit")),
            "credits_used": _amount(balance.get("credits_used")),
            "credits_remaining": _amount(balance.get("credits_remaining")),
            "total_remaining": _amount(balance.get("total_remaining")),
            "max_concurrent": _count(limits.get("max_concurrent")),
            "max_rpm": _count(limits.get("max_rpm")),
            "max_output_tokens": _count(limits.get("max_output_tokens")),
        }

    def expire(self) -> None:
        """Forget a token the service no longer accepts."""
        if self.token:
            self.token = ""
            self.config = {}
            self.session_expired = True
            self._save()

    def _apply(self, config: Json, token: str = "") -> None:
        if token:
            self.token = token
        self.config = {
            key: config[key] for key in ("gateway", "user", "plan", "limits", "balance")
            if isinstance(config.get(key), dict)
        }
        self.session_expired = False
        self._refreshed_at = time.monotonic()
        self._save()

    # ------------------------------------------------------------------ portal
    async def _portal(self, method: str, path: str, *, auth: bool, body: Json | None = None,
                      params: Json | None = None) -> httpx.Response:
        try:
            return await self.client.request(
                method, portal_url() + path, headers=self.headers(auth=auth), json=body,
                params=params, timeout=12.0,
            )
        except httpx.HTTPError as exc:
            raise KiraError(
                "PROVIDER_UNAVAILABLE",
                "Impossibile contattare Kira Cloud. Controlla la connessione e riprova.",
            ) from exc

    @staticmethod
    def _json(response: httpx.Response) -> Json:
        with contextlib.suppress(ValueError):
            return _mapping(response.json())
        return {}

    async def refresh(self) -> Json:
        """Reload plan and balance from the portal."""
        if not self.connected:
            return self.status()
        response = await self._portal("GET", "/v1/pro/config", auth=True)
        if response.status_code in {401, 403}:
            self.expire()
            return self.status()
        config = self._json(response)
        if response.status_code != 200 or config.get("ok") is not True:
            raise KiraError("PROVIDER_UNAVAILABLE", "Kira Cloud non ha restituito i dati dell'account. Riprova tra poco.")
        self._apply(config)
        return self.status()

    async def refresh_if_stale(self) -> None:
        """Keep the balance current after usage without querying the portal on every round."""
        if self.connected and time.monotonic() - self._refreshed_at >= BALANCE_REFRESH_INTERVAL:
            self._refreshed_at = time.monotonic()
            with contextlib.suppress(KiraError):
                await self.refresh()

    async def connect_with_token(self, token: str) -> Json:
        cleaned = token.strip()
        if not _TOKEN_RE.fullmatch(cleaned):
            raise KiraError("INVALID_ARGUMENT", "Il token Kira Desktop non è valido (inizia con kira_usr_).")
        previous, self.token = self.token, cleaned
        try:
            response = await self._portal("GET", "/v1/pro/config", auth=True)
        except KiraError:
            self.token = previous
            raise
        config = self._json(response)
        if response.status_code != 200 or config.get("ok") is not True:
            self.token = previous
            if response.status_code in {401, 403}:
                raise KiraError("AUTH_REJECTED", "Token Kira Desktop non riconosciuto. Generane uno nuovo dalla Console.")
            raise KiraError("PROVIDER_UNAVAILABLE", "Kira Cloud non ha restituito i dati dell'account. Riprova tra poco.")
        self._apply(config, token=cleaned)
        return self.status()

    async def start_browser_login(self) -> Json:
        response = await self._portal("POST", "/api/auth/desktop-session/start", auth=False, body={})
        payload = self._json(response)
        code = payload.get("code")
        auth_url = payload.get("auth_url")
        if (response.status_code != 200 or not isinstance(code, str) or not code
                or not isinstance(auth_url, str)):
            raise KiraError("PROVIDER_UNAVAILABLE", "Kira Cloud non ha avviato l'accesso dal browser. Riprova tra poco.")
        trusted = _portal_page(auth_url)
        if not trusted:
            raise KiraError("PROVIDER_UNAVAILABLE", "Kira Cloud ha restituito un indirizzo di accesso non valido.")
        return {"ok": True, "code": code[:128], "auth_url": trusted}

    async def poll_browser_login(self, code: str) -> Json:
        cleaned = code.strip()
        if not cleaned or len(cleaned) > 128:
            return {"ok": False, "poll_status": "invalid"}
        try:
            response = await self._portal("GET", "/api/auth/desktop-session/poll", auth=False,
                                          params={"code": cleaned})
        except KiraError:
            return {"ok": True, "poll_status": "pending"}
        payload = self._json(response)
        if response.status_code == 404:
            return {"ok": False, "poll_status": "expired"}
        if payload.get("status") != "approved":
            return {"ok": True, "poll_status": "pending"}
        token = payload.get("desktop_token")
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            return {"ok": False, "poll_status": "invalid"}
        self._apply(_mapping(payload.get("pro_config")), token=token)
        return {"ok": True, "poll_status": "approved", **self.status()}

    async def console_link(self) -> Json:
        """A short-lived, single-use link that opens the web Console already signed in."""
        base = portal_url()
        if not self.connected:
            return {"ok": True, "url": base + "/signin"}
        response = await self._portal("POST", "/api/auth/console-ticket", auth=True, body={})
        if response.status_code in {401, 403}:
            self.expire()
            return {"ok": True, "url": base + "/signin"}
        url = self._json(response).get("url")
        if response.status_code != 200 or not isinstance(url, str) or not _portal_page(url):
            return {"ok": True, "url": base + "/console"}
        return {"ok": True, "url": url}

    def logout(self) -> Json:
        self.token = ""
        self.config = {}
        self.session_expired = False
        self._save()
        return self.status()

    async def close(self) -> None:
        await self.client.aclose()


class KiraCloudAccounts:
    """Service-side manager for the Kira Cloud account."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self._auth: KiraCloudAccount | None = None
        self.changing = False

    @property
    def auth(self) -> KiraCloudAccount:
        if self._auth is None:
            self._auth = KiraCloudAccount(
                self.service.paths.config / "kira_cloud",
                protect_secrets=functools.partial(
                    self.service.redactor.set_provider_values,
                    owner=BACKEND,
                ),
            )
        return self._auth

    def busy(self) -> bool:
        return self.changing

    async def _drop_backend(self) -> None:
        provider = self.service._control_backends.pop(BACKEND, None)
        if provider is not None:
            await provider.close()

    async def dispatch(self, action: str, params: Json | None = None) -> Json:
        params = params or {}
        if action == "status":
            return self.auth.status()
        if action == "poll_browser_login":
            result = await self.auth.poll_browser_login(str(params.get("code") or ""))
            if result.get("poll_status") == "approved":
                await self._drop_backend()
            return result
        if action == "console_link":
            return await self.auth.console_link()
        if self.changing:
            raise KiraError("BUSY", "L'account Kira Cloud è già in fase di aggiornamento")
        self.changing = True
        try:
            if action in {"refresh", "login"}:
                return await self.auth.refresh()
            if action == "start_browser_login":
                return await self.auth.start_browser_login()
            if action == "connect_token":
                result = await self.auth.connect_with_token(str(params.get("token") or ""))
                await self._drop_backend()
                return result
            if action == "logout":
                result = self.auth.logout()
                await self._drop_backend()
                return result
            raise KiraError("INVALID_ARGUMENT", "Azione Kira Cloud non supportata")
        finally:
            self.changing = False

    async def prepare(self) -> None:
        if self.changing:
            raise KiraError("BUSY", "L'account Kira Cloud è in fase di aggiornamento")
        if self.service.backend_factory is not None and self._auth is None:
            return
        if not self.auth.connected:
            raise KiraError(
                "AUTH_REQUIRED",
                "Accedi al tuo account Kira Cloud nel pannello Modelli prima di usare questo provider",
            )

    async def close(self) -> None:
        if self._auth is not None:
            await self._auth.close()
            self._auth = None


def _price(prices: Json, key: str, default: float = 0.0) -> float:
    value = prices.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return default
    return float(value)


def _model_detail(row: Json) -> Json:
    """Translate one catalog entry into the detail shape shared by Kira's HTTP backends."""
    model_id = str(row.get("id") or "").strip()
    name = str(row.get("name") or model_id).strip() or model_id
    capabilities = _mapping(row.get("capabilities"))
    context_window = _count(row.get("context_length")) or 128_000
    max_output_tokens = _count(row.get("max_output_tokens")) or min(DEFAULT_REQUEST_MAX_TOKENS, context_window // 4)
    usd = _mapping(_mapping(row.get("pricing")).get("usd"))
    eur = _mapping(_mapping(row.get("pricing")).get("eur"))
    input_per_m = _price(usd, "input_per_mtok")
    output_per_m = _price(usd, "output_per_mtok")
    register_dynamic_model_pricing(
        BACKEND,
        model_id,
        ModelPricing(
            family=model_id,
            billing_mode="api",
            input_per_m=input_per_m,
            cached_input_per_m=_price(usd, "cached_input_per_mtok", input_per_m),
            cache_write_per_m=_price(usd, "cache_write_per_mtok", input_per_m),
            output_per_m=output_per_m,
            default_context_window=context_window,
        ),
    )
    supports_reasoning = capabilities.get("reasoning") is True
    return {
        "name": name,
        "description": str(row.get("description") or "")[:240],
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
        "capabilities": [
            *(["tools"] if capabilities.get("tools") is True else []),
            *(["vision"] if capabilities.get("vision") is True else []),
        ],
        "supports_reasoning": supports_reasoning,
        "supports_temperature": capabilities.get("temperature") is not False,
        "supports_prompt_cache": capabilities.get("prompt_cache") is True,
        "supported_reasoning_efforts": ["off", "low", "medium", "high", "xhigh"] if supports_reasoning else [],
        "model_info": {
            "id": model_id,
            "name": name,
            "context_length": context_window,
            "max_completion_tokens": max_output_tokens,
            "pricing": {
                "input_per_1m_usd": input_per_m,
                "output_per_1m_usd": output_per_m,
                "cached_input_per_1m_usd": _price(usd, "cached_input_per_mtok", input_per_m),
                "input_per_1m_eur": _price(eur, "input_per_mtok"),
                "output_per_1m_eur": _price(eur, "output_per_mtok"),
            },
        },
    }


_LEAKED_CALL_MARK = re.compile(r"</?(?:arg_key|arg_value|tool_call)>")
_LEAKED_CALL = re.compile(
    r"([A-Za-z_][\w.-]{0,63})\s*((?:<arg_key>.*?</arg_key>\s*<arg_value>.*?</arg_value>\s*)+)", re.S)
_LEAKED_ARG = re.compile(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", re.S)


def _recover_leaked_tool_calls(text: str, offered: set[str]) -> tuple[str, list[Json]]:
    """Split text containing leaked tool-call markup into the readable part and the calls it names.

    Only calls to tools offered in this request are returned; the readable part is everything
    before the first piece of markup, so no markup ever reaches the transcript.
    """
    first = _LEAKED_CALL_MARK.search(text)
    if first is None:
        return text, []
    clean = text[:first.start()].rstrip()
    calls: list[Json] = []
    for match in _LEAKED_CALL.finditer(text[first.start():] if first.group(0).startswith("</") else text):
        name = match.group(1)
        if name not in offered:
            continue
        arguments: Json = {}
        for key, value in _LEAKED_ARG.findall(match.group(2)):
            key = key.strip()
            if not key:
                continue
            try:
                arguments[key] = json.loads(value)
            except ValueError:
                arguments[key] = value
        calls.append({"id": f"call_recovered_{len(calls)}", "type": "function",
                      "function": {"name": name, "arguments": arguments}})
        if len(calls) >= 8:
            break
    if not calls:
        # The call may start before the first mark: "<name><arg_key>…" with the name in clean text.
        whole = _LEAKED_CALL.search(text)
        if whole is not None and whole.group(1) in offered:
            clean = text[:whole.start()].rstrip()
            arguments = {}
            for key, value in _LEAKED_ARG.findall(whole.group(2)):
                try:
                    arguments[key.strip()] = json.loads(value)
                except ValueError:
                    arguments[key.strip()] = value
            calls.append({"id": "call_recovered_0", "type": "function",
                          "function": {"name": whole.group(1), "arguments": arguments}})
    return clean, calls


_UNLISTED_MODEL: Json = {
    "context_length": 128_000,
    "max_output_tokens": DEFAULT_REQUEST_MAX_TOKENS,
    "capabilities": {"tools": True, "vision": False, "reasoning": False, "temperature": True},
}


def _message_serialized_chars(msg: Json) -> int:
    content = msg.get("content", "")
    total = len(content) if isinstance(content, str) else len(json.dumps(content, ensure_ascii=False))
    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        total += len(json.dumps(tool_calls, ensure_ascii=False))
    return total


def _bound_tool_result(content: str) -> str:
    """Keep the head and tail of an oversized tool result; the model can ask for a specific part."""
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content
    head, tail = MAX_TOOL_RESULT_CHARS * 3 // 4, MAX_TOOL_RESULT_CHARS // 5
    omitted = len(content) - head - tail
    return (content[:head]
            + f"\n...[{omitted} characters omitted by Kira to keep this request affordable; "
              "ask for a narrower range, a search or a filtered command to see more]...\n"
            + content[-tail:])


def _compact_messages(source: Any, max_chars: int = 240_000) -> list[Json]:
    """Bound tool outputs so the conversation fits the selected model and stays affordable."""
    if not isinstance(source, list):
        return []
    cloned: list[Json] = [dict(m) for m in source if isinstance(m, dict)]
    # Every request is paid per token, and every later step of the same turn sends the result
    # again. The bound is the same at any age so the prefix stays identical and cacheable.
    for idx, msg in enumerate(cloned):
        content = msg.get("content")
        if isinstance(content, str) and len(content) > MAX_TOOL_RESULT_CHARS and (
                msg.get("role") == "tool"
                or (msg.get("role") == "user" and content.startswith("Recorded tool outcome (historical data,"))):
            cloned[idx] = {**msg, "content": _bound_tool_result(content)}
    total = sum(_message_serialized_chars(m) for m in cloned)
    if total <= max_chars:
        return cloned
    non_system = [i for i, m in enumerate(cloned) if m.get("role") != "system"]
    protected_tail = set(non_system[-6:])
    for idx in non_system:
        if total <= max_chars:
            break
        if idx in protected_tail:
            continue
        msg = cloned[idx]
        role, content = msg.get("role"), msg.get("content")
        historical = role == "tool" or (
            role == "user" and isinstance(content, str)
            and content.startswith("Recorded tool outcome (historical data,"))
        if historical and isinstance(content, str) and len(content) > 1200:
            total -= len(content) - 1050
            cloned[idx] = {**msg, "content": content[:800] + "\n...[truncated historical tool output]...\n" + content[-200:]}
    if total <= max_chars:
        return cloned
    strict_tail = set(non_system[-4:])
    for idx in non_system:
        if total <= max_chars:
            break
        if idx in strict_tail:
            continue
        msg = cloned[idx]
        content = msg.get("content")
        if isinstance(content, str) and len(content) > 500:
            total -= len(content) - 420
            cloned[idx] = {**msg, "content": content[:320] + "\n...[compacted]...\n" + content[-80:]}
    return cloned


def _attachment_image_part(image: Any) -> Json:
    if (
        not isinstance(image, dict)
        or image.get("type") != "input_image"
        or not isinstance(image.get("image_url"), str)
        or not image["image_url"].startswith(PNG_DATA_URL)
    ):
        raise StreamError("Kira Cloud received an invalid Kira image attachment")
    if len(image["image_url"]) - len(PNG_DATA_URL) > MAX_IMAGE_BASE64_CHARS:
        raise StreamError("Kira Cloud image attachment exceeds size limit")
    return {"type": "image_url", "image_url": {"url": image["image_url"], "detail": "auto"}}


def _tool_image_part(image: Any) -> Json:
    data = image[len(PNG_DATA_URL):] if isinstance(image, str) and image.startswith(PNG_DATA_URL) else image
    if not isinstance(data, str) or not data or len(data) > MAX_IMAGE_BASE64_CHARS:
        raise StreamError("Kira Cloud received an invalid or oversized Kira tool image")
    try:
        base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise StreamError("Kira Cloud received an invalid Kira tool image") from exc
    return {"type": "image_url", "image_url": {"url": f"{PNG_DATA_URL}{data}", "detail": "auto"}}


def _bind_active_prompt(source: Any, ctx: RunContext) -> list[Json]:
    if not isinstance(source, list):
        raise StreamError("Invalid provider message history")
    for index in range(len(source) - 1, -1, -1):
        message = source[index]
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
            and "run_id" not in message
            and message.get("content") == ctx.prompt
        ):
            return [*source[:index], {**message, "run_id": ctx.run_id}, *source[index + 1:]]
    raise StreamError("Kira Cloud image inputs have no matching user turn")


def _chat_messages(source: Any, image_inputs: dict[str, list[Json]], *, vision: bool = True) -> tuple[list[Json], str]:
    """Translate Kira's internal history into the chat format of the Kira Cloud gateway."""
    if not isinstance(source, list):
        raise StreamError("Invalid provider message history")
    system_parts: list[str] = []
    result: list[Json] = []
    pending_ids: dict[str, list[str]] = {}

    for message in source:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
        elif role == "assistant":
            out_calls: list[Json] = []
            for idx, call in enumerate(message.get("tool_calls", [])):
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    continue
                fn = call["function"]
                name = fn.get("name")
                args = fn.get("arguments", {})
                if not isinstance(name, str) or not name:
                    continue
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    call_id = f"kira-call-{len(result)}-{idx}"
                args_str = args if isinstance(args, str) else json.dumps(
                    args if isinstance(args, dict) else {}, ensure_ascii=False)
                out_calls.append({"id": call_id, "type": "function",
                                  "function": {"name": name, "arguments": args_str}})
                pending_ids.setdefault(name, []).append(call_id)
            text = content if isinstance(content, str) else ""
            if not text and not out_calls:
                continue
            assistant: Json = {"role": "assistant", "content": text}
            if out_calls:
                assistant["tool_calls"] = out_calls
            # Opaque continuation state issued by the service; returned untouched.
            state = message.get("reasoning_state")
            if isinstance(state, str) and state:
                assistant["reasoning_state"] = state
            result.append(assistant)
        elif role == "tool":
            name = message.get("tool_name")
            if not isinstance(name, str) or not name:
                continue
            call_id = message.get("tool_call_id")
            candidates = pending_ids.get(name, [])
            if not isinstance(call_id, str) or not call_id:
                call_id = candidates.pop(0) if candidates else f"kira-tool-{len(result)}"
            elif call_id in candidates:
                candidates.remove(call_id)
            value = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            images = message.get("images", [])
            image_parts = ([_tool_image_part(img) for img in images]
                           if vision and isinstance(images, list) and images else [])
            tool_content: str | list[Json] = (
                [{"type": "text", "text": value}, *image_parts] if image_parts else value)
            result.append({"role": "tool", "tool_call_id": call_id, "content": tool_content})
        elif role == "user" and isinstance(content, str):
            run_id = message.get("run_id")
            images = image_inputs.get(run_id, []) if vision and isinstance(run_id, str) else []
            if images:
                parts: list[Json] = [{"type": "text", "text": content}] if content else []
                parts.extend(_attachment_image_part(img) for img in images)
                result.append({"role": "user", "content": parts})
            elif content:
                result.append({"role": "user", "content": content})

    if not result:
        result.append({"role": "user", "content": "Continue."})
    return result, "\n\n".join(system_parts)


class KiraCloudBackend(OllamaBackend):
    """Chat completions through the Kira Cloud gateway, with Kira's full tool loop."""

    def __init__(
        self,
        account: KiraCloudAccount,
        client: httpx.AsyncClient | None = None,
        backoff_delays: tuple[float, ...] = RETRY_BACKOFF_DELAYS,
        first_token_timeout: float | None = None,
    ) -> None:
        super().__init__(DEFAULT_PORTAL_URL)
        if client is not None:
            self.client = client
        else:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(180.0, connect=10.0),
                follow_redirects=False,
                trust_env=False,
            )
        self.account = account
        self._details: dict[str, Json] = {}
        self._order: list[str] = []
        self._catalog_loaded_at = 0.0
        self._backoff_delays = backoff_delays
        self._first_token_timeout = first_token_timeout

    def _require_service(self) -> tuple[str, str]:
        if not self.account.connected:
            raise KiraCloudAuthError(
                "Accedi al tuo account Kira Cloud nel pannello Modelli prima di usare questo provider")
        base = self.account.gateway_url()
        if not base:
            raise KiraCloudAuthError("Kira Cloud non è ancora disponibile per questo account. Riprova più tardi.")
        return base, self.account.token

    async def _probe_tools(self, model: str, ctx: RunContext) -> bool:
        detail = await self._show(model)
        return "tools" in detail.get("capabilities", [])

    def _probe_outcome(self, provider: str, model: str, detail: Json) -> str:
        return "passed" if "tools" in detail.get("capabilities", []) else "not_run"

    async def _show(self, model: str, *, refresh: bool = False) -> Json:
        model = _model_name(model)
        if refresh or model not in self._details:
            with contextlib.suppress(Exception):
                await self._load_catalog()
        if model not in self._details:
            # Not listed (catalog unreachable or model retired): the service has the final word.
            return _model_detail({**_UNLISTED_MODEL, "id": model, "name": model})
        return self._details[model]

    async def _load_catalog(self) -> None:
        base, _token = self._require_service()
        response = await self.client.get(f"{base}/models", headers=self.account.headers(), timeout=15.0)
        if response.status_code == 401:
            self.account.expire()
            raise KiraCloudAuthError("Sessione Kira Cloud scaduta. Accedi di nuovo dal pannello Modelli.")
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise StreamError("Kira Cloud model catalog exceeds size limit")
        payload = response.json()
        entries = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise StreamError("Kira Cloud returned an invalid model catalog")
        loaded: dict[str, Json] = {}
        for row in entries:
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip():
                loaded[row["id"].strip()] = _model_detail(row)
        self._details = loaded
        self._order = list(loaded)[:MAX_MODELS]
        self._catalog_loaded_at = time.monotonic()

    async def discover(self, *, force: bool = False) -> Json:
        unavailable: Json = {"available": False, "models": [], "capabilities": KIRA_CLOUD_CAPABILITIES}
        if not self.account.connected:
            return {**unavailable, "account": self.account.status(), "error_code": "AUTH_REQUIRED",
                    "error": "Accedi al tuo account Kira Cloud nel pannello Modelli per vedere i modelli."}
        try:
            async with asyncio.timeout(DISCOVERY_TIMEOUT):
                if force or not self._order or time.monotonic() - self._catalog_loaded_at > CATALOG_TTL:
                    await self._load_catalog()
        except KiraCloudAuthError as exc:
            return {**unavailable, "account": self.account.status(), "error_code": "AUTH_REQUIRED", "error": str(exc)}
        except Exception:
            if not self._order:
                return {**unavailable, "account": self.account.status(), "error_code": "PROVIDER_UNAVAILABLE",
                        "error": "Kira Cloud non è raggiungibile in questo momento. Riprova tra poco."}
        models: list[Json] = []
        # Prices travel with each row: the UI process shows them on the model cards and
        # uses the USD rates to compute the cost of a turn (it has no access to this catalog).
        currency = "USD" if str(self.account.status().get("currency") or "").upper() == "USD" else "EUR"
        for model_id in self._order:
            detail = self._details[model_id]
            caps = detail.get("capabilities", [])
            has_tools = "tools" in caps
            prices = _mapping(_mapping(detail.get("model_info")).get("pricing"))
            suffix = "usd" if currency == "USD" else "eur"
            models.append({
                "id": model_id,
                "name": detail["name"],
                "capabilities": {
                    **KIRA_CLOUD_CAPABILITIES,
                    "tools": has_tools,
                    "images": "vision" in caps,
                    "reasoning": bool(detail.get("supports_reasoning")),
                },
                "tools_metadata": has_tools,
                "tools_verified": has_tools,
                "tool_probe": "passed" if has_tools else "not_run",
                "context_window": detail.get("context_window"),
                "max_output_tokens": detail.get("max_output_tokens"),
                "supported_reasoning_efforts": list(detail.get("supported_reasoning_efforts", [])),
                "catalog_fallback": False,
                "pricing": {
                    "currency": currency,
                    "input_per_1m": prices.get(f"input_per_1m_{suffix}", 0.0),
                    "output_per_1m": prices.get(f"output_per_1m_{suffix}", 0.0),
                    "input_per_1m_usd": prices.get("input_per_1m_usd", 0.0),
                    "output_per_1m_usd": prices.get("output_per_1m_usd", 0.0),
                    "cached_input_per_1m_usd": prices.get("cached_input_per_1m_usd", 0.0),
                },
            })
        result: Json = {"available": True, "models": models, "capabilities": KIRA_CLOUD_CAPABILITIES,
                        "account": self.account.status()}
        if _json_size(result) > MAX_DISCOVERY_BYTES:
            result["models"] = result["models"][:48]
        return result

    async def _stream(
        self,
        body: Json,
        ctx: RunContext | None = None,
        item_id: str | None = None,
    ) -> tuple[Json, Json]:
        base, _token = self._require_service()
        ctx_model = ctx.session.get("model") if ctx and isinstance(ctx.session, dict) else None
        model_id = _model_name(str(body.get("model") or ctx_model or "kira-auto"))
        detail = await self._show(model_id)
        caps = detail.get("capabilities", [])
        vision = "vision" in caps

        raw_source = body.get("messages", [])
        has_image_attachments = bool(ctx is not None and ctx.image_inputs.get(ctx.run_id))
        if has_image_attachments and ctx is not None:
            if not vision:
                raise StreamError(f"Il modello Kira Cloud selezionato ({model_id}) non supporta allegati immagine")
            source = _bind_active_prompt(raw_source, ctx)
        else:
            source = raw_source

        window_tokens = int(detail.get("context_window") or 128_000)
        max_context_chars = min(MAX_CONTEXT_CHARS, max(60_000, int(window_tokens * 1.8)))
        image_inputs = ctx.image_inputs if ctx else {}
        messages, system_text = _chat_messages(
            _compact_messages(source, max_chars=max_context_chars), image_inputs, vision=vision)
        if body.get("format"):
            system_text += ("\nReturn one JSON object matching this schema, with no surrounding text: "
                            + json.dumps(body["format"]))

        requested_max = body.get("max_tokens", DEFAULT_REQUEST_MAX_TOKENS)
        if not isinstance(requested_max, int) or isinstance(requested_max, bool) or requested_max <= 0:
            requested_max = DEFAULT_REQUEST_MAX_TOKENS
        max_tokens = min(requested_max, int(detail.get("max_output_tokens") or DEFAULT_REQUEST_MAX_TOKENS),
                         max(1024, window_tokens // 4))
        if ctx:
            byte_limit = ctx.settings.get("max_output_bytes")
            if type(byte_limit) is int and byte_limit > 0:
                max_tokens = min(max_tokens, max(256, byte_limit // 4))

        effort = ctx.settings.get("reasoning_effort") if ctx else body.get("reasoning_effort")
        effort_str = effort.strip().lower() if isinstance(effort, str) else ""
        think = body.get("think")
        reasoning_cfg: Json | None = None
        if detail.get("supports_reasoning"):
            if think is False or think in {"off", "false", "disabled"} or effort_str in {"off", "none"}:
                reasoning_cfg = {"enabled": False}
            elif effort_str in {"minimal", "low", "medium", "high", "xhigh", "max"}:
                reasoning_cfg = {"effort": effort_str}
            elif think is True:
                reasoning_cfg = {"enabled": True}

        temperature: float | int | None = None
        options = body.get("options")
        if detail.get("supports_temperature") and isinstance(options, dict):
            temp = options.get("temperature")
            if isinstance(temp, (int, float)) and not isinstance(temp, bool):
                temperature = temp

        tools = body.get("tools", [])
        tool_defs: list[Json] = []
        if isinstance(tools, list) and tools and "tools" in caps:
            tool_defs = [
                {"type": "function", "function": {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "parameters": item["function"].get("parameters", {"type": "object"}),
                }}
                for item in tools
                if isinstance(item, dict) and isinstance(item.get("function"), dict)
            ]

        session_id = str(ctx.session.get("id") or "")[:256] if ctx and isinstance(ctx.session, dict) else ""
        use_cache = bool(detail.get("supports_prompt_cache"))
        use_temperature = temperature is not None

        def build_request(cur_messages: list[Json], cur_max_tokens: int) -> Json:
            req_messages: list[Json] = []
            if system_text:
                req_messages.append({"role": "system", "content": system_text})
            req_messages.extend(cur_messages)
            request: Json = {"model": model_id, "messages": req_messages,
                             "max_tokens": cur_max_tokens, "stream": True}
            if session_id:
                request["session_id"] = session_id
            if use_cache:
                request["cache_control"] = {"type": "ephemeral"}
            if tool_defs:
                request["tools"] = tool_defs
            if reasoning_cfg is not None:
                request["reasoning"] = reasoning_cfg
            if use_temperature and temperature is not None:
                request["temperature"] = temperature
            return request

        summary_item_id = f"{item_id}:summary" if item_id else None
        summary_status_emitted = False
        retries = 0
        overflow_retried = False
        param_fallback_retried = False
        waiting_notice_sent = False
        first_token_deadline = self._first_token_timeout or DEFAULT_FIRST_TOKEN_TIMEOUT
        limit = int(ctx.settings.get("max_output_bytes", 4 * 1024 * 1024)) if ctx else 65_536

        text = ""
        thought_text = ""
        reasoning_state = ""
        fragmented_calls: dict[int, Json] = {}
        input_tokens: int | None = None
        cached_input_tokens: int | None = None
        output_tokens: int | None = None
        reasoning_output_tokens: int | None = None
        charged_usd: float | None = None
        total_tokens: int | None = None
        finish_reason = ""
        while True:
            text = ""
            thought_text = ""
            reasoning_state = ""
            fragmented_calls = {}
            input_tokens = None
            cached_input_tokens = None
            output_tokens = None
            reasoning_output_tokens = None
            total_tokens = None
            finish_reason = ""
            total_bytes = 0
            received = False
            try:
                async with self.client.stream(
                    "POST",
                    f"{base}/chat/completions",
                    json=build_request(messages, max_tokens),
                    headers=self.account.headers(),
                    timeout=httpx.Timeout(300.0, connect=20.0, read=max(30.0, first_token_deadline + 15.0)),
                ) as response:
                    if response.status_code >= 400:
                        error_body = bytearray()
                        async for chunk in response.aiter_bytes():
                            error_body.extend(chunk[: max(0, 65_537 - len(error_body))])
                            if len(error_body) > 65_536:
                                break
                        code, detail_message = _service_error(response, bytes(error_body[:65_536]))
                        if code == "invalid_token":
                            self.account.expire()
                            raise KiraCloudAuthError(detail_message)
                        if code in ("context_length_exceeded", "request_too_large"):
                            # Too long for the model, or more expensive than the plan allows for a
                            # single request: compact harder and try once more.
                            if overflow_retried:
                                raise KiraCloudContextOverflow(detail_message)
                            overflow_retried = True
                            messages, _ = _chat_messages(
                                _compact_messages(source, max_chars=max(24_000, max_context_chars // 2)),
                                image_inputs, vision=vision)
                            max_tokens = min(max_tokens, 4096)
                            continue
                        if code == "invalid_request" and not param_fallback_retried and (use_cache or use_temperature):
                            param_fallback_retried = True
                            use_cache = False
                            use_temperature = False
                            continue
                        if code in _RETRYABLE_CODES and retries < len(self._backoff_delays):
                            delay = self._backoff_delays[retries]
                            retries += 1
                            if code == "too_many_concurrent" and ctx is not None and not waiting_notice_sent:
                                waiting_notice_sent = True
                                await ctx.emit("backend.notice", {"message": detail_message, "model": model_id})
                            if await _cancelable_sleep(delay, ctx):
                                continue
                        raise KiraCloudHTTPError(response, code, detail_message)

                    stream_started_at = time.monotonic()
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if ctx is not None and ctx.cancel.is_set():
                            raise asyncio.CancelledError
                        if len(line.encode("utf-8")) > 8_388_608:
                            raise StreamError("Kira Cloud stream record exceeds size limit")
                        if line.startswith(":"):
                            # Keep-alive while the model prepares its answer: bound the wait.
                            if not received and time.monotonic() - stream_started_at >= first_token_deadline:
                                raise _FirstTokenStall("first token deadline exceeded")
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                            continue
                        if line or not data_lines:
                            continue
                        raw = "\n".join(data_lines)
                        data_lines.clear()
                        if raw.strip() == "[DONE]":
                            break
                        total_bytes += len(raw.encode("utf-8"))
                        if total_bytes > limit * 2 + MAX_IMAGE_BASE64_CHARS:
                            raise StreamError("Kira Cloud response exceeded configured output limit")
                        try:
                            record = json.loads(raw)
                        except ValueError as exc:
                            raise StreamError("Kira Cloud emitted an invalid stream record") from exc
                        if not isinstance(record, dict):
                            continue
                        error = record.get("error")
                        if isinstance(error, dict):
                            err_message = " ".join(str(error.get("message") or "").split())[:400] or (
                                "Kira Cloud ha interrotto la risposta. Riprova.")
                            if not received and error.get("code") in _RETRYABLE_CODES:
                                raise _FirstTokenStall(err_message)
                            raise StreamError(err_message)

                        usage = record.get("usage")
                        if isinstance(usage, dict):
                            if isinstance(usage.get("prompt_tokens"), int):
                                input_tokens = usage["prompt_tokens"]
                            if isinstance(usage.get("completion_tokens"), int):
                                output_tokens = usage["completion_tokens"]
                            if isinstance(usage.get("total_tokens"), int):
                                total_tokens = usage["total_tokens"]
                            reported = usage.get("cost_usd")
                            if isinstance(reported, (int, float)) and not isinstance(reported, bool) and reported >= 0:
                                charged_usd = float(reported)
                            prompt_details = usage.get("prompt_tokens_details")
                            if isinstance(prompt_details, dict) and isinstance(prompt_details.get("cached_tokens"), int):
                                cached_input_tokens = prompt_details["cached_tokens"]
                            completion_details = usage.get("completion_tokens_details")
                            if isinstance(completion_details, dict) and isinstance(
                                    completion_details.get("reasoning_tokens"), int):
                                reasoning_output_tokens = completion_details["reasoning_tokens"]

                        choices = record.get("choices")
                        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                            continue
                        choice = choices[0]
                        if isinstance(choice.get("finish_reason"), str) and choice["finish_reason"]:
                            finish_reason = choice["finish_reason"]
                        delta = choice.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        if isinstance(delta.get("reasoning_state"), str) and delta["reasoning_state"]:
                            reasoning_state = delta["reasoning_state"]

                        reasoning_delta = delta.get("reasoning")
                        if isinstance(reasoning_delta, str) and reasoning_delta:
                            received = True
                            thought_text += reasoning_delta
                            if ctx and summary_item_id:
                                if not summary_status_emitted:
                                    summary_status_emitted = True
                                    await ctx.emit("reasoning.summary.status", {
                                        "source": "provider_summary", "provider": BACKEND, "status": "requested"})
                                await ctx.emit("reasoning.summary.delta", {
                                    "source": "provider_summary", "provider": BACKEND,
                                    "item_id": summary_item_id, "content": reasoning_delta})

                        content_delta = delta.get("content")
                        if isinstance(content_delta, str) and content_delta:
                            received = True
                            text += content_delta
                            if ctx:
                                await ctx.emit("message.delta", {"content": content_delta, "item_id": item_id})

                        call_deltas = delta.get("tool_calls")
                        if isinstance(call_deltas, list) and call_deltas:
                            received = True
                            for call in call_deltas:
                                if not isinstance(call, dict):
                                    continue
                                index = call.get("index", 0)
                                if not isinstance(index, int):
                                    index = 0
                                entry = fragmented_calls.setdefault(index, {
                                    "id": f"kira-{index}", "type": "function",
                                    "function": {"name": "", "arguments": ""}})
                                if isinstance(call.get("id"), str) and call["id"]:
                                    entry["id"] = call["id"]
                                fn_delta = call.get("function")
                                if isinstance(fn_delta, dict):
                                    if isinstance(fn_delta.get("name"), str) and fn_delta["name"]:
                                        entry["function"]["name"] += fn_delta["name"]
                                    if isinstance(fn_delta.get("arguments"), str) and fn_delta["arguments"]:
                                        entry["function"]["arguments"] += fn_delta["arguments"]
                    break
            except (_FirstTokenStall, httpx.TimeoutException, httpx.TransportError) as stall:
                if not received and retries < len(self._backoff_delays):
                    delay = self._backoff_delays[retries]
                    retries += 1
                    if await _cancelable_sleep(delay, ctx):
                        continue
                if isinstance(stall, _FirstTokenStall) and str(stall) != "first token deadline exceeded":
                    raise StreamError(str(stall)) from stall
                raise StreamError(
                    f"Il modello {model_id} su Kira Cloud non ha risposto in tempo. Riprova tra poco."
                ) from stall

        if ctx and summary_item_id and thought_text:
            await ctx.emit("reasoning.summary.completed", {
                "source": "provider_summary", "provider": BACKEND,
                "item_id": summary_item_id, "content": thought_text})

        tool_calls: list[Json] = []
        incomplete_tool_input = False
        for index in sorted(fragmented_calls):
            raw_call = fragmented_calls[index]
            fn_name = raw_call["function"].get("name", "")
            raw_args = raw_call["function"].get("arguments", "")
            if not fn_name:
                continue
            if finish_reason == "length":
                incomplete_tool_input = True
                continue
            parsed_args: Any = {}
            if raw_args.strip():
                try:
                    parsed_args = json.loads(raw_args)
                except ValueError:
                    incomplete_tool_input = True
                    continue
            if not isinstance(parsed_args, dict):
                incomplete_tool_input = True
                continue
            tool_calls.append({"id": raw_call["id"], "type": "function",
                               "function": {"name": fn_name, "arguments": parsed_args}})
        if incomplete_tool_input:
            tool_calls.clear()

        # Some models occasionally write a tool call into the visible text instead of returning it
        # as a structured call. Recover it when it names an offered tool; otherwise report the turn
        # as incomplete rather than presenting the markup as a finished answer.
        if not tool_calls and not incomplete_tool_input and _LEAKED_ARG.search(text):
            offered = {str(_mapping(tool.get("function")).get("name") or "")
                       for tool in body.get("tools", []) if isinstance(tool, dict)}
            text, recovered = _recover_leaked_tool_calls(text, offered)
            if recovered:
                tool_calls.extend(recovered)
            else:
                incomplete_tool_input = True

        if not text.strip() and not tool_calls and thought_text.strip():
            text = thought_text.strip()
            if ctx:
                await ctx.emit("message.delta", {"content": text, "item_id": item_id})

        message: Json = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        if reasoning_state:
            message["reasoning_state"] = reasoning_state

        # The balance changed: refresh it in the background, at most once per interval.
        refresh = asyncio.get_running_loop().create_task(self.account.refresh_if_stale())
        refresh.add_done_callback(lambda task: task.cancelled() or task.exception())

        done_reason = (
            "tool_use" if tool_calls
            else "tool_input_incomplete" if incomplete_tool_input
            else "length" if finish_reason == "length"
            else finish_reason or "stop"
        )
        terminal: Json = {
            "done": True,
            "done_reason": done_reason,
            "prompt_eval_count": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "eval_count": output_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
            "total_tokens": total_tokens,
            "max_output_tokens": max_tokens,
            "tool_input_incomplete": incomplete_tool_input,
        }
        if charged_usd is not None:
            # The service reports what it actually charged; the UI shows this, not an estimate.
            terminal["charged_usd"] = charged_usd
        return message, terminal
