#!/usr/bin/env python3
"""Installed-only helper for adjacent package/schema/rollback qualification.

The driver copies this helper into an owned disposable container. Stored runs,
events and artifacts are explicitly synthetic migration fixtures, not AI work.
No provider calls or native desktop application launches are made here.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/kira-desktop/lib")

from kira import __version__  # noqa: E402
from kira.client import Client, ensure_service  # noqa: E402
from kira.domain.paths import Paths  # noqa: E402

HOME = Path.home()
STATE = HOME / "upgrade-fixture.json"
BASE_TABLES = ("projects", "sessions", "runs", "events", "artifacts", "settings")
TABLES = BASE_TABLES
OLD_SCHEMA, NEW_SCHEMA = 1, 2
OLD_VERSION, NEW_VERSION = "0.1.0", "0.2.0"


def private_json(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contents(database: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        tables = {}
        for table in TABLES:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            rows = [list(row) for row in connection.execute(f"SELECT * FROM {table}")]
            rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
            serialized = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":"))
            tables[table] = {"rows": len(rows), "sha256": hashlib.sha256(serialized.encode()).hexdigest()}
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        settings = dict(connection.execute("SELECT key,value FROM settings"))
    assert integrity == "ok" and not foreign_keys
    return {"schema": schema, "integrity": integrity, "foreign_key_errors": foreign_keys,
            "tables": tables, "settings": settings}


def original_files() -> dict[str, Any]:
    fixture = json.loads(STATE.read_text())
    result = {}
    for name, path in fixture["files"].items():
        try:
            content = Path(path).read_text()
        except UnicodeDecodeError:
            content = Path(path).read_bytes().hex()
        result[name] = {"sha256": digest(Path(path)), "content": content}
    return result


def snapshot() -> dict[str, Any]:
    return {**contents(Paths.from_env().db), "files": original_files(), "version": __version__}


async def create() -> dict[str, Any]:
    assert __version__ == OLD_VERSION
    source = json.loads(Path("/tmp/upgrade-input.json").read_text())
    root = HOME / "migration-project"
    root.mkdir(mode=0o700)
    input_path = root / "preserve-input.txt"
    input_path.write_text(source["input_content"])
    input_path.chmod(0o600)
    assert digest(input_path) == source["input_sha256"]
    await ensure_service()
    async with Client() as client:
        hello = await client.request("hello")
        assert hello["app_version"] == OLD_VERSION
        project = (await client.request("project.create", {
            "name": "Synthetic migration fixture " + source["nonce"], "root": str(root),
            "instructions": "Migration fixture only. Do not infer AI execution.",
            "permission": "read_only"}))['project']
        memory = {"current_goal": f"Preserve actual {OLD_VERSION} rows", "next_step": "Qualify migration",
                  "verified_decisions": [], "fixture_value": source["nonce"]}
        project = (await client.request("project.update", {"project_id": project["id"],
                                                            "memory": memory}))['project']
        settings = (await client.request("settings.update", {
            "ollama_url": "http://127.0.0.1:9", "codex_binary": "/nonexistent/kira-upgrade-codex",
            "tool_timeout": 31, "max_output_bytes": 8192, "max_tool_rounds": 3}))['settings']
        session = (await client.request("session.create", {
            "project_id": project["id"], "backend": "codex", "model": "fixture-configuration-only",
            "settings": {"reasoning_effort": "ultra", "tools": True},
            "handoff": "Synthetic stored session; no provider request performed."}))['session']
    fixture = {**source, "project_id": project["id"], "session_id": session["id"],
               "root": str(root), "settings": settings, "memory": memory,
               "files": {"input": str(input_path)}}
    private_json(STATE, fixture)
    return {"version": __version__, "project_id": project["id"], "session_id": session["id"],
            "memory": memory, "settings": settings, "input_sha256": digest(input_path),
            "provider_execution": False}


def seed() -> dict[str, Any]:
    assert __version__ == OLD_VERSION
    paths = Paths.from_env()
    assert not paths.socket.exists(), "Stop the real service before seeding migration fixtures"
    from kira.service.storage import Store, new_id, now
    fixture = json.loads(STATE.read_text())
    artifact = Path(fixture["root"]) / "synthetic-artifact.txt"
    artifact.write_text(f"Synthetic migration artifact, no AI execution: {fixture['nonce']}\n")
    artifact.chmod(0o600)
    store = Store(paths.db)
    try:
        run, _ = store.create_run(fixture["session_id"], new_id(),
                                  "SYNTHETIC MIGRATION FIXTURE — no backend was executed")
        store.event(run["id"], "message.completed", {
            "content": "Synthetic historical message for migration preservation."})
        store.event(run["id"], "artifact.created", {"path": str(artifact), "kind": "fixture",
                                                    "sha256": digest(artifact)})
        store.event(run["id"], "run.state", {"state": "completed", "outcome": {
            "fixture": True, "summary": "Synthetic fixture; this is not an inference result"}})
        if OLD_SCHEMA == 2:
            from PySide6.QtGui import QImage
            capture = paths.artifacts / "synthetic-migration-capture.png"
            picture = QImage(2, 2, QImage.Format.Format_RGB32)
            picture.fill(0x20262B)
            assert picture.save(str(capture))
            capture.chmod(0o600)
            timestamp = now()
            store.db.execute("INSERT INTO workspaces VALUES (?,?,?,?)", (
                fixture["project_id"], json.dumps(["folder", "editor"]),
                "Verify rollback separately", timestamp))
            store.db.execute("INSERT INTO file_watches VALUES (?,?,?,?)", (
                fixture["project_id"], "preserve-input.txt", fixture["input_sha256"], timestamp))
            store.db.execute("INSERT INTO desktop_requests VALUES (?,?,?,?,?,?,?,?)", (
                new_id(), "desktop.launch", "synthetic-fixture-" + fixture["nonce"],
                fixture["project_id"], "finished", json.dumps({"fixture": True, "status": "requested"}),
                timestamp, timestamp))
            store.db.execute("INSERT INTO captures VALUES (?,?,?,?,?)", (
                new_id(), fixture["project_id"], str(capture), digest(capture), timestamp))
            fixture["files"]["synthetic_capture"] = str(capture)
    finally:
        store.close()
    fixture["run_id"] = run["id"]
    fixture["files"]["synthetic_artifact"] = str(artifact)
    private_json(STATE, fixture)
    result = snapshot()
    assert result["schema"] == OLD_SCHEMA and all(t["rows"] > 0 for t in result["tables"].values())
    private_json(HOME / "upgrade-before.json", result)
    return result


async def migrate() -> dict[str, Any]:
    assert __version__ == NEW_VERSION
    fixture = json.loads(STATE.read_text())
    before = json.loads((HOME / "upgrade-before.json").read_text())
    # This canary is generated test data, not a credential or copied host environment.
    os.environ["KIRA_UPGRADE_PRIVATE_CANARY"] = fixture["diagnostic_canary"]
    await ensure_service()
    async with Client() as client:
        assert (await client.request("hello"))["app_version"] == NEW_VERSION
        after = snapshot()
        assert after["schema"] == NEW_SCHEMA
        for key in ("tables", "settings", "files"):
            assert after[key] == before[key], f"Old {key} changed during migration"
        backup_paths = list(Paths.from_env().data.glob(f"kira.sqlite3.pre-v{NEW_SCHEMA}-*.bak"))
        assert len(backup_paths) == 1
        backup = backup_paths[0]
        backup_data = contents(backup)
        assert backup_data["schema"] == OLD_SCHEMA and backup_data["tables"] == before["tables"]
        assert backup.stat().st_mode & 0o777 == 0o600
        assert backup.stat().st_uid == os.getuid()
        params = {"project_id": fixture["project_id"]}
        watch_states = []
        if OLD_SCHEMA == 1:
            result = await client.request("workspace.update", {**params, "tools": ["folder", "editor"],
                                                                  "next_step": "Verify rollback separately"})
            assert result["tools"] == ["folder", "editor"]
            assert result["resume"]["last_run"]["id"] == fixture["run_id"]
            watched = Path(fixture["root"]) / "new-workspace-watch.txt"
            watched.write_text("new v2 evidence " + fixture["nonce"])
            result = await client.request("workspace.watch", {**params, "path": watched.name})
            assert result["watches"][0]["state"] == "unchanged"
            watch_states.append(result["watches"][0]["state"])
            watched.write_text("changed v2 evidence " + fixture["nonce"])
            result = await client.request("workspace.get", params)
            assert result["watches"][0]["state"] == "changed"
            watch_states.append(result["watches"][0]["state"])
            assert result["watches"][0]["current_sha256"] == digest(watched)
            watched.unlink()
            result = await client.request("workspace.get", params)
            assert result["watches"][0]["state"] == "missing"
            watch_states.append(result["watches"][0]["state"])
            await client.request("workspace.unwatch", {**params, "path": watched.name})
        else:
            assert (await client.request("team.list", params))["teams"] == []
            workspace = await client.request("workspace.get", params)
            assert workspace["tools"] == ["folder", "editor"]
            assert len(workspace["watches"]) == 1
            watch = workspace["watches"][0]
            assert watch["path"] == "preserve-input.txt" and watch["state"] == "unchanged"
            assert watch["sha256"] == watch["current_sha256"] == fixture["input_sha256"]
            watch_states.append(watch["state"])
        diagnostic = await client.request("system.diagnostics")
        exported = await client.request("diagnostics.export")
        export = Path(exported["path"])
        assert export.stat().st_mode & 0o777 == 0o600
        assert json.loads(export.read_text()) == diagnostic
        serialized = json.dumps(diagnostic)
        for value in (fixture["nonce"], fixture["diagnostic_canary"], fixture["root"],
                      "fixture-configuration-only", "Migration fixture only"):
            assert value not in serialized
        assert diagnostic["database_schema"] == NEW_SCHEMA
        settings = (await client.request("settings.get"))["settings"]
        assert settings == fixture["settings"]
        project = (await client.request("project.snapshot", params))["project"]
        assert project["memory"] == fixture["memory"]
    fixture["migration_backup"] = str(backup)
    private_json(STATE, fixture)
    return {"preserved": after, "migration_backup": {"path": str(backup), "sha256": digest(backup),
             "mode": oct(backup.stat().st_mode & 0o777), **backup_data},
            "workspace_persisted": True,
            "watch_states": watch_states,
            "team_api_empty_after_migration": NEW_SCHEMA == 3,
            "diagnostics_omit_canary_project_content_and_paths": True,
            "diagnostics_export_mode": oct(export.stat().st_mode & 0o777),
            "native_launches": False, "provider_execution": False}


def refuse() -> dict[str, Any]:
    assert __version__ == OLD_VERSION
    paths = Paths.from_env()
    assert not paths.socket.exists()
    before = snapshot()
    original_hash = digest(paths.db)
    assert before["schema"] == NEW_SCHEMA
    result = subprocess.run(["/usr/bin/kira-desktop", "service"], capture_output=True,
                            text=True, check=False, timeout=15)
    assert result.returncode != 0
    assert "INCOMPATIBLE_SCHEMA" in result.stdout + result.stderr
    after = snapshot()
    assert after == before and digest(paths.db) == original_hash
    assert not paths.socket.exists()
    return {"returncode": result.returncode, "error": "INCOMPATIBLE_SCHEMA",
            "new_schema_untouched": True, "database_sha256": original_hash,
            "preserved_tables": after["tables"]}


def restore() -> dict[str, Any]:
    assert __version__ == OLD_VERSION
    fixture = json.loads(STATE.read_text())
    paths = Paths.from_env()
    assert not paths.socket.exists()
    backup = Path(fixture["migration_backup"])
    assert contents(backup)["schema"] == OLD_SCHEMA
    preserved = paths.db.with_name(f"kira-schema{NEW_SCHEMA}-preserved.sqlite3")
    assert not preserved.exists()
    # Read-only SQLite connections can leave nonempty shared-memory metadata.
    # Keep a consistent new-schema backup and retain the raw DB/WAL/SHM group.
    descriptor = os.open(preserved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(f"file:{paths.db}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(preserved)) as target:
            source.backup(target)
    assert contents(preserved)["schema"] == NEW_SCHEMA
    assert contents(preserved)["tables"] == contents(paths.db)["tables"]
    with closing(sqlite3.connect(preserved)) as connection:
        workspace = connection.execute("SELECT tools,next_step FROM workspaces WHERE project_id=?",
                                       (fixture["project_id"],)).fetchone()
    assert json.loads(workspace[0]) == ["folder", "editor"]
    assert workspace[1] == "Verify rollback separately"
    original_directory = paths.data / f"schema{NEW_SCHEMA}-original-files"
    original_directory.mkdir(mode=0o700)
    original_hashes = {}
    for suffix in ("", "-wal", "-shm"):
        source_file = paths.db.with_name(paths.db.name + suffix)
        if source_file.exists():
            original_hashes[source_file.name] = digest(source_file)
            destination = original_directory / source_file.name
            source_file.rename(destination)
            assert digest(destination) == original_hashes[source_file.name]
    shutil.copyfile(backup, paths.db)
    paths.db.chmod(0o600)
    restored = snapshot()
    baseline = json.loads((HOME / "upgrade-before.json").read_text())
    assert restored == baseline
    return {"restored": restored, "source_backup_sha256": digest(backup),
            "preserved_schema_path": str(preserved), "preserved_schema_sha256": digest(preserved),
            "preserved_schema_workspace": {"tools": json.loads(workspace[0]), "next_step": workspace[1]},
            "original_schema_group": str(original_directory), "original_group_hashes": original_hashes}


def gui(phase: str) -> dict[str, Any]:
    import PySide6
    from PySide6.QtCore import (
        QCoreApplication, QEvent, QEventLoop, QMetaObject, QObject, QTimer, qVersion,
    )
    from PySide6.QtGui import QColor, QGuiApplication, QPalette
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from kira.ui.app import create_engine
    from kira.ui.controller import Workspace
    fixture = json.loads(STATE.read_text())
    expected_qt = "6.11.2" if __version__ != "0.1.0" else "6.10.2"
    assert PySide6.__version__ == qVersion() == expected_qt
    cache_root = HOME / ".cache"

    def cache_hashes() -> dict[str, str]:
        return {str(path.relative_to(cache_root)): digest(path)
                for path in cache_root.rglob("*.qmlc") if path.is_file()}

    prior_cache = cache_hashes()
    cache_environment = os.environ.get("QML_DISK_CACHE_PATH")
    expected_cache = None
    if __version__ != "0.1.0":
        from kira.ui.qml_cache import qml_disk_cache
        qml_directory = Path(__import__("kira.ui.app", fromlist=["__file__"]).__file__).parent / "qml"
        with qml_disk_cache(qml_directory, "Kira Desktop") as expected_cache:
            assert expected_cache is not None, "This controlled test supplies no cache override"
        assert os.environ.get("QML_DISK_CACHE_PATH") == cache_environment
        if phase == "new-installed-qml":
            assert prior_cache, "The preceding old GUI must actually warm QML caches"
            assert not any((cache_root / name).is_relative_to(expected_cache) for name in prior_cache)
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    if __version__ != "0.1.0":
        native = QPalette(app.palette())
        native.setColor(QPalette.ColorRole.BrightText, QColor("#000000"))
        native.setColor(QPalette.ColorRole.Dark, QColor("#000000"))
        native.setColor(QPalette.ColorRole.Light, QColor("#242c30"))
        native.setColor(QPalette.ColorRole.Midlight, QColor("#20262b"))
        app.setPalette(native)
    workspace = Workspace()
    metatypes = {}
    if __version__ != "0.1.0":
        for name, qt_type, python_type in (
            ("projectWorkspace", "QVariantMap", dict), ("desktopActions", "QVariantList", list),
            ("project", "QVariantMap", dict), ("projects", "QVariantList", list),
            ("models", "QVariantList", list), ("sessions", "QVariantList", list),
            ("efforts", "QVariantList", list), ("settings", "QVariantMap", dict),
        ):
            index = workspace.metaObject().indexOfProperty(name)
            assert index >= 0, name
            prop = workspace.metaObject().property(index)
            assert prop.typeName() == qt_type and prop.hasNotifySignal(), name
            assert prop.isReadable() and not prop.isWritable(), name
            assert type(getattr(workspace, name)) is python_type, name
            metatypes[name] = {"qt_type": qt_type, "notify": True,
                               "python_type": python_type.__name__}
    engine = create_engine(workspace)
    assert len(engine.rootObjects()) == 1
    assert os.environ.get("QML_DISK_CACHE_PATH") == cache_environment
    window = engine.rootObjects()[0]
    window.resize(1280, 850)
    opened = False
    finished = False
    report: dict[str, Any] = {"version": __version__, "uid": os.getuid(), "cwd": str(Path.cwd()),
                              "platform": app.platformName(), "qml_root": True,
                              "qt": qVersion(), "pyside6": PySide6.__version__,
                              "installed_module": __import__("kira").__file__}

    def evaluate(item: Any, expression: str) -> Any:
        context = QQmlEngine.contextForObject(item)
        assert context is not None
        script = QQmlExpression(context, item, expression)
        value, _ = script.evaluate()
        assert not script.hasError(), script.error().toString()
        return value

    def contrast(foreground: list[float], background: list[float]) -> float:
        def luminance(rgb: list[float]) -> float:
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
        light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    def check() -> None:
        nonlocal opened, finished
        if workspace.connected and not opened:
            opened = True
            workspace.openProject(fixture["project_id"])
        if workspace.project.get("id") != fixture["project_id"] or finished:
            return
        if __version__ != "0.1.0" and not workspace.projectWorkspace:
            return
        if workspace.transcript.rowCount() == 0:
            return
        finished = True
        assert workspace.project["memory"] == fixture["memory"]
        assert workspace.selectedModel == "fixture-configuration-only"
        assert workspace.selectedEffort == "ultra"
        assert workspace.transcript.rowCount() > 0
        workspace.copy("Synthetic installed upgrade clipboard check")
        assert app.clipboard().text() == "Synthetic installed upgrade clipboard check"
        report.update(memory_preserved=True, model_effort_preserved=True, clipboard=True,
                      transcript_rows=workspace.transcript.rowCount())
        if __version__ != "0.1.0":
            assert workspace.projectWorkspace["tools"] == ["folder", "editor"]
            effort_combo = window.findChild(QObject, "effortBox")
            assert effort_combo is not None
            assert effort_combo.property("displayText") == "ultra · salvato"
            report["saved_effort_label"] = effort_combo.property("displayText")
            dialog = window.findChild(QObject, "workspaceDialog")
            assert dialog is not None
            QMetaObject.invokeMethod(dialog, "open")
            QCoreApplication.processEvents()
            tabs = [item for item in window.findChildren(QQuickItem)
                    if item.property("text") in {"Ripresa", "File di riferimento", "Schermate"}
                    and isinstance(item.property("checked"), bool)]
            assert len(tabs) == 3
            assert {tab.property("checked") for tab in tabs} == {False, True}
            for tab in tabs:
                assert tab.property("contentItem").property("color").name() == "#e7ece9"
                assert tab.property("background").property("color").name() in {"#161a1d", "#101416"}
            combos = [item for item in window.findChildren(QQuickItem)
                      if item.property("indicator") is not None
                      and isinstance(item.property("currentIndex"), int)]
            assert len(combos) >= 7
            assert all(combo.property("indicator").property("color").name() == "#abb4c0"
                       for combo in combos)
            report["contrast_controls"] = {"native_bright_text": "#000000",
                "checked_and_unchecked_tabs": "#e7ece9", "dark_tab_backgrounds_preserved": True,
                "combo_indicators": "#abb4c0", "combobox_count": len(combos)}
            QMetaObject.invokeMethod(dialog, "close")
            report["workspace_preserved"] = True
            report["qt_metatypes"] = metatypes
            evaluate(window, "newProject.open(); memoryDialog.open()")
            popup_labels = []
            popup_contrast = []
            for combo in combos:
                if not combo.property("count"):
                    continue
                window.setProperty("page", 3 if combo.objectName() == "densityChoice" else 0)
                evaluate(combo, "popup.open()")
                loop = QEventLoop()
                QTimer.singleShot(30, loop.quit)
                loop.exec()
                labels = json.loads(evaluate(combo, """
                    JSON.stringify(Array.from({length: count}, function(_, index) {
                        const item = popup.contentItem.itemAtIndex(index);
                        if (!item) return null;
                        const foreground = item.contentItem.color;
                        const background = item.background;
                        const underneath = popup.background.color;
                        const alpha = background.visible ? background.color.a * background.opacity : 0;
                        const rgb = ["r", "g", "b"].map(function(channel) {
                            return background.color[channel] * alpha + underneath[channel] * (1 - alpha);
                        });
                        return {expected: textAt(index), text: item.contentItem.text,
                            textFormat: item.contentItem.textFormat, highlighted: item.highlighted,
                            foreground: [foreground.r, foreground.g, foreground.b], background: rgb};
                    }))
                """))
                assert labels and all(item and item["expected"] == item["text"] and item["text"]
                                      and item["textFormat"] == 0
                                      for item in labels), labels
                popup_labels.append([item["text"] for item in labels])
                for item in labels:
                    item["contrast_ratio"] = contrast(item["foreground"], item["background"])
                    assert item["contrast_ratio"] >= 4.5, item
                    popup_contrast.append(item)
                evaluate(combo, "popup.close()")
            assert ["Ollama", "Codex"] in popup_labels
            assert ["Comoda", "Compatta"] in popup_labels
            assert ["Default provider"] in popup_labels
            report["plain_popup_labels"] = popup_labels
            assert {item["highlighted"] for item in popup_contrast} == {False, True}
            report["popup_contrast"] = {"rows": popup_contrast, "minimum_ratio": 4.5,
                "native_palette_fixture": {"light": "#242c30", "midlight": "#20262b"}}
            evaluate(window, "newProject.close(); memoryDialog.close()")
            window.setProperty("page", 0)
        QTimer.singleShot(800, finish)

    def finish() -> None:
        screenshot = HOME / f"upgrade-{phase}.png"
        assert window.grabWindow().save(str(screenshot))
        report["screenshot"] = str(screenshot)
        window.setProperty("closeConfirmed", True)
        window.close()
        app.quit()

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(check)
    timer.start()
    # Start the actual installed service; configured provider binaries/endpoints are absent.
    QTimer.singleShot(0, workspace.start)
    QTimer.singleShot(25000, lambda: app.exit(2))
    result = app.exec()
    workspace.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert result == 0 and finished
    current_cache = cache_hashes()
    assert current_cache, "Installed QML must produce observable disk cache files"
    if __version__ != "0.1.0":
        assert expected_cache is not None
        current_files = [name for name in current_cache
                         if (cache_root / name).is_relative_to(expected_cache)]
        assert current_files, "The new content-specific cache must contain actual QML bytecode"
        assert all(current_cache.get(name) == value for name, value in prior_cache.items())
        report["qml_cache"] = {"content_specific_path": str(expected_cache),
            "new_payload_files": current_files, "prior_file_hashes_preserved": prior_cache,
            "environment_restored": os.environ.get("QML_DISK_CACHE_PATH") == cache_environment,
            "cache_hit_proven": False}
    else:
        report["qml_cache"] = {"observed_file_hashes": current_cache, "cache_hit_proven": False}
    return report


def main() -> None:
    global OLD_SCHEMA, NEW_SCHEMA, OLD_VERSION, NEW_VERSION, TABLES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("create", "seed", "snapshot", "migrate", "refuse", "restore", "gui"))
    parser.add_argument("--label", default="gui")
    parser.add_argument("--from-schema", type=int, choices=(1, 2), default=1)
    parser.add_argument("--to-schema", type=int, choices=(2, 3), default=2)
    args = parser.parse_args()
    assert (args.from_schema, args.to_schema) in {(1, 2), (2, 3)}
    OLD_SCHEMA, NEW_SCHEMA = args.from_schema, args.to_schema
    OLD_VERSION, NEW_VERSION = f"0.{OLD_SCHEMA}.0", f"0.{NEW_SCHEMA}.0"
    TABLES = BASE_TABLES + (("workspaces", "file_watches", "desktop_requests", "captures")
                           if OLD_SCHEMA == 2 else ())
    assert os.getuid() != 0 and Path.cwd() == Path("/tmp")
    assert str(__import__("kira").__file__).startswith("/opt/kira-desktop/lib/")
    assert not Path("/tmp/pyproject.toml").exists()
    if args.phase in {"create", "migrate"}:
        result = asyncio.run({"create": create, "migrate": migrate}[args.phase]())
    elif args.phase == "gui":
        result = gui(args.label)
    else:
        result = {"seed": seed, "snapshot": snapshot, "refuse": refuse, "restore": restore}[args.phase]()
    report = {"phase": args.phase, "status": "PASS", "version": __version__, **result}
    private_json(HOME / f"upgrade-{args.label}.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
