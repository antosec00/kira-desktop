"""OS-level process sandboxing for tool and command execution (Landlock LSM & Bubblewrap)."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
import functools
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
from typing import Any

from kira.domain.contracts import KiraError

SANDBOX_MODES = ("unrestricted", "workspace_write", "read_only")

# Linux prctl constants
PR_SET_NO_NEW_PRIVS = 38

# Linux syscall numbers for Landlock (identical across x86_64, aarch64, riscv64)
SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1

# Landlock filesystem access flags (ABI v1..v3)
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
# The only rights a rule on a file (not a directory) may carry: the kernel
# rejects the whole rule with EINVAL when any directory-only right is included.
LANDLOCK_FILE_RIGHTS = (LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_WRITE_FILE
                        | LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_TRUNCATE)

O_PATH = getattr(os, "O_PATH", 0o10000000)


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


@functools.lru_cache(maxsize=1)
def _libc() -> Any:
    if platform.system() != "Linux":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        return libc
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def landlock_abi_version() -> int:
    """Return the kernel Landlock ABI version (0 if unsupported or disabled)."""
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "aarch64", "riscv64"}:
        return 0
    libc = _libc()
    if libc is None:
        return 0
    try:
        abi = int(
            libc.syscall(
                ctypes.c_long(SYS_LANDLOCK_CREATE_RULESET),
                ctypes.c_void_p(0),
                ctypes.c_size_t(0),
                ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION),
            )
        )
        return abi if abi > 0 else 0
    except Exception:
        return 0


@functools.lru_cache(maxsize=1)
def bwrap_usable() -> bool:
    """Return True if bubblewrap is installed and permitted to create user namespaces."""
    if platform.system() != "Linux":
        return False
    binary = shutil.which("bwrap")
    if not binary:
        return False
    try:
        completed = subprocess.run(
            [binary, "--ro-bind", "/", "/", "--", "/bin/true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def landlock_usable() -> bool:
    """Whether a child process is really confined here, not only whether the kernel answers.

    One trial child applies the same rules a command gets. A kernel that
    reports Landlock but refuses to enforce it counts as unavailable, so the
    command is reported as unconfined instead of being trusted to be confined.
    """
    if landlock_abi_version() <= 0:
        return False

    def trial() -> None:
        if not apply_landlock_sandbox(Path("/"), "read_only"):
            raise OSError("Landlock rules were not enforced")

    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", "pass"], preexec_fn=trial,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False)
        return completed.returncode == 0
    except Exception:
        return False


def confinement(mode: str, *, wrapper: bool = True) -> str:
    """The mechanism a process started now with this mode gets: landlock, bwrap or none.

    ``wrapper=False`` is for processes that cannot be wrapped in another
    program's argv and therefore rely on Landlock alone.
    """
    if mode not in SANDBOX_MODES:
        raise KiraError("INVALID_ARGUMENT", f"Unsupported sandbox mode: {mode}")
    if mode == "unrestricted":
        return "none"
    if landlock_usable():
        return "landlock"
    if wrapper and bwrap_usable():
        return "bwrap"
    return "none"


def confinement_record(mode: str, applied: str) -> dict[str, str]:
    """What a tool result states about the write confinement of the process it ran.

    When confinement was requested and none could be applied, the record
    carries a code: the receipt shows it, so an unconfined command under
    Trusted host is never indistinguishable from a confined one.
    """
    record = {"mode": mode, "applied": applied}
    if mode != "unrestricted" and applied == "none":
        record["code"] = "write_confinement_unavailable"
        record["message"] = ("This system offers no write confinement: the process could write "
                             "wherever the user can.")
    return record


def probe_sandbox() -> dict[str, Any]:
    """Report available OS-level sandboxing capabilities on this host."""
    abi = landlock_abi_version()
    bwrap = bwrap_usable()
    backend = "landlock" if landlock_usable() else ("bwrap" if bwrap else "none")
    return {
        "available": backend != "none",
        "backend": backend,
        "landlock_abi": abi,
        "bwrap_available": bwrap,
    }


def sandbox_tmpdir(root: Path) -> Path:
    """Return the writable temporary directory for a sandboxed command."""
    resolved_root = root.resolve()
    tmp_root = Path("/tmp").resolve()
    if resolved_root == tmp_root or resolved_root.is_relative_to(tmp_root):
        dedicated = tmp_root / f"kira-sandbox-{os.getuid()}"
        dedicated.mkdir(mode=0o700, exist_ok=True)
        return dedicated
    return tmp_root


def _handled_fs_mask(abi: int) -> int:
    mask = (
        LANDLOCK_ACCESS_FS_EXECUTE
        | LANDLOCK_ACCESS_FS_WRITE_FILE
        | LANDLOCK_ACCESS_FS_READ_FILE
        | LANDLOCK_ACCESS_FS_READ_DIR
        | LANDLOCK_ACCESS_FS_REMOVE_DIR
        | LANDLOCK_ACCESS_FS_REMOVE_FILE
        | LANDLOCK_ACCESS_FS_MAKE_CHAR
        | LANDLOCK_ACCESS_FS_MAKE_DIR
        | LANDLOCK_ACCESS_FS_MAKE_REG
        | LANDLOCK_ACCESS_FS_MAKE_SOCK
        | LANDLOCK_ACCESS_FS_MAKE_FIFO
        | LANDLOCK_ACCESS_FS_MAKE_BLOCK
        | LANDLOCK_ACCESS_FS_MAKE_SYM
    )
    if abi >= 2:
        mask |= LANDLOCK_ACCESS_FS_REFER
    if abi >= 3:
        mask |= LANDLOCK_ACCESS_FS_TRUNCATE
    return mask


def _add_path_rule(libc: Any, ruleset_fd: int, path: Path, allowed_access: int) -> bool:
    try:
        fd = os.open(str(path), O_PATH | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            allowed_access &= LANDLOCK_FILE_RIGHTS
        rule = _LandlockPathBeneathAttr(allowed_access=allowed_access, parent_fd=fd)
        return int(libc.syscall(
            ctypes.c_long(SYS_LANDLOCK_ADD_RULE),
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(rule),
            ctypes.c_uint32(0),
        )) == 0
    except OSError:
        return False
    finally:
        os.close(fd)


def apply_landlock_sandbox(root: Path, mode: str) -> bool:
    """Enforce Landlock filesystem sandbox on the calling process (intended for preexec_fn)."""
    if mode not in SANDBOX_MODES:
        raise KiraError("INVALID_ARGUMENT", f"Unsupported sandbox mode: {mode}")
    if mode == "unrestricted":
        return False
    abi = landlock_abi_version()
    libc = _libc()
    if abi <= 0 or libc is None:
        return False

    # Prevent privilege escalation via setuid binaries and authorize landlock_restrict_self
    if libc.prctl(ctypes.c_int(PR_SET_NO_NEW_PRIVS), ctypes.c_ulong(1), ctypes.c_ulong(0),
                  ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        return False

    handled = _handled_fs_mask(abi)
    # struct landlock_ruleset_attr was 8 bytes in ABI v1..v3 (handled_access_fs only)
    attr = _LandlockRulesetAttr(handled_access_fs=handled, handled_access_net=0)
    ruleset_fd = int(
        libc.syscall(
            ctypes.c_long(SYS_LANDLOCK_CREATE_RULESET),
            ctypes.byref(attr),
            ctypes.c_size_t(8),
            ctypes.c_uint32(0),
        )
    )
    if ruleset_fd < 0:
        return False

    try:
        read_exec = (
            LANDLOCK_ACCESS_FS_EXECUTE
            | LANDLOCK_ACCESS_FS_READ_FILE
            | LANDLOCK_ACCESS_FS_READ_DIR
        )
        read_write = handled & ~(LANDLOCK_ACCESS_FS_MAKE_BLOCK | LANDLOCK_ACCESS_FS_MAKE_CHAR)
        file_write = read_exec | LANDLOCK_ACCESS_FS_WRITE_FILE | (
            LANDLOCK_ACCESS_FS_TRUNCATE if abi >= 3 else 0
        )

        # Allow reading/executing across the filesystem while blocking writes outside allowed roots.
        # Without this rule nothing could even be executed, so a failure is reported.
        if not _add_path_rule(libc, ruleset_fd, Path("/"), read_exec):
            return False

        # Allow writes to the sandbox temp directory and harmless shared/device nodes
        temp_target = sandbox_tmpdir(root)
        if temp_target.exists():
            _add_path_rule(libc, ruleset_fd, temp_target, read_write)
        shm = Path("/dev/shm")
        if shm.exists():
            _add_path_rule(libc, ruleset_fd, shm, read_write)

        for dev_node in ("/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom"):
            candidate = Path(dev_node)
            if candidate.exists():
                _add_path_rule(libc, ruleset_fd, candidate, file_write)

        if mode == "workspace_write":
            resolved_root = root.resolve()
            if not _add_path_rule(libc, ruleset_fd, resolved_root, read_write):
                return False

        rc = int(
            libc.syscall(
                ctypes.c_long(SYS_LANDLOCK_RESTRICT_SELF),
                ctypes.c_int(ruleset_fd),
                ctypes.c_uint32(0),
            )
        )
        return rc == 0
    finally:
        os.close(ruleset_fd)


def sandbox_preexec(root: Path, mode: str) -> Callable[[], None] | None:
    """Return a preexec_fn that confines the child process using Landlock when supported.

    Where Landlock works, a child whose rules cannot be enforced is not
    started at all: the caller reports the failure instead of running the
    process unconfined behind a result that says otherwise.
    """
    if mode not in SANDBOX_MODES:
        raise KiraError("INVALID_ARGUMENT", f"Unsupported sandbox mode: {mode}")
    if mode == "unrestricted" or not landlock_usable():
        return None
    resolved_root = root.resolve()
    # Ensure dedicated sandbox tmpdir exists before fork if root is inside /tmp
    sandbox_tmpdir(resolved_root)

    def _preexec() -> None:
        if not apply_landlock_sandbox(resolved_root, mode):
            raise OSError("Landlock rules were not enforced")

    return _preexec


def wrap_bwrap_argv(argv: list[str], root: Path, mode: str) -> list[str]:
    """Wrap argv with bubblewrap when Landlock is unavailable and bwrap is usable."""
    if mode == "unrestricted" or not bwrap_usable():
        return argv
    binary = shutil.which("bwrap")
    if not binary:
        return argv
    resolved_root = str(root.resolve())
    wrapped = [
        binary,
        "--die-with-parent",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
    ]
    if mode == "workspace_write":
        wrapped.extend(["--bind", resolved_root, resolved_root])
    wrapped.extend(["--chdir", resolved_root, "--", *argv])
    return wrapped
