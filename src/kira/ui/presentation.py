"""Retranslatable application text; ordinary user/provider strings stay literal."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from kira.ui.i18n import tr as translate


class UiText(str):
    """A transient label keeps its source template across a language change."""

    source: str
    values: dict[str, Any]

    def __new__(cls, source: str, values: dict[str, Any] | None = None) -> UiText:
        values = values or {}
        instance = super().__new__(cls, translate(source, **refresh_ui(values)))
        instance.source = source
        instance.values = values
        return instance

    def __str__(self) -> str:
        return self

    def __add__(self, other: str) -> UiJoined:
        return UiJoined((self, other))

    def __radd__(self, other: str) -> UiJoined:
        return UiJoined((other, self))

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return UiText, (self.source, self.values)


class UiJoined(str):
    """A displayed tool summary can mix translated labels with literal output."""

    parts: tuple[str, ...]
    separator: str

    def __new__(cls, parts: Iterable[str], separator: str = "") -> UiJoined:
        pieces = tuple(parts)
        instance = super().__new__(cls, separator.join(str(refresh_ui(part)) for part in pieces))
        instance.parts = pieces
        instance.separator = separator
        return instance

    def __str__(self) -> str:
        return self

    def __add__(self, other: str) -> UiJoined:
        return UiJoined((self, other))

    def __radd__(self, other: str) -> UiJoined:
        return UiJoined((other, self))

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return UiJoined, (self.parts, self.separator)


def _prefix(value: str, limit: int) -> str:
    if isinstance(value, UiLimited):
        return _prefix(value.prefix, limit)
    if len(value) <= limit:
        return value
    if isinstance(value, UiJoined):
        parts = []
        remaining = limit
        for index, part in enumerate(value.parts):
            for piece in ((value.separator, part) if index else (part,)):
                prefix = _prefix(piece, remaining)
                parts.append(prefix)
                remaining -= len(prefix)
                if not remaining:
                    return UiJoined(parts)
        return UiJoined(parts)
    return value[:limit]


class UiLimited(str):
    """Keep only a bounded prefix, with a retranslatable truncation notice."""

    prefix: str
    limit: int

    def __new__(cls, value: str, limit: int) -> UiLimited:
        prefix = _prefix(refresh_ui(value), limit)
        notice = translate("\n[… visualizzazione limitata; esporta la cronologia completa]")
        instance = super().__new__(cls, str(prefix) + notice)
        instance.prefix, instance.limit = prefix, limit
        return instance

    def __str__(self) -> str:
        return self

    def __add__(self, other: str) -> UiJoined:
        return UiJoined((self, other))

    def __radd__(self, other: str) -> UiJoined:
        return UiJoined((other, self))

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return UiLimited, (self.prefix, self.limit)


def limit_ui(value: str, limit: int) -> str:
    if isinstance(value, UiLimited):
        return UiLimited(value.prefix, limit)
    return UiLimited(value, limit) if len(value) > limit else value


def tr(source: str, /, **values: Any) -> UiText:
    """Translate the application template before substituting literal parameters."""
    return UiText(source, values)


def join_ui(parts: Iterable[str], separator: str = "") -> UiJoined:
    return UiJoined(parts, separator)


def refresh_ui(value: Any) -> Any:
    """Refresh marked labels only; never infer that ordinary text is translatable."""
    if isinstance(value, UiText):
        return UiText(value.source, value.values)
    if isinstance(value, UiLimited):
        return UiLimited(value.prefix, value.limit)
    if isinstance(value, UiJoined):
        return UiJoined(value.parts, value.separator)
    if isinstance(value, dict):
        return {key: refresh_ui(item) for key, item in value.items()}
    if isinstance(value, list):
        return [refresh_ui(item) for item in value]
    if isinstance(value, tuple):
        return tuple(refresh_ui(item) for item in value)
    return value
