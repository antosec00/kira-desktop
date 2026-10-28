"""Typed, private GUI appearance; no service or provider interaction."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any

from PySide6.QtCore import SLOT, QCoreApplication, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusMessage,
    QDBusPendingCallWatcher,
    QDBusServiceWatcher,
    QDBusVariant,
)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication

from kira.ui.presentation import refresh_ui, tr
from kira.ui.preferences import (
    APPEARANCE_DEFAULTS,
    valid_appearance,
    valid_appearance_option,
    valid_font_family,
    valid_style_value,
)
from kira.ui.qt_properties import qt_property

_STYLE_KEYS = {"shared", "light", "dark"}
# Sliders and spin boxes change these continuously. The first change is saved at
# once; further changes within the pause are applied live and saved together.
_CONTINUOUS_OPTIONS = {"contrast", "ui_size", "content_size", "code_size"}
_PERSIST_DELAY_MS = 300
_THEMES = {
    # Kira: anthracite ground, warm off-white in light mode, amber accent after the mascot.
    "kira": {
        True: ("#15181b", "#e9ecef", "#e0a06a"),
        False: ("#f6f5f2", "#1f2428", "#9a5b1d"),
    },
    "neutral": {
        True: ("#1c1c1f", "#ededf0", "#aaa5ff"),
        False: ("#fafafa", "#252529", "#6254b8"),
    },
    "warm": {
        True: ("#221d19", "#f1e7d9", "#e7ac74"),
        False: ("#faf5eb", "#352a22", "#94511e"),
    },
}
_PORTAL = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SETTINGS = "org.freedesktop.portal.Settings"
_MOTION_KEYS = (
    ("org.freedesktop.appearance", "reduced-motion"),
    ("org.gnome.desktop.interface", "enable-animations"),
    ("org.kde.kdeglobals.KDE", "AnimationDurationFactor"),
)


def _mix(first: str, second: str, amount: float) -> str:
    a, b = QColor(first), QColor(second)
    return QColor(*(round(x + (y - x) * amount) for x, y in zip(
        (a.red(), a.green(), a.blue()), (b.red(), b.green(), b.blue()), strict=True
    ))).name()


def _luminance(color: str) -> float:
    rgb = QColor(color)
    channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                for v in (rgb.redF(), rgb.greenF(), rgb.blueF())]
    return sum(value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))


def _readable_text(background: str) -> str:
    luminance = _luminance(background)
    return "#000000" if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else "#ffffff"


def palette(style: dict[str, str], dark: bool, contrast: int) -> dict[str, str]:
    base, text, accent = _THEMES[style["theme"]][dark]
    base, text, accent = (style["background"] or base, style["foreground"] or text,
                          style["accent"] or accent)
    shade = "#000000" if dark else "#ffffff"
    ink = "#ffffff" if dark else "#000000"
    accent_text = _readable_text(accent)
    accent_toward = "#ffffff" if accent_text == "#000000" else "#000000"
    colors = {
        # Three surface levels and three text levels carry the hierarchy.
        "background": base, "surface": _mix(base, text, 0.035),
        "surfaceAlt": _mix(base, text, 0.065), "surfaceRaised": _mix(base, text, 0.11),
        "sidebar": _mix(base, shade, 0.12),
        "text": text, "textStrong": _mix(text, ink, 0.55),
        "proseText": _mix(base, text, 0.88 if dark else 0.92),
        "textSecondary": _mix(base, text, 0.72 + contrast * 0.002),
        "muted": _mix(base, text, 0.55 + contrast * 0.003),
        "border": _mix(base, text, 0.12 + contrast * 0.0018),
        "accent": accent, "accentText": accent_text, "accentSoft": _mix(base, accent, 0.16),
        "accentHover": _mix(accent, accent_toward, 0.10), "accentPressed": _mix(accent, accent_toward, 0.20),
        "selection": accent, "selectedText": accent_text,
        "hover": _mix(base, text, 0.08), "pressed": _mix(base, text, 0.14),
        "disabled": _mix(base, text, 0.055), "disabledText": _mix(base, text, 0.40),
        "danger": "#f09595" if dark else "#b42338",
        "warning": "#efc77c" if dark else "#87560e",
        "success": "#87d5ab" if dark else "#246a42",
        "codeBackground": _mix(base, shade, 0.22), "codeText": text,
        "inlineCodeBackground": _mix(base, text, 0.095 if dark else 0.08),
        "inlineCodeText": _mix(accent, ink, 0.16) if dark else _mix(accent, "#000000", 0.24),
    }
    for name in ("danger", "warning", "success"):
        colors[name + "Background"] = _mix(base, colors[name], 0.12)
    return colors


def _unwrap(value: Any) -> Any:
    for _ in range(4):
        if not isinstance(value, QDBusVariant):
            return value
        value = value.variant()
    return None


class _MotionPortal(QObject):
    """Optional asynchronous, read-only portal settings with a false fallback."""

    changed = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.available = False
        self.reduced = False
        self._values: dict[tuple[str, str], bool] = {}
        self._pending: set[QDBusPendingCallWatcher] = set()
        self._generation = 0
        self._bus: QDBusConnection | None = None
        self._watcher: QDBusServiceWatcher | None = None
        # A functor-only singleShot can survive its Python QObject wrapper.
        # Owning the timer cancels deferred startup when the appearance is destroyed.
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self.start)
        self._start_timer.start(0)

    @Slot()
    def start(self) -> None:
        self._bus = QDBusConnection.sessionBus()
        if not self._bus.isConnected():
            return
        # PySide's binding accepts SLOT's str; its generated stub incorrectly asks for bytes.
        self._bus.connect(_PORTAL, _PORTAL_PATH, _SETTINGS, "SettingChanged", self,  # type: ignore[call-overload]
                          SLOT("_setting_changed(QString,QString,QDBusVariant)"))
        self._watcher = QDBusServiceWatcher(
            _PORTAL, self._bus, QDBusServiceWatcher.WatchModeFlag.WatchForOwnerChange, self
        )
        self._watcher.serviceOwnerChanged.connect(self._owner_changed)
        self._read()

    def _read(self) -> None:
        if self._bus is None:
            return
        generation = self._generation
        for namespace, key in _MOTION_KEYS:
            message = QDBusMessage.createMethodCall(_PORTAL, _PORTAL_PATH, _SETTINGS, "Read")
            message.setArguments([namespace, key])
            message.setAutoStartService(False)
            watcher = QDBusPendingCallWatcher(self._bus.asyncCall(message, 1500), self)
            self._pending.add(watcher)
            watcher.finished.connect(
                lambda reply, ns=namespace, name=key, version=generation:
                self._received(reply, ns, name, version)
            )

    def _received(self, watcher: QDBusPendingCallWatcher, namespace: str, key: str, generation: int) -> None:
        reply = watcher.reply()
        if (generation == self._generation
                and reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments()):
            self._accept(namespace, key, reply.arguments()[0])
        self._pending.discard(watcher)
        watcher.deleteLater()

    @Slot(str, str, str)
    def _owner_changed(self, _service: str, _old: str, owner: str) -> None:
        self._generation += 1
        self._values.clear()
        self._publish()
        if owner:
            self._read()

    @Slot(str, str, QDBusVariant)
    def _setting_changed(self, namespace: str, key: str, value: Any) -> None:
        self._accept(namespace, key, value)

    def _accept(self, namespace: str, key: str, value: Any) -> None:
        pair = (namespace, key)
        if pair not in _MOTION_KEYS:
            return
        value = _unwrap(value)
        reduced: bool | None = None
        if pair == _MOTION_KEYS[0] and type(value) is int and value in {0, 1}:
            reduced = value == 1
        elif pair == _MOTION_KEYS[1] and type(value) is bool:
            reduced = not value
        elif (pair == _MOTION_KEYS[2] and type(value) in {int, float}
              and math.isfinite(value) and value >= 0):
            reduced = value == 0
        if reduced is None:
            self._values.pop(pair, None)
        else:
            self._values[pair] = reduced
        self._publish()

    def _publish(self) -> None:
        reduced = next((self._values[key] for key in _MOTION_KEYS if key in self._values), False)
        available = bool(self._values)
        if (available, reduced) != (self.available, self.reduced):
            self.available, self.reduced = available, reduced
            self.changed.emit()


class Appearance(QObject):
    """QML appearance facade, persisted atomically with legacy GUI settings."""

    changed = Signal()

    def __init__(self, workspace: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._workspace = workspace
        self._error = ""
        self._revision = 0
        self._system_dark = True
        self._config: dict[str, Any] = {}
        self._colors: dict[str, str] = {}
        self._legacy_motion = False
        self._unsaved = False
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(_PERSIST_DELAY_MS)
        self._persist_timer.timeout.connect(self.flush)
        application = QCoreApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.flush)
        self._motion = _MotionPortal(self)
        self._motion.changed.connect(self._system_motion_changed)
        self._families = sorted(family for family in QFontDatabase.families() if valid_font_family(family))
        hints = QGuiApplication.styleHints()
        self._system_color_changed(hints.colorScheme())
        hints.colorSchemeChanged.connect(self._system_color_changed)
        workspace.preferencesChanged.connect(self._preferences_changed)
        self._preferences_changed()

    def _preferences_changed(self) -> None:
        if self._unsaved:
            # Another preference was saved first: write the newer live appearance
            # with it instead of letting the older saved one replace it.
            self.flush()
            return
        preferences = self._workspace.uiPreferences
        value = preferences.get("appearance")
        config = deepcopy(value if valid_appearance(value) else APPEARANCE_DEFAULTS)
        config["content_size"] = preferences.get("chat_font_size", 14)
        legacy_motion = preferences.get("reduce_motion") is True
        if config != self._config or legacy_motion != self._legacy_motion:
            self._config, self._legacy_motion = config, legacy_motion
            self._refresh()

    def _refresh(self) -> None:
        if not self._config:
            return
        self._colors = palette(self.style, self.dark, self._config["contrast"])
        self._revision += 1
        self.changed.emit()

    def _system_color_changed(self, scheme: Qt.ColorScheme) -> None:
        if scheme == Qt.ColorScheme.Unknown:
            dark = QGuiApplication.palette().window().color().lightness() < 128
        else:
            dark = scheme == Qt.ColorScheme.Dark
        if dark != self._system_dark:
            self._system_dark = dark
            if self._config.get("mode") == "system":
                self._refresh()

    def _system_motion_changed(self) -> None:
        self._refresh()

    @qt_property("QVariantMap", notify=changed)
    def values(self) -> dict[str, Any]:
        values = {key: value for key, value in self._config.items() if key not in _STYLE_KEYS}
        if values.get("motion") == "legacy":
            values["motion"] = "on" if self._legacy_motion else "off"
        return values

    @qt_property("QVariantMap", notify=changed)
    def style(self) -> dict[str, str]:
        key = ("dark" if self.dark else "light") if self._config["separate"] else "shared"
        return dict(self._config[key])

    @qt_property("QVariantMap", notify=changed)
    def colors(self) -> dict[str, str]:
        return dict(self._colors)

    def _font(self, role: str) -> QFont:
        style = self.style
        family = style[role + "_family"]
        if family == "interface":
            family = style["ui_family"]
        if family in {"system", "interface"}:
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        elif family == "monospace":
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            font.setStyleHint(QFont.StyleHint.Monospace)
        else:
            font = QFont(family)
        font.setPixelSize(self._config[role + "_size"])
        font.setWeight({"medium": QFont.Weight.Medium, "semibold": QFont.Weight.DemiBold,
                        "bold": QFont.Weight.Bold}.get(style[role + "_style"], QFont.Weight.Normal))
        font.setItalic(style[role + "_style"] == "italic")
        return font

    @qt_property(QFont, notify=changed)
    def uiFont(self) -> QFont:
        return self._font("ui")

    @qt_property(QFont, notify=changed)
    def contentFont(self) -> QFont:
        return self._font("content")

    @qt_property(QFont, notify=changed)
    def codeFont(self) -> QFont:
        return self._font("code")

    @qt_property(bool, notify=changed)
    def dark(self) -> bool:
        return self._system_dark if self._config.get("mode") == "system" else self._config.get("mode", "dark") == "dark"

    @qt_property(bool, notify=changed)
    def reduceMotion(self) -> bool:
        motion = self._config["motion"]
        return (self._motion.reduced if motion == "system" else self._legacy_motion
                if motion == "legacy" else motion == "on")

    @qt_property(bool, notify=changed)
    def motionSystemAvailable(self) -> bool:
        return self._motion.available

    @qt_property(bool, notify=changed)
    def pointerCursors(self) -> bool:
        return self._config["pointer_cursors"]

    @qt_property(bool, notify=changed)
    def fullWidth(self) -> bool:
        return self._config["full_width"]

    @qt_property(int, notify=changed)
    def revision(self) -> int:
        return self._revision

    @qt_property("QStringList", constant=True)  # type: ignore[arg-type]
    def fontFamilies(self) -> list[str]:
        return list(self._families)

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot()
    def retranslate(self) -> None:
        self._error = refresh_ui(self._error)
        self.changed.emit()

    def _set_error(self, error: str) -> None:
        if error != self._error:
            self._error = error
            self.changed.emit()

    @Slot()
    def flush(self) -> None:
        """Save live continuous changes now; a failure restores the saved appearance."""
        self._persist_timer.stop()
        if not self._unsaved:
            return
        self._unsaved = False
        if not self._persist(deepcopy(self._config)):
            self._config = {}
            self._preferences_changed()

    def _persist(self, config: dict[str, Any]) -> bool:
        if not valid_appearance(config):
            self._set_error(tr("Configurazione dell’aspetto non valida."))
            return False
        # This save carries every live change; its own notification must not flush again.
        self._unsaved = False
        motion = config["motion"]
        reduced = (self._motion.reduced if motion == "system" else self._legacy_motion
                   if motion == "legacy" else motion == "on")
        update = {"appearance": config, "chat_font_size": config["content_size"], "reduce_motion": reduced}
        if any(self._workspace.uiPreferences.get(key) != value for key, value in update.items()):
            self._workspace._save_preferences(update)
        if any(self._workspace.uiPreferences.get(key) != value for key, value in update.items()):
            self._set_error(tr("Aspetto non salvato. Controlla lo spazio disponibile e i permessi."))
            return False
        self._set_error("")
        return True

    @Slot(str, "QVariant")
    def setOption(self, key: str, value: Any) -> None:
        if (key == "motion" and value == "legacy") or not valid_appearance_option(key, value):
            self._set_error(tr("Opzione dell’aspetto non valida: ") + key)
            return
        config = deepcopy(self._config)
        if key == "separate" and value is True and not config["separate"]:
            config["light"] = deepcopy(config["shared"])
            config["dark"] = deepcopy(config["shared"])
        if key == "separate" and value is False and config["separate"]:
            config["shared"] = deepcopy(self.style)
        config[key] = value
        if key in _CONTINUOUS_OPTIONS and self._persist_timer.isActive():
            if config != self._config:
                self._config = config
                self._unsaved = True
                self._refresh()
            self._persist_timer.start()
            return
        if self._persist(config) and key in _CONTINUOUS_OPTIONS:
            self._persist_timer.start()

    @Slot(str, str)
    def setStyle(self, key: str, value: str) -> None:
        if not valid_style_value(key, value):
            self._set_error(tr("Stile non valido: ") + key)
            return
        config = deepcopy(self._config)
        target = ("dark" if self.dark else "light") if config["separate"] else "shared"
        config[target][key] = value
        self._persist(config)

    @Slot()
    def reset(self) -> None:
        config = deepcopy(APPEARANCE_DEFAULTS)
        config["motion"] = "off"
        self._persist(config)

    @Slot(result=str)
    def exportTheme(self) -> str:
        config = deepcopy(self._config)
        config["motion"] = self.values["motion"]
        return json.dumps({"format": "kira-desktop-appearance", "version": 1, "appearance": config},
                          ensure_ascii=False, indent=2)

    @Slot(str, result=bool)
    def importTheme(self, text: str) -> bool:
        def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            if len(text.encode("utf-8")) > 16384:
                raise ValueError("too large")
            value = json.loads(text, object_pairs_hook=unique_keys)
            if (not isinstance(value, dict) or set(value) != {"format", "version", "appearance"}
                    or value["format"] != "kira-desktop-appearance" or type(value["version"]) is not int
                    or value["version"] != 1 or not valid_appearance(value["appearance"])
                    or value["appearance"]["motion"] == "legacy"):
                raise ValueError("unsupported appearance")
        except (ValueError, RecursionError):
            self._set_error(tr("Tema non valido: usa un’esportazione dell’aspetto Kira (versione 1, massimo 16 KiB)."))
            return False
        return self._persist(deepcopy(value["appearance"]))

    @Slot()
    def copyTheme(self) -> None:
        QGuiApplication.clipboard().setText(self.exportTheme())
        self._set_error("")
