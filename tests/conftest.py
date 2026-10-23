"""Shared test configuration: keep the suite independent of the developer's environment."""

import os
import re
import subprocess
import time

# Qt reads the platform when the first application object is created. Without
# this, a test run from a desktop session opens real windows and uses the
# real clipboard. An explicit choice by the caller still wins.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_SENSITIVE_ENVIRONMENT = re.compile(r"(?:token|password|secret|api[_-]?key|authorization|cookie)", re.I)
_XDG_DIRECTORIES = {"XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "state",
                    "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "run"}


@pytest.fixture(scope="session", autouse=True)
def _scrub_sensitive_environment():
    """The service redactor protects credential-like environment values.

    A developer's or CI runner's own tokens would otherwise be redacted out of
    synthetic fixtures and make assertions depend on the machine. Tests that
    exercise the redactor set their own values explicitly.
    """
    removed = {key: os.environ.pop(key) for key in list(os.environ) if _SENSITIVE_ENVIRONMENT.search(key)}
    try:
        yield
    finally:
        os.environ.update(removed)


@pytest.fixture(scope="session", autouse=True)
def _isolated_user_environment(tmp_path_factory):
    """Point per-user locations, the session bus and the time zone at test values.

    Kira, Qt and the translation compiler keep caches and state below the XDG
    directories, shared with an installed Kira. Tests use private temporary
    ones, an unreachable D-Bus session and UTC. KIRA_HOME and HOME are left as
    the caller set them; short names keep Unix socket paths within their limit.
    """
    root = tmp_path_factory.mktemp("env")
    with pytest.MonkeyPatch.context() as patch:
        for name, folder in _XDG_DIRECTORIES.items():
            directory = root / folder
            directory.mkdir()
            directory.chmod(0o700)
            patch.setenv(name, str(directory))
        patch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={root / 'no-session-bus'}")
        patch.setenv("TZ", "UTC")
        time.tzset()
        try:
            yield root
        finally:
            patch.undo()
            time.tzset()


class _NoDetachedService:
    """kira.client's view of subprocess, without the detached service start.

    ensure_service() starts a background `kira.service.server` when nothing
    answers on the socket. That service picks its profile from the inherited
    environment, not from the test's Paths, and outlives the test. Connecting to
    a running fixture service never reaches this call.
    """

    def __getattr__(self, name):
        return getattr(subprocess, name)

    @staticmethod
    def Popen(*args, **kwargs):
        raise RuntimeError("Tests must not start a detached Kira service; "
                           "connect to a fixture service or patch ensure_service")


@pytest.fixture(scope="session", autouse=True)
def _no_detached_service():
    import kira.client

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(kira.client, "subprocess", _NoDetachedService())
        yield


@pytest.fixture(autouse=True)
def _restore_application_font():
    """create_engine() sets the application font; later tests must not inherit it."""
    from PySide6.QtGui import QFont, QGuiApplication

    application = QGuiApplication.instance()
    saved = QFont(application.font()) if isinstance(application, QGuiApplication) else None
    yield
    if saved is not None and isinstance(QGuiApplication.instance(), QGuiApplication):
        QGuiApplication.setFont(saved)
