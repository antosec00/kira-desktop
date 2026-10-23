"""Shared project-team limits and provider-facing schemas.

These schemas guide model output; the service still validates identities,
UTF-8 byte bounds, project permissions, dependencies and lifecycle transitions.
"""

from kira.domain.contracts import Json

MAX_TEAM_MEMBERS = 12
MAX_TEAM_CONCURRENCY = 8
MAX_PLAN_TASKS = 24
DEFAULT_MAX_AGENTS = 4
DEFAULT_CONCURRENCY = 2
DEFAULT_ROUND_LIMIT = 3
MAX_TEAM_GOAL_BYTES = 64_000
MAX_MEMBER_NAME_BYTES = 100
MAX_MEMBER_ROLE_BYTES = 400
MAX_MEMBER_INSTRUCTIONS_BYTES = 8_000
MAX_TEAM_MESSAGE_BYTES = 8_000
MAX_MAILBOX_PAGE = 50
TEAM_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted", "paused"})
MEMBER_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,47}$"
MAX_COMPLETION_ANCESTORS = 32
COORDINATOR_INSTRUCTIONS = (
    "Return exactly the required JSON object. Kira, not the mailbox, schedules the team. "
    "A member exists in the roster but does not start working until you RETURN a continue "
    "decision with concrete nonempty tasks assigned to member IDs. Kira starts those tasks "
    "only after this coordinator turn finishes and its cleanup is confirmed. "
    "team.send_message records context only: it never starts or resumes a member. "
    "Do not wait for or repeatedly poll idle members; return their assignments now. "
    "After the tasks finish, your next coordinator turn receives their actual results. "
    "Use no native subagents. First automatic plan includes a roster with coordinator first; "
    "otherwise members must be []. Tasks form a same-round acyclic dependency graph. "
    "Preserve the chosen model and effort. Read-only planning cannot modify files. "
    "A multi-member team cannot complete with only coordinator narration: inspect actual "
    "worker results, or the supplied verified worker receipts of an explicit continuation. "
    "Never mark failed or uncertain assignments completed. Use needs_input when user input "
    "is necessary. Each coordinator turn consumes one round, including a corrective turn; "
    "round_limit=1 permits initial assignments but pauses before their final review. "
    "Organizational roles are not a sandbox."
)


def _object(properties: Json) -> Json:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def coordinator_output_schema() -> Json:
    """Return a fresh schema so per-turn callers cannot mutate shared state."""
    identifier = {"type": "string", "pattern": MEMBER_ID_PATTERN}
    access = {"type": "string", "enum": ["read", "write"]}
    member = _object({
        "id": identifier, "name": {"type": "string"},
        "role": {"type": "string"}, "instructions": {"type": "string"},
        "access": access,
    })
    task = _object({
        "id": identifier, "title": {"type": "string"}, "member_id": identifier,
        "instructions": {"type": "string"}, "access": access,
        "depends_on": {"type": "array", "items": identifier, "maxItems": MAX_PLAN_TASKS},
    })
    return _object({
        "decision": {"type": "string", "enum": ["continue", "completed", "needs_input"],
                     "description": "Return continue with tasks to start workers after this turn. "
                                    "Complete only after reviewing recorded worker results."},
        "summary": {"type": "string"},
        "members": {"type": "array", "items": member, "maxItems": MAX_TEAM_MEMBERS},
        "tasks": {"type": "array", "items": task, "maxItems": MAX_PLAN_TASKS,
                  "description": "Concrete assignments required for continue. Mailbox messages "
                                 "do not create tasks. Empty for completed or needs_input."},
    })


def mailbox_tool_specs() -> list[Json]:
    """Codex App Server dynamic namespace; execution identity is service-bound."""
    return [{"type": "namespace", "name": "team",
             "description": "Communicate with the other members of this project team.",
             "tools": [
                 {"type": "function", "name": "send_message",
                  "description": "Record context for a member ID or all; this never starts or "
                                 "resumes a member or grants permissions. The coordinator must "
                                 "return a continue plan with tasks before workers can run. "
                                 "Use at most 8000 UTF-8 bytes.",
                  "inputSchema": _object({"recipient_id": {"type": "string"},
                                          "content": {"type": "string"}})},
                 {"type": "function", "name": "read_messages",
                  "description": "Read messages addressed to you or all after a team sequence. "
                                 "Keep the returned cursor. Do not poll idle members: return a "
                                 "continue plan with assignments to start their work. Scheduling "
                                 "metadata reports who is actually running.",
                  "inputSchema": {"type": "object", "properties": {
                      "after_seq": {"type": "integer", "minimum": 0, "default": 0},
                      "limit": {"type": "integer", "minimum": 1,
                                "maximum": MAX_MAILBOX_PAGE, "default": 20}},
                                  "required": [], "additionalProperties": False}},
             ]}]
