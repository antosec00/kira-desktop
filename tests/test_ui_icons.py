"""Every icon the interface asks for is one the icon set can draw."""

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtQuick import QQuickItem

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace

QML = Path(__file__).parents[1] / "src/kira/ui/qml"


def defined_icons() -> set[str]:
    source = (QML / "KIcon.qml").read_text(encoding="utf-8")
    return set(re.findall(r'^\s*"([a-z0-9-]+)":\s*\[', source, re.M))


def test_literal_icon_names_in_the_interface_are_defined():
    """A name the set does not know draws nothing: the button keeps an empty space."""
    defined = defined_icons()
    assert {"user", "models", "settings", "close"} <= defined
    unknown: dict[str, set[str]] = {}
    for path in sorted(QML.glob("*.qml")):
        text = path.read_text(encoding="utf-8")
        # iconName: "x" and iconName: condition ? "x" : "y" (the icon is what follows ? or :).
        for expression in re.findall(r'\b(?:iconName|compactIcon)\s*:\s*([^;\n}]*)', text):
            names = re.findall(r'(?:^|[?:])\s*"([a-z0-9-]+)"', expression.strip())
            for name in names:
                if name not in defined:
                    unknown.setdefault(name, set()).add(path.name)
    assert not unknown, unknown


def test_icons_shown_by_the_main_window_are_defined(qapp, qtbot, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "p", "name": "Documenti", "root": str(tmp_path / "p"),
                          "permission": "read_only", "memory": {}}
    workspace._projects = [workspace._project]
    workspace._preferences["onboarding_complete"] = True
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: True)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        qtbot.wait(60)
        defined = defined_icons()
        icons = [item for item in window.findChildren(QQuickItem)
                 if item.metaObject().indexOfProperty("strokes") >= 0 and item.property("name")]
        assert len(icons) > 20
        unknown = {item.property("name") for item in icons} - defined
        assert not unknown, unknown
        # The account buttons, in the sidebar and in the top bar, carry their symbol.
        for name in ("kiraAccountNavButton", "topBarKiraAccountButton"):
            button = window.findChild(QQuickItem, name)
            assert button is not None and button.property("iconName") == "user"
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
