"""Kira's self-update on synthetic releases, temporary git repositories and stub tools.

Nothing reaches the network, the system package manager or a real service:
releases come from an httpx mock transport, uv and pkexec are stub scripts and
service answers are synthetic.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PySide6.QtCore import QCoreApplication, QEvent, QObject

from kira import __version__
from kira.domain.paths import Paths
from kira.ui import updates as updates_module
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.preferences import valid_preference
from kira.ui.updates import (
    RELEASE_PUBLIC_KEY, UpdateError, Updates, apply_source, install_kind, latest_release, parse_version,
    plain_notes, prepare_package, source_status, ubuntu_version, verified_checksums,
)

ROOT = Path(__file__).resolve().parents[1]
DEB = "kira-desktop_ubuntu24.04_amd64.deb"


def newer() -> str:
    major, minor, patch = parse_version(__version__)
    return f"{major}.{minor + 1}.0"


@pytest.fixture
def signer(monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(updates_module, "RELEASE_PUBLIC_KEY", public)
    return key


def release_server(payload: bytes, sums: bytes, signature: bytes | None, *, version: str | None = None):
    version = version or newer()
    assets = [{"name": DEB, "browser_download_url": "https://example.invalid/deb", "size": len(payload)},
              {"name": "SHA256SUMS", "browser_download_url": "https://example.invalid/sums", "size": len(sums)}]
    if signature is not None:
        assets.append({"name": "SHA256SUMS.sig", "browser_download_url": "https://example.invalid/sig",
                       "size": len(signature)})
    bodies = {"/deb": payload, "/sums": sums, "/sig": signature or b""}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"tag_name": f"v{version}", "draft": False, "prerelease": False,
                                             "body": "Novità sintetiche", "html_url": "https://example.invalid/r",
                                             "assets": assets})
        return httpx.Response(200, content=bodies[request.url.path])
    return httpx.Client(transport=httpx.MockTransport(handler))


def signed(signer, payload: bytes, name: str = DEB) -> tuple[bytes, bytes]:
    sums = f"{hashlib.sha256(payload).hexdigest()}  {name}\n".encode()
    return sums, signer.sign(sums)


def test_versions_and_the_embedded_key_match_the_published_one():
    assert parse_version("v0.19.0") == (0, 19, 0) and parse_version("0.20.1") == (0, 20, 1)
    assert parse_version("0.19") is None and parse_version("v1.2.3-rc1") is None and parse_version(None) is None
    assert RELEASE_PUBLIC_KEY == (ROOT / "packaging/release-signing-key.pub.pem").read_text()


def test_release_notes_are_shown_as_plain_text():
    notes = "## Novità\r\n\n\n\n- **Firma** verificata con [la chiave](https://example.invalid/key)\n* `uv sync`"
    assert plain_notes(notes) == "Novità\n\n• Firma verificata con la chiave\n• uv sync"


def test_checksums_are_read_only_with_a_valid_signature(signer):
    sums, signature = signed(signer, b"package")
    assert verified_checksums(sums, signature) == {DEB: hashlib.sha256(b"package").hexdigest()}
    with pytest.raises(UpdateError, match="firma"):
        verified_checksums(sums.replace(b"  ", b" *"), signature)
    with pytest.raises(UpdateError, match="firma"):
        verified_checksums(sums, Ed25519PrivateKey.generate().sign(sums))


def test_a_signed_package_is_downloaded_privately(signer, tmp_path):
    payload = b"synthetic package"
    with release_server(payload, *signed(signer, payload)) as client:
        release = latest_release(client)
        assert release["version"] == newer() and release["notes"] == "Novità sintetiche"
        target = prepare_package(client, release, "24.04", tmp_path / "updates")
    assert target.read_bytes() == payload and target.stat().st_mode & 0o777 == 0o600
    assert target.name == f"kira-desktop_{newer()}_ubuntu24.04_amd64.deb"


def test_unsigned_tampered_or_foreign_packages_are_refused(signer, tmp_path):
    payload = b"synthetic package"
    sums, signature = signed(signer, payload)
    with release_server(payload, sums, None) as client:
        with pytest.raises(UpdateError, match="non è firmata"):
            prepare_package(client, latest_release(client), "24.04", tmp_path)
    with release_server(b"tampered package", sums, signature) as client:
        with pytest.raises(UpdateError, match="impronta"):
            prepare_package(client, latest_release(client), "24.04", tmp_path)
    assert not list(tmp_path.glob("*.deb"))
    with release_server(payload, sums, signature) as client:
        with pytest.raises(UpdateError, match="Ubuntu"):
            prepare_package(client, latest_release(client), "22.04", tmp_path)
    other = signed(signer, payload, "kira-desktop_ubuntu26.04_amd64.deb")
    with release_server(payload, *other) as client:
        with pytest.raises(UpdateError, match="comprendono"):
            prepare_package(client, latest_release(client), "24.04", tmp_path)


def test_a_test_release_endpoint_still_needs_the_built_in_signature(signer, tmp_path, monkeypatch):
    seen = []
    payload = b"synthetic package"
    sums, _ = signed(signer, payload)
    monkeypatch.setenv("KIRA_UPDATE_RELEASE_URL", "http://127.0.0.1:9/latest")

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/latest":
            return httpx.Response(200, json={"tag_name": f"v{newer()}", "assets": [
                {"name": name, "browser_download_url": f"http://127.0.0.1:9/{name}"}
                for name in (DEB, "SHA256SUMS", "SHA256SUMS.sig")]})
        return httpx.Response(200, content={"SHA256SUMS": sums, "SHA256SUMS.sig": b"x" * 64}.get(
            request.url.path.lstrip("/"), payload))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        release = latest_release(client)
        with pytest.raises(UpdateError, match="firma"):
            prepare_package(client, release, "24.04", tmp_path)
    assert seen[0] == "http://127.0.0.1:9/latest"


def test_drafts_and_missing_releases_are_not_offered():
    def handler(request):
        return httpx.Response(200, json={"tag_name": "v9.0.0", "draft": True, "assets": []})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(UpdateError):
        latest_release(client)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        with pytest.raises(UpdateError, match="release"):
            latest_release(client)


def test_installation_kind_and_ubuntu_version(tmp_path):
    assert install_kind(Path("/opt/kira-desktop/lib/kira/__init__.py"))[0] == "package"
    checkout = tmp_path / "checkout"
    (checkout / "src/kira").mkdir(parents=True)
    (checkout / ".git").mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "kira-desktop"\n')
    assert install_kind(checkout / "src/kira/__init__.py") == ("source", checkout)
    (checkout / "pyproject.toml").write_text('[project]\nname = "other"\n')
    assert install_kind(checkout / "src/kira/__init__.py") == ("other", None)
    release = tmp_path / "os-release"
    release.write_text('NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\n')
    assert ubuntu_version(release) == "24.04"
    release.write_text('ID=debian\nVERSION_ID="13"\n')
    assert ubuntu_version(release) == ""
    assert ubuntu_version(tmp_path / "missing") == ""


def git(*arguments, cwd):
    return subprocess.run(["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "Kira Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                               "GIT_COMMITTER_NAME": "Kira Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
                          ).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A clone one commit behind its origin, and a uv stub whose result the test chooses."""
    origin, local, upstream = tmp_path / "origin.git", tmp_path / "local", tmp_path / "upstream"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    git("clone", str(origin), str(upstream), cwd=tmp_path)
    (upstream / "src/kira").mkdir(parents=True)
    (upstream / "src/kira/__init__.py").write_text('__version__ = "0.1.0"\n')
    git("add", ".", cwd=upstream)
    git("commit", "-m", "first", cwd=upstream)
    git("push", "origin", "main", cwd=upstream)
    git("clone", str(origin), str(local), cwd=tmp_path)
    (upstream / "src/kira/__init__.py").write_text('__version__ = "0.2.0"\n')
    git("commit", "-am", "Synthetic improvement", cwd=upstream)
    git("push", "origin", "main", cwd=upstream)
    tools = tmp_path / "bin"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text('#!/bin/sh\necho "$@" >> "$UV_LOG"\nexit "${UV_EXIT:-0}"\n')
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("UV_LOG", str(tmp_path / "uv.log"))
    return local, tmp_path / "uv.log"


