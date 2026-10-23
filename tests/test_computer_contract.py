"""Closed visual-action contract before any browser effect."""

import pytest

from kira.domain.computer import COMPUTER_ACTIONS, computer_tool_specs, validate_computer_arguments


@pytest.mark.parametrize("arguments", [
    {"action": "click", "x": 10, "y": 20},
    {"action": "double_click", "x": 10, "y": 20},
    {"action": "move", "x": 10, "y": 20},
    {"action": "scroll", "direction": "down", "amount": 600},
    {"action": "scroll", "direction": "right", "x": 100, "y": 80},
    {"action": "type", "text": "Synthetic 日本語"},
    {"action": "key", "key": "Control+A"},
    {"action": "drag", "path": [{"x": 10, "y": 20}, {"x": 100, "y": 200}]},
    {"action": "wait", "duration_ms": 1500},
])
def test_bounded_visual_actions(arguments):
    validate_computer_arguments("act", {"observation_id": "synthetic-observation", **arguments})


@pytest.mark.parametrize("arguments", [
    {"action": "click", "x": True, "y": 0},
    {"action": "click", "x": 1600, "y": 0},
    {"action": "click", "x": 0, "y": -1},
    {"action": "click", "x": 1.0, "y": 1},
    {"action": "click", "x": 0, "y": 0, "button": "unknown"},
    {"action": "double_click", "x": 0, "y": 0, "button": "left"},
    {"action": "scroll", "direction": "down", "x": 12},
    {"action": "scroll", "direction": "down", "amount": 2401},
    {"action": "scroll", "direction": "diagonal"},
    {"action": "type", "text": ""},
    {"action": "type", "text": "x" * 10001},
    {"action": "type", "text": "x\0y"},
    {"action": "type", "text": "synthetic", "x": 0},
    {"action": "key", "key": "Meta+Q"},
    {"action": "key", "key": []},
    {"action": "drag", "path": [{"x": 1, "y": 1}]},
    {"action": "drag", "path": [{"x": 1, "y": 1}] * 33},
    {"action": "drag", "path": [{"x": 1, "y": 1}, {"x": 2, "y": 2, "script": "synthetic"}]},
    {"action": "wait", "duration_ms": 1501},
    {"action": "wait", "duration_ms": float("nan")},
    {"action": "evaluate", "code": "synthetic"},
    {"action": "click", "x": 0, "y": 0, "observation_id": ""},
])
def test_invalid_actions_rejected_before_dispatch(arguments):
    with pytest.raises(ValueError):
        validate_computer_arguments("act", {"observation_id": "synthetic-observation", **arguments})


def test_observation_and_action_scopes_are_closed():
    validate_computer_arguments("observe", {})
    with pytest.raises(ValueError):
        validate_computer_arguments("observe", {"desktop": True})
    with pytest.raises(ValueError):
        validate_computer_arguments("act", {"action": "wait"})
    namespace = computer_tool_specs()[0]
    assert namespace["name"] == "computer"
    assert {tool["name"] for tool in namespace["tools"]} == {"observe", "act"}
    assert set(namespace["tools"][1]["inputSchema"]["properties"]["action"]["enum"]) == COMPUTER_ACTIONS
