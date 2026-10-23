"""Qt presentation/transport checks with explicitly synthetic service fixtures."""

import asyncio
import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from shiboken6 import isValid
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QPoint, QRectF, Qt, QUrl
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from kira import __version__
from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace, tool_summary
from kira.ui.ipc import LocalClient
from kira.ui.models import TranscriptModel


@pytest.fixture
def workspace(qapp, tmp_path):
    paths = Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime")))
    view = Workspace(paths)
    yield view
    view.close()
    # The application exits its Qt loop before shutdown; pytest continues that
    # loop for later tests. Dispose the native QObject graph before yielding it.
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(view)


@pytest.fixture
def running_ui(workspace, qtbot):
    workspace._project = {"id": "fixture-project", "name": "Fixture project", "root": "/tmp",
                          "permission": "read_only", "memory": {}, "instructions": ""}
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    engine = create_engine(workspace)
    assert engine.rootObjects(), "QML must load successfully"
    window = engine.rootObjects()[0]
    qtbot.wait(100)
    yield window
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def event(seq, kind, payload=None, run_id="fixture-run"):
    return {"schema_version": 1, "run_id": run_id, "seq": seq,
            "type": kind, "timestamp": "2026-01-01T00:00:00Z", "payload": payload or {}}


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def test_plain_qml_text_uses_app_sans_font_even_with_serif_platform_default(workspace, qapp):
    previous = qapp.font()
    qapp.setFont(QFont("Liberation Serif", 10))
    engine = create_engine(workspace)
    try:
        assert qapp.font().family() == "Noto Sans"
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        heading = next(item for item in window.findChildren(QQuickItem)
                       if item.property("text") == "Uno spazio per lavorare.\nUn filo da ritrovare.")
        assert heading.property("font").family() == "Noto Sans"
    finally:
        for window in engine.rootObjects():
            window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.setFont(previous)


def test_transcript_bounded_without_stream_reset(qapp):
    model = TranscriptModel()
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    for index in range(model.MAX_ROWS + 7):
        model.append(kind="assistant", title="Fixture", body=str(index), runId=str(index))
    assert model.rowCount() == model.MAX_ROWS
    assert model.omitted == 7
    assert model.rows[0]["body"] == "7"
    model.update(0, body="a" * (model.MAX_BODY * 2))
    assert len(model.rows[0]["body"]) < model.MAX_BODY + 100
    assert resets == []


def test_replay_deduplicates_orders_and_coalesces(workspace, monkeypatch):
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: requests.append(args))
    workspace._event(event(3, "message.delta", {"content": "second", "item_id": "a"}))
    workspace._event(event(1, "message.user", {"content": "fixture prompt"}))
    workspace._event(event(2, "message.delta", {"content": "first ", "item_id": "a"}))
    workspace._event(event(2, "message.delta", {"content": "DUPLICATE", "item_id": "a"}))
    workspace._flush()
    assert workspace._cursors["fixture-run"] == 3
    assert [row["body"] for row in workspace.transcript.rows] == ["fixture prompt", "first second"]
    assert requests[0][0] == "run.subscribe"
    assert requests[0][1]["after_seq"] == 0


@pytest.mark.parametrize("completion", [{}, {"item_id": None}])
def test_legacy_message_null_id_and_missing_id_share_one_row(workspace, completion):
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    workspace._event(event(1, "message.delta", {"item_id": None, "content": "Ordinary "}))
    workspace._flush()
    workspace._event(event(2, "message.delta", {"content": "legacy tail"}))
    workspace._event(event(3, "message.completed", completion))
    assert len(workspace.transcript.rows) == 1
    row = workspace.transcript.rows[0]
    assert row["body"] == "Ordinary legacy tail"
    assert row["status"] == "completo" and row["toolId"] == ""
    assert workspace._cursors["fixture-run"] == 3 and not workspace._deltas


