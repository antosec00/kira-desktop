"""Compile bundled JSON messages to native Qt catalogs without Python callbacks.

Generated catalogs are private, content-addressed cache files. Source catalogs
remain readable JSON in the application package; no generated binary is tracked.
Each translation context receives only the messages it can look up, so the
compiled size follows the catalog size instead of catalog size times contexts.
"""

import ast
import functools
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Collection
from pathlib import Path

import PySide6
from PySide6.QtCore import QLibraryInfo

MAX_BYTES = 16 * 1024 * 1024
QM_MAGIC = bytes.fromhex("3cb86418caef9c95cd211cbf60a1bddd")
UI_ROOT = Path(__file__).parent
#: Contexts that can look up any message: Python tr() (templates and computed
#: labels such as run states) and QML created from data without a file URL.
FULL_CONTEXTS = frozenset({"", "Kira"})
#: Qt's own controls and dialogs translate their English labels in these contexts.
QT_CONTEXTS = frozenset({"QPlatformTheme", "QDialogButtonBox", "QQuickDialogButtonBox",
                         "QFileDialog", "QQuickFileDialog", "QQuickFileDialogImpl",
                         "QQuickFolderDialog", "QQuickFolderDialogImpl", "QQuickPlatformDialog"})
QML_SUFFIXES = frozenset({".qml", ".js", ".mjs"})
ALL_MESSAGES = "*"
_QML_CALL = re.compile(rb"\bqsTr\s*\(")
_PYTHON_CALL = re.compile(rb"\b(?:tr|translate_ui|UiText)\s*\(")
# A complete first argument: one plain string literal followed by "," or ")".
_LITERAL_ARGUMENT = re.compile(rb"""\s*("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')\s*[,)]""")


class CatalogCompilationError(RuntimeError):
    """Native translation preparation failed; the caller can retain its locale."""


def _literal_calls(source: bytes, call: re.Pattern[bytes]) -> tuple[frozenset[str], bool]:
    """Each call's literal first argument, and whether any call computes its text."""
    literals: set[str] = set()
    computed = False
    for match in call.finditer(source):
        argument = _LITERAL_ARGUMENT.match(source, match.end())
        value: object = None
        if argument is not None:
            try:
                value = ast.literal_eval(argument.group(1).decode("utf-8"))
            except (SyntaxError, UnicodeError, ValueError):
                value = None
        if isinstance(value, str):
            literals.add(value)
        else:
            computed = True
    return frozenset(literals), computed


@functools.cache
def _application_messages(qml: Path, python: Path) -> tuple[
        dict[str, tuple[frozenset[str], bool]], frozenset[str]]:
    """Literal qsTr() messages per QML context and literal Python interface messages.

    A QML context also records whether one of its calls translates computed text
    (for example a transcript status); such a context needs the whole catalog.
    """
    contexts: dict[str, tuple[frozenset[str], bool]] = {}
    for path in sorted(qml.rglob("*")):
        if path.suffix not in QML_SUFFIXES:
            continue
        try:
            literals, computed = _literal_calls(path.read_bytes(), _QML_CALL)
        except OSError:
            literals, computed = frozenset(), True
        previous, previous_computed = contexts.get(path.stem, (frozenset(), False))
        contexts[path.stem] = previous | literals, previous_computed or computed
    messages: set[str] = set()
    for path in sorted(python.rglob("*.py")):
        try:
            messages.update(_literal_calls(path.read_bytes(), _PYTHON_CALL)[0])
        except OSError:
            continue  # An unread module only leaves more messages in the Qt contexts.
    return contexts, frozenset(messages)


def translation_contexts() -> list[str]:
    qml, _python = _application_messages(UI_ROOT / "qml", UI_ROOT)
    return sorted(FULL_CONTEXTS | QT_CONTEXTS | set(qml))


def context_messages(catalog: dict[str, str], native: Collection[str] = ()) -> dict[str, str | list[str]]:
    """Map each context to the catalog sources it can look up; ALL_MESSAGES selects all.

    A QML file's context holds its literal qsTr() messages. Qt's contexts hold the
    catalog entries that no application literal uses (Qt's English labels and any
    unread literal, so a misread can only add entries) plus the locale's native
    label sources, which therefore stay available even when an application
    literal shares their text.
    """
    qml, python = _application_messages(UI_ROOT / "qml", UI_ROOT)
    literal = python.union(*(messages for messages, _computed in qml.values()))
    available = set(catalog)
    qt_labels = (available - literal) | (available & set(native))
    selection: dict[str, str | list[str]] = {}
    for name in sorted(FULL_CONTEXTS | QT_CONTEXTS | set(qml)):
        messages, computed = qml.get(name, (frozenset(), False))
        if name in FULL_CONTEXTS or computed:
            selection[name] = ALL_MESSAGES
            continue
        sources = available & messages
        if name in QT_CONTEXTS:
            sources |= qt_labels
        selection[name] = sorted(sources)
    return selection


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


def compiled_catalog(code: str, catalog: dict[str, str], native: Collection[str] = ()) -> Path:
    """Return a native QM for exactly these messages, contexts and Qt version.

The only executable is lrelease shipped with the pinned PySide6 installation.
On a cache miss this is a short bounded blocking operation: prepare it before
the first QML load, and before persisting an interactive language change. The
content-addressed cache keeps every later load of the same catalog cheap.
`native` names the sources of the locale's standard Qt labels (see
context_messages).
"""
    if (not isinstance(code, str) or len(code) > 24 or not isinstance(catalog, dict)
            or len(catalog) > 10000 or any(not isinstance(k, str) or not isinstance(v, str)
                                         for k, v in catalog.items())
            or isinstance(native, str) or len(native) > 10000
            or any(not isinstance(source, str) for source in native)):
        raise CatalogCompilationError("Invalid application translation input")
    selection = context_messages(catalog, native)
    # The key covers each context's exact selection; digests keep the 4 MiB
    # bound about the catalog itself rather than about its repetitions.
    contexts = {name: sources if sources == ALL_MESSAGES else hashlib.sha256(
        json.dumps(sources, ensure_ascii=False).encode()).hexdigest()
        for name, sources in selection.items()}
    payload = json.dumps({"version": 2, "code": code,
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
    for name, selected in selection.items():
        sources = sorted(catalog) if selected == ALL_MESSAGES else selected
        if not sources:
            continue
        context = ET.SubElement(root, "context")
        ET.SubElement(context, "name").text = name
        for source in sources:
            message = ET.SubElement(context, "message")
            ET.SubElement(message, "source").text = source
            ET.SubElement(message, "translation").text = catalog[source]
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
