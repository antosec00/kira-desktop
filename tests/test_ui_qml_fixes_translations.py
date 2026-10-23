"""Catalog hygiene for QML-owned messages: unused entries, shared words and English names."""

import json
import runpy
from pathlib import Path

from kira.ui.language_catalog import load_catalog

ROOT = Path(__file__).parents[1]
CHECKER = runpy.run_path(str(ROOT / "scripts/check_translations.py"))
QML_CATALOGS = ("qml.json", "research.json", "reasoning_summary.json", "soul_integration.json",
                "souls.json", "workbench_ui.json")


def test_unused_check_tolerates_table_driven_labels_and_qt_catalogs(tmp_path):
    ui = tmp_path / "src/kira/ui"
    (ui / "qml").mkdir(parents=True)
    (ui / "states.py").write_text('STATE_LABELS = {"running": "In corso"}\n'
                                  'def label(state):\n    return tr(STATE_LABELS[state])\n')
    (ui / "qml/Fixture.qml").write_text(
        'Text { text: qsTr(entry.status); property var names: ({done: "Completato"}) '
        "property string other: 'Con \\'apici\\'' }")
    catalogs = tmp_path / "translations"
    (catalogs / "fr").mkdir(parents=True)
    (catalogs / "fr/ui.json").write_text(json.dumps({
        "In corso": "En cours", "Completato": "Terminé", "Con 'apici'": "Avec apostrophes",
        "Etichetta rimossa": "Libellé retiré"}))
    (catalogs / "fr/qt.json").write_text(json.dumps({"Cancel": "Annuler"}))
    literals = CHECKER["source_literals"](tmp_path)
    assert CHECKER["unused_entries"](catalogs / "fr", literals) == [("ui.json", "Etichetta rimossa")]


def test_qml_owned_catalogs_have_no_unused_entries():
    literals = CHECKER["source_literals"]()
    unused = [entry for entry in CHECKER["unused_entries"](ROOT / "src/kira/ui/translations/en", literals)
              if entry[0] in QML_CATALOGS]
    assert unused == []


def test_research_record_and_browser_tab_no_longer_share_a_source_text():
    catalog = load_catalog("en")
    research = (ROOT / "src/kira/ui/qml/ResearchDialog.qml").read_text(encoding="utf-8")
    browser = (ROOT / "src/kira/ui/qml/BrowserPane.qml").read_text(encoding="utf-8")
    assert 'qsTr("Nuova scheda di ricerca")' in research and 'qsTr("Nuova scheda")' not in research
    assert 'qsTr("Nuova scheda")' in browser
    assert catalog["Nuova scheda di ricerca"] == "New record"
    assert catalog["Nuova scheda"] == "New tab"


def test_english_names_follow_the_interface_and_the_documentation():
    catalog = load_catalog("en")
    assert catalog["Anima di Kira"] == "Kira's Soul"
    assert catalog["Anima di Kira · identità e ricordi"] == "Kira's Soul · identity and memories"
    assert catalog["Computer di Kira"] == "Kira's Computer"
    assert catalog["OpenAI diretto · ChatGPT"] == "Direct OpenAI · ChatGPT"
    assert catalog["Richieste da verificare: %1"] == "Requests to check: %1"
    assert catalog["Fine della chat corrente"] == "End of the current chat"
    assert catalog["Disconnetti da Kira"] == "Disconnect from Kira"
    assert catalog["Il prossimo passo"] == "Next step"
    assert catalog["Esecuzione precedente"] == "Previous run"
    assert catalog["Esecuzione {id}"] == "Run {id}"
    assert catalog["Puoi riattivarli da Impostazioni · Lavoro"] == "You can show them again in Settings · Work"
    owned = {}
    for name in QML_CATALOGS:
        owned.update(json.loads((ROOT / "src/kira/ui/translations/en" / name).read_text(encoding="utf-8")))
    english = " ".join(owned.values())
    for stale in ("Kira soul", "Kira Computer", "OpenAI direct", "Sign out from Kira", "Execution {id}"):
        assert stale not in english
