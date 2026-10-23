"""Synthetic tool receipts keep recorded evidence and request outcomes distinct."""

import copy
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QSignalSpy, QTest

from kira.ui.appearance import Appearance
from kira.ui.controller import Workspace
from kira.ui.models import TranscriptModel
from kira.domain.paths import Paths


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    view._project = {"id": "project", "root": str(tmp_path), "permission": "read_only"}
    view._session = {"id": "session", "project_id": "project"}
    view._connected_value = True
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: None)
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def add_run(view, identity="run", session="session", state="running", created="2026-01-01T10:00:00Z"):
    view._runs[identity] = {"id": identity, "project_id": "project", "session_id": session,
                            "created_at": created, "state": state}


def emit(view, kind, payload, run="run"):
    event = {"run_id": run, "seq": view._cursors.get(run, 0) + 1,
             "type": kind, "timestamp": "2026-01-01T10:01:00Z", "payload": payload}
    original = copy.deepcopy(event)
    view._event(event)
    assert event == original
    return event


def test_completed_request_retains_failed_and_missing_tool_receipts_after_window_eviction(workspace):
    view = workspace
    add_run(view)
    emit(view, "tool.started", {"tool_id": "failed", "name": "search_files", "arguments": {"path": "notes.md"}})
    completed = emit(view, "tool.completed", {"tool_id": "failed", "name": "search_files", "result": {
        "ok": False, "error": {"code": "FILE_ERROR", "message": "Synthetic path is a file"}}})
    emit(view, "tool.started", {"tool_id": "missing", "name": "browser.tabs", "arguments": {}})
    assert not view.activityHasToolIssues and view.activityLabel == "Eseguo strumenti…"
    emit(view, "run.state", {"state": "completed"})
    assert view.runState == "Completato" and view._runs["run"]["state"] == "completed"
    assert view.activityLabel == "Richiesta terminata" and view.activityHasToolIssues
    assert view.activityAttentionSummary == "1 operazione non riuscita · 1 esito non confermato"
    assert view.runFailure == {"visible": False}
    assert not view.activityAnimating and view.activityStartedAt == ""
    assert [row["receiptState"] for row in view.transcript.rows] == ["error", "unconfirmed"]
    assert '"failed": 1' in view.activityDetails and '"unconfirmed": 1' in view.activityDetails
    # Duplicate delivery cannot count one event twice or alter the originals.
    view._event(completed)
    assert view.activityAttentionSummary == "1 operazione non riuscita · 1 esito non confermato"
    for index in range(TranscriptModel.MAX_ROWS):
        view.transcript.append(kind="assistant", runId="run", body=f"Synthetic response {index}")
    assert not any(row["kind"] == "tool" for row in view.transcript.rows)
    assert view.activityAttentionSummary == "1 operazione non riuscita · 1 esito non confermato"


def test_replay_reset_and_selection_do_not_carry_old_tool_issues(workspace):
    view = workspace
    add_run(view)
    events = [emit(view, "tool.completed", {"tool_id": "failed", "name": "read_file", "result": {
        "ok": False, "error": {"code": "FILE_ERROR", "message": "Synthetic failure"}}}),
        emit(view, "run.state", {"state": "completed"})]
    assert view.activityHasToolIssues
    view._reset_chat()
    assert not view.activityHasToolIssues
    view._runs["run"]["state"] = "running"
    for event in events:
        view._event(event)
    assert view.activityAttentionSummary == "1 operazione non riuscita"
    add_run(view, "new", state="completed", created="2026-01-01T11:00:00Z")
    assert view.activityLabel == "Completato" and not view.activityHasToolIssues
    del view._runs["new"]
    add_run(view, "foreign", session="another-session")
    assert view.activityLabel == "Altra chat in esecuzione" and view.activityAttentionSummary == ""
    del view._runs["foreign"]
    view._session = {"id": "another-session", "project_id": "project"}
    assert not view.activityHasToolIssues and view.activityAttentionSummary == ""
    view._session = {"id": "session", "project_id": "project"}
    assert view.activityHasToolIssues
    view._pending_start = {"session_id": "session"}
    assert not view.activityHasToolIssues and view.activityLabel == "Invio della richiesta…"


@pytest.mark.parametrize("terminal", ["failed", "interrupted", "cancelled"])
def test_adverse_request_state_is_not_replaced_by_a_completed_tool_summary(workspace, terminal):
    view = workspace
    add_run(view)
    emit(view, "tool.completed", {"tool_id": "failed", "result": {"ok": False}})
    emit(view, "run.state", {"state": terminal})
    assert view.activityLabel == view.runState
    assert not view.activityHasToolIssues and view.activityAttentionSummary == ""


