"""Renderer selection respects user intent and stays scoped to WSL presentation."""

import pytest

from kira.ui.graphics import configure_graphics


def test_wslg_uses_software_without_modifying_inference_or_driver_options():
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "CUDA_VISIBLE_DEVICES": "0"}
    configure_graphics(env, kernel_release="6.18.40.1-microsoft-standard-WSL2")
    assert env == {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "CUDA_VISIBLE_DEVICES": "0",
                   "QT_QPA_PLATFORM": "wayland;xcb", "QT_QUICK_BACKEND": "software"}


@pytest.mark.parametrize("key,value", [
    ("QT_QUICK_BACKEND", "rhi"), ("QT_QUICK_BACKEND", "software"),
    ("QSG_RHI_BACKEND", "vulkan"), ("QSG_RHI_BACKEND", "opengl"),
    ("QMLSCENE_DEVICE", "software"),
])
def test_explicit_renderer_is_respected(key, value):
    env = {"WAYLAND_DISPLAY": "wayland-0", "WSL_DISTRO_NAME": "Ubuntu", key: value}
    configure_graphics(env, kernel_release="Linux")
    assert env[key] == value
    if key != "QT_QUICK_BACKEND":
        assert "QT_QUICK_BACKEND" not in env


def test_native_kde_retains_hardware_rendering_defaults():
    env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}
    configure_graphics(env, kernel_release="6.8.0-90-generic")
    assert "QT_QUICK_BACKEND" not in env
    assert env["QT_QPA_PLATFORM"] == "wayland;xcb"


def test_explicit_qpa_and_headless_selection_are_retained():
    env = {"QT_QPA_PLATFORM": "offscreen"}
    configure_graphics(env, kernel_release="microsoft-standard-WSL2")
    assert env == {"QT_QPA_PLATFORM": "offscreen"}
    env = {"DISPLAY": ":0", "WSL_INTEROP": "/run/WSL/test_interop", "QT_QPA_PLATFORM": "xcb"}
    configure_graphics(env, kernel_release="Linux")
    assert env["QT_QPA_PLATFORM"] == "xcb"
    assert env["QT_QUICK_BACKEND"] == "software"
