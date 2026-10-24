"""Kira updates itself on request: release checks, verified downloads and a clean restart.

A package install follows the public releases: the package for this Ubuntu
version is downloaded, the signature of SHA256SUMS is checked with the key built
into Kira, and the package is installed with the system password prompt or with
a command the user runs. A source checkout follows its own branch with git and
uv. Nothing changes while work runs: Kira makes a consistent backup and the
service must stop cleanly before anything is replaced.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

import kira
from kira import __version__
from kira.ui.presentation import tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
REPOSITORY = "antosec00/kira-desktop"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
# The public half of the release signing key; the private half signs SHA256SUMS
# in the release workflow. packaging/release-signing-key.pub.pem holds the same key.
RELEASE_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA+ZJge2G6enp6/wEHzfYJFwB3tW6aeHHpBvdt3Sh3PxY=
-----END PUBLIC KEY-----
"""
PACKAGE_ROOT = Path("/opt/kira-desktop")
PACKAGE_ASSETS = {"24.04": "kira-desktop_ubuntu24.04_amd64.deb", "26.04": "kira-desktop_ubuntu26.04_amd64.deb"}
FIRST_CHECK_DELAY_MS = 8000
CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_SMALL_BYTES = 64 * 1024
MAX_NOTES_CHARS = 4000
STEPS = ("backup", "prepare", "stop", "install", "restart")


class UpdateError(Exception):
    """A failed update step; the message is shown to the user."""


def parse_version(text: Any) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})", str(text or "").strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def install_kind(module_file: Path | None = None) -> tuple[str, Path | None]:
    """'package' under /opt/kira-desktop, 'source' in a git checkout, otherwise 'other'."""
    package_dir = Path(module_file or kira.__file__).resolve().parent
    if package_dir.is_relative_to(PACKAGE_ROOT):
        return "package", PACKAGE_ROOT
    for root in package_dir.parents:
        pyproject = root / "pyproject.toml"
        if pyproject.is_file() and (root / ".git").exists():
            try:
                if 'name = "kira-desktop"' in pyproject.read_text(encoding="utf-8"):
                    return "source", root
            except (OSError, UnicodeError):
                pass
            break
    return "other", None


def ubuntu_version(os_release: Path = Path("/etc/os-release")) -> str:
    fields: dict[str, str] = {}
    try:
        for line in os_release.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip().strip('"')
    except (OSError, UnicodeError):
        return ""
    if fields.get("ID") != "ubuntu" and "ubuntu" not in fields.get("ID_LIKE", "").split():
        return ""
    return fields.get("VERSION_ID", "")


def verified_checksums(sums: bytes, signature: bytes, public_key_pem: str | None = None) -> dict[str, str]:
    """The checksums of a release, only when their signature is valid."""
    key = load_pem_public_key((public_key_pem or RELEASE_PUBLIC_KEY).encode())
    if not isinstance(key, Ed25519PublicKey):
        raise UpdateError(tr("La chiave di verifica degli aggiornamenti non è valida."))
    try:
        key.verify(signature, sums)
    except InvalidSignature as error:
        raise UpdateError(tr("La firma dell'aggiornamento non è valida: non installo nulla.")) from error
    result: dict[str, str] = {}
    for line in sums.decode("utf-8", "replace").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?(\S+)", line.strip())
        if match:
            result[match[2]] = match[1]
    return result


def plain_notes(markdown: str) -> str:
    """Release notes as readable plain text: no heading marks, link targets or emphasis."""
    lines = []
    for line in markdown.replace("\r\n", "\n").splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"(\*\*|__|`)", "", line)
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:MAX_NOTES_CHARS]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def http_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True,
                        headers={"User-Agent": f"Kira-Desktop/{__version__}",
                                 "Accept": "application/vnd.github+json"})


def releases_url() -> str:
    """GitHub, or a test or mirror release endpoint; the built-in key still decides what installs."""
    return os.environ.get("KIRA_UPDATE_RELEASE_URL") or RELEASES_API


