#!/usr/bin/env python3
"""Run inside a disposable machine with the .deb actually installed.

Exercises bundled modules and QML from /tmp, as a non-root user, without checkout.
No fixture backend is presented as a real provider.
"""

import asyncio
import faulthandler
import hashlib
import json
import os
import sys
from importlib.util import find_spec
from pathlib import Path

sys.path.insert(0, "/opt/kira-desktop/lib")

import PySide6  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QCoreApplication, QEvent, QEventLoop, QMetaObject, QMimeData, QObject, QPointF, QTimer, Qt, QUrl, qVersion,
)
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QGuiApplication, QImage, QPalette  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from kira.client import Client, ensure_service  # noqa: E402
from kira.domain.paths import Paths  # noqa: E402
from kira.ui.app import create_engine  # noqa: E402
from kira.ui.controller import Workspace  # noqa: E402
from kira.ui.qml_cache import qml_disk_cache  # noqa: E402


async def verify_installed_assistant_bridge():
    """Exercise the actual vendored MCP bootstrap without any external provider."""
    from kira.backends.kira_mcp import KiraMcpRelay
    from kira.domain.assistant import ASSISTANT_TOOL_NAMES, assistant_tool_specs
    from kira.domain.contracts import RunContext
    calls = []

    async def no_effect(*_args):
        raise AssertionError("The installed fixture must not execute project tools")

    async def callback(call_id, name, arguments):
        calls.append((call_id, name, arguments))
        return {"ok": True, "fixture": "installed callback"}

    ctx = RunContext("installed-fixture-run", {"root": str(Path.cwd()), "permission": "read_only"},
                     {"model": "synthetic-no-inference", "settings": {}}, "Synthetic probe", "",
                     no_effect, no_effect, no_effect, asyncio.Event(),
                     client_tools=assistant_tool_specs(), client_tool_call=callback)
    relay = KiraMcpRelay(ctx, set(ASSISTANT_TOOL_NAMES))
    process = None
    try:
        config = await relay.start()
        process = await asyncio.create_subprocess_exec(config["command"], *config["args"],
            cwd="/tmp", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)

        async def rpc(identifier, method, params):
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier,
                "method": method, "params": params}).encode() + b"\n")
            await process.stdin.drain()
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
            assert "error" not in response, response
            return response["result"]

        await rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "installed-fixture", "version": "1"}})
        listing = await rpc(2, "tools/list", {})
        assert {tool["name"] for tool in listing["tools"]} == {name.split(".")[1] for name in ASSISTANT_TOOL_NAMES}
        first = await rpc(3, "tools/call", {"name": "environment", "arguments": {}})
        duplicate = await rpc(3, "tools/call", {"name": "environment", "arguments": {}})
        assert first == duplicate and not first.get("isError") and len(calls) == 1
        assert relay.ready
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0
        return {"private_mcp_bootstrap": True, "four_tools_listed": True,
                "callback_deduplicated": True, "provider_inference": False}
    finally:
        if process and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
        await relay.close()
        assert relay.directory is None or not relay.directory.exists()


