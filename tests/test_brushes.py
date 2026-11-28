"""libmypaint as the brush engine: presets, sizing, pressure controls,
pressure response, tilt, stabilizer, erasers, input diagnostic, migration.

Synthetic tablet events check the code path, not physical pens; the input
diagnostic (Ctrl+Shift+I) is for checking real hardware.
"""

import statistics
import unittest
from unittest import mock

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QTabletEvent  # noqa: E402

from crit_overlay import brush_preview, brushes  # noqa: E402
from crit_overlay import mypaint_engine as mp  # noqa: E402
from crit_overlay.models import Stroke  # noqa: E402
from tests.test_capture import PEN, CaptureCase  # noqa: E402

APP = qt_env.app


def stroke_layer(preset, size, pressure, size_p=None, opacity_p=None, n=120):
    s = Stroke(QColor(0, 0, 0), size, 1.0)
    s.points = [(QPointF(50 + i * 2, 200), pressure) for i in range(n + 1)]
    s.dtimes = [0.0] + [0.006] * n
    s.brush = brushes.spec_for(preset, size_p, opacity_p)
    return brushes.freehand_layer(s, 1.0, cache=False)


def measure(layer):
    """(median visible width at half the stroke's peak, peak alpha)."""
    pix, origin = layer
    img = pix.toImage()
    widths, peaks = [], []
    for x in range(140, 220, 5):
        col = [img.pixelColor(x - origin.x(), y).alphaF() for y in range(img.height())]
        peak = max(col)
        cov = [y for y, a in enumerate(col) if peak > 0.02 and a >= peak * 0.5]
        widths.append(cov[-1] - cov[0] + 1 if cov else 0)
        peaks.append(peak)
    return statistics.median(widths), statistics.median(peaks)


def extent(layer):
    """Full height of the ink across the stroke's middle (any alpha)."""
    pix, origin = layer
    img = pix.toImage()
    x = 180 - origin.x()
    ys = [y for y in range(img.height()) if img.pixelColor(x, y).alpha() > 3]
    return ys[-1] - ys[0] + 1 if ys else 0


class EngineTests(unittest.TestCase):
    def test_libmypaint_is_staged_and_loads(self):
        self.assertTrue(mp.available())
        for f in brushes.BRUSH_FILES:
            self.assertTrue((mp.DIR / "brushes" / f).exists(), f)

    def test_five_presets_and_two_erasers(self):
        self.assertEqual(list(brushes.LABELS.values()), ["Studio Pen", "Pencil", "Ink", "Marker", "Soft Brush"])
        self.assertEqual(list(brushes.ERASERS), ["hard", "soft"])

    def test_every_brush_draws_at_its_size(self):
        for preset in list(brushes.PRESETS.values()) + list(brushes.ERASERS.values()):
            for size in (8, 24):
                with self.subTest(preset=preset.key, size=size):
                    width, _ = measure(stroke_layer(preset, size, 1.0))
                    self.assertAlmostEqual(width, size, delta=max(2, size * 0.1))

    def test_pressure_controls_are_independent(self):
        pen = brushes.PRESETS["studio_pen"]
        light_w, _ = measure(stroke_layer(pen, 30, 0.2, size_p=True, opacity_p=False))
        full_w, full_a = measure(stroke_layer(pen, 30, 1.0, size_p=True, opacity_p=False))
        self.assertLess(light_w, full_w * 0.6, "pressure drives size")
        # Opacity, compared at a fixed size (thin lines lose peak alpha to
        # antialiasing).
        _, fixed_light_a = measure(stroke_layer(pen, 30, 0.2, size_p=False, opacity_p=False))
        self.assertAlmostEqual(fixed_light_a, full_a, delta=0.05, msg="opacity untouched with opacity pressure off")
        light = stroke_layer(pen, 30, 0.2, size_p=False, opacity_p=True)
        _, a = measure(light)
        self.assertAlmostEqual(extent(light), extent(stroke_layer(pen, 30, 1.0, size_p=False, opacity_p=False)),
                               delta=3, msg="size untouched with size pressure off")
        self.assertLess(a, full_a * 0.6, "pressure drives opacity")
        self.assertAlmostEqual(brushes.diameter(brushes.spec_for(pen), 30, 1.0), 30)
        self.assertLess(brushes.diameter(brushes.spec_for(pen), 30, 0.0), 30 * 0.2)

    def test_brush_lag_is_off_so_strokes_reach_the_pen(self):
        for preset in brushes.PRESETS.values():
            pix, origin = stroke_layer(preset, 10, 1.0, n=60)
            right = origin.x() + pix.width() / pix.devicePixelRatio()
            with self.subTest(preset=preset.key):
                self.assertGreater(right, 50 + 120 - 2, "ink up to the last point")

    def test_replay_is_exact(self):
        pen = brushes.PRESETS["pencil"]
        a = stroke_layer(pen, 12, 0.7)[0].toImage()
        b = stroke_layer(pen, 12, 0.7)[0].toImage()
        self.assertEqual(a, b)

    def test_log_size_mapping(self):
        for size in (0.5, 1, 2.5, 12, 80, 160):
            self.assertAlmostEqual(brushes.slider_to_size(brushes.size_to_slider(size)), size, delta=size * 0.01)
        self.assertEqual(brushes.size_to_slider(2) - brushes.size_to_slider(1),
                         brushes.size_to_slider(160) - brushes.size_to_slider(80))

    def test_previews_use_the_engine(self):
        for key in list(brushes.PRESETS) + list(brushes.ERASERS):
            img = brush_preview.preview(key, "#ffffff", 120, 40, 1.0).toImage()
            inked = sum(1 for x in range(0, 120, 2) for y in range(0, 40, 2) if img.pixelColor(x, y).alpha() > 10)
            with self.subTest(key=key):
                self.assertGreater(inked, 10)
        self.assertIsNotNone(brush_preview.dab(brushes.spec_for(brushes.PRESETS["ink"]), "#fff", 20, 1.0))

    def test_legacy_presets_migrate(self):
        for old, new in (("pen", "studio_pen"), ("classic", "studio_pen"), ("soft", "soft_brush"),
                         ("mypaint:deevad_liner.myb", "ink"), ("garbage", "studio_pen"), ("pencil", "pencil")):
            self.assertEqual(brushes.migrate_preset(old), new)


