"""OpenRouter API backend and key management for Kira Desktop.

Supports all OpenRouter models (Anthropic Claude, Google Gemini, OpenAI GPT/o-series,
DeepSeek, xAI Grok, Qwen, Mistral, Meta Llama, and ``openrouter/auto``) over
OpenRouter's unified Chat Completions SSE API with:
- Dynamic model capability & context-window adaptation from ``/api/v1/models``
- Full Kira tool loop (files, shell, isolated Browser & Computer vision, Questions,
  Knowledge Base, Project Tools, MCP, Team delegation, Studio, and Autonomous Goals)
- Live reasoning token streaming (``reasoning`` / ``reasoning_details``) and
  multi-turn ``reasoning_details`` preservation across tool calls
- Automatic prompt caching (``session_id`` sticky routing + ``cache_control``)
- Automatic ``max_tokens`` adaptation on HTTP 402 credit-reservation checks
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

BACKEND = "openrouter"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/antosec00/kira-desktop"
OPENROUTER_TITLE = "Kira Desktop"
MAX_AUTH_BYTES = 65_536
PNG_DATA_URL = "data:image/png;base64,"
MAX_IMAGE_BASE64_CHARS = 12_000_000
DEFAULT_REQUEST_MAX_TOKENS = 16_384
MAX_OPENROUTER_OUTPUT_TOKENS = 131_072
RATE_LIMIT_BACKOFF_DELAYS: tuple[float, ...] = (1.5, 3.5, 7.0)
FREE_FIRST_TOKEN_TIMEOUT = 15.0
DEFAULT_FIRST_TOKEN_TIMEOUT = 60.0

FREE_MODEL_FALLBACK_IDS: tuple[str, ...] = (
    "openrouter/free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "cohere/north-mini-code:free",
)

OPENROUTER_CAPABILITIES: Json = {
    **CAPABILITIES,
    "tools": True,
    "images": True,
    "context_controls": True,
}

FEATURED_MODEL_IDS: tuple[str, ...] = (
    "openrouter/auto",
    "openrouter/free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "cohere/north-mini-code:free",
    "anthropic/claude-opus-5.5",
    "anthropic/claude-sonnet-5.5",
    "anthropic/claude-haiku-5.5",
    "google/gemini-4-argon",
    "google/gemini-3.8-flash",
    "openai/gpt-6.1-terra-pro",
    "openai/gpt-6.1-terra",
    "openai/gpt-6.1-sol-pro",
    "deepseek/deepseek-v4.1-flash",
    "deepseek/deepseek-v4-pro-0813",
    "x-ai/grok-4.7",
    "qwen/qwen3.8-max-prime",
    "mistralai/mistral-large-4-0",
    "meta-llama/llama-4-maverick",
)

BUILTIN_MODEL_CATALOG: tuple[Json, ...] = (
    {
        "id": "openrouter/auto",
        "name": "OpenRouter: Auto Router (Best Model)",
        "context_length": 1_000_000,
        "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 128_000},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000002"},
    },
    {
        "id": "openrouter/free",
        "name": "OpenRouter: Free Smart Router (Auto Free)",
        "context_length": 200_000,
        "top_provider": {"context_length": 200_000, "max_completion_tokens": 32_768},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0", "completion": "0"},
    },
    {
        "id": "nvidia/nemotron-3.5-lightning:free",
        "name": "NVIDIA: Nemotron 3.5 Lightning (free)",
        "context_length": 262_144,
        "top_provider": {"context_length": 262_144, "max_completion_tokens": 32_768},
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0", "completion": "0"},
    },
    {
        "id": "nvidia/nemotron-3.5-super-vl:free",
        "name": "NVIDIA: Nemotron 3.5 Super VL (free)",
        "context_length": 262_144,
        "top_provider": {"context_length": 262_144, "max_completion_tokens": 32_768},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0", "completion": "0"},
    },
    {
        "id": "cohere/north-mini-code:free",
        "name": "Cohere: North Mini Code (free)",
        "context_length": 262_144,
        "top_provider": {"context_length": 262_144, "max_completion_tokens": 32_768},
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0", "completion": "0"},
    },
    {
        "id": "anthropic/claude-opus-5.5",
        "name": "Anthropic: Claude Opus 5.5",
        "context_length": 1_000_000,
        "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 128_000},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "max_tokens"],
        "pricing": {"prompt": "0.000004", "completion": "0.00002", "input_cache_read": "0.0000002"},
    },
    {
        "id": "anthropic/claude-sonnet-5.5",
        "name": "Anthropic: Claude Sonnet 5.5",
        "context_length": 1_000_000,
        "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 128_000},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "max_tokens"],
        "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000001"},
    },
    {
        "id": "anthropic/claude-haiku-5.5",
        "name": "Anthropic: Claude Haiku 5.5",
        "context_length": 1_000_000,
        "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 128_000},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "max_tokens"],
        "pricing": {"prompt": "0.0000006", "completion": "0.000003", "input_cache_read": "0.00000003"},
    },
    {
        "id": "google/gemini-4-argon",
        "name": "Google: Gemini 4 Argon",
        "context_length": 1_048_576,
        "top_provider": {"context_length": 1_048_576, "max_completion_tokens": 943_718},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000001"},
    },
    {
        "id": "google/gemini-3.8-flash",
        "name": "Google: Gemini 3.8 Flash",
        "context_length": 1_048_576,
        "top_provider": {"context_length": 1_048_576, "max_completion_tokens": 65_536},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0.0000003", "completion": "0.0000025", "input_cache_read": "0.00000003"},
    },
    {
        "id": "openai/gpt-6.1-terra-pro",
        "name": "OpenAI: GPT-6.1 Terra Pro",
        "context_length": 1_050_000,
        "top_provider": {"context_length": 1_050_000, "max_completion_tokens": 128_000},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "max_tokens"],
        "pricing": {"prompt": "0.000001", "completion": "0.000005", "input_cache_read": "0.00000005"},
    },
    {
        "id": "openai/gpt-6.1-terra",
        "name": "OpenAI: GPT-6.1 Terra",
        "context_length": 1_050_000,
        "top_provider": {"context_length": 1_050_000, "max_completion_tokens": 128_000},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "max_tokens"],
        "pricing": {"prompt": "0.0000005", "completion": "0.0000025", "input_cache_read": "0.000000025"},
    },
    {
        "id": "deepseek/deepseek-v4.1-flash",
        "name": "DeepSeek: V4.1 Flash",
        "context_length": 1_000_000,
        "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 131_072},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0.00000027", "completion": "0.0000011", "input_cache_read": "0.00000007"},
    },
    {
        "id": "x-ai/grok-4.7",
        "name": "xAI: Grok 4.7",
        "context_length": 1_000_000,
        "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 131_072},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000002"},
    },
    {
        "id": "qwen/qwen3.8-max-prime",
        "name": "Qwen: Qwen 3.8 Max Prime",
        "context_length": 1_000_000,
        "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 131_072},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0.0000012", "completion": "0.000006", "input_cache_read": "0.00000012"},
    },
    {
        "id": "mistralai/mistral-large-4-0",
        "name": "Mistral: Mistral Large 4.0",
        "context_length": 262_144,
        "top_provider": {"context_length": 262_144, "max_completion_tokens": 131_072},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "tool_choice", "reasoning", "reasoning_effort", "temperature", "max_tokens"],
        "pricing": {"prompt": "0.000002", "completion": "0.000006"},
    },
)

_AFFORDABLE_TOKENS_RE = re.compile(r"can only afford\s+(\d+)", re.IGNORECASE)


class OpenRouterAuthError(StreamError):
    """Safe, non-secret OpenRouter authentication or configuration failure."""


class OpenRouterContextOverflow(StreamError):
    """A bounded HTTP rejection identifying an oversized prompt context."""

    context_overflow = True


class OpenRouterHTTPError(httpx.HTTPStatusError):
    """HTTP failure with a bounded, redacted OpenRouter diagnostic."""

    def __init__(self, response: httpx.Response, detail: str) -> None:
        super().__init__(
            f"OpenRouter HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
        self.safe_detail = detail


def _safe_error_detail(body: bytes, secret: str = "") -> str:
    """Extract bounded error code/type/message/metadata from an OpenRouter error body."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        text = body.decode("utf-8", errors="ignore").strip()
        if secret:
            text = text.replace(secret, "[redacted]")
        return " ".join(text.split())[:360]
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error", payload)
    if not isinstance(error, dict):
        return ""
    parts: list[str] = []
    for key in ("code", "type", "message"):
        value = error.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        cleaned = " ".join(str(value).split())
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
        cleaned = cleaned[:300]
        if cleaned:
            parts.append(f"{key}={cleaned}" if key != "message" else cleaned)
    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        raw_meta = metadata.get("raw") or metadata.get("provider_name")
        if isinstance(raw_meta, str) and raw_meta.strip():
            cleaned_meta = " ".join(raw_meta.split())
            if secret:
                cleaned_meta = cleaned_meta.replace(secret, "[redacted]")
            parts.append(f"upstream={cleaned_meta[:240]}")
    return "; ".join(parts)[:720]


