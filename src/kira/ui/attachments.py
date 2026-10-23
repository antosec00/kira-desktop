"""GUI-owned local attachment references; never execute or upload their contents."""

from __future__ import annotations

import json
import os
import re
import stat
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from PySide6.QtCore import (
    Q_ARG, QBuffer, QByteArray, QEvent, QIODevice, QMetaObject, QMimeData,
    QObject, QPointF, Qt, QUrl, Signal, Slot,
)
from PySide6.QtGui import QGuiApplication, QImage, QImageReader, QKeyEvent, QKeySequence, QPixmap
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtQml import QQmlEngine
from shiboken6 import isValid

from kira.ui.presentation import refresh_ui, tr

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

MAX_PATHS = 64
MAX_PATH_CHARS = 16_384
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000


class WindowsClipboardUnavailable(ValueError):
    """No reliable Windows clipboard reply; original text may still be pasted."""


@dataclass(frozen=True)
class MimeSnapshot:
    urls: tuple[str, ...] = ()
    text: str = ""
    image: QImage | None = None
    windows_fallback: bool = False
    image_bytes: bytes = b""


def snapshot_mime(mime: QMimeData) -> MimeSnapshot:
    """Copy platform-owned MIME on the GUI thread, before the event expires."""
    urls = mime.urls() if mime.hasUrls() else []
    if any(url.isLocalFile() for url in urls):
        if len(urls) > MAX_PATHS:
            raise ValueError(tr("Puoi inserire al massimo 64 file per volta."))
        return MimeSnapshot(urls=tuple(url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
                                       for url in urls))
    if mime.hasImage():
        value = mime.imageData()
        image = value.toImage() if isinstance(value, QPixmap) else value
        if isinstance(image, QImage) and not image.isNull():
            if image.width() * image.height() > MAX_IMAGE_PIXELS:
                raise ValueError(tr("L'immagine supera il limite di 32 milioni di pixel."))
            return MimeSnapshot(image=image.copy())
    for format_name in ("image/png", "image/jpeg", "image/webp", "image/bmp"):
        if mime.hasFormat(format_name):
            encoded = mime.data(format_name)
            if encoded.isEmpty():
                raise ValueError(tr("L’immagine negli appunti è vuota."))
            if encoded.size() > MAX_IMAGE_BYTES:
                raise ValueError(tr("L'immagine supera il limite di 32 MiB."))
            return MimeSnapshot(image_bytes=encoded.data())
    if urls:
        if len(urls) > MAX_PATHS:
            raise ValueError(tr("Puoi inserire al massimo 64 file per volta."))
        return MimeSnapshot(urls=tuple(url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
                                       for url in urls))
    if mime.hasText():
        return MimeSnapshot(text=mime.text())
    raise ValueError(tr("Questo contenuto non offre testo, file locali o un'immagine utilizzabile."))


def _local_path(value: str, distro: str | None, wsl: bool) -> Path | None:
    if value.startswith("file:"):
        url = QUrl(value)
        if not url.isValid() or not url.isLocalFile():
            raise ValueError(tr("Il percorso del file non è valido."))
        # Decode exactly once, including literal percent filenames. localhost is
        # the local machine under the file URI scheme, not a network file server.
        value = (url.path(QUrl.ComponentFormattingOption.FullyDecoded)
                 if url.host().lower() == "localhost" else url.toLocalFile())
    value = value.strip('"') if value.startswith('"') and value.endswith('"') else value
    windows = re.match(r"^/?([A-Za-z]):[\\/](.*)$", value, re.DOTALL)
    if windows:
        if not wsl:
            raise ValueError(tr("Il percorso Windows richiede una sessione WSL con il disco montato."))
        return Path("/mnt") / windows[1].lower() / windows[2].replace("\\", "/")
    if value.startswith(("\\\\", "//")):
        parts = value.replace("\\", "/").lstrip("/").split("/")
        if (len(parts) < 3 or parts[0].lower() not in ("wsl.localhost", "wsl$")
                or not wsl or not distro or parts[1].casefold() != distro.casefold()):
            raise ValueError(tr("Il percorso di rete non appartiene alla distribuzione WSL corrente."))
        return Path("/") / "/".join(parts[2:])
    if value.startswith("/"):
        return Path(value)
    return None


def _quote_path(path: Path) -> str:
    value = str(path)
    if any(char.isspace() or ord(char) < 32 or char in '\\"' for char in value):
        return json.dumps(value, ensure_ascii=False)
    return value


