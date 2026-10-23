"""Bounded, provider-reported account quotas; unrelated to per-turn token usage."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from kira.domain.contracts import Json

MAX_LIMIT_GROUPS = 16
MAX_TIMESTAMP = 253_402_300_799
# Claude plan windows as Claude Code names them; durations in minutes.
CLAUDE_WINDOWS: dict[str, int] = {"five_hour": 300, "seven_day": 10_080,
                                  "seven_day_opus": 10_080, "seven_day_sonnet": 10_080}
CLAUDE_STATUSES = frozenset({"allowed", "allowed_warning", "rejected"})


def _text(value: Any, maximum: int = 256) -> str | None:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    return value


def _integer(value: Any, maximum: int, minimum: int = 0) -> int | None:
    return value if type(value) is int and minimum <= value <= maximum else None


def _boolean(value: Any) -> bool | None:
    return value if type(value) is bool else None


def codex_account(account: Any, account_id: Any = None) -> Json:
    """Whitelist identity display fields, never forwarding arbitrary auth payloads."""
    account = account if isinstance(account, dict) else {}
    return {"id": _text(account_id), "type": _text(account.get("type"), 64),
            "plan_type": _text(account.get("planType"), 64),
            "label": _text(account.get("email"), 254)}


def unavailable_codex_usage(reason: str, account: Any = None) -> Json:
    return {"available": False, "source": "codex", "scope": "provider_account",
            "observed_at": None, "account": codex_account(account), "limits": [],
            "limits_omitted": 0, "reason": reason}


def _window(value: Any, kind: str) -> Json | None:
    if not isinstance(value, dict):
        return None
    used = _integer(value.get("usedPercent"), 100)
    return {"kind": kind, "used_percent": used,
            "remaining_percent": None if used is None else 100 - used,
            "duration_minutes": _integer(value.get("windowDurationMins"), 52_596_000, 1),
            "resets_at": _integer(value.get("resetsAt"), MAX_TIMESTAMP)}


def _credits(value: Any) -> Json | None:
    if not isinstance(value, dict):
        return None
    return {"has_credits": _boolean(value.get("hasCredits")),
            "unlimited": _boolean(value.get("unlimited")),
            "balance": _text(value.get("balance"), 128)}


def _limit(value: Json, key: str | None) -> Json:
    return {"id": key or _text(value.get("limitId")),
            "name": _text(value.get("limitName")),
            "plan_type": _text(value.get("planType"), 64),
            "windows": [window for kind in ("primary", "secondary")
                        if (window := _window(value.get(kind), kind)) is not None],
            "credits": _credits(value.get("credits"))}


def normalize_codex_usage(payload: Json, account: Json) -> Json:
    """Normalize a complete RPC snapshot, never merging a prior account's data.

    Dictionary keys identify multi-bucket quotas. The legacy view is a fallback,
    not an extra bucket to add to the same report. Unknown fields remain null;
    percentages are neither clamped nor interpreted as an admission decision.
    """
    limits: list[Json] = []
    omitted = 0
    groups = payload.get("rateLimitsByLimitId")
    if isinstance(groups, dict):
        for key, value in groups.items():
            identifier = _text(key)
            if identifier is None or not isinstance(value, dict):
                continue
            if len(limits) < MAX_LIMIT_GROUPS:
                limits.append(_limit(value, identifier))
            else:
                omitted += 1
    if not limits:
        legacy = payload.get("rateLimits")
        if not isinstance(legacy, dict):
            return unavailable_codex_usage("invalid_response", account)
        limits.append(_limit(legacy, None))
    return {"available": True, "source": "codex", "scope": "provider_account",
            "observed_at": datetime.now(UTC).isoformat(),
            "account": codex_account(account, payload.get("accountId")),
            "limits": limits, "limits_omitted": omitted, "reason": None}


def _status(value: Any) -> str | None:
    return value if isinstance(value, str) and value in CLAUDE_STATUSES else None


def _percent(value: Any) -> float | None:
    """A reported fraction of the window (0.0 = unused) as a percentage; others stay unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if value < 0 or value > 100:
        return None
    return round(float(value) * 100, 1)


def unavailable_claude_usage(reason: str) -> Json:
    return {"available": False, "source": "claude_code", "scope": "provider_account",
            "observed_at": None, "account": {"type": "claude_code", "label": None, "plan_type": None},
            "limits": [], "limits_omitted": 0, "reason": reason}


def normalize_claude_plan(payload: Any, observed_at: Any) -> Json:
    """Normalize the plan-limit notice Claude Code derives from its own API responses.

    Kira never reads Claude credentials or calls a usage endpoint: the only
    source is the rate-limit event the Agent SDK delivers during a run, which
    carries the same window percentages Claude Code shows in its own usage
    indicator. Percentages are reported, not clamped; a missing window stays
    absent and never becomes zero. No account identity is involved.
    """
    if not isinstance(payload, dict):
        return unavailable_claude_usage("invalid_record")
    status = _status(payload.get("status"))
    limit_type = _text(payload.get("limit_type"), 64)
    raw_windows = payload.get("windows")
    windows: list[Json] = []
    for kind, minutes in CLAUDE_WINDOWS.items():
        item = raw_windows.get(kind) if isinstance(raw_windows, dict) else None
        if item is None and kind == limit_type:
            item = {"utilization": payload.get("utilization"), "resets_at": payload.get("resets_at")}
        if not isinstance(item, dict):
            continue
        used = _percent(item.get("utilization"))
        windows.append({"kind": kind, "used_percent": used,
                        "remaining_percent": None if used is None else max(0.0, round(100 - used, 1)),
                        "duration_minutes": minutes,
                        "resets_at": _integer(item.get("resets_at"), MAX_TIMESTAMP),
                        "status": status if kind == limit_type else None})
    if not windows:
        return unavailable_claude_usage("invalid_record")
    overage = {"kind": "overage", "status": _status(payload.get("overage_status")),
               "reason": _text(payload.get("overage_disabled_reason"), 64),
               "active": _boolean(payload.get("using_overage")),
               "resets_at": _integer(payload.get("overage_resets_at"), MAX_TIMESTAMP)}
    return {"available": True, "source": "claude_code", "scope": "provider_account",
            "observed_at": _text(observed_at, 64),
            "account": {"type": "claude_code", "label": None, "plan_type": None},
            "limits": [{"id": "claude_plan", "name": None, "plan_type": None, "status": status,
                        "limit_type": limit_type, "windows": windows, "credits": overage}],
            "limits_omitted": 0, "reason": "observed_during_runs"}
