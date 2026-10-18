"""Keep reproducibly timestamped QML upgrades out of older Qt bytecode caches."""

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def qml_disk_cache(directory: Path, application: str) -> Iterator[Path | None]:
    """Select a content-specific cache before this process's first QML load.

    Qt 6.10 validates disk bytecode using the source timestamp, which our packages
    intentionally keep reproducible. It reads QML_DISK_CACHE_PATH once at the first
    load. Restore the environment afterward so native child applications and the
    service do not inherit Kira's cache location. Explicit user overrides win.
    """
    if "QML_DISK_CACHE_PATH" in os.environ:
        yield None
        return
    digest = hashlib.sha256(b"Kira QML payload v1\0")
    files = sorted(path for path in directory.rglob("*")
                   if path.is_file() and (path.suffix in (".qml", ".js", ".mjs")
                                          or path.name == "qmldir"))
    for path in files:
        data = path.read_bytes()
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    if not base.is_absolute():
        base = Path.home() / ".cache"
    cache = base / "Kira" / application / "qml-by-payload" / digest.hexdigest()
    os.environ["QML_DISK_CACHE_PATH"] = str(cache)
    try:
        yield cache
    finally:
        os.environ.pop("QML_DISK_CACHE_PATH", None)
