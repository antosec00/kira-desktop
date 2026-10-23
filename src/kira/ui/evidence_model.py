"""Canonical bounded event payloads and presentation-only evidence metadata."""

import json
from datetime import datetime
from typing import Any

from kira.ui.models import TranscriptModel


def readable_timestamp(value: str) -> str:
    """Keep the recorded time zone; the complete timestamp remains available."""
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError, OverflowError):
        return value
    label = parsed.strftime("%Y-%m-%d  %H:%M:%S")
    if parsed.microsecond:
        label += f".{parsed.microsecond // 1000:03d}"
    offset = parsed.strftime("%z")
    if offset:
        label += " UTC" + ("" if offset == "+0000" else offset[:3] + ":" + offset[3:])
    return label


class EvidenceModel(TranscriptModel):
    FIELDS = (*TranscriptModel.FIELDS, "sequence", "timestampLabel", "bodyTruncated")

    def append_event(self, event: dict[str, Any]) -> int:
        source = json.dumps(event.get("payload", {}), ensure_ascii=False, indent=2, default=str)
        timestamp = str(event.get("timestamp", ""))
        return self.append(kind="event", title=str(event.get("type", "")),
                           status=timestamp, timestampLabel=readable_timestamp(timestamp),
                           sequence=int(event.get("seq", 0)), runId=str(event.get("run_id", "")),
                           body=source, bodyTruncated=len(source) > self.MAX_BODY)
