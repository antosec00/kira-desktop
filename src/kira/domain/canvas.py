"""Contracts and validation for Interactive Canvas / Artifacts and Granular Diff Hunks."""

from __future__ import annotations

from typing import Any

from kira.domain.contracts import Json, KiraError

CANVAS_METHODS = frozenset({
    "canvas.list",
    "canvas.get",
    "canvas.create",
    "canvas.update",
    "canvas.restore",
    "canvas.delete",
    "canvas.diff",
    "canvas.apply_hunk",
    "canvas.workspace_hunks",
    "canvas.workspace_apply_hunk",
})
CANVAS_TOOL_NAMES = frozenset({"kira.canvas"})
CANVAS_ACTIONS = ("list", "get", "create", "update", "patch_lines", "diff")
ARTIFACT_KINDS = frozenset({"code", "markdown", "mermaid", "svg", "html"})
HUNK_DECISIONS = frozenset({"accept", "reject"})

MAX_ARTIFACT_CONTENT_BYTES = 512_000
MAX_ARTIFACTS_PER_PROJECT = 200
MAX_REVISIONS_PER_ARTIFACT = 200


def _text(value: Any, name: str, maximum: int = 256, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or "\0" in value
        or len(value) > maximum
        or (not empty and not value.strip())
    ):
        raise KiraError("INVALID_ARGUMENT", f"{name} must be bounded text (at most {maximum} chars)")
    return value.strip()


def _content_text(value: Any, name: str = "content", *, empty: bool = True) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise KiraError("INVALID_ARGUMENT", f"{name} must be valid text without NUL bytes")
    encoded = value.encode("utf-8", "replace")
    if len(encoded) > MAX_ARTIFACT_CONTENT_BYTES:
        raise KiraError(
            "INVALID_ARGUMENT",
            f"{name} exceeds maximum size ({MAX_ARTIFACT_CONTENT_BYTES} bytes)",
        )
    if not empty and not value.strip():
        raise KiraError("INVALID_ARGUMENT", f"{name} must not be empty")
    return value


