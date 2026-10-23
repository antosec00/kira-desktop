"""Appearance affects derived presentation while source and code remain literal."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QTextDocument
from PySide6.QtQml import QQmlEngine

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.i18n import Localization
from kira.ui.markdown import MAX_CACHE_BYTES, MarkdownRenderer


def plain(html):
    document = QTextDocument()
    document.setHtml(html)
    return document.toPlainText()


@pytest.fixture
def appearance(qapp):
    font = QFont("monospace")
    font.setPixelSize(12)
    return SimpleNamespace(
        codeFont=font,
        colors={"codeText": "#202020", "codeBackground": "#fafafa",
                "accent": "#0044aa", "success": "#006622", "danger": "#aa2222"},
        values={"diff_indicators": "color", "smart_punctuation": False},
    )


def test_theme_font_and_cache_change_together(appearance):
    renderer = MarkdownRenderer(appearance=appearance)
    source = "[Help](https://example.invalid/help)\n\n```python\nprint('test')\n```"
    first = renderer.renderForAppearance(source, 0)
    appearance.colors = {**appearance.colors, "codeText": "#eeeeee",
                         "codeBackground": "#121212", "accent": "#99ccff"}
    appearance.codeFont.setPixelSize(18)
    second = renderer.renderForAppearance(source, 1)
    assert first != second
    assert "font-size:18px" in second and "background-color:#121212" in second
    assert "color:#99ccff" in second
    assert plain(first) == plain(second)
    assert renderer.renderForAppearance(source, 1) == second
    assert renderer._cache_bytes <= MAX_CACHE_BYTES


def test_smart_punctuation_is_opt_in_and_excludes_code(appearance):
    renderer = MarkdownRenderer(appearance=appearance)
    source = '"Ciao" -- prova...\n\n`"code" --literal...`\n\n```sh\nprintf "text..." --flag\n```'
    literal = plain(renderer.renderForAppearance(source, 0))
    assert '"Ciao" -- prova...' in literal
    appearance.values["smart_punctuation"] = True
    smart = plain(renderer.renderForAppearance(source, 1))
    assert "“Ciao”" in smart and "prova…" in smart
    assert '"code" --literal...' in smart
    assert 'printf "text..." --flag' in smart
    assert source.startswith('"Ciao" -- prova...')
    assert plain(renderer.render(source)) == literal


def test_diff_colors_never_remove_signs_or_interpret_source_html(appearance):
    renderer = MarkdownRenderer(appearance=appearance)
    diff = '-old <img src="file:///synthetic.png">\n+new <b>literal</b>\n unchanged'
    source = "```diff\n" + diff + "\n```"
    colored = renderer.renderForAppearance(source, 0)
    assert "color:#006622" in colored and "color:#aa2222" in colored
    assert "<img" not in colored and "<b>" not in colored
    assert plain(colored) == diff
    appearance.values["diff_indicators"] = "signs"
    monochrome = renderer.renderForAppearance(source, 1)
    assert "<span" not in monochrome
    assert plain(monochrome) == diff


def test_language_refreshes_only_generated_markers_and_cache(appearance, tmp_path):
    workspace = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    engine = QQmlEngine()
    localization = Localization(workspace, engine, engine)
    renderer = MarkdownRenderer(appearance=appearance)
    source = 'Chiudi\n\n![Impostazioni](https://example.invalid/image.png)\n\n`Chiudi`'
    try:
        italian = plain(renderer.renderForAppearance(source, 0))
        assert "[Immagine: Impostazioni]" in italian
        localization.setLanguage("en")
        english = plain(renderer.renderForAppearance(source, 0))
        assert "[Image: Impostazioni]" in english
        assert english.count("Chiudi") == 2
        assert "Settings" not in english and "Close" not in english
        localization.setLanguage("it")
        assert plain(renderer.renderForAppearance(source, 0)) == italian
        assert source.startswith("Chiudi")
    finally:
        workspace.close()
        engine.deleteLater()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