def test_a_source_checkout_sees_and_applies_its_upstream(checkout):
    local, log = checkout
    status = source_status(local)
    assert status["behind"] == 1 and status["version"] == "0.2.0"
    assert status["notes"] == "Synthetic improvement" and status["fast_forward"] and not status["dirty"]
    apply_source(local, ["claude-code"])
    assert (local / "src/kira/__init__.py").read_text() == '__version__ = "0.2.0"\n'
    assert log.read_text().splitlines() == ["sync --frozen --extra claude-code"]
    assert source_status(local)["behind"] == 0


def test_a_failed_sync_returns_to_the_previous_commit(checkout, monkeypatch):
    local, log = checkout
    before = git("rev-parse", "HEAD", cwd=local)
    monkeypatch.setenv("UV_EXIT", "1")
    with pytest.raises(UpdateError, match="ripristinato"):
        apply_source(local, [])
    assert git("rev-parse", "HEAD", cwd=local) == before
    assert len(log.read_text().splitlines()) == 2


def test_local_changes_block_a_source_update(checkout):
    local, log = checkout
    (local / "src/kira/__init__.py").write_text('__version__ = "0.1.0-local"\n')
    assert source_status(local)["dirty"]
    with pytest.raises(UpdateError, match="modifiche non salvate"):
        apply_source(local, [])
    assert not log.exists()


