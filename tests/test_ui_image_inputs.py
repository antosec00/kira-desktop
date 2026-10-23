"""Explicit image preparation stays local until an acknowledged message send."""
import hashlib
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QObject, QUrl, Slot
from PySide6.QtGui import QImage

from kira.domain.paths import Paths
from kira.ui.attachments import AttachmentInputs, MimeSnapshot, prepare_composer_attachment
from kira.ui.controller import Workspace
from kira.ui.drafts import DraftStore


def metadata(view, name="<literal image>.png"):
    identity = str(uuid.uuid4())
    directory = view.paths.data / "image-attachments"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{identity}.png"
    image = QImage(12, 8, QImage.Format.Format_RGB32)
    image.fill(0xff225588)
    assert image.save(str(path), "PNG")
    path.chmod(0o400)
    return {"id": identity, "name": name, "mime": "image/png", "width": 12, "height": 8,
            "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "preview_url": path.as_uri()}


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        for key, value in {"attachmentReceiver": True, "imageAttachments": True, "attachmentBusy": False,
                           "attachmentError": "", "attachmentContext": "project:chat", "text": "draft",
                           "selectionStart": 5, "selectionEnd": 5, "visible": True, "enabled": True,
                           "readOnly": False}.items():
            self.setProperty(key, value)

    @Slot(int, int)
    def select(self, start, end):
        self.setProperty("selectionStart", start)
        self.setProperty("selectionEnd", end)

    @Slot("QVariant")
    def insertAttachment(self, text):
        self.setProperty("text", self.property("text") + text)

    @Slot("QVariant")
    def insertPlainText(self, text):
        self.setProperty("text", self.property("text") + text)


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "project"}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "openai_chatgpt",
                          "model": "image-model", "settings": {}}
    workspace._backend, workspace._model = "openai_chatgpt", "image-model"
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: calls.append(args))
    yield workspace, calls
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_plain_path_is_literal_but_explicit_file_image_is_import_candidate(view):
    workspace, calls = view
    row = metadata(workspace)
    path = QUrl(row["preview_url"]).toLocalFile()
    result = prepare_composer_attachment(MimeSnapshot(text=path), workspace.paths.data, distro=None, wsl=False)
    assert result == (path, [], None, False)
    result = prepare_composer_attachment(MimeSnapshot(urls=(row["preview_url"],)), workspace.paths.data,
                                        distro=None, wsl=False)
    assert result == ("", [path], None, True)
    assert not calls


def test_import_metadata_preview_and_restart_preserve_image_only_draft(view, qtbot):
    workspace, calls = view
    row = metadata(workspace)
    bridge = AttachmentInputs(workspace.paths.data, workspace=workspace)
    target = Receiver()
    mime = QMimeData()
    mime.setUrls([QUrl(row["preview_url"])])
    bridge.submit(target, mime)
    qtbot.waitUntil(lambda: bool(calls))
    assert calls[-1][0] == "attachment.import" and target.property("attachmentBusy")
    calls[-1][2]({"attachment": row})
    assert not target.property("attachmentBusy") and target.property("text") == "draft"
    assert workspace.draft_image_ids() == [row["id"]]
    assert DraftStore(workspace.paths.config).images[("project", "chat")] == [row]
    reopened = Workspace(workspace.paths)
    reopened._project, reopened._session = workspace.project, workspace._session
    assert reopened.draftImages == [row]
    assert not any(call[0] in {"run.start", "queue.add"} for call in calls)
    reopened.close()
    reopened.deleteLater()
    bridge.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_async_import_cannot_attach_to_changed_chat(view, qtbot):
    workspace, calls = view
    row = metadata(workspace)
    bridge = AttachmentInputs(workspace.paths.data, workspace=workspace)
    target = Receiver()
    bridge.importUrls(target, [QUrl(row["preview_url"])])
    qtbot.waitUntil(lambda: bool(calls))
    target.setProperty("attachmentContext", "other")
    workspace._session = {**workspace._session, "id": "other"}
    calls[-1][2]({"attachment": row})
    assert not workspace._draft_images and target.property("attachmentError")
    bridge.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_image_only_send_has_ids_and_ack_does_not_remove_later_image(view):
    workspace, calls = view
    first, later = metadata(workspace), metadata(workspace)
    assert workspace.addDraftImage(first, ("project", "chat"))
    workspace.send("")
    request = calls[-1]
    assert request[0] == "run.start" and request[1]["attachments"] == [first["id"]]
    assert "preview_url" not in str(request[1])
    workspace.addDraftImage(later, ("project", "chat"))
    request[2]({"run": {"id": "run", "session_id": "chat", "state": "running"}})
    assert workspace.draft_image_ids() == [later["id"]]


