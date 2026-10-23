"""Controller regressions: synthetic RPCs, sockets and profiles only, no provider."""

import json
import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui import controller as controller_module
from kira.ui.appearance import Appearance, _MotionPortal
from kira.ui.assistant_controller import AssistantController
from kira.ui.context_controller import ContextController
from kira.ui.controller import Workspace
from kira.ui.external_links import BrowserOpenResult
from kira.ui.ipc import LocalClient
from kira.ui.metrics import context_card
from kira.ui.preferences import read_preferences
from kira.ui.presentation import UiText
from kira.ui.studio_controller import StudioController

UI = Path(controller_module.__file__).parent


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path),
                          "permission": "project_write", "memory": {}}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "ollama",
                          "model": "local-model", "settings": {}}
    workspace._sessions = [workspace._session.copy()]
    workspace._model, workspace._backend = "local-model", "ollama"
    workspace._connected_value = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args))
    workspace.changed.emit()
    yield workspace, calls
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def request(calls, method):
    return next(call for call in reversed(calls) if call[0] == method)


# UP-11: Team and context failures share the translated error summaries.
def test_team_command_rejection_is_explained(view):
    workspace, calls = view
    workspace.teams._command("team.cancel", {"team_id": "team"})
    request(calls, "team.cancel")[3]({"code": "MANAGED_RUN", "message": "Stop the team instead"})
    assert workspace.teams.error.startswith("Questo lavoro appartiene a un Team")
    assert workspace.teams.error.endswith("Stop the team instead")


def test_context_failure_is_explained(view):
    workspace, calls = view
    context = ContextController(workspace)
    context.pin("Keep this constraint")
    request(calls, "session.context.pin")[3]({"code": "BUSY", "message": "Project is busy"})
    assert context.error.startswith("Il servizio sta completando altro lavoro")
    assert not workspace._pending_intents


# UP-17: the capacity note and the context error describe the selected provider.
def test_deepseek_capacity_note_names_kira_compaction():
    policy = {"context_window": 128000, "compact_threshold": 60000, "source": "provider_catalog"}
    deepseek = context_card({"backend": "deepseek_account", "mode": "automatic_kira", "policy": policy})
    direct = context_card({"backend": "openai_chatgpt", "mode": "automatic_maximum", "policy": policy})
    assert deepseek["note"].startswith("Automatico — compattazione Kira. Fonte: Catalogo del provider.")
    assert direct["note"].startswith("Automatico — massimo disponibile. Fonte: Catalogo del provider.")


def test_context_error_without_a_chat_is_provider_neutral(view):
    workspace, _ = view
    workspace._session = {}
    workspace.changed.emit()
    context = ContextController(workspace)
    context.refresh()
    assert context.error == "Apri una chat e connettiti al servizio per consultarne il contesto."


# UP-13 and UP-23: Studio fallbacks are interface text; dead properties are gone.
def test_studio_fallbacks_are_translatable_and_dead_properties_removed(view):
    workspace, calls = view
    studio = StudioController(workspace)
    studio.refresh()
    request(calls, "studio.status")[3]({"code": "STUDIO_DISABLED"})
    assert studio.error == "Operazione Studio non riuscita." and isinstance(studio._error, UiText)
    assert "M1" not in (StudioController.__doc__ or "") + Path(UI / "studio_controller.py").read_text()
    for owner, name in ((StudioController, "textColor"), (AssistantController, "environmentText")):
        assert owner.staticMetaObject.indexOfProperty(name) == -1


