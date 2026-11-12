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
from PySide6.QtCore import QMetaObject, QObject, Slot

from kira.ui.i18n import current_language, tr

if TYPE_CHECKING:
    from kira.ui.appearance import Appearance

MAX_SOURCE = 131_072
MAX_OUTPUT = 1_572_864
MAX_CACHE_BYTES = 2 * 1024 * 1024
MAX_CACHE_ENTRIES = 64
_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
_CODE_STYLE = "font-family:monospace; color:#dbe6f8; background-color:#10171d"
_INLINE_CODE_STYLE = "font-family:monospace; color:#e5af82; background-color:#292e33"


@dataclass(frozen=True)
class RenderStyle:
    code: str = _CODE_STYLE
    link: str = "#8cb8ff"
    added: str = ""
    removed: str = ""
    smart: bool = False
    language: str = "it"
    inline_code: str = _INLINE_CODE_STYLE
    body: str = "#d0d5da"
    strong: str = "#f5f7f8"
    muted: str = "#9aa3ad"
    rule: str = "#2a313a"
    table_header: str = ""
    syntax_keyword: str = ""
    syntax_string: str = ""
    syntax_comment: str = ""
    syntax_number: str = ""


DEFAULT_STYLE = RenderStyle()

_HASH_COMMENT_LANGS = frozenset({
    "py", "python", "sh", "bash", "zsh", "shell", "yaml", "yml", "toml", "rb", "ruby", "r",
})
_SLASH_COMMENT_LANGS = frozenset({
    "js", "javascript", "ts", "typescript", "jsx", "tsx", "rs", "rust", "go", "golang",
    "c", "cpp", "cxx", "cc", "h", "hpp", "java", "kt", "kotlin", "swift", "cs", "csharp",
    "jsonc", "qml", "css", "scss",
})
_SQL_LANGS = frozenset({"sql", "sqlite", "postgres", "postgresql", "mysql"})
_HIGHLIGHT_LANGS = _HASH_COMMENT_LANGS | _SLASH_COMMENT_LANGS | _SQL_LANGS | frozenset({"json"})

_KEYWORDS = frozenset({
    "def", "class", "return", "yield", "async", "await", "import", "from", "as", "if",
    "elif", "else", "for", "while", "break", "continue", "try", "except", "finally",
    "raise", "with", "lambda", "pass", "True", "False", "None", "and", "or", "not", "in",
    "is", "match", "case", "fn", "let", "mut", "pub", "impl", "struct", "enum", "trait",
    "use", "mod", "crate", "where", "const", "static", "type", "interface", "export",
    "default", "function", "var", "new", "throw", "catch", "typeof", "instanceof",
    "null", "undefined", "true", "false", "func", "package", "defer", "go", "chan",
    "select", "void", "int", "float", "double", "bool", "char", "auto", "SELECT", "FROM",
    "WHERE", "INSERT", "INTO", "UPDATE", "DELETE", "JOIN", "LEFT", "INNER", "GROUP",
    "ORDER", "BY", "LIMIT", "CREATE", "TABLE", "INDEX", "PRIMARY", "KEY", "NULL", "AND", "OR",
})

_HASH_LEXER = re.compile(
    r'(?P<comment>#[^\n]*)'
    r'|(?P<string>"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
    r'|(?P<ident>\b[A-Za-z_][A-Za-z0-9_]*\b)'
    r'|(?P<number>\b(?:0[xXoObB][0-9a-fA-F_]+|\d+(?:\.\d+)?)\b)'
)
_SLASH_LEXER = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)'
    r'|(?P<string>`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
    r'|(?P<ident>\b[A-Za-z_][A-Za-z0-9_]*\b)'
    r'|(?P<number>\b(?:0[xXoObB][0-9a-fA-F_]+|\d+(?:\.\d+)?)\b)'
)
_SQL_LEXER = re.compile(
    r'(?P<comment>--[^\n]*|/\*[\s\S]*?\*/)'
    r'|(?P<string>\'(?:\'\'|[^\'\n])*\'|"(?:\\.|[^"\\\n])*")'
    r'|(?P<ident>\b[A-Za-z_][A-Za-z0-9_]*\b)'
    r'|(?P<number>\b\d+(?:\.\d+)?\b)'
)
_JSON_LEXER = re.compile(
    r'(?P<string>"(?:\\.|[^"\\\n])*")'
    r'|(?P<ident>\b(?:true|false|null)\b)'
    r'|(?P<number>-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)'
)
_FENCE_LINE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _highlight_code(content: str, lang: str, style: RenderStyle) -> str:
    """Safe, resource-free lexical syntax highlighting preserving exact plain text."""
    if not style.syntax_keyword or len(content) > 24_000 or lang not in _HIGHLIGHT_LANGS:
        return escape(content)
    lexer = (
        _HASH_LEXER if lang in _HASH_COMMENT_LANGS
        else _SLASH_LEXER if lang in _SLASH_COMMENT_LANGS
        else _SQL_LEXER if lang in _SQL_LANGS
        else _JSON_LEXER
    )
    out: list[str] = []
    pos = 0
    for match in lexer.finditer(content):
        start, end = match.span()
        if start > pos:
            out.append(escape(content[pos:start]))
        raw = match.group(0)
        group = match.lastgroup
        color = ""
        if group == "comment":
            color = style.syntax_comment
        elif group == "string":
            color = style.syntax_string
        elif group == "number":
            color = style.syntax_number
        elif group == "ident" and (raw in _KEYWORDS or (lang in _SQL_LANGS and raw.upper() in _KEYWORDS)):
            color = style.syntax_keyword
        escaped = escape(raw)
        out.append(f'<span style="color:{color}">{escaped}</span>' if color else escaped)
        pos = end
    if pos < len(content):
        out.append(escape(content[pos:]))
    return "".join(out)


