#!/usr/bin/env python3
"""Verify an installed .deb in an owned disposable Ubuntu target container.

Runs real installed QML/service smoke checks as an unprivileged user outside the
checkout. Providers are intentionally absent. A synthetic package revision tests
package-manager upgrade/removal behavior; it does not claim a schema migration.
No host installation, privileged container, or host data mount is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
# Noble validation image, pinned by digest like the resolute recipe's images.
NOBLE_IMAGE = "ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac"
TEST_USER = "kira-test"
TEST_HOME = f"/home/{TEST_USER}"
TEST_RUNTIME = f"{TEST_HOME}/runtime"


class PackageCheck:
    def __init__(self, artifact: Path, evidence: Path, keep: bool = False,
                 target: str = "noble") -> None:
        self.artifact = artifact.resolve(strict=True)
        self.evidence = evidence.resolve()
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.container = f"kira-package-validation-{uuid.uuid4().hex[:10]}"
        self.keep = keep
        self.target = target
        self.image = (json.loads((ROOT / "packaging/resolute/source.json").read_text())["base_image"]
                      if target == "resolute" else NOBLE_IMAGE)
        self.created = False
        self.log_path = self.evidence / "package-validation.log"
        self.report_path = self.evidence / "package-validation.json"
        with self.artifact.open("rb") as package:
            artifact_sha256 = hashlib.file_digest(package, "sha256").hexdigest()
        self.report: dict[str, Any] = {
            "status": "RUNNING", "test_kind": "real installed package in disposable container",
            "started_at": datetime.now(UTC).isoformat(), "image": self.image, "target": target,
            "artifact": str(self.artifact),
            "artifact_sha256": artifact_sha256,
            "container": self.container, "steps": [], "smoke_reports": {},
            "limitations": ["Container has no running systemd user manager or KDE/Wayland desktop",
                            "External providers are absent; these checks do not verify live inference",
                            "Synthetic upgrade changes Debian version metadata only, not application schema"],
        }

    def command(self, argv: list[str], *, timeout: int = 180, quiet: bool = False) -> str:
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(argv) + "\n")
        try:
            completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(partial + "\nCOMMAND TIMED OUT\n")
            raise
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(completed.stdout + "\n")
        if not quiet:
            print(f"[{completed.returncode}] {' '.join(argv[:5])}", flush=True)
        if completed.returncode != 0:
            raise RuntimeError(f"Command failed ({completed.returncode}): {argv!r}\n"
                               f"{completed.stdout[-6000:]}")
        return completed.stdout

    def inside(self, *argv: str, user: bool = False, timeout: int = 180,
               quiet: bool = False) -> str:
        prefix = ["docker", "exec", "--workdir", "/tmp", self.container]
        if user:
            prefix += ["runuser", "--user", TEST_USER, "--", "env",
                       f"XDG_RUNTIME_DIR={TEST_RUNTIME}"]
        return self.command(prefix + list(argv), timeout=timeout, quiet=quiet)

    def record(self, step: str, **evidence: Any) -> None:
        self.report["steps"].append({"step": step, "status": "PASS", **evidence})
        self.save()

    def save(self) -> None:
        self.report_path.write_text(json.dumps(self.report, indent=2) + "\n", encoding="utf-8")

    def copy_out(self, source: str, destination: Path) -> None:
        self.command(["docker", "cp", f"{self.container}:{source}", str(destination)], quiet=True)

    def ownership(self) -> dict[str, Any]:
        code = """
import json, os
from pathlib import Path
matches=[]
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit(): continue
    try:
        args=(entry/'cmdline').read_bytes().split(b'\\0')
        if b'/opt/kira-desktop/launcher.py' in args and b'service' in args:
            matches.append({'pid':int(entry.name),'uid':entry.stat().st_uid})
    except (OSError,PermissionError): pass
assert matches and all(p['uid']==os.getuid() for p in matches), matches
print(json.dumps({'service_processes':matches,'expected_uid':os.getuid()}))
"""
        return dict(json.loads(self.inside("python3", "-c", code, user=True, quiet=True)))

    def smoke(self, phase: str, platform: str = "offscreen") -> dict[str, Any]:
        command = ["env", f"QT_QPA_PLATFORM={platform}", "QT_QUICK_BACKEND=software",
                   "QT_LOGGING_RULES=qt.qpa.*=false", "/usr/bin/python3", "-I",
                   "/tmp/installed_smoke.py"]
        if platform == "xcb":
            command = ["dbus-run-session", "--", "xvfb-run", "--auto-servernum", *command]
        self.inside(*command, user=True, timeout=75)
        destination = self.evidence / f"package-{phase}.json"
        self.copy_out(f"{TEST_HOME}/installed-report.json", destination)
        self.copy_out(f"{TEST_HOME}/installed-kira.png", self.evidence / f"package-{phase}.png")
        report = json.loads(destination.read_text())
        assert report["uid"] != 0 and report["cwd"] == "/tmp"
        assert report["qml_root"] and report["memory_persisted"] and report["clipboard"]
        assert report["platform"] == platform
        ownership = self.ownership()
        self.report["smoke_reports"][phase] = {**report, **ownership}
        self.record(phase, platform=platform, project_id=report["project_id"], **ownership)
        return dict(report)

    def stop_service(self) -> None:
        self.inside("/usr/bin/kira-desktop", "stop-service", user=True, timeout=30)
        code = """
