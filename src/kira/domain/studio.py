"""Closed and bounded 2D scene format for the opt-in Kira Studio prototype.

Scene documents are data, not Python/QML/JavaScript. Extension nodes require
separate audited schemas; never execute model-authored strings.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from kira.domain.contracts import Json, KiraError

FORMAT = "kira.scene/1"
WIDTH, HEIGHT = 960, 540
MAX_SCENE_BYTES = 16_384
MAX_TEXT = 90
MAX_OBJECTS = 12
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _invalid(message: str) -> None:
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