# UP-14 and UP-15: a browser that could not open stays reported after the refresh.
def test_deepseek_browser_failure_survives_the_status_refresh(view, monkeypatch):
    workspace, calls = view
    account = workspace.deepseekAccount
    monkeypatch.setattr(workspace.browser, "open", lambda url, opened, **kwargs: opened(
        BrowserOpenResult(False, error="browser_unavailable")))
    account.login()
    waiting = {"connected": False, "login": {"phase": "waiting_browser", "error": ""}}
    request(calls, "backend.account")[2]({**waiting, "authorization_url": "https://example.invalid/login"})
    status = next(call for call in reversed(calls) if call[0] == "backend.account"
                  and call[1] == {"backend": "deepseek_account", "action": "status"})
    status[2](waiting)
    assert account.error == "Impossibile aprire il browser per l'accesso DeepSeek."
    account.cancel()
    cancel = request(calls, "backend.account")
    assert cancel[1] == {"backend": "deepseek_account", "action": "cancel"}
    cancel[2]({"connected": False, "login": {"phase": "cancelled", "error": ""}})
    assert account.error == ""
    assert "[DEEPSEEK WEB]" not in (UI / "deepseek_account.py").read_text(encoding="utf-8")


# UP-20: continuous appearance controls apply live and are saved once they pause.
@pytest.fixture
def appearance(view, monkeypatch):
    workspace, _ = view
    monkeypatch.setattr(_MotionPortal, "start", lambda self: None)
    controller = Appearance(workspace, workspace)
    writes = []
    original = controller_module.write_preferences

    def counted(directory, value):
        writes.append(value["appearance"]["contrast"])
        original(directory, value)

    monkeypatch.setattr(controller_module, "write_preferences", counted)
    return workspace, controller, writes


def test_slider_steps_apply_live_and_persist_once_after_the_pause(appearance, qtbot):
    workspace, controller, writes = appearance
    controller.setOption("contrast", 10)
    assert writes == [10]
    low = controller.colors["muted"]
    for value in range(11, 41):
        controller.setOption("contrast", value)
    assert controller.values["contrast"] == 40 and controller.colors["muted"] != low
    assert writes == [10]
    qtbot.waitUntil(lambda: writes == [10, 40], timeout=2000)
    assert read_preferences(workspace.paths.config)["appearance"]["contrast"] == 40


def test_unsaved_slider_value_is_kept_when_another_preference_is_saved(appearance):
    workspace, controller, writes = appearance
    controller.setOption("contrast", 10)
    controller.setOption("contrast", 70)
    workspace.setUiPreference("density", "compact")
    saved = read_preferences(workspace.paths.config)
    assert saved["density"] == "compact" and saved["appearance"]["contrast"] == 70
    assert controller.values["contrast"] == 70


# UP-21: interface failures do not break the protocol; another version is not retried.
def frames(client, monkeypatch, *messages):
    data = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    monkeypatch.setattr(client.socket, "readAll", lambda: QByteArray(data))


def test_callback_error_does_not_drop_the_connection_or_buffered_frames(qapp, tmp_path, monkeypatch, caplog):
    client = LocalClient(str(tmp_path / "unused.sock"))
    problems, events = [], []
    client.problem.connect(problems.append)
    client.eventReceived.connect(events.append)

    def broken(_result):
        raise ValueError("Synthetic interface bug")

    client._pending["first"] = (broken, None)
    event = {"run_id": "run", "seq": 1, "type": "run.state", "payload": {"state": "running"}}
    frames(client, monkeypatch, {"version": 1, "id": "first", "result": {}}, {"version": 1, "event": event})
    with caplog.at_level(logging.ERROR, logger="kira.ui.ipc"):
        client._read()
    assert events == [event] and problems == []
    assert "Synthetic interface bug" in caplog.text
    client.close()


def test_other_protocol_version_is_refused_without_reconnect_loop(qapp, tmp_path, monkeypatch):
    client = LocalClient(str(tmp_path / "unused.sock"))
    problems = []
    client.problem.connect(problems.append)
    client._retry.start()
    frames(client, monkeypatch, {"version": 2, "id": "hello", "error": {"code": "INCOMPATIBLE_VERSION",
                                                                        "message": "IPC major version mismatch"}})
    client._read()
    assert client.incompatible and not client._retry.isActive()
    assert problems == ["Servizio incompatibile. Attendi la fine dei lavori, arresta il servizio "
                        "e riapri Kira dopo l'aggiornamento."]
    client.close()
