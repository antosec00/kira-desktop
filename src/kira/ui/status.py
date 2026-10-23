"""Small visual states derived from recorded evidence, never execution authority."""

from collections.abc import Iterable
from typing import Any


def run_visual_state(state: Any, *, waiting: bool = False,
                     tool_issues: Iterable[str] = ()) -> str:
    """Completion describes the request; adverse tool receipts remain visible."""
    if state == "running":
        return "waiting" if waiting else "running"
    if state == "completed":
        issues = set(tool_issues)
        if "error" in issues:
            return "error"
        if "unconfirmed" in issues:
            return "unconfirmed"
        return "completed"
    return {"queued": "queued", "awaiting_input": "waiting", "cancelling": "waiting",
            "failed": "error", "interrupted": "unconfirmed", "cancelled": "cancelled"}.get(
                state if isinstance(state, str) else "", "unknown")