def latest_release(client: httpx.Client) -> Json:
    response = client.get(releases_url())
    if response.status_code == 404:
        raise UpdateError(tr("Non ci sono ancora release pubblicate."))
    if response.status_code >= 400:
        raise UpdateError(tr("GitHub ha risposto con l'errore HTTP {status}.", status=response.status_code))
    data = response.json()
    version = parse_version(data.get("tag_name"))
    if version is None or data.get("draft") or data.get("prerelease"):
        raise UpdateError(tr("L'ultima release non ha un numero di versione valido."))
    assets = {str(asset.get("name")): {"url": str(asset.get("browser_download_url")), "size": int(asset.get("size") or 0)}
              for asset in data.get("assets") or [] if isinstance(asset, dict)}
    return {"version": ".".join(map(str, version)), "notes": plain_notes(str(data.get("body") or "")[:4 * MAX_NOTES_CHARS]),
            "url": str(data.get("html_url") or f"https://github.com/{REPOSITORY}/releases"), "assets": assets}


def download(client: httpx.Client, url: str, limit: int, destination: Path | None = None) -> bytes:
    """Stream at most limit bytes; a file destination is written privately and atomically."""
    received = bytearray()
    temporary = destination.with_suffix(destination.suffix + ".part") if destination else None
    with client.stream("GET", url) as response:
        if response.status_code >= 400:
            raise UpdateError(tr("Download non riuscito: HTTP {status}.", status=response.status_code))
        handle = None
        try:
            if temporary is not None:
                temporary.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                handle = os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb")
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise UpdateError(tr("Il file scaricato è più grande del previsto: non installo nulla."))
                if handle is not None:
                    handle.write(chunk)
                else:
                    received.extend(chunk)
        finally:
            if handle is not None:
                handle.close()
    if temporary is not None and destination is not None:
        os.replace(temporary, destination)
    return bytes(received)


def prepare_package(client: httpx.Client, release: Json, version_id: str, cache: Path) -> Path:
    """Download this system's package and accept it only with a valid signature and checksum."""
    name = PACKAGE_ASSETS.get(version_id)
    if name is None:
        raise UpdateError(tr("Non c'è un pacchetto di Kira per questa versione di Ubuntu."))
    assets = release["assets"]
    if name not in assets or "SHA256SUMS" not in assets:
        raise UpdateError(tr("La release non contiene il pacchetto per questo sistema."))
    if "SHA256SUMS.sig" not in assets:
        raise UpdateError(tr("La release non è firmata: per sicurezza non la installo da Kira."))
    sums = download(client, assets["SHA256SUMS"]["url"], MAX_SMALL_BYTES)
    signature = download(client, assets["SHA256SUMS.sig"]["url"], MAX_SMALL_BYTES)
    expected = verified_checksums(sums, signature).get(name)
    if expected is None:
        raise UpdateError(tr("Le impronte firmate non comprendono il pacchetto per questo sistema."))
    target = cache / f"kira-desktop_{release['version']}_ubuntu{version_id}_amd64.deb"
    download(client, assets[name]["url"], MAX_PACKAGE_BYTES, target)
    if file_sha256(target) != expected:
        target.unlink(missing_ok=True)
        raise UpdateError(tr("L'impronta del pacchetto scaricato non corrisponde: non installo nulla."))
    return target


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 120) -> str:
    # No credential or passphrase prompt can appear from a background check.
    environment = {"GIT_SSH_COMMAND": "ssh -o BatchMode=yes", **os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        completed = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True,
                                   timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError(tr("Il comando {command} non è riuscito: {error}", command=command[0],
                             error=type(error).__name__)) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or [""]
        raise UpdateError(tr("Il comando {command} non è riuscito: {error}", command=" ".join(command[:2]),
                             error=detail[0][:200]))
    return completed.stdout.strip()


def source_status(root: Path) -> Json:
    """New commits on this checkout's upstream branch, and whether they can be applied."""
    if shutil.which("git") is None:
        raise UpdateError(tr("git non è disponibile."))
    run(["git", "fetch", "--quiet"], cwd=root, timeout=120)
    try:
        upstream = run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], cwd=root)
    except UpdateError as error:
        raise UpdateError(tr("Questa cartella non segue un ramo remoto: aggiornala con git.")) from error
    behind = int(run(["git", "rev-list", "--count", "HEAD..@{upstream}"], cwd=root) or "0")
    version = __version__
    if behind:
        source = run(["git", "show", "@{upstream}:src/kira/__init__.py"], cwd=root)
        match = re.search(r'__version__ = "([^"]+)"', source)
        version = match[1] if match else __version__
    subjects = run(["git", "log", "--format=%s", "-20", "HEAD..@{upstream}"], cwd=root) if behind else ""
    dirty = bool(run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root))
    try:
        run(["git", "merge-base", "--is-ancestor", "HEAD", "@{upstream}"], cwd=root)
        fast_forward = True
    except UpdateError:
        fast_forward = False
    return {"upstream": upstream, "behind": behind, "version": version, "notes": subjects,
            "dirty": dirty, "fast_forward": fast_forward}


