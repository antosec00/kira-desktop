"""Render trusted Studio scene JSON in a short-lived headless Qt process.

Reads one JSON document on stdin, writes only PNG bytes to stdout.
This does not execute scene-authored source code and is not a general sandbox.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QGuiApplication

    from kira.domain.studio import MAX_SCENE_BYTES, validate_scene
    from kira.studio.paint2d import render_png

    raw = sys.stdin.buffer.read(MAX_SCENE_BYTES + 1)
    if len(raw) > MAX_SCENE_BYTES:
        return 2
    try:
        value = validate_scene(json.loads(raw.decode("utf-8")))
        app = QGuiApplication([])
        _ = app
        png = render_png(value)
        if len(png) > 4 * 1024 * 1024 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            return 3
        sys.stdout.buffer.write(png)
        sys.stdout.buffer.flush()
        return 0
    except (ValueError, TypeError, RuntimeError, OSError, KeyError):
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
