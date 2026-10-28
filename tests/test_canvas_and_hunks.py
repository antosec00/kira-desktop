"""Tests for Interactive Canvas, offline Mermaid/SVG/HTML previews, inline line patching, and granular hunk accept/reject."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from kira.domain.canvas import canvas_tool_specs, validate_canvas_action
from kira.domain.paths import Paths
from kira.service.canvas import (
    compute_hunks,
    patch_lines_in_text,
    reject_hunk_in_text,
    render_html_preview_svg,
    render_mermaid_to_svg,
    sanitize_svg,
)
from kira.service.server import Service


class ScriptedCanvasBackend:
    """Synthetic backend that invokes kira.canvas client tool during a run."""

    def __init__(self, calls):
        self.calls = calls
        self.results = []
        self.client_tools = []
        self.instructions = ""

    async def discover(self):
        return {"available": True, "models": [{"id": "m1", "name": "M1"}]}

    async def start_login(self):
        return {"url": None, "message": "not required"}

    async def run(self, ctx):
        self.client_tools = list(ctx.client_tools)
        self.instructions = ctx.assistant_instructions
        callback = ctx.client_tool_call
        assert callback is not None
        for index, (name, arguments) in enumerate(self.calls, 1):
            result = await callback(f"call_{index}", name, arguments)
            self.results.append(result)
        await ctx.emit("message.completed", {"content": "Done"})
        return {"state": "completed", "outcome": {"ok": True}}

    async def close(self):
        pass


def test_canvas_domain_validation_and_tool_specs() -> None:
    specs = canvas_tool_specs()
    assert len(specs) == 1
    assert specs[0]["name"] == "canvas"

    act, payload = validate_canvas_action(
        "create",
        {
            "title": "Architecture",
            "kind": "mermaid",
            "content": "flowchart TD\n  A[Start] --> B[End]",
        },
    )
    assert act == "create"
    assert payload["kind"] == "mermaid"

    act_patch, patch_payload = validate_canvas_action(
        "patch_lines",
        {
            "artifact_id": "canvas_123",
            "start_line": 2,
            "end_line": 4,
            "replacement": "  A --> C[Done]",
        },
    )
    assert act_patch == "patch_lines"
    assert patch_payload["start_line"] == 2
    assert patch_payload["end_line"] == 4


def test_mermaid_and_html_offline_svg_renderers_and_sanitizer() -> None:
    flowchart_svg = render_mermaid_to_svg(
        "flowchart LR\n  A[Client UI] -->|IPC| B(Service)\n  B --> C{SQLite}",
        title="System Flow",
    )
    assert flowchart_svg.startswith("<svg")
    assert "Client UI" in flowchart_svg
    assert "Service" in flowchart_svg
    assert "SQLite" in flowchart_svg

    seq_svg = render_mermaid_to_svg(
        "sequenceDiagram\n  participant U as User\n  participant K as Kira\n  U->>K: Ask prompt\n  K-->>U: Live preview",
        title="Sequence",
    )
    assert "<svg" in seq_svg
    assert "Ask prompt" in seq_svg

    dirty_svg = (
        '<svg viewBox="0 0 100 100" onload="alert(1)">'
        "<script>evil()</script>"
        "<foreignObject><div>bad</div></foreignObject>"
        '<a href="https://evil.example"><rect width="50" height="50"/></a>'
        "</svg>"
    )
    clean_svg = sanitize_svg(dirty_svg)
    assert "<script" not in clean_svg.lower()
    assert "foreignobject" not in clean_svg.lower()
    assert "onload" not in clean_svg.lower()
    assert "https://evil.example" not in clean_svg
    assert "<rect" in clean_svg

    html_svg, safe_html = render_html_preview_svg(
        "<h1>Dashboard</h1><p>Metriche attive</p><button>Salva</button><input placeholder='Cerca...'>",
        title="UI Preview",
    )
    assert "<svg" in html_svg
    assert "Dashboard" in html_svg
    assert "Salva" in html_svg
    assert "Content-Security-Policy" in safe_html


def test_patch_lines_and_granular_hunk_reject() -> None:
    original = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    # Modify line 2 and line 19 so they form 2 distinct unified diff hunks
    step1 = patch_lines_in_text(original, 2, 2, "line 2 modified")
    modified = patch_lines_in_text(step1, 19, 19, "line 19 modified")

    diff_text, hunks = compute_hunks(original, modified, "demo.txt")
    assert "@@" in diff_text
    assert len(hunks) == 2
    assert hunks[0]["added_count"] == 1
    assert hunks[1]["added_count"] == 1

    # Reject only the first hunk (line 2), keeping the second hunk (line 19)!
    reverted_first = reject_hunk_in_text(modified, hunks[0])
    assert "line 2\n" in reverted_first
    assert "line 2 modified" not in reverted_first
    assert "line 19 modified" in reverted_first


@pytest.mark.asyncio
async def test_canvas_service_lifecycle_and_hunk_revert(tmp_path: Path) -> None:
    home = tmp_path / "kira_home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = Paths(*(home / key for key in ("config", "data", "state", "runtime")))
    service = Service(paths)
    await service.start()
    try:
        hello = await service.dispatch("hello", {})
        assert hello["capabilities"]["interactive_canvas"] is True

        proj = (
            await service.dispatch(
                "project.create",
                {"name": "CanvasDemo", "root": str(workspace), "permission": "project_write"},
            )
        )["project"]
        pid = str(proj["id"])

        created = await service.dispatch(
            "canvas.create",
            {
                "project_id": pid,
                "title": "Flusso Ordini",
                "kind": "mermaid",
                "content": "\n".join(
                    [
                        "flowchart TD",
                        "  A[Carrello] --> B[Pagamento]",
                        "  B --> C[Magazzino]",
                        "  C --> D[Spedizione]",
                        "  D --> E[Consegna]",
                        "  E --> F[Archivio]",
                        "  F --> G[Notifica]",
                        "  G --> H[Chiusura]",
                        "  H --> I[Report]",
                        "  I --> J[Fine]",
                    ]
                ),
            },
        )
        art = created["artifact"]
        aid = art["id"]
        assert art["revision"] == 1
        assert Path(art["preview_svg_path"]).is_file()

        # Modify line 2 and line 10 in revision 2 so 2 hunks are produced
        lines = art["content"].splitlines()
        lines[1] = "  A[Carrello] --> B[Checkout Rapido]"
        lines[9] = "  I --> J[Completato]"
        updated = await service.dispatch(
            "canvas.update",
            {
                "project_id": pid,
                "artifact_id": aid,
                "content": "\n".join(lines),
                "summary": "Due modifiche distinte",
            },
        )
        assert updated["artifact"]["revision"] == 2
        diff_res = await service.dispatch(
            "canvas.diff",
            {"project_id": pid, "artifact_id": aid},
        )
        assert len(diff_res["hunks"]) == 2

        # Reject hunk #0 (reverts line 2 back to Pagamento, keeps line 10 Completato)
        after_reject = await service.dispatch(
            "canvas.apply_hunk",
            {
                "project_id": pid,
                "artifact_id": aid,
                "hunk_index": 0,
                "decision": "reject",
            },
        )
        content_rev3 = after_reject["artifact"]["content"]
        assert after_reject["artifact"]["revision"] == 3
        assert "B[Pagamento]" in content_rev3
        assert "J[Completato]" in content_rev3
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_workspace_git_hunks_and_single_hunk_reject(tmp_path: Path) -> None:
    home = tmp_path / "kira_home"
    root = tmp_path / "workspace"
    root.mkdir()

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True)

    target = root / "module.py"
    initial_lines = [f"def fn_{i}():\n    return {i}\n" for i in range(1, 15)]
    target.write_text("\n".join(initial_lines), encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)

    # Modify fn_1 and fn_14 so Git produces 2 hunks
    modified_lines = list(initial_lines)
    modified_lines[0] = "def fn_1():\n    return 100\n"
    modified_lines[-1] = "def fn_14():\n    return 1400\n"
    target.write_text("\n".join(modified_lines), encoding="utf-8")

    paths = Paths(*(home / key for key in ("config", "data", "state", "runtime")))
    service = Service(paths)
    await service.start()
    try:
        proj = (
            await service.dispatch(
                "project.create",
                {"name": "GitHunkDemo", "root": str(root), "permission": "project_write"},
            )
        )["project"]
        pid = str(proj["id"])

        ws_hunks = await service.dispatch("canvas.workspace_hunks", {"project_id": pid, "path": "module.py"})
        assert ws_hunks["available"] is True
        assert len(ws_hunks["hunks"]) == 2

        # Reject the first hunk in module.py
        applied = await service.dispatch(
            "canvas.workspace_apply_hunk",
            {
                "project_id": pid,
                "path": "module.py",
                "hunk_index": 0,
                "decision": "reject",
            },
        )
        assert applied["ok"] is True
        assert applied["decision"] == "reject"
        disk_text = target.read_text(encoding="utf-8")
        assert "return 1\n" in disk_text
        assert "return 100\n" not in disk_text
        assert "return 1400\n" in disk_text
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_kira_canvas_client_tool_in_run(tmp_path: Path) -> None:
    home = tmp_path / "kira_home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    backend = ScriptedCanvasBackend(
        [
            (
                "kira.canvas",
                {
                    "action": "create",
                    "payload": {
                        "title": "Architettura MCP",
                        "kind": "mermaid",
                        "content": "flowchart TD\n  UI --> Service\n  Service --> MCP",
                    },
                },
            ),
            (
                "kira.canvas",
                {
                    "action": "list",
                    "payload": {},
                },
            ),
        ]
    )
    paths = Paths(*(home / key for key in ("config", "data", "state", "runtime")))
    service = Service(paths, backend_factory=lambda *_: backend)
    await service.start()
    try:
        proj = (
            await service.dispatch(
                "project.create",
                {"name": "RunCanvas", "root": str(workspace), "permission": "project_write"},
            )
        )["project"]
        sess = (
            await service.dispatch(
                "session.create",
                {"project_id": proj["id"], "backend": "ollama", "model": "m1", "settings": {"tools": True}},
            )
        )["session"]
        run = (
            await service.dispatch(
                "run.start",
                {
                    "session_id": sess["id"],
                    "prompt": "Disegna l'architettura MCP nel Canvas",
                    "request_id": str(uuid4()),
                    "mode": "execute",
                },
            )
        )["run"]
        for _ in range(50):
            r = service.store.one("runs", run["id"])
            if r["state"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.05)
        assert backend.results[0]["ok"] is True
        assert backend.results[0]["artifact"]["kind"] == "mermaid"
        assert backend.results[1]["ok"] is True
        assert len(backend.results[1]["artifacts"]) == 1
    finally:
        await service.close()
