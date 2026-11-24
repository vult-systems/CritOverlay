"""Drawing region and annotation margin geometry.

The region is a rectangle in the overlay's logical coordinates, or None for
the full monitor. The overlay covers exactly one monitor, so these are also
that monitor's logical coordinates, which is what makes the mapping exact at
any display scale: Qt converts logical to physical pixels with the one
monitor's ratio for painting, input and screen grabs alike.

The margin is a band of fixed logical thickness around the region, clipped
to the monitor. The drawable area is the region plus its margin.

Kept free of Overlay state so it can be unit-tested directly.
"""

from PySide6.QtCore import QPoint, QRect, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from . import constants as C

MARGIN_COLORS = ("white", "black")


def selection_rect(a, b, bounds):
    """Integer rectangle spanned by two points (any order), clipped to
    bounds."""
    left, right = sorted((a.x(), b.x()))
    top, bottom = sorted((a.y(), b.y()))
    rect = QRect(QPoint(int(round(left)), int(round(top))), QPoint(int(round(right)) - 1, int(round(bottom)) - 1))
    return rect.intersected(bounds)


def is_usable(rect, min_size=C.MIN_REGION_SIZE):
    return rect is not None and rect.width() >= min_size and rect.height() >= min_size


def clamp_region(region, bounds, min_size=C.MIN_REGION_SIZE):
    """Fit a region onto a (possibly smaller) monitor after a switch or a
    resolution change. Keeps its size where it fits, moving it inside;
    shrinks it otherwise. None if nothing usable is left."""
    if region is None or bounds.isEmpty():
        return None
    w = min(region.width(), bounds.width())
    h = min(region.height(), bounds.height())
    x = max(bounds.left(), min(region.x(), bounds.right() + 1 - w))
    y = max(bounds.top(), min(region.y(), bounds.bottom() + 1 - h))
    rect = QRect(x, y, w, h)
    return rect if is_usable(rect, min_size) else None


def outer_rect(region, margin, bounds):
    """Region plus margin band, clipped to the monitor."""
    if region is None:
        return QRect(bounds)
    m = max(0, int(margin))
    return region.adjusted(-m, -m, m, m).intersected(bounds)


def margin_bands(region, outer):
    """The margin as up to four non-overlapping rectangles (top, bottom,
    left, right), so filling it never touches the region itself."""
    bands = [
        QRect(outer.left(), outer.top(), outer.width(), region.top() - outer.top()),
        QRect(outer.left(), region.bottom() + 1, outer.width(), outer.bottom() - region.bottom()),
        QRect(outer.left(), region.top(), region.left() - outer.left(), region.height()),
        QRect(region.right() + 1, region.top(), outer.right() - region.right(), region.height()),
    ]
    return [b for b in bands if b.width() > 0 and b.height() > 0]


def margin_color(name, opacity=1.0):
    base = QColor(0, 0, 0) if name == "black" else QColor(255, 255, 255)
    base.setAlpha(int(round(max(0.0, min(1.0, opacity)) * 255)))
    return base


def compose_capture(region_grab, canvas, region, outer, margin_fill):
    """Screenshot of a region and its margin.

    region_grab: a screen grab of exactly `region` (desktop, backdrop and
    strokes as shown). The margin band is rebuilt from `margin_fill` (made
    opaque) and the stroke canvas, so no desktop content from around the
    region ends up in the image. With no margin (outer == region) the grab
    is returned as-is. Output is at the grab's pixel density.
    """
    if outer == region or margin_fill is None:
        return region_grab
    dpr = region_grab.width() / max(1, region.width())
    image = QImage(round(outer.width() * dpr), round(outer.height() * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    fill = QColor(margin_fill)
    fill.setAlpha(255)
    image.fill(fill)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(-outer.topLeft())
        painter.setClipRect(outer)
        painter.drawPixmap(QRectF(0, 0, canvas.width() / canvas.devicePixelRatio(),
                                  canvas.height() / canvas.devicePixelRatio()), canvas, QRectF(canvas.rect()))
        painter.drawPixmap(QRectF(region), region_grab, QRectF(region_grab.rect()))
    finally:
        painter.end()
    return QPixmap.fromImage(image)
