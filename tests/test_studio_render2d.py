"""Real Qt offscreen raster smoke for small scenes; no inference and no network."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage

import pytest

from kira.domain.contracts import KiraError

from kira.domain.studio import patch_scene, poster_scene
from kira.studio.paint2d import render_png, star_path


def test_offscreen_poster_png_and_replay_are_deterministic(qapp):
    scene = poster_scene("Synthetic poster caption")
    png = render_png(scene)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert png == render_png(scene)
    image = QImage.fromData(png)
    assert image.width() == 960 and image.height() == 540
    assert image.pixelColor(0, 0).name() != "#ffffff"
    revised = render_png(patch_scene(scene, "message", {"color": "#182b68"}))
    assert revised != png
    assert scene["objects"][-1]["color"] == "#2467c9"


def test_bounded_own_star_path(qapp):
    path = star_path(100, 100, 20)
    assert not path.isEmpty()
    assert path.boundingRect().width() <= 41


@pytest.mark.asyncio
async def test_short_lived_packaged_worker_generates_private_preview_and_export(tmp_path):
    from uuid import uuid4
    from test_studio_service import fixture

    studio, store, pid = fixture(tmp_path)
    try:
        await studio.dispatch("studio.enable", {"project_id": pid,
            "enabled": True, "expected_enabled": False, "request_id": str(uuid4())})
        await studio.dispatch("studio.poster", {"project_id": pid,
            "text": "Synthetic poster caption", "expected_revision": 0, "request_id": str(uuid4())})
        preview = await studio.dispatch("studio.render", {"project_id": pid})
        assert preview["revision"] == 1
        with open(preview["path"], "rb") as image:
            assert image.read(8) == b"\x89PNG\r\n\x1a\n"
        exported = await studio.dispatch("studio.export", {"project_id": pid})
        assert exported["revision"] == 1
        assert "/studio/exports/" in exported["path"]
        with open(exported["path"], "rb") as image:
            assert image.read(8) == b"\x89PNG\r\n\x1a\n"
        assert not studio._renders
        await studio.dispatch("studio.enable", {"project_id": pid,
            "enabled": False, "expected_enabled": True, "request_id": str(uuid4())})
        with pytest.raises(KiraError):
            await studio.dispatch("studio.render", {"project_id": pid})
        assert not studio._renders
    finally:
        await studio.close()
        store.close()
