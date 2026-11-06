"""Cross-platform compatibility layer for running Kira Desktop natively on Windows."""

from __future__ import annotations

import builtins
import ctypes
import errno
import os
import signal
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

IS_WINDOWS = sys.platform == "win32"

_INSTALLED = False
_DIR_FD_COUNTER = 1_000_000
_DIR_FD_PATHS: dict[int, Path] = {}
_LOCKED_FDS: set[int] = set()


def _resolve_with_dir_fd(path: Any, dir_fd: int | None) -> Path:
    raw_str = os.fsdecode(path) if not isinstance(path, int) else str(path)
    if IS_WINDOWS and raw_str == "/":
        return Path(Path.cwd().anchor or "C:\\")
    if IS_WINDOWS and len(raw_str) == 2 and raw_str[1] == ":":
        return Path(raw_str + "\\")
    p = Path(raw_str)
    if dir_fd is not None and dir_fd in _DIR_FD_PATHS and not p.is_absolute():
        return _DIR_FD_PATHS[dir_fd] / p
    return p


def _patch_stat_result(res: os.stat_result, is_symlink: bool = False) -> os.stat_result:
    """Normalize Windows stat_result so POSIX ownership and 0o700/0o600 checks succeed."""
    mode = res.st_mode
    if is_symlink:
        mode = stat.S_IFLNK | 0o777
    elif stat.S_ISDIR(mode):
        mode = stat.S_IFDIR | 0o700
    else:
        mode = stat.S_IFREG | 0o600
    ino = res.st_ino if res.st_ino > 0 else 1
    dev = res.st_dev if res.st_dev > 0 else 1
    nlink = 1 if stat.S_ISREG(mode) else max(1, res.st_nlink)
    extra: dict[str, Any] = {
        "st_atime_ns": res.st_atime_ns,
        "st_mtime_ns": res.st_mtime_ns,
        "st_ctime_ns": res.st_ctime_ns,
    }
    if hasattr(res, "st_file_attributes"):
        extra["st_file_attributes"] = getattr(res, "st_file_attributes") or 0
    if hasattr(res, "st_reparse_tag"):
        extra["st_reparse_tag"] = getattr(res, "st_reparse_tag") or 0
    return os.stat_result(
        (
            mode,
            ino,
            dev,
            nlink,
            1000,  # st_uid matches os.getuid()
            1000,  # st_gid matches os.getgid()
            res.st_size,
            res.st_atime,
            res.st_mtime,
            res.st_ctime,
        ),
        extra,
    )