@pytest.mark.parametrize("item_id", ["None", "ordinary-item"])
def test_named_message_remains_distinct_from_legacy_message(workspace, item_id):
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    workspace._event(event(1, "message.delta", {"item_id": None, "content": "Legacy message"}))
    workspace._event(event(2, "message.completed"))
    workspace._event(event(3, "message.delta", {"item_id": item_id, "content": "Named message"}))
    workspace._event(event(4, "message.completed", {"item_id": item_id}))
    assert [(row["toolId"], row["body"], row["status"]) for row in workspace.transcript.rows] == [
        ("", "Legacy message", "completo"), (item_id, "Named message", "completo")]


@pytest.mark.parametrize("final_text", ["", "Authoritative final text"])
def test_completed_body_replaces_streamed_preview_including_empty_text(workspace, final_text):
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    workspace._event(event(1, "message.delta", {"item_id": "answer", "content": "Preview text"}))
    workspace._event(event(2, "message.completed", {"item_id": "answer", "content": final_text}))
    assert len(workspace.transcript.rows) == 1
    row = workspace.transcript.rows[0]
    assert row["body"] == final_text and row["status"] == "completo"
    assert workspace._cursors["fixture-run"] == 2


def test_terminal_event_never_claims_unconfirmed_tool_success(workspace):
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    workspace._event(event(1, "tool.started", {"tool_id": "tool-1", "name": "run_command",
                                              "arguments": {"argv": ["sleep", "10"]}}))
    workspace._event(event(2, "message.delta", {"content": "incomplete", "item_id": "a"}))
    workspace._event(event(3, "run.state", {"state": "cancelled"}))
    tool, assistant = workspace.transcript.rows
    assert tool["status"] == "esito non confermato"
    assert assistant["status"] == "incompleto"
    assert workspace.activity[0]["state"] == "cancelled"
    assert workspace.activity[0]["label"] == "Annullato"
    assert "RISULTATO" not in tool["body"]


def test_approval_replay_clears_resolved_request_before_terminal_state(workspace):
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "awaiting_input"}}
    workspace._event(event(1, "approval.requested", {"approval_id": "fixture-approval"}))
    assert workspace.approval
    workspace._event(event(2, "backend.notice", {"approval_id": "unrelated", "decision": "accept"}))
    assert workspace.approval
    workspace._event(event(3, "backend.notice", {"approval_id": "fixture-approval", "decision": "accept"}))
    workspace._event(event(4, "run.state", {"state": "running"}))
    assert not workspace.approval


def test_out_of_order_project_snapshots_cannot_replace_last_user_selection(workspace, monkeypatch):
    callbacks = {}
    def capture(method, params, done, failed=None):
        if method == "project.snapshot":
            callbacks[params["project_id"]] = done

    monkeypatch.setattr(workspace.client, "request", capture)
    workspace.openProject("first")
    workspace.openProject("last")
    callbacks["last"]({"project": {"id": "last"}, "runs": []})
    callbacks["first"]({"project": {"id": "first"}, "runs": []})
    assert workspace.project["id"] == "last"


def test_memory_edit_during_run_does_not_resubmit_unchanged_permission(workspace, monkeypatch):
    workspace._project = {"id": "fixture-project", "permission": "read_only"}
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace.updateProject("fixture instructions", "read_only", '{"goal": "fixture goal"}')
    assert "permission" not in requests[0][1]
    assert requests[0][1]["memory"]["goal"] == "fixture goal"


def test_closing_during_startup_cancels_bootstrap_wait(workspace, monkeypatch, qtbot):
    async def pending_bootstrap(_paths):
        await asyncio.Event().wait()

    monkeypatch.setattr("kira.ui.controller.ensure_service", pending_bootstrap)
    workspace.start()
    qtbot.wait(25)
    before = time.monotonic()
    workspace.close()
    assert time.monotonic() - before < 1
    qtbot.waitUntil(lambda: not workspace._jobs)


