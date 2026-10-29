"""Bounded, volatile live browser images; no webpage data is written to disk."""

from __future__ import annotations

import base64
import binascii
import threading
from typing import Any

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtQuick import QQuickImageProvider

MAX_FRAME_BYTES = 600_000
MAX_FRAME_EDGE = 2560
MAX_FRAME_PIXELS = 4_000_000


def decode_frame(payload: dict[str, Any]) -> QImage | None:
    """Check compressed and decoded budgets before allocating the image."""
    width, height, data = payload.get("width"), payload.get("height"), payload.get("data")
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= MAX_FRAME_EDGE or not 1 <= height <= MAX_FRAME_EDGE
            or width * height > MAX_FRAME_PIXELS or payload.get("mime") != "image/jpeg"
            or not isinstance(data, str) or len(data) > MAX_FRAME_BYTES * 4 // 3 + 4):
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw or len(raw) > MAX_FRAME_BYTES:
        return None
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"jpeg")
    size = reader.size()
    if size != QSize(width, height):
        return None
    image = reader.read()
    return None if image.isNull() else image


class BrowserFrameStore:
    """Python-owned storage survives replacement of the Qt engine/provider."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._image = QImage()
        self._key = ""

    def publish(self, key: str, image: QImage) -> None:
        with self._lock:
            self._key, self._image = key, image

    def clear(self) -> None:
        with self._lock:
            self._key, self._image = "", QImage()

    def image(self, key: str) -> QImage:
        with self._lock:
            if key == self._key or (key.startswith("live-") and self._key.startswith("live-")):
                return self._image
            return QImage()


class BrowserFrames(QQuickImageProvider):
    """One engine-owned image provider sharing a volatile Python-owned store."""

    def __init__(self, store: BrowserFrameStore | None = None) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.store = store if store is not None else BrowserFrameStore()

    def publish(self, key: str, image: QImage) -> None:
        self.store.publish(key, image)

    def clear(self) -> None:
        self.store.clear()

    def requestImage(self, identifier: str, size: QSize, requestedSize: QSize) -> QImage:
        del requestedSize
        image = self.store.image(identifier)
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image