class PenInputTests(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_smoothing(0)
        o._set_brush_preset("studio_pen")

    def tablet(self, kind, x, y, pressure=0.6, xtilt=0.0, ytilt=0.0, device=PEN):
        o = self.o
        etype = {"press": QEvent.Type.TabletPress, "move": QEvent.Type.TabletMove,
                 "release": QEvent.Type.TabletRelease}[kind]
        down = kind != "release"
        glob = QPointF(o.mapToGlobal(QPoint(int(x), int(y))))
        ev = QTabletEvent(etype, device, QPointF(x, y), glob, pressure if down else 0.0, xtilt, ytilt, 0.0, 0.0,
                          0.0, Qt.KeyboardModifier.NoModifier,
                          Qt.MouseButton.LeftButton if kind != "move" else Qt.MouseButton.NoButton,
                          Qt.MouseButton.LeftButton if down else Qt.MouseButton.NoButton)
        o.tabletEvent(ev)

    def pen_stroke(self, pressure=0.3, n=20, **kw):
        self.tablet("press", 200, 300, pressure, **kw)
        for i in range(1, n):
            self.tablet("move", 200 + i * 10, 300, pressure, **kw)
        self.tablet("release", 200 + n * 10, 300)
        return self.o.history.strokes[-1]

    def test_pen_pressure_reaches_libmypaint(self):
        s = self.pen_stroke(0.3)
        self.assertTrue(mp.is_spec(s.brush))
        self.assertAlmostEqual(s.points[-1][1], 0.3, delta=0.01, msg="raw pressure, Normal response")
        self.assertTrue(s.brush.size_pressure)

    def test_landing_zeros_never_disable_pressure(self):
        o = self.o
        self.tablet("press", 200, 300, 0.0)
        for i in range(1, 10):
            self.tablet("move", 200 + i * 5, 300, 0.0)   # tip landing: zeros
        for i in range(10, 25):
            self.tablet("move", 200 + i * 5, 300, 0.5)
        self.tablet("release", 330, 300)
        self.assertFalse(o._no_pressure_devices)
        self.assertAlmostEqual(self.pen_stroke(0.4).points[-1][1], 0.4, delta=0.01)

    def test_a_dead_sensor_falls_back_to_fixed_pressure(self):
        o = self.o
        self.tablet("press", 200, 300, 0.0)
        for i in range(1, 12):
            self.tablet("move", 200 + i * 5, 300, 0.0)
        self.tablet("release", 260, 300)
        self.assertEqual(len(o._no_pressure_devices), 1)

    def test_response_curves(self):
        o = self.o
        got = {}
        for name in ("soft", "normal", "firm"):
            o._set_pressure_curve(name)
            got[name] = self.pen_stroke(0.3).points[-1][1]
        self.assertGreater(got["soft"], got["normal"])
        self.assertGreater(got["normal"], got["firm"])

    def test_mouse_is_full_pressure_and_never_overrides_the_pen(self):
        o = self.o
        o._set_pressure_curve("firm")
        self.draw_line((200, 300), (500, 300))
        self.assertEqual(o.history.strokes[-1].points[-1][1], 1.0, "mouse: full pressure")
        # A pen stroke, then the mouse event Windows synthesizes from it.
        self.tablet("press", 200, 400, 0.25)
        self.tablet("move", 260, 400, 0.25)
        n = len(o.history)
        self.mouse("press", 260, 400)  # within the guard: ignored
        self.tablet("release", 300, 400)
        self.mouse("release", 300, 400)
        self.assertEqual(len(o.history), n + 1, "one stroke, not two")
        self.assertLess(o.history.strokes[-1].points[-1][1], 0.5, "the pen's pressure, not the mouse's")

    def test_pressure_toggles_per_brush_and_persist(self):
        o = self.o
        o.set_brush_pressure(size=False, opacity=True)
        s = self.pen_stroke(0.3)
        self.assertEqual((s.brush.size_pressure, s.brush.opacity_pressure), (False, True))
        o._set_brush_preset("pencil")
        self.assertEqual(o.pressure_flags(), (True, True), "each brush keeps its own")
        o.save_settings_now()
        from crit_overlay.config import AppConfig
        self.assertEqual(AppConfig(o._config.path).load()["brush_pressure"]["studio_pen"], (False, True))
        o._show_brush_popup(o.preset_btn)
        pop = o._brush_popup
        self.assertTrue(pop.opacity_pressure_btn.isChecked())
        pop.size_pressure_btn.click()
        self.assertEqual(o.pressure_flags(), (False, True))
        pop.close()

    def test_tilt_is_recorded_and_replays(self):
        o = self.o
        s = self.pen_stroke(0.7, xtilt=30.0, ytilt=-15.0)
        self.assertAlmostEqual(s.tilts[-1][0], 0.5, places=3)
        self.assertAlmostEqual(s.tilts[-1][1], -0.25, places=3)
        dpr = o._canvas_dpr()
        self.assertEqual(brushes.layer_cache.get(s, dpr)[0].toImage(), mp.stroke_layer(s, dpr)[0].toImage())

    def test_stabilizer(self):
        o = self.o
        o._set_smoothing(60)
        self.mouse("press", 200, 300)
        for i in range(1, 60):
            self.mouse("move", 200 + i * 6, 300 + (12 if i % 2 else -12))
        self.mouse("release", 560, 300)
        ys = [p.y() for p, _ in o.history.strokes[-1].points]
        self.assertLess(max(ys) - min(ys), 12)

    def test_erasers(self):
        o = self.o
        o._set_size(40)
        self.draw_line((150, 300), (650, 300))
        o._set_mode("eraser")
        o._set_size(40)
        self.draw_line((300, 200), (300, 400))
        self.assertLessEqual(self.painted(300, 300)[3], 2, "hard eraser clears fully")
        o._set_eraser_style("soft")
        self.tablet("press", 450, 200, 0.25)
        for i in range(1, 20):
            self.tablet("move", 450, 200 + i * 10, 0.25)
        self.tablet("release", 450, 400)
        self.assertEqual(o.history.strokes[-1].brush.brush, brushes.ERASERS["soft"].file)
        a = self.painted(450, 300)[3]
        self.assertTrue(20 < a < 250, f"soft eraser at light pressure erases partly: alpha {a}")

    def test_input_diagnostic(self):
        o = self.o
        o.toggle_input_diag()
        self.assertTrue(o.show_input_diag)
        self.tablet("press", 200, 300, 0.42, xtilt=10)
        self.tablet("move", 210, 300, 0.42, xtilt=10)
        d = o._diag
        self.assertEqual(d["via"], "tablet")
        self.assertAlmostEqual(d["raw"], 0.42, places=2)
        self.assertAlmostEqual(d["processed"], 0.42, places=2)
        self.assertTrue(d["pressure_cap"])
        self.assertEqual(d["tilt"][0], 10)
        self.tablet("release", 220, 300)
        img = o.grab().toImage()
        self.assertFalse(img.isNull())
        o._capturing = True
        self.assertFalse(o._diag_rect().isEmpty())
        o._capturing = False
        o.toggle_input_diag()
        self.assertFalse(o.show_input_diag)

    def test_devices_are_logged_once(self):
        with mock.patch("crit_overlay.overlay.logger") as log:
            self.pen_stroke(0.5, n=5)
            self.pen_stroke(0.5, n=5)
        seen = [c for c in log.info.call_args_list if "Input seen" in str(c)]
        self.assertEqual(len(seen), 1, seen)

    def test_export_full_resolution_on_a_fitted_capture(self):
        o = self.o
        o.set_margin_size(150)
        self.capture(QRect(0, 0, 1920, 1200))
        o._set_size(12)
        self.draw_line((900, 600), (1000, 600))
        self.assertAlmostEqual(o.history.strokes[-1].size, 12 / o.capture.scale, places=4)
        img = o.capture_export().toImage()
        reds = {y for y in range(img.height()) for x in range(0, img.width(), 7)
                if img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() < 80}
        self.assertGreaterEqual(len(reds), int(12 / o.capture.scale * o.capture.dpr) - 3)


if __name__ == "__main__":
    unittest.main()
