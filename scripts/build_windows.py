#!/usr/bin/env python3
"""Build, sync, and install the native Windows distribution of Kira Desktop.

Can be executed either from WSL2 (`uv run python scripts/build_windows.py`) or
natively on Windows (`python scripts/build_windows.py`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ICON_PNG = REPO_ROOT / "src" / "kira" / "ui" / "assets" / "kira-mascot.png"


def _detect_windows_user_dirs() -> tuple[Path, Path, Path]:
    """Return (localappdata, appdata, desktop) as host-accessible Paths."""
    if sys.platform == "win32":
        localappdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        desktop = Path.home() / "Desktop"
        return localappdata, appdata, desktop

    users_root = Path("/mnt/c/Users")
    skip = {"All Users", "Default", "Default User", "Public", "WsiAccount", "desktop.ini"}
    candidates: list[Path] = []
    if users_root.is_dir():
        for p in sorted(users_root.iterdir()):
            if p.name in skip or p.name.startswith("CodexSandbox"):
                continue
            try:
                if p.is_dir() and (p / "AppData" / "Local").is_dir() and (p / "Desktop").is_dir():
                    candidates.append(p)
            except OSError:
                continue
    if not candidates:
        raise RuntimeError("Impossibile individuare la cartella utente Windows in /mnt/c/Users")
    win_home = candidates[0]
    return (
        win_home / "AppData" / "Local",
        win_home / "AppData" / "Roaming",
        win_home / "Desktop",
    )


def _to_windows_path(path: Path) -> str:
    if sys.platform == "win32":
        return str(path.resolve())
    result = subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _write_ico_from_png(png_path: Path, ico_path: Path) -> None:
    """Write a multi-resolution Windows .ico container (16..256 px PNG frames)."""
    png_bytes = png_path.read_bytes()
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Not a valid PNG file: {png_path}")
    frames: list[tuple[int, bytes]] = []
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PySide6.QtGui import QImage

        img = QImage(str(png_path))
        if not img.isNull():
            for size in (16, 24, 32, 48, 64, 128, 256):
                scaled = img.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ba = QByteArray()
                buf = QBuffer(ba)
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                scaled.save(buf, b"PNG")
                buf.close()
                frames.append((size, bytes(ba.data())))
    except Exception:
        pass

    if not frames:
        width, _height = struct.unpack(">II", png_bytes[16:24])
        frames.append((min(width, 256), png_bytes))

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    dirents: list[bytes] = []
    payloads: list[bytes] = []
    for size, data in frames:
        dim = 0 if size >= 256 else size
        dirents.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        payloads.append(data)
        offset += len(data)
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    ico_path.write_bytes(header + b"".join(dirents) + b"".join(payloads))


def _sync_tree(src: Path, dst: Path) -> int:
    """Copy files from src to dst when missing or modified, removing stale files."""
    copied = 0
    dst.mkdir(parents=True, exist_ok=True)
    expected_names: set[str] = set()
    for item in src.iterdir():
        if item.name == "__pycache__" or item.suffix in {".pyc", ".pyo"}:
            continue
        expected_names.add(item.name)
        target = dst / item.name
        if item.is_dir():
            copied += _sync_tree(item, target)
        else:
            src_stat = item.stat()
            need_copy = True
            if target.exists():
                dst_stat = target.stat()
                if dst_stat.st_size == src_stat.st_size and int(dst_stat.st_mtime) >= int(src_stat.st_mtime):
                    need_copy = False
            if need_copy:
                shutil.copy2(item, target)
                copied += 1
    for existing in list(dst.iterdir()):
        if existing.name == "__pycache__" or existing.name.endswith(".egg-info"):
            continue
        if existing.name not in expected_names:
            if existing.is_dir():
                shutil.rmtree(existing, ignore_errors=True)
            else:
                existing.unlink(missing_ok=True)
    return copied


def _write_launchers(install_dir: Path, wsl_repo_win_path: str) -> None:
    launch_pyw = install_dir / "launch_kira.pyw"
    launch_pyw.write_text(
        f'''"""Native Windows launcher for Kira Desktop with automatic dev-repo sync."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