import fcntl,os,time
from pathlib import Path
socket=Path('""" + TEST_RUNTIME + """/kira/service.sock')
lock=Path.home()/'.local/share/kira/kira.lock'
def service_alive():
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try:
            args=(entry/'cmdline').read_bytes().split(b'\\0')
            state=(entry/'stat').read_text().rsplit(')',1)[1].split()[0]
            if (entry.stat().st_uid==os.getuid() and state!='Z' and
                b'/opt/kira-desktop/launcher.py' in args and b'service' in args):
                return True
        except (OSError,PermissionError,IndexError): pass
    return False
# Python >=3.13 may unlink the socket as soon as Server.close() begins.
# Quiescence requires the process and persistent-store lock to be released too.
for _ in range(600):
    if not socket.exists() and not service_alive():
        with lock.open('rb') as descriptor:
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                break
    time.sleep(0.05)
else:
    raise AssertionError('Service process or data-store lock still active after shutdown')
"""
        self.inside("python3", "-c", code, user=True, quiet=True)

    def user_files(self) -> dict[str, str]:
        code = """
import contextlib,fcntl,hashlib,json,sqlite3
from pathlib import Path
home=Path.home()
paths={'database':home/'.local/share/kira/kira.sqlite3',
       'project_file':home/'kira-install-project/preserve-me.txt'}
