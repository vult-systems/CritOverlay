"""Procedural vector toolbar icons.

Drawn with QPainter at a fixed logical size instead of shipping binary
assets, so every icon shares the same stroke weight and rounding and scales
cleanly with the toolbar's style. Each builder takes a foreground color so
the same glyph can be recolored for hover/checked/disabled states.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 22


def _canvas(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return pm, painter


def _stroke_pen(color, width):
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _finish(pixmap, painter):
    painter.end()
    return QIcon(pixmap)


def brush_glyph(painter, color, size):
    painter.setPen(_stroke_pen(color, size * 0.09))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(size * 0.74, size * 0.16), QPointF(size * 0.42, size * 0.48))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    tip = QPainterPath()
    tip.moveTo(size * 0.46, size * 0.42)
    tip.lineTo(size * 0.62, size * 0.58)
    tip.lineTo(size * 0.32, size * 0.86)
    tip.lineTo(size * 0.20, size * 0.74)
    tip.closeSubpath()
    painter.drawPath(tip)


def eraser_glyph(painter, color, size):
    painter.translate(size / 2, size / 2)
    painter.rotate(-28)
    painter.translate(-size / 2, -size / 2)
    rect = QRectF(size * 0.22, size * 0.36, size * 0.56, size * 0.34)
    painter.setPen(_stroke_pen(color, size * 0.09))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, size * 0.06, size * 0.06)
    painter.drawLine(
        QPointF(rect.left(), rect.top() + rect.height() * 0.5),
        QPointF(rect.right(), rect.top() + rect.height() * 0.5),
    )


def _arrow_arc(painter, color, size, clockwise):
    pen = _stroke_pen(color, size * 0.09)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = size / 2, size / 2
    r = size * 0.30
    rect = QRectF(cx - r, cy - r, r * 2, r * 2)
    start_deg = 60 if clockwise else 120
    span_deg = -260 if clockwise else 260
    painter.drawArc(rect, int(start_deg * 16), int(span_deg * 16))

    end_deg = start_deg + span_deg
    ang = math.radians(end_deg)
    tip = QPointF(cx + r * math.cos(ang), cy - r * math.sin(ang))

    # Direction of travel along the arc at its end point (screen coords, y
    # down): for increasing theta (span_deg > 0) that's (-sin, -cos); for
    # decreasing theta it's the negation.
    dir_x, dir_y = -math.sin(ang), -math.cos(ang)
    if span_deg < 0:
        dir_x, dir_y = -dir_x, -dir_y

    head_len = size * 0.24
    head_width = size * 0.17
    back = QPointF(tip.x() - head_len * dir_x, tip.y() - head_len * dir_y)
    perp_x, perp_y = -dir_y, dir_x
    left = QPointF(back.x() + head_width / 2 * perp_x, back.y() + head_width / 2 * perp_y)
    right = QPointF(back.x() - head_width / 2 * perp_x, back.y() - head_width / 2 * perp_y)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    path = QPainterPath()
    path.moveTo(tip)
    path.lineTo(left)
    path.lineTo(right)
    path.closeSubpath()
    painter.drawPath(path)


def undo_glyph(painter, color, size):
    _arrow_arc(painter, color, size, clockwise=False)


def redo_glyph(painter, color, size):
    _arrow_arc(painter, color, size, clockwise=True)


def clear_glyph(painter, color, size):
    pen = _stroke_pen(color, size * 0.09)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    body = QRectF(size * 0.28, size * 0.40, size * 0.44, size * 0.42)
    painter.drawRoundedRect(body, size * 0.04, size * 0.04)
    painter.drawLine(QPointF(size * 0.22, size * 0.34), QPointF(size * 0.78, size * 0.34))
    painter.drawLine(QPointF(size * 0.40, size * 0.34), QPointF(size * 0.44, size * 0.22))
    painter.drawLine(QPointF(size * 0.44, size * 0.22), QPointF(size * 0.56, size * 0.22))
    painter.drawLine(QPointF(size * 0.56, size * 0.22), QPointF(size * 0.60, size * 0.34))
    painter.drawLine(QPointF(size * 0.40, size * 0.50), QPointF(size * 0.40, size * 0.72))
    painter.drawLine(QPointF(size * 0.50, size * 0.50), QPointF(size * 0.50, size * 0.72))
    painter.drawLine(QPointF(size * 0.60, size * 0.50), QPointF(size * 0.60, size * 0.72))


def pass_through_glyph(painter, color, size):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    cursor = QPainterPath()
    cursor.moveTo(size * 0.24, size * 0.16)
    cursor.lineTo(size * 0.24, size * 0.72)
    cursor.lineTo(size * 0.40, size * 0.58)
    cursor.lineTo(size * 0.50, size * 0.80)
    cursor.lineTo(size * 0.60, size * 0.75)
    cursor.lineTo(size * 0.50, size * 0.53)
    cursor.lineTo(size * 0.68, size * 0.50)
    cursor.closeSubpath()
    painter.drawPath(cursor)

    pen = _stroke_pen(color, size * 0.06)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(size * 0.06, size * 0.06, size * 0.42, size * 0.42), 40 * 16, 200 * 16)


def snapshot_glyph(painter, color, size):
    pen = _stroke_pen(color, size * 0.09)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    body = QRectF(size * 0.16, size * 0.32, size * 0.68, size * 0.48)
    painter.drawRoundedRect(body, size * 0.06, size * 0.06)
    painter.drawLine(QPointF(size * 0.38, size * 0.32), QPointF(size * 0.44, size * 0.20))
    painter.drawLine(QPointF(size * 0.44, size * 0.20), QPointF(size * 0.62, size * 0.20))
    painter.drawLine(QPointF(size * 0.62, size * 0.20), QPointF(size * 0.68, size * 0.32))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(size * 0.50, size * 0.56), size * 0.15, size * 0.15)


def exit_glyph(painter, color, size):
    pen = _stroke_pen(color, size * 0.1)
    painter.setPen(pen)
    m = size * 0.28
    painter.drawLine(QPointF(m, m), QPointF(size - m, size - m))
    painter.drawLine(QPointF(size - m, m), QPointF(m, size - m))


def hide_glyph(painter, color, size):
    # A chevron dropping onto a bar: put away, not closed.
    pen = _stroke_pen(color, size * 0.1)
    painter.setPen(pen)
    m = size * 0.28
    painter.drawLine(QPointF(m, size * 0.74), QPointF(size - m, size * 0.74))
    painter.drawPolyline([QPointF(m + size * 0.04, size * 0.3), QPointF(size / 2, size * 0.54),
                          QPointF(size - m - size * 0.04, size * 0.3)])


def more_glyph(painter, color, size):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    r = size * 0.07
    cy = size / 2
    for cx in (size * 0.28, size * 0.5, size * 0.72):
        painter.drawEllipse(QPointF(cx, cy), r, r)


def backdrop_off_glyph(painter, color, size):
    # Circle with a diagonal slash, for the backdrop toggle's "off" state.
    pen = _stroke_pen(color, size * 0.09)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    d = size * 0.6
    painter.drawEllipse(QRectF((size - d) / 2, (size - d) / 2, d, d))
    m = size * 0.24
    painter.drawLine(QPointF(size - m, m), QPointF(m, size - m))


def eyedropper_glyph(painter, color, size):
    # A pipette: angled barrel, bulb at the top, point at the bottom, plus a
    # small drop.
    painter.setPen(_stroke_pen(color, size * 0.09))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(size * 0.70, size * 0.20), QPointF(size * 0.38, size * 0.52))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    bulb_d = size * 0.20
    painter.drawEllipse(QRectF(size * 0.62 - bulb_d / 2, size * 0.12 - bulb_d / 2, bulb_d, bulb_d))

    tip = QPainterPath()
    tip.moveTo(size * 0.44, size * 0.46)
    tip.lineTo(size * 0.56, size * 0.58)
    tip.lineTo(size * 0.30, size * 0.84)
    tip.lineTo(size * 0.24, size * 0.78)
    tip.closeSubpath()
    painter.drawPath(tip)

    painter.drawEllipse(QPointF(size * 0.22, size * 0.90), size * 0.045, size * 0.045)


def monitor_glyph(painter, color, size):
    # A display on a stand, with an arrow: "move to the next monitor".
    pen = _stroke_pen(color, size * 0.08)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(size * 0.12, size * 0.18, size * 0.76, size * 0.50), size * 0.06, size * 0.06)
    painter.drawLine(QPointF(size * 0.50, size * 0.68), QPointF(size * 0.50, size * 0.80))
    painter.drawLine(QPointF(size * 0.34, size * 0.82), QPointF(size * 0.66, size * 0.82))
    painter.drawLine(QPointF(size * 0.32, size * 0.43), QPointF(size * 0.64, size * 0.43))
    painter.drawLine(QPointF(size * 0.54, size * 0.33), QPointF(size * 0.65, size * 0.43))
    painter.drawLine(QPointF(size * 0.54, size * 0.53), QPointF(size * 0.65, size * 0.43))


def region_glyph(painter, color, size):
    # A dashed selection frame with solid corners: "drawing region".
    pen = _stroke_pen(color, size * 0.08)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    a, b = size * 0.16, size * 0.84
    c = size * 0.20
    painter.setPen(pen)
    for x, y, dx, dy in ((a, a, 1, 1), (b, a, -1, 1), (a, b, 1, -1), (b, b, -1, -1)):
        painter.drawLine(QPointF(x, y), QPointF(x + dx * c, y))
        painter.drawLine(QPointF(x, y), QPointF(x, y + dy * c))
    dashed = _stroke_pen(color, size * 0.06)
    dashed.setStyle(Qt.PenStyle.DotLine)
    painter.setPen(dashed)
    painter.drawRect(QRectF(size * 0.34, size * 0.34, size * 0.32, size * 0.32))


def rect_glyph(painter, color, size):
    painter.setPen(_stroke_pen(color, size * 0.09))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(size * 0.20, size * 0.26, size * 0.60, size * 0.48))


def ellipse_glyph(painter, color, size):
    painter.setPen(_stroke_pen(color, size * 0.09))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(size * 0.18, size * 0.26, size * 0.64, size * 0.48))


def line_glyph(painter, color, size):
    painter.setPen(_stroke_pen(color, size * 0.09))
    painter.drawLine(QPointF(size * 0.22, size * 0.78), QPointF(size * 0.78, size * 0.22))


def arrow_glyph(painter, color, size):
    painter.setPen(_stroke_pen(color, size * 0.09))
    tip = QPointF(size * 0.78, size * 0.22)
    painter.drawLine(QPointF(size * 0.22, size * 0.78), tip)
    painter.drawLine(tip, QPointF(size * 0.48, size * 0.24))
    painter.drawLine(tip, QPointF(size * 0.76, size * 0.52))


def text_glyph(painter, color, size):
    painter.setPen(_stroke_pen(color, size * 0.10))
    painter.drawLine(QPointF(size * 0.24, size * 0.24), QPointF(size * 0.76, size * 0.24))
    painter.drawLine(QPointF(size * 0.50, size * 0.24), QPointF(size * 0.50, size * 0.80))


def bold_glyph(painter, color, size):
    painter.setPen(_stroke_pen(color, size * 0.13))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(size * 0.34, size * 0.20)
    path.lineTo(size * 0.34, size * 0.80)
    path.lineTo(size * 0.58, size * 0.80)
    path.cubicTo(size * 0.76, size * 0.80, size * 0.76, size * 0.50, size * 0.56, size * 0.50)
    path.lineTo(size * 0.34, size * 0.50)
    path.moveTo(size * 0.56, size * 0.50)
    path.cubicTo(size * 0.72, size * 0.50, size * 0.72, size * 0.20, size * 0.54, size * 0.20)
    path.lineTo(size * 0.34, size * 0.20)
    painter.drawPath(path)


def _align_glyph(painter, color, size, align):
    painter.setPen(_stroke_pen(color, size * 0.08))
    for i, frac in enumerate((0.60, 0.40, 0.60, 0.32)):
        w = size * frac
        x = {"left": size * 0.2, "center": (size - w) / 2, "right": size * 0.8 - w}[align]
        y = size * (0.26 + i * 0.16)
        painter.drawLine(QPointF(x, y), QPointF(x + w, y))


def align_left_glyph(painter, color, size):
    _align_glyph(painter, color, size, "left")


def align_center_glyph(painter, color, size):
    _align_glyph(painter, color, size, "center")


def align_right_glyph(painter, color, size):
    _align_glyph(painter, color, size, "right")


def select_glyph(painter, color, size):
    # An outlined pointer: the Select/Move tool.
    painter.setPen(_stroke_pen(color, size * 0.08))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pointer = QPainterPath()
    pointer.moveTo(size * 0.30, size * 0.16)
    pointer.lineTo(size * 0.30, size * 0.78)
    pointer.lineTo(size * 0.45, size * 0.64)
    pointer.lineTo(size * 0.56, size * 0.86)
    pointer.lineTo(size * 0.65, size * 0.82)
    pointer.lineTo(size * 0.55, size * 0.60)
    pointer.lineTo(size * 0.74, size * 0.58)
    pointer.closeSubpath()
    painter.drawPath(pointer)


_GLYPHS = {
    "select": select_glyph,
    "text": text_glyph,
    "bold": bold_glyph,
    "align_left": align_left_glyph,
    "align_center": align_center_glyph,
    "align_right": align_right_glyph,
    "rect": rect_glyph,
    "ellipse": ellipse_glyph,
    "line": line_glyph,
    "arrow": arrow_glyph,
    "brush": brush_glyph,
    "eraser": eraser_glyph,
    "undo": undo_glyph,
    "redo": redo_glyph,
    "clear": clear_glyph,
    "pass_through": pass_through_glyph,
    "snapshot": snapshot_glyph,
    "exit": exit_glyph,
    "hide": hide_glyph,
    "more": more_glyph,
    "backdrop_off": backdrop_off_glyph,
    "eyedropper": eyedropper_glyph,
    "monitor": monitor_glyph,
    "region": region_glyph,
}


def build_icon(name, color="#eeeeef", size=SIZE):
    pixmap, painter = _canvas(size)
    _GLYPHS[name](painter, color, size)
    return _finish(pixmap, painter)


def build_color_swatch_icon(color, size=SIZE):
    pixmap, painter = _canvas(size)
    painter.setPen(QPen(QColor("#0c0c0d"), max(1.0, size * 0.05)))
    painter.setBrush(QBrush(QColor(color)))
    d = size * 0.62
    painter.drawEllipse(QRectF((size - d) / 2, (size - d) / 2, d, d))
    return _finish(pixmap, painter)
