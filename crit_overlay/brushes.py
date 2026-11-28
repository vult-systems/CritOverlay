"""Brush presets and shared brush helpers. All freehand painting, erasers
included, is libmypaint (mypaint_engine.py).

Each preset is a stock MyPaint brush from mypaint-brushes 1.3.1 (CC0),
tuned with a few base-value tweaks rather than new dynamics, plus the app's
two pressure controls (size, opacity) and a size scale measured so that the
visible line at full pressure is as wide as the size and the brush ring.
"""

import math
import weakref
from collections import OrderedDict, namedtuple

from PySide6.QtCore import QRectF

from . import mypaint_engine

Preset = namedtuple("Preset", "key label file size_pressure opacity_pressure tweaks size_scale")

PRESETS = OrderedDict((p.key, p) for p in (
    Preset("studio_pen", "Studio Pen", "deevad_soft-dip-pen.myb", True, False, (), 1.08),
    Preset("pencil", "Pencil", "tanda_pencil-2b.myb", True, True, (), 1.45),
    Preset("ink", "Ink", "kaerhon_v1_inkster_l.myb", True, False, (), 1.02),
    Preset("marker", "Marker", "tanda_marker-01.myb", False, False, (), 0.98),
    Preset("soft_brush", "Soft Brush", "deevad_airbrush.myb", True, True,
           (("opaque", 1.0), ("hardness", 0.4)), 1.36),
))
ERASERS = OrderedDict((p.key, p) for p in (
    Preset("hard", "Hard eraser", "deevad_large_hard_eraser.myb", True, False, (), 1.02),
    Preset("soft", "Soft eraser", "ramon_Soft_Eraser.myb", True, True, (), 1.32),
))
DEFAULT_PRESET = "studio_pen"
PRESET_KEYS = tuple(PRESETS)
LABELS = {k: p.label for k, p in PRESETS.items()}
BRUSH_FILES = [p.file for p in PRESETS.values()] + [p.file for p in ERASERS.values()]

# Earlier prototype presets (custom raster engine, Classic, MyPaint picks)
# map to the nearest current one when settings load.
LEGACY_PRESETS = {
    "pen": "studio_pen", "classic": "studio_pen", "soft": "soft_brush", "pencil": "pencil", "marker": "marker",
    "mypaint:deevad_ballpen.myb": "studio_pen", "mypaint:deevad_liner.myb": "ink",
    "mypaint:tanda_pencil-2b.myb": "pencil", "mypaint:deevad_4H_pencil.myb": "pencil",
    "mypaint:classic_textured_ink.myb": "ink", "mypaint:classic_calligraphy.myb": "ink",
    "mypaint:tanda_charcoal-01.myb": "pencil", "mypaint:tanda_marker-01.myb": "marker",
    "mypaint:deevad_airbrush.myb": "soft_brush",
}


def migrate_preset(key):
    key = LEGACY_PRESETS.get(key, key)
    return key if key in PRESETS else DEFAULT_PRESET


def spec_for(preset, size_pressure=None, opacity_pressure=None):
    """The MypaintSpec a new stroke records, so it replays exactly."""
    return mypaint_engine.spec(
        preset.file,
        preset.size_pressure if size_pressure is None else size_pressure,
        preset.opacity_pressure if opacity_pressure is None else opacity_pressure,
        preset.tweaks, preset.size_scale)


def diameter(spec, size, pressure):
    """Visible diameter at `pressure` for a stroke of `size` (full-pressure
    width): what the brush ring and the input diagnostic show."""
    return size * mypaint_engine.diameter_fraction(spec, pressure)


# -- size mapping ----------------------------------------------------------------
#
# The size slider is logarithmic: equal slider travel is an equal ratio, so
# 1 to 2 px gets as much room as 40 to 80 px. Sizes are on-screen (logical)
# pixels; strokes store them divided by the capture's view scale, so a brush
# looks the same size on any monitor scale and at any zoom.

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


# -- committed stroke layers -------------------------------------------------------

class _LayerCache:
    """Rendered layers of committed strokes, so a canvas rebuild (undo,
    selection drag, zoom) doesn't replay every stroke. Bounded by bytes;
    strokes are weakly referenced and never change once committed."""

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
    """The flattened layer for a committed freehand stroke, as (pixmap,
    origin), or None. Eraser strokes come back as their coverage mask, which
    the canvas removes with DestinationOut."""
    if cache:
        hit = layer_cache.get(stroke, dpr)
        if hit is not None:
            return hit
    layer = mypaint_engine.stroke_layer(stroke, dpr)
    if layer is not None and cache:
        layer_cache.put(stroke, dpr, layer)
    return layer


def freehand_bounds(stroke):
    """Stroke-coordinate bounds of a freehand stroke, cheaply."""
    if not stroke.points:
        return QRectF()
    xs = [p.x() for p, _ in stroke.points]
    ys = [p.y() for p, _ in stroke.points]
    pad = mypaint_engine.bounds_pad(stroke)
    return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)
