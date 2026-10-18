"""Attachment MIME, path/privacy boundaries and asynchronous draft lifetime checks."""

import json
import os
import subprocess
from pathlib import Path
from threading import Event
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QEvent, QIODevice, QMimeData
from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QImage

from kira.ui import attachments as io


def prepare(snapshot, tmp_path, **kwargs):
    return io.prepare_attachment(snapshot, tmp_path, distro=kwargs.get("distro", "Ubuntu"),
                                 wsl=kwargs.get("wsl", True))


def tiny_image():
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(0xff2255aa)
    return image


def encoded_image(image):
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return encoded.data()


def test_image_snapshot_is_independent_and_private(qapp, tmp_path):
    original = tiny_image()
    mime = QMimeData()
    mime.setImageData(original)
    snapshot = io.snapshot_mime(mime)
    original.fill(0xffffffff)
    mime.clear()
    text, file, attached = prepare(snapshot, tmp_path)
    assert attached and file and file == Path(text)
    assert file.stat().st_mode & 0o777 == 0o600
    assert file.parent.stat().st_mode & 0o777 == 0o700
    assert QImage(str(file)).pixelColor(0, 0) == tiny_image().pixelColor(0, 0)
    _, second, _ = prepare(snapshot, tmp_path)
    assert second != file


@pytest.mark.parametrize("mime_name", ["image/png", "image/jpeg", "image/webp", "image/bmp"])
def test_encoded_image_mime_is_decoded_as_image_not_text(qapp, tmp_path, mime_name):
    mime = QMimeData()
    mime.setData(mime_name, encoded_image(tiny_image()))
    mime.setText("https://example.invalid/picture")
    text, path, attached = prepare(io.snapshot_mime(mime), tmp_path)
    assert attached and path and str(path) == text
    assert QImage(str(path)).size() == tiny_image().size()


def test_mime_prefers_local_original_but_bitmap_over_remote_link(qapp, tmp_path):
    original = tmp_path / "original.png"
    original.write_bytes(encoded_image(tiny_image()))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(original))])
    mime.setImageData(tiny_image())
    assert prepare(io.snapshot_mime(mime), tmp_path) == (str(original), None, True)
    mime.setUrls([QUrl("https://example.invalid/picture.png")])
    _, path, attached = prepare(io.snapshot_mime(mime), tmp_path)
    assert attached and path != original


def test_image_limits_and_malformed_images(qapp, tmp_path, monkeypatch):
    mime = QMimeData()
    mime.setData("image/png", b"")
    with pytest.raises(ValueError, match="vuota"):
        io.snapshot_mime(mime)
    mime.setData("image/png", b"not an image")
    with pytest.raises(ValueError, match="leggibile"):
        prepare(io.snapshot_mime(mime), tmp_path)
    monkeypatch.setattr(io, "MAX_IMAGE_PIXELS", 10)
    mime.setImageData(tiny_image())
    with pytest.raises(ValueError, match="pixel"):
        io.snapshot_mime(mime)
    mime.clear()
    mime.setData("image/png", encoded_image(tiny_image()))
    with pytest.raises(ValueError, match="pixel"):
        prepare(io.snapshot_mime(mime), tmp_path)
    monkeypatch.setattr(io, "MAX_IMAGE_BYTES", 1)
    with pytest.raises(ValueError, match="MiB"):
        io.snapshot_mime(mime)
    with pytest.raises(ValueError, match="MiB"):
        prepare(io.MimeSnapshot(image=tiny_image()), tmp_path)
    assert not list(tmp_path.rglob("*.png"))


def test_private_directory_symlinks_and_open_permissions_rejected(qapp, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "attachments").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        prepare(io.MimeSnapshot(image=tiny_image()), data)
    assert not list(outside.iterdir())
    (data / "attachments").unlink()
    (data / "attachments").mkdir(mode=0o755)
    with pytest.raises(ValueError, match="0700"):
        prepare(io.MimeSnapshot(image=tiny_image()), data)


