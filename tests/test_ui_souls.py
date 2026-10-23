"""Synthetic identity editor and real Qt layout; no model calls or user profiles."""

import copy
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QRectF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.ui.app import create_engine

from kira.domain.paths import Paths
from kira.ui.appearance import Appearance
from kira.ui import app as ui_app
from kira.ui.controller import Workspace
from kira.ui.i18n import Localization
from kira.ui.soul_controller import SoulController
from test_ui_support import wait_opened


def soul(identifier="a", revision=1, **changes):
    return {"id": identifier, "name": "Synthetic <Kira> %2", "core": "Question assumptions when evidence differs.",
            "mode": "suggest", "revision": revision, "archived": False, "preferences": {},
            "preview": {"text": "Exact <capsule> %1", "bytes": 19, "tokens": None}, **changes}


def snapshot(identifier=None, **changes):
    return {"project_id": "project", "session_id": "chat", "soul": soul(identifier) if identifier else None,
            "epoch": 1, "selected": identifier is not None, "disabled": identifier is None, **changes}


class Replies:
    def __init__(self):
        self.calls = []
        self.pending = []

    def request(self, method, params, done, failed=None, **kwargs):
        if method.startswith("soul."):
            self.calls.append((method, copy.deepcopy(params)))
            self.pending.append((method, copy.deepcopy(params), done, failed))
        return True

    def take(self, method):
        row = next(row for row in self.pending if row[0] == method)
        self.pending.remove(row)
        return row

    def reply(self, method, result):
        self.take(method)[2](result)

    def methods(self):
        return [method for method, _ in self.calls]


@pytest.fixture
def editor(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path), "permission": "read_only"}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "ollama", "model": "chosen-model", "settings": {"tools": False}}
    workspace._connected_value = True
    workspace._preferences["onboarding_complete"] = True
    replies = Replies()
    monkeypatch.setattr(workspace.client, "request", replies.request)
    controller = SoulController(workspace)
    yield workspace, controller, replies
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def opened(editor, *items):
    workspace, controller, replies = editor
    controller.open()
    replies.reply("soul.list", {"souls": list(items) or [soul()], "has_more": False})
    replies.reply("soul.snapshot", {"snapshot": snapshot()})
    return workspace, controller, replies


def selected(editor, profile=None):
    workspace, controller, replies = opened(editor, profile or soul())
    controller.select("a")
    replies.reply("soul.get", {"soul": profile or soul()})
    return workspace, controller, replies


def finish_refresh(replies, profile, current=None):
    replies.reply("soul.list", {"souls": [profile]})
    replies.reply("soul.snapshot", {"snapshot": current or snapshot()})
    replies.reply("soul.get", {"soul": profile})


def evaluate(obj, script):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, script)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def test_open_is_metadata_only_and_has_no_inference_or_implicit_binding(editor):
    workspace, controller, replies = opened(editor)
    assert controller.available and controller.canBind
    assert replies.methods() == ["soul.list", "soul.snapshot"]
    assert not controller.selected["id"] and not controller.activeName
    assert not controller._store.path.exists()
    assert workspace.selectedModel == ""


def test_global_editing_works_without_chat_and_read_only_does_not_block_it(editor):
    workspace, controller, replies = editor
    workspace._session = {}
    workspace._project = {}
    workspace.changed.emit()
    controller.open()
    replies.reply("soul.list", {"souls": []})
    assert controller.canEdit and not controller.canBind
    controller.edit("Study", "Use concrete evidence.", "manual")
    controller.save()
    assert replies.calls[-1][0] == "soul.create"
    assert set(replies.calls[-1][1]) == {"request_id", "name", "core", "mode"}
    assert not any(method == "soul.bind" for method in replies.methods())


def test_create_does_not_bind_and_clears_only_committed_draft(editor):
    _, controller, replies = opened(editor)
    controller.newIdentity()
    controller.edit("Study", "Use evidence.", "manual")
    controller.save()
    request = replies.take("soul.create")
    assert controller.pending
    request[2]({"soul": soul("new-id", name="Study", core="Use evidence.", mode="manual")})
    assert not controller.pending and not controller.dirty
    assert controller.selected["id"] == "new-id"
    assert "soul.bind" not in replies.methods()


