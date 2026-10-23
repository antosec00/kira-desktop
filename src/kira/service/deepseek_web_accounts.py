"""Lifecycle and secure storage for the DeepSeek web session token."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import stat
from typing import Any

from kira.domain.contracts import Json, KiraError

BACKEND = "deepseek_web"
MAX_TOKEN_BYTES = 65_536
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
        # Why a stored token file was not loaded; saving a new token or signing
        # out replaces the file and clears it.
        self.error: KiraError | None = None
        self._load()

    def _unavailable(self, code: str, message: str) -> None:
        self.error = KiraError(code, message)
        self.state = {"phase": "unavailable", "connected": False}

    def _load(self) -> None:
        """Load a private token file; an unsafe or invalid one is reported, never used."""
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        except OSError:
            self._unavailable("UNSAFE_PATH", "The DeepSeek Web token file cannot be opened safely; "
                              "save the web token again")
            return
        with os.fdopen(fd, "rb") as stream:  # The file object owns and closes the descriptor.
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077):
                self._unavailable("UNSAFE_PATH", "The DeepSeek Web token file must be a private regular "
                                  "file owned by you; save the web token again")
                return
            raw = stream.read(MAX_TOKEN_BYTES + 1)
        try:
            value = json.loads(raw) if len(raw) <= MAX_TOKEN_BYTES else None
        except (ValueError, UnicodeError):
            value = None
        token = value.get("token") if isinstance(value, dict) else None
        if not valid_token(token):
            self._unavailable("CREDENTIAL_STORE", "The DeepSeek Web token file is invalid; "
                              "save the web token again")
            return
        assert isinstance(token, str)
        self.token = token
        self.state = {"phase": "signed_in", "connected": True}
        self.service.redactor.set_provider_values((token,), owner=BACKEND)

    def _save(self, token: str) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise KiraError("UNSAFE_PATH", "DeepSeek web token storage must be a user-owned directory")
        with contextlib.suppress(OSError):
            self.path.parent.chmod(0o700)
        data = json.dumps({"version": 1, "token": token}, separators=(",", ":")).encode()
        temporary = self.path.parent / f".token-{secrets.token_hex(16)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    def busy(self) -> bool:
        """DeepSeek Web work in progress, reserved or queued keeps the token in use."""
        db = self.service.store.db
        checks = (
            "SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id WHERE s.backend=? "
            "AND (r.state NOT IN ('completed','failed','cancelled','interrupted') "
            "OR EXISTS (SELECT 1 FROM project_reservations p WHERE p.owner_kind='run' AND p.owner_id=r.id)) LIMIT 1",
            "SELECT 1 FROM prompt_queue q JOIN sessions s ON s.id=q.session_id WHERE s.backend=? "
            "AND q.status IN ('pending','starting','running') LIMIT 1",
        )
        return any(db.execute(query, (BACKEND,)).fetchone() for query in checks)

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
            if self.error is not None:
                raise self.error
            return self.status()
        if action not in {"login", "logout"}:
            raise KiraError("INVALID_ARGUMENT", "Unsupported DeepSeek web account action")
        if self.busy():
            raise KiraError("BUSY", "Finish DeepSeek Web requests and queued requests before changing the web token")
        if action == "login":
            token = params.get("token")
            if not isinstance(token, str) or not token.strip():
                raise KiraError("AUTH_REQUIRED", "Provide the DeepSeek web userToken")
            token = token.strip()
            if not valid_token(token):
                raise KiraError("INVALID_ARGUMENT", "DeepSeek web token contains invalid characters")
            self._save(token)
            self.token = token
            self.error = None
            self.state = {"phase": "signed_in", "connected": True}
            self.service.redactor.set_provider_values((token,), owner=BACKEND)
            return self.status()
        self.token = ""
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self.error = None
        self.state = {"phase": "idle", "connected": False}
        return self.status()

    async def prepare(self) -> None:
        if self.error is not None:
            raise self.error
        if not self.token:
            raise KiraError("AUTH_REQUIRED", "Configure a DeepSeek web token before using this provider")
