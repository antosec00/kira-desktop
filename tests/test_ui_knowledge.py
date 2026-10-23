"""Synthetic lazy knowledge, tool receipts and Qt layout; no model or program runs."""

import copy
import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.knowledge_controller import KnowledgeController
from test_ui_support import wait_laid_out, wait_opened


def snapshot():
    return {"project_id": "project", "session_id": "chat", "identity": {
        "name": "Kira", "version": "fixture", "backend": "openai_chatgpt", "model": "Chosen %2 <model>",
        "reasoning_effort": "xhigh", "opaque": "DO_NOT_RENDER"},
        "anchor": {"goal": {"ref": "chat:goal", "title": "Saved goal", "excerpt": "Current synthetic goal", "status": "active"},
                   "request": {"ref": "chat:request:run", "title": "Current request", "excerpt": "Current synthetic request", "state": "running"},
                   "notes": {"count": 1, "ref": "chat:notes"}, "generated_memory": {"available": True, "ref": "chat:summary"}},
        "capabilities": [{"id": "knowledge", "label": "Knowledge", "status": "registered", "reason": "Synthetic capability",
                          "registered": True, "allowed": True, "transport": "unknown", "ref": "capability:knowledge",
                          "provider_native": "DO_NOT_RENDER"}],
        "sources": [{"id": "guides", "label": "Guide", "count": 2, "ref": "app:identity", "available": True},
                    {"id": "research", "label": "Research", "count": 0, "ref": "app:context", "sharing_enabled": False, "available": False}],
        "private_native": "DO_NOT_RENDER"}


def source(ref="app:identity", *, canonical=None, offset=0, next_offset=None, **changes):
    return {"project_id": "project", "session_id": "chat", "requested_ref": ref, "ref": canonical or ref,
            "title": "Original <source> %2", "kind": "guide", "revision": "revision-1", "offset": offset,
            "next_offset": next_offset, "truncated": next_offset is not None, "content": "Literal <b>source</b> %1",
            "provenance": {"kind": "guide", "source": "app", "opaque": "DO_NOT_RENDER"}, **changes}


def registered(**changes):
    return {"id": "tool-a", "project_id": "project", "name": "Synthetic <tool>", "description": "Synthetic literal description %1",
            "revision": 2, "current_revision": 2, "enabled": True, "is_current": True, "runnable": True,
            "source_path": "tools/synthetic.py", "source_sha256": "a" * 64, "revision_sha256": "b" * 64,
            "test_state": "passed", "last_test": {"operation_id": "test-op", "state": "completed", "passed": True,
                "cases_passed": 2, "cases_total": 2, "completed_at": "2026-10-16T12:00:00Z"},
            "manifest": {"input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "tests": [],
                         "timeout_seconds": 2, "max_output_bytes": 1024, "opaque": "DO_NOT_RENDER"},
            "source_code": "DO_NOT_RENDER", **changes}


class Replies:
    def __init__(self):
        self.calls = []
        self.pending = []

    def request(self, method, params, done, failed=None, **kwargs):
        self.calls.append((method, copy.deepcopy(params)))
        if method.startswith(("knowledge.", "project.tools.")):
            self.pending.append((method, copy.deepcopy(params), done, failed))
        return True

    def take(self, method):
        row = next(row for row in self.pending if row[0] == method)
        self.pending.remove(row)
        return row

    def reply(self, method, data):
        self.take(method)[2](data)

    def methods(self):
        return [method for method, _ in self.calls if method.startswith(("knowledge.", "project.tools."))]


@pytest.fixture
def knowledge(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path), "permission": "trusted_host"}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "openai_chatgpt", "model": "Chosen %2 <model>",
                          "provider_generation": 2, "settings": {"openai_account_id": "account", "reasoning_effort": "xhigh", "tools": True}}
    workspace._backend, workspace._model, workspace._effort = "openai_chatgpt", "Chosen %2 <model>", "xhigh"
    workspace._tools = workspace._connected_value = True
    workspace._preferences["onboarding_complete"] = True
    replies = Replies()
    monkeypatch.setattr(workspace.client, "request", replies.request)
    workspace.changed.emit()
    controller = KnowledgeController(workspace)
    yield workspace, controller, replies
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def opened(fixture):
    workspace, controller, replies = fixture
    controller.open()
    replies.reply("knowledge.snapshot", snapshot())
    return workspace, controller, replies


