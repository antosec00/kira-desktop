"""Public summary presentation uses synthetic durable events, no provider requests."""

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from kira.ui.controller import Workspace
from kira.ui.preferences import read_preferences
from kira.ui.reasoning_controller import MAX_SUMMARY_BYTES
from test_ui_workbench import evaluate, item, ui as ui, view as view


def active(workspace, run_id="run", session="chat", created="2026-01-01T00:00:00Z"):
    workspace._runs[run_id] = {"id": run_id, "session_id": session, "state": "running", "prompt": "Synthetic request", "created_at": created}
    workspace.changed.emit()


def summary(workspace, content="Public <b>summary</b>", *, run_id="run", seq=1, item_id="one", kind="delta", **fields):
    workspace._event({"run_id": run_id, "seq": seq, "type": "reasoning.summary." + kind,
                      "payload": {"item_id": item_id, "content": content, "provider": "openai_chatgpt", "source": "provider_summary", **fields}})


def test_summary_deltas_preserve_chat_draft_and_execution_phase(ui, qtbot):
    workspace, calls, _, window = ui
    active(workspace)
    prompt = item(window.contentItem(), "prompt")
    prompt.setProperty("text", "My editable draft")
    prompt.setProperty("cursorPosition", 5)
    state = deepcopy(workspace._session)
    calls.clear()
    transcript_signals = []
    workspace.transcriptChanged.connect(lambda: transcript_signals.append(True))
    for seq in range(1, 41):
        summary(workspace, "word ", seq=seq)
    qtbot.wait(180)
    panel = item(window.contentItem(), "reasoningSummaryPanel")
    assert panel.property("visible") and not panel.property("expanded")
    assert workspace.reasoning.current["text"] == "word " * 40
    assert workspace.activityLabel == "Sto pensando…"
    assert not workspace._streaming_items and not workspace.transcript.rows
    assert not transcript_signals and not calls
    assert workspace._session == state
    assert prompt.property("text") == "My editable draft" and prompt.property("cursorPosition") == 5
    assert not workspace.activityHasToolIssues
    toggle = item(window.contentItem(), "reasoningSummaryToggle")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=toggle.mapToScene(toggle.boundingRect().center()).toPoint())
    assert panel.property("expanded")
    toggle.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert not panel.property("expanded")


def test_reordered_and_duplicate_events_complete_once(view):
    workspace, _ = view
    active(workspace)
    summary(workspace, "complete public summary", seq=3, kind="completed", complete=True)
    summary(workspace, "public ", seq=1)
    summary(workspace, "summary", seq=2)
    summary(workspace, "public ", seq=1)
    assert workspace.reasoning.current["text"] == "complete public summary"
    summary(workspace, "late extra", seq=4)
    assert workspace.reasoning.current["text"] == "complete public summary"
    assert workspace.reasoning.current["status"] == "Ricevuto dal provider"


def test_cancelled_and_truncated_summary_stays_partial_and_plain(ui, qtbot):
    workspace, _, _, window = ui
    active(workspace)
    summary(workspace, "<b>Literal content</b>")
    summary(workspace, "<b>Literal content</b>", seq=2, kind="completed", complete=False, truncated=True)
    workspace._event({"run_id": "run", "seq": 3, "type": "run.state", "payload": {"state": "cancelled"}})
    qtbot.wait(180)
    panel = item(window.contentItem(), "reasoningSummaryPanel")
    panel.setProperty("expanded", True)
    text = item(window.contentItem(), "reasoningSummaryText")
    assert text.property("text") == "<b>Literal content</b>"
    assert evaluate(text, "textFormat === TextEdit.PlainText")
    assert workspace.reasoning.current["partial"] and workspace.reasoning.current["truncated"]
    assert workspace.reasoning.current["status"] == "Riepilogo parziale"
    assert not workspace.activityHasToolIssues


def test_current_run_never_displays_previous_turn_as_its_own(ui, qtbot):
    workspace, _, _, window = ui
    active(workspace)
    summary(workspace, "Previous public summary", kind="completed", complete=True)
    workspace._runs["run"]["state"] = "completed"
    active(workspace, "second", created="2026-01-02T00:00:00Z")
    assert workspace.reasoning.current == {"runId": "second", "available": False}
    panel = item(window.contentItem(), "reasoningSummaryPanel")
    panel.setProperty("expanded", True)
    qtbot.wait(180)
    assert item(window.contentItem(), "reasoningSummaryHistory").property("visible")
    workspace.reasoning.selectRun("run")
    assert workspace.reasoning.current["text"] == "Previous public summary"
    assert workspace.reasoning.current["historical"]
    workspace.reasoning.selectRun("")
    assert not workspace.reasoning.current["available"]


