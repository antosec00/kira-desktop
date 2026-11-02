"""Project/chat hierarchy and dialogs driven by real Qt controls and synthetic RPC replies."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def chat(key, project="p", title=None):
    return {"id": key, "project_id": project, "title": title or "Chat " + key.upper(),
            "backend": "codex", "model": "synthetic-model", "settings": {"reasoning_effort": "high"}}


def event(run_id, kind, **payload):
    return {"run_id": run_id, "seq": 1, "type": kind, "payload": payload,
            "timestamp": "2026-10-14T10:00:00+00:00"}


def request(calls, method, **params):
    return next(call for call in reversed(calls) if call[0] == method
                and all(call[1].get(key) == value for key, value in params.items()))


def reply_chat(calls, key, project="p", content=None, state="completed"):
    runs = [] if content is None else [{"id": "run-" + key, "session_id": key, "state": state,
        "prompt": "Check local files", "created_at": "2026-10-14T09:00:00+00:00"}]
    request(calls, "session.snapshot", session_id=key)[2](
        {"session": chat(key, project), "runs": runs, "artifacts": []})
    if content is not None:
        request(calls, "run.subscribe", run_id="run-" + key)[2](
            {"events": [event("run-" + key, "message.user", content=content)], "has_more": False})


@pytest.fixture
def view(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / n for n in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "p", "name": "Documenti", "root": str(tmp_path / "p"),
                          "memory": {"notes": "Usare esempi locali."}}
    workspace._projects = [workspace._project, {"id": "q", "name": "Altro progetto",
                           "root": str(tmp_path / "q"), "memory": {}}]
    workspace._sessions = [chat("a"), chat("b")]
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: calls.append(args))
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
    workspace.selectSession("a")
    reply_chat(calls, "a", content="Messaggio nella chat A")
    qtbot.wait(70)
    yield workspace, window, calls
    assert not any("binding loop" in record.message.lower() for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def walk(root):
    yield root
    for child in root.childItems():
        yield from walk(child)


def named(window, name):
    return next(obj for obj in walk(window.contentItem()) if obj.objectName() == name)


def button(window, text):
    return next(obj for obj in walk(window.contentItem())
                if obj.isVisible() and obj.property("text") == text
                and obj.property("checkable") is not None)


def click(window, obj):
    assert obj.isVisible() and obj.isEnabled()
    center = obj.mapToScene(obj.boundingRect().center())
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(center.x()), round(center.y())))


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def test_tree_selects_one_chat_history_and_separates_other_project(view, qtbot):
    workspace, window, calls = view
    assert named(window, "chatSelect_a").property("text") == "Chat A"
    assert named(window, "chatSelect_b").property("text") == "Chat B"
    assert named(window, "chatSelect_a").property("checked")
    click(window, named(window, "chatSelect_b"))
    reply_chat(calls, "b", content="Messaggio nella chat B")
    qtbot.wait(70)
    assert workspace.sessionId == "b"
    assert [row["body"] for row in workspace.transcript.rows] == ["Messaggio nella chat B"]
    assert named(window, "messageText_0").property("text") == "Messaggio nella chat B"
    click(window, button(window, "▸  Altro progetto"))
    request(calls, "project.snapshot", project_id="q")[2](
        {"project": workspace._projects[1], "sessions": [chat("c", "q")], "runs": [], "artifacts": []})
    reply_chat(calls, "c", project="q", content="Messaggio nel progetto Q")
    qtbot.wait(70)
    assert workspace.project["id"] == "q"
    assert [row["body"] for row in workspace.transcript.rows] == ["Messaggio nel progetto Q"]
    assert named(window, "chatSelect_c").isVisible()
    assert not any(obj.objectName() in {"chatSelect_a", "chatSelect_b"} and obj.isVisible()
                   for obj in walk(window.contentItem()))
    assert not any(c[0] == "run.start" for c in calls)


def test_new_chat_and_switch_restore_drafts_without_sending(view, qtbot):
    workspace, window, calls = view
    named(window, "prompt").setProperty("text", "Bozza della chat A")
    click(window, named(window, "newChatButton"))
    qtbot.wait(30)
    assert workspace.sessionId == "" and not workspace.transcript.rows
    assert named(window, "prompt").property("text") == ""
    assert (workspace.selectedModel, workspace.selectedEffort) == ("synthetic-model", "high")
    named(window, "prompt").setProperty("text", "Bozza della nuova chat")
    click(window, named(window, "chatSelect_a"))
    reply_chat(calls, "a")
    qtbot.wait(40)
    assert named(window, "prompt").property("text") == "Bozza della chat A"
    click(window, named(window, "newChatButton"))
    qtbot.wait(40)
    assert named(window, "prompt").property("text") == "Bozza della nuova chat"
    assert not any(c[0] in {"run.start", "session.create"} for c in calls)


def test_rename_dialog_sends_project_and_session_ownership(view, qtbot):
    workspace, window, calls = view
    click(window, named(window, "chatRename_b"))
    qtbot.wait(50)
    assert evaluate(window, "renameChatDialog.visible")
    named(window, "chatNameInput").setProperty("text", "  Revisione documenti  ")
    click(window, named(window, "saveChatName"))
    rename = request(calls, "session.rename")
    assert rename[1] == {"project_id": "p", "session_id": "b", "title": "Revisione documenti"}
    rename[2]({"session": chat("b", title="Revisione documenti")})
    qtbot.wait(50)
    assert named(window, "chatSelect_b").property("text") == "Revisione documenti"
    assert workspace.sessionId == "a"
    assert not any(c[0] == "run.start" for c in calls)


def test_integrated_usage_human_and_raw_views(view, qtbot):
    workspace, window, calls = view
    workspace._usage = {"source": "codex", "scope": "last_request", "input_tokens": 1200,
                        "output_tokens": 200, "total_tokens": 1400}
    workspace.changed.emit()
    click(window, named(window, "chatUsageButton"))
    qtbot.wait(60)
    assert not named(window, "usageRawText").isVisible()
    assert evaluate(window, "metricsDialog.summary.cards[0].rows[3].value") == "1.400"
    click(window, named(window, "usageRawToggle"))
    qtbot.wait(40)
    assert named(window, "usageRawText").isVisible()
    assert '"total_tokens": 1400' in named(window, "usageRawText").property("text")
    click(window, named(window, "usageRawToggle"))
    assert not evaluate(window, "metricsDialog.rawVisible")


def test_shared_memory_dialog_renders_only_scoped_context_and_switch(view, qtbot):
    workspace, window, calls = view
    click(window, named(window, "sharedMemoryButton"))
    qtbot.wait(50)
    context = request(calls, "project.context")
    assert context[1] == {"project_id": "p", "session_id": "a"}
    context[2]({"content": "Note: usare esempi locali.\nChat B: revisione della guida.", "enabled": True})
    qtbot.wait(50)
    text = named(window, "sharedMemoryText")
    assert text.property("text") == "Note: usare esempi locali.\nChat B: revisione della guida."
    assert "Messaggio nella chat A" not in text.property("text")
    assert "progetto Q" not in text.property("text")
    assert evaluate(text, "Number(textFormat)") == 0
    toggle = named(window, "shareChatsSwitch")
    assert toggle.property("checked")
    click(window, toggle)
    update = request(calls, "project.update")
    assert update[1] == {"project_id": "p", "share_session_summaries": False}
    project = copy.deepcopy(workspace.project)
    project["memory"]["share_session_summaries"] = update[1]["share_session_summaries"]
    update[2]({"project": project})
    request(calls, "project.context")[2]({"enabled": False, "content": ""})
    qtbot.wait(50)
    assert not toggle.property("checked")
    assert "disattivata" in text.property("text")
    assert not any(c[0] == "run.start" for c in calls)


def test_background_running_chat_can_switch_without_cancelling_it(view, qtbot):
    workspace, window, calls = view
    workspace._runs["run-a"]["state"] = "running"
    workspace.changed.emit()
    click(window, named(window, "chatSelect_b"))
    reply_chat(calls, "b", content="Chat B pronta")
    qtbot.wait(50)
    assert workspace.busy and workspace.sessionId == "b"
    assert workspace.activityLabel == "Altra chat in esecuzione"
    assert not named(window, "sendButton").isEnabled()
    assert not any(c[0] in {"run.cancel", "run.start"} for c in calls)


def test_chat_context_menu_pin_unread_branch_copy_archive_and_delete(view, qtbot):
    workspace, window, calls = view
    # By default (without hover or open menu), chat pill spans full width and action buttons are hidden (opacity 0)
    assert named(window, "chatSelect_b").width() == named(window, "chatSelect_b").parentItem().width()
    assert named(window, "chatMenu_a").parentItem().opacity() == 0.0
    assert named(window, "projectMenu_q").parentItem().opacity() == 0.0

    # Open context menu on chat B
    click(window, named(window, "chatMenu_b"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "chatContextMenu.opened")))
    qtbot.waitUntil(lambda: named(window, "chatMenu_b").parentItem().opacity() > 0.9)
    assert named(window, "chatMenuRename").isVisible()
    assert named(window, "chatMenuPin").isVisible()
    assert named(window, "chatMenuUnread").isVisible()
    assert named(window, "chatMenuCopyToggle").isVisible()
    assert named(window, "chatMenuBranch").isVisible()
    assert named(window, "chatMenuExport").isVisible()
    assert named(window, "chatMenuArchive").isVisible()
    assert named(window, "chatMenuDelete").isVisible()

    # Expand Copy submenu and copy title
    assert not named(window, "chatMenuCopyTitle").isVisible()
    click(window, named(window, "chatMenuCopyToggle"))
    qtbot.wait(30)
    assert named(window, "chatMenuCopyTitle").isVisible()
    assert named(window, "chatMenuCopyMarkdown").isVisible()
    assert named(window, "chatMenuCopyId").isVisible()
    assert named(window, "chatMenuCopyPath").isVisible()
    click(window, named(window, "chatMenuCopyTitle"))
    qtbot.wait(40)
    assert not evaluate(window, "chatContextMenu.opened")

    # Pin chat B via context menu -> B moves above A and shows pin badge
    click(window, named(window, "chatMenu_b"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "chatContextMenu.opened")))
    click(window, named(window, "chatMenuPin"))
    pin_req = request(calls, "session.pin", session_id="b")
    assert pin_req[1] == {"project_id": "p", "session_id": "b", "pinned": True}
    pinned_b = chat("b")
    pinned_b["pinned"] = True
    pinned_b["settings"]["chat_pinned"] = True
    pin_req[2]({"session": pinned_b})
    qtbot.wait(40)
    assert [s["id"] for s in workspace.sessions] == ["b", "a"]
    assert named(window, "chatPinBadge_b").isVisible()

    # Mark chat B as unread via context menu -> shows unread dot, then selecting B marks it read
    click(window, named(window, "chatMenu_b"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "chatContextMenu.opened")))
    click(window, named(window, "chatMenuUnread"))
    unread_req = request(calls, "session.unread", session_id="b")
    assert unread_req[1] == {"project_id": "p", "session_id": "b", "unread": True}
    unread_b = copy.deepcopy(pinned_b)
    unread_b["unread"] = True
    unread_b["settings"]["chat_unread"] = True
    unread_req[2]({"session": unread_b})
    qtbot.wait(40)
    assert named(window, "chatUnreadDot_b").isVisible()

    # Selecting unread chat B automatically sends session.unread with unread=False
    click(window, named(window, "chatSelect_b"))
    read_req = request(calls, "session.unread", session_id="b", unread=False)
    assert read_req[1] == {"project_id": "p", "session_id": "b", "unread": False}
    read_b = copy.deepcopy(unread_b)
    read_b["unread"] = False
    read_b["settings"]["chat_unread"] = False
    read_req[2]({"session": read_b})
    reply_chat(calls, "b", content="Messaggio nella chat B")
    qtbot.wait(50)

    # Branch chat A (non-active chat) via context menu -> calls session.fork
    click(window, named(window, "chatMenu_a"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "chatContextMenu.opened")))
    click(window, named(window, "chatMenuBranch"))
    branch_req = request(calls, "session.fork", session_id="a")
    assert branch_req[1]["project_id"] == "p" and branch_req[1]["session_id"] == "a"
    branch_req[2]({"session": chat("a-branch", title="Chat A · Ramo")})
    reply_chat(calls, "a-branch", content="Ramo di A")
    qtbot.wait(50)
    assert workspace.sessionId == "a-branch"

    # Delete chat B via context menu
    click(window, named(window, "chatMenu_b"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "chatContextMenu.opened")))
    click(window, named(window, "chatMenuDelete"))
    del_req = request(calls, "session.delete", session_id="b")
    assert del_req[1] == {"project_id": "p", "session_id": "b"}
    del_req[2]({"deleted": True, "session_id": "b"})
    qtbot.wait(50)
    assert "b" not in [s["id"] for s in workspace.sessions]


def test_chat_filter_menu_and_project_quick_actions(view, qtbot):
    workspace, window, calls = view
    # Archive chat B using inline quick button
    click(window, named(window, "chatArchive_b"))
    arch_req = request(calls, "session.archive", session_id="b")
    assert arch_req[1] == {"project_id": "p", "session_id": "b", "archived": True}
    archived_b = chat("b")
    archived_b["archived"] = 1
    arch_req[2]({"session": archived_b})
    qtbot.wait(40)
    # Default 'all' filter hides archived chats
    assert [s["id"] for s in workspace.sessions] == ["a"]

    # Switch filter to 'archived' via chatFilterButton
    click(window, named(window, "chatFilterButton"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "chatFilterMenu.opened")))
    click(window, named(window, "chatFilter_archived"))
    qtbot.wait(40)
    assert workspace.sessionFilter == "archived"
    assert [s["id"] for s in workspace.sessions] == ["b"]

    # Reset filter to 'all'
    click(window, named(window, "chatFilterButton"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "chatFilterMenu.opened")))
    click(window, named(window, "chatFilter_all"))
    qtbot.wait(40)
    assert workspace.sessionFilter == "all"
    assert [s["id"] for s in workspace.sessions] == ["a"]

    # Open project context menu via projectMenu_p
    click(window, named(window, "projectMenu_p"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "projectContextMenu.opened")))
    assert named(window, "projectMenuNewChat").isVisible()
    assert named(window, "projectMenuPermissions").isVisible()
    assert named(window, "projectMenuOpenFolder").isVisible()
    assert named(window, "projectMenuCopyPath").isVisible()
    click(window, named(window, "projectMenuCopyPath"))
    qtbot.wait(40)
    assert not evaluate(window, "projectContextMenu.opened")


def test_top_application_menu_bar_opens_menus_and_triggers_actions(view, qtbot):
    workspace, window, calls = view
    assert named(window, "appMenuBar").isVisible()
    for menu_name in ("appMenuFile", "appMenuEdit", "appMenuView", "appMenuChat", "appMenuTools", "appMenuHelp"):
        assert named(window, menu_name).isVisible()

    # Open Chat top menu and verify items exist
    click(window, named(window, "appMenuChat"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "appTopMenuPopup.opened")))
    assert named(window, "menuChatNew").isVisible()
    assert named(window, "menuChatPin").isVisible()
    assert named(window, "menuChatUnread").isVisible()
    assert named(window, "menuChatBranch").isVisible()
    assert named(window, "menuChatArchive").isVisible()
    assert named(window, "menuChatDelete").isVisible()

    # Click Pin from top Chat menu -> pins active chat 'a'
    click(window, named(window, "menuChatPin"))
    pin_req = request(calls, "session.pin", session_id="a")
    assert pin_req[1] == {"project_id": "p", "session_id": "a", "pinned": True}

    # Open View top menu and cycle sidebar mode
    click(window, named(window, "appMenuView"))
    qtbot.waitUntil(lambda: bool(evaluate(window, "appTopMenuPopup.opened")))
    initial_mode = workspace.sidebarMode
    click(window, named(window, "menuViewSidebar"))
    qtbot.wait(40)
    assert workspace.sidebarMode != initial_mode


def test_project_chats_pin_unread_and_soft_delete_persistence(tmp_path):
    from kira.domain.contracts import KiraError
    from kira.service.project_context import ProjectChats
    from kira.service.storage import Store, encode

    store = Store(tmp_path / "state.db")
    try:
        store.db.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
            ("p1", "Test", str(tmp_path), "", "workspace-write", "{}", "2026-11-01T10:00:00+00:00", "2026-11-01T10:00:00+00:00"),
        )
        store.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            ("s1", "p1", "codex", "gpt-5", encode({"chat_title": "Prima", "chat_pinned": True}), None, "2026-11-01T10:00:00+00:00"),
        )
        store.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            ("s2", "p1", "codex", "gpt-5", encode({"chat_title": "Seconda", "chat_unread": True}), None, "2026-11-01T11:00:00+00:00"),
        )
        chats = ProjectChats(store)
        listed = chats.list("p1", 0, 200)["sessions"]
        # Pinned s1 appears before newer unpinned s2
        assert [row["id"] for row in listed] == ["s1", "s2"]
        assert listed[0]["pinned"] is True and listed[0]["unread"] is False
        assert listed[1]["pinned"] is False and listed[1]["unread"] is True

        # Soft-delete s1 (as session.delete does)
        store.db.execute(
            "UPDATE sessions SET settings=? WHERE id='s1'",
            (encode({"chat_title": "Prima", "chat_deleted": True}),),
        )
        store.db.execute(
            "INSERT INTO session_workbench(session_id,archived,created_at,updated_at) VALUES ('s1',1,'2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00')",
        )
        assert [row["id"] for row in chats.list("p1", 0, 200, False)["sessions"]] == ["s2"]
        assert [row["id"] for row in chats.list("p1", 0, 200, True)["sessions"]] == []
        with pytest.raises(KiraError):
            chats.owned_session("p1", "s1")
    finally:
        store.db.close()

