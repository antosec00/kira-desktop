"""Actual Qt Markdown presentation and explicit-click routing, with synthetic data."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, Qt
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


@pytest.fixture
def workspace(qapp, tmp_path):
    view = Workspace(Paths(*(tmp_path / p for p in ("config", "data", "state", "runtime"))))
    view._project = {"id": "synthetic", "name": "Markdown fixture", "root": str(tmp_path)}
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def ui(workspace, qtbot):
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(880, 620)
    qtbot.wait(60)
    yield window
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def document(obj):
    # Keep QtQuick's wrapper and visual owner alive while inspecting the document.
    wrapper = obj.property("textDocument")
    doc = wrapper.textDocument()
    doc._quick_wrapper = wrapper
    doc._visual_owner = obj
    return doc


def test_formatted_message_selection_copy_and_source_at_minimum_size(workspace, ui, qtbot):
    source = "**Controllo superato**\n\n- Primo risultato\n- Secondo risultato\n\n```bash\npython3 tools/workflow.py check\n```"
    workspace.transcript.append(kind="assistant", title="KIRA", body=source)
    qtbot.wait(100)
    text = item(ui.contentItem(), "messageText_0")
    assert text is not None and text.width() > 100
    doc = document(text)
    rendered = doc.toPlainText()
    assert "Controllo superato" in rendered and "**" not in rendered
    assert "```" not in rendered and "python3 tools/workflow.py check" in rendered
    cursor = QTextCursor(doc)
    cursor.setPosition(0)
    cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
    assert cursor.charFormat().fontWeight() >= 600
    assert any(doc.findBlockByNumber(i).textList() is not None for i in range(doc.blockCount()))
    text.forceActiveFocus()
    QMetaObject.invokeMethod(text, "selectAll")
    QTest.keyClick(ui, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    copied = QGuiApplication.clipboard().text()
    assert "Primo risultato" in copied and "python3 tools/workflow.py check" in copied
    assert "**" not in copied and "```" not in copied
    code = "python3 tools/workflow.py check"
    code_cursor = doc.find(code)
    assert not code_cursor.isNull()
    evaluate(text, f"select({code_cursor.selectionStart()}, {code_cursor.selectionEnd()})")
    QTest.keyClick(ui, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QGuiApplication.clipboard().text() == code
    QMetaObject.invokeMethod(item(ui.contentItem(), "messageCopy_0"), "clicked")
    assert QGuiApplication.clipboard().text() == source
    QMetaObject.invokeMethod(item(ui.contentItem(), "messageSource_0"), "clicked")
    qtbot.wait(30)
    assert evaluate(text, "Number(textFormat)") == 0
    assert text.property("text") == source
    QMetaObject.invokeMethod(item(ui.contentItem(), "messageSource_0"), "clicked")
    qtbot.wait(30)
    assert "**" not in document(text).toPlainText()
    assert workspace.transcript.rows[0]["body"] == source


def test_html_images_literal_and_other_roles_unformatted(workspace, ui, qtbot):
    source = '**bold** <b>literal HTML</b> <img src="https://example.invalid/pixel">\n\n![diagram](https://example.invalid/image.png)'
    workspace.transcript.append(kind="assistant", title="KIRA", body=source)
    qtbot.wait(80)
    text = item(ui.contentItem(), "messageText_0")
    plain = document(text).toPlainText()
    assert "<b>literal HTML</b>" in plain and "<img src=" in plain
    assert "\ufffc" not in plain  # QTextDocument image replacement character
    workspace.transcript.clear()
    for role in ("user", "tool", "state"):
        workspace.transcript.append(kind=role, title="Literal", body=source)
    qtbot.wait(80)
    for index in range(3):
        literal = item(ui.contentItem(), f"messageText_{index}")
        assert literal is not None
        assert evaluate(literal, "Number(textFormat)") == 0
        assert literal.property("text") == source


def test_streaming_and_replayed_messages_use_same_renderer(workspace, ui, qtbot):
    workspace._runs = {"fixture": {"id": "fixture", "state": "running"}}
    def event(seq, kind, payload):
        return {"run_id": "fixture", "seq": seq, "type": kind, "payload": payload}
    workspace._event(event(1, "message.delta", {"item_id": "answer", "content": "**Ready"}))
    workspace._flush()
    qtbot.wait(50)
    workspace._event(event(2, "message.delta", {"item_id": "answer", "content": "**\n\n```bash\necho ready\n```"}))
    workspace._event(event(3, "message.completed", {"item_id": "answer"}))
    qtbot.wait(70)
    source = workspace.transcript.rows[0]["body"]
    rendered = document(item(ui.contentItem(), "messageText_0")).toPlainText()
    assert "Ready" in rendered and "**" not in rendered and "echo ready" in rendered
    workspace.transcript.clear()
    workspace.transcript.append(kind="assistant", title="KIRA", body=source)
    qtbot.wait(70)
    assert document(item(ui.contentItem(), "messageText_0")).toPlainText() == rendered


def test_source_toggle_survives_stream_but_not_recycled_rows(workspace, ui, qtbot):
    for index in range(40):
        workspace.transcript.append(kind="assistant", title="KIRA", body=f"**Item {index}**\n\n" + "content\n" * 6)
    qtbot.wait(100)
    last = item(ui.contentItem(), "messageText_39")
    assert last is not None
    QMetaObject.invokeMethod(item(ui.contentItem(), "messageSource_39"), "clicked")
    workspace.transcript.update(39, body="**Updated item**\n\nstream continued")
    qtbot.wait(30)
    assert evaluate(last, "Number(textFormat)") == 0
    assert last.property("text").startswith("**Updated")
    ui.setProperty("followOutput", False)
    conversation = item(ui.contentItem(), "conversation")
    QMetaObject.invokeMethod(conversation, "positionViewAtBeginning")
    qtbot.wait(80)
    QMetaObject.invokeMethod(conversation, "positionViewAtEnd")
    qtbot.wait(80)
    # Any delegate that leaves the reusable pool starts in formatted mode.
    assert evaluate(item(ui.contentItem(), "messageText_39"), "Number(textFormat)") == 1


def test_links_only_open_on_explicit_click(workspace, ui, monkeypatch, qtbot, tmp_path):
    opened = []
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl", lambda url: opened.append(url) or True)
    local = tmp_path / "notes.md"
    local.write_text("Synthetic notes")
    workspace.transcript.append(kind="assistant", title="KIRA", body="[Web](https://example.invalid/help) [Notes](notes.md)")
    qtbot.wait(80)
    assert opened == []
    text = item(ui.contentItem(), "messageText_0")
    evaluate(text, 'linkActivated("https://example.invalid/help")')
    assert opened[-1].toString() == "https://example.invalid/help"
    evaluate(text, 'linkActivated("notes.md")')
    qtbot.waitUntil(lambda: len(opened) == 2)
    assert opened[-1].toLocalFile() == str(local)
    qtbot.waitUntil(lambda: not workspace._jobs)


@pytest.mark.parametrize("target", ["javascript:alert(1)", "data:text/html,test", "ftp://example.invalid/file", "https://name:secret@example.invalid/", "file://remote/share/file", "//remote/file", "../outside.txt", "missing.txt", "launch.desktop", "script.sh", "app.AppImage", "report.hta", "Shortcut.LNK", "site.url", "executable.txt", "binary", "symlink.txt", "."])
def test_message_link_rejects_non_document_targets(workspace, monkeypatch, qtbot, tmp_path, target):
    opened = []
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl", lambda url: opened.append(url) or True)
    for name in ("launch.desktop", "script.sh", "app.AppImage", "report.hta", "Shortcut.LNK", "site.url",
                 "executable.txt"):
        (tmp_path / name).write_text("synthetic")
    (tmp_path / "executable.txt").chmod(0o700)
    (tmp_path / "binary").write_bytes(b"\x7fELFsynthetic")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside synthetic")
    (tmp_path / "symlink.txt").symlink_to(outside)
    workspace.openMessageLink(target)
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert opened == [] and workspace.notice


def test_message_link_project_switch_discards_pending_open(workspace, monkeypatch, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Synthetic")
    pending = []
    opened = []
    monkeypatch.setattr(workspace, "_background", lambda work, done: pending.append((work, done)))
    monkeypatch.setattr(workspace.client, "request", lambda *args: True)
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl", lambda url: opened.append(url) or True)
    workspace.openMessageLink(str(source))
    workspace.openProject("another-project")
    work, done = pending[0]
    done(work())
    assert opened == []
