"""Ambient AI domain: Active Window & Selection inspection, Audio capture & Hybrid Whisper STT."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import httpx

from kira.domain.contracts import Json

MAX_SELECTION_CHARS = 12_000


def _run_quick(cmd: list[str], timeout: float = 0.35) -> str:
    """Run a fast local inspection helper without blocking the UI or raising."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def read_os_primary_selection() -> str:
    """Read highlighted text from Linux X11/Wayland primary selection via CLI tools if available."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return ""
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        out = _run_quick(["wl-paste", "--primary", "--no-newline"], timeout=0.25)
        if out:
            return out[:MAX_SELECTION_CHARS]
    if shutil.which("xclip"):
        out = _run_quick(["xclip", "-o", "-selection", "primary"], timeout=0.25)
        if out:
            return out[:MAX_SELECTION_CHARS]
    if shutil.which("xsel"):
        out = _run_quick(["xsel", "-ob"], timeout=0.25)
        if out:
            return out[:MAX_SELECTION_CHARS]
    return ""


def inspect_active_window() -> dict[str, str]:
    """Inspect the currently focused desktop window (X11 / Wayland / KDE Plasma)."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return {"app_name": "", "window_title": "", "wm_class": ""}

    window_title = ""
    wm_class = ""

    # 1. Try kdotool on KDE Plasma Wayland/X11
    if shutil.which("kdotool"):
        win_id = _run_quick(["kdotool", "getactivewindow"], timeout=0.25)
        if win_id:
            window_title = _run_quick(["kdotool", "getwindowname", win_id], timeout=0.25)
            wm_class = _run_quick(["kdotool", "getwindowclassname", win_id], timeout=0.25)

    # 2. Try xdotool + xprop on X11 / XWayland
    if not window_title and shutil.which("xdotool"):
        win_id = _run_quick(["xdotool", "getactivewindow"], timeout=0.25)
        if win_id:
            window_title = _run_quick(["xdotool", "getwindowname", win_id], timeout=0.25)
            if shutil.which("xprop"):
                raw_class = _run_quick(["xprop", "-id", win_id, "WM_CLASS"], timeout=0.25)
                if "=" in raw_class:
                    parts = [p.strip().strip('"') for p in raw_class.split("=", 1)[1].split(",")]
                    wm_class = parts[-1] if parts else ""

    app_name = wm_class or ""
    if not app_name and " — " in window_title:
        app_name = window_title.rsplit(" — ", 1)[-1].strip()
    elif not app_name and " - " in window_title:
        app_name = window_title.rsplit(" - ", 1)[-1].strip()

    return {
        "app_name": app_name[:120],
        "window_title": window_title[:300],
        "wm_class": wm_class[:120],
    }


def build_ambient_context(
    *,
    app_name: str = "",
    window_title: str = "",
    selection: str = "",
) -> Json:
    """Format active window metadata and selected text into an inspectable chip model."""
    clean_app = (app_name or "").strip()
    clean_title = (window_title or "").strip()
    clean_sel = (selection or "").strip()[:MAX_SELECTION_CHARS]

    # Ignore Kira's own window title if no external selection is provided
    if clean_title.startswith("Kira Desktop") or clean_app.lower() in {"kira", "kira-desktop", "python3"}:
        if not clean_sel:
            clean_app = ""
            clean_title = ""

    lines = [line for line in clean_sel.splitlines() if line.strip()] if clean_sel else []
    line_count = len(lines)
    char_count = len(clean_sel)
    has_context = bool(clean_sel or clean_title or clean_app)

    if not has_context:
        return {
            "has_context": False,
            "app_name": "",
            "window_title": "",
            "selection": "",
            "selection_lines": 0,
            "selection_chars": 0,
            "chip_label": "",
            "preview": "",
            "formatted_block": "",
        }

    source_label = clean_app or clean_title or "Selezione"
    if clean_app and clean_title and clean_app.lower() not in clean_title.lower():
        source_label = f"{clean_app} · {clean_title[:45]}"
    elif clean_title:
        source_label = clean_title[:55]

    if clean_sel:
        if line_count > 1:
            chip_label = f"{source_label} · {line_count} righe selezionate"
        else:
            chip_label = f"{source_label} · {char_count} caratteri selezionati"
    else:
        chip_label = f"Finestra: {source_label}"

    preview = clean_sel[:280].replace("\n", " ↵ ") if clean_sel else clean_title

    header_parts = []
    if clean_app:
        header_parts.append(f"Applicazione: {clean_app}")
    if clean_title:
        header_parts.append(f"Finestra: {clean_title}")
    header_str = " | ".join(header_parts) if header_parts else "Selezione attiva del sistema"

    if clean_sel:
        formatted_block = (
            f"[Contesto Finestra Attiva — {header_str}]\n"
            f"```\n{clean_sel}\n```"
        )
    else:
        formatted_block = f"[Contesto Finestra Attiva — {header_str}]"

    return {
        "has_context": True,
        "app_name": clean_app,
        "window_title": clean_title,
        "selection": clean_sel,
        "selection_lines": line_count,
        "selection_chars": char_count,
        "chip_label": chip_label,
        "preview": preview,
        "formatted_block": formatted_block,
    }


