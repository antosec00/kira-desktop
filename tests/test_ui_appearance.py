"""Live appearance across real Qt surfaces, using synthetic data and no service."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := item(child, name):
            return found
    return None


def evaluate(obj, expression):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, expression)
    result, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


@pytest.fixture
def appearance_ui(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    view._project = {"id": "synthetic", "name": "Appearance fixture", "root": str(tmp_path),
                     "permission": "read_only"}
    calls = []
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: calls.append(args))
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    appearance = engine.rootContext().contextProperty("appearance")
    qtbot.wait(50)
    yield view, window, appearance, calls
    assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                   for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    view.close()
    engine.deleteLater()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_live_palette_reaches_dialogs_controls_and_literal_output(appearance_ui, qtbot, mode):
    view, window, appearance, calls = appearance_ui
    view.transcript.append(kind="tool", title="Synthetic result", body="<b>literal</b>\nexit 0")
    appearance.setOption("mode", mode)
    appearance.setStyle("accent", "#8046b4")
    evaluate(window, "inputDialog.active = true")
    qtbot.wait(60)
    assert window.color() == QColor(appearance.colors["background"])
    text = item(window.contentItem(), "messageText_0")
    assert text.property("color") == QColor(appearance.colors["codeText"])
    assert text.property("text") == "<b>literal</b>\nexit 0"
    for name in ("inputDialog", "actionDialog", "usageDialog", "workspaceDialog", "diagnosticsDialog"):
        dialog = window.findChild(QObject, name)
        assert dialog is not None
        assert evaluate(dialog, "background.color") in (
            QColor(appearance.colors["surface"]), QColor(appearance.colors["surfaceAlt"])
        )
    toggle = item(window.contentItem(), "modelControlsToggle")
    assert evaluate(toggle, "background.border.color") == QColor(appearance.colors["border"])
    sidebar = item(window.contentItem(), "sidebar")
    version_label = item(window.contentItem(), "appVersionLabel")
    assert version_label.property("contentWidth") <= version_label.width() + 0.5
    assert sidebar.property("color").alphaF() == 1
    appearance.setOption("sidebar_translucent", True)
    qtbot.wait(30)
    assert sidebar.property("color").alphaF() == pytest.approx(0.76, abs=0.01)
    assert not calls


def test_content_and_code_fonts_update_without_changing_originals(appearance_ui, qtbot):
    view, window, appearance, calls = appearance_ui
    source = "**Synthetic answer**\n\n```python\nprint('unchanged')\n```"
    view.transcript.append(kind="assistant", title="KIRA", body=source)
    view.transcript.append(kind="tool", title="Result", body="literal result")
    before = copy.deepcopy(view.transcript.rows)
    appearance.setStyle("content_family", "serif")
    appearance.setStyle("content_style", "italic")
    appearance.setStyle("code_style", "bold")
    appearance.setOption("content_size", 18)
    appearance.setOption("code_size", 16)
    appearance.setOption("ui_size", 15)
    qtbot.wait(70)
    answer = item(window.contentItem(), "messageText_0")
    tool = item(window.contentItem(), "messageText_1")
    prompt = item(window.contentItem(), "prompt")
    for content in (answer, prompt):
        assert content.property("font").family() == appearance.contentFont.family()
        assert content.property("font").pixelSize() == 18
        assert content.property("font").italic()
    assert tool.property("font").pixelSize() == 16
    assert tool.property("font").weight() == appearance.codeFont.weight()
    QMetaObject.invokeMethod(item(window.contentItem(), "messageSource_0"), "clicked")
    qtbot.wait(30)
    assert answer.property("text") == source
    assert answer.property("font").pixelSize() == 16
    QMetaObject.invokeMethod(item(window.contentItem(), "messageCopy_0"), "clicked")
    assert QGuiApplication.clipboard().text() == source
    assert view.transcript.rows == before
    assert not calls


def test_readable_width_and_font_changes_keep_the_reading_anchor(appearance_ui, qtbot):
    view, window, appearance, _ = appearance_ui
    window.resize(1680, 900)
    # The bounded reading column is the default; this checks the full-width choice first.
    appearance.setOption("full_width", True)
    for index in range(32):
        view.transcript.append(kind="assistant", title="KIRA", body=f"Message {index}\n\n" + "Synthetic readable content. " * 45)
    qtbot.wait(100)
    conversation = item(window.contentItem(), "conversation")
    window.setProperty("followOutput", False)
    evaluate(conversation, "positionViewAtIndex(10, ListView.Beginning)")
    qtbot.wait(50)
    evaluate(conversation, "rememberReadingAnchor()")
    anchor = conversation.property("readingAnchorIndex")
    offset = conversation.property("readingAnchorOffset")
    assert anchor >= 0
    assert item(window.contentItem(), f"messageEntry_{anchor}").width() > 900
    appearance.setOption("full_width", False)
    appearance.setOption("content_size", 17)
    qtbot.wait(120)
    entry = item(window.contentItem(), f"messageEntry_{anchor}")
    assert entry.width() == pytest.approx(900)
    assert entry.x() > 0
    assert item(window.contentItem(), "composerCard").width() == pytest.approx(900)
    assert entry.y() - conversation.property("contentY") == pytest.approx(offset, abs=2)
    assert not window.property("followOutput")
    window.resize(880, 620)
    qtbot.wait(70)
    assert 200 < item(window.contentItem(), "composerCard").width() < 900


def test_pointer_preference_does_not_intercept_button_or_text_input(appearance_ui, qtbot):
    view, window, appearance, calls = appearance_ui
    toggle = item(window.contentItem(), "modelControlsToggle")
    point = toggle.mapToScene(toggle.boundingRect().center()).toPoint()
    appearance.setOption("pointer_cursors", True)
    QTest.mouseMove(window, QPointF(1, 1).toPoint())
    qtbot.wait(20)
    QTest.mouseMove(window, point)
    qtbot.waitUntil(lambda: window.cursor().shape() == Qt.CursorShape.PointingHandCursor)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
    assert window.findChild(QObject, "modelPopover").property("visible")
    appearance.setOption("pointer_cursors", False)
    qtbot.wait(30)
    assert window.cursor().shape() == Qt.CursorShape.ArrowCursor
    prompt = item(window.contentItem(), "prompt")
    prompt.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_A)
    assert prompt.property("text") == "a"
    assert not calls


def test_maximum_fonts_keep_sidebar_inside_minimum_window(appearance_ui, qtbot):
    _, window, appearance, _ = appearance_ui
    window.resize(880, 620)
    appearance.setOption("ui_size", 18)
    appearance.setOption("content_size", 20)
    appearance.setOption("code_size", 20)
    qtbot.wait(70)
    sidebar = item(window.contentItem(), "sidebar")
    version_label = item(window.contentItem(), "appVersionLabel")
    assert version_label.property("contentWidth") <= version_label.width() + 0.5
    for name in ("teamNavButton", "actionPaletteButton"):
        control = item(window.contentItem(), name)
        bounds = control.mapRectToItem(sidebar, control.boundingRect())
        assert bounds.left() >= 0
        assert bounds.right() <= sidebar.width()
    assert evaluate(window, "sidebarScroll.contentHeight > sidebarScroll.availableHeight")
