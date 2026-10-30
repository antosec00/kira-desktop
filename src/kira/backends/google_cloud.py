"""Google Cloud / Vertex AI & Gemini provider backend and account management."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import configparser
import contextlib
import functools
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import httpx

from kira import __version__
from kira.backends.deepseek_account import (
    MAX_IMAGE_BASE64_CHARS,
    PNG_DATA_URL,
    _anthropic_messages,
    _bind_active_prompt,
)
from kira.backends.ollama import MAX_MODELS, OllamaBackend, StreamError
from kira.domain.contracts import KiraError, RunContext

Json = dict[str, Any]

BACKEND = "google_cloud"
DISCOVERY_TIMEOUT = 25.0
MAX_DISCOVERY_BYTES = 262_144
MAX_GOOGLE_OUTPUT_TOKENS = 65_536
MAX_VERTEX_CLAUDE_OUTPUT_TOKENS = 32_768
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_CRM_PROJECTS_URL = "https://cloudresourcemanager.googleapis.com/v1/projects"
GCLOUD_ADC_CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
GCLOUD_ADC_CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"
GOOGLE_OAUTH_SCOPES = (
    "openid https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/cloud-platform"
)
OAUTH_LOGIN_TIMEOUT = 300.0
SYSTEM_ACCOUNT_ID = "system"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com"

GOOGLE_CAPABILITIES: Json = {
    "streaming": True,
    "tools": True,
    "cancellation": True,
    "resume": False,
    "usage": True,
    "images": True,
    "context_controls": False,
}

SUPPORTED_LOCATIONS: tuple[str, ...] = (
    "global",
    "us-central1",
    "us-east5",
    "europe-west1",
    "europe-west4",
    "asia-northeast1",
)

BUILTIN_MODEL_CATALOG: tuple[Json, ...] = (
    {
        "id": "gemini-4-argon",
        "name": "Google Gemini 4 Argon",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high", "max"],
            "default_level": "high",
        },
    },
    {
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro Preview",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high", "max"],
            "default_level": "high",
        },
    },
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "medium",
        },
    },
    {
        "id": "gemini-3.5-flash",
        "name": "Gemini 3.5 Flash",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "medium",
        },
    },
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash-Lite",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "low",
        },
    },
    {
        "id": "gemini-3-flash-preview",
        "name": "Gemini 3 Flash Preview",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "medium",
        },
    },
    {
        "id": "argon",
        "name": "Google Argon",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high", "max"],
            "default_level": "high",
        },
    },
    {
        "id": "gemini-3-pro-preview",
        "name": "Gemini 3 Pro Preview",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high", "max"],
            "default_level": "high",
        },
    },
    {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high", "max"],
            "default_level": "medium",
        },
    },
    {
        "id": "gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "medium",
        },
    },
    {
        "id": "gemini-2.5-flash-lite",
        "name": "Gemini 2.5 Flash-Lite",
        "family": "gemini",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "low",
        },
    },
    {
        "id": "claude-opus-5-5",
        "name": "Claude Opus 5.5 · Vertex AI",
        "family": "anthropic_vertex",
        "context_window": 200_000,
        "max_output_tokens": 32_768,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high", "max"],
            "default_level": "high",
        },
    },
    {
        "id": "claude-sonnet-5-5",
        "name": "Claude Sonnet 5.5 · Vertex AI",
        "family": "anthropic_vertex",
        "context_window": 200_000,
        "max_output_tokens": 32_768,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "medium",
        },
    },
    {
        "id": "claude-opus-4-6",
        "name": "Claude Opus 4.6 · Vertex AI",
        "family": "anthropic_vertex",
        "context_window": 200_000,
        "max_output_tokens": 32_768,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high", "max"],
            "default_level": "high",
        },
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6 · Vertex AI",
        "family": "anthropic_vertex",
        "context_window": 200_000,
        "max_output_tokens": 32_768,
        "input_modalities": ["text", "image"],
        "effort": {
            "supported_levels": ["off", "low", "medium", "high"],
            "default_level": "medium",
        },
    },
)

VERTEX_MODEL_FALLBACKS: dict[str, str] = {
    "argon": "gemini-4-argon",
    "gemini-3-pro-preview": "gemini-3.1-pro-preview",
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
    "gemini-2.5-flash": "gemini-3.8-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
}


class GoogleCloudAuthError(KiraError):
    def __init__(self, message: str) -> None:
        super().__init__("AUTH_REQUIRED", message)


class GoogleCloudHTTPError(httpx.HTTPStatusError):
    def __init__(self, response: httpx.Response, safe_detail: str = "") -> None:
        self.safe_detail = safe_detail
        super().__init__(
            f"Google Cloud HTTP {response.status_code}" + (f": {safe_detail}" if safe_detail else ""),
            request=response.request,
            response=response,
        )


def _safe_error_detail(body: bytes, secret: str = "") -> str:
    try:
        parsed = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        text = body.decode("utf-8", "replace").strip()
    else:
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                text = str(err.get("message") or err.get("status") or "")
            elif isinstance(err, list) and err and isinstance(err[0], dict):
                e0 = err[0].get("error", err[0])
                text = str(e0.get("message") if isinstance(e0, dict) else e0)
            else:
                text = str(parsed.get("message") or err or "")
        elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            err = parsed[0].get("error", {})
            text = str(err.get("message") if isinstance(err, dict) else "")
        else:
            text = ""
    if secret and len(secret) >= 6:
        text = text.replace(secret, "[REDACTED]")
    return " ".join(text.split())[:260]


def _gcloud_config_dir() -> Path:
    custom = os.environ.get("CLOUDSDK_CONFIG", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".config" / "gcloud"


def _adc_file_path() -> Path:
    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return _gcloud_config_dir() / "application_default_credentials.json"


def _detect_gcloud_project() -> str:
    for env_key in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT", "VERTEX_PROJECT"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    adc_path = _adc_file_path()
    with contextlib.suppress(OSError, ValueError):
        if adc_path.is_file():
            data = json.loads(adc_path.read_text("utf-8"))
            if isinstance(data, dict):
                for key in ("quota_project_id", "project_id"):
                    val = str(data.get(key) or "").strip()
                    if val:
                        return val
    active_name = "default"
    active_file = _gcloud_config_dir() / "active_config"
    with contextlib.suppress(OSError):
        if active_file.is_file():
            candidate = active_file.read_text("utf-8").strip()
            if candidate and "/" not in candidate and "\\" not in candidate:
                active_name = candidate
    cfg_path = _gcloud_config_dir() / "configurations" / f"config_{active_name}"
    with contextlib.suppress(OSError, configparser.Error):
        if cfg_path.is_file():
            parser = configparser.ConfigParser()
            parser.read(cfg_path, encoding="utf-8")
            if parser.has_option("core", "project"):
                val = parser.get("core", "project").strip()
                if val:
                    return val
    return ""


def _detect_gcloud_account() -> str:
    active_name = "default"
    active_file = _gcloud_config_dir() / "active_config"
    with contextlib.suppress(OSError):
        if active_file.is_file():
            candidate = active_file.read_text("utf-8").strip()
            if candidate and "/" not in candidate and "\\" not in candidate:
                active_name = candidate
    cfg_path = _gcloud_config_dir() / "configurations" / f"config_{active_name}"
    with contextlib.suppress(OSError, configparser.Error):
        if cfg_path.is_file():
            parser = configparser.ConfigParser()
            parser.read(cfg_path, encoding="utf-8")
            if parser.has_option("core", "account"):
                val = parser.get("core", "account").strip()
                if val:
                    return val
    return ""


def _has_gcloud_credentials() -> bool:
    adc = _adc_file_path()
    if adc.is_file():
        return True
    creds_db = _gcloud_config_dir() / "credentials.db"
    return creds_db.is_file()


def _extract_email_from_id_token(id_token: str) -> str:
    parts = id_token.split(".")
    if len(parts) < 2:
        return ""
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    with contextlib.suppress(ValueError, KeyError, TypeError):
        raw = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("email"), str):
            return data["email"].strip()
    return ""


class GoogleCloudAccount:
    """Manage isolated Google Cloud accounts, read-only WSL2 gcloud ADC, and API key settings."""

    def __init__(
        self,
        root: Path,
        *,
        protect_secrets: Callable[[tuple[str, ...]], None] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.root = root
        self.path = root / "account.json"
        self.protect_secrets = protect_secrets or (lambda _values: None)
        self.client = client or httpx.AsyncClient(timeout=30.0)
        self.auth_mode = "adc"  # "adc" | "api_key"
        self.project_id = ""
        self.location = "global"
        self.api_key = ""
        self.custom_models: list[str] = []
        self.active_account_id = ""
        self.accounts: dict[str, Json] = {}
        self._cached_token = ""
        self._cached_token_expiry = 0.0
        self.pending: asyncio.subprocess.Process | None = None
        self._listener: asyncio.Server | None = None
        self._pending_oauth: Json | None = None
        self._oauth_handlers: set[asyncio.Task[Any]] = set()
        self.login_task: asyncio.Task[None] | None = None
        self.state: Json = {"phase": "idle", "connected": False}
        self._load()
        self._refresh_state()

    def _sync_redaction(self) -> None:
        values: list[str] = [self.api_key, self._cached_token]
        for acc in self.accounts.values():
            if isinstance(acc, dict):
                for key in ("refresh_token", "access_token", "client_secret"):
                    val = acc.get(key)
                    if isinstance(val, str):
                        values.append(val)
        secrets_tuple = tuple(s for s in values if isinstance(s, str) and len(s) >= 8)
        self.protect_secrets(secrets_tuple)

    def _load(self) -> None:
        if not self.path.exists() and not self.path.is_symlink():
            return
        if self.root.exists():
            info_root = self.root.lstat()
            if stat.S_ISLNK(info_root.st_mode) or info_root.st_uid != os.getuid():
                raise KiraError("UNSAFE_PATH", "Google Cloud config directory is unsafe")
        info = self.path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise KiraError("UNSAFE_PATH", "Google Cloud account file is unsafe")
        raw = json.loads(self.path.read_text("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Invalid Google Cloud account store")
        mode = str(raw.get("auth_mode") or "adc").strip()
        self.auth_mode = mode if mode in {"adc", "api_key"} else "adc"
        self.project_id = str(raw.get("project_id") or "").strip()[:128]
        loc = str(raw.get("location") or "global").strip()[:64]
        self.location = loc or "global"
        self.api_key = str(raw.get("api_key") or "").strip()[:512]
        self.active_account_id = str(raw.get("active_account_id") or "").strip()[:128]
        raw_accounts = raw.get("accounts", {})
        loaded_accounts: dict[str, Json] = {}
        if isinstance(raw_accounts, dict):
            for acc_id, row in list(raw_accounts.items())[:32]:
                if (
                    isinstance(acc_id, str)
                    and acc_id.strip()
                    and acc_id != SYSTEM_ACCOUNT_ID
                    and isinstance(row, dict)
                ):
                    clean_id = acc_id.strip()[:128]
                    exp_raw = row.get("expires_at")
                    exp_val = float(exp_raw) if isinstance(exp_raw, (int, float)) else 0.0
                    loaded_accounts[clean_id] = {
                        "id": clean_id,
                        "email": str(row.get("email") or "").strip()[:256],
                        "client_id": str(row.get("client_id") or GCLOUD_ADC_CLIENT_ID).strip()[:256],
                        "client_secret": str(row.get("client_secret") or GCLOUD_ADC_CLIENT_SECRET).strip()[:256],
                        "refresh_token": str(row.get("refresh_token") or "").strip()[:2048],
                        "access_token": str(row.get("access_token") or "").strip()[:4096],
                        "expires_at": exp_val,
                        "project_id": str(row.get("project_id") or "").strip()[:128],
                        "detected_project_id": str(row.get("detected_project_id") or "").strip()[:128],
                    }
        self.accounts = loaded_accounts
        models = raw.get("custom_models", [])
        if isinstance(models, list):
            self.custom_models = [
                str(m).strip()[:160]
                for m in models
                if isinstance(m, str) and str(m).strip()
            ][:32]
        self._sync_redaction()

    def _save(self) -> None:
        if self.root.is_symlink():
            raise KiraError("UNSAFE_PATH", "Google Cloud config directory must not be a symlink")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        data = json.dumps(
            {
                "version": 1,
                "auth_mode": self.auth_mode,
                "project_id": self.project_id,
                "location": self.location,
                "api_key": self.api_key,
                "active_account_id": self.active_account_id,
                "accounts": self.accounts,
                "custom_models": self.custom_models,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        temp = self.root / f".account.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()
        self._sync_redaction()

    @property
    def gcloud_available(self) -> bool:
        return shutil.which("gcloud") is not None

    @property
    def system_adc_available(self) -> bool:
        return _has_gcloud_credentials()

    @property
    def effective_account_id(self) -> str:
        if self.active_account_id and self.active_account_id in self.accounts:
            return self.active_account_id
        if self.active_account_id == SYSTEM_ACCOUNT_ID and self.system_adc_available:
            return SYSTEM_ACCOUNT_ID
        if self.accounts:
            return next(iter(self.accounts))
        if self.system_adc_available:
            return SYSTEM_ACCOUNT_ID
        return ""

    @property
    def active_isolated_account(self) -> Json | None:
        eff = self.effective_account_id
        if eff and eff != SYSTEM_ACCOUNT_ID:
            return self.accounts.get(eff)
        return None

    @property
    def adc_available(self) -> bool:
        return bool(self.accounts) or self.system_adc_available or bool(self._cached_token)

    @property
    def configured_project_id_for_active(self) -> str:
        iso = self.active_isolated_account
        if iso is not None and self.auth_mode == "adc":
            return str(iso.get("project_id") or "")
        return self.project_id

    @property
    def detected_project_id(self) -> str:
        iso = self.active_isolated_account
        if iso is not None and self.auth_mode == "adc":
            iso_det = str(iso.get("detected_project_id") or "").strip()
            if iso_det:
                return iso_det
        return _detect_gcloud_project()

    @property
    def account_email(self) -> str:
        iso = self.active_isolated_account
        if iso is not None:
            return str(iso.get("email") or "").strip()
        return _detect_gcloud_account()

    def effective_project_id(self) -> str:
        iso = self.active_isolated_account
        if iso is not None and self.auth_mode == "adc":
            return (
                str(iso.get("project_id") or "").strip()
                or str(iso.get("detected_project_id") or "").strip()
                or self.project_id
                or _detect_gcloud_project()
            )
        return self.project_id or _detect_gcloud_project()

    @property
    def connected(self) -> bool:
        if self.auth_mode == "api_key":
            return bool(self.api_key)
        return self.adc_available

    def accounts_summary(self) -> list[Json]:
        items: list[Json] = []
        if self.system_adc_available:
            sys_email = _detect_gcloud_account()
            sys_proj = self.project_id or _detect_gcloud_project()
            label = f"{sys_email} · WSL2 / gcloud" if sys_email else "gcloud ADC · Sistema WSL2"
            items.append(
                {
                    "id": SYSTEM_ACCOUNT_ID,
                    "email": sys_email,
                    "label": label,
                    "project_id": sys_proj,
                    "source": "system",
                    "read_only": True,
                    "connected": True,
                }
            )
        for acc_id, row in self.accounts.items():
            if not isinstance(row, dict):
                continue
            email = str(row.get("email") or "").strip()
            proj = str(row.get("project_id") or row.get("detected_project_id") or self.project_id or "").strip()
            label = f"{email} · Isolato in Kira" if email else f"Account Kira ({acc_id[:8]})"
            items.append(
                {
                    "id": acc_id,
                    "email": email,
                    "label": label,
                    "project_id": proj,
                    "source": "kira",
                    "read_only": False,
                    "connected": bool(row.get("refresh_token") or row.get("access_token")),
                }
            )
        return items

    def _refresh_state(self) -> None:
        if self.pending is not None or self._pending_oauth is not None:
            self.state = {
                **self.state,
                "phase": "authorizing",
                "connected": self.connected,
            }
        elif self.state.get("phase") in {"failed", "expired", "cancelled"}:
            self.state = {
                **self.state,
                "connected": self.connected,
            }
        else:
            self.state = {
                "phase": "signed_in" if self.connected else "idle",
                "connected": self.connected,
            }

    def status(self) -> Json:
        self._refresh_state()
        eff_id = self.effective_account_id
        return {
            "connected": self.connected,
            "auth_mode": self.auth_mode,
            "project_id": self.effective_project_id(),
            "configured_project_id": self.configured_project_id_for_active,
            "detected_project_id": self.detected_project_id,
            "location": self.location,
            "api_key_configured": bool(self.api_key),
            "adc_available": self.adc_available,
            "gcloud_available": self.gcloud_available,
            "account_email": self.account_email,
            "active_account_id": eff_id,
            "active_account_is_system": eff_id == SYSTEM_ACCOUNT_ID,
            "accounts": self.accounts_summary(),
            "custom_models": list(self.custom_models),
            "supported_locations": list(SUPPORTED_LOCATIONS),
            "login": dict(self.state),
        }

    def select(self, account_id: Any) -> Json:
        if not isinstance(account_id, str) or not account_id.strip():
            raise KiraError("INVALID_ARGUMENT", "Invalid Google Cloud account_id")
        target = account_id.strip()
        if target == SYSTEM_ACCOUNT_ID:
            if not self.system_adc_available:
                raise KiraError("NOT_FOUND", "L'account gcloud di sistema non è disponibile")
        elif target not in self.accounts:
            raise KiraError("NOT_FOUND", "Account Google Cloud isolato non trovato in Kira")
        self.active_account_id = target
        self.auth_mode = "adc"
        self._cached_token = ""
        self._cached_token_expiry = 0.0
        self.state = {"phase": "signed_in" if self.connected else "idle", "connected": self.connected}
        self._save()
        return self.status()

    def configure(
        self,
        *,
        auth_mode: Any = None,
        project_id: Any = None,
        location: Any = None,
        api_key: Any = None,
        custom_models: Any = None,
    ) -> Json:
        if auth_mode is not None:
            if not isinstance(auth_mode, str) or auth_mode not in {"adc", "api_key"}:
                raise KiraError("INVALID_ARGUMENT", "Google Cloud auth_mode must be 'adc' or 'api_key'")
            self.auth_mode = auth_mode
        if project_id is not None:
            if not isinstance(project_id, str) or len(project_id.strip()) > 128:
                raise KiraError("INVALID_ARGUMENT", "Invalid Google Cloud project_id")
            cleaned_proj = project_id.strip()
            iso = self.active_isolated_account
            if iso is not None and self.auth_mode == "adc":
                iso["project_id"] = cleaned_proj
            else:
                self.project_id = cleaned_proj
        if location is not None:
            if not isinstance(location, str) or not location.strip() or len(location.strip()) > 64:
                raise KiraError("INVALID_ARGUMENT", "Invalid Google Cloud location")
            self.location = location.strip()
        if api_key is not None:
            if not isinstance(api_key, str) or len(api_key.strip()) > 512:
                raise KiraError("INVALID_ARGUMENT", "Invalid Google Cloud API key")
            cleaned_key = api_key.strip()
            if cleaned_key:
                self.api_key = cleaned_key
        if custom_models is not None:
            if isinstance(custom_models, str):
                items = [part.strip() for part in custom_models.replace("\n", ",").split(",") if part.strip()]
            elif isinstance(custom_models, list):
                items = [str(part).strip() for part in custom_models if isinstance(part, str) and part.strip()]
            else:
                raise KiraError("INVALID_ARGUMENT", "custom_models must be a list or comma-separated string")
            deduped: list[str] = []
            for item in items[:32]:
                if len(item) > 160:
                    raise KiraError("INVALID_ARGUMENT", "Custom model ID is too long")
                if item not in deduped:
                    deduped.append(item)
            self.custom_models = deduped
        self.state = {"phase": "signed_in" if self.connected else "idle", "connected": self.connected}
        self._save()
        return self.status()

    async def resolve_token(self, *, force_refresh: bool = False) -> str:
        """Resolve an OAuth2 Bearer token from the active isolated Kira account or system ADC/gcloud."""
        iso = self.active_isolated_account
        if iso is not None:
            cached = str(iso.get("access_token") or "").strip()
            expires_at = float(iso.get("expires_at") or 0.0)
            if not force_refresh and cached and time.time() < expires_at - 60:
                self._sync_redaction()
                return cached
            client_id = str(iso.get("client_id") or GCLOUD_ADC_CLIENT_ID).strip()
            client_secret = str(iso.get("client_secret") or GCLOUD_ADC_CLIENT_SECRET).strip()
            refresh_token = str(iso.get("refresh_token") or "").strip()
            if refresh_token:
                self.protect_secrets((refresh_token, client_secret))
                with contextlib.suppress(OSError, ValueError, KeyError, httpx.HTTPError):
                    response = await self.client.post(
                        OAUTH_TOKEN_URL,
                        data={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "refresh_token": refresh_token,
                            "grant_type": "refresh_token",
                        },
                        timeout=15.0,
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        token = str(payload.get("access_token") or "").strip()
                        expires_in = int(payload.get("expires_in") or 3600)
                        if token:
                            iso["access_token"] = token
                            iso["expires_at"] = time.time() + max(120, expires_in)
                            self._save()
                            return token
            if cached and time.time() < expires_at:
                return cached
            email_label = str(iso.get("email") or iso.get("id") or "isolato")
            raise GoogleCloudAuthError(
                f"Impossibile rinnovare il token per l'account Google isolato ({email_label}). "
                "Ripeti l'accesso Google in Kira oppure cambia account."
            )

        if not force_refresh and self._cached_token and time.time() < self._cached_token_expiry - 60:
            return self._cached_token
        adc_path = _adc_file_path()
        if adc_path.is_file():
            with contextlib.suppress(OSError, ValueError, KeyError, httpx.HTTPError):
                raw = json.loads(adc_path.read_text("utf-8"))
                if (
                    isinstance(raw, dict)
                    and raw.get("type") == "authorized_user"
                    and isinstance(raw.get("client_id"), str)
                    and isinstance(raw.get("client_secret"), str)
                    and isinstance(raw.get("refresh_token"), str)
                ):
                    self.protect_secrets((raw["refresh_token"], raw["client_secret"]))
                    response = await self.client.post(
                        OAUTH_TOKEN_URL,
                        data={
                            "client_id": raw["client_id"],
                            "client_secret": raw["client_secret"],
                            "refresh_token": raw["refresh_token"],
                            "grant_type": "refresh_token",
                        },
                        timeout=15.0,
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        token = str(payload.get("access_token") or "").strip()
                        expires_in = int(payload.get("expires_in") or 3600)
                        if token:
                            self._cached_token = token
                            self._cached_token_expiry = time.time() + max(120, expires_in)
                            self._sync_redaction()
                            return token
        gcloud_bin = shutil.which("gcloud")
        if gcloud_bin:
            for subcmd in (
                ["auth", "application-default", "print-access-token", "--quiet"],
                ["auth", "print-access-token", "--quiet"],
            ):
                with contextlib.suppress(OSError, asyncio.TimeoutError):
                    proc = await asyncio.create_subprocess_exec(
                        gcloud_bin,
                        *subcmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=12.0)
                    if proc.returncode == 0:
                        token = stdout.decode("utf-8", "replace").strip()
                        if token and " " not in token:
                            self._cached_token = token
                            self._cached_token_expiry = time.time() + 2700.0
                            self._sync_redaction()
                            return token
        raise GoogleCloudAuthError(
            "Google Cloud credentials not found. Use 'Aggiungi / Cambia account Google' in Kira "
            "or configure a Google Cloud API key in Modelli."
        )

    async def login(self) -> Json:
        """Start an isolated loopback OAuth2 PKCE flow in Kira without modifying host ~/.config/gcloud."""
        if self._listener is not None or self.pending is not None:
            await self.cancel()
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        state_token = secrets.token_urlsafe(32)
        start_key = secrets.token_urlsafe(32)
        self._listener = await asyncio.start_server(self._handle_oauth_client, "127.0.0.1", 0, limit=16_384)
        port = self._listener.sockets[0].getsockname()[1]
        redirect_uri = f"http://localhost:{port}/"
        query = {
            "client_id": GCLOUD_ADC_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_OAUTH_SCOPES,
            "access_type": "offline",
            "prompt": "select_account consent",
            "state": state_token,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        google_auth_url = f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(query)}"
        local_start_url = f"http://127.0.0.1:{port}/auth/start/{start_key}"
        pending_oauth: Json = {
            "port": port,
            "verifier": verifier,
            "state": state_token,
            "start_key": start_key,
            "redirect_uri": redirect_uri,
            "google_auth_url": google_auth_url,
            "local_start_url": local_start_url,
        }
        self._pending_oauth = pending_oauth
        self.state = {
            "phase": "authorizing",
            "connected": self.connected,
            "authorization_url": local_start_url,
        }
        self.login_task = asyncio.create_task(self._expire_oauth(pending_oauth))
        return {**self.status(), "authorization_url": local_start_url}

    async def _expire_oauth(self, pending: Json) -> None:
        await asyncio.sleep(OAUTH_LOGIN_TIMEOUT)
        if self._pending_oauth is pending:
            self._finish_oauth(
                "expired",
                error="Tempo scaduto per l'accesso Google Cloud. Riprova da Kira Desktop.",
            )

    def _finish_oauth(self, phase: str, *, error: str = "") -> None:
        self._pending_oauth = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self.login_task is not None and self.login_task is not asyncio.current_task():
            self.login_task.cancel()
        self.login_task = None
        state: Json = {"phase": phase, "connected": self.connected}
        if error:
            state["error"] = error
        self.state = state

    @staticmethod
    async def _reply_http(
        writer: asyncio.StreamWriter,
        status: int,
        message: str,
        *,
        location: str | None = None,
    ) -> None:
        body = (
            "<!doctype html><html lang='it'><head><meta charset='utf-8'>"
            "<title>Kira Desktop · Google Cloud</title></head>"
            "<body style='font-family:system-ui,sans-serif;padding:32px;max-width:560px;margin:auto;'>"
            "<h2>Kira Desktop · Google Cloud</h2>"
            f"<p>{message}</p></body></html>"
        ).encode("utf-8")
        headers = [
            f"HTTP/1.1 {status} Response",
            "Connection: close",
            "Cache-Control: no-store",
            "Content-Type: text/html; charset=utf-8",
            f"Content-Length: {len(body)}",
        ]
        if location:
            headers.append(f"Location: {location}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + body)
        await writer.drain()

    async def _handle_oauth_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._oauth_handlers.add(task)
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
            if len(header) > 16_384:
                await self._reply_http(writer, 400, "Richiesta non valida.")
                return
            lines = header.decode("ascii", "replace").split("\r\n")
            request_line = lines[0].split(" ")
            if len(request_line) != 3:
                await self._reply_http(writer, 400, "Richiesta non valida.")
                return
            method, target, version = request_line
            pending = self._pending_oauth
            if method != "GET" or version not in {"HTTP/1.0", "HTTP/1.1"} or pending is None:
                await self._reply_http(writer, 400, "Sessione di accesso non attiva.")
                return
            parsed = urlsplit(target)
            if parsed.path == "/favicon.ico":
                await self._reply_http(writer, 404, "Not found")
                return
            if parsed.path == f"/auth/start/{pending['start_key']}":
                await self._reply_http(
                    writer,
                    302,
                    "Reindirizzamento a Google Accounts...",
                    location=str(pending["google_auth_url"]),
                )
                return
            if parsed.path not in {"/", "/auth/callback"}:
                await self._reply_http(writer, 404, "Percorso non trovato.")
                return
            values = parse_qs(parsed.query, keep_blank_values=True)
            params = {k: v[0] for k, v in values.items() if v}
            if "code" not in params and "error" not in params:
                await self._reply_http(writer, 400, "Parametri OAuth mancanti.")
                return
            if not secrets.compare_digest(params.get("state", ""), str(pending["state"])):
                await self._reply_http(writer, 400, "Stato OAuth non valido. Riprova da Kira Desktop.")
                return
            if "error" in params:
                err_code = params.get("error", "access_denied")
                self._finish_oauth(
                    "failed",
                    error=f"Accesso Google Cloud annullato o rifiutato ({err_code}).",
                )
                await self._reply_http(
                    writer,
                    200,
                    "Accesso Google Cloud non completato. Puoi chiudere questa scheda e tornare a Kira Desktop.",
                )
                return
            code = params.get("code", "").strip()
            await self._complete_oauth_code(pending, code)
            ok = self.state.get("phase") == "signed_in"
            msg = (
                "Accesso Google Cloud completato in modo isolato per Kira Desktop. "
                "Il tuo ambiente WSL2/gcloud non è stato modificato. Puoi chiudere questa scheda e tornare a Kira."
                if ok
                else f"Accesso Google Cloud non riuscito: {self.state.get('error', 'errore durante lo scambio del token')}."
            )
            await self._reply_http(writer, 200, msg)
        except (
            ValueError,
            UnicodeError,
            asyncio.TimeoutError,
            asyncio.LimitOverrunError,
            asyncio.IncompleteReadError,
            ConnectionError,
        ):
            with contextlib.suppress(ConnectionError, OSError):
                await self._reply_http(writer, 400, "Richiesta non valida.")
        finally:
            if task is not None:
                self._oauth_handlers.discard(task)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _fetch_userinfo_email(self, access_token: str) -> str:
        with contextlib.suppress(httpx.HTTPError, ValueError, KeyError, TimeoutError):
            response = await self.client.get(
                GOOGLE_OAUTH_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=8.0,
            )
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get("email"), str):
                    return payload["email"].strip()
        return ""

    async def _detect_account_project(self, access_token: str) -> str:
        with contextlib.suppress(httpx.HTTPError, ValueError, KeyError, TimeoutError):
            response = await self.client.get(
                GOOGLE_CRM_PROJECTS_URL,
                params={"filter": "lifecycleState:ACTIVE", "pageSize": "10"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=8.0,
            )
            if response.status_code == 200:
                payload = response.json()
                projects = payload.get("projects") if isinstance(payload, dict) else None
                if isinstance(projects, list):
                    for proj in projects:
                        if isinstance(proj, dict):
                            pid = str(proj.get("projectId") or "").strip()
                            if pid:
                                return pid
        return ""

    async def ensure_vertex_api_enabled(self, project_id: str = "", access_token: str = "") -> bool:
        proj = (project_id or self.effective_project_id()).strip()
        if not proj or self.auth_mode == "api_key":
            return False
        with contextlib.suppress(GoogleCloudAuthError, httpx.HTTPError, ValueError, KeyError, TimeoutError):
            tok = access_token or await self.resolve_token()
            if not tok:
                return False
            response = await self.client.post(
                f"https://serviceusage.googleapis.com/v1/projects/{proj}/services/aiplatform.googleapis.com:enable",
                json={},
                headers={"Authorization": f"Bearer {tok}", "x-goog-user-project": proj},
                timeout=15.0,
            )
            return response.status_code in {200, 201}
        return False

    async def _complete_oauth_code(self, pending: Json, code: str) -> None:
        try:
            response = await self.client.post(
                OAUTH_TOKEN_URL,
                data={
                    "client_id": GCLOUD_ADC_CLIENT_ID,
                    "client_secret": GCLOUD_ADC_CLIENT_SECRET,
                    "code": code,
                    "code_verifier": pending["verifier"],
                    "grant_type": "authorization_code",
                    "redirect_uri": pending["redirect_uri"],
                },
                timeout=20.0,
            )
            if response.status_code != 200:
                detail = _safe_error_detail(response.content)
                self._finish_oauth(
                    "failed",
                    error=detail or f"Scambio token Google OAuth fallito (HTTP {response.status_code}).",
                )
                return
            payload = response.json()
            access_token = str(payload.get("access_token") or "").strip()
            refresh_token = str(payload.get("refresh_token") or "").strip()
            id_token = str(payload.get("id_token") or "").strip()
            expires_in = int(payload.get("expires_in") or 3600)
            if not access_token:
                self._finish_oauth("failed", error="Google OAuth non ha restituito un access_token valido.")
                return
            self.protect_secrets(
                tuple(s for s in (access_token, refresh_token, GCLOUD_ADC_CLIENT_SECRET) if s and len(s) >= 8)
            )
            email = _extract_email_from_id_token(id_token) if id_token else ""
            if not email:
                email = await self._fetch_userinfo_email(access_token)
            detected_proj = await self._detect_account_project(access_token)
            if detected_proj:
                await self.ensure_vertex_api_enabled(detected_proj, access_token)
            existing_id = ""
            if email:
                for acc_id, row in self.accounts.items():
                    if isinstance(row, dict) and str(row.get("email") or "").strip().lower() == email.lower():
                        existing_id = acc_id
                        break
            acc_id = existing_id or f"kira-{uuid.uuid4().hex[:12]}"
            prev = self.accounts.get(acc_id, {}) if existing_id else {}
            self.accounts[acc_id] = {
                "id": acc_id,
                "email": email,
                "client_id": GCLOUD_ADC_CLIENT_ID,
                "client_secret": GCLOUD_ADC_CLIENT_SECRET,
                "refresh_token": refresh_token or str(prev.get("refresh_token") or ""),
                "access_token": access_token,
                "expires_at": time.time() + max(120, expires_in),
                "project_id": str(prev.get("project_id") or ""),
                "detected_project_id": detected_proj or str(prev.get("detected_project_id") or ""),
            }
            self.active_account_id = acc_id
            self.auth_mode = "adc"
            self._cached_token = ""
            self._cached_token_expiry = 0.0
            self._save()
            self._finish_oauth("signed_in")
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            self._finish_oauth("failed", error=f"Errore durante la verifica dell'account Google: {exc}")

    async def cancel(self) -> Json:
        proc, self.pending = self.pending, None
        if proc is not None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
        self._pending_oauth = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self.login_task is not None and self.login_task is not asyncio.current_task():
            self.login_task.cancel()
            await asyncio.gather(self.login_task, return_exceptions=True)
            self.login_task = None
        if self._oauth_handlers:
            handlers = tuple(h for h in self._oauth_handlers if h is not asyncio.current_task())
            for h in handlers:
                h.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)
        self.state = {"phase": "cancelled", "connected": self.connected}
        return self.status()

    async def logout(self, account_id: Any = None) -> Json:
        target = str(account_id or "").strip()
        if self.auth_mode == "api_key" and not target:
            self.api_key = ""
        else:
            eff = target or self.effective_account_id
            if eff and eff != SYSTEM_ACCOUNT_ID and eff in self.accounts:
                self.accounts.pop(eff, None)
                if self.active_account_id == eff:
                    self.active_account_id = ""
                    self.active_account_id = self.effective_account_id
            else:
                self.api_key = ""
        self._cached_token = ""
        self._cached_token_expiry = 0.0
        self._save()
        self.state = {"phase": "signed_in" if self.connected else "idle", "connected": self.connected}
        return self.status()

    async def close(self) -> None:
        if self.pending is not None or self._listener is not None:
            await self.cancel()
        await self.client.aclose()


class GoogleCloudAccounts:
    """Service lifecycle and admission for Google Cloud / Vertex AI credentials."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self._auth: GoogleCloudAccount | None = None
        self.changing = False

    @property
    def auth(self) -> GoogleCloudAccount:
        if self._auth is None:
            self._auth = GoogleCloudAccount(
                self.service.paths.config / "google-cloud",
                protect_secrets=functools.partial(
                    self.service.redactor.set_provider_values,
                    owner=BACKEND,
                ),
            )
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
        return self.changing or any(db.execute(query, (BACKEND,)).fetchone() for query in checks)

    async def dispatch(self, action: str, params: Json | None = None) -> Json:
        params = params or {}
        if self.service.backend_factory is not None and self._auth is None:
            if action == "status":
                return {
                    "connected": False,
                    "auth_mode": "adc",
                    "project_id": "",
                    "configured_project_id": "",
                    "detected_project_id": "",
                    "location": "global",
                    "api_key_configured": False,
                    "adc_available": False,
                    "gcloud_available": False,
                    "account_email": "",
                    "active_account_id": "",
                    "active_account_is_system": False,
                    "accounts": [],
                    "custom_models": [],
                    "supported_locations": list(SUPPORTED_LOCATIONS),
                    "login": {"phase": "idle", "connected": False},
                }
            raise KiraError("UNAVAILABLE", "Google Cloud sign-in is unavailable in the test backend")
        if action == "status":
            return self.auth.status()
        if self.busy():
            raise KiraError("BUSY", "Finish Google Cloud requests before changing account access")
        self.changing = True
        try:
            if action == "login":
                result = await self.auth.login()
            elif action == "select":
                result = self.auth.select(params.get("account_id"))
            elif action == "cancel":
                result = await self.auth.cancel()
            elif action == "logout":
                result = await self.auth.logout(params.get("account_id"))
            elif action == "configure":
                result = self.auth.configure(
                    auth_mode=params.get("auth_mode"),
                    project_id=params.get("project_id"),
                    location=params.get("location"),
                    api_key=params.get("api_key"),
                    custom_models=params.get("custom_models"),
                )
            else:
                raise KiraError("INVALID_ARGUMENT", "Unsupported Google Cloud account action")
            provider = self.service._control_backends.pop(BACKEND, None)
            if provider:
                await provider.close()
            return result
        finally:
            self.changing = False

    async def prepare(self) -> None:
        if self.changing:
            raise KiraError("BUSY", "Google Cloud account settings are changing")
        if self.service.backend_factory is not None and self._auth is None:
            return
        if self.auth.pending is not None or self.auth._pending_oauth is not None:
            raise KiraError("BUSY", "Finish or cancel Google Cloud sign-in before starting work")
        if self.auth.auth_mode == "api_key":
            if not self.auth.api_key:
                raise KiraError("AUTH_REQUIRED", "Configure the Google Cloud / Gemini API key in Kira settings")
        else:
            token = await self.auth.resolve_token()
            if not token:
                raise KiraError("AUTH_REQUIRED", "Connect Google Cloud (ADC / gcloud) before using this provider")

    async def close(self) -> None:
        if self._auth is not None:
            await self._auth.close()
            self._auth = None