def test_drafts_and_preference_edits_survive_tabs_chat_change_and_restart(editor):
    workspace, controller, replies = selected(editor)
    controller.edit("My literal %2", "Unsaved <b>core</b>", "manual")
    controller.editPreference("tone", "Unsent tone")
    controller.setImportText('{"unfinished":')
    controller.setView("history")
    replies.reply("soul.history", {"history": []})
    workspace._session = {**workspace._session, "id": "another-chat"}
    workspace.chatChanged.emit()
    assert controller.draft["core"] == "Unsaved <b>core</b>"
    restored = SoulController(workspace)
    restored._selected = soul()
    restored._editing = "a"
    assert restored.draft == controller.draft
    assert restored.preferences[0]["editedValue"] == "Unsent tone"
    assert restored.importText == '{"unfinished":'
    assert restored._store.path.stat().st_mode & 0o077 == 0


def test_unknown_mutation_is_retryable_with_exact_request_id_across_restart(editor):
    workspace, controller, replies = selected(editor)
    controller.edit("Study", "Careful", "suggest")
    controller.save()
    original = replies.take("soul.update")
    original[3]({"code": "TIMEOUT", "message": "Synthetic timeout"})
    assert controller.pending and not controller.canEdit
    controller.edit("Would overwrite", "Discard me", "manual")
    assert controller.draft["name"] == "Study"
    restored = SoulController(workspace)
    restored.retryPending()
    retry = replies.take("soul.update")
    assert retry[1] == original[1]
    retry[2]({"soul": soul(revision=2, name="Study", core="Careful")})
    assert not restored.pending
    assert restored.selected["revision"] == 2


def test_malformed_mutation_response_preserves_unknown_receipt(editor):
    _, controller, replies = selected(editor)
    controller.edit("Study", "Careful", "suggest")
    controller.save()
    replies.reply("soul.update", {"soul": {"id": "other", "revision": 2}})
    assert controller.pending and "Risposta non valida" in controller.error


def test_conflict_preserves_draft_and_requires_explicit_rebase(editor):
    _, controller, replies = selected(editor)
    controller.edit("Edited", "Keep my text", "suggest")
    controller.save()
    replies.take("soul.update")[3]({"code": "CONFLICT", "message": "Synthetic newer revision"})
    assert controller.dirty and not controller.pending
    controller.select("a")
    replies.reply("soul.get", {"soul": soul(revision=3, core="Changed elsewhere")})
    assert controller.staleDraft and not controller.canSave
    controller.save()
    assert len([m for m in replies.methods() if m == "soul.update"]) == 1
    controller.rebaseDraft()
    controller.save()
    assert replies.calls[-1][1]["expected_revision"] == 3
    assert replies.calls[-1][1]["core"] == "Keep my text"


def test_old_chat_response_does_not_replace_current_binding(editor):
    workspace, controller, replies = opened(editor)
    controller.refreshSnapshot()
    old = replies.take("soul.snapshot")
    workspace._session = {**workspace._session, "id": "other-chat"}
    workspace.chatChanged.emit()
    old[2]({"snapshot": snapshot("a")})
    assert not controller.activeName and not controller.snapshot


def test_binding_is_explicit_idle_only_and_does_not_change_model_or_goal(editor):
    workspace, controller, replies = selected(editor)
    workspace.workbench._goal = {"autonomous": {"status": "active"}}
    assert not controller.canBind
    controller.applyToChat()
    assert "soul.bind" not in replies.methods()
    workspace.workbench._goal = {"autonomous": {"status": "paused"}}
    controller.applyToChat()
    params = replies.calls[-1][1]
    assert params["soul_id"] == "a" and params["session_id"] == "chat"
    assert set(params) == {"request_id", "soul_id", "project_id", "session_id"}
    assert workspace._session["model"] == "chosen-model"
    replies.reply("soul.bind", {"snapshot": snapshot("a")})
    assert controller.activeName == "Synthetic <Kira> %2"


def test_preview_has_exact_bytes_unknown_tokens_and_no_extra_model_call(editor):
    _, controller, replies = selected(editor)
    controller.edit("Study", "🙂", "manual")
    assert controller.coreBytes == 4
    controller.previewDraft()
    assert replies.calls[-1][0] == "soul.preview"
    replies.reply("soul.preview", {"preview": {"text": "Exact <text>", "bytes": 12, "tokens": None}})
    assert controller.preview == "Exact <text>"
    assert "12 byte" in controller.previewSize and "token non misurati" in controller.previewSize
    controller.edit("Study", "🙂" * 301, "manual")
    assert not controller.canSave and not controller.preview


