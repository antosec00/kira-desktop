"""The parts of the Windows compatibility layer that decide what a name may reach.

On Windows a directory descriptor is emulated by a path, and a name used with
it is joined to that path. These rules run on every platform.
"""

import errno
import os
import stat
from pathlib import Path, PureWindowsPath

import pytest

from kira.domain import compat


def test_joining_a_root_or_a_drive_leaves_the_folder_on_windows():
    # Why a name is never joined unchecked: this is how Windows paths combine.
    folder = PureWindowsPath(r"C:\work\fixture\project")
    assert folder / "\\" == PureWindowsPath("C:\\")
    assert folder / "\\Windows" == PureWindowsPath(r"C:\Windows")
    assert folder / "D:" == PureWindowsPath("D:")
    assert folder / "D:\\" == PureWindowsPath("D:\\")


@pytest.mark.parametrize("name", ["notes.txt", ".", ".hidden", "name with spaces", "a.b.c", "x~1", "perch\u00e9"])
def test_single_entry_names_are_accepted(name):
    assert compat._entry_name(name) == name


@pytest.mark.parametrize("name", ["", "..", "\\", "/", "a/b", "a\\b", "\\Windows", "/Windows", "C:", "C:\\", "C:x",
                                  "notes.txt:stream", "..\\x", "../x", "\\\\server\\share"])
def test_names_that_could_leave_the_folder_are_refused(name):
    with pytest.raises(PermissionError):
        compat._entry_name(name)


def test_a_name_is_resolved_only_inside_its_opened_folder(monkeypatch, tmp_path):
    monkeypatch.setitem(compat._DIR_FD_PATHS, 1_999_999, tmp_path)
    assert compat.directory_path(1_999_999) == tmp_path
    assert compat._resolve_with_dir_fd("notes.txt", 1_999_999) == tmp_path / "notes.txt"
    assert compat._resolve_with_dir_fd(".", 1_999_999) == tmp_path / "."
    for name in ("\\", "C:", "..", "sub/notes.txt", "/etc/passwd", str(tmp_path.parent / "other")):
        with pytest.raises(PermissionError):
            compat._resolve_with_dir_fd(name, 1_999_999)


def test_an_unknown_directory_descriptor_is_an_error_not_the_current_folder():
    assert compat.directory_path(1_999_998) is None
    with pytest.raises(OSError) as caught:
        compat._resolve_with_dir_fd("notes.txt", 1_999_998)
    assert caught.value.errno == errno.EBADF


def test_a_path_without_descriptor_is_left_as_it_is():
    assert compat._resolve_with_dir_fd("some/relative/path", None) == Path("some/relative/path")


def test_only_regular_files_are_reported_as_regular(tmp_path):
    regular = tmp_path / "file.txt"
    regular.write_text("x")
    assert stat.S_ISREG(compat._patch_stat_result(os.stat(regular)).st_mode)
    assert stat.S_IMODE(compat._patch_stat_result(os.stat(regular)).st_mode) == 0o600
    assert stat.S_ISDIR(compat._patch_stat_result(os.stat(tmp_path)).st_mode)
    assert stat.S_IMODE(compat._patch_stat_result(os.stat(tmp_path)).st_mode) == 0o700
    # A device (NUL, CON, a serial port on Windows) keeps its type and is refused by the file tools.
    device = compat._patch_stat_result(os.stat(os.devnull))
    assert stat.S_ISCHR(device.st_mode) and not stat.S_ISREG(device.st_mode)
    assert stat.S_ISLNK(compat._patch_stat_result(os.stat(regular), True).st_mode)
