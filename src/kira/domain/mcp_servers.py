"""Bounded contracts and JSON import parser for Kira's external MCP Host/Client."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from kira.domain.contracts import Json, KiraError

MCP_TOOL_NAMES = frozenset({"kira.mcp_tools"})
MCP_TOOL_CALL_TIMEOUT = 120
MAX_SERVERS = 32
MAX_TOOLS_PER_SERVER = 128
MAX_MCP_OUTPUT_BYTES = 48 * 1024
MAX_MCP_IMPORT_BYTES = 128 * 1024
MCP_ACTIONS = frozenset({"list_servers", "list_tools", "describe", "call"})
SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
HEADER_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SENSITIVE_ENV_RE = re.compile(r"(?:token|password|secret|api[_-]?key|authorization|cookie|pat|cred)", re.I)


def _text(value: Any, name: str, maximum: int = 256, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or "\0" in value or len(value) > maximum
            or (not empty and not value.strip())):
        raise KiraError("INVALID_ARGUMENT", f"{name} must be bounded text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise KiraError("INVALID_ARGUMENT", f"{name} must be valid UTF-8") from exc
    return value


def slugify_server_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.strip().casefold()).strip("-_")
    if not slug:
        slug = "mcp-server"
    if not slug[0].isalnum():
        slug = "s-" + slug
    return slug[:63]


def is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_ENV_RE.search(key))


def validate_server_config(raw: Any, *, existing_env: dict[str, str] | None = None,
                           existing_headers: dict[str, str] | None = None) -> Json:
    if not isinstance(raw, dict):
        raise KiraError("INVALID_ARGUMENT", "MCP server configuration must be an object")
    allowed = {
        "id", "name", "transport", "command", "args", "env", "cwd",
        "url", "headers", "enabled", "read_only", "timeout_seconds",
    }
    extra = set(raw) - allowed
    if extra:
        raise KiraError("INVALID_ARGUMENT", f"Unsupported MCP server field: {sorted(extra)[0]}")
    name = _text(raw.get("name"), "name", 80).strip()
    server_id = raw.get("id")
    if isinstance(server_id, str) and server_id.strip():
        server_id = server_id.strip().casefold()
        if not SERVER_ID_RE.fullmatch(server_id):
            raise KiraError("INVALID_ARGUMENT", "Server ID must use lowercase letters, digits, hyphens or underscores")
    else:
        server_id = slugify_server_id(name)

    transport = raw.get("transport")
    if not isinstance(transport, str) or transport not in {"stdio", "http"}:
        raise KiraError("INVALID_ARGUMENT", "MCP transport must be 'stdio' or 'http'")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise KiraError("INVALID_ARGUMENT", "enabled must be a boolean")
    read_only = raw.get("read_only", False)
    if not isinstance(read_only, bool):
        raise KiraError("INVALID_ARGUMENT", "read_only must be a boolean")
    timeout = raw.get("timeout_seconds", 30)
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise KiraError("INVALID_ARGUMENT", "timeout_seconds must be an integer between 1 and 120")

    result: Json = {
        "id": server_id,
        "name": name,
        "transport": transport,
        "enabled": enabled,
        "read_only": read_only,
        "timeout_seconds": timeout,
        "command": "",
        "args": [],
        "env": {},
        "cwd": "",
        "url": "",
        "headers": {},
    }

    if transport == "stdio":
        command = _text(raw.get("command"), "command", 1024).strip()
        raw_args = raw.get("args", [])
        if not isinstance(raw_args, list) or len(raw_args) > 64:
            raise KiraError("INVALID_ARGUMENT", "args must be a list of at most 64 arguments")
        args = [_text(item, "arg", 2048, empty=True) for item in raw_args]
        raw_env = raw.get("env", {})
        if not isinstance(raw_env, dict) or len(raw_env) > 64:
            raise KiraError("INVALID_ARGUMENT", "env must be an object with at most 64 entries")
        env: dict[str, str] = {}
        for k, v in raw_env.items():
            if not isinstance(k, str) or not ENV_KEY_RE.fullmatch(k):
                raise KiraError("INVALID_ARGUMENT", f"Invalid environment variable name: {k}")
            val = _text(v, f"env.{k}", 4096, empty=True)
            if val == "[KEEP]" and existing_env and k in existing_env:
                env[k] = existing_env[k]
            else:
                env[k] = val
        cwd = _text(raw.get("cwd", ""), "cwd", 2048, empty=True).strip()
        result.update(command=command, args=args, env=env, cwd=cwd)
    else:
        url = _text(raw.get("url"), "url", 2048).strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise KiraError("INVALID_ARGUMENT", "MCP HTTP URL must use http:// or https://")
        raw_headers = raw.get("headers", {})
        if not isinstance(raw_headers, dict) or len(raw_headers) > 32:
            raise KiraError("INVALID_ARGUMENT", "headers must be an object with at most 32 entries")
        headers: dict[str, str] = {}
        for k, v in raw_headers.items():
            if not isinstance(k, str) or not HEADER_KEY_RE.fullmatch(k):
                raise KiraError("INVALID_ARGUMENT", f"Invalid HTTP header name: {k}")
            val = _text(v, f"headers.{k}", 4096, empty=True)
            if any(ch in val for ch in ("\r", "\n")):
                raise KiraError("INVALID_ARGUMENT", "HTTP header values cannot contain newlines")
            if val == "[KEEP]" and existing_headers and k in existing_headers:
                headers[k] = existing_headers[k]
            else:
                headers[k] = val
        result.update(url=url, headers=headers)
    return result


def parse_mcp_servers_json(raw_text: str) -> list[Json]:
    """Parse Claude Desktop / Cursor / Windsurf mcpServers JSON into validated configs."""
    text = _text(raw_text, "json", MAX_MCP_IMPORT_BYTES)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise KiraError("INVALID_ARGUMENT", "Invalid JSON for MCP server import") from exc
    if not isinstance(parsed, dict):
        raise KiraError("INVALID_ARGUMENT", "MCP configuration JSON must be an object")

    servers_map = parsed.get("mcpServers")
    if servers_map is None and "servers" in parsed and isinstance(parsed["servers"], dict):
        servers_map = parsed["servers"]
    if servers_map is None:
        if any(key in parsed for key in ("command", "url", "serverUrl", "httpUrl")):
            name = str(parsed.get("name") or parsed.get("id") or "mcp-server")
            servers_map = {name: parsed}
        else:
            servers_map = parsed

    if not isinstance(servers_map, dict) or not servers_map:
        raise KiraError("INVALID_ARGUMENT", "No MCP servers found in the provided JSON")
    if len(servers_map) > MAX_SERVERS:
        raise KiraError("LIMIT_REACHED", f"Cannot import more than {MAX_SERVERS} MCP servers")

    configs: list[Json] = []
    seen_ids: set[str] = set()
    for key, item in servers_map.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            raise KiraError("INVALID_ARGUMENT", "Each MCP server entry must be an object")
        url = item.get("url") or item.get("serverUrl") or item.get("httpUrl") or ""
        command = item.get("command") or ""
        transport = item.get("transport") or item.get("type") or ("http" if url and not command else "stdio")
        if transport in {"sse", "streamable-http", "streamable_http"}:
            transport = "http"
        base_id = slugify_server_id(str(item.get("id") or key))
        candidate_id = base_id
        suffix = 2
        while candidate_id in seen_ids:
            candidate_id = f"{base_id[:58]}-{suffix}"
            suffix += 1
        seen_ids.add(candidate_id)
        display_name = str(item.get("name") or key).strip()[:80] or candidate_id
        env_val = item.get("env")
        raw_env = env_val if isinstance(env_val, dict) else {}
        headers_val = item.get("headers")
        raw_headers = headers_val if isinstance(headers_val, dict) else {}
        args_val = item.get("args")
        raw_args = args_val if isinstance(args_val, list) else []
        normalized: Json = {
            "id": candidate_id,
            "name": display_name,
            "transport": transport,
            "enabled": bool(item.get("enabled", not item.get("disabled", False))),
            "read_only": bool(item.get("read_only", item.get("readOnly", False))),
            "timeout_seconds": int(item["timeout_seconds"])
            if type(item.get("timeout_seconds")) is int and 1 <= item["timeout_seconds"] <= 120 else 30,
        }
        if transport == "stdio":
            normalized.update(
                command=str(command),
                args=[str(arg) for arg in raw_args],
                env={str(k): str(v) for k, v in raw_env.items()},
                cwd=str(item.get("cwd") or ""),
            )
        else:
            normalized.update(
                url=str(url),
                headers={str(k): str(v) for k, v in raw_headers.items()},
            )
        configs.append(validate_server_config(normalized))
    return configs


def sanitize_mcp_tool(raw: Any) -> Json | None:
    """Sanitize a single tool descriptor returned by an external MCP server's tools/list."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 128 or "\0" in name:
        return None
    name = name.strip()
    raw_desc = raw.get("description")
    description = raw_desc if isinstance(raw_desc, str) else ""
    description = description.replace("\0", "").strip()[:1200]
    schema = raw.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    else:
        try:
            encoded = json.dumps(schema, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > 16_384:
                schema = {"type": "object", "properties": {}}
        except (TypeError, ValueError):
            schema = {"type": "object", "properties": {}}
    raw_ann = raw.get("annotations")
    annotations = raw_ann if isinstance(raw_ann, dict) else {}
    read_only = bool(annotations.get("readOnlyHint") is True or raw.get("readOnlyHint") is True)
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "read_only": read_only,
    }


