"""GUI Studio activation and IPC: no hidden model or browser grant."""

import os
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


@pytest.fixture(autouse=True)
def studio_available(monkeypatch):
    # Studio is not offered in this release; these tests keep its dormant code working.
    monkeypatch.setattr("kira.domain.studio.STUDIO_AVAILABLE", True)


def test_studio_off_by_default_and_project_switch_ignores_late_callbacks(qapp, tmp_path,
                                                                          monkeypatch):
    paths = Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    workspace._connected_value = True
    pid = str(uuid4())
    workspace._project = {"id": pid, "name": "Fixture", "root": str(tmp_path)}
    requests = []
    monkeypatch.setattr(workspace.client, "request",
                        lambda *args, **kwargs: requests.append(args))
    try:
        studio = workspace.studio
        assert not studio.enabled and not studio.paneVisible
        studio.showStudio()
        assert studio.paneVisible
        assert requests[-1][0] == "studio.status"
        requests[-1][2]({"enabled": False, "revision": 0, "scene": {}, "history": []})
        assert not studio.enabled
        studio.setEnabled(True)
        assert requests[-1][0] == "studio.enable"
        assert requests[-1][1]["enabled"] is True
        requests[-1][2]({"enabled": True, "revision": 0, "scene": {}, "history": []})
        assert studio.enabled
        studio.createPoster("Synthetic poster caption")
        assert requests[-1][0] == "studio.poster"
        assert requests[-1][1]["expected_revision"] == 0
        late = requests[-1][2]
        workspace._project = {"id": str(uuid4()), "name": "Other", "root": str(tmp_path)}
        workspace.projectOpened.emit()
        assert not studio.enabled and not studio.paneVisible
        late({"enabled": True, "revision": 1, "scene": {}, "history": []})
        assert not studio.enabled and studio.revision == 0
    finally:
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_qml_has_an_off_by_default_studio_pane_without_hiding_browser(qapp, tmp_path, monkeypatch):
    # Compiled catalogs and the QML disk cache stay out of the user's cache.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    workspace = Workspace(Paths(*(tmp_path / part for part in
                                   ("config", "data", "state", "runtime"))))
    engine = create_engine(workspace)
    try:
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        browser = window.findChild(type(window), "browserPane")
        studio = window.findChild(type(window), "studioPane")
        # Depending on the QML object type, locate by the QObject API.
        from PySide6.QtCore import QObject
        browser = browser or window.findChild(QObject, "browserPane")
        studio = studio or window.findChild(QObject, "studioPane")
        assert browser is not None and studio is not None
        assert not workspace.studio.enabled and not workspace.studio.paneVisible
    finally:
        engine.deleteLater()
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)



def test_studio_grant_is_explicit_per_chat_and_stale_callbacks_are_ignored(
        qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / x for x in
                                   ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    project = str(uuid4())
    workspace._project = {"id": project, "name": "Synthetic", "root": str(tmp_path),
                          "permission": "project_write"}
    workspace._session = {"id": str(uuid4()), "project_id": project,
                          "backend": "ollama", "model": "synthetic", "settings": {"tools": True}}
    workspace._backend = "ollama"
    workspace._tools = True
    calls = []
    monkeypatch.setattr(workspace.client, "request",
                        lambda *args, **kwargs: calls.append(args))
    studio = workspace.studio
    try:
        assert not studio.agentEnabled
        studio.showStudio()
        assert calls[-1][0] == "studio.status"
        assert calls[-1][1]["session_id"] == workspace.sessionId
        calls[-1][2]({"enabled": True, "revision": 0, "scene": {},
                     "history": [], "agent_enabled": False, "grant_generation": 0})
        assert studio.agentEligible and not studio.agentEnabled
        studio.setAgentEnabled(True)
        assert calls[-1][0] == "studio.grant"
        assert calls[-1][1]["session_id"] == workspace.sessionId
        callback = calls[-1][2]
        callback({"agent_enabled": True, "grant_generation": 1})
        assert studio.agentEnabled
        studio.setAgentEnabled(False)
        old_chat = calls[-1][2]
        workspace._session = {"id": str(uuid4()), "project_id": project,
                              "backend": "ollama", "model": "synthetic",
                              "settings": {"tools": True}}
        workspace.chatChanged.emit()
        assert not studio.agentEnabled
        old_chat({"agent_enabled": True, "grant_generation": 2})
        assert not studio.agentEnabled
    finally:
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