def test_chat_reset_and_delayed_old_replay_cannot_leak_summary(view, qtbot):
    workspace, calls = view
    active(workspace)
    summary(workspace, "First chat only")
    workspace._subscribe("run")
    callback = next(call[2] for call in reversed(calls) if call[0] == "run.subscribe")
    workspace._session = {**workspace._session, "id": "other"}
    workspace._reset_chat()
    active(workspace, "other-run", session="other")
    callback({"events": [{"run_id": "run", "seq": 2, "type": "reasoning.summary.delta", "payload": {"provider": "openai_chatgpt", "source": "provider_summary", "item_id": "one", "content": "Leaked"}}]})
    qtbot.wait(180)
    assert workspace.reasoning.history == []
    assert not workspace.reasoning.current.get("text")
    workspace._session["id"] = "chat"
    workspace._reset_chat()
    workspace.changed.emit()
    summary(workspace, "First chat only", kind="completed", complete=True)
    assert workspace.reasoning.current["text"] == "First chat only"


def test_hidden_preference_persists_without_changing_provider_or_events(ui, qtbot):
    workspace, calls, _, window = ui
    active(workspace)
    summary(workspace)
    snapshot = deepcopy(workspace._session)
    calls.clear()
    workspace.setUiPreference("show_reasoning_summaries", False)
    summary(workspace, " still received", seq=2)
    qtbot.wait(180)
    assert not item(window.contentItem(), "reasoningSummaryPanel").property("visible")
    assert workspace.reasoning.current["text"].endswith(" still received")
    assert read_preferences(workspace.paths.config)["show_reasoning_summaries"] is False
    reopened = Workspace(workspace.paths)
    try:
        assert reopened.uiPreferences["show_reasoning_summaries"] is False
    finally:
        reopened.close()
        reopened.deleteLater()
    workspace.setUiPreference("show_reasoning_summaries", True)
    assert item(window.contentItem(), "reasoningSummaryPanel").property("visible")
    assert workspace._session == snapshot and not calls


def test_language_changes_labels_only(ui, qtbot):
    workspace, calls, engine, window = ui
    active(workspace)
    summary(workspace, "Riepilogo parziale", kind="completed", complete=False)
    workspace._event({"run_id": "run", "seq": 2, "type": "run.state", "payload": {"state": "interrupted"}})
    calls.clear()
    engine.rootContext().contextProperty("localization").setLanguage("en")
    qtbot.wait(180)
    assert workspace.reasoning.current["status"] == "Partial summary"
    assert workspace.reasoning.current["text"] == "Riepilogo parziale"
    assert "Reasoning summary" in item(window.contentItem(), "reasoningSummaryToggle").property("text")
    assert not calls


@pytest.mark.parametrize("status", ["unavailable", "unsupported"])
def test_unavailable_provider_never_creates_fabricated_content(view, status):
    workspace, _ = view
    active(workspace)
    summary(workspace, "not summary content", kind="status", status=status, provider="ollama")
    assert workspace.reasoning.current["text"] == ""
    assert workspace.reasoning.current["provider"] == "Ollama"
    assert not workspace.transcript.rows


def test_only_public_projection_and_bounded_text_items(view):
    workspace, _ = view
    active(workspace)
    summary(workspace, "private", source="raw_reasoning")
    summary(workspace, "private", seq=2, provider="unknown-provider")
    assert not workspace.reasoning.current.get("available")
    for seq in range(3, 69):
        summary(workspace, "é" * 1024, seq=seq, item_id=f"part-{seq}", encrypted_content="must never display", raw_reasoning="must never display")
    result = workspace.reasoning.current
    assert len(result["text"].encode("utf-8")) <= MAX_SUMMARY_BYTES
    assert len(workspace.reasoning._runs["run"]["items"]) <= 64
    assert result["truncated"] and "must never display" not in str(result)


@pytest.mark.parametrize("browser", [False, True])
def test_big_font_narrow_panel_preserves_composer(ui, qtbot, browser):
    workspace, _, engine, window = ui
    active(workspace)
    summary(workspace, "Public summary text. " * 800)
    appearance = engine.rootContext().contextProperty("appearance")
    appearance.setOption("ui_size", 18)
    panel = item(window.contentItem(), "reasoningSummaryPanel")
    panel.setProperty("expanded", True)
    window.resize(1380 if browser else 880, 900 if browser else 620)
    if browser:
        workspace.web._pane_visible = True
        workspace.web.changed.emit()
    qtbot.wait(200)
    scroll = item(window.contentItem(), "reasoningSummaryScroll")
    prompt = item(window.contentItem(), "prompt")
    assert 0 < scroll.height() <= panel.property("maximumBodyHeight")
    assert prompt.property("visible") and prompt.height() > 0
    assert panel.width() <= item(window.contentItem(), "composerCard").width() + 1
    assert scroll.width() <= panel.width()


@pytest.mark.parametrize("reason", ["summary_item_limit", "summary_byte_limit", "summary_event_limit"])
def test_limit_status_marks_received_summary_as_truncated_without_losing_content(view, reason):
    workspace, _ = view
    active(workspace)
    summary(workspace, "Received public section", kind="completed", complete=True)
    summary(workspace, "", seq=2, kind="status", status="unavailable", reason=reason)
    current = workspace.reasoning.current
    assert current["text"] == "Received public section"
    assert current["status"] == "Ricevuto dal provider"
    assert current["truncated"] and not current["partial"]
    assert not workspace.activityHasToolIssues and not workspace.transcript.rows