def selected(fixture):
    workspace, controller, replies = opened(fixture)
    controller.setView("tools")
    replies.reply("project.tools.list", {"project_id": "project", "items": [registered()], "has_more": False})
    controller.readTool("tool-a", 0)
    replies.reply("project.tools.get", {"project_id": "project", "tool": registered()})
    return workspace, controller, replies


def evaluate(obj, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, code)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def bounds(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def named(window, name):
    item = window.findChild(QObject, name)
    assert item is not None, name
    return item


def click(window, item):
    assert item.isVisible() and item.isEnabled(), item.objectName()
    point = item.mapToScene(item.boundingRect().center()).toPoint()
    assert QRectF(0, 0, window.width(), window.height()).contains(point), item.objectName()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)


def test_open_is_metadata_only_private_fields_are_not_rendered_and_pins_stay_explicit(knowledge):
    workspace, controller, replies = opened(knowledge)
    assert replies.methods() == ["knowledge.snapshot"]
    assert controller.summary["name"] == "Kira"
    assert controller.summary["model"] == "Chosen %2 <model>"
    assert [row["excerpt"] for row in controller.summary["anchors"]] == ["Current synthetic goal", "Current synthetic request"]
    assert controller.summary["notes_count"] == 1 and not controller.source["content"]
    assert controller.items[1]["sharing_label"] == "Non condiviso con il modello"
    assert "DO_NOT_RENDER" not in json.dumps([controller.summary, controller.capabilities, controller.items])
    assert not controller._store.path.exists()
    assert not any(method in {"run.start", "research.configure", "session.context.pin", "project.tools.test", "project.tools.run"}
                   for method, _ in replies.calls)
    assert not workspace.transcript.rows


def test_search_is_debounced_and_unrelated_workspace_updates_never_search(knowledge, qtbot):
    workspace, controller, replies = opened(knowledge)
    for value in ("b", "br", "browser"):
        controller.setQuery(value)
    for _ in range(50):
        workspace.changed.emit()
    qtbot.wait(300)
    assert replies.methods() == ["knowledge.snapshot", "knowledge.search"]
    pending = replies.take("knowledge.search")
    assert pending[1] == {"project_id": "project", "session_id": "chat", "query": "browser", "scope": "all", "limit": 20}
    controller.setQuery("memory")
    qtbot.wait(280)
    assert len(replies.methods()) == 2
    pending[2]({"items": [{"ref": "app:browser", "title": "Stale query result"}], "has_more": False})
    assert not any(row["title"] == "Stale query result" for row in controller.items)
    qtbot.wait(280)
    assert replies.methods()[-1] == "knowledge.search"
    replies.reply("knowledge.search", {"items": [{"ref": "app:context", "title": "Memory guide", "summary": "Literal <summary>"}], "has_more": False})
    assert controller.items[0]["title"] == "Memory guide"
    count = len(replies.methods())
    for _ in range(30):
        workspace.changed.emit()
    qtbot.wait(280)
    assert len(replies.methods()) == count


def test_refresh_preserves_active_search_and_tab_change_resets_scope(knowledge):
    _, controller, replies = opened(knowledge)
    controller.setScope("project")
    controller.setQuery("fixture")
    controller.search()
    replies.reply("knowledge.search", {"items": [], "has_more": False})
    controller.refresh()
    assert replies.pending[-1][0] == "knowledge.search"
    assert replies.pending[-1][1]["query"] == "fixture"
    assert replies.pending[-1][1]["scope"] == "project"
    replies.reply("knowledge.search", {"items": [], "has_more": False})
    controller.setView("tools")
    replies.reply("project.tools.list", {"items": [], "has_more": False})
    controller.setView("knowledge")
    assert controller.scope == "all" and controller.query == ""
    assert replies.pending[-1][0] == "knowledge.snapshot"


