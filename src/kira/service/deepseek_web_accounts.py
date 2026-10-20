"""Lifecycle and secure storage for the DeepSeek web session token."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import stat
from typing import Any

from kira.domain.contracts import Json, KiraError

MAX_TOKEN_BYTES = 65_536
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
IS_WINDOWS = os.name == "nt"
MAX_TOKEN_CHARS = 8192


def valid_token(value: Any) -> bool:
    """A bearer token is bounded printable ASCII, so it cannot split HTTP headers."""
    return (isinstance(value, str) and 0 < len(value) <= MAX_TOKEN_CHARS
            and all(33 <= ord(c) <= 126 for c in value))


class DeepSeekWebAccounts:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.path = service.paths.config / "deepseek-web" / "token.json"
        self.token = ""
        self.state: Json = {"phase": "idle", "connected": False}
        self._load()

    def _load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        except (FileNotFoundError, OSError):
            return
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise KiraError("UNSAFE_PATH", "Web token file is not a regular file")
            if not IS_WINDOWS and (info.st_uid != os.getuid() or info.st_nlink != 1):
                raise KiraError("UNSAFE_PATH", "Web token file is not user-owned")
            if not IS_WINDOWS and info.st_mode & 0o077:
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "rb") as stream:
                raw = stream.read(MAX_TOKEN_BYTES + 1)
            if len(raw) > MAX_TOKEN_BYTES:
                return
            value = json.loads(raw)
            token = value.get("token") if isinstance(value, dict) else None
            if not isinstance(token, str) or not valid_token(token):
                return
            self.token = token
            self.state = {"phase": "signed_in", "connected": True}
            if self.service.redactor:
                self.service.redactor.set_provider_values((token,))
        except (ValueError, json.JSONDecodeError, UnicodeError):
            return
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _save(self, token: str) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (not IS_WINDOWS and info.st_uid != os.getuid()):
            raise KiraError("UNSAFE_PATH", "DeepSeek web token storage must be a user-owned directory")
        if not IS_WINDOWS:
            with contextlib.suppress(OSError):
                self.path.parent.chmod(0o700)
        data = json.dumps({"version": 1, "token": token}, separators=(",", ":")).encode()
        temporary = self.path.parent / f".token-{secrets.token_hex(16)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    async def expire(self, rejected: str) -> None:
        if self.token != rejected:
            return
        self.token = ""
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self.state = {"phase": "expired", "connected": False}

    def status(self) -> Json:
        return {"connected": bool(self.token), "login": dict(self.state)}

    async def dispatch(self, action: str, params: Json | None = None) -> Json:
        params = params or {}
        if action == "status":
            return self.status()
        if action == "login":
            token = params.get("token")
            if not isinstance(token, str) or not token.strip():
                raise KiraError("AUTH_REQUIRED", "Provide the DeepSeek web userToken")
            token = token.strip()
            if not valid_token(token):
                raise KiraError("INVALID_ARGUMENT", "DeepSeek web token contains invalid characters")
            self._save(token)
            self.token = token
            self.state = {"phase": "signed_in", "connected": True}
            if self.service.redactor:
                self.service.redactor.set_provider_values((token,))
            return self.status()
        if action == "logout":
            self.token = ""
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
            self.state = {"phase": "idle", "connected": False}
            return self.status()
        raise KiraError("INVALID_ARGUMENT", "Unsupported DeepSeek web account action")

    async def prepare(self) -> None:
        if not self.token:
            raise KiraError("AUTH_REQUIRED", "Configure a DeepSeek web token before using this provider")