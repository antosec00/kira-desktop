"""Token-window policy using provider metadata, without a turn-count expiry."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from kira.domain.contracts import Json


def positive_integer(value: Any) -> int | None:
    return value if type(value) is int and 0 < value < 2**53 else None


@dataclass(frozen=True)
class ContextPolicy:
    """Maximum catalog window and an explicitly local working-space reservation.

    The reservation is a planning margin, not a claim about maximum model output.
    A missing catalog limit remains unknown rather than borrowing another model's.
    """

    context_window: int | None
    default_context_window: int | None
    max_context_window: int | None
    max_output_tokens: int | None
    reserved_tokens: int
    compact_threshold: int | None
    source: str
    reservation_source: str

    @classmethod
    def from_catalog(cls, model: Json) -> ContextPolicy:
        default = positive_integer(model.get("context_window"))
        maximum = positive_integer(model.get("max_context_window"))
        window = max(value for value in (default, maximum) if value is not None) \
            if default is not None or maximum is not None else None
        output = positive_integer(model.get("max_output_tokens"))
        reserve = math.ceil(window * 0.18) if window else 0
        reservation_source = "local_planning_margin" if window else "unavailable"
        if window and output and output < window:
            # Allow the reported output budget plus reasoning/tool/counting slack.
            # This plans input admission; it does not send a max_output_tokens
            # parameter or promise that this route accepts a particular output.
            reserve = max(reserve, output + math.ceil(window * 0.03))
            reservation_source = "provider_output_metadata_with_planning_margin"
        if window:
            reserve = min(reserve, max(1, window - 1))
        threshold = max(1, window - reserve) if window else None
        return cls(window, default, maximum, output, reserve, threshold,
                   "provider_catalog_maximum" if maximum and maximum == window else
                   "provider_catalog" if default else "unavailable", reservation_source)

    def public(self) -> Json:
        return {"context_window": self.context_window,
                "default_context_window": self.default_context_window,
                "max_context_window": self.max_context_window,
                "max_output_tokens": self.max_output_tokens,
                "reserved_tokens": self.reserved_tokens,
                "reservation_source": self.reservation_source,
                "compact_threshold": self.compact_threshold,
                "capacity_source": self.source,
                "maximum_verified": False}


def has_opaque_context(value: Any) -> bool:
    """Opaque encrypted state has no locally observable token-to-byte ratio."""
    if isinstance(value, list):
        return any(has_opaque_context(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in ("reasoning", "compaction") and isinstance(
                value.get("encrypted_content"), str) and bool(value["encrypted_content"]):
            return True
        return any(has_opaque_context(item) for item in value.values())
    return False


def has_image_inputs(value: Any) -> bool:
    if isinstance(value, list):
        return any(has_image_inputs(item) for item in value)
    if isinstance(value, dict):
        return value.get("type") == "input_image" or any(has_image_inputs(item) for item in value.values())
    return False


def estimate_tokens(value: Any, *, bytes_per_token: float | None = None) -> int:
    """A labelled estimate, calibrated by observed request usage when available.

    Image cost remains approximate. Opaque encrypted provider content is excluded
    entirely: ciphertext bytes cannot establish context tokens. With opaque items
    this function estimates only the visible input and callers must label it as
    partial, leaving total usage unknown. The transport byte cap is independent.
    """
    def simplify(item: Any) -> Any:
        if isinstance(item, list):
            return [simplify(part) for part in item]
        if isinstance(item, dict):
            if item.get("type") == "input_image":
                return {"type": "input_image", "estimated_tokens": "x" * 8192}
            if isinstance(item.get("type"), str) and item["type"] in {"reasoning", "compaction"} \
                    and has_opaque_context(item):
                return {"type": item["type"], "opaque_context": True}
            return {key: simplify(part) for key, part in item.items()}
        return item

    size = len(json.dumps(simplify(value), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))
    ratio = max(1.0, min(4.0, bytes_per_token)) if bytes_per_token else 2.5
    return max(1, math.ceil(size / ratio))


def calibrated_ratio(value: Any, input_tokens: Any) -> float | None:
    if not positive_integer(input_tokens) or has_opaque_context(value) or has_image_inputs(value):
        return None
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return max(1.0, min(4.0, size / input_tokens))