def test_reopen_keeps_same_scope_query_source_page_and_selected_tool(knowledge):
    _, controller, replies = opened(knowledge)
    controller.setScope("project")
    controller.setQuery("literal guide")
    controller.search()
    replies.reply("knowledge.search", {"items": [{"ref": "app:identity", "title": "Guide"}], "has_more": False})
    controller.readSource("app:identity")
    replies.reply("knowledge.read", source(next_offset=7))
    controller.nextSourcePage()
    replies.reply("knowledge.read", source(offset=7))
    controller.hide()
    controller.open()
    assert replies.pending[-1][0] == "knowledge.read"
    assert replies.pending[-1][1]["offset"] == 7
    assert controller.query == "literal guide" and controller.scope == "project"
    replies.reply("knowledge.read", source(offset=7))
    controller.back()
    controller.setView("tools")
    replies.reply("project.tools.list", {"items": [registered()], "has_more": False})
    controller.readTool("tool-a", 2)
    replies.reply("project.tools.get", {"tool": registered()})
    controller.hide()
    controller.open()
    assert controller.view == "tools" and controller.selectedTool["id"] == "tool-a"
    assert replies.pending[-1][1]["revision"] == 2
    replies.reply("project.tools.get", {"tool": registered()})
    assert not any(method in {"run.start", "project.tools.run", "project.tools.test"} for method, _ in replies.calls)


def test_reopen_pending_read_does_not_duplicate_and_new_scope_rejects_old_reply(knowledge, qtbot):
    workspace, controller, replies = opened(knowledge)
    controller.setView("tools")
    pending = replies.take("project.tools.list")
    controller.hide()
    count = len(replies.methods())
    qtbot.wait(300)
    assert len(replies.methods()) == count
    controller.open()
    assert len(replies.methods()) == count and controller.busy
    workspace._session["id"] = "other-chat"
    workspace.changed.emit()
    pending[2]({"items": [registered()], "has_more": False})
    assert controller.view == "knowledge" and controller.scope == "all"
    assert not controller.tools and not controller.selectedTool
    assert not controller.busy and not controller._visible
    controller.open()
    assert replies.pending[-1][0] == "knowledge.snapshot"


def test_search_and_canonical_source_pages_preserve_literal_content(knowledge):
    _, controller, replies = opened(knowledge)
    controller.setQuery("guide")
    controller.search()
    replies.reply("knowledge.search", {"items": [{"ref": "app:identity", "title": "Kira"}], "has_more": True, "next_cursor": "next"})
    controller.nextPage()
    assert replies.pending[-1][1]["cursor"] == "next"
    replies.reply("knowledge.search", {"items": [{"ref": "app:context", "title": "Context"}], "has_more": False})
    controller.previousPage()
    assert "cursor" not in replies.pending[-1][1]
    replies.reply("knowledge.search", {"items": [{"ref": "app:identity", "title": "Kira"}], "has_more": False})
    controller.readSource("app:identity")
    canonical = "app:identity@" + "a" * 24
    original = source(canonical=canonical, next_offset=7)
    saved = copy.deepcopy(original)
    replies.reply("knowledge.read", original)
    assert controller.source["content"] == "Literal <b>source</b> %1" and original == saved
    assert "DO_NOT_RENDER" not in json.dumps(controller.source)
    controller.nextSourcePage()
    assert replies.pending[-1][1] == {"project_id": "project", "session_id": "chat", "ref": canonical, "offset": 7, "limit": 12000}
    replies.reply("knowledge.read", source(canonical, offset=7, content="Second page"))
    assert controller.source["has_previous"] and not controller.source["has_more"]
    controller.previousSourcePage()
    assert replies.pending[-1][1]["ref"] == canonical and replies.pending[-1][1]["offset"] == 0
    replies.take("knowledge.read")[3]({"code": "STALE_SOURCE", "message": "Synthetic source changed"})
    assert controller.error == "Synthetic source changed" and controller.source["content"] == "Second page"


