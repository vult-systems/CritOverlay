"""Shapes, text and the layer helpers they share.

Freehand painting is libmypaint (mypaint_engine.py, brushes.py). This module
draws the editable annotations: shapes and text, each flattened opaque on its
own small layer so a translucent annotation never darkens where it overlaps
itself; callers apply stroke.opacity once when compositing.

Layers are allocated at the monitor's device pixel ratio (new_layer), so on
a 150% or 200% display they render at full physical resolution. All
coordinates stay logical; QPainter applies the ratio.
"""

import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap

MIN_WIDTH_FACTOR = 0.35
PRESSURE_WIDTH_FACTOR = 1.15

# Shape outlines use the width a constant-pressure line had in the first
# engine, so shape sizes stay as they were.
SPEED_THIN_PX_PER_S = 2600.0
SPEED_WIDTH_FLOOR = 0.55


def effective_pressure(pressure, speed_px_s):
    """Blend raw pressure with movement speed into one width-driving value."""
    pressure = max(0.1, min(1.0, pressure))
    speed_t = max(0.0, min(1.0, speed_px_s / SPEED_THIN_PX_PER_S))
    return pressure * (1.0 - speed_t * (1.0 - SPEED_WIDTH_FLOOR))


def width_for(base_size, blended_pressure):
    return base_size * (MIN_WIDTH_FACTOR + blended_pressure * PRESSURE_WIDTH_FACTOR)


def new_layer(logical_size, dpr=1.0):
    """Transparent pixmap covering logical_size at device pixel ratio dpr.

    Filled transparent explicitly because QPixmap(size) can come back in the
    screen's opaque format, which turns CompositionMode_Clear into a no-op.
    """
    dpr = max(1.0, float(dpr))
    w = max(1, math.ceil(logical_size.width() * dpr))
    h = max(1, math.ceil(logical_size.height() * dpr))
    pixmap = QPixmap(w, h)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def logical_rect(origin, pixmap):
    """The logical-coordinate rect a layer covers when drawn at origin."""
    dpr = pixmap.devicePixelRatio() or 1.0
    return QRect(origin, QSize(math.ceil(pixmap.width() / dpr), math.ceil(pixmap.height() / dpr)))


# -- shapes ------------------------------------------------------------------
#
# A shape is a Stroke with .shape set and two points, start and end. It is
# rendered through the same opaque-layer path as a freehand stroke, at the
# constant width a mouse stroke gets, so it composites, erases, undoes and
# exports like any other stroke.

SHAPE_PRESSURE = 0.65
ARROW_HEAD_ANGLE = math.radians(28)


def shape_width(size):
    return width_for(size, effective_pressure(SHAPE_PRESSURE, 0.0))


def constrain_end(shape, start, end):
    """Shift-constraint: squares and circles for rect/ellipse, 45-degree
    steps for line/arrow."""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    if shape in ("rect", "ellipse"):
        side = max(abs(dx), abs(dy))
        return QPointF(start.x() + math.copysign(side, dx or 1.0), start.y() + math.copysign(side, dy or 1.0))
    length = math.hypot(dx, dy)
    step = math.pi / 4
    angle = round(math.atan2(dy, dx) / step) * step
    return QPointF(start.x() + length * math.cos(angle), start.y() + length * math.sin(angle))


def shape_path(stroke):
    """The shape's outline as a QPainterPath, in stroke coordinates."""
    (a, _), (b, _) = stroke.points[0], stroke.points[-1]
    path = QPainterPath()
    if stroke.shape == "rect":
        path.addRect(QRectF(a, b).normalized())
    elif stroke.shape == "ellipse":
        path.addEllipse(QRectF(a, b).normalized())
    else:
        path.moveTo(a)
        path.lineTo(b)
        if stroke.shape == "arrow":
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length = math.hypot(dx, dy)
            if length > 0:
                w = shape_width(stroke.size)
                head = min(max(w * 3.0, 10.0), length * 0.5)
                back = math.atan2(-dy, -dx)
                for side in (-1, 1):
                    ang = back + side * ARROW_HEAD_ANGLE
                    path.moveTo(b)
                    path.lineTo(b.x() + head * math.cos(ang), b.y() + head * math.sin(ang))
    return path