def _confirmed_context_overflow(body: bytes) -> bool:
    text = body.decode("utf-8", errors="ignore").casefold()
    terms = (
        "context length exceeded",
        "context_length_exceeded",
        "context window exceeded",
        "maximum context length",
        "context limit exceeded",
        "input tokens exceed",
        "prompt is too long",
        "maximum prompt length",
        "prompt token count exceeds",
        "too many input tokens",
    )
    return any(term in text for term in terms)


def _extract_affordable_tokens(detail: str) -> int | None:
    match = _AFFORDABLE_TOKENS_RE.search(detail)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _mask_key(api_key: str) -> str:
    cleaned = api_key.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 12:
        return cleaned[:4] + "…"
    return f"{cleaned[:9]}…{cleaned[-4:]}"


def _parse_custom_models(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("\n", ",").split(",")]
    elif isinstance(value, list):
        items = [str(part).strip() for part in value if isinstance(part, str)]
    else:
        return []
    result: list[str] = []
    for item in items:
        if not item or len(item) > 160 or any(ord(c) < 33 or ord(c) > 126 for c in item):
            continue
        if item not in result:
            result.append(item)
    return result[:32]


def _env_openrouter_key() -> str:
    val = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if val and len(val) <= 4096 and all(33 <= ord(c) <= 126 for c in val):
        return val
    return ""


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


class OpenRouterAccount:
    """Manage OpenRouter API key configuration and key/credit metadata in Kira's private profile."""

    def __init__(
        self,
        directory: Path,
        protect_secrets: Callable[[tuple[str, ...]], None] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise KiraError("UNSAFE_PATH", "OpenRouter credential storage cannot be a symlink")
        self.directory.chmod(0o700)
        info = self.directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise KiraError("UNSAFE_PATH", "OpenRouter credential storage must be private and user-owned")
        self.path = self.directory / "account.json"
        self.protect_secrets = protect_secrets
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=False,
            trust_env=False,
        )
        self.api_key: str = ""
        self.custom_models: list[str] = []
        self.key_info: Json = {}
        self._load()

    def _load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            env_key = _env_openrouter_key()
            if env_key and self.protect_secrets:
                self.protect_secrets((env_key,))
            return
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise KiraError("UNSAFE_PATH", "OpenRouter credential file must be private and user-owned")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_AUTH_BYTES + 1)
            if len(raw) > MAX_AUTH_BYTES:
                raise ValueError("OpenRouter credential file exceeds size limit")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("Invalid OpenRouter credential version")
            api_key = data.get("api_key", "")
            if not isinstance(api_key, str) or len(api_key) > 4096:
                raise ValueError("Invalid OpenRouter API key")
            if api_key and any(ord(c) < 33 or ord(c) > 126 for c in api_key):
                raise ValueError("OpenRouter API key contains invalid characters")
            self.api_key = api_key
            self.custom_models = _parse_custom_models(data.get("custom_models", []))
            raw_info = data.get("key_info")
            self.key_info = dict(raw_info) if isinstance(raw_info, dict) else {}
            secrets_to_protect = tuple(s for s in (self.api_key, _env_openrouter_key()) if s)
            if secrets_to_protect and self.protect_secrets:
                self.protect_secrets(secrets_to_protect)
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise KiraError("CREDENTIAL_STORE", "OpenRouter account storage is invalid") from exc
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _save(self) -> None:
        payload: Json = {
            "version": 1,
            "api_key": self.api_key,
            "custom_models": self.custom_models,
            "key_info": self.key_info,
        }
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
        secrets_to_protect = tuple(s for s in (self.api_key, _env_openrouter_key()) if s)
        if secrets_to_protect and self.protect_secrets:
            self.protect_secrets(secrets_to_protect)

    def effective_api_key(self) -> str:
        return self.api_key or _env_openrouter_key()

    @property
    def connected(self) -> bool:
        return bool(self.effective_api_key())

    def status(self) -> Json:
        eff_key = self.effective_api_key()
        env_key = _env_openrouter_key()
        using_env = not self.api_key and bool(env_key)
        auth_source = "saved" if self.api_key else ("env" if env_key else "none")
        label = str(self.key_info.get("label") or _mask_key(eff_key))
        usage = self.key_info.get("usage")
        limit_rem = self.key_info.get("limit_remaining")
        return {
            "connected": bool(eff_key),
            "api_key_configured": bool(self.api_key),
            "env_key_detected": bool(env_key),
            "env_key_available": bool(env_key),
            "using_env_key": using_env,
            "auth_source": auth_source,
            "key_label": label,
            "label": label,
            "usage": usage,
            "usage_usd": usage,
            "usage_daily": self.key_info.get("usage_daily"),
            "usage_monthly": self.key_info.get("usage_monthly"),
            "limit": self.key_info.get("limit"),
            "limit_remaining": limit_rem,
            "limit_remaining_usd": limit_rem,
            "is_free_tier": self.key_info.get("is_free_tier"),
            "key_info": dict(self.key_info),
            "custom_models": list(self.custom_models),
        }

    async def refresh_key_info(self) -> Json:
        key = self.effective_api_key()
        if not key:
            self.key_info = {}
            return self.status()
        try:
            response = await self.client.get(
                f"{OPENROUTER_API_BASE}/key",
                headers={
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": OPENROUTER_REFERER,
                    "X-Title": OPENROUTER_TITLE,
                    "User-Agent": f"Kira-Desktop/{__version__}",
                },
                timeout=12.0,
            )
            if response.status_code == 401:
                raise KiraError("AUTH_REJECTED", "Chiave API OpenRouter non valida o revocata (HTTP 401)")
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                self.key_info = {
                    "label": str(data.get("label") or _mask_key(key))[:80],
                    "usage": data.get("usage") if isinstance(data.get("usage"), (int, float)) else None,
                    "usage_daily": (
                        data.get("usage_daily") if isinstance(data.get("usage_daily"), (int, float)) else None
                    ),
                    "usage_monthly": (
                        data.get("usage_monthly") if isinstance(data.get("usage_monthly"), (int, float)) else None
                    ),
                    "limit": data.get("limit") if isinstance(data.get("limit"), (int, float)) else None,
                    "limit_remaining": (
                        data.get("limit_remaining")
                        if isinstance(data.get("limit_remaining"), (int, float))
                        else None
                    ),
                    "is_free_tier": data.get("is_free_tier") if isinstance(data.get("is_free_tier"), bool) else None,
                }
                if self.api_key:
                    self._save()
        except KiraError:
            raise
        except Exception:
            if not self.key_info.get("label"):
                self.key_info = {"label": _mask_key(key)}
        return self.status()

    def configure(self, api_key: str = "", custom_models: Any = None) -> Json:
        cleaned_key = api_key.strip()
        if cleaned_key:
            if len(cleaned_key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in cleaned_key):
                raise KiraError("INVALID_ARGUMENT", "La chiave API OpenRouter non è valida")
            self.api_key = cleaned_key
            self.key_info = {"label": _mask_key(cleaned_key)}
        if custom_models is not None:
            self.custom_models = _parse_custom_models(custom_models)
        if not self.effective_api_key():
            raise KiraError("INVALID_ARGUMENT", "Inserisci una chiave API OpenRouter valida (sk-or-v1-...)")
        self._save()
        return self.status()

    def logout(self) -> Json:
        self.api_key = ""
        self.key_info = {}
        self._save()
        return self.status()

    def clear(self) -> Json:
        return self.logout()

    async def close(self) -> None:
        await self.client.aclose()


