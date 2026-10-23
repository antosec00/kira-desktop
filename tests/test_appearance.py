"""Synthetic GUI appearance checks; no profile, provider or desktop settings writes."""

import json
from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtDBus import QDBusVariant
from PySide6.QtGui import QFont

from kira.domain.paths import Paths
from kira.ui.appearance import Appearance, _luminance, _MotionPortal
from kira.ui.controller import Workspace
from kira.ui.preferences import (
    APPEARANCE_DEFAULTS,
    DEFAULTS,
    read_preferences,
    valid_appearance,
    valid_font_family,
)


@pytest.fixture
def appearance(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(_MotionPortal, "start", lambda self: None)
    workspace = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))

    def forbidden(*args, **kwargs):
        pytest.fail("Appearance must never send IPC or provider requests")

    monkeypatch.setattr(workspace.client, "request", forbidden)
    view = Appearance(workspace, workspace)
    yield view
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("destroy_parent", [False, True])
def test_destroyed_appearance_cancels_queued_portal_start(
    qapp, tmp_path, monkeypatch, destroy_parent
):
    starts = []
    monkeypatch.setattr(_MotionPortal, "start", lambda self: starts.append(self))
    workspace = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    view = Appearance(workspace, workspace)
    if destroy_parent:
        workspace.close()
        workspace.deleteLater()
    else:
        view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert starts == []  # No desktop bus lookup can run after destruction.
    if not destroy_parent:
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_defaults_and_real_font_catalog_preserve_original_theme(appearance):
    assert appearance.dark and not appearance.fullWidth and appearance.pointerCursors
    assert not appearance.reduceMotion
    assert appearance.values["motion"] == "off"
    assert appearance.colors["background"] == "#15181b"
    assert appearance.colors["text"] == "#e9ecef"
    assert appearance.colors["accent"] == "#e0a06a"
    assert [font.pixelSize() for font in (appearance.uiFont, appearance.contentFont, appearance.codeFont)] == [13, 14, 12]
    assert appearance.uiFont.family() == "Noto Sans"
    assert appearance.contentFont.family() == appearance.uiFont.family()
    assert appearance.fontFamilies and all(valid_font_family(name) for name in appearance.fontFamilies)
    assert not appearance._workspace.paths.db.exists()
    assert not (appearance._workspace.paths.config / "ui.json").exists()
    values, style, colors = appearance.values, appearance.style, appearance.colors
    values["ui_size"], style["accent"], colors["text"] = 99, "unsafe", "unsafe"
    assert appearance.values["ui_size"] == 13 and appearance.style["accent"] == ""
    assert appearance.colors["text"] == "#e9ecef"


@pytest.mark.parametrize("key,value", [
    ("ui_size", True), ("ui_size", 11), ("ui_size", 19), ("content_size", 20.0),
    ("content_size", 21), ("code_size", 9), ("code_size", 21), ("contrast", -1),
    ("contrast", 101), ("mode", "invalid"), ("separate", 1), ("full_width", "true"),
    ("motion", "legacy"), ("diff_indicators", True), ("unknown", "injected"),
])
def test_invalid_option_does_not_mutate_or_write(appearance, key, value):
    original = appearance._workspace.uiPreferences
    revision = appearance.revision
    appearance.setOption(key, value)
    assert appearance._workspace.uiPreferences == original
    assert appearance.revision == revision and appearance.error
    assert not (appearance._workspace.paths.config / "ui.json").exists()


@pytest.mark.parametrize("key,value", [
    ("accent", "red"), ("background", "#123"), ("foreground", "#12345678"),
    ("theme", "injected"), ("ui_family", "Font; color:red"),
    ("content_family", 'Font" onload="evil'), ("code_family", "Font\nName"),
    ("code_family", "x" * 129), ("ui_family", "/private/font/path"),
    ("ui_style", "url(injected)"), ("code_style", "oblique"), ("unknown", "value"),
])
def test_invalid_style_cannot_enter_fonts_or_generated_css(appearance, key, value):
    original = appearance.exportTheme()
    appearance.setStyle(key, value)
    assert appearance.exportTheme() == original and appearance.error


