#!/usr/bin/env python3
"""Capture the actual running Qt app attached to a real Kira data store.

This is an evidence driver, not a fixture. It never fabricates transcript events.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.graphics import configure_graphics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--expect-active", default="")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--team-view", action="store_true")
    parser.add_argument("--team-id", default="")
    parser.add_argument("--team-editor", action="store_true")
    parser.add_argument("--team-tab", type=int, choices=(0, 1, 2, 3), default=0)
    args = parser.parse_args()
    configure_graphics()
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([sys.argv[0]])
    workspace = Workspace()
    engine = create_engine(workspace)
    if not engine.rootObjects():
        raise SystemExit("QML root failed to load")
    window = engine.rootObjects()[0]
    window.setWidth(args.width)
    window.setHeight(args.height)
    requested = False
    ready_time = None
    stop_requested = False
    active_seen = False
    success = False
    team_selected = False
    editor_opened = False
    started = time.monotonic()

    def step() -> None:
        nonlocal requested, ready_time, stop_requested, active_seen, success
        nonlocal team_selected, editor_opened
        if time.monotonic() - started > 35:
            print("GUI evidence timed out: " + workspace.notice, file=sys.stderr)
            app.exit(1)
            return
        if workspace.connected and not requested:
            requested = True
            workspace.openProject(args.project_id)
        if workspace.project.get("id") != args.project_id:
            return
        if args.team_view or args.team_id or args.team_editor:
            window.setProperty("page", 4)
            if args.team_id and not team_selected:
                team_selected = True
                workspace.teams.selectTeam(args.team_id)
            if args.team_id and workspace.teams.team.get("id") != args.team_id:
                return
            tabs = window.findChild(QObject, "teamTabs")
            if tabs is None:
                return
            tabs.setProperty("currentIndex", args.team_tab)
            if args.team_editor and not editor_opened:
                editor = window.findChild(QObject, "teamEditor")
                if editor is None:
                    return
                QMetaObject.invokeMethod(editor, "open")
                editor_opened = True
        if args.expect_active and workspace.activeRunId == args.expect_active:
            active_seen = True
        if args.expect_active and not active_seen:
            return
        if args.stop and active_seen and not stop_requested:
            stop_requested = True
            workspace.stop()
        if args.stop and workspace.activeRunId:
            return
        if ready_time is None:
            ready_time = time.monotonic()
        if time.monotonic() - ready_time < 2:
            return
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not window.grabWindow().save(str(output)):
            app.exit(1)
            return
        summary = {
            "source": "Actual running QML app and live service data", "connected": workspace.connected,
            "project_id": workspace.project.get("id"), "model": workspace.selectedModel,
            "backend": workspace.backend, "reasoning_effort": workspace.selectedEffort,
            "run_state": workspace.runState, "active_run": workspace.activeRunId,
            "active_seen": active_seen, "stop_requested_through_gui": stop_requested,
            "transcript_rows": workspace.transcript.rowCount(), "artifacts": len(workspace.artifacts),
            "width": window.width(), "height": window.height(),
            "scale": window.devicePixelRatio(), "platform": app.platformName(),
            "notice": workspace.notice, "screenshot": str(output),
        }
        if args.team_view or args.team_id or args.team_editor:
            summary.update(team_id=workspace.teams.team.get("id"),
                           team_state=workspace.teams.team.get("state"),
                           team_model=workspace.teams.team.get("model"),
                           team_reasoning_effort=workspace.teams.team.get("settings", {}).get(
                               "reasoning_effort"),
                           team_members=len(workspace.teams.members),
                           team_messages=workspace.teams.messages.rowCount(),
                           team_error=workspace.teams.error, editor_opened=editor_opened)
        output.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        success = True
        window.setProperty("closeConfirmed", True)
        window.close()
        app.quit()

    timer = QTimer()
    timer.setInterval(200)
    timer.timeout.connect(step)
    QTimer.singleShot(0, workspace.start)
    timer.start()
    code = app.exec()
    workspace.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return code if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
