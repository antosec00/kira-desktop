#!/usr/bin/env python3
"""Qualify actual adjacent Kira .deb migrations and backup-based rollback.

Uses one owned disposable target container, no host mounts or installation.
Old history is explicitly seeded as synthetic data, using the old installed
Store only after its service stops. Real installed service/QML and package
manager operations verify application-package upgrades and data recovery.
Native Wayland and authenticated inference require separate qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from check_deb import ROOT, TEST_HOME, TEST_RUNTIME, TEST_USER, PackageCheck


class UpgradeCheck(PackageCheck):
    def __init__(self, previous: Path, artifact: Path, evidence: Path, target: str,
                 from_schema: int = 1, to_schema: int = 2) -> None:
        super().__init__(artifact, evidence, target=target)
        if (from_schema, to_schema) not in {(1, 2), (2, 3)}:
            raise ValueError("Supported migrations are schema 1→2 and 2→3")
        self.from_schema, self.to_schema = from_schema, to_schema
        self.from_version, self.to_version = f"0.{from_schema}.0", f"0.{to_schema}.0"
        self.previous = previous.resolve(strict=True)
        self.log_path = self.evidence / "upgrade-validation.log"
        self.report_path = self.evidence / "upgrade-validation.json"
        if self.report_path.exists():
            raise ValueError("Choose a new evidence directory; previous qualification is retained")
        with self.previous.open("rb") as stream:
            old_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        self.report.update(test_kind="actual installed .deb upgrade and schema rollback",
                           previous_artifact=str(self.previous), previous_sha256=old_hash,
                           from_version=self.from_version, to_version=self.to_version,
                           from_schema=from_schema, to_schema=to_schema)
        self.report["helper_sha256"] = hashlib.sha256(
            (ROOT / "scripts/upgrade_smoke.py").read_bytes()).hexdigest()
        self.report["limitations"] = [
            "Runs/events/artifacts are synthetic migration fixtures, not AI execution",
            "Container has no full systemd/KDE/Wayland session or native application launch checks",
            "No provider is contacted beyond detection of configured unavailable providers",
            "Rollback restores the retained old-schema backup; newer state remains separately preserved",
            "Qualification covers Kira application packages and their data"]

    def helper(self, phase: str, label: str, *, graphical: bool = False) -> dict[str, Any]:
        command = ["/usr/bin/python3", "-I", "/tmp/upgrade_smoke.py", phase, "--label", label]
        command += ["--from-schema", str(self.from_schema), "--to-schema", str(self.to_schema)]
        if graphical:
            command = ["dbus-run-session", "--", "xvfb-run", "--auto-servernum", "env",
                       "QT_QPA_PLATFORM=xcb", "QT_QUICK_BACKEND=software", *command]
        self.inside(*command, user=True, timeout=90)
        destination = self.evidence / f"upgrade-{label}.json"
        self.copy_out(f"{TEST_HOME}/upgrade-{label}.json", destination)
        result = json.loads(destination.read_text())
        assert result["status"] == "PASS"
        if graphical:
            self.copy_out(f"{TEST_HOME}/upgrade-{label}.png", self.evidence / f"upgrade-{label}.png")
            assert result["uid"] != 0 and result["cwd"] == "/tmp"
            assert result["installed_module"].startswith("/opt/kira-desktop/lib/")
        self.record(label, report=destination.name)
        return dict(result)

    def install(self, artifact: str, version: str, *, downgrade: bool = False) -> None:
        argv = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
                "--no-remove", "--no-install-recommends"]
        if downgrade:
            argv.append("--allow-downgrades")
        self.inside(*argv, artifact, timeout=600)
        installed = self.inside("dpkg-query", "--show", "--showformat=${Version}",
                                "kira-desktop", quiet=True).strip()
        assert installed == version
        self.record("installed_" + version, artifact=artifact, version=installed)

    def verify(self) -> None:
        self.log_path.write_text("Actual Kira package migration and rollback qualification\n")
        self.save()
        for package, expected in ((self.previous, self.from_version),
                                  (self.artifact, self.to_version)):
            version = self.command(["dpkg-deb", "--field", str(package), "Version"], quiet=True).strip()
            name = self.command(["dpkg-deb", "--field", str(package), "Package"], quiet=True).strip()
            assert version == expected and name == "kira-desktop", (name, version)
        self.command(["docker", "run", "--detach", "--name", self.container,
                      "--label", "org.kira.test=actual-upgrade-validation",
                      "--log-opt", "max-size=10m", "--log-opt", "max-file=3", self.image,
                      "sleep", "infinity"], timeout=300)
        self.created = True
        self.report["image_id"] = self.command(
            ["docker", "inspect", "--format", "{{.Image}}", self.container], quiet=True).strip()
        for source, destination, expected_hash in (
            (self.previous, "/tmp/kira-v1.deb", self.report["previous_sha256"]),
            (self.artifact, "/tmp/kira-v2.deb", self.report["artifact_sha256"]),
        ):
            self.command(["docker", "cp", str(source), f"{self.container}:{destination}"], quiet=True)
            assert self.inside("sha256sum", destination, quiet=True).split()[0] == expected_hash
        self.command(["docker", "cp", str(ROOT / "scripts/upgrade_smoke.py"),
                      f"{self.container}:/tmp/upgrade_smoke.py"], quiet=True)
        assert self.inside("sha256sum", "/tmp/upgrade_smoke.py", quiet=True).split()[0] == self.report["helper_sha256"]
        nonce = secrets.token_hex(20)
        input_content = f"Independent generated migration fixture input: {nonce}\n"
        generated = {"nonce": nonce, "input_content": input_content,
                     "input_sha256": hashlib.sha256(input_content.encode()).hexdigest(),
                     "diagnostic_canary": "SYNTHETIC_PRIVATE_CANARY_" + secrets.token_hex(16)}
        fixture = self.evidence / "generated-fixture.json"
        fixture.write_text(json.dumps(generated, indent=2) + "\n")
        fixture.chmod(0o600)
        self.command(["docker", "cp", str(fixture), f"{self.container}:/tmp/upgrade-input.json"], quiet=True)
        self.inside("chmod", "0644", "/tmp/upgrade-input.json")
        self.inside("apt-get", "update", timeout=300)
        self.inside("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
                    "--no-install-recommends", "xvfb", "xauth", "dbus-x11", "fonts-dejavu-core", timeout=600)
        self.install("/tmp/kira-v1.deb", self.from_version)
        self.inside("useradd", "--create-home", "--uid", "1001", "--shell", "/bin/bash", TEST_USER)
        self.inside("install", "--directory", "--mode", "0700", "--owner", TEST_USER,
                    "--group", TEST_USER, TEST_RUNTIME)
        self.report["os_release"] = self.inside("cat", "/etc/os-release", quiet=True)
        self.report["python"] = self.inside("python3", "--version", quiet=True).strip()
        assert self.report["python"].startswith("Python 3.14." if self.target == "resolute" else "Python 3.12.")
        self.helper("create", "old-api-created")
        self.stop_service()
        baseline = self.helper("seed", "old-seeded-baseline")
        assert baseline["files"]["input"]["sha256"] == generated["input_sha256"]
        self.helper("gui", "old-installed-qml", graphical=True)
        self.report["old_service_ownership"] = self.ownership()
        self.stop_service()
        old_after_gui = self.helper("snapshot", "old-after-qml")
        for key in ("tables", "settings", "files"):
            assert old_after_gui[key] == baseline[key]
        self.install("/tmp/kira-v2.deb", self.to_version)
        migrated = self.helper("migrate", "real-schema-migration")
        self.report["migration_backup"] = migrated["migration_backup"]
        self.copy_out(migrated["migration_backup"]["path"],
                      self.evidence / f"pre-v{self.to_schema}-backup.sqlite3")
        self.helper("gui", "new-installed-qml", graphical=True)
        self.report["new_service_ownership"] = self.ownership()
        self.stop_service()
        new_after_gui = self.helper("snapshot", "new-after-qml")
        for key in ("tables", "settings", "files"):
            assert new_after_gui[key] == baseline[key]
        self.install("/tmp/kira-v1.deb", self.from_version, downgrade=True)
        self.helper("refuse", f"old-refuses-schema{self.to_schema}")
        restored = self.helper("restore", "restore-original-backup")
        self.copy_out(restored["preserved_schema_path"],
                      self.evidence / f"preserved-schema{self.to_schema}.sqlite3")
        self.copy_out(restored["original_schema_group"],
                      self.evidence / f"schema{self.to_schema}-original-files")
        self.helper("gui", "rollback-installed-qml", graphical=True)
        self.report["rollback_service_ownership"] = self.ownership()
        self.stop_service()
        rolled_back = self.helper("snapshot", "rollback-final")
        for key in ("schema", "tables", "settings", "files"):
            assert rolled_back[key] == baseline[key]
        self.report["baseline_tables"] = baseline["tables"]
        self.report["restored_tables"] = rolled_back["tables"]
        self.report["status"] = "PASS"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("noble", "resolute"), default="noble")
    parser.add_argument("--from-deb", type=Path, required=True)
    parser.add_argument("--to-deb", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--from-schema", type=int, choices=(1, 2), default=1)
    parser.add_argument("--to-schema", type=int, choices=(2, 3), default=2)
    args = parser.parse_args()
    if not shutil.which("docker"):
        parser.error("Docker is required; host installation is never attempted")
    check = UpgradeCheck(args.from_deb, args.to_deb, args.evidence, args.target,
                         args.from_schema, args.to_schema)
    start = time.monotonic()
    try:
        check.verify()
        print(f"PASS: {check.report_path}", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.TimeoutExpired) as exc:
        check.report.update(status="FAIL", error=str(exc))
        print(f"FAIL: {exc}\nEvidence: {check.report_path}", file=sys.stderr, flush=True)
        return 1
    finally:
        check.report["duration_seconds"] = round(time.monotonic() - start, 3)
        check.finish()


if __name__ == "__main__":
    raise SystemExit(main())
