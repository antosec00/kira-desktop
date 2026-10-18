"""Conservative WSLg presentation defaults, applied before Qt initialization."""

import os
import platform
from collections.abc import MutableMapping


def configure_graphics(
    environment: MutableMapping[str, str] | None = None,
    *,
    kernel_release: str | None = None,
) -> None:
    env = os.environ if environment is None else environment
    if env.get("WAYLAND_DISPLAY"):
        env.setdefault("QT_QPA_PLATFORM", "wayland;xcb")
    release = platform.release() if kernel_release is None else kernel_release
    is_wsl = bool(env.get("WSL_INTEROP") or env.get("WSL_DISTRO_NAME")) or "microsoft" in release.lower()
    has_display = bool(env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))
    # An explicit renderer always takes precedence, including Qt's legacy override.
    # Native Linux retains Qt's normal renderer selection. This only controls the
    # interface's Qt scene graph; inference/backend GPU settings are independent.
    explicit = any(key in env for key in ("QT_QUICK_BACKEND", "QSG_RHI_BACKEND", "QMLSCENE_DEVICE"))
    if is_wsl and has_display and not explicit:
        env["QT_QUICK_BACKEND"] = "software"