def _store_image(image: QImage, data: Path) -> Path:
    # Directory-relative exclusive creation prevents following pre-existing image symlinks.
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir("attachments", mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        directory_fd = os.open("attachments", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    name = f"clipboard-{uuid.uuid4().hex}.png"
    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError(tr("La cartella degli allegati deve essere privata (permessi 0700)."))
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):  # type: ignore[call-overload]
            raise ValueError(tr("Non è stato possibile salvare l'immagine come PNG."))
        buffer.close()
        if encoded.size() > MAX_IMAGE_BYTES:
            raise ValueError(tr("L'immagine PNG supera il limite di 32 MiB."))
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded.data())
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink(name, dir_fd=directory_fd)
            raise
        return data / "attachments" / name
    finally:
        os.close(directory_fd)


def prepare_attachment(snapshot: MimeSnapshot, data: Path, *, distro: str | None,
                       wsl: bool) -> tuple[str, Path | None, bool]:
    """Worker-only filesystem checks/encoding; return literal text and owned PNG."""
    image = snapshot.image
    if snapshot.image_bytes:
        buffer = QBuffer()
        buffer.setData(snapshot.image_bytes)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if (not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS):
            raise ValueError(tr("L'immagine non è leggibile o supera 32 milioni di pixel."))
        image = reader.read()
        if image.isNull():
            raise ValueError(tr("L'immagine non è leggibile."))
    if image is not None:
        saved_path = _store_image(image, data)
        return _quote_path(saved_path), saved_path, True
    values = snapshot.urls
    if snapshot.windows_fallback:
        try:
            values = windows_clipboard_files()
        except WindowsClipboardUnavailable:
            if not snapshot.text:
                raise
        if not values and not snapshot.text:
            raise ValueError(tr("Gli appunti non offrono file: usa Allega o Copia come percorso."))
    if not values:
        lines = snapshot.text.splitlines()
        try:
            candidates = [_local_path(line, distro, wsl) for line in lines]
            if not lines or not all(path is not None and path.exists() for path in candidates):
                return snapshot.text, None, False
        except (ValueError, OSError):
            return snapshot.text, None, False
        values = tuple(lines)
    if len(values) > MAX_PATHS:
        raise ValueError(tr("Puoi inserire al massimo 64 file per volta."))
    results = []
    for value in values:
        path = _local_path(value, distro, wsl)
        if path is None:
            # Remote links are literal references; never downloaded as attachments.
            results.append(value)
            continue
        try:
            path = path.resolve(strict=True)
            mode = path.stat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(tr("Il percorso non indica un file o una cartella utilizzabile."))
            access = os.R_OK | (os.X_OK if stat.S_ISDIR(mode) else 0)
            if not os.access(path, access):
                raise ValueError(tr("Il file non è accessibile in lettura."))
        except OSError as error:
            raise ValueError(tr("Il file non è disponibile da Kira: controlla percorso e montaggio.")) \
                from error
        results.append(_quote_path(path))
    text = "\n".join(results)
    if len(text) > MAX_PATH_CHARS:
        raise ValueError(tr("I percorsi superano il limite di 16.384 caratteri."))
    return text, None, True


def prepare_composer_attachment(snapshot: MimeSnapshot, data: Path, *, distro: str | None,
                                wsl: bool) -> tuple[str, list[str], Path | None, bool]:
    """Explicit MIME files/images become candidates; plain text never reads a path."""
    if snapshot.image is not None or snapshot.image_bytes:
        _, owned, _ = prepare_attachment(snapshot, data, distro=distro, wsl=wsl)
        return "", [str(owned)], owned, True
    values = snapshot.urls
    if snapshot.windows_fallback:
        try:
            values = windows_clipboard_files()
        except WindowsClipboardUnavailable:
            if not snapshot.text:
                raise
    if not values:
        return snapshot.text, [], None, False
    # Reuse the established path validation, without interpreting textual path strings.
    text, _, _ = prepare_attachment(MimeSnapshot(urls=values), data, distro=distro, wsl=wsl)
    images, other = [], []
    for value in values:
        path = _local_path(value, distro, wsl)
        if path is not None:
            path = path.resolve(strict=True)
        if (path is not None and path.is_file()
                and (path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
                     or not QImageReader.imageFormat(str(path)).isEmpty())):
            images.append(str(path))
        else:
            other.append(_quote_path(path) if path is not None else value)
    if len(images) > 4:
        raise ValueError(tr("Puoi allegare al massimo 4 immagini per messaggio."))
    return "\n".join(other) if images else text, images, None, True