def test_memory_source_pagination_is_scoped_and_literal(editor):
    _, controller, replies = selected(editor)
    controller.setView("memories")
    proposal = {"id": "proposal", "source_id": "source", "statement": "Synthetic"}
    replies.reply("soul.proposals", {"proposals": [proposal], "has_more": True, "next_offset": 20})
    replies.reply("soul.memories", {"memories": []})
    controller.readSource("unknown")
    assert "soul.source" not in replies.methods()
    controller.readSource("source")
    assert replies.calls[-1][1] == {"soul_id": "a", "source_id": "source", "project_id": "project", "session_id": "chat"}
    replies.reply("soul.source", {"source": {"id": "source", "content": "<b>literal</b>", "offset": 0, "next_offset": 14}})
    assert controller.source["content"] == "<b>literal</b>"
    controller.nextSourcePage()
    assert replies.calls[-1][1]["offset"] == 14
    replies.reply("soul.source", {"source": {"id": "source", "content": "remainder", "offset": 14, "next_offset": None}})
    controller.closeSource()
    controller.more("proposals")
    assert replies.calls[-1][1]["offset"] == 20
    replies.reply("soul.proposals", {"proposals": [{"id": "second"}], "has_more": False})
    assert len(controller.proposals) == 2


def test_archive_and_rollback_are_revision_checked_and_never_rebind(editor):
    _, controller, replies = selected(editor)
    controller.archive()
    assert replies.calls[-1][1]["expected_revision"] == 1
    archived = soul(revision=2, archived=True)
    replies.reply("soul.archive", {"soul": archived})
    finish_refresh(replies, archived)
    controller.setView("history")
    replies.reply("soul.history", {"history": [soul(revision=1), archived]})
    controller.rollback(1)
    assert replies.calls[-1][0] == "soul.rollback"
    assert replies.calls[-1][1]["expected_revision"] == 2
    assert "soul.bind" not in replies.methods()


def test_preference_write_is_explicit_revision_checked_and_removal_is_null(editor):
    _, controller, replies = selected(editor)
    controller.editPreference("tone", "direct")
    controller.setPreference("tone", "direct")
    assert replies.calls[-1][1]["key"] == "tone"
    assert replies.calls[-1][1]["expected_revision"] == 1
    updated = soul(revision=2, preferences={"tone": {"value": "direct"}})
    replies.reply("soul.preference", {"soul": updated})
    finish_refresh(replies, updated)
    controller.setPreference("tone", "")
    assert replies.calls[-1][1]["value"] is None


def test_import_export_and_duplicate_exclude_memory_payloads(editor, monkeypatch):
    workspace, controller, replies = selected(editor)
    copied = []
    monkeypatch.setattr(workspace, "copy", copied.append)
    profile = {"format": "kira-soul-v1", "name": "Synthetic", "core": "Direct", "mode": "manual", "preferences": {}}
    controller.exportProfile()
    replies.reply("soul.export", {"profile": profile})
    assert json.loads(copied[0]) == profile
    controller.duplicate()
    replies.reply("soul.export", {"profile": profile})
    assert replies.calls[-1][0] == "soul.import"
    assert replies.calls[-1][1]["profile"]["name"] == "Synthetic · 2"
    assert "soul.bind" not in replies.methods()