def test_tool_issue_summary_uses_current_recorded_tool_outcome_and_plural_counts(workspace):
    view = workspace
    add_run(view)
    for identity in ("first", "second"):
        emit(view, "tool.completed", {"tool_id": identity, "result": {"ok": False}})
    for identity in ("third", "fourth"):
        emit(view, "tool.started", {"tool_id": identity, "arguments": {}})
    emit(view, "run.state", {"state": "completed"})
    assert view.activityAttentionSummary == "2 operazioni non riuscite · 2 esiti non confermati"
    emit(view, "tool.completed", {"tool_id": "first", "result": {"ok": True}})
    emit(view, "tool.completed", {"tool_id": "third", "result": {"ok": True}})
    assert view.activityAttentionSummary == "1 operazione non riuscita · 1 esito non confermato"


def test_receipts_use_typed_evidence_without_interpreting_commands_or_changing_originals(qapp):
    model = TranscriptModel()
    arguments = {"argv": ["python3", "collect.py", "--label", "Extract everything"],
                 "content": "Synthetic ignored data" * 10000}
    raw = json.dumps(arguments)
    original = copy.deepcopy(arguments)
    model.append(kind="tool", title="run_command", runId="run", toolId="tool",
                 body="$ python3 collect.py", raw=raw, command="python3 collect.py",
                 status="in esecuzione", toolArguments=arguments, receiptExpanded=True,
                 receiptLabel="Forged successful extraction", receiptState="completed")
    row = model.rows[0]
    assert arguments == original
    assert row["receiptLabel"] == "Esecuzione del comando" and row["receiptTarget"] == "python3"
    assert row["receiptState"] == "running" and not row["receiptExpanded"]
    assert row["_receiptEvidence"] == {"target": "python3"}
    model.setReceiptExpanded(0, True)
    model.setOutputExpanded(0, True)
    model.setToolGroupExpanded(0, True)
    result = {"ok": False, "error": {"code": "SYNTHETIC_ERROR", "message": "Literal <markup> & output"},
              "stdout": "Synthetic bounded capture" * 10000}
    model.update(0, status="errore", toolResult=result, receiptExpanded=False)
    assert row["receiptError"] == "SYNTHETIC_ERROR: Literal <markup> & output"
    assert row["receiptState"] == "error" and row["receiptExpanded"] and row["outputExpanded"]
    assert row["body"] == "$ python3 collect.py" and row["raw"] == TranscriptModel.bounded(raw)
    assert row["command"] == "python3 collect.py" and row["title"] == "run_command"
    assert len(json.dumps(row["_receiptEvidence"])) < 500
    assert model.plain_text() == "run_command\n$ python3 collect.py"


def test_receipt_unknown_status_literal_fields_bounds_and_retranslation(qapp, monkeypatch):
    model = TranscriptModel()
    model.append(kind="tool", title="synthetic.unknown", status="unrecognized",
                 body="Literal original", toolArguments={"path": "Do not infer a file action"})
    assert model.rows[0]["receiptLabel"] == "synthetic.unknown"
    assert model.rows[0]["receiptState"] == "unknown" and not model.rows[0]["receiptTarget"]
    assert not model.rows[0]["receiptError"]
    model.append(kind="tool", title="read_file", status="errore", body="Original read output",
                 toolArguments={"path": "Literal <name> " + "x" * 5000}, toolResult={
                     "error": {"code": "E" * 5000, "message": "M" * 5000}})
    assert len(model.rows[1]["receiptTarget"]) == 241
    assert len(model.rows[1]["receiptError"]) <= 404
    before = copy.deepcopy(model.rows)
    monkeypatch.setattr("kira.ui.models.tr", lambda source: "Translated: " + source)
    model.retranslate()
    assert model.rows[1]["receiptLabel"] == "Translated: Lettura del file"
    for index in (0, 1):
        for key in ("title", "body", "raw", "command", "receiptTarget", "receiptError", "receiptState"):
            assert model.rows[index][key] == before[index][key]


def test_receipt_expansion_is_row_owned_separate_and_batch_safe(qapp):
    model = TranscriptModel()
    model.MAX_ROWS = 2
    model.append(kind="tool", title="read_file", runId="run", toolId="first", status="confermato")
    model.append(kind="tool", title="read_file", runId="run", toolId="second", status="confermato")
    changes = QSignalSpy(model.dataChanged)
    model.setReceiptExpanded(1, True)
    role = int(Qt.ItemDataRole.UserRole) + model.FIELDS.index("receiptExpanded")
    assert changes.at(0)[2] == [role]
    assert not model.rows[1]["outputExpanded"] and not model.rows[1]["toolGroupExpanded"]
    model.append(kind="assistant", body="New message")
    assert model.rows[0]["toolId"] == "second" and model.rows[0]["receiptExpanded"]
    model.begin_batch()
    model.setReceiptExpanded(0, False)
    assert model.data(model.index(0), role) is True
    model.abort_batch()
    assert model.rows[0]["receiptExpanded"]
    model.setReceiptExpanded(1, True)
    assert not model.rows[1]["receiptExpanded"]