def test_queue_failure_preserves_images_and_retry_keeps_identity(view):
    workspace, calls = view
    row = metadata(workspace)
    workspace.addDraftImage(row, ("project", "chat"))
    workspace.workbench.enqueue("")
    first = calls[-1]
    assert first[0] == "queue.add" and first[1]["attachments"] == [row["id"]]
    first[3]({"code": "DISCONNECTED", "message": "Unknown acknowledgement"})
    workspace.workbench.enqueue("")
    assert calls[-1][1] == first[1] and workspace.draft_image_ids() == [row["id"]]
    calls[-1][2]({"item": {"id": "queued", "status": "pending"}})
    assert not workspace.draftImages


def test_unsupported_provider_refuses_send_preserving_images(view):
    workspace, calls = view
    row = metadata(workspace)
    workspace.addDraftImage(row, ("project", "chat"))
    workspace._backend = "codex"
    workspace.send("describe")
    workspace.workbench.enqueue("describe")
    assert not calls and workspace.draft_image_ids() == [row["id"]]


def test_preview_never_uses_remote_or_arbitrary_metadata_path(view):
    workspace, _ = view
    row = metadata(workspace)
    forged = {**row, "preview_url": "https://example.invalid/tracker"}
    assert workspace.imagePreview(forged) == row["preview_url"]
    forged["id"] = "../../unexpected"
    assert workspace.imagePreview(forged) == ""
    path = Path(QUrl(row["preview_url"]).toLocalFile())
    path.unlink()
    path.symlink_to(workspace.paths.config / "composer.json")
    assert workspace.imagePreview(row) == ""


def test_image_draft_removal_and_limits_preserve_other_chat_data(view):
    workspace, _ = view
    images = [metadata(workspace) for _ in range(5)]
    for row in images[:4]:
        assert workspace.addDraftImage(row, ("project", "chat"))
    saved = deepcopy(workspace._draft_images)
    assert not workspace.addDraftImage(images[-1], ("project", "chat"))
    assert workspace._draft_images == saved
    workspace.removeDraftImage(images[0]["id"])
    assert workspace.draft_image_ids() == [row["id"] for row in images[1:4]]
    assert len(DraftStore(workspace.paths.config).images[("project", "chat")]) == 3


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        if found := visual_item(child, name):
            return found
    return None


def close_engine(engine, window):
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_real_composer_previews_and_removes_images_with_literal_names(view, qtbot, qtlog):
    from PySide6.QtCore import QMetaObject
    from kira.ui.app import create_engine

    workspace, calls = view
    workspace._preferences["onboarding_complete"] = True
    row = metadata(workspace)
    workspace.addDraftImage(row, ("project", "chat"))
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        window.resize(880, 620)
        qtbot.wait(80)
        preview = visual_item(window.contentItem(), "imageThumbnail_" + row["id"])
        assert preview and preview.property("source").toString() == row["preview_url"]
        assert visual_item(window.contentItem(), "sendButton").property("enabled")
        chip = visual_item(window.contentItem(), "draftImageAttachments")
        assert chip.width() > 0 and chip.height() == 92
        engine.rootContext().contextProperty("localization").setLanguage("en")
        qtbot.wait(20)
        remove = visual_item(window.contentItem(), "removeImage_" + row["id"])
        assert remove.property("text") == "Remove"
        QMetaObject.invokeMethod(remove, "clicked")
        assert not workspace.draftImages
        assert not any(call[0] in {"run.start", "queue.add"} for call in calls)
        assert not any("ReferenceError" in r.message or "binding loop" in r.message.lower() for r in qtlog.records)
    finally:
        close_engine(engine, window)


