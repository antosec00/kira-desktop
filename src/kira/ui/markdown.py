"""Derived, resource-free Markdown presentation for untrusted assistant text.

The transcript remains Markdown. Only this allowlisted HTML reaches Qt's rich
text engine: raw HTML and images never become Qt markup or resource requests.
Link activation needs the additional project/file checks in the controller.
"""

import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, replace
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token
from PySide6.QtCore import QObject, Slot

from kira.ui.i18n import current_language, tr

if TYPE_CHECKING:
    from kira.ui.appearance import Appearance

MAX_SOURCE = 131_072
MAX_OUTPUT = 1_572_864
MAX_CACHE_BYTES = 2 * 1024 * 1024
MAX_CACHE_ENTRIES = 64
_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
_CODE_STYLE = "font-family:monospace; color:#dbe6f8; background-color:#10171d"


@dataclass(frozen=True)
class RenderStyle:
    code: str = _CODE_STYLE
    link: str = "#8cb8ff"
    added: str = ""
    removed: str = ""
    smart: bool = False
    language: str = "it"


DEFAULT_STYLE = RenderStyle()


def safe_href(url: str) -> str | None:
    """Accept explicit web URLs or local paths, never active/resource schemes.

    Return the original encoding for Qt/controller consumption. Inspect decoded
    forms too, so a scheme or network path cannot be hidden by percent encoding.
    Spaces in local paths must be URL-encoded (the Markdown parser does this).
    This is syntactic filtering, not authorization to open a local file.
    """
    if not url or len(url) > 8192:
        return None
    candidate = url
    for _ in range(5):
        # Spaces produced by URL decoding are ordinary filename characters;
        # actual controls and backslashes remain disallowed in all forms.
        forbidden = _CONTROL.search(candidate) if candidate == url else re.search(
            r"[\x00-\x1f\x7f-\x9f\\]", candidate
        )
        if forbidden or candidate != candidate.strip() or candidate.startswith("//"):
            return None
        try:
            parts = urlsplit(candidate)
            if parts.scheme.lower() in {"http", "https"}:
                if (not parts.hostname or parts.username is not None
                        or parts.password is not None or "%" in parts.netloc):
                    return None
                _ = parts.port  # Reject malformed/out-of-range ports too.
            elif parts.scheme.lower() == "file":
                if parts.netloc.lower() not in {"", "localhost"} or not parts.path.startswith("/"):
                    return None
                if parts.path.startswith("//"):
                    return None
            elif parts.scheme or parts.netloc or ":" in parts.path.split("/", 1)[0]:
                return None
        except ValueError:
            return None
        decoded = unquote(candidate)
        if decoded == candidate:
            return url
        candidate = decoded
    return None


def _plain_inline(tokens: list[Token]) -> str:
    """Image descriptions are inert text, including nested inline decoration."""
    return "".join(
        _plain_inline(token.children) if token.children else
        "\n" if token.type in {"softbreak", "hardbreak"} else token.content
        for token in tokens
    )


def _render_tokens(tokens: list[Token], style: RenderStyle = DEFAULT_STYLE) -> str:
    chunks: list[str] = []
    link_closers: list[str] = []
    simple_tags = {"p", "strong", "em", "s", "ul", "ol", "li", "blockquote",
                   "h1", "h2", "h3", "h4", "h5", "h6", "thead", "tbody", "tr"}
    for token in tokens:
        kind = token.type
        if kind == "inline":
            chunks.append(_render_tokens(token.children or [], style))
        elif kind in {"text", "html_inline", "html_block"}:
            chunks.append(escape(token.content))
        elif kind in {"fence", "code_block"}:
            # Qt interprets a final newline before </pre> as an extra blank
            # paragraph. Retain internal whitespace, tabs and all code text.
            content = token.content.removesuffix("\n")
            if token.info.strip().split(maxsplit=1)[:1] in (["diff"], ["patch"]) and style.added:
                lines = []
                for line in content.split("\n"):
                    color = style.added if line.startswith("+") else (
                        style.removed if line.startswith("-") else "")
                    literal = escape(line)
                    lines.append(f'<span style="color:{color}">{literal}</span>' if color else literal)
                rendered = "\n".join(lines)
            else:
                rendered = escape(content)
            # Long lines wrap inside the block: the transcript has no horizontal scroll,
            # so unwrapped lines would be cut off. Copy and the source view keep them intact.
            chunks.append(f'<pre style="{style.code}; margin:10px 0; white-space:pre-wrap">{rendered}</pre>')
        elif kind == "code_inline":
            chunks.append(f'<code style="{style.code}">{escape(token.content)}</code>')
        elif kind in {"softbreak", "hardbreak"}:
            chunks.append("<br>" if kind == "hardbreak" else "\n")
        elif kind == "image":
            alt = _plain_inline(token.children or []) or token.content
            chunks.append(escape(tr("[Immagine: {alt}]", alt=alt) if alt else tr("[Immagine]")))
        elif kind == "link_open":
            href = safe_href(str(token.attrGet("href") or ""))
            chunks.append(f'<a href="{escape(href, quote=True)}" style="color:{style.link}">'
                          if href else "<span>")
            link_closers.append("</a>" if href else "</span>")
        elif kind == "link_close":
            chunks.append(link_closers.pop() if link_closers else "")
        elif kind == "hr":
            chunks.append("<hr>")
        elif token.tag in {"th", "td"}:
            if token.nesting == -1:
                chunks.append(f"</{token.tag}>")
            else:
                alignment = str(token.attrGet("style") or "")
                align = next((value for value in ("left", "center", "right")
                              if alignment == f"text-align:{value}"), "left")
                chunks.append(f'<{token.tag} align="{align}" style="padding:6px">')
        elif token.tag == "table":
            chunks.append('<table border="1" cellspacing="0" cellpadding="6">'
                          if token.nesting == 1 else "</table>")
        elif token.tag in simple_tags and token.nesting:
            if token.hidden:  # Tight list paragraphs should not add margins.
                continue
            if token.nesting == -1:
                chunks.append(f"</{token.tag}>")
            elif token.tag == "ol":
                start = token.attrGet("start") or 1
                chunks.append(f'<ol start="{int(start)}">')
            else:
                chunks.append(f"<{token.tag}>")
        # Unknown token types are intentionally not interpreted as HTML.
    return "".join(chunks)


