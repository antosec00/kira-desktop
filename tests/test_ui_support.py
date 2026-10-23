"""Shared waits and isolated-service helpers for the GUI tests; this module has no tests."""

import os
import subprocess
import sys
import time

from PySide6.QtCore import QPoint, QRectF

# Waits that depend on a fixture service and its provider: generous upper
# bounds for slow runners (waitUntil returns as soon as the condition holds).
SERVICE_WAIT_MS = 30_000


def isolated_service_environment(monkeypatch, home, **extra):
    """Point this process and a service at the disposable profile ``home``.

    The GUI process receives KIRA_HOME too, so even an unexpected service
    recovery can only ever reach the disposable profile.
    """
    monkeypatch.setenv("KIRA_HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("CODEX_HOME", str(home / "codex"))
    return {**os.environ, **{key: str(value) for key, value in extra.items()}}


def start_service(arguments, environment, log):
    """Start a service process in isolated mode.

    -I resolves Kira through the selected interpreter's installed package,
    never a script-added source path or an inherited PYTHONPATH.
    """
    return subprocess.Popen([sys.executable, "-I", *map(str, arguments)], env=environment,
                            stdout=log, stderr=log)


def stop_service(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def scene_rect(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def wait_settled(qtbot, read, *, stable_ms=60, timeout=5000):
    """Return ``read()`` once it is not None and unchanged for ``stable_ms``.

    Popups and side panels size themselves from their content over several
    frames, so measuring them after a fixed delay is unreliable on slow runners.
    """
    state = {"value": None, "since": 0.0}

    def settled():
        value = read()
        now = time.monotonic()
        if value is None or value != state["value"]:
            state["value"], state["since"] = value, now
        assert value is not None and (now - state["since"]) * 1000 >= stable_ms, ("not settled", value)

    qtbot.waitUntil(settled, timeout=timeout)
    return state["value"]


def wait_laid_out(qtbot, *items, stable_ms=60, timeout=5000):
    """Wait until every item is visible, has a size and stopped moving."""
    def read():
        if not all(item is not None and item.isVisible() and item.width() > 0 and item.height() > 0
                   for item in items):
            return None
        return tuple((round(rect.x()), round(rect.y()), round(rect.width()), round(rect.height()))
                     for rect in map(scene_rect, items))

    wait_settled(qtbot, read, stable_ms=stable_ms, timeout=timeout)


def wait_opened(qtbot, popup, *items, timeout=5000):
    """Wait until a Popup/Dialog has opened and its content and ``items`` are laid out."""
    qtbot.waitUntil(lambda: popup.property("opened") is True, timeout=timeout)
    wait_laid_out(qtbot, popup.property("contentItem"), *items, timeout=timeout)


def settled_point(qtbot, read, **kwargs):
    """A QPoint from ``read() -> (x, y) | None`` once the position stopped moving."""
    def rounded():
        point = read()
        return None if point is None else (round(point[0]), round(point[1]))

    x, y = wait_settled(qtbot, rounded, **kwargs)
    return QPoint(x, y)
