"""Qt controller for Kira Interactive Canvas / Artifacts and Granular Diff Hunks."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QImage,
    QSyntaxHighlighter,
    QTextCharFormat,
)

from kira.service.canvas import compute_hunks, find_chromium_executable
from kira.ui.browser_frames import BrowserFrameStore
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property
from kira.ui.service_errors import service_error_text

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


def _qt_button_name(btn: int) -> str:
    if btn == 1:
        return "left"
    if btn == 2:
        return "right"
    if btn == 4:
        return "middle"
    return "none"


class _LiveCanvasStream:
    """Background Chromium CDP screencast + pointer/resize bridge for interactive HTML/Canvas artifacts."""

    def __init__(self, controller: CanvasController) -> None:
        self._controller = controller
        self._cmd_queue: queue.SimpleQueue[tuple[Any, ...]] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active_html = ""
        self._width = 720
        self._height = 480
        self._seq = 0
        self._pending_move: tuple[float, float, int] | None = None

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run_thread, name="kira-canvas-live", daemon=True
            )
            self._thread.start()

    def start_or_update(self, html_path: str, width: int, height: int) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        w = max(240, min(2560, int(width or 720)))
        h = max(180, min(1600, int(height or 480)))
        self._width, self._height = w, h
        self._ensure_thread()
        if html_path != self._active_html:
            self._active_html = html_path
            self._cmd_queue.put(("load", html_path, w, h))
        else:
            self._cmd_queue.put(("resize", w, h))

    def resize(self, width: int, height: int) -> None:
        if not self._active_html or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        w = max(240, min(2560, int(width or 720)))
        h = max(180, min(1600, int(height or 480)))
        if (w, h) == (self._width, self._height):
            return
        self._width, self._height = w, h
        self._cmd_queue.put(("resize", w, h))

    def pointer(
        self,
        event_type: str,
        x: float,
        y: float,
        button: int,
        buttons: int,
        delta_x: float,
        delta_y: float,
    ) -> None:
        if not self._active_html:
            return
        if event_type == "mouseMoved":
            self._pending_move = (float(x), float(y), int(buttons))
            self._cmd_queue.put(("move_flush",))
        else:
            self._cmd_queue.put(
                ("mouse", event_type, float(x), float(y), int(button), int(buttons), float(delta_x), float(delta_y))
            )

    def stop(self) -> None:
        if self._active_html:
            self._active_html = ""
            self._cmd_queue.put(("stop",))

    def _run_thread(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_loop())
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                loop.close()

    async def _async_loop(self) -> None:
        exe = find_chromium_executable(None)
        if exe is None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return

        async with async_playwright() as p:
            browser = None
            for use_sandbox in (True, False):
                try:
                    browser = await p.chromium.launch(
                        executable_path=str(exe),
                        headless=True,
                        chromium_sandbox=use_sandbox,
                        args=[
                            "--disable-background-networking",
                            "--disable-extensions",
                            "--disable-sync",
                            "--hide-scrollbars",
                            "--host-resolver-rules=MAP * ~NOTFOUND",
                        ],
                    )
                    break
                except Exception:
                    continue
            if browser is None:
                return

            try:
                context = await browser.new_context(
                    viewport={"width": self._width, "height": self._height},
                    device_scale_factor=1,
                    java_script_enabled=True,
                    offline=True,
                    permissions=[],
                    service_workers="block",
                )
                page = await context.new_page()
                async def _handle_route(route: Any) -> None:
                    if route.request.url.startswith(("file://", "data:", "blob:", "about:")):
                        await route.continue_()
                    else:
                        await route.abort()

                await page.route("**/*", _handle_route)
                cdp = await context.new_cdp_session(page)
                await cdp.send("Page.enable")
                streaming = False
                cur_w, cur_h = self._width, self._height

                def on_frame(params: dict[str, Any]) -> None:
                    sid = params.get("sessionId")
                    if sid is not None:
                        with contextlib.suppress(Exception):
                            asyncio.get_running_loop().create_task(
                                cdp.send("Page.screencastFrameAck", {"sessionId": sid})
                            )
                    if not self._active_html:
                        return
                    b64 = params.get("data", "")
                    if not isinstance(b64, str) or not b64:
                        return
                    try:
                        raw = base64.b64decode(b64)
                        img = QImage.fromData(raw)
                        if not img.isNull():
                            self._seq += 1
                            key = f"live-{self._seq}"
                            self._controller.frames.publish(key, img)
                            self._controller.liveFramePushed.emit(f"image://kira-canvas/{key}")
                    except Exception:
                        pass

                cdp.on("Page.screencastFrame", on_frame)

                while True:
                    try:
                        cmd = self._cmd_queue.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.012)
                        continue

                    kind = cmd[0]
                    if kind == "stop":
                        if streaming:
                            with contextlib.suppress(Exception):
                                await cdp.send("Page.stopScreencast")
                            streaming = False
                        self._controller.frames.clear()
                        self._controller.liveFramePushed.emit("")
                    elif kind == "load":
                        _, html_path, w, h = cmd
                        if not Path(html_path).is_file():
                            continue
                        cur_w, cur_h = w, h
                        with contextlib.suppress(Exception):
                            if streaming:
                                await cdp.send("Page.stopScreencast")
                                streaming = False
                            await page.set_viewport_size({"width": cur_w, "height": cur_h})
                            await page.goto(Path(html_path).as_uri(), wait_until="load", timeout=6000)
                            await cdp.send(
                                "Page.startScreencast",
                                {
                                    "format": "jpeg",
                                    "quality": 82,
                                    "maxWidth": cur_w,
                                    "maxHeight": cur_h,
                                    "everyNthFrame": 1,
                                },
                            )
                            streaming = True
                    elif kind == "resize":
                        _, w, h = cmd
                        # Drain any queued resizes to apply only the latest size
                        while True:
                            try:
                                nxt = self._cmd_queue.get_nowait()
                                if nxt[0] == "resize":
                                    _, w, h = nxt
                                else:
                                    self._cmd_queue.put(nxt)
                                    break
                            except queue.Empty:
                                break
                        if (w, h) != (cur_w, cur_h):
                            cur_w, cur_h = w, h
                            with contextlib.suppress(Exception):
                                await page.set_viewport_size({"width": cur_w, "height": cur_h})
                                if streaming:
                                    await cdp.send("Page.stopScreencast")
                                    await cdp.send(
                                        "Page.startScreencast",
                                        {
                                            "format": "jpeg",
                                            "quality": 82,
                                            "maxWidth": cur_w,
                                            "maxHeight": cur_h,
                                            "everyNthFrame": 1,
                                        },
                                    )
                    elif kind == "move_flush":
                        mv = self._pending_move
                        self._pending_move = None
                        if mv is not None and streaming:
                            mx, my, mbuttons = mv
                            with contextlib.suppress(Exception):
                                await cdp.send(
                                    "Input.dispatchMouseEvent",
                                    {
                                        "type": "mouseMoved",
                                        "x": mx,
                                        "y": my,
                                        "button": "none",
                                        "buttons": mbuttons,
                                    },
                                )
                    elif kind == "mouse" and streaming:
                        _, ev_type, mx, my, mbtn, mbuttons, dx, dy = cmd
                        payload: dict[str, Any] = {
                            "type": ev_type,
                            "x": mx,
                            "y": my,
                            "button": _qt_button_name(mbtn),
                            "buttons": mbuttons,
                            "clickCount": 1 if ev_type in {"mousePressed", "mouseReleased"} else 0,
                        }
                        if ev_type == "mouseWheel":
                            payload["deltaX"] = dx
                            payload["deltaY"] = dy
                        with contextlib.suppress(Exception):
                            await cdp.send("Input.dispatchMouseEvent", payload)
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()


PRESET_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "mermaid": (
        "Architettura del flusso",
        "mermaid",
        "flowchart TD\n"
        "    User([Utente]) -->|Richiesta| Gateway[API Gateway]\n"
        "    Gateway -->|Verifica| Auth{Autenticato?}\n"
        "    Auth -->|Sì| Service[Servizio Core]\n"
        "    Auth -->|No| Error[401 Non autorizzato]\n"
        "    Service -->|Query| DB[(Database SQLite)]\n",
    ),
    "html": (
        "Componente UI Interattivo",
        "html",
        "<div class=\"card\">\n"
        "  <h1>Dashboard Progetto</h1>\n"
        "  <h3>Stato dei servizi in tempo reale</h3>\n"
        "  <p>Tutti i nodi locali e l'indice semantico AST sono operativi.</p>\n"
        "  <input placeholder=\"Cerca metrica o componente...\" />\n"
        "  <button>Aggiorna stato</button>\n"
        "  <button>Esporta report</button>\n"
        "</div>\n",
    ),
    "svg": (
        "Diagramma Vettoriale SVG",
        "svg",
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">\n'
        '  <rect width="640" height="360" rx="16" fill="#0f172a"/>\n'
        '  <circle cx="180" cy="180" r="76" fill="#1e293b" stroke="#38bdf8" stroke-width="4"/>\n'
        '  <circle cx="460" cy="180" r="76" fill="#1e293b" stroke="#a78bfa" stroke-width="4"/>\n'
        '  <line x1="256" y1="180" x2="384" y2="180" stroke="#60a5fa" stroke-width="4"/>\n'
        '  <text x="180" y="185" fill="#f8fafc" font-family="sans-serif" font-size="16" font-weight="bold" text-anchor="middle">Kira Core</text>\n'
        '  <text x="460" y="185" fill="#f8fafc" font-family="sans-serif" font-size="16" font-weight="bold" text-anchor="middle">Live Canvas</text>\n'
        "</svg>\n",
    ),
    "markdown": (
        "Specifica Tecnica",
        "markdown",
        "# Specifica Tecnica\n\n"
        "## Obiettivo\n"
        "Definire l'architettura modulare e i contratti di sicurezza del componente.\n\n"
        "## Punti chiave\n"
        "- Isolamento per progetto su database locale\n"
        "- Revisioni incrementali con diff blocco per blocco\n"
        "- Riscrittura mirata su selezione di righe\n",
    ),
    "code": (
        "Modulo Python",
        "python",
        "def process_items(items: list[str]) -> list[str]:\n"
        '    """Normalizza e filtra gli elementi validi."""\n'
        "    cleaned = [item.strip() for item in items if item and item.strip()]\n"
        "    return sorted(set(cleaned))\n",
    ),
}


_EXTENSION_LANG_MAP: dict[str, tuple[str, str]] = {
    ".py": ("code", "python"),
    ".pyi": ("code", "python"),
    ".js": ("code", "javascript"),
    ".mjs": ("code", "javascript"),
    ".cjs": ("code", "javascript"),
    ".ts": ("code", "typescript"),
    ".tsx": ("code", "tsx"),
    ".jsx": ("code", "jsx"),
    ".qml": ("code", "qml"),
    ".json": ("code", "json"),
    ".yaml": ("code", "yaml"),
    ".yml": ("code", "yaml"),
    ".toml": ("code", "toml"),
    ".ini": ("code", "ini"),
    ".cfg": ("code", "ini"),
    ".sh": ("code", "bash"),
    ".bash": ("code", "bash"),
    ".zsh": ("code", "zsh"),
    ".css": ("code", "css"),
    ".scss": ("code", "scss"),
    ".sql": ("code", "sql"),
    ".rs": ("code", "rust"),
    ".go": ("code", "go"),
    ".c": ("code", "c"),
    ".h": ("code", "c"),
    ".cpp": ("code", "cpp"),
    ".hpp": ("code", "cpp"),
    ".java": ("code", "java"),
    ".kt": ("code", "kotlin"),
    ".csv": ("code", "csv"),
    ".tsv": ("code", "tsv"),
    ".xml": ("code", "xml"),
    ".txt": ("markdown", "text"),
    ".log": ("code", "log"),
    ".md": ("markdown", "markdown"),
    ".markdown": ("markdown", "markdown"),
    ".rst": ("markdown", "rst"),
    ".html": ("html", "html"),
    ".htm": ("html", "html"),
    ".svg": ("svg", "svg"),
    ".mmd": ("mermaid", "mermaid"),
    ".mermaid": ("mermaid", "mermaid"),
}

_IGNORED_DIRS: frozenset[str] = frozenset({
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".idea",
    ".vscode",
})

_BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".whl", ".so", ".dll",
    ".dylib", ".exe", ".bin", ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".wav", ".ogg", ".mp4", ".webm",
})


def _detect_file_kind_and_language(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext in _EXTENSION_LANG_MAP:
        return _EXTENSION_LANG_MAP[ext]
    return ("code", ext.lstrip(".") or "text")


def _scan_project_files(root: Path, query: str = "", limit: int = 120) -> list[Json]:
    if not root.is_dir():
        return []
    q = query.strip().lower()
    results: list[Json] = []
    scanned = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".kira")
            )
            for fname in sorted(filenames):
                if fname.startswith(".") and fname not in {".env.example", ".gitignore"}:
                    continue
                scanned += 1
                if scanned > 3000:
                    break
                fpath = Path(dirpath) / fname
                if fpath.suffix.lower() in _BINARY_EXTENSIONS:
                    continue
                try:
                    rel = fpath.relative_to(root).as_posix()
                except ValueError:
                    continue
                if q and q not in rel.lower():
                    continue
                kind, lang = _detect_file_kind_and_language(fpath)
                results.append(
                    {
                        "path": str(fpath),
                        "rel_path": rel,
                        "name": fname,
                        "kind": kind,
                        "language": lang,
                    }
                )
                if len(results) >= limit:
                    break
            if scanned > 3000 or len(results) >= limit:
                break
    except OSError:
        pass
    return results


class KiraSyntaxHighlighter(QSyntaxHighlighter):
    """Native Qt syntax highlighter for Python, JS/TS, QML, HTML/SVG, JSON, CSS, Shell, SQL, and Markdown."""

    def __init__(self, document: Any) -> None:
        super().__init__(document)
        self._language = "python"

        def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.DemiBold)
            if italic:
                f.setFontItalic(True)
            return f

        self._kw_fmt = _fmt("#c084fc", bold=True)
        self._type_fmt = _fmt("#38bdf8")
        self._def_fmt = _fmt("#60a5fa", bold=True)
        self._dec_fmt = _fmt("#fbbf24")
        self._str_fmt = _fmt("#4ade80")
        self._num_fmt = _fmt("#fb923c")
        self._cmt_fmt = _fmt("#64748b", italic=True)
        self._tag_fmt = _fmt("#f472b6", bold=True)
        self._attr_fmt = _fmt("#93c5fd")

        self._rules: list[tuple[re.Pattern[str], int, QTextCharFormat]] = [
            (
                re.compile(
                    r"\b(?:def|class|return|import|from|if|elif|else|for|while|try|except|finally|"
                    r"with|as|pass|break|continue|raise|yield|async|await|lambda|in|not|and|or|is|"
                    r"global|nonlocal|assert|del|match|case|const|let|var|function|export|default|"
                    r"interface|type|extends|implements|new|this|typeof|instanceof|switch|throw|"
                    r"property|readonly|signal|required|id|anchors|Layout|SplitView|fn|pub|struct|"
                    r"enum|impl|trait|use|mod|crate|where|mut|ref|go|defer|package|chan|select|"
                    r"SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|INDEX|"
                    r"JOIN|LEFT|INNER|GROUP|ORDER|BY|LIMIT|HAVING)\b"
                ),
                0,
                self._kw_fmt,
            ),
            (
                re.compile(
                    r"\b(?:True|False|None|true|false|null|undefined|self|cls|int|str|float|bool|"
                    r"list|dict|set|tuple|bytes|Any|Optional|Callable|Path|Exception|ValueError|"
                    r"TypeError|OSError|RuntimeError|print|len|range|enumerate|zip|map|filter|"
                    r"sorted|min|max|sum|abs|isinstance|getattr|setattr|hasattr|super|"
                    r"Rectangle|ColumnLayout|RowLayout|GridLayout|StackLayout|ScrollView|ListView|"
                    r"Repeater|Text|TextField|TextArea|MouseArea|Item|Connections|KButton|KIcon|"
                    r"KComboBox|ReadText)\b"
                ),
                0,
                self._type_fmt,
            ),
            (re.compile(r"\b(?:def|class|function)\s+([A-Za-z_][A-Za-z0-9_]*)"), 1, self._def_fmt),
            (re.compile(r"@[A-Za-z_][A-Za-z0-9_.]*"), 0, self._dec_fmt),
            (re.compile(r"</?[A-Za-z_][A-Za-z0-9_.:-]*|/?>"), 0, self._tag_fmt),
            (re.compile(r"\b([A-Za-z_][A-Za-z0-9_-]*)\s*="), 1, self._attr_fmt),
            (re.compile(r"\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b"), 0, self._num_fmt),
            (re.compile(r'"""[^"]*"""|\'\'\'[^\']*\'\'\'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''), 0, self._str_fmt),
            (re.compile(r"#[^\n]*|//[^\n]*"), 0, self._cmt_fmt),
        ]

    def set_language(self, language: str) -> None:
        lang = (language or "text").lower()
        if lang != self._language:
            self._language = lang
            self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if not text:
            return
        for pattern, group_idx, fmt in self._rules:
            for match in pattern.finditer(text):
                start = match.start(group_idx)
                length = match.end(group_idx) - start
                if start >= 0 and length > 0:
                    self.setFormat(start, length, fmt)


