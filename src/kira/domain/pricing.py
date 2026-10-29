"""Deterministic model pricing, prompt-caching savings, context gauge, and token/cost budgeting."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

Json = dict[str, Any]


@dataclass(frozen=True)
class ModelPricing:
    family: str
    billing_mode: str  # "local" | "subscription" | "api"
    input_per_m: float
    cached_input_per_m: float
    cache_write_per_m: float
    output_per_m: float
    default_context_window: int


def _valid_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or not math.isfinite(value):
        return None
    return value


def _valid_int(value: Any) -> int | None:
    num = _valid_number(value)
    if num is None:
        return None
    return int(num)


def resolve_model_pricing(backend: str, model: str = "") -> ModelPricing:
    """Return per-1M-token USD rates and default context window for a backend/model pair."""
    b = (backend or "").strip().lower()
    m = (model or "").strip().lower()

    if b == "ollama":
        return ModelPricing(
            family=m or "ollama-local",
            billing_mode="local",
            input_per_m=0.0,
            cached_input_per_m=0.0,
            cache_write_per_m=0.0,
            output_per_m=0.0,
            default_context_window=32_768,
        )

    billing = "subscription" if b in {"claude_code", "openai_chatgpt", "deepseek_web"} else "api"

    # Anthropic / Claude Code models (up to 90% prompt caching discount on cached reads)
    if b == "claude_code" or "claude" in m:
        if "opus" in m:
            return ModelPricing(
                family="claude-opus",
                billing_mode=billing,
                input_per_m=15.00,
                cached_input_per_m=1.50,
                cache_write_per_m=18.75,
                output_per_m=75.00,
                default_context_window=200_000,
            )
        if "haiku" in m:
            return ModelPricing(
                family="claude-haiku",
                billing_mode=billing,
                input_per_m=0.80,
                cached_input_per_m=0.08,
                cache_write_per_m=1.00,
                output_per_m=4.00,
                default_context_window=200_000,
            )
        return ModelPricing(
            family="claude-sonnet",
            billing_mode=billing,
            input_per_m=3.00,
            cached_input_per_m=0.30,
            cache_write_per_m=3.75,
            output_per_m=15.00,
            default_context_window=200_000,
        )

    # DeepSeek models (DeepSeek V3 / R1 with automatic disk/context caching)
    if b in {"deepseek_account", "deepseek_web"} or "deepseek" in m:
        if "reasoner" in m or "r1" in m:
            return ModelPricing(
                family="deepseek-reasoner",
                billing_mode=billing,
                input_per_m=0.55,
                cached_input_per_m=0.14,
                cache_write_per_m=0.55,
                output_per_m=2.19,
                default_context_window=128_000,
            )
        return ModelPricing(
            family="deepseek-chat",
            billing_mode=billing,
            input_per_m=0.27,
            cached_input_per_m=0.07,
            cache_write_per_m=0.27,
            output_per_m=1.10,
            default_context_window=128_000,
        )

    # OpenAI / Codex models
    if "mini" in m or "nano" in m:
        return ModelPricing(
            family="openai-mini",
            billing_mode=billing,
            input_per_m=0.40,
            cached_input_per_m=0.10,
            cache_write_per_m=0.40,
            output_per_m=1.60,
            default_context_window=200_000,
        )
    if m.startswith(("o1", "o3", "o4")):
        return ModelPricing(
            family="openai-reasoning",
            billing_mode=billing,
            input_per_m=2.00,
            cached_input_per_m=0.50,
            cache_write_per_m=2.00,
            output_per_m=8.00,
            default_context_window=200_000,
        )
    if "gpt-4" in m:
        return ModelPricing(
            family="gpt-4",
            billing_mode=billing,
            input_per_m=2.00,
            cached_input_per_m=0.50,
            cache_write_per_m=2.00,
            output_per_m=8.00,
            default_context_window=128_000,
        )
    return ModelPricing(
        family="gpt-5-codex",
        billing_mode=billing,
        input_per_m=1.25,
        cached_input_per_m=0.125,
        cache_write_per_m=1.25,
        output_per_m=10.00,
        default_context_window=400_000 if b == "codex" else 200_000,
    )


def calculate_cost_telemetry(usage: Json, backend: str = "", model: str = "") -> Json:
    """Compute exact USD cost, prompt-caching hit rate, and cache savings from a usage mapping."""
    resolved_backend = str(usage.get("source") or backend or "").strip()
    resolved_model = str(usage.get("model") or model or "").strip()
    pricing = resolve_model_pricing(resolved_backend, resolved_model)

    raw_in = _valid_int(usage.get("input_tokens"))
    raw_cached = _valid_int(usage.get("cached_input_tokens"))
    raw_write = _valid_int(usage.get("cache_creation_input_tokens"))
    raw_out = _valid_int(usage.get("output_tokens"))
    raw_total = _valid_int(usage.get("total_tokens"))

    measured = raw_in is not None or raw_out is not None
    input_tokens = raw_in or 0
    cached_tokens = min(input_tokens, raw_cached or 0) if raw_in is not None else (raw_cached or 0)
    write_tokens = min(max(0, input_tokens - cached_tokens), raw_write or 0) if raw_in is not None else (raw_write or 0)
    uncached_tokens = max(0, input_tokens - cached_tokens - write_tokens)
    output_tokens = raw_out or 0
    total_tokens = raw_total if raw_total is not None else (input_tokens + output_tokens if measured else 0)

    input_cost = (uncached_tokens / 1_000_000.0) * pricing.input_per_m
    cached_cost = (cached_tokens / 1_000_000.0) * pricing.cached_input_per_m
    write_cost = (write_tokens / 1_000_000.0) * pricing.cache_write_per_m
    output_cost = (output_tokens / 1_000_000.0) * pricing.output_per_m
    total_cost = input_cost + cached_cost + write_cost + output_cost

    # What the same prompt + completion would have cost without prompt caching reads
    baseline_input_cost = (input_tokens / 1_000_000.0) * pricing.input_per_m
    baseline_total_cost = baseline_input_cost + output_cost
    cache_savings_usd = max(
        0.0,
        (cached_tokens / 1_000_000.0) * max(0.0, pricing.input_per_m - pricing.cached_input_per_m),
    )

    cache_hit_percent = (cached_tokens / input_tokens * 100.0) if input_tokens > 0 else 0.0
    cache_savings_percent = (
        (cache_savings_usd / baseline_input_cost * 100.0) if baseline_input_cost > 0 else 0.0
    )

    return {
        "measured": measured,
        "backend": resolved_backend,
        "model": resolved_model,
        "family": pricing.family,
        "billing_mode": pricing.billing_mode,
        "input_tokens": input_tokens if raw_in is not None else None,
        "uncached_input_tokens": uncached_tokens if raw_in is not None else None,
        "cached_input_tokens": cached_tokens if raw_cached is not None else None,
        "cache_creation_input_tokens": write_tokens if raw_write is not None else None,
        "output_tokens": output_tokens if raw_out is not None else None,
        "total_tokens": total_tokens if measured else None,
        "input_cost_usd": round(input_cost, 6),
        "cached_input_cost_usd": round(cached_cost, 6),
        "cache_write_cost_usd": round(write_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(total_cost, 6),
        "baseline_cost_usd": round(baseline_total_cost, 6),
        "cache_savings_usd": round(cache_savings_usd, 6),
        "cache_hit_percent": round(cache_hit_percent, 2),
        "cache_savings_percent": round(cache_savings_percent, 2),
        "rates_per_m": {
            "input": pricing.input_per_m,
            "cached_input": pricing.cached_input_per_m,
            "cache_write": pricing.cache_write_per_m,
            "output": pricing.output_per_m,
        },
    }


def estimate_context_gauge(
    usage: Json | None,
    context: Json | None = None,
    *,
    backend: str = "",
    model: str = "",
    draft_text: str = "",
    transcript_tokens: int = 0,
    catalog_window: int | None = None,
) -> Json:
    """Compute real-time context window occupancy and fill thresholds for the input bar."""
    u = usage if isinstance(usage, dict) else {}
    ctx = context if isinstance(context, dict) else {}
    raw_policy = ctx.get("policy")
    policy: Json = raw_policy if isinstance(raw_policy, dict) else {}
    raw_metrics = ctx.get("metrics")
    metrics: Json = raw_metrics if isinstance(raw_metrics, dict) else {}

    resolved_backend = str(backend or u.get("source") or ctx.get("backend") or "codex").strip()
    resolved_model = str(model or u.get("model") or ctx.get("model") or "").strip()
    pricing = resolve_model_pricing(resolved_backend, resolved_model)

    capacity = (
        _valid_int(policy.get("active_context_window"))
        or _valid_int(policy.get("context_window"))
        or _valid_int(u.get("model_context_window"))
        or _valid_int(catalog_window)
        or _valid_int(policy.get("max_context_window"))
        or pricing.default_context_window
    )

    raw_last = u.get("last_request")
    last_req: Json = raw_last if isinstance(raw_last, dict) else {}
    measured_history = (
        _valid_int(last_req.get("input_tokens"))
        or _valid_int(metrics.get("preflight_input_tokens"))
        or _valid_int(metrics.get("input_tokens"))
        or _valid_int(metrics.get("estimated_tokens"))
        or _valid_int(metrics.get("visible_estimated_tokens"))
        or _valid_int(u.get("input_tokens"))
    )
    is_measured = measured_history is not None
    history_tokens = measured_history if measured_history is not None else max(0, int(transcript_tokens or 0))

    draft_bytes = len((draft_text or "").strip().encode("utf-8"))
    draft_tokens = (draft_bytes + 3) // 4 if draft_bytes > 0 else 0
    used_tokens = history_tokens + draft_tokens

    fill_percent = min(100.0, (used_tokens / capacity) * 100.0) if capacity > 0 else 0.0
    compact_threshold = _valid_int(policy.get("compact_threshold"))
    if fill_percent >= 85.0:
        level = "critical"
    elif fill_percent >= 65.0 or (compact_threshold and used_tokens >= compact_threshold):
        level = "warning"
    else:
        level = "normal"

    return {
        "backend": resolved_backend,
        "model": resolved_model,
        "capacity_tokens": capacity,
        "history_tokens": history_tokens,
        "draft_tokens": draft_tokens,
        "used_tokens": used_tokens,
        "remaining_tokens": max(0, capacity - used_tokens),
        "fill_percent": round(fill_percent, 1),
        "level": level,
        "measured": is_measured,
        "compact_threshold": compact_threshold,
    }


def evaluate_budget(
    total_tokens: int,
    total_cost_usd: float,
    *,
    token_budget: int = 0,
    cost_budget_usd: float = 0.0,
) -> Json:
    """Evaluate token and cost usage against configured budget thresholds."""
    t_limit = max(0, int(token_budget or 0))
    c_limit = max(0.0, float(cost_budget_usd or 0.0))
    t_used = max(0, int(total_tokens or 0))
    c_used = max(0.0, float(total_cost_usd or 0.0))

    token_pct = (t_used / t_limit * 100.0) if t_limit > 0 else 0.0
    cost_pct = (c_used / c_limit * 100.0) if c_limit > 0 else 0.0
    max_pct = max(token_pct, cost_pct)

    token_exceeded = t_limit > 0 and t_used >= t_limit
    cost_exceeded = c_limit > 0 and c_used >= c_limit
    exceeded = token_exceeded or cost_exceeded
    warning = not exceeded and max_pct >= 80.0

    return {
        "active": t_limit > 0 or c_limit > 0,
        "token_budget": t_limit,
        "cost_budget_usd": round(c_limit, 4),
        "tokens_used": t_used,
        "cost_used_usd": round(c_used, 6),
        "token_percent": round(min(100.0, token_pct), 1) if t_limit > 0 else 0.0,
        "cost_percent": round(min(100.0, cost_pct), 1) if c_limit > 0 else 0.0,
        "max_percent": round(min(100.0, max_pct), 1),
        "token_exceeded": token_exceeded,
        "cost_exceeded": cost_exceeded,
        "exceeded": exceeded,
        "warning": warning,
        "level": "critical" if exceeded else ("warning" if warning else "normal"),
    }