def format_prompt_with_ambient_context(prompt: str, context: Json | None) -> str:
    """Attach the captured ambient context block to the user prompt when present."""
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        return ""
    if not isinstance(context, dict) or not context.get("has_context"):
        return clean_prompt
    block = str(context.get("formatted_block") or "").strip()
    if not block:
        return clean_prompt
    return f"{block}\n\n{clean_prompt}"


def detect_audio_recorder() -> tuple[str, list[str]] | None:
    """Return (backend_name, command_prefix_without_output_path) for native 16kHz mono WAV capture."""
    if shutil.which("pw-record"):
        return ("pw-record", ["pw-record", "--rate=16000", "--channels=1", "--format=s16"])
    if shutil.which("parecord"):
        return ("parecord", ["parecord", "--rate=16000", "--channels=1", "--format=s16le", "--file-format=wav"])
    if shutil.which("arecord"):
        return ("arecord", ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav"])
    if shutil.which("ffmpeg"):
        return (
            "ffmpeg",
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "pulse", "-i", "default", "-ac", "1", "-ar", "16000"],
        )
    return None


def detect_local_whisper() -> tuple[str, str] | None:
    """Detect a local Whisper STT binary or Python module (`whisper-cli`, `whisper-cpp`, `whisper`, `faster_whisper`)."""
    for binary in ("whisper-cli", "whisper-cpp", "whisper"):
        resolved = shutil.which(binary)
        if resolved:
            return (binary, resolved)
    try:
        import importlib.util

        if importlib.util.find_spec("faster_whisper") is not None:
            return ("faster_whisper", "python:faster_whisper")
    except Exception:
        pass
    return None


def probe_voice_capabilities() -> Json:
    """Report available audio recording and STT transcription backends."""
    rec = detect_audio_recorder()
    local_stt = detect_local_whisper()
    has_openai_key = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    return {
        "audio_recorder": rec[0] if rec else None,
        "local_stt": local_stt[0] if local_stt else None,
        "cloud_stt_configured": has_openai_key,
        "available": rec is not None,
        "mode": "local" if local_stt else ("cloud" if has_openai_key else "hybrid"),
    }


def transcribe_audio_file(
    wav_path: Path,
    *,
    language: str = "it",
    api_key: str = "",
    api_url: str = "https://api.openai.com/v1/audio/transcriptions",
    model: str = "whisper-1",
) -> Json:
    """Hybrid STT transcription: tries local Whisper first, then falls back to OpenAI-compatible Whisper API."""
    if not wav_path.is_file() or wav_path.stat().st_size < 100:
        raise ValueError("Registrazione audio vuota o troppo breve.")

    # 1. Try local Whisper CLI / faster-whisper if installed
    local = detect_local_whisper()
    if local is not None:
        kind, binary = local
        if kind == "faster_whisper":
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]

                wmodel = WhisperModel("base", device="cpu", compute_type="int8")
                segments, _ = wmodel.transcribe(str(wav_path), language=language or None)
                text = " ".join(seg.text.strip() for seg in segments).strip()
                if text:
                    return {"text": text, "engine": "faster-whisper (locale)"}
            except Exception:
                pass
        elif kind in {"whisper-cli", "whisper-cpp"}:
            try:
                proc = subprocess.run(
                    [binary, "-f", str(wav_path), "-l", language or "auto", "-nt"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return {"text": proc.stdout.strip(), "engine": f"{kind} (locale)"}
            except Exception:
                pass
        elif kind == "whisper":
            try:
                out_dir = wav_path.parent
                proc = subprocess.run(
                    [
                        binary,
                        str(wav_path),
                        "--model",
                        "base",
                        "--language",
                        language or "Italian",
                        "--output_format",
                        "txt",
                        "--output_dir",
                        str(out_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=45,
                    check=False,
                )
                txt_file = wav_path.with_suffix(".txt")
                if proc.returncode == 0 and txt_file.is_file():
                    text = txt_file.read_text(encoding="utf-8", errors="ignore").strip()
                    txt_file.unlink(missing_ok=True)
                    if text:
                        return {"text": text, "engine": "whisper (locale)"}
            except Exception:
                pass

    # 2. Fallback to OpenAI-compatible Whisper API
    resolved_key = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if resolved_key:
        with wav_path.open("rb") as audio_fp:
            files = {"file": (wav_path.name, audio_fp, "audio/wav")}
            data: dict[str, Any] = {"model": model or "whisper-1"}
            if language:
                data["language"] = language[:2]
            resp = httpx.post(
                api_url,
                headers={"Authorization": f"Bearer {resolved_key}"},
                data=data,
                files=files,
                timeout=30.0,
            )
            resp.raise_for_status()
            payload = resp.json()
            text = str(payload.get("text") or "").strip()
            if text:
                return {"text": text, "engine": "Whisper API"}

    raise ValueError(
        "Nessun motore STT configurato: installa 'whisper-cli' / 'faster-whisper' in locale "
        "oppure imposta OPENAI_API_KEY per il fallback Whisper API."
    )