INSTALL_DIR = Path(__file__).resolve().parent
APP_DIR = INSTALL_DIR / "app"
DEV_REPO = Path({wsl_repo_win_path!r})


def _sync_dir(src: Path, dst: Path) -> int:
    changed = 0
    dst.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for item in src.iterdir():
        if item.name == "__pycache__" or item.suffix in {{".pyc", ".pyo"}}:
            continue
        seen.add(item.name)
        target = dst / item.name
        if item.is_dir():
            changed += _sync_dir(item, target)
        else:
            try:
                s_stat = item.stat()
                if target.exists():
                    t_stat = target.stat()
                    if t_stat.st_size == s_stat.st_size and int(t_stat.st_mtime) >= int(s_stat.st_mtime):
                        continue
                shutil.copy2(item, target)
                changed += 1
            except OSError:
                pass
    for existing in list(dst.iterdir()):
        if existing.name == "__pycache__" or existing.name.endswith(".egg-info"):
            continue
        if existing.name not in seen:
            try:
                if existing.is_dir():
                    shutil.rmtree(existing, ignore_errors=True)
                else:
                    existing.unlink(missing_ok=True)
            except OSError:
                pass
    return changed


def _auto_sync_from_dev_repo() -> bool:
    try:
        src_kira = DEV_REPO / "src" / "kira"
        if not src_kira.is_dir():
            return False
        changed = _sync_dir(src_kira, APP_DIR / "src" / "kira")
        for meta in ("pyproject.toml", "README.md", "LICENSE"):
            s_file = DEV_REPO / meta
            t_file = APP_DIR / meta
            if s_file.is_file():
                if not t_file.exists() or int(s_file.stat().st_mtime) > int(t_file.stat().st_mtime):
                    shutil.copy2(s_file, t_file)
                    changed += 1
        return changed > 0
    except OSError:
        return False


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kira.Desktop")
        except Exception:
            pass
    for var in ("WAYLAND_DISPLAY", "DISPLAY", "WSL_DISTRO_NAME", "WSL_INTEROP"):
        os.environ.pop(var, None)
    synced_changes = _auto_sync_from_dev_repo()
    src_path = str(APP_DIR / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    if synced_changes:
        try:
            import asyncio
            from kira.client import Client
            from kira.domain.paths import Paths

            async def _restart_idle_service() -> None:
                paths = Paths.from_env()
                if not paths.socket.exists():
                    return
                async with Client(paths=paths) as client:
                    status = await asyncio.wait_for(client.request("hello"), timeout=1.5)
                    if status:
                        await asyncio.wait_for(client.request("service.shutdown"), timeout=1.5)

            asyncio.run(_restart_idle_service())
        except Exception:
            pass
    from kira.cli import main as kira_main
    return kira_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )

    vbs_path = install_dir / "Kira Desktop.vbs"
    vbs_content = (
        'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
        'baseDir = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
        'kiraExe = baseDir & "\\venv\\Scripts\\KiraDesktop.exe"\r\n'
        'If Not fso.FileExists(kiraExe) Then\r\n'
        '    kiraExe = baseDir & "\\venv\\Scripts\\pythonw.exe"\r\n'
        'End If\r\n'
        'launcher = baseDir & "\\launch_kira.pyw"\r\n'
        'Set shell = CreateObject("WScript.Shell")\r\n'
        'shell.CurrentDirectory = baseDir\r\n'
        'shell.Run Chr(34) & kiraExe & Chr(34) & " -I " & Chr(34) & launcher & Chr(34), 0, False\r\n'
    )
    vbs_path.write_bytes(vbs_content.encode("ascii"))

    debug_cmd = install_dir / "Kira Desktop (Console).cmd"
    debug_cmd.write_bytes(
        (
            '@echo off\r\n'
            'cd /d "%~dp0"\r\n'
            '"%~dp0venv\\Scripts\\python.exe" -I "%~dp0launch_kira.pyw" %*\r\n'
        ).encode("ascii")
    )

    cli_cmd = install_dir / "kira.cmd"
    cli_cmd.write_bytes(
        (
            '@echo off\r\n'
            '"%~dp0venv\\Scripts\\python.exe" -I "%~dp0launch_kira.pyw" %*\r\n'
        ).encode("ascii")
    )


