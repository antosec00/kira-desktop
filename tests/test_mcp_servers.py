"""Tests for Kira's Universal External MCP Host/Client (domain, service, and UI)."""

from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any

import pytest

from kira.backends.client_tools import validate_tools
from kira.domain.mcp_servers import (
    MCP_TOOL_NAMES,
    mcp_tool_specs,
    parse_mcp_servers_json,
    validate_mcp_action,
    validate_server_config,
)
from kira.domain.paths import Paths
from kira.service.server import Service

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from kira.domain.contracts import KiraError
from kira.ui.app import create_engine
from kira.ui.controller import Workspace

_STDIO_MCP_SERVER_SCRIPT = """
import json, os, sys

secret = os.environ.get("MCP_TEST_SECRET", "")
for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method", "")
    msg_id = msg.get("id")
    if msg_id is None:
        continue
    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stdio-demo", "version": "1.0.0"},
            },
        }
    elif method == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "echo_note",
                        "description": "Echo back a note from the external MCP server.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ]
            },
        }
    elif method == "tools/call":
        params = msg.get("params") or {}
        args = params.get("arguments") or {}
        text = str(args.get("text", ""))
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": f"echo:{text}:has_secret={bool(secret)}"}],
                "isError": False,
            },
        }
    else:
        resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}
    sys.stdout.write(json.dumps(resp) + "\\n")
    sys.stdout.flush()
"""


def test_domain_parse_mcp_servers_json_and_validation() -> None:
    raw = json.dumps(
        {
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "ghp_secret123456"},
                },
                "remote-docs": {
                    "url": "https://example.com/mcp/sse",
                    "headers": {"Authorization": "Bearer tok_987654"},
                    "read_only": True,
                },
            }
        }
    )
    parsed = parse_mcp_servers_json(raw)
    assert len(parsed) == 2
    by_id = {item["id"]: item for item in parsed}
    assert by_id["github"]["transport"] == "stdio"
    assert by_id["github"]["command"] == "npx"
    assert by_id["github"]["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert by_id["github"]["env"] == {"GITHUB_TOKEN": "ghp_secret123456"}
    assert by_id["remote-docs"]["transport"] == "http"
    assert by_id["remote-docs"]["url"] == "https://example.com/mcp/sse"
    assert by_id["remote-docs"]["read_only"] is True

    spec = mcp_tool_specs()[0]
    assert f"kira.{spec['name']}" in MCP_TOOL_NAMES
    act, payload = validate_mcp_action(
        "call", {"server_id": "github", "tool_name": "search_issues", "arguments": {"q": "bug"}}
    )
    assert act == "call"
    assert payload["server_id"] == "github"
    assert payload["tool_name"] == "search_issues"
    assert payload["arguments"] == {"q": "bug"}

    with pytest.raises(KiraError):
        validate_server_config({"name": "bad", "transport": "stdio", "command": ""})


async def test_service_mcp_stdio_and_http_discovery_and_execution(tmp_path: Path) -> None:
    contexts: list[Any] = []
    call_results: list[dict[str, Any]] = []

    class Backend:
        async def run(self, ctx: Any) -> dict[str, Any]:
            contexts.append(ctx)
            res_list = await ctx.client_tool_call(
                "mcp-list", "kira.mcp_tools", {"action": "list_servers", "payload": {}}
            )
            res_ok = await ctx.client_tool_call(
                "mcp-call-ok",
                "kira.mcp_tools",
                {
                    "action": "call",
                    "payload": {"server_id": "demo-stdio", "tool_name": "echo_note", "arguments": {"text": "ciao"}},
                },
            )
            res_blocked = await ctx.client_tool_call(
                "mcp-call-blocked",
                "kira.mcp_tools",
                {
                    "action": "call",
                    "payload": {"server_id": "demo-http", "tool_name": "http_ping", "arguments": {}},
                },
            )
            call_results.extend([res_list, res_ok, res_blocked])
            return {"state": "completed"}

        async def close(self) -> None:
            pass

        async def discover(self) -> dict[str, Any]:
            return {"available": True, "models": []}

    with tempfile.TemporaryDirectory(prefix="kira-mcp-fixture-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: Backend())
        await service.start()
        try:
            # 1. Save a stdio MCP server with a secret env var
            saved = await service.dispatch(
                "mcp.servers.save",
                {
                    "id": "demo-stdio",
                    "name": "Demo Stdio",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-c", _STDIO_MCP_SERVER_SCRIPT],
                    "env": {"MCP_TEST_SECRET": "super_secret_mcp_token_value"},
                    "read_only": True,
                },
            )
            servers = saved["servers"]
            assert len(servers) == 1
            assert servers[0]["id"] == "demo-stdio"
            # Public representation must never expose secret values
            assert "env" not in servers[0]
            assert servers[0]["env_keys"] == ["MCP_TEST_SECRET"]
            assert service.redactor.text("leak super_secret_mcp_token_value") == "leak [REDACTED]"

            # 2. Test & discover tools on the stdio server
            tested = await service.dispatch("mcp.servers.test", {"server_id": "demo-stdio"})
            assert tested["ok"] is True
            assert [t["name"] for t in tested["tools"]] == ["echo_note"]
            assert tested["server"]["status"] == "ready"

            # 3. Also test an HTTP/SSE MCP server using a local HTTPServer
            class _McpHttpHandler(BaseHTTPRequestHandler):
                def do_POST(self) -> None:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    method = body.get("method", "")
                    msg_id = body.get("id")
                    if msg_id is None:
                        self.send_response(202)
                        self.end_headers()
                        return
                    if method == "initialize":
                        payload = {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}},
                        }
                    elif method == "tools/list":
                        payload = {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "tools": [
                                    {
                                        "name": "http_ping",
                                        "description": "Ping over SSE",
                                        "inputSchema": {"type": "object", "properties": {}},
                                    }
                                ]
                            },
                        }
                    else:
                        payload = {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {"content": [{"type": "text", "text": "pong"}], "isError": False},
                        }
                    sse_bytes = f"event: message\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(sse_bytes)))
                    self.end_headers()
                    self.wfile.write(sse_bytes)

                def log_message(self, format: str, *args: Any) -> None:
                    return

            httpd = HTTPServer(("127.0.0.1", 0), _McpHttpHandler)
            port = httpd.server_port
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                await service.dispatch(
                    "mcp.servers.save",
                    {
                        "id": "demo-http",
                        "name": "Demo HTTP",
                        "transport": "http",
                        "url": f"http://127.0.0.1:{port}/mcp",
                        "read_only": False,
                    },
                )
                http_test = await service.dispatch("mcp.servers.test", {"server_id": "demo-http"})
                assert http_test["ok"] is True
                assert [t["name"] for t in http_test["tools"]] == ["http_ping"]

                # 4. Run a session with TRUSTED_HOST permission and PLAN mode to verify:
                # - stdio read_only server succeeds in PLAN mode
                # - http non-read_only server is blocked in PLAN mode
                project = (
                    await service.dispatch(
                        "project.create",
                        {"name": "Fixture", "root": str(tmp_path), "permission": "trusted_host"},
                    )
                )["project"]
                session = (
                    await service.dispatch(
                        "session.create",
                        {
                            "project_id": project["id"],
                            "backend": "ollama",
                            "model": "fixture-model",
                            "settings": {"tools": True},
                        },
                    )
                )["session"]
                started = (
                    await service.dispatch(
                        "run.start",
                        {
                            "session_id": session["id"],
                            "request_id": "mcp-run-1",
                            "prompt": "Use MCP",
                            "mode": "plan",
                        },
                    )
                )["run"]
                await asyncio.wait_for(service.tasks[started["id"]], 10)
                run = service.store.one("runs", started["id"])
                assert run["state"] == "completed", run["error"]

                ctx = contexts[0]
                assert "kira.mcp_tools" in validate_tools(ctx)
                assert "External MCP servers enabled:" in ctx.assistant_instructions
                assert "echo_note" in ctx.assistant_instructions

                res_list, res_ok, res_blocked = call_results
                assert res_list["ok"] is True
                assert len(res_list["servers"]) == 2
                assert res_ok["ok"] is True
                assert "echo:ciao:has_secret=True" in json.dumps(res_ok)
                # Non-read-only server is blocked in Plan mode
                assert res_blocked["ok"] is False
                assert "Execute mode" in res_blocked["error"]["message"]
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            await service.close()


