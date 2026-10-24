"""Synthetic project registry UI: durable drafts, literal evidence, no inference."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.research_controller import ResearchController, empty_record, present_record
from test_ui_support import wait_laid_out, wait_opened


def record(**changes):
    return {**empty_record(), "id": "record-a", "project_id": "project", "revision": 1,
            "title": "Synthetic <question>", "body": "Literal <body> %1", "origin": "user", **changes}


def listed(**changes):
    return {"project_id": "project", "records": [], "settings": {"enabled": False, "revision": 0},
            "has_more": False, "next_cursor": None, **changes}


@pytest.fixture
def research(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path)}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "codex",
                          "model": "synthetic-model", "settings": {}}
    workspace._backend, workspace._model = "codex", "synthetic-model"
    workspace.changed.emit()
    requests = []
    def request(*args, **kwargs):
        if args[0] == "goal.get":
            args[2]({"goal": None})
        elif args[0] != "assistant.snapshot":
            requests.append(args)
    monkeypatch.setattr(workspace.client, "request", request)
    controller = ResearchController(workspace)
    yield workspace, controller, requests
    controller.flushDrafts()
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    result, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


def test_research_open_only_lists_project_without_starting_work(research):
    workspace, controller, requests = research
    workspace.setDraftText("Unsent composer")
    controller.open()
    assert requests[0][:2] == ("research.list", {"project_id": "project", "view": "knowledge", "limit": 20})
    requests[-1][2](listed(records=[record()]))
    assert not controller.report["settings"]["enabled"]
    assert controller.report["records"][0]["title"] == "Synthetic <question>"
    assert workspace.draftText == "Unsent composer"
    assert len(requests) == 1


def test_project_change_drops_late_responses_but_preserves_drafts(research):
    workspace, controller, requests = research
    controller.setField("title", "First draft")
    controller.refresh()
    old = requests[-1][2]
    workspace._project = {"id": "other", "name": "Other", "root": workspace.project["root"]}
    workspace.changed.emit()
    old(listed(records=[record(title="Do not render")]))
    assert controller.editor["title"] == "" and controller.report["records"] == []
    controller.setField("title", "Other draft")
    workspace._project["id"] = "project"
    workspace.changed.emit()
    assert controller.editor["title"] == "First draft"


def test_chat_switch_and_language_changes_keep_project_draft(research):
    workspace, controller, requests = research
    controller.setField("body", "Literal <code> %2")
    controller.refresh()
    workspace._session["id"] = "second-chat"
    workspace.changed.emit()
    requests[-1][2](listed(records=[record()]))
    assert controller.report["records"]
    workspace.preferencesChanged.emit()
    assert controller.editor["body"] == "Literal <code> %2"


def test_uncertain_save_survives_restart_and_reuses_exact_receipt(research):
    workspace, controller, requests = research
    controller.setField("title", "Retained objective")
    controller.save()
    original = requests[-1]
    receipt = original[1]["request_id"]
    stored = json.loads(controller._store.path.read_text())
    assert receipt in json.dumps(stored)
    assert not workspace._pending_intents
    original[3]({"code": "DISCONNECTED"})
    assert controller.editor["title"] == "Retained objective"
    restarted = ResearchController(workspace)
    assert restarted.editor["title"] == "Retained objective"
    restarted.save()
    assert requests[-1][1] == original[1]
    requests[-1][2]({"project_id": "project", "record": record(title="Retained objective"),
                      "operation": {"request_id": receipt, "status": "completed"}})
    assert restarted.editor["expected_revision"] == 1
    assert restarted.report["pending_count"] == 0


def test_rejected_save_keeps_literal_draft_and_new_retry_identity(research):
    _, controller, requests = research
    controller.setField("title", "Retained")
    controller.setField("body", "x" * 8001)
    controller.save()
    first = requests[-1][1]["request_id"]
    requests[-1][3]({"code": "INVALID_ARGUMENT", "message": "Synthetic bound"})
    assert controller.editor["body"] == "x" * 8001
    assert controller.report["pending_count"] == 0
    controller.setField("body", "Corrected")
    controller.save()
    assert requests[-1][1]["request_id"] != first


def test_revision_requires_reason_and_explicit_reassessment(research):
    _, controller, requests = research
    controller.editRecord("record-a")
    requests[-1][2]({"project_id": "project", "record": record(assessment="supported", origin="assistant")})
    controller.setField("body", "Correction")
    controller.save()
    assert len(requests) == 1
    controller.setField("reason", "New evidence")
    controller.save()
    params = requests[-1][1]
    assert params["expected_revision"] == 1 and params["reason"] == "New evidence"
    assert "reassess" not in params
    assert "origin" not in params["record"]
    requests[-1][3]({"code": "CONFLICT", "message": "Revision changed"})
    assert controller.editor["body"] == "Correction"
    assert controller.editor["origin_label"] == "Proposta dell’assistente"


def test_list_source_and_history_pagination_preserve_original_values(research):
    _, controller, requests = research
    controller.refresh()
    requests[-1][2](listed(has_more=True, next_cursor="r:20"))
    controller.nextPage()
    assert requests[-1][1]["cursor"] == "r:20"
    requests[-1][2](listed())
    controller.previousPage()
    assert "cursor" not in requests[-1][1]
    requests[-1][2](listed())
    controller.openSources()
    requests[-1][2]({"project_id": "project", "items": [{"ref": {"kind": "run", "run_id": "r"},
                                                         "label": "Synthetic run"}], "has_more": False})
    controller.openRunSources("r")
    assert requests[-1][1]["run_id"] == "r"
    requests[-1][2]({"project_id": "project", "items": [], "has_more": False})
    source = {"kind": "event", "run_id": "r", "seq": 7}
    controller.addSource(source)
    controller.readSource(source)
    requests[-1][2]({"source": source, "content": "<literal first>", "offset": 0, "next_offset": 123,
                      "has_more": True, "availability": "available", "integrity": "durable_record"})
    assert controller.source["content"] == "<literal first>"
    controller.nextSourcePage()
    assert requests[-1][1]["offset"] == 123
    requests[-1][2]({"source": source, "content": "final", "offset": 123, "has_more": False})
    controller.previousSourcePage()
    assert requests[-1][1]["offset"] == 0


def test_capture_dependencies_and_record_links_never_execute_tools(research):
    _, controller, requests = research
    controller.captureDependencies("src/example.py\nREADME.md")
    assert requests[-1][:2] == ("research.dependencies.capture", {"project_id": "project",
                                                                "paths": ["src/example.py", "README.md"]})
    deps = [{"path": "README.md", "sha256": "a" * 64}]
    requests[-1][2]({"dependencies": deps})
    assert controller.editor["dependencies"] == deps
    controller.addLink("other-record", 3, "contradicts")
    assert controller.editor["links"] == [{"record_id": "other-record", "revision": 3, "relation": "contradicts"}]
    assert all(request[0].startswith("research.") for request in requests)


def test_corrupt_research_draft_is_retained_and_blocks_mutations(research):
    workspace, controller, requests = research
    controller.setField("title", "Safe draft")
    controller.flushDrafts()
    controller._store.path.write_text("{broken", encoding="utf-8")
    restarted = ResearchController(workspace)
    assert not restarted.canEdit
    restarted.save()
    assert not requests
    assert controller._store.path.read_text() == "{broken"
    # Avoid fixture recovery overwriting intentional corruption through the old object.
    controller._store.blocked = True


def test_present_record_never_renders_private_native_fields():
    shown = present_record(record(encrypted_content="DO_NOT_RENDER", reasoning="DO_NOT_RENDER"))
    assert "DO_NOT_RENDER" not in json.dumps(shown)
    assert shown["body"] == "Literal <body> %1"


@pytest.mark.parametrize("language,size", [("it", 13), ("en", 18)])
def test_actual_research_dialog_is_scrollable_translated_and_literal(research, qtbot, qtlog, language, size):
    workspace, _, requests = research
    workspace.setUiPreference("language", language)
    workspace.setUiPreference("appearance", {**workspace.uiPreferences["appearance"], "ui_size": size})
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(880)
    window.setHeight(620)
    try:
        dialog = window.findChild(QObject, "researchDialog")
        assert dialog is not None
        evaluate(dialog, "open()")
        requests[-1][2](listed())
        wait_opened(qtbot, dialog)
        assert dialog.property("title") == ("Project research" if language == "en" else "Ricerca del progetto")
        evaluate(dialog, 'projectResearch.newRecord(); page = "editor"; syncEditor()')
        evaluate(dialog, 'projectResearch.setField("kind", "decision")')
        title = window.findChild(QObject, "researchTitle")
        title.setProperty("text", "Literal <title> %2")
        body = window.findChild(QObject, "researchBody")
        body.setProperty("text", "<b>Literal source</b>")
        assert evaluate(dialog, "projectResearch.editor.title") == "Literal <title> %2"
        assert window.findChild(QObject, "researchKind").property("currentValue") == "decision"
        assert evaluate(dialog, "projectResearch.editor.body") == "<b>Literal source</b>"
        scroll = window.findChild(QObject, "researchScroll")
        wait_laid_out(qtbot, scroll, title, body)
        assert dialog.property("height") <= 580
        assert scroll.property("contentHeight") > scroll.property("height")
        assert window.findChild(QObject, "researchSave").property("enabled")
        dialog.setProperty("pendingAction", "discard")
        assert evaluate(dialog, "projectResearch.editor.title") == "Literal <title> %2"
        evaluate(window.findChild(QObject, "researchCancelConfirmation"), "clicked()")
        assert dialog.property("pendingAction") == ""
        assert evaluate(dialog, "projectResearch.editor.title") == "Literal <title> %2"
        dialog.setProperty("pendingAction", "discard")
        evaluate(window.findChild(QObject, "researchConfirm"), "clicked()")
        assert evaluate(dialog, "projectResearch.editor.title") == ""
        assert not any("binding loop" in item.message.lower() or "ReferenceError" in item.message
                       or "TypeError" in item.message for item in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_late_save_adopts_original_project_without_hijacking_current_editor(research):
    workspace, controller, requests = research
    controller.setField("title", "Original")
    controller.save()
    old = requests[-1][2]
    workspace._project["id"] = "other"
    workspace.changed.emit()
    controller.setField("title", "Other project draft")
    old({"project_id": "project", "record": record(title="Original")})
    assert controller.editor["title"] == "Other project draft"
    assert not controller.report["pending_count"]
    workspace._project["id"] = "project"
    workspace.changed.emit()
    assert controller.editor["record_id"] == "record-a"
    assert controller.editor["title"] == "Original"
    controller.save()
    assert len(requests) == 1  # A revision needs a reason; this is not a second create.


def test_uncertain_create_blocks_new_mutation_and_retry_keeps_only_user_delta(research):
    _, controller, requests = research
    controller.setField("title", "Submitted")
    controller.setField("assessment", "supported")
    controller.save()
    initial = requests[-1]
    initial[3]({"code": "DISCONNECTED"})
    controller.setField("body", "Edited after disconnect")
    controller.save()
    assert len(requests) == 1
    assert "ricevuta precedente" in controller.error
    controller.retryPending()
    assert requests[-1][1] == initial[1]
    requests[-1][2]({"project_id": "project", "record": record(title="Submitted", body="",
                                                              assessment="needs_recheck")})
    assert controller.editor["record_id"] == "record-a"
    assert controller.editor["body"] == "Edited after disconnect"
    assert controller.editor["assessment"] == "needs_recheck"
    assert controller.report["pending_count"] == 0


def test_reload_failure_preserves_unsaved_record_and_dependency_paths(research):
    workspace, controller, requests = research
    controller.editRecord("record-a")
    requests[-1][2]({"project_id": "project", "record": record()})
    controller.setField("body", "Unsent correction")
    controller.setField("dependency_paths", "src/not-captured-yet.py")
    controller.reloadRecord()
    requests[-1][3]({"code": "DISCONNECTED"})
    assert controller.editor["body"] == "Unsent correction"
    controller.flushDrafts()
    restored = ResearchController(workspace)
    assert restored.editor["dependency_paths"] == "src/not-captured-yet.py"


def test_revision_reads_original_sources_without_replacing_current_draft(research):
    _, controller, requests = research
    controller.editRecord("record-a")
    requests[-1][2]({"project_id": "project", "record": record(revision=3)})
    controller.setField("body", "Unsent correction")
    controller.openHistory()
    assert requests[-1][0] == "research.history"
    requests[-1][2]({"project_id": "project", "revisions": [{"revision": 3}, {"revision": 2}],
                      "has_more": True, "next_cursor": 2})
    controller.nextHistory()
    assert requests[-1][1]["before_revision"] == 2
    requests[-1][2]({"project_id": "project", "revisions": [{"revision": 1}], "has_more": False})
    controller.readRevision(1)
    original = record(body="Original claim", sources=[{"kind": "run", "run_id": "original-run"}])
    requests[-1][2]({"project_id": "project", "record": original})
    assert "Original claim" in controller.revisionText
    assert controller.history["detail"]["sources"] == original["sources"]
    assert controller.editor["body"] == "Unsent correction"
    assert controller.editor["expected_revision"] == 3


def test_source_scope_mismatch_and_invalid_cursor_are_rejected(research):
    _, controller, requests = research
    source = {"kind": "run", "run_id": "source"}
    controller.readSource(source)
    requests[-1][2]({"source": source, "content": "foreign content",
                      "provenance": {"project_id": "other"}})
    assert controller.source["content"] == ""
    assert controller.error
    controller.readSource(source)
    requests[-1][2]({"source": source, "content": "invalid pagination", "offset": 0,
                      "has_more": True, "next_offset": 0})
    assert controller.source["content"] == ""
    assert controller.error


def test_uncertain_configuration_reconciles_without_new_identity(research):
    _, controller, requests = research
    controller.configure(True)
    initial = requests[-1]
    initial[3]({"code": "DISCONNECTED"})
    controller.configure(False)
    assert len(requests) == 1
    controller.retryPending()
    assert requests[-1][1] == initial[1]
    requests[-1][2]({"project_id": "project", "settings": {"enabled": True, "revision": 1}})
    assert controller.report["settings"] == {"enabled": True, "revision": 1}
    assert controller.report["pending_count"] == 0


def test_reading_records_does_not_accumulate_pristine_drafts(research):
    _, controller, requests = research
    for number in range(30):
        record_id = f"record-{number}"
        controller.editRecord(record_id)
        requests[-1][2]({"project_id": "project", "record": record(id=record_id, body="x" * 8000)})
    assert len(controller._state()["drafts"]) == 1
    controller.setField("body", "Unsent correction")
    controller.editRecord("another")
    requests[-1][2]({"project_id": "project", "record": record(id="another")})
    assert len(controller._state()["drafts"]) == 2
    controller.editRecord("record-29")
    requests[-1][2]({"project_id": "project", "record": record(id="record-29", body="remote old text")})
    assert controller.editor["body"] == "Unsent correction"


def test_uncertain_receipt_prevents_discard_replacing_its_draft(research):
    _, controller, requests = research
    controller.setField("title", "Pending")
    controller.save()
    requests[-1][3]({"code": "DISCONNECTED"})
    controller.discardDraft()
    assert controller.editor["title"] == "Pending"
    assert controller.report["pending_count"] == 1


@pytest.mark.parametrize("language", ["it", "en"])
def test_research_and_compact_chat_header_actions_remain_reachable(research, qtbot, qtlog, language):
    """All release/research actions remain usable without sacrificing chat space."""
    workspace, _, requests = research
    workspace.setUiPreference("language", language)
    workspace.setUiPreference("appearance", {**workspace.uiPreferences["appearance"], "ui_size": 18})
    workspace.setUiPreference("details_visible", False)
    selection = (workspace.sessionId, workspace.backend, workspace.selectedModel, workspace.selectedEffort)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]

    def item(name):
        found = window.findChild(QObject, name)
        assert found is not None, name
        return found

    def bounds(control):
        return control.mapRectToScene(QRectF(0, 0, control.width(), control.height()))

    def click(control):
        assert control.isVisible() and control.isEnabled()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                         pos=control.mapToScene(control.boundingRect().center()).toPoint())

    try:
        item("prompt").setProperty("text", "Unsent literal draft <sample>")
        assert workspace.draftText == "Unsent literal draft <sample>"
        controls = [item(name) for name in (
            "projectResearchButton", "resumeWorkspaceButton", "quickActionsButton",
            "projectExportButton", "detailsToggle", "headerNewProjectButton")]
        for width, height in ((1380, 900), (880, 620)):
            window.resize(width, height)
            window.requestActivate()
            qtbot.wait(80)
            header_bounds = bounds(item("projectHeader"))
            title_bounds = bounds(item("projectHeaderTitle"))
            assert title_bounds.width() >= 150
            assert header_bounds.right() <= window.width() + 1
            assert bounds(item("sidebar")).right() <= header_bounds.left()
            assert bounds(item("appVersionLabel")).right() <= bounds(item("sidebar")).right()
            for index, control in enumerate(controls):
                rect = bounds(control)
                assert control.isVisible() and control.isEnabled(), control.objectName()
                assert control.width() >= control.property("implicitWidth") - 1
                assert header_bounds.adjusted(-1, -1, 1, 1).contains(rect), control.objectName()
                assert not rect.intersects(title_bounds), control.objectName()
                assert all(not rect.intersects(bounds(other)) for other in controls[index + 1:])
            assert header_bounds.bottom() <= bounds(item("chatColumn")).top()
            empty = item("emptyConversationScroll")
            assert empty.isVisible() and empty.property("clip")
            # Model controls live in the composer: the chip stays inside its card.
            assert bounds(item("composerCard")).contains(bounds(item("modelControlsToggle")))
            assert bounds(empty).bottom() <= bounds(item("composerCard")).top()
            assert bounds(empty).height() > 40
            if width == 880:
                assert header_bounds.height() <= 65
                assert item("conversation").height() > 160
                for control in controls[2:]:
                    assert control.width() == 38
                    assert control.property("toolTipText")
                    assert evaluate(control, "Accessible.name")
                # All explanatory text stays reachable within its own clipped viewport.
                evaluate(empty, "contentY = Math.max(0, contentHeight - height)")
                qtbot.wait(20)
                assert bounds(item("emptyConversationHint")).bottom() <= bounds(empty).bottom() + 1

        requests.clear()
        for button, dialog_name in (
            ("projectResearchButton", "researchDialog"),
            ("resumeWorkspaceButton", "workspaceDialog"),
            ("quickActionsButton", "actionPalette"),
            ("headerNewProjectButton", "newProjectDialog"),
        ):
            click(item(button))
            dialog = item(dialog_name)
            qtbot.waitUntil(lambda: bool(dialog.property("opened")))
            if dialog_name == "researchDialog":
                assert dialog.property("title") == (
                    "Project research" if language == "en" else "Ricerca del progetto")
                request = next(call for call in reversed(requests) if call[0] == "research.list")
                request[2](listed())
            evaluate(dialog, "close()")
            qtbot.waitUntil(lambda: not dialog.property("visible"))

        # A real pointer opens the file chooser; cancel without exporting personal data.
        export = item("projectExportDialog")
        click(item("projectExportButton"))
        qtbot.waitUntil(lambda: bool(export.property("visible")))
        evaluate(export, "close()")
        qtbot.waitUntil(lambda: not export.property("visible"))
        click(item("detailsToggle"))
        drawer = item("detailsDrawer")
        qtbot.waitUntil(lambda: bool(drawer.property("opened")))
        assert workspace.uiPreferences["details_visible"]
        evaluate(drawer, "close()")
        qtbot.waitUntil(lambda: not drawer.property("visible"))

        # Team still exposes research, while chat-only resume/export/details stay hidden.
        window.setProperty("page", 4)
        qtbot.wait(40)
        assert item("projectResearchButton").isVisible()
        assert not item("resumeWorkspaceButton").isVisible()
        click(item("projectResearchButton"))
        dialog = item("researchDialog")
        qtbot.waitUntil(lambda: bool(dialog.property("opened")))
        evaluate(dialog, "close()")
        qtbot.waitUntil(lambda: not dialog.property("visible"))
        window.setProperty("page", 0)
        qtbot.wait(40)
        assert item("prompt").property("text") == workspace.draftText == "Unsent literal draft <sample>"
        assert selection == (workspace.sessionId, workspace.backend,
                             workspace.selectedModel, workspace.selectedEffort)
        assert not any(call[0] in {"run.start", "session.configure", "session.switch", "project.create",
                                  "project.export"} for call in requests)
        assert not any("binding loop" in entry.message.lower() or "ReferenceError" in entry.message
                       or "TypeError" in entry.message for entry in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_pending_proposal_count_tracks_events_without_blocking_the_dialog(research, qtbot):
    workspace, controller, requests = research
    controller.refreshProposals()
    assert requests[-1][:2] == ("research.list", {"project_id": "project", "view": "proposals", "limit": 30})
    count = requests[-1]
    assert not controller.busy
    controller.open()
    assert requests[-1][:2] == ("research.list", {"project_id": "project", "view": "knowledge", "limit": 20})
    count[2](listed(records=[record(accepted=False, origin="assistant"), record(id="b", accepted=False),
                             record(id="c", accepted=True)]))
    assert controller.pendingProposals == 2 and controller.pendingProposalsText == "2"
    requests.clear()
    workspace.client.eventReceived.emit({"type": "tool.completed", "payload": {"name": "kira.help_search"}})
    qtbot.wait(400)
    assert not requests
    workspace.client.eventReceived.emit({"type": "tool.completed", "payload": {"name": "kira.research_propose"}})
    qtbot.wait(400)
    assert [call[:2] for call in requests] == [("research.list", {"project_id": "project", "view": "proposals", "limit": 30})]
    requests[-1][2](listed(records=[]))
    assert controller.pendingProposals == 0
    requests.clear()
    # Claude Code reports Kira's tool with its provider prefix.
    workspace.client.eventReceived.emit({"type": "tool.completed", "payload": {"name": "claude_code.kira.research_propose"}})
    qtbot.wait(400)
    assert [call[:2] for call in requests] == [("research.list", {"project_id": "project", "view": "proposals", "limit": 30})]
    requests[-1][2](listed(records=[record(accepted=False, origin="assistant")], has_more=True))
    assert controller.pendingProposalsText == "1+"
    stale = requests[-1][2]
    workspace._project = {"id": "other", "name": "Other", "root": workspace.project["root"]}
    workspace.changed.emit()
    assert controller.pendingProposals == 0 and controller.pendingProposalsText == ""
    stale(listed(records=[record(accepted=False)]))
    assert controller.pendingProposals == 0
    requests[-1][2]({"project_id": "foreign", "records": [record(accepted=False)]})
    assert controller.pendingProposals == 0


def test_accepting_a_proposal_from_the_list_sends_get_then_save_with_receipt(research):
    _, controller, requests = research
    controller.open()
    requests[-1][2](listed(records=[record(accepted=False, origin="assistant", body="Preview"),
                                    record(id="done", accepted=True)]))
    before = len(requests)
    controller.acceptProposal("done")
    controller.acceptProposal("missing")
    assert len(requests) == before
    controller.acceptProposal("record-a")
    assert requests[-1][:2] == ("research.get", {"project_id": "project", "record_id": "record-a"})
    requests[-1][2]({"project_id": "project", "record": record(accepted=False, origin="assistant",
                                                                body="Full <body> %1", scope="Literal <scope>")})
    method, params = requests[-1][:2]
    assert method == "research.save"
    assert params["record_id"] == "record-a" and params["expected_revision"] == 1 and params["request_id"]
    assert params["reason"] == "Accettata dall’elenco delle proposte"
    assert params["record"]["accepted"] is True
    assert params["record"]["body"] == "Full <body> %1" and params["record"]["scope"] == "Literal <scope>"
    assert set(params["record"]) == set(empty_record())
    assert controller.report["pending_count"] == 1
    requests[-1][2]({"project_id": "project", "record": record(accepted=True, revision=2, origin="assistant"),
                     "operation": {"request_id": params["request_id"], "status": "completed"}})
    assert controller.report["pending_count"] == 0
    assert requests[-1][:2] == ("research.list", {"project_id": "project", "view": "knowledge", "limit": 20})
    assert controller.editor["record_id"] == "new"


def test_header_counts_pending_proposals_and_list_accepts_without_editor(research, qtbot, qtlog):
    workspace, _, requests = research
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(1380)
    window.setHeight(900)

    def item(name):
        found = window.findChild(QObject, name)
        assert found is not None, name
        return found

    def visual(root, name):
        # Repeater delegates are visual children without a QObject parent chain.
        for child in root.childItems():
            if child.objectName() == name:
                return child
            found = visual(child, name)
            if found is not None:
                return found
        return None

    try:
        button = item("projectResearchButton")
        assert button.property("text") == "Ricerca" and not button.property("primary")
        evaluate(window, "projectResearch.refreshProposals()")
        count = next(call for call in reversed(requests) if call[1].get("view") == "proposals")
        count[2](listed(records=[record(accepted=False, origin="assistant"), record(id="b", accepted=False)]))
        qtbot.wait(30)
        assert button.property("text") == "Ricerca" and button.property("badge") == 2
        assert button.property("toolTipText") == "Ricerca del progetto · proposte dell’assistente da esaminare: 2"
        dialog = item("researchDialog")
        evaluate(dialog, "open()")
        listing = next(call for call in reversed(requests) if call[1].get("view") == "knowledge")
        listing[2](listed(records=[record(accepted=False, origin="assistant"), record(id="b", accepted=True)]))
        qtbot.wait(30)
        assert evaluate(dialog, "projectResearch.report.records.length") == 2
        accept = visual(window.contentItem(), "researchAcceptProposal")
        assert accept is not None
        assert accept.isVisible() and accept.isEnabled() and accept.property("text") == "Accetta"
        evaluate(accept, "clicked()")
        assert requests[-1][:2] == ("research.get", {"project_id": "project", "record_id": "record-a"})
        assert dialog.property("page") == "list"
        assert not any("binding loop" in entry.message.lower() or "ReferenceError" in entry.message
                       or "TypeError" in entry.message for entry in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
