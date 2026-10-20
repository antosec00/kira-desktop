#!/usr/bin/env python3
"""Build a self-contained .deb dependency tree from the committed hash lock.

No root privileges or system Python package modifications. Python itself and
system display libraries are provided by Ubuntu 24.04 packages.
"""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
DEPENDS = (
    "python3 (>= 3.12), python3 (<< 3.13), libc6 (>= 2.39), libstdc++6, libgcc-s1, "
    "libglib2.0-0t64, libegl1, libgl1, libopengl0, libfontconfig1, libdbus-1-3, "
    "libgssapi-krb5-2, libharfbuzz0b, "
    "libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, "
    "libxcb-keysyms1, libxcb-render-util0, libxcb-xinerama0, libxcb-xkb1, "
    "libxcb-shape0, libxcb-randr0, libxcb-sync1, libxcb-render0, libxcb-shm0, "
    "libx11-xcb1, libxcb1, libx11-6, libxext6, libwayland-client0, "
    "libwayland-cursor0, libwayland-egl1, xdg-utils, "
    "libnss3, libnspr4, libatk1.0-0t64, libatk-bridge2.0-0t64, libatspi2.0-0t64, "
    "libcups2t64, libdrm2, libgbm1, libxcomposite1, libxdamage1, libxfixes3, "
    "libxrandr2, libasound2t64, libcairo2, libpango-1.0-0, xvfb, libxtst6"
)


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def remove_local_install_provenance(library: Path) -> None:
    """Do not distribute the wheel installer's private builder path (PEP 610)."""
    for direct_url in library.glob("*.dist-info/direct_url.json"):
        provenance = json.loads(direct_url.read_text())
        if not str(provenance.get("url", "")).startswith("file:"):
            continue
        record = direct_url.parent / "RECORD"
        if record.exists():
            with record.open(newline="") as stream:
                rows = list(csv.reader(stream))
            relative = direct_url.relative_to(library).as_posix()
            with record.open("w", newline="") as stream:
                csv.writer(stream).writerows(row for row in rows if row and row[0] != relative)
        direct_url.unlink()


def build() -> None:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Build with Ubuntu 24.04 /usr/bin/python3 (3.12)")
    stage = ROOT / "build/deb"
    if stage.exists():
        shutil.rmtree(stage)
    dest = stage / "opt/kira-desktop"
    lib = dest / "lib"
    lib.mkdir(parents=True)
    run("uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format",
        "requirements-txt", "-o", "packaging/requirements.lock.txt", "--quiet")
    run("uv", "pip", "install", "--python", "/usr/bin/python3", "--target", str(lib),
        "--require-hashes", "--only-binary", ":all:", "--link-mode", "copy",
        "-r", "packaging/requirements.lock.txt")
    run("uv", "build", "--wheel", "--out-dir", "build/wheels")
    wheel = ROOT / f"build/wheels/kira_desktop-{VERSION}-py3-none-any.whl"
    run("uv", "pip", "install", "--python", "/usr/bin/python3", "--target", str(lib),
        "--no-deps", "--link-mode", "copy", str(wheel))
    remove_local_install_provenance(lib)
    # Do not ship tools whose generated shebang points to the builder machine.
    shutil.rmtree(lib / "bin", ignore_errors=True)
    for cache in lib.rglob("__pycache__"):
        shutil.rmtree(cache)
    shutil.copy2(ROOT / "packaging/launcher.py", dest / "launcher.py")
    binary = stage / "usr/bin/kira-desktop"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nexec /usr/bin/python3 -I /opt/kira-desktop/launcher.py "$@"\n')
    binary.chmod(0o755)
    placements = {
        "packaging/kira-desktop.desktop": "usr/share/applications/kira-desktop.desktop",
        "src/kira/ui/assets/kira-mascot.png": "usr/share/pixmaps/kira-desktop.png",
        "packaging/kira-desktop.service": "usr/lib/systemd/user/kira-desktop.service",
        "LICENSE": "usr/share/doc/kira-desktop/copyright",
        "packaging/requirements.lock.txt": "usr/share/doc/kira-desktop/requirements.lock.txt",
    }
    for filename in ("README.md", "docs/ARCHITECTURE.md", "docs/CONTRACTS.md", "docs/BACKENDS.md", "docs/OPENAI_DIRECT.md", "docs/WEB_BROWSER.md",
                     "docs/V03_TEAMS.md", "docs/INPUT_QUESTIONS.md", "docs/USER_GUIDE_IT.md",
                     "docs/THIRD_PARTY_NOTICES.md", "docs/MARKDOWN_OUTPUT.md", "docs/DESIGN_033.md", "docs/DESIGN_018.md", "docs/TOOL_OUTPUT.md", "docs/ATTACHMENT_INPUT.md", "docs/CHAT_SETTINGS.md", "docs/APPEARANCE.md", "docs/LOCALIZATION.md", "docs/WORKBENCH.md", "docs/RUN_STATUS.md", "docs/PROJECT_CHATS.md"):
        if (ROOT / filename).exists():
            placements[filename] = f"usr/share/doc/kira-desktop/{Path(filename).name}"
    for source, target in placements.items():
        target_path = stage / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, target_path)
    licenses = ROOT / "packaging/licenses"
    if licenses.exists():
        shutil.copytree(licenses, stage / "usr/share/doc/kira-desktop/licenses")
    dependencies = sorted(
        ({"name": dist.metadata["Name"], "version": dist.version,
          "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
          "homepage": dist.metadata.get("Home-page")}
         for dist in importlib.metadata.distributions(path=[str(lib)])),
        key=lambda entry: entry["name"].lower(),
    )
    (stage / "usr/share/doc/kira-desktop/dependencies.json").write_text(
        json.dumps(dependencies, indent=2) + "\n"
    )
    control = stage / "DEBIAN"
    control.mkdir()
    size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) // 1024
    (control / "control").write_text(
        f"Package: kira-desktop\nVersion: {VERSION}\nArchitecture: amd64\n"
        "Maintainer: Kira Desktop contributors <kira@example.invalid>\n"
        f"Installed-Size: {size}\nDepends: {DEPENDS}\n"
        "Section: devel\nPriority: optional\n"
        "Description: Native durable workspace for Ollama, Codex and OpenAI agents\n"
        " Python 3.12 and bundled Qt 6.11.2, Ubuntu 24.04 LTS baseline.\n"
    )
    # No maintainer scripts: installs/upgrades/removals never touch active work or user data.
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    artifact = dist / f"kira-desktop_{VERSION}_amd64.deb"
    os.environ.setdefault("SOURCE_DATE_EPOCH", "1791417600")
    run("dpkg-deb", "--root-owner-group", "-Zxz", "-z3", "--build", str(stage), str(artifact))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (dist / "SHA256SUMS").write_text(f"{digest}  {artifact.name}\n")
    print(f"Built {artifact} ({artifact.stat().st_size / 1024**2:.1f} MiB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("noble", "resolute"), default="noble")
    arguments = parser.parse_args()
    if arguments.target == "resolute":
        subprocess.run([sys.executable, str(ROOT / "packaging/resolute/build.py")], check=True)
    else:
        build()