@pytest.mark.parametrize("updates", [
    {"project_id": "other"}, {"session_id": "other"}, {"provider_generation": 3},
    {"account_id": "other"}, {"identity": {"backend": "codex", "model": "Other model"}},
])
def test_response_scope_metadata_cannot_change_selected_provider(knowledge, updates):
    workspace, controller, replies = knowledge
    original = copy.deepcopy(workspace._session)
    controller.open()
    replies.reply("knowledge.snapshot", {**snapshot(), **updates})
    assert controller.error and not controller.summary["loaded"]
    assert workspace._session == original and workspace.selectedModel == original["model"]


def test_unknown_transport_never_becomes_confirmation(knowledge):
    _, controller, replies = knowledge
    controller.open()
    value = snapshot()
    value["capabilities"][0].pop("transport")
    replies.reply("knowledge.snapshot", value)
    assert controller.capabilities[0]["transport"] == "Supporto del modello da verificare"


@pytest.mark.parametrize("ref", ["/private/not-a-source", "../../not-a-source", "file:///private/data", "https://example.test/", "chat:h:999999"])
def test_unknown_refs_and_paths_never_become_reads(knowledge, ref):
    _, controller, replies = opened(knowledge)
    controller.readSource(ref)
    controller.readTool(ref, 1)
    assert replies.methods() == ["knowledge.snapshot"]


def test_forged_path_in_source_or_tool_catalog_remains_inert(knowledge):
    _, controller, replies = opened(knowledge)
    controller.setQuery("fixture")
    controller.search()
    replies.reply("knowledge.search", {"items": [{"ref": "../../private", "title": "Literal path"}], "has_more": False})
    assert controller.items[0]["ref"] == ""
    controller.readSource("../../private")
    assert "knowledge.read" not in replies.methods()
    controller.setView("tools")
    replies.reply("project.tools.list", {"items": [registered(id="../private")], "has_more": False})
    assert not controller.tools


@pytest.mark.parametrize("scope", ["project", "chat", "provider", "model", "account", "generation", "offline"])
def test_late_response_cannot_adopt_another_context(knowledge, scope):
    workspace, controller, replies = knowledge
    controller.open()
    pending = replies.take("knowledge.snapshot")
    if scope == "project":
        workspace._project["id"] = "other"
    elif scope == "chat":
        workspace._session["id"] = "other"
    elif scope == "provider":
        workspace._backend = "ollama"
    elif scope == "model":
        workspace._model = "other"
    elif scope == "account":
        workspace._session["settings"]["openai_account_id"] = "other"
    elif scope == "generation":
        workspace._session["provider_generation"] = 3
    else:
        workspace._connected_value = False
    workspace.changed.emit()
    pending[2](snapshot())
    pending[3]({"message": "Stale failure"})
    assert not controller.summary["loaded"] and not controller.items and not controller.error and not controller.busy
    assert replies.methods() == ["knowledge.snapshot"]


def test_tool_versions_and_declared_test_state_are_distinct_from_implementation_content(knowledge):
    _, controller, replies = selected(knowledge)
    assert controller.selectedTool["status_label"] == "Disponibile"
    assert "2/2" in controller.selectedTool["test_detail"]
    assert "DO_NOT_RENDER" not in json.dumps(controller.selectedTool)
    assert json.loads(controller.selectedTool["contract"])["input_schema"] == {"type": "object"}
    controller.showHistory()
    older = registered(revision=1, is_current=False, enabled=False, runnable=False, test_state="untested", last_test=None)
    replies.reply("project.tools.history", {"project_id": "project", "tool_id": "tool-a", "items": [older], "has_more": True, "next_cursor": "older"})
    assert controller.history["items"][0]["status_label"] == "Revisione precedente"
    controller.nextHistoryPage()
    assert replies.pending[-1][1]["cursor"] == "older"
    replies.reply("project.tools.history", {"project_id": "project", "tool_id": "tool-a", "items": [], "has_more": False})
    controller.previousHistoryPage()
    assert "cursor" not in replies.pending[-1][1]
    replies.reply("project.tools.history", {"project_id": "project", "tool_id": "tool-a", "items": [older], "has_more": False})
    controller.readTool("tool-a", 1)
    replies.reply("project.tools.get", {"project_id": "project", "tool": older})
    assert not controller.canConfigure and not controller.selectedTool["test_detail"]