def shape_bounds(stroke, margin=3.0):
    if stroke.shape == "text":
        return text_rect(stroke).adjusted(-margin, -margin, margin, margin).toAlignedRect()
    pad = shape_width(stroke.size) / 2 + margin
    return shape_path(stroke).boundingRect().adjusted(-pad, -pad, pad, pad).toAlignedRect()


def shape_is_degenerate(stroke, min_extent):
    if stroke.shape == "text":
        return not stroke.text.strip()
    (a, _), (b, _) = stroke.points[0], stroke.points[-1]
    return max(abs(b.x() - a.x()), abs(b.y() - a.y())) < min_extent


def render_shape_layer(stroke, dpr=1.0):
    """Like render_stroke_layer, for a shape. Returns (pixmap, origin) or
    None."""
    if not stroke.points or (stroke.shape != "text" and len(stroke.points) < 2):
        return None
    bounds = shape_bounds(stroke)
    if bounds.width() <= 0 or bounds.height() <= 0:
        return None
    layer = new_layer(bounds.size(), dpr)
    painter = QPainter(layer)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(-bounds.topLeft())
        color = QColor(stroke.color)
        color.setAlpha(255)
        if stroke.shape == "text":
            paint_text(painter, stroke, color)
        else:
            painter.setPen(QPen(color, shape_width(stroke.size), Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(shape_path(stroke))
        if stroke.erasures:
            from . import brushes
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            for eraser, transform in stroke.erasures:
                found = brushes.freehand_layer(eraser, dpr)
                if found is None:
                    continue
                painter.save()
                painter.setTransform(transform, True)
                painter.setOpacity(max(0.01, min(1.0, eraser.opacity)))
                painter.drawPixmap(found[1], found[0])
                painter.restore()
    finally:
        painter.end()
    return layer, bounds.topLeft()


# -- text ----------------------------------------------------------------------
#
# Text is a Stroke with shape "text": an anchor point, the text, a pixel size
# (stroke.size, in stroke coordinates) and a TextStyle. The anchor is the
# block's top edge at its left, center or right, by alignment, so text grows
# away from where it was clicked.


def text_font(stroke):
    font = QFont(stroke.font.family)
    font.setPixelSize(max(1, round(stroke.size)))
    font.setBold(stroke.font.bold)
    return font


def _text_lines(stroke):
    font = text_font(stroke)
    fm = QFontMetricsF(font)
    lines = stroke.text.split("\n")
    widths = [fm.horizontalAdvance(line) for line in lines]
    return font, fm, lines, widths


def text_rect(stroke):
    """The text block's rect, in stroke coordinates. Never narrower than a
    caret, so an empty box still has a place to show."""
    _font, fm, lines, widths = _text_lines(stroke)
    width = max(max(widths), fm.averageCharWidth() * 0.5)
    height = fm.lineSpacing() * len(lines)
    anchor = stroke.points[0][0]
    left = {"left": anchor.x(), "center": anchor.x() - width / 2, "right": anchor.x() - width}[stroke.font.align]
    return QRectF(left, anchor.y(), width, height)


def _line_x(rect, width, align):
    if align == "center":
        return rect.left() + (rect.width() - width) / 2
    if align == "right":
        return rect.right() - width
    return rect.left()


def text_layout(stroke):
    """(font metrics, lines, line widths, block rect) for caret and hit
    testing, from the same layout paint_text() draws."""
    _font, fm, lines, widths = _text_lines(stroke)
    return fm, lines, widths, text_rect(stroke)


def paint_text(painter, stroke, color):
    font, fm, lines, widths = _text_lines(stroke)
    rect = text_rect(stroke)
    painter.setFont(font)
    painter.setPen(color)
    for i, (line, width) in enumerate(zip(lines, widths)):
        painter.drawText(QPointF(_line_x(rect, width, stroke.font.align), rect.top() + fm.ascent() + i * fm.lineSpacing()), line)


def text_caret(stroke):
    """(top, bottom) QPointFs of a caret after the last character."""
    _font, fm, lines, widths = _text_lines(stroke)
    rect = text_rect(stroke)
    i = len(lines) - 1
    x = _line_x(rect, widths[-1], stroke.font.align) + widths[-1]
    top = rect.top() + i * fm.lineSpacing()
    return QPointF(x, top), QPointF(x, top + fm.lineSpacing())


line_x = _line_x