class _SafeMarkdown(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        return safe_href(url) is not None


class MarkdownRenderer(QObject):
    """UI-thread renderer with bounded source, nesting, output and cache memory."""

    def __init__(self, parent: QObject | None = None, appearance: "Appearance | None" = None) -> None:
        super().__init__(parent)
        self._appearance = appearance
        self._parser = _SafeMarkdown("commonmark", {"html": False, "maxNesting": 20})
        self._parser.enable(["table", "strikethrough"])
        self._smart_parser = _SafeMarkdown("commonmark", {
            "html": False, "maxNesting": 20, "typographer": True, "quotes": "“”‘’",
        })
        self._smart_parser.enable(["table", "strikethrough", "replacements", "smartquotes"])
        self._cache: OrderedDict[tuple[RenderStyle, str], str] = OrderedDict()
        self._cache_bytes = 0

    @Slot(str, result=str)
    def render(self, text: str) -> str:
        return self._render(text, DEFAULT_STYLE)

    @Slot(str, int, result=str)
    def renderForAppearance(self, text: str, revision: int) -> str:
        """The revision creates a QML dependency; styles come only from local preferences."""
        if self._appearance is None:
            return self.render(text)
        colors = self._appearance.colors
        font = self._appearance.codeFont
        # Escape font names in addition to preference validation. No source text
        # or user-provided CSS is used as a style declaration.
        family = escape(font.family(), quote=True)
        code = (f"font-family:'{family}'; font-size:{font.pixelSize()}px; "
                f"font-weight:{font.weight().value}; font-style:{'italic' if font.italic() else 'normal'}; "
                f"color:{colors['codeText']}; background-color:{colors['codeBackground']}")
        values = self._appearance.values
        colored = values["diff_indicators"] == "color"
        style = RenderStyle(code, colors["accent"], colors["success"] if colored else "",
                            colors["danger"] if colored else "", values["smart_punctuation"] is True)
        return self._render(text, style)

    def _render(self, text: str, style: RenderStyle) -> str:
        style = replace(style, language=current_language())
        truncated = len(text) > MAX_SOURCE
        source = text[:MAX_SOURCE]
        if truncated:
            source += "\n" + tr("[… visualizzazione limitata; esporta la cronologia completa]")
        key = (style, source)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        parser = self._smart_parser if style.smart else self._parser
        rendered = _render_tokens(parser.parse(source), style)
        if len(rendered) > MAX_OUTPUT:
            rendered = "<p>" + escape(source).replace("\n", "<br>") + "</p>"
        size = self._entry_size(key, rendered)
        if size <= MAX_CACHE_BYTES:
            while self._cache and (self._cache_bytes + size > MAX_CACHE_BYTES
                                   or len(self._cache) >= MAX_CACHE_ENTRIES):
                old_key, value = self._cache.popitem(last=False)
                self._cache_bytes -= self._entry_size(old_key, value)
            self._cache[key] = rendered
            self._cache_bytes += size
        return rendered

    @staticmethod
    def _entry_size(key: tuple[RenderStyle, str], rendered: str) -> int:
        style, source = key
        return (sys.getsizeof(key) + sys.getsizeof(style) + sys.getsizeof(source)
                + sys.getsizeof(rendered) + sum(sys.getsizeof(value) for value in (
                    style.code, style.link, style.added, style.removed, style.language)))
