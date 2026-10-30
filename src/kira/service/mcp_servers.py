"""Service-owned MCP Host/Client for external stdio and HTTP MCP servers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import signal
import stat
from typing import TYPE_CHECKING, Any

import httpx

from kira import __version__
from kira.domain.contracts import HOST_PERMISSIONS, WRITE_PERMISSIONS, Json, KiraError
from kira.domain.mcp_servers import (
    MAX_MCP_OUTPUT_BYTES,
    MAX_SERVERS,
    MAX_TOOLS_PER_SERVER,
    MCP_TOOL_NAMES,
    mcp_action_contract,
    mcp_tool_specs,
    parse_mcp_servers_json,
    sanitize_mcp_tool,
    validate_mcp_action,
    validate_server_config,
)
from kira.service.project_context import ProjectChats
from kira.service.storage import encode, now
from kira.service.tool_policy import kira_tools_enabled
from kira.tools.runner import command_environment

if TYPE_CHECKING:
    from kira.service.server import Service

METHODS = frozenset({
    "mcp.servers.list",
    "mcp.servers.save",
    "mcp.servers.import",
    "mcp.servers.toggle",
    "mcp.servers.delete",
    "mcp.servers.test",
})
MAX_STORE_BYTES = 512 * 1024
MCP_PROTOCOL_VERSION = "2025-06-18"


def _failure(code: str, message: str, **extra: Any) -> Json:
    return {"ok": False, "error": {"code": code, "message": message}, **extra}


def _clip_utf8(text: str, maximum: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= maximum:
        return text, False
    return raw[:maximum].decode("utf-8", "ignore"), True


class McpServers:
    """Manage external MCP server configurations, tool discovery and run-bound invocation."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = service.store
        self.path = service.paths.config / "mcp" / "servers.json"
        self._servers: dict[str, Json] = {}
        self._lock = asyncio.Lock()
        self._active_processes: set[asyncio.subprocess.Process] = set()
        self._closing = False
        self._load()

    def _sync_redactor(self) -> None:
        secrets_list: list[str] = []
        for server in self._servers.values():
            for val in server.get("env", {}).values():
                if isinstance(val, str) and len(val) >= 6:
                    secrets_list.append(val)
            for val in server.get("headers", {}).values():
                if isinstance(val, str) and len(val) >= 6:
                    secrets_list.append(val)
        self.service.redactor.set_provider_values(tuple(secrets_list), owner="mcp")

    def _load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077):
                    return
                raw = stream.read(MAX_STORE_BYTES + 1)
            if len(raw) > MAX_STORE_BYTES:
                return
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
                return
            loaded: dict[str, Json] = {}
            for entry in data["servers"][:MAX_SERVERS]:
                if not isinstance(entry, dict):
                    continue
                try:
                    cfg = validate_server_config({
                        k: v for k, v in entry.items()
                        if k in {"id", "name", "transport", "command", "args", "env",
                                 "cwd", "url", "headers", "enabled", "read_only", "timeout_seconds"}
                    })
                except KiraError:
                    continue
                tools_raw = entry.get("tools", [])
                tools: list[Json] = []
                if isinstance(tools_raw, list):
                    for item in tools_raw[:MAX_TOOLS_PER_SERVER]:
                        sanitized = sanitize_mcp_tool(item)
                        if sanitized is not None:
                            tools.append(sanitized)
                cfg["tools"] = tools
                cfg["status"] = str(entry.get("status") or ("ready" if tools else "untested"))
                cfg["last_error"] = str(entry.get("last_error") or "")[:500]
                cfg["last_tested_at"] = str(entry.get("last_tested_at") or "")[:64]
                loaded[cfg["id"]] = cfg
            self._servers = loaded
            self._sync_redactor()
        except (OSError, ValueError, UnicodeError):
            return

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise KiraError("UNSAFE_PATH", "MCP configuration directory must belong to the current user")
        with contextlib.suppress(OSError):
            self.path.parent.chmod(0o700)
        payload = {"version": 1, "servers": list(self._servers.values())}
        data = encode(payload).encode("utf-8")
        if len(data) > MAX_STORE_BYTES:
            raise KiraError("LIMIT_REACHED", "MCP server configuration exceeds storage limit")
        temporary = self.path.parent / f".servers-{secrets.token_hex(12)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        self._sync_redactor()

    @staticmethod
    def _public_server(server: Json) -> Json:
        tools = server.get("tools", [])
        return {
            "id": server["id"],
            "name": server["name"],
            "transport": server["transport"],
            "enabled": bool(server.get("enabled", True)),
            "read_only": bool(server.get("read_only", False)),
            "timeout_seconds": int(server.get("timeout_seconds", 30)),
            "command": str(server.get("command", "")),
            "args": list(server.get("args", [])),
            "cwd": str(server.get("cwd", "")),
            "url": str(server.get("url", "")),
            "env_keys": sorted(server.get("env", {}).keys()),
            "header_keys": sorted(server.get("headers", {}).keys()),
            "status": str(server.get("status", "untested")),
            "last_error": str(server.get("last_error", "")),
            "last_tested_at": str(server.get("last_tested_at", "")),
            "tool_count": len(tools),
            "tools": [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "read_only": bool(t.get("read_only") or server.get("read_only")),
                    "inputSchema": t.get("inputSchema", {"type": "object", "properties": {}}),
                }
                for t in tools
            ],
        }

    def list_public(self) -> list[Json]:
        return [self._public_server(srv) for srv in self._servers.values()]

    def enabled_servers(self) -> list[Json]:
        return [srv for srv in self._servers.values() if srv.get("enabled", True)]

    async def dispatch(self, method: str, params: Json) -> Json:
        if not isinstance(params, dict):
            raise KiraError("INVALID_ARGUMENT", "params must be an object")
        if method == "mcp.servers.list":
            if params:
                raise KiraError("INVALID_ARGUMENT", "mcp.servers.list takes no parameters")
            return {"servers": self.list_public()}

        if self._closing or self.service._closing or self.service.stopping.is_set():
            raise KiraError("SHUTTING_DOWN", "Service is shutting down")

        if method == "mcp.servers.save":
            allowed = {
                "id", "name", "transport", "command", "args", "env", "cwd",
                "url", "headers", "enabled", "read_only", "timeout_seconds",
                "preserve_secrets",
            }
            if set(params) - allowed:
                raise KiraError("INVALID_ARGUMENT", "Unsupported fields in mcp.servers.save")
            raw_cfg = {k: v for k, v in params.items() if k != "preserve_secrets"}
            existing_id = str(raw_cfg.get("id") or "").strip().casefold()
            existing = self._servers.get(existing_id) if existing_id else None
            if existing is None and len(self._servers) >= MAX_SERVERS:
                raise KiraError("LIMIT_REACHED", f"At most {MAX_SERVERS} MCP servers can be configured")
            preserve = bool(params.get("preserve_secrets", True))
            existing_env = dict(existing.get("env", {})) if (existing and preserve) else {}
            existing_headers = dict(existing.get("headers", {})) if (existing and preserve) else {}
            if existing and preserve:
                if "env" not in raw_cfg:
                    raw_cfg["env"] = existing_env
                if "headers" not in raw_cfg:
                    raw_cfg["headers"] = existing_headers
            validated = validate_server_config(
                raw_cfg, existing_env=existing_env, existing_headers=existing_headers
            )
            server_id = validated["id"]
            previous = self._servers.get(server_id) or existing
            if previous is not None and (
                previous.get("transport") == validated["transport"]
                and previous.get("command") == validated["command"]
                and previous.get("args") == validated["args"]
                and previous.get("url") == validated["url"]
            ):
                validated["tools"] = list(previous.get("tools", []))
                validated["status"] = previous.get("status", "untested")
                validated["last_error"] = previous.get("last_error", "")
                validated["last_tested_at"] = previous.get("last_tested_at", "")
            else:
                validated["tools"] = []
                validated["status"] = "untested"
                validated["last_error"] = ""
                validated["last_tested_at"] = ""
            async with self._lock:
                if existing_id and existing_id != server_id:
                    self._servers.pop(existing_id, None)
                self._servers[server_id] = validated
                self._save()
            return {"server": self._public_server(validated), "servers": self.list_public()}

        if method == "mcp.servers.import":
            if set(params) != {"json"} or not isinstance(params.get("json"), str):
                raise KiraError("INVALID_ARGUMENT", "mcp.servers.import requires a json string")
            imported_configs = parse_mcp_servers_json(params["json"])
            async with self._lock:
                for cfg in imported_configs:
                    sid = cfg["id"]
                    if sid not in self._servers and len(self._servers) >= MAX_SERVERS:
                        raise KiraError("LIMIT_REACHED", f"At most {MAX_SERVERS} MCP servers can be configured")
                    prev = self._servers.get(sid)
                    cfg["tools"] = list(prev.get("tools", [])) if prev else []
                    cfg["status"] = prev.get("status", "untested") if prev else "untested"
                    cfg["last_error"] = prev.get("last_error", "") if prev else ""
                    cfg["last_tested_at"] = prev.get("last_tested_at", "") if prev else ""
                    self._servers[sid] = cfg
                self._save()
            return {"imported": len(imported_configs), "servers": self.list_public()}

        if method == "mcp.servers.toggle":
            if set(params) - {"server_id", "enabled", "read_only"} or "server_id" not in params:
                raise KiraError("INVALID_ARGUMENT", "mcp.servers.toggle requires server_id")
            sid = str(params["server_id"]).strip().casefold()
            async with self._lock:
                server = self._servers.get(sid)
                if server is None:
                    raise KiraError("NOT_FOUND", "MCP server not found")
                if "enabled" in params:
                    if not isinstance(params["enabled"], bool):
                        raise KiraError("INVALID_ARGUMENT", "enabled must be a boolean")
                    server["enabled"] = params["enabled"]
                if "read_only" in params:
                    if not isinstance(params["read_only"], bool):
                        raise KiraError("INVALID_ARGUMENT", "read_only must be a boolean")
                    server["read_only"] = params["read_only"]
                self._save()
            return {"server": self._public_server(server), "servers": self.list_public()}

        if method == "mcp.servers.delete":
            if set(params) != {"server_id"} or not isinstance(params.get("server_id"), str):
                raise KiraError("INVALID_ARGUMENT", "mcp.servers.delete requires server_id")
            sid = params["server_id"].strip().casefold()
            async with self._lock:
                if sid not in self._servers:
                    raise KiraError("NOT_FOUND", "MCP server not found")
                self._servers.pop(sid, None)
                self._save()
            return {"deleted": sid, "servers": self.list_public()}

        if method == "mcp.servers.test":
            if set(params) != {"server_id"} or not isinstance(params.get("server_id"), str):
                raise KiraError("INVALID_ARGUMENT", "mcp.servers.test requires server_id")
            sid = params["server_id"].strip().casefold()
            server = self._servers.get(sid)
            if server is None:
                raise KiraError("NOT_FOUND", "MCP server not found")
            snapshot = dict(server)
            try:
                tools = await self._discover_server_tools(snapshot)
                error_msg = ""
                status = "ready"
                ok = True
            except Exception as exc:
                tools = list(server.get("tools", []))
                error_msg = self.service.redactor.text(str(exc))[:500]
                status = "error"
                ok = False
            async with self._lock:
                current = self._servers.get(sid)
                if current is not None:
                    current["tools"] = tools
                    current["status"] = status
                    current["last_error"] = error_msg
                    current["last_tested_at"] = now()
                    self._save()
                    public = self._public_server(current)
                else:
                    public = self._public_server({**snapshot, "tools": tools, "status": status,
                                                 "last_error": error_msg, "last_tested_at": now()})
            return {
                "ok": ok,
                "server": public,
                "tools": public["tools"],
                "error": error_msg,
                "servers": self.list_public(),
            }

        raise KiraError("METHOD_NOT_FOUND", "Unknown MCP server method")

    async def _discover_server_tools(self, server: Json) -> list[Json]:
        result = await self._exchange(server, "tools/list", {})
        raw_tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(raw_tools, list):
            raise KiraError("MCP_PROTOCOL_ERROR", "MCP server did not return a tools list")
        sanitized_list: list[Json] = []
        seen: set[str] = set()
        for item in raw_tools[:MAX_TOOLS_PER_SERVER]:
            tool = sanitize_mcp_tool(item)
            if tool is not None and tool["name"] not in seen:
                seen.add(tool["name"])
                sanitized_list.append(tool)
        return sanitized_list

    async def _call_server_tool(self, server: Json, tool_name: str, arguments: Json,
                                cancel: asyncio.Event | None = None) -> Json:
        result = await self._exchange(
            server, "tools/call", {"name": tool_name, "arguments": arguments}, cancel=cancel
        )
        if not isinstance(result, dict):
            raise KiraError("MCP_PROTOCOL_ERROR", "MCP tool call returned an invalid response")
        is_error = bool(result.get("isError"))
        content_items = result.get("content")
        texts: list[str] = []
        structured: list[Json] = []
        if isinstance(content_items, list):
            for item in content_items[:32]:
                if not isinstance(item, dict):
                    continue
                kind = item.get("type")
                if kind == "text" and isinstance(item.get("text"), str):
                    texts.append(item["text"])
                elif kind == "resource" and isinstance(item.get("resource"), dict):
                    res = item["resource"]
                    if isinstance(res.get("text"), str):
                        texts.append(res["text"])
                    structured.append({"type": "resource", "uri": str(res.get("uri", ""))[:512]})
                else:
                    structured.append({"type": str(kind or "unknown")[:64]})
        combined_text = "\n".join(texts) if texts else encode(result)
        redacted_text = self.service.redactor.text(combined_text)
        clipped_text, truncated = _clip_utf8(redacted_text, MAX_MCP_OUTPUT_BYTES)
        if is_error:
            return _failure(
                "MCP_TOOL_ERROR",
                clipped_text or f"MCP tool {tool_name} reported an error",
                server_id=server["id"],
                tool_name=tool_name,
                truncated=truncated,
            )
        return {
            "ok": True,
            "server_id": server["id"],
            "tool_name": tool_name,
            "content": clipped_text,
            "items": structured,
            "truncated": truncated,
        }

    async def _exchange(self, server: Json, method: str, params: Json,
                        *, cancel: asyncio.Event | None = None) -> Json:
        timeout = float(server.get("timeout_seconds", 30))
        task = asyncio.create_task(
            self._stdio_exchange(server, method, params, timeout)
            if server["transport"] == "stdio"
            else self._http_exchange(server, method, params, timeout)
        )
        if cancel is None:
            return await task
        cancel_wait = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait({task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_wait in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise KiraError("CANCELLED", "MCP operation was cancelled")
            return await task
        finally:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)

    async def _stdio_exchange(self, server: Json, method: str, params: Json, timeout: float) -> Json:
        command = str(server.get("command") or "").strip()
        if not command:
            raise KiraError("INVALID_ARGUMENT", "MCP stdio server has no command configured")
        args = [str(a) for a in server.get("args", [])]
        env = command_environment()
        for k, v in server.get("env", {}).items():
            if isinstance(k, str) and isinstance(v, str):
                env[k] = v
        cwd_raw = str(server.get("cwd") or "").strip()
        cwd: str | None = None
        if cwd_raw:
            resolved_cwd = Path(cwd_raw).expanduser().resolve()
            if not resolved_cwd.is_dir():
                raise KiraError("INVALID_ARGUMENT", f"MCP working directory does not exist: {cwd_raw}")
            cwd = str(resolved_cwd)

        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                start_new_session=True,
                limit=512 * 1024,
            )
        except OSError as exc:
            raise KiraError("MCP_SPAWN_FAILED", f"Could not start MCP command '{command}': {exc}") from exc

        self._active_processes.add(process)
        try:
            assert process.stdin is not None and process.stdout is not None

            async def send(message: Json) -> None:
                line = encode(message).encode("utf-8") + b"\n"
                process.stdin.write(line)  # type: ignore[union-attr]
                await process.stdin.drain()  # type: ignore[union-attr]

            async def read_response(expected_id: int) -> Json:
                for _ in range(128):
                    raw_line = await process.stdout.readline()  # type: ignore[union-attr]
                    if not raw_line:
                        stderr_bytes = b""
                        if process.stderr is not None:
                            with contextlib.suppress(Exception):
                                stderr_bytes = await asyncio.wait_for(process.stderr.read(4096), 0.5)
                        detail = stderr_bytes.decode("utf-8", "replace").strip()
                        suffix = f": {detail[:240]}" if detail else ""
                        raise KiraError("MCP_EOF", f"MCP server closed stdout unexpectedly{suffix}")
                    text_line = raw_line.decode("utf-8", "replace").strip()
                    if not text_line:
                        continue
                    try:
                        msg = json.loads(text_line)
                    except ValueError:
                        continue
                    if not isinstance(msg, dict) or msg.get("id") != expected_id:
                        continue
                    if "error" in msg:
                        err = msg["error"]
                        err_msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise KiraError("MCP_RPC_ERROR", f"MCP error: {err_msg or 'unknown error'}")
                    result = msg.get("result")
                    if not isinstance(result, dict):
                        raise KiraError("MCP_PROTOCOL_ERROR", "MCP response result must be an object")
                    return result
                raise KiraError("MCP_PROTOCOL_ERROR", "Too many non-matching lines from MCP stdio server")

            async def run_sequence() -> Json:
                await send({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "Kira Desktop", "version": __version__},
                    },
                })
                await read_response(1)
                await send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
                await send({"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
                return await read_response(2)

            return await asyncio.wait_for(run_sequence(), timeout=timeout)
        except TimeoutError as exc:
            raise KiraError("TIMEOUT", f"MCP server '{server['name']}' timed out after {int(timeout)}s") from exc
        finally:
            self._active_processes.discard(process)
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(ProcessLookupError, OSError):
                    process.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), 2.0)

    async def _http_exchange(self, server: Json, method: str, params: Json, timeout: float) -> Json:
        url = str(server.get("url") or "").strip()
        if not url:
            raise KiraError("INVALID_ARGUMENT", "MCP HTTP server has no URL configured")
        base_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        for k, v in server.get("headers", {}).items():
            if isinstance(k, str) and isinstance(v, str):
                base_headers[k] = v

        session_id = ""

        async def post_rpc(client: httpx.AsyncClient, payload: Json, expected_id: int | None) -> Json:
            nonlocal session_id
            req_headers = dict(base_headers)
            if session_id:
                req_headers["Mcp-Session-Id"] = session_id
            response = await client.post(url, content=encode(payload).encode("utf-8"), headers=req_headers)
            if response.status_code >= 400:
                raise KiraError("MCP_HTTP_ERROR", f"MCP HTTP {response.status_code} from {server['name']}")
            new_sid = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
            if new_sid and isinstance(new_sid, str) and len(new_sid) <= 256:
                session_id = new_sid
            if expected_id is None:
                return {}
            ctype = (response.headers.get("Content-Type") or "").casefold()
            body = response.content
            if len(body) > 1024 * 1024:
                raise KiraError("OUTPUT_LIMIT", "MCP HTTP response exceeded 1 MiB")
            text = body.decode("utf-8", "replace")
            if "text/event-stream" in ctype:
                for line in text.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    data_str = stripped[5:].strip()
                    if not data_str:
                        continue
                    try:
                        msg = json.loads(data_str)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and msg.get("id") == expected_id:
                        if "error" in msg:
                            err = msg["error"]
                            err_msg = err.get("message") if isinstance(err, dict) else str(err)
                            raise KiraError("MCP_RPC_ERROR", f"MCP error: {err_msg or 'unknown error'}")
                        res = msg.get("result")
                        if isinstance(res, dict):
                            return res
                raise KiraError("MCP_PROTOCOL_ERROR", "No matching JSON-RPC event in MCP SSE stream")
            try:
                msg = json.loads(text)
            except ValueError as exc:
                raise KiraError("MCP_PROTOCOL_ERROR", "MCP HTTP server returned invalid JSON") from exc
            if not isinstance(msg, dict):
                raise KiraError("MCP_PROTOCOL_ERROR", "MCP HTTP response must be a JSON object")
            if "error" in msg:
                err = msg["error"]
                err_msg = err.get("message") if isinstance(err, dict) else str(err)
                raise KiraError("MCP_RPC_ERROR", f"MCP error: {err_msg or 'unknown error'}")
            result = msg.get("result")
            if not isinstance(result, dict):
                raise KiraError("MCP_PROTOCOL_ERROR", "MCP HTTP result must be an object")
            return result

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False) as client:
                await post_rpc(client, {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "Kira Desktop", "version": __version__},
                    },
                }, expected_id=1)
                with contextlib.suppress(KiraError, httpx.HTTPError):
                    await post_rpc(
                        client,
                        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                        expected_id=None,
                    )
                return await post_rpc(
                    client,
                    {"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
                    expected_id=2,
                )
        except httpx.TimeoutException as exc:
            raise KiraError("TIMEOUT", f"MCP HTTP server '{server['name']}' timed out") from exc
        except httpx.HTTPError as exc:
            raise KiraError("MCP_HTTP_ERROR", f"MCP HTTP transport failed: {exc}") from exc

    def attach(self, session: Json, project: Json, run_id: str, cancel: asyncio.Event,
               mode: str, options: Json) -> Json:
        """Attach kira.mcp_tools to a run when at least one external MCP server is enabled."""
        enabled = self.enabled_servers()
        if (not enabled
                or session["backend"] not in {"codex", "openai_chatgpt", "ollama",
                                              "deepseek_account", "deepseek_web", "claude_code",
                                              "google_cloud"}
                or not kira_tools_enabled(session)
                or self.store.db.execute("SELECT 1 FROM team_members WHERE session_id=?",
                                         (session["id"],)).fetchone()):
            return options

        result = dict(options)
        namespaces = [{**namespace, "tools": list(namespace["tools"])}
                      for namespace in result.get("client_tools", [])]
        kira = next((namespace for namespace in namespaces if namespace.get("name") == "kira"), None)
        if kira is None:
            kira = {"type": "namespace", "name": "kira", "description": "Kira's current project tools.", "tools": []}
            namespaces.append(kira)
        existing = {tool.get("name") for tool in kira["tools"]}
        kira["tools"].extend(tool for tool in mcp_tool_specs() if tool["name"] not in existing)
        previous = result.get("client_tool_call")
        generation = self.service.provider_routing.current_generation(session["id"])

        def active() -> Json:
            actual_session = ProjectChats(self.store).owned_session(project["id"], session["id"])
            actual_project = self.store.one("projects", project["id"])
            run = self.store.one("runs", run_id)
            if (self._closing or self.service._closing or cancel.is_set()
                    or run_id not in self.service.tasks
                    or run["session_id"] != session["id"]
                    or run["state"] not in {"running", "awaiting_input"}
                    or any(actual_session[key] != session[key] for key in ("backend", "model"))
                    or actual_session["settings"].get("openai_account_id")
                    != session["settings"].get("openai_account_id")
                    or not kira_tools_enabled(actual_session)
                    or self.service.provider_routing.current_generation(session["id"]) != generation):
                raise KiraError("CANCELLED", "The originating run no longer permits MCP tool calls")
            return self.service.effective_project(run_id, actual_project)

        async def call(call_id: str, name: str, arguments: Json) -> Json:
            if name not in MCP_TOOL_NAMES:
                if previous is None:
                    raise KiraError("UNSUPPORTED", "This MCP callback is not registered")
                return dict(await previous(call_id, name, arguments))
            return await self._handle_tool_call(run_id, cancel, mode, call_id, arguments, active)

        summary_parts: list[str] = []
        for srv in enabled[:12]:
            tool_names = [t["name"] for t in srv.get("tools", [])[:12]]
            tools_label = ", ".join(tool_names) if tool_names else "use list_tools to discover"
            summary_parts.append(f"{srv['id']} ({srv['name']}: {tools_label})")
        servers_summary = "; ".join(summary_parts)

        result.update(client_tools=namespaces, client_tool_call=call)
        result["assistant_instructions"] = result.get("assistant_instructions", "") + (
            f"\nExternal MCP servers enabled: {servers_summary}. "
            "Use kira.mcp_tools with actions list_servers, list_tools, describe, or call "
            "(payload: {server_id, tool_name, arguments})."
        )
        return result

    async def _handle_tool_call(self, run_id: str, cancel: asyncio.Event, mode: str,
                                call_id: str, arguments: Json, active: Any) -> Json:
        try:
            active()
            if not isinstance(arguments, dict) or set(arguments) != {"action", "payload"}:
                raise KiraError("INVALID_ARGUMENT", "kira.mcp_tools requires action and payload")
            action, payload = validate_mcp_action(arguments.get("action"), arguments.get("payload"))
        except KiraError as exc:
            return _failure(exc.code, str(exc))

        await self.service.emit(run_id, "tool.started", {
            "tool_id": call_id, "name": "kira.mcp_tools", "arguments": arguments,
        })
        try:
            actual_project = active()
            if action == "list_servers":
                servers = [
                    {
                        "id": srv["id"],
                        "name": srv["name"],
                        "transport": srv["transport"],
                        "read_only": bool(srv.get("read_only", False)),
                        "tool_count": len(srv.get("tools", [])),
                        "tools": [t["name"] for t in srv.get("tools", [])[:64]],
                    }
                    for srv in self.enabled_servers()
                ]
                response: Json = {"ok": True, "servers": servers, "contracts": mcp_action_contract()}
            elif action == "list_tools":
                target_id = payload.get("server_id")
                query = (payload.get("query") or "").casefold()
                items: list[Json] = []
                for srv in self.enabled_servers():
                    if target_id and srv["id"] != target_id:
                        continue
                    tools = srv.get("tools", [])
                    if not tools and target_id == srv["id"]:
                        # Lazy discovery if the server has not been probed yet.
                        if srv["transport"] == "stdio" and actual_project["permission"] not in HOST_PERMISSIONS:
                            raise KiraError(
                                "PERMISSION_DENIED",
                                "Local stdio MCP servers require trusted_host or full_access permission",
                            )
                        tools = await self._discover_server_tools(srv)
                        srv["tools"] = tools
                        srv["status"] = "ready"
                        srv["last_tested_at"] = now()
                    for tool in tools:
                        haystack = f"{srv['id']} {tool['name']} {tool.get('description', '')}".casefold()
                        if query and not all(term in haystack for term in query.split()):
                            continue
                        items.append({
                            "server_id": srv["id"],
                            "server_name": srv["name"],
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "read_only": bool(tool.get("read_only") or srv.get("read_only")),
                        })
                response = {"ok": True, "tools": items[:64]}
            elif action == "describe":
                matched_srv = next((s for s in self.enabled_servers() if s["id"] == payload["server_id"]), None)
                if matched_srv is None:
                    raise KiraError("NOT_FOUND", f"Enabled MCP server '{payload['server_id']}' not found")
                tools = matched_srv.get("tools", [])
                if not tools:
                    if matched_srv["transport"] == "stdio" and actual_project["permission"] not in HOST_PERMISSIONS:
                        raise KiraError(
                            "PERMISSION_DENIED",
                            "Local stdio MCP servers require trusted_host or full_access permission",
                        )
                    tools = await self._discover_server_tools(matched_srv)
                    matched_srv["tools"] = tools
                matched_tool = next((t for t in tools if t["name"] == payload["tool_name"]), None)
                if matched_tool is None:
                    raise KiraError(
                        "NOT_FOUND", f"Tool '{payload['tool_name']}' not found on server '{matched_srv['id']}'"
                    )
                response = {
                    "ok": True,
                    "server_id": matched_srv["id"],
                    "tool": {
                        "name": matched_tool["name"],
                        "description": matched_tool.get("description", ""),
                        "read_only": bool(matched_tool.get("read_only") or matched_srv.get("read_only")),
                        "inputSchema": matched_tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                }
            else:
                matched_srv = next((s for s in self.enabled_servers() if s["id"] == payload["server_id"]), None)
                if matched_srv is None:
                    raise KiraError("NOT_FOUND", f"Enabled MCP server '{payload['server_id']}' not found")
                tools = matched_srv.get("tools", [])
                matched_tool = next((t for t in tools if t["name"] == payload["tool_name"]), None)
                is_read_only = bool(matched_srv.get("read_only") or (matched_tool and matched_tool.get("read_only")))
                if matched_srv["transport"] == "stdio" and actual_project["permission"] not in HOST_PERMISSIONS:
                    raise KiraError(
                        "PERMISSION_DENIED",
                        "Calling local stdio MCP servers requires trusted_host or full_access project permission",
                    )
                if not is_read_only:
                    required_perms = HOST_PERMISSIONS if matched_srv["transport"] == "stdio" else WRITE_PERMISSIONS
                    if mode != "execute" or actual_project["permission"] not in required_perms:
                        raise KiraError(
                            "PERMISSION_DENIED",
                            "Non-read-only MCP tools require Execute mode and write/host project permissions",
                        )
                response = await self._call_server_tool(
                    matched_srv, payload["tool_name"], payload["arguments"], cancel=cancel
                )
        except KiraError as exc:
            response = _failure(exc.code, str(exc))
        except Exception as exc:
            response = _failure("MCP_FAILED", self.service.redactor.text(str(exc))[:300])

        response = self.service.redactor.clean(response)
        await self.service.emit(run_id, "tool.completed", {
            "tool_id": call_id, "name": "kira.mcp_tools", "result": response,
        })
        return response

    async def close(self) -> None:
        self._closing = True
        procs = list(self._active_processes)
        for proc in procs:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
        if procs:
            await asyncio.gather(*(proc.wait() for proc in procs), return_exceptions=True)
        self._active_processes.clear()
