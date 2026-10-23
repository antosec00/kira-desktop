"""M1 scene language: closed schemas, precise edits and bounded Unicode."""

from copy import deepcopy

import pytest

from kira.domain.contracts import KiraError
from kira.domain.studio import MAX_SCENE_BYTES, patch_scene, poster_scene, validate_scene


def test_exact_editable_blue_brush_phrase_and_stable_other_layers():
    scene = poster_scene("Synthetic poster caption")
    text = next(item for item in scene["objects"] if item["id"] == "message")
    assert text["text"] == "Synthetic poster caption" and text["color"] == "#2467c9"
    before = deepcopy(scene)
    changed = patch_scene(scene, "message", {"color": "#182b68", "x": 140})
    assert scene == before  # immutable source, no flattening
    assert changed["objects"][:-1] == scene["objects"][:-1]
    assert changed["objects"][-1]["text"] == "Synthetic poster caption"
    assert changed["objects"][-1]["x"] == 140
    assert changed["objects"][-1]["color"] == "#182b68"
    assert validate_scene(changed) == changed


@pytest.mark.parametrize("value", [
    "", "A" * 91, "line\nbreak", "\x00", 4, None,
])
def test_invalid_poster_text_rejected(value):
    with pytest.raises(KiraError):
        poster_scene(value)


@pytest.mark.parametrize("key,value", [
    ("color", "file:///private.png"), ("color", "#abc"), ("size", True),
    ("x", float("inf")), ("x", -90000), ("x", 100000),
    ("text", "A" * 500), ("shader", "import os"),
])
def test_bad_patches_rejected(key, value):
    with pytest.raises(KiraError):
        patch_scene(poster_scene("Synthetic poster caption"), "message", {key: value})


def test_untrusted_programs_and_resource_bombs_never_parse_as_scenes():
    scene = poster_scene("Synthetic poster caption")
    tests = []
    for key in ("script", "qml", "javascript", "file", "url"):
        invalid = deepcopy(scene)
        invalid["objects"][0][key] = "dangerous code"
        tests.append(invalid)
    recursive = deepcopy(scene)
    recursive["objects"].append(recursive["objects"][0])
    tests.append(recursive)
    duplicates = deepcopy(scene)
    duplicates["objects"].append(deepcopy(duplicates["objects"][-1]))
    tests.append(duplicates)
    too_many = deepcopy(scene)
    too_many["objects"].extend(deepcopy(scene["objects"][1:]) * 15)
    tests.append(too_many)
    huge = deepcopy(scene)
    huge["objects"][-1]["text"] = "Z" * MAX_SCENE_BYTES
    tests.append(huge)
    for payload in tests:
        with pytest.raises(KiraError):
            validate_scene(payload)


def test_star_paths_are_data_and_unicode_text_is_preserved():
    scene = poster_scene("Città già più ♡")
    assert scene["objects"][-1]["text"] == "Città già più ♡"
    with pytest.raises(KiraError):
        patch_scene(scene, "star1", {"text": "injected"})
