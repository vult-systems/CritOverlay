"""Stroke previews for brush tiles, drawn by the brush engines themselves.

Each preview is the same S-curve with a pressure swell (thin, full, thin),
so brushes compare like for like: taper, edge, texture and density are what
the brush really paints. Cached per brush, color, size and pixel density.
"""

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

from . import brush, dab
from . import mypaint_engine as mp
from .models import Stroke

_cache = {}


def _stroke(key, color, w, h, size):
    s = Stroke(QColor(color), size, 1.0)
    n = 90
    pad = size * 0.9 + 2
    pts = []
    for i in range(n + 1):
        t = i / n
        x = pad + t * (w - 2 * pad)
        y = h / 2 + (h / 2 - pad) * 0.55 * math.sin(t * math.pi * 2)
        pressure = 0.15 + 0.85 * math.sin(t * math.pi) ** 0.7
        pts.append((QPointF(x, y), pressure))
    if key == dab.CLASSIC:
        s.points = [(p, brush.effective_pressure(pr, 0.0)) for p, pr in pts]
    elif key.startswith(mp.PREFIX):
        if not mp.available():
            return None
        s.brush = mp.spec(key[len(mp.PREFIX):])
        s.points = pts
        s.dtimes = [0.0] + [0.006] * n
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