def test_uncertain_revocation_survives_reopen_and_retries_exact_receipt(knowledge):
    workspace, controller, replies = selected(knowledge)
    controller.configureTool(False)
    first = replies.take("project.tools.configure")
    assert first[1]["expected_revision"] == 2 and first[1]["enabled"] is False
    assert controller._store.path.is_file() and controller._store.path.stat().st_mode & 0o777 == 0o600
    controller.configureTool(False)
    assert replies.methods().count("project.tools.configure") == 1
    first[3]({"code": "DISCONNECTED", "message": "Synthetic uncertain reply"})
    assert controller.pendingCount == 1 and controller.selectedTool["enabled"]
    reopened = KnowledgeController(workspace)
    try:
        before = len(replies.methods())
        reopened.open()
        replies.reply("knowledge.snapshot", snapshot())
        assert reopened.pendingCount == 1 and replies.methods()[before:] == ["knowledge.snapshot"]
        reopened.retryPending()
        repeated = replies.take("project.tools.configure")
        assert repeated[1] == first[1]
        repeated[2]({"project_id": "project", "tool": registered(enabled=False, runnable=False)})
        assert reopened.pendingCount == 0
        assert json.loads(reopened._store.path.read_text())["drafts"] == []
    finally:
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_revocation_reply_and_storage_failure_cannot_claim_unconfirmed_changes(knowledge, monkeypatch):
    _, controller, replies = selected(knowledge)
    controller.configureTool(False)
    request = replies.take("project.tools.configure")
    request[2]({"project_id": "foreign", "tool": registered(enabled=False, runnable=False)})
    assert controller.pendingCount == 1 and controller.selectedTool["enabled"] and controller.error
    controller.retryPending()
    retried = replies.take("project.tools.configure")
    assert retried[1] == request[1]
    retried[2]({"project_id": "project", "tool": registered(enabled=False, runnable=False)})
    assert controller.selectedTool["status_label"] == "Disattivato" and not controller.pendingCount
    def fail(*args, **kwargs):
        raise OSError("Synthetic full disk")
    monkeypatch.setattr(controller._store, "save", fail)
    before = len(replies.methods())
    controller.configureTool(True)
    assert len(replies.methods()) == before and not controller.selectedTool["enabled"] and controller.error


@pytest.mark.parametrize("test", [False, True])
def test_tool_request_only_emits_normalized_reference_and_preserves_draft(knowledge, test):
    workspace, controller, replies = selected(knowledge)
    workspace.setDraftText("Synthetic unsent draft")
    emitted = []
    controller.insertRequested.connect(emitted.append)
    before = len(replies.methods())
    controller.insertTool(test)
    assert len(emitted) == 1 and "tool-a" in emitted[0] and "2" in emitted[0]
    assert "<tool>" not in emitted[0] and workspace.draftText == "Synthetic unsent draft"
    assert len(replies.methods()) == before


