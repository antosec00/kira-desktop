"""Application-local translation with engine-owned Qt translators and JSON data.

    Only application-authored strings belong in tr()/translate_ui(). User text,
    provider output, tool results and stored project data remain literal.
"""

from __future__ import annotations

import re
import warnings
import weakref
from dataclasses import dataclass
from subprocess import SubprocessError
from typing import Any

from PySide6.QtCore import QCoreApplication, QLocale, QObject, QTranslator, Signal, Slot
from PySide6.QtQml import QQmlEngine
from shiboken6 import isValid

from kira.ui.language_catalog import (
    CatalogError,
    available_languages,
    load_catalog,
    native_labels,
    supported_language,
)
from kira.ui.qt_properties import qt_property


def _safe_catalog(code: str) -> dict[str, str]:
    try:
        return load_catalog(code)
    except CatalogError as error:
        warnings.warn(str(error), RuntimeWarning, stacklevel=2)
        return {}


def _native_translator(code: str, parent: QObject, *, strict: bool = False) -> tuple[QTranslator, dict[str, str]]:
    # Qt's QML loader can translate on a worker while the GUI thread is inside a
    # Python-triggered synchronous load. A Python QTranslator virtual method
    # deadlocks on the GIL there; the actual translator must stay entirely native.
    from kira.ui.translation_compiler import compiled_catalog

    translator = QTranslator(parent)
    try:
        catalog = load_catalog(code) if strict else _safe_catalog(code)
        if not catalog:
            return translator, {}
        if not translator.load(str(compiled_catalog(code, catalog, native_labels(code)))):
            raise ValueError("Native catalog could not be loaded")
    except (OSError, ValueError, RuntimeError, SubprocessError) as error:
        if strict:
            translator.deleteLater()
            raise
        warnings.warn(f"Native translation unavailable for {code}: {type(error).__name__}",
                      RuntimeWarning, stacklevel=2)
        return translator, {}
    return translator, catalog


@dataclass
class _Registration:
    translator: weakref.ReferenceType[QTranslator]
    owner: weakref.ReferenceType[Localization]
    code: str
    catalog: dict[str, str]


_registrations: dict[object, _Registration] = {}
_order: list[object] = []
_installed: weakref.ReferenceType[QTranslator] | None = None


def current_language() -> str:
    return _registrations[_order[-1]].code if _order else "it"


def locale() -> QLocale:
    """A formatter locale; never changes QLocale's default or the host locale."""
    return QLocale(current_language())


def _effective_catalog() -> dict[str, str]:
    return _registrations[_order[-1]].catalog if _order else _safe_catalog("it")


def translate_ui(text: str) -> str:
    """Translate an exact known application label, never substrings or payloads."""
    return _effective_catalog().get(text, text)


def tr(source: str, /, **values: Any) -> str:
    translated = (QCoreApplication.translate("Kira", source)
                  if _order and QCoreApplication.instance() is not None else translate_ui(source))
    if not values:
        return translated
    try:
        return translated.format(**values)
    except (KeyError, IndexError, ValueError, AttributeError):
        # A malformed optional catalog cannot break the UI or interpolate a
        # translated variable value. The original application template is safe.
        try:
            return source.format(**values)
        except (KeyError, IndexError, ValueError, AttributeError):
            return source


def _refresh_registered() -> None:
    for token in list(_order):
        registration = _registrations.get(token)
        owner = registration.owner() if registration is not None else None
        if owner is not None and isValid(owner) and isValid(owner._engine):
            owner._refresh()


def _install_active() -> None:
    global _installed
    previous = _installed() if _installed is not None else None
    current = _registrations[_order[-1]].translator() if _order else None
    if previous is current:
        return
    if QCoreApplication.instance() is None:
        _installed = None
        return
    if previous is not None and isValid(previous):
        QCoreApplication.removeTranslator(previous)
    _installed = None
    # Install only one native translator. Missing English entries must fall back
    # to their English Qt source, never to an earlier engine's Italian catalog.
    if current is not None and isValid(current) and not current.isEmpty():
        QCoreApplication.installTranslator(current)
        _installed = weakref.ref(current)


