"""Real Qt subprocesses reproduce same-timestamp updates against warmed caches."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

EPOCH = 1791417600
RUNNER = """
import json, os, sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from kira.ui.qml_cache import qml_disk_cache
strategy, source = sys.argv[1:]
app = QGuiApplication([])
app.setOrganizationName('Kira')
app.setApplicationName('CacheRegression')
source = Path(source)
cache = None
def load():
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(source / 'Main.qml')))
    assert engine.rootObjects()
    return engine
if strategy == 'legacy':
    engine = load()
else:
    with qml_disk_cache(source, 'CacheRegression') as cache:
        engine = load()
# Qt must also keep using the chosen cache for lazily loaded components after
# the temporary environment override has been restored for child processes.
component = QQmlComponent(engine, QUrl.fromLocalFile(str(source / 'Late.qml')))
late = component.create()
assert late is not None, [str(error) for error in component.errors()]
print(json.dumps({'payload': engine.rootObjects()[0].property('payload'),
                  'late': late.property('payload'),
                  'cache': str(cache) if cache else None,
                  'ambient_override': os.environ.get('QML_DISK_CACHE_PATH')}))
late.deleteLater()
component.deleteLater()
engine.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
"""


@pytest.fixture
def cache_helper():
    from kira.ui.qml_cache import qml_disk_cache
    return qml_disk_cache


def test_cache_scope_respects_user_options_and_restores_environment(cache_helper, monkeypatch,
                                                                   tmp_path):
    source = tmp_path / "qml"
    source.mkdir()
    (source / "Main.qml").write_text("import QtQuick; Item {}")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("QML_DISK_CACHE_PATH", str(tmp_path / "explicit"))
    monkeypatch.setenv("QML_DISABLE_DISK_CACHE", "1")
    monkeypatch.setenv("QML_DISK_CACHE", "aot")
    original = dict(os.environ)
    with cache_helper(source, "ScopeCheck") as cache:
        assert cache is None
        assert dict(os.environ) == original
    assert dict(os.environ) == original
    monkeypatch.delenv("QML_DISK_CACHE_PATH")
    original = dict(os.environ)
    with pytest.raises(RuntimeError, match="load failure"):
        with cache_helper(source, "ScopeCheck") as first:
            assert os.environ["QML_DISK_CACHE_PATH"] == str(first)
            assert os.environ["QML_DISABLE_DISK_CACHE"] == "1"
            raise RuntimeError("load failure")
    assert dict(os.environ) == original
    with cache_helper(source, "ScopeCheck") as again:
        assert again == first
    assert dict(os.environ) == original


def test_real_qt_upgrade_uses_changed_payload_despite_same_mtime_and_size(tmp_path):
    source = tmp_path / "qml"
    source.mkdir()
    (source / "Main.qml").write_text(
        "import QtQuick\nItem { property string payload: marker.payload; Marker { id: marker } }\n"
    )
    (source / "Late.qml").write_text('import QtQuick\nItem { property string payload: "late" }\n')
    marker = source / "Marker.qml"

    def update(value):
        marker.write_text(f'import QtQuick\nItem {{ property string payload: "{value}" }}\n')
        os.utime(marker, (EPOCH, EPOCH))

    environment = {key: value for key, value in os.environ.items()
                   if key not in ("QML_DISK_CACHE_PATH", "QML_DISABLE_DISK_CACHE",
                                  "QML_FORCE_DISK_CACHE", "QML_DISK_CACHE")}
    environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                       XDG_CACHE_HOME=str(tmp_path / "cache"))

    def run(strategy):
        process = subprocess.run(
            [sys.executable, "-c", RUNNER, strategy, str(source)],
            env=environment, text=True, capture_output=True, timeout=15, check=True,
        )
        assert process.stderr == ""
        return json.loads(process.stdout)

    def hashes(directory):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.glob("*.qmlc")}

    update("sage")
    stat_before = marker.stat()
    assert run("legacy")["payload"] == "sage"
    legacy = tmp_path / "cache/Kira/CacheRegression/qmlcache"
    warmed = hashes(legacy)
    assert len(warmed) == 3, "Main, imported Marker and deferred Late must really be cached"
    update("blue")
    assert (marker.stat().st_mtime_ns, marker.stat().st_size) == (
        stat_before.st_mtime_ns, stat_before.st_size)
    # Control: a Qt that validates cached bytecode only by source timestamp and
    # size keeps serving the old compilation from a cache path not keyed by content.
    stale_control = run("legacy")["payload"] == "sage"
    warmed = hashes(legacy)
    blue = run("fixed")
    assert blue["payload"] == "blue"
    assert blue["late"] == "late"
    assert blue["ambient_override"] is None
    blue_cache = hashes(Path(blue["cache"]))
    assert len(blue_cache) == 3
    assert hashes(legacy) == warmed, "The fix must leave previous caches untouched"
    update("cyan")
    cyan = run("fixed")
    assert cyan["payload"] == "cyan"
    assert cyan["cache"] != blue["cache"]
    assert hashes(Path(blue["cache"])) == blue_cache
    assert run("fixed") == cyan
    if not stale_control:
        pytest.skip("The installed Qt already revalidates same-timestamp QML, so the stale-cache "
                    "control cannot be reproduced; the content-keyed cache itself was verified")
