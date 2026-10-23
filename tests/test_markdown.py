"""Presentation tests use synthetic content and never activate external links."""

import os
from html.parser import HTMLParser

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QTextDocument

from kira.ui.markdown import (
    MAX_CACHE_BYTES,
    MAX_CACHE_ENTRIES,
    MAX_OUTPUT,
    MAX_SOURCE,
    MarkdownRenderer,
    safe_href,
)


class Tags(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags = []
        self.attributes = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)


@pytest.fixture
def renderer(qapp):
    return MarkdownRenderer()


def plain(html):
    document = QTextDocument()
    document.setHtml(html)
    return document.toPlainText()


def test_common_markdown_is_real_qt_formatting(renderer):
    html = renderer.render(
        "# Risultati\n\n**Superato** e *verificato* con `check`.\n\n"
        "- Uno\n- Due\n\n3. Terzo\n4. Quarto\n\n> Nota utile\n\n---"
    )
    assert {"h1", "strong", "em", "code", "ul", "ol", "li", "blockquote", "hr"} <= set(
        Tags(html).tags
    )
    document = QTextDocument()
    document.setHtml(html)
    cursor = document.find("Superato")
    assert cursor.charFormat().fontWeight() >= 700
    assert document.find("verificato").charFormat().fontItalic()
    assert "**" not in document.toPlainText()
    assert document.find("Terzo").block().textList().format().start() == 3


def test_code_fence_preserves_unicode_indentation_and_literal_markup(renderer):
    code = 'python3 tools/workflow.py check\n  print("caffè <b>&</b> 🚀")\n\t# tab'
    html = renderer.render(f"```bash\n{code}\n```\n")
    assert plain(html) == code
    assert Tags(html).tags == ["pre"]
    assert "font-family:monospace" in html
    assert "background-color:#10171d" in html


def test_partial_streaming_fence_is_readable_without_losing_completed_output(renderer):
    initial = renderer.render("In esecuzione:\n\n```py\nprint('è')")
    final = renderer.render("In esecuzione:\n\n```py\nprint('è')\n```\n\n**Fatto**")
    assert "print('è')" in plain(initial)
    assert "print('è')" in plain(final)
    assert "Fatto" in plain(final)
    assert "```" not in plain(final)
    assert "strong" in Tags(final).tags


def test_literal_html_and_images_cannot_create_resources(renderer):
    source = (
        '<script>alert(1)</script>\n\n<img src="file:///private.txt">\n\n'
        '<style>@import "https://example.invalid/a";</style>\n\n'
        '![logo **blu**](https://example.invalid/pixel.png)\n\n'
        '![locale](file:///private.png)\n\n'
        '<iframe src="https://example.invalid"></iframe>'
    )
    html = renderer.render(source)
    tags = Tags(html)
    assert set(tags.tags) <= {"p"}
    assert "src" not in dict(tags.attributes)
    content = plain(html)
    assert '<img src="file:///private.txt">' in content
    assert "<script>alert(1)</script>" in content
    assert "[Immagine: logo blu]" in content
    assert "[Immagine: locale]" in content


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "javascript%3Aalert(1)",
    "%256aavascript%253Aalert", "data:text/html,test", "qrc:/image", "ftp://example.org/a",
    "//example.org/file", "%2F%2Fexample.org/file", "file://remote-host/etc/file",
    "file:////remote-host/etc/file", "https://user:pass@example.org/",
    "https://user@example.org/", "https://example.org:99999", "https:///missing-host",
    "https://example.org/%0areturn", "https://example.org\\hidden", "https://[broken",
    "\nhttps://example.org", "https://example.org/a ", "https://%65xample.org/",
    "file:///tmp/%5Cremote", "https://example.org/%00file", "https://example.org/%257f",
])
def test_unsafe_href_is_rejected(url):
    assert safe_href(url) is None


@pytest.mark.parametrize("url", [
    "https://example.org/doc?q=two%20words#anchor", "http://127.0.0.1:8080/doc",
    "https://example.org/caff%C3%A8", "/home/fixture/project/file.md", "docs/file.md",
    "../parent.md", "./report.md", "file:///home/fixture/a%20b.md",
    "file://localhost/home/fixture/report.md", "https://[::1]/doc",
])
def test_safe_href_preserves_encoding_for_controller_validation(url):
    assert safe_href(url) == url


def test_links_are_allowlisted_and_attributes_escaped(renderer):
    html = renderer.render(
        '[Web](https://example.org/a?q=1&b=2) [File](file:///home/fixture/report.md) '
        '[Unsafe](javascript%3Aalert) [Relative](docs/report.md)'
    )
    attrs = Tags(html).attributes
    assert [value for key, value in attrs if key == "href"] == [
        "https://example.org/a?q=1&b=2", "file:///home/fixture/report.md", "docs/report.md",
    ]
    assert "&amp;b=2" in html
    assert "[Unsafe](javascript%3Aalert)" in plain(html)


def test_tables_are_visible_and_alignment_is_allowlisted(renderer):
    html = renderer.render("| Nome | Stato |\n| :--- | ---: |\n| Uno | **OK** |")
    assert {"table", "thead", "tr", "th", "tbody", "td", "strong"} <= set(Tags(html).tags)
    assert "Nome" in plain(html) and "OK" in plain(html)
    assert 'align="right"' in html


def test_rendering_is_bounded_and_cache_is_evicted(renderer):
    html = renderer.render("x" * (MAX_SOURCE + 1000))
    assert plain(html).count("x") == MAX_SOURCE
    assert "visualizzazione limitata" in plain(html)
    # Cache keys are (style, bounded source) pairs; the bounded source ends with the limit notice.
    assert any(source.startswith("x" * MAX_SOURCE) for _, source in renderer._cache)
    for index in range(MAX_CACHE_ENTRIES + 5):
        renderer.render(f"{index}: " + "é" * 40_000)
        assert renderer._cache_bytes <= MAX_CACHE_BYTES
        assert len(renderer._cache) <= MAX_CACHE_ENTRIES
    assert not any(source.startswith("x" * MAX_SOURCE) for _, source in renderer._cache)
    repeated_markup = "`a` " * (MAX_SOURCE // 4)
    assert len(renderer.render(repeated_markup)) <= MAX_OUTPUT


def test_nesting_limit_and_source_copy_contract(renderer):
    source = "> " * 200 + "nested\n\n**Original**\n\n```py\nprint(1)\n```"
    html = renderer.render(source)
    assert Tags(html).tags.count("blockquote") <= 20
    assert source.endswith("```py\nprint(1)\n```")
    assert renderer.render(source) == html
