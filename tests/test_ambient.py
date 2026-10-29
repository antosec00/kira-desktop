"""Tests for Pillar 8: OS Ambient AI Integration, Spotlight Overlay, Active Window/Selection Chip, and Hybrid Whisper Voice Dictation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject

from kira.cli import main as cli_main
from kira.domain.ambient import (
    build_ambient_context,
    format_prompt_with_ambient_context,
    inspect_active_window,
    read_os_primary_selection,
    transcribe_audio_file,
)
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def _visual_item(root, name: str):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := _visual_item(child, name):
            return found
    return None


def test_build_and_format_ambient_context():
    ctx = build_ambient_context(
        app_name="Code",
        window_title="server.py — kira-desktop",
        selection="def dispatch():\n    return True\n",
    )
    assert ctx["has_context"] is True
    assert ctx["selection_lines"] == 2
    assert ctx["selection_chars"] > 10
    assert "def dispatch():" in ctx["preview"]

    formatted = format_prompt_with_ambient_context("Spiega questa funzione", ctx)
    assert "[Contesto Finestra Attiva — Applicazione: Code | Finestra: server.py — kira-desktop]" in formatted
    assert "```\ndef dispatch():\n    return True\n```" in formatted
    assert formatted.endswith("Spiega questa funzione")

    empty = build_ambient_context()
    assert empty["has_context"] is False
    assert format_prompt_with_ambient_context("Ciao", empty) == "Ciao"


def test_os_selection_and_window_inspection_helpers(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr("kira.domain.ambient.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def fake_run(cmd, timeout=0.35):
        if cmd[0] == "wl-paste":
            return "testo evidenziato su wayland"
        if cmd[:2] == ["kdotool", "getactivewindow"]:
            return "win-42"
        if cmd[:2] == ["kdotool", "getwindowname"]:
            return "terminale — bash"
        if cmd[:2] == ["kdotool", "getwindowclassname"]:
            return "org.kde.konsole"
        return ""

    monkeypatch.setattr("kira.domain.ambient._run_quick", fake_run)
    assert read_os_primary_selection() == "testo evidenziato su wayland"
    win = inspect_active_window()
    assert win["window_title"] == "terminale — bash"
    assert win["app_name"] == "org.kde.konsole"


def test_hybrid_whisper_transcription_local_and_api_fallback(tmp_path, monkeypatch):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 200)

    # 1. Local whisper-cli available
    monkeypatch.setattr(
        "kira.domain.ambient.detect_local_whisper",
        lambda: ("whisper-cli", "/usr/bin/whisper-cli"),
    )
    monkeypatch.setattr(
        "kira.domain.ambient.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Trascrizione locale completata\n"),
    )
    res_local = transcribe_audio_file(audio, language="it")
    assert res_local == {"text": "Trascrizione locale completata", "engine": "whisper-cli (locale)"}

    # 2. Local whisper unavailable -> fallback to OpenAI Whisper API
    monkeypatch.setattr("kira.domain.ambient.detect_local_whisper", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "Trascrizione cloud OpenAI completata"}

    monkeypatch.setattr("kira.domain.ambient.httpx.post", lambda *args, **kwargs: FakeResp())
    res_api = transcribe_audio_file(audio, language="it")
    assert res_api == {"text": "Trascrizione cloud OpenAI completata", "engine": "Whisper API"}

    # 3. Neither available -> clear ValueError
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Nessun motore STT configurato"):
        transcribe_audio_file(audio, language="it")


def test_cli_spotlight_flag_dispatches_to_running_service(monkeypatch):
    sent = []

    class FakeClient:
        async def connect(self):
            return {"version": "v1"}

        async def request(self, method, params=None):
            sent.append((method, params))
            return {"delivered": 1}

        async def close(self):
            pass

    monkeypatch.setattr("kira.client.Client", FakeClient)
    monkeypatch.setattr(
        "kira.domain.ambient.inspect_active_window",
        lambda: {"app_name": "VSCode", "window_title": "test.py", "wm_class": "Code"},
    )
    monkeypatch.setattr("kira.domain.ambient.read_os_primary_selection", lambda: "selected code")
    assert cli_main(["--spotlight"]) == 0
    assert cli_main(["spotlight"]) == 0
    assert len(sent) == 2
    assert sent[0][0] == "ambient.toggle"
    assert sent[0][1]["selection"] == "selected code"


def test_service_ambient_toggle_broadcasts_to_subscribers(tmp_path):
    class FakeSub:
        def __init__(self, sink):
            self.closed = False
            self._sink = sink

        def send(self, msg):
            self._sink.append(msg)

    async def scenario():
        paths = Paths(*(tmp_path / p for p in ("config", "data", "state", "runtime")))
        service = Service(paths)
        events = []
        fake_ui_client = FakeSub(events)
        caller_client = FakeSub([])
        service.clients.add(fake_ui_client)
        service.clients.add(caller_client)
        result = await service._dispatch(
            "ambient.toggle",
            {
                "app_name": "VSCode",
                "window_title": "ambient.py",
                "selection": "print('hello')",
            },
            caller_client,
        )
        assert result["delivered"] == 1
        assert len(events) == 1
        event = events[0]["event"]
        assert event["type"] == "ambient.toggle"
        assert event["payload"]["app_name"] == "VSCode"
        assert event["payload"]["selection"] == "print('hello')"

    asyncio.run(scenario())


def test_ui_ambient_chip_and_spotlight_overlay(qapp, qtbot, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    workspace._project = {"id": "project-a", "name": "Ambient Project", "root": str(tmp_path), "permission": "project_write"}
    workspace._session = {"id": "chat-a", "project_id": "project-a", "backend": "openai_chatgpt", "model": "gpt-5"}
    workspace._sessions = [workspace._session]
    workspace._backend, workspace._model = "openai_chatgpt", "gpt-5"
    workspace._connected_value = True

    calls = []

    def request(method, params, received=None, failed=None, **kwargs):
        if method == "goal.get":
            if received:
                received({"goal": None})
            return True
        calls.append((method, params, received, failed))
        if method == "run.start" and received:
            received({"run": {"id": "run-1", "session_id": "chat-a", "state": "running", "prompt": params["prompt"]}})
        return True

    monkeypatch.setattr(workspace.client, "request", request)
    workspace.inputs._timer.stop()
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    qtbot.wait(60)

    try:
        root = window.contentItem()
        chip = _visual_item(root, "composerAmbientChip")
        assert chip is not None
        assert not chip.isVisible()

        # 1. Set manual context and verify chip appears in composer
        workspace.ambient.setManualContext("VS Code", "app.py", "def main():\n    pass")
        qtbot.wait(40)
        assert workspace.ambient.hasCapturedContext is True
        assert chip.isVisible()
        assert "VS Code" in workspace.ambient.contextChipLabel
        assert "2" in workspace.ambient.contextChipLabel

        # 2. Voice dictation inserts into draft
        workspace.setDraftText("Analizza")
        workspace.ambient._apply_transcribed_text("composer", "questo blocco di codice")
        qtbot.wait(30)
        assert workspace.draftText == "Analizza questo blocco di codice"

        # 3. Submitting from composer consumes context, prepends it to run.start, and clears draftText
        calls.clear()
        workspace.workbench.submit(workspace.draftText)
        qtbot.wait(40)
        start_calls = [c for c in calls if c[0] == "run.start"]
        assert len(start_calls) == 1
        sent_prompt = start_calls[0][1]["prompt"]
        assert "[Contesto Finestra Attiva — Applicazione: VS Code | Finestra: app.py]" in sent_prompt
        assert "def main():" in sent_prompt
        assert sent_prompt.endswith("Analizza questo blocco di codice")
        assert workspace.ambient.hasCapturedContext is False
        assert workspace.draftText == ""

        # 4. IPC event ambient.toggle opens Spotlight overlay with captured context
        workspace.ambient._on_ipc_event({
            "type": "ambient.toggle",
            "payload": {
                "app_name": "Konsole",
                "window_title": "pytest",
                "selection": "AssertionError: expected 200",
            },
        })
        qtbot.wait(40)
        assert workspace.ambient.overlayVisible is True
        overlay = window.findChild(QObject, "ambientOverlay")
        assert overlay is not None
        assert overlay.property("visible") is True
        assert "Konsole" in workspace.ambient.contextChipLabel
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_kira_builtin_ai_native_editor(qapp, qtbot, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    project_root = tmp_path / "proj"
    project_root.mkdir(parents=True, exist_ok=True)
    py_file = project_root / "src" / "engine.py"
    py_file.parent.mkdir(parents=True, exist_ok=True)
    py_file.write_text("def compute(x: int) -> int:\n    return x * 2\n", encoding="utf-8")
    txt_file = project_root / "notes.txt"
    txt_file.write_text("Appunti del progetto\nSeconda riga\n", encoding="utf-8")

    workspace._project = {
        "id": "project-ed",
        "name": "Editor Project",
        "root": str(project_root),
        "permission": "project_write",
    }
    workspace._session = {
        "id": "chat-ed",
        "project_id": "project-ed",
        "backend": "openai_chatgpt",
        "model": "gpt-5",
    }
    workspace._sessions = [workspace._session]
    workspace._connected_value = True
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: True)

    try:
        # 1. Set file_opener preference to "kira"
        workspace.setUiPreference("file_opener", "kira")
        assert workspace.uiPreferences["file_opener"] == "kira"

        # 2. Opening registered artifact (even a .py script!) opens safely in Kira's built-in editor
        workspace._artifacts = [{"path": str(py_file)}, {"path": str(txt_file)}]
        workspace.openArtifact(str(py_file))
        assert workspace.canvas.paneVisible is True
        assert workspace.canvas.isProjectFileActive is True
        assert workspace.canvas.currentTitle == "src/engine.py"
        assert workspace.canvas.currentLanguage == "python"
        assert "def compute(x: int)" in workspace.canvas.currentContent
        assert len(workspace.canvas.openFileTabs) == 1

        # 3. Open second file tab via message link in "kira" mode
        workspace.openMessageLink(txt_file.as_uri())
        qtbot.waitUntil(lambda: not workspace._jobs)
        assert len(workspace.canvas.openFileTabs) == 2
        assert workspace.canvas.currentTitle == "notes.txt"

        # 4. Switch back to src/engine.py tab, edit, check dirty state, and save to disk
        workspace.canvas.selectFileTab("src/engine.py")
        assert workspace.canvas.currentTitle == "src/engine.py"
        assert workspace.canvas.activeFileDirty is False
        new_code = "def compute(x: int) -> int:\n    return x * 10\n"
        workspace.canvas.updateEditorDraft(new_code)
        assert workspace.canvas.activeFileDirty is True
        workspace.canvas.saveContent(new_code, "")
        assert workspace.canvas.activeFileDirty is False
        assert py_file.read_text(encoding="utf-8") == new_code
        assert len(workspace.canvas.diffHunks) == 1

        # 5. Attach editor selection to chat composer as an Ambient Context Chip
        workspace.canvas.attachSelectionToChat(1, 2, "")
        assert workspace.ambient.hasCapturedContext is True
        assert "Kira Editor" in workspace.ambient.contextChipLabel
        assert "return x * 10" in workspace.ambient.contextPreview

        # 5b. Explain / Ask selection vs Optimize / Fix selection
        workspace.canvas.askKiraSelection(1, 2, "")
        assert "Richiesta di spiegazione" in workspace.draftText
        assert "senza apportare modifiche" in workspace.draftText
        workspace.canvas.optimizeSelection(1, 2, "")
        assert "Modifica / Ottimizzazione" in workspace.draftText
        assert "Riscrivi e migliora" in workspace.draftText

        # 6. Search project files inside the built-in explorer
        workspace.canvas.openProjectExplorer()
        assert workspace.canvas.fileExplorerVisible is True
        workspace.canvas.searchProjectFiles("engine")
        assert len(workspace.canvas.projectFiles) == 1
        assert workspace.canvas.projectFiles[0]["rel_path"] == "src/engine.py"

        # 7. Close tab
        workspace.canvas.closeFileTab("src/engine.py")
        assert len(workspace.canvas.openFileTabs) == 1
        assert workspace.canvas.currentTitle == "notes.txt"
    finally:
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

