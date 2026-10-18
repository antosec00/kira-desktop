"""Bounded, plain-text presentation of recorded JSON event payloads.

Readable output is JSON-like, not a serialization: ``|`` introduces a literal
text block, whose end is marked by lower indentation rather than an appended
comma. The caller retains the recorded JSON separately for exact copying.
"""

import json
import re
from typing import Any

MAX_SOURCE = 131_072
MAX_OUTPUT = 131_072
MAX_DEPTH = 24
MAX_NODES = 4096
MAX_EMBEDDED = 64

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


class _Number(str):
    """Keep JSON number spelling without floating-point conversion."""


class _Invalid(ValueError):
    pass


class _Full(Exception):
    pass


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid("Duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise _Invalid("Non-JSON constant")


class _Formatter:
    def __init__(self) -> None:
        self.nodes = 0
        self.embedded = 0
        self.parts: list[str] = []
        self.length = 0

    def parse(self, source: str, depth: int = 0) -> Any:
        value = json.loads(source, object_pairs_hook=_object, parse_int=_Number,
                           parse_float=_Number, parse_constant=_constant)
        if not isinstance(value, (dict, list)):
            raise _Invalid("Expected object or array")
        pending = [(value, depth)]
        count = 0
        while pending:
            item, level = pending.pop()
            count += 1
            if level > MAX_DEPTH or self.nodes + count > MAX_NODES:
                raise _Invalid("Presentation budget")
            if isinstance(item, dict):
                pending.extend((child, level + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, level + 1) for child in item)
        self.nodes += count
        return value

    def write(self, text: str) -> None:
        available = MAX_OUTPUT - self.length
        if len(text) > available:
            self.parts.append(text[:available])
            self.length = MAX_OUTPUT
            raise _Full
        self.parts.append(text)
        self.length += len(text)

    def embedded_json(self, line: str, depth: int) -> Any:
        stripped = line.strip()
        if (self.embedded >= MAX_EMBEDDED or not stripped
                or (stripped[0], stripped[-1]) not in (("{", "}"), ("[", "]"))):
            return None
        self.embedded += 1
        try:
            return self.parse(stripped, depth)
        except (ValueError, RecursionError):
            return None

    def text_block(self, value: str, depth: int, parsed: Any = None) -> None:
        self.write("|\n")
        indent = "  " * (depth + 1)
        if parsed is not None:
            self.write(indent)
            self.value(parsed, depth + 1)
            return
        fence = ""
        for index, line in enumerate(value.split("\n")):
            if index:
                self.write("\n")
            self.write(indent)
            marker = _FENCE.match(line)
            if marker:
                token = marker.group(1)
                if not fence:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    if not line[marker.end():].strip():
                        fence = ""
                self.write(line)
                continue
            # Indented code and fenced code remain literal, even when valid JSON.
            nested = None if fence or line.startswith(("    ", "\t")) else (
                self.embedded_json(line, depth + 1)
            )
            if nested is None:
                self.write(line)
            else:
                leading = line[:len(line) - len(line.lstrip())]
                trailing = line[len(line.rstrip()):]
                self.write(leading)
                self.value(nested, depth + 1)
                self.write(trailing)

    def value(self, value: Any, depth: int = 0) -> bool:
        """Render a value and report whether its last line belongs to a text block."""
        indent = "  " * depth
        if isinstance(value, (dict, list)):
            is_object = isinstance(value, dict)
            opening, closing = ("{", "}") if is_object else ("[", "]")
            self.write(opening)
            items = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
            previous_block = False
            for index, (key, child) in enumerate(items):
                # A separator on a literal line would appear to be provider text.
                self.write((",\n" if index and not previous_block else "\n") + indent + "  ")
                if is_object:
                    self.write(json.dumps(key, ensure_ascii=False) + ": ")
                previous_block = self.value(child, depth + 1)
            if items:
                self.write("\n" + indent)
            self.write(closing)
        elif isinstance(value, _Number):
            self.write(value)
        elif isinstance(value, str):
            if "\n" in value:
                self.text_block(value, depth)
                return True
            else:
                nested = self.embedded_json(value, depth + 1)
                if nested is not None:
                    self.text_block(value, depth, nested)
                    return True
                else:
                    self.write(json.dumps(value, ensure_ascii=False))
        else:
            self.write(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return False


def readable_payload(source_json: str) -> str:
    """Format only complete, bounded JSON; keep malformed display input literal.

    Bounds use characters, matching the transcript model. JSON strings are
    decoded once per explicitly parsed object/array, never with unicode-escape
    decoding or textual escape replacement. Excess output ends in ``\n…``.
    """
    if len(source_json) > MAX_SOURCE:
        return source_json[:MAX_OUTPUT - 2] + "\n…"
    formatter = _Formatter()
    try:
        value = formatter.parse(source_json)
    except (ValueError, RecursionError):
        return source_json
    try:
        formatter.value(value)
    except _Full:
        return "".join(formatter.parts)[:MAX_OUTPUT - 2] + "\n…"
    return "".join(formatter.parts)
