"""Conservative redaction at persistence and diagnostic boundaries."""

import os
import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?:token|password|secret|api[_-]?key|authorization|cookie)", re.I)
# An inline assignment names a credential with the last part of an identifier
# (TOKEN, GITHUB_TOKEN, db.password, x-api-key, SECRET_KEY, Set-Cookie) followed
# by ':' or '='. Plural or longer identifiers (tokens, max_tokens, token_url) are
# ordinary words. A quoted value is hidden whatever its length; an unquoted value
# needs eight characters, so short prose such as "token: 5" stays readable.
_CREDENTIAL_NAME = (r"(?:token|passw(?:or)?d|secret(?:[_-]?key)?"
                    r"|a(?:ccess[_-]?key|pi[_-]?key|uthorization)|cookie)")
_NAME_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
_INLINE = re.compile(
    r"(?<![A-Za-z0-9_.\-])"
    # A quoted name (JSON, YAML, Python) accepts only a quoted value, so a JSON
    # number or literal is never replaced by bare text.
    r"(?P<key>(?P<key_quote>[\"'])[A-Za-z0-9_.\-]*?" + _CREDENTIAL_NAME + r"(?P=key_quote)"
    r"|[A-Za-z0-9_.\-]*?" + _CREDENTIAL_NAME + r")"
    r"(?P<separator>\s*[:=]\s*)"
    # Quoted values end at their quote or line; a double quote also ends a
    # single-quoted value, which keeps the match inside a JSON string.
    r"(?:(?P<quote>[\"'])(?P<quoted>(?:\\[^\n]|(?!(?P=quote))[^\\\n\"])+)(?P<close>(?P=quote)?)"
    # A backslash belongs to an unquoted value only with the character it
    # escapes, so JSON-encoded text keeps valid escapes after replacement.
    r"|(?(key_quote)(?!)|(?:Bearer\s+)?(?!=)(?:[^\s,;\"'\\]|\\[^\s\"']){8,}))",
    re.IGNORECASE,
)
# Every inline match contains a credential name followed by an optional closing
# quote and ':' or '='. Searching for that anchor first keeps ordinary text fast.
_ANCHOR = re.compile(_CREDENTIAL_NAME + r"[\"']?\s*[:=]", re.IGNORECASE)
_CREDENTIAL_SUFFIX = re.compile(_CREDENTIAL_NAME + r"\Z", re.IGNORECASE)
_PRIVATE_FIELDS = {"thinking", "reasoning", "analysis"}
_QUOTES = "\"'"
_VALUE_DELIMITERS = ",;\"'"
MIN_UNQUOTED_VALUE = 8
_NAME_TAIL = 32
_MAX_BEARER_HOLD = 256
REDACTED = "[REDACTED]"
MAX_PENDING_CHARS = 65_536


def _inline_replacement(match: re.Match[str]) -> str:
    prefix = match["key"] + match["separator"]
    if match["quoted"] is not None:
        return prefix + match["quote"] + REDACTED + match["close"]
    return prefix + REDACTED