def _is_anthropic_vertex_model(model: str) -> bool:
    clean = model.strip().lower()
    return clean.startswith("claude-") or "publishers/anthropic/" in clean or clean.startswith("anthropic/")


def _clean_vertex_model_id(model: str) -> str:
    clean = model.strip()
    for prefix in ("publishers/google/models/", "publishers/anthropic/models/", "models/", "google/", "anthropic/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    return clean


def _vertex_base_url(location: str) -> str:
    loc = (location or "global").strip()
    if loc == "global":
        return "https://aiplatform.googleapis.com"
    return f"https://{loc}-aiplatform.googleapis.com"


def _clean_gemini_schema(schema: Any) -> Json:
    """Sanitize a JSON Schema into the OpenAPI 3.0 subset accepted by Vertex AI / Gemini."""
    if not isinstance(schema, dict):
        return {"type": "object"}
    allowed_keys = {"type", "description", "properties", "required", "items", "enum", "nullable"}
    cleaned: Json = {}
    for key, value in schema.items():
        if key not in allowed_keys:
            continue
        if key == "type" and isinstance(value, list):
            non_null = [t for t in value if t != "null"]
            cleaned["type"] = non_null[0] if non_null else "string"
            if "null" in value:
                cleaned["nullable"] = True
        elif key == "properties" and isinstance(value, dict):
            cleaned["properties"] = {
                prop_name: _clean_gemini_schema(prop_schema)
                for prop_name, prop_schema in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            cleaned["items"] = _clean_gemini_schema(value)
        elif key == "required" and isinstance(value, list):
            props = schema.get("properties")
            if isinstance(props, dict):
                req = [r for r in value if isinstance(r, str) and r in props]
            else:
                req = [r for r in value if isinstance(r, str)]
            if req:
                cleaned["required"] = req
        else:
            cleaned[key] = value
    if "type" not in cleaned:
        cleaned["type"] = "object" if "properties" in cleaned else "string"
    return cleaned


def _gemini_image_part(image: Any) -> Json:
    if isinstance(image, dict):
        url = image.get("image_url", "")
        if not isinstance(url, str) or not url.startswith(PNG_DATA_URL):
            raise StreamError("Google Cloud received an invalid Kira image attachment")
        data = url[len(PNG_DATA_URL):]
    elif isinstance(image, str):
        data = image[len(PNG_DATA_URL):] if image.startswith(PNG_DATA_URL) else image
    else:
        raise StreamError("Google Cloud received an invalid image input")
    if not data or len(data) > MAX_IMAGE_BASE64_CHARS:
        raise StreamError("Google Cloud image exceeds request size limit")
    try:
        base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise StreamError("Google Cloud received invalid base64 image data") from exc
    return {"inlineData": {"mimeType": "image/png", "data": data}}


def _gemini_contents(source: Any, image_inputs: dict[str, list[Json]]) -> tuple[list[Json], str]:
    """Translate Kira's internal message list into Vertex AI / Gemini contents and systemInstruction."""
    if not isinstance(source, list):
        raise StreamError("Invalid provider message history")
    system: list[str] = []
    contents: list[Json] = []

    def append_turn(role: str, parts: list[Json]) -> None:
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for message in source:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str) and content:
                system.append(content)
            continue
        if role == "assistant":
            model_parts: list[Json] = []
            if isinstance(content, str) and content:
                model_parts.append({"text": content})
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    continue
                fn = call["function"]
                name = fn.get("name")
                args = fn.get("arguments")
                if not isinstance(name, str) or not isinstance(args, dict):
                    continue
                part: Json = {"functionCall": {"name": name, "args": args}}
                sig = call.get("thought_signature")
                if isinstance(sig, str) and sig:
                    part["thoughtSignature"] = sig
                model_parts.append(part)
            append_turn("model", model_parts)
        elif role == "tool":
            name = message.get("tool_name")
            if not isinstance(name, str) or not name:
                continue
            parsed_result: Any
            if isinstance(content, str):
                try:
                    parsed_result = json.loads(content)
                except ValueError:
                    parsed_result = {"output": content}
            else:
                parsed_result = content
            if not isinstance(parsed_result, dict):
                parsed_result = {"result": parsed_result}
            tool_parts: list[Json] = [
                {"functionResponse": {"name": name, "response": parsed_result}},
            ]
            images = message.get("images", [])
            if isinstance(images, list):
                tool_parts.extend(_gemini_image_part(img) for img in images)
            append_turn("user", tool_parts)
        elif role == "user":
            user_parts: list[Json] = []
            if isinstance(content, str) and content:
                user_parts.append({"text": content})
            run_id = message.get("run_id")
            images = image_inputs.get(run_id, []) if isinstance(run_id, str) else []
            user_parts.extend(_gemini_image_part(img) for img in images)
            append_turn("user", user_parts)

    if not contents or contents[-1]["role"] != "user":
        append_turn("user", [{"text": "Continue."}])
    return contents, "\n\n".join(system)


def _thinking_budget_for_effort(effort: str | None) -> int | None:
    if not effort or effort == "off":
        return 0
    return {
        "low": 2048,
        "medium": 8192,
        "high": 24576,
        "max": 32768,
    }.get(effort)


def _thinking_config_for_gemini(model_id: str, effort: str | None, max_output: int) -> Json | None:
    if not effort or effort == "off":
        return None
    m = model_id.lower()
    if "2.5" in m:
        budget = _thinking_budget_for_effort(effort)
        if budget and budget > 0:
            return {
                "includeThoughts": True,
                "thinkingBudget": min(budget, max(1024, max_output - 1024)),
            }
        return None
    if "gemini-3" in m or "gemini-4" in m or "argon" in m:
        level = {
            "low": "LOW",
            "medium": "MEDIUM",
            "high": "HIGH",
            "max": "HIGH",
        }.get(effort, "MEDIUM")
        return {
            "includeThoughts": True,
            "thinkingLevel": level,
        }
    return None


class GoogleCloudBackend(OllamaBackend):
    """Google Cloud / Vertex AI & Gemini backend supporting Gemini, Argon, and Vertex Partner Claude models."""

    def __init__(self, account: GoogleCloudAccount, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(_vertex_base_url(account.location))
        if client is not None:
            self.client = client
        self.account = account
        self._details: dict[str, Json] = {}
        self._probed: dict[str, bool] = {}
        self._populate_builtin_details()

    def _populate_builtin_details(self) -> None:
        self._details = {}
        for row in BUILTIN_MODEL_CATALOG:
            modalities = row.get("input_modalities", ["text", "image"])
            self._details[row["id"]] = {
                "name": row["name"],
                "context_window": row["context_window"],
                "max_output_tokens": row["max_output_tokens"],
                "capabilities": ["tools", *(["vision"] if "image" in modalities else [])],
                "model_info": row,
                "catalog_fallback": False,
            }
        for custom_id in self.account.custom_models:
            if custom_id not in self._details:
                is_claude = _is_anthropic_vertex_model(custom_id)
                row = {
                    "id": custom_id,
                    "name": f"{custom_id} · Vertex AI",
                    "family": "anthropic_vertex" if is_claude else "gemini",
                    "context_window": 200_000 if is_claude else 1_048_576,
                    "max_output_tokens": 32_768 if is_claude else 65_536,
                    "input_modalities": ["text", "image"],
                    "effort": {
                        "supported_levels": ["off", "low", "medium", "high", "max"],
                        "default_level": "high",
                    },
                }
                self._details[custom_id] = {
                    "name": row["name"],
                    "context_window": row["context_window"],
                    "max_output_tokens": row["max_output_tokens"],
                    "capabilities": ["tools", "vision"],
                    "model_info": row,
                    "catalog_fallback": False,
                }

    async def _show(self, model: str, *, refresh: bool = False) -> Json:
        clean = _clean_vertex_model_id(model)
        if refresh or not self._details:
            self._populate_builtin_details()
        if clean not in self._details:
            is_claude = _is_anthropic_vertex_model(clean)
            row = {
                "id": clean,
                "name": clean,
                "family": "anthropic_vertex" if is_claude else "gemini",
                "context_window": 200_000 if is_claude else 1_048_576,
                "max_output_tokens": 32_768 if is_claude else 65_536,
                "input_modalities": ["text", "image"],
                "effort": {
                    "supported_levels": ["off", "low", "medium", "high", "max"],
                    "default_level": "medium",
                },
            }
            self._details[clean] = {
                "name": clean,
                "context_window": row["context_window"],
                "max_output_tokens": row["max_output_tokens"],
                "capabilities": ["tools", "vision"],
                "model_info": row,
                "catalog_fallback": False,
            }
        return self._details[clean]

    async def _probe_tools(self, model: str, ctx: RunContext) -> bool:
        return True

    def _probe_outcome(self, provider: str, model: str, detail: Json) -> str:
        return "passed"

    async def discover(self) -> Json:
        try:
            self._populate_builtin_details()
            status = self.account.status()
            if self.account.auth_mode == "api_key":
                if not self.account.api_key:
                    return {
                        "available": False,
                        "models": [],
                        "capabilities": GOOGLE_CAPABILITIES,
                        "account": status,
                        "error_code": "AUTH_REQUIRED",
                        "error": "Inserisci una chiave API Google Cloud / Gemini nel pannello Modelli.",
                    }
            else:
                if not self.account.connected:
                    return {
                        "available": False,
                        "models": [],
                        "capabilities": GOOGLE_CAPABILITIES,
                        "account": status,
                        "error_code": "AUTH_REQUIRED",
                        "error": "Collega Google Cloud (gcloud ADC) o inserisci una chiave API nel pannello Modelli.",
                    }
            models: list[Json] = []
            has_project = bool(self.account.effective_project_id())
            for name, detail in self._details.items():
                if _is_anthropic_vertex_model(name) and self.account.auth_mode == "api_key" and not has_project:
                    # Vertex AI Partner Claude models require a Google Cloud project_id.
                    continue
                info = detail.get("model_info", {})
                effort = info.get("effort", {}) if isinstance(info, dict) else {}
                levels = effort.get("supported_levels", []) if isinstance(effort, dict) else []
                models.append(
                    {
                        "id": name,
                        "name": detail["name"],
                        "capabilities": {**GOOGLE_CAPABILITIES, "tools": True, "images": True},
                        "tools_metadata": True,
                        "tools_verified": True,
                        "tool_probe": "passed",
                        "context_window": detail.get("context_window"),
                        "max_output_tokens": detail.get("max_output_tokens"),
                        "supported_reasoning_efforts": levels if isinstance(levels, list) else [],
                        "catalog_fallback": False,
                    }
                )
            return {
                "available": True,
                "models": models[:MAX_MODELS],
                "capabilities": GOOGLE_CAPABILITIES,
                "account": status,
            }
        except Exception as exc:
            return {
                "available": False,
                "models": [],
                "capabilities": GOOGLE_CAPABILITIES,
                "account": self.account.status(),
                "error_code": "PROVIDER_UNAVAILABLE",
                "error": f"Google Cloud provider non disponibile: {str(exc)[:200]}",
            }

    async def _auth_headers_and_params(self) -> tuple[dict[str, str], dict[str, str], str]:
        """Return (headers, query_params, secret_for_redaction)."""
        headers = {"user-agent": f"Kira-Desktop/{__version__}"}
        params: dict[str, str] = {}
        project = self.account.effective_project_id()
        if self.account.auth_mode == "api_key":
            if not self.account.api_key:
                raise GoogleCloudAuthError("Configure the Google Cloud / Gemini API key before using this provider")
            headers["x-goog-api-key"] = self.account.api_key
            if project:
                headers["x-goog-user-project"] = project
            return headers, params, self.account.api_key
        token = await self.account.resolve_token()
        headers["Authorization"] = f"Bearer {token}"
        if project:
            headers["x-goog-user-project"] = project
        return headers, params, token

    async def _stream(
        self,
        body: Json,
        ctx: RunContext | None = None,
        item_id: str | None = None,
    ) -> tuple[Json, Json]:
        model_id = _clean_vertex_model_id(str(body.get("model") or (ctx.model if ctx else "gemini-4-argon")))
        if _is_anthropic_vertex_model(model_id):
            return await self._stream_vertex_anthropic(model_id, body, ctx, item_id)
        return await self._stream_gemini(model_id, body, ctx, item_id)

    async def _stream_gemini(
        self,
        model_id: str,
        body: Json,
        ctx: RunContext | None,
        item_id: str | None,
    ) -> tuple[Json, Json]:
        headers, query_params, secret = await self._auth_headers_and_params()
        query_params["alt"] = "sse"
        project = self.account.effective_project_id()
        location = (self.account.location or "global").strip()

        source = body.get("messages", [])
        if ctx is not None and ctx.image_inputs.get(ctx.run_id):
            source = _bind_active_prompt(source, ctx)
        contents, system_text = _gemini_contents(source, ctx.image_inputs if ctx else {})
        if body.get("format"):
            system_text += (
                "\nReturn one JSON object matching this schema, with no surrounding text: "
                + json.dumps(body["format"])
            )

        detail = await self._show(model_id)
        max_output = int(detail.get("max_output_tokens") or MAX_GOOGLE_OUTPUT_TOKENS)
        if ctx:
            byte_limit = ctx.settings.get("max_output_bytes")
            if type(byte_limit) is int and byte_limit > 0:
                max_output = min(max_output, max(256, byte_limit // 4))

        effort = ctx.settings.get("reasoning_effort") if ctx else body.get("reasoning_effort")
        effort_str = effort if isinstance(effort, str) else None

        tools = body.get("tools", [])
        declarations: list[Json] = []
        if isinstance(tools, list) and tools:
            declarations = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "parameters": _clean_gemini_schema(item["function"].get("parameters", {"type": "object"})),
                }
                for item in tools
                if isinstance(item, dict) and isinstance(item.get("function"), dict)
            ]

        def build_url_and_request(target_model: str, target_loc: str) -> tuple[str, Json]:
            if self.account.auth_mode == "api_key" and not project:
                target_url = f"{GEMINI_API_BASE}/v1beta/models/{target_model}:streamGenerateContent"
            else:
                if not project:
                    raise GoogleCloudAuthError(
                        "Project ID Google Cloud mancante. Imposta il Project ID nel pannello Modelli "
                        "o con 'gcloud config set project <PROJECT_ID>'."
                    )
                base = _vertex_base_url(target_loc)
                target_url = (
                    f"{base}/v1/projects/{project}/locations/{target_loc}"
                    f"/publishers/google/models/{target_model}:streamGenerateContent"
                )
            gen_cfg: Json = {"maxOutputTokens": max_output}
            if isinstance(body.get("options"), dict):
                temp = body["options"].get("temperature")
                if isinstance(temp, (int, float)) and not isinstance(temp, bool):
                    gen_cfg["temperature"] = temp
            think_cfg = _thinking_config_for_gemini(target_model, effort_str, max_output)
            if think_cfg is not None:
                gen_cfg["thinkingConfig"] = think_cfg
            req: Json = {
                "contents": contents,
                "generationConfig": gen_cfg,
            }
            if system_text:
                req["systemInstruction"] = {"parts": [{"text": system_text}]}
            if declarations:
                req["tools"] = [{"functionDeclarations": declarations}]
            return target_url, req

        # Build candidate (model, location) attempts so regional 404 or legacy alias 404 falls back cleanly
        attempts: list[tuple[str, str]] = [(model_id, location)]
        if location != "global":
            attempts.append((model_id, "global"))
        fallback_model = VERTEX_MODEL_FALLBACKS.get(model_id)
        if fallback_model and fallback_model != model_id:
            if (fallback_model, "global") not in attempts:
                attempts.append((fallback_model, "global"))

        text = ""
        thought_text = ""
        tool_calls: list[Json] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        finish_reason = ""
        limit = int(ctx.settings.get("max_output_bytes", 4 * 1024 * 1024)) if ctx else 65_536
        total_bytes = 0
        summary_item_id = f"{item_id}:summary" if item_id else None

        api_enable_attempted = False
        idx = 0
        while idx < len(attempts):
            cur_model, cur_loc = attempts[idx]
            url, request = build_url_and_request(cur_model, cur_loc)
            async with self.client.stream(
                "POST",
                url,
                params=query_params,
                json=request,
                headers=headers,
                timeout=180.0,
            ) as response:
                if response.status_code >= 400:
                    error_body = bytearray()
                    async for chunk in response.aiter_bytes():
                        error_body.extend(chunk[: max(0, 65_537 - len(error_body))])
                        if len(error_body) > 65_536:
                            break
                    safe_detail = _safe_error_detail(bytes(error_body[:65_536]), secret)
                    if (
                        response.status_code == 403
                        and not api_enable_attempted
                        and project
                        and (
                            "aiplatform.googleapis.com" in safe_detail
                            or "has not been used in project" in safe_detail
                            or "SERVICE_DISABLED" in safe_detail
                        )
                    ):
                        api_enable_attempted = True
                        if await self.account.ensure_vertex_api_enabled(project):
                            await asyncio.sleep(2.5)
                            continue
                    if response.status_code == 404 and idx + 1 < len(attempts):
                        idx += 1
                        continue
                    raise GoogleCloudHTTPError(response, safe_detail)
                response.raise_for_status()
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if len(line.encode("utf-8")) > 1_048_576:
                        raise StreamError("Google Cloud stream record exceeds size limit")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        raw = "\n".join(data_lines)
                        data_lines.clear()
                        if raw.strip() == "[DONE]":
                            break
                        total_bytes += len(raw.encode("utf-8"))
                        if total_bytes > limit * 2:
                            raise StreamError("Google Cloud response exceeded configured output limit")
                        try:
                            record = json.loads(raw)
                        except ValueError as exc:
                            raise StreamError("Google Cloud emitted invalid JSON chunk") from exc
                        if not isinstance(record, dict):
                            continue
                        usage_meta = record.get("usageMetadata")
                        if isinstance(usage_meta, dict):
                            if isinstance(usage_meta.get("promptTokenCount"), int):
                                input_tokens = usage_meta["promptTokenCount"]
                            cand_tokens = usage_meta.get("candidatesTokenCount")
                            thought_tokens = usage_meta.get("thoughtsTokenCount") or 0
                            if isinstance(cand_tokens, int):
                                output_tokens = cand_tokens + (thought_tokens if isinstance(thought_tokens, int) else 0)
                        candidates = record.get("candidates")
                        if isinstance(candidates, list) and candidates:
                            cand = candidates[0]
                            if isinstance(cand, dict):
                                if isinstance(cand.get("finishReason"), str):
                                    finish_reason = cand["finishReason"]
                                content_obj = cand.get("content")
                                parts = content_obj.get("parts") if isinstance(content_obj, dict) else None
                                if isinstance(parts, list):
                                    for part in parts:
                                        if not isinstance(part, dict):
                                            continue
                                        if part.get("thought") is True and isinstance(part.get("text"), str):
                                            delta_thought = part["text"]
                                            if delta_thought:
                                                thought_text += delta_thought
                                                if ctx and summary_item_id:
                                                    await ctx.emit(
                                                        "reasoning.summary.delta",
                                                        {
                                                            "source": "provider_summary",
                                                            "provider": BACKEND,
                                                            "item_id": summary_item_id,
                                                            "content": delta_thought,
                                                        },
                                                    )
                                        elif isinstance(part.get("text"), str):
                                            value = part["text"]
                                            if value:
                                                text += value
                                                if ctx:
                                                    await ctx.emit("message.delta", {"content": value, "item_id": item_id})
                                        if isinstance(part.get("functionCall"), dict):
                                            fc = part["functionCall"]
                                            fn_name = fc.get("name")
                                            fn_args = fc.get("args", {})
                                            if isinstance(fn_name, str) and isinstance(fn_args, dict):
                                                call_entry: Json = {
                                                    "id": f"google-{len(tool_calls)}",
                                                    "type": "function",
                                                    "function": {"name": fn_name, "arguments": fn_args},
                                                }
                                                sig = part.get("thoughtSignature")
                                                if isinstance(sig, str) and sig:
                                                    call_entry["thought_signature"] = sig
                                                tool_calls.append(call_entry)
                break

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
        elif ctx and not thought_text:
            await ctx.emit(
                "reasoning.summary.status",
                {
                    "source": "provider_summary",
                    "provider": BACKEND,
                    "status": "unavailable",
                    "reason": "provider_no_public_summary",
                },
            )

        message: Json = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        terminal: Json = {
            "done": True,
            "done_reason": "tool_use" if tool_calls else ("length" if finish_reason == "MAX_TOKENS" else "stop"),
            "prompt_eval_count": input_tokens,
            "eval_count": output_tokens,
            "max_output_tokens": max_output,
            "tool_input_incomplete": False,
        }
        return message, terminal

    async def _stream_vertex_anthropic(
        self,
        model_id: str,
        body: Json,
        ctx: RunContext | None,
        item_id: str | None,
    ) -> tuple[Json, Json]:
        headers, query_params, secret = await self._auth_headers_and_params()
        project = self.account.effective_project_id()
        if not project:
            raise GoogleCloudAuthError(
                "I modelli Claude su Vertex AI richiedono un Project ID Google Cloud configurato."
            )
        location = (self.account.location or "global").strip()

        source = body.get("messages", [])
        if ctx is not None and ctx.image_inputs.get(ctx.run_id):
            source = _bind_active_prompt(source, ctx)
        messages, system_text = _anthropic_messages(source, ctx.image_inputs if ctx else {})
        if body.get("format"):
            system_text += (
                "\nReturn one JSON object matching this schema, with no surrounding text: "
                + json.dumps(body["format"])
            )

        detail = await self._show(model_id)
        request_cap = int(detail.get("max_output_tokens") or MAX_VERTEX_CLAUDE_OUTPUT_TOKENS)
        if ctx:
            byte_limit = ctx.settings.get("max_output_bytes")
            if type(byte_limit) is int and byte_limit > 0:
                request_cap = min(request_cap, max(1024, byte_limit // 4))

        request: Json = {
            "anthropic_version": "vertex-2023-10-16",
            "max_tokens": request_cap,
            "messages": messages,
            "stream": True,
        }
        if system_text:
            request["system"] = system_text

        tools = body.get("tools", [])
        if isinstance(tools, list) and tools:
            request["tools"] = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "input_schema": item["function"].get("parameters", {"type": "object"}),
                }
                for item in tools
                if isinstance(item, dict) and isinstance(item.get("function"), dict)
            ]

        effort = ctx.settings.get("reasoning_effort") if ctx else None
        budget = _thinking_budget_for_effort(effort if isinstance(effort, str) else None)
        if budget and budget > 0 and request_cap > budget + 1024:
            request["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif isinstance(body.get("options"), dict):
            temperature = body["options"].get("temperature")
            if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
                request["temperature"] = temperature

        text = ""
        thought_text = ""
        blocks: dict[int, Json] = {}
        tool_calls: list[Json] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        stop_reason = ""
        message_stopped = False
        incomplete_tool_input = False
        total = 0
        limit = int(ctx.settings.get("max_output_bytes", 4 * 1024 * 1024)) if ctx else 65_536
        summary_item_id = f"{item_id}:summary" if item_id else None

        locations_to_try = [location] if location == "global" else [location, "global"]
        api_enable_attempted = False
        loc_idx = 0
        while loc_idx < len(locations_to_try):
            cur_loc = locations_to_try[loc_idx]
            base = _vertex_base_url(cur_loc)
            url = (
                f"{base}/v1/projects/{project}/locations/{cur_loc}"
                f"/publishers/anthropic/models/{model_id}:streamRawPredict"
            )
            async with self.client.stream(
                "POST",
                url,
                params=query_params,
                json=request,
                headers=headers,
                timeout=180.0,
            ) as response:
                if response.status_code >= 400:
                    error_body = bytearray()
                    async for chunk in response.aiter_bytes():
                        error_body.extend(chunk[: max(0, 65_537 - len(error_body))])
                        if len(error_body) > 65_536:
                            break
                    safe_detail = _safe_error_detail(bytes(error_body[:65_536]), secret)
                    if (
                        response.status_code == 403
                        and not api_enable_attempted
                        and (
                            "aiplatform.googleapis.com" in safe_detail
                            or "has not been used in project" in safe_detail
                            or "SERVICE_DISABLED" in safe_detail
                        )
                    ):
                        api_enable_attempted = True
                        if await self.account.ensure_vertex_api_enabled(project):
                            await asyncio.sleep(2.5)
                            continue
                    if response.status_code == 404 and loc_idx + 1 < len(locations_to_try):
                        loc_idx += 1
                        continue
                    if response.status_code == 429 and "global_online_prediction_requests_per_base_model" in safe_detail:
                        safe_detail = (
                            "Quota Anthropic Partner impostata a 0 su questo progetto Google Cloud. "
                            "Per usare Claude su Vertex AI devi: (1) abilitare il modello in Google Cloud Console → Vertex AI → Model Garden "
                            "accettando i termini del Partner Marketplace Anthropic, e (2) avere un profilo di fatturazione sbloccato "
                            "(i crediti Free Trial Google non coprono modelli di terze parti) o aumentare la quota "
                            "'global_online_prediction_requests_per_base_model' in IAM e amministrazione → Quote. "
                            "I modelli nativi Google (Gemini 4 Argon, Gemini 3.1 Pro, Gemini 3.8 Flash) sono invece subito inclusi e attivi."
                        )
                    raise GoogleCloudHTTPError(response, safe_detail)
                response.raise_for_status()
                event = ""
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if len(line.encode("utf-8")) > 1_048_576:
                        raise StreamError("Vertex AI Claude stream record exceeds size limit")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        raw = "\n".join(data_lines)
                        data_lines.clear()
                        total += len(raw.encode("utf-8"))
                        if total > limit * 2:
                            raise StreamError("Vertex AI Claude response exceeded configured output limit")
                        try:
                            record = json.loads(raw)
                        except ValueError as exc:
                            raise StreamError("Vertex AI Claude emitted invalid Messages data") from exc
                        if not isinstance(record, dict):
                            continue
                        event_type = event or str(record.get("type") or "")
                        if event_type == "message_start":
                            usage = record.get("message", {}).get("usage", {})
                            input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
                        elif event_type == "content_block_start":
                            index = record.get("index")
                            block = record.get("content_block")
                            if type(index) is int and isinstance(block, dict):
                                blocks[index] = dict(block)
                                if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
                                    text += block["text"]
                                    if ctx:
                                        await ctx.emit("message.delta", {"content": block["text"], "item_id": item_id})
                        elif event_type == "content_block_delta":
                            index = record.get("index")
                            delta = record.get("delta")
                            if type(index) is int and isinstance(delta, dict):
                                dtype = delta.get("type")
                                if dtype == "text_delta" and isinstance(delta.get("text"), str):
                                    value = delta["text"]
                                    text += value
                                    if ctx and value:
                                        await ctx.emit("message.delta", {"content": value, "item_id": item_id})
                                elif dtype == "thinking_delta" and isinstance(delta.get("thinking"), str):
                                    tval = delta["thinking"]
                                    if tval:
                                        thought_text += tval
                                        if ctx and summary_item_id:
                                            await ctx.emit(
                                                "reasoning.summary.delta",
                                                {
                                                    "source": "provider_summary",
                                                    "provider": BACKEND,
                                                    "item_id": summary_item_id,
                                                    "content": tval,
                                                },
                                            )
                                elif dtype == "input_json_delta":
                                    blk = blocks.get(index)
                                    if blk is not None and blk.get("type") == "tool_use":
                                        blk["partial_json"] = blk.get("partial_json", "") + str(
                                            delta.get("partial_json", "")
                                        )
                        elif event_type == "message_delta":
                            delta = record.get("delta", {})
                            usage = record.get("usage", {})
                            stop_reason = str(delta.get("stop_reason", "")) if isinstance(delta, dict) else ""
                            output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
                        elif event_type == "error":
                            raise StreamError("Vertex AI Claude Messages request failed")
                        elif event_type == "message_stop":
                            message_stopped = True
                            break
                        event = ""
                break

        if not message_stopped:
            raise StreamError("Vertex AI Claude stream ended without message_stop")

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
        elif ctx and not thought_text:
            await ctx.emit(
                "reasoning.summary.status",
                {
                    "source": "provider_summary",
                    "provider": BACKEND,
                    "status": "unavailable",
                    "reason": "provider_no_public_summary",
                },
            )

        for index, block in sorted(blocks.items()):
            if block.get("type") != "tool_use":
                continue
            if stop_reason == "max_tokens":
                incomplete_tool_input = True
                continue
            arguments = block.get("input", {})
            if block.get("partial_json"):
                try:
                    arguments = json.loads(block["partial_json"])
                except ValueError:
                    incomplete_tool_input = True
                    continue
            if not isinstance(arguments, dict) or not isinstance(block.get("name"), str):
                raise StreamError("Vertex AI Claude returned invalid tool arguments")
            tool_calls.append(
                {
                    "id": block.get("id", f"vertex-claude-{index}"),
                    "type": "function",
                    "function": {"name": block["name"], "arguments": arguments},
                }
            )
        if incomplete_tool_input:
            tool_calls.clear()

        message: Json = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        terminal = {
            "done": True,
            "done_reason": (
                "tool_use"
                if tool_calls
                else "length"
                if stop_reason == "max_tokens"
                else "tool_input_incomplete"
                if incomplete_tool_input
                else stop_reason
            ),
            "prompt_eval_count": input_tokens,
            "eval_count": output_tokens,
            "max_output_tokens": request_cap,
            "tool_input_incomplete": incomplete_tool_input,
        }
        return message, terminal
