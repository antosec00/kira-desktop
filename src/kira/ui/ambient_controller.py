"""Ambient AI & Voice UI Controller: Spotlight Overlay, Active Window/Selection Chip, and Hybrid Whisper Dictation."""

from __future__ import annotations

from pathlib import Path
import signal
import subprocess
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QClipboard, QGuiApplication

from kira.domain.ambient import (
    build_ambient_context,
    detect_audio_recorder,
    format_prompt_with_ambient_context,
    inspect_active_window,
    read_os_primary_selection,
    transcribe_audio_file,
)
from kira.ui.presentation import tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


class AmbientController(QObject):
    changed = Signal()
    dictationTextReady = Signal(str, str)
    raiseMainWindowRequested = Signal()

    def __init__(self, workspace: "Workspace", parent: QObject | None = None) -> None:
        super().__init__(parent or workspace)
        self.workspace = workspace
        self._overlay_visible = False
        self._context: Json = build_ambient_context()
        self._recording = False
        self._transcribing = False
        self._record_target = "composer"
        self._record_proc: subprocess.Popen[bytes] | None = None
        self._record_path: Path | None = None
        self._record_started_at = 0.0
        self._record_elapsed_sec = 0
        self._auto_send_voice = False
        self._voice_error = ""
        self._last_engine = ""

        self._record_timer = QTimer(self)
        self._record_timer.setInterval(500)
        self._record_timer.timeout.connect(self._tick_recording)

        workspace.client.eventReceived.connect(self._on_ipc_event)
        workspace.changed.connect(self.changed.emit)
        workspace.transcript.rowsInserted.connect(lambda *_: self.changed.emit())
        workspace.transcript.dataChanged.connect(lambda *_: self.changed.emit())

    def retranslate(self) -> None:
        self.changed.emit()

    def _on_ipc_event(self, event: Json) -> None:
        if event.get("type") != "ambient.toggle":
            return
        raw_payload = event.get("payload")
        payload: Json = raw_payload if isinstance(raw_payload, dict) else {}
        app_name = str(payload.get("app_name") or "")
        win_title = str(payload.get("window_title") or "")
        sel = str(payload.get("selection") or "")
        if not sel:
            sel = self._read_qt_selection()
        if app_name or win_title or sel:
            self._context = build_ambient_context(
                app_name=app_name,
                window_title=win_title,
                selection=sel,
            )
        self._overlay_visible = not self._overlay_visible
        self.changed.emit()

    @staticmethod
    def _read_qt_selection() -> str:
        app = QGuiApplication.instance()
        if not isinstance(app, QGuiApplication):
            return ""
        clipboard = app.clipboard()
        if clipboard is None:
            return ""
        text = ""
        if clipboard.supportsSelection():
            text = clipboard.text(QClipboard.Mode.Selection) or ""
        return text.strip()[:12_000]

    @qt_property(bool, notify=changed)
    def overlayVisible(self) -> bool:
        return self._overlay_visible

    @qt_property(bool, notify=changed)
    def hasCapturedContext(self) -> bool:
        return bool(self._context.get("has_context"))

    @qt_property(str, notify=changed)
    def contextChipLabel(self) -> str:
        if not self.hasCapturedContext:
            return ""
        app = str(self._context.get("app_name") or "").strip()
        title = str(self._context.get("window_title") or "").strip()
        lines = int(self._context.get("selection_lines") or 0)
        chars = int(self._context.get("selection_chars") or 0)
        source = app or title or tr("Selezione attiva")
        if app and title and app.lower() not in title.lower():
            source = f"{app} · {title[:42]}"
        elif title:
            source = title[:52]
        if chars > 0:
            if lines > 1:
                return tr("{source} · {count} righe selezionate", source=source, count=lines)
            return tr("{source} · {count} caratteri selezionati", source=source, count=chars)
        return tr("Finestra attiva: {source}", source=source)

    @qt_property(str, notify=changed)
    def contextPreview(self) -> str:
        return str(self._context.get("preview") or "")

    @qt_property(bool, notify=changed)
    def recording(self) -> bool:
        return self._recording

    @qt_property(bool, notify=changed)
    def transcribing(self) -> bool:
        return self._transcribing

    @qt_property(bool, notify=changed)
    def autoSendVoice(self) -> bool:
        return self._auto_send_voice

    @qt_property(str, notify=changed)
    def voiceStatusLabel(self) -> str:
        if self._recording:
            return tr("Ascolto vocale in corso ({sec}s)… Clicca per trascrivere", sec=self._record_elapsed_sec)
        if self._transcribing:
            return tr("Trascrizione vocale con Whisper in corso…")
        if self._voice_error:
            return self._voice_error
        return ""

    @qt_property(str, notify=changed)
    def spotlightResponse(self) -> str:
        for row in reversed(self.workspace.transcript.rows):
            if row.get("kind") == "assistant" and str(row.get("body") or "").strip():
                return str(row.get("body") or "").strip()[:1800]
        return ""

    @qt_property(str, notify=changed)
    def spotlightCommandHint(self) -> str:
        return "kira --spotlight"

    @Slot()
    def openSpotlight(self) -> None:
        self.captureContextNow()
        self._overlay_visible = True
        self.changed.emit()

    @Slot()
    def closeSpotlight(self) -> None:
        if self._overlay_visible:
            self._overlay_visible = False
            self.changed.emit()

    @Slot()
    def toggleSpotlight(self) -> None:
        if self._overlay_visible:
            self.closeSpotlight()
        else:
            self.openSpotlight()

    @Slot()
    def captureContextNow(self) -> None:
        win = inspect_active_window()
        sel = self._read_qt_selection() or read_os_primary_selection()
        self._context = build_ambient_context(
            app_name=win.get("app_name", ""),
            window_title=win.get("window_title", ""),
            selection=sel,
        )
        self.changed.emit()

    @Slot(str, str, str)
    def setManualContext(self, app_name: str, window_title: str, selection: str) -> None:
        self._context = build_ambient_context(
            app_name=app_name,
            window_title=window_title,
            selection=selection,
        )
        self.changed.emit()

    @Slot()
    def clearContext(self) -> None:
        if self.hasCapturedContext:
            self._context = build_ambient_context()
            self.changed.emit()

    def consume_for_prompt(self, prompt: str) -> str:
        """If ambient context is attached, prepend it to the prompt and clear the chip."""
        if not self.hasCapturedContext:
            return prompt
        enriched = format_prompt_with_ambient_context(prompt, self._context)
        self._context = build_ambient_context()
        self.changed.emit()
        return enriched

    @Slot(str)
    def submitSpotlight(self, prompt: str) -> None:
        clean = (prompt or "").strip()
        if not clean:
            return
        self.workspace.workbench.submit(clean)
        self.changed.emit()

    @Slot()
    def expandToMainWindow(self) -> None:
        self._overlay_visible = False
        self.changed.emit()
        self.raiseMainWindowRequested.emit()

    @Slot()
    def copySpotlightCommand(self) -> None:
        self.workspace.copy("kira --spotlight")

    @Slot(bool)
    def setAutoSendVoice(self, enabled: bool) -> None:
        if self._auto_send_voice != bool(enabled):
            self._auto_send_voice = bool(enabled)
            self.changed.emit()

    def _tick_recording(self) -> None:
        if not self._recording:
            self._record_timer.stop()
            return
        self._record_elapsed_sec = max(0, int(time.monotonic() - self._record_started_at))
        self.changed.emit()

    @Slot(str)
    def toggleDictation(self, target: str = "composer") -> None:
        self._voice_error = ""
        if self._recording:
            self.stopDictation()
        else:
            self.startDictation(target or "composer")

    @Slot(str)
    def startDictation(self, target: str = "composer") -> None:
        if self._recording or self._transcribing:
            return
        self._voice_error = ""
        self._record_target = target or "composer"
        recorder = detect_audio_recorder()
        if recorder is None:
            self._voice_error = tr("Nessun registratore audio trovato (installa pipewire/pulseaudio o alsa-utils).")
            self.changed.emit()
            return
        _, cmd_prefix = recorder
        runtime_dir = self.workspace.paths.runtime
        runtime_dir.mkdir(parents=True, exist_ok=True)
        wav_path = runtime_dir / "kira-voice-dictation.wav"
        wav_path.unlink(missing_ok=True)
        try:
            self._record_proc = subprocess.Popen(
                [*cmd_prefix, str(wav_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._voice_error = tr("Impossibile avviare il microfono: {error}", error=str(exc))
            self.changed.emit()
            return
        self._record_path = wav_path
        self._recording = True
        self._record_started_at = time.monotonic()
        self._record_elapsed_sec = 0
        self._record_timer.start()
        self.changed.emit()

    @Slot()
    def stopDictation(self) -> None:
        if not self._recording:
            return
        self._record_timer.stop()
        self._recording = False
        proc = self._record_proc
        self._record_proc = None
        wav_path = self._record_path
        target = self._record_target
        if proc is not None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    proc.kill()
        if wav_path is None or not wav_path.is_file():
            self._voice_error = tr("Nessun audio acquisito dal microfono.")
            self.changed.emit()
            return
        self._transcribing = True
        self.changed.emit()

        lang = "it"

        def work_func() -> Any:
            try:
                return transcribe_audio_file(wav_path, language=lang)
            except Exception as exc:
                return exc

        def _done(result: Any) -> None:
            self._transcribing = False
            wav_path.unlink(missing_ok=True)
            if isinstance(result, Exception):
                self._voice_error = str(result)
                self.changed.emit()
                return
            text = str((result or {}).get("text") or "").strip()
            self._last_engine = str((result or {}).get("engine") or "")
            if not text:
                self._voice_error = tr("Nessun parlato rilevato nella registrazione.")
                self.changed.emit()
                return
            self._apply_transcribed_text(target, text)
            self.changed.emit()

        self.workspace._background(work_func, _done)

    def _apply_transcribed_text(self, target: str, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        self.dictationTextReady.emit(target, clean)
        if target == "composer":
            existing = (self.workspace.draftText or "").strip()
            combined = f"{existing} {clean}".strip() if existing else clean
            self.workspace.setDraftText(combined)
            if self._auto_send_voice and self.workspace.workbench.canSubmit:
                self.workspace.workbench.submit(combined)
        elif target == "spotlight" and self._auto_send_voice:
            self.submitSpotlight(clean)

    def close(self) -> None:
        self._record_timer.stop()
        if self._record_proc is not None:
            try:
                self._record_proc.terminate()
            except Exception:
                pass
            self._record_proc = None
