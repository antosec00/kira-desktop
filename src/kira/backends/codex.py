"""Codex 0.162 App Server bridge. Native tool events are observations only."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import signal
from pathlib import Path
from typing import Any

from kira import __version__
from kira.backends.client_tools import (
    KIRA_CLIENT_TOOL_NAMES,
    ClientToolBridge,
    ClientToolError,
    encoded,
    validate_tools,
)
from kira.backends.reasoning_summary import CodexSummaryFilter, PublicSummaries
from kira.backends.kira_mcp import MCP_SERVER_NAME, KiraMcpRelay, mcp_tool_name
from kira.domain.account_usage import normalize_codex_usage, unavailable_codex_usage
from kira.domain.contracts import HOST_PERMISSIONS, PERMISSIONS, Json, KiraError, ProviderStarted, RunContext
from kira.domain.questions import MAX_INPUT_REQUESTS, validate_answers, validate_request

CAPABILITIES = {"streaming": True, "tools": True, "cancellation": True, "resume": True,
                "usage": True, "images": False, "context_controls": True,
                "steering": True, "planning": True, "native_fork": True,
                "native_compaction": True}
NATIVE_CONTROL_CAPABILITIES = frozenset({"steering", "planning", "native_fork",
                                       "native_compaction", "context_controls"})
MAX_RPC_FRAME = 8 * 1024 * 1024
NATIVE_AGENT_FEATURES = frozenset({"multi_agent", "multi_agent_v2", "multi_agent_v2_dynamic_tools"})
MAX_STRUCTURED_TEXT_BYTES = 65_536
QUESTION_FEATURES = frozenset({"default_mode_request_user_input", "send_message_to_user_async"})
MAX_STEERING_REQUESTS = 256
MAX_CONTROL_TEXT_BYTES = 65_536
COMPACTION_TIMEOUT = 90.0


class ProtocolError(Exception):
    """Codex did not provide a trustworthy protocol result."""


class RpcError(ProtocolError):
    pass


def child_environment(binary: str = "codex") -> dict[str, str]:
    """Keep provider auth with Codex; avoid unrelated host app credentials."""
    allowed = {"HOME", "PATH", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM",
               "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
               "XDG_RUNTIME_DIR", "CODEX_HOME", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "OPENAI_API_KEY", "CODEX_API_KEY", "DISPLAY", "WAYLAND_DISPLAY",
               "DBUS_SESSION_BUS_ADDRESS"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    if Path(binary).is_absolute():
        # npm/nvm's Codex wrapper uses /usr/bin/env node. Desktop and systemd
        # environments may omit nvm, even when the configured wrapper is absolute.
        # Only the explicitly configured executable's directory is added, never cwd.
        directory = str(Path(binary).parent)
        existing = environment.get("PATH", os.defpath)
        if existing.split(os.pathsep)[0] != directory:
            environment["PATH"] = directory + os.pathsep + existing
    return environment


class AppServer:
    """Correlated JSON RPC stdio, bounded notifications, independent stderr drain."""

    def __init__(self, binary: str) -> None:
        self.binary = binary
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[int, asyncio.Future[Json]] = {}
        self.notifications: asyncio.Queue[Json] = asyncio.Queue(maxsize=2048)
        self.tasks: list[asyncio.Task[Any]] = []
        self._counter = 0
        self._write_lock = asyncio.Lock()
        self.closed = False
        self.stderr_bytes = 0
        self.initialize_result: Json = {}
        self._summary_filter = CodexSummaryFilter()

    async def start(self, *, experimental_api: bool = True) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.binary, "app-server", "--listen", "stdio://",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=MAX_RPC_FRAME,
            start_new_session=True, env=child_environment(self.binary))
        self.tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._drain_stderr())]
        self.initialize_result = await self.request("initialize", {
            "clientInfo": {"name": "kira_desktop", "title": "Kira Desktop", "version": __version__},
            "capabilities": {"experimentalApi": experimental_api}})
        await self.send({"method": "initialized"})

    async def send(self, value: Json) -> None:
        if self.closed or not self.process or not self.process.stdin:
            raise ProtocolError("Codex App Server is disconnected")
        data = (json.dumps(value, ensure_ascii=False) + "\n").encode()
        if len(data) > MAX_RPC_FRAME:
            raise ProtocolError("Codex request exceeds 8 MiB")
        async with self._write_lock:
            try:
                self.process.stdin.write(data)
                await self.process.stdin.drain()
            except (ConnectionError, BrokenPipeError) as exc:
                raise ProtocolError("Codex App Server input closed") from exc

    async def request(self, method: str, params: Json, timeout: float = 30) -> Json:
        self._counter += 1
        request_id = self._counter
        future: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def respond(self, request_id: Any, result: Json) -> None:
        await self.send({"id": request_id, "result": result})

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        error = "Codex App Server closed its output; active outcome is unknown"
        try:
            while line := await self.process.stdout.readline():
                if len(line) > MAX_RPC_FRAME:
                    raise ProtocolError("Codex frame exceeds 8 MiB")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ProtocolError("Codex response must be an object")
                if "method" not in value and "id" in value:
                    future = self.pending.get(value["id"])
                    if future and not future.done():
                        if "error" in value:
                            message = value["error"].get("message", "Codex RPC failed")
                            future.set_exception(RpcError(str(message)[:1000]))
                        elif isinstance(value.get("result"), dict):
                            future.set_result(value["result"])
                        else:
                            future.set_exception(ProtocolError("Invalid Codex RPC response"))
                elif isinstance(value.get("method"), str):
                    method = value["method"]
                    if not isinstance(value.get("params", {}), dict):
                        raise ProtocolError("Codex notification params must be an object")
                    if method.startswith("codex/event/"):
                        continue
                    item = value.get("params", {}).get("item", {})
                    if method.startswith("item/reasoning/") or (
                            isinstance(item, dict) and item.get("type") == "reasoning"):
                        # Only public summaries reach the queue. Raw content/text,
                        # unknown methods and all other reasoning fields are discarded.
                        summary = self._summary_filter.event(value)
                        if summary is not None and not self.notifications.full():
                            self.notifications.put_nowait(summary)
                        continue
                    self.notifications.put_nowait(value)
        except asyncio.CancelledError:
            raise
        except (ValueError, UnicodeError, asyncio.QueueFull, ProtocolError) as exc:
            error = f"Codex protocol failure: {type(exc).__name__}"
        finally:
            self.closed = True
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(ProtocolError(error))
            # Reserve delivery of the lost connection marker even on overflow.
            if self.notifications.full():
                self.notifications.get_nowait()
            self.notifications.put_nowait({"method": "kira/disconnected", "params": {"error": error}})

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        while chunk := await self.process.stderr.read(65536):
            # Raw diagnostic text can contain credentials. Count and discard it.
            self.stderr_bytes += len(chunk)

    async def close(self) -> None:
        self.closed = True
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def permission_overlay(ctx: RunContext) -> tuple[str, Json]:
    if ctx.permission == "read_only":
        return "read-only", {"type": "readOnly", "networkAccess": False}
    if ctx.permission == "project_write":
        return "workspace-write", {"type": "workspaceWrite", "writableRoots": [str(ctx.root)],
                                   "networkAccess": False, "excludeSlashTmp": True,
                                   "excludeTmpdirEnvVar": True}
    if ctx.permission in HOST_PERMISSIONS:
        return "danger-full-access", {"type": "dangerFullAccess"}
    raise ProtocolError("Unsupported project permission")


class CodexBackend:
    def __init__(self, binary: str = "codex") -> None:
        self.binary = binary
        self._control: AppServer | None = None
        self._control_lock = asyncio.Lock()
        self._active: set[AppServer] = set()
        self._login_id: str | None = None

    async def _get_control(self) -> AppServer:
        async with self._control_lock:
            if self._control is None or self._control.closed:
                if self._control:
                    await self._control.close()
                self._control = AppServer(self.binary)
                await self._control.start()
            return self._control

    async def discover(self) -> Json:
        try:
            rpc = await self._get_control()
            # initialize.userAgent carries the binary version, not the client
            # application version. Unknown/older versions do not advertise the
            # newly integrated protocol surface. Actual access remains unverified.
            version = re.match(r"^kira_desktop/(\d+)\.(\d+)\.(\d+)(?:[\s+-]|$)",
                               str(rpc.initialize_result.get("userAgent", "")))
            supports_controls = bool(version and tuple(map(int, version.groups())) >= (0, 162, 1))
            capabilities = {**CAPABILITIES,
                            **dict.fromkeys(NATIVE_CONTROL_CAPABILITIES, supports_controls)}
            account = await rpc.request("account/read", {"refreshToken": False})
            raw_account = account.get("account") or {}
            public_account = {"connected": bool(raw_account), "type": raw_account.get("type"),
                              "plan_type": raw_account.get("planType"),
                              "requires_openai_auth": account.get("requiresOpenaiAuth")}
            models = []
            cursor = None
            while True:
                request: Json = {"limit": 100}
                if cursor:
                    request["cursor"] = cursor
                catalog = await rpc.request("model/list", request)
                for item in catalog.get("data", []):
                    models.append({"id": item["model"], "name": item["displayName"],
                                   "capabilities": capabilities,
                                   "supported_reasoning_efforts": [e["reasoningEffort"]
                                        for e in item.get("supportedReasoningEfforts", [])],
                                   "default_reasoning_effort": item.get("defaultReasoningEffort"),
                                   "default": item.get("isDefault", False),
                                   "access_verified": False,
                                   "service_tiers": item.get("serviceTiers", []),
                                   "default_service_tier": item.get("defaultServiceTier")})
                cursor = catalog.get("nextCursor")
                if not cursor:
                    break
            return {"available": True, "models": models, "capabilities": capabilities,
                    "account": public_account, "binary": self.binary,
                    "server": {"user_agent": rpc.initialize_result.get("userAgent")},
                    "notice": "Model discovery does not verify account access; a real turn does."}
        except (OSError, ProtocolError, TimeoutError) as exc:
            return {"available": False, "models": [], "capabilities": {
                    **CAPABILITIES, **dict.fromkeys(NATIVE_CONTROL_CAPABILITIES, False)},
                    "error": f"Codex unavailable: {type(exc).__name__}: {str(exc)[:500]}"}

    async def login(self) -> Json:
        rpc = await self._get_control()
        # Codex owns browser flow and credential persistence. Kira never receives OAuth tokens.
        result = await rpc.request("account/login/start", {"type": "chatgpt"})
        self._login_id = result.get("loginId")
        return {key: result[key] for key in ("type", "loginId", "authUrl") if key in result}

    async def account_usage(self) -> Json:
        """Read the authenticated Codex account's quota without creating a turn.

        Account metadata is checked around the read so an observed sign-in
        change cannot label a snapshot with the previous account. App Server
        does not expose stable identity in account/read; the snapshot's optional
        accountId is retained, never equated with another provider's identity.
        """
        try:
            rpc = await self._get_control()
            before = await rpc.request("account/read", {"refreshToken": False})
            account = before.get("account")
            if account is None:
                return unavailable_codex_usage("not_connected")
            if not isinstance(account, dict):
                return unavailable_codex_usage("invalid_response")
            if account.get("type") != "chatgpt":
                return unavailable_codex_usage("unsupported_account", account)
            # No fallback, reset redemption or billing mutation is requested.
            snapshot = await rpc.request("account/rateLimits/read", {})
            after = await rpc.request("account/read", {"refreshToken": False})
            if after.get("account") != account or (
                    after.get("requiresOpenaiAuth") != before.get("requiresOpenaiAuth")):
                return unavailable_codex_usage("account_changed")
            return normalize_codex_usage(snapshot, account)
        except (OSError, ProtocolError, TimeoutError):
            # Provider diagnostics can contain sensitive data. A failed read
            # returns no previous snapshot or raw exception text.
            return unavailable_codex_usage("unavailable")

    async def wait_login(self, timeout: float = 600) -> Json:
        """Keep the official browser callback server alive, returning no auth payloads."""
        rpc = await self._get_control()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    event = await rpc.notifications.get()
                    if event.get("method") == "kira/disconnected":
                        return {"success": False, "error": "Codex login connection closed"}
                    if event.get("method") != "account/login/completed":
                        continue
                    params = event.get("params", {})
                    if params.get("loginId") not in (None, self._login_id):
                        continue
                    if params.get("success"):
                        return {"success": True}
                    return {"success": False, "error": "Codex browser login did not complete"}
        except TimeoutError:
            return {"success": False, "error": f"Codex browser login timed out after {timeout:g}s"}

    async def run(self, ctx: RunContext) -> Json:
        rpc = AppServer(self.binary)
        self._active.add(rpc)
        turn_started = False
        control_active = False
        assistant_relay: KiraMcpRelay | None = None
        try:
            if ctx.cancel.is_set():
                return {"state": "cancelled", "outcome": {"started": False}}
            client_names = validate_tools(ctx)
            if ctx.mode not in {"execute", "plan", "review", "recap"}:
                raise ProtocolError("Unsupported run mode")
            if ctx.mode != "execute" and ctx.permission != "read_only":
                raise ProtocolError("Planning, review and recap require read-only permissions")
            if ctx.output_schema is not None and (
                    not isinstance(ctx.output_schema, dict)
                    or len(encoded(ctx.output_schema)) > MAX_STRUCTURED_TEXT_BYTES):
                raise ProtocolError("Structured output schema must be an object of at most 64 KiB")
            if client_names or ctx.request_input is not None:
                await rpc.start(experimental_api=True)
            else:
                await rpc.start()
            if ctx.provider_started:
                if rpc.process is None:
                    raise ProtocolError("Codex provider ownership cannot be recorded without a process")
                try:
                    await ctx.provider_started(rpc.process.pid)
                except Exception as exc:
                    raise ProtocolError("Codex provider ownership could not be recorded") from exc
            sandbox, policy = permission_overlay(ctx)
            thread_params: Json = {"model": ctx.model, "cwd": str(ctx.root),
                "sandbox": sandbox, "approvalPolicy": "never", "approvalsReviewer": "user",
                "config": {"shell_environment_policy": {"inherit": "core"}}}
            if ctx.assistant_instructions:
                if not isinstance(ctx.assistant_instructions, str) or len(
                        ctx.assistant_instructions.encode("utf-8")) > MAX_CONTROL_TEXT_BYTES:
                    raise ProtocolError("Kira assistant instructions exceed their size limit")
                # Only the service-owned operating contract has developer
                # authority. Project instructions, memory and tool output stay
                # in the inspectable user-level supplied context below.
                thread_params["developerInstructions"] = ctx.assistant_instructions
            if client_names & KIRA_CLIENT_TOOL_NAMES:
                # thread/resume cannot add dynamicTools. A thread-local MCP
                # overlay attaches the same service-bound tools, including an
                # autonomous goal's, to both existing and new native
                # conversations without resetting their history.
                assistant_relay = KiraMcpRelay(ctx, client_names)
                thread_params["config"]["mcp_servers"] = {
                    MCP_SERVER_NAME: await assistant_relay.start()}
            effort = ctx.settings.get("reasoning_effort")
            if effort:
                thread_params["config"]["model_reasoning_effort"] = effort
            if ctx.settings.get("service_tier"):
                thread_params["serviceTier"] = ctx.settings["service_tier"]
            disable_native_agents = ctx.disable_native_agents or bool(client_names)
            if disable_native_agents:
                thread_params["config"]["features"] = dict.fromkeys(NATIVE_AGENT_FEATURES, False)
            if ctx.request_input is not None:
                thread_params["config"].setdefault("features", {}).update(
                    dict.fromkeys(QUESTION_FEATURES, True))
            backend_ref = ctx.session.get("backend_ref")
            if backend_ref:
                if await self._unfinished_native_goal(rpc, backend_ref):
                    raise ProtocolError("This Codex thread has an unfinished native goal. "
                                        "Finish that goal before continuing in Kira; "
                                        "Kira did not change or resume it")
                thread_params["threadId"] = backend_ref
                thread_params["excludeTurns"] = True
                thread_response = await rpc.request("thread/resume", thread_params)
            else:
                if client_names and assistant_relay is None:
                    thread_params["dynamicTools"] = ctx.client_tools
                thread_response = await rpc.request("thread/start", thread_params)
            thread_id = thread_response["thread"]["id"]
            if summary_filter := getattr(rpc, "_summary_filter", None):
                summary_filter.bind_thread(thread_id)
            if thread_response.get("model") != ctx.model:
                raise ProtocolError("Codex resolved a different model; turn not started")
            if effort and thread_response.get("reasoningEffort") != effort:
                raise ProtocolError("Codex resolved a different reasoning effort; turn not started")
            actual_sandbox = thread_response.get("sandbox", {})
            if actual_sandbox.get("type") != policy["type"]:
                raise ProtocolError("Codex resolved a different permission boundary; turn not started")
            if ctx.permission not in HOST_PERMISSIONS and \
                    actual_sandbox.get("networkAccess", False) is not False:
                raise ProtocolError("Codex enabled network access outside the project permission")
            if ctx.permission == "project_write":
                for root in actual_sandbox.get("writableRoots", []):
                    try:
                        Path(root).resolve().relative_to(ctx.root.resolve())
                    except (ValueError, TypeError) as exc:
                        raise ProtocolError("Codex resolved writable roots outside the project") from exc
            if disable_native_agents:
                await self._verify_native_agents_disabled(rpc, thread_id)
            if ctx.request_input is not None:
                await self._verify_question_features(rpc, thread_id)
            if assistant_relay is not None:
                await self._verify_assistant_tools(rpc, thread_id, assistant_relay)
            await ctx.emit("session.updated", {"backend_ref": thread_id})
            await ctx.emit("backend.notice", {"message": "Codex session configured",
                            "model": thread_response.get("model"),
                            "reasoning_effort": thread_response.get("reasoningEffort"),
                            "permission": ctx.permission,
                            "sandbox": thread_response.get("sandbox"),
                            "native_tools": "observed_only",
                            **({"native_agents": "disabled_and_verified"} if disable_native_agents
                               else {}),
                            **({"kira_tools": "connected_and_verified"}
                               if assistant_relay is not None else {})})
            if ctx.cancel.is_set():
                return {"state": "cancelled", "outcome": {"started": False}}
            prompt = ctx.prompt
            if ctx.supplied_context:
                prompt = "Kira-supplied project context:\n" + ctx.supplied_context + "\n\nTask:\n" + prompt
            if ctx.request_input is not None:
                prompt = ("Kira displays structured information questions persistently. Use "
                          "request_user_input_async when available for preferences or missing "
                          "information, with at most three questions and six choices each; "
                          "continue independent work while the question is pending. Use "
                          "request_user_input when the answer is required before proceeding. "
                          "Ask through these tools instead of only writing a question in prose. "
                          "Never infer consent from silence; these questions do not grant "
                          "permissions. Never request passwords, tokens or other secrets.\n\n"
                          + prompt)
            params: Json = {"threadId": thread_id, "model": ctx.model,
                            "cwd": str(ctx.root), "sandboxPolicy": policy,
                            "approvalPolicy": "never", "approvalsReviewer": "user",
                            "input": [{"type": "text", "text": prompt}],
                            "clientUserMessageId": ctx.run_id, "summary": "auto"}
            if effort:
                params["effort"] = effort
            if ctx.settings.get("service_tier"):
                params["serviceTier"] = ctx.settings["service_tier"]
            if ctx.output_schema is not None:
                params["outputSchema"] = ctx.output_schema
            # A native collaboration mode is sticky. Explicitly clear a previous
            # plan on ordinary turns, preserving the resolved model and effort.
            params["collaborationMode"] = {
                "mode": "plan" if ctx.mode == "plan" else "default",
                "settings": {"model": ctx.model,
                             "reasoning_effort": thread_response.get("reasoningEffort"),
                             "developer_instructions": None}}
            if ctx.mode == "plan":
                modes = await rpc.request("collaborationMode/list", {})
                if not any(isinstance(mode, dict) and mode.get("mode") == "plan"
                           for mode in modes.get("data", [])):
                    raise ProtocolError("Codex does not advertise native planning mode")
            turn_started = True  # Uncertain turn/start acknowledgement must never mean failed safely.
            turn = (await rpc.request("turn/start", params))["turn"]
            turn_id = turn["id"]
            if not isinstance(turn_id, str) or not turn_id:
                raise ProtocolError("Codex did not identify the active turn")
            if summary_filter := getattr(rpc, "_summary_filter", None):
                summary_filter.bind_turn(thread_id, turn_id)
            control_active = True
            controls: dict[str, tuple[str, Json | KiraError]] = {}
            control_lock = asyncio.Lock()

            async def control(action: str, values: Json) -> Json:
                if action != "steer":
                    raise KiraError("UNSUPPORTED", "Codex does not support this active-run action")
                request_id, text = values.get("request_id"), values.get("prompt")
                if (set(values) != {"request_id", "prompt"}
                        or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256
                        or not isinstance(text, str) or not text.strip()
                        or len(text.encode("utf-8")) > MAX_CONTROL_TEXT_BYTES):
                    raise KiraError("INVALID_PARAMS", "Invalid steering request")
                fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
                async with control_lock:
                    previous = controls.get(request_id)
                    if previous is not None:
                        if previous[0] != fingerprint:
                            raise KiraError("CONFLICT", "Steering request ID has different content")
                        if isinstance(previous[1], KiraError):
                            raise previous[1]
                        return dict(previous[1])
                    if not control_active or ctx.cancel.is_set() or rpc.closed:
                        raise KiraError("NOT_ACTIVE", "Codex turn is no longer available for steering")
                    if len(controls) >= MAX_STEERING_REQUESTS:
                        raise KiraError("LIMIT", "Steering request limit reached for this run")
                    unknown = KiraError("DELIVERY_UNKNOWN", "Steering delivery is unconfirmed; "
                                        "the request will not be sent again")
                    controls[request_id] = (fingerprint, unknown)
                    try:
                        accepted = await rpc.request("turn/steer", {
                            "threadId": thread_id, "expectedTurnId": turn_id,
                            "clientUserMessageId": request_id,
                            "input": [{"type": "text", "text": text}]})
                        if accepted.get("turnId") != turn_id:
                            raise ProtocolError("Steering acknowledgement has a different turn ID")
                    except RpcError as exc:
                        rejected = KiraError("PROVIDER_REJECTED", str(exc)[:1000])
                        controls[request_id] = (fingerprint, rejected)
                        raise rejected from exc
                    except (OSError, ProtocolError, TimeoutError) as exc:
                        raise unknown from exc
                    result = {"accepted": True, "thread_id": thread_id,
                              "turn_id": turn_id, "request_id": request_id}
                    controls[request_id] = (fingerprint, result)
                    return dict(result)

            if ctx.register_control:
                ctx.register_control(control)
            return await self._observe(rpc, ctx, thread_id, turn_id, client_names)
        except asyncio.CancelledError:
            raise
        except (OSError, ProtocolError, ClientToolError, TimeoutError, KeyError, TypeError) as exc:
            if ctx.cancel.is_set() and not turn_started:
                return {"state": "cancelled", "outcome": {"started": False}}
            return {"state": "interrupted" if turn_started else "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:1000]}"}
        finally:
            control_active = False
            try:
                if ctx.register_control:
                    ctx.register_control(None)
            finally:
                try:
                    await rpc.close()
                finally:
                    self._active.discard(rpc)
                    if assistant_relay is not None:
                        await assistant_relay.close()

    async def _verify_assistant_tools(self, rpc: AppServer, thread_id: str,
                                      relay: KiraMcpRelay) -> None:
        """Fail before inference when the local provider did not attach Kira."""
        expected = {mcp_tool_name(name) for name in relay.names}
        async with asyncio.timeout(15):
            while True:
                inventory = await rpc.request("mcpServerStatus/list", {
                    "threadId": thread_id, "serverName": MCP_SERVER_NAME,
                    "detail": "toolsAndAuthOnly", "limit": 100}, timeout=10)
                servers = inventory.get("data", [])
                if not isinstance(servers, list):
                    raise ProtocolError("Codex returned invalid Kira tool inventory")
                matches = [item for item in servers if isinstance(item, dict)
                           and item.get("name") == MCP_SERVER_NAME]
                if len(matches) != 1:
                    raise ProtocolError("Codex did not attach the Kira assistant tools")
                server = matches[0]
                catalog = server.get("tools")
                actual = ({tool.get("name") for tool in catalog.values()
                           if isinstance(tool, dict) and isinstance(tool.get("name"), str)}
                          if isinstance(catalog, dict) else set())
                if relay.ready and actual == expected and not server.get("toolsError") \
                        and server.get("runtimeStatus") in {None, "connected"}:
                    return
                if server.get("runtimeStatus") not in {None, "notStarted", "starting"}:
                    raise ProtocolError("Codex could not verify all Kira assistant tools")
                if relay.ctx.cancel.is_set():
                    raise ProtocolError("Run cancelled while connecting Kira assistant tools")
                await asyncio.sleep(0.1)

    async def manage_thread(self, action: str, session: Json, project: Json,
                            params: Json, *, provider_started: ProviderStarted | None = None) -> Json:
        """Perform one explicit idle-thread action on an owned, short-lived server.

        The caller persists operation identity before dispatch and serializes
        project work. Neither a lost acknowledgement nor cancellation is retried.
        """
        if action not in {"fork", "compact"}:
            raise KiraError("UNSUPPORTED", "Unsupported Codex thread action")
        allowed = {"last_turn_id"} if action == "fork" else {"timeout_seconds"}
        if set(params) - allowed:
            raise KiraError("INVALID_PARAMS", "Unsupported Codex thread action parameters")
        source_id = session.get("backend_ref")
        model = session.get("model")
        if not isinstance(source_id, str) or not source_id or not isinstance(model, str) or not model:
            raise KiraError("NO_PROVIDER_THREAD", "The chat has no usable Codex thread")
        last_turn_id = params.get("last_turn_id")
        if last_turn_id is not None and (
                not isinstance(last_turn_id, str) or not 1 <= len(last_turn_id) <= 256):
            raise KiraError("INVALID_PARAMS", "Invalid native branch turn ID")
        timeout = params.get("timeout_seconds", COMPACTION_TIMEOUT)
        if type(timeout) not in {int, float} or not 0 < timeout <= COMPACTION_TIMEOUT:
            raise KiraError("INVALID_PARAMS", "Invalid compaction timeout")
        permission = project.get("permission", "read_only")
        if permission not in PERMISSIONS:
            raise KiraError("INVALID_PARAMS", "Invalid project permission")
        root = Path(project["root"])
        settings = session.get("settings", {})
        effort = settings.get("reasoning_effort")
        request: Json = {
            "threadId": source_id, "model": model, "cwd": str(root),
            "sandbox": {"read_only": "read-only", "project_write": "workspace-write",
                        "trusted_host": "danger-full-access",
                        "full_access": "danger-full-access"}[permission],
            "approvalPolicy": "never", "approvalsReviewer": "user", "excludeTurns": True,
            "config": {"shell_environment_policy": {"inherit": "core"}}}
        if effort:
            request["config"]["model_reasoning_effort"] = effort
        if settings.get("service_tier"):
            request["serviceTier"] = settings["service_tier"]
        if action == "fork":
            # Copying a native goal must not start its automatic continuation.
            request["deferGoalContinuation"] = True
            if last_turn_id is not None:
                request["lastTurnId"] = last_turn_id
        rpc = AppServer(self.binary)
        self._active.add(rpc)
        dispatched = False
        compact_turn: list[str] = []
        try:
            async with asyncio.timeout(float(timeout)):
                await rpc.start()
                if provider_started is not None:
                    if rpc.process is None:
                        raise ProtocolError("Codex control process ownership cannot be established")
                    try:
                        await provider_started(rpc.process.pid)
                    except Exception as exc:
                        raise ProtocolError("Codex control process ownership could not be recorded") from exc
                metadata = await rpc.request("thread/read", {
                    "threadId": source_id, "includeTurns": False})
                thread = metadata.get("thread", {})
                if thread.get("id") != source_id or thread.get("status", {}).get("type") \
                        not in {"idle", "notLoaded"}:
                    raise KiraError("BUSY", "Codex thread is not confirmed idle")
                if action == "compact":
                    # An independently managed active goal could cause implicit
                    # continuation when the thread is resumed. Never alter it.
                    if await self._unfinished_native_goal(rpc, source_id):
                        raise KiraError("BUSY", "Finish the native Codex goal before compaction")
                if action == "fork":
                    dispatched = True
                response = await rpc.request("thread/fork" if action == "fork"
                                             else "thread/resume", request)
                expected_type = {"read_only": "readOnly", "project_write": "workspaceWrite",
                                 "trusted_host": "dangerFullAccess",
                                 "full_access": "dangerFullAccess"}[permission]
                sandbox = response.get("sandbox", {})
                if response.get("model") != model or (effort and response.get("reasoningEffort")
                                                       != effort):
                    raise ProtocolError("Codex resolved a different model or reasoning effort")
                if sandbox.get("type") != expected_type:
                    raise ProtocolError("Codex resolved a different permission boundary")
                if permission not in HOST_PERMISSIONS and sandbox.get("networkAccess", False) is not False:
                    raise ProtocolError("Codex enabled network access outside the project permission")
                if permission == "project_write":
                    for writable in sandbox.get("writableRoots", []):
                        try:
                            Path(writable).resolve().relative_to(root.resolve())
                        except (TypeError, ValueError) as exc:
                            raise ProtocolError("Codex resolved writable roots outside the project") from exc
                target = response.get("thread", {})
                target_id = target.get("id")
                if not isinstance(target_id, str) or not target_id or (
                        action == "fork" and target_id == source_id) or (
                        action == "compact" and target_id != source_id):
                    raise ProtocolError("Codex returned an invalid thread identity")
                if action == "fork":
                    if target.get("status", {}).get("type") != "idle":
                        raise ProtocolError("Forked Codex thread is not confirmed idle")
                    return {"backend_ref": target_id, "source_backend_ref": source_id,
                            "model": model, "reasoning_effort": response.get("reasoningEffort"),
                            **({"last_turn_id": last_turn_id} if last_turn_id else {})}
                if target.get("status", {}).get("type") != "idle":
                    raise KiraError("BUSY", "Resumed Codex thread is not confirmed idle")
                # Resume metadata cannot be mistaken for the new compaction.
                while not rpc.notifications.empty():
                    event = rpc.notifications.get_nowait()
                    if event.get("method") == "kira/disconnected":
                        raise ProtocolError("Codex disconnected before compaction")
                    if event.get("method") == "turn/started" and \
                            event.get("params", {}).get("threadId") == source_id:
                        raise KiraError("BUSY", "Codex unexpectedly started work before compaction")
                dispatched = True
                await rpc.request("thread/compact/start", {"threadId": source_id})
                return await self._observe_compaction(rpc, source_id, compact_turn)
        except asyncio.CancelledError:
            await self._interrupt_compaction(rpc, source_id, compact_turn)
            raise
        except RpcError as exc:
            raise KiraError("PROVIDER_REJECTED", str(exc)[:1000]) from exc
        except (OSError, ProtocolError, TimeoutError, KeyError, TypeError, ValueError) as exc:
            await self._interrupt_compaction(rpc, source_id, compact_turn)
            raise KiraError("DELIVERY_UNKNOWN" if dispatched else "PROVIDER_UNAVAILABLE",
                            "Codex thread action was not confirmed; it will not be repeated: "
                            + str(exc)[:500]) from exc
        finally:
            await rpc.close()
            self._active.discard(rpc)

    async def _unfinished_native_goal(self, rpc: AppServer, thread_id: str) -> bool:
        """Resume has no defer flag in 0.162.1; refuse implicit goal continuation."""
        goal = (await rpc.request("thread/goal/get", {"threadId": thread_id})).get("goal")
        return goal is not None and (not isinstance(goal, dict) or goal.get("status") != "complete")

    async def _interrupt_compaction(self, rpc: AppServer, thread_id: str,
                                    turn: list[str]) -> None:
        if turn and not rpc.closed:
            with contextlib.suppress(OSError, ProtocolError, TimeoutError):
                await rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn[0]},
                                  timeout=3)

    async def _observe_compaction(self, rpc: AppServer, thread_id: str,
                                  turn: list[str]) -> Json:
        started_items: set[str] = set()
        compacted = False
        while True:
            event = await rpc.notifications.get()
            method, values = event.get("method"), event.get("params", {})
            if method == "kira/disconnected":
                raise ProtocolError("Codex disconnected during compaction")
            if values.get("threadId") != thread_id:
                continue
            if "id" in event:
                # Compaction is not a permission-granting or tool-running action.
                await rpc.send({"id": event["id"], "error": {
                    "code": -32601, "message": "Callbacks are unavailable during compaction"}})
                raise ProtocolError("Codex requested an unexpected callback during compaction")
            if method == "turn/started":
                turn_id = values.get("turn", {}).get("id")
                if not isinstance(turn_id, str) or not turn_id or (turn and turn[0] != turn_id):
                    raise ProtocolError("Compaction started an unexpected turn")
                turn[:] = [turn_id]
                continue
            if not turn:
                if method in {"item/started", "item/completed", "turn/completed"}:
                    raise ProtocolError("Compaction progress lacks its turn-start identity")
                continue
            if values.get("turnId", turn[0]) != turn[0]:
                continue
            if method in {"item/started", "item/completed"}:
                item = values.get("item", {})
                item_id = item.get("id")
                if values.get("turnId") != turn[0]:
                    raise ProtocolError("Compaction item lacks its exact turn identity")
                if item.get("type") != "contextCompaction" or not isinstance(item_id, str) \
                        or not 1 <= len(item_id) <= 256:
                    raise ProtocolError("Unexpected item during compaction")
                if method == "item/started":
                    if len(started_items) >= 16 and item_id not in started_items:
                        raise ProtocolError("Compaction exceeded its item bound")
                    started_items.add(item_id)
                elif item_id in started_items:
                    compacted = True
                else:
                    raise ProtocolError("Compaction completion lacks its item-start identity")
            elif method == "turn/completed":
                terminal = values.get("turn", {})
                if terminal.get("id") != turn[0]:
                    continue
                if terminal.get("status") == "completed" and compacted:
                    return {"backend_ref": thread_id, "turn_id": turn[0], "compacted": True}
                raise ProtocolError("Compaction did not provide a successful terminal outcome")

    async def _verify_native_agents_disabled(self, rpc: AppServer, thread_id: str) -> None:
        """Require the effective thread configuration, never just the requested overlay."""
        remaining = set(NATIVE_AGENT_FEATURES)
        cursor: str | None = None
        cursors: set[str] = set()
        for _ in range(4):
            params: Json = {"threadId": thread_id, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = await rpc.request("experimentalFeature/list", params, timeout=10)
            features = result.get("data")
            if not isinstance(features, list):
                raise ProtocolError("Codex native-agent settings could not be verified")
            for feature in features:
                if not isinstance(feature, dict):
                    raise ProtocolError("Codex returned an invalid native-agent setting")
                name = feature.get("name")
                if name in NATIVE_AGENT_FEATURES:
                    if feature.get("enabled") is not False:
                        raise ProtocolError("Codex native-agent delegation is not disabled")
                    remaining.discard(name)
            cursor = result.get("nextCursor")
            if not cursor:
                if remaining:
                    raise ProtocolError("Codex does not expose every required native-agent setting")
                return
            if not isinstance(cursor, str) or cursor in cursors:
                raise ProtocolError("Codex native-agent settings pagination is invalid")
            cursors.add(cursor)
        raise ProtocolError("Codex native-agent settings exceed the verification page bound")

    async def _verify_question_features(self, rpc: AppServer, thread_id: str) -> None:
        """Question tools must actually exist in this thread, including resumed threads."""
        remaining = set(QUESTION_FEATURES)
        cursor: str | None = None
        cursors: set[str] = set()
        for _ in range(4):
            params: Json = {"threadId": thread_id, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = await rpc.request("experimentalFeature/list", params, timeout=10)
            features = result.get("data")
            if not isinstance(features, list):
                raise ProtocolError("Codex structured-question features could not be verified")
            for feature in features:
                if not isinstance(feature, dict):
                    raise ProtocolError("Codex returned an invalid question feature")
                if feature.get("name") in QUESTION_FEATURES:
                    if feature.get("enabled") is not True:
                        raise ProtocolError("Codex structured-question tools are unavailable")
                    remaining.discard(feature["name"])
            cursor = result.get("nextCursor")
            if not cursor:
                if remaining:
                    raise ProtocolError("Codex does not expose the required structured-question "
                                        "features; use a supported Codex version")
                return
            if not isinstance(cursor, str) or cursor in cursors:
                raise ProtocolError("Codex question feature pagination is invalid")
            cursors.add(cursor)
        raise ProtocolError("Codex question features exceed the verification page bound")

    async def _question(self, rpc: AppServer, ctx: RunContext, event: Json) -> Json:
        params = event["params"]
        if ctx.request_input is None or ctx.read_input is None:
            raise ProtocolError("Structured user input requires the Kira question service")
        identity: str | None = None
        retrieved = False
        delivered = False
        try:
            request = validate_request({"request_id": params.get("itemId"),
                "blocking": params.get("isBlocking", True), "questions": params.get("questions")})
            result = await ctx.request_input(request)
            if not isinstance(result, dict):
                raise ProtocolError("Question service returned an invalid result")
            if result.get("status") in {"pending", "answered"}:
                identity = result.get("request_id")
                if not isinstance(identity, str) or not identity:
                    raise ProtocolError("Question service omitted the durable request ID")
            if result.get("status") == "pending":
                assert identity is not None
                result = await ctx.read_input(identity, True)
            if not isinstance(result, dict):
                raise ProtocolError("Question service returned an invalid answer result")
            if result.get("status") == "answered" and result.get("ok") is True:
                retrieved = True
                answers = validate_answers(request["questions"], result.get("answers"))
                response = {"answers": answers}
            elif result.get("status") in {"closed", "cancelled"}:
                # An empty map represents no answer, never a default selection or consent.
                response = {"answers": {}}
            else:
                raise ProtocolError("Question service did not provide an explicit outcome")
            await rpc.respond(event["id"], response)
            delivered = True
            if retrieved:
                await ctx.emit("input.delivered", {"request_id": identity,
                                                  "transport": "json-rpc-response"})
            return response
        except (Exception, asyncio.CancelledError) as exc:
            if retrieved and not delivered:
                await ctx.emit("input.delivery_failed", {"request_id": identity,
                    "reason": "Answer delivery to the active Codex request was not confirmed; "
                              "copy the saved answer into a later explicit turn. No automatic retry."})
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, ProtocolError):
                raise
            if isinstance(exc, KiraError):
                raise ProtocolError(f"Structured question rejected: {exc}") from exc
            raise ProtocolError("Question callback failed; inspect durable state") from exc

    async def _async_question(self, rpc: AppServer, ctx: RunContext, thread_id: str,
                              turn_id: str, item: Json) -> Json:
        """Native async questions are structured agent-message metadata, not RPC requests."""
        if ctx.request_input is None or ctx.read_input is None:
            raise ProtocolError("Structured user input requires the Kira question service")
        raw_questions = item.get("questions")
        if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 3:
            raise ProtocolError("Async question metadata must contain one to three questions")
        questions: list[Json] = []
        for index, question in enumerate(raw_questions):
            if not isinstance(question, dict) or set(question) - {"title", "options"}:
                raise ProtocolError("Unsupported async question metadata")
            options = question.get("options")
            if options is None:
                options = []
            if not isinstance(options, list):
                raise ProtocolError("Async question choices must be an array")
            questions.append({"id": f"q{index + 1}", "header": "", "question": question.get("title"),
                "isOther": True, "options": [{"label": option, "description": ""}
                                             for option in options]})
        identity: str | None = None
        retrieved = False
        delivered = False
        try:
            request = validate_request({"request_id": item.get("id"), "blocking": False,
                                        "questions": questions})
            result = await ctx.request_input(request)
            if not isinstance(result, dict) or not isinstance(result.get("request_id"), str):
                raise ProtocolError("Question service omitted the durable request ID")
            identity = result["request_id"]
            if result.get("status") == "pending":
                result = await ctx.read_input(identity, True)
            if not isinstance(result, dict):
                raise ProtocolError("Question service returned an invalid answer result")
            if result.get("status") in {"closed", "cancelled"}:
                return {}
            if result.get("status") != "answered" or result.get("ok") is not True:
                raise ProtocolError("Question service did not provide an explicit outcome")
            retrieved = True
            answers = validate_answers(request["questions"], result.get("answers"))
            entries = [{"question": question["question"],
                "answer": answers[question["id"]]["answers"][0],
                "questionItemId": json.dumps(["request_user_input_async", item["id"], index],
                                             separators=(",", ":"))}
                       for index, question in enumerate(request["questions"])]
            content = json.dumps(entries, ensure_ascii=False).replace("<", "\\u003c")
            response = await rpc.request("turn/steer", {"threadId": thread_id,
                "expectedTurnId": turn_id, "clientUserMessageId": identity,
                "input": [{"type": "text", "text": "<send_user_message_question_reply>"
                           + content + "</send_user_message_question_reply>"}]})
            if response.get("turnId") != turn_id:
                raise ProtocolError("Codex did not confirm the expected turn for the answer")
            delivered = True
            await ctx.emit("input.delivered", {"request_id": identity, "transport": "turn/steer"})
            return {}
        except (Exception, asyncio.CancelledError) as exc:
            if retrieved and not delivered:
                await ctx.emit("input.delivery_failed", {"request_id": identity,
                    "reason": "Answer delivery to the active Codex turn was not confirmed; "
                              "copy the saved answer into a later explicit turn. No automatic retry."})
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, ProtocolError):
                raise
            if isinstance(exc, KiraError):
                raise ProtocolError(f"Structured question rejected: {exc}") from exc
            raise ProtocolError("Async question callback failed; inspect durable state") from exc

    async def _cancel_questions(self, tasks: list[asyncio.Task[Json]]) -> None:
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=1)
        await asyncio.gather(*done, return_exceptions=True)
        if pending:
            for task in pending:
                task.add_done_callback(lambda finished: None if finished.cancelled()
                                       else finished.exception())
            raise ProtocolError("Question callback cancellation was not confirmed within one second")

    async def _observe(self, rpc: AppServer, ctx: RunContext, thread_id: str, turn_id: str,
                       client_names: set[str] | None = None) -> Json:
        interrupted = False
        summaries = PublicSummaries(ctx, "codex")
        await summaries.status("requested")
        seen: set[tuple[str, str]] = set()
        streamed: set[str] = set()
        client_tools = ClientToolBridge(ctx, client_names or set(), thread_id, turn_id)
        final_text: str | None = None
        cancel_wait = asyncio.create_task(ctx.cancel.wait())
        next_event: asyncio.Task[Json] | None = None
        interrupt_deadline: float | None = None
        question_tasks: dict[str, asyncio.Task[Json]] = {}
        question_responses: dict[str, Json] = {}
        question_inputs: dict[str, bytes] = {}
        try:
            while True:
                if next_event is None:
                    next_event = asyncio.create_task(rpc.notifications.get())
                tasks: set[asyncio.Task[Any]] = {next_event, *question_tasks.values()}
                if not interrupted:
                    tasks.add(cancel_wait)
                timeout = None if interrupt_deadline is None else max(
                    0.0, interrupt_deadline - asyncio.get_running_loop().time())
                ready, _ = await asyncio.wait(tasks, timeout=timeout,
                                              return_when=asyncio.FIRST_COMPLETED)
                if not ready:
                    return {"state": "interrupted", "error": "Codex interruption was requested "
                            "but no terminal confirmation arrived within 15 seconds"}
                for key, task in tuple(question_tasks.items()):
                    if task in ready:
                        question_responses[key] = await task
                        del question_tasks[key]
                if cancel_wait in ready and not interrupted:
                    interrupted = True
                    interrupt_deadline = asyncio.get_running_loop().time() + 15
                    await rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id},
                                      timeout=5)
                    if not next_event.done():
                        next_event.cancel()
                        await asyncio.gather(next_event, return_exceptions=True)
                        next_event = None
                        continue
                if not next_event.done():
                    continue
                event = await next_event
                next_event = None
                method, params = event["method"], event.get("params", {})
                if method == "kira/disconnected":
                    raise ProtocolError(params["error"])
                if method == "item/tool/call" and "id" in event:
                    # Client mailbox callbacks are distinct from observed native tool items.
                    await rpc.respond(event["id"], await client_tools.call(params))
                    continue
                if params.get("threadId") not in (None, thread_id):
                    continue
                if params.get("turnId") not in (None, turn_id):
                    continue
                if method == "serverRequest/resolved":
                    key = encoded(params.get("requestId")).decode("utf-8")
                    pending_question = question_tasks.pop(key, None)
                    if pending_question:
                        await self._cancel_questions([pending_question])
                    continue
                if method == "item/tool/requestUserInput" and "id" in event:
                    if params.get("threadId") != thread_id or params.get("turnId") != turn_id:
                        raise ProtocolError("Structured question lacks exact active-run correlation")
                    if type(event["id"]) not in (int, str):
                        raise ProtocolError("Structured question has an invalid RPC ID")
                    key = encoded(event["id"]).decode("utf-8")
                    if len(key.encode("utf-8")) > 512:
                        raise ProtocolError("Structured question RPC ID exceeds its bound")
                    fingerprint = encoded(params)
                    if len(fingerprint) > MAX_STRUCTURED_TEXT_BYTES:
                        raise ProtocolError("Structured question exceeds 64 KiB")
                    if key in question_inputs:
                        if question_inputs[key] != fingerprint:
                            raise ProtocolError("Question RPC ID was reused with different input")
                        if key in question_responses:
                            await rpc.respond(event["id"], question_responses[key])
                        continue
                    if len(question_inputs) >= MAX_INPUT_REQUESTS:
                        raise ProtocolError("Structured question request bound reached")
                    question_inputs[key] = fingerprint
                    question_tasks[key] = asyncio.create_task(self._question(rpc, ctx, event))
                    continue
                if "id" in event:
                    await self._approval(rpc, ctx, event)
                    continue
                summary_item = params.get("item")
                if method in {"item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
                              "kira/reasoningSummaryLimit"} or (
                        method in {"item/started", "item/completed"}
                        and isinstance(summary_item, dict) and summary_item.get("type") == "reasoning"):
                    if params.get("threadId") == thread_id and params.get("turnId") == turn_id:
                        await summaries.codex_event(method, params)
                    continue
                if method in {"item/agentMessage/delta", "item/plan/delta"}:
                    item_id = params.get("itemId", "")
                    streamed.add(item_id)
                    await ctx.emit("message.delta", {"content": params["delta"], "item_id": item_id,
                        **({"source": "codex.plan"} if method == "item/plan/delta" else {})})
                elif method in {"item/started", "item/completed"}:
                    item = params.get("item", {})
                    item_key = (method, item.get("id", ""))
                    if method == "item/completed" and item.get("type") == "agentMessage" \
                            and item.get("questions") is not None:
                        if params.get("threadId") != thread_id or params.get("turnId") != turn_id \
                                or item.get("delivery") != "async":
                            raise ProtocolError("Async question lacks exact active-run correlation")
                        async_key = "async:" + str(item.get("id"))
                        fingerprint = encoded({"id": item.get("id"), "questions": item["questions"]})
                        if len(fingerprint) > MAX_STRUCTURED_TEXT_BYTES:
                            raise ProtocolError("Async question metadata exceeds 64 KiB")
                        if async_key in question_inputs:
                            if question_inputs[async_key] != fingerprint:
                                raise ProtocolError("Async question item ID was reused with different input")
                        else:
                            if len(question_inputs) >= MAX_INPUT_REQUESTS:
                                raise ProtocolError("Structured question request bound reached")
                            question_inputs[async_key] = fingerprint
                            question_tasks[async_key] = asyncio.create_task(
                                self._async_question(rpc, ctx, thread_id, turn_id, item))
                    if item_key in seen:
                        continue
                    seen.add(item_key)
                    await self._item(ctx, method, item, streamed)
                    if ctx.output_schema is not None and method == "item/completed" \
                            and item.get("type") in {"agentMessage", "plan"} \
                            and item.get("phase") in (None, "final_answer"):
                        text = item.get("text")
                        if not isinstance(text, str) or len(text.encode("utf-8")) > \
                                MAX_STRUCTURED_TEXT_BYTES:
                            raise ProtocolError("Codex structured final text exceeds 64 KiB or is invalid")
                        final_text = text
                elif method == "thread/tokenUsage/updated":
                    usage = params.get("tokenUsage", {})
                    last = usage.get("last", {})
                    payload = {"source": "codex", "measurement": "provider", "scope": "last_request",
                               "model_context_window": usage.get("modelContextWindow"),
                               "thread_total": usage.get("total", {})}
                    for old, new in [("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"),
                                     ("cachedInputTokens", "cached_input_tokens"),
                                     ("totalTokens", "total_tokens")]:
                        if old in last:
                            payload[new] = last[old]
                    await ctx.emit("usage", payload)
                elif method == "error":
                    await ctx.emit("backend.notice", {"message": params.get("error", {}).get(
                        "message", "Codex reported an error"),
                        "will_retry": params.get("willRetry", False)})
                elif method == "turn/completed":
                    terminal = params.get("turn", {})
                    if terminal.get("id") != turn_id:
                        continue
                    status = terminal.get("status")
                    if status == "completed":
                        return {"state": "completed", "outcome": {"thread_id": thread_id,
                                "turn_id": turn_id, "model": ctx.model,
                                "reasoning_effort": ctx.settings.get("reasoning_effort"),
                                **({"final_text": final_text} if ctx.output_schema is not None
                                   else {})}}
                    if status == "interrupted":
                        return {"state": "cancelled" if interrupted else "interrupted",
                                "outcome": {"provider_status": status,
                                            "interruption_confirmed": True}}
                    return {"state": "failed", "error": (terminal.get("error") or {}).get(
                        "message", f"Codex terminal status: {status}")}
        finally:
            await summaries.finish()
            cancel_wait.cancel()
            if next_event:
                next_event.cancel()
            await asyncio.gather(cancel_wait, *([next_event] if next_event else []),
                                 return_exceptions=True)
            await self._cancel_questions(list(question_tasks.values()))

    async def _item(self, ctx: RunContext, method: str, item: Json, streamed: set[str]) -> None:
        item_type, item_id = item.get("type"), str(item.get("id", ""))
        completed = method == "item/completed"
        if item_type in {"agentMessage", "plan"} and completed:
            # Proposed plans are public user-facing output, distinct from private
            # reasoning. The completed text is authoritative even when it differs
            # from concatenated item/plan/delta notifications.
            metadata = {"source": "codex.plan", "phase": "final_answer"} if item_type == "plan" else {}
            if item_id not in streamed and item.get("text"):
                await ctx.emit("message.delta", {"content": item["text"], "item_id": item_id,
                                                  **metadata})
            await ctx.emit("message.completed", {"content": item.get("text", ""), "item_id": item_id,
                                                  **metadata})
        elif item_type in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
                           "webSearch", "functionCallOutput", "imageView", "collabAgentToolCall"}:
            # MCP notifications are observations, including calls blocked before
            # they reach the Kira relay. Keep their result/error visible; the
            # service-bound callback independently owns any actual tool effect.
            if item_type == "dynamicToolCall" and ctx.client_tools:
                name = f"{item.get('namespace')}.{item.get('tool')}"
                if name in {"team.send_message", "team.read_messages"}:
                    # The service records actual mailbox effects with the durable call ID.
                    return
            name = f"codex.{item_type}"
            if completed:
                # Do not invoke ctx.execute_tool: execution already belongs to Codex.
                result = {**item, "source": "codex", "observed_only": True,
                          "ok": item.get("status") == "completed" and item.get("exitCode", 0) == 0}
                await ctx.emit("tool.completed", {"tool_id": item_id, "name": name, "result": result})
                if item_type == "fileChange" and item.get("status") == "completed":
                    for change in item.get("changes", []):
                        await self._artifact(ctx, change.get("path"), item_id)
            else:
                arguments = {k: v for k, v in item.items()
                             if k in {"command", "cwd", "changes", "arguments", "tool", "server", "query"}}
                await ctx.emit("tool.started", {"tool_id": item_id, "name": name,
                                                "arguments": arguments, "source": "codex"})

    async def _artifact(self, ctx: RunContext, value: Any, tool_id: str) -> None:
        if not isinstance(value, str):
            return
        path = Path(value)
        if not path.is_absolute():
            path = ctx.root / path
        try:
            path = path.resolve(strict=True)
            if ctx.permission != "full_access":
                path.relative_to(ctx.root.resolve())
            if not path.is_file():
                return
            def digest() -> str:
                with path.open("rb") as source:
                    return hashlib.file_digest(source, "sha256").hexdigest()
            sha = await asyncio.to_thread(digest)
            await ctx.emit("artifact.created", {"path": str(path), "kind": "file",
                                               "sha256": sha, "tool_id": tool_id, "source": "codex"})
        except (OSError, ValueError):
            return

    async def _approval(self, rpc: AppServer, ctx: RunContext, event: Json) -> None:
        method, params = event["method"], event.get("params", {})
        request_id = event["id"]
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if ctx.permission not in HOST_PERMISSIONS:
                # An approval can escape the sandbox, so it cannot enlarge project permissions.
                await ctx.emit("backend.notice", {"message": "Codex requested permission outside "
                    "the project policy; request declined", "method": method})
                await rpc.respond(request_id, {"decision": "decline"})
            else:
                pending = asyncio.ensure_future(ctx.approve({
                    "approval_id": str(request_id), "backend": "codex",
                    "method": method, "command": params.get("command"),
                    "reason": params.get("reason"), "cwd": params.get("cwd")}))
                cancellation = asyncio.create_task(ctx.cancel.wait())
                try:
                    waiting: set[asyncio.Future[Any]] = {pending, cancellation}
                    done, _ = await asyncio.wait(waiting,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    decision = "cancel" if cancellation in done else (
                        await pending).get("decision", "decline")
                finally:
                    pending.cancel()
                    cancellation.cancel()
                    await asyncio.gather(pending, cancellation, return_exceptions=True)
                # Never apply persistent exec/network policy amendments.
                await rpc.respond(request_id, {"decision": decision if decision in
                                  {"accept", "decline", "cancel"} else "decline"})
        elif method == "item/permissions/requestApproval":
            await rpc.respond(request_id, {"permissions": {}, "scope": "turn"})
        else:
            await rpc.send({"id": request_id, "error": {"code": -32601,
                            "message": "Kira does not implement this App Server client method"}})

    async def close(self) -> None:
        for rpc in tuple(self._active):
            await rpc.close()
        if self._control:
            await self._control.close()
            self._control = None