def windows_boot_id() -> str:
    """Return a deterministic boot identifier for the current Windows boot session."""
    if not IS_WINDOWS:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        uptime_ms = int(kernel32.GetTickCount64())
        import time

        boot_epoch = int((time.time() - (uptime_ms / 1000.0)) // 10)
        return f"win-boot-{boot_epoch}"
    except Exception:
        return "win-boot-default"


def windows_process_identity(pid: int) -> dict[str, Any] | None:
    """Query Win32 process liveness and creation time via kernel32."""
    if not IS_WINDOWS or pid <= 0:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            exit_code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            state = "S" if exit_code.value == STILL_ACTIVE else "Z"

            creation_time = ctypes.c_ulonglong(0)
            exit_time = ctypes.c_ulonglong(0)
            kernel_time = ctypes.c_ulonglong(0)
            user_time = ctypes.c_ulonglong(0)
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation_time),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            return {
                "pid": int(pid),
                "uid": 1000,
                "boot_id": windows_boot_id(),
                "state": state,
                "ppid": os.getpid(),
                "pgid": int(pid),
                "sid": int(pid),
                "start_ticks": str(creation_time.value),
                "pid_namespace": {"device": 1, "inode": 1},
            }
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def install_windows_compat() -> None:
    """Install POSIX compatibility shims when running on Windows."""
    global _INSTALLED
    if not IS_WINDOWS or _INSTALLED:
        return
    _INSTALLED = True

    # 1. UID/GID and missing open/signal constants
    if not hasattr(os, "getuid"):
        setattr(os, "getuid", lambda: 1000)
    if not hasattr(os, "geteuid"):
        setattr(os, "geteuid", lambda: 1000)
    if not hasattr(os, "getgid"):
        setattr(os, "getgid", lambda: 1000)
    if not hasattr(os, "fchmod"):
        setattr(os, "fchmod", lambda fd, mode: None)
    if not hasattr(os, "uname"):
        import platform

        _cached_uname = platform.uname()
        setattr(os, "uname", lambda: _cached_uname)

    O_DIRECTORY_FLAG = 0x20000000
    O_PATH_FLAG = 0x40000000
    for attr, default in (
        ("O_NOFOLLOW", 0),
        ("O_DIRECTORY", O_DIRECTORY_FLAG),
        ("O_PATH", O_PATH_FLAG),
        ("O_NONBLOCK", 0),
        ("O_NOCTTY", 0),
        ("O_CLOEXEC", getattr(os, "O_NOINHERIT", 0)),
    ):
        if not hasattr(os, attr):
            setattr(os, attr, default)

    if not hasattr(signal, "SIGKILL"):
        setattr(signal, "SIGKILL", signal.SIGTERM)
    if not hasattr(signal, "SIGHUP"):
        setattr(signal, "SIGHUP", signal.SIGTERM)

    def _win_killpg(pgid: int, sig: int) -> None:
        if pgid <= 0:
            raise ProcessLookupError(errno.ESRCH, "No such process")
        info = windows_process_identity(pgid)
        if info is None or info["state"] == "Z":
            raise ProcessLookupError(errno.ESRCH, "No such process")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pgid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=creationflags,
        )

    if not hasattr(os, "killpg"):
        setattr(os, "killpg", _win_killpg)
    if not hasattr(os, "getpgid"):
        setattr(os, "getpgid", lambda pid: int(pid))

    orig_execute_child = getattr(subprocess.Popen, "_execute_child", None)
    if orig_execute_child is not None and not getattr(orig_execute_child, "_kira_win_patched", False):
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        create_new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)

        def _win_execute_child(
            self: Any,
            args: Any,
            executable: Any,
            preexec_fn: Any,
            close_fds: Any,
            pass_fds: Any,
            cwd: Any,
            env: Any,
            startupinfo: Any,
            creationflags: int,
            *rest: Any,
        ) -> Any:
            if not (int(creationflags or 0) & create_new_console):
                creationflags = int(creationflags or 0) | create_no_window
            return orig_execute_child(
                self,
                args,
                executable,
                preexec_fn,
                close_fds,
                pass_fds,
                cwd,
                env,
                startupinfo,
                creationflags,
                *rest,
            )

        setattr(_win_execute_child, "_kira_win_patched", True)
        setattr(subprocess.Popen, "_execute_child", _win_execute_child)

    # 2. fcntl shim backed by msvcrt.locking
    if "fcntl" not in sys.modules:
        import msvcrt

        fcntl_mod = ModuleType("fcntl")
        LOCK_SH = 1
        LOCK_EX = 2
        LOCK_NB = 4
        LOCK_UN = 8
        setattr(fcntl_mod, "LOCK_SH", LOCK_SH)
        setattr(fcntl_mod, "LOCK_EX", LOCK_EX)
        setattr(fcntl_mod, "LOCK_NB", LOCK_NB)
        setattr(fcntl_mod, "LOCK_UN", LOCK_UN)

        def _flock(fd: Any, operation: int) -> None:
            fileno = fd.fileno() if hasattr(fd, "fileno") else int(fd)
            try:
                os.lseek(fileno, 0, os.SEEK_SET)
                if operation & LOCK_UN:
                    if fileno in _LOCKED_FDS:
                        msvcrt.locking(fileno, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                        _LOCKED_FDS.discard(fileno)
                else:
                    mode = msvcrt.LK_NBLCK if (operation & LOCK_NB) else msvcrt.LK_LOCK  # type: ignore[attr-defined]
                    msvcrt.locking(fileno, mode, 1)  # type: ignore[attr-defined]
                    _LOCKED_FDS.add(fileno)
            except OSError as exc:
                raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable") from exc

        setattr(fcntl_mod, "flock", _flock)
        sys.modules["fcntl"] = fcntl_mod

    # 3. Wrap os.open, os.close, os.stat, os.lstat, os.fstat, os.replace, os.unlink,
    #    os.chmod, os.mkdir, os.scandir, os.listdir, os.link, os.fsync
    orig_open = os.open
    orig_close = os.close
    orig_stat = os.stat
    orig_fstat = os.fstat
    orig_replace = os.replace
    orig_unlink = os.unlink
    orig_chmod = os.chmod
    orig_fdopen = os.fdopen
    orig_mkdir = os.mkdir
    orig_scandir = os.scandir
    orig_listdir = os.listdir
    orig_link = os.link
    orig_fsync = os.fsync

    def _is_raw_symlink(target_str: str) -> bool:
        try:
            return stat.S_ISLNK(orig_stat(target_str, follow_symlinks=False).st_mode)
        except OSError:
            return False

    def _win_open(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        global _DIR_FD_COUNTER
        target = _resolve_with_dir_fd(path, dir_fd)
        target_str = str(target)
        is_dir_open = bool(flags & (O_DIRECTORY_FLAG | O_PATH_FLAG))
        clean_flags = flags & ~(O_DIRECTORY_FLAG | O_PATH_FLAG)
        if _is_raw_symlink(target_str):
            raise OSError(errno.ELOOP, "Too many levels of symbolic links", target_str)
        try:
            raw_info = orig_stat(target_str, follow_symlinks=False)
            target_exists = True
            target_is_dir = stat.S_ISDIR(raw_info.st_mode)
        except OSError:
            target_exists = False
            target_is_dir = False
        if is_dir_open or target_is_dir:
            if not target_exists:
                raise FileNotFoundError(errno.ENOENT, "No such directory", target_str)
            if not target_is_dir:
                raise NotADirectoryError(errno.ENOTDIR, "Not a directory", target_str)
            _DIR_FD_COUNTER += 1
            fake_fd = _DIR_FD_COUNTER
            _DIR_FD_PATHS[fake_fd] = Path(os.path.abspath(target_str))
            return fake_fd
        clean_flags |= getattr(os, "O_BINARY", 0)
        return orig_open(target_str, clean_flags, 0o666)

    def _win_close(fd: int) -> None:
        if fd in _DIR_FD_PATHS:
            _DIR_FD_PATHS.pop(fd, None)
            return
        _LOCKED_FDS.discard(fd)
        orig_close(fd)

    def _win_stat(
        path: Any,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if isinstance(path, int):
            if path in _DIR_FD_PATHS:
                res = orig_stat(str(_DIR_FD_PATHS[path]))
                return _patch_stat_result(res, False)
            return _patch_stat_result(orig_fstat(path), False)
        target = _resolve_with_dir_fd(path, dir_fd)
        raw = orig_stat(str(target), follow_symlinks=follow_symlinks)
        is_link = not follow_symlinks and stat.S_ISLNK(raw.st_mode)
        return _patch_stat_result(raw, is_link)

    def _win_lstat(path: Any, *, dir_fd: int | None = None) -> os.stat_result:
        return _win_stat(path, dir_fd=dir_fd, follow_symlinks=False)

    def _win_fstat(fd: int) -> os.stat_result:
        if fd in _DIR_FD_PATHS:
            return _win_stat(_DIR_FD_PATHS[fd])
        return _patch_stat_result(orig_fstat(fd), False)

    def _win_replace(
        src: Any,
        dst: Any,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        s = _resolve_with_dir_fd(src, src_dir_fd)
        d = _resolve_with_dir_fd(dst, dst_dir_fd)
        try:
            orig_chmod(str(s), stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        if d.exists():
            try:
                orig_chmod(str(d), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        orig_replace(str(s), str(d))

    def _win_unlink(path: Any, *, dir_fd: int | None = None) -> None:
        target = _resolve_with_dir_fd(path, dir_fd)
        try:
            orig_chmod(str(target), stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        orig_unlink(str(target))

    def _win_chmod(path: Any, mode: int, *, dir_fd: int | None = None, follow_symlinks: bool = True) -> None:
        if isinstance(path, int):
            return
        target = _resolve_with_dir_fd(path, dir_fd)
        win_mode = stat.S_IREAD | (stat.S_IWRITE if (mode & 0o200) else 0)
        try:
            orig_chmod(str(target), win_mode or (stat.S_IREAD | stat.S_IWRITE))
        except OSError:
            pass

    def _win_mkdir(path: Any, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        target = _resolve_with_dir_fd(path, dir_fd)
        orig_mkdir(str(target), 0o777)

    def _win_scandir(path: Any = ".") -> Any:
        if isinstance(path, int) and path in _DIR_FD_PATHS:
            return orig_scandir(str(_DIR_FD_PATHS[path]))
        return orig_scandir(path)

    def _win_listdir(path: Any = ".") -> list[Any]:
        if isinstance(path, int) and path in _DIR_FD_PATHS:
            return orig_listdir(str(_DIR_FD_PATHS[path]))
        return orig_listdir(path)

    def _win_link(
        src: Any,
        dst: Any,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        s = _resolve_with_dir_fd(src, src_dir_fd)
        d = _resolve_with_dir_fd(dst, dst_dir_fd)
        orig_link(str(s), str(d), follow_symlinks=follow_symlinks)

    def _win_fsync(fd: int) -> None:
        if fd in _DIR_FD_PATHS:
            return
        try:
            orig_fsync(fd)
        except OSError:
            pass

    def _win_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if fd in _DIR_FD_PATHS:
            raise IsADirectoryError(errno.EISDIR, "Is a directory")
        return orig_fdopen(fd, *args, **kwargs)

    setattr(os, "open", _win_open)
    setattr(os, "close", _win_close)
    setattr(os, "stat", _win_stat)
    setattr(os, "lstat", _win_lstat)
    setattr(os, "fstat", _win_fstat)
    setattr(os, "replace", _win_replace)
    setattr(os, "unlink", _win_unlink)
    setattr(os, "remove", _win_unlink)
    setattr(os, "chmod", _win_chmod)
    setattr(os, "mkdir", _win_mkdir)
    setattr(os, "scandir", _win_scandir)
    setattr(os, "listdir", _win_listdir)
    setattr(os, "link", _win_link)
    setattr(os, "fsync", _win_fsync)
    setattr(os, "fdopen", _win_fdopen)
    setattr(builtins, "_kira_win_compat", True)