def test_tool_result_has_readable_output_and_copyable_command(workspace):
    workspace._runs = {"fixture-run": {"id": "fixture-run", "state": "running"}}
    workspace._event(event(1, "tool.started", {"tool_id": "t", "name": "codex.commandExecution",
                                              "arguments": {"command": "python3 check.py"}}))
    workspace._event(event(2, "tool.completed", {"tool_id": "t", "name": "codex.commandExecution",
        "result": {"ok": True, "exitCode": 0, "aggregatedOutput": "fixture check passed",
                   "commandActions": [{"opaque": "diagnostic"}]}}))
    row = workspace.transcript.rows[0]
    assert row["command"] == "python3 check.py"
    assert "fixture check passed" in row["body"]
    assert "Codice di uscita: 0" in row["body"]
    assert "commandActions" not in row["body"]
    assert "commandActions" in row["raw"]
    workspace.copy(row["command"])
    assert QGuiApplication.clipboard().text() == "python3 check.py"


def test_tool_summary_preserves_error_and_stderr():
    summary = tool_summary({"exit_code": 2, "stdout": "part", "stderr": "failure",
                            "error": {"code": "FAILED"}, "truncated": True})
    assert "Codice di uscita: 2" in summary
    assert "stderr:\nfailure" in summary
    assert "FAILED" in summary and "limitato" in summary


def test_session_restores_explicit_model_effort_without_catalog_defaults(workspace):
    workspace._sessions = [{"id": "fixture-session", "backend": "codex", "model": "chosen-model",
                            "settings": {"reasoning_effort": "ultra"}}]
    workspace._catalogs = {"codex": {"models": [{"id": "other-model", "name": "Default"}]}}
    workspace.selectSession("fixture-session")
    assert workspace.selectedModel == "chosen-model"
    assert workspace.selectedEffort == "ultra"
    workspace.setBackend("ollama")
    # Existing chats keep their choice until a confirmed provider-switch RPC.
    assert workspace.sessionId == "fixture-session" and workspace.selectedModel == "chosen-model"
    assert workspace.selectedEffort == "ultra"


def test_metadata_tools_allow_explicit_probe_without_false_verified_claim(workspace):
    workspace._model = "fixture-model"
    workspace._catalogs = {"ollama": {"models": [{"id": "fixture-model", "tools_metadata": True,
                                       "tools_verified": False, "capabilities": {"tools": False}}]}}
    assert workspace.canUseTools
    assert "Strumenti: non disponibile / non verificato" in workspace.capabilitiesText
    workspace.setTools(True)
    assert workspace.toolsEnabled


def test_disconnected_account_explains_login(workspace):
    workspace._backend = "codex"
    workspace._catalogs = {"codex": {"available": True, "account": {"connected": False}}}
    assert "Account non connesso" in workspace.providerText


def test_run_start_retry_keeps_stable_id(workspace, monkeypatch):
    workspace._project = {"id": "fixture-project"}
    workspace._session = {"id": "fixture-session"}
    workspace._model = "fixture-model"
    workspace._connected_value = True
    calls = []

    def request(method, params, done, failed=None, **kwargs):
        calls.append((method, dict(params)))
        if method == "run.start" and len(calls) == 1:
            failed({"code": "DISCONNECTED", "message": "fixture disconnect"})

    monkeypatch.setattr(workspace.client, "request", request)
    workspace.send("fixture task")
    original_id = calls[0][1]["request_id"]
    workspace._send_start()
    assert calls[1][1]["request_id"] == original_id