def test_update_preferences_are_validated():
    assert valid_preference("check_updates", False) and not valid_preference("check_updates", "no")
    assert valid_preference("skipped_update_version", "0.20.0") and valid_preference("skipped_update_version", "")
    assert not valid_preference("skipped_update_version", "latest")


class Inline:
    """threading.Thread that runs its target at start(), so deliveries are immediate."""

    def __init__(self, target, name=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def flow(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "ipc"))))
    requests, held, recovered = [], [], []

    def request(method, params, done, failed=None, **kwargs):
        requests.append((method, done, failed))
        return True

    monkeypatch.setattr(workspace.client, "request", request)
    monkeypatch.setattr(workspace, "_recover_local_service", lambda: recovered.append(True))
    original_hold = workspace.holdService
    monkeypatch.setattr(workspace, "holdService", lambda hold: (held.append(hold), original_hold(hold)))
    workspace._connected_value = True
    monkeypatch.setattr(updates_module.threading, "Thread", Inline)
    release = {"version": newer(), "notes": "Novità", "url": "https://example.invalid/r", "assets": {}}
    monkeypatch.setattr(updates_module, "latest_release", lambda client: dict(release))
    monkeypatch.setattr(updates_module, "ubuntu_version", lambda: "24.04")
    package = tmp_path / "kira.deb"
    package.write_bytes(b"deb")
    monkeypatch.setattr(updates_module, "prepare_package", lambda client, release, version, cache: package)
    launched, exits = [], []
    monkeypatch.setattr(updates_module.QProcess, "startDetached",
                        lambda program, arguments, directory: (launched.append(program), (True, 1))[1])
    monkeypatch.setattr(updates_module.QCoreApplication, "exit", lambda code=0: exits.append(code))
    controller = Updates(workspace, kind=("package", Path("/opt/kira-desktop")))
    state = {"workspace": workspace, "updates": controller, "requests": requests, "held": held,
             "recovered": recovered, "launched": launched, "exits": exits, "package": package, "tmp": tmp_path}
    yield state
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def answer(requests, method, result=None, error=None):
    for name, done, failed in requests:
        if name == method:
            if error is not None:
                failed(error)
            else:
                done(result or {})
            return
    raise AssertionError(f"no {method} request")


def test_a_newer_release_is_offered_and_can_be_skipped(flow):
    updates = flow["updates"]
    updates.check()
    assert updates.state == "available" and updates.available and updates.latestVersion == newer()
    updates.skip()
    assert flow["workspace"].uiPreferences["skipped_update_version"] == newer()
    updates._automatic_check()
    assert updates.state == "current" and not updates.available
    updates.check()
    assert updates.state == "available", "a manual check still shows a skipped version"
    updates.setEnabled(False)
    assert not updates.enabled
    updates._state = "idle"
    updates._automatic_check()
    assert updates.state == "idle"


def test_the_installed_version_is_current(flow, monkeypatch):
    monkeypatch.setattr(updates_module, "latest_release",
                        lambda client: {"version": __version__, "notes": "", "url": "", "assets": {}})
    flow["updates"].check()
    assert flow["updates"].state == "current"


