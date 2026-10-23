"""Closed and bounded 2D scene format for the opt-in Kira Studio prototype.

Scene documents are data, not Python/QML/JavaScript. Extension nodes require
separate audited schemas; never execute model-authored strings.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, NoReturn

from kira.domain.contracts import Json, KiraError

# Studio is not offered in this release: no interface entry or model tool exposes it.
# Its service methods and stored data stay, so turning this on restores the feature.
STUDIO_AVAILABLE = False

FORMAT = "kira.scene/1"
WIDTH, HEIGHT = 960, 540
MAX_SCENE_BYTES = 16_384
MAX_TEXT = 90
MAX_OBJECTS = 12
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _invalid(message: str) -> NoReturn:
    raise KiraError("INVALID_ARGUMENT", message)


def _object(value: Any, keys: set[str]) -> Json:
    if not isinstance(value, dict) or set(value) != keys:
        _invalid("Studio objects require exactly the documented fields")
    return value


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _color(value: Any) -> bool:
    return isinstance(value, str) and COLOR.fullmatch(value) is not None


def validate_scene(value: Any) -> Json:
    """Return a deep copy of a strictly validated version-one poster scene."""
    import json

    _object(value, {"format", "canvas", "objects"})
    if value["format"] != FORMAT:
        _invalid("Unsupported Studio scene format")
    canvas = _object(value["canvas"], {"width", "height"})
    if canvas != {"width": WIDTH, "height": HEIGHT}:
        _invalid("Unsupported Studio canvas dimensions")
    objects = value["objects"]
    if not isinstance(objects, list) or not 2 <= len(objects) <= MAX_OBJECTS:
        _invalid("Invalid Studio object count")
    ids: set[str] = set()
    backgrounds = messages = 0
    for index, obj in enumerate(objects):
        if not isinstance(obj, dict):
            _invalid("Studio scene objects must be maps")
        kind = obj.get("kind")
        if kind == "background":
            _object(obj, {"id", "kind", "color"})
            if obj["id"] != "background" or not _color(obj["color"]) or index != 0:
                _invalid("Invalid background layer")
            backgrounds += 1
        elif kind == "brush_text":
            _object(obj, {"id", "kind", "text", "color", "x", "y", "size"})
            if obj["id"] != "message" or not isinstance(obj["text"], str) \
                    or not 1 <= len(obj["text"]) <= MAX_TEXT \
                    or any(ord(c) < 32 for c in obj["text"]) \
                    or not _color(obj["color"]) \
                    or not _integer(obj["x"], 0, WIDTH) or not _integer(obj["y"], 0, HEIGHT) \
                    or not _integer(obj["size"], 20, 110):
                _invalid("Invalid editable brush-text layer")
            messages += 1
        elif kind == "star":
            _object(obj, {"id", "kind", "color", "x", "y", "radius"})
            if (not isinstance(obj["id"], str)
                    or re.fullmatch(r"star[1-9][0-9]?", obj["id"]) is None) \
                    or not _color(obj["color"]) \
                    or not _integer(obj["x"], 0, WIDTH) or not _integer(obj["y"], 0, HEIGHT) \
                    or not _integer(obj["radius"], 2, 80):
                _invalid("Invalid decorative star")
        else:
            _invalid("Unsupported Studio object kind")
        if obj["id"] in ids:
            _invalid("Duplicate Studio object ID")
        ids.add(obj["id"])
    if backgrounds != 1 or messages != 1:
        _invalid("The poster requires one background and one editable message")
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(serialized) > MAX_SCENE_BYTES:
            _invalid("Studio scene exceeds 16 KiB")
    except (TypeError, ValueError) as exc:
        raise KiraError("INVALID_ARGUMENT", "Non-serializable Studio scene") from exc
    return deepcopy(value)


def poster_scene(text: str, *, blue: str = "#2467c9",
                 background: str = "#f0e7f3") -> Json:
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
        _invalid("Poster caption must contain 1-90 characters")
    return validate_scene({
        "format": FORMAT,
        "canvas": {"width": WIDTH, "height": HEIGHT},
        "objects": [
            {"id": "background", "kind": "background", "color": background},
            {"id": "star1", "kind": "star", "color": "#e3b768", "x": 120, "y": 105, "radius": 16},
            {"id": "star2", "kind": "star", "color": "#e3b768", "x": 790, "y": 126, "radius": 22},
            {"id": "star3", "kind": "star", "color": "#dfb9db", "x": 792, "y": 419, "radius": 13},
            {"id": "message", "kind": "brush_text", "text": text, "color": blue,
             "x": 108, "y": 285, "size": 78},
        ],
    })


def patch_scene(scene: Json, object_id: str, values: Any) -> Json:
    previous = validate_scene(scene)
    if not isinstance(object_id, str) or not isinstance(values, dict) or not values:
        _invalid("Studio patch requires an object and nonempty fields")
    target = next((obj for obj in previous["objects"] if obj["id"] == object_id), None)
    if target is None:
        _invalid("The requested Studio object does not exist")
    allowed = ({"color"} if target["kind"] == "background" else
               {"text", "color", "x", "y", "size"} if target["kind"] == "brush_text" else
               {"color", "x", "y", "radius"})
    if not set(values) <= allowed:
        _invalid("Studio patch contains unsupported fields")
    target.update(values)
    return validate_scene(previous)


# A single narrowly-scoped provider tool, reusing the existing Kira namespace.
# No action can enable Studio, grant model access, or execute generated code.
STUDIO_TOOL_NAMES = frozenset({"kira.studio"})
STUDIO_ACTIONS = frozenset({
    "describe", "inspect", "poster", "patch", "restore", "history", "render", "export",
})


def studio_tool_specs() -> list[Json]:
    """Provider-independent Studio gateway. Detailed contracts are discoverable."""
    return [{
        "type": "function",
        "name": "studio",
        "description": (
            "Work with the user's explicitly enabled, project-owned Kira Studio canvas. "
            "Use describe to learn the exact action schemas and inspect to learn the "
            "current revision before edits. Create an editable poster, change individual "
            "layers or their colors/coordinates, restore, render or export PNG. "
            "Only available with a separate per-chat user grant and Execute/write permission. "
            "No source code, scripts, network or 3D actions. Never claim a PNG was "
            "visually verified simply because rendering completed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": sorted(STUDIO_ACTIONS)},
                "payload": {"type": "object"},
            },
            "required": ["action", "payload"],
            "additionalProperties": False,
        },
    }]


def validate_studio_action(action: Any, payload: Any) -> tuple[str, Json]:
    """Strict service-side field/type/size policy, independent of model schema."""
    if not isinstance(action, str) or action not in STUDIO_ACTIONS:
        _invalid("Unsupported Studio action")
    if not isinstance(payload, dict):
        _invalid("Studio action payload must be an object")
    shapes: dict[str, tuple[set[str], set[str]]] = {
        "describe": (set(), set()),
        "inspect": (set(), set()),
        "history": (set(), set()),
        "render": (set(), set()),
        "export": (set(), set()),
        "poster": ({"text", "expected_revision"}, {"text_color", "background"}),
        "patch": ({"object_id", "values", "expected_revision"}, set()),
        "restore": ({"target_revision", "expected_revision"}, set()),
    }
    required, optional = shapes[action]
    if set(payload) - (required | optional) or required - set(payload):
        _invalid("Unexpected fields in Studio action")
    if "expected_revision" in payload and (
            type(payload["expected_revision"]) is not int
            or not 0 <= payload["expected_revision"] < 2000):
        _invalid("Invalid expected Studio revision")
    if "target_revision" in payload and (
            type(payload["target_revision"]) is not int
            or not 1 <= payload["target_revision"] <= 2000):
        _invalid("Invalid target Studio revision")
    if "text" in payload and (
            not isinstance(payload["text"], str)
            or not 1 <= len(payload["text"]) <= MAX_TEXT
            or any(ord(c) < 32 for c in payload["text"])):
        _invalid("Invalid Studio poster text")
    for key in ("text_color", "background"):
        if key in payload and not _color(payload[key]):
            _invalid("Invalid Studio color")
    if "object_id" in payload and (
            not isinstance(payload["object_id"], str)
            or len(payload["object_id"]) > 32):
        _invalid("Invalid Studio object ID")
    if "values" in payload and (not isinstance(payload["values"], dict)
                                or not payload["values"] or len(payload["values"]) > 5):
        _invalid("Invalid Studio patch fields")
    import json
    try:
        encoded = json.dumps(payload, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise KiraError("INVALID_ARGUMENT", "Invalid Studio payload") from exc
    if len(encoded.encode("utf-8")) > 8192:
        _invalid("Studio action payload exceeds 8 KiB")
    return action, deepcopy(payload)


def studio_action_contract() -> Json:
    return {
        "revision": "Read inspect before edits; pass the current expected_revision.",
        "actions": {
            "describe": {},
            "inspect": {},
            "history": {},
            "poster": {
                "text": "Exact text, 1-90 Unicode characters",
                "expected_revision": "Current revision, 0 for new world",
                "text_color": "#RRGGBB (optional, default blue)",
                "background": "#RRGGBB (optional, default lilac)",
            },
            "patch": {
                "object_id": "background | message | star1 | star2 | star3",
                "values": "Allowed fields depend on kind; see current scene",
                "expected_revision": "Current revision",
            },
            "restore": {
                "target_revision": "Existing revision (>=1)",
                "expected_revision": "Current revision",
            },
            "render": {},
            "export": {},
        },
        "limitations": (
            "One 960x540 editable vector/raster poster scene per project. "
            "No arbitrary image generation/inpainting, scripts, external URLs or 3D. "
            "A render success reports a PNG file, not model visual inspection."
        ),
    }