def _build_branded_executable(install_dir: Path) -> None:
    """Create venv\\Scripts\\KiraDesktop.exe with the Kira PE icon resource."""
    win_python = install_dir / "venv" / "Scripts" / "python.exe"
    if not win_python.is_file():
        return
    win_install = _to_windows_path(install_dir)
    script = f"""
import ctypes, shutil, struct, sys
from ctypes import wintypes
from pathlib import Path

install_dir = Path({win_install!r})
scripts_dir = install_dir / "venv" / "Scripts"
ico_path = install_dir / "kira.ico"
base_dir = Path(sys.base_prefix)

for dll in ("python312.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
    src = base_dir / dll
    dst = scripts_dir / dll
    if src.exists() and not dst.exists():
        shutil.copy2(src, dst)

kira_exe = scripts_dir / "KiraDesktop.exe"
try:
    shutil.copy2(base_dir / "pythonw.exe", kira_exe)
except PermissionError:
    raise SystemExit(0)

data = bytearray(kira_exe.read_bytes())
pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
magic = struct.unpack_from("<H", data, pe_offset + 24)[0]
dd_offset = pe_offset + 24 + (112 if magic == 0x20B else 96)
sec_dir_offset = dd_offset + 4 * 8
sec_rva, sec_size = struct.unpack_from("<II", data, sec_dir_offset)
if sec_rva and sec_size:
    struct.pack_into("<II", data, sec_dir_offset, 0, 0)
    if sec_rva + sec_size == len(data):
        del data[sec_rva:]
dll_char_offset = pe_offset + 24 + 70
dll_chars = struct.unpack_from("<H", data, dll_char_offset)[0]
struct.pack_into("<H", data, dll_char_offset, dll_chars & ~0x0080)
kira_exe.write_bytes(data)

ico = ico_path.read_bytes()
_, _, count = struct.unpack_from("<HHH", ico, 0)
kernel32 = ctypes.windll.kernel32
kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
kernel32.UpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.UpdateResourceW.restype = wintypes.BOOL
kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
kernel32.EndUpdateResourceW.restype = wintypes.BOOL

h_upd = kernel32.BeginUpdateResourceW(str(kira_exe), False)
if h_upd:
    grp_entries = []
    for i in range(count):
        w, h, colors, res, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", ico, 6 + i * 16)
        icon_id = i + 1
        img_bytes = ico[offset : offset + size]
        buf = ctypes.create_string_buffer(img_bytes, len(img_bytes))
        kernel32.UpdateResourceW(h_upd, ctypes.cast(3, wintypes.LPCWSTR), ctypes.cast(icon_id, wintypes.LPCWSTR), 1033, buf, len(img_bytes))
        grp_entries.append(struct.pack("<BBBBHHIH", w, h, colors, res, planes, bpp, size, icon_id))
    grp_data = struct.pack("<HHH", 0, 1, count) + b"".join(grp_entries)
    grp_buf = ctypes.create_string_buffer(grp_data, len(grp_data))
    for gid in (1, 2):
        kernel32.UpdateResourceW(h_upd, ctypes.cast(14, wintypes.LPCWSTR), ctypes.cast(gid, wintypes.LPCWSTR), 1033, grp_buf, len(grp_data))
        kernel32.UpdateResourceW(h_upd, ctypes.cast(14, wintypes.LPCWSTR), ctypes.cast(gid, wintypes.LPCWSTR), 0, grp_buf, len(grp_data))
    kernel32.EndUpdateResourceW(h_upd, False)
"""
    subprocess.run([str(win_python), "-c", script], check=True)


def _create_windows_shortcuts(install_dir: Path, desktop_dir: Path, appdata_dir: Path) -> None:
    win_install = _to_windows_path(install_dir)
    win_exe = f"{win_install}\\venv\\Scripts\\KiraDesktop.exe"
    win_launcher = f"{win_install}\\launch_kira.pyw"
    win_ico = f"{win_install}\\kira.ico"

    shortcuts = [
        _to_windows_path(desktop_dir / "Kira Desktop.lnk"),
        _to_windows_path(appdata_dir / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Kira Desktop.lnk"),
    ]
    ps_lines = ["$ws = New-Object -ComObject WScript.Shell"]
    for lnk in shortcuts:
        ps_lines.extend([
            f"$s = $ws.CreateShortcut('{lnk}')",
            f"$s.TargetPath = '{win_exe}'",
            f'$s.Arguments = \'-I "{win_launcher}"\'',
            f"$s.WorkingDirectory = '{win_install}'",
            f"$s.IconLocation = '{win_ico},0'",
            "$s.Description = 'Kira Desktop'",
            "$s.Save()",
        ])
    powershell = "powershell.exe" if shutil.which("powershell.exe") else "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "; ".join(ps_lines)],
        check=True,
    )


