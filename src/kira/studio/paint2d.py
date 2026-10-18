"""Trusted, offline QPainter rasterizer of Kira's closed poster scene.

The scene DSL is validated before drawing. Qt handles glyph shaping and image
encoding; Kira owns the vector decorations, scene ordering and brush treatment.
No HTML, QML or user-provided program is interpreted here.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen,
)

from kira.domain.studio import HEIGHT, WIDTH, validate_scene


def star_path(x: int, y: int, radius: int) -> QPainterPath:
    path = QPainterPath()
    for vertex in range(10):
        angle = -math.pi / 2 + vertex * math.pi / 5
        length = radius if vertex % 2 == 0 else radius * 0.44
        position = QPointF(x + length * math.cos(angle),
                           y + length * math.sin(angle))
        if vertex == 0:
            path.moveTo(position)
        else:
            path.lineTo(position)
    path.closeSubpath()
    return path


def render_png(value: dict) -> bytes:
    """Paint and encode one bounded scene. Caller owns worker lifecycle."""
    scene = validate_scene(value)
    if QGuiApplication.instance() is None:
        raise RuntimeError("Studio render needs a QGuiApplication worker")
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise RuntimeError("Studio raster allocation failed")
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        for obj in scene["objects"]:
            if obj["kind"] == "background":
                top = QColor(obj["color"])
                bottom = top.lighter(116)
                gradient = QLinearGradient(0, 0, WIDTH, HEIGHT)
                gradient.setColorAt(0, top)
                gradient.setColorAt(1, bottom)
                painter.fillRect(0, 0, WIDTH, HEIGHT, gradient)
                continue
            if obj["kind"] == "star":
                path = star_path(obj["x"], obj["y"], obj["radius"])
                painter.setPen(Qt.PenStyle.NoPen)
                painter.fillPath(path, QColor(obj["color"]))
                continue
            if obj["kind"] == "brush_text":
                font = QFont("DejaVu Sans")
                font.setWeight(QFont.Weight.DemiBold)
                font.setPixelSize(obj["size"])
                metrics = QFontMetricsF(font)
                # Layout with Qt's real glyph shaping; bound visual overflow.
                width = max(1, metrics.horizontalAdvance(obj["text"]))
                scale = min(1.0, (WIDTH - obj["x"] - 40) / width)
                scale = max(0.10, scale)
                painter.save()
                painter.translate(obj["x"], obj["y"])
                painter.scale(scale, scale)
                path = QPainterPath()
                path.addText(QPointF(0, 0), font, obj["text"])
                ink = QColor(obj["color"])
                painter.fillPath(path, ink)
                outline = QColor(ink)
                outline.setAlpha(135)
                painter.setPen(QPen(outline, 1.25, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPath(path)
                painter.restore()
    finally:
        painter.end()
    blob = QByteArray()
    buffer = QBuffer(blob)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Studio PNG buffer could not be opened")
    try:
        # PySide6 accepts a str at runtime for the format argument. Its type
        # stubs incorrectly require bytes: see the existing attachments encoder.
        if not image.save(buffer, "PNG"):  # type: ignore[call-overload]
            raise RuntimeError("Studio PNG encoding failed")
    finally:
        buffer.close()
    return bytes(blob.data())
