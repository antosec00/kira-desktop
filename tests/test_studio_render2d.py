"""Real Qt offscreen raster smoke for small scenes; no inference and no network."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage

from kira.domain.studio import patch_scene, poster_scene
from kira.studio.paint2d import render_png, star_path


def test_offscreen_poster_png_and_replay_are_deterministic(qapp):
    scene = poster_scene("Ambra mi manchi")
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
