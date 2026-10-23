"""Readable evidence metadata preserves the recorded payload and zone."""

import json

from kira.ui.evidence_model import EvidenceModel, readable_timestamp


def test_payload_remains_valid_json_and_keeps_all_literal_values(qapp):
    payload = {"content": 'Heading\n{"nested": {"model": "fixture"}}\n<b>plain</b>', "value": 0}
    model = EvidenceModel()
    model.append_event({"run_id": "fixture-run", "seq": 42, "type": "context.supplied",
                        "timestamp": "2026-10-15T14:07:08.123456+02:00", "payload": payload})
    row = model.rows[0]
    assert json.loads(row["body"]) == payload
    assert row["sequence"] == 42 and not row["bodyTruncated"]
    assert row["status"] == "2026-10-15T14:07:08.123456+02:00"
    assert row["timestampLabel"] == "2026-10-15  14:07:08.123 UTC+02:00"
    assert model.rows[0]["title"] == "context.supplied"


def test_original_payload_preview_is_explicitly_marked_if_bounded(qapp):
    model = EvidenceModel()
    model.append_event({"seq": 1, "payload": {"content": "x" * (model.MAX_BODY + 1)}})
    assert model.rows[0]["bodyTruncated"]
    assert len(model.rows[0]["body"]) <= model.MAX_BODY + 100


def test_timestamps_never_invent_a_zone_or_reinterpret_invalid_input():
    assert readable_timestamp("2026-10-15T12:00:00Z") == "2026-10-15  12:00:00 UTC"
    assert readable_timestamp("2026-10-15T12:00:00") == "2026-10-15  12:00:00"
    assert readable_timestamp("12:00") == "12:00"
    assert readable_timestamp("<invalid>") == "<invalid>"
