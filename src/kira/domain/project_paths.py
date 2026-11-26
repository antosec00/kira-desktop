"""Lexical rules for a path that a model, a mention or a tool names inside a project.

Nothing here touches the file system: a path is accepted or refused from its
text alone, so a refused path never opens a file, a device or a network share.
The file tools then reach an accepted path one entry at a time, without
following links.
"""

import ntpath
import posixpath
import re
import sys
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from kira.domain.contracts import KiraError

OUTSIDE_PROJECT = "Path is outside the project"
PARENT_TRAVERSAL = "Parent traversal is not allowed"
# Windows opens these as devices in every folder, also with an extension or
# trailing spaces ("nul.txt", "COM1 .log"); the superscript digits count too.
WINDOWS_DEVICE_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"{port}{digit}" for port in ("COM", "LPT") for digit in "0123456789¹²³")})
_WINDOWS_RESERVED_CHARACTERS = frozenset('<>"|?*')
# The short (8.3) names NTFS generates for ".git": "GIT~1", or two letters and
# four hexadecimal digits once the plain form is taken ("GI1A2B~1").
_GIT_SHORT_NAME = re.compile(r"(?:git|gi[0-9a-f]{4})~[0-9]+")


def windows_semantics(windows: bool | None = None) -> bool:
    """Whether Windows path rules apply: this platform, unless a caller states it."""
    return sys.platform == "win32" if windows is None else windows


def windows_name_problem(name: str) -> str | None:
    """Why Windows would not open the plain entry called ``name``, or None.

    Windows drops trailing dots and spaces, reads ``:`` as a drive letter or
    an alternate data stream and reserves device names in every folder. Such a
    name reaches something other than the file or folder it spells, so it is
    never an entry of a project.
    """
    if not name:
        return "empty name"
    if name in {".", ".."}:
        return "relative folder reference"
    if "/" in name or "\\" in name:
        return "path separator"
    if ":" in name:
        return "drive letter or alternate data stream"
    if _WINDOWS_RESERVED_CHARACTERS.intersection(name) or any(ord(character) < 32 for character in name):
        return "reserved character"
    if name[-1] in ". ":
        return "trailing dot or space"
    if name.partition(".")[0].rstrip(" ").upper() in WINDOWS_DEVICE_NAMES:
        return "device name"
    return None


def git_metadata_name(name: str) -> bool:
    """Whether a file system can resolve ``name`` to a ``.git`` entry.

    NTFS reaches ``.git`` also without regard to case, through a trailing dot
    or space, through a stream suffix and through its generated short name.
    This holds on Windows and on Windows drives mounted in Linux, so the
    spelling is judged the same way on every platform.
    """
    plain = name.split(":", 1)[0].rstrip(". ").casefold()
    return plain == ".git" or _GIT_SHORT_NAME.fullmatch(plain) is not None


def project_relative_parts(value: str, root: PurePath | str, *, windows: bool | None = None,
                           allow_parent: bool = False) -> tuple[str, ...]:
    """Names of the entries that lead from the project ``root`` to ``value``.

    ``value`` is a path relative to the project, or an absolute path inside
    it; anything else is PATH_DENIED. On Windows a path with a root but no
    drive (``\\x``) or a drive but no root (``C:x``) is relative to the current
    drive or folder of the process, never to the project, and every name must
    be a plain entry (see ``windows_name_problem``): a drive letter inside the
    path, a device or share prefix, a stream or a device name is refused.

    ``allow_parent`` keeps ``..`` for callers that resolve the result and
    verify themselves that it stays inside the project.
    """
    windows = windows_semantics(windows)
    flavour = PureWindowsPath if windows else PurePosixPath
    candidate = flavour(value)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(flavour(root))
        except ValueError:
            raise KiraError("PATH_DENIED", OUTSIDE_PROJECT) from None
    elif candidate.anchor:
        raise KiraError("PATH_DENIED", OUTSIDE_PROJECT)
    for name in candidate.parts:
        if name == "..":
            if not allow_parent:
                raise KiraError("PATH_DENIED", PARENT_TRAVERSAL)
        elif windows:
            _require_plain_windows_name(name)
    return candidate.parts


def host_parts(value: str, root: PurePath | str, *, windows: bool | None = None) -> tuple[str, ...]:
    """Normalized parts of a path anywhere on the host, for full access.

    A relative path starts in the project. On Windows the first part is the
    anchor (drive, share or device prefix) when it differs from the project's;
    the names after it must be plain entries, so that a device, a stream or a
    name that Windows rewrites is refused in every scope.
    """
    windows = windows_semantics(windows)
    flavour, normalize = ((PureWindowsPath, ntpath.normpath) if windows
                          else (PurePosixPath, posixpath.normpath))
    base = flavour(root)
    candidate = flavour(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    normalized = flavour(normalize(str(candidate)))
    if windows:
        for name in normalized.parts[1:]:
            _require_plain_windows_name(name)
        if normalized.anchor.upper() != base.anchor.upper():
            return normalized.parts
    return normalized.parts[1:]


def _require_plain_windows_name(name: str) -> None:
    problem = windows_name_problem(name)
    if problem:
        raise KiraError("PATH_DENIED", f"Not a plain file or folder name on Windows: {problem}")
