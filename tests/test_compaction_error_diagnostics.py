"""Output-shape diagnostics contain no provider text or private native state."""

import json

import pytest

from kira.backends.openai_responses import OutputItemValidationError, OutputItems


@pytest.mark.parametrize("index,code", [
    (None, "output_item_index_type"), (True, "output_item_index_type"),
    ("private-index-canary", "output_item_index_type"), (-1, "output_item_index_range"),
    (64, "output_item_limit"), (10**20, "output_item_limit"),
])
def test_index_shape_diagnostics_are_bounded_and_non_content(index, code):
    event = {"type": "response.output_item.added", "output_index": index,
             "item": {"type": "compaction", "id": "private-id-canary",
                      "encrypted_content": "private-state-canary"}}
    with pytest.raises(OutputItemValidationError) as caught:
        OutputItems().observe(event)
    details = caught.value.details
    assert details["source"] == "kira_output_validation" and details["code"] == code
    assert details["item_type"] == "compaction" and details["item_object_type"] == "dict"
    assert details["max_output_items"] == 64
    encoded = json.dumps(details)
    assert "canary" not in encoded and len(encoded) < 500
    assert ("output_index" in details) is (type(index) is int and -1_000_000 <= index <= 1_000_000)


@pytest.mark.parametrize("item", [None, "private-item-canary", ["private-list-canary"]])
def test_item_object_diagnostics_never_retain_literal_rejected_content(item):
    with pytest.raises(OutputItemValidationError) as caught:
        OutputItems().observe({"type": "response.output_item.done", "output_index": 0, "item": item})
    assert caught.value.details["code"] == "output_item_object_type"
    assert caught.value.details["output_index"] == 0
    assert "canary" not in json.dumps(caught.value.details)


def test_unrecognized_item_identity_and_event_type_are_not_echoed():
    with pytest.raises(OutputItemValidationError) as caught:
        OutputItems().observe({"type": ["private-event-canary"], "output_index": -1,
                              "item": {"type": "private-type-canary"}})
    assert caught.value.details["event_type"] == caught.value.details["item_type"] == "other"
    assert "canary" not in json.dumps(caught.value.details)
