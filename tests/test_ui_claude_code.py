"""Synthetic GUI checks for the personal-use Claude Code provider selection."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def session(backend="claude_code", model="opus"):
    return {"id": "chat", "project_id": "project", "backend": backend, "model": model,
            "settings": {"reasoning_effort": "high"}}


def catalog(connected=True, count=13):
    return {"available": True, "capabilities": {"streaming": True, "tools": True},
            "models": [{"id": f"model-{index}", "name": f"Model {index}",
                        "supported_reasoning_efforts": ["low", "high"]} for index in range(count)],
            "account": {"connected": connected, "subscription": "Claude Max" if connected else None,
                        "api_provider": "firstParty"}}


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    space = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    space._connected_value = True
    space._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path)}
    space._session = session()
    space._backend, space._model = "claude_code", "opus"
    requests = []

    def request(*args, **kwargs):
        if args[0] == "goal.get":
            args[2]({"goal": None})
        elif args[0] != "assistant.snapshot":
            requests.append(args)
    monkeypatch.setattr(space.client, "request", request)
    space.changed.emit()
    yield space, requests
    space.close()
    space.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def discoveries(requests):
    return [call for call in requests if call[0] == "backend.discover"]


def test_restored_claude_code_chat_discovers_its_catalog_once(workspace):
    space, requests = workspace
    space._adopt_session(session())
    assert [call[1] for call in discoveries(requests)] == [{"backend": "claude_code"}]
    assert str(space.providerText) == "Rilevamento del provider…"
    space._adopt_session(session())
    assert len(discoveries(requests)) == 1
    discoveries(requests)[0][2](catalog())
    assert str(space.providerText) == "13 modelli · login di Claude Code · solo per uso personale"
    assert len(space.models) == 13 and space.efforts == []
    space._adopt_session(session(model="model-3"))
    assert space.efforts == ["low", "high"]
    assert len(discoveries(requests)) == 1

    space._catalogs.pop("claude_code")
    space._adopt_session(session())
    assert len(discoveries(requests)) == 2
    discoveries(requests)[1][2](catalog(connected=False))
    assert str(space.providerText) == ("Claude Code non risulta collegato. Accedi da un terminale con "
                                       "claude e /login, poi aggiorna.")

    requests.clear()
    space._adopt_session(session(backend="ollama", model="llama"))
    assert not discoveries(requests)


def test_models_page_shows_only_the_selected_provider_pane(workspace, qtbot, qtlog):
    space, _ = workspace
    engine = create_engine(space)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        window.setProperty("page", 1)
        qtbot.wait(30)
        pane = window.findChild(QObject, "deepseekWebPane")
        notice = window.findChild(QObject, "claudeCodePersonalNotice")
        button = window.findChild(QObject, "claudeCodeBackendButton")
        assert pane is not None and notice is not None and button is not None
        assert button.property("checked") and notice.isVisible() and not pane.isVisible()
        space._backend = "deepseek_web"
        space.changed.emit()
        qtbot.wait(30)
        assert pane.isVisible() and not notice.isVisible() and not button.property("checked")
        assert not any("binding loop" in entry.message.lower() or "ReferenceError" in entry.message
                       or "TypeError" in entry.message for entry in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
