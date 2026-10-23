"""Explicit, profile-local Chromium setup; never installs into a personal browser."""

import fcntl
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths


def install_browser(paths: Paths | None = None) -> int:
    paths = paths or Paths.from_env()
    paths.ensure()
    destination = paths.data / "browser-engine"
    if destination.is_symlink():
        raise ValueError("Browser engine storage must not be a symlink")
    destination.mkdir(mode=0o700, exist_ok=True)
    destination.chmod(0o700)
    spec = importlib.util.find_spec("playwright")
    if spec is None or spec.origin is None:
        raise RuntimeError("This Kira installation is missing its Playwright dependency")
    package_root = str(Path(spec.origin).parent.parent)
    fd = os.open(destination / ".install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise KiraError("BUSY", "Another browser engine installation is running for this "
                            "profile; wait for it to finish") from None
        # Keep previously installed revisions for running browsers and rollback.
        # The child gets no provider credentials or inherited HTTP proxy settings.
        environment = {key: value for key, value in os.environ.items()
                       if key in {"HOME", "PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
        environment.update({"PLAYWRIGHT_BROWSERS_PATH": str(destination), "PLAYWRIGHT_SKIP_BROWSER_GC": "1"})
        bootstrap = "import sys;sys.path.insert(0,sys.argv.pop(1));from playwright.__main__ import main;main()"
        return subprocess.run([sys.executable, "-I", "-c", bootstrap, package_root,
                               "install", "chromium", "--no-shell"], env=environment, check=False).returncode
    finally:
        os.close(fd)
