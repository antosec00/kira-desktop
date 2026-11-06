"""Fixed, explicit native desktop actions, separate from provider tool execution."""

import asyncio
import contextlib
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from kira.domain.contracts import Json, KiraError


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    description: str
    binaries: tuple[str, ...]
    requires_project: bool = False


ACTIONS = (
    Action("project.folder", "Cartella del progetto", "Apri i file con Dolphin.", ("dolphin",), True),
    Action("project.terminal", "Terminale del progetto", "Apri Konsole nella cartella del progetto.", ("konsole",), True),
    Action("project.editor", "Editor del progetto", "Apri una sessione Kate con la cartella del progetto come directory di lavoro.", ("kate",), True),
    Action("browser.open", "Browser", "Apri Firefox.", ("firefox",)),
    Action("browser.search", "Cerca sul web", "Cerca il testo su Google con Firefox.", ("firefox",)),
    Action("screenshot.capture", "Acquisisci schermata", "Acquisisci lo schermo e associa il file al progetto selezionato.", ("spectacle",), True),
    Action("system.settings", "Impostazioni", "Apri le impostazioni di Plasma.", ("systemsettings", "systemsettings6", "systemsettings5")),
    Action("system.monitor", "Monitor di sistema", "Controlla risorse e processi.", ("plasma-systemmonitor",)),
    Action("system.updates", "Aggiornamenti", "Apri Discover per controllare gli aggiornamenti.", ("plasma-discover",)),
    Action("system.backup", "Backup dei file", "Apri Déjà Dup per configurare o ripristinare un backup.", ("deja-dup",)),
)
WINDOWS_ACTIONS = (
    Action("project.folder", "Cartella del progetto", "Apri i file con Esplora file.", ("explorer.exe",), True),
    Action("project.terminal", "Terminale del progetto", "Apri il terminale nella cartella del progetto.", ("wt.exe", "powershell.exe"), True),
    Action("project.editor", "Editor del progetto", "Apri l'editor nella cartella del progetto.", ("code.cmd", "notepad.exe"), True),
    Action("browser.open", "Browser", "Apri il browser predefinito.", ("powershell.exe",)),
    Action("browser.search", "Cerca sul web", "Cerca il testo su Google nel browser.", ("powershell.exe",)),
    Action("screenshot.capture", "Acquisisci schermata", "Acquisisci lo schermo e associa il file al progetto selezionato.", ("powershell.exe",), True),
    Action("system.settings", "Impostazioni", "Apri le impostazioni di Windows.", ("powershell.exe",)),
    Action("system.monitor", "Monitor di sistema", "Controlla risorse e processi con Gestione attività.", ("taskmgr.exe",)),
    Action("system.updates", "Aggiornamenti", "Apri Windows Update.", ("powershell.exe",)),
    Action("system.backup", "Backup dei file", "Apri le impostazioni di backup di Windows.", ("powershell.exe",)),
)
ACTION_IDS = {action.id for action in ACTIONS}


def native_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # The bundled Qt/Python runtime must never be loaded by native KDE programs.
    for key in list(environment):
        if key.startswith("_PYI_") or key in {
            "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM",
            "QT_QUICK_BACKEND", "QSG_RHI_BACKEND", "QMLSCENE_DEVICE", "QML2_IMPORT_PATH",
            "QML_IMPORT_PATH", "QML_DISK_CACHE_PATH", "LD_LIBRARY_PATH", "LD_PRELOAD",
            "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
        }:
            environment.pop(key, None)
    return environment


def executable(names: tuple[str, ...]) -> str | None:
    if sys.platform == "win32":
        sys_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        win_dirs = [
            sys_root,
            sys_root / "System32",
            sys_root / "System32" / "WindowsPowerShell" / "v1.0",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "bin",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft VS Code" / "bin",
        ]
        for name in names:
            for directory in win_dirs:
                candidate = directory / name
                if candidate.is_file():
                    return str(candidate)
            found = shutil.which(name)
            if found:
                return found
        return None
    # Ignore cwd/PATH overrides. This catalog addresses installed OS programs only.
    for name in names:
        for directory in ("/usr/bin", "/bin", "/snap/bin"):
            path = Path(directory) / name
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


def user_manager() -> bool:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    return bool(runtime and (Path(runtime) / "systemd/private").exists())


def project_directory(project: Json) -> Path:
    root = Path(project["root"])
    if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
        raise KiraError("PATH_DENIED", "Project directory is missing or changed to a symlink")
    return root