@pytest.mark.parametrize(("source", "expected"), [
    (r"C:\Users\Test\image.png", "/mnt/c/Users/Test/image.png"),
    ("file:///C:/Users/Test/image%20one.png", "/mnt/c/Users/Test/image one.png"),
    (r'"C:\Users\Test\image one.png"', "/mnt/c/Users/Test/image one.png"),
    (r"\\wsl.localhost\Ubuntu\home\test\file.txt", "/home/test/file.txt"),
    ("file://wsl.localhost/Ubuntu/home/test/file.txt", "/home/test/file.txt"),
    ("file:///tmp/name%2520literal.txt", "/tmp/name%20literal.txt"),
    ("file://localhost/tmp/name%2520literal.txt", "/tmp/name%20literal.txt"),
])
def test_windows_and_file_urls_are_decoded_once(source, expected):
    assert io._local_path(source, "Ubuntu", True) == Path(expected)


@pytest.mark.parametrize("source", [r"\\wsl.localhost\Other\home\file", "file://server/share/file",
                                    "file://wsl.localhost/Other/home/file"])
def test_other_distribution_and_remote_file_servers_rejected(source):
    with pytest.raises(ValueError, match="distribuzione"):
        io._local_path(source, "Ubuntu", True)


def test_paths_quote_spaces_newlines_unicode_and_preserve_originals(tmp_path):
    paths = [tmp_path / "a è.txt", tmp_path / "new\nline.txt", tmp_path / "literal%20.txt"]
    for path in paths:
        path.write_text("fixture")
    snapshot = io.MimeSnapshot(urls=tuple(QUrl.fromLocalFile(str(p)).toString() for p in paths))
    result, owned, attached = prepare(snapshot, tmp_path)
    assert attached and owned is None
    assert result.splitlines() == [json.dumps(str(paths[0]), ensure_ascii=False),
                                  json.dumps(str(paths[1])), str(paths[2])]
    assert all(path.read_text() == "fixture" for path in paths)


@pytest.mark.parametrize("text", ["prefix **text** è\n", "/usr/bin/python3 check.py", "/not/a/file",
                                 "C:\\does-not-exist\\copied.txt", "line\nhttps://example.invalid/"])
def test_ordinary_text_is_preserved_exactly(tmp_path, text):
    assert prepare(io.MimeSnapshot(text=text), tmp_path) == (text, None, False)


def test_missing_offered_file_is_error_and_urls_never_download(tmp_path):
    with pytest.raises(ValueError, match="disponibile"):
        prepare(io.MimeSnapshot(urls=("file:///does-not-exist/file",)), tmp_path)
    remote = "https://example.invalid/image.png"
    assert prepare(io.MimeSnapshot(urls=(remote,)), tmp_path) == (remote, None, True)
    assert not list(tmp_path.iterdir())


def test_attachment_path_limits(qapp, tmp_path, monkeypatch):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/tmp/file")] * 65)
    with pytest.raises(ValueError, match="64"):
        io.snapshot_mime(mime)
    file = tmp_path / "file"
    file.touch()
    monkeypatch.setattr(io, "MAX_PATH_CHARS", 1)
    with pytest.raises(ValueError, match="16.384"):
        prepare(io.MimeSnapshot(urls=(QUrl.fromLocalFile(str(file)).toString(),)), tmp_path)


def test_windows_helper_is_static_bounded_and_never_shell_interpolated(monkeypatch):
    monkeypatch.setattr(io.shutil, "which", lambda name: "/synthetic/powershell.exe")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps([r"C:\A $(literal).txt"]).encode())

    monkeypatch.setattr(io.subprocess, "run", run)
    assert io.windows_clipboard_files() == (r"C:\A $(literal).txt",)
    argv, options = calls[0]
    assert "-STA" in argv and "-NoProfile" in argv
    assert "C:\\A" not in argv[-1]
    assert options["timeout"] == 3 and "shell" not in options
    assert options["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize(("code", "expected_error"), [(2, None), (3, ValueError), (1, io.WindowsClipboardUnavailable)])
def test_windows_helper_distinguishes_absent_invalid_unavailable(monkeypatch, code, expected_error):
    monkeypatch.setattr(io.shutil, "which", lambda name: "/synthetic/powershell.exe")
    monkeypatch.setattr(io.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=code, stdout=b""))
    if expected_error:
        with pytest.raises(expected_error):
            io.windows_clipboard_files()
    else:
        assert io.windows_clipboard_files() == ()


