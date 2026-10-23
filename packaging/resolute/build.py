#!/usr/bin/env python3
"""Build the Python 3.14 application target inside a pinned Ubuntu 26.04 container."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "packaging/resolute"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
BUILDER = "kira-app-builder:resolute-0.1"


def run(*arguments: str, capture: bool = False) -> str:
    result = subprocess.run(arguments, cwd=ROOT, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else ""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dependencies() -> str:
    noble = runpy.run_path(str(ROOT / "scripts/build_deb.py"))["DEPENDS"]
    old = "python3 (>= 3.12), python3 (<< 3.13), libc6 (>= 2.39)"
    if old not in noble:
        raise RuntimeError("The shared runtime dependency contract changed; review Resolute")
    return noble.replace(old, "python3 (>= 3.14), python3 (<< 3.15), libc6 (>= 2.43)") + (
        ", fonts-noto-core")


def require_platform() -> None:
    release = platform.freedesktop_os_release()
    if (sys.version_info[:2] != (3, 14) or release.get("ID") != "ubuntu"
            or release.get("VERSION_ID") != "26.04" or platform.machine() != "x86_64"):
        raise RuntimeError("Resolute packaging requires Ubuntu 26.04 amd64 / Python 3.14 "
                           "inside its disposable builder")


def build_inside() -> None:
    require_platform()
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1791417600"))
    os.environ["SOURCE_DATE_EPOCH"] = str(epoch)
    build = Path("/build")
    output = Path("/dist")
    stage = build / "deb"
    if stage.exists():
        shutil.rmtree(stage)
    destination = stage / "opt/kira-desktop"
    library = destination / "lib"
    library.mkdir(parents=True)
    requirements = build / "requirements.lock.txt"
    run("uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format",
        "requirements-txt", "--output-file", str(requirements), "--quiet")
    run("uv", "pip", "install", "--python", "/usr/bin/python3", "--target", str(library),
        "--require-hashes", "--only-binary", ":all:", "--link-mode", "copy",
        "--requirement", str(requirements))

    build_environment = build / "build-env"
    run("uv", "venv", "--python", "/usr/bin/python3", "--clear", str(build_environment))
    build_python = str(build_environment / "bin/python")
    run("uv", "pip", "install", "--python", build_python, "--require-hashes",
        "--only-binary", ":all:", "--requirement", str(RECIPE / "build-requirements.lock.txt"))
    run("uv", "build", "--wheel", "--no-build-isolation", "--python", build_python,
        "--out-dir", str(build / "wheels"))
    wheel = build / f"wheels/kira_desktop-{VERSION}-py3-none-any.whl"
    run("uv", "pip", "install", "--python", "/usr/bin/python3", "--target", str(library),
        "--no-deps", "--link-mode", "copy", str(wheel))
    runpy.run_path(str(ROOT / "scripts/build_deb.py"))["remove_local_install_provenance"](library)
    shutil.rmtree(library / "bin", ignore_errors=True)
    for cache in library.rglob("__pycache__"):
        shutil.rmtree(cache)
    shutil.copy2(ROOT / "packaging/launcher.py", destination / "launcher.py")
    (stage / "usr/bin").mkdir(parents=True)
    # `kira` is the short name of the same launcher.
    for name in ("kira-desktop", "kira"):
        executable = stage / "usr/bin" / name
        executable.write_text('#!/bin/sh\nexec /usr/bin/python3 -I /opt/kira-desktop/launcher.py "$@"\n')
        executable.chmod(0o755)
    placements = {
        "packaging/kira-desktop.desktop": "usr/share/applications/kira-desktop.desktop",
        "src/kira/ui/assets/kira-mascot.png": "usr/share/pixmaps/kira-desktop.png",
        "packaging/kira-desktop.service": "usr/lib/systemd/user/kira-desktop.service",
        "LICENSE": "usr/share/doc/kira-desktop/copyright",
    }
    noble = runpy.run_path(str(ROOT / "scripts/build_deb.py"))
    placements.update(noble["documentation_placements"]())
    for source, target_filename in placements.items():
        target_path = stage / target_filename
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, target_path)
    documentation = stage / "usr/share/doc/kira-desktop"
    shutil.copy2(requirements, documentation / "requirements.lock.txt")
    shutil.copytree(ROOT / "packaging/licenses", documentation / "licenses")
    installed = sorted(
        ({"name": item.metadata["Name"], "version": item.version,
          "license": item.metadata.get("License-Expression") or item.metadata.get("License"),
          "homepage": item.metadata.get("Home-page")}
         for item in importlib.metadata.distributions(path=[str(library)])),
        key=lambda item: (item["name"] or "").lower(),
    )
    (documentation / "dependencies.json").write_text(json.dumps(installed, indent=2) + "\n")
    target = {"target": "resolute", "ubuntu": "26.04", "python": "3.14",
              "app_version": VERSION, "ipc_version": 1,
              "source_lock_sha256": digest(ROOT / "uv.lock")}
    (documentation / "target.json").write_text(json.dumps(target, indent=2) + "\n")
    control = stage / "DEBIAN"
    control.mkdir()
    size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file()) // 1024
    (control / "control").write_text(
        f"Package: kira-desktop\nVersion: {VERSION}\nArchitecture: amd64\n"
        "Maintainer: antosec00 <antosec00@gmail.com>\n"
        f"Installed-Size: {size}\nDepends: {dependencies()}\n"
        f"Section: devel\nPriority: optional\nHomepage: {noble['HOMEPAGE']}\n"
        "X-Kira-Target: resolute\n"
        "Description: Native desktop harness for AI projects, chats, tools and agents\n"
        " Python 3.14 and bundled Qt 6.11.2, Ubuntu 26.04 LTS target.\n")
    noble["normalize_stage"](stage, epoch)
    artifact = output / f"kira-desktop_{VERSION}_amd64.deb"
    temporary = artifact.with_suffix(".deb.building")
    run("dpkg-deb", "--root-owner-group", "-Zxz", "-z3", "--build", str(stage), str(temporary))
    temporary.replace(artifact)
    artifact_hash = digest(artifact)
    (output / "SHA256SUMS").write_text(f"{artifact_hash}  {artifact.name}\n")
    sys.path.insert(0, str(library))
    from PySide6.QtCore import qVersion
    report = {
        **target, "built_at": datetime.now(UTC).isoformat(), "python_version": sys.version,
        "qt_version": qVersion(), "platform": platform.freedesktop_os_release(),
        "recipe": json.loads((RECIPE / "source.json").read_text()),
        "builder_image_id": os.environ["KIRA_BUILDER_IMAGE_ID"],
        "source_date_epoch": epoch, "artifact": artifact.name,
        "artifact_sha256": artifact_hash, "artifact_bytes": artifact.stat().st_size,
        "wheel_sha256": digest(wheel), "requirements_sha256": digest(requirements),
        "build_requirements_sha256": digest(RECIPE / "build-requirements.lock.txt"),
        "pyproject_sha256": digest(ROOT / "pyproject.toml"), "dependencies": installed,
        "builder_packages": run("dpkg-query", "-W", "--showformat=${Package}\t${Version}\n",
                                capture=True).splitlines(),
    }
    (output / f"kira-desktop-{VERSION}-build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Built {artifact}: {artifact_hash}", flush=True)


def container_command(root: Path, image_id: str) -> list[str]:
    """Keep the checkout read-only and restrict writable binds to this target."""
    return ["docker", "run", "--rm", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--read-only", "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,mode=1777,size=1g",
            "--mount", f"type=bind,src={root},dst=/workspace,readonly",
            "--mount", f"type=bind,src={root / 'build/targets/resolute'},dst=/build",
            "--mount", f"type=bind,src={root / 'dist/resolute'},dst=/dist",
            "--env", "UV_CACHE_DIR=/build/uv-cache",
            "--env", f"KIRA_BUILDER_IMAGE_ID={image_id}",
            image_id, "/usr/bin/python3", "/workspace/packaging/resolute/build.py", "--inside"]


def build() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is required; no host installation will be attempted")
    recipe = json.loads((RECIPE / "source.json").read_text())
    (ROOT / "build/targets/resolute").mkdir(parents=True, exist_ok=True)
    (ROOT / "dist/resolute").mkdir(parents=True, exist_ok=True)
    # A mutable tag can be replaced by another checkout between build and run.
    # Bind both execution and provenance to the ID returned by this exact build.
    with tempfile.TemporaryDirectory(prefix=".builder-", dir=ROOT / "build/targets/resolute") as tmp:
        iidfile = Path(tmp) / "image-id"
        run("docker", "build", "--progress=plain", "--iidfile", str(iidfile),
            "--build-arg", f"BASE_IMAGE={recipe['base_image']}",
            "--build-arg", f"UV_IMAGE={recipe['uv_image']}", "--tag", BUILDER, str(RECIPE))
        image_id = iidfile.read_text().strip()
        if (not image_id.startswith("sha256:") or len(image_id) != 71
                or any(character not in "0123456789abcdef" for character in image_id[7:])):
            raise RuntimeError("Docker did not return a valid immutable builder image ID")
    run(*container_command(ROOT, image_id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    build_inside() if args.inside else build()