def test_strict_nested_preference_rejects_unknown_or_incomplete_values(tmp_path):
    original = deepcopy(APPEARANCE_DEFAULTS)
    assert valid_appearance(original)
    for changed in ({**original, "account": "synthetic"},
                    {key: value for key, value in original.items() if key != "light"},
                    {**original, "shared": {**original["shared"], "api_key": "synthetic"}}):
        assert not valid_appearance(changed)
        (tmp_path / "ui.json").write_text(json.dumps({"appearance": changed}))
        assert read_preferences(tmp_path)["appearance"] == APPEARANCE_DEFAULTS
    loaded = read_preferences(tmp_path)
    loaded["appearance"]["shared"]["accent"] = "changed"
    assert DEFAULTS["appearance"]["shared"]["accent"] == ""


def test_legacy_settings_remain_effective_and_new_sizes_write_atomically(appearance):
    workspace = appearance._workspace
    workspace.setUiPreference("chat_font_size", 19)
    workspace.setUiPreference("reduce_motion", True)
    assert appearance.contentFont.pixelSize() == 19 and appearance.values["content_size"] == 19
    assert appearance.reduceMotion and appearance.values["motion"] == "on"
    signals = []
    workspace.preferencesChanged.connect(lambda: signals.append(workspace.uiPreferences))
    appearance.setOption("content_size", 16)
    assert len(signals) == 1
    assert signals[0]["chat_font_size"] == signals[0]["appearance"]["content_size"] == 16
    appearance.setOption("motion", "off")
    assert not appearance.reduceMotion and workspace.uiPreferences["reduce_motion"] is False
    assert read_preferences(workspace.paths.config) == workspace.uiPreferences
    assert (workspace.paths.config / "ui.json").stat().st_mode & 0o777 == 0o600


def test_changes_are_live_signalled_and_repeated_values_do_not_write(appearance):
    notifications = []
    appearance.changed.connect(lambda: notifications.append(appearance.revision))
    before = appearance.revision
    appearance.setOption("mode", "light")
    assert not appearance.dark and appearance.colors["background"] == "#f6f5f2"
    assert notifications == [before + 1]
    appearance.setOption("mode", "light")
    assert notifications == [before + 1]
    appearance.setStyle("ui_style", "semibold")
    appearance.setStyle("code_style", "italic")
    appearance.setOption("code_size", 20)
    assert appearance.uiFont.weight() == QFont.Weight.DemiBold
    assert appearance.codeFont.italic() and appearance.codeFont.pixelSize() == 20


def test_separate_styles_are_isolated_and_toggling_preserves_current_style(appearance):
    appearance.setStyle("accent", "#123456")
    appearance.setStyle("ui_family", "DejaVu Sans")
    shared = appearance.style
    appearance.setOption("separate", True)
    config = appearance._workspace.uiPreferences["appearance"]
    assert config["light"] == config["dark"] == shared
    appearance.setStyle("accent", "#abcdef")
    appearance.setOption("mode", "light")
    assert appearance.style == shared
    appearance.setStyle("background", "#eeeeee")
    appearance.setOption("mode", "dark")
    assert appearance.style["accent"] == "#abcdef" and appearance.style["background"] == ""
    dark_style = appearance.style
    appearance.setOption("separate", False)
    assert appearance.style == dark_style
    assert appearance._workspace.uiPreferences["appearance"]["shared"] == dark_style


def test_system_color_scheme_switches_live_without_persisting_system_state(appearance):
    appearance.setOption("mode", "system")
    persisted = appearance._workspace.uiPreferences
    appearance._system_color_changed(Qt.ColorScheme.Light)
    assert not appearance.dark
    revision = appearance.revision
    appearance._system_color_changed(Qt.ColorScheme.Dark)
    assert appearance.dark and appearance.revision == revision + 1
    appearance._system_color_changed(Qt.ColorScheme.Dark)
    assert appearance.revision == revision + 1
    assert appearance._workspace.uiPreferences == persisted


def test_motion_portal_supports_updates_precedence_and_safe_unavailable_fallback(appearance):
    portal = appearance._motion
    appearance.setOption("motion", "system")
    assert not appearance.reduceMotion and not appearance.motionSystemAvailable
    portal._accept("org.gnome.desktop.interface", "enable-animations", QDBusVariant(False))
    assert appearance.reduceMotion and appearance.motionSystemAvailable
    portal._accept("org.freedesktop.appearance", "reduced-motion", QDBusVariant(QDBusVariant(0)))
    assert not appearance.reduceMotion
    portal._accept("org.freedesktop.appearance", "reduced-motion", "bad-type")
    assert appearance.reduceMotion
    portal._accept("org.gnome.desktop.interface", "enable-animations", None)
    assert not appearance.reduceMotion and not appearance.motionSystemAvailable
    portal._accept("org.kde.kdeglobals.KDE", "AnimationDurationFactor", 0.0)
    assert appearance.reduceMotion
    portal._accept("org.kde.kdeglobals.KDE", "AnimationDurationFactor", float("nan"))
    assert not appearance.reduceMotion and not appearance.motionSystemAvailable
    appearance.setOption("motion", "on")
    portal._accept("org.gnome.desktop.interface", "enable-animations", True)
    assert appearance.reduceMotion  # Explicit user setting wins over the system.