@pytest.mark.parametrize("language", ["it", "en"])
@pytest.mark.parametrize("size", [(380, 820), (520, 760), (1080, 840)])
def test_real_qml_dialog_is_literal_accessible_and_responsive(editor, qtbot, qtlog, size, language):
    workspace, controller, replies = editor
    workspace.setUiPreference("language", language)
    engine = QQmlApplicationEngine()
    localization = Localization(workspace, engine, engine)
    appearance = Appearance(workspace, engine)
    appearance.setOption("ui_size", 18)
    engine.rootContext().setContextProperty("souls", controller)
    engine.rootContext().setContextProperty("appearance", appearance)
    engine.rootContext().setContextProperty("localization", localization)
    engine.rootContext().setContextProperty("workspace", workspace)
    qml_dir = Path(ui_app.__file__).resolve().parent / "qml"
    engine.loadData(('import QtQuick\nimport QtQuick.Controls\nimport "' + QUrl.fromLocalFile(str(qml_dir)).toString() + '"\n'
                    'ApplicationWindow { visible: true; width: 1080; height: 840; KiraSoulsDialog { id: panel; Component.onCompleted: open() } }').encode(), QUrl.fromLocalFile(str(qml_dir / "SoulTest.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(*size)
    try:
        replies.reply("soul.list", {"souls": [soul()]})
        replies.reply("soul.snapshot", {"snapshot": snapshot()})
        controller.select("a")
        replies.reply("soul.get", {"soul": soul()})
        dialog = window.findChild(QObject, "kiraSoulsDialog")
        assert dialog is not None
        names = (("soulPicker" if size[0] < 800 else "soulList"), "soulIdentityTab", "soulMemoryTab",
                 "soulHistoryTab", "soulEditorScroll", "soulBind", "soulDisable")
        items = [window.findChild(QObject, name) for name in names]
        assert None not in items, names
        wait_opened(qtbot, dialog, *items)
        assert dialog.property("title") == ("Anima di Kira" if language == "it" else "Kira's Soul")
        assert window.findChild(QObject, "soulName").property("text") == "Synthetic <Kira> %2"
        preview = window.findChild(QObject, "soulPreview")
        assert preview.property("text") == "Exact <capsule> %1"
        assert evaluate(preview, "Number(textFormat)") == 0
        for name, item in zip(names, items, strict=True):
            assert item.isVisible(), name
            rect = item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))
            assert QRectF(0, 0, *size).adjusted(-1, -1, 1, 1).contains(rect), (name, rect)
        assert window.findChild(QObject, "soulEditorScroll").height() >= 80
        problems = [record.message for record in qtlog.records if any(term in record.message for term in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"))]
        assert not problems
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("entry", ["chat", "settings"])
def test_actual_application_entrypoint_and_create_flow(editor, qtbot, qtlog, entry):
    workspace, _, replies = editor
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
    controller = engine.rootContext().contextProperty("souls")
    def click(name):
        item = window.findChild(QObject, name)
        assert item is not None and item.isVisible() and item.isEnabled(), name
        point = item.mapToScene(item.boundingRect().center()).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
    try:
        if entry == "settings":
            window.setProperty("page", 3)
            qtbot.wait(30)
            click("settingsWorkCategory")
            qtbot.wait(20)
            click("settingsSoulsButton")
        else:
            qtbot.wait(30)
            click("chatSoulButton")
        replies.reply("soul.list", {"souls": []})
        replies.reply("soul.snapshot", {"snapshot": snapshot()})
        qtbot.wait(30)
        dialog = window.findChild(QObject, "kiraSoulsDialog")
        assert dialog.property("visible") is True
        click("soulNew")
        field = window.findChild(QObject, "soulName")
        evaluate(field, 'text = "Synthetic Studio"; textEdited()')
        core = window.findChild(QObject, "soulCore")
        evaluate(core, 'text = "Use evidence before conclusions."')
        assert controller.draft["core"] == "Use evidence before conclusions."
        # The actual entrypoint opens a side panel; scroll the save action into view.
        scroll = window.findChild(QObject, "soulEditorScroll")
        save = window.findChild(QObject, "soulSave")
        flickable = scroll.property("contentItem")
        position = save.mapToItem(flickable.property("contentItem"), QPointF(0, 0))
        flickable.setProperty("contentY", max(0, position.y() - 20))
        qtbot.wait(30)
        click("soulSave")
        assert replies.calls[-1][0] == "soul.create"
        created = soul("created", name="Synthetic Studio", core="Use evidence before conclusions.")
        replies.reply("soul.create", {"soul": created})
        finish_refresh(replies, created)
        assert controller.selected["id"] == "created"
        assert "soul.bind" not in replies.methods()
        assert not [record.message for record in qtlog.records if any(term in record.message for term in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"))]
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_archived_snapshot_never_looks_like_active_identity(editor):
    _, controller, _ = editor
    controller._snapshot = snapshot("a", disabled=True)
    assert controller.activeName == ""
    controller._snapshot = snapshot("a")
    controller._snapshot["soul"]["archived"] = True
    assert controller.activeName == ""


def test_malformed_draft_storage_is_preserved_and_prevents_mutation(editor):
    workspace, controller, replies = editor
    path = controller._store.path
    path.parent.mkdir(parents=True, mode=0o700)
    original = b'{"version":1,"drafts": "invalid"}'
    path.write_bytes(original)
    path.chmod(0o600)
    restored = SoulController(workspace)
    assert not restored.canEdit and restored.error
    restored.edit("Synthetic", "Core", "manual")
    restored.save()
    assert path.read_bytes() == original
    assert not replies.calls


def test_reviewed_proposal_cannot_be_decided_again(editor):
    _, controller, replies = selected(editor)
    controller._collections["proposals"] = [{"id": "reviewed", "status": "accepted"}, {"id": "pending", "status": "pending"}]
    controller.decide("reviewed", "reject")
    assert "soul.decide" not in replies.methods()
    controller.decide("pending", "accept")
    assert replies.calls[-1][0] == "soul.decide"
    assert replies.calls[-1][1]["expected_revision"] == 1


@pytest.mark.parametrize("helpful", [True, False])
def test_feedback_is_explicit_revision_checked_and_retries_same_receipt(editor, helpful):
    _, controller, replies = selected(editor)
    controller._collections["memories"] = [{"id": "lesson", "statement": "Synthetic lesson"}]
    controller.feedback("unknown", helpful)
    assert "soul.feedback" not in replies.methods()
    controller.feedback("lesson", helpful)
    sent = replies.take("soul.feedback")
    assert sent[1]["helpful"] is helpful and sent[1]["expected_revision"] == 1
    sent[3]({"code": "TIMEOUT", "message": "Synthetic timeout"})
    controller.retryPending()
    assert replies.take("soul.feedback")[1] == sent[1]



def test_new_chat_selection_is_local_durable_and_never_binds_existing_chat(editor):
    workspace, controller, replies = selected(editor)
    workspace._session = {}
    workspace.chatChanged.emit()
    assert controller.canBind and controller.isNewChat
    controller.applyToChat()
    assert controller.newChatSoulId == "a" and controller.hasSelection
    assert controller.activeName == "Synthetic <Kira> %2"
    assert "soul.bind" not in replies.methods()
    restored = SoulController(workspace)
    assert restored.newChatSoulId == "a"
    workspace._session = {"id": "existing", "project_id": "project"}
    workspace.chatChanged.emit()
    assert not restored.newChatSoulId and not restored.activeName
    assert "soul.bind" not in replies.methods()
    workspace._session = {}
    workspace.chatChanged.emit()
    assert restored.newChatSoulId == "a"
    restored.consumeNewChatSelection("project", "wrong-id")
    assert restored.newChatSoulId == "a"
    restored.consumeNewChatSelection("project", "a")
    assert not restored.newChatSoulId and not restored.hasSelection
    assert not SoulController(workspace).newChatSoulId


def test_new_chat_selection_is_per_project_and_can_be_cleared_before_sending(editor):
    workspace, controller, replies = selected(editor)
    workspace._session = {}
    workspace.chatChanged.emit()
    controller.applyToChat()
    workspace._project = {"id": "other-project"}
    workspace.changed.emit()
    assert not controller.newChatSoulId
    workspace._project = {"id": "project"}
    workspace.changed.emit()
    assert controller.newChatSoulId == "a"
    controller.disableForChat()
    assert not controller.newChatSoulId
    assert "soul.bind" not in replies.methods()



def test_fresh_snapshot_reconciles_only_native_generation_and_reference(editor):
    workspace, controller, replies = opened(editor)
    workspace._session.update(provider_generation=1, backend_ref="old-native")
    workspace._sessions = [dict(workspace._session)]
    settings = dict(workspace._session["settings"])
    refreshed = {**workspace._session, "provider_generation": 2, "backend_ref": None}
    controller.refreshSnapshot()
    replies.reply("soul.snapshot", {"snapshot": snapshot("a"), "session": refreshed})
    assert workspace._session["provider_generation"] == 2 and workspace._session["backend_ref"] is None
    assert workspace._sessions[0]["provider_generation"] == 2
    assert workspace._session["model"] == "chosen-model" and workspace._session["settings"] == settings
    controller.refreshSnapshot()
    replies.reply("soul.snapshot", {"snapshot": snapshot("a"), "session": {**refreshed, "provider_generation": 1, "backend_ref": "stale"}})
    assert workspace._session["provider_generation"] == 2
    controller.refreshSnapshot()
    replies.reply("soul.snapshot", {"snapshot": snapshot("a"), "session": {**refreshed, "provider_generation": 3, "model": "different"}})
    assert workspace._session["model"] == "chosen-model" and workspace._session["provider_generation"] == 2


def test_removed_preference_renders_empty_and_saved_identity_needs_edit_before_save(editor):
    _, controller, replies = selected(editor, soul(preferences={"tone": {"value": None}}))
    assert controller.preferences[0]["value"] == "" and controller.preferences[0]["editedValue"] == ""
    assert not controller.canSave
    controller.save()
    assert "soul.update" not in replies.methods()
