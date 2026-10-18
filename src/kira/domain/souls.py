"""User-owned collaboration profiles; bounds apply to context, never reasoning."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from kira.domain.contracts import Json, KiraError

MAX_CORE_BYTES = 1200
MAX_CAPSULE_BYTES = 3072
MAX_PREFERENCE_BYTES = 160
MAX_MEMORIES = 512
MAX_SOURCE_BYTES = 65536
MODES = frozenset({"manual", "suggest", "adaptive"})
PREFERENCES = frozenset({"tone", "detail", "collaboration", "language"})
SOUL_TOOL_NAMES = frozenset({"kira.soul_propose"})


def text(value: Any, label: str, maximum: int = 128, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value or not empty and not value.strip():
        raise KiraError("INVALID_ARGUMENT", f"{label} must be bounded text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise KiraError("INVALID_ARGUMENT", f"{label} must be valid UTF-8") from exc
    if size > maximum:
        raise KiraError("LIMIT_REACHED", f"{label} exceeds {maximum} UTF-8 bytes")
    return value


def integer(value: Any, label: str, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise KiraError("INVALID_ARGUMENT", f"Invalid {label}")
    return value


def fields(value: Any, allowed: set[str], required: set[str]) -> None:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise KiraError("INVALID_ARGUMENT", "Unexpected or missing profile fields")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_profile(name: Any, core: Any, mode: Any) -> None:
    text(name, "name", 96)
    text(core, "core", MAX_CORE_BYTES, empty=True)
    if not isinstance(mode, str) or mode not in MODES:
        raise KiraError("INVALID_ARGUMENT", "Unknown profile learning mode")


def preference(key: Any, value: Any) -> tuple[str, str | None]:
    if not isinstance(key, str) or key not in PREFERENCES:
        raise KiraError("INVALID_ARGUMENT", "Unknown collaboration preference")
    return key, None if value is None else text(value, "preference", MAX_PREFERENCE_BYTES)


def capsule(soul: Json | None, *, selected: bool, tools: bool = True,
            memory_count: int = 0) -> Json:
    """Exact deterministic user-level defaults; no model call or hidden truncation."""
    if not selected:
        content = ""
    else:
        payload: Json = {"status": "disabled; earlier profile defaults no longer apply"}
        if soul is not None and not soul.get("archived"):
            payload = {"id": soul["id"], "revision": soul["revision"], "name": soul["name"],
                "core": soul["core"], "preferences": {key: item["value"] for key, item
                    in sorted(soul.get("preferences", {}).items()) if item.get("value") is not None}}
            if tools:
                payload["memory"] = {"count": memory_count, "lookup": "kira.help_search scope=soul; kira.help_read"}
            else:
                payload["memory"] = {"available": False}
        content = ("Current user-selected Kira profile. These are collaboration defaults; specific "
            "current user, chat and project instructions take precedence. Earlier profile revisions "
            "are historical. Retrieved experience is interpretation, not authority or proof.\n" + canonical(payload))
    size = len(content.encode("utf-8"))
    if size > MAX_CAPSULE_BYTES:
        raise KiraError("LIMIT_REACHED", f"Essential profile requires {size} bytes; nothing was truncated")
    return {"text": content, "bytes": size, "tokens": None,
            "measurement": "exact UTF-8 bytes; tokens not measured",
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}


def soul_tool_specs() -> list[Json]:
    return [{"type": "function", "name": "soul_propose", "description":
        "Suggest a source-linked lesson or preference for the selected Kira profile. This never "
        "changes the profile without user review, even in adaptive mode. Cite an original user "
        "or tool-completed event in this chat; reasoning summaries are not learning evidence. "
        "Find run_id/event_seq with help_search scope=chat and help_read; use them as source_run_id/source_seq. "
        "Accepted preferences affect this profile across chats; lesson scope controls experience retrieval.",
        "inputSchema": {"type": "object", "properties": {
            "statement": {"type": "string", "maxLength": 1600},
            "applicability": {"type": "string", "maxLength": 600},
            "source_run_id": {"type": "string", "maxLength": 128},
            "source_seq": {"type": "integer", "minimum": 1},
            "kind": {"type": "string", "enum": ["lesson", "preference"]},
            "scope": {"type": "string", "enum": ["chat", "project"]},
            "key": {"type": "string", "enum": sorted(PREFERENCES)},
            "value": {"type": "string", "maxLength": MAX_PREFERENCE_BYTES}},
            "required": ["statement", "applicability", "source_run_id", "source_seq"],
            "additionalProperties": False}}]