@pytest.mark.parametrize("accent", ["#000000", "#ffffff", "#ff0000", "#123456", "#88ff88", "#666666", "#777777"])
def test_custom_accent_preserves_readable_button_and_selection_text(appearance, accent):
    appearance.setStyle("accent", accent)
    for background, foreground in (("accent", "accentText"), ("selection", "selectedText"),
                                   ("accentHover", "accentText"), ("accentPressed", "accentText")):
        a, b = sorted((_luminance(appearance.colors[background]), _luminance(appearance.colors[foreground])))
        assert (b + 0.05) / (a + 0.05) >= 4.5


def test_contrast_adjusts_muted_text_and_borders(appearance):
    appearance.setOption("contrast", 0)
    low = appearance.colors
    appearance.setOption("contrast", 100)
    high = appearance.colors
    assert _luminance(high["muted"]) > _luminance(low["muted"])
    assert _luminance(high["border"]) > _luminance(low["border"])
    assert high["background"] == low["background"] and high["text"] == low["text"]


def test_export_import_roundtrip_is_atomic_and_contains_only_appearance(appearance):
    workspace = appearance._workspace
    workspace.setUiPreference("density", "compact")
    workspace.setUiPreference("selected_sessions", {"synthetic-project": "synthetic-chat"})
    appearance.setOption("mode", "light")
    appearance.setOption("content_size", 17)
    appearance.setStyle("theme", "warm")
    exported = appearance.exportTheme()
    envelope = json.loads(exported)
    assert set(envelope) == {"format", "version", "appearance"}
    assert "synthetic-project" not in exported and str(workspace.paths.config) not in exported
    appearance.reset()
    assert appearance.dark and appearance.contentFont.pixelSize() == 14
    notifications = []
    workspace.preferencesChanged.connect(lambda: notifications.append(True))
    assert appearance.importTheme(exported)
    assert notifications == [True]
    assert appearance.exportTheme() == exported
    assert workspace.uiPreferences["density"] == "compact"
    assert workspace.uiPreferences["selected_sessions"] == {"synthetic-project": "synthetic-chat"}


@pytest.mark.parametrize("mutation", ["unknown", "invalid-style", "bool-version", "legacy", "duplicate", "large", "broken"])
def test_invalid_import_is_rejected_without_partial_application(appearance, mutation):
    original = appearance._workspace.uiPreferences
    data = json.loads(appearance.exportTheme())
    if mutation == "unknown":
        data["credentials"] = "synthetic"
    elif mutation == "invalid-style":
        data["appearance"]["dark"]["ui_family"] = "Font; background:url(injected)"
    elif mutation == "bool-version":
        data["version"] = True
    elif mutation == "legacy":
        data["appearance"]["motion"] = "legacy"
    text = json.dumps(data)
    if mutation == "duplicate":
        text = text.replace('"version": 1', '"version": 1, "version": 1')
    elif mutation == "large":
        text += " " * 16385
    elif mutation == "broken":
        text = "{"
    assert not appearance.importTheme(text)
    assert appearance.error and appearance._workspace.uiPreferences == original
    assert not (appearance._workspace.paths.config / "ui.json").exists()


def test_failed_write_retains_state_and_failed_import_reports_error(appearance, monkeypatch):
    appearance.setStyle("theme", "neutral")
    original = appearance._workspace.uiPreferences
    persisted = (appearance._workspace.paths.config / "ui.json").read_bytes()
    revision = appearance.revision

    def fail(*args):
        raise OSError("synthetic full disk")

    monkeypatch.setattr("kira.ui.controller.write_preferences", fail)
    data = json.loads(appearance.exportTheme())
    data["appearance"]["mode"] = "light"
    assert not appearance.importTheme(json.dumps(data))
    assert appearance.error and appearance._workspace.uiPreferences == original
    assert appearance.revision == revision and appearance.dark
    assert (appearance._workspace.paths.config / "ui.json").read_bytes() == persisted
