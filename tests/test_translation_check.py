"""Catalog validation protects display arguments and file-picker filters."""

import runpy
from pathlib import Path

CHECKER = runpy.run_path(str(Path(__file__).parents[1] / "scripts/check_translations.py"))


def test_reordered_placeholders_allowed_but_missing_values_and_filters_fail():
    validate = CHECKER["placeholder_errors"]
    assert validate("{name}: {count}", "{count} for {name}", {"python"}) == []
    assert validate("%1: %2", "%2 for %1", {"qml"}) == []
    assert validate("{name}: {count}", "{name}: {total}", {"python"})
    assert validate("%1: %2", "%1", {"qml"})
    assert validate("Markdown (*.md)", "Markdown (*.txt)", {"qml"})


def test_extractor_handles_escaped_qml_and_python_templates(tmp_path):
    ui = tmp_path / "src/kira/ui"
    (ui / "qml").mkdir(parents=True)
    (ui / "qml/Fixture.qml").write_text('Text { text: qsTr("L’app \\\"%1\\\""); objectName: "not a label" }')
    (ui / "fixture.py").write_text('value = tr("File {path}", path="literal")\n')
    assert CHECKER["source_messages"](tmp_path) == {
        'L’app "%1"': {"qml"}, "File {path}": {"python"},
    }