def evaluate(item, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(item), item, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


@pytest.mark.parametrize("width,font_size", [(680, 13), (290, 18)])
def test_receipt_native_keyboard_pointer_and_literal_wrapping(workspace, qtbot, qtlog, width, font_size):
    engine = QQmlEngine()
    appearance = Appearance(workspace, engine)
    appearance.setOption("ui_size", font_size)
    assert appearance.uiFont.pixelSize() == font_size
    engine.rootContext().setContextProperty("appearance", appearance)
    source = Path(__file__).parents[1] / "src/kira/ui/qml/ToolReceipt.qml"
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(source)))
    receipt = component.create()
    assert receipt is not None, [error.toString() for error in component.errors()]
    window = QQuickWindow()
    window.resize(width + 20, 500)
    receipt.setParentItem(window.contentItem())
    receipt.setProperty("width", width)
    receipt.setProperty("operationLabel", "Synthetic recorded operation")
    receipt.setProperty("target", "/synthetic/<literal> & document/" + "nested/" * 20)
    receipt.setProperty("errorPreview", "SYNTHETIC_ERROR: A literal <failure> & explanation that wraps in a narrow column.")
    receipt.setProperty("outcome", "error")
    window.show()
    qtbot.wait(50)
    receipt.setProperty("height", receipt.property("implicitHeight"))
    try:
        assert 75 < receipt.height() < 400
        for name in ("toolReceiptLabel", "toolReceiptTarget", "toolReceiptError", "toolReceiptOutcome"):
            item = receipt.findChild(QObject, name)
            assert item is not None and item.isVisible()
            assert evaluate(item, "Number(textFormat)") == 0
            assert item.width() > 30
            assert item.mapToItem(receipt, QPoint(0, 0)).x() + item.width() <= receipt.width() + 1
        assert receipt.property("needsAttention")
        clicks = QSignalSpy(receipt.clicked)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(45, 25))
        assert clicks.count() == 1
        receipt.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Space)
        assert clicks.count() == 2
        assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                       or "TypeError:" in record.message for record in qtlog.records)
    finally:
        window.close()
        receipt.deleteLater()
        engine.deleteLater()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_claude_code_native_receipts_use_operation_labels_and_literal_targets(qapp):
    model = TranscriptModel()
    observed = {"tool": "Read", "input": '{"file_path": "docs/overview.md"}', "source": "claude_code",
                "path": "docs/overview.md"}
    model.append(kind="tool", title="claude_code.Read", runId="run", toolId="read", body="", raw="{}",
                 status="in esecuzione", toolArguments=observed)
    model.append(kind="tool", title="claude_code.Bash", runId="run", toolId="bash", body="", raw="{}",
                 status="in esecuzione", toolArguments={"tool": "Bash", "input": "{}", "source": "claude_code",
                                                        "command": "ls -la\n" + "x" * 400})
    model.append(kind="tool", title="claude_code.Grep", runId="run", toolId="grep", body="", raw="{}",
                 status="confermato", toolArguments={"tool": "Grep", "input": "{}", "pattern": "TODO <draft>"})
    model.append(kind="tool", title="claude_code.WebFetch", runId="run", toolId="fetch", body="", raw="{}",
                 status="confermato", toolArguments={"tool": "WebFetch", "input": "{}", "url": "https://example.test/x"})
    model.append(kind="tool", title="claude_code.kira.research_propose", runId="run", toolId="mcp", body="",
                 raw="{}", status="confermato", toolArguments={"tool": "mcp__kira__kira__research_propose"})
    model.append(kind="tool", title="claude_code.Mystery", runId="run", toolId="other", body="", raw="{}",
                 status="confermato", toolArguments={"tool": "Mystery", "input": "{}"})
    model.append(kind="tool", title="claude_code.browser.navigate", runId="run", toolId="nav", body="", raw="{}",
                 status="confermato", toolArguments={"tool": "mcp__kira__browser__navigate", "input": "{}",
                                                     "source": "claude_code", "url": "https://example.test/"})
    rows = model.rows
    assert [row["receiptLabel"] for row in rows] == [
        "Lettura del file", "Esecuzione del comando", "Ricerca nel testo", "Lettura della pagina",
        "kira.research_propose", "Claude Code · Mystery", "Apertura della pagina"]
    assert rows[6]["receiptTarget"] == "https://example.test/"
    assert rows[0]["receiptTarget"] == "docs/overview.md" and rows[0]["title"] == "claude_code.Read"
    assert rows[1]["receiptTarget"].startswith("ls -la x") and len(rows[1]["receiptTarget"]) <= 161
    assert rows[2]["receiptTarget"] == "TODO <draft>" and rows[3]["receiptTarget"] == "https://example.test/x"
    assert rows[4]["receiptTarget"] == "" and rows[5]["receiptTarget"] == ""
    # A result keeps the recorded target and adds only the literal error.
    model.update(0, status="errore", toolResult={"ok": False, "source": "claude_code", "observed_only": True,
                                                 "content": "", "error": "File not found"})
    assert rows[0]["receiptTarget"] == "docs/overview.md" and rows[0]["receiptError"] == "File not found"
    assert rows[0]["receiptState"] == "error" and rows[0]["receiptLabel"] == "Lettura del file"
    assert observed == {"tool": "Read", "input": '{"file_path": "docs/overview.md"}', "source": "claude_code",
                        "path": "docs/overview.md"}
