"""Service-owned Interactive Canvas / Artifacts, Offline Live Renderer & Granular Diff Hunk Engine.

Each project owns an isolated SQLite store under ``$KIRA_HOME/data/canvas/<project_id>/canvas.db``
and rendered SVG/HTML previews under ``$KIRA_HOME/data/canvas/<project_id>/previews/``.
All rendering (Mermaid diagrams, sanitized SVG, HTML UI cards, Markdown, and Code) is performed
100% locally and deterministically without external CDN dependencies or network access.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import html
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from kira.domain.canvas import (
    CANVAS_METHODS,
    CANVAS_TOOL_NAMES,
    HUNK_DECISIONS,
    MAX_ARTIFACT_CONTENT_BYTES,
    MAX_ARTIFACTS_PER_PROJECT,
    MAX_REVISIONS_PER_ARTIFACT,
    canvas_action_contract,
    canvas_tool_specs,
    validate_canvas_action,
)
from kira.domain.contracts import WRITE_PERMISSIONS, Json, KiraError
from kira.service.desktop import private_directory
from kira.service.project_context import ProjectChats
from kira.service.storage import new_id, now
from kira.service.tool_policy import kira_tools_enabled
from kira.service.workbench import _git, _private, _relative

METHODS = CANVAS_METHODS

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_SVG_DANGEROUS_TAG_RE = re.compile(
    r"<\s*/?\s*(?:script|foreignObject|iframe|object|embed|applet|meta|link)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_BLOCKED_TAG_RE = re.compile(
    r"<\s*/?\s*(?:iframe|frame|frameset|object|embed|applet|base)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_EXTERNAL_SCRIPT_LINK_RE = re.compile(
    r"<(?:script\b[^>]*\bsrc\s*=|link\b[^>]*\bhref\s*=)\s*(?:\"https?:|'https?:|\"//|'//)[^>]*>",
    re.IGNORECASE,
)
_SVG_EVENT_ATTR_RE = re.compile(r"\s+on[a-zA-Z0-9_]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_SVG_EXTERNAL_HREF_RE = re.compile(
    r"\s+(?:xlink:)?href\s*=\s*(?:\"\s*(?:javascript:|https?:|file:)[^\"]*\"|'\s*(?:javascript:|https?:|file:)[^']*')",
    re.IGNORECASE,
)

_MERMAID_EDGE_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)"
    r"(?:\s*(\[\[?.+?\]\]?|\(\(?.+?\)\)?|\{\{?.+?\}\}?))?"
    r"\s*(?:(-->|---|-\.->|==>|-\.-|--)\s*(?:\|([^|]+)\|)?\s*)"
    r"([A-Za-z0-9_.-]+)"
    r"(?:\s*(\[\[?.+?\]\]?|\(\(?.+?\)\)?|\{\{?.+?\}\}?))?\s*$"
)
_MERMAID_NODE_ONLY_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)\s*(\[\[?.+?\]\]?|\(\(?.+?\)\)?|\{\{?.+?\}\}?)\s*$"
)
_MERMAID_SEQ_MSG_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)\s*(->>|-->>|->|-->|-x|--x)\s*([A-Za-z0-9_.-]+)\s*:\s*(.+)$"
)
_MERMAID_PARTICIPANT_RE = re.compile(
    r"^\s*(?:participant|actor)\s+([A-Za-z0-9_.-]+)(?:\s+as\s+(.+))?$",
    re.IGNORECASE,
)


def _xml_escape(text: str) -> str:
    return html.escape(text, quote=True)


def _strip_node_shape(raw_label: str | None, fallback: str) -> tuple[str, str]:
    if not raw_label:
        return fallback, "rect"
    s = raw_label.strip()
    if s.startswith("((") and s.endswith("))"):
        return s[2:-2].strip().strip("\"'") or fallback, "circle"
    if s.startswith("(") and s.endswith(")"):
        return s[1:-1].strip().strip("\"'") or fallback, "round"
    if s.startswith("{") and s.endswith("}"):
        return s.strip("{}").strip().strip("\"'") or fallback, "diamond"
    if s.startswith("[") and s.endswith("]"):
        return s.strip("[]").strip().strip("\"'") or fallback, "rect"
    return s.strip("\"'") or fallback, "rect"


def render_mermaid_to_svg(source: str, title: str = "Mermaid Diagram") -> str:
    """Compile Mermaid syntax (flowchart/graph, sequenceDiagram, state/class) into standalone SVG."""
    lines = [
        ln.strip()
        for ln in source.splitlines()
        if ln.strip() and not ln.strip().startswith("%%")
    ]
    header = lines[0].lower() if lines else "flowchart td"

    if header.startswith("sequencediagram"):
        return _render_sequence_svg(lines[1:], title)

    direction = "TD"
    first_parts = lines[0].split() if lines else []
    if len(first_parts) >= 2 and first_parts[0].lower() in {"flowchart", "graph"}:
        dir_token = first_parts[1].upper()
        if dir_token in {"LR", "RL", "TD", "TB", "BT"}:
            direction = dir_token
        body_lines = lines[1:]
    elif header.startswith(("statediagram", "classdiagram", "erdiagram")):
        body_lines = lines[1:]
    else:
        body_lines = lines

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def ensure_node(node_id: str, raw_shape: str | None = None) -> None:
        clean_id = node_id.strip()
        if clean_id == "[*]":
            clean_id = "START_END"
            raw_shape = raw_shape or "((●))"
        label, shape = _strip_node_shape(raw_shape, clean_id)
        if clean_id not in nodes:
            nodes[clean_id] = {"id": clean_id, "label": label, "shape": shape, "order": len(nodes)}
        elif raw_shape:
            nodes[clean_id]["label"] = label
            nodes[clean_id]["shape"] = shape

    for line in body_lines:
        if line.lower().startswith(("subgraph ", "end", "style ", "classdef ", "class ", "click ")):
            continue
        # State diagram arrow A --> B : label
        if "-->" in line and ":" in line and "|" not in line:
            left, right_label = line.split(":", 1)
            parts = left.split("-->", 1)
            if len(parts) == 2:
                src, dst = parts[0].strip(), parts[1].strip()
                ensure_node(src)
                ensure_node(dst)
                edges.append({"src": "START_END" if src == "[*]" else src, "dst": "START_END" if dst == "[*]" else dst, "label": right_label.strip(), "style": "-->"})
                continue
        m_edge = _MERMAID_EDGE_RE.match(line)
        if m_edge:
            src, src_lbl, arrow, edge_lbl, dst, dst_lbl = m_edge.groups()
            ensure_node(src, src_lbl)
            ensure_node(dst, dst_lbl)
            edges.append({
                "src": src,
                "dst": dst,
                "style": arrow or "-->",
                "label": (edge_lbl or "").strip().strip("\"'"),
            })
            continue
        m_node = _MERMAID_NODE_ONLY_RE.match(line)
        if m_node:
            nid, nlbl = m_node.groups()
            ensure_node(nid, nlbl)

    if not nodes:
        for idx, line in enumerate(body_lines[:8]):
            nid = f"N{idx + 1}"
            nodes[nid] = {"id": nid, "label": line[:48], "shape": "rect", "order": idx}

    # Compute topological layers for clean layout
    indegree: dict[str, int] = {nid: 0 for nid in nodes}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        if e["src"] in adj and e["dst"] in indegree and e["src"] != e["dst"]:
            adj[e["src"]].append(e["dst"])
            indegree[e["dst"]] += 1

    layer_of: dict[str, int] = {}
    queue = [nid for nid, deg in indegree.items() if deg == 0]
    if not queue and nodes:
        queue = [next(iter(nodes))]
    for nid in queue:
        layer_of[nid] = 0
    visited_order = list(queue)
    idx_q = 0
    while idx_q < len(visited_order):
        curr = visited_order[idx_q]
        idx_q += 1
        curr_layer = layer_of.get(curr, 0)
        for nxt in adj.get(curr, []):
            if nxt not in layer_of or layer_of[nxt] < curr_layer + 1:
                if curr_layer + 1 < 12:
                    layer_of[nxt] = curr_layer + 1
            if nxt not in visited_order:
                visited_order.append(nxt)
    for nid in nodes:
        layer_of.setdefault(nid, 0)

    layers: dict[int, list[str]] = {}
    for nid, lyr in layer_of.items():
        layers.setdefault(lyr, []).append(nid)

    horizontal = direction in {"LR", "RL"}
    max_layer = max(layers.keys(), default=0)
    max_per_layer = max((len(v) for v in layers.values()), default=1)

    box_w, box_h = 180, 54
    gap_x, gap_y = (90, 36) if horizontal else (44, 72)

    if horizontal:
        width = max(680, 80 + (max_layer + 1) * (box_w + gap_x))
        height = max(360, 100 + max_per_layer * (box_h + gap_y))
    else:
        width = max(680, 80 + max_per_layer * (box_w + gap_x))
        height = max(380, 100 + (max_layer + 1) * (box_h + gap_y))

    coords: dict[str, tuple[float, float]] = {}
    for lyr, nids in sorted(layers.items()):
        count = len(nids)
        for i, nid in enumerate(nids):
            if horizontal:
                col = (max_layer - lyr) if direction == "RL" else lyr
                cx = 60 + col * (box_w + gap_x) + box_w / 2
                total_h = count * box_h + (count - 1) * gap_y
                start_y = max(76.0, (height - total_h) / 2 + 20)
                cy = start_y + i * (box_h + gap_y) + box_h / 2
            else:
                row = (max_layer - lyr) if direction == "BT" else lyr
                cy = 82 + row * (box_h + gap_y) + box_h / 2
                total_w = count * box_w + (count - 1) * gap_x
                start_x = max(40.0, (width - total_w) / 2)
                cx = start_x + i * (box_w + gap_x) + box_w / 2
            coords[nid] = (cx, cy)

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(width)}" height="{int(height)}" viewBox="0 0 {int(width)} {int(height)}">',
        '<defs>',
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#6ea8fe"/>',
        '</marker>',
        '</defs>',
        f'<rect width="{int(width)}" height="{int(height)}" rx="14" fill="#13181d"/>',
        f'<text x="24" y="34" fill="#9fb3c8" font-family="sans-serif" font-size="13" font-weight="bold">{_xml_escape(title[:80])}</text>',
    ]

    for e in edges:
        if e["src"] not in coords or e["dst"] not in coords:
            continue
        x1, y1 = coords[e["src"]]
        x2, y2 = coords[e["dst"]]
        if horizontal:
            sx = x1 + (box_w / 2 if x2 >= x1 else -box_w / 2)
            sy = y1
            tx = x2 - (box_w / 2 if x2 >= x1 else -box_w / 2)
            ty = y2
        else:
            sx = x1
            sy = y1 + (box_h / 2 if y2 >= y1 else -box_h / 2)
            tx = x2
            ty = y2 - (box_h / 2 if y2 >= y1 else -box_h / 2)
        dash = ' stroke-dasharray="5,4"' if "." in e["style"] else ""
        marker = ' marker-end="url(#arrow)"' if ">" in e["style"] else ""
        svg_parts.append(
            f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" stroke="#6ea8fe" stroke-width="2"{dash}{marker}/>'
        )
        if e["label"]:
            mx, my = (sx + tx) / 2, (sy + ty) / 2 - 6
            lbl = _xml_escape(e["label"][:28])
            svg_parts.append(
                f'<rect x="{mx - len(lbl) * 3.4 - 6:.1f}" y="{my - 12:.1f}" width="{len(lbl) * 6.8 + 12:.1f}" height="18" rx="5" fill="#1c242c" stroke="#334155" stroke-width="1"/>'
            )
            svg_parts.append(
                f'<text x="{mx:.1f}" y="{my + 1:.1f}" fill="#cbd5e1" font-family="sans-serif" font-size="11" text-anchor="middle">{lbl}</text>'
            )

    for nid, info in nodes.items():
        cx, cy = coords[nid]
        x0, y0 = cx - box_w / 2, cy - box_h / 2
        shape = info["shape"]
        label = _xml_escape(str(info["label"])[:26])
        if shape == "diamond":
            pts = f"{cx:.1f},{y0 - 4:.1f} {x0 + box_w + 8:.1f},{cy:.1f} {cx:.1f},{y0 + box_h + 4:.1f} {x0 - 8:.1f},{cy:.1f}"
            svg_parts.append(
                f'<polygon points="{pts}" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/>'
            )
        elif shape == "circle":
            svg_parts.append(
                f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{box_w / 2:.1f}" ry="{box_h / 2:.1f}" fill="#1e293b" stroke="#a78bfa" stroke-width="2"/>'
            )
        else:
            rx = 24 if shape == "round" else 10
            svg_parts.append(
                f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{box_w}" height="{box_h}" rx="{rx}" fill="#1e293b" stroke="#60a5fa" stroke-width="2"/>'
            )
        svg_parts.append(
            f'<text x="{cx:.1f}" y="{cy + 5:.1f}" fill="#f1f5f9" font-family="sans-serif" font-size="13" font-weight="600" text-anchor="middle">{label}</text>'
        )

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def _render_sequence_svg(lines: list[str], title: str) -> str:
    participants: dict[str, str] = {}
    messages: list[tuple[str, str, str, str]] = []
    for line in lines:
        mp = _MERMAID_PARTICIPANT_RE.match(line)
        if mp:
            pid, alias = mp.groups()
            participants[pid] = (alias or pid).strip().strip("\"'")
            continue
        mm = _MERMAID_SEQ_MSG_RE.match(line)
        if mm:
            src, arrow, dst, msg = mm.groups()
            participants.setdefault(src, src)
            participants.setdefault(dst, dst)
            messages.append((src, dst, arrow, msg.strip()))

    if not participants:
        participants = {"Client": "Client", "Service": "Service"}

    pids = list(participants.keys())
    col_w = 190
    width = max(640, 100 + len(pids) * col_w)
    height = max(360, 150 + len(messages) * 48)
    x_of = {pid: 95 + i * col_w for i, pid in enumerate(pids)}

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<defs>',
        '<marker id="seqarrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#60a5fa"/>',
        '</marker>',
        '</defs>',
        f'<rect width="{width}" height="{height}" rx="14" fill="#13181d"/>',
        f'<text x="24" y="32" fill="#9fb3c8" font-family="sans-serif" font-size="13" font-weight="bold">{_xml_escape(title[:80])}</text>',
    ]
    for pid in pids:
        cx = x_of[pid]
        lbl = _xml_escape(participants[pid][:22])
        out.append(f'<line x1="{cx}" y1="84" x2="{cx}" y2="{height - 32}" stroke="#334155" stroke-width="2" stroke-dasharray="6,4"/>')
        out.append(f'<rect x="{cx - 72}" y="48" width="144" height="38" rx="8" fill="#1e293b" stroke="#60a5fa" stroke-width="2"/>')
        out.append(f'<text x="{cx}" y="72" fill="#f1f5f9" font-family="sans-serif" font-size="13" font-weight="600" text-anchor="middle">{lbl}</text>')

    for idx, (src, dst, arrow, msg) in enumerate(messages):
        y = 122 + idx * 46
        x1, x2 = x_of[src], x_of[dst]
        dash = ' stroke-dasharray="5,4"' if "--" in arrow else ""
        if x1 == x2:
            out.append(f'<path d="M {x1} {y} L {x1 + 42} {y} L {x1 + 42} {y + 20} L {x1} {y + 20}" fill="none" stroke="#60a5fa" stroke-width="2"{dash} marker-end="url(#seqarrow)"/>')
            out.append(f'<text x="{x1 + 48}" y="{y + 14}" fill="#e2e8f0" font-family="sans-serif" font-size="12">{_xml_escape(msg[:36])}</text>')
        else:
            out.append(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="#60a5fa" stroke-width="2"{dash} marker-end="url(#seqarrow)"/>')
            mx = (x1 + x2) / 2
            out.append(f'<text x="{mx:.1f}" y="{y - 7}" fill="#e2e8f0" font-family="sans-serif" font-size="12" text-anchor="middle">{_xml_escape(msg[:42])}</text>')

    out.append("</svg>")
    return "\n".join(out)


def sanitize_svg(source: str, title: str = "SVG Preview") -> str:
    """Sanitize untrusted SVG by stripping scripts, foreignObjects, event handlers, and external links."""
    cleaned = _SVG_DANGEROUS_TAG_RE.sub("", source)
    cleaned = _SVG_EVENT_ATTR_RE.sub("", cleaned)
    cleaned = _SVG_EXTERNAL_HREF_RE.sub("", cleaned)
    if "<svg" not in cleaned.lower():
        return render_document_preview_svg(source, "svg", "svg", title)
    return cleaned.strip()


def render_html_preview_svg(source: str, title: str = "HTML Live Preview") -> tuple[str, str]:
    """Generate both a visual UI wireframe SVG preview and a CSP-sandboxed HTML document."""
    headings = [
        re.sub(r"<[^>]+>", "", m).strip()
        for m in re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", source, flags=re.IGNORECASE | re.DOTALL)
    ]
    buttons = [
        re.sub(r"<[^>]+>", "", m).strip()
        for m in re.findall(r"<button[^>]*>(.*?)</button>", source, flags=re.IGNORECASE | re.DOTALL)
    ]
    inputs = re.findall(
        r"<input[^>]*placeholder=[\"']([^\"']+)[\"'][^>]*>",
        source,
        flags=re.IGNORECASE,
    )
    paragraphs = [
        re.sub(r"<[^>]+>", "", m).strip()
        for m in re.findall(r"<(?:p|li|div|span)[^>]*>(.*?)</(?:p|li|div|span)>", source, flags=re.IGNORECASE | re.DOTALL)
    ]
    paragraphs = [p for p in paragraphs if 2 <= len(p) <= 120][:8]

    width, height = 760, 460
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" rx="14" fill="#0f172a"/>',
        f'<rect x="16" y="16" width="{width - 32}" height="38" rx="8" fill="#1e293b"/>',
        '<circle cx="34" cy="35" r="6" fill="#ef4444"/>',
        '<circle cx="52" cy="35" r="6" fill="#f59e0b"/>',
        '<circle cx="70" cy="35" r="6" fill="#10b981"/>',
        f'<text x="96" y="40" fill="#cbd5e1" font-family="sans-serif" font-size="13" font-weight="600">{_xml_escape(title[:64])} · HTML/UI Preview</text>',
        f'<rect x="16" y="64" width="{width - 32}" height="{height - 80}" rx="10" fill="#f8fafc"/>',
    ]
    y = 104
    main_heading = headings[0] if headings else title
    svg.append(
        f'<text x="42" y="{y}" fill="#0f172a" font-family="sans-serif" font-size="22" font-weight="bold">{_xml_escape(main_heading[:52])}</text>'
    )
    y += 32

    for sub in headings[1:3]:
        svg.append(
            f'<text x="42" y="{y}" fill="#334155" font-family="sans-serif" font-size="16" font-weight="600">{_xml_escape(sub[:64])}</text>'
        )
        y += 26

    for p in paragraphs[:5]:
        svg.append(
            f'<text x="42" y="{y}" fill="#475569" font-family="sans-serif" font-size="13">{_xml_escape(p[:82])}</text>'
        )
        y += 22

    if inputs:
        y += 8
        for inp in inputs[:3]:
            svg.append(
                f'<rect x="42" y="{y}" width="320" height="34" rx="6" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.5"/>'
            )
            svg.append(
                f'<text x="54" y="{y + 22}" fill="#94a3b8" font-family="sans-serif" font-size="12">{_xml_escape(inp[:38])}</text>'
            )
            y += 44

    if buttons:
        bx = 42
        by = min(height - 68, max(y + 8, 340))
        for btn in buttons[:4]:
            lbl = _xml_escape(btn[:22] or "Action")
            bw = max(96, len(lbl) * 8 + 28)
            svg.append(
                f'<rect x="{bx}" y="{by}" width="{bw}" height="36" rx="8" fill="#2563eb"/>'
            )
            svg.append(
                f'<text x="{bx + bw / 2:.1f}" y="{by + 23}" fill="#ffffff" font-family="sans-serif" font-size="13" font-weight="600" text-anchor="middle">{lbl}</text>'
            )
            bx += bw + 12

    svg.append("</svg>")

    csp = (
        '<meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob:; "
        "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; "
        "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none';\">"
    )
    cleaned_html = _HTML_BLOCKED_TAG_RE.sub("", source)
    cleaned_html = _HTML_EXTERNAL_SCRIPT_LINK_RE.sub("", cleaned_html)
    if re.search(r"<\s*html\b", cleaned_html, flags=re.IGNORECASE):
        if re.search(r"<\s*head\b[^>]*>", cleaned_html, flags=re.IGNORECASE):
            sandboxed_doc = re.sub(
                r"(<\s*head\b[^>]*>)",
                lambda m: f"{m.group(1)}\n{csp}",
                cleaned_html,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            sandboxed_doc = f"{csp}\n{cleaned_html}"
    else:
        default_style = (
            "<style>"
            "*,*::before,*::after{box-sizing:border-box}"
            "body{margin:0;padding:28px;font-family:system-ui,-apple-system,sans-serif;"
            "background:#0f172a;color:#f8fafc;line-height:1.5}"
            "h1,h2,h3,h4{margin-top:0;color:#f8fafc}"
            "button{background:#2563eb;color:#fff;border:none;border-radius:8px;"
            "padding:9px 16px;font-weight:600;cursor:pointer;margin-right:8px}"
            "input,textarea,select{background:#1e293b;color:#f8fafc;border:1px solid #334155;"
            "border-radius:8px;padding:8px 12px;margin-right:8px}"
            ".card{background:#1e293b;border:1px solid #334155;border-radius:14px;padding:24px}"
            "</style>"
        )
        sandboxed_doc = (
            f"<!DOCTYPE html>\n<html><head>{csp}<title>{_xml_escape(title)}</title>"
            f"{default_style}</head><body>\n{cleaned_html}\n</body></html>"
        )
    return "\n".join(svg), sandboxed_doc


def find_chromium_executable(data_dir: Path | None = None) -> Path | None:
    """Locate Kira's local Chromium engine binary without downloading."""
    roots: list[Path] = []
    if data_dir is not None:
        roots.append(data_dir / "browser-engine")
        roots.append(data_dir.parent / "browser-engine")
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_path:
        roots.append(Path(env_path))
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    roots.append(xdg_data / "kira" / "browser-engine")
    xdg_cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    roots.append(xdg_cache / "ms-playwright")

    rel_candidates = (
        "chrome-linux64/chrome",
        "chrome-linux/chrome",
        "chrome-win64/chrome.exe",
        "chrome-win/chrome.exe",
        "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
        "chrome-mac-x64/Chromium.app/Contents/MacOS/Chromium",
        "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    )
    for root in roots:
        if not root.is_dir():
            continue
        with contextlib.suppress(OSError):
            dirs = sorted(
                (d for d in root.iterdir() if d.is_dir() and d.name.startswith("chromium-")),
                key=lambda p: p.name,
                reverse=True,
            )
            for cdir in dirs:
                for rel in rel_candidates:
                    exe = cdir / rel
                    if exe.is_file() and os.access(exe, os.X_OK):
                        return exe
    return None


def render_html_with_chromium_sync(
    html_file: Path,
    png_file: Path,
    data_dir: Path | None = None,
    width: int = 680,
    height: int = 440,
) -> bool:
    """Render a sandboxed HTML/Canvas/JS artifact to a PNG via Kira's local Chromium engine."""
    exe = find_chromium_executable(data_dir)
    if exe is None or not html_file.is_file():
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    for use_sandbox in (True, False):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    executable_path=str(exe),
                    headless=True,
                    chromium_sandbox=use_sandbox,
                    args=[
                        "--disable-background-networking",
                        "--disable-extensions",
                        "--disable-sync",
                        "--hide-scrollbars",
                        "--host-resolver-rules=MAP * ~NOTFOUND",
                    ],
                )
                try:
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                        java_script_enabled=True,
                        offline=True,
                        permissions=[],
                        service_workers="block",
                    )
                    page = context.new_page()
                    page.route(
                        "**/*",
                        lambda route: (
                            route.continue_()
                            if route.request.url.startswith(("file://", "data:", "blob:", "about:"))
                            else route.abort()
                        ),
                    )
                    page.goto(html_file.as_uri(), wait_until="load", timeout=6000)
                    page.mouse.move(int(width * 0.6), int(height * 0.43))
                    page.wait_for_timeout(65)
                    page.screenshot(path=str(png_file), type="png")
                finally:
                    browser.close()
            if png_file.is_file() and png_file.stat().st_size > 0:
                with contextlib.suppress(OSError):
                    os.chmod(png_file, 0o600)
                return True
        except Exception:
            continue
    return False