class OpenRouterAccounts:
    """Service-side manager for OpenRouter credentials and configuration."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self._auth: OpenRouterAccount | None = None
        self.changing = False

    @property
    def auth(self) -> OpenRouterAccount:
        if self._auth is None:
            self._auth = OpenRouterAccount(
                self.service.paths.config / "openrouter",
                protect_secrets=functools.partial(
                    self.service.redactor.set_provider_values,
                    owner=BACKEND,
                ),
            )
        return self._auth

    def busy(self) -> bool:
        return self.changing

    async def dispatch(self, action: str, params: Json | None = None) -> Json:
        params = params or {}
        if action == "status":
            return self.auth.status()
        if self.changing:
            raise KiraError("BUSY", "Le impostazioni OpenRouter sono già in fase di aggiornamento")
        self.changing = True
        try:
            if action == "configure":
                api_key = str(params.get("api_key") or "")
                custom_models = params.get("custom_models")
                result = self.auth.configure(api_key=api_key, custom_models=custom_models)
                provider = self.service._control_backends.pop(BACKEND, None)
                if provider is not None:
                    await provider.close()
                return result
            if action == "refresh":
                return await self.auth.refresh_key_info()
            if action == "logout":
                result = self.auth.logout()
                provider = self.service._control_backends.pop(BACKEND, None)
                if provider is not None:
                    await provider.close()
                return result
            raise KiraError("INVALID_ARGUMENT", "Azione OpenRouter non supportata")
        finally:
            self.changing = False

    async def prepare(self) -> None:
        if self.changing:
            raise KiraError("BUSY", "Le impostazioni OpenRouter sono in fase di modifica")
        if self.service.backend_factory is not None and self._auth is None:
            return
        if not self.auth.connected:
            raise KiraError(
                "AUTH_REQUIRED",
                "Configura la tua chiave API OpenRouter nel pannello Modelli prima di usare questo provider",
            )

    async def close(self) -> None:
        if self._auth is not None:
            await self._auth.close()
            self._auth = None


def _parse_price_per_m(raw: Any, default: float = 0.0) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            val = float(raw.strip())
        except ValueError:
            return default
    else:
        return default
    if val < 0:
        return default
    return round(val * 1_000_000, 6)


def _model_detail_from_row(row: Json, *, fallback: bool = False) -> Json:
    model_id = str(row.get("id") or "").strip()
    name = str(row.get("name") or model_id).strip() or model_id
    raw_top = row.get("top_provider")
    top_provider: Json = raw_top if isinstance(raw_top, dict) else {}
    ctx_len = top_provider.get("context_length") or row.get("context_length") or 128_000
    context_window = int(ctx_len) if isinstance(ctx_len, int) and ctx_len > 0 else 128_000
    max_comp = top_provider.get("max_completion_tokens") or row.get("max_output_tokens")
    max_output_tokens = (
        int(max_comp)
        if isinstance(max_comp, int) and max_comp > 0
        else min(MAX_OPENROUTER_OUTPUT_TOKENS, max(4096, context_window // 4))
    )
    raw_arch = row.get("architecture")
    arch: Json = raw_arch if isinstance(raw_arch, dict) else {}
    raw_mods = arch.get("input_modalities")
    if not isinstance(raw_mods, list):
        row_mods = row.get("input_modalities")
        modalities: list[Any] = row_mods if isinstance(row_mods, list) else ["text"]
    else:
        modalities = raw_mods
    vision = "image" in modalities
    supported_params_raw = row.get("supported_parameters")
    supported_params: set[str] = (
        {str(p) for p in supported_params_raw if isinstance(p, str)}
        if isinstance(supported_params_raw, list)
        else {"tools", "tool_choice", "temperature", "max_tokens"}
    )
    supports_tools = "tools" in supported_params or not supported_params_raw
    supports_reasoning = any(
        p in supported_params for p in ("reasoning", "reasoning_effort", "include_reasoning")
    )
    supports_temperature = "temperature" in supported_params or not supported_params_raw
    raw_pricing = row.get("pricing")
    pricing: Json = raw_pricing if isinstance(raw_pricing, dict) else {}
    in_per_m = _parse_price_per_m(pricing.get("prompt"), 1.0)
    out_per_m = _parse_price_per_m(pricing.get("completion"), 4.0)
    cache_read_per_m = _parse_price_per_m(pricing.get("input_cache_read"), in_per_m)
    cache_write_per_m = _parse_price_per_m(pricing.get("input_cache_write"), in_per_m)
    supports_cache_control = "input_cache_read" in pricing or model_id.startswith(
        ("anthropic/", "google/", "openai/", "deepseek/")
    )
    register_dynamic_model_pricing(
        BACKEND,
        model_id,
        ModelPricing(
            family=model_id,
            billing_mode="api",
            input_per_m=in_per_m,
            cached_input_per_m=cache_read_per_m,
            cache_write_per_m=cache_write_per_m,
            output_per_m=out_per_m,
            default_context_window=context_window,
        ),
    )
    model_caps = [
        *(["tools"] if supports_tools else []),
        *(["vision"] if vision else []),
    ]
    efforts = ["off", "low", "medium", "high", "xhigh"] if supports_reasoning else []
    return {
        "name": name,
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
        "capabilities": model_caps,
        "supported_parameters": sorted(supported_params),
        "supports_reasoning": supports_reasoning,
        "supports_temperature": supports_temperature,
        "supports_cache_control": supports_cache_control,
        "supported_reasoning_efforts": efforts,
        "created": row.get("created") if isinstance(row.get("created"), int) else 0,
        "model_info": {
            "id": model_id,
            "name": name,
            "context_length": context_window,
            "max_completion_tokens": max_output_tokens,
            "input_modalities": list(modalities),
            "supported_parameters": sorted(supported_params),
            "pricing": {
                "input_per_1m_usd": in_per_m,
                "output_per_1m_usd": out_per_m,
                "cached_input_per_1m_usd": cache_read_per_m,
            },
        },
        "catalog_fallback": fallback,
    }


def _message_serialized_chars(msg: Json) -> int:
    content = msg.get("content", "")
    total = len(content) if isinstance(content, str) else len(json.dumps(content, ensure_ascii=False))
    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        total += len(json.dumps(tool_calls, ensure_ascii=False))
    return total


def _compact_messages_for_openrouter(source: Any, max_chars: int = 240_000) -> list[Json]:
    """Bound historical tool outputs to fit the selected OpenRouter model's context window."""
    if not isinstance(source, list):
        return []
    cloned: list[Json] = [dict(m) for m in source if isinstance(m, dict)]
    total = sum(_message_serialized_chars(m) for m in cloned)
    if total <= max_chars:
        return cloned

    non_system_indices = [i for i, m in enumerate(cloned) if m.get("role") != "system"]
    protected_tail = set(non_system_indices[-6:])

    for idx in non_system_indices:
        if total <= max_chars:
            break
        if idx in protected_tail:
            continue
        msg = cloned[idx]
        role = msg.get("role")
        content = msg.get("content")
        if role == "tool" and isinstance(content, str) and len(content) > 1200:
            saved = len(content) - 1050
            cloned[idx] = {
                **msg,
                "content": content[:800] + "\n...[truncated historical tool output]...\n" + content[-200:],
            }
            total -= saved
        elif (
            role == "user"
            and isinstance(content, str)
            and content.startswith("Recorded tool outcome (historical data,")
            and len(content) > 1200
        ):
            saved = len(content) - 1050
            cloned[idx] = {
                **msg,
                "content": content[:800] + "\n...[truncated historical tool outcome]...\n" + content[-200:],
            }
            total -= saved

    if total <= max_chars:
        return cloned

    protected_tail_strict = set(non_system_indices[-4:])
    for idx in non_system_indices:
        if total <= max_chars:
            break
        if idx in protected_tail_strict:
            continue
        msg = cloned[idx]
        content = msg.get("content")
        if isinstance(content, str) and len(content) > 500:
            saved = len(content) - 420
            cloned[idx] = {**msg, "content": content[:320] + "\n...[compacted]...\n" + content[-80:]}
            total -= saved
    return cloned


