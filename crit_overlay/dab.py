"""Raster dab brush engine (exp/shape-tools prototype).

A freehand stroke whose .brush is a BrushSpec is drawn as stamps ("dabs")
along its centerline instead of as round-capped line segments (the classic
engine in brush.py, kept as a comparison option).

Pipeline:
  points (smoothed position, pressure)
    -> adaptive Catmull-Rom samples (about one per stroke unit, so fast
       strokes with far-apart input points stay round)
    -> dabs placed by arc length every `spacing` x diameter, so slow and
       fast strokes come out identical
    -> each dab: diameter and flow from pressure (and a taper for input
       without pressure), stamped from a cached radial image.

Flow is normalised for spacing: a dab's alpha is 1 - (1 - flow)^spacing, so
about one diameter of travel builds up to `flow` whatever the spacing. The
stroke is flattened on its own layer and stroke.opacity is applied once when
compositing, like the classic engine, so the opacity slider caps a stroke
and flow shapes it from within.

Dabs are deterministic (seeded by the stroke) and computed one spline
segment at a time. DabRenderer carries the walk state, so the live preview
renders each segment once, in order, and gets the same dabs as a full
re-render.
"""

import math
import random
import weakref
from collections import OrderedDict, namedtuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient

from . import brush


class Preset:
    __slots__ = ("key", "label", "hardness", "spacing", "min_size", "size_gamma", "flow",
                 "opacity_pressure", "grain", "jitter", "aspect", "angle", "taper")

    def __init__(self, key, label, hardness, spacing, min_size, flow, size_gamma=1.0,
                 opacity_pressure=0.0, grain=0.0, jitter=0.0, aspect=1.0, angle=0.0, taper=True):
        self.key = key
        self.label = label
        self.hardness = hardness      # 0 soft .. 1 hard edge
        self.spacing = spacing        # dab step as a fraction of diameter
        self.min_size = min_size      # diameter at zero pressure, fraction of size
        self.size_gamma = size_gamma  # pressure curve for diameter
        self.flow = flow              # build-up per diameter of travel
        self.opacity_pressure = opacity_pressure  # 0: flow ignores pressure
        self.grain = grain            # paper-like speckle in the stamp
        self.jitter = jitter          # dab position scatter, fraction of diameter
        self.aspect = aspect          # stamp height / width
        self.angle = angle            # stamp rotation, degrees
        self.taper = taper            # taper ends of strokes without pressure


PRESETS = OrderedDict((p.key, p) for p in (
    Preset("pen", "Studio Pen", hardness=0.92, spacing=0.07, min_size=0.08, flow=1.0, size_gamma=1.15),
    Preset("soft", "Soft Brush", hardness=0.0, spacing=0.06, min_size=0.5, flow=0.9,
           opacity_pressure=0.75),
    Preset("pencil", "Pencil", hardness=0.7, spacing=0.1, min_size=0.6, flow=0.97,
           opacity_pressure=0.75, grain=0.5, jitter=0.05),
    Preset("marker", "Marker", hardness=0.8, spacing=0.05, min_size=0.85, flow=1.0,
           aspect=0.45, angle=-35.0),
))
ERASER = Preset("eraser", "Eraser", hardness=0.85, spacing=0.07, min_size=0.35, flow=1.0, taper=False)
CLASSIC = "classic"
DEFAULT_PRESET = "pen"
PRESET_KEYS = tuple(PRESETS) + (CLASSIC,)
LABELS = dict({k: p.label for k, p in PRESETS.items()}, classic="Classic")

# What a stroke records: the preset and the tuning in effect when it was
# drawn, so later tuning doesn't change old strokes.
BrushSpec = namedtuple("BrushSpec", "preset hardness spacing taper")

MIN_STEP = 0.35         # stroke units; floor on dab spacing
TAPER_START = 0.22      # diameter factor at the very start of a taper
PRESSURE_EASE = 0.55    # smoothing of raw pen pressure between events


def preset_for(spec):
    return ERASER if spec.preset == "eraser" else PRESETS.get(spec.preset, PRESETS[DEFAULT_PRESET])


def make_spec(key, hardness=None, spacing=None, taper=False):
    p = ERASER if key == "eraser" else PRESETS[key]
    h = p.hardness if hardness is None else max(0.0, min(1.0, hardness))
    s = p.spacing if spacing is None else max(0.02, min(0.5, spacing))
    return BrushSpec(key, h, s, bool(taper and p.taper))


def diameter(preset, size, pressure):
    p = max(0.0, min(1.0, pressure))
    return size * (preset.min_size + (1.0 - preset.min_size) * p ** preset.size_gamma)


