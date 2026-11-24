"""The generated Windows launcher: a packaged install follows no folder, a development one still does."""

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_windows():
    spec = importlib.util.spec_from_file_location("kira_build_windows", ROOT / "scripts/build_windows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def launcher(install_dir, dev_repo):
    """Write the launcher as the build does and load it without starting Kira."""
    install_dir.mkdir()
    build_windows()._write_launchers(install_dir, dev_repo)
    path = install_dir / "launch_kira.pyw"
    namespace = {"__file__": str(path), "__name__": "launch_kira"}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    installed = install_dir / "app" / "src" / "kira"
    installed.mkdir(parents=True)
    (installed / "installed_only.py").write_text("INSTALLED = True\n", encoding="utf-8")
    return namespace, installed


def checkout(root):
    source = root / "src" / "kira"
    source.mkdir(parents=True)
    (source / "from_checkout.py").write_text("CHECKOUT = True\n", encoding="utf-8")
    return root


def test_packaged_launcher_never_syncs_from_the_folder_it_is_started_in(tmp_path, monkeypatch):
    namespace, installed = launcher(tmp_path / "packaged", "")
    assert namespace["DEV_REPO"] is None
    # Started from inside some checkout of Kira's source, as `kira.cmd doctor` can be.
    monkeypatch.chdir(checkout(tmp_path / "elsewhere"))
    assert Path("src/kira/from_checkout.py").is_file()
    assert namespace["_auto_sync_from_dev_repo"]() is False
    assert sorted(item.name for item in installed.iterdir()) == ["installed_only.py"]
    # Even a launcher that carries a relative path follows nothing.
    namespace["DEV_REPO"] = Path("")
    assert namespace["_auto_sync_from_dev_repo"]() is False
    assert sorted(item.name for item in installed.iterdir()) == ["installed_only.py"]


def test_development_launcher_still_follows_its_working_copy(tmp_path, monkeypatch):
    repo = checkout(tmp_path / "working-copy")
    (repo / "pyproject.toml").write_text("[project]\nname = 'kira-desktop'\n", encoding="utf-8")
    namespace, installed = launcher(tmp_path / "development", str(repo))
    assert namespace["DEV_REPO"] == repo
    monkeypatch.chdir(tmp_path)
    assert namespace["_auto_sync_from_dev_repo"]() is True
    # The installation mirrors the working copy: new files arrive, stale ones go.
    assert sorted(item.name for item in installed.iterdir()) == ["from_checkout.py"]
    assert (installed.parents[1] / "pyproject.toml").is_file()
    assert namespace["_auto_sync_from_dev_repo"]() is False, "nothing changed since the last start"
    os.utime(repo / "src" / "kira" / "from_checkout.py", (2_000_000_000, 2_000_000_000))
    assert namespace["_auto_sync_from_dev_repo"]() is True
