"""Service-owned Semantic Codebase Indexing (AST Symbol Graph, Call Graph & Hybrid Vector RAG).

Each project owns an isolated SQLite index under ``$KIRA_HOME/data/code_index/<project_id>/index.db``.
File scanning, AST parsing, and vector computation run off the asyncio event loop via
``asyncio.to_thread`` under a per-project lock. Secret files and excluded directories are
never indexed, and all returned excerpts pass through the service Redactor.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import math
import os
import re
import sqlite3
import stat
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from kira.domain.code_index import (
    CODE_INDEX_METHODS,
    CODE_INDEX_TOOL_NAMES,
    code_index_action_contract,
    code_index_tool_specs,
    validate_code_index_action,
)
from kira.domain.contracts import Json, KiraError
from kira.service.desktop import private_directory
from kira.service.project_context import ProjectChats
from kira.service.storage import now
from kira.service.tool_policy import kira_tools_enabled
from kira.service.workbench import EXCLUDED_DIRS, _private

METHODS = CODE_INDEX_METHODS
MAX_INDEX_FILES = 5_000
MAX_INDEX_FILE_BYTES = 512_000
MAX_EXCERPT_CHARS = 1_400
MAX_BOOTSTRAP_CHARS = 2_600
EMBEDDING_DIM = 256

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".qml": "qml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
}

BRACE_LANGUAGES = frozenset({
    "typescript", "javascript", "rust", "go", "c", "cpp", "java", "kotlin", "csharp", "php",
})

STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "were",
    "have", "has", "had", "not", "but", "what", "when", "where", "which", "who", "how",
    "can", "could", "should", "would", "will", "all", "any", "some", "none", "true", "false",
    "null", "self", "cls", "return", "def", "class", "import", "const", "let", "var", "function",
    "async", "await", "public", "private", "protected", "static", "void", "int", "str", "bool",
    "che", "per", "con", "una", "uno", "gli", "delle", "dello", "della", "dei", "degli",
    "nel", "nella", "nei", "nelle", "sul", "sulla", "come", "dove", "quando", "quale", "quali",
    "sono", "fare", "fatto", "puo", "anche", "solo", "tutto", "tutti", "questo", "questa",
})

CALL_IGNORE_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "sizeof", "typeof", "delete",
    "new", "super", "this", "self", "cls", "match", "case", "else", "elif", "except",
    "with", "assert", "yield", "await", "print", "len", "str", "int", "float", "bool",
    "dict", "list", "set", "tuple", "range", "isinstance", "getattr", "setattr", "hasattr",
})

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,63}|[0-9]{2,16}")
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{1,63})\s*\(")

_BRACE_SYMBOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:public\s+|private\s+|internal\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("interface", re.compile(r"^\s*(?:export\s+)?(?:public\s+)?(?:interface|trait)\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("struct", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:type\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("struct", re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+struct\b")),
    ("interface", re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+interface\b")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*[<(]")),
    ("function", re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>")),
    ("method", re.compile(r"^\s*(?:public|private|protected|internal|static|async|override|virtual|final|\s)+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?::\s*[^{]+)?\{")),
)

_IMPORT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"),
    re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]"),
    re.compile(r"^\s*import\s+([A-Za-z0-9_.]+)"),
    re.compile(r"^\s*use\s+([A-Za-z0-9_:]+)"),
    re.compile(r"^\s*#include\s+[<\"]([^>\"]+)[>\"]"),
)

_QML_COMPONENT_RE = re.compile(r"^(\s*)([A-Z][A-Za-z0-9_.]*)\s*\{")
_QML_ID_RE = re.compile(r"^\s*id\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\b")
_QML_FUNC_RE = re.compile(r"^(\s*)function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
_QML_PROP_RE = re.compile(r"^\s*(?:readonly\s+|default\s+|required\s+)?property\s+([A-Za-z0-9_.<>]+)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_QML_SIGNAL_RE = re.compile(r"^\s*signal\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?")


def split_identifier(text: str) -> list[str]:
    """Split camelCase, PascalCase, snake_case, and dot/path identifiers into normalized tokens."""
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        lower = raw.lower()
        if len(lower) >= 2 and lower not in STOPWORDS:
            out.append(lower)
        parts = _CAMEL_RE.sub("_", raw).split("_")
        if len(parts) > 1:
            for part in parts:
                p = part.lower()
                if len(p) >= 2 and p not in STOPWORDS and p != lower:
                    out.append(p)
    return out


def fts_tokenize_text(text: str) -> str:
    """Produce a space-separated string of identifier & sub-identifier tokens for FTS5."""
    tokens = split_identifier(text)
    return " ".join(tokens[:1500])


def compute_semantic_vector(
    *,
    name: str,
    qualified_name: str,
    kind: str,
    path: str,
    signature: str,
    docstring: str,
    body: str,
    calls: list[str] | None = None,
) -> bytes:
    """Compute a 256-dim L2-normalized semantic vector using weighted AST role features."""
    weights: defaultdict[str, float] = defaultdict(float)

    for tok in split_identifier(name):
        weights[f"sym:{tok}"] += 6.0
        weights[tok] += 5.0
    for tok in split_identifier(qualified_name):
        weights[tok] += 3.5
    if kind:
        weights[f"kind:{kind.lower()}"] += 2.5

    path_stem = Path(path).stem
    for tok in split_identifier(path_stem):
        weights[f"file:{tok}"] += 3.0
        weights[tok] += 2.5
    for part in Path(path).parts[:-1]:
        for tok in split_identifier(part):
            weights[tok] += 1.5

    for tok in split_identifier(signature):
        weights[tok] += 2.5

    doc_tokens = split_identifier(docstring)
    for tok in doc_tokens[:200]:
        weights[tok] += 2.2
    for i in range(min(len(doc_tokens) - 1, 120)):
        weights[f"bi:{doc_tokens[i]}_{doc_tokens[i + 1]}"] += 1.8

    body_tokens = split_identifier(body)
    body_counts = Counter(body_tokens[:800])
    for tok, count in body_counts.items():
        weights[tok] += 1.0 + math.log1p(count)

    if calls:
        for callee in calls[:40]:
            for tok in split_identifier(callee):
                weights[f"call:{tok}"] += 2.0
                weights[tok] += 1.5

    vec = [0.0] * EMBEDDING_DIM
    if not weights:
        return struct.pack(f"<{EMBEDDING_DIM}f", *vec)

    for feat, weight in weights.items():
        digest = hashlib.blake2b(feat.encode("utf-8", "ignore"), digest_size=8).digest()
        h1, h2 = struct.unpack("<II", digest)
        idx1 = h1 % EMBEDDING_DIM
        sign1 = 1.0 if (h1 & 0x80000000) == 0 else -1.0
        idx2 = h2 % EMBEDDING_DIM
        sign2 = 1.0 if (h2 & 0x80000000) == 0 else -1.0
        vec[idx1] += sign1 * weight
        vec[idx2] += sign2 * (weight * 0.5)

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-9:
        inv = 1.0 / norm
        vec = [v * inv for v in vec]
    return struct.pack(f"<{EMBEDDING_DIM}f", *vec)


def compute_query_vector(query: str) -> tuple[float, ...]:
    """Compute a 256-dim L2-normalized query vector compatible with chunk semantic vectors."""
    tokens = split_identifier(query)
    weights: defaultdict[str, float] = defaultdict(float)
    for tok in tokens:
        weights[tok] += 4.0
        weights[f"sym:{tok}"] += 2.5
        weights[f"file:{tok}"] += 1.5
    for i in range(len(tokens) - 1):
        weights[f"bi:{tokens[i]}_{tokens[i + 1]}"] += 2.5

    vec = [0.0] * EMBEDDING_DIM
    for feat, weight in weights.items():
        digest = hashlib.blake2b(feat.encode("utf-8", "ignore"), digest_size=8).digest()
        h1, h2 = struct.unpack("<II", digest)
        idx1 = h1 % EMBEDDING_DIM
        sign1 = 1.0 if (h1 & 0x80000000) == 0 else -1.0
        idx2 = h2 % EMBEDDING_DIM
        sign2 = 1.0 if (h2 & 0x80000000) == 0 else -1.0
        vec[idx1] += sign1 * weight
        vec[idx2] += sign2 * (weight * 0.5)

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-9:
        inv = 1.0 / norm
        vec = [v * inv for v in vec]
    return tuple(vec)


def cosine_similarity(query_vec: tuple[float, ...], blob: bytes | None) -> float:
    if not blob or len(blob) != EMBEDDING_DIM * 4:
        return 0.0
    chunk_vec = struct.unpack(f"<{EMBEDDING_DIM}f", blob)
    dot = sum(a * b for a, b in zip(query_vec, chunk_vec, strict=False))
    return max(0.0, min(1.0, dot))


def _clip_excerpt(lines: list[str], start_line: int, end_line: int, maximum: int = MAX_EXCERPT_CHARS) -> str:
    lo = max(1, start_line)
    hi = min(len(lines), max(lo, end_line))
    snippet = "\n".join(lines[lo - 1 : min(hi, lo + 45)])
    if len(snippet) > maximum:
        return snippet[: maximum - 1] + "…"
    return snippet


def _extract_python_calls(node: ast.AST) -> list[tuple[str, int]]:
    """Extract called function/method names inside an AST node without descending into nested defs."""
    calls: list[tuple[str, int]] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        curr = stack.pop()
        if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(curr, ast.Call):
            callee = ""
            if isinstance(curr.func, ast.Name):
                callee = curr.func.id
            elif isinstance(curr.func, ast.Attribute):
                callee = curr.func.attr
            if callee and callee not in CALL_IGNORE_KEYWORDS:
                calls.append((callee, getattr(curr, "lineno", 1)))
        stack.extend(ast.iter_child_nodes(curr))
    return calls


def _format_py_signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases[:4]]
        base_str = f"({', '.join(bases)})" if bases else ""
        return f"class {node.name}{base_str}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args_str = ast.unparse(node.args)
    except Exception:
        args_str = "..."
    if len(args_str) > 140:
        args_str = args_str[:137] + "..."
    ret = ""
    if node.returns is not None:
        with contextlib.suppress(Exception):
            ret = f" -> {ast.unparse(node.returns)}"
    return f"{prefix} {node.name}({args_str}){ret}"


def _parse_python_ast(rel_path: str, source: str, lines: list[str]) -> tuple[list[Json], list[Json], list[Json]]:
    """Parse Python source into symbols, imports, and call edges using stdlib ast."""
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return _parse_sections(rel_path, "python", lines), [], []

    symbols: list[Json] = []
    imports: list[Json] = []
    calls: list[Json] = []

    for top in tree.body:
        if isinstance(top, ast.Import):
            for alias in top.names:
                imports.append({
                    "imported_module": alias.name,
                    "imported_name": alias.asname or alias.name.split(".")[-1],
                    "line": getattr(top, "lineno", 1),
                })
        elif isinstance(top, ast.ImportFrom):
            mod = ("." * (top.level or 0)) + (top.module or "")
            for alias in top.names:
                imports.append({
                    "imported_module": mod or alias.name,
                    "imported_name": alias.name,
                    "line": getattr(top, "lineno", 1),
                })

    def visit_node(node: ast.AST, parent_name: str = "") -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            qualified = f"{parent_name}.{name}" if parent_name else name
            if isinstance(node, ast.ClassDef):
                kind = "class"
            elif parent_name:
                kind = "method"
            elif isinstance(node, ast.AsyncFunctionDef):
                kind = "async_function"
            else:
                kind = "function"
            start_line = getattr(node, "lineno", 1)
            end_line = getattr(node, "end_lineno", start_line) or start_line
            signature = _format_py_signature(node)
            doc = (ast.get_docstring(node) or "").strip()
            if len(doc) > 500:
                doc = doc[:499] + "…"
            excerpt = _clip_excerpt(lines, start_line, end_line)
            node_calls = _extract_python_calls(node)
            callee_names = []
            seen_callees: set[tuple[str, int]] = set()
            for callee, call_line in node_calls:
                if (callee, call_line) not in seen_callees:
                    seen_callees.add((callee, call_line))
                    callee_names.append(callee)
                    calls.append({
                        "caller_symbol": name,
                        "caller_qualified": qualified,
                        "callee_name": callee,
                        "line": call_line,
                    })
            symbols.append({
                "name": name,
                "qualified_name": qualified,
                "kind": kind,
                "parent_symbol": parent_name,
                "signature": signature,
                "docstring": doc,
                "start_line": start_line,
                "end_line": end_line,
                "excerpt": excerpt,
                "callees": callee_names[:40],
            })
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    visit_node(child, qualified)
            else:
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        visit_node(child, qualified)

    for stmt in tree.body:
        visit_node(stmt)

    if not symbols and lines:
        mod_doc = (ast.get_docstring(tree) or "").strip()[:500]
        symbols.append({
            "name": Path(rel_path).stem,
            "qualified_name": Path(rel_path).stem,
            "kind": "module",
            "parent_symbol": "",
            "signature": f"module {rel_path}",
            "docstring": mod_doc,
            "start_line": 1,
            "end_line": len(lines),
            "excerpt": _clip_excerpt(lines, 1, min(len(lines), 45)),
            "callees": [],
        })
    return symbols, imports, calls


def _find_block_end(lines: list[str], start_idx: int) -> int:
    """Find the 1-indexed closing line of a brace-delimited block starting at start_idx (0-indexed)."""
    depth = 0
    opened = False
    for idx in range(start_idx, min(len(lines), start_idx + 600)):
        line = lines[idx]
        for ch in line:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth <= 0:
                    return idx + 1
    return min(len(lines), start_idx + 35)


def _preceding_comment(lines: list[str], idx: int) -> str:
    """Extract preceding doc comments (//, ///, #, /* ... */) immediately above line idx (0-indexed)."""
    collected: list[str] = []
    cur = idx - 1
    while cur >= 0 and len(collected) < 8:
        stripped = lines[cur].strip()
        if not stripped:
            if collected:
                break
            cur -= 1
            continue
        if stripped.startswith(("///", "//", "#")):
            collected.append(stripped.lstrip("/# ").strip())
            cur -= 1
        elif stripped.startswith(("*", "/*")) or stripped.endswith("*/"):
            cleaned = stripped.strip("/* ").strip()
            if cleaned:
                collected.append(cleaned)
            if stripped.startswith("/*"):
                break
            cur -= 1
        else:
            break
    collected.reverse()
    doc = " ".join(collected).strip()
    return doc[:500]


def _extract_regex_calls(lines: list[str], start_line: int, end_line: int, self_name: str) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for line_no in range(max(1, start_line), min(len(lines), end_line) + 1):
        line = lines[line_no - 1]
        for match in _CALL_RE.finditer(line):
            callee = match.group(1)
            if callee in CALL_IGNORE_KEYWORDS or (line_no == start_line and callee == self_name):
                continue
            if (callee, line_no) not in seen:
                seen.add((callee, line_no))
                calls.append((callee, line_no))
    return calls


def _parse_qml(rel_path: str, lines: list[str]) -> tuple[list[Json], list[Json], list[Json]]:
    """Extract QML components, functions, properties, signals, imports and call graph."""
    symbols: list[Json] = []
    imports: list[Json] = []
    calls: list[Json] = []
    root_name = Path(rel_path).stem

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import "):
            parts = stripped[7:].strip().split()
            if parts:
                imports.append({
                    "imported_module": parts[0].strip("'\""),
                    "imported_name": parts[-1].strip("'\"Provides"),
                    "line": idx + 1,
                })
            continue

        func_match = _QML_FUNC_RE.match(line)
        if func_match:
            fname = func_match.group(2)
            fargs = func_match.group(3).strip()
            start_line = idx + 1
            end_line = _find_block_end(lines, idx)
            qualified = f"{root_name}.{fname}"
            fn_calls = _extract_regex_calls(lines, start_line, end_line, fname)
            for callee, call_line in fn_calls:
                calls.append({
                    "caller_symbol": fname,
                    "caller_qualified": qualified,
                    "callee_name": callee,
                    "line": call_line,
                })
            symbols.append({
                "name": fname,
                "qualified_name": qualified,
                "kind": "function",
                "parent_symbol": root_name,
                "signature": f"function {fname}({fargs})",
                "docstring": _preceding_comment(lines, idx),
                "start_line": start_line,
                "end_line": end_line,
                "excerpt": _clip_excerpt(lines, start_line, end_line),
                "callees": [c for c, _ in fn_calls[:30]],
            })
            continue

        prop_match = _QML_PROP_RE.match(line)
        if prop_match:
            ptype, pname = prop_match.group(1), prop_match.group(2)
            symbols.append({
                "name": pname,
                "qualified_name": f"{root_name}.{pname}",
                "kind": "property",
                "parent_symbol": root_name,
                "signature": f"property {ptype} {pname}",
                "docstring": _preceding_comment(lines, idx),
                "start_line": idx + 1,
                "end_line": idx + 1,
                "excerpt": line.strip()[:300],
                "callees": [],
            })
            continue

        sig_match = _QML_SIGNAL_RE.match(line)
        if sig_match:
            sname = sig_match.group(1)
            symbols.append({
                "name": sname,
                "qualified_name": f"{root_name}.{sname}",
                "kind": "signal",
                "parent_symbol": root_name,
                "signature": stripped.rstrip("{;").strip(),
                "docstring": _preceding_comment(lines, idx),
                "start_line": idx + 1,
                "end_line": idx + 1,
                "excerpt": stripped[:300],
                "callees": [],
            })
            continue

        comp_match = _QML_COMPONENT_RE.match(line)
        if comp_match:
            indent = len(comp_match.group(1))
            comp_type = comp_match.group(2)
            comp_id = ""
            for look in range(idx + 1, min(len(lines), idx + 8)):
                id_match = _QML_ID_RE.match(lines[look])
                if id_match:
                    comp_id = id_match.group(1)
                    break
                if "{" in lines[look] or "}" in lines[look]:
                    break
            if indent == 0 or comp_id:
                cname = comp_id or root_name
                start_line = idx + 1
                end_line = _find_block_end(lines, idx)
                symbols.append({
                    "name": cname,
                    "qualified_name": f"{root_name}.{cname}" if indent > 0 else root_name,
                    "kind": "component",
                    "parent_symbol": root_name if indent > 0 else "",
                    "signature": f"{comp_type} {{ id: {comp_id} }}" if comp_id else f"{comp_type} ({root_name})",
                    "docstring": _preceding_comment(lines, idx),
                    "start_line": start_line,
                    "end_line": end_line,
                    "excerpt": _clip_excerpt(lines, start_line, min(end_line, start_line + 35)),
                    "callees": [],
                })

    if not symbols and lines:
        symbols.extend(_parse_sections(rel_path, "qml", lines))
    return symbols, imports, calls


def _parse_brace_language(
    rel_path: str, language: str, lines: list[str]
) -> tuple[list[Json], list[Json], list[Json]]:
    """Structural AST/block parser for TS/JS/Rust/Go/C/C++/Java/Kotlin/C#."""
    symbols: list[Json] = []
    imports: list[Json] = []
    calls: list[Json] = []
    current_class = ""
    class_end_line = 0

    for idx, line in enumerate(lines):
        line_no = idx + 1
        if current_class and line_no > class_end_line:
            current_class = ""

        for imp_re in _IMPORT_PATTERNS:
            m = imp_re.match(line)
            if m:
                target = m.group(1).strip()
                imports.append({
                    "imported_module": target,
                    "imported_name": Path(target).stem or target.split(":")[-1].split(".")[-1],
                    "line": line_no,
                })
                break

        for kind, pattern in _BRACE_SYMBOL_PATTERNS:
            m = pattern.match(line)
            if not m:
                continue
            name = m.group(1)
            if name in CALL_IGNORE_KEYWORDS:
                continue
            end_line = _find_block_end(lines, idx) if "{" in line or (
                idx + 1 < len(lines) and "{" in lines[idx + 1]
            ) else min(len(lines), line_no + 25)
            parent = current_class if kind not in {"class", "interface", "struct"} else ""
            actual_kind = "method" if parent and kind == "function" else kind
            qualified = f"{parent}.{name}" if parent else name
            if kind in {"class", "interface", "struct"}:
                current_class = name
                class_end_line = end_line
            sig = line.strip().rstrip("{").strip()
            if len(sig) > 160:
                sig = sig[:157] + "..."
            fn_calls = _extract_regex_calls(lines, line_no, end_line, name)
            for callee, call_line in fn_calls:
                calls.append({
                    "caller_symbol": name,
                    "caller_qualified": qualified,
                    "callee_name": callee,
                    "line": call_line,
                })
            symbols.append({
                "name": name,
                "qualified_name": qualified,
                "kind": actual_kind,
                "parent_symbol": parent,
                "signature": sig,
                "docstring": _preceding_comment(lines, idx),
                "start_line": line_no,
                "end_line": end_line,
                "excerpt": _clip_excerpt(lines, line_no, end_line),
                "callees": [c for c, _ in fn_calls[:30]],
            })
            break

    if not symbols and lines:
        symbols.extend(_parse_sections(rel_path, language, lines))
    return symbols, imports, calls


def _parse_sections(rel_path: str, language: str, lines: list[str]) -> list[Json]:
    """Chunk Markdown or generic files by headings or bounded logical windows."""
    if not lines:
        return []
    symbols: list[Json] = []
    if language == "markdown":
        headings: list[tuple[int, str]] = []
        for idx, line in enumerate(lines):
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                if title:
                    headings.append((idx + 1, title[:120]))
        if headings:
            for i, (start_line, title) in enumerate(headings):
                end_line = headings[i + 1][0] - 1 if i + 1 < len(headings) else len(lines)
                symbols.append({
                    "name": title,
                    "qualified_name": f"{Path(rel_path).stem}:{title}",
                    "kind": "section",
                    "parent_symbol": "",
                    "signature": f"# {title}",
                    "docstring": "",
                    "start_line": start_line,
                    "end_line": max(start_line, end_line),
                    "excerpt": _clip_excerpt(lines, start_line, max(start_line, end_line)),
                    "callees": [],
                })
            return symbols

    step = 60
    stem = Path(rel_path).stem
    for start in range(1, len(lines) + 1, step):
        end = min(len(lines), start + step - 1)
        label = f"{stem}:{start}-{end}" if start > 1 else stem
        symbols.append({
            "name": label,
            "qualified_name": label,
            "kind": "chunk",
            "parent_symbol": "",
            "signature": f"{rel_path} (lines {start}..{end})",
            "docstring": "",
            "start_line": start,
            "end_line": end,
            "excerpt": _clip_excerpt(lines, start, end),
            "callees": [],
        })
    return symbols


def parse_source_file(rel_path: str, language: str, source: str) -> tuple[list[Json], list[Json], list[Json]]:
    """Parse any supported source file into (symbols, imports, call_edges)."""
    lines = source.splitlines()
    if language == "python":
        return _parse_python_ast(rel_path, source, lines)
    if language == "qml":
        return _parse_qml(rel_path, lines)
    if language in BRACE_LANGUAGES:
        return _parse_brace_language(rel_path, language, lines)
    return _parse_sections(rel_path, language, lines), [], []


class CodeIndex:
    """Service-owned per-project AST & Semantic Vector Indexer."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.store = service.store
        self.paths = service.paths
        self.redactor = service.redactor
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, project_id: str) -> asyncio.Lock:
        lock = self._locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[project_id] = lock
        return lock

    def _index_dir(self, project_id: str) -> Path:
        base = private_directory(self.paths.data / "code_index")
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id)[:96]
        return private_directory(base / safe_id)

    def _db_path(self, project_id: str) -> Path:
        return self._index_dir(project_id) / "index.db"

    @staticmethod
    def _open_db(db_path: Path) -> tuple[sqlite3.Connection, bool]:
        if db_path.is_symlink():
            raise KiraError("UNSAFE_PATH", "Code index database must not be a symlink")
        conn = sqlite3.connect(db_path, timeout=10.0)
        with contextlib.suppress(OSError):
            os.chmod(db_path, 0o600)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS indexed_files (
                path TEXT PRIMARY KEY,
                language TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                symbol_count INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS code_symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL REFERENCES indexed_files(path) ON DELETE CASCADE,
                language TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                parent_symbol TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL,
                docstring TEXT NOT NULL DEFAULT '',
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                excerpt TEXT NOT NULL,
                tokens TEXT NOT NULL,
                embedding BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_symbols_path ON code_symbols(path);
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON code_symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON code_symbols(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_kind ON code_symbols(kind);

            CREATE TABLE IF NOT EXISTS call_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                caller_path TEXT NOT NULL REFERENCES indexed_files(path) ON DELETE CASCADE,
                caller_symbol TEXT NOT NULL,
                caller_qualified TEXT NOT NULL,
                callee_name TEXT NOT NULL,
                line INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_calls_callee ON call_edges(callee_name);
            CREATE INDEX IF NOT EXISTS idx_calls_caller ON call_edges(caller_symbol);
            CREATE INDEX IF NOT EXISTS idx_calls_path ON call_edges(caller_path);

            CREATE TABLE IF NOT EXISTS file_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                importer_path TEXT NOT NULL REFERENCES indexed_files(path) ON DELETE CASCADE,
                imported_module TEXT NOT NULL,
                imported_name TEXT NOT NULL,
                line INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_imports_path ON file_imports(importer_path);
            CREATE INDEX IF NOT EXISTS idx_imports_name ON file_imports(imported_name);
            CREATE INDEX IF NOT EXISTS idx_imports_module ON file_imports(imported_module);
            """
        )
        has_fts = False
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS code_symbols_fts USING fts5(
                    symbol_id UNINDEXED,
                    path,
                    name,
                    qualified_name,
                    signature,
                    docstring,
                    tokens
                )
                """
            )
            has_fts = True
        except sqlite3.OperationalError:
            has_fts = False
        return conn, has_fts

    def _scan_and_sync_sync(self, project_id: str, root: Path, *, force: bool = False) -> Json:
        db_path = self._db_path(project_id)
        conn, has_fts = self._open_db(db_path)
        try:
            if force:
                conn.execute("DELETE FROM indexed_files")
                if has_fts:
                    conn.execute("DELETE FROM code_symbols_fts")
                conn.commit()

            existing = {
                row["path"]: (row["mtime_ns"], row["size"], row["sha256"])
                for row in conn.execute("SELECT path, mtime_ns, size, sha256 FROM indexed_files")
            }

            discovered: dict[str, tuple[Path, str, int, int]] = {}
            stack: list[tuple[Path, int]] = [(root, 0)]
            while stack and len(discovered) < MAX_INDEX_FILES:
                current, depth = stack.pop()
                if depth > 24:
                    continue
                try:
                    entries = sorted(current.iterdir(), key=lambda p: p.name, reverse=True)
                except OSError:
                    continue
                for entry in entries:
                    try:
                        st = entry.lstat()
                    except OSError:
                        continue
                    if stat.S_ISLNK(st.st_mode):
                        continue
                    try:
                        rel = entry.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    if _private(rel):
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        if entry.name.lower() not in EXCLUDED_DIRS:
                            stack.append((entry, depth + 1))
                    elif stat.S_ISREG(st.st_mode) and 0 < st.st_size <= MAX_INDEX_FILE_BYTES:
                        lang = LANGUAGE_BY_SUFFIX.get(entry.suffix.lower())
                        if lang:
                            discovered[rel] = (entry, lang, st.st_mtime_ns, st.st_size)

            removed_paths = set(existing) - set(discovered)
            updated_files = 0
            ts = now()

            conn.execute("BEGIN IMMEDIATE")
            try:
                for gone in removed_paths:
                    if has_fts:
                        conn.execute("DELETE FROM code_symbols_fts WHERE path=?", (gone,))
                    conn.execute("DELETE FROM indexed_files WHERE path=?", (gone,))

                for rel, (full_path, lang, mtime_ns, size) in sorted(discovered.items()):
                    prev = existing.get(rel)
                    if not force and prev is not None and prev[0] == mtime_ns and prev[1] == size:
                        continue
                    try:
                        fd = os.open(full_path, os.O_RDONLY | os.O_NOFOLLOW)
                        with os.fdopen(fd, "rb") as stream:
                            raw = stream.read(MAX_INDEX_FILE_BYTES + 1)
                    except OSError:
                        continue
                    if len(raw) > MAX_INDEX_FILE_BYTES or b"\0" in raw[:4096]:
                        continue
                    sha = hashlib.sha256(raw).hexdigest()
                    if not force and prev is not None and prev[2] == sha:
                        conn.execute(
                            "UPDATE indexed_files SET mtime_ns=?, size=?, indexed_at=? WHERE path=?",
                            (mtime_ns, size, ts, rel),
                        )
                        continue

                    source = raw.decode("utf-8", "replace")
                    symbols, imports, calls = parse_source_file(rel, lang, source)

                    if has_fts:
                        conn.execute("DELETE FROM code_symbols_fts WHERE path=?", (rel,))
                    conn.execute("DELETE FROM indexed_files WHERE path=?", (rel,))
                    conn.execute(
                        "INSERT INTO indexed_files (path, language, sha256, mtime_ns, size, symbol_count, indexed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (rel, lang, sha, mtime_ns, size, len(symbols), ts),
                    )

                    for sym in symbols:
                        clean_sig = self.redactor.text(sym["signature"])
                        clean_doc = self.redactor.text(sym["docstring"])
                        clean_excerpt = self.redactor.text(sym["excerpt"])
                        tok_str = fts_tokenize_text(
                            f"{rel} {sym['name']} {sym['qualified_name']} {clean_sig} {clean_doc} {clean_excerpt}"
                        )
                        emb = compute_semantic_vector(
                            name=sym["name"],
                            qualified_name=sym["qualified_name"],
                            kind=sym["kind"],
                            path=rel,
                            signature=clean_sig,
                            docstring=clean_doc,
                            body=clean_excerpt,
                            calls=sym.get("callees", []),
                        )
                        cur = conn.execute(
                            """
                            INSERT INTO code_symbols (
                                path, language, name, qualified_name, kind, parent_symbol,
                                signature, docstring, start_line, end_line, excerpt, tokens, embedding
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                rel,
                                lang,
                                sym["name"],
                                sym["qualified_name"],
                                sym["kind"],
                                sym.get("parent_symbol", ""),
                                clean_sig,
                                clean_doc,
                                sym["start_line"],
                                sym["end_line"],
                                clean_excerpt,
                                tok_str,
                                emb,
                            ),
                        )
                        sym_id = cur.lastrowid
                        if has_fts:
                            conn.execute(
                                """
                                INSERT INTO code_symbols_fts (
                                    symbol_id, path, name, qualified_name, signature, docstring, tokens
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    str(sym_id),
                                    rel,
                                    sym["name"],
                                    sym["qualified_name"],
                                    clean_sig,
                                    clean_doc,
                                    tok_str,
                                ),
                            )

                    for imp in imports:
                        conn.execute(
                            "INSERT INTO file_imports (importer_path, imported_module, imported_name, line) "
                            "VALUES (?, ?, ?, ?)",
                            (rel, imp["imported_module"][:256], imp["imported_name"][:128], imp["line"]),
                        )

                    for call in calls:
                        conn.execute(
                            "INSERT INTO call_edges (caller_path, caller_symbol, caller_qualified, callee_name, line) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                rel,
                                call["caller_symbol"][:128],
                                call["caller_qualified"][:256],
                                call["callee_name"][:128],
                                call["line"],
                            ),
                        )
                    updated_files += 1

                if updated_files or removed_paths or force:
                    conn.execute(
                        "INSERT INTO index_meta (key, value) VALUES ('last_indexed_at', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (ts,),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

            return self._status_from_conn(conn, has_fts, updated_files=updated_files, removed_files=len(removed_paths))
        finally:
            conn.close()

    @staticmethod
    def _status_from_conn(
        conn: sqlite3.Connection,
        has_fts: bool,
        *,
        updated_files: int = 0,
        removed_files: int = 0,
    ) -> Json:
        file_count = int(conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0])
        symbol_count = int(conn.execute("SELECT COUNT(*) FROM code_symbols").fetchone()[0])
        call_count = int(conn.execute("SELECT COUNT(*) FROM call_edges").fetchone()[0])
        import_count = int(conn.execute("SELECT COUNT(*) FROM file_imports").fetchone()[0])
        langs = {
            row["language"]: int(row["cnt"])
            for row in conn.execute("SELECT language, COUNT(*) AS cnt FROM indexed_files GROUP BY language")
        }
        last_row = conn.execute("SELECT value FROM index_meta WHERE key='last_indexed_at'").fetchone()
        return {
            "indexed": file_count > 0,
            "files": file_count,
            "symbols": symbol_count,
            "call_edges": call_count,
            "imports": import_count,
            "languages": langs,
            "updated_files": updated_files,
            "removed_files": removed_files,
            "engine": "ast+fts5+vec256" if has_fts else "ast+vec256",
            "embedding_dim": EMBEDDING_DIM,
            "last_indexed_at": str(last_row[0]) if last_row else "",
        }

    async def ensure_indexed(self, project: Json, *, force: bool = False) -> Json:
        root = Path(project["root"])
        if not root.is_dir() or root.is_symlink():
            raise KiraError("UNSAFE_PATH", "Project root must be a real directory")
        async with self._lock(project["id"]):
            return await asyncio.to_thread(
                self._scan_and_sync_sync, project["id"], root, force=force
            )

    async def status(self, project: Json) -> Json:
        return await self.ensure_indexed(project, force=False)

    def _search_sync(
        self,
        project_id: str,
        *,
        query: str,
        path_prefix: str = "",
        kind: str = "",
        language: str = "",
        limit: int = 8,
    ) -> list[Json]:
        db_path = self._db_path(project_id)
        conn, has_fts = self._open_db(db_path)
        try:
            clauses: list[str] = []
            params: list[Any] = []
            if path_prefix:
                norm_prefix = path_prefix.strip("/").replace("\\", "/")
                clauses.append("(path = ? OR path LIKE ?)")
                params.extend([norm_prefix, f"{norm_prefix}%"])
            if kind:
                clauses.append("kind = ?")
                params.append(kind)
            if language:
                clauses.append("language = ?")
                params.append(language.lower())

            where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"""
                SELECT id, path, language, name, qualified_name, kind, parent_symbol,
                       signature, docstring, start_line, end_line, excerpt, tokens, embedding
                FROM code_symbols
                {where_sql}
                """,
                params,
            ).fetchall()
            if not rows:
                return []

            fts_ranks: dict[int, int] = {}
            q_tokens = split_identifier(query)
            if has_fts and q_tokens:
                fts_query = " OR ".join(f'"{t}"*' for t in q_tokens[:12])
                with contextlib.suppress(sqlite3.OperationalError):
                    fts_rows = conn.execute(
                        "SELECT symbol_id FROM code_symbols_fts WHERE code_symbols_fts MATCH ? "
                        "ORDER BY bm25(code_symbols_fts) LIMIT 120",
                        (fts_query,),
                    ).fetchall()
                    for rank_idx, frow in enumerate(fts_rows):
                        fts_ranks[int(frow["symbol_id"])] = rank_idx + 1

            query_vec = compute_query_vector(query)
            q_lower = query.strip().lower()
            q_token_set = set(q_tokens)

            scored: list[tuple[float, Json]] = []
            for row in rows:
                sym_id = int(row["id"])
                name_lower = str(row["name"]).lower()
                qual_lower = str(row["qualified_name"]).lower()
                path_lower = str(row["path"]).lower()
                sig_lower = str(row["signature"]).lower()
                doc_lower = str(row["docstring"]).lower()
                sym_tokens = set(str(row["tokens"]).split())

                reasons: list[str] = []
                ast_score = 0.0
                if q_lower == name_lower or q_lower == qual_lower:
                    ast_score += 1.0
                    reasons.append("exact_symbol")
                elif q_lower in name_lower or q_lower in qual_lower:
                    ast_score += 0.72
                    reasons.append("symbol")
                elif q_lower in path_lower:
                    ast_score += 0.55
                    reasons.append("path")

                if q_token_set:
                    name_toks = set(split_identifier(f"{row['name']} {row['qualified_name']}"))
                    overlap_name = len(q_token_set & name_toks) / len(q_token_set)
                    overlap_all = len(q_token_set & sym_tokens) / len(q_token_set)
                    if overlap_name > 0:
                        ast_score += 0.55 * overlap_name
                        if "symbol" not in reasons and "exact_symbol" not in reasons:
                            reasons.append("symbol")
                    if overlap_all > 0:
                        ast_score += 0.35 * overlap_all
                    if any(tok in sig_lower or tok in doc_lower for tok in q_token_set):
                        ast_score += 0.15

                bm25_score = 0.0
                if sym_id in fts_ranks:
                    bm25_score = 1.0 / (15.0 + fts_ranks[sym_id]) * 16.0
                    reasons.append("bm25")

                sem_score = cosine_similarity(query_vec, row["embedding"])
                if sem_score >= 0.18:
                    reasons.append("semantic")

                combined = (ast_score * 0.52) + (bm25_score * 0.23) + (sem_score * 0.45)
                if row["kind"] in {"class", "function", "async_function", "method", "component", "interface", "struct"}:
                    combined *= 1.08

                if combined < 0.06:
                    continue

                scored.append((
                    combined,
                    {
                        "path": row["path"],
                        "language": row["language"],
                        "symbol": row["name"],
                        "qualified_name": row["qualified_name"],
                        "kind": row["kind"],
                        "parent_symbol": row["parent_symbol"],
                        "signature": row["signature"],
                        "docstring": row["docstring"],
                        "start_line": int(row["start_line"]),
                        "end_line": int(row["end_line"]),
                        "excerpt": row["excerpt"],
                        "score": round(min(0.999, combined), 4),
                        "semantic_similarity": round(sem_score, 4),
                        "match_reasons": reasons or ["semantic"],
                    },
                ))

            scored.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["start_line"]))
            return [item[1] for item in scored[:limit]]
        finally:
            conn.close()

    async def search(
        self,
        project: Json,
        *,
        query: str,
        path: str = "",
        kind: str = "",
        language: str = "",
        limit: int = 8,
    ) -> Json:
        status = await self.ensure_indexed(project, force=False)
        async with self._lock(project["id"]):
            results = await asyncio.to_thread(
                self._search_sync,
                project["id"],
                query=query,
                path_prefix=path,
                kind=kind,
                language=language,
                limit=limit,
            )
        return {
            "ok": True,
            "query": query,
            "results": self.redactor.clean(results),
            "total_indexed_symbols": status["symbols"],
            "total_indexed_files": status["files"],
        }

    def _symbols_sync(
        self,
        project_id: str,
        *,
        query: str = "",
        path_prefix: str = "",
        kind: str = "",
        limit: int = 25,
    ) -> list[Json]:
        db_path = self._db_path(project_id)
        conn, _ = self._open_db(db_path)
        try:
            clauses: list[str] = []
            params: list[Any] = []
            if path_prefix:
                norm_prefix = path_prefix.strip("/").replace("\\", "/")
                clauses.append("(path = ? OR path LIKE ?)")
                params.extend([norm_prefix, f"{norm_prefix}%"])
            if kind:
                clauses.append("kind = ?")
                params.append(kind)
            if query:
                q_like = f"%{query.strip()}%"
                clauses.append("(name LIKE ? OR qualified_name LIKE ? OR signature LIKE ?)")
                params.extend([q_like, q_like, q_like])
            where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"""
                SELECT path, language, name, qualified_name, kind, parent_symbol,
                       signature, docstring, start_line, end_line
                FROM code_symbols
                {where_sql}
                ORDER BY
                    CASE WHEN kind IN ('class','function','async_function','method','component','interface','struct') THEN 0 ELSE 1 END,
                    path ASC, start_line ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [
                {
                    "path": r["path"],
                    "language": r["language"],
                    "symbol": r["name"],
                    "qualified_name": r["qualified_name"],
                    "kind": r["kind"],
                    "parent_symbol": r["parent_symbol"],
                    "signature": r["signature"],
                    "docstring": r["docstring"],
                    "start_line": int(r["start_line"]),
                    "end_line": int(r["end_line"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    async def symbols(
        self,
        project: Json,
        *,
        query: str = "",
        path: str = "",
        kind: str = "",
        limit: int = 25,
    ) -> Json:
        await self.ensure_indexed(project, force=False)
        async with self._lock(project["id"]):
            items = await asyncio.to_thread(
                self._symbols_sync,
                project["id"],
                query=query,
                path_prefix=path,
                kind=kind,
                limit=limit,
            )
        return {"ok": True, "symbols": self.redactor.clean(items)}

    def _graph_sync(
        self,
        project_id: str,
        *,
        symbol: str = "",
        path: str = "",
        limit: int = 25,
    ) -> Json:
        db_path = self._db_path(project_id)
        conn, _ = self._open_db(db_path)
        try:
            definitions: list[Json] = []
            callers: list[Json] = []
            callees: list[Json] = []
            imports: list[Json] = []
            importers: list[Json] = []

            target_names: set[str] = set()
            if symbol:
                short_name = symbol.split(".")[-1]
                target_names.add(short_name)
                def_rows = conn.execute(
                    """
                    SELECT path, language, name, qualified_name, kind, signature, docstring, start_line, end_line
                    FROM code_symbols
                    WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ?
                    ORDER BY path ASC, start_line ASC
                    LIMIT ?
                    """,
                    (short_name, symbol, f"%.{short_name}", limit),
                ).fetchall()
                for r in def_rows:
                    definitions.append({
                        "path": r["path"],
                        "language": r["language"],
                        "symbol": r["name"],
                        "qualified_name": r["qualified_name"],
                        "kind": r["kind"],
                        "signature": r["signature"],
                        "docstring": r["docstring"],
                        "start_line": int(r["start_line"]),
                        "end_line": int(r["end_line"]),
                    })

                caller_rows = conn.execute(
                    """
                    SELECT caller_path, caller_symbol, caller_qualified, callee_name, line
                    FROM call_edges
                    WHERE callee_name = ?
                    ORDER BY caller_path ASC, line ASC
                    LIMIT ?
                    """,
                    (short_name, limit),
                ).fetchall()
                for r in caller_rows:
                    callers.append({
                        "caller_path": r["caller_path"],
                        "caller_symbol": r["caller_symbol"],
                        "caller_qualified": r["caller_qualified"],
                        "callee_name": r["callee_name"],
                        "line": int(r["line"]),
                    })

                callee_rows = conn.execute(
                    """
                    SELECT caller_path, caller_qualified, callee_name, line
                    FROM call_edges
                    WHERE caller_symbol = ? OR caller_qualified = ?
                    ORDER BY caller_path ASC, line ASC
                    LIMIT ?
                    """,
                    (short_name, symbol, limit),
                ).fetchall()
                for r in callee_rows:
                    callee_defs = conn.execute(
                        "SELECT DISTINCT path, start_line FROM code_symbols WHERE name = ? LIMIT 4",
                        (r["callee_name"],),
                    ).fetchall()
                    callees.append({
                        "caller_path": r["caller_path"],
                        "caller_qualified": r["caller_qualified"],
                        "callee_name": r["callee_name"],
                        "line": int(r["line"]),
                        "defined_in": [
                            {"path": d["path"], "start_line": int(d["start_line"])}
                            for d in callee_defs
                        ],
                    })

            if path:
                norm_path = path.strip("/").replace("\\", "/")
                if not definitions:
                    file_defs = conn.execute(
                        """
                        SELECT path, language, name, qualified_name, kind, signature, docstring, start_line, end_line
                        FROM code_symbols
                        WHERE path = ?
                        ORDER BY start_line ASC
                        LIMIT ?
                        """,
                        (norm_path, limit),
                    ).fetchall()
                    for r in file_defs:
                        target_names.add(r["name"])
                        definitions.append({
                            "path": r["path"],
                            "language": r["language"],
                            "symbol": r["name"],
                            "qualified_name": r["qualified_name"],
                            "kind": r["kind"],
                            "signature": r["signature"],
                            "docstring": r["docstring"],
                            "start_line": int(r["start_line"]),
                            "end_line": int(r["end_line"]),
                        })

                imp_rows = conn.execute(
                    """
                    SELECT imported_module, imported_name, line
                    FROM file_imports
                    WHERE importer_path = ?
                    ORDER BY line ASC
                    LIMIT ?
                    """,
                    (norm_path, limit),
                ).fetchall()
                for r in imp_rows:
                    imports.append({
                        "imported_module": r["imported_module"],
                        "imported_name": r["imported_name"],
                        "line": int(r["line"]),
                    })

                stem = Path(norm_path).stem
                dotted = norm_path.removesuffix(Path(norm_path).suffix).replace("/", ".")
                if dotted.startswith("src."):
                    dotted = dotted[4:]
                importers_rows = conn.execute(
                    """
                    SELECT DISTINCT importer_path, imported_module, imported_name, line
                    FROM file_imports
                    WHERE importer_path != ?
                      AND (imported_name = ? OR imported_module = ? OR imported_module LIKE ?)
                    ORDER BY importer_path ASC, line ASC
                    LIMIT ?
                    """,
                    (norm_path, stem, dotted, f"%{stem}%", limit),
                ).fetchall()
                for r in importers_rows:
                    importers.append({
                        "importer_path": r["importer_path"],
                        "imported_module": r["imported_module"],
                        "imported_name": r["imported_name"],
                        "line": int(r["line"]),
                    })

            if symbol and not importers:
                short_name = symbol.split(".")[-1]
                imp_sym_rows = conn.execute(
                    """
                    SELECT DISTINCT importer_path, imported_module, imported_name, line
                    FROM file_imports
                    WHERE imported_name = ?
                    ORDER BY importer_path ASC, line ASC
                    LIMIT ?
                    """,
                    (short_name, limit),
                ).fetchall()
                for r in imp_sym_rows:
                    importers.append({
                        "importer_path": r["importer_path"],
                        "imported_module": r["imported_module"],
                        "imported_name": r["imported_name"],
                        "line": int(r["line"]),
                    })

            return {
                "ok": True,
                "symbol": symbol,
                "path": path,
                "definitions": self.redactor.clean(definitions),
                "callers": self.redactor.clean(callers),
                "callees": self.redactor.clean(callees),
                "imports": self.redactor.clean(imports),
                "importers": self.redactor.clean(importers),
            }
        finally:
            conn.close()

    async def graph(
        self,
        project: Json,
        *,
        symbol: str = "",
        path: str = "",
        limit: int = 25,
    ) -> Json:
        await self.ensure_indexed(project, force=False)
        async with self._lock(project["id"]):
            return await asyncio.to_thread(
                self._graph_sync,
                project["id"],
                symbol=symbol,
                path=path,
                limit=limit,
            )

    async def bootstrap(self, project: Json, session: Json, prompt: str) -> str:
        """Build a bounded zero-turn semantic codebase orientation block for a run."""
        if not prompt or len(prompt.strip()) < 4:
            return ""
        try:
            status = await self.ensure_indexed(project, force=False)
            if not status.get("symbols"):
                return ""
            async with self._lock(project["id"]):
                hits = await asyncio.to_thread(
                    self._search_sync,
                    project["id"],
                    query=prompt[:500],
                    limit=6,
                )
        except Exception:
            return ""
        if not hits:
            return ""
        header = (
            f"\nCodebase semantic index ({status['files']} files, {status['symbols']} AST symbols, "
            f"{status['call_edges']} call-graph edges). Top relevant locations for this request:\n"
        )
        lines: list[str] = [header]
        budget = MAX_BOOTSTRAP_CHARS - len(header)
        for hit in hits:
            doc_part = f" — {hit['docstring'][:120]}" if hit.get("docstring") else ""
            entry = (
                f"- {hit['path']}:{hit['start_line']}-{hit['end_line']} "
                f"[{hit['kind']}] `{hit['signature']}`{doc_part}\n"
            )
            if len(entry) > budget:
                break
            lines.append(entry)
            budget -= len(entry)
        return "".join(lines)

    def attach(
        self,
        session: Json,
        project: Json,
        run_id: str,
        cancel: asyncio.Event,
        mode: str,
        options: Json,
    ) -> Json:
        """Attach kira.code_index to a run so any provider can query AST symbols, call graphs & semantic chunks."""
        if (
            session["backend"]
            not in {"codex", "openai_chatgpt", "ollama", "deepseek_account", "deepseek_web", "claude_code"}
            or not kira_tools_enabled(session)
            or self.store.db.execute(
                "SELECT 1 FROM team_members WHERE session_id=?", (session["id"],)
            ).fetchone()
        ):
            return options

        result = dict(options)
        namespaces = [
            {**namespace, "tools": list(namespace["tools"])}
            for namespace in result.get("client_tools", [])
        ]
        kira = next((ns for ns in namespaces if ns.get("name") == "kira"), None)
        if kira is None:
            kira = {
                "type": "namespace",
                "name": "kira",
                "description": "Kira's current project tools.",
                "tools": [],
            }
            namespaces.append(kira)
        existing = {tool.get("name") for tool in kira["tools"]}
        kira["tools"].extend(
            tool for tool in code_index_tool_specs() if tool["name"] not in existing
        )
        previous = result.get("client_tool_call")
        generation = self.service.provider_routing.current_generation(session["id"])

        def active() -> Json:
            actual_session = ProjectChats(self.store).owned_session(project["id"], session["id"])
            actual_project = self.store.one("projects", project["id"])
            run = self.store.one("runs", run_id)
            if (
                self.service._closing
                or cancel.is_set()
                or run_id not in self.service.tasks
                or run["session_id"] != session["id"]
                or run["state"] not in {"running", "awaiting_input"}
                or any(actual_session[key] != session[key] for key in ("backend", "model"))
                or actual_session["settings"].get("openai_account_id")
                != session["settings"].get("openai_account_id")
                or not kira_tools_enabled(actual_session)
                or self.service.provider_routing.current_generation(session["id"]) != generation
            ):
                raise KiraError("CANCELLED", "The originating run no longer permits code_index calls")
            return self.service.effective_project(run_id, actual_project)

        async def call(call_id: str, name: str, arguments: Json) -> Json:
            if name not in CODE_INDEX_TOOL_NAMES:
                if previous is None:
                    raise KiraError("UNSUPPORTED", "This callback is not registered")
                return dict(await previous(call_id, name, arguments))
            return await self._handle_tool_call(run_id, call_id, arguments, active)

        result.update(client_tools=namespaces, client_tool_call=call)
        result["assistant_instructions"] = result.get("assistant_instructions", "") + (
            "\nSemantic codebase index available: use kira.code_index with actions "
            "search (hybrid semantic + BM25 + AST chunk lookup), symbols (list functions/classes/methods), "
            "graph (find callers, callees, definitions, imports and importers for a symbol or file), "
            "status, or rebuild."
        )
        return result

    async def _handle_tool_call(
        self, run_id: str, call_id: str, arguments: Json, active: Any
    ) -> Json:
        try:
            actual_project = active()
            if not isinstance(arguments, dict) or set(arguments) != {"action", "payload"}:
                raise KiraError("INVALID_ARGUMENT", "kira.code_index requires action and payload")
            action, payload = validate_code_index_action(
                arguments.get("action"), arguments.get("payload")
            )
        except KiraError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}

        await self.service.emit(
            run_id,
            "tool.started",
            {"tool_id": call_id, "name": "kira.code_index", "arguments": arguments},
        )
        try:
            actual_project = active()
            if action == "status":
                status = await self.status(actual_project)
                response: Json = {"ok": True, "status": status, "contracts": code_index_action_contract()}
            elif action == "rebuild":
                status = await self.ensure_indexed(actual_project, force=bool(payload.get("force")))
                response = {"ok": True, "status": status}
            elif action == "search":
                response = await self.search(
                    actual_project,
                    query=payload["query"],
                    path=payload.get("path", ""),
                    kind=payload.get("kind", ""),
                    language=payload.get("language", ""),
                    limit=payload.get("limit", 8),
                )
            elif action == "symbols":
                response = await self.symbols(
                    actual_project,
                    query=payload.get("query", ""),
                    path=payload.get("path", ""),
                    kind=payload.get("kind", ""),
                    limit=payload.get("limit", 25),
                )
            else:
                response = await self.graph(
                    actual_project,
                    symbol=payload.get("symbol", ""),
                    path=payload.get("path", ""),
                    limit=payload.get("limit", 25),
                )
        except KiraError as exc:
            response = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            response = {
                "ok": False,
                "error": {"code": "INDEX_ERROR", "message": self.redactor.text(str(exc))[:300]},
            }

        response = self.redactor.clean(response)
        await self.service.emit(
            run_id,
            "tool.completed",
            {"tool_id": call_id, "name": "kira.code_index", "result": response},
        )
        return response

    async def dispatch(self, method: str, params: Json) -> Json:
        if not isinstance(params, dict) or "project_id" not in params:
            raise KiraError("INVALID_ARGUMENT", "project_id is required")
        project_id = params.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 128:
            raise KiraError("INVALID_ARGUMENT", "project_id must be bounded text")
        project = self.store.one("projects", project_id)

        if method == "codeindex.status":
            if set(params) - {"project_id"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown codeindex.status parameter")
            status = await self.status(project)
            return {"status": status}

        if method == "codeindex.rebuild":
            if set(params) - {"project_id", "force"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown codeindex.rebuild parameter")
            force = params.get("force", True)
            if not isinstance(force, bool):
                raise KiraError("INVALID_ARGUMENT", "force must be a boolean")
            status = await self.ensure_indexed(project, force=force)
            return {"status": status}

        if method == "codeindex.search":
            if set(params) - {"project_id", "query", "path", "kind", "language", "limit"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown codeindex.search parameter")
            _, clean = validate_code_index_action(
                "search",
                {k: v for k, v in params.items() if k != "project_id"},
            )
            return await self.search(
                project,
                query=clean["query"],
                path=clean["path"],
                kind=clean["kind"],
                language=clean["language"],
                limit=clean["limit"],
            )

        if method == "codeindex.symbols":
            if set(params) - {"project_id", "query", "path", "kind", "limit"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown codeindex.symbols parameter")
            _, clean = validate_code_index_action(
                "symbols",
                {k: v for k, v in params.items() if k != "project_id"},
            )
            return await self.symbols(
                project,
                query=clean["query"],
                path=clean["path"],
                kind=clean["kind"],
                limit=clean["limit"],
            )

        if method == "codeindex.graph":
            if set(params) - {"project_id", "symbol", "path", "limit"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown codeindex.graph parameter")
            _, clean = validate_code_index_action(
                "graph",
                {k: v for k, v in params.items() if k != "project_id"},
            )
            return await self.graph(
                project,
                symbol=clean["symbol"],
                path=clean["path"],
                limit=clean["limit"],
            )

        raise KiraError("METHOD_NOT_FOUND", f"Unknown codeindex method: {method}")