def _int_bound(value: Any, name: str, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise KiraError("INVALID_ARGUMENT", f"{name} must be an integer between {low} and {high}")
    return value


def validate_canvas_action(action: Any, payload: Any) -> tuple[str, Json]:
    """Validate arguments for the kira.canvas client tool."""
    act = _text(action, "action", 32)
    if act not in CANVAS_ACTIONS:
        raise KiraError("INVALID_ARGUMENT", f"Unsupported canvas action: {act}")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise KiraError("INVALID_ARGUMENT", "payload must be an object")

    if act == "list":
        extra = set(payload) - {"kind", "limit"}
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown list field: {sorted(extra)[0]}")
        kind = _text(payload.get("kind", ""), "kind", 32, empty=True).lower()
        if kind and kind not in ARTIFACT_KINDS:
            raise KiraError("INVALID_ARGUMENT", f"Unsupported artifact kind: {kind}")
        limit = _int_bound(payload.get("limit"), "limit", 25, 1, 100)
        return act, {"kind": kind, "limit": limit}

    if act == "get":
        extra = set(payload) - {"artifact_id", "revision"}
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown get field: {sorted(extra)[0]}")
        artifact_id = _text(payload.get("artifact_id"), "artifact_id", 128)
        revision = payload.get("revision")
        rev_num = (
            _int_bound(revision, "revision", 0, 1, MAX_REVISIONS_PER_ARTIFACT)
            if revision is not None
            else None
        )
        return act, {"artifact_id": artifact_id, "revision": rev_num}

    if act == "create":
        extra = set(payload) - {"title", "kind", "language", "content", "summary"}
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown create field: {sorted(extra)[0]}")
        title = _text(payload.get("title"), "title", 160)
        kind = _text(payload.get("kind", "code"), "kind", 32).lower()
        if kind not in ARTIFACT_KINDS:
            raise KiraError("INVALID_ARGUMENT", f"Unsupported artifact kind: {kind}")
        language = _text(payload.get("language", kind), "language", 64, empty=True).lower()
        content = _content_text(payload.get("content"), "content", empty=False)
        summary = _text(payload.get("summary", "Initial version"), "summary", 240, empty=True)
        return act, {
            "title": title,
            "kind": kind,
            "language": language or kind,
            "content": content,
            "summary": summary or "Initial version",
        }

    if act == "update":
        extra = set(payload) - {"artifact_id", "title", "content", "summary"}
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown update field: {sorted(extra)[0]}")
        artifact_id = _text(payload.get("artifact_id"), "artifact_id", 128)
        title = _text(payload.get("title", ""), "title", 160, empty=True)
        content = _content_text(payload.get("content"), "content", empty=False)
        summary = _text(payload.get("summary", "Updated content"), "summary", 240, empty=True)
        return act, {
            "artifact_id": artifact_id,
            "title": title,
            "content": content,
            "summary": summary or "Updated content",
        }

    if act == "patch_lines":
        extra = set(payload) - {
            "artifact_id",
            "start_line",
            "end_line",
            "replacement",
            "summary",
        }
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown patch_lines field: {sorted(extra)[0]}")
        artifact_id = _text(payload.get("artifact_id"), "artifact_id", 128)
        start_line = _int_bound(payload.get("start_line"), "start_line", 1, 1, 100_000)
        end_line = _int_bound(payload.get("end_line"), "end_line", start_line, start_line, 100_000)
        replacement = _content_text(payload.get("replacement", ""), "replacement", empty=True)
        summary = _text(
            payload.get("summary", f"Updated lines {start_line}-{end_line}"),
            "summary",
            240,
            empty=True,
        )
        return act, {
            "artifact_id": artifact_id,
            "start_line": start_line,
            "end_line": end_line,
            "replacement": replacement,
            "summary": summary or f"Updated lines {start_line}-{end_line}",
        }

    # act == "diff"
    extra = set(payload) - {"artifact_id", "from_revision", "to_revision"}
    if extra:
        raise KiraError("INVALID_ARGUMENT", f"Unknown diff field: {sorted(extra)[0]}")
    artifact_id = _text(payload.get("artifact_id"), "artifact_id", 128)
    from_rev = payload.get("from_revision")
    to_rev = payload.get("to_revision")
    return act, {
        "artifact_id": artifact_id,
        "from_revision": (
            _int_bound(from_rev, "from_revision", 1, 1, MAX_REVISIONS_PER_ARTIFACT)
            if from_rev is not None
            else None
        ),
        "to_revision": (
            _int_bound(to_rev, "to_revision", 1, 1, MAX_REVISIONS_PER_ARTIFACT)
            if to_rev is not None
            else None
        ),
    }


def canvas_action_contract() -> Json:
    return {
        "list": {"payload": {"kind": "optional filter (code, markdown, mermaid, svg, html)", "limit": "1..100"}},
        "get": {"payload": {"artifact_id": "artifact ID", "revision": "optional revision number"}},
        "create": {
            "payload": {
                "title": "artifact title",
                "kind": "code | markdown | mermaid | svg | html",
                "language": "optional language tag (python, tsx, html, mermaid, ...)",
                "content": "full source content",
                "summary": "optional revision summary",
            }
        },
        "update": {
            "payload": {
                "artifact_id": "artifact ID",
                "title": "optional new title",
                "content": "new full source content",
                "summary": "optional revision summary",
            }
        },
        "patch_lines": {
            "payload": {
                "artifact_id": "artifact ID",
                "start_line": "1-indexed start line to replace (inclusive)",
                "end_line": "1-indexed end line to replace (inclusive)",
                "replacement": "new text for that line range",
                "summary": "optional summary of targeted inline edit",
            }
        },
        "diff": {
            "payload": {
                "artifact_id": "artifact ID",
                "from_revision": "optional base revision",
                "to_revision": "optional target revision",
            }
        },
    }


def canvas_tool_specs() -> list[Json]:
    return [
        {
            "type": "function",
            "name": "canvas",
            "description": (
                "Create, inspect, and collaboratively edit interactive Kira Canvas / Artifacts "
                "docked alongside the chat with live preview (Mermaid diagrams, SVG graphics, "
                "single-file HTML/CSS UI previews, Markdown documents, and source code). "
                "Supports surgical inline line-range rewrites via patch_lines when the user "
                "asks to modify selected lines."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "payload"],
                "properties": {
                    "action": {"type": "string", "enum": list(CANVAS_ACTIONS)},
                    "payload": {
                        "type": "object",
                        "description": (
                            "Action parameters: for create use {title, kind, content, language?, summary?}; "
                            "for patch_lines use {artifact_id, start_line, end_line, replacement, summary?}; "
                            "for update use {artifact_id, content, title?, summary?}; "
                            "for get use {artifact_id, revision?}; for diff use {artifact_id, from_revision?, to_revision?}; "
                            "for list use {kind?, limit?}."
                        ),
                    },
                },
            },
        }
    ]