def source_extras() -> list[str]:
    """Keep the optional extras that are installed, so the update does not remove them."""
    return ["claude-code"] if importlib.util.find_spec("claude_agent_sdk") is not None else []


def apply_source(root: Path, extras: list[str]) -> None:
    """Fast-forward and sync; on a failed sync go back to the previous commit and environment."""
    if shutil.which("uv") is None:
        raise UpdateError(tr("uv non è disponibile."))
    status = source_status(root)
    if status["dirty"]:
        raise UpdateError(tr("Ci sono modifiche non salvate nella cartella dei sorgenti: non aggiorno."))
    if not status["fast_forward"]:
        raise UpdateError(tr("Il ramo locale si è separato da quello remoto: aggiornalo con git."))
    previous = run(["git", "rev-parse", "HEAD"], cwd=root)
    sync = ["uv", "sync", "--frozen", *[part for extra in extras for part in ("--extra", extra)]]
    run(["git", "merge", "--ff-only", "--quiet", "@{upstream}"], cwd=root)
    try:
        run(sync, cwd=root, timeout=900)
    except UpdateError as error:
        run(["git", "reset", "--keep", previous], cwd=root)
        try:
            run(sync, cwd=root, timeout=900)
        except UpdateError:
            pass
        raise UpdateError(tr("Aggiornamento delle dipendenze non riuscito; ho ripristinato la versione precedente. {error}",
                             error=str(error))) from error