def render_document_preview_svg(source: str, kind: str, language: str, title: str = "Artifact") -> str:
    """Render Markdown or Code artifact into a visual SVG preview card."""
    lines = source.splitlines()[:18]
    width, height = 760, 440
    badge = f"{kind.upper()} · {language.upper()}" if language and language != kind else kind.upper()
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" rx="14" fill="#13181d"/>',
        f'<rect x="16" y="16" width="{width - 32}" height="38" rx="8" fill="#1e293b"/>',
        f'<text x="32" y="40" fill="#f1f5f9" font-family="sans-serif" font-size="14" font-weight="bold">{_xml_escape(title[:56])}</text>',
        f'<text x="{width - 32}" y="40" fill="#60a5fa" font-family="monospace" font-size="12" text-anchor="end">{_xml_escape(badge[:28])}</text>',
    ]
    y = 80
    for idx, raw in enumerate(lines, start=1):
        if kind == "markdown" and raw.lstrip().startswith("#"):
            clean_h = raw.lstrip("#").strip()
            out.append(
                f'<text x="32" y="{y}" fill="#60a5fa" font-family="sans-serif" font-size="16" font-weight="bold">{_xml_escape(clean_h[:68])}</text>'
            )
        else:
            out.append(
                f'<text x="28" y="{y}" fill="#64748b" font-family="monospace" font-size="12">{idx:2d}</text>'
            )
            out.append(
                f'<text x="58" y="{y}" fill="#e2e8f0" font-family="monospace" font-size="12">{_xml_escape(raw.expandtabs(4)[:82])}</text>'
            )
        y += 20
        if y > height - 24:
            break
    out.append("</svg>")
    return "\n".join(out)


