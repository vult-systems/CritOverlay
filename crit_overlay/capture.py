"""Centered capture: a still image of a selected area, shown centered on the
monitor as a drawing workspace.

Document coordinates are the captured image's logical pixels, with (0, 0)
at its top-left. The margin extends into negative coordinates, so changing
its thickness never moves strokes relative to the image. A view transform
(offset plus a fit scale) maps document coordinates to the window.

The image keeps the pixel density it was grabbed at. Exports render the
document at that density whatever the view scale, so fitting a large
capture onto the monitor never lowers the saved resolution.

Kept free of Overlay state so it can be unit-tested directly.
"""

import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform

FIT_PADDING = 24  # logical px kept clear around a fitted canvas
# Wait after clearing our own paint before grabbing, so DWM has composed a
# frame without it.
GRAB_DELAY_MS = 80
# Dims the desktop around a capture so the workspace reads as its own space.
SURROUND = QColor(14, 14, 16, 200)


def _edge(value):
    """Round a device coordinate to a pixel edge. Every edge in the capture
    goes through this one rule, so neighbouring rects always share their
    edges exactly (Python's round() rounds halves to even, which doesn't)."""
    return math.floor(value + 0.5)


def device_rect(rect, dpr):
    """Device-pixel rect for a logical rect, rounding each edge (not the
    size), so adjacent rects meet with no gap or overlap."""
    x0 = _edge(rect.x() * dpr)
    y0 = _edge(rect.y() * dpr)
    x1 = _edge((rect.x() + rect.width()) * dpr)
    y1 = _edge((rect.y() + rect.height()) * dpr)
    return QRect(x0, y0, x1 - x0, y1 - y0)


def crop_grab(full, rect, dpr):
    """The selection, cut from a full-monitor grab at its device-pixel
    edges. QScreen.grabWindow with a sub-rect rounds the size up instead
    (401 logical px at 150% comes back 602 px wide, not 601), which skewed
    the image's pixel ratio and left fractional, blended edges."""
    dev = device_rect(rect, dpr).intersected(QRect(0, 0, full.width(), full.height()))
    image = full.copy(dev)
    image.setDevicePixelRatio(dpr)
    return image


class Capture:
    __slots__ = ("image", "dpr", "offset", "scale", "source_rect")

    def __init__(self, image, source_rect):
        self.image = image
        self.dpr = image.devicePixelRatio() or 1.0
        self.offset = QPointF(0, 0)
        self.scale = 1.0
        # Where it was selected, in window coordinates (for the log only).
        self.source_rect = source_rect

    def size(self):
        """Logical size of the image."""
        return QRectF(0, 0, self.image.width() / self.dpr, self.image.height() / self.dpr).size()

    def image_rect(self):
        s = self.size()
        return QRectF(0, 0, s.width(), s.height())

    def margin_px(self, margin):
        """Margin thickness in whole image pixels."""
        return _edge(max(0, margin) * self.dpr)

    def outer_rect(self, margin):
        """Image plus margin in document coordinates. The margin is snapped
        to whole image pixels, so its edges sit on the image's pixel grid."""
        m = self.margin_px(margin) / self.dpr
        return self.image_rect().adjusted(-m, -m, m, m)

    def view_device_rects(self, margin, window_dpr):
        """(outer, image) as integer device-pixel rects in the window, from
        the same edge rounding, so the margin and image share edges exactly
        at any fit scale or monitor scale."""
        def to_device(r):
            tl = self.to_view(r.topLeft())
            br = self.to_view(QPointF(r.x() + r.width(), r.y() + r.height()))
            return device_rect(QRectF(tl, br), window_dpr)
        return to_device(self.outer_rect(margin)), to_device(self.image_rect())

    def transform(self):
        t = QTransform()
        t.translate(self.offset.x(), self.offset.y())
        t.scale(self.scale, self.scale)
        return t

    def to_doc(self, pos):
        return QPointF((pos.x() - self.offset.x()) / self.scale, (pos.y() - self.offset.y()) / self.scale)

    def to_view(self, pos):
        return QPointF(pos.x() * self.scale + self.offset.x(), pos.y() * self.scale + self.offset.y())


def snap(value, dpr):
    """Round a logical coordinate to the device pixel grid, so an unscaled
    image lands 1:1 on screen pixels instead of being resampled."""
    return round(value * dpr) / dpr


def fit(outer, bounds, dpr, padding=FIT_PADDING):
    """(scale, offset) that centers `outer` (document rect) in `bounds`
    (window rect), shrinking it only if it doesn't fit inside the padding."""
    avail_w = max(1.0, bounds.width() - 2 * padding)
    avail_h = max(1.0, bounds.height() - 2 * padding)
    scale = min(1.0, avail_w / max(1.0, outer.width()), avail_h / max(1.0, outer.height()))
    cx = bounds.x() + bounds.width() / 2.0
    cy = bounds.y() + bounds.height() / 2.0
    x = cx - (outer.x() + outer.width() / 2.0) * scale
    y = cy - (outer.y() + outer.height() / 2.0) * scale
    return scale, QPointF(snap(x, dpr), snap(y, dpr))


def canvas_size(outer):
    """Integer logical size for a stroke layer covering `outer`."""
    return QSize(math.ceil(outer.width()), math.ceil(outer.height()))


def compose_export(capture, canvas, canvas_origin, margin, margin_fill, backdrop_fill):
    """The capture as saved by F9: margin (opaque), image, backdrop wash and
    strokes, at the image's own pixel density. No desktop content.

    canvas: stroke layer whose top-left is at `canvas_origin` (document
    coordinates). margin_fill: QColor or None for no margin. backdrop_fill:
    QColor or None.
    """
    dpr = capture.dpr
    m_px = capture.margin_px(margin) if margin_fill is not None else 0
    img_w, img_h = capture.image.width(), capture.image.height()
    image = QImage(img_w + 2 * m_px, img_h + 2 * m_px, QImage.Format.Format_ARGB32_Premultiplied)
    fill = QColor(margin_fill) if margin_fill is not None else QColor(0, 0, 0)
    fill.setAlpha(255)
    image.fill(fill)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # Device pixels throughout: the image goes in 1:1.
        painter.drawPixmap(QRectF(m_px, m_px, img_w, img_h), capture.image, QRectF(0, 0, img_w, img_h))
        if backdrop_fill is not None:
            painter.fillRect(QRectF(m_px, m_px, img_w, img_h), backdrop_fill)
        if canvas is not None and not canvas.isNull():
            cdpr = canvas.devicePixelRatio() or 1.0
            x = m_px + canvas_origin.x() * dpr
            y = m_px + canvas_origin.y() * dpr
            k = dpr / cdpr
            painter.drawPixmap(QRectF(x, y, canvas.width() * k, canvas.height() * k), canvas,
                               QRectF(0, 0, canvas.width(), canvas.height()))
    finally:
        painter.end()
    out = QPixmap.fromImage(image)
    out.setDevicePixelRatio(dpr)
    return out
