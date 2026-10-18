"""Native Qt test isolation: closed fixture objects must not outlive their event loop."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from shiboken6 import isValid

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def test_native_fixture_graph_is_destroyed_before_next_event_processing(qapp, qtbot, tmp_path):
    """Exercise queued worker results and real QML teardown, not just Python references.

    Waiting for QThread alone does not dispatch its queued result. The fixture
    finalizer must dispose the native parent graph so timers and that queued
    result cannot run during a later test's general event processing.
    """
    deliveries = []
    timer_ticks = []
    destroyed = []
    for index in range(4):
        base = tmp_path / str(index)
        view = Workspace(Paths(*(base / name for name in ("config", "data", "state", "ipc"))))
        engine = create_engine(view)
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        qtbot.wait(10)
        timers = view.findChildren(QTimer)
        view._flush_timer.timeout.connect(lambda: timer_ticks.append("closed fixture tick"))
        # No processEvents between wait and native destruction: this leaves a
        # genuine cross-thread result queued, matching the original teardown risk.
        view._background(lambda: "queued result", deliveries.append)
        assert view._jobs[0].wait(5000)
        assert deliveries == []
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        view.destroyed.connect(lambda: destroyed.append("workspace"))
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(engine) and not isValid(view)
        assert all(not isValid(timer) for timer in timers)
        timer_ticks.clear()
        QCoreApplication.processEvents()
        qtbot.wait(60)
        assert deliveries == [] and timer_ticks == []
    assert len(destroyed) == 4