def _find(root: QObject, name: str) -> QObject:
    if root.objectName() == name:
        return root
    found = root.findChild(QObject, name)
    assert found is not None, f"missing QML object {name}"
    return found


def test_ui_mcp_servers_pane_in_settings(qapp: Any, qtbot: Any, tmp_path: Path, monkeypatch: Any) -> None:
    paths = Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime")))
    view = Workspace(paths)
    view._connected_value = True
    view._project = {"id": "synthetic-project", "name": "Settings fixture", "root": str(tmp_path), "permission": "read_only"}
    view.teams._project_id = "synthetic-project"
    requests: list[tuple[Any, ...]] = []
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: requests.append(args))
    view._receive_settings(
        {
            "ollama_endpoint": "http://127.0.0.1:11434",
            "codex_binary": "codex",
            "tool_timeout_seconds": 30,
            "max_tool_rounds": 15,
            "max_output_bytes": 1048576,
            "mcp_servers": [
                {
                    "id": "github",
                    "name": "GitHub",
                    "transport": "stdio",
                    "enabled": True,
                    "read_only": False,
                    "timeout_seconds": 30,
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "cwd": "",
                    "url": "",
                    "env_keys": ["GITHUB_TOKEN"],
                    "header_keys": [],
                    "status": "ready",
                    "last_error": "",
                    "tools": [{"name": "search_repositories", "description": "Search repos", "inputSchema": {}}],
                }
            ],
        }
    )

    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setProperty("page", 3)
    qtbot.wait(40)
    try:
        assert not requests
        mcp_pane = _find(window, "mcpServersPane")
        assert view.mcp.count == 1
        assert view.mcp.servers[0]["name"] == "GitHub"

        connections_tab = _find(window, "settingsConnectionsCategory")
        center = connections_tab.mapToScene(
            QPointF(connections_tab.width() / 2, connections_tab.height() / 2)
        ).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        QTest.qWait(30)
        assert mcp_pane.property("visible") is True

        # Import JSON via McpController
        view.mcp.importJson(
            json.dumps(
                {"mcpServers": {"postgres": {"command": "npx", "args": ["@modelcontextprotocol/server-postgres"]}}}
            )
        )
        assert requests[-1][0] == "mcp.servers.import"
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        view.close()
        engine.deleteLater()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


