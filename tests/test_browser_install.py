"""Browser installation is explicit, profile-local and credential-free."""

from types import SimpleNamespace

import pytest

from kira.domain.paths import Paths
from kira.tools.browser_install import install_browser


def test_install_preserves_old_engines_and_does_not_inherit_credentials(tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    # Avoid the socket path limit in pytest's long temporary directory name;
    # installation never opens a socket or starts the application service.
    monkeypatch.setattr(Paths, "ensure", lambda _: paths.data.mkdir(parents=True, exist_ok=True))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-token")
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic.invalid")
    paths.data.mkdir()
    engine = paths.data / "browser-engine"
    engine.mkdir()
    old = engine / "retained-old-revision"
    old.write_text("synthetic original engine")
    requests = []

    def run(argv, **kwargs):
        requests.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("kira.tools.browser_install.subprocess.run", run)
    assert install_browser(paths) == 0
    argv, options = requests[0]
    assert argv[-3:] == ["install", "chromium", "--no-shell"]
    assert "-I" in argv
    assert options["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(engine)
    assert options["env"]["PLAYWRIGHT_SKIP_BROWSER_GC"] == "1"
    assert "OPENAI_API_KEY" not in options["env"] and "HTTP_PROXY" not in options["env"]
    assert old.read_text() == "synthetic original engine"


def test_install_rejects_redirected_engine_directory(tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    monkeypatch.setattr(Paths, "ensure", lambda _: None)
    paths.data.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (paths.data / "browser-engine").symlink_to(unrelated)
    with pytest.raises(ValueError, match="symlink"):
        install_browser(paths)
    assert list(unrelated.iterdir()) == []
