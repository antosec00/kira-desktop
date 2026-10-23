"""Bounded history publication and end-position layout with synthetic Qt data."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QAbstractItemModelTester, QSignalSpy

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.models import TranscriptModel


def test_batch_publishes_once_and_preserves_visible_data_until_commit(qapp):
    model = TranscriptModel()
    model.append(kind="assistant", body="Previously visible")
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
    assert tester.model() is model
    resets = QSignalSpy(model.modelReset)
    inserted = QSignalSpy(model.rowsInserted)
    removed = QSignalSpy(model.rowsRemoved)
    changed = QSignalSpy(model.dataChanged)
    body_role = int(Qt.ItemDataRole.UserRole) + model.FIELDS.index("body")
    model.begin_batch()
    model.clear()
    for number in range(model.MAX_ROWS + 30):
        position = model.append(kind="assistant", runId=str(number), body=f"History {number}")
        model.update(position, body=f"Final history {number}")
    assert model.rowCount() == 1
    assert model.data(model.index(0), body_role) == "Previously visible"
    assert model.omitted == 30
    assert model.find("629", "assistant") == 599
    assert not any(spy.count() for spy in (resets, inserted, removed, changed))
    model.end_batch()
    assert resets.count() == 1
    assert not any(spy.count() for spy in (inserted, removed, changed))
    assert model.rowCount() == model.MAX_ROWS
    assert model.data(model.index(0), body_role) == "Final history 30"
    assert model.rows[-1]["body"] == "Final history 629"
    model.append(kind="assistant", body="Live reply")
    model.update(599, body="Live reply continued")
    assert inserted.count() == removed.count() == changed.count() == 1
    assert resets.count() == 1


def test_nested_batch_abort_restores_content_preview_and_expansion(qapp):
    model = TranscriptModel()
    body = "<literal>&\r\n" * 30
    model.append(kind="tool", body=body, raw=body, toolId="tool")
    model.setOutputExpanded(0, True)
    original = copy.deepcopy(model.rows)
    resets = QSignalSpy(model.modelReset)
    model.begin_batch()
    model.begin_batch()
    model.update(0, body="replacement")
    model.setOutputExpanded(0, False)
    model.end_batch()
    assert resets.count() == 0
    model.abort_batch()
    assert model.rows == original
    assert model.omitted == 0
    assert resets.count() == 0
    model.end_batch()
    model.abort_batch()
    assert resets.count() == 0
    model.begin_batch()
    model.update(0, body=body + "tail")
    model.end_batch()
    assert resets.count() == 1
    assert model.rows[0]["outputExpanded"] is True
    assert model.rows[0]["body"] == body + "tail"
    assert model.rows[0]["previewBody"] == "<literal>&\r\n" * 12
    assert model.rows[0]["raw"] == body


def test_empty_nested_batch_commits_once(qapp):
    model = TranscriptModel()
    model.append(kind="assistant", body="Old chat")
    resets = QSignalSpy(model.modelReset)
    model.begin_batch()
    model.begin_batch()
    model.clear()
    model.end_batch()
    assert model.rowCount() == 1 and resets.count() == 0
    model.end_batch()
    assert model.rowCount() == 0 and resets.count() == 1


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def evaluate(obj, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, code)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


@pytest.fixture
def history_ui(qapp, tmp_path, qtbot, qtlog, monkeypatch):
    view = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    view._project = {"id": "fixture", "name": "Synthetic history", "root": str(tmp_path),
                     "permission": "read_only"}
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: None)
    engine = create_engine(view)
    window = engine.rootObjects()[0]
    qtbot.wait(30)
    yield view, window
    assert not any("binding loop" in row.message.lower() or "ReferenceError" in row.message
                   for row in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_history_is_hidden_until_single_reset_is_positioned_at_end(history_ui, qtbot):
    view, window = history_ui
    conversation = item(window.contentItem(), "conversation")
    loading = item(window.contentItem(), "chatHistoryLoading")
    view._chat_loading = True
    view.chatLoadingChanged.emit()
    view.transcript.begin_batch()
    for number in range(600):
        view.transcript.append(kind="assistant", title="Synthetic reply",
                               body=f"Reply {number}\n\n" + "Visible history. " * (number % 20 + 1))
    qtbot.wait(30)
    assert loading.isVisible()
    assert conversation.opacity() == 0
    assert conversation.property("count") == 0
    revealed_positions = []
    conversation.opacityChanged.connect(
        lambda: revealed_positions.append(conversation.property("atYEnd"))
        if conversation.opacity() else None)
    view.transcript.end_batch()
    view._chat_loading = False
    view.chatLoadingChanged.emit()
    view.transcriptChanged.emit()
    qtbot.waitUntil(lambda: conversation.opacity() == 1)
    assert not loading.isVisible()
    assert conversation.property("atYEnd")
    assert revealed_positions == [True]
    assert conversation.property("count") == 600
    assert item(window.contentItem(), "messageEntry_599") is not None
    # A reader scrolling upward must not be pulled back by coalesced live changes.
    window.setProperty("followOutput", False)
    evaluate(conversation, "positionViewAtIndex(580, ListView.Beginning)")
    qtbot.wait(30)
    before = conversation.property("contentY")
    view.transcript.update(599, body="New streamed content. " * 200)
    for _ in range(20):
        view.transcriptChanged.emit()
    qtbot.wait(50)
    assert conversation.property("contentY") == pytest.approx(before, abs=2)
    assert not conversation.property("atYEnd")
