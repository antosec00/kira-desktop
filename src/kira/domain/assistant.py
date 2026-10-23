"""Provider schemas for the service-bound Kira assistant.

Provider arguments cannot select a project, provider, model or permission. The
service binds those identities to the originating run and validates all input.
"""

from kira.domain.contracts import Json
from kira.domain.teams import (
    MAX_MEMBER_INSTRUCTIONS_BYTES,
    MAX_MEMBER_NAME_BYTES,
    MAX_MEMBER_ROLE_BYTES,
    MAX_TEAM_CONCURRENCY,
    MAX_TEAM_MEMBERS,
    MAX_TEAM_MESSAGE_BYTES,
    MEMBER_ID_PATTERN,
)

ASSISTANT_TOOL_NAMES = frozenset({
    "kira.environment", "kira.team_delegate", "kira.team_status", "kira.team_message",
    "kira.team_resume",
})
MAX_DELEGATION_GOAL_BYTES = 16_000
MAX_DELEGATION_BRIEF_BYTES = 16_000


def _object(properties: Json, required: list[str] | None = None) -> Json:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def assistant_tool_specs() -> list[Json]:
    """Fresh schemas, shared by MCP transport and service context introspection."""
    identifier = {"type": "string", "minLength": 1, "maxLength": 128}
    member = _object({
        "id": {"type": "string", "pattern": MEMBER_ID_PATTERN},
        "name": {"type": "string", "minLength": 1, "maxLength": MAX_MEMBER_NAME_BYTES},
        "role": {"type": "string", "minLength": 1, "maxLength": MAX_MEMBER_ROLE_BYTES},
        "instructions": {"type": "string", "maxLength": MAX_MEMBER_INSTRUCTIONS_BYTES},
        "access": {"type": "string", "enum": ["read", "write"]},
    })
    return [{"type": "namespace", "name": "kira",
             "description": "Operate the current Kira Desktop project and its managed team. "
                            "Project, chat, model and permissions are bound by Kira.",
             "tools": [
                 {"type": "function", "name": "environment",
                  "description": "Read the actual current project, chat, model, permissions, "
                                 "available Kira actions and active work. Other projects and "
                                 "credentials are excluded.",
                  "inputSchema": _object({})},
                 {"type": "function", "name": "team_delegate",
                  "description": "Queue one managed team for this chat. Use manual mode for "
                                 "user-chosen members, automatic mode for a coordinator-designed "
                                 "team. In manual mode the first member must have id coordinator; "
                                 "the total member count includes that coordinator. In automatic "
                                 "mode omit members and optionally set max_agents. "
                                 "Include decisions, scope, evidence references and success "
                                 "criteria in brief. The team starts after this turn completes "
                                 "successfully; then its results return to this chat. Do not "
                                 "wait for it inside this turn. The selected model, reasoning "
                                 "effort and project permission are preserved.",
                  "inputSchema": _object({
                      "goal": {"type": "string", "minLength": 1,
                               "maxLength": MAX_DELEGATION_GOAL_BYTES},
                      "brief": {"type": "string", "minLength": 1,
                                "maxLength": MAX_DELEGATION_BRIEF_BYTES},
                      "mode": {"type": "string", "enum": ["automatic", "manual"]},
                      "members": {"type": "array", "items": member,
                                  "maxItems": MAX_TEAM_MEMBERS},
                      "max_agents": {"type": "integer", "minimum": 1,
                                     "maximum": MAX_TEAM_MEMBERS},
                      "concurrency": {"type": "integer", "minimum": 1,
                                      "maximum": MAX_TEAM_CONCURRENCY},
                      "round_limit": {"type": "integer", "minimum": 0, "maximum": 10000,
                                      "description": "Coordinator rounds; default 3. Zero removes "
                                                     "the automatic round ceiling."},
                  }, ["goal", "brief", "mode"])},
                 {"type": "function", "name": "team_status",
                  "description": "Read this chat's managed team status and recorded results. "
                                 "Provide delegation_id for bounded member, task and message details; "
                                 "omit it for recent summary cards. A queued "
                                 "handoff cannot start until the current turn completes.",
                  "inputSchema": _object({"delegation_id": identifier}, [])},
                 {"type": "function", "name": "team_message",
                  "description": "Send context to a member or all in this chat's managed team. "
                                 "This cannot change permissions or start independent work.",
                  "inputSchema": _object({
                      "delegation_id": identifier,
                      "recipient_id": {"type": "string", "minLength": 1, "maxLength": 48},
                      "content": {"type": "string", "minLength": 1,
                                  "maxLength": MAX_TEAM_MESSAGE_BYTES},
                  })},
                 {"type": "function", "name": "team_resume",
                  "description": "Queue continuation of a settled team in this chat, retaining its "
                                 "goal, evidence, model and effort. Optional round_limit changes the new "
                                 "continuation only when the user requests it (0 unlimited); otherwise omit. "
                                 "Finish this turn without polling; starts after cleanup.",
                  "inputSchema": _object({"delegation_id": identifier,
                      "instruction": {"type": "string", "maxLength": MAX_DELEGATION_BRIEF_BYTES},
                      "round_limit": {"type": "integer", "minimum": 0, "maximum": 10000}},
                      ["delegation_id"])},
             ]}]
