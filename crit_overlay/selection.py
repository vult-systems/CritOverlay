"""Geometry for the Select tool: hit testing, handles, move and resize.

Works on shape and text annotations (Strokes with .shape set). Freehand
strokes aren't selectable. Everything is in stroke coordinates and returns
new Strokes; committed strokes are never changed in place (see History).
"""

import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QTransform
from PySide6.QtGui import QPainterPathStroker

from . import brush
from . import dab


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


def absorb(strokes, stroke):
    """A copy of committed `stroke` that also carries the eraser strokes
    drawn over it later, so its erased parts travel with it when it is moved,
    resized or duplicated. The erasers stay in the history as they were; the
    canvas rebuild re-reveals an absorbing annotation inside them (see
    Overlay._restore_under_eraser), so nothing is erased twice."""
    strokes = list(strokes)
    dup = stroke.copy()
    idx = next((i for i, s in enumerate(strokes) if s is stroke), None)
    if idx is None:
        return dup
    have = {id(e) for e, _t in dup.erasures}
    pad = brush.shape_width(stroke.size) / 2 + 2
    area = bounds(stroke).adjusted(-pad, -pad, pad, pad)
    added = []
    for e in strokes[idx + 1:]:
        if not e.eraser or id(e) in have:
            continue
        if e.points and dab.freehand_bounds(e).intersects(area):
            added.append((e, QTransform()))
    dup.erasures = dup.erasures + tuple(added)
    return dup


def _transform_erasures(dup, m):
    if dup.erasures:
        dup.erasures = tuple((e, t * m) for e, t in dup.erasures)
    return dup


def translated(stroke, dx, dy):
    dup = stroke.copy()
    dup.points = [(QPointF(p.x() + dx, p.y() + dy), pr) for p, pr in dup.points]
    return _transform_erasures(dup, QTransform.fromTranslate(dx, dy))


MIN_TEXT_SIZE = 2.0


def _similarity(a0, a1, b0, b1):
    """The rotation + uniform scale + translation taking a0->b0, a1->b1."""
    ax, ay = a1.x() - a0.x(), a1.y() - a0.y()
    bx, by = b1.x() - b0.x(), b1.y() - b0.y()
    d = ax * ax + ay * ay
    if d < 1e-12:
        return QTransform.fromTranslate(b0.x() - a0.x(), b0.y() - a0.y())
    # Complex division (b / a).
    re, im = (bx * ax + by * ay) / d, (by * ax - bx * ay) / d
    dx = b0.x() - (re * a0.x() - im * a0.y())
    dy = b0.y() - (im * a0.x() + re * a0.y())
    return QTransform(re, im, -im, re, dx, dy)


def resized(orig, handle, pos, constrain=False):
    """A copy of orig with `handle` dragged to pos. With constrain (Shift),
    rectangles and ellipses keep their proportions and line or arrow ends
    snap to 45-degree steps. Text always scales proportionally."""
    dup = orig.copy()
    if orig.shape in ("line", "arrow"):
        last = len(dup.points) - 1
        i, fixed = (0, last) if handle == "p0" else (last, 0)
        anchor = dup.points[fixed][0]
        if constrain:
            pos = brush.constrain_end("line", anchor, pos)
        dup.points[i] = (QPointF(pos), dup.points[i][1])
        a = (orig.points[0][0], orig.points[-1][0])
        b = (dup.points[0][0], dup.points[-1][0])
        return _transform_erasures(dup, _similarity(a[0], a[1], b[0], b[1]))
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
        s = dup.size / max(orig.size, 1e-6)
        m = QTransform(s, 0, 0, s, left - r.left() * s, top - r.top() * s)
        return _transform_erasures(dup, m)
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
    w, h = r.width(), r.height()
    if constrain and w > 1e-6 and h > 1e-6:
        horiz, vert = ("l" in handle or "r" in handle), ("t" in handle or "b" in handle)
        if horiz and vert:
            fx = r.right() if "l" in handle else r.left()
            fy = r.bottom() if "t" in handle else r.top()
            mx = left if "l" in handle else right
            my = top if "t" in handle else bottom
            s = max(abs(mx - fx) / w, abs(my - fy) / h)
            mx = fx + math.copysign(s * w, (mx - fx) or (-1 if "l" in handle else 1))
            my = fy + math.copysign(s * h, (my - fy) or (-1 if "t" in handle else 1))
            left, right = (mx, fx) if "l" in handle else (fx, mx)
            top, bottom = (my, fy) if "t" in handle else (fy, my)
        elif horiz:
            nh = h * abs(right - left) / w
            cy = r.center().y()
            top, bottom = cy - nh / 2, cy + nh / 2
        else:
            nw = w * abs(bottom - top) / h
            cx = r.center().x()
            left, right = cx - nw / 2, cx + nw / 2
    dup.points = [(QPointF(left, top), dup.points[0][1]), (QPointF(right, bottom), dup.points[-1][1])]
    sx = (right - left) / w if w > 1e-6 else 1.0
    sy = (bottom - top) / h if h > 1e-6 else 1.0
    m = QTransform(sx, 0, 0, sy, left - r.left() * sx, top - r.top() * sy)
    return _transform_erasures(dup, m)
