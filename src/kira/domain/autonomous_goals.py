"""Provider-neutral goal tools; scheduling authority stays in the service."""

from kira.domain.contracts import Json

GOAL_TOOL_NAMES = frozenset({"goal.status", "goal.checkpoint"})

GOAL_INSTRUCTIONS = (
    "This chat has an explicitly authorized autonomous Kira goal. Its service-owned original objective "
    "and current checkpoint are supplied each cycle, independently of model history compaction. "
    "Honor newer user corrections; do not silently replace the objective or expand permissions. "
    "Work toward its success criteria. When a useful turn is finished, call goal.checkpoint with a "
    "bounded summary of verified work, remaining work and next_step. Use goal.status to discover exact "
    "run/seq evidence references. Record completed only when all requested work is achieved, and attach "
    "actual evidence or an explicit text deliverable for a writing task. Completion is your assessment, "
    "not independent proof. Missing evidence, unknown effects or a necessary user decision must be "
    "reported honestly; never repeat uncertain actions. A completed provider turn is not goal completion. "
    "Kira schedules the next cycle after successful cleanup; do not poll or keep the turn alive to wait. "
    "If you delegate a Team, checkpoint the handoff and finish this turn; the service waits for Team "
    "results before advancing. Use the available history/help tools to retrieve original details on "
    "demand; do not infer that the bounded summary contains the complete conversation."
)


def goal_tool_specs(*, writable: bool) -> Json:
    tools: list[Json] = [{"type": "function", "name": "status",
        "description": "Read the original autonomous goal, durable checkpoint and recent recorded "
                       "evidence references in this chat. Reading never starts or resumes work.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]
    if writable:
        tools.append({"type": "function", "name": "checkpoint",
            "description": "Save this goal cycle's progress assessment. completed is committed only "
                           "after this provider turn completes and cleanup succeeds. Evidence references "
                           "come from goal.status. An explicit text deliverable can support a pure writing goal. "
                           "blocked requires a concrete next_step explaining the missing decision or dependency.",
            "inputSchema": {"type": "object", "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["continue", "completed", "blocked"]},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 6000},
                    "next_step": {"type": "string", "maxLength": 3000},
                    "evidence": {"type": "array", "maxItems": 24, "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {"run_id": {"type": "string", "maxLength": 128},
                                       "seq": {"type": "integer", "minimum": 1}},
                        "required": ["run_id", "seq"]}},
                    "deliverable": {"type": "string", "maxLength": 12000}},
                "required": ["status", "summary", "next_step", "evidence"]}})
    return {"type": "namespace", "name": "goal", "description": "Kira's explicitly enabled autonomous objective.",
            "tools": tools}