def test_a_busy_service_stops_the_update_before_anything_changes(flow):
    updates, requests = flow["updates"], flow["requests"]
    updates.check()
    updates.install()
    assert updates.state == "updating"
    answer(requests, "database.backup", {"path": "/synthetic/backup.sqlite3"})
    assert flow["held"] == [True]
    answer(requests, "service.shutdown", error={"code": "BUSY", "message": "Active runs must finish"})
    assert updates.state == "failed" and flow["held"] == [True, False]
    assert {step["key"]: step["status"] for step in updates.steps}["stop"] == "failed"
    assert not flow["launched"] and updates.canInstall


def test_the_password_prompt_installs_and_restarts(flow, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(updates_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(updates_module.subprocess, "run", fake_run)
    updates, requests = flow["updates"], flow["requests"]
    updates.check()
    updates.install()
    answer(requests, "database.backup", {"path": "/synthetic/backup.sqlite3"})
    answer(requests, "service.shutdown", {"stopping": True})
    assert calls == [["pkexec", "apt-get", "install", "-y", "--no-remove", str(flow["package"])]]
    assert flow["launched"] == ["/usr/bin/kira-desktop"] and flow["exits"] == [0]


def test_without_a_password_prompt_kira_shows_the_command(flow, monkeypatch):
    monkeypatch.setattr(updates_module.shutil, "which", lambda name: None if name == "pkexec" else f"/usr/bin/{name}")
    updates, requests = flow["updates"], flow["requests"]
    updates.check()
    updates.install()
    answer(requests, "database.backup", {"path": "/synthetic/backup.sqlite3"})
    answer(requests, "service.shutdown", {"stopping": True})
    assert updates.state == "manual"
    assert updates.manualCommand == f"sudo apt install --no-remove {flow['package']}"
    assert not flow["launched"]
    updates.resume()
    assert updates.state == "available" and flow["held"][-1] is False and flow["recovered"]


def test_a_failed_install_brings_the_installed_service_back(flow, monkeypatch):
    monkeypatch.setattr(updates_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(updates_module.subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 100, "", "E: broken"))
    updates, requests = flow["updates"], flow["requests"]
    updates.check()
    updates.install()
    answer(requests, "database.backup", {"path": "/synthetic/backup.sqlite3"})
    answer(requests, "service.shutdown", {"stopping": True})
    assert updates.state == "failed" and "E: broken" in updates.message
    assert flow["held"] == [True, False] and not flow["launched"]


def test_a_source_checkout_restarts_from_its_folder(flow, monkeypatch, tmp_path):
    applied = []
    monkeypatch.setattr(updates_module, "source_status", lambda root: {
        "upstream": "origin/main", "behind": 2, "version": newer(), "notes": "Synthetic", "dirty": False,
        "fast_forward": True})
    monkeypatch.setattr(updates_module, "apply_source", lambda root, extras: applied.append(root))
    updates = Updates(flow["workspace"], kind=("source", tmp_path))
    updates.check()
    assert updates.state == "available" and updates.notes == "Synthetic"
    updates.install()
    answer(flow["requests"], "database.backup", {"path": "/synthetic/backup.sqlite3"})
    answer(flow["requests"], "service.shutdown", {"stopping": True})
    assert applied == [tmp_path] and flow["launched"] == [sys.executable] and flow["exits"] == [0]


def test_the_update_dialog_and_chip_load_cleanly(qapp, tmp_path, monkeypatch, qtbot, qtlog):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "ipc"))))
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: True)
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    try:
        updates = engine.rootContext().contextProperty("updates")
        assert isinstance(updates, Updates) and not window.findChild(QObject, "updateChip").property("visible")
        updates._latest = {"version": newer(), "notes": "Novità sintetiche", "url": "https://example.invalid/r"}
        updates._set(state="available")
        qtbot.waitUntil(lambda: window.findChild(QObject, "updateChip").property("visible"))
        dialog = window.findChild(QObject, "updateDialog")
        dialog.open()
        qtbot.waitUntil(lambda: dialog.property("opened"))
        assert window.findChild(QObject, "updateNotes").property("text") == "Novità sintetiche"
        updates._steps = {"backup": "done", "prepare": "running"}
        updates._set(state="updating")
        qtbot.wait(50)
        assert not window.findChild(QObject, "updateInstall").property("enabled")
        dialog.close()
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        workspace.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any("TypeError" in record.message or "ReferenceError" in record.message
                   or "Unable to assign" in record.message or "UpdateDialog" in record.message
                   for record in qtlog.records), [record.message for record in qtlog.records]