def mcp_action_contract() -> Json:
    return {
        "list_servers": {"payload": {}},
        "list_tools": {"payload": {"server_id": "optional server id", "query": "optional search text"}},
        "describe": {"payload": {"server_id": "required server id", "tool_name": "required tool name"}},
        "call": {"payload": {"server_id": "required server id", "tool_name": "required tool name", "arguments": {}}},
    }


def validate_mcp_action(action: Any, payload: Any) -> tuple[str, Json]:
    if not isinstance(action, str) or action not in MCP_ACTIONS:
        raise KiraError("INVALID_ARGUMENT", "Unknown MCP tool action")
    if not isinstance(payload, dict):
        raise KiraError("INVALID_ARGUMENT", "MCP payload must be an object")
    if action == "list_servers":
        if payload:
            raise KiraError("INVALID_ARGUMENT", "list_servers takes an empty payload")
        return action, {}
    if action == "list_tools":
        if set(payload) - {"server_id", "query"}:
            raise KiraError("INVALID_ARGUMENT", "list_tools accepts only server_id and query")
        result: Json = {}
        if "server_id" in payload and payload["server_id"] is not None:
            result["server_id"] = _text(payload["server_id"], "server_id", 64).strip().casefold()
        if "query" in payload and payload["query"] is not None:
            result["query"] = _text(payload["query"], "query", 200, empty=True).strip()
        return action, result
    if action == "describe":
        if set(payload) != {"server_id", "tool_name"}:
            raise KiraError("INVALID_ARGUMENT", "describe requires server_id and tool_name")
        return action, {
            "server_id": _text(payload["server_id"], "server_id", 64).strip().casefold(),
            "tool_name": _text(payload["tool_name"], "tool_name", 128).strip(),
        }
    if set(payload) - {"server_id", "tool_name", "arguments"} or {"server_id", "tool_name"} - set(payload):
        raise KiraError("INVALID_ARGUMENT", "call requires server_id, tool_name and optional arguments")
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        raise KiraError("INVALID_ARGUMENT", "MCP tool arguments must be a JSON object")
    try:
        raw = json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise KiraError("INVALID_ARGUMENT", "MCP tool arguments must be finite JSON") from exc
    if len(raw) > 32 * 1024:
        raise KiraError("OUTPUT_LIMIT", "MCP tool arguments exceed 32 KiB")
    return action, {
        "server_id": _text(payload["server_id"], "server_id", 64).strip().casefold(),
        "tool_name": _text(payload["tool_name"], "tool_name", 128).strip(),
        "arguments": arguments,
    }


def mcp_tool_specs() -> list[Json]:
    return [{
        "type": "function",
        "name": "mcp_tools",
        "description": (
            "Discover, inspect and invoke tools from user-configured external MCP servers. "
            "Use list_servers/list_tools/describe to inspect schemas, and call to invoke a tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": sorted(MCP_ACTIONS)},
                "payload": {"type": "object"},
            },
            "required": ["action", "payload"],
            "additionalProperties": False,
        },
    }]
