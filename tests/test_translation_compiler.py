"""Native Qt catalogs avoid Python translation callbacks in lazy QML loading."""

import os

import pytest
from PySide6.QtCore import QTranslator

from kira.ui.translation_compiler import CatalogCompilationError, compiled_catalog


def test_native_catalog_cache_is_content_keyed_and_preserves_environment(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    before = dict(os.environ)
    first = compiled_catalog("en", {"Chiudi": "Close", "File {path}": "File {path}"})
    translator = QTranslator()
    assert translator.load(str(first))
    assert translator.translate("Main", "Chiudi") == "Close"
    assert translator.translate("Kira", "File {path}") == "File {path}"
    second = compiled_catalog("en", {"Chiudi": "Dismiss"})
    assert second != first and first.is_file()
    assert first.stat().st_mode & 0o777 == 0o600
    assert first.parent.stat().st_mode & 0o777 == 0o700
    monkeypatch.setattr("kira.ui.translation_compiler.subprocess.run",
                        lambda *a, **k: pytest.fail("A verified catalog must reuse its cache"))
    assert compiled_catalog("en", {"Chiudi": "Close", "File {path}": "File {path}"}) == first
    assert dict(os.environ) == before


def test_corrupted_cache_is_rebuilt_but_symlinks_are_refused(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cached = compiled_catalog("en", {"Chiudi": "Close"})
    cached.write_bytes(b"damaged")
    assert compiled_catalog("en", {"Chiudi": "Close"}) == cached
    translator = QTranslator()
    assert translator.load(str(cached))
    outside = tmp_path / "unrelated.txt"
    outside.write_text("preserve this file")
    cached.unlink()
    cached.symlink_to(outside)
    with pytest.raises(CatalogCompilationError, match="safely"):
        compiled_catalog("en", {"Chiudi": "Close"})
    assert outside.read_text() == "preserve this file"