def _unregister(token: object) -> None:
    registration = _registrations.pop(token, None)
    if registration is None:
        return
    _order.remove(token)
    _install_active()
    _refresh_registered()


class Localization(QObject):
    """One GUI language preference; only the owning engine controls its lifetime."""

    changed = Signal()

    def __init__(self, workspace: Any, engine: QQmlEngine, parent: QObject | None = None) -> None:
        super().__init__(parent if parent is not None else engine)
        self._workspace = workspace
        self._engine = engine
        self._revision = 0
        self._prepared: tuple[str, QTranslator, dict[str, str]] | None = None
        code = workspace.uiPreferences.get("language", "it")
        self._language = code if supported_language(code) else "it"
        self._translator, catalog = _native_translator(self._language, self)
        self._token = object()
        _registrations[self._token] = _Registration(
            weakref.ref(self._translator), weakref.ref(self), self._language, catalog
        )
        _order.append(self._token)
        token = self._token
        # Context-free callbacks retain only a token. They remain safe even when
        # engine/owner destruction happens before a queued Qt language event.
        engine.destroyed.connect(lambda *_: _unregister(token))
        self.destroyed.connect(lambda *_: _unregister(token))
        if isinstance(workspace, QObject):
            workspace.destroyed.connect(lambda *_: _unregister(token))
        workspace.preferencesChanged.connect(self._preferences_changed)
        if QCoreApplication.instance() is None:
            _unregister(token)
            raise RuntimeError("Localization requires an active Qt application")
        _install_active()
        _refresh_registered()

    def _preferences_changed(self) -> None:
        if self._token not in _registrations:
            return
        code = self._workspace.uiPreferences.get("language", "it")
        code = code if supported_language(code) else "it"
        if code == self._language:
            return
        if self._prepared is not None and self._prepared[0] == code:
            _, translator, catalog = self._prepared
            self._prepared = None
        else:
            translator, catalog = _native_translator(code, self)
        previous = self._translator
        self._translator = translator
        self._language = code
        _registrations[self._token].code = code
        _registrations[self._token].catalog = catalog
        _registrations[self._token].translator = weakref.ref(translator)
        _order.remove(self._token)
        _order.append(self._token)
        _install_active()
        previous.deleteLater()
        _refresh_registered()

    def _refresh(self) -> None:
        self._engine.retranslate()
        callback = getattr(self._workspace, "retranslate", None)
        if callable(callback) and (not isinstance(self._workspace, QObject) or isValid(self._workspace)):
            callback()
        self._revision += 1
        self.changed.emit()

    @qt_property(str, notify=changed)
    def language(self) -> str:
        return self._language

    @qt_property("QVariantList", notify=changed)
    def languages(self) -> list[dict[str, str]]:
        return available_languages()

    @qt_property(int, notify=changed)
    def revision(self) -> int:
        return self._revision

    @Slot(str)
    def setLanguage(self, code: str) -> None:
        if self._token not in _registrations:
            return
        if not supported_language(code) or code == self._language:
            self._workspace.setUiPreference("language", code)
            return
        # Native preparation must succeed before the one atomic preference write.
        # The synchronous preferencesChanged callback consumes this exact instance.
        try:
            translator, catalog = _native_translator(code, self, strict=True)
        except (OSError, ValueError, RuntimeError, SubprocessError):
            from kira.ui.presentation import UiText

            message = UiText("Impossibile caricare la lingua selezionata.")
            report = getattr(self._workspace, "_problem", None)
            if callable(report):
                report(message)
            else:
                warnings.warn(message, RuntimeWarning, stacklevel=2)
            return
        self._prepared = (code, translator, catalog)
        try:
            self._workspace.setUiPreference("language", code)
        finally:
            if self._prepared is not None:
                self._prepared[1].deleteLater()
                self._prepared = None

    @Slot(str, "QVariantList", result=str)
    def format(self, template: str, values: list[Any]) -> str:
        """Replace original Qt-style slots once; inserted values stay literal."""
        def replace(match: re.Match[str]) -> str:
            index = int(match.group(1)) - 1
            return str(values[index]) if index < len(values) else match.group(0)

        return re.sub(r"%([1-9][0-9]?)(?![0-9])", replace, template)
