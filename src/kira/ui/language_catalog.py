"""Bounded, local-only language registry and plain JSON translation catalogs."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from typing import Any

CATALOG_ROOT = Path(__file__).with_name("translations")
#: A locale's standard Qt labels (dialog and file-picker buttons), keyed by Qt's
#: English source text.
NATIVE_LABELS = "qt.json"
MAX_CATALOG_BYTES = 1024 * 1024
MAX_LANGUAGE_BYTES = 4 * MAX_CATALOG_BYTES
MAX_CATALOG_ENTRIES = 10000
_CODE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}")


class CatalogError(ValueError):
    """Invalid bundled language data; never a reason to access external files."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result and result[key] != value:
            raise CatalogError("Conflicting duplicate translation or registry key")
        result[key] = value
    return result


def _read_json(path: Path, limit: int) -> Any:
    if path.is_symlink() or not path.resolve().is_relative_to(CATALOG_ROOT.resolve()):
        raise CatalogError("Language data must be bundled regular files")
    try:
        if not path.is_file():
            raise CatalogError("Language data must be bundled regular files")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise CatalogError("Bundled language data exceeds its size limit")
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, CatalogError):
            raise
        raise CatalogError(f"Invalid bundled language data: {path.name}") from error


def load_languages() -> list[dict[str, str]]:
    """Validate registry entries; adding a locale requires data, not application code."""
    data = _read_json(CATALOG_ROOT / "languages.json", 16384)
    if (not isinstance(data, dict) or set(data) != {"version", "languages"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["languages"], list) or not 1 <= len(data["languages"]) <= 64):
        raise CatalogError("Invalid bundled language registry")
    result = []
    codes = set()
    for entry in data["languages"]:
        if (not isinstance(entry, dict) or set(entry) != {"code", "name"}
                or not isinstance(entry["code"], str) or not _CODE.fullmatch(entry["code"])
                or entry["code"] in codes or not isinstance(entry["name"], str)
                or not 1 <= len(entry["name"]) <= 80 or entry["name"] != entry["name"].strip()
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in entry["name"])):
            raise CatalogError("Invalid or duplicate bundled language entry")
        codes.add(entry["code"])
        result.append(dict(entry))
    if "it" not in codes:
        raise CatalogError("The Italian source language must remain available")
    return result


def available_languages() -> list[dict[str, str]]:
    try:
        return load_languages()
    except CatalogError as error:
        warnings.warn(str(error), RuntimeWarning, stacklevel=2)
        return [{"code": "it", "name": "Italiano"}]


def supported_language(code: object) -> bool:
    return isinstance(code, str) and any(item["code"] == code for item in available_languages())


def load_catalog(code: str) -> dict[str, str]:
    """Merge one locale's catalogs; conflicting duplicates reject the whole locale.

    Missing files are valid during incremental development and fall back to source
    text. Invalid files are reported, never silently mixed with valid fragments.
    """
    if not supported_language(code):
        raise CatalogError("Unsupported language code")
    directory = CATALOG_ROOT / code
    if directory.is_symlink() or not directory.resolve().is_relative_to(CATALOG_ROOT.resolve()):
        raise CatalogError("Language catalogs must remain in their bundled directory")
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise CatalogError("A language catalog directory is required")
    files = sorted(directory.glob("*.json"))
    if len(files) > 64:
        raise CatalogError("Too many bundled catalog files")
    result: dict[str, str] = {}
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError as error:
            raise CatalogError(f"Cannot read bundled catalog: {code}/{path.name}") from error
        if total > MAX_LANGUAGE_BYTES:
            raise CatalogError("Bundled language catalogs exceed their total size limit")
        data = _read_json(path, MAX_CATALOG_BYTES)
        if not isinstance(data, dict) or len(data) > MAX_CATALOG_ENTRIES:
            raise CatalogError(f"Invalid bundled catalog: {code}/{path.name}")
        for source, translated in data.items():
            if (not isinstance(source, str) or not isinstance(translated, str)
                    or not 1 <= len(source) <= 32768 or not 1 <= len(translated) <= 32768
                    or "\0" in source or "\0" in translated):
                raise CatalogError(f"Invalid translation entry: {code}/{path.name}")
            if source in result and result[source] != translated:
                raise CatalogError(f"Conflicting translation across catalogs: {code}/{path.name}")
            result[source] = translated
        if len(result) > MAX_CATALOG_ENTRIES:
            raise CatalogError("Too many translation entries")
    return result


def native_labels(code: str) -> frozenset[str]:
    """Sources of the locale's standard Qt labels; they always keep Qt's contexts.

    Other catalog files may translate Qt labels too: those reach Qt's contexts
    unless the application uses the same source text itself.
    """
    if not supported_language(code):
        raise CatalogError("Unsupported language code")
    path = CATALOG_ROOT / code / NATIVE_LABELS
    if not path.exists() and not path.is_symlink():
        return frozenset()
    data = _read_json(path, MAX_CATALOG_BYTES)
    if not isinstance(data, dict) or len(data) > MAX_CATALOG_ENTRIES:
        raise CatalogError(f"Invalid bundled catalog: {code}/{NATIVE_LABELS}")
    return frozenset(source for source in data if isinstance(source, str))