def patch_lines_in_text(source: str, start_line: int, end_line: int, replacement: str) -> str:
    """Replace 1-indexed inclusive [start_line..end_line] in source with replacement."""
    lines = source.splitlines()
    had_trailing_nl = source.endswith("\n")
    lo = max(1, min(start_line, len(lines) + 1))
    hi = max(lo - 1, min(end_line, len(lines)))
    rep_lines = replacement.splitlines() if replacement else []
    new_lines = lines[: lo - 1] + rep_lines + lines[hi:]
    result = "\n".join(new_lines)
    if (had_trailing_nl or replacement.endswith("\n")) and result and not result.endswith("\n"):
        result += "\n"
    return result


def compute_hunks(old_text: str, new_text: str, path_label: str = "artifact") -> tuple[str, list[Json]]:
    """Compute unified diff text and structured hunks between old_text and new_text."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
            lineterm="",
        )
    )
    diff_text = "\n".join(diff_lines)
    return diff_text, parse_unified_diff_hunks(diff_text, default_path=path_label)


def parse_unified_diff_hunks(diff_text: str, default_path: str = "") -> list[Json]:
    """Parse a unified diff into structured interactive hunks."""
    hunks: list[Json] = []
    current_path = default_path
    current: Json | None = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            target = raw_line[4:].strip()
            if target.startswith("b/"):
                target = target[2:]
            if target and target != "/dev/null":
                current_path = target
            continue
        if raw_line.startswith("--- ") or raw_line.startswith("diff --git ") or raw_line.startswith("index "):
            continue

        m = _HUNK_HEADER_RE.match(raw_line)
        if m:
            if current is not None:
                current["added_count"] = current["added"]
                current["removed_count"] = current["removed"]
                current["lines"] = current["raw_lines"]
                current["preview"] = "\n".join(current["raw_lines"][:30])
                hunks.append(current)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            section_hint = (m.group(5) or "").strip()
            current = {
                "index": len(hunks),
                "path": current_path,
                "header": raw_line,
                "section": section_hint,
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "added": 0,
                "removed": 0,
                "added_count": 0,
                "removed_count": 0,
                "old_block": [],
                "new_block": [],
                "raw_lines": [raw_line],
                "lines": [raw_line],
            }
            continue

        if current is not None:
            if raw_line.startswith("\\ No newline"):
                continue
            current["raw_lines"].append(raw_line)
            if raw_line.startswith("+"):
                current["added"] += 1
                current["new_block"].append(raw_line[1:])
            elif raw_line.startswith("-"):
                current["removed"] += 1
                current["old_block"].append(raw_line[1:])
            elif raw_line.startswith(" "):
                current["old_block"].append(raw_line[1:])
                current["new_block"].append(raw_line[1:])

    if current is not None:
        current["added_count"] = current["added"]
        current["removed_count"] = current["removed"]
        current["lines"] = current["raw_lines"]
        current["preview"] = "\n".join(current["raw_lines"][:30])
        hunks.append(current)
    return hunks


def reject_hunk_in_text(current_text: str, hunk: Json) -> str:
    """Revert a single hunk (replacing its new_block with old_block) in current_text."""
    lines = current_text.splitlines()
    had_trailing_nl = current_text.endswith("\n")
    new_block: list[str] = list(hunk.get("new_block", []))
    old_block: list[str] = list(hunk.get("old_block", []))
    target_idx = max(0, int(hunk.get("new_start", 1)) - 1)
    block_len = len(new_block)

    match_pos: int | None = None
    if block_len == 0:
        match_pos = min(target_idx, len(lines))
    elif lines[target_idx : target_idx + block_len] == new_block:
        match_pos = target_idx
    else:
        for delta in range(-40, 41):
            cand = target_idx + delta
            if 0 <= cand <= len(lines) - block_len and lines[cand : cand + block_len] == new_block:
                match_pos = cand
                break

    if match_pos is None:
        raise KiraError(
            "HUNK_CONFLICT",
            "The selected diff hunk no longer matches the current file lines; refresh the diff first.",
        )

    updated = lines[:match_pos] + old_block + lines[match_pos + block_len :]
    result = "\n".join(updated)
    if had_trailing_nl and result and not result.endswith("\n"):
        result += "\n"
    return result


class CanvasService:
    """Service-owned per-project Interactive Canvas / Artifacts and Granular Diff Hunk manager."""

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

    def _canvas_dir(self, project_id: str) -> Path:
        base = private_directory(self.paths.data / "canvas")
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id)[:96]
        return private_directory(base / safe_id)

    def _preview_dir(self, project_id: str) -> Path:
        return private_directory(self._canvas_dir(project_id) / "previews")

    def _db_path(self, project_id: str) -> Path:
        return self._canvas_dir(project_id) / "canvas.db"

    @staticmethod
    def _open_db(db_path: Path) -> sqlite3.Connection:
        if db_path.is_symlink():
            raise KiraError("UNSAFE_PATH", "Canvas database must not be a symlink")
        conn = sqlite3.connect(db_path, timeout=10.0)
        with contextlib.suppress(OSError):
            os.chmod(db_path, 0o600)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS canvas_artifacts (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                language TEXT NOT NULL,
                current_revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_canvas_project ON canvas_artifacts(project_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS canvas_revisions (
                artifact_id TEXT NOT NULL REFERENCES canvas_artifacts(id) ON DELETE CASCADE,
                revision INTEGER NOT NULL,
                content TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'user',
                sha256 TEXT NOT NULL,
                preview_svg_path TEXT NOT NULL DEFAULT '',
                preview_html_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (artifact_id, revision)
            );
            """
        )
        return conn

    def _render_preview_files(
        self,
        project_id: str,
        artifact_id: str,
        revision: int,
        title: str,
        kind: str,
        language: str,
        content: str,
    ) -> tuple[str, str]:
        pdir = self._preview_dir(project_id)
        safe_aid = re.sub(r"[^A-Za-z0-9_.-]", "_", artifact_id)[:64]
        svg_path = pdir / f"{safe_aid}-r{revision}.svg"
        html_path_str = ""

        if kind == "mermaid":
            svg_text = render_mermaid_to_svg(content, title)
        elif kind == "svg":
            svg_text = sanitize_svg(content, title)
        elif kind == "html":
            svg_text, sandboxed_html = render_html_preview_svg(content, title)
            html_file = pdir / f"{safe_aid}-r{revision}.html"
            html_file.write_text(sandboxed_html, encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(html_file, 0o600)
            html_path_str = str(html_file)
            png_file = pdir / f"{safe_aid}-r{revision}.png"
            render_html_with_chromium_sync(html_file, png_file, self.paths.data)
        else:
            svg_text = render_document_preview_svg(content, kind, language, title)

        svg_path.write_text(svg_text, encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(svg_path, 0o600)
        return str(svg_path), html_path_str

    def _format_artifact_row(
        self,
        art_row: sqlite3.Row,
        rev_row: sqlite3.Row | None = None,
        history: list[Json] | None = None,
    ) -> Json:
        item: Json = {
            "id": art_row["id"],
            "project_id": art_row["project_id"],
            "session_id": art_row["session_id"],
            "title": art_row["title"],
            "kind": art_row["kind"],
            "language": art_row["language"],
            "current_revision": int(art_row["current_revision"]),
            "created_at": art_row["created_at"],
            "updated_at": art_row["updated_at"],
        }
        if rev_row is not None:
            svg_path = str(rev_row["preview_svg_path"] or "")
            html_path = str(rev_row["preview_html_path"] or "")
            content = str(rev_row["content"])
            rev_num = int(rev_row["revision"])
            if art_row["kind"] == "html" and svg_path:
                png_candidate = Path(svg_path).with_suffix(".png")
                if not png_candidate.is_file():
                    svg_path, html_path = self._render_preview_files(
                        str(art_row["project_id"]),
                        str(art_row["id"]),
                        rev_num,
                        str(art_row["title"]),
                        "html",
                        str(art_row["language"]),
                        content,
                    )
            preview_target = ""
            if art_row["kind"] == "html" and svg_path and Path(svg_path).with_suffix(".png").is_file():
                preview_target = Path(svg_path).with_suffix(".png").as_uri()
            elif svg_path and Path(svg_path).exists():
                preview_target = Path(svg_path).as_uri()
            item.update(
                revision=rev_num,
                content=content,
                line_count=max(1, len(content.splitlines())),
                summary=rev_row["summary"],
                origin=rev_row["origin"],
                sha256=rev_row["sha256"],
                preview_svg_path=svg_path,
                preview_url=preview_target,
                preview_html_path=html_path,
                preview_html_url=Path(html_path).as_uri() if html_path and Path(html_path).exists() else "",
            )
        if history is not None:
            item["history"] = history
        return item

    def _list_sync(self, project_id: str, *, kind: str = "", limit: int = 50) -> list[Json]:
        conn = self._open_db(self._db_path(project_id))
        try:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM canvas_artifacts WHERE project_id=? AND kind=? ORDER BY updated_at DESC LIMIT ?",
                    (project_id, kind, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM canvas_artifacts WHERE project_id=? ORDER BY updated_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            out: list[Json] = []
            for r in rows:
                rev = conn.execute(
                    "SELECT * FROM canvas_revisions WHERE artifact_id=? AND revision=?",
                    (r["id"], r["current_revision"]),
                ).fetchone()
                out.append(self._format_artifact_row(r, rev))
            return out
        finally:
            conn.close()

    def _get_sync(self, project_id: str, artifact_id: str, revision: int | None = None) -> Json:
        conn = self._open_db(self._db_path(project_id))
        try:
            art = conn.execute(
                "SELECT * FROM canvas_artifacts WHERE id=? AND project_id=?",
                (artifact_id, project_id),
            ).fetchone()
            if art is None:
                raise KiraError("NOT_FOUND", f"Canvas artifact not found: {artifact_id}")
            target_rev = revision if revision is not None else int(art["current_revision"])
            rev = conn.execute(
                "SELECT * FROM canvas_revisions WHERE artifact_id=? AND revision=?",
                (artifact_id, target_rev),
            ).fetchone()
            if rev is None:
                raise KiraError("NOT_FOUND", f"Canvas revision {target_rev} not found")
            hist_rows = conn.execute(
                "SELECT revision, summary, origin, sha256, created_at FROM canvas_revisions "
                "WHERE artifact_id=? ORDER BY revision DESC LIMIT 30",
                (artifact_id,),
            ).fetchall()
            history = [
                {
                    "revision": int(h["revision"]),
                    "summary": h["summary"],
                    "origin": h["origin"],
                    "sha256": h["sha256"],
                    "created_at": h["created_at"],
                }
                for h in hist_rows
            ]
            return self._format_artifact_row(art, rev, history)
        finally:
            conn.close()

    def _create_sync(
        self,
        project_id: str,
        *,
        session_id: str = "",
        title: str,
        kind: str,
        language: str,
        content: str,
        summary: str = "Initial version",
        origin: str = "user",
    ) -> Json:
        conn = self._open_db(self._db_path(project_id))
        try:
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM canvas_artifacts WHERE project_id=?", (project_id,)
                ).fetchone()[0]
            )
            if count >= MAX_ARTIFACTS_PER_PROJECT:
                raise KiraError("LIMIT_EXCEEDED", "Maximum Canvas artifacts per project reached")
            artifact_id = new_id()
            ts = now()
            sha = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()
            svg_path, html_path = self._render_preview_files(
                project_id, artifact_id, 1, title, kind, language, content
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO canvas_artifacts (
                        id, project_id, session_id, title, kind, language, current_revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (artifact_id, project_id, session_id, title, kind, language, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO canvas_revisions (
                        artifact_id, revision, content, summary, origin, sha256, preview_svg_path, preview_html_path, created_at
                    ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (artifact_id, content, summary, origin, sha, svg_path, html_path, ts),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return self._get_sync(project_id, artifact_id)
        finally:
            conn.close()

    def _update_sync(
        self,
        project_id: str,
        artifact_id: str,
        *,
        content: str | None = None,
        title: str = "",
        start_line: int | None = None,
        end_line: int | None = None,
        replacement: str | None = None,
        summary: str = "Updated artifact",
        origin: str = "user",
    ) -> Json:
        conn = self._open_db(self._db_path(project_id))
        try:
            art = conn.execute(
                "SELECT * FROM canvas_artifacts WHERE id=? AND project_id=?",
                (artifact_id, project_id),
            ).fetchone()
            if art is None:
                raise KiraError("NOT_FOUND", f"Canvas artifact not found: {artifact_id}")
            curr_rev_num = int(art["current_revision"])
            if curr_rev_num >= MAX_REVISIONS_PER_ARTIFACT:
                raise KiraError("LIMIT_EXCEEDED", "Maximum revisions for this Canvas artifact reached")
            curr_rev = conn.execute(
                "SELECT * FROM canvas_revisions WHERE artifact_id=? AND revision=?",
                (artifact_id, curr_rev_num),
            ).fetchone()
            assert curr_rev is not None
            old_content = str(curr_rev["content"])

            if start_line is not None and end_line is not None:
                new_content = patch_lines_in_text(
                    old_content, start_line, end_line, replacement or ""
                )
            elif content is not None:
                new_content = content
            else:
                raise KiraError("INVALID_ARGUMENT", "Either content or start_line..end_line is required")

            if len(new_content.encode("utf-8", "replace")) > MAX_ARTIFACT_CONTENT_BYTES:
                raise KiraError("INVALID_ARGUMENT", "Updated Canvas content exceeds size limit")

            new_title = title.strip() if title and title.strip() else str(art["title"])
            next_rev = curr_rev_num + 1
            ts = now()
            sha = hashlib.sha256(new_content.encode("utf-8", "replace")).hexdigest()
            svg_path, html_path = self._render_preview_files(
                project_id,
                artifact_id,
                next_rev,
                new_title,
                str(art["kind"]),
                str(art["language"]),
                new_content,
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE canvas_artifacts SET title=?, current_revision=?, updated_at=? WHERE id=?",
                    (new_title, next_rev, ts, artifact_id),
                )
                conn.execute(
                    """
                    INSERT INTO canvas_revisions (
                        artifact_id, revision, content, summary, origin, sha256, preview_svg_path, preview_html_path, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (artifact_id, next_rev, new_content, summary, origin, sha, svg_path, html_path, ts),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return self._get_sync(project_id, artifact_id)
        finally:
            conn.close()

    def _restore_sync(self, project_id: str, artifact_id: str, target_revision: int) -> Json:
        target = self._get_sync(project_id, artifact_id, revision=target_revision)
        return self._update_sync(
            project_id,
            artifact_id,
            content=target["content"],
            summary=f"Restored revision {target_revision}",
            origin="restore",
        )

    def _delete_sync(self, project_id: str, artifact_id: str) -> Json:
        conn = self._open_db(self._db_path(project_id))
        try:
            cur = conn.execute(
                "DELETE FROM canvas_artifacts WHERE id=? AND project_id=?",
                (artifact_id, project_id),
            )
            conn.commit()
            return {"ok": True, "deleted": cur.rowcount > 0, "artifact_id": artifact_id}
        finally:
            conn.close()

    def _diff_sync(
        self,
        project_id: str,
        artifact_id: str,
        from_revision: int | None = None,
        to_revision: int | None = None,
    ) -> Json:
        conn = self._open_db(self._db_path(project_id))
        try:
            art = conn.execute(
                "SELECT * FROM canvas_artifacts WHERE id=? AND project_id=?",
                (artifact_id, project_id),
            ).fetchone()
            if art is None:
                raise KiraError("NOT_FOUND", f"Canvas artifact not found: {artifact_id}")
            curr_rev = int(art["current_revision"])
            to_rev = to_revision if to_revision is not None else curr_rev
            from_rev = from_revision if from_revision is not None else max(1, to_rev - 1)
            r_from = conn.execute(
                "SELECT content FROM canvas_revisions WHERE artifact_id=? AND revision=?",
                (artifact_id, from_rev),
            ).fetchone()
            r_to = conn.execute(
                "SELECT content FROM canvas_revisions WHERE artifact_id=? AND revision=?",
                (artifact_id, to_rev),
            ).fetchone()
            if r_from is None or r_to is None:
                raise KiraError("NOT_FOUND", "Requested Canvas revision for diff was not found")
            diff_text, hunks = compute_hunks(
                str(r_from["content"]), str(r_to["content"]), str(art["title"])
            )
            return {
                "ok": True,
                "artifact_id": artifact_id,
                "from_revision": from_rev,
                "to_revision": to_rev,
                "diff": diff_text,
                "hunks": hunks,
            }
        finally:
            conn.close()

    def _apply_hunk_sync(
        self,
        project_id: str,
        artifact_id: str,
        hunk_index: int,
        decision: str,
        from_revision: int | None = None,
        to_revision: int | None = None,
    ) -> Json:
        diff_info = self._diff_sync(project_id, artifact_id, from_revision, to_revision)
        hunks = diff_info["hunks"]
        if not 0 <= hunk_index < len(hunks):
            raise KiraError("INVALID_ARGUMENT", f"Invalid hunk_index {hunk_index}")
        if decision == "accept":
            artifact = self._get_sync(project_id, artifact_id)
            return {
                "ok": True,
                "decision": "accept",
                "hunk_index": hunk_index,
                "artifact": artifact,
            }
        # decision == "reject": revert this hunk on current content
        current = self._get_sync(project_id, artifact_id)
        reverted_text = reject_hunk_in_text(current["content"], hunks[hunk_index])
        updated = self._update_sync(
            project_id,
            artifact_id,
            content=reverted_text,
            summary=f"Rejected hunk #{hunk_index + 1} ({hunks[hunk_index]['header']})",
            origin="hunk_reject",
        )
        return {
            "ok": True,
            "decision": "reject",
            "hunk_index": hunk_index,
            "artifact": updated,
        }

    def _workspace_hunks_sync(self, root: Path, rel_path: str = "") -> Json:
        if not root.is_dir() or root.is_symlink():
            raise KiraError("UNSAFE_PATH", "Project root must be a real directory")
        target = _relative(rel_path) if rel_path else ""
        if target and _private(target):
            raise KiraError("PATH_DENIED", "Private or secret files cannot be diffed")
        args = ["diff", "--no-ext-diff", "--unified=3", "HEAD", "--"]
        if target:
            args.append(target)
        code, out, _ = _git(root, args)
        if code not in {0, 1}:
            return {"ok": True, "available": False, "path": target, "hunks": [], "diff": ""}
        diff_text = self.redactor.text(out.decode("utf-8", "replace"))
        hunks = [
            h
            for h in parse_unified_diff_hunks(diff_text, default_path=target)
            if not _private(h.get("path", ""))
        ]
        for idx, h in enumerate(hunks):
            h["index"] = idx
        return {
            "ok": True,
            "available": True,
            "path": target,
            "diff": diff_text,
            "hunks": hunks,
        }

    def _workspace_apply_hunk_sync(
        self, root: Path, rel_path: str, hunk_index: int, decision: str
    ) -> Json:
        info = self._workspace_hunks_sync(root, rel_path)
        hunks = info["hunks"]
        if not 0 <= hunk_index < len(hunks):
            raise KiraError("INVALID_ARGUMENT", f"Invalid workspace hunk_index {hunk_index}")
        hunk = hunks[hunk_index]
        target_rel = _relative(hunk["path"] or rel_path)
        if _private(target_rel):
            raise KiraError("PATH_DENIED", "Refusing to modify private file")
        if decision == "accept":
            return {"ok": True, "decision": "accept", "path": target_rel, "hunk_index": hunk_index}

        full_path = (root / target_rel).resolve()
        try:
            full_path.relative_to(root.resolve())
        except ValueError as exc:
            raise KiraError("PATH_DENIED", "File is outside the project root") from exc
        if not full_path.is_file() or full_path.is_symlink():
            raise KiraError("UNSAFE_PATH", "Target file must be a regular file")

        current_text = full_path.read_text(encoding="utf-8")
        reverted_text = reject_hunk_in_text(current_text, hunk)
        full_path.write_text(reverted_text, encoding="utf-8")
        refreshed = self._workspace_hunks_sync(root, rel_path)
        return {
            "ok": True,
            "decision": "reject",
            "path": target_rel,
            "hunk_index": hunk_index,
            "hunks": refreshed["hunks"],
            "diff": refreshed["diff"],
        }

    def attach(
        self,
        session: Json,
        project: Json,
        run_id: str,
        cancel: asyncio.Event,
        mode: str,
        options: Json,
    ) -> Json:
        """Attach kira.canvas client tool to supported agent runs."""
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
            tool for tool in canvas_tool_specs() if tool["name"] not in existing
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
                raise KiraError("CANCELLED", "The originating run no longer permits canvas calls")
            return self.service.effective_project(run_id, actual_project)

        async def call(call_id: str, name: str, arguments: Json) -> Json:
            if name not in CANVAS_TOOL_NAMES:
                if previous is None:
                    raise KiraError("UNSUPPORTED", "This callback is not registered")
                return dict(await previous(call_id, name, arguments))
            return await self._handle_tool_call(run_id, session["id"], mode, call_id, arguments, active)

        result.update(client_tools=namespaces, client_tool_call=call)
        result["assistant_instructions"] = result.get("assistant_instructions", "") + (
            "\nInteractive Canvas / Artifacts available: use kira.canvas to create, update, "
            "or patch_lines on visual artifacts (kind: mermaid, svg, html, markdown, code). "
            "When the user references a Canvas artifact ID and line range, prefer action='patch_lines' "
            "to rewrite only the requested lines."
        )
        return result

    async def _handle_tool_call(
        self,
        run_id: str,
        session_id: str,
        mode: str,
        call_id: str,
        arguments: Json,
        active: Any,
    ) -> Json:
        try:
            actual_project = active()
            if not isinstance(arguments, dict) or set(arguments) != {"action", "payload"}:
                raise KiraError("INVALID_ARGUMENT", "kira.canvas requires action and payload")
            action, payload = validate_canvas_action(
                arguments.get("action"), arguments.get("payload")
            )
            if action in {"create", "update", "patch_lines"} and mode != "execute":
                raise KiraError("PERMISSION_DENIED", "Canvas modifications require execute mode")
        except KiraError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}

        await self.service.emit(
            run_id,
            "tool.started",
            {"tool_id": call_id, "name": "kira.canvas", "arguments": arguments},
        )
        try:
            pid = actual_project["id"]
            async with self._lock(pid):
                if action == "list":
                    items = await asyncio.to_thread(
                        self._list_sync, pid, kind=payload["kind"], limit=payload["limit"]
                    )
                    response: Json = {"ok": True, "artifacts": items, "contracts": canvas_action_contract()}
                elif action == "get":
                    art = await asyncio.to_thread(
                        self._get_sync, pid, payload["artifact_id"], payload["revision"]
                    )
                    response = {"ok": True, "artifact": art}
                elif action == "create":
                    art = await asyncio.to_thread(
                        self._create_sync,
                        pid,
                        session_id=session_id,
                        title=payload["title"],
                        kind=payload["kind"],
                        language=payload["language"],
                        content=payload["content"],
                        summary=payload["summary"],
                        origin=f"agent:{run_id[:8]}",
                    )
                    response = {"ok": True, "artifact": art}
                elif action == "update":
                    art = await asyncio.to_thread(
                        self._update_sync,
                        pid,
                        payload["artifact_id"],
                        content=payload["content"],
                        title=payload["title"],
                        summary=payload["summary"],
                        origin=f"agent:{run_id[:8]}",
                    )
                    response = {"ok": True, "artifact": art}
                elif action == "patch_lines":
                    art = await asyncio.to_thread(
                        self._update_sync,
                        pid,
                        payload["artifact_id"],
                        start_line=payload["start_line"],
                        end_line=payload["end_line"],
                        replacement=payload["replacement"],
                        summary=payload["summary"],
                        origin=f"agent:{run_id[:8]}",
                    )
                    response = {"ok": True, "artifact": art}
                else:
                    response = await asyncio.to_thread(
                        self._diff_sync,
                        pid,
                        payload["artifact_id"],
                        payload["from_revision"],
                        payload["to_revision"],
                    )
        except KiraError as exc:
            response = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            response = {
                "ok": False,
                "error": {"code": "CANVAS_ERROR", "message": self.redactor.text(str(exc))[:300]},
            }

        response = self._bound_client_tool_response(self.redactor.clean(response), action)
        await self.service.emit(
            run_id,
            "tool.completed",
            {"tool_id": call_id, "name": "kira.canvas", "result": response},
        )
        return response

    @staticmethod
    def _bound_client_tool_response(response: Json, action: str) -> Json:
        """Keep kira.canvas client-tool callback payloads well below the 64 KiB wire limit."""
        max_bytes = 48_000

        def _encoded_len(obj: Any) -> int:
            return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

        if not isinstance(response, dict):
            return response

        # For list actions, omit full artifact bodies so multiple HTML/WebGL artifacts never overflow 64 KiB.
        if action == "list" and isinstance(response.get("artifacts"), list):
            compact_items: list[dict[str, Any]] = []
            for item in response["artifacts"]:
                if not isinstance(item, dict):
                    continue
                entry = {k: v for k, v in item.items() if k not in {"content", "revisions"}}
                raw_content = str(item.get("content") or "")
                entry["content_preview"] = raw_content[:360] + ("…" if len(raw_content) > 360 else "")
                compact_items.append(entry)
            response = {**response, "artifacts": compact_items}

        if _encoded_len(response) <= max_bytes:
            return response

        # Step 1: compact diff hunks on artifact or top-level diff response.
        if isinstance(response.get("artifact"), dict):
            art = dict(response["artifact"])
            art.pop("revisions", None)
            if isinstance(art.get("diff"), dict):
                diff_obj = dict(art["diff"])
                raw_hunks = diff_obj.get("hunks")
                if isinstance(raw_hunks, list):
                    diff_obj["hunks"] = [
                        {
                            "index": h.get("index"),
                            "header": h.get("header"),
                            "old_start": h.get("old_start"),
                            "old_count": h.get("old_count"),
                            "new_start": h.get("new_start"),
                            "new_count": h.get("new_count"),
                            "added": h.get("added"),
                            "removed": h.get("removed"),
                        }
                        for h in raw_hunks
                        if isinstance(h, dict)
                    ]
                art["diff"] = diff_obj
            response = {**response, "artifact": art}

        if isinstance(response.get("hunks"), list):
            response = {
                **response,
                "hunks": [
                    {
                        "index": h.get("index"),
                        "header": h.get("header"),
                        "old_start": h.get("old_start"),
                        "old_count": h.get("old_count"),
                        "new_start": h.get("new_start"),
                        "new_count": h.get("new_count"),
                        "added": h.get("added"),
                        "removed": h.get("removed"),
                    }
                    for h in response["hunks"][:60]
                    if isinstance(h, dict)
                ],
            }

        if _encoded_len(response) <= max_bytes:
            return response

        # Step 2: truncate echoed artifact content in the callback payload (full content remains stored on disk/SQLite).
        if isinstance(response.get("artifact"), dict):
            art = dict(response["artifact"])
            content = str(art.get("content") or "")
            if content:
                art["content"] = (
                    content[:12_000]
                    + f"\n/* ... [contenuto salvato integralmente nel Canvas ({len(content)} caratteri); "
                    "usa patch_lines o get per ispezionare] ... */"
                )
                art["content_truncated"] = True
            response = {**response, "artifact": art}

        return response

    async def dispatch(self, method: str, params: Json) -> Json:
        if not isinstance(params, dict) or "project_id" not in params:
            raise KiraError("INVALID_ARGUMENT", "project_id is required")
        project_id = params.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 128:
            raise KiraError("INVALID_ARGUMENT", "project_id must be bounded text")
        project = self.store.one("projects", project_id)

        if method == "canvas.list":
            if set(params) - {"project_id", "kind", "limit"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.list parameter")
            _, clean = validate_canvas_action(
                "list", {k: v for k, v in params.items() if k != "project_id"}
            )
            async with self._lock(project_id):
                items = await asyncio.to_thread(
                    self._list_sync, project_id, kind=clean["kind"], limit=clean["limit"]
                )
            return {"artifacts": self.redactor.clean(items)}

        if method == "canvas.get":
            if set(params) - {"project_id", "artifact_id", "revision"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.get parameter")
            _, clean = validate_canvas_action(
                "get", {k: v for k, v in params.items() if k != "project_id"}
            )
            async with self._lock(project_id):
                art = await asyncio.to_thread(
                    self._get_sync, project_id, clean["artifact_id"], clean["revision"]
                )
            return {"artifact": self.redactor.clean(art)}

        if method == "canvas.create":
            if set(params) - {"project_id", "session_id", "title", "kind", "language", "content", "summary"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.create parameter")
            session_id = str(params.get("session_id") or "").strip()[:128]
            _, clean = validate_canvas_action(
                "create",
                {k: v for k, v in params.items() if k not in {"project_id", "session_id"}},
            )
            async with self._lock(project_id):
                art = await asyncio.to_thread(
                    self._create_sync,
                    project_id,
                    session_id=session_id,
                    title=clean["title"],
                    kind=clean["kind"],
                    language=clean["language"],
                    content=clean["content"],
                    summary=clean["summary"],
                    origin="user",
                )
            return {"artifact": self.redactor.clean(art)}

        if method == "canvas.update":
            allowed = {
                "project_id",
                "artifact_id",
                "title",
                "content",
                "start_line",
                "end_line",
                "replacement",
                "summary",
            }
            if set(params) - allowed:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.update parameter")
            if "start_line" in params or "end_line" in params:
                _, clean = validate_canvas_action(
                    "patch_lines",
                    {
                        "artifact_id": params.get("artifact_id"),
                        "start_line": params.get("start_line"),
                        "end_line": params.get("end_line"),
                        "replacement": params.get("replacement", params.get("content", "")),
                        "summary": params.get("summary", ""),
                    },
                )
                async with self._lock(project_id):
                    art = await asyncio.to_thread(
                        self._update_sync,
                        project_id,
                        clean["artifact_id"],
                        start_line=clean["start_line"],
                        end_line=clean["end_line"],
                        replacement=clean["replacement"],
                        summary=clean["summary"],
                        origin="user",
                    )
            else:
                _, clean = validate_canvas_action(
                    "update",
                    {k: v for k, v in params.items() if k != "project_id"},
                )
                async with self._lock(project_id):
                    art = await asyncio.to_thread(
                        self._update_sync,
                        project_id,
                        clean["artifact_id"],
                        content=clean["content"],
                        title=clean["title"],
                        summary=clean["summary"],
                        origin="user",
                    )
            return {"artifact": self.redactor.clean(art)}

        if method == "canvas.restore":
            if set(params) - {"project_id", "artifact_id", "revision"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.restore parameter")
            _, clean = validate_canvas_action(
                "get", {k: v for k, v in params.items() if k != "project_id"}
            )
            if clean["revision"] is None:
                raise KiraError("INVALID_ARGUMENT", "revision is required for canvas.restore")
            async with self._lock(project_id):
                art = await asyncio.to_thread(
                    self._restore_sync, project_id, clean["artifact_id"], clean["revision"]
                )
            return {"artifact": self.redactor.clean(art)}

        if method == "canvas.delete":
            if set(params) - {"project_id", "artifact_id"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.delete parameter")
            artifact_id = params.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id.strip():
                raise KiraError("INVALID_ARGUMENT", "artifact_id is required")
            async with self._lock(project_id):
                res = await asyncio.to_thread(self._delete_sync, project_id, artifact_id.strip())
            return res

        if method == "canvas.diff":
            if set(params) - {"project_id", "artifact_id", "from_revision", "to_revision"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.diff parameter")
            _, clean = validate_canvas_action(
                "diff", {k: v for k, v in params.items() if k != "project_id"}
            )
            async with self._lock(project_id):
                res = await asyncio.to_thread(
                    self._diff_sync,
                    project_id,
                    clean["artifact_id"],
                    clean["from_revision"],
                    clean["to_revision"],
                )
            return self.redactor.clean(res)

        if method == "canvas.apply_hunk":
            if set(params) - {"project_id", "artifact_id", "hunk_index", "decision", "from_revision", "to_revision"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.apply_hunk parameter")
            artifact_id = str(params.get("artifact_id") or "").strip()
            hunk_index = params.get("hunk_index")
            decision = str(params.get("decision") or "").strip().lower()
            if not artifact_id or not isinstance(hunk_index, int) or decision not in HUNK_DECISIONS:
                raise KiraError("INVALID_ARGUMENT", "Invalid canvas.apply_hunk arguments")
            async with self._lock(project_id):
                res = await asyncio.to_thread(
                    self._apply_hunk_sync,
                    project_id,
                    artifact_id,
                    hunk_index,
                    decision,
                    params.get("from_revision"),
                    params.get("to_revision"),
                )
            return self.redactor.clean(res)

        if method == "canvas.workspace_hunks":
            if set(params) - {"project_id", "path"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.workspace_hunks parameter")
            rel_path = str(params.get("path") or "").strip()
            async with self._lock(project_id):
                res = await asyncio.to_thread(
                    self._workspace_hunks_sync, Path(project["root"]), rel_path
                )
            return self.redactor.clean(res)

        if method == "canvas.workspace_apply_hunk":
            if set(params) - {"project_id", "path", "hunk_index", "decision"}:
                raise KiraError("INVALID_ARGUMENT", "Unknown canvas.workspace_apply_hunk parameter")
            if project["permission"] not in WRITE_PERMISSIONS:
                raise KiraError("PERMISSION_DENIED", "This project is read-only")
            rel_path = str(params.get("path") or "").strip()
            hunk_index = params.get("hunk_index")
            decision = str(params.get("decision") or "").strip().lower()
            if isinstance(hunk_index, bool) or not isinstance(hunk_index, int) or decision not in HUNK_DECISIONS:
                raise KiraError("INVALID_ARGUMENT", "Invalid canvas.workspace_apply_hunk arguments")
            async with self._lock(project_id):
                res = await asyncio.to_thread(
                    self._workspace_apply_hunk_sync,
                    Path(project["root"]),
                    rel_path,
                    hunk_index,
                    decision,
                )
            return self.redactor.clean(res)

        raise KiraError("METHOD_NOT_FOUND", f"Unknown canvas method: {method}")