async def prepare():
    assert os.getuid() != 0, "Installed app verification must be unprivileged"
    assert not Path("pyproject.toml").exists(), "Run outside the checkout"
    await ensure_service()
    async with Client() as client:
        projects = (await client.request("project.list"))["projects"]
        project = next((p for p in projects if p["name"] == "Installation check"), None)
        if not project:
            root = Path.home() / "kira-install-project"
            root.mkdir(exist_ok=True)
            (root / "preserve-me.txt").write_text("Package removal must preserve this file.\n")
            project = (await client.request("project.create", {
                "name": "Installation check", "root": str(root), "permission": "read_only",
            }))["project"]
            await client.request("project.update", {"project_id": project["id"], "memory": {
                "current_goal": "Verify installed application", "verified_decisions": [],
                "open_questions": ["External providers are intentionally absent in this container"],
                "next_step": "Use a real configured backend on the target desktop",
            }})
        assert hasattr(Workspace, "newChat"), "installed Workspace lacks project chats"
        if hasattr(Workspace, "newChat"):
            chats = (await client.request("session.list", {"project_id": project["id"]}))["sessions"]
            for title in ("Installed chat A", "Installed chat B"):
                if not any(chat.get("title") == title for chat in chats):
                    chat = (await client.request("session.create", {"project_id": project["id"],
                        "backend": "codex", "model": "package-fixture-no-inference", "title": title}))["session"]
                    chats.append(chat)
            assert len(chats) == 2
            if find_spec("kira.domain.assistant") is not None:
                assistant = await client.request("assistant.snapshot", {"project_id": project["id"], "session_id": chats[0]["id"]})
                assert assistant["environment"]["chat"]["id"] == chats[0]["id"] and not assistant["delegations"]
            for chat in chats:
                isolated = await client.request("session.snapshot", {"project_id": project["id"],
                                                                       "session_id": chat["id"]})
                assert isolated["session"]["project_id"] == project["id"] and not isolated["runs"]
            continuity = await client.request("project.context", {"project_id": project["id"],
                                                                   "session_id": chats[0]["id"]})
            assert continuity["enabled"] and not continuity["sources"]
        snapshot = await client.request("project.snapshot", {"project_id": project["id"]})
        backup = await client.request("database.backup")
        exports = await client.request("project.export", {"project_id": project["id"]})
        assert "Verify installed application" in exports.get("text", "")
        providers = {name: await client.request("backend.discover", {"backend": name})
                     for name in ("ollama", "codex", "openai_chatgpt")}
        assert all(not value["available"] for value in providers.values())
        paths = Paths.from_env()
        report = {
            "installed_root": "/opt/kira-desktop", "cwd": str(Path.cwd()), "uid": os.getuid(),
            "project_id": project["id"], "memory_persisted": bool(snapshot["project"]["memory"]),
            "backup_exists": Path(backup["path"]).exists(),
            "socket_uid": paths.socket.stat().st_uid, "socket_mode": oct(paths.socket.stat().st_mode & 0o777),
            "providers_honestly_unavailable": True, "project_file_preserved":
                (Path(project["root"]) / "preserve-me.txt").read_text(),
        }
        assert report["socket_uid"] == os.getuid() and report["socket_mode"] == "0o600"
        if find_spec("kira.domain.assistant") is not None:
            report["kira_assistant"] = {"scoped_environment_snapshot": True,
                                        **await verify_installed_assistant_bridge()}
        return project["id"], report


def verify_subscription_crypto():
    """Exercise installed native crypto wheels on the target Python ABI."""
    import time
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {"sub": "installed-synthetic", "iss": "https://issuer.example.invalid",
              "aud": "synthetic-client", "iat": int(time.time()), "exp": int(time.time()) + 60}
    token = jwt.encode(claims, key, algorithm="RS256")
    verified = jwt.decode(token, key.public_key(), algorithms=["RS256"],
                          audience="synthetic-client", issuer=claims["iss"])
    assert verified == claims
    try:
        jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="other-client")
    except jwt.InvalidAudienceError:
        return {"installed_rs256_signature": True, "wrong_audience_rejected": True}
    raise AssertionError("Installed JWT audience validation failed")