def _attachment_image_part(image: Any) -> Json:
    if (
        not isinstance(image, dict)
        or image.get("type") != "input_image"
        or not isinstance(image.get("image_url"), str)
        or not image["image_url"].startswith(PNG_DATA_URL)
    ):
        raise StreamError("OpenRouter received an invalid Kira image attachment")
    data = image["image_url"][len(PNG_DATA_URL) :]
    if len(data) > MAX_IMAGE_BASE64_CHARS:
        raise StreamError("OpenRouter image attachment exceeds size limit")
    return {"type": "image_url", "image_url": {"url": image["image_url"], "detail": "auto"}}


def _tool_image_part(image: Any) -> Json:
    data = image[len(PNG_DATA_URL) :] if isinstance(image, str) and image.startswith(PNG_DATA_URL) else image
    if not isinstance(data, str) or not data or len(data) > MAX_IMAGE_BASE64_CHARS:
        raise StreamError("OpenRouter received an invalid or oversized Kira tool image")
    try:
        base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise StreamError("OpenRouter received an invalid Kira tool image") from exc
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
            return [*source[:index], {**message, "run_id": ctx.run_id}, *source[index + 1 :]]
    raise StreamError("OpenRouter image inputs have no matching user turn")


def _openrouter_messages(
    source: Any,
    image_inputs: dict[str, list[Json]],
    *,
    vision: bool = True,
) -> tuple[list[Json], str]:
    """Translate Kira's internal message history to OpenRouter's Chat Completions format."""
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
            continue
        if role == "assistant":
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
                    call_id = f"openrouter-call-{len(result)}-{idx}"
                args_str = (
                    args
                    if isinstance(args, str)
                    else json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False)
                )
                out_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": args_str},
                    }
                )
                pending_ids.setdefault(name, []).append(call_id)
            text_str = content if isinstance(content, str) else ""
            if not text_str and not out_calls:
                continue
            asst_msg: Json = {"role": "assistant", "content": text_str}
            if out_calls:
                asst_msg["tool_calls"] = out_calls
            reasoning_details = message.get("reasoning_details")
            if isinstance(reasoning_details, list) and reasoning_details:
                asst_msg["reasoning_details"] = reasoning_details
            result.append(asst_msg)
        elif role == "tool":
            name = message.get("tool_name")
            if not isinstance(name, str) or not name:
                continue
            call_id = message.get("tool_call_id")
            candidates = pending_ids.get(name, [])
            if not isinstance(call_id, str) or not call_id:
                call_id = candidates.pop(0) if candidates else f"openrouter-tool-{len(result)}"
            elif call_id in candidates:
                candidates.remove(call_id)
            value = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            images = message.get("images", [])
            image_parts = (
                [_tool_image_part(img) for img in images]
                if (vision and isinstance(images, list) and images)
                else []
            )
            tool_content: str | list[Json] = (
                [{"type": "text", "text": value}, *image_parts] if image_parts else value
            )
            result.append({"role": "tool", "tool_call_id": call_id, "content": tool_content})
        elif role == "user":
            if isinstance(content, str):
                run_id = message.get("run_id")
                images = image_inputs.get(run_id, []) if (vision and isinstance(run_id, str)) else []
                if images:
                    parts: list[Json] = []
                    if content:
                        parts.append({"type": "text", "text": content})
                    parts.extend(_attachment_image_part(img) for img in images)
                    result.append({"role": "user", "content": parts})
                elif content:
                    result.append({"role": "user", "content": content})

    if not result:
        result.append({"role": "user", "content": "Continue."})
    return result, "\n\n".join(system_parts)