def _remove_owned(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # A retained private clipboard PNG is preferable to raising from a GUI callback.
        pass


def windows_clipboard_files() -> tuple[str, ...]:
    """Explicit-paste fallback for Windows CF_HDROP omitted by WSLg; no file contents."""
    executable = shutil.which("powershell.exe")
    if executable is None:
        candidate = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise WindowsClipboardUnavailable(tr("Windows non espone questi file: usa Allega o Copia come percorso."))
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$files = [System.Windows.Forms.Clipboard]::GetFileDropList()
if ($files.Count -eq 0) { exit 2 }
if ($files.Count -gt 64) { exit 3 }
$total = 0
$paths = @()
foreach ($file in $files) {
    $total += $file.Length
    if ($total -gt 16384) { exit 3 }
    $paths += [string]$file
}
ConvertTo-Json -InputObject $paths -Compress
"""
    try:
        result = subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
                                 "-Command", script], check=False, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=3)
        if result.returncode == 2:
            return ()
        if result.returncode == 3 or len(result.stdout) > 131_072:
            raise ValueError(tr("Gli appunti superano il limite di 64 file o 16.384 caratteri."))
        if result.returncode:
            raise WindowsClipboardUnavailable(tr("Gli appunti Windows non sono disponibili: usa Allega."))
        values = json.loads(result.stdout.decode("utf-8-sig"))
        if (not isinstance(values, list) or not 1 <= len(values) <= MAX_PATHS
                or not all(isinstance(value, str) for value in values)
                or sum(len(value) for value in values) > MAX_PATH_CHARS):
            raise ValueError(tr("Il formato dei percorsi Windows non è utilizzabile."))
        return tuple(values)
    except (OSError, subprocess.TimeoutExpired, UnicodeError, json.JSONDecodeError) as error:
        raise WindowsClipboardUnavailable(tr("Gli appunti Windows non sono disponibili: usa Allega o Copia come percorso.")) \
            from error


class AttachmentInputs(QObject):
    """Bridge shared by marked text editors, including popup question answers."""

    finished = Signal(object)
    imagesPrepared = Signal(object)

    def __init__(self, data: Path, parent: QObject | None = None, *,
                 workspace: "Workspace | None" = None) -> None:
        super().__init__(parent)
        self.data = data
        self.workspace = workspace
        self._errors: WeakKeyDictionary[QObject, str] = WeakKeyDictionary()
        self._engine = parent if isinstance(parent, QQmlEngine) else None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kira-attachments")
        self._distro = os.environ.get("WSL_DISTRO_NAME")
        self._wsl = bool(self._distro or "microsoft" in os.uname().release.lower())
        self.finished.connect(self._finish)
        self.imagesPrepared.connect(self._finish_images)
        pool = self._pool
        self.destroyed.connect(lambda: pool.shutdown(wait=False, cancel_futures=True))

    def _set_error(self, target: QObject, message: str) -> None:
        message = refresh_ui(message)
        if message:
            self._errors[target] = message
        else:
            self._errors.pop(target, None)
        target.setProperty("attachmentError", message)

    @Slot()
    def retranslate(self) -> None:
        for target, message in list(self._errors.items()):
            if not isValid(target) or target.property("attachmentError") != message:
                self._errors.pop(target, None)
                continue
            self._set_error(target, message)

    def _receiver(self, obj: QObject | None) -> QObject | None:
        while obj is not None:
            if obj.property("attachmentReceiver") is True:
                context = QQmlEngine.contextForObject(obj)
                if (obj.property("visible") and obj.property("enabled")
                        and (self._engine is None or (context and context.engine() == self._engine))):
                    return obj
                return None
            obj = obj.parentItem() if isinstance(obj, QQuickItem) else obj.parent()
        return None

    def _at(self, window: QQuickWindow, position: QPointF) -> QObject | None:
        def visit(item: QQuickItem, point: QPointF, depth: int = 0) -> tuple[QObject | None, bool]:
            if not item.isVisible() or not item.isEnabled() or depth > 100:
                return None, False
            inside = item.contains(point)
            if item.clip() and not inside:
                return None, False
            children = sorted(enumerate(item.childItems()),
                              key=lambda pair: (pair[1].z(), pair[0]), reverse=True)
            for _, child in children:
                receiver, blocked = visit(child, child.mapFromItem(item, point), depth + 1)
                if receiver is not None or blocked:
                    return receiver, blocked
            if inside and item.property("attachmentReceiver") is True:
                return self._receiver(item), True
            # A popup's empty chrome blocks editors underneath. Passive full-window
            # Overlay/Loader items do not, unlike Qt childAt's first-item lookup.
            if inside and "PopupItem" in item.metaObject().className():
                return None, True
            return None, False

        def modal(item: QQuickItem) -> QQuickItem | None:
            if not item.isVisible():
                return None
            for child in reversed(sorted(item.childItems(), key=lambda entry: entry.z())):
                if found := modal(child):
                    return found
            parent = item.parent()
            if ("PopupItem" in item.metaObject().className() and parent is not None
                    and parent.property("modal") is True):
                return item
            return None

        root = window.contentItem()
        if popup := modal(root):
            receiver, _ = visit(popup, popup.mapFromItem(root, position))
        else:
            receiver, _ = visit(root, position)
        return receiver

    @Slot(QObject)
    def paste(self, target: QObject) -> None:
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData() if clipboard is not None else None
        if mime is not None:
            self.submit(target, mime, windows_clipboard=not clipboard.ownsClipboard())

    @Slot(QObject, "QVariantList")
    def importUrls(self, target: QObject, urls: list[Any]) -> None:
        mime = QMimeData()
        mime.setUrls([value if isinstance(value, QUrl) else QUrl(str(value)) for value in urls])
        self.submit(target, mime)

    def submit(self, target: QObject, mime: QMimeData, *,
               windows_clipboard: bool = False) -> None:
        if not isValid(target) or self._receiver(target) is not target:
            return
        if target.property("attachmentBusy") or target.property("readOnly"):
            return
        self._set_error(target, "")
        try:
            if (windows_clipboard and self._wsl and not mime.hasUrls() and not mime.hasImage()
                    and not any(name.startswith("image/") for name in mime.formats())):
                snapshot = MimeSnapshot(text=mime.text(), windows_fallback=True)
            else:
                snapshot = snapshot_mime(mime)
        except ValueError as error:
            self._set_error(target, str(error))
            return
        selection = (target.property("selectionStart"), target.property("selectionEnd"))
        target.setProperty("attachmentBusy", True)
        state = (target.property("attachmentContext"), target.property("text"),
                 target.property("selectionStart"), target.property("selectionEnd"))
        if target.property("imageAttachments") is True and self.workspace is not None:
            self._submit_images(target, state, selection, snapshot)
            return
        future = self._pool.submit(prepare_attachment, snapshot, self.data,
                                   distro=self._distro, wsl=self._wsl)

        def done(result: Any) -> None:
            try:
                text, owned_path, is_attachment = result.result()
                error = ""
            except Exception as failure:
                text, owned_path, is_attachment = "", None, False
                error = str(failure) if isinstance(failure, ValueError) else (
                    tr("Non è stato possibile inserire l'allegato: verifica spazio e permessi."))
            try:
                if isValid(self):
                    self.finished.emit((target, state, selection, text, owned_path, error,
                                        is_attachment))
                elif owned_path:
                    _remove_owned(owned_path)
            except RuntimeError:
                if owned_path:
                    _remove_owned(owned_path)

        future.add_done_callback(done)

    def _submit_images(self, target: QObject, state: tuple[Any, ...], selection: tuple[Any, ...],
                       snapshot: MimeSnapshot) -> None:
        future = self._pool.submit(prepare_composer_attachment, snapshot, self.data,
                                   distro=self._distro, wsl=self._wsl)

        def done(result: Any) -> None:
            owned = None
            try:
                text, paths, owned, attached = result.result()
                error = ""
            except Exception as failure:
                text, paths, attached = "", [], False
                error = str(failure) if isinstance(failure, ValueError) else tr("Non è stato possibile preparare l'immagine.")
            try:
                if isValid(self):
                    self.imagesPrepared.emit((target, state, selection, text, paths, owned, attached, error))
                elif owned:
                    _remove_owned(owned)
            except RuntimeError:
                if owned:
                    _remove_owned(owned)

        future.add_done_callback(done)

    @Slot(object)
    def _finish_images(self, result: tuple[Any, ...]) -> None:
        target, state, selection, text, paths, owned, attached, error = result

        def current() -> bool:
            return bool(isValid(target) and self._receiver(target) is target
                        and state == (target.property("attachmentContext"), target.property("text"),
                                      target.property("selectionStart"), target.property("selectionEnd")))

        def finish(message: str = "") -> None:
            if owned:
                _remove_owned(owned)
            if isValid(target):
                target.setProperty("attachmentBusy", False)
                if message:
                    self._set_error(target, message)

        if not current():
            finish(tr("Il testo è cambiato: incolla di nuovo l'allegato."))
            return
        if error:
            finish(error)
            return
        workspace = self.workspace
        if workspace is None:
            finish()
            return
        context = (str(workspace.project.get("id", "")), workspace.sessionId)
        if len(workspace.draft_image_ids()) + len(paths) > 4:
            finish(tr("Puoi allegare al massimo 4 immagini per messaggio."))
            return
        if paths and not workspace.connected:
            finish(tr("Attendi la connessione al servizio prima di allegare immagini."))
            return
        if paths and not workspace.imageInputsEnabled:
            finish(tr("Il modello selezionato non supporta immagini. Cambia modello o rimuovi le immagini prima di inviare."))
            return
        remaining = list(paths)

        def next_image() -> None:
            if not current() or context != (str(workspace.project.get("id", "")), workspace.sessionId):
                finish(tr("La conversazione è cambiata: scegli di nuovo l'allegato."))
                return
            if not remaining:
                finish()
                if text:
                    QMetaObject.invokeMethod(target, "select", Qt.ConnectionType.DirectConnection,
                                             Q_ARG(int, selection[0]), Q_ARG(int, selection[1]))
                    QMetaObject.invokeMethod(target, "insertAttachment" if attached else "insertPlainText",
                                             Qt.ConnectionType.DirectConnection, Q_ARG("QVariant", text))
                return
            path = remaining.pop(0)

            def received(reply: dict[str, Any]) -> None:
                if not current() or context != (str(workspace.project.get("id", "")), workspace.sessionId):
                    finish(tr("La conversazione è cambiata: scegli di nuovo l'allegato."))
                    return
                metadata = reply.get("attachment", {})
                if not isinstance(metadata, dict) or not workspace.addDraftImage(metadata, context):
                    finish(tr("L'immagine non è stata aggiunta. Verifica formato, dimensione e spazio disponibile."))
                    return
                next_image()

            workspace.client.request("attachment.import", {"path": path}, received,
                lambda failure: finish(str(failure.get("message") or tr("Impossibile importare l'immagine."))))

        next_image()

    @Slot(object)
    def _finish(self, result: tuple[Any, ...]) -> None:
        target, state, selection, text, owned_path, error, is_attachment = result
        valid = isValid(target)
        if valid:
            current = (target.property("attachmentContext"), target.property("text"),
                       target.property("selectionStart"), target.property("selectionEnd"))
            valid = current == state and self._receiver(target) is target
            target.setProperty("attachmentBusy", False)
        if not valid:
            if owned_path:
                _remove_owned(owned_path)
            if isValid(target):
                self._set_error(target, tr("Il testo è cambiato: incolla di nuovo l'allegato."))
            return
        if error:
            self._set_error(target, error)
            return
        if not text:
            return
        QMetaObject.invokeMethod(target, "select", Qt.ConnectionType.DirectConnection,
                                 Q_ARG(int, selection[0]), Q_ARG(int, selection[1]))
        method = "insertAttachment" if is_attachment else "insertPlainText"
        QMetaObject.invokeMethod(target, method, Qt.ConnectionType.DirectConnection,
                                 Q_ARG("QVariant", text))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            if isinstance(event, QKeyEvent) and event.matches(QKeySequence.StandardKey.Paste):
                target = self._receiver(QGuiApplication.focusObject())
                if target is not None:
                    event.accept()
                    if kind == QEvent.Type.KeyPress:
                        self.paste(target)
                    return True
        if isinstance(watched, QQuickWindow) and kind in (
                QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            target = self._at(watched, event.position())  # type: ignore[attr-defined]
            if target is not None:
                event.setDropAction(Qt.DropAction.CopyAction)  # type: ignore[attr-defined]
                event.accept()
                if kind == QEvent.Type.Drop:
                    self.submit(target, event.mimeData())  # type: ignore[attr-defined]
                return True
        return False
