"""Question presentation contracts with synthetic IPC replies, never live-provider evidence."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def request(request_id="request-a", *, run_id="member-run", status="pending", **values):
    return {"run_id": run_id, "request_id": request_id, "blocking": False, "received": False,
            "status": status, "questions": [{"id": "material", "header": "Materiale",
            "question": "Quale documento sintetico vuoi leggere per primo?",
            "options": [{"label": "Documento A", "description": "Una panoramica breve."},
                        {"label": "Documento B", "description": "La specifica completa."}],
            "isOther": True}], **values}


class Replies:
    def __init__(self):
        self.rows = []
        self.calls = []
        self.pending = []
        self.delay_lists = False

    def request(self, method, params, done, failed=None, **kwargs):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "input.answer" or (method == "input.list" and self.delay_lists):
            self.pending.append((method, params, done, failed))
        elif method == "input.list":
            done({"requests": copy.deepcopy(self.rows), "has_more": False})
        return True


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "ipc"))))
    workspace._project = {"id": "fixture-project", "name": "Progetto dimostrativo", "root": "/tmp",
                          "permission": "read_only", "memory": {}, "instructions": ""}
    workspace._connected_value = True
    replies = Replies()
    monkeypatch.setattr(workspace.client, "request", replies.request)
    workspace.projectOpened.emit()
    yield workspace, workspace.inputs, replies
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_exact_answers_no_preselection_and_duplicate_submit(view):
    _, inputs, replies = view
    replies.rows = [request()]
    inputs.refresh()
    assert inputs.pendingCount == 1 and not inputs.complete
    inputs.submit()
    assert not replies.pending
    inputs.setDraft("material", "choice", "option:1")
    assert inputs.complete
    inputs.submit()
    inputs.submit()
    assert len(replies.pending) == 1
    _, params, done, _ = replies.pending.pop()
    assert params == {"run_id": "member-run", "request_id": "request-a",
                      "answers": {"material": {"answers": ["Documento B"]}}}
    row = request(status="answered", answers=params["answers"])
    replies.rows = [row]
    done({"accepted": True, "request": row})
    assert "salvata" in inputs.statusText
    assert "inoltrata" not in inputs.statusText
    row["received"] = True
    inputs.refresh()
    assert "in inoltro" in inputs.statusText
    assert "inoltrata al provider" not in inputs.statusText
    row["delivered"] = True
    inputs.refresh()
    assert "inoltrata al provider" in inputs.statusText
    assert "non è confermato" in inputs.statusText


def test_other_free_text_and_every_question_required(view):
    _, inputs, replies = view
    row = request()
    row["questions"].append({"id": "comment", "header": "Nota", "question": "Aggiungi una nota?",
                             "options": [], "isOther": False})
    replies.rows = [row]
    inputs.refresh()
    inputs.setDraft("material", "choice", "other")
    inputs.setDraft("material", "text", "Documento C")
    assert not inputs.complete
    inputs.setDraft("comment", "choice", "other")
    inputs.setDraft("comment", "text", "  ")
    assert not inputs.complete
    inputs.setDraft("comment", "text", "  Controlla le conclusioni.  ")
    inputs.submit()
    assert replies.pending[0][1]["answers"] == {
        "material": {"answers": ["Documento C"]},
        "comment": {"answers": ["Controlla le conclusioni."]}}


def test_drafts_survive_reconnect_events_and_unknown_team_run(view):
    workspace, inputs, replies = view
    replies.rows = [request()]
    inputs.refresh()
    inputs.setDraft("material", "choice", "other")
    inputs.setDraft("material", "text", "Una bozza privata")
    workspace._connected_value = False
    workspace.projectOpened.emit()
    inputs.submit()
    assert not replies.pending
    workspace._connected_value = True
    workspace.projectOpened.emit()
    assert inputs.drafts["material"]["text"] == "Una bozza privata"
    before = len(replies.calls)
    assert "member-run" not in workspace._runs
    workspace.client.eventReceived.emit({"type": "input.requested", "run_id": "member-run"})
    assert len(replies.calls) > before
    assert inputs.drafts["material"]["text"] == "Una bozza privata"
    replies.rows = []
    workspace._project = {"id": "another-project"}
    workspace.projectOpened.emit()
    assert inputs.requests == [] and inputs.drafts == {}


def test_stale_project_and_preanswer_snapshot_cannot_restore_pending(view):
    workspace, inputs, replies = view
    replies.rows = [request()]
    inputs.refresh()
    inputs.setDraft("material", "choice", "option:0")
    replies.delay_lists = True
    inputs.refresh()
    stale_list = replies.pending.pop()
    inputs.submit()
    answer = replies.pending.pop()
    accepted = request(status="answered", answers=answer[1]["answers"])
    answer[2]({"accepted": True, "request": accepted})
    stale_list[2]({"requests": [request()]})
    assert inputs.current["status"] == "answered"
    # The retry queued after discovering the stale response belongs to old project.
    retry = replies.pending.pop()
    workspace._project = {"id": "other-project"}
    workspace.projectOpened.emit()
    retry[2]({"requests": [request()]})
    assert inputs.requests == []


def test_closed_question_cannot_submit_and_can_copy_for_new_turn(view):
    _, inputs, replies = view
    replies.rows = [request()]
    inputs.refresh()
    inputs.setDraft("material", "choice", "option:0")
    replies.rows = [request(status="closed", reason="run_finished")]
    inputs.refresh()
    inputs.submit()
    assert not replies.pending
    assert "non più attiva" in inputs.statusText
    inputs.copyQuestion()
    assert "documento sintetico" in QGuiApplication.clipboard().text()


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


@pytest.fixture
def window(view, qtbot):
    workspace, _, _ = view
    engine = create_engine(workspace)
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    qtbot.wait(50)
    yield root
    root.setProperty("closeConfirmed", True)
    root.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size", [(1380, 900), (880, 620)])
def test_nonmodal_question_keyboard_other_draft_focus_and_reopen(view, window, qtbot, size):
    workspace, inputs, replies = view
    window.resize(*size)
    replies.rows = [request()]
    inputs.refresh()
    dialog = window.findChild(QObject, "inputDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert not dialog.property("modal")
    button = visual_item(window.contentItem(), "inputSubmit")
    assert button and not button.isEnabled()
    other = visual_item(window.contentItem(), "inputOther_0")
    other.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    text = visual_item(window.contentItem(), "inputText_0")
    assert text.hasActiveFocus()
    for character in "Documento C":
        QTest.keyClick(window, character)
        workspace.changed.emit()
        inputs.refresh()
        QCoreApplication.processEvents()
        assert text is visual_item(window.contentItem(), "inputText_0")
        assert text.hasActiveFocus()
    replies.rows.append(request("request-b", run_id="another-member"))
    inputs.refresh()
    QCoreApplication.processEvents()
    assert text is visual_item(window.contentItem(), "inputText_0")
    assert text.hasActiveFocus() and inputs.pendingCount == 2
    assert button.isEnabled()
    assert button.mapToScene(button.boundingRect().center()).y() < window.height()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert inputs.drafts["material"]["text"] == "Documento C"
    assert QMetaObject.invokeMethod(dialog, "open")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert visual_item(window.contentItem(), "inputText_0").property("text") == "Documento C"
    button.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert replies.pending[0][1]["answers"]["material"]["answers"] == ["Documento C"]


def test_paged_project_questions_merge_without_losing_earlier_draft(view):
    _, inputs, replies = view
    replies.rows = [request()]
    inputs.refresh()
    inputs.setDraft("material", "choice", "other")
    inputs.setDraft("material", "text", "Conserva questa scelta")
    replies.delay_lists = True
    inputs.refresh()
    first = replies.pending.pop()
    first[2]({"requests": [request()], "has_more": True, "next_offset": 1})
    second = replies.pending.pop()
    assert second[1]["offset"] == 1
    second[2]({"requests": [request("second-request")], "has_more": False})
    assert inputs.pendingCount == 2
    assert inputs.drafts["material"]["text"] == "Conserva questa scelta"


def test_submission_error_survives_refresh_and_can_be_retried(view):
    _, inputs, replies = view
    replies.rows = [request()]
    inputs.refresh()
    inputs.setDraft("material", "choice", "option:0")
    inputs.submit()
    replies.pending.pop()[3]({"code": "TEMPORARY", "message": "Synthetic temporary failure"})
    assert "temporary" in inputs.error
    assert inputs.drafts["material"]["choice"] == "option:0" and not inputs.sending
    inputs.submit()
    assert len(replies.pending) == 1


def test_long_multiquestion_dialog_scrolls_and_footer_stays_reachable(view, window, qtbot):
    _, inputs, replies = view
    window.resize(880, 620)
    row = request()
    base = row["questions"][0]
    row["questions"] = [{**copy.deepcopy(base), "id": f"question-{i}",
                         "question": "Domanda lunga. " * 100} for i in range(3)]
    replies.rows = [row]
    inputs.refresh()
    dialog = window.findChild(QObject, "inputDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    qtbot.wait(40)
    scroll = visual_item(window.contentItem(), "inputQuestionScroll")
    flickable = scroll.property("contentItem")
    assert flickable.property("contentHeight") > flickable.height()
    for i in range(3):
        inputs.setDraft(f"question-{i}", "choice", "option:0")
    button = visual_item(window.contentItem(), "inputSubmit")
    assert button.isEnabled()
    assert 0 < button.mapToScene(button.boundingRect().center()).y() < window.height()
    button.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert len(replies.pending[0][1]["answers"]) == 3


def test_delivery_failure_closes_form_and_retains_answer_for_explicit_copy(view):
    workspace, inputs, replies = view
    answers = {"material": {"answers": ["Documento B"]}}
    replies.rows = [request(status="answered", received=True, answers=answers)]
    inputs.refresh()
    replies.rows = [request(status="closed", reason="Synthetic delivery not confirmed",
                           received=False, answers=answers)]
    workspace.client.eventReceived.emit({"type": "input.delivery_failed", "run_id": "member-run"})
    assert "not confirmed" in inputs.statusText
    inputs.submit()
    assert not replies.pending
    inputs.copyQuestion()
    assert "Risposta salvata: Documento B" in QGuiApplication.clipboard().text()


def test_free_text_question_keeps_first_keystroke_and_newline(view, window, qtbot):
    _, inputs, replies = view
    row = request()
    row["questions"][0].update(options=[], isOther=False)
    replies.rows = [row]
    inputs.refresh()
    dialog = window.findChild(QObject, "inputDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    text = visual_item(window.contentItem(), "inputText_0")
    text.forceActiveFocus()
    QTest.keyClick(window, "A")
    assert text.property("text") == inputs.drafts["material"]["text"] == "A"
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.keyClick(window, "B")
    assert text.property("text") == "A\nB"
    assert inputs.complete