def test_plain_text_selection_mouse_and_control_c(workspace, running_ui, qtbot):
    workspace.transcript.append(kind="assistant", title="FIXTURE", runId="fixture-run",
                                body="Select this fixture text, including <b>plain markup</b>.")
    qtbot.wait(150)
    text_item = visual_item(running_ui.contentItem(), "messageText_0")
    assert text_item is not None
    assert text_item.property("readOnly") and text_item.property("selectByMouse")
    point = text_item.mapToScene(QPoint(4, 10)).toPoint()
    QTest.mousePress(running_ui, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(running_ui, point + QPoint(140, 0), 80)
    QTest.mouseRelease(running_ui, Qt.MouseButton.LeftButton, pos=point + QPoint(140, 0))
    qtbot.wait(30)
    assert text_item.property("selectedText")
    selected = text_item.property("selectedText")
    QTest.keyClick(running_ui, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QGuiApplication.clipboard().text() == selected
    QMetaObject.invokeMethod(text_item, "selectAll")
    QTest.keyClick(running_ui, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert "<b>plain markup</b>" in QGuiApplication.clipboard().text()


def test_user_scroll_position_survives_new_output(workspace, running_ui, qtbot):
    for index in range(35):
        workspace.transcript.append(kind="assistant", title="FIXTURE", runId=str(index),
                                    body=(f"Fixture {index}: long content\n" * 15))
    qtbot.wait(150)
    conversation = running_ui.findChild(QQuickItem, "conversation")
    assert conversation is not None
    running_ui.setProperty("followOutput", False)
    QMetaObject.invokeMethod(conversation, "positionViewAtBeginning")
    qtbot.wait(100)
    position = conversation.property("contentY")
    workspace.transcript.append(kind="assistant", title="FIXTURE", body="new streamed text")
    workspace.transcriptChanged.emit()
    qtbot.wait(100)
    assert abs(conversation.property("contentY") - position) < 2
    assert not running_ui.property("followOutput")


@pytest.mark.parametrize("width,height", [(880, 620), (1000, 720), (1440, 940)])
def test_supported_sizes_keep_composer_inside_window(running_ui, qtbot, width, height):
    running_ui.resize(width, height)
    qtbot.wait(120)
    prompt = running_ui.findChild(QQuickItem, "prompt")
    send = running_ui.findChild(QQuickItem, "sendButton")
    for item in (prompt, send):
        rect = item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))
        assert rect.left() >= 0 and rect.top() >= 0
        assert rect.right() <= width + 1 and rect.bottom() <= height + 1
        assert rect.width() >= 20 and rect.height() >= 20


def test_large_export_background_copy_and_path_validation(workspace, monkeypatch, qtbot, tmp_path):
    # The top-bar export saves the selected chat (session.export), never the project.
    workspace._project = {"id": "fixture-project"}
    workspace._session = {"id": "fixture-chat", "project_id": "fixture-project"}
    workspace._connected_value = True
    export_dir = workspace.paths.data / "exports"
    export_dir.mkdir(parents=True)
    source = export_dir / "fixture.md"
    source.write_text("# Fixture chat\n\ncomplete export\n")
    destination = tmp_path / "saved.md"
    methods = []

    def reply(path):
        def request(method, params, done, failed=None):
            methods.append((method, params))
            done({"path": str(path)})
        return request

    monkeypatch.setattr(workspace.client, "request", reply(source))
    workspace.exportTranscript(QUrl.fromLocalFile(str(destination)).toString())
    qtbot.waitUntil(lambda: destination.exists())
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert destination.read_text() == source.read_text()
    assert [call for call in methods if call[0].endswith(".export")] == [
        ("session.export", {"project_id": "fixture-project", "session_id": "fixture-chat"})]
    outside = tmp_path / "outside.md"
    outside.write_text("fixture must not be exported")
    monkeypatch.setattr(workspace.client, "request", reply(outside))
    workspace.exportTranscript(QUrl.fromLocalFile(str(destination)).toString())
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert destination.read_text() == source.read_text()


def test_artifact_open_uses_explicit_registered_local_target(workspace, monkeypatch, qtbot, tmp_path):
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("synthetic artifact")
    workspace._project = {"root": str(tmp_path)}
    workspace._artifacts = [{"path": str(artifact)}]
    opened = []
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl",
                        lambda url: opened.append(url.toLocalFile()) or True)
    workspace.openArtifact(str(artifact))
    qtbot.waitUntil(lambda: bool(opened))
    workspace.openArtifact("https://example.invalid/remote")
    assert opened == [str(artifact)]
    assert "non è un artefatto registrato" in workspace.notice


@pytest.mark.parametrize("name,content", [("report.hta", b"<script>synthetic</script>"),
                                          ("notes.txt", b"#!/bin/sh\necho synthetic\n"),
                                          ("launcher.desktop", b"[Desktop Entry]\n")])
