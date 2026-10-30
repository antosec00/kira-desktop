"""Tests for Universal @Mentions and Contextual /Slash Commands in the Composer (Pillar 9)."""

from __future__ import annotations

from pathlib import Path
import subprocess

from PySide6.QtCore import QObject

from kira.domain.mentions import (
    detect_composer_token,
    extract_inline_mentions,
    format_prompt_with_mentions,
    parse_inline_slash_command,
    resolve_mention_context,
    search_slash_commands,
    search_universal_mentions,
)
from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def test_detect_composer_token_at_and_slash() -> None:
    # @ mentions anywhere after start or whitespace
    tok = detect_composer_token("@git", 4)
    assert tok is not None
    assert tok["trigger"] == "@"
    assert tok["query"] == "git"
    assert tok["start"] == 0

    tok2 = detect_composer_token("Controlla @file:src/main.py ora", 27)
    assert tok2 is not None
    assert tok2["trigger"] == "@"
    assert tok2["query"] == "file:src/main.py"
    assert tok2["start"] == 10

    # Email address does not trigger @mention because @ is not at start of token
    assert detect_composer_token(" Contatta user@example.com", 26) is None

    # /slash commands only at the start of prompt
    slash_tok = detect_composer_token("/comp", 5)
    assert slash_tok is not None
    assert slash_tok["trigger"] == "/"
    assert slash_tok["query"] == "comp"

    # Slash after normal words is not a command trigger
    assert detect_composer_token("Usa a /plan qui", 11) is None


def test_search_universal_mentions_and_slash_commands(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    souls = [{"id": "architect", "name": "Architetto", "tagline": "Progettazione rigorosa"}]

    all_items = search_universal_mentions("", tmp_path, souls)
    kinds = {item["kind"] for item in all_items}
    assert {"git", "git_log", "terminal", "web", "team", "soul", "folder"}.issubset(kinds)

    git_items = search_universal_mentions("git", tmp_path, souls)
    assert any(item["kind"] == "git" for item in git_items)

    soul_items = search_universal_mentions("soul:arch", tmp_path, souls)
    assert any(item["kind"] == "soul" and item["value"] == "architect" for item in soul_items)

    file_items = search_universal_mentions("app.py", tmp_path, souls)
    assert any(item["kind"] == "file" and item["value"] == "src/app.py" for item in file_items)

    slash_items = search_slash_commands("comp")
    assert any(item["command"] == "/compact" for item in slash_items)

    parsed = parse_inline_slash_command("/plan Rivedi architettura")
    assert parsed == ("/plan", "Rivedi architettura")
    assert parse_inline_slash_command("/nuova") == ("/clear", "")
    assert parse_inline_slash_command("/ricerca WebAssembly") == ("/research", "WebAssembly")


def test_resolve_and_format_mentions_with_security_bounds(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    hello = src / "hello.py"
    hello.write_text("def greet():\n    return 'ciao'\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(tmp_path), check=False, capture_output=True)

    # Valid @file and @folder
    file_ctx = resolve_mention_context({"kind": "file", "value": "src/hello.py"}, tmp_path)
    assert "def greet():" in file_ctx

    folder_ctx = resolve_mention_context({"kind": "folder", "value": "src"}, tmp_path)
    assert "src/hello.py" in folder_ctx

    # Path traversal outside project root must be blocked
    outside = tmp_path.parent / "secret_outside.txt"
    outside.write_text("TOP_SECRET", encoding="utf-8")
    blocked_ctx = resolve_mention_context({"kind": "file", "value": "../secret_outside.txt"}, tmp_path)
    assert "TOP_SECRET" not in blocked_ctx
    assert "File non trovato o non accessibile" in blocked_ctx

    # Inline + chip combination
    prompt = "Spiega @terminal e @web per favore"
    _, inline = extract_inline_mentions(prompt)
    assert len(inline) == 2

    enriched, fx = format_prompt_with_mentions(
        prompt,
        [{"kind": "file", "value": "src/hello.py", "label": "@file:src/hello.py"}],
        tmp_path,
        terminal_output="Traceback: ValueError: invalid literal",
        souls=[{"id": "reviewer", "name": "Revisore", "tagline": "Controllo qualità"}],
    )
    assert "def greet():" in enriched
    assert "Traceback: ValueError: invalid literal" in enriched
    assert "[Direttiva @web" in enriched
    assert fx["web"] is True
    assert fx["mention_count"] == 3


def test_ui_mentions_controller_and_qml_integration(qapp, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Workspace, "start", lambda self: None)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("x = 42\n", encoding="utf-8")

    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._startup_stage = "ready"
    workspace._project = {"id": "p1", "name": "Demo", "root": str(project_dir), "permission": "project_write"}
    workspace._session = {"id": "s1", "project_id": "p1", "backend": "ollama", "model": "qwen3:8b", "settings": {}}
    workspace._backend = "ollama"
    workspace._model = "qwen3:8b"
    workspace._catalogs = {"ollama": {"available": True, "models": [{"id": "qwen3:8b", "name": "Qwen 3"}]}}

    sent_requests: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        workspace.client,
        "request",
        lambda method, params, on_result=None, on_error=None: sent_requests.append((method, dict(params))),
    )

    engine = create_engine(workspace)
    try:
        window = engine.rootObjects()[0]
        mentions_bar = window.findChild(QObject, "composerMentionsBar")
        inline_popup = window.findChild(QObject, "composerInlinePopup")
        assert mentions_bar is not None
        assert inline_popup is not None
        assert not bool(mentions_bar.property("visible"))

        # Typing @git activates inline suggestions
        workspace.mentions.updateInputCursor("@git", 4)
        qapp.processEvents()
        assert workspace.mentions.inlinePopupVisible
        assert workspace.mentions.inlineTriggerKind == "@"
        assert len(workspace.mentions.inlineSuggestions) >= 1

        # Applying inline suggestion adds @git chip and removes "@git" from composer text
        updated_text = workspace.mentions.applyInlineSuggestion(0, "@git controlla questo", 4)
        qapp.processEvents()
        assert updated_text == "controlla questo"
        assert workspace.mentions.hasActiveChips
        assert bool(mentions_bar.property("visible"))

        # Add a @file chip as well
        workspace.mentions.addFileChip("main.py")
        assert workspace.mentions.activeChipCount == 2

        # Submitting consumes the chips and injects context into run.start
        workspace.workbench.submit("Verifica il file")
        start_calls = [params for method, params in sent_requests if method == "run.start"]
        assert len(start_calls) == 1
        sent_prompt = start_calls[0]["prompt"]
        assert "x = 42" in sent_prompt
        assert "Verifica il file" in sent_prompt
        assert not workspace.mentions.hasActiveChips

        # Contextual /slash command switches mode and sends when argument is provided
        sent_requests.clear()
        workspace._pending_start = None
        workspace.workbench.submit("/plan Progetta il modulo")
        assert workspace.workbench.mode == "plan"
        plan_calls = [params for method, params in sent_requests if method == "run.start"]
        assert len(plan_calls) == 1
        assert plan_calls[0]["mode"] == "plan"
        assert "Progetta il modulo" in plan_calls[0]["prompt"]
    finally:
        workspace.close()
        for root in engine.rootObjects():
            root.close()
            root.deleteLater()
        engine.deleteLater()
        qapp.processEvents()
