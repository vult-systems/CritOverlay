"""The brush engine: libmypaint, through ctypes.

crit_overlay/mypaint/mypaint.dll is built by
third_party/libmypaint/build_libmypaint.py and is a required part of the
build. libmypaint does all the brush work: dynamics, smoothing ("slow
tracking"), dab shapes, textures and jitter from standard .myb brush files.
We feed it input events and read back its surface.

A stroke records the events it was drawn with (position, pressure, time
step) so it can be replayed exactly: a fresh brush from the same .myb file
starts from the same random seed. Each stroke paints on its own sparse,
transparent surface (crit_surface.c) in device pixels, and the result is a
layer like any other freehand stroke, so opacity, erasing, undo and exports
work unchanged.
"""

import ctypes
import logging
import math
from collections import namedtuple
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPixmap

logger = logging.getLogger(__name__)

DIR = Path(__file__).resolve().parent / "mypaint"
MOUSE_PRESSURE = 1.0   # full pressure, like the dab engine, so a mouse line matches the ring

# Windows Ink reports tilt in degrees, about -60..60; libmypaint wants -1..1.
TILT_RANGE = 60.0


def tilt(x_degrees, y_degrees):
    clamp = lambda v: max(-1.0, min(1.0, v / TILT_RANGE))  # noqa: E731
    return clamp(x_degrees), clamp(y_degrees)
_GRID = 4              # see dab._GRID

# brush: the .myb file. size_pressure / opacity_pressure: whether pen
# pressure drives size and opacity (the app's own mappings, replacing the
# brush's pressure curves for those two settings). tweaks: base values to
# override, as ((setting, value), ...).
MypaintSpec = namedtuple("MypaintSpec", "engine brush size_pressure opacity_pressure tweaks scale",
                         defaults=(True, True, (), 1.0))

# Pressure -> size: diameter at zero pressure, as a fraction of full size.
MIN_SIZE_FRACTION = 0.12
# Pressure -> opacity off still stops painting at exactly zero pressure
# (the pen lifting), then jumps straight to full.
_OPACITY_STEP = 0.015


def spec(brush_file, size_pressure=True, opacity_pressure=True, tweaks=(), scale=1.0):
    """scale: brush radius per unit of size, measured per preset so the
    visible line at full pressure is as wide as the size."""
    return MypaintSpec("mypaint", brush_file, bool(size_pressure), bool(opacity_pressure), tuple(tweaks),
                       float(scale))


def diameter_fraction(spec_, pressure):
    """Diameter at `pressure` as a fraction of full size, under our mapping."""
    if not getattr(spec_, "size_pressure", True):
        return 1.0
    p = max(0.0, min(1.0, pressure))
    return math.exp(math.log(MIN_SIZE_FRACTION) * (1.0 - p))


def is_spec(brush):
    return getattr(brush, "engine", None) == "mypaint"


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int)]


_lib = None
_load_failed = False
_brush_json = {}


def _load():
    global _lib, _load_failed
    if _lib is not None or _load_failed:
        return _lib
    path = DIR / "mypaint.dll"
    if not path.exists():
        _load_failed = True
        return None
    try:
        lib = ctypes.CDLL(str(path))
        vp, f, i = ctypes.c_void_p, ctypes.c_float, ctypes.c_int
        sig = {
            "mypaint_brush_new": ([], vp),
            "mypaint_brush_unref": ([vp], None),
            "mypaint_brush_from_string": ([vp, ctypes.c_char_p], i),
            "mypaint_brush_new_stroke": ([vp], None),
            "mypaint_brush_reset": ([vp], None),
            "mypaint_brush_stroke_to": ([vp, vp, f, f, f, f, f, ctypes.c_double], i),
            "mypaint_brush_set_base_value": ([vp, i, f], None),
            "mypaint_brush_get_base_value": ([vp, i], f),
            "mypaint_brush_setting_from_cname": ([ctypes.c_char_p], i),
            "mypaint_brush_input_from_cname": ([ctypes.c_char_p], i),
            "mypaint_brush_set_mapping_n": ([vp, i, i, i], None),
            "mypaint_brush_set_mapping_point": ([vp, i, i, i, f, f], None),
            "mypaint_surface_begin_atomic": ([vp], None),
            "mypaint_surface_end_atomic": ([vp, ctypes.POINTER(_Rect)], None),
            "mypaint_surface_unref": ([vp], None),
            "crit_surface_new": ([], vp),
            "crit_surface_read": ([vp, i, i, i, i, vp, i], None),
        }
        for name, (args, res) in sig.items():
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = res
        lib.settings = {n: lib.mypaint_brush_setting_from_cname(n.encode())
                        for n in ("radius_logarithmic", "color_h", "color_s", "color_v", "opaque_multiply",
                                  "eraser", "lock_alpha", "slow_tracking", "slow_tracking_per_dab", "opaque")}
        lib.all_inputs = [i for i in (lib.mypaint_brush_input_from_cname(n.encode()) for n in (
            "pressure", "speed1", "speed2", "random", "stroke", "direction", "tilt_declination",
            "tilt_ascension", "custom")) if i >= 0]
        lib.pressure_input = lib.mypaint_brush_input_from_cname(b"pressure")
        _lib = lib
    except (OSError, AttributeError):
        logger.exception("Couldn't load %s; the MyPaint engine is unavailable", path)
        _load_failed = True
    return _lib


