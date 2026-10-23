"""Native catalogs stay small: each translation context holds only what it can look up."""

import ast
import re
import runpy
from pathlib import Path

import pytest
from PySide6.QtCore import QTranslator

from kira.ui import translation_compiler
from kira.ui.language_catalog import load_catalog, native_labels
from kira.ui.translation_compiler import MAX_BYTES, compiled_catalog

ROOT = Path(__file__).parents[1]
QML = ROOT / "src/kira/ui/qml"
# The repository's own catalog checker is the independent oracle for literals.
CHECKER = runpy.run_path(str(ROOT / "scripts/check_translations.py"))
ANY_QML_CALL = re.compile(r"\bqsTr\s*\(")
# Statuses the transcript model stores and Main.qml shows with qsTr(entry.status).
TRANSCRIPT_STATUSES = ("completo", "streaming", "in esecuzione", "confermato", "errore",
                       "in attesa del browser", "incompleto", "esito non confermato")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def loaded(code: str, catalog: dict[str, str], native=()) -> QTranslator:
    translator = QTranslator()
    assert translator.load(str(compiled_catalog(code, catalog, native)))
    return translator


def qml_literals(path: Path) -> tuple[list[str], bool]:
    """A file's literal qsTr() messages, and whether another call computes its text."""
    text = path.read_text(encoding="utf-8")
    literals = [ast.literal_eval(match.group(1)) for match in CHECKER["QML_CALL"].finditer(text)]
    complete = re.findall(r'''\bqsTr\s*\(\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\s*[,)]''', text)
    return literals, len(complete) != len(ANY_QML_CALL.findall(text))


def test_english_catalog_keeps_a_wide_margin_below_the_native_limit(qapp, cache):
    path = compiled_catalog("en", load_catalog("en"), native_labels("en"))
    # Writing every message into every QML and Qt context made the file grow
    # with messages times contexts and reach the limit, past which English
    # silently fell back to Italian. A quarter of the limit leaves room for a
    # catalog several times larger and for new QML files.
    assert path.stat().st_size <= MAX_BYTES // 4


def test_every_qml_literal_translates_in_its_own_file_context(qapp, cache):
    catalog = load_catalog("en")
    translator = loaded("en", catalog, native_labels("en"))
    checked, missing, computed = 0, [], []
    for path in sorted(QML.glob("*.qml")):
        literals, dynamic = qml_literals(path)
        if dynamic:
            computed.append(path.stem)
        for source in literals:
            if source in catalog:
                checked += 1
                if translator.translate(path.stem, source) != catalog[source]:
                    missing.append((path.name, source))
    assert checked and not missing
    # A file that translates computed text can look up every catalog message.
    assert not [(name, source) for name in computed for source in catalog
                if translator.translate(name, source) != catalog[source]]
    # A file without qsTr() calls contributes no messages of its own.
    untranslated = [path.stem for path in QML.glob("*.qml")
                    if "qsTr" not in path.read_text(encoding="utf-8")]
    assert all(translator.translate(name, "Nuova chat") == "" for name in untranslated)


def test_python_labels_and_transcript_statuses_resolve_in_their_contexts(qapp, cache):
    catalog = load_catalog("en")
    translator = loaded("en", catalog, native_labels("en"))
    python = [source for source, kinds in CHECKER["source_messages"]().items()
              if "python" in kinds and source in catalog]
    assert python and not [source for source in python
                           if translator.translate("Kira", source) != catalog[source]]
    # Computed Python labels (tr(STATE_LABELS[state])) are not literal tr() arguments.
    assert translator.translate("Kira", "Interrotto · esito non confermato") == (
        catalog["Interrotto · esito non confermato"])
    for status in TRANSCRIPT_STATUSES:
        assert translator.translate("Main", status) == catalog[status], status


def test_italian_qt_labels_keep_qt_contexts(qapp, cache):
    translator = loaded("it", load_catalog("it"), native_labels("it"))
    assert translator.translate("QPlatformTheme", "Save") == "Salva"
    assert translator.translate("QQuickDialogButtonBox", "Cancel") == "Annulla"
    assert translator.translate("QPlatformTheme", "Cancel") == "Annulla"


def test_selection_rules_and_cache_key_follow_the_sources(qapp, cache, tmp_path, monkeypatch):
    ui = tmp_path / "ui"
    (ui / "qml").mkdir(parents=True)
    plain = ui / "qml/Plain.qml"
    plain.write_text('Text { text: qsTr("Chiudi") + qsTr("Apri", "disambiguation") }')
    (ui / "qml/Status.qml").write_text("Text { text: qsTr(entry.status) }")
    (ui / "qml/Joined.qml").write_text('Text { text: qsTr("Pre" + suffix) }')
    (ui / "labels.py").write_text('LABEL = tr("Salva bozza")\nOK = tr("OK", value=1)\n')
    monkeypatch.setattr(translation_compiler, "UI_ROOT", ui)
    catalog = {"Chiudi": "Close", "Apri": "Open", "Salva bozza": "Save draft",
               "OK": "D'accord", "Cancel": "Annuler", "completo": "complete"}

    first = compiled_catalog("fr", catalog, {"OK"})
    translator = QTranslator()
    assert translator.load(str(first))
    assert translator.translate("Plain", "Chiudi") == "Close"
    assert translator.translate("Plain", "Apri") == "Open"
    assert translator.translate("Plain", "completo") == ""
    assert translator.translate("Plain", "Salva bozza") == ""
    for full in ("Status", "Joined", "Kira", ""):
        assert all(translator.translate(full, source) == value for source, value in catalog.items())
    # Qt's contexts receive entries no application literal uses, plus the
    # locale's native labels even when the application shares their text.
    assert translator.translate("QPlatformTheme", "Cancel") == "Annuler"
    assert translator.translate("QQuickDialogButtonBox", "OK") == "D'accord"
    assert translator.translate("QPlatformTheme", "Salva bozza") == ""
    assert translator.translate("QPlatformTheme", "Chiudi") == ""

    without_native = QTranslator()
    assert without_native.load(str(compiled_catalog("fr", catalog)))
    assert without_native.translate("QQuickDialogButtonBox", "OK") == ""

    # New QML strings change the key: an older compiled catalog is never reused.
    plain.write_text('Text { text: qsTr("Chiudi") + qsTr("completo") }')
    translation_compiler._application_messages.cache_clear()
    second = compiled_catalog("fr", catalog, {"OK"})
    assert second != first
    updated = QTranslator()
    assert updated.load(str(second))
    assert updated.translate("Plain", "completo") == "complete"
    assert updated.translate("Plain", "Apri") == ""