def test_artifact_open_refuses_scripts_and_launchers(workspace, monkeypatch, qtbot, tmp_path,
                                                     name, content):
    artifact = tmp_path / name
    artifact.write_bytes(content)
    workspace._project = {"root": str(tmp_path)}
    workspace._artifacts = [{"path": str(artifact)}]
    opened = []
    monkeypatch.setattr("kira.ui.controller.QDesktopServices.openUrl",
                        lambda url: opened.append(url.toLocalFile()) or True)
    workspace.openArtifact(str(artifact))
    qtbot.waitUntil(lambda: not workspace._jobs)
    assert opened == [] and "script ed eseguibili" in workspace.notice


def test_qt_local_transport_split_utf8_and_reconnect(qapp, qtbot, tmp_path):
    server = QLocalServer()
    socket_path = str(tmp_path / "fixture.sock")
    assert server.listen(socket_path)
    peers = []
    buffers = {}

    def accepted():
        peer = server.nextPendingConnection()
        peers.append(peer)
        buffers[peer] = bytearray()

        def read():
            buffers[peer].extend(bytes(peer.readAll()))
            while b"\n" in buffers[peer]:
                line, _, remainder = buffers[peer].partition(b"\n")
                buffers[peer] = bytearray(remainder)
                request = json.loads(line)
                result = {"version": 1, "app_version": __version__} if request["method"] == "hello" else {"value": "caffè ☕"}
                response = json.dumps({"version": 1, "id": request["id"], "result": result},
                                      ensure_ascii=False).encode() + b"\n"
                if request["method"] == "hello":
                    peer.write(response)
                else:
                    split = response.index("è".encode()) + 1
                    peer.write(response[:split])
                    peer.flush()
                    QCoreApplication.processEvents()
                    peer.write(response[split:])

        peer.readyRead.connect(read)

    server.newConnection.connect(accepted)
    client = LocalClient(socket_path)
    events = []
    client.eventReceived.connect(events.append)
    client.connect_service()
    qtbot.waitUntil(lambda: client.compatible)
    responses = []
    client.request("fixture.read", {}, responses.append)
    qtbot.waitUntil(lambda: bool(responses))
    assert responses == [{"value": "caffè ☕"}]
    notification = event(1, "backend.notice", {"message": "Ordinary service event"})
    peers[-1].write(json.dumps({"version": 1, "event": notification}).encode() + b"\n")
    qtbot.waitUntil(lambda: events == [notification])
    peers[-1].abort()
    qtbot.waitUntil(lambda: not client.compatible)
    client._retry.setInterval(20)
    qtbot.waitUntil(lambda: client.compatible)
    assert len(peers) == 2
    # QObject event dispatch and deferred destruction remain available to Qt.
    assert isinstance(QCoreApplication.sendEvent(client, QEvent(QEvent.Type.User)), bool)
    client.close()
    destroyed = []
    client.destroyed.connect(lambda: destroyed.append(True))
    client.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert destroyed == [True]
    server.close()


def test_qt_client_refuses_incompatible_service(qapp, qtbot, tmp_path):
    server = QLocalServer()
    socket_path = str(tmp_path / "incompatible.sock")
    assert server.listen(socket_path)
    peers = []

    def accepted():
        peer = server.nextPendingConnection()
        peers.append(peer)

        def read():
            request = json.loads(bytes(peer.readAll()))
            peer.write(json.dumps({"version": 1, "id": request["id"],
                                   "result": {"version": 1, "app_version": "99.0"}}).encode() + b"\n")

        peer.readyRead.connect(read)

    server.newConnection.connect(accepted)
    client = LocalClient(socket_path)
    client.connect_service()
    qtbot.waitUntil(lambda: client.incompatible)
    assert not client.compatible
    assert not client._retry.isActive()
    assert not client.request("run.start", {}, lambda _: pytest.fail("Mutation must be rejected"))
    client.close()
    server.close()
