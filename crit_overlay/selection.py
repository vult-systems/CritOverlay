"""Geometry for the Select tool: hit testing, handles, move and resize.

Works on shape and text annotations (Strokes with .shape set). Freehand
strokes aren't selectable. Everything is in stroke coordinates and returns
new Strokes; committed strokes are never changed in place (see History).
"""

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPathStroker

from . import brush


def selectable(stroke):
    return stroke.shape is not None and not stroke.eraser


def bounds(stroke):
    if stroke.shape == "text":
        return brush.text_rect(stroke)
    return brush.shape_path(stroke).boundingRect()


def hit(stroke, pos, tolerance):
    if stroke.shape == "text":
        return brush.text_rect(stroke).adjusted(-tolerance, -tolerance, tolerance, tolerance).contains(pos)
    stroker = QPainterPathStroker()
    stroker.setWidth(brush.shape_width(stroke.size) + 2 * tolerance)
    return stroker.createStroke(brush.shape_path(stroke)).contains(pos)


def topmost_hit(strokes, pos, tolerance):
    for stroke in reversed(list(strokes)):
        if selectable(stroke) and hit(stroke, pos, tolerance):
            return stroke
    return None


def handles(stroke):
    """{name: point}. Lines and arrows have their two ends; rectangles and
    ellipses corners and edges; text its corners (they scale the font)."""
    if stroke.shape in ("line", "arrow"):
        return {"p0": stroke.points[0][0], "p1": stroke.points[-1][0]}
    r = bounds(stroke) if stroke.shape == "text" else QRectF(stroke.points[0][0], stroke.points[-1][0]).normalized()
    out = {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight()}
    if stroke.shape != "text":
        c = r.center()
        out.update({"t": QPointF(c.x(), r.top()), "b": QPointF(c.x(), r.bottom()),
                    "l": QPointF(r.left(), c.y()), "r": QPointF(r.right(), c.y())})
    return out


def handle_at(stroke, pos, tolerance):
    best, best_d = None, tolerance
    for name, p in handles(stroke).items():
        d = max(abs(p.x() - pos.x()), abs(p.y() - pos.y()))
        if d <= best_d:
            best, best_d = name, d
    return best


def translated(stroke, dx, dy):
    dup = stroke.copy()
    dup.points = [(QPointF(p.x() + dx, p.y() + dy), pr) for p, pr in dup.points]
    return dup


MIN_TEXT_SIZE = 2.0


def resized(orig, handle, pos):
    """A copy of orig with `handle` dragged to pos."""
    dup = orig.copy()
    if orig.shape in ("line", "arrow"):
        i = 0 if handle == "p0" else len(dup.points) - 1
        dup.points[i] = (QPointF(pos), dup.points[i][1])
        return dup
    if orig.shape == "text":
        r = brush.text_rect(orig)
        fixed = QPointF(r.right() if "l" in handle else r.left(), r.bottom() if "t" in handle else r.top())
        scale = abs(pos.y() - fixed.y()) / max(r.height(), 1e-6)
        dup.size = max(MIN_TEXT_SIZE, orig.size * scale)
        nr = brush.text_rect(dup)
        left = fixed.x() - nr.width() if "l" in handle else fixed.x()
        top = fixed.y() - nr.height() if "t" in handle else fixed.y()
        x = {"left": left, "center": left + nr.width() / 2, "right": left + nr.width()}[dup.font.align]
        dup.points = [(QPointF(x, top), dup.points[0][1])]
        return dup
    r = QRectF(orig.points[0][0], orig.points[-1][0]).normalized()
    left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
    if "l" in handle:
        left = pos.x()
    if "r" in handle:
        right = pos.x()
    if "t" in handle:
        top = pos.y()
    if "b" in handle:
        bottom = pos.y()
    dup.points = [(QPointF(left, top), dup.points[0][1]), (QPointF(right, bottom), dup.points[-1][1])]
    return dup