def _merge_reasoning_detail(accumulated: list[Json], item: Json) -> None:
    """Merge a streamed OpenRouter reasoning_details delta into accumulated blocks."""
    rtype = item.get("type")
    if not isinstance(rtype, str):
        return
    rindex = item.get("index") if isinstance(item.get("index"), int) else 0
    existing = next(
        (b for b in accumulated if b.get("type") == rtype and b.get("index", 0) == rindex),
        None,
    )
    if existing is None:
        entry: Json = {"type": rtype, "index": rindex}
        for k in ("id", "format", "signature"):
            if isinstance(item.get(k), str) and item.get(k):
                entry[k] = item[k]
        if rtype == "reasoning.text":
            entry["text"] = str(item.get("text") or "")
        elif rtype == "reasoning.summary":
            entry["summary"] = str(item.get("summary") or "")
        elif rtype == "reasoning.encrypted":
            entry["data"] = str(item.get("data") or "")
        accumulated.append(entry)
        return
    for k in ("id", "format", "signature"):
        if isinstance(item.get(k), str) and item.get(k):
            existing[k] = item[k]
    if rtype == "reasoning.text" and isinstance(item.get("text"), str):
        existing["text"] = existing.get("text", "") + item["text"]
    elif rtype == "reasoning.summary" and isinstance(item.get("summary"), str):
        existing["summary"] = existing.get("summary", "") + item["summary"]
    elif rtype == "reasoning.encrypted" and isinstance(item.get("data"), str):
        existing["data"] = existing.get("data", "") + item["data"]


def _is_free_openrouter_model(model_id: str) -> bool:
    cleaned = model_id.strip().lower()
    return cleaned.endswith(":free") or cleaned == "openrouter/free"


class _OpenRouterUpstreamStallError(StreamError):
    """Raised when an OpenRouter upstream stalls on `: OPENROUTER PROCESSING` or mid-stream error before emitting tokens."""


