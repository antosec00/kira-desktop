"""Keep each package target's writable paths, ABI contract and contents consistent."""

import csv
import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESOLUTE = runpy.run_path(str(ROOT / "packaging/resolute/build.py"))
NOBLE = runpy.run_path(str(ROOT / "scripts/build_deb.py"))


def test_resolute_builder_cannot_write_noble_artifacts_or_checkout(tmp_path):
    command = RESOLUTE["container_command"](tmp_path, "sha256:fixture")
    mounts = [command[index + 1] for index, part in enumerate(command) if part == "--mount"]
    assert mounts == [f"type=bind,src={tmp_path},dst=/workspace,readonly",
                      f"type=bind,src={tmp_path / 'build/targets/resolute'},dst=/build",
                      f"type=bind,src={tmp_path / 'dist/resolute'},dst=/dist"]
    assert "--read-only" in command
    assert "--privileged" not in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert command[-4] == "sha256:fixture"
    assert "KIRA_BUILDER_IMAGE_ID=sha256:fixture" in command
    assert RESOLUTE["BUILDER"] not in command


def test_target_runtime_constraints_keep_each_supported_python_abi():
    assert "python3 (>= 3.12), python3 (<< 3.13)" in NOBLE["DEPENDS"]
    dependencies = RESOLUTE["dependencies"]()
    assert "python3 (>= 3.14), python3 (<< 3.15)" in dependencies
    assert "libc6 (>= 2.43)" in dependencies
    assert "libglib2.0-0t64" in dependencies
    assert "fonts-noto-core" in dependencies


def test_resolute_inner_build_rejects_wrong_os_before_staging(monkeypatch):
    monkeypatch.setattr("platform.freedesktop_os_release",
                        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    with pytest.raises(RuntimeError, match="inside its disposable builder"):
        RESOLUTE["build_inside"]()


def test_package_metadata_excludes_local_builder_paths_and_keeps_public_provenance(tmp_path):
    local = tmp_path / "kira_desktop-0.0.0.dist-info"
    local.mkdir()
    provenance = local / "direct_url.json"
    provenance.write_text(json.dumps({"url": (tmp_path / "private-build/app.whl").as_uri()}))
    record = local / "RECORD"
    with record.open("w", newline="") as stream:
        csv.writer(stream).writerows([
            ["kira/__init__.py", "sha256=fixture", "12"],
            [provenance.relative_to(tmp_path).as_posix(), "sha256=private-fixture", "100"],
            [record.relative_to(tmp_path).as_posix(), "", ""],
        ])
    public = tmp_path / "dependency-0.0.0.dist-info"
    public.mkdir()
    public_provenance = public / "direct_url.json"
    public_provenance.write_text('{"url":"https://example.invalid/dependency.whl"}')
    NOBLE["remove_local_install_provenance"](tmp_path)
    assert not provenance.exists()
    with record.open(newline="") as stream:
        assert list(csv.reader(stream)) == [
            ["kira/__init__.py", "sha256=fixture", "12"],
            [record.relative_to(tmp_path).as_posix(), "", ""],
        ]
    assert public_provenance.exists()


def test_application_and_package_versions_agree():
    import tomllib

    from kira import __version__

    assert __version__ == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_packaged_documentation_keeps_repository_layout_for_relative_links():
    placements = NOBLE["documentation_placements"]()
    assert placements["README.md"] == "usr/share/doc/kira-desktop/README.md"
    assert placements["docs/it/GUIDA_UTENTE.md"] == "usr/share/doc/kira-desktop/docs/it/GUIDA_UTENTE.md"
    assert placements["docs/en/DEVELOPMENT.md"] == "usr/share/doc/kira-desktop/docs/en/DEVELOPMENT.md"
    assert "docs/images/chat-dark.png" in placements and "LICENSE" in placements
    italian = {Path(name).name for name in placements if name.startswith("docs/it/")}
    english = {Path(name).name for name in placements if name.startswith("docs/en/")}
    assert len(italian) == len(english) == len(list((ROOT / "docs/it").glob("*.md")))
