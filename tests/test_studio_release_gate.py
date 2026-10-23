"""Studio stays hidden from the interface and from models while it is not offered."""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject

from kira.domain import studio as studio_domain
from kira.domain.paths import Paths
from kira.service.studio_agent import attach_studio
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def test_studio_is_not_offered_in_this_release():
    assert studio_domain.STUDIO_AVAILABLE is False


def test_models_never_receive_the_studio_tool_while_it_is_off():
    options = {"client_tools": [{"type": "namespace", "name": "kira", "tools": []}]}
    session = {"id": "chat", "backend": "codex", "settings": {"tools": True}}
    project = {"id": "project", "permission": "project_write"}
    result = attach_studio(None, session, project, "run", asyncio.Event(), "execute", options)
    assert result is options


def test_studio_button_is_hidden_while_it_is_off(qapp, tmp_path):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    engine = create_engine(workspace)
    try:
        window = engine.rootObjects()[0]
        button = window.findChild(QObject, "chatStudioButton")
        assert button is not None and not button.property("visible")
        assert not workspace.studio.available
        workspace.studio.showStudio()
        assert not workspace.studio.paneVisible
    finally:
        engine.deleteLater()
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


async def test_service_does_not_announce_studio_while_it_is_off(tmp_path):
    from kira.service.server import Service

    service = Service(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    capabilities = (await service.dispatch("hello", {}))["capabilities"]
    assert "studio_m1" not in capabilities and "studio_agent_gateway" not in capabilities
