"""Lexical path rules, with Windows semantics forced so that they run on every platform."""

from pathlib import PurePosixPath, PureWindowsPath

import pytest

from kira.domain.contracts import KiraError
from kira.domain.project_paths import (
    git_metadata_name,
    host_parts,
    project_relative_parts,
    windows_name_problem,
)

ROOT = PureWindowsPath(r"C:\work\fixture\project")


def windows_parts(value: str, **options) -> tuple[str, ...]:
    return project_relative_parts(value, ROOT, windows=True, **options)


@pytest.mark.parametrize("value", [
    # A root without a drive: relative to the current drive, not to the project.
    r"\Windows\win.ini", "/Windows/win.ini", "\\", "/",
    # A drive without a root: relative to the current folder of that drive.
    "C:Windows", "C:", "D:notes.txt", "c:project\\notes.txt",
    # Shares and device namespaces, even when they spell a path inside the project.
    r"\\server\share\notes.txt", "//server/share/notes.txt", r"\\server", r"\\localhost\C$\Windows",
    r"\\?\C:\work\fixture\project\notes.txt", "//?/C:/work/fixture/project/notes.txt",
    r"\\.\C:\work\fixture\project\notes.txt", r"\\.\NUL", r"\\.\pipe\fixture",
    r"\\?\UNC\server\share\notes.txt", r"\??\C:\Windows\win.ini",
    # Absolute paths outside the project, including a sibling with the same prefix.
    r"C:\Windows\win.ini", r"C:\work\fixture\project-other\notes.txt", r"D:\work\fixture\project\notes.txt",
])
def test_windows_paths_that_leave_the_project_are_refused(value):
    with pytest.raises(KiraError, match="outside the project") as caught:
        windows_parts(value)
    assert caught.value.code == "PATH_DENIED"


@pytest.mark.parametrize("value", [
    "sub/C:/work/fixture/other.txt", "sub\\C:\\Windows\\win.ini", "sub/C:notes.txt", "sub/D:",
    "sub/sub/c:/windows/win.ini", r"C:\work\fixture\project\sub\D:\notes.txt",
])
def test_windows_drive_letter_inside_a_path_is_refused(value):
    # Joined to an opened folder, "C:" used to lead to the root of that drive.
    with pytest.raises(KiraError, match="drive letter") as caught:
        windows_parts(value)
    assert caught.value.code == "PATH_DENIED"


@pytest.mark.parametrize("value", [
    r"..\outside.txt", "../outside.txt", r"sub\..\..\outside.txt", "sub/..\\../outside.txt",
    r"C:\work\fixture\project\..\outside.txt",
])
def test_windows_parent_traversal_is_refused_with_any_separator(value):
    with pytest.raises(KiraError, match="Parent traversal") as caught:
        windows_parts(value)
    assert caught.value.code == "PATH_DENIED"


@pytest.mark.parametrize("value", [
    # Dots and spaces that Windows drops or reads as another folder.
    ".. \\outside.txt", "...\\outside.txt", ". .\\outside.txt", " \\notes.txt", "sub\\ \\notes.txt",
    "sub.\\notes.txt", "notes.txt.", "notes.txt ", "notes.txt. .", "sub /notes.txt",
    # Alternate data streams.
    "notes.txt:stream", "notes.txt::$DATA", "sub:stream/notes.txt", "ab:cd",
    # Devices, in any folder, with any extension and case.
    "NUL", "nul", "nul.txt", "sub\\NUL", "CON", "con.txt", "PRN", "AUX.tar.gz", "COM1", "com9.log",
    "LPT1 .txt", "COM0", "CONIN$", "CONOUT$", "COM\u00b9", "lpt\u00b3.txt",
    # Wildcards and characters that no Windows name contains.
    "no*es.txt", "note?.txt", "a<b", "a>b", "a|b", 'a"b', "tab\tname.txt", "line\nname.txt",
    r"C:\work\fixture\project\NUL", r"C:\work\fixture\project\notes.txt:stream",
])
def test_windows_names_that_are_not_plain_entries_are_refused(value):
    with pytest.raises(KiraError, match="Not a plain file or folder name") as caught:
        windows_parts(value)
    assert caught.value.code == "PATH_DENIED"
    # The same names stay refused where ".." is left to the caller.
    with pytest.raises(KiraError):
        windows_parts(value, allow_parent=True)


@pytest.mark.parametrize("value, expected", [
    ("notes.txt", ("notes.txt",)),
    ("./notes.txt", ("notes.txt",)),
    (".", ()),
    ("", ()),
    ("sub/folder with spaces/perch\u00e9.txt", ("sub", "folder with spaces", "perch\u00e9.txt")),
    ("sub\\folder with spaces\\perch\u00e9.txt", ("sub", "folder with spaces", "perch\u00e9.txt")),
    ("sub//double\\\\separators/x.txt", ("sub", "double", "separators", "x.txt")),
    ("a.b.c/file.v1.2.txt", ("a.b.c", "file.v1.2.txt")),
    (".env.example", (".env.example",)),
    (".git/config", (".git", "config")),
    ("copy~1.txt", ("copy~1.txt",)),
    ("it's & (1) [x] {y} #1 @2 +3 =4,5;6.txt", ("it's & (1) [x] {y} #1 @2 +3 =4,5;6.txt",)),
    (" leading space.txt", (" leading space.txt",)),
    ("com10.txt", ("com10.txt",)),
    ("console.log", ("console.log",)),
    ("NULLA.txt", ("NULLA.txt",)),
    ("aux1", ("aux1",)),
    (r"C:\work\fixture\project", ()),
    (r"C:\work\fixture\project\sub\notes.txt", ("sub", "notes.txt")),
    ("c:/WORK/FIXTURE/Project/sub/notes.txt", ("sub", "notes.txt")),
])
def test_windows_paths_inside_the_project_keep_working(value, expected):
    assert windows_parts(value) == expected