def main():
    project_id, report = asyncio.run(prepare())
    report["direct_subscription_crypto"] = verify_subscription_crypto()
    assert PySide6.__version__ == qVersion() == "6.11.2"
    report.update(qt=qVersion(), pyside6=PySide6.__version__, helper_sha256=
                  hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    cache_root = Path.home() / ".cache"

    def cache_hashes():
        return {str(path.relative_to(cache_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in cache_root.rglob("*.qmlc") if path.is_file()}

    prior_cache = cache_hashes()
    cache_environment = os.environ.get("QML_DISK_CACHE_PATH")
    qml_directory = Path(__import__("kira.ui.app", fromlist=["__file__"]).__file__).parent / "qml"
    with qml_disk_cache(qml_directory, "Kira Desktop") as expected_cache:
        assert expected_cache is not None, "This controlled test supplies no cache override"
    assert os.environ.get("QML_DISK_CACHE_PATH") == cache_environment
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    native = QPalette(app.palette())
    native.setColor(QPalette.ColorRole.Light, QColor("#242c30"))
    native.setColor(QPalette.ColorRole.Midlight, QColor("#20262b"))
    app.setPalette(native)
    workspace = Workspace()
    metatypes = {}
    for name, qt_type, python_type in (
        ("projectWorkspace", "QVariantMap", dict), ("desktopActions", "QVariantList", list),
        ("project", "QVariantMap", dict), ("projects", "QVariantList", list),
        ("models", "QVariantList", list), ("sessions", "QVariantList", list),
        ("efforts", "QVariantList", list), ("settings", "QVariantMap", dict),
    ):
        index = workspace.metaObject().indexOfProperty(name)
        assert index >= 0, name
        prop = workspace.metaObject().property(index)
        assert prop.typeName() == qt_type and prop.hasNotifySignal(), name
        assert prop.isReadable() and not prop.isWritable(), name
        assert type(getattr(workspace, name)) is python_type, name
        metatypes[name] = {"qt_type": qt_type, "notify": True,
                           "python_type": python_type.__name__}
    report["qt_metatypes"] = metatypes
    engine = create_engine(workspace)
    assert len(engine.rootObjects()) == 1
    assert os.environ.get("QML_DISK_CACHE_PATH") == cache_environment
    window = engine.rootObjects()[0]
    opened = False
    finished = False
    markdown_documents = []

    def verify_design():
        assert hasattr(workspace, "modelControlsVisible"), "installed Workspace lacks model controls"
        assert not app.windowIcon().isNull()
        asset = qml_directory.parent / "assets" / "kira-mascot.png"
        menu_icon = Path("/usr/share/pixmaps/kira-desktop.png")
        image = QImage(str(asset))
        assert not image.isNull() and image.hasAlphaChannel()
        assert image.pixelColor(0, 0).alpha() == 0
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == hashlib.sha256(menu_icon.read_bytes()).hexdigest()
        popover = window.findChild(QObject, "modelPopover")
        toggle = window.findChild(QObject, "modelControlsToggle")
        conversation = window.findChild(QObject, "conversation")
        assert popover is not None and toggle is not None and conversation is not None
        selected = (workspace.backend, workspace.selectedModel, workspace.selectedEffort, workspace.sessionId)

        def settled_height():
            # A real display applies the requested window size asynchronously;
            # measure only once the size is applied and the layout stops moving.
            previous = None
            for _ in range(250):
                QTest.qWait(20)
                current = (window.width(), window.height(), conversation.property("height"))
                if current == previous and current[:2] == (1280, 800):
                    return current[2]
                previous = current
            raise AssertionError(f"window layout did not settle: {previous}")

        collapsed = settled_height()
        assert not popover.property("visible")
        QMetaObject.invokeMethod(toggle, "clicked")
        # The options float over the conversation instead of taking its space.
        expanded = settled_height()
        assert popover.property("visible")
        assert abs(collapsed - expanded) < 1, (collapsed, expanded)
        QMetaObject.invokeMethod(toggle, "clicked")
        QTest.qWait(30)
        assert not popover.property("visible")
        assert selected == (workspace.backend, workspace.selectedModel, workspace.selectedEffort, workspace.sessionId)
        saved = json.loads((workspace.paths.config / "ui.json").read_text())
        assert saved.get("model_controls_visible", False) is False
        return {"available": True, "window_icon": True, "transparent_mascot": True,
                "menu_icon_matches": True, "show_hide_persisted": True,
                "popover_keeps_chat_height": True, "model_and_session_unchanged": True}

    def verify_markdown():
        renderer = engine.rootContext().contextProperty("markdownRenderer")
        assert renderer is not None, "installed QML lacks the Markdown renderer"
        source = "**Installed Markdown**\n\n```python\nprint('fixture')\n```\n\n![diagram](https://example.invalid/image.png)"
        workspace.transcript.append(kind="assistant", title="SYNTHETIC PACKAGE CHECK", body=source)
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)
        loop.exec()

        def find_item(root, name):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                found = find_item(child, name)
                if found is not None:
                    return found
            return None

        text = find_item(window.contentItem(), "messageText_0")
        assert text is not None
        wrapper = text.property("textDocument")
        document = wrapper.textDocument()
        markdown_documents.append((wrapper, document))
        plain = document.toPlainText()
        assert "Installed Markdown" in plain and "**" not in plain and "```" not in plain
        assert "print('fixture')" in plain and "\ufffc" not in plain
        cursor = document.find("Installed Markdown")
        assert cursor.charFormat().fontWeight() >= 600
        text.forceActiveFocus()
        QMetaObject.invokeMethod(text, "selectAll")
        QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        copied = app.clipboard().text()
        assert "print('fixture')" in copied and "```" not in copied
        assert workspace.transcript.rows[0]["body"] == source
        return {"available": True, "formatted": True, "rendered_copy": True,
                "source_unchanged": True, "images_inert": True,
                "data_kind": "synthetic presentation only, no model run"}

    def verify_output_preview():
        assert hasattr(workspace.transcript, "setOutputExpanded"), "installed transcript lacks output preview"
        source = "$ python3 check_files.py\n\n" + "\n".join(
            f"{index:03d} file_{index:03d}.txt: <b>literal</b> **verified**"
            for index in range(60)
        )
        row = workspace.transcript.append(
            kind="tool", title="SYNTHETIC PACKAGE OUTPUT CHECK", body=source,
            status="confermato", command="python3 check_files.py",
        )
        QTest.qWait(100)
        # Tool receipts start collapsed; the output and its toggle live inside.
        assert hasattr(workspace.transcript, "setReceiptExpanded"), "installed transcript lacks receipts"
        workspace.transcript.setReceiptExpanded(row, True)
        QTest.qWait(80)

        def find_item(root, name):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                found = find_item(child, name)
                if found is not None:
                    return found
            return None

        def settled(condition):
            # A slow runner lays out after a change later than a fixed pause.
            for _ in range(150):
                QTest.qWait(20)
                if condition():
                    return True
            return condition()

        assert settled(lambda: all(find_item(window.contentItem(), f"{name}_{row}") is not None
                                   for name in ("messageText", "outputToggle", "messageCopy")))
        text = find_item(window.contentItem(), f"messageText_{row}")
        toggle = find_item(window.contentItem(), f"outputToggle_{row}")
        copy = find_item(window.contentItem(), f"messageCopy_{row}")
        assert settled(lambda: toggle.isVisible() and 0 < text.height() <= 144)
        assert toggle.property("text") == "Mostra tutto"
        assert text.property("text") == workspace.transcript.rows[row]["previewBody"]
        assert text.property("text") != source
        QMetaObject.invokeMethod(copy, "clicked")
        assert app.clipboard().text() == source
        QMetaObject.invokeMethod(toggle, "clicked")
        assert settled(lambda: toggle.property("text") == "Riduci" and text.height() > 144)
        assert workspace.transcript.rows[row]["outputExpanded"] is True
        assert text.property("text") == source
        expression = QQmlExpression(QQmlEngine.contextForObject(text), text, "Number(textFormat)")
        value, _ = expression.evaluate()
        assert not expression.hasError() and value == 0
        assert not window.property("followOutput")
        QMetaObject.invokeMethod(toggle, "clicked")
        assert settled(lambda: toggle.property("text") == "Mostra tutto" and 0 < text.height() <= 144)
        assert workspace.transcript.rows[row]["outputExpanded"] is False
        assert workspace.transcript.rows[row]["body"] == source
        return {"available": True, "compact_height": text.height(),
                "expand_collapse": True, "full_source_copy": True,
                "source_unchanged": True, "literal_output": True,
                "autofollow_paused": True,
                "data_kind": "synthetic presentation only, no model run"}

    def verify_attachments():
        bridge = engine.rootContext().contextProperty("attachmentInputs")
        assert bridge is not None, "installed QML lacks attachment inputs"
        prompt = window.findChild(QObject, "prompt")
        assert prompt is not None
        # Xvfb without a window manager may leave the window inactive after a
        # popup. Synthetic key delivery must model a focused, user-facing window.
        window.requestActivate()
        assert QTest.qWaitForWindowActive(window, 3000)

        def wait_import(target):
            for _ in range(500):
                QTest.qWait(10)
                if not target.property("attachmentBusy"):
                    break
            if target.property("attachmentBusy"):
                # Preserve worker stacks on a timeout without relaxing the gate.
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            assert not target.property("attachmentBusy")
            assert not target.property("attachmentError"), target.property("attachmentError")

        source = Path(workspace.project["root"]) / "preserve-me.txt"
        before = source.read_bytes()
        prompt.setProperty("text", "")
        prompt.forceActiveFocus()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])
        app.clipboard().setMimeData(mime)
        QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        wait_import(prompt)
        assert prompt.property("text").strip() == str(source), {
            "phase": "file_paste", "active_focus": prompt.property("activeFocus"),
            "window_active": window.isActive(), "focus_object":
                app.focusObject().objectName() if app.focusObject() else None,
            "text": prompt.property("text"), "error": prompt.property("attachmentError"),
        }
        prompt.setProperty("text", "")
        bitmap = QImage(16, 12, QImage.Format.Format_ARGB32)
        bitmap.fill(QColor("#82aaff"))
        app.clipboard().setImage(bitmap)
        prompt.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Insert, Qt.KeyboardModifier.ShiftModifier)
        wait_import(prompt)
        literal = prompt.property("text").strip()
        saved = Path(json.loads(literal) if literal.startswith('"') else literal)
        assert saved.is_file() and saved.parent == workspace.paths.data / "attachments"
        actual = QImage(str(saved))
        assert not actual.isNull() and actual.size() == bitmap.size()
        assert saved.stat().st_mode & 0o777 == 0o600
        assert saved.parent.stat().st_mode & 0o777 == 0o700
        prompt.setProperty("text", "")
        QTest.qWait(30)
        point = prompt.mapToScene(QPointF(15, 15))
        dropped = QMimeData()
        dropped.setUrls([QUrl.fromLocalFile(str(source))])
        enter = QDragEnterEvent(point.toPoint(), Qt.DropAction.CopyAction, dropped,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(window, enter)
        event = QDropEvent(point, Qt.DropAction.CopyAction, dropped,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(window, event)
        wait_import(prompt)
        assert event.isAccepted() and prompt.property("text").strip() == str(source)
        assert source.read_bytes() == before
        prompt.setProperty("text", "")
        return {"available": True, "file_url_paste": True, "image_shift_insert": True,
                "private_png": True, "native_qt_drop": True, "source_unchanged": True,
                "data_kind": "synthetic Qt MIME/events, not Windows Explorer or live inference"}

    def verify_chat_settings():
        assert hasattr(workspace, "uiPreferences"), "installed Workspace lacks UI preferences"
        selected = (workspace.backend, workspace.selectedModel, workspace.selectedEffort, workspace.sessionId)
        window.setProperty("page", 0)
        window.resize(1280, 800)
        workspace.setUiPreference("details_visible", False)
        QTest.qWait(40)
        panel = window.findChild(QObject, "detailsPanel")
        toggle = window.findChild(QObject, "detailsToggle")
        assert panel is not None and toggle is not None and not panel.isVisible()
        QMetaObject.invokeMethod(toggle, "clicked")
        QTest.qWait(40)
        assert panel.isVisible() and workspace.uiPreferences["details_visible"]
        window.resize(880, 620)
        drawer = window.findChild(QObject, "detailsDrawer")
        assert drawer is not None
        for _ in range(200):
            QTest.qWait(10)
            if drawer.property("opened"):
                break
        assert drawer.property("opened")
        QMetaObject.invokeMethod(drawer, "close")
        for _ in range(200):
            QTest.qWait(10)
            if not workspace.uiPreferences["details_visible"]:
                break
        assert not workspace.uiPreferences["details_visible"]
        window.resize(1280, 800)
        window.setProperty("page", 3)
        QTest.qWait(40)
        assert window.findChild(QObject, "settingsPane") is not None
        workspace.setUiPreference("chat_font_size", 16)
        workspace.setUiPreference("confirm_close", "always")
        saved = json.loads((workspace.paths.config / "ui.json").read_text())
        assert saved["chat_font_size"] == 16 and saved["confirm_close"] == "always"
        workspace.setUiPreference("confirm_close", "active")
        workspace.setUiPreference("chat_font_size", 14)
        window.setProperty("page", 0)
        original_runs = workspace._runs
        workspace._runs = {"synthetic-activity": {"id": "synthetic-activity", "state": "running", "session_id": workspace.sessionId}}
        workspace.changed.emit()
        QTest.qWait(40)
        indicator = window.findChild(QObject, "activityIndicator")
        assert indicator is not None and indicator.property("label") == "Sto pensando…"
        assert indicator.property("animating")
        workspace.setUiPreference("reduce_motion", True)
        QTest.qWait(30)
        assert not indicator.property("animating")
        workspace.setUiPreference("reduce_motion", False)
        rows = workspace.transcript.rowCount()
        workspace._apply_event({"run_id": "synthetic-activity", "type": "backend.notice",
                                "payload": {"message": "Codex session configured"}})
        assert workspace.transcript.rowCount() == rows
        workspace._runs["synthetic-activity"].update(
            prompt="Review the synthetic document", created_at="2026-10-14T01:00:00Z")
        workspace._apply_event({"run_id": "synthetic-activity", "type": "run.state", "seq": 3,
                                "timestamp": "2026-10-14T01:02:00Z",
                                "payload": {"state": "completed"}})
        assert workspace.transcript.rowCount() == rows
        summary = workspace.activity[0]
        assert "Review the synthetic document" in summary["description"]
        assert json.loads(summary["details"])["execution_id"] == "synthetic-activity"
        workspace._runs = original_runs
        workspace.changed.emit()
        workspace.transcript.append(kind="assistant", title="SYNTHETIC SCROLL CHECK",
                                    body="Synthetic scroll fixture.\n\n" * 60)
        QTest.qWait(80)
        bar = window.findChild(QObject, "conversationScrollBar")
        assert bar is not None and bar.isVisible() and bar.width() >= 16
        assert bar.property("contentItem").height() >= 35
        assert selected == (workspace.backend, workspace.selectedModel, workspace.selectedEffort, workspace.sessionId)
        return {"available": True, "details_persisted": True, "narrow_drawer": True,
                "preferences_persisted": True, "thinking_indicator": True,
                "reduced_motion": True, "routine_notice_hidden": True, "run_status_context": True,
                "scrollbar_width": bar.width(), "thumb_height": bar.property("contentItem").height(),
                "model_and_session_unchanged": True,
                "data_kind": "synthetic GUI states; no provider execution"}

    def verify_project_chats():
        assert hasattr(workspace, "newChat"), "installed Workspace lacks project chats"
        assert len(workspace.sessions) == 2 and workspace.sessionId
        original = workspace.sessionId
        other = next(chat for chat in workspace.sessions if chat["id"] != original)
        workspace.setDraftText("Saved draft for the first chat")
        workspace.selectSession(other["id"])
        for _ in range(500):
            QTest.qWait(10)
            if not workspace.sessionsLoading:
                break
        assert not workspace.sessionsLoading
        assert workspace.sessionId == other["id"] and not workspace.transcript.rows
        assert workspace.draftText == ""
        workspace.selectSession(original)
        for _ in range(500):
            QTest.qWait(10)
            if not workspace.sessionsLoading:
                break
        assert workspace.sessionId == original and workspace.draftText == "Saved draft for the first chat"
        assert not workspace.transcript.rows
        workspace.setDraftText("")
        window.findChild(QObject, "prompt").setProperty("text", "")
        window.setProperty("page", 0)
        dialog = window.findChild(QObject, "usageDialog")
        assert dialog is not None
        QMetaObject.invokeMethod(dialog, "open")
        QTest.qWait(60)
        assert not dialog.property("rawVisible")
        QMetaObject.invokeMethod(window.findChild(QObject, "usageRawToggle"), "clicked")
        assert dialog.property("rawVisible")
        QMetaObject.invokeMethod(dialog, "close")
        return {"available": True, "owned_sessions": 2, "separate_histories": True,
                "draft_restore": True, "human_usage_and_raw": True,
                "data_kind": "actual installed IPC/session CRUD and Qt; no inference"}

    def verify_popup():
        was_visible = getattr(workspace, "modelControlsVisible", True)
        popover = window.findChild(QObject, "modelPopover")
        if popover is not None:
            QMetaObject.invokeMethod(popover, "open")
        elif hasattr(workspace, "setModelControlsVisible"):
            workspace.setModelControlsVisible(True)
        QTest.qWait(30)
        combo = window.findChild(QObject, "backendBox")
        assert combo is not None

        def evaluate(expression):
            script = QQmlExpression(QQmlEngine.contextForObject(combo), combo, expression)
            value, _ = script.evaluate()
            assert not script.hasError(), script.error().toString()
            return value

        def luminance(rgb):
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

        evaluate("popup.open()")
        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec()
        rows = json.loads(evaluate("""
            JSON.stringify(Array.from({length: count}, function(_, index) {
                const item = popup.contentItem.itemAtIndex(index);
                if (!item) return null;
                const foreground = item.contentItem.color;
                const background = item.background;
                const underneath = popup.background.color;
                const alpha = background.visible ? background.color.a * background.opacity : 0;
                const rgb = ["r", "g", "b"].map(function(channel) {
                    return background.color[channel] * alpha + underneath[channel] * (1 - alpha);
                });
                return {text: item.contentItem.text, textFormat: item.contentItem.textFormat,
                    highlighted: item.highlighted, foreground: [foreground.r, foreground.g, foreground.b],
                    background: rgb};
            }))
        """))
        assert rows and all(row and row["textFormat"] == 0 for row in rows)
        assert [row["text"] for row in rows] == [
            "Ollama", "Codex", "OpenAI diretto · ChatGPT", "DeepSeek Account",
            "DeepSeek Web · sperimentale", "Claude Code · uso personale"]
        assert {row["highlighted"] for row in rows} == {False, True}
        for row in rows:
            light, dark = sorted((luminance(row["foreground"]), luminance(row["background"])),
                                 reverse=True)
            row["contrast_ratio"] = (light + 0.05) / (dark + 0.05)
            assert row["contrast_ratio"] >= 4.5, row
        evaluate("popup.close()")
        if hasattr(workspace, "setModelControlsVisible"):
            workspace.setModelControlsVisible(was_visible)
        return {"rows": rows, "minimum_ratio": 4.5,
                "native_palette_fixture": {"light": "#242c30", "midlight": "#20262b"}}

    def verify_appearance():
        appearance = engine.rootContext().contextProperty("appearance")
        assert appearance is not None, "installed QML lacks appearance settings"
        preferences = workspace.uiPreferences
        original_theme = appearance.exportTheme()
        selected = (workspace.backend, workspace.selectedModel, workspace.selectedEffort, workspace.sessionId)
        page = window.property("page")
        try:
            window.setProperty("page", 3)
            assert window.findChild(QObject, "appearancePane") is not None
            appearance.setOption("mode", "light")
            appearance.setOption("code_size", 17)
            appearance.setStyle("accent", "#4060a0")
            QTest.qWait(40)
            assert window.color().name() == appearance.colors["background"]
            assert not appearance.dark and appearance.codeFont.pixelSize() == 17
            saved = json.loads((workspace.paths.config / "ui.json").read_text())
            assert saved["appearance"]["mode"] == "light"
            assert saved["appearance"]["code_size"] == 17
            exported = appearance.exportTheme()
            assert not appearance.importTheme('{"provider":"not-an-appearance-option"}')
            assert appearance.exportTheme() == exported
            assert appearance.importTheme(original_theme)
            assert selected == (workspace.backend, workspace.selectedModel, workspace.selectedEffort, workspace.sessionId)
            return {"available": True, "live_palette": True, "independent_code_size": True,
                    "private_persistence": True, "invalid_import_preserves_state": True,
                    "theme_roundtrip": True, "provider_selection_preserved": True,
                    "data_kind": "synthetic installed Qt interaction; no provider turn"}
        finally:
            workspace._save_preferences(preferences)
            window.setProperty("page", page)
            QTest.qWait(20)

    def check():
        nonlocal opened, finished
        if workspace.connected and not opened:
            opened = True
            workspace.openProject(project_id)
        if workspace.project.get("id") != project_id or getattr(workspace, "sessionsLoading", False):
            return
        if not finished:
            finished = True
            window.setWidth(1280)
            window.setHeight(800)
            workspace.copy("Installed Kira clipboard verification")
            assert app.clipboard().text() == "Installed Kira clipboard verification"
            report.update(qml_root=True, clipboard=True, platform=app.platformName())
            report["desktop_design"] = verify_design()
            report["popup_contrast"] = verify_popup()
            report["markdown_output"] = verify_markdown()
            report["attachment_input"] = verify_attachments()
            report["tool_output_preview"] = verify_output_preview()
            report["chat_settings"] = verify_chat_settings()
            report["appearance"] = verify_appearance()
            report["project_chats"] = verify_project_chats()
            assert hasattr(workspace, "teams"), "installed Workspace lacks Teams"
            if hasattr(workspace, "teams"):
                # Load the actual packaged team QML, with the installed service.
                # Editing a draft does not start inference or a team fixture.
                window.setProperty("page", 4)
                QCoreApplication.processEvents()
                view = window.findChild(QObject, "teamView")
                editor = window.findChild(QObject, "teamEditor")
                assert view is not None and editor is not None
                workspace.teams.setMode("manual")
                workspace.teams.setCount(3)
                QMetaObject.invokeMethod(editor, "open")
                loop = QEventLoop()
                QTimer.singleShot(200, loop.quit)
                loop.exec()
                def visual_item(root, name):
                    if root.objectName() == name:
                        return root
                    for child in root.childItems():
                        found = visual_item(child, name)
                        if found is not None:
                            return found
                    return None
                role = visual_item(window.contentItem(), "teamMemberRole_1")
                assert role is not None
                role.forceActiveFocus()
                QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                for character in "Installed draft verification":
                    QTest.keyClick(window, character)
                    QCoreApplication.processEvents()
                assert role.property("text") == "Installed draft verification"
                assert workspace.teams.draft["members"][1]["role"] == "Installed draft verification"
                assert window.findChild(QObject, "teamCountInput").property("value") == 3
                report["team_ui"] = {"packaged_view_loaded": True, "packaged_editor_loaded": True,
                                      "manual_members": 3, "draft_edited": True,
                                      "provider_execution": False}
            QTimer.singleShot(1500, finish)

    def finish():
        output = Path.home() / "installed-kira.png"
        assert window.grabWindow().save(str(output))
        report["screenshot"] = str(output)
        window.close()
        app.quit()

    timer = QTimer()
    timer.setInterval(200)
    timer.timeout.connect(check)
    timer.start()
    QTimer.singleShot(0, workspace.start)
    # A ceiling for slow runners, not a pause: a healthy run ends long before.
    QTimer.singleShot(60000, lambda: app.exit(2))
    code = app.exec()
    workspace.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert code == 0 and finished
    current_cache = cache_hashes()
    current_files = [name for name in current_cache
                     if (cache_root / name).is_relative_to(expected_cache)]
    assert current_files, "Installed QML must write actual bytecode in its content-specific cache"
    assert all(current_cache.get(name) == value for name, value in prior_cache.items())
    report["qml_cache"] = {"content_specific_path": str(expected_cache),
        "observed_payload_files": current_files, "prior_file_hashes_preserved": prior_cache,
        "environment_restored": os.environ.get("QML_DISK_CACHE_PATH") == cache_environment,
        "cache_hit_proven": False}
    (Path.home() / "installed-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
