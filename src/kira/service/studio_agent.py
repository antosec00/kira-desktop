"""Single opt-in Kira Studio gateway shared by Codex (MCP), OpenAI direct, Ollama and DeepSeek Account.

No provider is allowed to enable Studio or grant itself editing authority.
Mutations use run-scoped durable UUIDs and append-only, attributed revisions.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from kira.domain.contracts import WRITE_PERMISSIONS, Json, KiraError
from kira.domain import studio as studio_domain
from kira.domain.studio import (
    STUDIO_TOOL_NAMES, studio_action_contract, studio_tool_specs, validate_studio_action,
)
from kira.service.tool_policy import kira_tools_enabled

if TYPE_CHECKING:
    from kira.service.studio import Studio


def _failure(exc: Exception) -> Json:
    return {"ok": False, "error": {
        "code": exc.code if isinstance(exc, KiraError) else "STUDIO_UNAVAILABLE",
        "message": str(exc) if isinstance(exc, KiraError) else "Studio operation failed",
    }}


def attach_studio(studio: Studio, session: Json, project: Json, run_id: str,
                  cancel: asyncio.Event, mode: str, options: Json) -> Json:
    """Enable only for an allowed ordinary chat, on a new executable model turn."""
    if not studio_domain.STUDIO_AVAILABLE:
        return options
    if (session["backend"] not in {"codex", "openai_chatgpt", "ollama", "deepseek_account"}
            or not kira_tools_enabled(session) or mode != "execute"
            or project["permission"] not in WRITE_PERMISSIONS
            or studio.service._closing or studio.service.stopping.is_set()):
        return options
    if studio.store.db.execute(
            "SELECT 1 FROM team_members WHERE session_id=?", (session["id"],)).fetchone():
        return options
    try:
        studio._session(project["id"], session["id"])
    except KiraError:
        return options
    if (not studio._world(project["id"])["enabled"]
            or not studio.grant_state(project["id"], session["id"])["agent_enabled"]):
        return options

    result = dict(options)
    namespaces = [{**namespace, "tools": list(namespace["tools"])}
                  for namespace in result.get("client_tools", [])]
    kira = next((row for row in namespaces if row.get("name") == "kira"), None)
    if kira is None:
        kira = {"type": "namespace", "name": "kira",
                "description": "User-authorized project tools.", "tools": []}
        namespaces.append(kira)
    existing = {tool.get("name") for tool in kira["tools"]}
    kira["tools"].extend(tool for tool in studio_tool_specs() if tool["name"] not in existing)
    previous = result.get("client_tool_call")
    generation = studio.service.provider_routing.current_generation(session["id"])
    grant_generation = studio.grant_state(project["id"], session["id"])["grant_generation"]
    # Strict per-turn budgets; exported images are durable profile files.
    limits = {"edits": 32, "renders": 8, "exports": 4}
    consumed = {"edits": 0, "renders": 0, "exports": 0}

    def active() -> None:
        """Must run immediately before each effect, not just on tool registration."""
        current_project = studio.store.one("projects", project["id"])
        current_session = studio._session(project["id"], session["id"])
        run = studio.store.one("runs", run_id)
        current_grant = studio.grant_state(project["id"], session["id"])
        if (studio.service._closing or studio.service.stopping.is_set()
                or cancel.is_set() or run_id not in studio.service.tasks
                or run["session_id"] != session["id"]
                or run["state"] not in {"running", "awaiting_input"}
                or current_project["permission"] not in WRITE_PERMISSIONS
                or not studio._world(project["id"])["enabled"]
                or not current_grant["agent_enabled"]
                or current_grant["grant_generation"] != grant_generation
                or current_session["backend"] != session["backend"]
                or current_session["model"] != session["model"]
                or not kira_tools_enabled(current_session)
                or current_session["settings"].get("openai_account_id") !=
                    session["settings"].get("openai_account_id")
                or studio.service.provider_routing.current_generation(session["id"])
                    != generation):
            raise KiraError("PERMISSION_DENIED", "Studio authority was revoked or the run changed")

    async def call(call_id: str, name: str, arguments: Json) -> Json:
        if name not in STUDIO_TOOL_NAMES:
            if previous is None:
                raise KiraError("UNSUPPORTED", "Unregistered Kira Studio tool")
            return dict(await previous(call_id, name, arguments))
        try:
            active()
            if not isinstance(arguments, dict) or set(arguments) != {"action", "payload"}:
                raise KiraError("INVALID_ARGUMENT", "Studio requires action and payload")
            action, payload = validate_studio_action(arguments["action"], arguments["payload"])
        except KiraError as exc:
            return _failure(exc)

        await studio.service.emit(run_id, "tool.started", {
            "tool_id": call_id, "name": "kira.studio", "arguments": arguments})
        try:
            active()
            budget = ("edits" if action in {"poster", "patch", "restore"} else
                      "renders" if action == "render" else
                      "exports" if action == "export" else None)
            if budget is not None:
                if consumed[budget] >= limits[budget]:
                    raise KiraError("LIMIT_REACHED", "Studio run budget exhausted")
                consumed[budget] += 1
            if action == "describe":
                response: Json = {"ok": True, "contract": studio_action_contract()}
            elif action == "inspect":
                response = {"ok": True, **studio._world(project["id"])}
            elif action == "history":
                response = {"ok": True, "history": studio._world(project["id"])["history"]}
            elif action in {"render", "export"}:
                result_png = await studio._render(project["id"], export=action == "export",
                                                  guard=active)
                active()
                response = {"ok": True, "revision": result_png["revision"],
                            "png_ready": True,
                            "path": result_png["path"] if action == "export" else "",
                            "visual_inspection": "not_performed"}
            else:
                # A model cannot choose a project ID, chat, grant or request ID.
                # The run and its model call ID produce one immutable operation ID.
                request_id = str(uuid.uuid5(uuid.UUID(run_id), "studio:" + call_id))
                method = "studio." + action
                values = dict(payload)
                active()
                state = studio._mutate(project["id"], request_id, method, values,
                                       agent={"session_id": session["id"],
                                              "run_id": run_id, "call_id": call_id})
                response = {"ok": True, "revision": state["revision"],
                            "scene": state["scene"],
                            "history": state["history"],
                            "source": "agent_edit"}
        except (KiraError, OSError, ValueError, TypeError, TimeoutError) as exc:
            response = _failure(exc)
        response = studio.service.redactor.clean(response)
        await studio.service.emit(run_id, "tool.completed", {
            "tool_id": call_id, "name": "kira.studio", "result": response})
        return response

    result["client_tools"] = namespaces
    result["client_tool_call"] = call
    result["assistant_instructions"] = result.get("assistant_instructions", "") + (
        "\nKira Studio is explicitly activated and visual editing has been granted "
        "for this chat and current executable turn. When the user asks for a poster, "
        "diagram-like poster, a text-on-background scene or a change to the Studio "
        "canvas, use kira.studio: first describe/inspect, then poster or patch with "
        "the exact expected revision, then inspect or render. Keep all requested "
        "text exactly spelled and edit individual objects instead of replacing "
        "unrelated layers. No source code or shell required. The current Studio "
        "engine only supports a small 2D poster; do not claim semantic image editing, "
        "photorealistic generation, 3D or security simulation capabilities. A "
        "successful renderer is not proof of visual inspection or scientific validity. "
        "Revoking the grant immediately blocks further model Studio mutations.")
    return result