def available():
    return _load() is not None


def _json(brush_file):
    if brush_file not in _brush_json:
        path = Path(brush_file)
        if not path.is_absolute():
            path = DIR / "brushes" / brush_file
        _brush_json[brush_file] = path.read_bytes()
    return _brush_json[brush_file]


def _aligned(x0, y0, x1, y1, dpr):
    """Device rect -> (logical grid-aligned QRect, device x, y, w, h)."""
    g = _GRID
    left = math.floor(x0 / dpr / g) * g
    top = math.floor(y0 / dpr / g) * g
    right = math.ceil(x1 / dpr / g) * g
    bottom = math.ceil(y1 / dpr / g) * g
    logical = QRect(left, top, right - left, bottom - top)
    return logical, round(left * dpr), round(top * dpr), round((right - left) * dpr), round((bottom - top) * dpr)


class StrokePainter:
    """One stroke on its own libmypaint surface. Feed events with add(),
    read pieces with read(), and the whole result with layer()."""

    def __init__(self, stroke, dpr):
        lib = _load()
        self.lib = lib
        self.dpr = float(dpr)
        self.brush = lib.mypaint_brush_new()
        if not lib.mypaint_brush_from_string(self.brush, _json(stroke.brush.brush)):
            logger.warning("Couldn't parse brush %s", stroke.brush.brush)
        # Size: the brush's own base radius becomes our size (diameter, in
        # stroke units) at this pixel density; its pressure curves still
        # scale it from there.
        s = lib.settings
        scale = getattr(stroke.brush, "scale", 1.0)
        lib.mypaint_brush_set_base_value(self.brush, s["radius_logarithmic"],
                                         math.log(max(0.2, stroke.size * scale * self.dpr / 2.0)))
        c = stroke.color
        h = c.hsvHueF()
        lib.mypaint_brush_set_base_value(self.brush, s["color_h"], 0.0 if h < 0 else h)
        lib.mypaint_brush_set_base_value(self.brush, s["color_s"], c.hsvSaturationF())
        lib.mypaint_brush_set_base_value(self.brush, s["color_v"], c.valueF())
        sp = stroke.brush
        for name, value in getattr(sp, "tweaks", ()):
            sid = lib.mypaint_brush_setting_from_cname(name.encode())
            if sid >= 0:
                lib.mypaint_brush_set_base_value(self.brush, sid, float(value))
        # Each stroke paints on its own transparent surface; an eraser stroke
        # becomes a coverage mask the canvas subtracts. libmypaint's own
        # eraser mode would erase that empty surface instead, so it's off.
        lib.mypaint_brush_set_base_value(self.brush, s["eraser"], 0.0)
        lib.mypaint_brush_set_base_value(self.brush, s["lock_alpha"], 0.0)
        # Smoothing is the app's one Stabilize setting, the same for every
        # brush; a brush's own slow tracking would trail the pen and end
        # strokes short of where it lifted.
        for name in ("slow_tracking", "slow_tracking_per_dab"):
            lib.mypaint_brush_set_base_value(self.brush, s[name], 0.0)
            for inp in lib.all_inputs:
                lib.mypaint_brush_set_mapping_n(self.brush, s[name], inp, 0)
        self._apply_pressure(sp)
        lib.mypaint_brush_reset(self.brush)
        lib.mypaint_brush_new_stroke(self.brush)
        self.surface = lib.crit_surface_new()
        self.bbox = None  # device (x0, y0, x1, y1) painted so far
        self.started = False

    def _apply_pressure(self, sp):
        """Our pressure -> size and pressure -> opacity, independent of
        each other and of the brush's other dynamics (speed, tilt, random,
        which stay as the brush defines them). The Soft/Normal/Firm response
        is already in the pressure values."""
        lib, s, pin = self.lib, self.lib.settings, self.lib.pressure_input
        radius = s["radius_logarithmic"]
        if getattr(sp, "size_pressure", True):
            lib.mypaint_brush_set_mapping_n(self.brush, radius, pin, 2)
            lib.mypaint_brush_set_mapping_point(self.brush, radius, pin, 0, 0.0, math.log(MIN_SIZE_FRACTION))
            lib.mypaint_brush_set_mapping_point(self.brush, radius, pin, 1, 1.0, 0.0)
        else:
            lib.mypaint_brush_set_mapping_n(self.brush, radius, pin, 0)
        # Opacity: our opaque_multiply curve is the only pressure link (a
        # brush's own pressure -> opaque curve would make "off" not off).
        # Pressure links that shape texture (hardness, scatter) stay.
        lib.mypaint_brush_set_mapping_n(self.brush, s["opaque"], pin, 0)
        om = s["opaque_multiply"]
        lib.mypaint_brush_set_base_value(self.brush, om, 0.0)
        if getattr(sp, "opacity_pressure", True):
            lib.mypaint_brush_set_mapping_n(self.brush, om, pin, 2)
            lib.mypaint_brush_set_mapping_point(self.brush, om, pin, 0, 0.0, 0.0)
            lib.mypaint_brush_set_mapping_point(self.brush, om, pin, 1, 1.0, 1.0)
        else:
            lib.mypaint_brush_set_mapping_n(self.brush, om, pin, 3)
            lib.mypaint_brush_set_mapping_point(self.brush, om, pin, 0, 0.0, 0.0)
            lib.mypaint_brush_set_mapping_point(self.brush, om, pin, 1, _OPACITY_STEP, 1.0)
            lib.mypaint_brush_set_mapping_point(self.brush, om, pin, 2, 1.0, 1.0)

    def add(self, events):
        """events: [(x, y, pressure, dtime, xtilt, ytilt)] in stroke units.
        Returns the device rect (x0, y0, x1, y1) that changed, or None."""
        lib, d = self.lib, self.dpr
        lib.mypaint_surface_begin_atomic(self.surface)
        if events and not self.started:
            # The brush starts at (0, 0); a long pressure-free step makes
            # libmypaint jump to the first point instead of easing in from
            # the corner (its slow tracking would draw that path).
            x, y = events[0][0] * d, events[0][1] * d
            lib.mypaint_brush_stroke_to(self.brush, self.surface, x, y, 0.0, 0.0, 0.0, 10.0)
            self.started = True
        for x, y, pressure, dt, xt, yt in events:
            # libmypaint complains about (and mishandles) a zero time step.
            lib.mypaint_brush_stroke_to(self.brush, self.surface, x * d, y * d, pressure, xt, yt, max(dt, 0.001))
        roi = _Rect()
        lib.mypaint_surface_end_atomic(self.surface, ctypes.byref(roi))
        if roi.width <= 0 or roi.height <= 0:
            return None
        r = (roi.x, roi.y, roi.x + roi.width, roi.y + roi.height)
        b = self.bbox
        self.bbox = r if b is None else (min(b[0], r[0]), min(b[1], r[1]), max(b[2], r[2]), max(b[3], r[3]))
        return r

    def read(self, rect):
        """(QPixmap, logical origin) of a device rect, grid aligned."""
        logical, x, y, w, h = _aligned(*rect, self.dpr)
        if w <= 0 or h <= 0:
            return None
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        view = (ctypes.c_ubyte * img.sizeInBytes()).from_buffer(img.bits())
        self.lib.crit_surface_read(self.surface, x, y, w, h, ctypes.addressof(view), img.bytesPerLine())
        del view
        img.setDevicePixelRatio(self.dpr)
        pixmap = QPixmap.fromImage(img)
        pixmap.setDevicePixelRatio(self.dpr)
        return pixmap, logical.topLeft()

    def layer(self):
        return self.read(self.bbox) if self.bbox is not None else None

    def close(self):
        if self.brush:
            self.lib.mypaint_brush_unref(self.brush)
            self.brush = None
        if self.surface:
            self.lib.mypaint_surface_unref(self.surface)
            self.surface = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def events_of(stroke):
    tilts = stroke.tilts or [(0.0, 0.0)] * len(stroke.points)
    return [(p.x(), p.y(), pr, dt, xt, yt)
            for (p, pr), dt, (xt, yt) in zip(stroke.points, stroke.dtimes or (), tilts)]


def stroke_layer(stroke, dpr):
    """Replay a committed stroke: (pixmap, origin) or None."""
    if not available():
        return None
    painter = StrokePainter(stroke, dpr)
    try:
        painter.add(events_of(stroke))
        return painter.layer()
    finally:
        painter.close()


def bounds_pad(stroke):
    # Brushes can scatter dabs a few radii from the line.
    return stroke.size * getattr(stroke.brush, "scale", 1.0) * 3 + 8

