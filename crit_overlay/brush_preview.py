"""Stroke previews for brush tiles, drawn by the brush engines themselves.

Every brush paints the same standardized stroke, so they compare like for
like: a light entry, a firm downstroke, a dip at a change of direction, a
second swell and a long taper, at a hand-like varying speed. What shows is
what the brush really paints (taper, edge, texture, density, and for MyPaint
brushes their speed and direction response). Cached per brush, color, size
and pixel density.
"""

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

from . import brushes
from . import mypaint_engine as mp
from .models import Stroke

_cache = {}


def _pressure(t):
    """Light entry, firm, a dip at the turn, a second swell, a long taper."""
    def bump(c, width):
        return math.exp(-((t - c) / width) ** 2)
    p = 0.95 * bump(0.28, 0.17) + 0.8 * bump(0.68, 0.16) + 0.25 * bump(0.48, 0.3)
    return max(0.04, min(1.0, p)) * min(1.0, t / 0.06 + 0.2) * min(1.0, (1 - t) / 0.18 + 0.05)


def _stroke(key, color, w, h, size):
    s = Stroke(QColor(color), size, 1.0)
    n = 160
    pad = size * 0.9 + 2
    amp = (h / 2 - pad)
    pts, dts = [], []
    prev = None
    for i in range(n + 1):
        t = i / n
        # One and a half waves, the second smaller: down, up, and a hook.
        x = pad + t * (w - 2 * pad)
        y = h / 2 + amp * 0.75 * math.sin(t * math.pi * 3) * (1 - 0.45 * t)
        p = QPointF(x, y)
        pts.append((p, _pressure(t)))
        # Time steps follow the speed a hand would have: slower at the turns.
        step = 0.0 if prev is None else math.hypot(x - prev.x(), y - prev.y())
        dts.append(0.0 if prev is None else 0.004 + step * 0.0012)
        prev = p
    if not mp.available():
        return None
    s.brush = brushes.spec_for(brushes.PRESETS.get(key) or brushes.ERASERS[key])
    s.points = pts
    s.dtimes = dts
    return s


def preview(key, color, w, h, dpr=1.0, size=None):
    """A QPixmap of w x h logical pixels showing `key`'s stroke."""
    size = size if size is not None else max(2.0, h * 0.32)
    ck = (key, QColor(color).rgba(), w, h, round(dpr, 2), round(size, 2))
    pm = _cache.get(ck)
    if pm is not None:
        return pm
    pm = QPixmap(round(w * dpr), round(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    s = _stroke(key, color, w, h, size)
    if s is not None:
        layer = brushes.freehand_layer(s, dpr, cache=False)
        if layer is not None:
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawPixmap(layer[1], layer[0])
            p.end()
    _cache[ck] = pm
    if len(_cache) > 200:
        _cache.pop(next(iter(_cache)))
    return pm


_dabs = {}


def dab(spec, color, diameter, dpr=1.0):
    """One libmypaint dab of `spec` at full pressure, `diameter` logical px
    wide: the brush-size preview."""
    if not mp.available():
        return None
    ck = (spec, QColor(color).rgba(), round(diameter, 1), round(dpr, 2))
    pm = _dabs.get(ck)
    if pm is not None:
        return pm
    s = Stroke(QColor(color), max(0.5, diameter), 1.0)
    s.brush = spec
    # libmypaint paints dabs along movement, so a short stroke (a fifth of
    # the diameter) stands in for a single dab.
    c = diameter * 2 + 16
    n = 6
    s.points = [(QPointF(c - diameter * 0.1 + diameter * 0.2 * i / n, c), 1.0) for i in range(n + 1)]
    s.dtimes = [0.0] + [0.01] * n
    layer = brushes.freehand_layer(s, dpr, cache=False)
    if layer is None:
        return None
    pm = layer[0]
    _dabs[ck] = pm
    if len(_dabs) > 64:
        _dabs.pop(next(iter(_dabs)))
    return pm
