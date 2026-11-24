"""The brush engine: centerline smoothing, stroke dynamics, compositing.

Translucent strokes have to be flattened before they are blended. Painting a
stroke as independent alpha-blended segments makes overlapping segments
(corners, slow retraced curves) blend against earlier segments of the same
stroke, so joints build up opacity and darken. render_stroke_layer() paints
the whole stroke opaque on a small offscreen layer instead; callers apply
stroke.opacity once via QPainter.setOpacity() when compositing it.

Eraser strokes skip that step. CompositionMode_Clear sets destination alpha
to 0, which is idempotent, so overlaps never build up.

smoothed_path() resamples the raw points through a Catmull-Rom spline, since
straight lines between sampled points facet on fast curves.

Layers are allocated at the monitor's device pixel ratio (new_layer), so on
a 150% or 200% display strokes render at full physical resolution instead of
being drawn at 100% and upscaled. All coordinates stay logical; QPainter
applies the ratio.
"""

import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

CATMULL_ROM_SAMPLES = 6

MIN_WIDTH_FACTOR = 0.35
PRESSURE_WIDTH_FACTOR = 1.15

# Stroke speed feeds into the effective "pressure" that drives width. Mice
# report a constant fake pressure, so without this they draw a dead even
# line, and fast pen strokes taper the way real ink does.
SPEED_THIN_PX_PER_S = 2600.0
SPEED_WIDTH_FLOOR = 0.55


def effective_pressure(pressure, speed_px_s):
    """Blend raw pressure with movement speed into one width-driving value."""
    pressure = max(0.1, min(1.0, pressure))
    speed_t = max(0.0, min(1.0, speed_px_s / SPEED_THIN_PX_PER_S))
    return pressure * (1.0 - speed_t * (1.0 - SPEED_WIDTH_FLOOR))


def width_for(base_size, blended_pressure):
    return base_size * (MIN_WIDTH_FACTOR + blended_pressure * PRESSURE_WIDTH_FACTOR)


def _catmull_rom_point(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    x = 0.5 * (
        (2 * p1.x())
        + (-p0.x() + p2.x()) * t
        + (2 * p0.x() - 5 * p1.x() + 4 * p2.x() - p3.x()) * t2
        + (-p0.x() + 3 * p1.x() - 3 * p2.x() + p3.x()) * t3
    )
    y = 0.5 * (
        (2 * p1.y())
        + (-p0.y() + p2.y()) * t
        + (2 * p0.y() - 5 * p1.y() + 4 * p2.y() - p3.y()) * t2
        + (-p0.y() + 3 * p1.y() - 3 * p2.y() + p3.y()) * t3
    )
    return QPointF(x, y)


def smoothed_path(points, samples=CATMULL_ROM_SAMPLES, extra_before=None):
    """points: [(QPointF, pressure), ...]. Returns a denser list of the same
    shape, resampled along a Catmull-Rom spline.

    extra_before is the point preceding points[0] in the full stroke, for
    callers passing a mid-stroke window rather than a whole stroke. Without
    it the first segment duplicates points[0] as its own predecessor, which
    is right at a real stroke start and wrong at a window boundary.
    """
    n = len(points)
    if n < 3:
        return list(points)

    out = []
    for i in range(n - 1):
        if i > 0:
            p0 = points[i - 1][0]
        elif extra_before is not None:
            p0 = extra_before
        else:
            p0 = points[i][0]
        p1, pr1 = points[i]
        p2, pr2 = points[i + 1]
        p3 = points[i + 2][0] if i + 2 < n else p2
        steps = samples if i < n - 2 else samples + 1
        for s in range(steps):
            t = s / samples
            out.append((_catmull_rom_point(p0, p1, p2, p3, t), pr1 + (pr2 - pr1) * t))
    return out


def segments_for_points(points, size, extra_before=None):
    """Returns [(QPointF, width), ...] ready to paint. One entry is a tap
    (draw a dot), two or more is a polyline of round-capped segments.

    Takes a points list rather than a Stroke so callers can render a window
    of a stroke. extra_before is forwarded to smoothed_path().
    """
    if not points:
        return []
    if len(points) == 1:
        p, pressure = points[0]
        return [(p, width_for(size, pressure))]
    return [(p, width_for(size, pr)) for p, pr in smoothed_path(points, extra_before=extra_before)]


def stroke_segments(stroke):
    return segments_for_points(stroke.points, stroke.size)


def paint_segments(painter, segments, color):
    """Paints pre-computed (point, width) segments in a flat color. Callers
    control translucency via color.alpha() or painter.setOpacity()."""
    if not segments:
        return
    if len(segments) == 1:
        p, w = segments[0]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(p, w / 2, w / 2)
        return
    for i in range(1, len(segments)):
        p0, w0 = segments[i - 1]
        p1, w1 = segments[i]
        width = (w0 + w1) / 2
        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(p0, p1)


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


def _segment_bounds(segments, margin=3.0):
    xs = [p.x() for p, _ in segments]
    ys = [p.y() for p, _ in segments]
    pad = max((w for _, w in segments), default=0.0) / 2 + margin
    rect = QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + pad * 2, max(ys) - min(ys) + pad * 2)
    return rect.toAlignedRect()


def render_stroke_layer(stroke, segments, dpr=1.0):
    """Flattens a non-eraser stroke onto an opaque offscreen pixmap sized to
    its bounding box. Returns (pixmap, origin) to composite at, or None if
    there is nothing to draw. The caller applies stroke.opacity once when
    compositing. See the module docstring."""
    if not segments:
        return None
    bounds = _segment_bounds(segments)
    if bounds.width() <= 0 or bounds.height() <= 0:
        return None

    layer = new_layer(bounds.size(), dpr)
    painter = QPainter(layer)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(-bounds.topLeft())
        color = QColor(stroke.color)
        color.setAlpha(255)
        paint_segments(painter, segments, color)
    finally:
        painter.end()
    return layer, bounds.topLeft()