class CanvasController(QObject):
    changed = Signal()
    liveFrameChanged = Signal()
    liveFramePushed = Signal(str)

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.frames = BrowserFrameStore()
        self._live = _LiveCanvasStream(self)
        self._live_frame_url = ""
        self._viewport_width = 720
        self._viewport_height = 480
        self._project_id = ""
        self._generation = 0
        self._pane_visible = False
        self._expanded = False
        self._busy = False
        self._error = ""
        self._status_text = ""
        self._view_mode = "preview"
        self._artifacts: list[Json] = []
        self._current: Json = {}
        self._diff_text = ""
        self._diff_hunks: list[Json] = []
        self._file_tabs: list[Json] = []
        self._active_file_path = ""
        self._file_explorer_visible = False
        self._project_files: list[Json] = []
        self._file_search_query = ""
        self._highlighter: KiraSyntaxHighlighter | None = None
        self.liveFramePushed.connect(self._onLiveFramePushed)
        self.changed.connect(self._syncLiveStream)
        workspace.projectOpened.connect(self.syncProject)
        workspace.eventApplied.connect(self._onRunEvent)
        workspace.client.disconnected.connect(self._disconnected)

    @Slot(QObject)
    def attachHighlighter(self, quick_doc: QObject | None) -> None:
        if quick_doc is None:
            return
        with contextlib.suppress(Exception):
            text_doc = quick_doc.textDocument()  # type: ignore[attr-defined]
            if text_doc is not None:
                self._highlighter = KiraSyntaxHighlighter(text_doc)
                self._highlighter.set_language(self.currentLanguage)

    def _active_file_tab(self) -> Json | None:
        if not self._active_file_path:
            return None
        for tab in self._file_tabs:
            if tab.get("path") == self._active_file_path:
                return tab
        return None

    @Slot(str)
    def _onLiveFramePushed(self, url: str) -> None:
        if url != self._live_frame_url:
            self._live_frame_url = url
            self.liveFrameChanged.emit()

    def _syncLiveStream(self) -> None:
        if self._highlighter is not None:
            with contextlib.suppress(Exception):
                self._highlighter.set_language(self.currentLanguage)
        tab = self._active_file_tab()
        if tab is not None:
            html_path = str(tab.get("path", "")) if tab.get("kind") == "html" else ""
        else:
            html_path = str(self._current.get("preview_html_path", ""))
        if (
            self._pane_visible
            and self._view_mode == "preview"
            and self.currentKind == "html"
            and html_path
            and Path(html_path).is_file()
        ):
            self._live.start_or_update(html_path, self._viewport_width, self._viewport_height)
        else:
            self._live.stop()
            if self._live_frame_url:
                self._live_frame_url = ""
                self.liveFrameChanged.emit()

    def retranslate(self) -> None:
        self._error = str(refresh_ui(self._error))
        self._status_text = str(refresh_ui(self._status_text))
        self.changed.emit()

    @qt_property(bool, notify=changed)
    def paneVisible(self) -> bool:
        return self._pane_visible

    @qt_property(bool, notify=changed)
    def expanded(self) -> bool:
        return self._expanded

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property(str, notify=changed)
    def statusText(self) -> str:
        return str(refresh_ui(self._status_text))

    @qt_property(str, notify=changed)
    def viewMode(self) -> str:
        return self._view_mode

    @qt_property("QVariantList", notify=changed)
    def artifacts(self) -> list[Json]:
        return self._artifacts

    @qt_property("QVariantList", notify=changed)
    def openFileTabs(self) -> list[Json]:
        return [
            {
                "path": t["path"],
                "rel_path": t["rel_path"],
                "name": t["name"],
                "language": t["language"],
                "kind": t["kind"],
                "dirty": bool(t.get("dirty", False)),
                "line_count": int(t.get("line_count", 1)),
            }
            for t in self._file_tabs
        ]

    @qt_property(str, notify=changed)
    def activeFilePath(self) -> str:
        return self._active_file_path

    @qt_property(bool, notify=changed)
    def isProjectFileActive(self) -> bool:
        return self._active_file_tab() is not None

    @qt_property(bool, notify=changed)
    def activeFileDirty(self) -> bool:
        tab = self._active_file_tab()
        return bool(tab.get("dirty", False)) if tab is not None else False

    @qt_property(bool, notify=changed)
    def fileExplorerVisible(self) -> bool:
        return self._file_explorer_visible

    @qt_property("QVariantList", notify=changed)
    def projectFiles(self) -> list[Json]:
        return self._project_files

    @qt_property(str, notify=changed)
    def fileSearchQuery(self) -> str:
        return self._file_search_query

    @qt_property("QVariantMap", notify=changed)
    def currentArtifact(self) -> Json:
        tab = self._active_file_tab()
        if tab is not None:
            return dict(tab)
        return self._current

    @qt_property(str, notify=changed)
    def currentId(self) -> str:
        tab = self._active_file_tab()
        if tab is not None:
            return f"file:{tab['rel_path']}"
        return str(self._current.get("id", ""))

    @qt_property(str, notify=changed)
    def currentTitle(self) -> str:
        tab = self._active_file_tab()
        if tab is not None:
            return str(tab.get("rel_path", tab.get("name", "")))
        return str(self._current.get("title", ""))

    @qt_property(str, notify=changed)
    def currentKind(self) -> str:
        tab = self._active_file_tab()
        if tab is not None:
            return str(tab.get("kind", "code"))
        return str(self._current.get("kind", ""))

    @qt_property(str, notify=changed)
    def currentLanguage(self) -> str:
        tab = self._active_file_tab()
        if tab is not None:
            return str(tab.get("language", "text"))
        return str(self._current.get("language", ""))

    @qt_property(str, notify=changed)
    def currentContent(self) -> str:
        tab = self._active_file_tab()
        if tab is not None:
            return str(tab.get("content", ""))
        return str(self._current.get("content", ""))

    @qt_property(int, notify=changed)
    def currentRevision(self) -> int:
        tab = self._active_file_tab()
        if tab is not None:
            return int(tab.get("revision", 1) or 1)
        return int(self._current.get("revision", self._current.get("current_revision", 0)) or 0)

    @qt_property(int, notify=changed)
    def currentLineCount(self) -> int:
        tab = self._active_file_tab()
        if tab is not None:
            return int(tab.get("line_count", 1) or 1)
        return int(self._current.get("line_count", 1) or 1)

    @qt_property(str, notify=changed)
    def previewUrl(self) -> str:
        tab = self._active_file_tab()
        if tab is not None:
            if tab.get("kind") == "svg" and Path(tab["path"]).is_file():
                return Path(tab["path"]).as_uri()
            return ""
        url = str(self._current.get("preview_url", ""))
        rev = self.currentRevision
        return f"{url}?rev={rev}" if url else ""

    @qt_property(str, notify=liveFrameChanged)
    def liveFrameUrl(self) -> str:
        return self._live_frame_url

    @qt_property(str, notify=changed)
    def previewHtmlUrl(self) -> str:
        tab = self._active_file_tab()
        if tab is not None:
            if tab.get("kind") == "html" and Path(tab["path"]).is_file():
                return Path(tab["path"]).as_uri()
            return ""
        html_path = str(self._current.get("preview_html_path", ""))
        if html_path and Path(html_path).is_file():
            return Path(html_path).as_uri()
        return str(self._current.get("preview_html_url", ""))

    @qt_property("QVariantList", notify=changed)
    def history(self) -> list[Json]:
        tab = self._active_file_tab()
        if tab is not None:
            return list(tab.get("history", []))
        return list(self._current.get("history", []))

    @qt_property(str, notify=changed)
    def diffText(self) -> str:
        return self._diff_text

    @qt_property("QVariantList", notify=changed)
    def diffHunks(self) -> list[Json]:
        return self._diff_hunks

    def _selected_project_id(self) -> str:
        return str(self.workspace.project.get("id", ""))

    def _project_root_path(self) -> Path | None:
        root_str = str(self.workspace.project.get("root", "")).strip()
        if not root_str:
            return None
        try:
            root = Path(root_str).resolve(strict=True)
            return root if root.is_dir() else None
        except OSError:
            return None

    def _disconnected(self) -> None:
        self._generation += 1
        self._busy = False
        self._pane_visible = False
        self._error = ""
        self._live.stop()
        self.changed.emit()

    @Slot(int, int)
    def setViewport(self, width: int, height: int) -> None:
        if width < 120 or height < 120:
            return
        self._viewport_width = max(240, min(2560, int(width)))
        self._viewport_height = max(180, min(1600, int(height)))
        self._live.resize(self._viewport_width, self._viewport_height)

    @Slot(str, float, float, int, int, float, float)
    def sendPointer(
        self,
        event_type: str,
        x: float,
        y: float,
        button: int,
        buttons: int,
        delta_x: float,
        delta_y: float,
    ) -> None:
        self._live.pointer(event_type, x, y, button, buttons, delta_x, delta_y)

    @Slot()
    def syncProject(self) -> None:
        pid = self._selected_project_id()
        if pid != self._project_id:
            self._generation += 1
            self._project_id = pid
            self._pane_visible = False
            self._expanded = False
            self._busy = False
            self._error = ""
            self._status_text = ""
            self._artifacts = []
            self._current = {}
            self._diff_text = ""
            self._diff_hunks = []
            self._file_tabs = []
            self._active_file_path = ""
            self._file_explorer_visible = False
            self._project_files = []
            self._file_search_query = ""
            self.changed.emit()
        if self._pane_visible and pid:
            self.refresh()

    @Slot()
    def showCanvas(self) -> None:
        if not self._selected_project_id():
            return
        self._pane_visible = True
        self._error = ""
        self.changed.emit()
        self.refresh()

    @Slot()
    def hideCanvas(self) -> None:
        if self._pane_visible or self._expanded:
            self._pane_visible = False
            self._expanded = False
            self.changed.emit()

    @Slot()
    def toggleCanvas(self) -> None:
        if self._pane_visible:
            self.hideCanvas()
        else:
            self.showCanvas()

    @Slot()
    def toggleExpanded(self) -> None:
        if self._pane_visible:
            self._expanded = not self._expanded
            self.changed.emit()

    @Slot()
    def toggleFileExplorer(self) -> None:
        self._file_explorer_visible = not self._file_explorer_visible
        if self._file_explorer_visible and not self._project_files:
            self._refresh_project_files()
        self.changed.emit()

    @Slot()
    def openProjectExplorer(self) -> None:
        root = self._project_root_path()
        if root is None:
            self._error = tr("Apri un progetto per consultare i suoi file.")
            self.changed.emit()
            return
        with contextlib.suppress(Exception):
            self.workspace.web.hideBrowser()
            self.workspace.studio.hideStudio()
        self._pane_visible = True
        self._file_explorer_visible = True
        self._error = ""
        self._refresh_project_files()
        self.changed.emit()

    @Slot(str)
    def searchProjectFiles(self, query: str) -> None:
        self._file_search_query = query
        self._refresh_project_files()
        self.changed.emit()

    def _refresh_project_files(self) -> None:
        root = self._project_root_path()
        if root is None:
            self._project_files = []
            return
        self._project_files = _scan_project_files(root, self._file_search_query)

    @Slot(str, result=bool)
    def openProjectFile(self, path: str) -> bool:
        root = self._project_root_path()
        if root is None:
            self._error = tr("Apri un progetto per consultare i suoi file.")
            self.workspace._problem(self._error)
            self.changed.emit()
            return False
        raw = path.strip()
        if not raw:
            return False
        try:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve(strict=True)
            rel = resolved.relative_to(root).as_posix()
            if not resolved.is_file():
                raise ValueError(tr("Il collegamento non indica un file regolare del progetto."))
            if resolved.stat().st_size > 2 * 1024 * 1024:
                raise ValueError(tr("Il file supera il limite di 2 MiB per l'editor integrato."))
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            msg = tr("Il file è binario e non può essere aperto come testo nell'editor di Kira.")
            self._error = msg
            self.workspace._problem(msg)
            self.changed.emit()
            return False
        except (OSError, ValueError, RuntimeError) as exc:
            msg = str(exc) or tr("File non disponibile o fuori dalla cartella del progetto.")
            self._error = msg
            self.workspace._problem(msg)
            self.changed.emit()
            return False

        with contextlib.suppress(Exception):
            self.workspace.web.hideBrowser()
            self.workspace.studio.hideStudio()

        kind, lang = _detect_file_kind_and_language(resolved)
        resolved_str = str(resolved)
        existing = next((t for t in self._file_tabs if t.get("path") == resolved_str), None)
        if existing is not None:
            if not existing.get("dirty"):
                existing["content"] = content
                existing["saved_content"] = content
                existing["line_count"] = max(1, len(content.splitlines()))
        else:
            self._file_tabs.append(
                {
                    "path": resolved_str,
                    "rel_path": rel,
                    "name": resolved.name,
                    "kind": kind,
                    "language": lang,
                    "content": content,
                    "saved_content": content,
                    "prev_content": content,
                    "dirty": False,
                    "revision": 1,
                    "line_count": max(1, len(content.splitlines())),
                    "history": [
                        {
                            "revision": 1,
                            "author": "user",
                            "summary": tr("File del progetto aperto ({path})", path=rel),
                        }
                    ],
                }
            )
        self._active_file_path = resolved_str
        self._pane_visible = True
        self._view_mode = "code"
        self._error = ""
        self._status_text = tr("File aperto nell'editor di Kira: {path}", path=rel)
        if not self._project_files:
            self._refresh_project_files()
        tab = self._active_file_tab()
        if tab is not None:
            self._diff_text, self._diff_hunks = compute_hunks(
                str(tab.get("prev_content", "")),
                str(tab.get("content", "")),
                rel,
            )
        self.changed.emit()
        return True

    @Slot(str)
    def selectFileTab(self, path: str) -> None:
        target = path.strip()
        tab = next((t for t in self._file_tabs if t.get("path") == target or t.get("rel_path") == target), None)
        if tab is None:
            return
        self._active_file_path = str(tab["path"])
        self._error = ""
        self._diff_text, self._diff_hunks = compute_hunks(
            str(tab.get("prev_content", "")),
            str(tab.get("content", "")),
            str(tab.get("rel_path", "file")),
        )
        self.changed.emit()

    @Slot(str)
    def closeFileTab(self, path: str) -> None:
        target = path.strip()
        self._file_tabs = [
            t for t in self._file_tabs if t.get("path") != target and t.get("rel_path") != target
        ]
        if self._active_file_path == target or not any(
            t.get("path") == self._active_file_path for t in self._file_tabs
        ):
            self._active_file_path = str(self._file_tabs[-1]["path"]) if self._file_tabs else ""
        self.changed.emit()

    @Slot(str)
    def updateEditorDraft(self, content: str) -> None:
        tab = self._active_file_tab()
        if tab is None:
            return
        if tab.get("content") == content:
            return
        tab["content"] = content
        tab["line_count"] = max(1, len(content.splitlines()))
        new_dirty = content != tab.get("saved_content", "")
        if bool(tab.get("dirty")) != new_dirty:
            tab["dirty"] = new_dirty
            self.changed.emit()

    @Slot(str, result=bool)
    def saveProjectFile(self, content: str) -> bool:
        tab = self._active_file_tab()
        root = self._project_root_path()
        if tab is None or root is None:
            return False
        try:
            target = Path(str(tab["path"])).resolve(strict=True)
            rel = target.relative_to(root).as_posix()
            target.write_text(content, encoding="utf-8")
        except (OSError, ValueError) as exc:
            self._error = tr("Impossibile salvare il file: {error}", error=str(exc))
            self.changed.emit()
            return False

        prev_saved = str(tab.get("saved_content", content))
        tab["prev_content"] = prev_saved
        tab["saved_content"] = content
        tab["content"] = content
        tab["dirty"] = False
        tab["line_count"] = max(1, len(content.splitlines()))
        rev = int(tab.get("revision", 1)) + 1
        tab["revision"] = rev
        history = list(tab.get("history", []))
        history.append(
            {
                "revision": rev,
                "author": "user",
                "summary": tr("Salvato su disco ({path})", path=rel),
                "content": content,
            }
        )
        tab["history"] = history[-25:]
        self._diff_text, self._diff_hunks = compute_hunks(prev_saved, content, rel)
        self._error = ""
        self._status_text = tr("File salvato su disco: {path}", path=rel)
        self.changed.emit()
        return True

    @Slot(int, int, str)
    def attachSelectionToChat(self, start_line: int, end_line: int, selected_text: str = "") -> None:
        title = self.currentTitle
        content = self.currentContent
        if not title or not content:
            return
        lines = content.splitlines()
        lo = max(1, min(start_line, max(1, len(lines))))
        hi = max(lo, min(end_line, max(1, len(lines))))
        clean_sel = selected_text.strip()
        if clean_sel:
            excerpt = clean_sel
            win_title = title
            source_label = f"Kira Editor · {title}"
        else:
            excerpt = "\n".join(lines[lo - 1 : hi])
            win_title = f"{title}:{lo}-{hi}"
            source_label = f"Kira Editor · {title}:{lo}-{hi}"
        if not excerpt:
            return
        self.workspace.ambient.setManualContext("Kira Editor", win_title, excerpt)
        self._status_text = tr(
            "Selezione allegata alla chat come chip di contesto ({label}).",
            label=source_label,
        )
        self.changed.emit()

    @Slot(str)
    def setViewMode(self, mode: str) -> None:
        if mode in {"preview", "code", "diff", "history"} and mode != self._view_mode:
            self._view_mode = mode
            self.changed.emit()
            if mode == "diff" and self.currentId:
                self.refreshDiff()

    @Slot()
    def openInteractivePreview(self) -> None:
        tab = self._active_file_tab()
        if tab is not None and tab.get("kind") == "html":
            html_path_str = str(tab.get("path", ""))
        else:
            html_path_str = str(self._current.get("preview_html_path", ""))
        if not html_path_str:
            return
        html_file = Path(html_path_str)
        if not html_file.is_file():
            return
        exe = find_chromium_executable(None)
        if exe is not None:
            try:
                profile_dir = html_file.parent / "interactive-profile"
                profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                subprocess.Popen(
                    [
                        str(exe),
                        f"--app={html_file.as_uri()}",
                        "--window-size=1040,680",
                        f"--user-data-dir={profile_dir}",
                        "--disable-background-networking",
                        "--disable-extensions",
                        "--disable-sync",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--host-resolver-rules=MAP * ~NOTFOUND",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._status_text = tr("Finestra interattiva 60fps aperta in modalità isolata offline.")
                self.changed.emit()
                return
            except OSError:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_file)))
        self._status_text = tr("Finestra interattiva aperta.")
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if self._file_explorer_visible:
            self._refresh_project_files()
        tab = self._active_file_tab()
        if tab is not None and not tab.get("dirty"):
            with contextlib.suppress(OSError, UnicodeDecodeError):
                fresh = Path(str(tab["path"])).read_text(encoding="utf-8")
                tab["content"] = fresh
                tab["saved_content"] = fresh
                tab["line_count"] = max(1, len(fresh.splitlines()))
        pid = self._selected_project_id()
        if not pid or not self.workspace.connected:
            self.changed.emit()
            return
        gen = self._generation

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._artifacts = list(data.get("artifacts", []))
            if self._active_file_tab() is not None:
                self.changed.emit()
                return
            if self._current:
                cid = self._current.get("id")
                if cid and any(a.get("id") == cid for a in self._artifacts):
                    self.selectArtifact(str(cid))
                    return
            if self._artifacts:
                self.selectArtifact(str(self._artifacts[0]["id"]))
            else:
                self._current = {}
                self._diff_text = ""
                self._diff_hunks = []
                self.changed.emit()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self.changed.emit()
        self.workspace.client.request("canvas.list", {"project_id": pid}, done, failed)

    @Slot(str)
    def selectArtifact(self, artifact_id: str) -> None:
        pid = self._selected_project_id()
        aid = artifact_id.strip()
        if not pid or not aid or not self.workspace.connected:
            return
        self._active_file_path = ""
        gen = self._generation

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._error = ""
            self._current = dict(data.get("artifact", {}))
            self.changed.emit()
            if self._view_mode == "diff":
                self.refreshDiff()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self.changed.emit()
        self.workspace.client.request(
            "canvas.get", {"project_id": pid, "artifact_id": aid}, done, failed
        )

    @Slot(str)
    def createPreset(self, kind: str) -> None:
        preset = PRESET_TEMPLATES.get(kind, PRESET_TEMPLATES["mermaid"])
        title, lang, content = preset
        self.createArtifact(tr(title), kind, lang, content)

    @Slot(str, str, str, str)
    def createArtifact(self, title: str, kind: str, language: str, content: str) -> None:
        pid = self._selected_project_id()
        if not pid or not self.workspace.connected:
            return
        self._active_file_path = ""
        gen = self._generation
        clean_title = title.strip() or tr("Nuovo artefatto")
        clean_kind = kind.strip().lower() if kind.strip().lower() in PRESET_TEMPLATES else "code"
        clean_lang = language.strip().lower() or clean_kind

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._error = ""
            self._current = dict(data.get("artifact", {}))
            self._status_text = tr("Artefatto creato: {title}", title=self._current.get("title", clean_title))
            self.refresh()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self._error = ""
        self.changed.emit()
        self.workspace.client.request(
            "canvas.create",
            {
                "project_id": pid,
                "session_id": self.workspace.sessionId,
                "title": clean_title,
                "kind": clean_kind,
                "language": clean_lang,
                "content": content,
                "summary": tr("Creato da Canvas"),
            },
            done,
            failed,
        )

    @Slot(str, str)
    def saveContent(self, content: str, summary: str = "") -> None:
        if self._active_file_tab() is not None:
            self.saveProjectFile(content)
            return
        pid = self._selected_project_id()
        aid = self.currentId
        if not pid or not aid or not self.workspace.connected:
            return
        gen = self._generation

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._error = ""
            self._current = dict(data.get("artifact", {}))
            self._status_text = tr(
                "Revisione {rev} salvata.", rev=self.currentRevision
            )
            self.refresh()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self._error = ""
        self.changed.emit()
        self.workspace.client.request(
            "canvas.update",
            {
                "project_id": pid,
                "artifact_id": aid,
                "content": content,
                "summary": summary.strip() or tr("Modifica manuale nel Canvas"),
            },
            done,
            failed,
        )

    def _extract_selection_range(
        self, start_line: int, end_line: int, selected_text: str = ""
    ) -> tuple[int, int, str]:
        content = self.currentContent
        lines = content.splitlines()
        lo = max(1, min(start_line, max(1, len(lines))))
        hi = max(lo, min(end_line, max(1, len(lines))))
        excerpt = selected_text.strip("\r\n") if selected_text.strip() else "\n".join(lines[lo - 1 : hi])
        return lo, hi, excerpt

    @Slot(int, int, str)
    @Slot(int, int, str, str)
    def askKiraSelection(
        self, start_line: int, end_line: int, instruction: str, selected_text: str = ""
    ) -> None:
        aid = self.currentId
        title = self.currentTitle
        if not aid or not self.currentContent:
            return
        lo, hi, excerpt = self._extract_selection_range(start_line, end_line, selected_text)
        user_req = instruction.strip() or tr(
            "Spiega in modo chiaro cosa fanno queste righe, come funzionano e qual è il loro ruolo nel file (senza apportare modifiche)."
        )
        if self._active_file_tab() is not None:
            self.workspace.ambient.setManualContext("Kira Editor", f"{title}:{lo}-{hi}", excerpt)
            prompt_block = (
                f"[File del progetto `{title}` · righe {lo}..{hi} — Richiesta di spiegazione]\n"
                f"{user_req}"
            )
        else:
            prompt_block = (
                f"[Canvas Artifact `{aid}` ({title}) · righe {lo}..{hi} — Richiesta di spiegazione]\n"
                f"{user_req}\n"
                f"```\n{excerpt}\n```"
            )
        self.workspace.setDraftText(prompt_block)
        self.workspace.workbench.useHistory(prompt_block)
        self._status_text = tr(
            "Domanda sulle righe {lo}–{hi} pronta nella chat.", lo=lo, hi=hi
        )
        self.changed.emit()

    @Slot(int, int, str)
    @Slot(int, int, str, str)
    def optimizeSelection(
        self, start_line: int, end_line: int, instruction: str, selected_text: str = ""
    ) -> None:
        aid = self.currentId
        title = self.currentTitle
        if not aid or not self.currentContent:
            return
        lo, hi, excerpt = self._extract_selection_range(start_line, end_line, selected_text)
        user_req = instruction.strip() or tr(
            "Riscrivi e migliora queste righe mantenendo la coerenza con il resto."
        )
        if self._active_file_tab() is not None:
            self.workspace.ambient.setManualContext("Kira Editor", f"{title}:{lo}-{hi}", excerpt)
            prompt_block = (
                f"[File del progetto `{title}` · righe {lo}..{hi} — Modifica / Ottimizzazione]\n"
                f"{user_req}"
            )
        else:
            prompt_block = (
                f"[Canvas Artifact `{aid}` ({title}) · righe {lo}..{hi}]\n"
                f"{user_req}\n"
                f"Usa `kira.canvas` con `action=\"patch_lines\"`, `artifact_id=\"{aid}\"`, "
                f"`start_line={lo}`, `end_line={hi}`.\n"
                f"```\n{excerpt}\n```"
            )
        self.workspace.setDraftText(prompt_block)
        self.workspace.workbench.useHistory(prompt_block)
        self._status_text = tr(
            "Richiesta di ottimizzazione righe {lo}–{hi} inserita nella chat.", lo=lo, hi=hi
        )
        self.changed.emit()

    @Slot(int)
    def restoreRevision(self, revision: int) -> None:
        tab = self._active_file_tab()
        if tab is not None:
            for item in tab.get("history", []):
                if int(item.get("revision", 0)) == int(revision) and "content" in item:
                    self.saveProjectFile(str(item["content"]))
                    self._status_text = tr("Ripristinata versione {rev}.", rev=revision)
                    self.changed.emit()
                    return
            return
        pid = self._selected_project_id()
        aid = self.currentId
        if not pid or not aid or revision <= 0 or not self.workspace.connected:
            return
        gen = self._generation

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._error = ""
            self._current = dict(data.get("artifact", {}))
            self._status_text = tr("Ripristinata versione {rev}.", rev=revision)
            self.refresh()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self.changed.emit()
        self.workspace.client.request(
            "canvas.restore",
            {"project_id": pid, "artifact_id": aid, "revision": int(revision)},
            done,
            failed,
        )

    @Slot(str)
    def deleteArtifact(self, artifact_id: str) -> None:
        tab = self._active_file_tab()
        if tab is not None:
            self.closeFileTab(str(tab["path"]))
            return
        pid = self._selected_project_id()
        aid = (artifact_id or self.currentId).strip()
        if not pid or not aid or not self.workspace.connected:
            return
        gen = self._generation

        def done(_: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._current = {}
            self._status_text = tr("Artefatto rimosso.")
            self.refresh()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self.changed.emit()
        self.workspace.client.request(
            "canvas.delete", {"project_id": pid, "artifact_id": aid}, done, failed
        )

    @Slot()
    def refreshDiff(self) -> None:
        tab = self._active_file_tab()
        if tab is not None:
            base = str(tab.get("saved_content", "")) if tab.get("dirty") else str(tab.get("prev_content", ""))
            self._diff_text, self._diff_hunks = compute_hunks(
                base,
                str(tab.get("content", "")),
                str(tab.get("rel_path", "file")),
            )
            self.changed.emit()
            return
        pid = self._selected_project_id()
        aid = self.currentId
        if not pid or not aid or not self.workspace.connected:
            return
        gen = self._generation

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._diff_text = str(data.get("diff", ""))
            self._diff_hunks = list(data.get("hunks", []))
            self.changed.emit()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self.changed.emit()
        self.workspace.client.request(
            "canvas.diff", {"project_id": pid, "artifact_id": aid}, done, failed
        )

    @Slot(int, str)
    def applyHunk(self, hunk_index: int, decision: str) -> None:
        if self._active_file_tab() is not None:
            return
        pid = self._selected_project_id()
        aid = self.currentId
        if not pid or not aid or not self.workspace.connected:
            return
        gen = self._generation

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._error = ""
            if "artifact" in data:
                self._current = dict(data["artifact"])
            if decision == "reject":
                self._status_text = tr("Blocco #{idx} rifiutato e ripristinato.", idx=hunk_index + 1)
            else:
                self._status_text = tr("Blocco #{idx} confermato.", idx=hunk_index + 1)
            self.refreshDiff()
            self.changed.emit()

        def failed(err: Any) -> None:
            if gen != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, str(err.get("message") or "") if isinstance(err, dict) else str(err))
            self.changed.emit()

        self._busy = True
        self.changed.emit()
        self.workspace.client.request(
            "canvas.apply_hunk",
            {
                "project_id": pid,
                "artifact_id": aid,
                "hunk_index": int(hunk_index),
                "decision": decision,
            },
            done,
            failed,
        )

    def _onRunEvent(self, event: Json) -> None:
        if event.get("type") != "tool.completed":
            return
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("name") == "kira.canvas":
            result = payload.get("result")
            if isinstance(result, dict) and result.get("ok"):
                art = result.get("artifact")
                if isinstance(art, dict) and art.get("id"):
                    self._active_file_path = ""
                    self._current = dict(art)
                    self._pane_visible = True
                    self.refresh()
                elif self._pane_visible:
                    self.refresh()
