"""Service-owned browser admission, evidence, screenshots and per-chat preferences."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import stat
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import UUID

from kira.domain.contracts import MAX_FRAME_BYTES, Json, KiraError
from kira.domain.providers import WEB_BACKENDS as WEB_BACKENDS  # Re-exported for existing importers.
from kira.domain.computer import (
    COMPUTER_MUTATIONS, MAX_COMPUTER_OBSERVATIONS, computer_tool_specs,
    validate_computer_arguments,
)
from kira.domain.web import BROWSER_ACTIONS, INTERACTIONS, WEB_DEFAULTS, WEB_SETTINGS, browser_tool_specs, validate_web_settings
from kira.service.project_context import ProjectChats
from kira.service.processes import ProcessRegistry
from kira.service.storage import encode, new_id, now
from kira.service.tool_policy import kira_tools_enabled

if TYPE_CHECKING:
    from kira.service.server import Client, Service

METHODS = frozenset({"session.web.get", "session.web.update", "session.browser.open",
                     "session.browser.watch", "session.browser.unwatch", "session.browser.command", "session.browser.close"})
MAX_BROWSERS = 2
MAX_SCREENSHOTS = 16
MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
SCHEMAS = {item["name"]: item["inputSchema"] for item in browser_tool_specs()[0]["tools"]}
UI_ACTIONS = frozenset({"viewer", "ui_takeover", "ui_resume", "ui_navigate", "ui_back", "ui_forward",
                        "ui_reload", "ui_new_tab", "ui_select_tab", "ui_close_tab", "ui_mouse", "ui_key", "ui_text", "ui_copy"})
MAX_REQUESTS = 2048
MAX_STREAM_RECEIPTS = 128
MAX_INPUT_STREAMS = 32
MAX_COMPUTER_CALLS = 128


@dataclass
class InputStream:
    client: Any
    high_water: int = 0
    receipts: OrderedDict[int, Json] = field(default_factory=OrderedDict)


@dataclass
class BrowserActor:
    id: str
    session: Json
    generation: int
    engine: Any
    profile: Path
    persistent: bool
    state: Json = field(default_factory=dict)
    viewers: set[Any] = field(default_factory=set)
    seq: int = 0
    last_run_id: str | None = None
    closed: bool = False
    streams: dict[str, InputStream] = field(default_factory=dict)
    automation_ready: asyncio.Event = field(default_factory=asyncio.Event)
    uncertain_runs: set[str] = field(default_factory=set)
    waiting_runs: set[str] = field(default_factory=set)
    close_when_idle: bool = False

    def __post_init__(self) -> None:
        self.automation_ready.set()


def validate_arguments(action: str, arguments: Json) -> None:
    schema = SCHEMAS.get(action)
    if schema is None or not isinstance(arguments, dict):
        raise KiraError("INVALID_ARGUMENT", "Unsupported browser action or arguments")
    if set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
        raise KiraError("INVALID_ARGUMENT", "Unexpected or missing browser argument")
    for key, value in arguments.items():
        field = schema["properties"][key]
        if field["type"] == "string" and (not isinstance(value, str) or "\0" in value):
            raise KiraError("INVALID_ARGUMENT", "Browser argument requires text")
        if field["type"] == "integer" and type(value) is not int:
            raise KiraError("INVALID_ARGUMENT", "Browser argument requires an integer")
        if "enum" in field and value not in field["enum"]:
            raise KiraError("INVALID_ARGUMENT", "Unsupported browser argument value")
        if "maxLength" in field and len(value.encode("utf-8")) > field["maxLength"]:
            raise KiraError("INVALID_ARGUMENT", "Browser text exceeds its size limit")


def _worker_exited(engine: Any) -> bool:
    """Whether the browser engine was closed or its worker process has exited."""
    if getattr(engine, "_closed", False):
        return True
    process = getattr(engine, "_process", None)
    return process is not None and getattr(process, "returncode", None) is not None


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise KiraError("UNSAFE_PATH", "Browser storage must not be a symlink")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise KiraError("UNSAFE_PATH", "Browser storage must be a private owned directory")
    path.chmod(0o700)
    return path


class Web:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store
        self.sessions: dict[str, Any] = {}
        self.actors: dict[str, BrowserActor] = {}
        self.temporary: dict[str, Path] = {}
        self.previews: dict[str, Json] = {}
        self.images: dict[str, dict[str, Json]] = {}
        self.manual: set[str] = set()
        self.locks: dict[str, asyncio.Lock] = {}
        self.processes = ProcessRegistry(self.store, kind="browser")
        self.start_lock = asyncio.Lock()
        self.configuring: set[str] = set()
        self.closed_runs: set[str] = set()
        self.browser_captures: dict[str, int] = {}
        self.computer_cycles: dict[str, int] = {}
        self.computer_observations: dict[str, dict[str, str]] = {}
        self.computer_receipts: dict[str, dict[str, Json]] = {}

    @property
    def engine_path(self) -> Path:
        return self.service.paths.data / "browser-engine"

    @staticmethod
    def headed() -> bool:
        return True

    def _current(self, actor: BrowserActor) -> bool:
        session = self.store.one("sessions", actor.session["id"])
        return (not actor.closed and session["backend"] in WEB_BACKENDS
                and session["backend"] == actor.session["backend"]
                and session["model"] == actor.session["model"]
                and session["settings"].get("openai_account_id", "") == actor.session["settings"].get("openai_account_id", "")
                and self.service.provider_routing.current_generation(session["id"]) == actor.generation)

    def snapshot(self, session: Json) -> Json:
        from kira.tools.browser import engine_available

        actor = self.actors.get(session["id"])
        preview = self.previews.get(session["id"], {})
        state = actor.state if actor and self._current(actor) else {}
        return {"project_id": session["project_id"], "session_id": session["id"],
                "backend": session["backend"], "model": session["model"],
                "account_id": session["settings"].get("openai_account_id", ""),
                "provider_generation": self.service.provider_routing.current_generation(session["id"]),
                "settings": {key: session["settings"].get(key, value) for key, value in WEB_DEFAULTS.items()},
                "browser": {"available": bool(actor) or engine_available(self.engine_path), "active": bool(actor and self._current(actor)),
                            "headed": True, "mode": "embedded", "browser_id": actor.id if actor else "",
                            "generation": actor.generation if actor else self.service.provider_routing.current_generation(session["id"]),
                            "manual": False, "tabs": [], "tab_id": "", "loading": False,
                            "can_back": False, "can_forward": False,
                            "preview_url": preview.get("preview_url", ""),
                            "title": preview.get("title", ""), "url": preview.get("url", ""), **state,
                            **({"error": preview["error"]} if preview.get("error") else {})}}

    def _owned(self, params: Json) -> Json:
        for key in ("project_id", "session_id"):
            if not isinstance(params.get(key), str) or not 0 < len(params[key]) <= 128:
                raise KiraError("INVALID_ARGUMENT", "An owned project and chat are required")
        session = ProjectChats(self.store).owned_session(params["project_id"], params["session_id"])
        if session["backend"] not in WEB_BACKENDS:
            raise KiraError("UNSUPPORTED", "Kira web preferences require a supported browser provider")
        return session

    async def dispatch(self, method: str, params: Json, subscriber: Client | None = None) -> Json:
        if method.startswith("session.browser."):
            return await self.browser_dispatch(method, params, subscriber)
        allowed = {"project_id", "session_id"} | (
            WEB_SETTINGS | {"expected_generation"} if method == "session.web.update" else set())
        if set(params) - allowed:
            raise KiraError("INVALID_ARGUMENT", "Unexpected web preference parameter")
        session = self._owned(params)
        if method == "session.web.update":
            if "expected_generation" in params:
                expected = params["expected_generation"]
                if ("browser_action_approval" not in params or type(expected) is not int
                        or expected < 0):
                    raise KiraError("INVALID_ARGUMENT", "Browser policy requires a valid provider generation")
                if expected != self.service.provider_routing.current_generation(session["id"]):
                    raise KiraError("CONFLICT", "The chat provider changed; reload before changing browser consent")
            if self.service._closing or self.service.stopping.is_set() or self.service._settings_changing:
                raise KiraError("BUSY", "Service settings are changing")
            if ProjectChats(self.store).enrich(session)["archived"]:
                raise KiraError("ARCHIVED", "Restore this chat before changing its web preferences")
            if set(params) - {"project_id", "session_id", "expected_generation"} == {"browser_action_approval"}:
                return self.snapshot(self._set_action_policy(session, params["browser_action_approval"]))
            project = self.store.one("projects", session["project_id"])
            self.service.workbench._idle(project)
            if self.store.db.execute("SELECT 1 FROM prompt_queue WHERE project_id=? AND status IN "
                                     "('pending','starting','running')", (project["id"],)).fetchone():
                raise KiraError("BUSY", "Finish or remove queued requests before changing web preferences")
            settings = {**session["settings"], **{key: params[key] for key in WEB_SETTINGS if key in params}}
            validate_web_settings(settings)
            if any(settings.get(key) != session["settings"].get(key) for key in ("browser_enabled", "browser_persistent")):
                await self.before_configuration(session["project_id"], session["id"])
                self.service.workbench._idle(project)
                if self.store.one("sessions", session["id"]) != session:
                    raise KiraError("CONFLICT", "Chat settings changed; reload before retrying")
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                self.store.db.execute("UPDATE sessions SET settings=? WHERE id=?", (encode(settings), session["id"]))
                session = {**session, "settings": settings}
                self.service.provider_routing.refresh_generation(session)
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
        return self.snapshot(session)

    def _set_action_policy(self, session: Json, value: Any) -> Json:
        """A live, explicit desktop preference; never closes or resumes a browser."""
        validate_web_settings({"browser_action_approval": value})
        if (self.service._closing or self.service.stopping.is_set() or self.service._settings_changing
                or self.service.direct_accounts.changing or session["id"] in self.configuring):
            raise KiraError("BUSY", "Browser settings are changing")
        if (session["backend"] not in WEB_BACKENDS
                or self.store.db.execute("SELECT 1 FROM team_members WHERE session_id=?",
                                         (session["id"],)).fetchone()):
            raise KiraError("UNSUPPORTED", "Browser action policy requires an ordinary supported chat")
        if ProjectChats(self.store).enrich(session)["archived"]:
            raise KiraError("ARCHIVED", "Restore this chat before changing browser action policy")
        updated = {**session, "settings": {**session["settings"], "browser_action_approval": value}}
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            if self.store.one("sessions", session["id"]) != session:
                raise KiraError("CONFLICT", "Chat settings changed; reload before retrying")
            self.service.provider_routing.refresh_generation(session)
            self.store.db.execute("UPDATE sessions SET settings=? WHERE id=?",
                                  (encode(updated["settings"]), session["id"]))
            self.service.provider_routing.refresh_generation(updated)
            self.store.db.execute("COMMIT")
        except BaseException:
            self.store.db.execute("ROLLBACK")
            raise
        if value == "always":
            # Commit and resolve have no await boundary. Never grant unrelated
            # provider/command/manual-login approvals, including in another chat.
            for key in tuple(self.service.browser_action_approvals):
                try:
                    pending_session = self._pending_action_session(*key)
                except KiraError:
                    continue
                if pending_session["id"] == session["id"]:
                    self.service.approvals[key].set_result(
                        {"decision": "accept", "browser_action_approval": "always"})
        return updated

    def _pending_action_session(self, run_id: str, approval_id: str) -> Json:
        key = (run_id, approval_id)
        trusted = self.service.browser_action_approvals.get(key)
        future = self.service.approvals.get(key)
        if not trusted or future is None or future.done():
            raise KiraError("INVALID_ARGUMENT", "Always allow requires a pending Kira browser action")
        run = self.store.one("runs", run_id)
        session = self.store.one("sessions", run["session_id"])
        project = self.store.one("projects", session["project_id"])
        actor = self.actors.get(session["id"])
        request = trusted["request"]
        tool, arguments = request.get("tool"), request.get("arguments", {})
        valid_action = (isinstance(tool, str) and (
            tool in {"browser.click", "browser.type", "browser.press"}
            or tool == "computer.act" and isinstance(arguments, dict)
            and isinstance(arguments.get("action"), str)
            and arguments["action"] in COMPUTER_MUTATIONS))
        cancel = self.service.cancellations.get(run_id)
        if (request.get("kind") != "browser_action" or not valid_action
                or run["state"] not in {"running", "awaiting_input"} or cancel and cancel.is_set()
                or self.service._mode(run_id) != "execute" or project["permission"] == "read_only"
                or actor is None or not self._current(actor)
                or trusted.get("browser_id") != actor.id or trusted.get("generation") != actor.generation):
            raise KiraError("PERMISSION_DENIED", "The pending browser action is no longer eligible")
        return session

    def allow_always_from_approval(self, run_id: str, approval_id: str) -> None:
        session = self._pending_action_session(run_id, approval_id)
        self._set_action_policy(session, "always")

    async def _publish(self, actor: BrowserActor, event: str, payload: Json) -> None:
        if not self._current(actor):
            return
        actor.seq += 1
        message = {"version": 1, "browser_event": {"type": event, "project_id": actor.session["project_id"],
                   "session_id": actor.session["id"], "browser_id": actor.id, "generation": actor.generation,
                   "seq": actor.seq, "payload": payload}}
        if len(encode(message).encode("utf-8")) + 1 > MAX_FRAME_BYTES:
            return
        if event == "frame":
            for client in tuple(actor.viewers):
                client.send_browser_frame(actor.id, message)
        else:
            for client in tuple(self.service.clients):
                client.send(message)

    async def _event(self, actor: BrowserActor, message: Json) -> None:
        if not self._current(actor):
            return
        payload = message.get("data", {})
        if not isinstance(payload, dict):
            return
        if message.get("event") == "frame":
            if actor.viewers:
                await self._publish(actor, "frame", payload)
        elif message.get("event") == "state":
            allowed = {"tabs", "tab_id", "url", "title", "loading", "can_back", "can_forward", "manual",
                       "surface_id", "surface_mode", "surface_error", "surface_error_code", "auth_pending",
                       "auth_needs_manual", "auth_provider", "auth_operation", "auth_origin", "auth_result"}
            changes = {key: payload[key] for key in allowed if key in payload and actor.state.get(key) != payload[key]}
            if not changes:
                return
            actor.state.update(changes)
            if "manual" in changes:
                if actor.state["manual"]:
                    actor.automation_ready.clear()
                else:
                    actor.automation_ready.set()
                self.store.db.execute("UPDATE browser_sessions SET manual=?,updated_at=? WHERE id=?",
                                      (int(bool(actor.state.get("manual"))), now(), actor.id))
            await self._publish(actor, "state", self.snapshot(actor.session)["browser"])

    def _receipt(self, client: Client, scope: str, params: Json) -> tuple[str, Json | None]:
        request_id = params.get("request_id")
        try:
            if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise KiraError("INVALID_ARGUMENT", "Browser mutations require a request UUID") from None
        key = scope + ":" + request_id
        digest = hashlib.sha256(encode(params).encode()).hexdigest()
        previous = client.browser_requests.get(key)
        if previous:
            if previous["digest"] != digest:
                raise KiraError("CONFLICT", "Browser request identity was reused with different arguments")
            if previous.get("result") is None:
                raise KiraError("UNKNOWN_OUTCOME", "Browser action outcome is unknown; it will not be replayed")
            return key, dict(previous["result"])
        if len(client.browser_requests) >= MAX_REQUESTS:
            raise KiraError("OUTPUT_LIMIT", "Browser request limit reached; reconnect the view before continuing")
        # Keep only a fingerprint, never GUI typing, passwords or pixels.
        client.browser_requests[key] = {"digest": digest, "result": None}
        return key, None

    def _stream(self, actor: BrowserActor, client: Client) -> str:
        for identifier, stream in actor.streams.items():
            if stream.client is client:
                return identifier
        if len(actor.streams) >= MAX_INPUT_STREAMS:
            # Expired identities are never registered again. An old command
            # remains denied even once its disconnected tombstone is discarded.
            revoked = next((key for key, stream in actor.streams.items() if stream.client is None), None)
            if revoked is None:
                raise KiraError("BROWSER_BUSY", "Too many browser input clients")
            actor.streams.pop(revoked)
        identifier = new_id()
        actor.streams[identifier] = InputStream(client)
        return identifier

    def _input_receipt(self, actor: BrowserActor, client: Client, params: Json) -> tuple[Json, Json | None]:
        identifier, sequence = params.get("stream_id"), params.get("sequence")
        if not isinstance(identifier, str) or identifier not in actor.streams or actor.streams[identifier].client is not client:
            raise KiraError("STALE_INPUT", "This browser input stream is no longer registered to this desktop")
        if type(sequence) is not int or not 1 <= sequence <= 2**53 - 1:
            raise KiraError("INVALID_ARGUMENT", "Browser input sequence must be a positive integer")
        request_id = params.get("request_id")
        try:
            if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise KiraError("INVALID_ARGUMENT", "Browser mutations require a request UUID") from None
        stream = actor.streams[identifier]
        digest = hashlib.sha256(encode(params).encode()).hexdigest()
        if any(item.get("request_id") == request_id and observed != sequence
               for observed, item in stream.receipts.items()):
            raise KiraError("CONFLICT", "Browser request identity belongs to a different input sequence")
        if sequence <= stream.high_water:
            previous = stream.receipts.get(sequence)
            if previous is None:
                raise KiraError("STALE_INPUT", "This browser input was already observed; it will not be repeated")
            if previous["digest"] != digest:
                raise KiraError("CONFLICT", "Browser input sequence was reused with different arguments")
            if previous.get("result") is None:
                raise KiraError("UNKNOWN_OUTCOME", "Browser action outcome is unknown; it will not be replayed")
            return previous, dict(previous["result"])
        # Mark observed before the effect. Cancellation/connection loss leaves an
        # unknown receipt. Old sequences remain forbidden after bounded eviction.
        receipt: Json = {"digest": digest, "request_id": request_id, "result": None}
        stream.high_water = sequence
        stream.receipts[sequence] = receipt
        while len(stream.receipts) > MAX_STREAM_RECEIPTS:
            stream.receipts.popitem(last=False)
        return receipt, None

    async def browser_dispatch(self, method: str, params: Json, client: Client | None) -> Json:
        if client is None:
            raise KiraError("INVALID_REQUEST", "Embedded browser operations require a connected desktop client")
        operation = method.rsplit(".", 1)[1]
        allowed = {"project_id", "session_id"} | ({"request_id", "url"} if operation == "open" else {"browser_id"})
        if operation in {"command", "close"}:
            allowed.add("request_id")
        if operation == "command":
            allowed |= {"action", "arguments", "stream_id", "sequence"}
        if set(params) - allowed:
            raise KiraError("INVALID_ARGUMENT", "Unexpected embedded browser parameter")
        session = self._owned(params)
        if operation not in {"watch", "unwatch"} and ProjectChats(self.store).enrich(session)["archived"]:
            raise KiraError("ARCHIVED", "Restore this chat before using its browser")
        if self.service.direct_accounts.changing or session["id"] in self.configuring:
            raise KiraError("BUSY", "Browser account settings are changing")
        actor = self.actors.get(session["id"])
        receipt: str | None = None
        input_receipt: Json | None = None
        stream_id: str | None = None
        clipboard_text: str | None = None
        if operation == "open":
            if not session["settings"].get("browser_enabled", True):
                raise KiraError("PERMISSION_DENIED", "Enable the browser in this chat's web settings")
            if "url" in params:
                from kira.tools.browser import validate_browser_action
                try:
                    validate_browser_action("ui_navigate", {"url": params["url"]})
                except (ValueError, TypeError):
                    raise KiraError("INVALID_ARGUMENT", "Invalid browser address") from None
            receipt, previous = self._receipt(client, session["id"], params)
            if previous is not None:
                return previous
            await self._engine(session, None)
            actor = self.actors[session["id"]]
            response = await actor.engine.call("ui_takeover", {})
            await self._retire_failed_engine(actor, response)
            if not response.get("ok"):
                client.browser_requests[receipt]["result"] = response
                return dict(response)
            await self._event(actor, {"event": "state", "data": {"manual": True}})
            if "url" in params:
                stream_id = self._stream(actor, client)
                actor.viewers.add(client)
                actor.close_when_idle = False
                client.browser_ids.add(actor.id)
                viewing = await actor.engine.call("viewer", {"enabled": True})
                await self._retire_failed_engine(actor, viewing)
                if not viewing.get("ok"):
                    actor.viewers.discard(client)
                    client.browser_ids.discard(actor.id)
                    client.browser_requests[receipt]["result"] = viewing
                    return dict(viewing)
                response = await actor.engine.call("ui_navigate", {"url": params["url"]})
                await self._retire_failed_engine(actor, response)
                if not response.get("ok"):
                    client.browser_requests[receipt]["result"] = response
                    return dict(response)
        else:
            if operation == "close":
                browser_id = params.get("browser_id")
                if not isinstance(browser_id, str):
                    raise KiraError("INVALID_ARGUMENT", "A browser identity is required")
                receipt, previous = self._receipt(client, browser_id, params)
                if previous is not None:
                    return previous
            if not actor or params.get("browser_id") != actor.id or not self._current(actor):
                raise KiraError("STALE_BROWSER", "This browser no longer belongs to the current chat provider")
            if operation == "watch":
                stream_id = self._stream(actor, client)
                actor.viewers.add(client)
                actor.close_when_idle = False
                client.browser_ids.add(actor.id)
                response = await actor.engine.call("viewer", {"enabled": True})
                await self._retire_failed_engine(actor, response)
                if not response.get("ok"):
                    actor.viewers.discard(client)
                    client.browser_ids.discard(actor.id)
                    return dict(response)
            elif operation == "unwatch":
                await self._unwatch(actor, client)
            elif operation == "close":
                await self._close_actor(actor, user_closed=True)
            elif operation == "command":
                input_receipt, previous = self._input_receipt(actor, client, params)
                if previous is not None:
                    return previous
                action, arguments = params.get("action"), params.get("arguments")
                if not isinstance(action, str) or action not in UI_ACTIONS or not isinstance(arguments, dict):
                    raise KiraError("INVALID_ARGUMENT", "Unsupported embedded browser command")
                if client not in actor.viewers:
                    raise KiraError("PERMISSION_DENIED", "Watch this browser before interacting with it")
                if action not in {"viewer", "ui_takeover", "ui_resume"} and not actor.state.get("manual", False):
                    raise KiraError("BROWSER_AGENT_CONTROL", "Take control before interacting with the browser")
                from kira.tools.browser import validate_browser_action
                try:
                    validate_browser_action(action, arguments)
                except (ValueError, TypeError):
                    raise KiraError("INVALID_ARGUMENT", "Invalid embedded browser command arguments") from None
                if action == "viewer" and arguments.get("enabled") is False:
                    await self._unwatch(actor, client)
                    response = {"ok": True}
                else:
                    response = await actor.engine.call(action, arguments)
                if action == "ui_copy" and response.get("ok"):
                    copied = response.get("text")
                    if not isinstance(copied, str) or len(copied.encode("utf-8")) > 64 * 1024:
                        raise KiraError("OUTPUT_LIMIT", "Browser selection exceeds the clipboard limit")
                    clipboard_text = copied
                await self._retire_failed_engine(actor, response)
                if response.get("ok") and action in {"ui_takeover", "ui_resume"}:
                    await self._event(actor, {"event": "state", "data": {"manual": action == "ui_takeover"}})
                if not response.get("ok"):
                    input_receipt["result"] = response
                    return dict(response)
        result = self.snapshot(session)
        if stream_id:
            result["browser"]["input_stream_id"] = stream_id
        if receipt is not None:
            client.browser_requests[receipt]["result"] = result
        if input_receipt is not None:
            input_receipt["result"] = dict(result)
        # A copied selection is transient and delivered once to its requesting
        # desktop only. Replay receipts deliberately retain no clipboard text.
        if clipboard_text is not None:
            result["clipboard_text"] = clipboard_text
        return result

    async def _unwatch(self, actor: BrowserActor, client: Client) -> None:
        actor.viewers.discard(client)
        client.browser_ids.discard(actor.id)
        client.browser_frames.pop(actor.id, None)
        if not actor.viewers and not actor.closed:
            await actor.engine.call("viewer", {"enabled": False})

    async def disconnect(self, client: Client) -> None:
        last_client = len(self.service.clients) <= 1
        for actor in tuple(self.actors.values()):
            for stream in actor.streams.values():
                if stream.client is client:
                    stream.client = None
                    stream.receipts.clear()
            if client in actor.viewers or last_client:
                if client in actor.viewers:
                    try:
                        await self._unwatch(actor, client)
                    except (OSError, RuntimeError, KiraError):
                        pass
                if not actor.viewers:
                    actor.close_when_idle = True
                    if (actor.state.get("manual")
                            or not self._session_has_active_run(actor.session["id"])):
                        try:
                            await self._close_actor(actor, user_closed=True)
                        except KiraError:
                            pass
        client.browser_requests.clear()

    def _session_has_active_run(self, session_id: str) -> bool:
        return self.store.db.execute("SELECT 1 FROM runs WHERE session_id=? AND state NOT IN "
            "('completed','failed','cancelled','interrupted') LIMIT 1", (session_id,)).fetchone() is not None

    def attach(self, session: Json, project: Json, run_id: str, cancel: asyncio.Event,
               mode: str, options: Json) -> Json:
        tools_enabled = kira_tools_enabled(session)
        browser_only = session["backend"] == "ollama" and not tools_enabled
        if (session["backend"] not in WEB_BACKENDS
                or not session["settings"].get("browser_enabled", True)
                or not tools_enabled and not browser_only):
            return options
        if self.store.db.execute("SELECT 1 FROM team_members WHERE session_id=?", (session["id"],)).fetchone():
            return options
        options = dict(options)
        existing = options.get("client_tool_call")
        base_tools = ([namespace for namespace in options.get("client_tools", [])
                       if namespace.get("name") == "kira_history"]
                      if browser_only else options.get("client_tools", []))
        options["client_tools"] = [*base_tools, *browser_tool_specs(), *computer_tool_specs()]

        async def tool(call_id: str, name: str, arguments: Json) -> Json:
            if name.startswith("browser."):
                return await self.call(session, project, run_id, cancel, mode, call_id, name, arguments)
            if name.startswith("computer."):
                async with self.locks.setdefault(session["id"], asyncio.Lock()):
                    return await self._computer_call(session, project, run_id, cancel, mode, call_id, name, arguments)
            if existing is None:
                raise KiraError("UNSUPPORTED", "This client tool is not registered")
            return dict(await existing(call_id, name, arguments))

        async def image(image_id: str) -> Json:
            metadata = self.images.get(run_id, {}).get(image_id)
            if metadata is None:
                raise KiraError("INVALID_ATTACHMENT", "Screenshot does not belong to this active run")
            return dict((await asyncio.to_thread(self.service.image_inputs.images.read_parts, [metadata]))[0])

        options["client_tool_call"] = tool
        options["tool_image"] = image
        options["assistant_instructions"] = options.get("assistant_instructions", "") + (
            "\nBrowser tabs persist until closed; when asked to close, use browser.close and do not reopen it this turn. "
            "Click/type/press require Execute and honor the chat's browser consent policy. "
            "Manual login requires explicit user request via browser.manual_login; never request credentials in chat "
            "or bypass restrictions via host commands/files. Cite visited sources; browser auth is never exported.")
        if session["backend"] == "ollama":
            options["assistant_instructions"] += (
                "\nWith Ollama, use the registered browser__* and computer__* tool names. "
                "Call computer__observe before visual actions, then use its current observation_id "
                "for one computer__act action and inspect the returned image before continuing. "
                "Do not repeat an action when its result is uncertain. "
                "Use browser__search for public web search, then inspect pages with browser__navigate "
                "and browser__snapshot. Visual browser tools are offered only when "
                "the selected model declares image input support.")
        return options

    async def _engine(self, session: Json, run_id: str | None) -> Any:
        async with self.start_lock:
            if run_id is not None and run_id in self.closed_runs:
                raise KiraError("BROWSER_USER_CLOSED", "The user closed this turn's browser; continue without reopening it")
            if session["id"] in self.configuring or self.service.direct_accounts.changing:
                raise KiraError("BUSY", "Browser account settings are changing")
            current = self.store.one("sessions", session["id"])
            ProjectChats(self.store).owned_session(session["project_id"], session["id"])
            generation = self.service.provider_routing.current_generation(session["id"])
            if (current["backend"] not in WEB_BACKENDS or current["backend"] != session["backend"]
                    or current["model"] != session["model"]
                    or current["settings"].get("openai_account_id") != session["settings"].get("openai_account_id")
                    or session.get("provider_generation", generation) != generation):
                raise KiraError("STALE_BROWSER", "The browser request belongs to an older chat provider selection")
            actor = self.actors.get(session["id"])
            if actor is not None and not self._current(actor):
                await self._close_actor(actor)
                actor = None
            if actor is None:
                if self.store.db.execute("SELECT 1 FROM browser_sessions WHERE session_id=? AND state='unknown'", (session["id"],)).fetchone():
                    raise KiraError("BROWSER_CLEANUP", "Previous browser cleanup is not confirmed")
                count = self.store.db.execute("SELECT COUNT(*) FROM browser_sessions WHERE state!='stopped'").fetchone()[0]
                if count >= MAX_BROWSERS:
                    raise KiraError("BROWSER_BUSY", "Close an existing browser before opening another chat browser")
                from kira.tools.browser import BrowserSession, BrowserStartError, engine_available

                if not engine_available(self.engine_path):
                    raise KiraError("BROWSER_UNAVAILABLE", "Install Kira's browser with kira-desktop browser-install for the selected profile")
                persistent = session["settings"].get("browser_persistent", False)
                generation = self.service.provider_routing.current_generation(session["id"])
                browser_id = new_id()
                root = private_directory(self.service.paths.data / ("browser-profiles" if persistent else "browser-temporary"))
                profile = private_directory(self._retained_profile(root, session) if persistent else root / browser_id)
                artifacts = private_directory(private_directory(self.service.paths.artifacts / "browser") / (run_id or browser_id))
                async def event(message: Json) -> None:
                    assert actor is not None
                    await self._event(actor, message)
                engine = BrowserSession(profile, artifacts, headed=True, embedded=True, persistent=persistent,
                    started=lambda pid: self.processes.register(browser_id, pid), browser_path=self.engine_path, on_event=event)
                actor = BrowserActor(browser_id, dict(session), generation, engine, profile, persistent,
                                     last_run_id=run_id)
                self.store.db.execute("INSERT INTO browser_sessions "
                    "(id,session_id,project_id,generation,account_id,persistent,state,manual,last_run_id,error,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,'starting',0,?,NULL,?,?)",
                    (browser_id, session["id"], session["project_id"], generation,
                     session["settings"].get("openai_account_id", ""), int(persistent), run_id, now(), now()))
                self.actors[session["id"]] = actor
                self.sessions[browser_id] = engine
                if not persistent:
                    self.temporary[browser_id] = profile
                try:
                    await engine.start()
                except asyncio.CancelledError:
                    cleanup = asyncio.create_task(self._close_actor(actor))
                    await asyncio.shield(cleanup)
                    raise
                except (OSError, RuntimeError, KiraError) as exc:
                    await self._close_actor(actor)
                    if isinstance(exc, BrowserStartError):
                        # The worker's code and fixed public text, such as a sandbox restriction.
                        raise KiraError(exc.code, str(exc)) from exc
                    raise KiraError("BROWSER_UNAVAILABLE", "The isolated browser could not start; inspect engine installation and desktop sandbox support") from exc
                self.store.db.execute("UPDATE browser_sessions SET state='active',updated_at=? WHERE id=?", (now(), browser_id))
                await self._publish(actor, "state", self.snapshot(session)["browser"])
            if run_id is not None:
                actor.last_run_id = run_id
                self.store.db.execute("UPDATE browser_sessions SET last_run_id=?,updated_at=? WHERE id=?", (run_id, now(), actor.id))
            return actor.engine

    def _retained_profile(self, root: Path, session: Json) -> Path:
        """One retained profile per chat, provider and account, across provider generations.

        A profile kept under a key that also named a provider generation is
        adopted, most recent generation first, instead of starting empty. Other
        profile directories are never deleted.
        """
        def key(*identity: Any) -> str:
            return hashlib.sha256(encode([session["id"], session["backend"],
                                          session["settings"].get("openai_account_id"), *identity]).encode()).hexdigest()

        profile = root / key()
        if not profile.exists() and not profile.is_symlink():
            for row in self.store.all("SELECT generation FROM session_provider_generations WHERE session_id=? "
                                      "AND backend=? ORDER BY generation DESC", (session["id"], session["backend"])):
                legacy = root / key(row["generation"])
                if legacy.is_dir() and not legacy.is_symlink():
                    legacy.rename(profile)
                    break
        return profile

    def _check_actor(self, actor: BrowserActor, run_id: str, cancel: asyncio.Event) -> None:
        if cancel.is_set():
            raise KiraError("CANCELLED", "Browser request was cancelled")
        if run_id in self.closed_runs:
            raise KiraError("BROWSER_USER_CLOSED", "The user closed this turn's browser; continue without reopening it")
        if not self._current(actor) or self.actors.get(actor.session["id"]) is not actor:
            raise KiraError("BROWSER_CLOSED", "The browser was closed while the agent was waiting")

    async def _await_automation(self, actor: BrowserActor, run_id: str, cancel: asyncio.Event,
                                *, tool_id: str, name: str) -> None:
        waiting = False
        while True:
            self._check_actor(actor, run_id, cancel)
            if actor.automation_ready.is_set():
                if waiting:
                    await self.service.emit(run_id, "tool.progress", {
                        "tool_id": tool_id, "name": name, "phase": "resuming", "reason": "manual_control"})
                return
            if not waiting:
                # Record the observed ownership gate, never page or input data.
                # Waiting is not an approval, an executed action or a timeout.
                await self.service.emit(run_id, "tool.progress", {
                    "tool_id": tool_id, "name": name, "phase": "waiting_for_browser_control",
                    "reason": "manual_control"})
                waiting = True
            actor.waiting_runs.add(run_id)
            resumed = asyncio.create_task(actor.automation_ready.wait())
            cancelled = asyncio.create_task(cancel.wait())
            try:
                await asyncio.wait({resumed, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                actor.waiting_runs.discard(run_id)
                for task in (resumed, cancelled):
                    task.cancel()
                await asyncio.gather(resumed, cancelled, return_exceptions=True)

    async def _model_call(self, actor: BrowserActor, run_id: str, cancel: asyncio.Event,
                          action: str, arguments: Json, *, tool_id: str, name: str, manual: bool = False,
                          on_dispatch: Callable[[bool], None] | None = None,
                          approval_request: Json | None = None) -> Json:
        approved_once = False
        while True:
            if manual:
                self._check_actor(actor, run_id, cancel)
            else:
                await self._await_automation(actor, run_id, cancel, tool_id=tool_id, name=name)
            if approval_request is not None and not approved_once:
                current = self.store.one("sessions", actor.session["id"])
                if current["settings"].get("browser_action_approval", "ask") != "always":
                    decision = await self.service._approve(run_id, approval_request, cancel,
                        browser_action={"browser_id": actor.id, "generation": actor.generation})
                    if decision.get("decision") != "accept":
                        raise KiraError("PERMISSION_DENIED", "The browser interaction was not approved")
                    approved_once = decision.get("browser_action_approval") != "always"
                    # A decision can race cancellation or manual takeover.
                    # Persistent grants are read afresh at every admission;
                    # an explicit one-action decision remains valid only here.
                    continue
            dispatched = False

            def before_dispatch() -> Json | None:
                nonlocal dispatched
                # The real worker invokes this with its IPC lock held. UI
                # commands can queue ahead of us, so admission outside that lock
                # is not sufficient for revocation or cancellation.
                self._check_actor(actor, run_id, cancel)
                if not manual and not actor.automation_ready.is_set():
                    return {"ok": False, "error": {"code": "browser_paused"}}
                if approval_request is not None and not approved_once:
                    current = self.store.one("sessions", actor.session["id"])
                    if current["settings"].get("browser_action_approval", "ask") != "always":
                        return {"ok": False, "error": {"code": "browser_approval_changed"}}
                dispatched = True
                if on_dispatch is not None:
                    on_dispatch(True)
                return None

            try:
                guarded = getattr(actor.engine, "call_guarded", None)
                if guarded is not None:
                    result = dict(await guarded(action, arguments, before_dispatch))
                else:
                    rejected = before_dispatch()
                    result = rejected if rejected is not None else dict(await actor.engine.call(action, arguments))
            except asyncio.CancelledError:
                # The worker closes on an interrupted exchange. A cancellation
                # while merely waiting above never touched the manual page.
                if dispatched:
                    actor.uncertain_runs.add(run_id)
                raise
            except (OSError, RuntimeError):
                if dispatched:
                    actor.uncertain_runs.add(run_id)
                raise
            if result.get("error", {}).get("code") == "browser_approval_changed" and not dispatched:
                continue
            if not manual and result.get("error", {}).get("code") == "browser_paused":
                # Takeover can win the engine queue after admission. This exact
                # response guarantees no effect occurred; wait before dispatch.
                await self._event(actor, {"event": "state", "data": {"manual": True}})
                if on_dispatch is not None:
                    on_dispatch(False)
                continue
            return result

    async def call(self, session: Json, project: Json, run_id: str, cancel: asyncio.Event,
                   mode: str, call_id: str, name: str, arguments: Json) -> Json:
        async with self.locks.setdefault(session["id"], asyncio.Lock()):
            return await self._call(session, project, run_id, cancel, mode, call_id, name, arguments)

    async def _call(self, session: Json, project: Json, run_id: str, cancel: asyncio.Event,
                    mode: str, call_id: str, name: str, arguments: Json) -> Json:
        action = name.removeprefix("browser.")
        # Record the identity before any browser effect. Browser type text is
        # literal user/model content; passwords are rejected by the engine.
        await self.service.emit(run_id, "tool.started", {"tool_id": call_id, "name": name, "arguments": arguments})
        result: Json
        if action == "close":
            try:
                validate_arguments(action, arguments)
                if cancel.is_set():
                    raise KiraError("CANCELLED", "Browser request was cancelled")
                actor = self.actors.get(session["id"])
                if actor is None:
                    unknown = self.store.db.execute(
                        "SELECT 1 FROM browser_sessions WHERE session_id=? AND state='unknown' LIMIT 1",
                        (session["id"],)).fetchone()
                    if unknown:
                        raise KiraError("BROWSER_CLEANUP", "Browser cleanup is not confirmed")
                    result = {"ok": True, "closed": True, "already_closed": True}
                else:
                    if not self._current(actor):
                        raise KiraError("STALE_BROWSER", "The browser belongs to an older chat configuration")
                    await self._close_actor(actor, user_closed=True)
                    result = {"ok": True, "closed": True, "already_closed": False}
            except KiraError as exc:
                result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
            result = self.service.redactor.clean(result)
            await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name,
                                                                   "result": result})
            return result
        try:
            if action not in BROWSER_ACTIONS:
                raise KiraError("UNSUPPORTED", "Unsupported browser action")
            validate_arguments(action, arguments)
            if action == "search":
                action = "navigate"
                arguments = {"url": "https://www.google.com/search?q=" +
                             quote(arguments["query"].strip(), safe="")}
            if cancel.is_set():
                raise KiraError("CANCELLED", "Browser request was cancelled")
            if action in INTERACTIONS | {"manual_login"} and (mode != "execute" or project["permission"] == "read_only"):
                raise KiraError("PERMISSION_DENIED", "Browser interactions require execute mode and a writable project")
            if action == "manual_login":
                if arguments:
                    raise KiraError("INVALID_ARGUMENT", "Manual login has no parameters")
            if action == "screenshot" and self.browser_captures.get(run_id, 0) >= MAX_SCREENSHOTS:
                raise KiraError("OUTPUT_LIMIT", "The browser screenshot limit for this turn is reached; use text snapshots")
            await self._engine(session, run_id)
            actor = self.actors[session["id"]]
            if action != "manual_login":
                await self._await_automation(actor, run_id, cancel, tool_id=call_id, name=name)
            artifacts = private_directory(private_directory(self.service.paths.artifacts / "browser") / run_id)
            binding = await self._model_call(actor, run_id, cancel, "set_artifacts", {"path": str(artifacts)},
                                             tool_id=call_id, name=name, manual=action == "manual_login")
            if not binding.get("ok"):
                await self._retire_failed_engine(actor, binding)
                error = binding.get("error", {})
                raise KiraError(str(error.get("code", "BROWSER_STORAGE")), str(error.get("message", "Browser evidence location is unavailable")))
            prepared: Json | None = None
            approval_request: Json | None = None
            if action in INTERACTIONS:
                prepared = await self._model_call(actor, run_id, cancel, "prepare_interaction", {"action": action, "arguments": arguments},
                                                  tool_id=call_id, name=name)
                if not prepared.get("ok"):
                    error = prepared.get("error", {})
                    raise KiraError(str(error.get("code", "BROWSER_TARGET")), str(error.get("message", "Browser target is unavailable")))
                approval_request = {"kind": "browser_action", "tool": name,
                    "arguments": arguments, "target": prepared.get("target", {}), "url": prepared.get("url", ""),
                    "message": "Approve this browser interaction. It may submit information to the website."}
            if cancel.is_set():
                raise KiraError("CANCELLED", "Browser request was cancelled before execution")
            if action == "manual_login":
                paused = await self._model_call(actor, run_id, cancel, "manual_pause", {}, tool_id=call_id, name=name, manual=True)
                if not paused.get("ok"):
                    result = dict(paused)
                else:
                    self.manual.add(run_id)
                    await self._event(actor, {"event": "state", "data": {"manual": True}})
                    try:
                        approval = await self.service._approve(run_id, {"kind": "browser_manual_login",
                            "message": "Sign in manually in Kira's integrated browser panel. Do not put credentials in chat. Choose Allow once only after finishing; Reject cancels automated continuation."}, cancel)
                        self._check_actor(actor, run_id, cancel)
                        if approval.get("decision") != "accept" or cancel.is_set():
                            raise KiraError("PERMISSION_DENIED", "Manual browser login was not confirmed")
                        result = await self._model_call(actor, run_id, cancel, "resume", {}, tool_id=call_id, name=name, manual=True)
                        if result.get("ok"):
                            await self._event(actor, {"event": "state", "data": {"manual": False}})
                    finally:
                        self.manual.discard(run_id)
            else:
                result = (await self._model_call(actor, run_id, cancel, "perform_interaction", {"token": prepared["token"]},
                                                 tool_id=call_id, name=name, approval_request=approval_request)
                          if prepared else await self._model_call(actor, run_id, cancel, action, arguments,
                                                                  tool_id=call_id, name=name))
            if result.get("ok") and action == "screenshot":
                result = await self._screenshot(run_id, call_id, result)
                self.browser_captures[run_id] = self.browser_captures.get(run_id, 0) + 1
            await self._retire_failed_engine(actor, result)
            if result.get("ok"):
                preview = self.previews.setdefault(session["id"], {})
                preview.pop("error", None)
                if (action in {"navigate", "back", "click", "type", "press", "scroll", "select_tab", "close_tab", "manual_login"}
                        or result.get("url", preview.get("url")) != preview.get("url")):
                    preview.pop("preview_url", None)
                for key in ("title", "url"):
                    if isinstance(result.get(key), str):
                        preview[key] = result[key]
                if metadata := self.images.get(run_id, {}).get(result.get("browser_image_id", "")):
                    preview["preview_url"] = metadata["preview_url"]
            elif isinstance(result.get("error"), dict):
                self.previews.setdefault(session["id"], {})["error"] = str(result["error"].get("message", "Browser failed"))
        except KiraError as exc:
            result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except OSError:
            result = {"ok": False, "error": {"code": "BROWSER_STORAGE", "message": "Browser evidence could not be read or stored safely"}}
        except asyncio.CancelledError:
            await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name,
                "result": {"ok": False, "error": {"code": "INTERRUPTED", "message": "Browser action interrupted; effects may be uncertain. It will not be replayed."}}})
            raise
        result = self.service.redactor.clean(result)
        if not result.get("ok") and isinstance(result.get("error"), dict):
            self.previews.setdefault(session["id"], {})["error"] = str(result["error"].get("message", "Browser failed"))
        await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name, "result": result})
        return result

    def _computer_receipt(self, run_id: str, call_id: str, name: str, arguments: Json) -> tuple[Json, Json | None]:
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 256:
            raise KiraError("INVALID_ARGUMENT", "Computer calls require a bounded provider call identity")
        try:
            digest = hashlib.sha256(json.dumps([name, arguments], sort_keys=True,
                ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        except (TypeError, ValueError, UnicodeError):
            raise KiraError("INVALID_ARGUMENT", "Computer arguments must be valid JSON") from None
        receipts = self.computer_receipts.setdefault(run_id, {})
        if call_id in receipts:
            previous = receipts[call_id]
            if previous["digest"] != digest:
                raise KiraError("CONFLICT", "Computer call identity was reused with different arguments")
            if previous.get("result") is None:
                raise KiraError("UNKNOWN_OUTCOME", "Computer action outcome is unknown; the action will not be repeated")
            return previous, dict(previous["result"])
        if len(receipts) >= MAX_COMPUTER_CALLS:
            raise KiraError("OUTPUT_LIMIT", "The computer call identity limit for this turn is reached")
        receipt: Json = {"digest": digest, "result": None}
        receipts[call_id] = receipt
        return receipt, None

    async def _computer_capture(self, actor: BrowserActor, run_id: str, call_id: str, result: Json) -> Json:
        observation_id, viewport = result.get("observation_id"), result.get("viewport")
        if (not isinstance(observation_id, str) or not 1 <= len(observation_id) <= 128 or "\0" in observation_id
                or not isinstance(result.get("tab_id"), str) or not 1 <= len(result["tab_id"]) <= 128
                or not isinstance(viewport, dict) or set(viewport) != {"width", "height"}
                or type(viewport["width"]) is not int or not 1 <= viewport["width"] <= 1600
                or type(viewport["height"]) is not int or not 1 <= viewport["height"] <= 1200):
            raise KiraError("INVALID_OBSERVATION", "Computer returned an invalid viewport observation")
        if observation_id in self.computer_observations.get(run_id, {}):
            raise KiraError("INVALID_OBSERVATION", "Computer reused a previous observation identity")
        result = await self._screenshot(run_id, call_id, result, viewport=viewport)
        self.computer_observations.setdefault(run_id, {})[observation_id] = actor.id
        return result

    async def _computer_call(self, session: Json, project: Json, run_id: str, cancel: asyncio.Event,
                             mode: str, call_id: str, name: str, arguments: Json) -> Json:
        try:
            receipt, replay = self._computer_receipt(run_id, call_id, name, arguments)
        except KiraError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        if replay is not None:
            return replay
        await self.service.emit(run_id, "tool.started", {"tool_id": call_id, "name": name, "arguments": arguments})
        operation = name.removeprefix("computer.")
        result: Json
        action_dispatched = False
        action_executed = False
        outcome_known = True

        def dispatched(started: bool) -> None:
            nonlocal action_dispatched, outcome_known
            action_dispatched = started
            outcome_known = not started

        try:
            try:
                validate_computer_arguments(operation, arguments)
            except (ValueError, TypeError, UnicodeError):
                raise KiraError("INVALID_ARGUMENT", "Invalid computer operation or arguments") from None
            if cancel.is_set():
                raise KiraError("CANCELLED", "Computer request was cancelled")
            if run_id in self.closed_runs:
                raise KiraError("BROWSER_USER_CLOSED", "The user closed this turn's browser; continue without reopening it")
            if operation == "act" and arguments["action"] in COMPUTER_MUTATIONS and (
                    mode != "execute" or project["permission"] == "read_only"):
                raise KiraError("PERMISSION_DENIED", "Computer interactions require execute mode and a writable project")
            if operation == "act":
                existing = self.actors.get(session["id"])
                observation_owner = self.computer_observations.get(run_id, {}).get(arguments["observation_id"])
                if existing is None or not self._current(existing) or observation_owner != existing.id:
                    raise KiraError("INVALID_OBSERVATION", "Observe this run's current browser before acting on its pixels")
            if self.computer_cycles.get(run_id, 0) >= MAX_COMPUTER_OBSERVATIONS:
                raise KiraError("OUTPUT_LIMIT", "The 32 computer observation cycles for this turn are exhausted; use browser text tools")
            await self._engine(session, run_id)
            actor = self.actors[session["id"]]
            await self._await_automation(actor, run_id, cancel, tool_id=call_id, name=name)
            # Reserve the result image before preparation, approval or effects.
            # Failed and declined cycles retain this reservation to bound work.
            self.computer_cycles[run_id] = self.computer_cycles.get(run_id, 0) + 1
            artifacts = private_directory(private_directory(self.service.paths.artifacts / "browser") / run_id)
            binding = await self._model_call(actor, run_id, cancel, "set_artifacts", {"path": str(artifacts)},
                                             tool_id=call_id, name=name)
            if not binding.get("ok"):
                result = binding
            elif operation == "observe":
                result = await self._model_call(actor, run_id, cancel, "computer_observe", {}, tool_id=call_id, name=name)
            else:
                prepared = await self._model_call(actor, run_id, cancel, "computer_prepare", arguments, tool_id=call_id, name=name)
                if not prepared.get("ok"):
                    result = prepared
                else:
                    token = prepared.get("token")
                    if not isinstance(token, str) or not 1 <= len(token) <= 256:
                        raise KiraError("INVALID_OBSERVATION", "Computer returned no valid action preparation")
                    approval_request = None
                    if arguments["action"] in COMPUTER_MUTATIONS:
                        approval_request = {"kind": "browser_action", "tool": name,
                            "arguments": arguments, "target": prepared.get("target", {}), "url": prepared.get("url", ""),
                            "message": "Approve this visual browser interaction. It may submit information to the website."}
                    await self._await_automation(actor, run_id, cancel, tool_id=call_id, name=name)
                    result = await self._model_call(actor, run_id, cancel, "computer_perform", {"token": token},
                                                    tool_id=call_id, name=name, on_dispatch=dispatched,
                                                    approval_request=approval_request)
                    if type(result.get("action_executed")) is bool:
                        action_executed = result["action_executed"]
                        outcome_known = True
                    elif result.get("ok"):
                        raise KiraError("INVALID_OBSERVATION", "Computer did not confirm whether its action executed")
            await self._retire_failed_engine(actor, result)
            if result.get("ok"):
                result = await self._computer_capture(actor, run_id, call_id, result)
                preview = self.previews.setdefault(session["id"], {})
                preview.pop("error", None)
                for key in ("url", "title"):
                    if isinstance(result.get(key), str):
                        preview[key] = result[key]
                preview["preview_url"] = result["screenshot"]["preview_url"]
        except KiraError as exc:
            result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except (OSError, RuntimeError):
            result = {"ok": False, "error": {"code": "COMPUTER_STORAGE", "message": "Computer evidence could not be read or stored safely"}}
        except asyncio.CancelledError:
            result = {"ok": False, "error": {"code": "INTERRUPTED", "message":
                "Computer request was interrupted. It will not be repeated automatically."}}
            if operation == "act":
                if outcome_known:
                    result["action_executed"] = action_executed
                else:
                    result.update(action_outcome="unknown", retry_safe=False)
            receipt["result"] = result
            await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name, "result": result})
            raise
        if operation == "act":
            if action_executed:
                result["action_executed"] = True
            elif action_dispatched and not outcome_known:
                result.update(action_outcome="unknown", retry_safe=False)
            else:
                result["action_executed"] = False
        if not result.get("ok") and isinstance(result.get("error"), dict):
            preview = self.previews.setdefault(session["id"], {})
            preview["error"] = str(result["error"].get("message", "Computer operation failed"))
            if action_executed or not outcome_known:
                preview.pop("preview_url", None)
        result = self.service.redactor.clean(result)
        receipt["result"] = result
        await self.service.emit(run_id, "tool.completed", {"tool_id": call_id, "name": name, "result": result})
        return result

    async def _screenshot(self, run_id: str, call_id: str, result: Json, *, viewport: Json | None = None) -> Json:
        screenshot = result.get("screenshot")
        if not isinstance(screenshot, dict) or not isinstance(screenshot.get("path"), str):
            raise KiraError("INVALID_ATTACHMENT", "Browser returned no verified screenshot")
        path = Path(screenshot["path"])
        root = self.service.paths.artifacts / "browser" / run_id
        if path.parent != root or path.is_symlink() or path.suffix != ".png":
            raise KiraError("INVALID_ATTACHMENT", "Browser screenshot path is outside this run")
        if path.stat().st_size > MAX_SCREENSHOT_BYTES:
            raise KiraError("OUTPUT_LIMIT", "Browser screenshot exceeds 2 MiB; inspect a smaller viewport")
        metadata = await asyncio.to_thread(self.service.image_inputs.images.import_file, str(path))
        if viewport is not None and (metadata["width"], metadata["height"]) != (viewport["width"], viewport["height"]):
            raise KiraError("INVALID_OBSERVATION", "Computer screenshot dimensions differ from its coordinate viewport")
        if metadata["bytes"] > MAX_SCREENSHOT_BYTES:
            raise KiraError("OUTPUT_LIMIT", "Normalized browser screenshot exceeds 2 MiB")
        self.service.image_inputs.save(metadata)
        self.images.setdefault(run_id, {})[metadata["id"]] = metadata
        await self.service.emit(run_id, "artifact.created", {"kind": "browser_screenshot", "tool_id": call_id,
            "path": str(self.service.image_inputs.images.root / (metadata["id"] + ".png")),
            "sha256": metadata["sha256"], "attachment_id": metadata["id"]})
        return {**result, "browser_image_id": metadata["id"], "screenshot": metadata}

    async def _close_actor(self, actor: BrowserActor, *, user_closed: bool = False) -> None:
        if actor.closed:
            return
        if user_closed:
            self.closed_runs.update(row["id"] for row in self.store.all(
                "SELECT id FROM runs WHERE session_id=? AND state NOT IN ('completed','failed','cancelled','interrupted')",
                (actor.session["id"],)))
        actor.automation_ready.set()
        actor.closed = True
        # Closing the view is an explicit rejection of its outstanding browser
        # approval. Other tools' approvals in this run remain untouched.
        if actor.last_run_id is not None:
            for (run_id, approval_id), future in tuple(self.service.approvals.items()):
                if run_id != actor.last_run_id or future.done():
                    continue
                browser_approval = self.store.db.execute(
                    "SELECT 1 FROM events WHERE run_id=? AND type='approval.requested' "
                    "AND json_extract(payload,'$.approval_id')=? "
                    "AND json_extract(payload,'$.kind') IN ('browser_action','browser_manual_login') LIMIT 1",
                    (run_id, approval_id)).fetchone()
                if browser_approval:
                    future.set_result({"decision": "decline"})
        closed = True
        try:
            await actor.engine.close()
        except (OSError, RuntimeError, KiraError):
            closed = False
        registered = self.store.db.execute("SELECT 1 FROM browser_processes WHERE run_id=?", (actor.id,)).fetchone()
        cleaned = await self.processes.cleanup(actor.id) and (closed or registered is not None)
        self.store.db.execute("UPDATE browser_sessions SET state=?,manual=0,error=?,updated_at=? WHERE id=?",
            ("stopped" if cleaned else "unknown", None if cleaned else "Browser cleanup identity unavailable", now(), actor.id))
        for client in tuple(actor.viewers):
            client.browser_ids.discard(actor.id)
            client.browser_frames.pop(actor.id, None)
        actor.viewers.clear()
        # The chat may already have a newer browser, opened while this one closed.
        if self.actors.get(actor.session["id"]) is actor:
            del self.actors[actor.session["id"]]
        self.sessions.pop(actor.id, None)
        actor.seq += 1
        message = {"version": 1, "browser_event": {"type": "state", "project_id": actor.session["project_id"],
                   "session_id": actor.session["id"], "browser_id": actor.id, "generation": actor.generation,
                   "seq": actor.seq, "payload": {**self.snapshot(actor.session)["browser"], "browser_id": actor.id, "active": False}}}
        for client in tuple(self.service.clients):
            client.send(message)
        if cleaned:
            self._remove_temporary(actor.id)
        else:
            raise KiraError("BROWSER_CLEANUP", "Browser cleanup is not confirmed; its reservation is retained")

    async def _retire_failed_engine(self, actor: BrowserActor, result: Json) -> None:
        """Close a browser that is gone; a failed or timed-out action alone keeps it open."""
        error = result.get("error")
        if not isinstance(error, dict) or result.get("ok"):
            return
        if error.get("code") in {"browser_closed", "browser_unavailable"} or _worker_exited(actor.engine):
            self.previews.setdefault(actor.session["id"], {})["error"] = str(error.get("message", "Browser stopped"))
            await self._close_actor(actor)

    def _remove_temporary(self, browser_id: str) -> None:
        path = self.temporary.pop(browser_id, self.service.paths.data / "browser-temporary" / browser_id)
        if path.is_dir() and not path.is_symlink() and path.parent.resolve() == path.parent:
            try:
                shutil.rmtree(path)
            except OSError:
                pass

    async def before_configuration(self, project_id: str, session_id: str) -> None:
        ProjectChats(self.store).owned_session(project_id, session_id)
        actor = self.actors.get(session_id)
        if actor and actor.state.get("manual"):
            raise KiraError("BUSY", "Return browser control to the agent or close it before changing chat settings")
        self.configuring.add(session_id)
        try:
            async with self.start_lock:
                actor = self.actors.get(session_id)
                if actor and actor.state.get("manual"):
                    raise KiraError("BUSY", "Close the manually controlled browser before changing chat settings")
                if actor:
                    await self._close_actor(actor)
            if self.store.db.execute("SELECT 1 FROM browser_sessions WHERE session_id=? AND state!='stopped'", (session_id,)).fetchone():
                raise KiraError("BROWSER_CLEANUP", "Browser cleanup must finish before changing chat settings")
            self.previews.pop(session_id, None)
        finally:
            self.configuring.discard(session_id)

    async def close_account(self, account_id: str | None) -> None:
        selected = [actor for actor in self.actors.values() if account_id is None
                    or actor.session["settings"].get("openai_account_id") == account_id]
        if any(actor.state.get("manual") for actor in selected):
            raise KiraError("BUSY", "Close manually controlled browsers before changing accounts")
        for actor in selected:
            await self._close_actor(actor)
        query = "SELECT 1 FROM browser_sessions WHERE state!='stopped'"
        args: tuple[str, ...] = ()
        if account_id is not None:
            query += " AND account_id=?"
            args = (account_id,)
        if self.store.db.execute(query, args).fetchone():
            raise KiraError("BROWSER_CLEANUP", "Browser cleanup must finish before changing its account")

    def shutdown_busy(self) -> bool:
        """A browser under manual control is live user work. An unconfirmed
        cleanup record is durable and checked again at the next start."""
        return any(actor.state.get("manual") for actor in self.actors.values())

    async def close_run(self, run_id: str) -> None:
        # Keep a chat browser across successful turns while its desktop is
        # connected. If the last desktop disconnected, close it once the run ends.
        state = self.store.one("runs", run_id)["state"]
        for actor in tuple(self.actors.values()):
            if actor.close_when_idle and not actor.viewers \
                    and not self._session_has_active_run(actor.session["id"]):
                try:
                    await self._close_actor(actor, user_closed=True)
                except KiraError:
                    pass
                continue
            if state != "completed":
                if actor.last_run_id == run_id:
                    if actor.state.get("manual") and run_id not in actor.uncertain_runs:
                        # User-owned manual work has no dependency on a model
                        # turn waiting/cancelling without an engine exchange.
                        continue
                    await self._close_actor(actor)

    def finalized(self, run_id: str) -> None:
        self.manual.discard(run_id)
        self.images.pop(run_id, None)
        self.closed_runs.discard(run_id)
        self.browser_captures.pop(run_id, None)
        self.computer_cycles.pop(run_id, None)
        self.computer_observations.pop(run_id, None)
        self.computer_receipts.pop(run_id, None)

    async def ensure_run_cleanup(self, run_id: str) -> bool:
        rows = self.store.all("SELECT id FROM browser_sessions WHERE last_run_id=? AND state='unknown'", (run_id,))
        for row in rows:
            registered = self.store.db.execute("SELECT 1 FROM browser_processes WHERE run_id=?", (row["id"],)).fetchone()
            if registered is None or not await self.processes.cleanup(row["id"]):
                return False
            self.store.db.execute("UPDATE browser_sessions SET state='stopped',error=NULL,updated_at=? WHERE id=?", (now(), row["id"]))
            self._remove_temporary(row["id"])
        return True

    async def recover(self, *, rebooted: bool = False) -> None:
        await self.processes.recover()
        for row in self.store.all("SELECT id FROM browser_sessions WHERE state!='stopped'"):
            registered = self.store.db.execute("SELECT 1 FROM browser_processes WHERE run_id=?", (row["id"],)).fetchone()
            # A worker that never registered its process can only be ruled out
            # by a reboot since the previous service start.
            cleaned = await self.processes.cleanup(row["id"]) if registered is not None else rebooted
            self.store.db.execute("UPDATE browser_sessions SET state=?,manual=0,error=?,updated_at=? WHERE id=?",
                ("stopped" if cleaned else "unknown", None if cleaned else "Browser cleanup identity unavailable", now(), row["id"]))
            if cleaned:
                self._remove_temporary(row["id"])
        # Remove only verified ownership paths, never discover arbitrary profiles.
        for row in self.store.all("SELECT id FROM browser_sessions WHERE state='stopped' AND persistent=0"):
            self._remove_temporary(row["id"])

    async def close(self) -> None:
        for actor in tuple(self.actors.values()):
            try:
                await self._close_actor(actor)
            except KiraError:
                pass
        await self.processes.close()
