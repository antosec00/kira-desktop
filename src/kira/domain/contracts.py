"""Single integration seam for service, providers, and GUI."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

IPC_VERSION = 1
SCHEMA_VERSION = 16
EVENT_SCHEMA_VERSION = 1
MAX_FRAME_BYTES = 1_048_576
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
RUN_STATES = frozenset({"queued", "running", "awaiting_input", "cancelling"}) | TERMINAL_STATES
PERMISSIONS = frozenset({"read_only", "project_write", "trusted_host", "full_access"})
WRITE_PERMISSIONS = frozenset({"project_write", "trusted_host", "full_access"})
HOST_PERMISSIONS = frozenset({"trusted_host", "full_access"})

Json = dict[str, Any]
Emit = Callable[[str, Json], Awaitable[None]]
ExecuteTool = Callable[[str, Json], Awaitable[Json]]
Approve = Callable[[Json], Awaitable[Json]]
ClientToolCall = Callable[[str, str, Json], Awaitable[Json]]
ProviderStarted = Callable[[int], Awaitable[None]]
RequestInput = Callable[[Json], Awaitable[Json]]
ReadInput = Callable[[str, bool], Awaitable[Json]]
RunControl = Callable[[str, Json], Awaitable[Json]]
RegisterControl = Callable[[RunControl | None], None]
RunGuidance = Callable[[str, Json], Awaitable[Json]]
ContextSave = Callable[[Json], Awaitable[Json]]
ContextPage = Callable[[Json], Awaitable[Json]]
ToolImage = Callable[[str], Awaitable[Json]]


@dataclass
class RunContext:
    run_id: str
    project: Json
    session: Json
    prompt: str
    supplied_context: str
    emit: Emit
    execute_tool: ExecuteTool
    approve: Approve
    cancel: asyncio.Event
    history: list[Json] = field(default_factory=list)
    image_inputs: dict[str, list[Json]] = field(default_factory=dict)
    client_tools: list[Json] = field(default_factory=list)
    client_tool_call: ClientToolCall | None = None
    output_schema: Json | None = None
    disable_native_agents: bool = False
    provider_started: ProviderStarted | None = None
    request_input: RequestInput | None = None
    read_input: ReadInput | None = None
    mode: str = "execute"
    register_control: RegisterControl | None = None
    guidance: RunGuidance | None = None
    assistant_instructions: str = ""
    context_state: Json | None = None
    context_save: ContextSave | None = None
    context_page: ContextPage | None = None
    context_catalog: Json | None = None
    tool_image: ToolImage | None = None

    @property
    def root(self) -> Path:
        return Path(self.project["root"])

    @property
    def model(self) -> str:
        return str(self.session["model"])

    @property
    def settings(self) -> Json:
        return dict(self.session.get("settings", {}))

    @property
    def permission(self) -> str:
        return str(self.project.get("permission", "read_only"))


class Backend(Protocol):
    async def discover(self) -> Json: ...
    async def run(self, ctx: RunContext) -> Json: ...
    async def close(self) -> None: ...


class KiraError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
