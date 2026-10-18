"""Execution outcomes live in Activity, retaining context without chat clutter."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


@pytest.fixture
def view(qapp, qtbot, qtlog, tmp_path):
    workspace = Workspace(Paths(*(tmp_path / p for p in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "synthetic", "name": "Execution summaries", "root": str(tmp_path)}
    workspace._session = {"id": "chat", "project_id": "synthetic"}
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
    qtbot.wait(60)
    yield workspace, window
    assert not any("binding loop" in record.message.lower() for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def control(window, name, index=0):
    result = item(window.contentItem(), f"{name}_{index}")
    assert result is not None
    return result


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def descendants(root):
    yield root
    for child in root.childItems():
        yield from descendants(child)


def add_state(workspace, prompt, *, index=0, state="completed", error=""):
    run_id = f"synthetic-{index}"
    workspace._runs[run_id] = {"id": run_id, "session_id": "chat", "prompt": prompt,
        "state": "running", "created_at": f"2026-10-14T03:0{index}:00+00:00"}
    workspace._event({"run_id": run_id, "seq": 1, "type": "run.state",
        "timestamp": f"2026-10-14T03:0{index}:30+00:00",
        "payload": {"state": state, "error": error}})


def activity_items(window):
    return list(descendants(evaluate(window, "activityList.contentItem")))


def test_four_completed_runs_only_in_activity_with_request_context(view, qtbot):
    workspace, window = view
    prompts = ["Controlla i file", "Aggiorna la guida", "Esegui i test", "Prepara il pacchetto"]
    for index, prompt in enumerate(prompts):
        add_state(workspace, prompt, index=index)
    qtbot.wait(80)
    assert workspace.transcript.rows == []
    assert evaluate(window, "conversation.count") == 0
    assert all(not obj.isVisible() for obj in activity_items(window))
    window.setProperty("page", 2)
    qtbot.wait(80)
    texts = [obj.property("text") for obj in activity_items(window) if obj.isVisible()]
    assert texts.count("Completato") == 4
    for prompt in prompts:
        assert prompt in texts
    for entry in workspace.activity:
        assert entry["startedAt"] and entry["endedAt"]
        assert json.loads(entry["details"])["request"] in prompts


@pytest.mark.parametrize(("state", "title", "error"), [
    ("failed", "Non riuscito", "Connessione al provider non disponibile"),
    ("interrupted", "Interrotto · esito non confermato", "Conferma dell'esito non disponibile"),
    ("cancelled", "Annullato", ""),
])
def test_unsuccessful_outcomes_keep_request_and_error_in_activity(view, qtbot, state, title, error):
    workspace, window = view
    add_state(workspace, "Controlla README.md", state=state, error=error)
    assert workspace.transcript.rows == []
    window.setProperty("page", 2)
    qtbot.wait(80)
    texts = [obj.property("text") for obj in activity_items(window) if obj.isVisible()]
    assert title in texts
    assert "Controlla README.md" in texts
    if error:
        assert error in texts


def test_activity_excerpt_is_literal_and_details_contain_full_request(view, qtbot):
    workspace, window = view
    prompt = '<img src="https://invalid.test/image.png"> **literal** ' + "test " * 300
    error = '<b>Literal failure</b>'
    add_state(workspace, prompt, state="failed", error=error)
    window.setProperty("page", 2)
    qtbot.wait(80)
    excerpt = next(obj for obj in activity_items(window)
                   if obj.property("text") == workspace.activity[0]["requestExcerpt"])
    assert evaluate(excerpt, "Number(textFormat)") == 0
    assert len(excerpt.property("text")) <= 160
    button = next(obj for obj in activity_items(window)
                  if obj.property("text") == "Dettagli" and obj.property("checkable") is not None)
    QMetaObject.invokeMethod(button, "clicked")
    qtbot.wait(80)
    assert evaluate(window, "eventDialog.visible")
    assert window.property("eventDetailsTitle") == "Dettagli dell'esecuzione"
    details = item(window.contentItem(), "eventDetailsText")
    assert details is not None
    raw = json.loads(details.property("text"))
    assert raw["request"] == prompt and raw["error"] == error
    assert raw["started_at"] and raw["event_timestamp"]
    assert evaluate(details, "Number(textFormat)") == 0
    evaluate(window, "eventDialog.close()")


def test_tool_and_assistant_presentation_remain_unchanged(view, qtbot):
    workspace, window = view
    workspace.transcript.append(kind="tool", title="Check files", body="<b>literal</b>\n" * 30,
                                status="confermato", raw='{"ok":true}')
    workspace.transcript.append(kind="assistant", title="KIRA", body="**Check complete**")
    qtbot.wait(80)
    receipt = control(window, "toolReceipt")
    assert receipt.isVisible() and receipt.property("outcome") == "completed"
    assert not control(window, "outputToggle").isVisible()
    receipt.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    qtbot.wait(30)
    assert workspace.transcript.rows[0]["receiptExpanded"]
    assert control(window, "messageDetails").property("text") == "Dati"
    assert control(window, "outputToggle").isVisible()
    assert evaluate(control(window, "messageText"), "Number(textFormat)") == 0
    assert control(window, "messageStatus").isVisible()
    assert evaluate(control(window, "messageText", 1), "Number(textFormat)") == 1
    assert control(window, "messageSource", 1).isVisible()
    QMetaObject.invokeMethod(control(window, "messageDetails"), "clicked")
    qtbot.wait(60)
    assert window.property("eventDetailsTitle") == "Dati registrati dello strumento"
