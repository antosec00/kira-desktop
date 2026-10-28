"""Qt controller for Kira Interactive Canvas / Artifacts and Granular Diff Hunks."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from pathlib import Path
import queue
import subprocess
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage

from kira.service.canvas import find_chromium_executable
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
        self.liveFramePushed.connect(self._onLiveFramePushed)
        self.changed.connect(self._syncLiveStream)
        workspace.projectOpened.connect(self.syncProject)
        workspace.eventApplied.connect(self._onRunEvent)
        workspace.client.disconnected.connect(self._disconnected)

    @Slot(str)
    def _onLiveFramePushed(self, url: str) -> None:
        if url != self._live_frame_url:
            self._live_frame_url = url
            self.liveFrameChanged.emit()

    def _syncLiveStream(self) -> None:
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

    @qt_property("QVariantMap", notify=changed)
    def currentArtifact(self) -> Json:
        return self._current

    @qt_property(str, notify=changed)
    def currentId(self) -> str:
        return str(self._current.get("id", ""))

    @qt_property(str, notify=changed)
    def currentTitle(self) -> str:
        return str(self._current.get("title", ""))

    @qt_property(str, notify=changed)
    def currentKind(self) -> str:
        return str(self._current.get("kind", ""))

    @qt_property(str, notify=changed)
    def currentLanguage(self) -> str:
        return str(self._current.get("language", ""))

    @qt_property(str, notify=changed)
    def currentContent(self) -> str:
        return str(self._current.get("content", ""))

    @qt_property(int, notify=changed)
    def currentRevision(self) -> int:
        return int(self._current.get("revision", self._current.get("current_revision", 0)) or 0)

    @qt_property(int, notify=changed)
    def currentLineCount(self) -> int:
        return int(self._current.get("line_count", 1) or 1)

    @qt_property(str, notify=changed)
    def previewUrl(self) -> str:
        url = str(self._current.get("preview_url", ""))
        rev = self.currentRevision
        return f"{url}?rev={rev}" if url else ""

    @qt_property(str, notify=liveFrameChanged)
    def liveFrameUrl(self) -> str:
        return self._live_frame_url

    @qt_property(str, notify=changed)
    def previewHtmlUrl(self) -> str:
        html_path = str(self._current.get("preview_html_path", ""))
        if html_path and Path(html_path).is_file():
            return Path(html_path).as_uri()
        return str(self._current.get("preview_html_url", ""))

    @qt_property("QVariantList", notify=changed)
    def history(self) -> list[Json]:
        return list(self._current.get("history", []))

    @qt_property(str, notify=changed)
    def diffText(self) -> str:
        return self._diff_text

    @qt_property("QVariantList", notify=changed)
    def diffHunks(self) -> list[Json]:
        return self._diff_hunks

    def _selected_project_id(self) -> str:
        return str(self.workspace.project.get("id", ""))

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

    @Slot(str)
    def setViewMode(self, mode: str) -> None:
        if mode in {"preview", "code", "diff", "history"} and mode != self._view_mode:
            self._view_mode = mode
            self.changed.emit()
            if mode == "diff" and self.currentId:
                self.refreshDiff()

    @Slot()
    def openInteractivePreview(self) -> None:
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
        pid = self._selected_project_id()
        if not pid or not self.workspace.connected:
            return
        gen = self._generation

        def done(data: Json) -> None:
            if gen != self._generation or pid != self._selected_project_id():
                return
            self._busy = False
            self._artifacts = list(data.get("artifacts", []))
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

    @Slot(int, int, str)
    def askKiraSelection(self, start_line: int, end_line: int, instruction: str) -> None:
        aid = self.currentId
        title = self.currentTitle
        content = self.currentContent
        if not aid or not content:
            return
        lines = content.splitlines()
        lo = max(1, min(start_line, len(lines)))
        hi = max(lo, min(end_line, len(lines)))
        excerpt = "\n".join(lines[lo - 1 : hi])
        user_req = instruction.strip() or tr("Riscrivi e migliora queste righe mantenendo la coerenza con il resto.")
        prompt_block = (
            f"[Canvas Artifact `{aid}` ({title}) · righe {lo}..{hi}]\n"
            f"{user_req}\n"
            f"Usa `kira.canvas` con `action=\"patch_lines\"`, `artifact_id=\"{aid}\"`, "
            f"`start_line={lo}`, `end_line={hi}`.\n"
            f"```\n{excerpt}\n```"
        )
        self.workspace.workbench.useHistory(prompt_block)
        self._status_text = tr(
            "Selezione righe {lo}–{hi} inserita nella bozza della chat.", lo=lo, hi=hi
        )
        self.changed.emit()

    @Slot(int)
    def restoreRevision(self, revision: int) -> None:
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
                    self._current = dict(art)
                    self._pane_visible = True
                    self.refresh()
                elif self._pane_visible:
                    self.refresh()
