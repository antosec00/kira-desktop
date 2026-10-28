"""Contracts and validation for Semantic Codebase Indexing (AST, Call Graph & Hybrid RAG)."""

from __future__ import annotations

from typing import Any

from kira.domain.contracts import Json, KiraError

CODE_INDEX_METHODS = frozenset({
    "codeindex.status",
    "codeindex.rebuild",
    "codeindex.search",
    "codeindex.symbols",
    "codeindex.graph",
})
CODE_INDEX_TOOL_NAMES = frozenset({"kira.code_index"})
CODE_INDEX_ACTIONS = ("status", "search", "symbols", "graph", "rebuild")
SYMBOL_KINDS = frozenset({
    "function",
    "async_function",
    "class",
    "method",
    "interface",
    "struct",
    "component",
    "property",
    "signal",
    "section",
    "module",
    "chunk",
})


def _text(value: Any, name: str, maximum: int = 256, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or "\0" in value
        or len(value) > maximum
        or (not empty and not value.strip())
    ):
        raise KiraError("INVALID_ARGUMENT", f"{name} must be bounded text (at most {maximum} chars)")
    return value.strip()


def _int_bound(value: Any, name: str, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise KiraError("INVALID_ARGUMENT", f"{name} must be an integer between {low} and {high}")
    return value


def validate_code_index_action(action: Any, payload: Any) -> tuple[str, Json]:
    """Validate arguments for the kira.code_index client tool."""
    act = _text(action, "action", 32)
    if act not in CODE_INDEX_ACTIONS:
        raise KiraError("INVALID_ARGUMENT", f"Unsupported code_index action: {act}")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise KiraError("INVALID_ARGUMENT", "payload must be an object")

    if act == "status":
        if set(payload):
            raise KiraError("INVALID_ARGUMENT", "status takes an empty payload")
        return act, {}

    if act == "rebuild":
        extra = set(payload) - {"force"}
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown rebuild field: {sorted(extra)[0]}")
        force = payload.get("force", False)
        if not isinstance(force, bool):
            raise KiraError("INVALID_ARGUMENT", "force must be a boolean")
        return act, {"force": force}

    if act == "search":
        extra = set(payload) - {"query", "path", "kind", "language", "limit"}
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown search field: {sorted(extra)[0]}")
        query = _text(payload.get("query"), "query", 512)
        path = _text(payload.get("path", ""), "path", 512, empty=True)
        kind = _text(payload.get("kind", ""), "kind", 64, empty=True)
        if kind and kind not in SYMBOL_KINDS:
            raise KiraError("INVALID_ARGUMENT", f"Unsupported symbol kind: {kind}")
        language = _text(payload.get("language", ""), "language", 64, empty=True).lower()
        limit = _int_bound(payload.get("limit"), "limit", 8, 1, 25)
        return act, {
            "query": query,
            "path": path,
            "kind": kind,
            "language": language,
            "limit": limit,
        }

    if act == "symbols":
        extra = set(payload) - {"query", "path", "kind", "limit"}
        if extra:
            raise KiraError("INVALID_ARGUMENT", f"Unknown symbols field: {sorted(extra)[0]}")
        query = _text(payload.get("query", ""), "query", 256, empty=True)
        path = _text(payload.get("path", ""), "path", 512, empty=True)
        kind = _text(payload.get("kind", ""), "kind", 64, empty=True)
        if kind and kind not in SYMBOL_KINDS:
            raise KiraError("INVALID_ARGUMENT", f"Unsupported symbol kind: {kind}")
        limit = _int_bound(payload.get("limit"), "limit", 25, 1, 60)
        return act, {
            "query": query,
            "path": path,
            "kind": kind,
            "limit": limit,
        }

    # act == "graph"
    extra = set(payload) - {"symbol", "path", "limit"}
    if extra:
        raise KiraError("INVALID_ARGUMENT", f"Unknown graph field: {sorted(extra)[0]}")
    symbol = _text(payload.get("symbol", ""), "symbol", 256, empty=True)
    path = _text(payload.get("path", ""), "path", 512, empty=True)
    if not symbol and not path:
        raise KiraError("INVALID_ARGUMENT", "graph requires symbol or path")
    limit = _int_bound(payload.get("limit"), "limit", 25, 1, 60)
    return act, {"symbol": symbol, "path": path, "limit": limit}


def code_index_action_contract() -> Json:
    return {
        "status": {"payload": {}},
        "search": {
            "payload": {
                "query": "natural-language or identifier query (required)",
                "path": "optional relative path prefix filter",
                "kind": "optional symbol kind filter (function, class, method, component, ...)",
                "language": "optional language filter (python, typescript, qml, rust, go, ...)",
                "limit": "1..25 (default 8)",
            }
        },
        "symbols": {
            "payload": {
                "query": "optional symbol name or substring",
                "path": "optional relative file path or prefix",
                "kind": "optional symbol kind",
                "limit": "1..60 (default 25)",
            }
        },
        "graph": {
            "payload": {
                "symbol": "function/class/method name to inspect callers, callees and definitions",
                "path": "or relative file path to inspect imports, importers and defined symbols",
                "limit": "1..60 (default 25)",
            }
        },
        "rebuild": {"payload": {"force": "optional boolean (default false)"}},
    }


def code_index_tool_specs() -> list[Json]:
    return [
        {
            "type": "function",
            "name": "code_index",
            "description": (
                "Semantic codebase RAG, AST symbol explorer and dependency/call-graph analyzer for "
                "the current project. Actions: search (hybrid semantic + BM25 + AST chunk retrieval), "
                "symbols (list/filter functions, classes, methods, QML components), graph (find "
                "definitions, callers, callees, imports and importers for a symbol or file), "
                "status, rebuild."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "payload"],
                "properties": {
                    "action": {"type": "string", "enum": list(CODE_INDEX_ACTIONS)},
                    "payload": {
                        "type": "object",
                        "description": (
                            "Action parameters: for search use {query, path?, kind?, language?, limit?}; "
                            "for symbols use {query?, path?, kind?, limit?}; "
                            "for graph use {symbol?, path?, limit?}; "
                            "for status use {}; for rebuild use {force?}."
                        ),
                    },
                },
            },
        }
    ]
