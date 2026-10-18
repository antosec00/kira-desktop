"""Compile bundled JSON messages to native Qt catalogs without Python callbacks.

Generated catalogs are private, content-addressed cache files. Source catalogs
remain readable JSON in the application package; no generated binary is tracked.
"""

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import PySide6
from PySide6.QtCore import QLibraryInfo

MAX_BYTES = 16 * 1024 * 1024
QM_MAGIC = bytes.fromhex("3cb86418caef9c95cd211cbf60a1bddd")


class CatalogCompilationError(RuntimeError):
    """Native translation preparation failed; the caller can retain its locale."""


def translation_contexts() -> list[str]:
    qml = Path(__file__).with_name("qml")
    return sorted({"", "Kira", "QPlatformTheme", "QDialogButtonBox", "QQuickDialogButtonBox",
                   "QFileDialog", "QQuickFileDialog", "QQuickFileDialogImpl",
                   "QQuickFolderDialog", "QQuickFolderDialogImpl", "QQuickPlatformDialog",
                   *(path.stem for path in qml.rglob("*")
                     if path.suffix in {".qml", ".js", ".mjs"})})


def _cache_directory() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    if not base.is_absolute():
        base = Path.home() / ".cache"
    directory = base / "kira-desktop" / "translations"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise CatalogCompilationError("Translation cache must be a private user-owned directory")
    directory.chmod(0o700)
    return directory


def _read_private(path: Path, maximum: int) -> bytes | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CatalogCompilationError("Translation cache file cannot be read safely") from error
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > maximum):
            raise CatalogCompilationError("Translation cache file is not a private regular file")
        return stream.read(maximum + 1)


def compiled_catalog(code: str, catalog: dict[str, str]) -> Path:
    """Return a native QM for exactly these messages, contexts and Qt version.

The only executable is lrelease shipped with the pinned PySide6 installation.
On a cache miss this is a bounded blocking operation; prepare it before first
QML load or off the GUI thread for an interactive language change.
"""
    contexts = translation_contexts()
    if (not isinstance(code, str) or len(code) > 24 or not isinstance(catalog, dict)
            or len(catalog) > 10000 or any(not isinstance(k, str) or not isinstance(v, str)
                                         for k, v in catalog.items())):
        raise CatalogCompilationError("Invalid application translation input")
    payload = json.dumps({"version": 1, "code": code,
                          "qt": QLibraryInfo.version().toString(),
                          "contexts": contexts, "messages": catalog},
                         sort_keys=True, ensure_ascii=False).encode()
    if len(payload) > 4 * 1024 * 1024:
        raise CatalogCompilationError("Application translation input exceeds its size limit")
    key = hashlib.sha256(payload).hexdigest()
    directory = _cache_directory()
    target = directory / (key + ".qm")
    checksum = directory / (key + ".sha256")
    existing = _read_private(target, MAX_BYTES)
    digest = _read_private(checksum, 128)
    if (existing and existing.startswith(QM_MAGIC) and digest
            and hashlib.sha256(existing).hexdigest().encode() == digest.strip()):
        return target

    root = ET.Element("TS", {"version": "2.1", "language": code, "sourcelanguage": "it"})
    for name in contexts:
        context = ET.SubElement(root, "context")
        ET.SubElement(context, "name").text = name
        for source, translated in sorted(catalog.items()):
            message = ET.SubElement(context, "message")
            ET.SubElement(message, "source").text = source
            ET.SubElement(message, "translation").text = translated
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(xml) > MAX_BYTES:
        raise CatalogCompilationError("Compiled translation source exceeds its size limit")
    binary = Path(PySide6.__file__).parent / "lrelease"
    if not binary.is_file():
        raise CatalogCompilationError("The installed Qt translation compiler is unavailable")
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "SYSTEMROOT", "TMPDIR"}}
    with tempfile.TemporaryDirectory(prefix=".compile-", dir=directory) as temporary:
        scratch = Path(temporary)
        source_file, output_file = scratch / "catalog.ts", scratch / "catalog.qm"
        source_file.write_bytes(xml)
        try:
            subprocess.run([str(binary), "-silent", str(source_file), "-qm", str(output_file)],
                           cwd=scratch, env=environment, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise CatalogCompilationError("Native application translation compilation failed") from error
        if not output_file.is_file() or output_file.stat().st_size > MAX_BYTES:
            raise CatalogCompilationError("Invalid native application translation output")
        data = output_file.read_bytes()
        if not data.startswith(QM_MAGIC):
            raise CatalogCompilationError("Native application translation output has an invalid header")
        output_file.chmod(0o600)
        digest_file = scratch / "catalog.sha256"
        digest_file.write_text(hashlib.sha256(data).hexdigest() + "\n")
        digest_file.chmod(0o600)
        os.replace(output_file, target)
        os.replace(digest_file, checksum)
    return target