@pytest.mark.parametrize("backend", ["codex", "ollama", "openai_chatgpt", "claude_code"])
def test_real_clipboard_routes_direct_images_and_preserves_other_provider_paths(view, qtbot, monkeypatch, backend):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from kira.service.image_attachments import ImageAttachments
    from kira.ui.app import create_engine

    workspace, calls = view
    workspace._backend = backend
    workspace._session["backend"] = backend
    workspace._preferences["onboarding_complete"] = True
    store = ImageAttachments(workspace.paths.data / "image-attachments")

    def request(*args):
        calls.append(args)
        if args[0] == "attachment.import":
            args[2]({"attachment": store.import_file(args[1]["path"])})

    monkeypatch.setattr(workspace.client, "request", request)
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    try:
        engine.rootContext().contextProperty("attachmentInputs")._wsl = False
        prompt = window.findChild(QObject, "prompt")
        prompt.setProperty("text", "Literal draft")
        prompt.setProperty("cursorPosition", len("Literal draft"))
        prompt.forceActiveFocus()
        qtbot.wait(20)
        image = QImage(8, 12, QImage.Format.Format_RGB32)
        image.fill(0xff225588)
        mime = QMimeData()
        mime.setImageData(image)
        QGuiApplication.clipboard().setMimeData(mime)
        QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        qtbot.waitUntil(lambda: not prompt.property("attachmentBusy"))
        if backend in {"openai_chatgpt", "claude_code"}:
            assert prompt.property("text") == "Literal draft"
            assert len(workspace.draftImages) == 1
            assert any(call[0] == "attachment.import" for call in calls)
        else:
            assert prompt.property("text").startswith("Literal draft\n")
            assert not workspace.draftImages
            assert not any(call[0] == "attachment.import" for call in calls)
        assert not any(call[0] in {"run.start", "queue.add"} for call in calls)
    finally:
        QGuiApplication.clipboard().clear()
        close_engine(engine, window)


def test_image_only_history_and_queue_show_thumbnail_metadata(view, qtbot):
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    from kira.ui.app import create_engine

    workspace, calls = view
    workspace._preferences["onboarding_complete"] = True
    row = metadata(workspace)
    workspace.transcript.append(kind="user", title="TU", body="", runId="previous", attachments=[row])
    workspace.workbench._queue = [{"id": "queued", "session_id": "chat", "status": "pending",
        "prompt": "", "mode": "execute", "attachments": [row]}]
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    try:
        qtbot.wait(80)
        historical = visual_item(window.contentItem(), "imageThumbnail_" + row["id"])
        assert historical and historical.property("source").toString() == row["preview_url"]
        assert historical.isVisible()
        dialog = window.findChild(QObject, "workbenchDialog")
        expression = QQmlExpression(QQmlEngine.contextForObject(dialog), dialog, 'openSection("queue")')
        expression.evaluate()
        assert not expression.hasError()
        request = next(call for call in reversed(calls) if call[0] == "queue.list")
        request[2]({"paused": True, "items": [{"id": "queued", "session_id": "chat", "status": "pending",
            "prompt": "", "mode": "execute", "attachments": [row]}]})
        qtbot.wait(50)
        queued = visual_item(window.contentItem(), "queueImages_queued")
        assert queued and queued.isVisible() and queued.height() > 0
        assert visual_item(queued, "imageThumbnail_" + row["id"]).property("source").toString() == row["preview_url"]
    finally:
        close_engine(engine, window)