def _read_version() -> str:
    init_text = (REPO_ROOT / "src" / "kira" / "__init__.py").read_text(encoding="utf-8")
    for line in init_text.splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    return "0.28.0"


def _write_post_install_script(target_dir: Path) -> None:
    (target_dir / "setup_windows_env.py").write_text(
        '''"""Post-install environment setup for Kira Desktop on Windows."""

from __future__ import annotations

import ctypes
import os
import shutil
import struct
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

INSTALL_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = INSTALL_DIR / "venv" / "Scripts"
ICO_PATH = INSTALL_DIR / "kira.ico"


def patch_kira_executable() -> None:
    base_dir = Path(sys.base_prefix)
    for dll in ("python312.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        src = base_dir / dll
        dst = SCRIPTS_DIR / dll
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    kira_exe = SCRIPTS_DIR / "KiraDesktop.exe"
    try:
        shutil.copy2(base_dir / "pythonw.exe", kira_exe)
    except PermissionError:
        return
    data = bytearray(kira_exe.read_bytes())
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    magic = struct.unpack_from("<H", data, pe_offset + 24)[0]
    dd_offset = pe_offset + 24 + (112 if magic == 0x20B else 96)
    sec_dir_offset = dd_offset + 4 * 8
    sec_rva, sec_size = struct.unpack_from("<II", data, sec_dir_offset)
    if sec_rva and sec_size:
        struct.pack_into("<II", data, sec_dir_offset, 0, 0)
        if sec_rva + sec_size == len(data):
            del data[sec_rva:]
    dll_char_offset = pe_offset + 24 + 70
    dll_chars = struct.unpack_from("<H", data, dll_char_offset)[0]
    struct.pack_into("<H", data, dll_char_offset, dll_chars & ~0x0080)
    kira_exe.write_bytes(data)

    if not ICO_PATH.is_file():
        return
    ico = ICO_PATH.read_bytes()
    _, _, count = struct.unpack_from("<HHH", ico, 0)
    kernel32 = ctypes.windll.kernel32
    kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
    kernel32.UpdateResourceW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.WORD, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.UpdateResourceW.restype = wintypes.BOOL
    kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    kernel32.EndUpdateResourceW.restype = wintypes.BOOL

    h_upd = kernel32.BeginUpdateResourceW(str(kira_exe), False)
    if h_upd:
        grp_entries = []
        for i in range(count):
            w, h, colors, res, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", ico, 6 + i * 16)
            icon_id = i + 1
            img_bytes = ico[offset : offset + size]
            buf = ctypes.create_string_buffer(img_bytes, len(img_bytes))
            kernel32.UpdateResourceW(h_upd, ctypes.cast(3, wintypes.LPCWSTR), ctypes.cast(icon_id, wintypes.LPCWSTR), 1033, buf, len(img_bytes))
            grp_entries.append(struct.pack("<BBBBHHIH", w, h, colors, res, planes, bpp, size, icon_id))
        grp_data = struct.pack("<HHH", 0, 1, count) + b"".join(grp_entries)
        grp_buf = ctypes.create_string_buffer(grp_data, len(grp_data))
        for gid in (1, 2):
            kernel32.UpdateResourceW(h_upd, ctypes.cast(14, wintypes.LPCWSTR), ctypes.cast(gid, wintypes.LPCWSTR), 1033, grp_buf, len(grp_data))
            kernel32.UpdateResourceW(h_upd, ctypes.cast(14, wintypes.LPCWSTR), ctypes.cast(gid, wintypes.LPCWSTR), 0, grp_buf, len(grp_data))
        kernel32.EndUpdateResourceW(h_upd, False)


def compile_translations() -> None:
    src_dir = str(INSTALL_DIR / "app" / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    try:
        from kira.ui.language_catalog import load_catalog, native_labels
        from kira.ui.translation_compiler import compiled_catalog

        compiled_catalog("en", load_catalog("en"), native_labels("en"))
    except Exception:
        pass


def create_shortcuts() -> None:
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    desktop = Path.home() / "Desktop"
    win_install = str(INSTALL_DIR)
    win_exe = str(SCRIPTS_DIR / "KiraDesktop.exe")
    if not Path(win_exe).is_file():
        win_exe = str(SCRIPTS_DIR / "pythonw.exe")
    win_launcher = str(INSTALL_DIR / "launch_kira.pyw")
    win_ico = str(ICO_PATH)
    shortcuts = [
        str(desktop / "Kira Desktop.lnk"),
        str(appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Kira Desktop.lnk"),
    ]
    ps_lines = ["$ws = New-Object -ComObject WScript.Shell"]
    for lnk in shortcuts:
        ps_lines.extend([
            f"$s = $ws.CreateShortcut('{lnk}')",
            f"$s.TargetPath = '{win_exe}'",
            f'$s.Arguments = \\'-I "{win_launcher}"\\'',
            f"$s.WorkingDirectory = '{win_install}'",
            f"$s.IconLocation = '{win_ico},0'",
            "$s.Description = 'Kira Desktop'",
            "$s.Save()",
        ])
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "; ".join(ps_lines)],
        check=False,
        creationflags=creationflags,
    )


if __name__ == "__main__":
    patch_kira_executable()
    compile_translations()
    if "--no-shortcuts" not in sys.argv:
        create_shortcuts()
''',
        encoding="utf-8",
    )