def test_parent_components_are_kept_only_on_request():
    assert windows_parts("sub/../notes.txt", allow_parent=True) == ("sub", "..", "notes.txt")
    assert windows_parts(r"C:\work\fixture\project\..\x", allow_parent=True) == ("..", "x")
    for value in ("\\Windows", "C:x", "//server/share/x", "sub/C:/x", "NUL"):
        with pytest.raises(KiraError):
            windows_parts(value, allow_parent=True)


def test_posix_rules_are_unchanged():
    root = PurePosixPath("/srv/fixture/project")

    def parts(value: str) -> tuple[str, ...]:
        return project_relative_parts(value, root, windows=False)

    assert parts("/srv/fixture/project/sub/notes.txt") == ("sub", "notes.txt")
    assert parts("sub//notes.txt") == ("sub", "notes.txt")
    # Names that only Windows reads differently are ordinary names here.
    for name in ("NUL", "notes.txt.", "notes.txt ", "a:b", "back\\slash", "C:", "...", "what?.txt"):
        assert parts(name) == (name,)
    for value in ("/etc/passwd", "/srv/fixture/project-other/x", "/"):
        with pytest.raises(KiraError, match="outside the project"):
            parts(value)
    for value in ("../x", "sub/../../x", "/srv/fixture/project/../x"):
        with pytest.raises(KiraError, match="Parent traversal"):
            parts(value)


@pytest.mark.parametrize("name, problem", [
    ("", "empty"), (".", "relative"), ("..", "relative"), ("a/b", "separator"), ("a\\b", "separator"),
    ("C:", "drive"), ("x:y", "stream"), ("x.", "trailing"), ("x ", "trailing"), ("...", "trailing"),
    ("NUL", "device"), ("nul.tar.gz", "device"), ("Com1 .txt", "device"), ("a*b", "reserved"),
    ("a\x1fb", "reserved"),
])
def test_windows_name_problem_names_the_reason(name, problem):
    assert problem in (windows_name_problem(name) or "")


@pytest.mark.parametrize("name", ["notes.txt", ".git", ".hidden", "name with spaces", "a.b.c", "x~1",
                                  "perch\u00e9", "null", "con1", "com", "lpt", "$recycle.bin", "~$doc.tmp"])
def test_windows_plain_names_have_no_problem(name):
    assert windows_name_problem(name) is None


@pytest.mark.parametrize("name", [".git", ".GIT", ".Git", ".git.", ".git ", ".git. .", ".git...", "GIT~1", "git~1",
                                  "Git~12", "GI1A2B~1", "gi0f3c~2", ".git::$INDEX_ALLOCATION", ".git:stream",
                                  "GIT~1.", "git~1::$INDEX_ALLOCATION"])
def test_every_spelling_of_git_metadata_is_recognized(name):
    assert git_metadata_name(name)


@pytest.mark.parametrize("name", [".gitignore", ".github", ".gitattributes", ".gitmodules", "git", "x.git", ".git2",
                                  "GITHUB~1", "GITIGN~1", "digit~1", "gi~1", "notes.txt", ".gitx.", "legit"])
def test_other_names_are_not_git_metadata(name):
    assert not git_metadata_name(name)


@pytest.mark.parametrize("value, expected", [
    (r"C:\Windows\win.ini", ("Windows", "win.ini")),
    (r"\Windows\win.ini", ("Windows", "win.ini")),
    (r"..\other\notes.txt", ("work", "fixture", "other", "notes.txt")),
    ("notes.txt", ("work", "fixture", "project", "notes.txt")),
    (r"D:\data\notes.txt", ("D:\\", "data", "notes.txt")),
    ("D:/data/../notes.txt", ("D:\\", "notes.txt")),
    (r"\\server\share\folder\notes.txt", ("\\\\server\\share\\", "folder", "notes.txt")),
    (r"\\?\C:\data\notes.txt", ("\\\\?\\C:\\", "data", "notes.txt")),
])
def test_full_access_paths_keep_their_anchor_and_plain_names(value, expected):
    assert host_parts(value, ROOT, windows=True) == expected


@pytest.mark.parametrize("value", [r"C:\data\NUL", r"D:\data\notes.txt:stream", r"C:\data.\notes.txt",
                                   r"\\server\share\con.txt", "sub\\x.txt "])
def test_full_access_still_refuses_names_that_are_not_plain_entries(value):
    with pytest.raises(KiraError, match="Not a plain file or folder name"):
        host_parts(value, ROOT, windows=True)


def test_full_access_never_keeps_a_parent_component():
    # Under a device prefix Windows reads ".." as a name: it is collapsed or refused, never walked.
    try:
        parts = host_parts(r"\\?\C:\data\..\notes.txt", ROOT, windows=True)
    except KiraError as exc:
        assert exc.code == "PATH_DENIED"
    else:
        assert ".." not in parts


def test_full_access_posix_paths_are_unchanged():
    root = PurePosixPath("/srv/fixture/project")
    assert host_parts("/etc/hostname", root, windows=False) == ("etc", "hostname")
    assert host_parts("../other/x", root, windows=False) == ("srv", "fixture", "other", "x")
    assert host_parts("a/./b/../c", root, windows=False) == ("srv", "fixture", "project", "a", "c")
    assert host_parts("NUL", root, windows=False) == ("srv", "fixture", "project", "NUL")
