"""Local catalog, persistence, QML and translator-lifetime checks with synthetic data."""

import gc
import json
import subprocess
import sys
from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Signal
from PySide6.QtQml import QQmlComponent, QQmlEngine
from shiboken6 import isValid

from kira.ui import i18n, language_catalog
from kira.ui.i18n import Localization, current_language, locale, tr, translate_ui
from kira.ui.language_catalog import CatalogError, load_catalog, load_languages, supported_language
from kira.ui.preferences import DEFAULTS, read_preferences, valid_preference, write_preferences


class WorkspaceFixture(QObject):
    preferencesChanged = Signal()

    def __init__(self, directory, language="it"):
        super().__init__()
        self.directory = directory
        self._preferences = {**deepcopy(DEFAULTS), "language": language}
        self.refreshes = 0
        self.fail_write = False
        self.provider = {"model": "synthetic-model", "effort": "unchanged"}

    @property
    def uiPreferences(self):
        return deepcopy(self._preferences)

    def setUiPreference(self, key, value):
        if not valid_preference(key, value) or self.fail_write or self._preferences[key] == value:
            return
        proposed = {**self._preferences, key: value}
        write_preferences(self.directory, proposed)
        self._preferences = proposed
        self.preferencesChanged.emit()

    def retranslate(self):
        self.refreshes += 1

    def request(self, *args, **kwargs):
        pytest.fail("Language changes must not call a provider or service")


@pytest.fixture
def catalog_root(tmp_path, monkeypatch):
    original = language_catalog.CATALOG_ROOT
    directory = tmp_path / "translations"
    (directory / "it").mkdir(parents=True)
    (directory / "en").mkdir()
    for relative in ("languages.json", "it/qt.json", "en/core.json"):
        (directory / relative).write_bytes((original / relative).read_bytes())
    (directory / "en/fixture.json").write_text(json.dumps({
        "Lingua": "Language",
        "Progetto: {name}": "Project: {name}",
        "Creato": "Created",
        "Valore {value}": "Value {wrong_placeholder}",
    }))
    monkeypatch.setattr(language_catalog, "CATALOG_ROOT", directory)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return directory


@pytest.fixture
def factory(qapp, tmp_path, catalog_root):
    created = []

    def create(language="it"):
        workspace = WorkspaceFixture(tmp_path / f"profile-{len(created)}", language)
        engine = QQmlEngine()
        localization = Localization(workspace, engine)
        engine.rootContext().setContextProperty("localization", localization)
        created.append((workspace, engine, localization))
        return workspace, engine, localization

    yield create
    for workspace, engine, _localization in reversed(created):
        if isValid(engine):
            engine.deleteLater()
        if isValid(workspace):
            workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def qml_window(engine):
    component = QQmlComponent(engine)
    component.setData(b"""
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            visible: true; width: 320; height: 200
            property string label: qsTr("Lingua")
            property string literalData: "Lingua"
            property string formatted: localization.format("%1/%2", ["%2", "literal tail"])
            property string cancelLabel: buttons.standardButton(DialogButtonBox.Cancel).text
            property string closeLabel: buttons.standardButton(DialogButtonBox.Close).text
            DialogButtonBox { id: buttons; standardButtons: DialogButtonBox.Cancel | DialogButtonBox.Close }
        }
    """, QUrl())
    window = component.create()
    assert window is not None, component.errors()
    QObject.setParent(window, engine)
    QCoreApplication.processEvents()
    return window


