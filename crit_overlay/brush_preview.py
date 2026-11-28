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

from . import brush, dab
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
    if key == dab.CLASSIC:
        s.points = [(p, brush.effective_pressure(pr, 0.0)) for p, pr in pts]
    elif key.startswith(mp.PREFIX):
        if not mp.available():
            return None
        s.brush = mp.spec(key[len(mp.PREFIX):])
        s.points = pts
        s.dtimes = dts
    else:
        s.brush = dab.make_spec(key)
        s.points = pts
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
        layer = dab.freehand_layer(s, dpr, cache=False)
        if layer is not None:
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawPixmap(layer[1], layer[0])
            p.end()
    _cache[ck] = pm
    if len(_cache) > 200:
        _cache.pop(next(iter(_cache)))
    return pm
