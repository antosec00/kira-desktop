"""Bounded, provider-reported account quotas; unrelated to per-turn token usage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from kira.domain.contracts import Json

MAX_LIMIT_GROUPS = 16
MAX_TIMESTAMP = 253_402_300_799


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