def _redact_inline(value: str) -> str:
    """Same result as _INLINE.sub, trying the full pattern only at anchored names."""
    pieces: list[str] = []
    done = 0
    for anchor in _ANCHOR.finditer(value):
        start = anchor.start()
        if start < done:
            continue
        while start > done and value[start - 1] in _NAME_CHARACTERS:
            start -= 1
        match = None
        if start > done and value[start - 1] in _QUOTES:
            match = _INLINE.match(value, start - 1)
        if match is None:
            match = _INLINE.match(value, start)
        if match is None:
            continue
        pieces += (value[done:match.start()], _inline_replacement(match))
        done = match.end()
    if not pieces:
        return value
    pieces.append(value[done:])
    return "".join(pieces)


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
        # owner -> (current set, preceding set)
        self._owner_values: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    def set_provider_values(self, values: tuple[str, ...], *, owner: str = "provider") -> None:
        """Protect each credential owner's current and previous sets without growing forever.

        Every owner (for example OpenAI direct or a DeepSeek account) keeps its
        own current and preceding set, so registering one provider never drops
        another provider's values. The service passes these in memory only. A
        rotation keeps its preceding values recognizable while late tool/stream
        output drains. Environment secrets retain their original lifetime. This
        is not a universal detector for arbitrary historical or third-party
        secrets.
        """
        current = tuple(sorted({value for value in values if value}, key=len, reverse=True))
        held = self._owner_values.get(owner, ((), ()))[0]
        if current == held:
            return
        self._owner_values[owner] = (current, held)
        provider = {value for sets in self._owner_values.values() for group in sets for value in group}
        self.values = tuple(sorted(set(self._base_values) | provider, key=len, reverse=True))
        self._prefixes = [(secret, _prefix_table(secret)) for secret in self.values]

    def text(self, value: str) -> str:
        for secret in self.values:
            value = value.replace(secret, REDACTED)
        return _redact_inline(value)

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

    Known values use shared prefix tables. Inline assignments follow the same
    rules as Redactor.text through a small state machine: a quoted value is
    hidden from its first character, an unquoted value once it reaches eight
    characters, and everything through its delimiter stays hidden. Unfinished
    known-value prefixes are hidden at completion/interruption, since a
    disconnected provider cannot disambiguate them. This is deliberately
    conservative, not universal secret detection.
    """

    def __init__(self, redactor: Redactor) -> None:
        self.redactor = redactor
        self.pending = ""
        self._reset()

    def _reset(self) -> None:
        self._mode = "text"
        self._previous = ""
        self._name = ""
        self._name_quote = ""
        self._quoted_key = False
        self._quote = ""
        self._escaped = False
        self._value = ""
        self._bearer = ""
        self._hidden = False

    @property
    def pending_chars(self) -> int:
        return len(self.pending) + len(self._value) + len(self._bearer)

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
            self._step(character, output)
            self._previous = character
        return "".join(output)

    def _step(self, character: str, output: list[str]) -> None:
        if self._mode == "quoted":
            if self._escaped:
                self._escaped = False
                if character != "\n":
                    return
            elif character == "\\" or character not in {self._quote, '"', "\n"}:
                if not self._hidden:
                    output.append(REDACTED)
                    self._hidden = True
                self._escaped = character == "\\"
                return
            # A closing quote, a line end or a double quote ends the value.
            self._mode, self._hidden = "text", False
        elif self._mode == "bare":
            if not character.isspace() and character not in _VALUE_DELIMITERS:
                if not self._hidden:
                    self._value += character
                    if len(self._value) >= MIN_UNQUOTED_VALUE:
                        output.append(REDACTED)
                        self._value = self._bearer = ""
                        self._hidden = True
                return
            if (character.isspace() and not self._hidden and not self._bearer
                    and self._value.lower() == "bearer"):
                # Hold the scheme: "Bearer <credential>" is redacted as a whole.
                self._bearer, self._value, self._mode = self._value + character, "", "value"
                return
            # A short value is an ordinary word and stays visible.
            output.append(self._bearer + self._value)
            self._bearer, self._value, self._hidden, self._mode = "", "", False, "text"
        elif self._mode == "value":
            if character.isspace():
                if not self._bearer:
                    output.append(character)
                    return
                if len(self._bearer) < _MAX_BEARER_HOLD:
                    self._bearer += character
                    return
            elif self._bearer:
                if character not in _VALUE_DELIMITERS and character != "=":
                    self._mode, self._value = "bare", character
                    return
            elif character in _QUOTES:
                output.append(character)
                self._mode, self._quote = "quoted", character
                return
            elif (not self._quoted_key and character not in _VALUE_DELIMITERS
                  and character != "="):
                self._mode, self._value = "bare", character
                return
            output.append(self._bearer)
            self._bearer, self._mode = "", "text"
        elif self._mode == "key":
            if character.isspace():
                output.append(character)
                return
            if character in ":=":
                output.append(character)
                self._mode = "value"
                return
            self._mode = "text"
        if character in _NAME_CHARACTERS:
            if not self._name:
                self._name_quote = self._previous if self._previous in _QUOTES else ""
            self._name = (self._name + character)[-_NAME_TAIL:]
            output.append(character)
            return
        name, self._name = self._name, ""
        if name and _CREDENTIAL_SUFFIX.search(name):
            if character in ":=":
                self._mode, self._quoted_key = "value", False
            elif character.isspace():
                self._mode, self._quoted_key = "key", False
            elif character in _QUOTES and character == self._name_quote:
                self._mode, self._quoted_key = "key", True
        output.append(character)

    def finish(self) -> str:
        output = self._inline(REDACTED if self.pending else "")
        self.pending = ""
        if not self._hidden:
            output += self._bearer + self._value
        self._reset()
        return output
