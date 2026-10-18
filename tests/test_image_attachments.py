"""Local generated fixtures only; importing and checking images uses no network."""

import base64
import hashlib
import os
import struct
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from PySide6.QtGui import QColorSpace, QImage

from kira.domain.contracts import KiraError
from kira.service import image_attachments
from kira.service.image_attachments import ImageAttachments, validate_metadata


def make_image(path: Path, *, format: str = "PNG") -> Path:
    image = QImage(12, 8, QImage.Format.Format_RGBA8888)
    image.fill(0xFF317BCC)
    image.setText("Description", "synthetic-private-metadata")
    image.setColorSpace(QColorSpace.NamedColorSpace.SRgb)
    assert image.save(str(path), format)
    return path


def preview(item):
    return Path(unquote(urlsplit(item["preview_url"]).path))


def test_import_is_an_immutable_metadata_free_snapshot(tmp_path):
    source = make_image(tmp_path / "selected.png")
    images = ImageAttachments(tmp_path / "image-attachments")
    item = images.import_file(str(source))
    saved = preview(item)
    original = QImage(str(source))
    normalized = QImage(str(saved))
    assert normalized.size() == original.size()
    assert normalized.pixelColor(2, 3) == original.pixelColor(2, 3)
    assert normalized.textKeys() == [] and not normalized.colorSpace().isValid()
    assert b"synthetic-private-metadata" not in saved.read_bytes()
    assert item["mime"] == "image/png" and item["name"] == "selected.png"
    assert item["width"] == 12 and item["height"] == 8
    assert saved.parent.stat().st_mode & 0o777 == 0o700
    assert saved.stat().st_mode & 0o777 == 0o400
    assert item["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
    source.write_bytes(b"The originally selected file changed after preview")
    part = images.read_parts([item])[0]
    assert part["type"] == "input_image" and part["detail"] == "auto"
    assert base64.b64decode(part["image_url"].split(",", 1)[1]) == saved.read_bytes()
    assert set(item) == {"id", "name", "mime", "width", "height", "bytes", "sha256", "preview_url"}


def test_jpeg_exif_rotation_is_preserved_without_exif(tmp_path):
    source = make_image(tmp_path / "rotated.jpg", format="JPEG")
    tiff = b"II\x2a\0\x08\0\0\0\x01\0" + struct.pack("<HHIHHI", 0x0112, 3, 1, 6, 0, 0)
    exif = b"Exif\0\0" + tiff
    raw = source.read_bytes()
    source.write_bytes(raw[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + raw[2:])
    item = ImageAttachments(tmp_path / "images").import_file(str(source))
    assert (item["width"], item["height"]) == (8, 12)
    assert b"Exif" not in preview(item).read_bytes()


def test_static_gif_is_imported_but_animation_is_rejected(tmp_path):
    header = bytes.fromhex("47494638396101000100800000000000ffffff")
    frame = bytes.fromhex("21f90400000000002c0000000001000100000202440100")
    source = tmp_path / "fixture.gif"
    images = ImageAttachments(tmp_path / "images")
    source.write_bytes(header + frame + b";")
    assert images.import_file(str(source))["width"] == 1
    source.write_bytes(header + frame + frame + b";")
    with pytest.raises(KiraError, match="Animated"):
        images.import_file(str(source))


def test_apng_control_chunk_is_rejected_even_when_decoder_would_show_first_frame(tmp_path):
    source = make_image(tmp_path / "animated.png")
    raw = source.read_bytes()
    body = b"acTL" + struct.pack(">II", 2, 0)
    control = struct.pack(">I", 8) + body + struct.pack(">I", zlib.crc32(body))
    source.write_bytes(raw[:33] + control + raw[33:])
    with pytest.raises(KiraError, match="Animated"):
        ImageAttachments(tmp_path / "images").import_file(str(source))


@pytest.mark.parametrize("format_", ["PNG", "JPEG", "WEBP"])
def test_complete_inputs_import_and_missing_final_bytes_are_rejected(tmp_path, format_):
    source = make_image(tmp_path / "image", format=format_)
    images = ImageAttachments(tmp_path / "images")
    assert images.import_file(str(source))["width"] == 12
    source.write_bytes(source.read_bytes()[:-1])
    with pytest.raises(KiraError, match="incomplete or invalid"):
        images.import_file(str(source))


@pytest.mark.parametrize("kind", ["symlink", "parent_symlink", "fifo", "directory", "missing", "text"])
def test_import_rejects_nonregular_and_nonimage_inputs(tmp_path, kind):
    source = tmp_path / "selected"
    if kind == "symlink":
        source.symlink_to(make_image(tmp_path / "real.png"))
    elif kind == "parent_symlink":
        parent = tmp_path / "real"
        parent.mkdir()
        make_image(parent / "image.png")
        source.symlink_to(parent, target_is_directory=True)
        source = source / "image.png"
    elif kind == "fifo":
        os.mkfifo(source)
    elif kind == "directory":
        source.mkdir()
    elif kind == "text":
        source.write_text("Not an image")
    with pytest.raises(KiraError):
        ImageAttachments(tmp_path / "images").import_file(str(source))


@pytest.mark.parametrize("limit,value", [("MAX_SOURCE_BYTES", 20), ("MAX_PIXELS", 10),
                                       ("MAX_IMAGE_BYTES", 10)])
def test_import_limits_fail_instead_of_resizing_or_truncating(tmp_path, monkeypatch, limit, value):
    source = make_image(tmp_path / "image.png")
    monkeypatch.setattr(image_attachments, limit, value)
    with pytest.raises(KiraError):
        ImageAttachments(tmp_path / "images").import_file(str(source))
    assert not (tmp_path / "images").exists()


@pytest.mark.parametrize("kind", ["changed", "symlink", "public_file", "hardlink", "missing"])
def test_send_rechecks_the_preview_snapshot(tmp_path, kind):
    images = ImageAttachments(tmp_path / "images")
    item = images.import_file(str(make_image(tmp_path / "source.png")))
    path = preview(item)
    if kind == "changed":
        path.chmod(0o600)
        path.write_bytes(b"replacement")
    elif kind == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "source.png")
    elif kind == "public_file":
        path.chmod(0o644)
    elif kind == "hardlink":
        os.link(path, tmp_path / "another-link")
    else:
        path.unlink()
    with pytest.raises(KiraError):
        images.read_parts([item])


def test_private_cache_rejects_symlink_and_public_directory(tmp_path):
    source = make_image(tmp_path / "image.png")
    cache = tmp_path / "images"
    cache.mkdir(mode=0o755)
    images = ImageAttachments(cache)
    with pytest.raises(KiraError, match="private directory"):
        images.import_file(str(source))
    cache.rmdir()
    cache.symlink_to(tmp_path)
    with pytest.raises(KiraError):
        images.import_file(str(source))


def test_metadata_bounds_identity_and_aggregate(tmp_path):
    images = ImageAttachments(tmp_path / "images")
    source = make_image(tmp_path / "image.png")
    items = [images.import_file(str(source)) for _ in range(4)]
    validate_metadata(items)
    for invalid in ([items[0], items[0]], items + [items[0]], [{**items[0], "id": "../escape"}],
                    [{**items[0], "mime": "image/jpeg"}], [{**items[0], "width": True}],
                    [{**items[0], "sha256": "wrong"}]):
        with pytest.raises(KiraError):
            validate_metadata(invalid)
    with pytest.raises(KiraError, match="16 MiB"):
        validate_metadata([{**item, "bytes": 8 * 1024 * 1024} for item in items])


def test_original_modification_during_read_is_rejected(tmp_path, monkeypatch):
    source = make_image(tmp_path / "image.png")
    actual = os.read

    def changed(fd, size):
        result = actual(fd, size)
        if result:
            source.write_bytes(b"changed while read")
        return result

    monkeypatch.setattr(image_attachments.os, "read", changed)
    with pytest.raises(KiraError, match="changed while"):
        ImageAttachments(tmp_path / "images").import_file(str(source))