def test_pure_helpers_default_to_italian_without_application():
    code = """
from PySide6.QtCore import QCoreApplication
from kira.ui.i18n import current_language, tr, locale
assert QCoreApplication.instance() is None
assert current_language() == 'it'
assert tr('Progetto: {name}', name='Synthetic') == 'Progetto: Synthetic'
assert tr('Cancel') == 'Annulla'
assert locale().name().startswith('it')
assert QCoreApplication.instance() is None
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)


def test_live_qml_and_native_buttons_follow_language_without_touching_literals(factory):
    workspace, engine, localization = factory()
    window = qml_window(engine)
    assert window.property("label") == "Lingua" and window.property("cancelLabel") == "Annulla"
    assert window.property("closeLabel") == "Chiudi"
    assert QCoreApplication.translate("QPlatformTheme", "Save") == "Salva"
    before = deepcopy(workspace.provider)
    revision = localization.revision
    localization.setLanguage("en")
    QCoreApplication.processEvents()
    assert localization.language == current_language() == "en"
    assert localization.revision == revision + 1
    assert window.property("label") == "Language"
    assert window.property("literalData") == "Lingua"
    assert window.property("cancelLabel") == "Cancel" and window.property("closeLabel") == "Close"
    assert QCoreApplication.translate("QPlatformTheme", "Save") == "Save"
    assert tr("Progetto: {name}", name="Lingua") == "Project: Lingua"
    assert tr("Literal {source}", source="source value") == "Literal source value"
    assert tr("Valore {value}", value="literal") == "Valore literal"
    assert translate_ui("Creato") == "Created"
    assert translate_ui("User text: Creato") == "User text: Creato"
    assert workspace.provider == before and workspace.refreshes >= 2
    assert read_preferences(workspace.directory)["language"] == "en"
    assert (workspace.directory / "ui.json").stat().st_mode & 0o777 == 0o600
    assert locale().name().startswith("en")
    localization.setLanguage("it")
    QCoreApplication.processEvents()
    assert window.property("cancelLabel") == "Annulla" and window.property("label") == "Lingua"


@pytest.mark.parametrize("value", ["fr", "EN", "../../en", "en/../it", "", 1, True, None])
def test_unknown_or_malformed_language_is_rejected(value, catalog_root):
    assert not supported_language(value)
    assert not valid_preference("language", value)


def test_invalid_and_failed_preference_writes_leave_active_translator_unchanged(factory):
    workspace, _engine, localization = factory()
    before = localization.revision
    localization.setLanguage("unsupported")
    assert localization.language == "it" and localization.revision == before
    assert not workspace.directory.exists()
    workspace.fail_write = True
    localization.setLanguage("en")
    assert localization.language == "it" and current_language() == "it"
    assert localization.revision == before and not workspace.directory.exists()


def test_failed_native_preparation_does_not_persist_or_apply_language(factory, monkeypatch):
    workspace, engine, localization = factory()
    window = qml_window(engine)
    revision = localization.revision

    def fail(*args):
        raise RuntimeError("synthetic compiler failure")

    monkeypatch.setattr("kira.ui.translation_compiler.compiled_catalog", fail)
    with pytest.warns(RuntimeWarning, match="Impossibile caricare"):
        localization.setLanguage("en")
    assert localization.language == current_language() == "it"
    assert workspace.uiPreferences["language"] == "it" and not workspace.directory.exists()
    assert localization.revision == revision
    assert window.property("label") == "Lingua" and window.property("cancelLabel") == "Annulla"


def test_multi_argument_labels_do_not_interpret_placeholders_in_literal_values(factory):
    _workspace, engine, localization = factory("en")
    assert localization.format("%1/%2", ["%2", "literal tail"]) == "%2/literal tail"
    assert localization.format("%2 — %1 — %2", ["{source}", "100%1"]) == "100%1 — {source} — 100%1"
    assert localization.format("%1 %3 %100", ["literal"]) == "literal %3 %100"
    assert qml_window(engine).property("formatted") == "%2/literal tail"


def test_missing_optional_catalog_falls_back_to_source_and_english_native_buttons(factory, catalog_root):
    for path in (catalog_root / "en").glob("*.json"):
        path.unlink()
    first_workspace, first_engine, _first = factory("it")
    second_workspace, second_engine, second = factory("en")
    window = qml_window(second_engine)
    assert second.language == "en" and window.property("label") == "Lingua"
    assert window.property("cancelLabel") == "Cancel"
    assert tr("Unknown literal") == "Unknown literal"
    assert first_workspace.provider == second_workspace.provider
    assert isValid(first_engine)


def test_new_language_only_needs_registry_entry_and_catalog(factory, catalog_root):
    registry = json.loads((catalog_root / "languages.json").read_text())
    registry["languages"].append({"code": "fr", "name": "Français"})
    (catalog_root / "languages.json").write_text(json.dumps(registry))
    (catalog_root / "fr").mkdir()
    (catalog_root / "fr/core.json").write_text(json.dumps({"Lingua": "Langue", "Cancel": "Annuler"}))
    workspace, engine, localization = factory()
    assert valid_preference("language", "fr")
    assert {"code": "fr", "name": "Français"} in localization.languages
    window = qml_window(engine)
    localization.setLanguage("fr")
    QCoreApplication.processEvents()
    assert window.property("label") == "Langue" and window.property("cancelLabel") == "Annuler"
    assert read_preferences(workspace.directory)["language"] == "fr"


def test_catalog_conflicting_duplicates_are_rejected_and_runtime_falls_back(factory, catalog_root):
    (catalog_root / "en/conflict.json").write_text(json.dumps({"Lingua": "Conflicting translation"}))
    with pytest.raises(CatalogError, match="Conflicting"):
        load_catalog("en")
    with pytest.warns(RuntimeWarning, match="Conflicting"):
        _workspace, engine, localization = factory("en")
    assert localization.language == "en"
    assert qml_window(engine).property("label") == "Lingua"


def test_identical_duplicates_merge_but_conflicting_json_keys_fail(catalog_root):
    (catalog_root / "en/duplicate.json").write_text('{"Lingua":"Language","Lingua":"Language"}')
    assert load_catalog("en")["Lingua"] == "Language"
    (catalog_root / "en/duplicate.json").write_text('{"Lingua":"Language","Lingua":"Different"}')
    with pytest.raises(CatalogError, match="Conflicting"):
        load_catalog("en")


@pytest.mark.parametrize("invalid", [
    {"version": True, "languages": [{"code": "it", "name": "Italiano"}]},
    {"version": 1, "languages": [{"code": "../it", "name": "Italiano"}]},
    {"version": 1, "languages": [{"code": "it", "name": "Bad\nname"}]},
    {"version": 1, "languages": [{"code": "en", "name": "English"}]},
    {"version": 1, "languages": [{"code": "it", "name": "Italiano"}] * 2},
])
def test_invalid_registry_cannot_enable_path_traversal_or_duplicate_languages(catalog_root, invalid):
    (catalog_root / "languages.json").write_text(json.dumps(invalid))
    with pytest.raises(CatalogError):
        load_languages()


def test_catalog_limits_and_external_symlinks_are_rejected(catalog_root, tmp_path):
    external = tmp_path / "outside.json"
    external.write_text('{"Lingua":"External"}')
    linked = catalog_root / "en/external.json"
    linked.symlink_to(external)
    with pytest.raises(CatalogError, match="bundled regular files"):
        load_catalog("en")
    linked.unlink()
    large = catalog_root / "en/large.json"
    large.write_text(" " * (language_catalog.MAX_CATALOG_BYTES + 1))
    with pytest.raises(CatalogError, match="size limit"):
        load_catalog("en")


def test_engine_destruction_restores_previous_translator_and_default_italian(factory):
    _workspace, first_engine, first = factory("it")
    first_window = qml_window(first_engine)
    _other_workspace, second_engine, second = factory("en")
    assert current_language() == "en" and first_window.property("label") == "Language"
    second_engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert current_language() == "it" and tr("Lingua") == "Lingua"
    assert first_window.property("label") == "Lingua"
    assert first.language == "it" and not isValid(second)
    first_engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert current_language() == "it"


@pytest.mark.parametrize("owner", ["localization", "workspace"])
def test_non_engine_destruction_also_removes_translator(factory, owner):
    workspace, engine, localization = factory("en")
    target = localization if owner == "localization" else workspace
    target.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert current_language() == "it"
    assert isValid(engine)


def test_registry_does_not_keep_unreferenced_engines_alive(qapp, catalog_root, tmp_path):
    before = set(i18n._registrations)
    workspace = WorkspaceFixture(tmp_path / "temporary")
    engine = QQmlEngine()
    localization = Localization(workspace, engine)
    token = localization._token
    del localization, engine
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    assert token not in i18n._registrations
    assert set(i18n._registrations) == before
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