def _streaming_heading_split(source: str) -> int:
    """Return a safe CommonMark ATX heading boundary index for incremental streaming, or 0."""
    if len(source) < 1_200 or "]:" in source:
        return 0
    fence_char = ""
    fence_len = 0
    last_boundary = 0
    offset = 0
    prev_blank = False
    for line in source.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        fence_match = _FENCE_LINE_RE.match(stripped)
        if fence_match:
            marker, rest = fence_match.group(1), fence_match.group(2)
            if not fence_char:
                if "`" not in marker or "`" not in rest:
                    fence_char = marker[0]
                    fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len and not rest.strip():
                fence_char = ""
                fence_len = 0
        elif not fence_char and prev_blank and stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= hashes <= 6 and len(stripped) > hashes and stripped[hashes] == " ":
                if offset >= 512:
                    last_boundary = offset
        prev_blank = not fence_char and stripped == ""
        offset += len(line)
    return last_boundary if 0 < last_boundary < len(source) - 64 else 0


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


def _open_tag(tag: str, style: RenderStyle, start: int = 1) -> str:
    if tag == "p":
        return f'<p style="line-height:150%; margin-top:6px; margin-bottom:10px; color:{style.body}">'
    if tag == "li":
        return f'<li style="line-height:150%; margin-top:4px; margin-bottom:6px; color:{style.body}">'
    if tag == "ul":
        return '<ul style="margin-top:6px; margin-bottom:10px">'
    if tag == "ol":
        return f'<ol start="{start}" style="margin-top:6px; margin-bottom:10px">'
    if tag == "blockquote":
        return (f'<blockquote style="line-height:150%; margin-top:8px; margin-bottom:10px; '
                f'margin-left:14px; color:{style.muted}">')
    if tag == "strong":
        return f'<strong style="color:{style.strong}; font-weight:700">'
    if tag == "h1":
        return (f'<h1 style="font-size:x-large; font-weight:700; line-height:130%; '
                f'margin-top:16px; margin-bottom:8px; color:{style.strong}">')
    if tag == "h2":
        return (f'<h2 style="font-size:large; font-weight:700; line-height:135%; '
                f'margin-top:14px; margin-bottom:6px; color:{style.strong}">')
    if tag == "h3":
        return (f'<h3 style="font-size:large; font-weight:600; line-height:138%; '
                f'margin-top:12px; margin-bottom:5px; color:{style.strong}">')
    if tag in {"h4", "h5", "h6"}:
        return (f'<{tag} style="font-size:medium; font-weight:600; line-height:140%; '
                f'margin-top:10px; margin-bottom:4px; color:{style.strong}">')
    return f"<{tag}>"


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
            fence_lang = (token.info.strip().split(maxsplit=1)[0].casefold()
                          if token.info and token.info.strip() else "")
            if fence_lang in {"diff", "patch"} and style.added:
                lines = []
                for line in content.split("\n"):
                    color = style.added if line.startswith("+") else (
                        style.removed if line.startswith("-") else "")
                    literal = escape(line)
                    lines.append(f'<span style="color:{color}">{literal}</span>' if color else literal)
                rendered = "\n".join(lines)
            elif fence_lang and style.syntax_keyword:
                rendered = _highlight_code(content, fence_lang, style)
            else:
                rendered = escape(content)
            # Long lines wrap inside the block: the transcript has no horizontal scroll,
            # so unwrapped lines would be cut off. Copy and the source view keep them intact.
            chunks.append(
                f'<pre style="{style.code}; line-height:140%; margin:10px 0; white-space:pre-wrap">{rendered}</pre>'
            )
        elif kind == "code_inline":
            chunks.append(f'<code style="{style.inline_code}">{escape(token.content)}</code>')
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
            chunks.append(f'<hr style="background-color:{style.rule}; margin-top:10px; margin-bottom:10px">')
        elif token.tag in {"th", "td"}:
            if token.nesting == -1:
                chunks.append(f"</{token.tag}>")
            else:
                alignment = str(token.attrGet("style") or "")
                align = next((value for value in ("left", "center", "right")
                              if alignment == f"text-align:{value}"), "left")
                cell_style = (
                    f"padding:7px 10px; color:{style.strong}; background-color:{style.table_header}"
                    if token.tag == "th" and style.table_header
                    else f"padding:6px 10px; color:{style.body}"
                )
                chunks.append(f'<{token.tag} align="{align}" style="{cell_style}">')
        elif token.tag == "table":
            chunks.append(
                f'<table border="1" cellspacing="0" cellpadding="6" '
                f'style="border-collapse:collapse; border-color:{style.rule}; margin-top:8px; margin-bottom:10px">'
                if token.nesting == 1 else "</table>"
            )
        elif token.tag in simple_tags and token.nesting:
            if token.hidden:  # Tight list paragraphs should not add margins.
                continue
            if token.nesting == -1:
                chunks.append(f"</{token.tag}>")
            elif token.tag == "ol":
                start = token.attrGet("start") or 1
                chunks.append(_open_tag("ol", style, int(start)))
            else:
                chunks.append(_open_tag(token.tag, style))
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
        self._stream_prefix_cache: OrderedDict[tuple[RenderStyle, str], str] = OrderedDict()

    @Slot(QObject)
    def invalidate(self, item: QObject | None) -> None:
        """Force QQuickTextEdit to rebuild its scene-graph textNodeMap on RichText updates.

        Qt 6 appends child frames (HTML tables) after rootFrame blocks in textNodeMap,
        leaving it unsorted by document position; incremental UpdateSomeNodes during
        streaming can stop early and drop every glyph node above the first table.
        """
        if item is not None:
            QMetaObject.invokeMethod(item, "q_invalidate")

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
        font_base = (f"font-family:'{family}'; font-size:{font.pixelSize()}px; "
                     f"font-weight:{font.weight().value}; "
                     f"font-style:{'italic' if font.italic() else 'normal'}; ")
        code = f"{font_base}color:{colors['codeText']}; background-color:{colors['codeBackground']}"
        inline_text = colors.get("inlineCodeText", colors["codeText"])
        inline_bg = colors.get("inlineCodeBackground", colors["codeBackground"])
        inline_code = f"{font_base}color:{inline_text}; background-color:{inline_bg}"
        values = self._appearance.values
        colored = values["diff_indicators"] == "color"
        style = RenderStyle(
            code=code,
            link=colors["accent"],
            added=colors["success"] if colored else "",
            removed=colors["danger"] if colored else "",
            smart=values["smart_punctuation"] is True,
            inline_code=inline_code,
            body=colors.get("proseText", colors.get("text", "#d0d5da")),
            strong=colors.get("textStrong", colors.get("text", "#f5f7f8")),
            muted=colors.get("textSecondary", colors.get("muted", "#9aa3ad")),
            rule=colors.get("border", "#2a313a"),
            table_header=colors.get("surfaceAlt", ""),
            syntax_keyword=colors["accent"] if colored else "",
            syntax_string=colors.get("success", "") if colored else "",
            syntax_comment=colors.get("muted", colors.get("textSecondary", "#9aa3ad")) if colored else "",
            syntax_number=colors.get("warning", colors.get("inlineCodeText", colors["accent"])) if colored else "",
        )
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
        split = _streaming_heading_split(source)
        if split > 0:
            prefix_src, tail_src = source[:split], source[split:]
            prefix_key = (style, prefix_src)
            if prefix_key in self._stream_prefix_cache:
                self._stream_prefix_cache.move_to_end(prefix_key)
                prefix_html = self._stream_prefix_cache[prefix_key]
            else:
                prefix_html = _render_tokens(parser.parse(prefix_src), style)
                if len(prefix_src) + len(prefix_html) <= 262_144:
                    while len(self._stream_prefix_cache) >= 4:
                        self._stream_prefix_cache.popitem(last=False)
                    self._stream_prefix_cache[prefix_key] = prefix_html
            rendered = prefix_html + _render_tokens(parser.parse(tail_src), style)
        else:
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
                    style.code, style.link, style.added, style.removed, style.language,
                    style.inline_code, style.body, style.strong, style.muted, style.rule,
                    style.table_header, style.syntax_keyword, style.syntax_string,
                    style.syntax_comment, style.syntax_number)))
