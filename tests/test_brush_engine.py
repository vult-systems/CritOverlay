"""Raster dab brush engine (exp/shape-tools prototype)."""

import math
import unittest

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QPointF, QRect  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from crit_overlay import constants as C  # noqa: E402
from crit_overlay import dab  # noqa: E402
from crit_overlay.models import Stroke  # noqa: E402
from tests.test_capture import CaptureCase  # noqa: E402

APP = qt_env.app


def make(key, pts, size=12.0, taper=False, color=QColor(0, 0, 0)):
    s = Stroke(color, size, 1.0)
    s.brush = dab.make_spec(key, taper=taper)
    s.points = [(QPointF(x, y), pr) for x, y, pr in pts]
    return s


def line(x0, x1, y, n, pressure=1.0):
    return [(x0 + (x1 - x0) * i / n, y, pressure) for i in range(n + 1)]


def alpha_at(layer, x, y):
    pixmap, origin = layer
    d = pixmap.devicePixelRatio()
    img = pixmap.toImage()
    px, py = int((x - origin.x()) * d), int((y - origin.y()) * d)
    if not img.valid(px, py):
        return 0.0
    return img.pixelColor(px, py).alphaF()


class EngineTests(unittest.TestCase):
    def test_dab_spacing_is_independent_of_input_speed(self):
        slow = dab.all_dabs(make("pen", line(0, 300, 50, 300)))
        fast = dab.all_dabs(make("pen", line(0, 300, 50, 6)))
        self.assertAlmostEqual(len(slow), len(fast), delta=2)
        steps = [b[0] - a[0] for a, b in zip(fast, fast[1:])]
        self.assertLess(max(steps) - min(steps), 0.01, "even steps along a straight line")

    def test_live_segments_match_full_render(self):
        s = make("pencil", [(10 + i * 7, 40 + 15 * math.sin(i / 3), 0.3 + i / 40) for i in range(25)])
        r = dab.DabRenderer(s)
        live = r.start()
        for i in range(len(s.points) - 1):
            live.extend(r.segment(i))
        self.assertEqual(live, dab.all_dabs(s, final=False))

    def test_pressure_drives_diameter_and_soft_flow(self):
        pen = dab.PRESETS["pen"]
        self.assertLess(dab.diameter(pen, 20, 0.1), dab.diameter(pen, 20, 1.0) * 0.3)
        self.assertAlmostEqual(dab.diameter(pen, 20, 1.0), 20)
        soft = dab.PRESETS["soft"]
        self.assertLess(dab._flow(soft, 0.1, 0.2), dab._flow(soft, 0.1, 1.0))
        self.assertEqual(dab._flow(pen, 0.07, 0.2), 1.0, "pen flow ignores pressure")

    def test_flow_density_does_not_depend_on_spacing(self):
        values = []
        for spacing in (0.04, 0.2):
            s = make("soft", line(0, 200, 50, 40), size=30)
            s.brush = dab.make_spec("soft", spacing=spacing)
            values.append(alpha_at(dab.freehand_layer(s, 1.0, cache=False), 100, 50))
        self.assertAlmostEqual(values[0], values[1], delta=0.12)

    def test_hardness_changes_the_edge(self):
        hard = make("pen", line(0, 200, 50, 20), size=30)
        hard.brush = dab.make_spec("pen", hardness=1.0)
        soft = make("pen", line(0, 200, 50, 20), size=30)
        soft.brush = dab.make_spec("pen", hardness=0.0)
        edge_hard = alpha_at(dab.freehand_layer(hard, 1.0, cache=False), 100, 50 + 11)
        edge_soft = alpha_at(dab.freehand_layer(soft, 1.0, cache=False), 100, 50 + 11)
        self.assertGreater(edge_hard, 0.9)
        self.assertLess(edge_soft, 0.6)

    def test_taper_only_without_pressure(self):
        tapered = dab.all_dabs(make("pen", line(0, 300, 50, 30), taper=True))
        plain = dab.all_dabs(make("pen", line(0, 300, 50, 30)))
        self.assertLess(tapered[0][2], tapered[len(tapered) // 2][2] * 0.4)
        self.assertLess(tapered[-1][2], tapered[len(tapered) // 2][2] * 0.4)
        self.assertAlmostEqual(plain[0][2], plain[len(plain) // 2][2])
        tap = dab.all_dabs(make("pen", [(5, 5, 1.0)], taper=True))
        self.assertEqual(len(tap), 1)
        self.assertAlmostEqual(tap[0][2], 12.0, msg="a tap is a full dot")

    def test_same_logical_width_at_any_dpr(self):
        widths = []
        for dpr in (1.0, 2.0):
            layer = dab.freehand_layer(make("pen", line(0, 200, 50, 20), size=20), dpr, cache=False)
            covered = [y for y in range(30, 71) if alpha_at(layer, 100, y + 0.25) > 0.5]
            widths.append(len(covered))
        self.assertAlmostEqual(widths[0], widths[1], delta=1)
        self.assertAlmostEqual(widths[0], 20, delta=2)

    def test_layer_cache(self):
        s = make("pen", line(0, 100, 10, 10))
        a = dab.freehand_layer(s, 1.0)
        self.assertIs(dab.freehand_layer(s, 1.0), a)
        self.assertIsNot(dab.freehand_layer(s, 2.0), a)

    def test_log_size_mapping(self):
        for size in (0.5, 1, 2.5, 12, 80, 160):
            self.assertAlmostEqual(dab.slider_to_size(dab.size_to_slider(size)), size, delta=size * 0.01)
        small = dab.size_to_slider(2) - dab.size_to_slider(1)
        large = dab.size_to_slider(160) - dab.size_to_slider(80)
        self.assertEqual(small, large, "equal ratios get equal travel")
        self.assertGreater(small, 100)
        self.assertGreater(dab.step_size(1.0, 1), 1.0)
        self.assertLess(dab.step_size(1.0, -1), 1.0)


class OverlayBrushTests(CaptureCase):
    def setUp(self):
        super().setUp()
        self.o._set_color(QColor(255, 0, 0))
        self.o._set_opacity(100)
        self.o._set_smoothing(0)

    def test_mouse_stroke_uses_preset_and_undoes(self):
        o = self.o
        o._set_brush_preset("pencil")
        self.draw_line((200, 300), (500, 300))
        s = o.history.strokes[-1]
        self.assertEqual(s.brush.preset, "pencil")
        self.assertTrue(s.brush.taper, "mouse strokes taper")
        self.assertGreater(self.painted(350, 300)[0], 120)
        o.undo()
        self.assertEqual(len(o.history), 0)
        o.redo()
        self.assertGreater(self.painted(350, 300)[0], 120)

    def test_pen_pressure_and_live_tail(self):
        o = self.o
        o._set_brush_preset("pen")
        o._set_size(30)
        self.pen("press", 200, 400, 0.2)
        for i in range(1, 30):
            self.pen("move", 200 + i * 10, 400, min(1.0, 0.2 + i * 0.05))
        self.assertIsNotNone(o._live_tail, "provisional tail while drawing")
        self.assertIsNotNone(o.grab())
        self.pen("release", 490, 400)
        s = o.history.strokes[-1]
        self.assertFalse(s.brush.taper, "real pressure, no forced taper")
        pressures = [pr for _p, pr in s.points]
        self.assertLess(pressures[0], 0.5)
        self.assertGreater(pressures[-1], 0.9)
        self.assertIsNone(o._live_tail)

    def test_pen_up_layer_matches_a_full_render(self):
        o = self.o
        o._set_brush_preset("soft")
        o._set_size(24)
        self.pen("press", 200, 400, 0.3)
        for i in range(1, 25):
            self.pen("move", 200 + i * 9, 400 + 30 * math.sin(i / 4), 0.3 + i * 0.02)
        self.pen("release", 420, 400)
        s = o.history.strokes[-1]
        dpr = o._canvas_dpr()
        cached = dab.freehand_layer(s, dpr)
        fresh = dab.freehand_layer(s, dpr, cache=False)
        a, b = cached[0].toImage(), fresh[0].toImage()
        dx = round((fresh[1].x() - cached[1].x()) * dpr)
        dy = round((fresh[1].y() - cached[1].y()) * dpr)
        worst = 0
        for y in range(0, b.height(), 3):
            for x in range(0, b.width(), 3):
                if a.valid(x + dx, y + dy):
                    other = a.pixelColor(x + dx, y + dy).alpha()
                else:
                    other = 0
                worst = max(worst, abs(other - b.pixelColor(x, y).alpha()))
        self.assertLessEqual(worst, 4, "live-built layer equals a fresh render (8-bit rounding)")

    def test_classic_preset_keeps_the_old_engine(self):
        o = self.o
        o._set_brush_preset(dab.CLASSIC)
        self.draw_line((200, 300), (500, 300))
        self.assertIsNone(o.history.strokes[-1].brush)
        self.assertGreater(self.painted(350, 300)[0], 200)

    def test_eraser_size_is_independent(self):
        o = self.o
        o._set_mode("brush")
        o._set_size(6)
        o._set_mode("eraser")
        o._set_size(40)
        self.assertEqual(o.size_value_label.text(), "40px")
        o._set_mode("brush")
        self.assertEqual(o.brush_size, 6)
        self.assertEqual(o.size_value_label.text(), "6.0px")
        self.draw_line((200, 300), (500, 300))
        o._set_mode("eraser")
        self.draw_line((350, 250), (350, 350))
        eraser = o.history.strokes[-1]
        self.assertTrue(eraser.eraser)
        self.assertEqual(eraser.size, 40)
        self.assertNotEqual(self.painted(350, 300)[:3], (255, 0, 0))
        self.assertEqual(self.painted(250, 300)[:3], (255, 0, 0))

    def test_tuning_is_remembered_per_preset(self):
        o = self.o
        o._set_brush_preset("soft")
        o._set_brush_tuning(hardness=0.4, spacing=0.2)
        o._set_brush_preset("pen")
        self.assertEqual(o._tuning_for("pen"), (dab.PRESETS["pen"].hardness, dab.PRESETS["pen"].spacing))
        o._set_brush_preset("soft")
        self.draw_line((200, 300), (500, 300))
        spec = o.history.strokes[-1].brush
        self.assertEqual((spec.hardness, spec.spacing), (0.4, 0.2))
        o.save_settings_now()
        from crit_overlay.config import AppConfig
        loaded = AppConfig(o._config.path).load()
        self.assertEqual(loaded["brush_preset"], "soft")
        self.assertEqual(loaded["brush_tuning"]["soft"], (0.4, 0.2))

    def test_size_preview_and_ring(self):
        o = self.o
        o._set_mode("brush")
        o._set_brush_preset("soft")
        self.mouse("move", 600, 600)
        o._set_size(50)
        self.assertGreater(o._size_preview_until, 0)
        self.assertAlmostEqual(o._ring_diameter(), 50)
        img = o.grab().toImage()
        self.assertIsNotNone(img)
        o._capturing = True
        self.assertNotEqual(self.painted(600, 600)[:3], (255, 0, 0), "no preview in screenshots")
        o._capturing = False

    def test_export_full_resolution_and_capture_scale(self):
        o = self.o
        o.set_margin_size(150)
        self.capture(QRect(0, 0, 1920, 1200))
        scale = o.capture.scale
        o._set_size(12)
        self.draw_line((900, 600), (1000, 600))
        s = o.history.strokes[-1]
        self.assertAlmostEqual(s.size, 12 / scale, places=4)
        img = o.capture_export().toImage()
        # Export is at the image's own resolution; the stroke is 12 / scale
        # image pixels wide, so wider than on screen.
        red_rows = {y for y in range(img.height()) for x in range(0, img.width(), 7)
                    if img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() < 80}
        self.assertGreaterEqual(len(red_rows), int(12 / scale * o.capture.dpr) - 3)

    def test_presets_popup(self):
        o = self.o
        o._set_mode("brush")
        o._on_brush_button()  # already the brush: opens the presets
        popup = o._brush_popup
        self.assertIsNotNone(popup)
        popup.buttons["marker"].click()
        self.assertEqual(o.brush_preset, "marker")
        popup.hardness.setValue(30)
        self.assertAlmostEqual(o._tuning_for("marker")[0], 0.3)
        popup.buttons[dab.CLASSIC].click()
        self.assertTrue(popup.tuning.isHidden(), "no tuning for the classic engine")
        popup.close()

    def test_settings_size_is_fractional(self):
        o = self.o
        o._set_size(1.37)
        self.assertEqual(o.brush_size, 1.37)
        self.assertEqual(o.size_value_label.text(), "1.4px")
        o._set_size(9999)
        self.assertEqual(o.brush_size, C.MAX_SIZE)


if __name__ == "__main__":
    unittest.main()