def _flow(preset, spacing, pressure):
    f = preset.flow * ((1.0 - preset.opacity_pressure) + preset.opacity_pressure * max(0.0, min(1.0, pressure)))
    if f >= 1.0:
        return 1.0
    return 1.0 - (1.0 - max(0.0, f)) ** spacing


# -- path ---------------------------------------------------------------------

def _segment_samples(points, i):
    """Samples along spline segment i (points[i] -> points[i+1]), excluding
    its start, including its end. Needs points[i+2] for the final curve;
    without it the segment ends as the stroke would at that point."""
    n = len(points)
    p1, pr1 = points[i]
    p2, pr2 = points[i + 1]
    p0 = points[i - 1][0] if i > 0 else p1
    p3 = points[i + 2][0] if i + 2 < n else p2
    chord = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
    steps = max(1, min(96, int(math.ceil(chord / 0.75))))
    out = []
    for s in range(1, steps + 1):
        t = s / steps
        out.append((brush._catmull_rom_point(p0, p1, p2, p3, t), pr1 + (pr2 - pr1) * t))
    return out


def path_length(points):
    total, prev = 0.0, points[0][0]
    for i in range(len(points) - 1):
        for p, _pr in _segment_samples(points, i):
            total += math.hypot(p.x() - prev.x(), p.y() - prev.y())
            prev = p
    return total


# -- dabs ---------------------------------------------------------------------

class DabRenderer:
    """Walks a stroke's samples and emits dabs (x, y, diameter, alpha,
    variant). feed() may be called segment by segment."""

    def __init__(self, stroke, total_length=None):
        self.stroke = stroke
        self.spec = stroke.brush
        self.preset = preset_for(self.spec)
        first = stroke.points[0][0]
        self.rng = random.Random((int(first.x() * 131.0) * 92821 + int(first.y() * 977.0)) & 0xFFFFFFFF)
        self.prev = None
        self.carry = 0.0
        self.dist = 0.0
        self.total = total_length
        self.size = stroke.size
        self.taper_len = max(3.0 * self.size, 4.0)

    def clone(self):
        dup = DabRenderer.__new__(DabRenderer)
        for name in ("stroke", "spec", "preset", "prev", "carry", "dist", "total", "size", "taper_len"):
            setattr(dup, name, getattr(self, name))
        dup.rng = random.Random()
        dup.rng.setstate(self.rng.getstate())
        return dup

    def _taper(self, at):
        if not self.spec.taper:
            return 1.0
        f = 1.0
        if self.total is not None and self.total < 1e-6:
            return 1.0  # a tap: a full dot
        u = min(1.0, at / self.taper_len)
        f = min(f, TAPER_START + (1.0 - TAPER_START) * math.sin(u * math.pi / 2))
        if self.total is not None:
            u = min(1.0, max(0.0, self.total - at) / self.taper_len)
            f = min(f, TAPER_START + (1.0 - TAPER_START) * math.sin(u * math.pi / 2))
        return f

    def _dab(self, x, y, pressure, out):
        pr = self.preset
        d = diameter(pr, self.size, pressure) * self._taper(self.dist)
        # A tap is one dab, so it gets the whole flow rather than the
        # per-dab share of a stroke.
        alpha = _flow(pr, 1.0 if self.total == 0.0 else self.spec.spacing, pressure)
        variant = 0
        if pr.jitter:
            x += (self.rng.random() - 0.5) * 2 * pr.jitter * d
            y += (self.rng.random() - 0.5) * 2 * pr.jitter * d
        if pr.grain:
            variant = self.rng.randrange(_GRAIN_VARIANTS)
            alpha *= 0.75 + 0.25 * self.rng.random()
        out.append((x, y, d, alpha, variant))
        return d

    def _step(self, pressure):
        d = diameter(self.preset, self.size, pressure) * self._taper(self.dist)
        return max(MIN_STEP, self.spec.spacing * d)

    def feed(self, samples):
        out = []
        for p, pr in samples:
            if self.prev is None:
                self.prev = (p, pr)
                self._dab(p.x(), p.y(), pr, out)
                self.carry = self._step(pr)
                continue
            (q, qpr) = self.prev
            dx, dy = p.x() - q.x(), p.y() - q.y()
            seg = math.hypot(dx, dy)
            if seg < 1e-9:
                continue
            walked = 0.0
            while self.carry <= seg - walked:
                walked += self.carry
                self.dist += self.carry
                t = walked / seg
                ppr = qpr + (pr - qpr) * t
                self._dab(q.x() + dx * t, q.y() + dy * t, ppr, out)
                self.carry = self._step(ppr)
            self.carry -= seg - walked
            self.dist += seg - walked
            self.prev = (p, pr)
        return out

    def start(self):
        return self.feed([self.stroke.points[0]])

    def segment(self, i):
        return self.feed(_segment_samples(self.stroke.points, i))