with paths['database'].with_suffix('.lock').open('rb') as descriptor:
    fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with contextlib.closing(sqlite3.connect(paths['database'].as_uri()+'?mode=ro',uri=True)) as database:
        project_id=database.execute('SELECT id FROM projects').fetchone()[0]
        memory=database.execute('SELECT memory FROM projects').fetchone()[0]
    result={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    result.update(project_id=project_id,memory=memory)
print(json.dumps(result))
"""
        return dict(json.loads(self.inside("python3", "-c", code, user=True, quiet=True)))

    def verify(self) -> None:
        self.log_path.write_text("Kira installed package validation\n", encoding="utf-8")
        self.save()
        self.command(["docker", "run", "--detach", "--name", self.container,
                      "--label", "org.kira.test=package-validation", self.image,
                      "sleep", "infinity"], timeout=300)
        self.created = True
        self.report["image_id"] = self.command(
            ["docker", "inspect", "--format", "{{.Image}}", self.container], quiet=True).strip()
        self.command(["docker", "cp", str(self.artifact), f"{self.container}:/tmp/kira-original.deb"])
        copied_sha256 = self.inside("sha256sum", "/tmp/kira-original.deb", quiet=True).split()[0]
        assert copied_sha256 == self.report["artifact_sha256"], "Package changed while copying"
        self.command(["docker", "cp", str(ROOT / "scripts/installed_smoke.py"),
                      f"{self.container}:/tmp/installed_smoke.py"])
        # Ubuntu's minimal Docker image excludes /usr/share/doc by default.
        # Include this package's notices explicitly, matching a normal desktop install.
        self.inside("sh", "-c", "printf '%s\\n' "
                    "'path-include=/usr/share/doc/kira-desktop' "
                    "'path-include=/usr/share/doc/kira-desktop/*' "
                    "> /etc/dpkg/dpkg.cfg.d/zz-kira-package-test")
        self.report["container_documentation_override"] = (
            "Explicit dpkg path-include for /usr/share/doc/kira-desktop/*")
        self.inside("apt-get", "update", timeout=300)
        self.inside("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
                    "--no-install-recommends", "/tmp/kira-original.deb", "xvfb", "xauth",
                    "dbus-x11", "fonts-dejavu-core", timeout=600)
        self.inside("useradd", "--create-home", "--uid", "1001", "--shell", "/bin/bash", TEST_USER)
        self.inside("install", "--directory", "--mode", "0700", "--owner", TEST_USER,
                    "--group", TEST_USER, TEST_RUNTIME)
        version = self.inside("dpkg-query", "--show", "--showformat=${Version}",
                              "kira-desktop", quiet=True).strip()
        self.report["version"] = version
        self.report["os_release"] = self.inside("cat", "/etc/os-release", quiet=True)
        self.report["python"] = self.inside("python3", "--version", quiet=True).strip()
        expected_python = "Python 3.14." if self.target == "resolute" else "Python 3.12."
        assert self.report["python"].startswith(expected_python), self.report["python"]
        files = ["/usr/bin/kira-desktop", "/usr/bin/kira",
                 "/usr/share/applications/kira-desktop.desktop",
                 "/usr/lib/systemd/user/kira-desktop.service",
                 "/usr/share/pixmaps/kira-desktop.png",
                 "/opt/kira-desktop/lib/kira/ui/qml/Main.qml",
                 "/usr/share/doc/kira-desktop/requirements.lock.txt"]
        self.inside("python3", "-c", "import pathlib,sys; missing=[p for p in sys.argv[1:] "
                    "if not pathlib.Path(p).is_file()]; assert not missing, missing; "
                    "print('All installed resources present')", *files)
        self.report["installed_files"] = files
        self.report["launcher_version"] = self.inside("/usr/bin/kira-desktop", "--version",
                                                     user=True, quiet=True).strip()
        short_version = self.inside("/usr/bin/kira", "--version", user=True, quiet=True).strip()
        assert short_version == self.report["launcher_version"], short_version
        # The package version (pyproject) and the application version (kira.__version__) agree.
        assert self.report["launcher_version"] == f"Kira Desktop {VERSION}", self.report["launcher_version"]
        self.record("fresh_install", debian_version=version)
        original = self.smoke("installed-offscreen")
        self.smoke("installed-xcb", "xcb")
        self.stop_service()
        preserved = self.user_files()
        self.inside("dpkg-deb", "--raw-extract", "/tmp/kira-original.deb", "/tmp/kira-upgrade")
        upgrade_version = version + "+packagetest1"
        self.inside("python3", "-c",
                    "from pathlib import Path; import sys; p=Path('/tmp/kira-upgrade/DEBIAN/control'); "
                    "lines=p.read_text().splitlines(); p.write_text('\\n'.join('Version: '+sys.argv[1] "
                    "if line.startswith('Version: ') else line for line in lines)+'\\n')",
                    upgrade_version)
        self.inside("dpkg-deb", "--root-owner-group", "-Zgzip", "-z1", "--build",
                    "/tmp/kira-upgrade", "/tmp/kira-upgrade.deb", timeout=180)
        self.inside("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
                    "/tmp/kira-upgrade.deb", timeout=300)
        installed_version = self.inside("dpkg-query", "--show", "--showformat=${Version}",
                                        "kira-desktop", quiet=True).strip()
        assert installed_version == upgrade_version
        assert self.user_files() == preserved
        self.record("package_upgrade_preserves_user_files", from_version=version,
                    to_version=upgrade_version, hashes=preserved)
        upgraded = self.smoke("upgraded-offscreen")
        assert upgraded["project_id"] == original["project_id"]
        self.stop_service()
        before_removal = self.user_files()
        self.inside("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "remove", "-y",
                    "kira-desktop", timeout=300)
        self.inside("python3", "-c", "from pathlib import Path; "
                    "assert not Path('/usr/bin/kira-desktop').exists(); "
                    "assert not Path('/usr/bin/kira').exists(); "
                    "assert not Path('/usr/share/applications/kira-desktop.desktop').exists(); "
                    "assert not Path('/usr/lib/systemd/user/kira-desktop.service').exists()")
        assert self.user_files() == before_removal
        self.record("package_removal_preserves_user_files", hashes=before_removal)
        self.inside("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
                    "/tmp/kira-original.deb", timeout=300)
        assert self.user_files() == before_removal
        reinstalled = self.smoke("reinstalled-offscreen")
        assert reinstalled["project_id"] == original["project_id"]
        self.stop_service()
        self.record("reinstall_preserves_project_and_launches", project_id=original["project_id"])
        self.report["status"] = "PASS"

    def finish(self) -> None:
        # The container name is unique to this run, so removing it is safe even
        # when `docker run` timed out after the container was created.
        if not self.keep:
            try:
                self.command(["docker", "rm", "--force", self.container], quiet=True)
                self.report["container_removed"] = True
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                self.report["container_removed"] = False
                if self.created:
                    self.report["cleanup_error"] = str(exc)
        else:
            self.report["container_removed"] = False
        self.report["finished_at"] = datetime.now(UTC).isoformat()
        self.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deb", nargs="?", type=Path)
    parser.add_argument("--target", choices=("noble", "resolute"), default="noble")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--keep-container", action="store_true",
                        help="Keep only this driver's owned container for debugging")
    args = parser.parse_args()
    if shutil.which("docker") is None:
        parser.error("Docker is required; no host installation will be attempted")
    target_directory = ROOT / "dist/resolute" if args.target == "resolute" else ROOT / "dist"
    artifact = args.deb or target_directory / f"kira-desktop_{VERSION}_amd64.deb"
    evidence = args.evidence or ROOT / "build/releases" / VERSION / args.target / "package"
    check = PackageCheck(artifact, evidence, args.keep_container, args.target)
    start = time.monotonic()
    try:
        check.verify()
        print(f"PASS: {check.report_path}", flush=True)
        return 0
    except Exception as exc:  # Any failure must still end in a FAIL report.
        check.report["status"] = "FAIL"
        check.report["error"] = str(exc)
        print(f"FAIL: {exc}\nEvidence: {check.report_path}", file=sys.stderr, flush=True)
        return 1
    finally:
        check.report["duration_seconds"] = round(time.monotonic() - start, 3)
        check.finish()


if __name__ == "__main__":
    raise SystemExit(main())
