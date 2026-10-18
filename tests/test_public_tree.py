"""Publication checks must catch private content without printing its value."""

import runpy
import subprocess
from pathlib import Path

import pytest

CHECKER = runpy.run_path(str(Path(__file__).parents[1] / "scripts/check_public_tree.py"))
inspect_content = CHECKER["inspect_content"]
inspect_repository = CHECKER["inspect_repository"]


@pytest.mark.parametrize("content", [
    b"/home/" + b"personal-user/project",
    b"file:///Users/" + b"personal-user/project",
    b"C:\\Users\\" + b"personal-user\\project",
    b"C:\\\\Users\\\\" + b"personal-user\\\\project",
])
def test_private_paths_are_reported_without_values(content):
    assert inspect_content("README.md", content) == {"personal-home-path"}


def test_standard_paths_and_scoped_fixture_exceptions():
    assert inspect_content("README.md", b"${XDG_DATA_HOME:-$HOME/.local/share}/kira") == set()
    fixture = b"/home/" + b"fixture/project"
    assert inspect_content("tests/test_markdown.py", fixture) == set()
    assert inspect_content("README.md", fixture) == {"personal-home-path"}


def test_credential_database_and_unreviewed_image_rejected():
    token = b"ghp_" + b"X" * 36
    assert inspect_content("config.txt", token) == {"github-token"}
    assert "database" in inspect_content("renamed.bin", b"SQLite format 3\x00")
    assert "unreviewed-binary" in inspect_content("screenshot.png", b"\x89PNG\xff")
    assert "private-file-or-raw-report" in inspect_content("docs/evidence/raw.json", b"{}")


def test_history_detects_removed_private_file_and_commit_identity(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args],
                                       stderr=subprocess.DEVNULL)

    git("init", "-b", "main")
    git("config", "user.name", "Fixture author")
    git("config", "user.email", "fixture@example.invalid")
    path = tmp_path / "private.txt"
    path.write_bytes(b"/home/" + b"personal-user/private")
    git("add", ".")
    git("commit", "-m", "Fixture initial")
    path.write_text("Public instructions\n")
    git("add", ".")
    git("commit", "-m", "Fixture cleanup")
    assert inspect_repository(tmp_path) == set()
    assert inspect_repository(tmp_path, "HEAD") == set()
    assert inspect_repository(tmp_path, "HEAD", history=True) == {
        "private.txt: personal-home-path",
    }
    git("-c", "user.email=fixture@example.org", "commit", "--allow-empty", "-m", "Identity")
    assert any("review-commit-email" in f for f in inspect_repository(tmp_path, history=True))


def test_symlink_target_is_not_read(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "outside").symlink_to(tmp_path.parent / "missing-private-file")
    assert inspect_repository(tmp_path) == {"outside: unreviewed-symlink-or-submodule"}
