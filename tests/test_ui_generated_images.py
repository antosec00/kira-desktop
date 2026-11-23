"""Pictures produced by a model: shown with the answer from Kira's private copy, opened on request."""

import hashlib
import os
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow

from kira.domain.paths import Paths
from kira.ui.appearance import Appearance
from kira.ui.controller import Workspace
from kira.ui.models import TranscriptModel

QML = Path(__file__).parents[1] / "src/kira/ui/qml"


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime")))
    project = tmp_path / "project"
    project.mkdir()
    view = Workspace(paths)
    view._project = {"id": "project", "root": str(project), "permission": "project_write"}
    view._session = {"id": "session", "project_id": "project"}
    view._connected_value = True
    view._runs["run"] = {"id": "run", "project_id": "project", "session_id": "session",
                         "created_at": "2026-01-01T10:00:00Z", "state": "running"}
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: None)
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def private_copy(view, width=64, height=36):
    """A normalized picture in Kira's private cache, as the service leaves it."""
    identity = str(uuid.uuid4())
    directory = view.paths.data / "image-attachments"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{identity}.png"
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0xff225588)
    assert image.save(str(path), "PNG")
    path.chmod(0o400)
    return {"id": identity, "name": "kira-20260101-100000.png", "mime": "image/png", "width": width,
            "height": height, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def visual_child(item, name):
    """Repeater delegates are visual children, not QObject children, of the component."""
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_child(child, name)
        if found is not None:
            return found
    return None


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def emit(view, kind, payload):
    view._event({"run_id": "run", "seq": view._cursors.get("run", 0) + 1, "type": kind,
                 "timestamp": "2026-01-01T10:01:00Z", "payload": payload})


def test_answer_with_only_pictures_becomes_a_row_with_its_private_copies(workspace):
    view = workspace
    picture = private_copy(view)
    saved = Path(view._project["root"]) / "generated_images" / picture["name"]
    emit(view, "tool.started", {"tool_id": "t", "name": "kira_cloud.image_generation", "source": "kira_cloud",
                                "arguments": {"model": "kira-image", "prompt": "Un gatto", "reference_images": 0}})
    row = view.transcript.rows[-1]
    assert row["receiptLabel"] == "Generazione dell’immagine" and row["receiptTarget"] == "kira-image"
    emit(view, "tool.completed", {"tool_id": "t", "name": "kira_cloud.image_generation", "result": {
        "ok": True, "count": 1, "path": str(saved), "images": [{"path": str(saved), "name": saved.name}]}})
    assert view.transcript.rows[-1]["receiptTarget"] == str(saved)
    assert view.transcript.rows[-1]["status"] == "confermato"

    emit(view, "message.completed", {"content": "", "item_id": "item",
                                     "attachments": [{**picture, "path": str(saved)}, "junk", None]})
    answer = view.transcript.rows[-1]
    assert answer["kind"] == "assistant" and answer["body"] == ""
    assert answer["attachments"] == [{**picture, "path": str(saved)}]
    # Only Kira's own normalized copy is ever rendered, wherever the event says the file is.
    preview = view.imagePreview(answer["attachments"][0])
    assert QUrl(preview).toLocalFile() == str(view.paths.data / "image-attachments" / (picture["id"] + ".png"))

    # A streamed text answer gains its pictures when it completes.
    emit(view, "message.delta", {"content": "Ecco ", "item_id": "second"})
    emit(view, "message.completed", {"content": "Ecco il gatto.", "item_id": "second", "attachments": [picture]})
    assert view.transcript.rows[-1]["body"] == "Ecco il gatto."
    assert view.transcript.rows[-1]["attachments"] == [picture]
    # Ordinary answers are unchanged.
    emit(view, "message.completed", {"content": "Solo testo.", "item_id": "third"})
    assert view.transcript.rows[-1]["attachments"] == []
    assert isinstance(view.transcript, TranscriptModel)


def test_open_uses_the_saved_original_only_where_kira_saves_pictures(workspace, qtbot, monkeypatch, tmp_path):
    view = workspace
    opened: list[str] = []
    problems: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    monkeypatch.setattr(view, "_problem", lambda message: problems.append(str(message)))
    picture = private_copy(view)
    private = str(view.paths.data / "image-attachments" / (picture["id"] + ".png"))
    folder = Path(view._project["root"]) / "generated_images"
    folder.mkdir()
    saved = folder / picture["name"]
    saved.write_bytes(Path(private).read_bytes())

    view.openGeneratedImage({**picture, "path": str(saved)})
    qtbot.waitUntil(lambda: len(opened) == 1)
    assert opened == [str(saved)]

    # Outside the project and Kira's own records, a link, a script, or a missing file: the private copy.
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(saved.read_bytes())
    script = folder / "run.sh"
    script.write_text("#!/bin/sh\n")
    link = folder / "link.png"
    link.symlink_to(outside)
    for path in (str(outside), str(script), str(link), str(folder / "missing.png"), "relative.png", ""):
        opened.clear()
        view.openGeneratedImage({**picture, "path": path})
        qtbot.waitUntil(lambda: len(opened) == 1)
        assert opened == [private], path

    # The picture kept next to the run's private records opens too (read-only projects).
    kept = view.paths.artifacts / "run" / "image-1.png"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(saved.read_bytes())
    opened.clear()
    view.openGeneratedImage({**picture, "path": str(kept)})
    qtbot.waitUntil(lambda: len(opened) == 1)
    assert opened == [str(kept)]

    # Nothing left on disk: the user is told, nothing is opened.
    opened.clear()
    view.openGeneratedImage({"id": str(uuid.uuid4()), "mime": "image/png", "path": str(folder / "missing.png")})
    qtbot.waitUntil(lambda: bool(problems))
    assert not opened and "non è più disponibile" in problems[0]


@pytest.mark.parametrize(("generated", "size"), [(True, (640, 360)), (True, (300, 900)), (False, (640, 360))])
def test_picture_cards_are_large_for_answers_and_compact_for_attachments(workspace, qtbot, qtlog, generated, size):
    view = workspace
    picture = private_copy(view, *size)
    engine = QQmlEngine()
    appearance = Appearance(view, engine)
    engine.rootContext().setContextProperty("appearance", appearance)
    engine.rootContext().setContextProperty("workspace", view)
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML / "ImageAttachments.qml")))
    item = component.create()
    assert item is not None, [error.toString() for error in component.errors()]
    window = QQuickWindow()
    window.resize(900, 500)
    item.setParentItem(window.contentItem())
    item.setProperty("width", 880)
    item.setProperty("generated", generated)
    item.setProperty("images", [picture])
    item.setProperty("height", item.property("implicitHeight"))
    window.show()
    try:
        qtbot.waitUntil(lambda: visual_child(item, "imageThumbnail_" + picture["id"]) is not None)
        thumbnail = visual_child(item, "imageThumbnail_" + picture["id"])
        open_button = visual_child(item, "openImage_" + picture["id"])
        remove_button = visual_child(item, "removeImage_" + picture["id"])
        assert thumbnail is not None and open_button is not None and remove_button is not None
        qtbot.waitUntil(lambda: thumbnail.property("paintedWidth") > 0, timeout=5000)  # decoded and shown
        assert thumbnail.property("source").toLocalFile().endswith(picture["id"] + ".png")
        assert not remove_button.isVisible()
        if generated:
            assert item.property("implicitHeight") > 250 and thumbnail.height() >= 180
            # The card follows the picture's shape within fixed bounds.
            assert 150 <= thumbnail.width() <= 420
            assert open_button.isVisible()
            assert open_button.mapToItem(item, open_button.boundingRect().bottomLeft()).y() <= item.height()
        else:
            assert item.property("implicitHeight") == 92 and thumbnail.width() == 58 and thumbnail.height() == 58
            assert not open_button.isVisible()
        assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                       or "TypeError:" in record.message for record in qtlog.records)
    finally:
        window.close()
        item.deleteLater()
        engine.deleteLater()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_model_cards_say_which_models_make_pictures_and_what_one_costs(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    from kira.ui.app import create_engine

    view = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    view._connected_value = True
    view._project = {"id": "p", "name": "Documenti", "root": str(tmp_path / "p"), "permission": "read_only", "memory": {}}
    view._projects = [view._project]
    view._backend, view._model, view._effort = "kira_cloud", "kira-image", ""
    price = {"currency": "EUR", "input_per_1m": 2.5, "output_per_1m": 12.0}
    view._catalogs = {"kira_cloud": {"available": True, "account": {"connected": True, "plan_active": True}, "models": [
        {"id": "kira-image", "name": "Kira Image", "pricing": {**price, "per_image": 0.046},
         "capabilities": {"tools": False, "images": True, "image_generation": True}},
        {"id": "kira-image-lite", "name": "Kira Image Lite", "pricing": {"currency": "USD", "per_image": 0.25},
         "capabilities": {"tools": False, "images": False, "image_generation": True}},
        {"id": "kira-auto", "name": "Kira Auto", "tools_verified": True, "pricing": {**price, "per_image": 0.0},
         "capabilities": {"tools": True, "images": True, "image_generation": False}}]}}
    view._preferences["onboarding_complete"] = True
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: True)
    engine = create_engine(view)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
    window.setProperty("page", 1)
    qtbot.wait(60)
    try:
        def text(name):
            item = visual_child(window.contentItem(), name)
            assert item is not None, name
            return item.property("text")

        assert text("modelCapability_kira-image").startswith("Crea e modifica immagini")
        assert text("modelCapability_kira-image-lite") == "Crea immagini da una descrizione"
        assert text("modelCapability_kira-auto") == "Strumenti verificati"
        # A picture is priced as a picture; text models keep their per-token prices.
        assert text("modelPrice_kira-image") == "Costo indicativo: da 0,046 € per immagine" \
            or text("modelPrice_kira-image") == "Costo indicativo: da 0.046 € per immagine"
        assert text("modelPrice_kira-image-lite").endswith("25 $ per immagine")
        assert text("modelPrice_kira-auto").startswith("Costo per 1M di token:")
        # Reference pictures can be attached only to image models that read images.
        assert view.imageInputsEnabled is True and view.imageGenerationSelected is True
        prompt = next(item for item in walk(window.contentItem()) if item.objectName() == "prompt")
        assert prompt.property("placeholderText").startswith("Descrivi l'immagine da creare")
        view._model = "kira-image-lite"
        assert view.imageInputsEnabled is False and view.imageGenerationSelected is True
        view._model = "kira-auto"
        view.changed.emit()
        qtbot.wait(30)
        assert view.imageGenerationSelected is False
        assert prompt.property("placeholderText").startswith("Descrivi il prossimo passo")
        assert not any("ReferenceError" in record.message or "binding loop" in record.message.lower()
                       or "TypeError:" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_too_many_reference_pictures_are_refused_while_still_in_the_draft(qapp, tmp_path, monkeypatch):
    """Said before sending: the images stay attached and nothing is requested or charged."""
    view = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    try:
        view._connected_value = True
        view._project = {"id": "project"}
        view._session = {"id": "chat", "project_id": "project", "backend": "kira_cloud",
                         "model": "kira-image", "settings": {}}
        view._backend, view._model = "kira_cloud", "kira-image"
        view._catalogs = {"kira_cloud": {"available": True, "models": [
            {"id": "kira-image", "max_reference_images": 1,
             "capabilities": {"images": True, "image_generation": True}},
            {"id": "kira-auto", "max_reference_images": None,
             "capabilities": {"images": True, "image_generation": False}}]}}
        calls: list = []
        problems: list[str] = []
        monkeypatch.setattr(view.client, "request", lambda *args: calls.append(args))
        monkeypatch.setattr(view, "_problem", lambda message: problems.append(str(message)))
        directory = view.paths.data / "image-attachments"
        first, second = ({**row, "preview_url": (directory / (row["id"] + ".png")).as_uri()}
                         for row in (private_copy(view), private_copy(view)))
        assert view.addDraftImage(first, ("project", "chat")) and view.addDraftImage(second, ("project", "chat"))
        assert view.referenceImageLimit == 1

        view.send("Uniscile")
        assert not calls and "una sola immagine di riferimento" in problems[-1]
        assert view.draft_image_ids() == [first["id"], second["id"]]

        view.removeDraftImage(second["id"])
        view.send("Rendila blu")
        assert calls[-1][0] == "run.start" and calls[-1][1]["attachments"] == [first["id"]]

        # A text model with vision has no such limit of its own.
        view._model = "kira-auto"
        assert view.referenceImageLimit == 0
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