def all_dabs(stroke, final=True):
    points = stroke.points
    total = path_length(points) if (final and stroke.brush.taper and len(points) > 1) else None
    if final and len(points) == 1:
        total = 0.0
    r = DabRenderer(stroke, total)
    out = r.start()
    for i in range(len(points) - 1):
        out.extend(r.segment(i))
    return out


# -- stamps -------------------------------------------------------------------

_GRAIN_VARIANTS = 6
_stamps = OrderedDict()
_STAMP_CACHE_MAX = 160


def _stamp(preset, hardness, size_px, rgb, variant):
    key = (preset.key, round(hardness, 2), size_px, rgb, variant)
    img = _stamps.get(key)
    if img is not None:
        _stamps.move_to_end(key)
        return img
    img = QImage(size_px, size_px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    r = size_px / 2.0
    # At least ~1.2 stamp pixels of falloff so the hardest edge still
    # antialiases.
    h = min(hardness, max(0.0, 1.0 - 1.2 / r))
    color = QColor.fromRgb(rgb)
    grad = QRadialGradient(QPointF(r, r), r)
    stops = 10
    for k in range(stops + 1):
        u = k / stops
        t = h + (1.0 - h) * u
        if hardness < 0.3:
            # Soft: a gaussian-like bell, which reads as airbrush rather
            # than a blurred disc.
            a = (1.0 - t * t) ** 2.2
        else:
            a = 1.0 - (3 * u * u - 2 * u * u * u)
        c = QColor(color)
        c.setAlphaF(max(0.0, min(1.0, a)))
        grad.setColorAt(t, c)
    if h > 0:
        c = QColor(color)
        c.setAlphaF(1.0)
        grad.setColorAt(0.0, c)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(r, r)
        p.rotate(preset.angle)
        p.scale(1.0, preset.aspect)
        p.translate(-r, -r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QRectF(0, 0, size_px, size_px))
        if preset.grain:
            p.resetTransform()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            rng = random.Random(variant * 7919 + size_px)
            n = int(size_px * size_px * 0.22)
            for _ in range(n):
                c = QColor(0, 0, 0)
                c.setAlphaF(preset.grain * rng.random())
                p.fillRect(QRectF(rng.random() * size_px, rng.random() * size_px, 1, 1), c)
    finally:
        p.end()
    _stamps[key] = img
    if len(_stamps) > _STAMP_CACHE_MAX:
        _stamps.popitem(last=False)
    return img


def _stamp_size(d_device):
    s = 8
    while s < d_device and s < 512:
        s *= 2
    return s


# -- layers -------------------------------------------------------------------

def dab_bounds(dabs):
    pad = max(d for _x, _y, d, _a, _v in dabs) / 2 + 2
    xs = [x for x, _y, _d, _a, _v in dabs]
    ys = [y for _x, y, _d, _a, _v in dabs]
    return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad,
                  max(ys) - min(ys) + 2 * pad).toAlignedRect()


def paint_dabs(painter, stroke, dabs, dpr):
    preset = preset_for(stroke.brush)
    color = QColor(0, 0, 0) if stroke.eraser else QColor(stroke.color)
    rgb = color.rgb() & 0xFFFFFF
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for x, y, d, alpha, variant in dabs:
        if d <= 0 or alpha <= 0:
            continue
        img = _stamp(preset, stroke.brush.hardness, _stamp_size(d * dpr), rgb, variant)
        if d * dpr < 1.0:
            # Below a device pixel: keep the coverage, not the size.
            alpha *= d * dpr
            d = 1.0 / dpr
        painter.setOpacity(min(1.0, alpha))
        painter.drawImage(QRectF(x - d / 2, y - d / 2, d, d), img)
    painter.setOpacity(1.0)


# Live pieces are merged into one layer as a stroke grows. At 125%-175%
# scaling a whole logical pixel isn't a whole device pixel, so pieces at
# arbitrary logical offsets would be resampled (softened) on every merge.
# On a 4 px grid every offset is a whole device pixel at any multiple of 25%.
_GRID = 4


def _grid_aligned(rect):
    g = _GRID
    left = math.floor(rect.left() / g) * g
    top = math.floor(rect.top() / g) * g
    right = math.ceil((rect.left() + rect.width()) / g) * g
    bottom = math.ceil((rect.top() + rect.height()) / g) * g
    return QRect(left, top, right - left, bottom - top)


