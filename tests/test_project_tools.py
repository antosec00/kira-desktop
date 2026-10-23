"""Strict project-tool contracts; fixtures contain no accounts or live services."""

import copy
import hashlib
import math

import pytest

from kira.domain.contracts import KiraError
from kira.domain.project_tools import (
    bounded_json, project_tool_specs, registration_example, validate_action, validate_manifest,
    validate_schema, validate_value,
)


def manifest(**changes):
    return {"name": "fixture_sum", "description": "Sum two synthetic integers.",
        "input_schema": {"type": "object", "properties": {
            "a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {"sum": {"type": "integer"}},
            "required": ["sum"], "additionalProperties": False},
        "tests": [{"name": "small sum", "arguments": {"a": 2, "b": 3}, "expected": {"sum": 5}}],
        # Service tests run a real worker, interpreter start-up included, within
        # this limit; a test that must time out sets its own short limit.
        "timeout_seconds": 30, **changes}


@pytest.mark.parametrize("action,payload", [
    ("enable", {}), ([], {}), ("describe", {"action": []}), ("describe", {"action": None}),
    ("read", {"tool_id": "x", "project_id": "foreign"}), ("read", {"tool_id": "x", "revision": True}),
    ("run", {"tool_id": "x", "revision": 1, "arguments": []}),
    ("test", {"tool_id": "x", "revision": 0}), ("search", {"session_id": "foreign"}),
    ("register", {"source_path": "../outside.py", "expected_sha256": "0" * 64, "manifest": manifest()}),
    ("register", {"source_path": "tool.py", "expected_sha256": "0" * 64, "manifest": manifest(), "tool_id": "x"}),
    ("register", {"source_path": "tool.py", "expected_sha256": "bad", "manifest": manifest()}),
    ("read", {"tool_id": "bad\ud800"}),
])
def test_actions_reject_scope_forgery_malformed_and_stale_values(action, payload):
    with pytest.raises(KiraError):
        validate_action(action, payload)


@pytest.mark.parametrize("changes", [
    {"name": "not-a-slug"}, {"description": ""}, {"tests": []}, {"timeout_seconds": 61},
    {"timeout_seconds": True}, {"max_output_bytes": 1}, {"python_path": "/other/python"},
    {"tests": [{"name": "x", "arguments": {"a": 1, "b": 2}, "expected": {"sum": "3"}}]},
    {"input_schema": {"type": "object", "additionalProperties": True}},
    {"output_schema": {"type": "string", "format": "email"}},
    {"output_schema": {"$ref": "https://example.invalid/schema"}},
    {"output_schema": {"type": "number", "minimum": math.inf}},
])
def test_manifest_is_a_bounded_closed_contract(changes):
    with pytest.raises(KiraError):
        validate_manifest(manifest(**changes))


def test_normalization_does_not_mutate_manifest_and_schemas_are_strict():
    raw = manifest()
    before = copy.deepcopy(raw)
    normalized = validate_manifest(raw)
    assert raw == before
    assert normalized["max_output_bytes"] == 16384
    for invalid in ({"a": True, "b": 3}, {"a": 2}, {"a": 2, "b": 3, "foreign": 0}):
        with pytest.raises(KiraError):
            validate_value(invalid, normalized["input_schema"])
    validate_value(10 ** 1000, validate_schema({"type": "integer"}))


def test_nested_schema_limit_and_data_limit_are_separate():
    schema = {"type": "integer"}
    for _ in range(4):
        schema = {"type": "object", "properties": {"nested": schema}, "required": ["nested"]}
    validate_schema(schema)
    with pytest.raises(KiraError):
        validate_schema({"type": "array", "items": schema})
    for value in (float("nan"), {"x": "\ud800"}, {"x": "y" * 32768}):
        with pytest.raises(KiraError):
            bounded_json(value)


def test_fixed_gateway_has_no_tool_implementation_or_host_authority():
    spec, = project_tool_specs()
    schema = spec["inputSchema"]
    assert spec["name"] == "project_tools"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"action", "payload"}
    assert "configure" not in schema["properties"]["action"]["enum"]


def test_registration_example_is_complete_and_hash_matches_exact_source():
    example = registration_example()
    action, payload = validate_action(**example["call"])
    assert action == "register"
    assert payload["expected_sha256"] == hashlib.sha256(example["source"].encode()).hexdigest()
    assert payload["manifest"]["tests"][0]["expected"] == 2
