"""Small private GUI preferences; project data are always owned by the service."""

import json
import os
import re
import stat
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from kira.ui.language_catalog import supported_language

STYLE_DEFAULTS: dict[str, str] = {
    "theme": "kira", "accent": "", "background": "", "foreground": "",
    "ui_family": "Noto Sans", "content_family": "interface", "code_family": "monospace",
    "ui_style": "normal", "content_style": "normal", "code_style": "normal",
}
APPEARANCE_DEFAULTS: dict[str, Any] = {
    "mode": "dark", "separate": False,
    "ui_size": 13, "content_size": 14, "code_size": 12,
    "motion": "legacy", "sidebar_translucent": False, "contrast": 45,
    "smart_punctuation": False, "full_width": False,
    "diff_indicators": "color", "pointer_cursors": True,
    "shared": deepcopy(STYLE_DEFAULTS), "light": deepcopy(STYLE_DEFAULTS),
    "dark": deepcopy(STYLE_DEFAULTS),
}


def valid_font_family(value: object) -> bool:
    """Keep font names safe for both QFont and quoted generated CSS."""
    return (isinstance(value, str) and 1 <= len(value) <= 128 and value == value.strip()
            and all(char.isalnum() or char in " _-." for char in value))


def valid_style_value(key: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    if key == "theme":
        return value in {"kira", "neutral", "warm"}
    if key in {"accent", "background", "foreground"}:
        return value == "" or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is not None
    if key in {"ui_family", "content_family", "code_family"}:
        return valid_font_family(value)
    if key in {"ui_style", "content_style", "code_style"}:
        return value in {"normal", "medium", "semibold", "bold", "italic"}
    return False


def valid_appearance_option(key: str, value: object) -> bool:
    if key in {"separate", "sidebar_translucent", "smart_punctuation", "full_width", "pointer_cursors"}:
        return type(value) is bool
    ranges = {"ui_size": (12, 18), "content_size": (12, 20), "code_size": (10, 20), "contrast": (0, 100)}
    if key in ranges:
        low, high = ranges[key]
        return type(value) is int and low <= value <= high
    enums = {"mode": {"light", "dark", "system"}, "motion": {"legacy", "system", "on", "off"},
             "diff_indicators": {"color", "signs"}}
    return isinstance(value, str) and value in enums.get(key, set())


def valid_appearance(value: object) -> bool:
    if not isinstance(value, dict) or value.keys() != APPEARANCE_DEFAULTS.keys():
        return False
    for key, setting in value.items():
        if key in {"shared", "light", "dark"}:
            if (not isinstance(setting, dict) or setting.keys() != STYLE_DEFAULTS.keys()
                    or not all(valid_style_value(name, field) for name, field in setting.items())):
                return False
        elif not valid_appearance_option(key, setting):
            return False
    return True

DEFAULTS = {
    "language": "it",
    "onboarding_complete": False,
    "chatgpt_plan_welcome_seen": False,
    "density": "comfortable",
    "model_controls_visible": False,
    "resume_hint_visible": True,
    "sidebar_mode": "full",
    "tool_labels": False,
    "show_reasoning_summaries": True,
    "details_visible": False,
    "chat_font_size": 14,
    "reduce_motion": False,
    "confirm_close": "active",
    "file_opener": "system",
    "default_permission": "read_only",
    "check_updates": True,
    "skipped_update_version": "",
    "selected_sessions": {},
    "appearance": deepcopy(APPEARANCE_DEFAULTS),
}


def valid_preference(key: str, value: object) -> bool:
    """Accept typed presentation values; bool is never a numeric font size."""
    if key in {"onboarding_complete", "chatgpt_plan_welcome_seen", "model_controls_visible", "resume_hint_visible", "details_visible", "reduce_motion", "show_reasoning_summaries", "tool_labels", "check_updates"}:
        return type(value) is bool
    if key == "skipped_update_version":
        return isinstance(value, str) and (value == "" or re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", value) is not None)
    if key == "language":
        return supported_language(value)
    if key == "appearance":
        return valid_appearance(value)
    if key == "selected_sessions":
        return (isinstance(value, dict) and len(value) <= 32
                and all(isinstance(k, str) and isinstance(v, str)
                        and 1 <= len(k) <= 64 and 1 <= len(v) <= 64
                        for k, v in value.items()))
    if key == "chat_font_size":
        return type(value) is int and 12 <= value <= 20
    enums = {
        "density": {"comfortable", "compact"},
        "sidebar_mode": {"full", "rail", "hidden"},
        "confirm_close": {"active", "always", "never"},
        "file_opener": {"system", "vscode", "kira"},
        "default_permission": {"read_only", "project_write", "trusted_host", "full_access"},
    }
    return isinstance(value, str) and value in enums.get(key, set())


def read_preferences(directory: Path) -> dict[str, object]:
    path = directory / "ui.json"
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            return deepcopy(DEFAULTS)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return deepcopy(DEFAULTS)
        return {key: value[key] if key in value and valid_preference(key, value[key]) else deepcopy(default)
                for key, default in DEFAULTS.items()}
    except (OSError, ValueError, AttributeError):
        return deepcopy(DEFAULTS)


def write_preferences(directory: Path, value: dict[str, object]) -> None:
    """Atomic private write; diagnostics are English keys the interface explains."""
    if directory.is_symlink():
        raise ValueError("Preferences directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_uid != os.getuid():
        raise PermissionError("Preferences directory must belong to this user")
    directory.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".ui-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, directory / "ui.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
