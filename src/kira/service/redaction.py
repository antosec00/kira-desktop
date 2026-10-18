"""Conservative redaction at persistence and diagnostic boundaries."""

import os
import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?:token|password|secret|api[_-]?key|authorization|cookie)", re.I)
_INLINE = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|password|authorization)\s*[:=]\s*)"
    r"(?:Bearer\s+)?([^\s,;\"']{8,})"
)
_PRIVATE_FIELDS = {"thinking", "reasoning", "analysis"}
_INLINE_KEYS = ("api_key", "api-key", "apikey", "access_token", "access-token",
                "accesstoken", "password", "authorization")
REDACTED = "[REDACTED]"
MAX_PENDING_CHARS = 65_536


def _prefix_table(value: str) -> list[int]:
    table = [0] * len(value)
    matched = 0
    for index in range(1, len(value)):
        while matched and value[index] != value[matched]:
            matched = table[matched - 1]
        if value[index] == value[matched]:
            matched += 1
        table[index] = matched
    return table


class Redactor:
    def __init__(self, *, values: tuple[str, ...] | None = None) -> None:
        self.values = tuple(
            sorted(
                {value for key, value in os.environ.items() if _SENSITIVE_KEY.search(key)
                 and len(value) >= 8} if values is None else {value for value in values if value},
                key=len, reverse=True
            )
        )
        self._prefixes = [(secret, _prefix_table(secret)) for secret in self.values]
        self._base_values = self.values
        self._provider_values: tuple[str, ...] = ()

    def set_provider_values(self, values: tuple[str, ...]) -> None:
        """Protect current and previous credential sets without growing forever.

        The service passes these in memory only. A rotation keeps its preceding
        values recognizable while late tool/stream output drains. Environment
        secrets retain their original lifetime. This is not a universal detector
        for arbitrary historical or third-party secrets.
        """
        current = tuple(sorted(set(values), key=len, reverse=True))
        if current == self._provider_values:
            return
        self.values = tuple(sorted(set(self._base_values + self._provider_values + current),
                                   key=len, reverse=True))
        self._provider_values = current
        self._prefixes = [(secret, _prefix_table(secret)) for secret in self.values]

    def text(self, value: str) -> str:
        for secret in self.values:
            value = value.replace(secret, "[REDACTED]")
        return _INLINE.sub(r"\1[REDACTED]", value)

    def clean(self, value: Any, *, private: bool = False) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key))
                and isinstance(item, str) else self.clean(item, private=private)
                for key, item in value.items()
                if not private or str(key).lower() not in _PRIVATE_FIELDS
            }
        if isinstance(value, (tuple, list)):
            return [self.clean(item, private=private) for item in value]
        return value


class StreamRedactor:
    """Keep only an ambiguous suffix, independent of transport fragment sizes.

    Known values use shared prefix tables. Inline assignments use a small state
    machine: once their value starts, all characters through its delimiter are
    hidden (including short values). Unfinished known-value prefixes are hidden
    at completion/interruption, since a disconnected provider cannot disambiguate
    them. This is deliberately conservative, not universal secret detection.
    """

    def __init__(self, redactor: Redactor) -> None:
        self.redactor = redactor
        self.pending = ""
        self._candidate = ""
        self._mode = "key"
        self._token = ""

    @property
    def pending_chars(self) -> int:
        return len(self.pending) + len(self._candidate) + len(self._token)

    def feed(self, value: str) -> str:
        value = self.pending + value
        self.pending = ""
        for secret in self.redactor.values:
            value = value.replace(secret, REDACTED)
        hold = 0
        for secret, table in self.redactor._prefixes:
            matched = 0
            # Only the final len(secret)-1 characters can be an incomplete match.
            for character in value[max(0, len(value) - len(secret) + 1):]:
                while matched and character != secret[matched]:
                    matched = table[matched - 1]
                if character == secret[matched]:
                    matched += 1
            hold = max(hold, matched)
        if hold > MAX_PENDING_CHARS:
            raise ValueError("Sensitive stream prefix exceeds redaction memory limit")
        if hold:
            value, self.pending = value[:-hold], value[-hold:]
        return self._inline(value)

    def _inline(self, value: str) -> str:
        output: list[str] = []
        for character in value:
            if self._mode == "value":
                if not character.isspace() and character not in ",;\"'":
                    self._token = (self._token + character.lower())[:7]
                    continue
                self._mode = ("before_value" if self._token == "bearer"
                              and character.isspace() else "key")
                self._token = ""
                output.append(character)
                continue
            if self._mode in {"after_key", "before_value"}:
                if character.isspace():
                    output.append(character)
                    continue
                if self._mode == "after_key" and character in ":=":
                    output.append(character)
                    self._mode = "before_value"
                    continue
                if self._mode == "before_value" and character not in ",;\"'":
                    output.append(REDACTED)
                    self._token = character.lower()
                    self._mode = "value"
                    continue
                self._mode = "key"
            self._candidate += character
            while self._candidate and not any(
                key.startswith(self._candidate.lower()) for key in _INLINE_KEYS
            ):
                output.append(self._candidate[0])
                self._candidate = self._candidate[1:]
            if self._candidate.lower() in _INLINE_KEYS:
                output.append(self._candidate)
                self._candidate = ""
                self._mode = "after_key"
        return "".join(output)

    def finish(self) -> str:
        output = self._inline(REDACTED if self.pending else "")
        self.pending = ""
        output += self._candidate
        self._candidate = ""
        self._mode = "key"
        self._token = ""
        return output