class Updates(QObject):
    """Update checks and the guided update; exposed to QML as 'updates'."""

    changed = Signal()
    _finished = Signal(object)

    def __init__(self, workspace: Workspace, parent: QObject | None = None, *,
                 kind: tuple[str, Path | None] | None = None) -> None:
        super().__init__(parent)
        self._workspace = workspace
        self._kind, self._root = kind or install_kind()
        self._state = "idle"
        self._latest: Json = {}
        self._message = ""
        self._manual_command = ""
        self._steps: dict[str, str] = {}
        self._last_checked = ""
        self._package: Path | None = None
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self._automatic_check)
        self._finished.connect(self._deliver)
        # A slot, so the connection ends with this object when its engine is recreated.
        workspace.preferencesChanged.connect(self._preferences_changed)

    @Slot()
    def _preferences_changed(self) -> None:
        self.changed.emit()

    # Background work and its delivery on the GUI thread.

    def _run(self, kind: str, work: Callable[[], Any]) -> None:
        generation = self._generation

        def body() -> None:
            try:
                outcome: tuple[bool, Any] = (True, work())
            except Exception as error:  # reported to the user, never raised in a thread
                outcome = (False, error)
            try:
                self._finished.emit((kind, generation, outcome))
            except RuntimeError:
                pass

        threading.Thread(target=body, name=f"kira-update-{kind}", daemon=True).start()

    @Slot(object)
    def _deliver(self, message: tuple[str, int, tuple[bool, Any]]) -> None:
        kind, generation, (ok, value) = message
        if generation != self._generation:
            return
        if kind in {"check", "auto"}:
            self._checked(ok, value, automatic=kind == "auto")
        elif kind == "prepare":
            self._prepared(ok, value)
        elif kind == "install":
            self._installed(ok, value)

    # Properties read by QML.

    @qt_property(str, notify=changed)
    def state(self) -> str:
        return self._state

    @qt_property(str, notify=changed)
    def kind(self) -> str:
        return self._kind

    @qt_property(bool, notify=changed)
    def enabled(self) -> bool:
        return bool(self._workspace.uiPreferences.get("check_updates", True))

    @qt_property(str, notify=changed)
    def currentVersion(self) -> str:
        return __version__

    @qt_property(str, notify=changed)
    def latestVersion(self) -> str:
        return str(self._latest.get("version", ""))

    @qt_property(str, notify=changed)
    def notes(self) -> str:
        return str(self._latest.get("notes", ""))

    @qt_property(str, notify=changed)
    def notesUrl(self) -> str:
        return str(self._latest.get("url", f"https://github.com/{REPOSITORY}/releases"))

    @qt_property(str, notify=changed)
    def message(self) -> str:
        return self._message

    @qt_property(str, notify=changed)
    def manualCommand(self) -> str:
        return self._manual_command

    @qt_property(str, notify=changed)
    def lastChecked(self) -> str:
        return self._last_checked

    @qt_property(bool, notify=changed)
    def available(self) -> bool:
        return self._state in {"available", "failed", "manual"} and bool(self._latest)

    @qt_property(bool, notify=changed)
    def canInstall(self) -> bool:
        return self._kind in {"package", "source"} and self._state in {"available", "failed"}

    @qt_property("QVariantList", notify=changed)
    def steps(self) -> list[Json]:
        labels = {"backup": tr("Backup del database"),
                  "prepare": tr("Download e verifica della firma") if self._kind == "package"
                  else tr("Controllo dei sorgenti"),
                  "stop": tr("Arresto del servizio quando è inattivo"),
                  "install": tr("Installazione"), "restart": tr("Riavvio di Kira")}
        return [{"key": key, "label": labels[key], "status": self._steps.get(key, "pending")} for key in STEPS]

    # Checking.

    def startAutomaticChecks(self) -> None:
        """Called by the application launch only; tests never reach the network."""
        QTimer.singleShot(FIRST_CHECK_DELAY_MS, self._automatic_check)
        self._timer.start()

    def _automatic_check(self) -> None:
        if self.enabled and self._state in {"idle", "current", "error"}:
            self._check(automatic=True)

    @Slot()
    def check(self) -> None:
        if self._state not in {"updating", "checking"}:
            self._check(automatic=False)

    def _check(self, *, automatic: bool) -> None:
        self._generation += 1
        self._set(state="checking", message="")
        if self._kind == "source" and self._root is not None:
            root = self._root
            self._run("auto" if automatic else "check", lambda: source_status(root))
        else:
            def work() -> Json:
                with http_client() as client:
                    return latest_release(client)
            self._run("auto" if automatic else "check", work)

    def _checked(self, ok: bool, value: Any, *, automatic: bool) -> None:
        self._last_checked = time.strftime("%Y-%m-%d %H:%M")
        if not ok:
            self._latest = {}
            message = str(value) if isinstance(value, UpdateError) else tr("Controllo degli aggiornamenti non riuscito.")
            self._set(state="error", message=message)
            return
        if self._kind == "source":
            newer = value["behind"] > 0
            release = {"version": value["version"], "notes": value["notes"], "url": "",
                       "upstream": value["upstream"], "behind": value["behind"]}
            if newer and value["dirty"]:
                release["blocked"] = tr("Ci sono modifiche non salvate nella cartella dei sorgenti.")
        else:
            current, latest = parse_version(__version__), parse_version(value["version"])
            newer = bool(current and latest and latest > current)
            release = value
        skipped = self._workspace.uiPreferences.get("skipped_update_version", "")
        if not newer or (automatic and self._kind != "source" and release["version"] == skipped):
            self._latest = {}
            self._set(state="current", message=tr("Kira è aggiornata."))
            return
        self._latest = release
        self._set(state="available", message=str(release.get("blocked", "")))

    @Slot()
    def skip(self) -> None:
        if self._latest and self._kind != "source":
            self._workspace.setUiPreference("skipped_update_version", self.latestVersion)
        self._latest = {}
        self._set(state="idle", message="")

    @Slot(bool)
    def setEnabled(self, enabled: bool) -> None:
        self._workspace.setUiPreference("check_updates", bool(enabled))

    @Slot()
    def copyCommand(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None and self._manual_command:
            clipboard.setText(self._manual_command)

    # The guided update: backup, prepare, stop when idle, install, restart.

    @Slot()
    def install(self) -> None:
        if not self.canInstall or self._latest.get("blocked"):
            return
        if not self._workspace.connected:
            self._set(state="failed", message=tr("Attendi che il servizio sia collegato, poi riprova."))
            return
        self._generation += 1
        self._steps = {"backup": "running"}
        self._set(state="updating", message="")
        self._workspace.flushDrafts()
        self._workspace.client.request("database.backup", {}, self._backed_up, self._backup_failed)

    def _backup_failed(self, error: Json) -> None:
        self._fail("backup", tr("Backup non riuscito: {error}", error=str(error.get("message", ""))))

    def _backed_up(self, result: Json) -> None:
        self._steps.update(backup="done", prepare="running")
        self._set(message=tr("Backup coerente creato: {path}", path=str(result.get("path", ""))))
        if self._kind == "package":
            release = dict(self._latest)
            version_id = ubuntu_version()
            cache = self._workspace.paths.data / "updates"

            def work() -> Path:
                with http_client() as client:
                    return prepare_package(client, release, version_id, cache)
            self._run("prepare", work)
        else:
            root = self._root
            assert root is not None

            def check() -> Json:
                status = source_status(root)
                if status["dirty"]:
                    raise UpdateError(tr("Ci sono modifiche non salvate nella cartella dei sorgenti: non aggiorno."))
                if not status["fast_forward"]:
                    raise UpdateError(tr("Il ramo locale si è separato da quello remoto: aggiornalo con git."))
                return status
            self._run("prepare", check)

    def _prepared(self, ok: bool, value: Any) -> None:
        if not ok:
            self._fail("prepare", str(value) if isinstance(value, UpdateError) else tr("Preparazione non riuscita."))
            return
        if isinstance(value, Path):
            self._package = value
        self._steps.update(prepare="done", stop="running")
        self.changed.emit()
        # From here the service is meant to stop: it must not be restarted meanwhile.
        self._workspace.holdService(True)
        self._workspace.client.request("service.shutdown", {}, self._stopped, self._stop_refused)

    def _stop_refused(self, error: Json) -> None:
        self._workspace.holdService(False)
        reason = str(error.get("message", ""))
        self._fail("stop", tr("Il servizio ha lavoro in corso e non si è fermato, quindi non ho cambiato nulla. "
                              "Riprova quando ha finito. ({reason})", reason=reason))

    def _stopped(self, _result: Json) -> None:
        self._steps.update(stop="done", install="running")
        self.changed.emit()
        if self._kind == "package":
            package = self._package
            assert package is not None

            def work() -> str:
                if shutil.which("pkexec") is None:
                    return "manual"
                completed = subprocess.run(["pkexec", "apt-get", "install", "-y", "--no-remove", str(package)],
                                           capture_output=True, text=True, timeout=1800, check=False)
                # 126/127: the prompt was dismissed or no agent can show it.
                if completed.returncode in {126, 127}:
                    return "manual"
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or [""]
                    raise UpdateError(tr("L'installazione non è riuscita: {error}", error=detail[0][:200]))
                return "installed"
            self._run("install", work)
        else:
            root = self._root
            assert root is not None
            extras = source_extras()

            def apply() -> str:
                apply_source(root, extras)
                return "installed"
            self._run("install", apply)

    def _installed(self, ok: bool, value: Any) -> None:
        if not ok:
            # The previous version stays installed: its service starts again.
            self._fail("install", str(value) if isinstance(value, UpdateError) else tr("Installazione non riuscita."),
                       resume_service=True)
            return
        if value == "manual":
            self._steps.update(install="manual")
            self._manual_command = f"sudo apt install --no-remove {self._package}"
            self._set(state="manual", message=tr("Kira non può chiedere la password da qui. Esegui il comando "
                                                 "nel terminale, poi premi «Riavvia Kira»."))
            return
        self._steps.update(install="done", restart="running")
        self.changed.emit()
        self.restart()

    def _fail(self, step: str, message: str, *, resume_service: bool = False) -> None:
        self._steps[step] = "failed"
        if resume_service:
            self._workspace.holdService(False)
        self._set(state="failed", message=message)

    @Slot()
    def resume(self) -> None:
        """Leave the manual install for later: the installed service starts again."""
        if self._state != "manual":
            return
        self._manual_command = ""
        self._steps = {}
        self._workspace.holdService(False)
        self._set(state="available", message="")

    @Slot()
    def restart(self) -> None:
        """Start the installed Kira again and leave; the new interface starts the service."""
        if self._kind == "source" and self._root is not None:
            program, arguments, directory = sys.executable, ["-I", "-m", "kira"], str(self._root)
        else:
            program = "/usr/bin/kira-desktop"
            arguments, directory = [], str(Path.home())
        started, _pid = QProcess.startDetached(program, arguments, directory)
        if not started:
            self._workspace.holdService(False)
            self._set(state="failed", message=tr("Non sono riuscito a riavviare Kira: aprila di nuovo dal menu."))
            return
        QCoreApplication.exit(0)

    def _set(self, *, state: str | None = None, message: str | None = None) -> None:
        if state is not None:
            self._state = state
        if message is not None:
            self._message = message
        self.changed.emit()
