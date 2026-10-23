"""Recorded visual states and bounded native animation, without provider work."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickWindow

from kira.domain.paths import Paths
from kira.ui.appearance import Appearance
from kira.ui.controller import Workspace


@pytest.fixture
def component(qapp, tmp_path, qtbot, qtlog):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    engine = QQmlEngine()
    appearance = Appearance(workspace, engine)
    appearance.setOption("motion", "off")
    engine.rootContext().setContextProperty("appearance", appearance)
    window = QQuickWindow()
    window.resize(900, 500)
    objects = []

    def load(name, **properties):
        source = Path(__file__).parents[1] / "src/kira/ui/qml" / (name + ".qml")
        definition = QQmlComponent(engine, QUrl.fromLocalFile(str(source)))
        item = definition.create()
        assert item is not None, [error.toString() for error in definition.errors()]
        item.setParentItem(window.contentItem())
        for key, value in properties.items():
            assert item.setProperty(key, value), key
        objects.append((definition, item))
        return item

    window.show()
    qtbot.wait(20)
    yield load, appearance, window
    window.close()
    for definition, item in objects:
        item.deleteLater()
        definition.deleteLater()
    engine.deleteLater()
    window.deleteLater()
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                   or "TypeError:" in record.message for record in qtlog.records)


def evaluate(item, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(item), item, code)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_indicator_outcomes_have_distinct_static_symbols_and_accessible_labels(component, mode):
    load, appearance, _window = component
    appearance.setOption("mode", mode)
    item = load("StatusIndicator", animate=True)
    expected = {
        "completed": ("success", "✓", "Confermato"),
        "error": ("danger", "!", "Non riuscita"),
        "waiting": ("warning", "·", "In attesa"),
        "paused": ("warning", "Ⅱ", "In pausa"),
        "unconfirmed": ("warning", "?", "Esito non confermato"),
        "queued": ("muted", "…", "In coda"),
        "cancelled": ("muted", "−", "Annullato"),
        "unknown": ("muted", "?", "Esito sconosciuto"),
        "idle": ("muted", "", "Pronto"),
    }
    for state, (color, symbol, label) in expected.items():
        item.setProperty("state", state)
        assert item.property("statusColor").name() == appearance.colors[color]
        assert item.property("statusSymbol") == symbol
        assert evaluate(item, "Accessible.name") == label
        assert not item.property("animating")
        assert item.findChild(QObject, "statusMarker").property("opacity") == 1
    item.setProperty("state", "not-a-recorded-state")
    assert item.property("statusColor").name() == appearance.colors["muted"]
    assert item.property("stateLabel") == "Esito sconosciuto"


def test_running_pulse_never_moves_layout_and_stops_when_not_presented(component, qtbot):
    load, appearance, window = component
    item = load("StatusIndicator", state="running", animate=True)
    marker = item.findChild(QObject, "statusMarker")
    assert item.property("animating")
    geometry = (item.width(), item.height(), marker.width(), marker.height())
    qtbot.wait(300)
    assert 0.42 <= marker.property("opacity") < 0.98
    assert (item.width(), item.height(), marker.width(), marker.height()) == geometry
    for option, value in [("visible", False), ("animate", False)]:
        item.setProperty(option, value)
        assert not item.property("animating")
        assert marker.property("opacity") == 1
        item.setProperty(option, True)
        assert item.property("animating")
    appearance.setOption("motion", "on")
    assert not item.property("animating") and marker.property("opacity") == 1
    appearance.setOption("motion", "off")
    assert item.property("animating")
    window.hide()
    assert not item.property("animating") and marker.property("opacity") == 1
    window.showMinimized()
    qtbot.wait(20)
    assert not item.property("animating")
    window.showNormal()
    qtbot.wait(20)
    assert item.property("animating")
    item.setProperty("state", "completed")
    assert not item.property("animating") and marker.property("opacity") == 1


@pytest.mark.parametrize("name,status_name", [
    ("ToolReceipt", "toolReceiptStatus"),
    ("ToolGroupHeader", "toolGroupStatus"),
    ("ActivityIndicator", "activityStatus"),
])
def test_consumers_require_explicit_motion_and_keep_terminal_evidence_static(component, name, status_name):
    load, _appearance, _window = component
    item = load(name, outcome="running", width=600)
    if name == "ActivityIndicator":
        item.setProperty("label", "Synthetic recorded activity")
        item.setProperty("animate", True)
    status = item.findChild(QObject, status_name)
    assert status is not None and not status.property("animating")
    item.setProperty("motionEnabled", True)
    assert status.property("animating")
    for state in ("completed", "error", "unconfirmed", "waiting", "paused", "cancelled"):
        item.setProperty("outcome", state)
        assert status.property("state") == state and not status.property("animating")


@pytest.mark.parametrize("width,font_size", [(820, 13), (290, 20)])
def test_receipt_status_stays_near_label_and_retains_literal_paths(component, qtbot, width, font_size):
    load, appearance, _window = component
    appearance.setOption("ui_size", font_size)
    item = load("ToolReceipt", outcome="completed", width=width,
                operationLabel="Synthetic file read", target="/synthetic/<literal> & path/readme.md")
    qtbot.wait(30)
    item.setProperty("height", item.property("implicitHeight"))
    title = item.findChild(QObject, "toolReceiptLabel")
    outcome = item.findChild(QObject, "toolReceiptOutcome")
    target = item.findChild(QObject, "toolReceiptTarget")
    assert target.property("text") == "/synthetic/<literal> & path/readme.md"
    for child in (title, outcome, target):
        assert evaluate(child, "Number(textFormat)") == 0
        origin = child.mapToItem(item, QPointF(0, 0))
        assert origin.x() >= 0 and origin.x() + child.width() <= item.width() + 1
    title_origin = title.mapToItem(item, QPointF(0, 0))
    outcome_origin = outcome.mapToItem(item, QPointF(0, 0))
    if width > 600:
        assert outcome_origin.y() == title_origin.y()
        assert 0 <= outcome_origin.x() - title_origin.x() - title.width() <= 16
        assert outcome_origin.x() < width / 2
    else:
        assert outcome_origin.y() >= title_origin.y() + title.height()
