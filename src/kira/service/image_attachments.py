"""Explicit, bounded local image imports; only immutable PNG bytes reach providers.

Importing prepares a private preview and does not perform any network operation.
The service owns attachment metadata and durable run links; this module never
stores data URLs or source paths in SQLite, events, or sidecar files.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import zlib
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColorSpace, QImage, QImageReader

from kira.domain.contracts import Json, KiraError

MAX_IMAGES = 4
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
SUPPORTED_FORMATS = frozenset({b"png", b"jpeg", b"webp", b"gif"})


def _error(message: str) -> KiraError:
    return KiraError("INVALID_ATTACHMENT", message)


def _directory(path: Path) -> int:
    """Open a directory by components, refusing symlinks including ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise _error("The image path must be absolute and contain no parent traversal")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _bytes(fd: int, limit: int, *, private: bool = False) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise _error("Only regular local image files are supported")
    if private and (before.st_uid != os.getuid() or before.st_mode & 0o077
                    or before.st_nlink != 1):
        raise _error("The saved image is not a private owned file")
    if not 0 < before.st_size <= limit:
        raise _error("The image file is empty or exceeds its byte limit")
    chunks = bytearray()
    while len(chunks) <= limit:
        chunk = os.read(fd, min(1024 * 1024, limit + 1 - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
    after = os.fstat(fd)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise _error("The image changed while being read; attach it again")
    if len(chunks) != before.st_size or len(chunks) > limit:
        raise _error("The image is incomplete or exceeds its byte limit")
    return bytes(chunks)


def _check_container(raw: bytes, format_: bytes) -> None:
    """Reject incomplete files even when an image plugin tolerates missing tails."""
    if format_ == b"png":
        position = 8
        first = True
        while position + 12 <= len(raw):
            length = int.from_bytes(raw[position:position + 4], "big")
            end = position + length + 12
            kind = raw[position + 4:position + 8]
            if end > len(raw) or first and (kind != b"IHDR" or length != 13) \
                    or zlib.crc32(raw[position + 4:end - 4]) != int.from_bytes(raw[end - 4:end], "big"):
                break
            # Some readers expose APNG's first frame without reporting animation.
            if kind == b"acTL":
                raise _error("Animated images are not supported; choose a single still image")
            if kind == b"IEND":
                if length == 0 and end == len(raw):
                    return
                break
            first = False
            position = end
    elif format_ == b"jpeg" and raw.endswith(b"\xff\xd9"):
        return
    elif format_ == b"gif" and raw.endswith(b";"):
        return
    elif format_ == b"webp" and len(raw) >= 12 and int.from_bytes(raw[4:8], "little") + 8 == len(raw):
        return
    raise _error("The image container is incomplete or invalid; attach the complete image")


def _normalize(raw: bytes) -> tuple[bytes, int, int]:
    source = QBuffer()
    source.setData(QByteArray(raw))
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    format_ = bytes(reader.format().data()).lower()
    if format_ not in SUPPORTED_FORMATS:
        raise _error("Attach a PNG, JPEG, WebP, or non-animated GIF image")
    _check_container(raw, format_)
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_PIXELS:
        raise _error("The image has invalid dimensions or exceeds 32 million pixels")
    if reader.imageCount() > 1:
        raise _error("Animated images are not supported; choose a single still image")
    decoded = reader.read()
    if decoded.isNull():
        raise _error("The image could not be decoded; attach a complete supported image")
    if reader.supportsAnimation() and reader.canRead():
        raise _error("Animated images are not supported; choose a single still image")
    width, height = decoded.width(), decoded.height()
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise _error("The decoded image exceeds the pixel limit")
    if decoded.colorSpace().isValid():
        decoded = decoded.convertedToColorSpace(QColorSpace.NamedColorSpace.SRgb)
    # A new image copies sRGB pixels, not text chunks, EXIF, ICC profiles or comments.
    # AutoTransform above preserves the visible orientation without resizing.
    pixels = decoded.convertToFormat(QImage.Format.Format_RGBA8888)
    normalized = QImage(width, height, QImage.Format.Format_RGBA8888)
    if normalized.isNull() or pixels.isNull():
        raise _error("The image could not be prepared within local memory limits")
    cast(memoryview, normalized.bits())[:] = pixels.constBits()
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not normalized.save(output, "PNG"):  # type: ignore[call-overload]
        raise _error("The image could not be saved as a private PNG")
    data = bytes(output.data().data())
    if len(data) > MAX_IMAGE_BYTES:
        raise _error("The prepared PNG exceeds 8 MiB; choose a smaller image")
    return data, width, height


def validate_metadata(attachments: list[Json]) -> None:
    """Validate the service-owned metadata for one user message."""
    if not isinstance(attachments, list) or len(attachments) > MAX_IMAGES:
        raise _error("A message can contain at most four images")
    seen: set[str] = set()
    total = 0
    for item in attachments:
        if not isinstance(item, dict):
            raise _error("Invalid image attachment metadata")
        identity, digest = item.get("id"), item.get("sha256")
        try:
            valid_id = isinstance(identity, str) and str(UUID(identity)) == identity
        except ValueError:
            valid_id = False
        if not valid_id or not isinstance(identity, str) or identity in seen:
            raise _error("Image attachment identifiers must be unique UUIDs")
        if item.get("mime") != "image/png" or not isinstance(digest, str) \
                or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise _error("Invalid saved image type or fingerprint")
        width, height, size = item.get("width"), item.get("height"), item.get("bytes")
        if type(width) is not int or type(height) is not int or min(width, height) <= 0 \
                or width * height > MAX_PIXELS or type(size) is not int \
                or not 0 < size <= MAX_IMAGE_BYTES:
            raise _error("Invalid saved image dimensions or byte size")
        seen.add(identity)
        total += size
    if total > MAX_MESSAGE_BYTES:
        raise _error("The images in one message exceed 16 MiB")


class ImageAttachments:
    def __init__(self, root: Path):
        self.root = root

    def _cache(self) -> int:
        parent_fd = _directory(self.root.parent)
        try:
            try:
                os.mkdir(self.root.name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            fd = os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                         | os.O_CLOEXEC, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(fd)
            raise _error("The image cache must be a private directory owned by this user")
        return fd

    def import_file(self, path: str) -> Json:
        if not isinstance(path, str) or not path or len(path) > 4096 or "\0" in path:
            raise _error("Select a valid local image path")
        source = Path(path)
        if not source.is_absolute() or not source.name or ".." in source.parts:
            raise _error("Select an absolute local image path without parent traversal")
        try:
            parent_fd = _directory(source.parent)
            try:
                fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                             | os.O_NONBLOCK, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
            try:
                data, width, height = _normalize(_bytes(fd, MAX_SOURCE_BYTES))
            finally:
                os.close(fd)
            identity = str(uuid4())
            name = identity + ".png"
            cache_fd = self._cache()
            try:
                output_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                    | os.O_CLOEXEC, 0o400, dir_fd=cache_fd)
                try:
                    view = memoryview(data)
                    while view:
                        count = os.write(output_fd, view)
                        if count <= 0:
                            raise OSError("Incomplete image cache write")
                        view = view[count:]
                    os.fsync(output_fd)
                except BaseException:
                    os.unlink(name, dir_fd=cache_fd)
                    raise
                finally:
                    os.close(output_fd)
                os.fsync(cache_fd)
            finally:
                os.close(cache_fd)
            return {"id": identity, "name": source.name[:255], "mime": "image/png",
                    "width": width, "height": height, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "preview_url": (self.root / name).as_uri()}
        except OSError as exc:
            raise _error("The image cannot be opened or saved as a private regular file") from exc

    def read_parts(self, attachments: list[Json]) -> list[Json]:
        """Resolve verified local snapshots immediately before a provider request."""
        validate_metadata(attachments)
        if not attachments:
            return []
        try:
            cache_fd = self._cache()
            try:
                result = []
                for item in attachments:
                    fd = os.open(item["id"] + ".png", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                                 | os.O_NONBLOCK, dir_fd=cache_fd)
                    try:
                        data = _bytes(fd, MAX_IMAGE_BYTES, private=True)
                    finally:
                        os.close(fd)
                    if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                        raise _error("The saved preview changed or is incomplete; attach it again")
                    result.append({"type": "input_image", "image_url": "data:image/png;base64,"
                                   + base64.b64encode(data).decode("ascii"), "detail": "auto"})
                return result
            finally:
                os.close(cache_fd)
        except OSError as exc:
            raise _error("A saved image is missing or no longer a private regular file") from exc