@pytest.mark.parametrize("language", ["it", "en"])
@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
def test_real_dialog_is_lazy_literal_and_fits_large_fonts(knowledge, qtbot, qtlog, language, size):
    workspace, _, replies = knowledge
    workspace.setUiPreference("language", language)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(*size)
    engine.rootContext().contextProperty("appearance").setOption("ui_size", 18)
    controller = engine.rootContext().contextProperty("knowledge")
    dialog = window.findChild(QObject, "assistantEnvironmentDialog")
    try:
        evaluate(dialog, "open()")
        replies.reply("knowledge.snapshot", snapshot())
        panel = ("knowledgeIdentity", "knowledgeModel", "knowledgeTab", "knowledgeToolsTab", "knowledgeRefresh",
                 "knowledgeQuery", "knowledgeScope", "knowledgeSearch", "knowledgeScroll", "knowledgePrevious", "knowledgeNext")
        footer = ("knowledgeMemory", "knowledgeResearch", "knowledgeClose")
        wait_opened(qtbot, dialog, *(named(window, name) for name in panel + footer))
        assert dialog.property("title") == ("Kira · Conoscenza e strumenti" if language == "it" else "Kira · Knowledge and tools")
        assert "Chosen %2 <model>" in window.findChild(QObject, "knowledgeModel").property("text")
        content = dialog.property("contentItem")
        for name in panel:
            item = window.findChild(QObject, name)
            assert item.isVisible(), name
            assert bounds(content).adjusted(-1, -1, 1, 1).contains(bounds(item)), (name, bounds(item), bounds(content))
            assert QRectF(0, 0, *size).contains(bounds(item)), (name, evaluate(dialog,
                "JSON.stringify({x:x,y:y,width:width,height:height,origin:hostOrigin,"
                "parentSize:[parent.width,parent.height],host:[panelHost.x,panelHost.y,panelHost.width,panelHost.height],"
                "currentOrigin:panelHost.mapToItem(parent,0,0)})"))
        assert window.findChild(QObject, "knowledgeScroll").height() > 80
        for name in footer:
            item = window.findChild(QObject, name)
            assert item.isVisible() and QRectF(0, 0, *size).contains(bounds(item)), name
        assert replies.methods() == ["knowledge.snapshot"]
        controller.readSource("app:identity")
        replies.reply("knowledge.read", source())
        qtbot.wait(30)
        rendered = window.findChild(QObject, "assistantEnvironmentText")
        assert rendered.property("text") == "Literal <b>source</b> %1"
        assert evaluate(rendered, "Number(textFormat)") == 0
        controller.back()
        click(window, window.findChild(QObject, "knowledgeToolsTab"))
        replies.reply("project.tools.list", {"project_id": "project", "items": [registered()], "has_more": False})
        controller.readTool("tool-a", 0)
        replies.reply("project.tools.get", {"project_id": "project", "tool": registered()})
        qtbot.wait(40)
        assert window.findChild(QObject, "knowledgeToolName").property("text") == "Synthetic <tool>"
        dialog.setProperty("showRaw", True)
        assert '"input_schema"' in window.findChild(QObject, "knowledgeToolContract").property("text")
        click(window, window.findChild(QObject, "knowledgeTab"))
        replies.reply("knowledge.snapshot", snapshot())
        assert controller.view == "knowledge" and not controller.selectedTool
        click(window, window.findChild(QObject, "knowledgeToolsTab"))
        replies.reply("project.tools.list", {"project_id": "project", "items": [registered()], "has_more": False})
        controller.readTool("tool-a", 0)
        replies.reply("project.tools.get", {"project_id": "project", "tool": registered()})
        actions = named(window, "knowledgeToolActions")
        scroll = named(window, "knowledgeScroll")
        wait_laid_out(qtbot, actions, scroll)
        # Scroll the detail to its action row before testing actual pointer use.
        position = bounds(actions).top() - bounds(scroll).top() + evaluate(scroll, "contentItem.contentY")
        evaluate(scroll, f"contentItem.contentY = Math.max(0, Math.min({position}, contentItem.contentHeight - height))")
        use_tool = named(window, "knowledgeUseTool")
        wait_laid_out(qtbot, use_tool)
        prompt = window.findChild(QObject, "prompt")
        prompt.setProperty("text", "Keep this draft <literal>")
        prompt.setProperty("cursorPosition", len("Keep this draft <literal>"))
        before = len(replies.calls)
        click(window, use_tool)
        assert "Keep this draft <literal>" in workspace.draftText and "tool-a" in workspace.draftText
        assert "tool-a" in prompt.property("text") and not dialog.property("visible")
        assert not any(method in {"run.start", "queue.add", "project.tools.run", "project.tools.test"} for method, _ in replies.calls[before:])
        assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                       or "TypeError" in record.message or "Unable to assign" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_unavailable_dialog_explains_scope_and_starts_no_requests(knowledge, qtbot):
    workspace, _, replies = knowledge
    workspace._connected_value = False
    workspace.changed.emit()
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    try:
        dialog = window.findChild(QObject, "assistantEnvironmentDialog")
        evaluate(dialog, "open()")
        qtbot.wait(30)
        assert not replies.methods()
        assert not window.findChild(QObject, "knowledgeQuery").isEnabled()
        assert not window.findChild(QObject, "knowledgeToolsTab").isEnabled()
        assert "connettiti al servizio" in window.findChild(QObject, "knowledgeStatus").property("text")
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