def _build_windows_installer(output_exe: Path) -> Path:
    """Build the self-extracting native Windows installer kira-desktop_windows_amd64_setup.exe."""
    import zipfile

    version = _read_version()
    work_dir = REPO_ROOT / "build" / "windows-installer"
    stage_dir = work_dir / "stage"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    output_exe.parent.mkdir(parents=True, exist_ok=True)

    _sync_tree(REPO_ROOT / "src", stage_dir / "app" / "src")
    for meta in ("pyproject.toml", "README.md", "LICENSE"):
        src_meta = REPO_ROOT / meta
        if src_meta.is_file():
            shutil.copy2(src_meta, stage_dir / "app" / meta)

    ico_path = work_dir / "kira.ico"
    _write_ico_from_png(ICON_PNG, ico_path)
    shutil.copy2(ico_path, stage_dir / "kira.ico")
    shutil.copy2(ico_path, stage_dir / "app" / "src" / "kira" / "ui" / "assets" / "kira.ico")
    _write_launchers(stage_dir, "")
    _write_post_install_script(stage_dir)

    payload_zip = work_dir / "payload.zip"
    with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in sorted(stage_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(stage_dir).as_posix())

    cs_source = work_dir / "KiraSetup.cs"
    cs_source.write_text(
        f"""using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Kira Desktop Setup")]
[assembly: AssemblyDescription("Kira Desktop {version} Installer")]
[assembly: AssemblyProduct("Kira Desktop")]
[assembly: AssemblyVersion("{version}.0")]
[assembly: AssemblyFileVersion("{version}.0")]

namespace KiraDesktopSetup {{
    static class Program {{
        [STAThread]
        static int Main(string[] args) {{
            bool silent = false;
            bool noRestart = false;
            foreach (string arg in args) {{
                string upper = (arg ?? "").ToUpperInvariant();
                if (upper == "/VERYSILENT" || upper == "/SILENT" || upper == "/S" || upper == "-S")
                    silent = true;
                if (upper == "/NORESTART")
                    noRestart = true;
            }}
            try {{
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string installDir = Path.Combine(Path.Combine(localAppData, "Programs"), "Kira Desktop");
                Directory.CreateDirectory(installDir);

                using (Stream res = Assembly.GetExecutingAssembly().GetManifestResourceStream("KiraPayload.zip")) {{
                    if (res == null)
                        throw new InvalidOperationException("Missing embedded KiraPayload.zip resource.");
                    using (ZipArchive archive = new ZipArchive(res, ZipArchiveMode.Read)) {{
                        foreach (ZipArchiveEntry entry in archive.Entries) {{
                            if (string.IsNullOrEmpty(entry.Name))
                                continue;
                            string destPath = Path.Combine(installDir, entry.FullName.Replace('/', Path.DirectorySeparatorChar));
                            string destDir = Path.GetDirectoryName(destPath);
                            if (!string.IsNullOrEmpty(destDir))
                                Directory.CreateDirectory(destDir);
                            entry.ExtractToFile(destPath, true);
                        }}
                    }}
                }}

                string venvPython = Path.Combine(installDir, @"venv\\Scripts\\python.exe");
                if (!File.Exists(venvPython)) {{
                    string psBootstrap =
                        "$ErrorActionPreference = 'Stop'; " +
                        "$uv = Join-Path $env:USERPROFILE '.local\\\\bin\\\\uv.exe'; " +
                        "if (-not (Test-Path $uv)) {{ $uvCmd = Get-Command uv -ErrorAction SilentlyContinue; if ($uvCmd) {{ $uv = $uvCmd.Source }} }}; " +
                        "if (-not (Test-Path $uv)) {{ irm https://astral.sh/uv/install.ps1 | iex; $uv = Join-Path $env:USERPROFILE '.local\\\\bin\\\\uv.exe' }}; " +
                        "Set-Location -LiteralPath '" + installDir.Replace("'", "''") + "'; " +
                        "& $uv venv --python 3.12 venv; " +
                        "& $uv pip install --python venv\\\\Scripts\\\\python.exe " +
                        "PySide6-Essentials==6.11.2 cryptography==50.0.2 httpx==0.28.1 markdown-it-py==4.0.0 " +
                        "pydantic==2.12.5 PyJWT[crypto]==2.15.1 playwright==1.64.0 \\"wasmtime>=49.0.0\\" claude-agent-sdk";
                    ProcessStartInfo psi = new ProcessStartInfo("powershell.exe", "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \\"" + psBootstrap + "\\"");
                    psi.CreateNoWindow = true;
                    psi.UseShellExecute = false;
                    using (Process p = Process.Start(psi)) {{
                        p.WaitForExit();
                    }}
                }}

                if (File.Exists(venvPython)) {{
                    string setupPy = Path.Combine(installDir, "setup_windows_env.py");
                    string setupArgs = "\\"" + setupPy + "\\"" + (silent ? " --no-shortcuts" : "");
                    ProcessStartInfo psi = new ProcessStartInfo(venvPython, setupArgs);
                    psi.WorkingDirectory = installDir;
                    psi.CreateNoWindow = true;
                    psi.UseShellExecute = false;
                    using (Process p = Process.Start(psi)) {{
                        p.WaitForExit();
                    }}
                }}

                if (!silent) {{
                    DialogResult dr = MessageBox.Show(
                        "Kira Desktop {version} è stato installato con successo.\\n\\nVuoi avviare Kira Desktop adesso?",
                        "Kira Desktop Setup",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Information
                    );
                    if (dr == DialogResult.Yes && !noRestart) {{
                        string kiraExe = Path.Combine(installDir, @"venv\\Scripts\\KiraDesktop.exe");
                        if (!File.Exists(kiraExe))
                            kiraExe = Path.Combine(installDir, @"venv\\Scripts\\pythonw.exe");
                        string launcher = Path.Combine(installDir, "launch_kira.pyw");
                        if (File.Exists(kiraExe) && File.Exists(launcher)) {{
                            ProcessStartInfo startPsi = new ProcessStartInfo(kiraExe, "-I \\"" + launcher + "\\"");
                            startPsi.WorkingDirectory = installDir;
                            startPsi.UseShellExecute = false;
                            Process.Start(startPsi);
                        }}
                    }}
                }}
                return 0;
            }} catch (Exception ex) {{
                if (!silent) {{
                    MessageBox.Show("Installazione non riuscita: " + ex.Message, "Kira Desktop Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }}
                return 1;
            }}
        }}
    }}
}}
""",
        encoding="utf-8",
    )

    csc_candidates = [
        Path("/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"),
        Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    ]
    csc = next((p for p in csc_candidates if p.is_file()), None)
    if csc is not None:
        win_out = _to_windows_path(output_exe)
        win_ico = _to_windows_path(ico_path)
        win_zip = _to_windows_path(payload_zip)
        win_cs = _to_windows_path(cs_source)
        subprocess.run(
            [
                str(csc),
                "/nologo",
                "/target:winexe",
                "/optimize+",
                f"/out:{win_out}",
                f"/win32icon:{win_ico}",
                f"/resource:{win_zip},KiraPayload.zip",
                "/r:System.IO.Compression.dll",
                "/r:System.IO.Compression.FileSystem.dll",
                "/r:System.Windows.Forms.dll",
                "/r:System.Drawing.dll",
                win_cs,
            ],
            check=True,
        )
    elif shutil.which("mcs"):
        subprocess.run(
            [
                "mcs",
                "-target:winexe",
                "-optimize+",
                f"-out:{output_exe}",
                f"-win32icon:{ico_path}",
                f"-resource:{payload_zip},KiraPayload.zip",
                "-r:System.IO.Compression.dll",
                "-r:System.IO.Compression.FileSystem.dll",
                "-r:System.Windows.Forms.dll",
                "-r:System.Drawing.dll",
                str(cs_source),
            ],
            check=True,
        )
    else:
        raise RuntimeError("Nessun compilatore C# trovato (csc.exe o mono-mcs)")
    return output_exe


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync and build native Windows Kira Desktop.")
    parser.add_argument("--no-shortcuts", action="store_true", help="Skip creating Desktop and Start Menu shortcuts.")
    parser.add_argument("--package", action="store_true", help="Build standalone dist/kira-desktop_<version>_windows_amd64_setup.exe installer.")
    parser.add_argument("--output", type=Path, default=None, help="Custom output path for --package.")
    args = parser.parse_args()

    if args.package:
        version = _read_version()
        out_path = args.output or (REPO_ROOT / "dist" / f"kira-desktop_{version}_windows_amd64_setup.exe")
        built = _build_windows_installer(out_path)
        print(f"Installer Windows generato: {built} ({built.stat().st_size} byte)")
        return 0

    localappdata, appdata, desktop = _detect_windows_user_dirs()
    install_dir = localappdata / "Programs" / "Kira Desktop"
    app_dir = install_dir / "app"
    install_dir.mkdir(parents=True, exist_ok=True)
    app_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Sincronizzazione sorgenti in {app_dir} ...")
    copied = _sync_tree(REPO_ROOT / "src", app_dir / "src")
    for meta in ("pyproject.toml", "README.md", "LICENSE"):
        src_meta = REPO_ROOT / meta
        if src_meta.is_file():
            shutil.copy2(src_meta, app_dir / meta)
    print(f"      File aggiornati: {copied}")

    print("[2/4] Generazione icona nativa Windows (kira.ico), eseguibile KiraDesktop.exe e launcher ...")
    _write_ico_from_png(ICON_PNG, install_dir / "kira.ico")
    _write_ico_from_png(ICON_PNG, app_dir / "src" / "kira" / "ui" / "assets" / "kira.ico")
    _build_branded_executable(install_dir)
    wsl_repo_win = _to_windows_path(REPO_ROOT)
    _write_launchers(install_dir, wsl_repo_win)

    print("[3/4] Compilazione traduzioni Qt (.qm) sull'ambiente Windows ...")
    win_python = install_dir / "venv" / "Scripts" / "python.exe"
    if win_python.is_file():
        subprocess.run(
            [
                str(win_python),
                "-c",
                "import kira; from kira.ui.language_catalog import load_catalog, native_labels; "
                "from kira.ui.translation_compiler import compiled_catalog; "
                "p = compiled_catalog('en', load_catalog('en'), native_labels('en')); "
                "print('      Traduzione compilata:', p)",
            ],
            check=True,
        )

    if not args.no_shortcuts:
        print("[4/4] Creazione collegamenti su Desktop e Menu Start di Windows ...")
        _create_windows_shortcuts(install_dir, desktop, appdata)
    print("Completato! Kira Desktop per Windows è pronto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