def render_dabs(stroke, dabs, dpr):
    """(pixmap, origin) holding `dabs`, or None."""
    if not dabs:
        return None
    bounds = _grid_aligned(dab_bounds(dabs))
    layer = brush.new_layer(bounds.size(), dpr)
    painter = QPainter(layer)
    try:
        painter.translate(-bounds.topLeft())
        paint_dabs(painter, stroke, dabs, dpr)
    finally:
        painter.end()
    return layer, bounds.topLeft()


class _LayerCache:
    """Rendered layers of committed strokes, so a canvas rebuild (undo,
    selection drag, capture pan) doesn't re-stamp every dab. Bounded by
    bytes; strokes are weakly referenced and never change once committed."""

    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self.bytes = 0
        self.items = OrderedDict()

    def get(self, stroke, dpr):
        entry = self.items.get(id(stroke))
        if entry is None or entry[0]() is not stroke or entry[1] != dpr:
            return None
        self.items.move_to_end(id(stroke))
        return entry[2]

    def put(self, stroke, dpr, layer):
        self.drop(id(stroke))
        size = layer[0].width() * layer[0].height() * 4
        key = id(stroke)
        self.items[key] = (weakref.ref(stroke, lambda _r, k=key: self.drop(k)), dpr, layer, size)
        self.bytes += size
        while self.bytes > self.max_bytes and len(self.items) > 1:
            k, _e = next(iter(self.items.items()))
            self.drop(k)

    def drop(self, key):
        entry = self.items.pop(key, None)
        if entry is not None:
            self.bytes -= entry[3]

    def clear(self):
        self.items.clear()
        self.bytes = 0


layer_cache = _LayerCache(256 * 1024 * 1024)


def freehand_layer(stroke, dpr, cache=True):
    """The flattened layer for a committed freehand stroke (either engine),
    as (pixmap, origin), or None. Erasers come back as an opaque-ish mask
    for DestinationOut."""
    if stroke.brush is None:
        return brush.render_stroke_layer(stroke, brush.stroke_segments(stroke), dpr)
    if cache:
        hit = layer_cache.get(stroke, dpr)
        if hit is not None:
            return hit
    from . import mypaint_engine
    if mypaint_engine.is_spec(stroke.brush):
        layer = mypaint_engine.stroke_layer(stroke, dpr)
    else:
        layer = render_dabs(stroke, all_dabs(stroke), dpr)
    if layer is not None and cache:
        layer_cache.put(stroke, dpr, layer)
    return layer


def freehand_bounds(stroke):
    """Stroke-coordinate bounds of a freehand stroke, cheaply."""
    if stroke.brush is None:
        segments = brush.stroke_segments(stroke)
        return QRectF(brush.segment_bounds(segments)) if segments else QRectF()
    xs = [p.x() for p, _ in stroke.points]
    ys = [p.y() for p, _ in stroke.points]
    from . import mypaint_engine
    if mypaint_engine.is_spec(stroke.brush):
        pad = mypaint_engine.bounds_pad(stroke)
    else:
        pad = stroke.size * (1 + preset_for(stroke.brush).jitter * 2) / 2 + 2
    return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)


# -- size mapping ---------------------------------------------------------------
#
# The size slider is logarithmic: equal slider travel is an equal ratio, so
# 1 to 2 px gets as much room as 40 to 80 px. Sizes are on-screen (logical)
# pixels; strokes store them divided by the capture's view scale, so a brush
# looks the same size on any monitor scale and in a fitted capture.

SIZE_STEPS = 1000


def slider_to_size(pos, lo=None, hi=None):
    from . import constants as C
    lo = C.MIN_SIZE if lo is None else lo
    hi = C.MAX_SIZE if hi is None else hi
    t = max(0, min(SIZE_STEPS, pos)) / SIZE_STEPS
    return round(lo * (hi / lo) ** t, 2)


def size_to_slider(size, lo=None, hi=None):
    from . import constants as C
    lo = C.MIN_SIZE if lo is None else lo
    hi = C.MAX_SIZE if hi is None else hi
    size = max(lo, min(hi, float(size)))
    return int(round(math.log(size / lo) / math.log(hi / lo) * SIZE_STEPS))


def step_size(size, delta):
    """[ and ]: about 12% per press, at least 0.5 px."""
    new = size * (1.12 ** delta)
    if abs(new - size) < 0.5:
        new = size + 0.5 * (1 if delta > 0 else -1)
    return new


def format_size(size):
    return f"{size:.1f}px" if size < 10 else f"{size:.0f}px"
