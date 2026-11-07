"""Icons drawn with QPainter so they look the same on every desktop and need no icon theme or SVG module."""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _pm(size, color, draw):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), size / 11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.scale(size / 24, size / 24)
    draw(p)
    p.end()
    return pm


def _home(p):
    p.drawPolyline([QPointF(3, 12), QPointF(12, 4), QPointF(21, 12)])
    p.drawPolyline([QPointF(6, 10.5), QPointF(6, 20), QPointF(18, 20), QPointF(18, 10.5)])


def _sun(p):
    p.drawEllipse(QPointF(12, 12), 4, 4)
    for i in range(8):
        a = i * math.pi / 4
        p.drawLine(QPointF(12 + 7 * math.cos(a), 12 + 7 * math.sin(a)), QPointF(12 + 10 * math.cos(a), 12 + 10 * math.sin(a)))


def _gauge(p):
    p.drawArc(QRectF(3, 4, 18, 18), 200 * 16, -220 * 16)
    p.drawLine(QPointF(12, 13), QPointF(17, 8))


def _moon(p):
    path = QPainterPath()
    path.moveTo(19, 14.5)
    path.arcTo(QRectF(4, 4, 16, 16), -20, 250)
    path.cubicTo(14, 17, 10, 13, 10, 7)
    p.drawPath(path)


def _pen(p):
    p.drawPolyline([QPointF(4, 20), QPointF(5, 15), QPointF(16, 4), QPointF(20, 8), QPointF(9, 19), QPointF(4, 20)])
    p.drawLine(QPointF(14, 6), QPointF(18, 10))


def _camera(p):
    p.drawRoundedRect(QRectF(3, 7, 18, 12), 3, 3)
    p.drawEllipse(QPointF(12, 13), 3.5, 3.5)
    p.drawLine(QPointF(8, 7), QPointF(9.5, 4.5))
    p.drawLine(QPointF(9.5, 4.5), QPointF(14.5, 4.5))


def _book(p):
    p.drawRoundedRect(QRectF(4, 3, 16, 11), 2, 2)
    p.drawLine(QPointF(2, 21), QPointF(22, 21))
    p.drawLine(QPointF(5, 17), QPointF(19, 17))
    p.drawLine(QPointF(5, 17), QPointF(5, 21))
    p.drawLine(QPointF(19, 17), QPointF(19, 21))


def _wrench(p):
    p.drawEllipse(QPointF(16, 8), 4.5, 4.5)
    p.drawLine(QPointF(13, 11), QPointF(4, 20))


def _box(p):
    p.drawPolygon([QPointF(12, 3), QPointF(21, 8), QPointF(21, 16), QPointF(12, 21), QPointF(3, 16), QPointF(3, 8)])
    p.drawPolyline([QPointF(3, 8), QPointF(12, 13), QPointF(21, 8)])
    p.drawLine(QPointF(12, 13), QPointF(12, 21))


def _battery(p):
    p.drawRoundedRect(QRectF(3, 7, 16, 10), 2, 2)
    p.drawLine(QPointF(21, 10), QPointF(21, 14))


DRAW = {"home": _home, "display": _sun, "performance": _gauge, "sleep": _moon, "pen": _pen,
        "camera": _camera, "book": _book, "fixes": _wrench, "software": _box, "battery": _battery}


def icon(name: str, color: str, size: int = 20) -> QIcon:
    return QIcon(_pm(size, color, DRAW[name]))


def app_icon() -> QIcon:
    """Antenna-free, simple: a rounded tile with a gauge."""
    def draw(p):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0f6cbd"))
        p.drawRoundedRect(QRectF(1, 1, 22, 22), 6, 6)
        pen = QPen(QColor("white"), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(5, 6, 14, 14), 200 * 16, -220 * 16)
        p.drawLine(QPointF(12, 14), QPointF(16, 9.5))
    ic = QIcon()
    for s in (16, 32, 64, 128):
        pm = QPixmap(s * 2, s * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(s / 24, s / 24)
        draw(p)
        p.end()
        ic.addPixmap(pm)
    return ic


def warning_pixmap(size: int, color: str) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    pen = QPen(QColor(color), 2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(QColor(color))
    p.drawPolygon([QPointF(12, 2.5), QPointF(22.5, 20.5), QPointF(1.5, 20.5)])
    p.setPen(QPen(QColor("white"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(12, 9), QPointF(12, 14.5))
    p.drawPoint(QPointF(12, 17.6))
    p.end()
    return pm