class Desktop:
    """Test seam for native programs; fixtures never count as native qualification."""

    def catalog(self, project: Json | None = None, query: str = "") -> list[Json]:
        output = []
        session = True if sys.platform == "win32" else bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
        actions = WINDOWS_ACTIONS if sys.platform == "win32" else ACTIONS
        for action in actions:
            reason = ""
            if action.requires_project and project is None:
                reason = "Seleziona un progetto."
            elif not session:
                reason = "Sessione grafica non disponibile per il servizio."
            elif not executable(action.binaries):
                reason = f"Applicazione non installata: {action.binaries[0]}."
            elif sys.platform != "win32" and os.environ.get("INVOCATION_ID") and not user_manager():
                reason = "Gestore della sessione utente non disponibile."
            elif action.requires_project:
                try:
                    assert project is not None
                    project_directory(project)
                except (OSError, KiraError, RuntimeError):
                    reason = "La cartella del progetto non è disponibile o è cambiata."
            entry = {"id": action.id, "label": action.label, "description": action.description,
                     "requires_project": action.requires_project, "available": not reason,
                     "reason": reason}
            text = f"{action.id} {action.label} {action.description}".casefold()
            if all(word in text for word in query.casefold().split()):
                output.append(entry)
        return output

    def argv(self, action_id: str, project: Json | None, query: str,
             capture_path: Path | None = None) -> tuple[list[str], Path]:
        actions = WINDOWS_ACTIONS if sys.platform == "win32" else ACTIONS
        action = next(action for action in actions if action.id == action_id)
        binary = executable(action.binaries)
        if not binary:
            raise KiraError("UNAVAILABLE", "Native application is not installed")
        cwd = project_directory(project) if action.requires_project and project else Path.home()
        if sys.platform == "win32":
            if action_id == "project.folder":
                return [binary, str(cwd)], cwd
            if action_id == "project.terminal":
                if Path(binary).name.lower() == "wt.exe":
                    return [binary, "-d", str(cwd)], cwd
                escaped_cwd = str(cwd).replace("'", "''")
                return [binary, "-NoExit", "-Command", f"Set-Location -LiteralPath '{escaped_cwd}'"], cwd
            if action_id == "project.editor":
                if Path(binary).name.lower().startswith("code"):
                    return [binary, str(cwd)], cwd
                return [binary], cwd
            if action_id == "browser.open":
                return [binary, "-NoProfile", "-WindowStyle", "Hidden", "-Command", "Start-Process 'https://www.google.com'"], cwd
            if action_id == "browser.search":
                url = ("https://www.google.com/search?" + urlencode({"q": query})).replace("'", "%27")
                return [binary, "-NoProfile", "-WindowStyle", "Hidden", "-Command", f"Start-Process '{url}'"], cwd
            if action_id == "system.settings":
                return [binary, "-NoProfile", "-WindowStyle", "Hidden", "-Command", "Start-Process 'ms-settings:'"], cwd
            if action_id == "system.updates":
                return [binary, "-NoProfile", "-WindowStyle", "Hidden", "-Command", "Start-Process 'ms-settings:windowsupdate'"], cwd
            if action_id == "system.backup":
                return [binary, "-NoProfile", "-WindowStyle", "Hidden", "-Command", "Start-Process 'ms-settings:backup'"], cwd
            if action_id == "screenshot.capture":
                assert capture_path is not None
                escaped_out = str(capture_path).replace("'", "''")
                ps_script = (
                    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                    "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                    "$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height); "
                    "$g = [System.Drawing.Graphics]::FromImage($bmp); "
                    "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
                    f"$bmp.Save('{escaped_out}', [System.Drawing.Imaging.ImageFormat]::Png); "
                    "$g.Dispose(); $bmp.Dispose()"
                )
                return [binary, "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script], cwd
            return [binary], cwd
        argv = [binary]
        if action_id == "project.folder":
            argv += [str(cwd)]
        elif action_id == "project.terminal":
            argv += ["--workdir", str(cwd)]
        elif action_id == "project.editor":
            # Kate's positional URLs are documents, not a portable folder API.
            # A fresh session inherits project cwd without loading unrelated work.
            argv += ["--startanon"]
        elif action_id == "browser.open":
            argv += ["--new-window", "about:blank"]
        elif action_id == "browser.search":
            argv += ["--new-tab", "https://www.google.com/search?" + urlencode({"q": query})]
        elif action_id == "system.updates":
            argv += ["--mode", "Update"]
        elif action_id == "screenshot.capture":
            assert capture_path is not None
            argv += ["--background", "--nonotify", "--fullscreen", "--output", str(capture_path)]
        return argv, cwd

    async def launch(self, action_id: str, project: Json | None, query: str,
                     capture_path: Path | None = None) -> None:
        argv, cwd = self.argv(action_id, project, query, capture_path)
        environment = native_environment()
        if action_id == "screenshot.capture":
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                code = await asyncio.wait_for(process.wait(), 45)
                if code:
                    raise KiraError("NATIVE_FAILED", "Screen capture was cancelled or failed")
            finally:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
            return
        if sys.platform != "win32":
            if user_manager():
                launcher = executable(("systemd-run",))
                if not launcher:
                    raise KiraError("UNAVAILABLE", "Session launcher is not installed")
                # A scope moves the app out of kira-desktop.service's control-group.
                # --scope inherits our cleaned environment (without placing it in argv).
                argv = [launcher, "--user", "--scope", "--collect", "--quiet",
                        "--unit", "kira-desktop-action-" + uuid.uuid4().hex, "--", *argv]
            elif os.environ.get("INVOCATION_ID"):
                raise KiraError("UNAVAILABLE", "Cannot detach application from the service")
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
            "close_fds": (sys.platform != "win32"),
        }
        if sys.platform == "win32" and "-NoExit" in argv:
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        process_native = subprocess.Popen(argv, **popen_kwargs)
        # Reap without tying the app's lifetime to Kira's event loop or its shutdown.
        threading.Thread(target=process_native.wait, daemon=True).start()
        await asyncio.sleep(0.15)
        if process_native.poll() not in {None, 0}:
            raise KiraError("NATIVE_FAILED", "Native launch failed before completion")


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise KiraError("UNSAFE_PATH", "Private output directory must not be a symlink")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise KiraError("UNSAFE_PATH", "Output directory must be owned by this user")
    path.chmod(0o700)
    return path
