"""Procedural application icon.

Drawn at runtime with QPainter instead of shipping a binary asset, so the
window/taskbar/tray icon needs no external file. packaging/build_icon.py
reuses this same builder to bake the PyInstaller .exe icon.
"""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap


def build_app_icon(size=64):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setBrush(QBrush(QColor("#1b1b1e")))
    painter.setPen(QPen(QColor("#343439"), max(1, size // 32)))
    inset = size * 0.03
    painter.drawRoundedRect(
        QRectF(inset, inset, size - 2 * inset, size - 2 * inset),
        size * 0.18,
        size * 0.18,
    )

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#ff4b4b")))
    d = size * 0.44
    painter.drawEllipse(QRectF((size - d) / 2, (size - d) / 2, d, d))

    painter.end()
    return QIcon(pixmap)