class OpenRouterBackend(OllamaBackend):
    """OpenRouter Chat Completions streaming backend with full Kira tool and reasoning support."""

    def __init__(
        self,
        account: OpenRouterAccount,
        client: httpx.AsyncClient | None = None,
        backoff_delays: tuple[float, ...] = RATE_LIMIT_BACKOFF_DELAYS,
        first_token_timeout: float | None = None,
    ) -> None:
        super().__init__(OPENROUTER_API_BASE)
        if client is not None:
            self.client = client
        else:
            self.client = httpx.AsyncClient(
                base_url=OPENROUTER_API_BASE,
                timeout=httpx.Timeout(180.0, connect=10.0),
                follow_redirects=False,
                trust_env=False,
            )
        self.account = account
        self._details: dict[str, Json] = {}
        self._catalog_loaded_at: float = 0.0
        self._backoff_delays = backoff_delays
        self._first_token_timeout = first_token_timeout
        self._populate_builtin_details()

    def _populate_builtin_details(self) -> None:
        for row in BUILTIN_MODEL_CATALOG:
            model_id = str(row["id"])
            if model_id not in self._details:
                self._details[model_id] = _model_detail_from_row(row, fallback=True)
        for custom_id in self.account.custom_models:
            if custom_id not in self._details:
                self._details[custom_id] = _model_detail_from_row(
                    {
                        "id": custom_id,
                        "name": custom_id,
                        "context_length": 200_000,
                        "top_provider": {"context_length": 200_000, "max_completion_tokens": 32_768},
                        "architecture": {"input_modalities": ["text", "image"]},
                        "supported_parameters": [
                            "tools",
                            "tool_choice",
                            "reasoning",
                            "reasoning_effort",
                            "temperature",
                            "max_tokens",
                        ],
                    },
                    fallback=True,
                )

    async def _probe_tools(self, model: str, ctx: RunContext) -> bool:
        detail = await self._show(model)
        return "tools" in detail.get("capabilities", [])

    def _probe_outcome(self, provider: str, model: str, detail: Json) -> str:
        return "passed" if "tools" in detail.get("capabilities", []) else "not_run"

    async def _show(self, model: str, *, refresh: bool = False) -> Json:
        model = _model_name(model)
        if not refresh and model in self._details:
            return self._details[model]
        with contextlib.suppress(Exception):
            await self._load_catalog()
        if model not in self._details:
            self._details[model] = _model_detail_from_row(
                {
                    "id": model,
                    "name": model,
                    "context_length": 200_000,
                    "top_provider": {"context_length": 200_000, "max_completion_tokens": 32_768},
                    "architecture": {"input_modalities": ["text", "image"]},
                    "supported_parameters": [
                        "tools",
                        "tool_choice",
                        "reasoning",
                        "reasoning_effort",
                        "temperature",
                        "max_tokens",
                    ],
                },
                fallback=True,
            )
        return self._details[model]

    async def _load_catalog(self) -> None:
        headers = {
            "HTTP-Referer": OPENROUTER_REFERER,
            "X-Title": OPENROUTER_TITLE,
            "User-Agent": f"Kira-Desktop/{__version__}",
        }
        key = self.account.effective_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        response = await self.client.get(f"{OPENROUTER_API_BASE}/models", headers=headers, timeout=15.0)
        response.raise_for_status()
        if len(response.content) > 4_000_000:
            raise StreamError("OpenRouter model catalog exceeds size limit")
        payload = response.json()
        entries = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise StreamError("OpenRouter returned an invalid model catalog")
        loaded: dict[str, Json] = {}
        for row in entries:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            model_id = row["id"].strip()
            if not model_id:
                continue
            loaded[model_id] = _model_detail_from_row(row, fallback=False)
        for row in BUILTIN_MODEL_CATALOG:
            bid = str(row["id"])
            if bid not in loaded:
                loaded[bid] = _model_detail_from_row(row, fallback=True)
        for custom_id in self.account.custom_models:
            if custom_id not in loaded:
                loaded[custom_id] = _model_detail_from_row(
                    {
                        "id": custom_id,
                        "name": custom_id,
                        "context_length": 200_000,
                        "top_provider": {"context_length": 200_000, "max_completion_tokens": 32_768},
                        "architecture": {"input_modalities": ["text", "image"]},
                        "supported_parameters": [
                            "tools",
                            "tool_choice",
                            "reasoning",
                            "reasoning_effort",
                            "temperature",
                            "max_tokens",
                        ],
                    },
                    fallback=True,
                )
        self._details = loaded
        self._catalog_loaded_at = time.monotonic()

    def _prioritized_model_ids(self) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for custom_id in self.account.custom_models:
            if custom_id in self._details and custom_id not in seen:
                ordered.append(custom_id)
                seen.add(custom_id)
        for featured_id in FEATURED_MODEL_IDS:
            if featured_id in self._details and featured_id not in seen:
                ordered.append(featured_id)
                seen.add(featured_id)
        remaining = [
            (mid, det)
            for mid, det in self._details.items()
            if mid not in seen and not mid.endswith(":batch") and "tools" in det.get("capabilities", [])
        ]
        remaining.sort(key=lambda item: int(item[1].get("created") or 0), reverse=True)
        for mid, _ in remaining:
            if len(ordered) >= MAX_MODELS:
                break
            ordered.append(mid)
            seen.add(mid)
        if len(ordered) < MAX_MODELS:
            for mid in self._details:
                if len(ordered) >= MAX_MODELS:
                    break
                if mid not in seen and not mid.endswith(":batch"):
                    ordered.append(mid)
                    seen.add(mid)
        return ordered[:MAX_MODELS]

    async def discover(self, *, force: bool = False) -> Json:
        try:
            self._populate_builtin_details()
            status = self.account.status()
            if not self.account.connected:
                return {
                    "available": False,
                    "models": [],
                    "capabilities": OPENROUTER_CAPABILITIES,
                    "account": status,
                    "error_code": "AUTH_REQUIRED",
                    "error": "Inserisci la tua chiave API OpenRouter nel pannello Modelli per collegare i modelli.",
                }
            async with asyncio.timeout(DISCOVERY_TIMEOUT):
                if (
                    force
                    or self._catalog_loaded_at == 0.0
                    or time.monotonic() - self._catalog_loaded_at > 120.0
                ):
                    with contextlib.suppress(Exception):
                        await self._load_catalog()
                if force or self.account.key_info.get("usage") is None:
                    with contextlib.suppress(Exception):
                        await self.account.refresh_key_info()
            models: list[Json] = []
            for model_id in self._prioritized_model_ids():
                detail = self._details[model_id]
                caps = detail.get("capabilities", [])
                has_tools = "tools" in caps
                has_vision = "vision" in caps
                has_reasoning = bool(detail.get("supports_reasoning"))
                models.append(
                    {
                        "id": model_id,
                        "name": detail["name"],
                        "capabilities": {
                            **OPENROUTER_CAPABILITIES,
                            "tools": has_tools,
                            "images": has_vision,
                            "reasoning": has_reasoning,
                        },
                        "tools_metadata": has_tools,
                        "tools_verified": has_tools,
                        "tool_probe": "passed" if has_tools else "not_run",
                        "context_window": detail.get("context_window"),
                        "max_output_tokens": detail.get("max_output_tokens"),
                        "supported_reasoning_efforts": list(detail.get("supported_reasoning_efforts", [])),
                        "catalog_fallback": detail.get("catalog_fallback") is True,
                    }
                )
            result: Json = {
                "available": True,
                "models": models[:MAX_MODELS],
                "capabilities": OPENROUTER_CAPABILITIES,
                "account": self.account.status(),
            }
            if _json_size(result) > MAX_DISCOVERY_BYTES:
                result["models"] = result["models"][:32]
            return result
        except Exception as exc:
            return {
                "available": False,
                "models": [],
                "capabilities": OPENROUTER_CAPABILITIES,
                "account": self.account.status(),
                "error_code": "PROVIDER_UNAVAILABLE",
                "error": f"OpenRouter non disponibile: {str(exc)[:200]}",
            }

    async def _stream(
        self,
        body: Json,
        ctx: RunContext | None = None,
        item_id: str | None = None,
    ) -> tuple[Json, Json]:
        api_key = self.account.effective_api_key()
        if not api_key:
            raise OpenRouterAuthError(
                "Configura la tua chiave API OpenRouter nel pannello Modelli prima di usare questo provider"
            )
        ctx_model = ctx.session.get("model") if ctx and isinstance(ctx.session, dict) else None
        model_id = _model_name(str(body.get("model") or ctx_model or "openrouter/auto"))
        detail = await self._show(model_id)
        caps = detail.get("capabilities", [])
        vision = "vision" in caps
        supports_reasoning = bool(detail.get("supports_reasoning", True))
        supports_temperature = bool(detail.get("supports_temperature", True))
        supports_cache_control = bool(detail.get("supports_cache_control", False))

        raw_source = body.get("messages", [])
        has_image_attachments = bool(ctx is not None and ctx.image_inputs.get(ctx.run_id))
        if has_image_attachments and ctx is not None:
            if not vision:
                raise StreamError(f"Il modello OpenRouter selezionato ({model_id}) non supporta allegati immagine")
            source = _bind_active_prompt(raw_source, ctx)
        else:
            source = raw_source

        window_tokens = int(detail.get("context_window") or 128_000)
        max_context_chars = min(360_000, max(60_000, int(window_tokens * 1.8)))
        compacted_source = _compact_messages_for_openrouter(source, max_chars=max_context_chars)
        messages, system_text = _openrouter_messages(
            compacted_source,
            ctx.image_inputs if ctx else {},
            vision=vision,
        )
        if body.get("format"):
            system_text += (
                "\nReturn one JSON object matching this schema, with no surrounding text: "
                + json.dumps(body["format"])
            )

        model_max_out = int(detail.get("max_output_tokens") or DEFAULT_REQUEST_MAX_TOKENS)
        requested_max = body.get("max_tokens", DEFAULT_REQUEST_MAX_TOKENS)
        if not isinstance(requested_max, int) or isinstance(requested_max, bool) or requested_max <= 0:
            requested_max = DEFAULT_REQUEST_MAX_TOKENS
        max_tokens = min(requested_max, model_max_out, max(1024, window_tokens // 4))
        if ctx:
            byte_limit = ctx.settings.get("max_output_bytes")
            if type(byte_limit) is int and byte_limit > 0:
                max_tokens = min(max_tokens, max(256, byte_limit // 4))

        effort = ctx.settings.get("reasoning_effort") if ctx else body.get("reasoning_effort")
        effort_str = effort.strip().lower() if isinstance(effort, str) else ""
        think = body.get("think")

        reasoning_cfg: Json | None = None
        if supports_reasoning:
            if think is False or think in {"off", "false", "disabled"} or effort_str in {"off", "none"}:
                reasoning_cfg = {"enabled": False}
            elif effort_str in {"minimal", "low", "medium", "high", "xhigh", "max"}:
                reasoning_cfg = {"effort": effort_str}
            elif think is True:
                reasoning_cfg = {"enabled": True}

        temperature_val: float | int | None = None
        if supports_temperature and isinstance(body.get("options"), dict):
            temp = body["options"].get("temperature")
            if isinstance(temp, (int, float)) and not isinstance(temp, bool):
                if not (reasoning_cfg and reasoning_cfg.get("enabled") is not False and model_id.startswith(("anthropic/", "openai/"))):
                    temperature_val = temp

        tools = body.get("tools", [])
        tool_defs: list[Json] = []
        if isinstance(tools, list) and tools and "tools" in caps:
            tool_defs = [
                {
                    "type": "function",
                    "function": {
                        "name": item["function"]["name"],
                        "description": item["function"].get("description", ""),
                        "parameters": item["function"].get("parameters", {"type": "object"}),
                    },
                }
                for item in tools
                if isinstance(item, dict) and isinstance(item.get("function"), dict)
            ]

        session_id = str(ctx.session.get("id") or "")[:256] if ctx and isinstance(ctx.session, dict) else ""
        include_cache_control = supports_cache_control
        include_reasoning = reasoning_cfg is not None
        include_temperature = temperature_val is not None
        current_model_id = model_id
        free_fallback_queue: list[str] = (
            [
                candidate
                for candidate in FREE_MODEL_FALLBACK_IDS
                if candidate != current_model_id
                and (not has_image_attachments or candidate in {"openrouter/free", "nvidia/nemotron-3.5-super-vl:free"})
            ]
            if _is_free_openrouter_model(current_model_id)
            else []
        )

        def build_request(cur_model: str, cur_messages: list[Json], cur_max_tokens: int) -> Json:
            req_msgs: list[Json] = []
            if system_text:
                req_msgs.append({"role": "system", "content": system_text})
            req_msgs.extend(cur_messages)
            req: Json = {
                "model": cur_model,
                "messages": req_msgs,
                "max_tokens": cur_max_tokens,
                "stream": True,
            }
            if session_id:
                req["session_id"] = session_id
            if include_cache_control:
                req["cache_control"] = {"type": "ephemeral"}
            if tool_defs:
                req["tools"] = tool_defs
            if include_reasoning and reasoning_cfg is not None:
                req["reasoning"] = reasoning_cfg
            if include_temperature and temperature_val is not None:
                req["temperature"] = temperature_val
            return req

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": OPENROUTER_REFERER,
            "X-Title": OPENROUTER_TITLE,
            "X-OpenRouter-Title": OPENROUTER_TITLE,
            "User-Agent": f"Kira-Desktop/{__version__}",
        }
        if session_id:
            headers["x-session-id"] = session_id

        summary_item_id = f"{item_id}:summary" if item_id else None
        summary_status_emitted = False

        rate_limit_retries = 0
        credit_cap_retried = False
        overflow_retried = False
        param_fallback_retried = False
        text = ""
        thought_text = ""
        reasoning_details: list[Json] = []
        fragmented_calls: dict[int, Json] = {}
        input_tokens: int | None = None
        cached_input_tokens: int | None = None
        output_tokens: int | None = None
        reasoning_output_tokens: int | None = None
        total_tokens: int | None = None
        finish_reason = ""

        while True:
            request_payload = build_request(current_model_id, messages, max_tokens)
            text = ""
            thought_text = ""
            reasoning_details = []
            fragmented_calls = {}
            input_tokens = None
            cached_input_tokens = None
            output_tokens = None
            reasoning_output_tokens = None
            total_tokens = None
            finish_reason = ""
            total_bytes = 0
            received_meaningful_chunk = False
            limit = int(ctx.settings.get("max_output_bytes", 4 * 1024 * 1024)) if ctx else 65_536
            if self._first_token_timeout is not None:
                first_token_deadline = self._first_token_timeout
            elif _is_free_openrouter_model(current_model_id):
                first_token_deadline = FREE_FIRST_TOKEN_TIMEOUT
            else:
                first_token_deadline = DEFAULT_FIRST_TOKEN_TIMEOUT

            try:
                async with self.client.stream(
                    "POST",
                    f"{OPENROUTER_API_BASE}/chat/completions",
                    json=request_payload,
                    headers=headers,
                    timeout=httpx.Timeout(300.0, connect=20.0, read=max(30.0, first_token_deadline + 15.0)),
                ) as response:
                    if response.status_code >= 400:
                        error_body = bytearray()
                        async for chunk in response.aiter_bytes():
                            error_body.extend(chunk[: max(0, 65_537 - len(error_body))])
                            if len(error_body) > 65_536:
                                break
                        body_bytes = bytes(error_body[:65_536])
                        safe_detail = _safe_error_detail(body_bytes, api_key)

                        if response.status_code == 402 and not credit_cap_retried:
                            affordable = _extract_affordable_tokens(safe_detail)
                            if affordable is not None and affordable >= 256:
                                credit_cap_retried = True
                                max_tokens = max(256, min(max_tokens - 1, int(affordable * 0.9)))
                                if ctx is not None:
                                    await ctx.emit(
                                        "backend.notice",
                                        {
                                            "message": (
                                                f"Adatto automaticamente max_tokens a {max_tokens} in base al credito "
                                                f"residuo della chiave OpenRouter su {current_model_id}."
                                            ),
                                            "model": current_model_id,
                                        },
                                    )
                                continue
                            if "max_tokens" in safe_detail.casefold() and max_tokens > 2048:
                                credit_cap_retried = True
                                max_tokens = max(1024, max_tokens // 4)
                                if ctx is not None:
                                    await ctx.emit(
                                        "backend.notice",
                                        {
                                            "message": (
                                                f"Riduco automaticamente max_tokens a {max_tokens} per rispettare "
                                                f"il credito disponibile su OpenRouter ({current_model_id})."
                                            ),
                                            "model": current_model_id,
                                        },
                                    )
                                continue

                        if response.status_code in {400, 413} and _confirmed_context_overflow(body_bytes):
                            if not overflow_retried:
                                overflow_retried = True
                                compacted_source = _compact_messages_for_openrouter(
                                    source, max_chars=max(24_000, max_context_chars // 2)
                                )
                                messages, _ = _openrouter_messages(
                                    compacted_source,
                                    ctx.image_inputs if ctx else {},
                                    vision=vision,
                                )
                                max_tokens = min(max_tokens, 4096)
                                continue
                            raise OpenRouterContextOverflow("OpenRouter rejected the request for context capacity")

                        if response.status_code == 400 and not param_fallback_retried and (
                            include_cache_control or include_reasoning or include_temperature
                        ):
                            param_fallback_retried = True
                            include_cache_control = False
                            include_temperature = False
                            continue

                        if response.status_code in {404, 408, 429, 500, 502, 503, 504} and free_fallback_queue:
                            prev_model = current_model_id
                            current_model_id = free_fallback_queue.pop(0)
                            if ctx is not None:
                                await ctx.emit(
                                    "backend.notice",
                                    {
                                        "message": (
                                            f"Il modello gratuito {prev_model} su OpenRouter è temporaneamente "
                                            f"saturo o non disponibile (HTTP {response.status_code}); passo "
                                            f"automaticamente a {current_model_id}."
                                        ),
                                        "model": current_model_id,
                                    },
                                )
                            continue

                        if response.status_code in {429, 500, 502, 503, 504} and rate_limit_retries < len(
                            self._backoff_delays
                        ):
                            delay = self._backoff_delays[rate_limit_retries]
                            rate_limit_retries += 1
                            if await _cancelable_sleep(delay, ctx):
                                continue

                        raise OpenRouterHTTPError(response, safe_detail)

                    response.raise_for_status()
                    stream_started_at = time.monotonic()
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if ctx is not None and ctx.cancel.is_set():
                            raise asyncio.CancelledError
                        if len(line.encode("utf-8")) > 1_048_576:
                            raise StreamError("OpenRouter stream record exceeds size limit")
                        if line.startswith(":"):
                            # OpenRouter heartbeat comment (e.g. ": OPENROUTER PROCESSING").
                            # Enforce a strict first-token deadline so congested :free upstreams
                            # cannot freeze a turn indefinitely by resetting socket read timeouts.
                            if (
                                not received_meaningful_chunk
                                and (time.monotonic() - stream_started_at) >= first_token_deadline
                            ):
                                raise _OpenRouterUpstreamStallError(
                                    f"Timeout in attesa del primo token da {current_model_id} "
                                    "(keep-alive : OPENROUTER PROCESSING)"
                                )
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line and data_lines:
                            raw = "\n".join(data_lines)
                            data_lines.clear()
                            if raw.strip() == "[DONE]":
                                break
                            total_bytes += len(raw.encode("utf-8"))
                            if total_bytes > limit * 2:
                                raise StreamError("OpenRouter response exceeded configured output limit")
                            try:
                                record = json.loads(raw)
                            except ValueError as exc:
                                raise StreamError("OpenRouter emitted invalid JSON chunk") from exc
                            if not isinstance(record, dict):
                                continue
                            if isinstance(record.get("error"), dict):
                                err_obj = record["error"]
                                err_msg = str(err_obj.get("message") or "OpenRouter stream error")
                                if api_key:
                                    err_msg = err_msg.replace(api_key, "[redacted]")
                                if not received_meaningful_chunk and (
                                    free_fallback_queue
                                    or any(
                                        tok in err_msg.casefold()
                                        for tok in (
                                            "timeout",
                                            "rate-limit",
                                            "rate limit",
                                            "provider returned error",
                                            "overloaded",
                                        )
                                    )
                                ):
                                    raise _OpenRouterUpstreamStallError(f"OpenRouter stream error: {err_msg[:360]}")
                                raise StreamError(f"OpenRouter stream error: {err_msg[:360]}")

                            usage_obj = record.get("usage")
                            if isinstance(usage_obj, dict):
                                if isinstance(usage_obj.get("prompt_tokens"), int):
                                    input_tokens = usage_obj["prompt_tokens"]
                                if isinstance(usage_obj.get("completion_tokens"), int):
                                    output_tokens = usage_obj["completion_tokens"]
                                if isinstance(usage_obj.get("total_tokens"), int):
                                    total_tokens = usage_obj["total_tokens"]
                                p_details = usage_obj.get("prompt_tokens_details")
                                if isinstance(p_details, dict) and isinstance(p_details.get("cached_tokens"), int):
                                    cached_input_tokens = p_details["cached_tokens"]
                                c_details = usage_obj.get("completion_tokens_details")
                                if isinstance(c_details, dict) and isinstance(c_details.get("reasoning_tokens"), int):
                                    reasoning_output_tokens = c_details["reasoning_tokens"]

                            choices = record.get("choices")
                            if not isinstance(choices, list) or not choices:
                                continue
                            choice = choices[0]
                            if not isinstance(choice, dict):
                                continue
                            if isinstance(choice.get("finish_reason"), str) and choice["finish_reason"]:
                                finish_reason = choice["finish_reason"]
                            delta = choice.get("delta")
                            if not isinstance(delta, dict):
                                continue

                            delta_reasoning_text = ""
                            if isinstance(delta.get("reasoning"), str) and delta["reasoning"]:
                                delta_reasoning_text = delta["reasoning"]
                            elif isinstance(delta.get("reasoning_content"), str) and delta["reasoning_content"]:
                                delta_reasoning_text = delta["reasoning_content"]

                            raw_rdetails = delta.get("reasoning_details")
                            if isinstance(raw_rdetails, list):
                                for ritem in raw_rdetails:
                                    if not isinstance(ritem, dict):
                                        continue
                                    _merge_reasoning_detail(reasoning_details, ritem)
                                    if not delta_reasoning_text:
                                        if ritem.get("type") == "reasoning.text" and isinstance(
                                            ritem.get("text"), str
                                        ):
                                            delta_reasoning_text += ritem["text"]
                                        elif ritem.get("type") == "reasoning.summary" and isinstance(
                                            ritem.get("summary"), str
                                        ):
                                            delta_reasoning_text += ritem["summary"]

                            if delta_reasoning_text:
                                received_meaningful_chunk = True
                                thought_text += delta_reasoning_text
                                if ctx and summary_item_id:
                                    if not summary_status_emitted:
                                        summary_status_emitted = True
                                        await ctx.emit(
                                            "reasoning.summary.status",
                                            {
                                                "source": "provider_summary",
                                                "provider": BACKEND,
                                                "status": "requested",
                                            },
                                        )
                                    await ctx.emit(
                                        "reasoning.summary.delta",
                                        {
                                            "source": "provider_summary",
                                            "provider": BACKEND,
                                            "item_id": summary_item_id,
                                            "content": delta_reasoning_text,
                                        },
                                    )

                            content_delta = delta.get("content")
                            if isinstance(content_delta, str) and content_delta:
                                received_meaningful_chunk = True
                                text += content_delta
                                if ctx:
                                    await ctx.emit("message.delta", {"content": content_delta, "item_id": item_id})

                            tc_deltas = delta.get("tool_calls")
                            if isinstance(tc_deltas, list) and tc_deltas:
                                received_meaningful_chunk = True
                                for tc in tc_deltas:
                                    if not isinstance(tc, dict):
                                        continue
                                    tc_idx = tc.get("index", 0)
                                    if not isinstance(tc_idx, int):
                                        tc_idx = 0
                                    entry = fragmented_calls.setdefault(
                                        tc_idx,
                                        {
                                            "id": f"openrouter-{tc_idx}",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""},
                                        },
                                    )
                                    if isinstance(tc.get("id"), str) and tc["id"]:
                                        entry["id"] = tc["id"]
                                    fn_delta = tc.get("function")
                                    if isinstance(fn_delta, dict):
                                        if isinstance(fn_delta.get("name"), str) and fn_delta["name"]:
                                            entry["function"]["name"] += fn_delta["name"]
                                        if isinstance(fn_delta.get("arguments"), str) and fn_delta["arguments"]:
                                            entry["function"]["arguments"] += fn_delta["arguments"]
                    break
            except (_OpenRouterUpstreamStallError, httpx.TimeoutException, httpx.TransportError) as stall_exc:
                if not received_meaningful_chunk:
                    if free_fallback_queue:
                        prev_model = current_model_id
                        current_model_id = free_fallback_queue.pop(0)
                        if ctx is not None:
                            await ctx.emit(
                                "backend.notice",
                                {
                                    "message": (
                                        f"Il modello gratuito {prev_model} su OpenRouter è in coda senza risposta; "
                                        f"passo automaticamente a {current_model_id}."
                                    ),
                                    "model": current_model_id,
                                },
                            )
                        continue
                    if rate_limit_retries < len(self._backoff_delays):
                        delay = self._backoff_delays[rate_limit_retries]
                        rate_limit_retries += 1
                        if await _cancelable_sleep(delay, ctx):
                            continue
                raise StreamError(
                    f"Il modello {current_model_id} su OpenRouter non ha inviato alcun token entro "
                    f"{int(max(1.0, first_token_deadline))}s ({stall_exc})."
                ) from stall_exc

        if ctx and summary_item_id and thought_text:
            await ctx.emit(
                "reasoning.summary.completed",
                {
                    "source": "provider_summary",
                    "provider": BACKEND,
                    "item_id": summary_item_id,
                    "content": thought_text,
                },
            )

        tool_calls: list[Json] = []
        incomplete_tool_input = False
        for tc_idx in sorted(fragmented_calls):
            raw_call = fragmented_calls[tc_idx]
            fn_obj = raw_call["function"]
            fn_name = fn_obj.get("name", "")
            raw_args = fn_obj.get("arguments", "")
            if not fn_name:
                continue
            if finish_reason == "length":
                incomplete_tool_input = True
                continue
            if not raw_args.strip():
                parsed_args: Any = {}
            else:
                try:
                    parsed_args = json.loads(raw_args)
                except ValueError:
                    incomplete_tool_input = True
                    continue
            if not isinstance(parsed_args, dict):
                incomplete_tool_input = True
                continue
            tool_calls.append(
                {
                    "id": raw_call["id"],
                    "type": "function",
                    "function": {"name": fn_name, "arguments": parsed_args},
                }
            )
        if incomplete_tool_input:
            tool_calls.clear()

        if not text.strip() and not tool_calls and thought_text.strip():
            text = thought_text.strip()
            if ctx:
                await ctx.emit("message.delta", {"content": text, "item_id": item_id})

        message: Json = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        if reasoning_details:
            message["reasoning_details"] = reasoning_details

        done_reason = (
            "tool_use"
            if tool_calls
            else "tool_input_incomplete"
            if incomplete_tool_input
            else "length"
            if finish_reason == "length"
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
        return message, terminal
