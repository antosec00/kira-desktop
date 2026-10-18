#!/usr/bin/env python3
"""Activate this standalone checkout only after an idle, verified profile cutover.

All installation paths must be provided explicitly. --apply is required; a busy
service is never stopped. Private evidence stays beneath build/ and the original
profile remains as an archive. Never restore an old archive over new work
performed after activation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import signal
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kira import __version__
from kira.client import Client
from kira.domain.paths import Paths
from separate_profile import ProfileSeparation

ROOT = Path(__file__).resolve().parents[1]
PROC = Path("/proc")


def paths(home: Path) -> Paths:
    return Paths(*(home / name for name in ("config", "data", "state", "runtime")))


def identity(pid: int) -> tuple[bytes, int]:
    directory = PROC / str(pid)
    return ((directory / "cmdline").read_bytes(),
            int((directory / "stat").read_text().rsplit(")", 1)[1].split()[19]))


def process_kind(args: list[bytes], root: Path) -> str | None:
    binaries = root / ".venv/bin"
    console = os.fsencode(binaries / "kira-desktop")
    if args == [console]:
        return "gui"
    if not args:
        return None
    executable = Path(os.fsdecode(args[0]))
    if executable.parent != binaries or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable.name):
        return None
    arguments = args[1:]
    while arguments and arguments[0] in {b"-I", b"-s", b"-S", b"-E", b"-B", b"-u", b"-O", b"-OO", b"-P"}:
        arguments = arguments[1:]
    if arguments == [b"-m", b"kira.service.server"]:
        return "service"
    if arguments in ([b"-m", b"kira"], [console]):
        return "gui"
    if len(arguments) == 1:
        helper = Path(os.fsdecode(arguments[0]))
        if helper.name == "open_updated_desktop.py" and helper.resolve().is_relative_to((root / "build").resolve()):
            return "gui"
    return None


def processes(root: Path, home: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    guis, services = [], []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            command, birth = identity(int(entry.name))
            args = [part for part in command.split(b"\0") if part]
            kind = process_kind(args, root)
            if kind is None:
                continue
            env = dict(part.split(b"=", 1) for part in (entry / "environ").read_bytes().split(b"\0") if b"=" in part)
            if env.get(b"KIRA_HOME") != os.fsencode(home):
                continue
            record = {"pid": int(entry.name), "identity": (command, birth),
                      "env": {os.fsdecode(k): os.fsdecode(v) for k, v in env.items()}}
            (services if kind == "service" else guis).append(record)
        except (OSError, ValueError, IndexError):
            continue
    if len(services) != 1:
        raise RuntimeError("Cannot identify exactly one original service; no process will be stopped")
    return guis, services[0]


def terminate_exact(record: dict[str, Any]) -> None:
    try:
        fd = os.pidfd_open(record["pid"])
    except ProcessLookupError:
        return
    try:
        if identity(record["pid"]) != record["identity"]:
            raise RuntimeError("Process identity changed; refusing to signal it")
        signal.pidfd_send_signal(fd, signal.SIGTERM)
    except (ProcessLookupError, FileNotFoundError):
        pass
    finally:
        os.close(fd)


def alive(record: dict[str, Any]) -> bool:
    try:
        directory = PROC / str(record["pid"])
        return identity(record["pid"]) == record["identity"] and (directory / "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def atomic_write(path: Path, data: bytes, mode: int = 0o700) -> None:
    temporary = path.with_name(path.name + ".separation-new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def launch(python: Path, cwd: Path, env: dict[str, str], log: Path) -> int:
    with log.open("ab") as stream:
        process = subprocess.Popen([str(python), "-I", "-m", "kira"], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
    return process.pid


async def activate(args: argparse.Namespace) -> int:
    source_root = args.source_root.absolute()
    source = args.source_profile.absolute()
    destination = args.profile.absolute()
    launcher = args.launcher.absolute()
    if source.is_symlink():
        raise RuntimeError("Original profile is already an alias; inspect the recorded migration before retrying")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Target profile already exists; it will not be overwritten")
    client = Client(paths(source))
    await client.connect()
    try:
        diagnostic = await client.request("system.diagnostics")
        if diagnostic["app_version"] != __version__:
            raise RuntimeError("Use matching original/application versions for this separation")
        with sqlite3.connect(paths(source).db.as_uri() + "?mode=ro", uri=True) as db:
            teams = db.execute("SELECT count(*) FROM team_runs WHERE state IN ('planning','running','cancelling')").fetchone()[0]
            selected = db.execute("SELECT r.session_id,s.project_id,s.model,s.settings FROM runs r JOIN sessions s ON s.id=r.session_id WHERE NOT EXISTS(SELECT 1 FROM team_run_links l WHERE l.run_id=r.id) ORDER BY r.created_at DESC LIMIT 1").fetchone()
            preference_file = source / "config/ui.json"
            preferences = json.loads(preference_file.read_text()) if preference_file.exists() else {}
            remembered = preferences.get("selected_sessions", {})
            if isinstance(remembered, dict):
                for project_id, session_id in reversed(list(remembered.items())):
                    saved = db.execute("SELECT s.id,s.project_id,s.model,s.settings FROM sessions s WHERE s.id=? AND s.project_id=? AND NOT EXISTS(SELECT 1 FROM team_members m WHERE m.session_id=s.id)", (session_id, project_id)).fetchone()
                    if saved is not None:
                        selected = saved
                        break
        db.close()
        if diagnostic["service"]["active_run_count"] or teams:
            print(json.dumps({"status": "waiting_for_idle", "active_runs": diagnostic["service"]["active_run_count"], "active_teams": teams, "changed": False}))
            return 3
        if not args.apply:
            print(json.dumps({"status": "ready", "source": str(source), "destination": str(destination), "apply_required": True}))
            return 0
        assert selected is not None, "Existing project/session required for verified GUI restoration"
        assert not launcher.is_symlink() and launcher.stat().st_uid == os.getuid()
        launcher_before = launcher.read_bytes()
        assert os.fsencode(source_root / ".venv/bin/python") in launcher_before
        assert os.fsencode(source) in launcher_before
        subprocess.run(["git", "diff", "--quiet"], cwd=ROOT, check=True)
        subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=True)
        guis, service = processes(source_root, source)
        env = dict((guis[-1] if guis else service)["env"])
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_dir = ROOT / "build" / ("separation-" + stamp)
        report_dir.mkdir(mode=0o700, parents=True)
        archive = source.with_name(source.name + ".pre-separation-" + stamp)
        backup = (await client.request("database.backup"))["path"]
        before = {"version": __version__, "backup": backup, "session_id": selected[0],
                  "project_id": selected[1], "model": selected[2],
                  "reasoning_effort": json.loads(selected[3]).get("reasoning_effort"),
                  "source": str(source), "destination": str(destination), "archive": str(archive),
                  "gui_pids": [p["pid"] for p in guis], "service_pid": service["pid"]}
        (report_dir / "before.json").write_text(json.dumps(before, indent=2) + "\n")
        (report_dir / "launcher-before").write_bytes(launcher_before)
        launcher_written = False
        guis_closed = False
        stopped = False
        try:
            # Quiesce known interactive clients before the final service check.
            # The legacy daemon delays shutdown by 100 ms; it does not atomically
            # close admission. Do not submit work from other clients during cutover.
            for process in guis:
                terminate_exact(process)
            guis_closed = bool(guis)
            for _ in range(200):
                if not any(alive(p) for p in guis):
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Original GUI did not close; service remains untouched")
            await asyncio.sleep(0.2)  # Let already-sent GUI requests reach the service.
            if (await client.request("system.diagnostics"))["service"]["active_run_count"]:
                raise RuntimeError("New work arrived during GUI closure; service was not stopped")
            await client.request("service.shutdown")
            await client.close()
            for _ in range(600):
                if not alive(service) and not paths(source).socket.exists():
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Original service/socket did not finish cleanly")
            stopped = True
            with ProfileSeparation(source, destination, archive) as move:
                (report_dir / "copy-verified.json").write_text(json.dumps(move.report, indent=2) + "\n")
                move.switch()
                assert launcher.read_bytes() == launcher_before, "Launcher changed during migration"
                new_python = ROOT / ".venv/bin/python"
                content = ("#!/bin/sh\n# Standalone Kira Desktop; personal data live outside source checkouts.\n"
                    "set -eu\n"
                    f"kira_python={shlex.quote(str(new_python))}\n"
                    f"kira_profile={shlex.quote(str(destination))}\n"
                    'if [ ! -x "$kira_python" ]; then\n'
                    "    printf '%s\\n' 'Kira Desktop: ambiente Python non disponibile.' >&2\n"
                    "    exit 127\nfi\n"
                    'export KIRA_HOME="${KIRA_HOME:-$kira_profile}"\n'
                    'exec "$kira_python" -I -m kira "$@"\n').encode()
                atomic_write(launcher, content)
                launcher_written = True
                (report_dir / "cutover.json").write_text(json.dumps(move.report, indent=2) + "\n")
            # Store locks are released only when the data and launcher cutover is complete.
        except BaseException:
            if launcher_written and launcher.read_bytes() == content:
                atomic_write(launcher, launcher_before)
            if (stopped or guis_closed) and source.is_dir() and not source.is_symlink() and not archive.exists():
                rollback_env = {**env, "KIRA_HOME": str(source)}
                launch(source_root / ".venv/bin/python", source_root, rollback_env, report_dir / "rollback-gui.log")
            raise
        env["KIRA_HOME"] = str(destination)
        if env.get("VIRTUAL_ENV") == str(source_root / ".venv"):
            env["VIRTUAL_ENV"] = str(ROOT / ".venv")
        env["PATH"] = os.pathsep.join(str(ROOT / ".venv/bin") if part == str(source_root / ".venv/bin") else part
                                     for part in env.get("PATH", "").split(os.pathsep))
        gui_pid = launch(ROOT / ".venv/bin/python", ROOT, env, report_dir / "gui.log")
        (report_dir / "new-gui.json").write_text(json.dumps({"pid": gui_pid, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}) + "\n")
        (ROOT / "build/current-separation-path.txt").write_text(str(report_dir) + "\n")
        print(json.dumps({"status": "activated_pending_verification", "report": str(report_dir), "gui_pid": gui_pid, "profile": str(destination)}))
        return 0
    finally:
        await client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="Existing source checkout with its .venv installation")
    parser.add_argument("--source-profile", type=Path, required=True,
                        help="Existing KIRA_HOME directory containing config, data, state and runtime")
    parser.add_argument("--profile", type=Path, required=True,
                        help="New, nonexistent KIRA_HOME directory outside source checkouts")
    parser.add_argument("--launcher", type=Path, required=True,
                        help="Existing owned launcher to replace after verified profile transfer")
    parser.add_argument("--apply", action="store_true", help="Perform the cutover after an idle check")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(activate(args)))


if __name__ == "__main__":
    main()