def test_windows_files_override_stale_text_but_invalid_files_do_not_fall_back(tmp_path, monkeypatch):
    file = tmp_path / "from-windows.txt"
    file.touch()
    snapshot = io.MimeSnapshot(text="stale text", windows_fallback=True)
    monkeypatch.setattr(io, "windows_clipboard_files", lambda: (str(file),))
    assert prepare(snapshot, tmp_path) == (str(file), None, True)
    monkeypatch.setattr(io, "windows_clipboard_files", lambda: ())
    assert prepare(snapshot, tmp_path) == ("stale text", None, False)

    def invalid():
        raise ValueError("64 file")

    monkeypatch.setattr(io, "windows_clipboard_files", invalid)
    with pytest.raises(ValueError, match="64"):
        prepare(snapshot, tmp_path)

    def unavailable():
        raise io.WindowsClipboardUnavailable("Windows unavailable")

    monkeypatch.setattr(io, "windows_clipboard_files", unavailable)
    assert prepare(snapshot, tmp_path) == ("stale text", None, False)


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        for key, value in {"attachmentReceiver": True, "attachmentBusy": False,
                           "attachmentError": "", "attachmentContext": "project:session",
                           "text": "original", "selectionStart": 8, "selectionEnd": 8,
                           "visible": True, "enabled": True, "readOnly": False}.items():
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


@pytest.mark.parametrize("change", ["context", "text", "selection", "delete"])
def test_pending_image_cannot_replace_changed_or_destroyed_draft(qapp, qtbot, tmp_path, monkeypatch, change):
    bridge = io.AttachmentInputs(tmp_path)
    target = Receiver()
    release = Event()
    original = io.prepare_attachment

    def delayed(*args, **kwargs):
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(io, "prepare_attachment", delayed)
    mime = QMimeData()
    mime.setImageData(tiny_image())
    bridge.submit(target, mime)
    assert target.property("attachmentBusy")
    if change == "delete":
        target.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    else:
        key = {"context": "attachmentContext", "text": "text", "selection": "selectionStart"}[change]
        target.setProperty(key, 0 if change == "selection" else "changed")
    with qtbot.waitSignal(bridge.finished, timeout=3000):
        release.set()
    qtbot.wait(30)
    assert not list(tmp_path.rglob("*.png"))
    if change != "delete":
        assert target.property("text") == ("changed" if change == "text" else "original")
        assert target.property("attachmentError")
    bridge.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_drop_text_never_reads_windows_clipboard(qapp, qtbot, tmp_path, monkeypatch):
    bridge = io.AttachmentInputs(tmp_path)
    bridge._wsl = True
    monkeypatch.setattr(io, "windows_clipboard_files", lambda: pytest.fail("not an explicit paste"))
    target = Receiver()
    mime = QMimeData()
    mime.setText("dropped text")
    bridge.submit(target, mime)
    qtbot.waitUntil(lambda: not target.property("attachmentBusy"))
    assert target.property("text") == "originaldropped text"
    bridge.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_empty_clipboard_never_invokes_insertion_or_replaces_selection(qapp, qtbot, tmp_path, monkeypatch):
    bridge = io.AttachmentInputs(tmp_path)
    target = Receiver()
    target.setProperty("selectionStart", 0)
    target.setProperty("selectionEnd", 8)
    monkeypatch.setattr(io.QMetaObject, "invokeMethod", lambda *args, **kwargs:
                        pytest.fail("Empty paste must not edit the draft"))
    mime = QMimeData()
    mime.setText("")
    bridge.submit(target, mime)
    qtbot.waitUntil(lambda: not target.property("attachmentBusy"))
    assert target.property("text") == "original"
    monkeypatch.setattr(io, "windows_clipboard_files", lambda: ())
    with pytest.raises(ValueError, match="non offrono"):
        prepare(io.MimeSnapshot(windows_fallback=True), tmp_path)
    bridge.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
