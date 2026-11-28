"""Brush consistency: size normalization, pressure response, tilt,
stabilization across engines, soft eraser, purpose catalog."""

import statistics
import unittest

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QPoint, QPointF, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QTabletEvent  # noqa: E402

from crit_overlay import brush_popup, dab  # noqa: E402
from crit_overlay import mypaint_engine as mp  # noqa: E402
from crit_overlay.models import Stroke  # noqa: E402
from tests.test_capture import PEN, CaptureCase  # noqa: E402

APP = qt_env.app


def visible_width(key, size, dpr=1.0):
    """Median width at half the stroke's own peak opacity, full pressure."""
    s = Stroke(QColor(0, 0, 0), size, 1.0)
    n = 120
    s.points = [(QPointF(50 + i * 2, 200), 1.0) for i in range(n + 1)]
    if key.startswith(mp.PREFIX):
        s.brush = mp.spec(key[len(mp.PREFIX):])
        s.dtimes = [0.0] + [0.005] * n
        pix, origin = mp.stroke_layer(s, dpr)
    else:
        s.brush = dab.make_spec(key)
        pix, origin = dab.freehand_layer(s, dpr, cache=False)
    img = pix.toImage()
    widths = []
    for x in range(140, 220, 5):
        col = [img.pixelColor(x - origin.x(), y).alphaF() for y in range(img.height())]
        peak = max(col)
        covered = [y for y, a in enumerate(col) if a >= peak * 0.5]
        widths.append(covered[-1] - covered[0] + 1 if covered else 0)
    return statistics.median(widths)


class SizeTests(unittest.TestCase):
    def test_recommended_brushes_match_their_size(self):
        keys = [k for _p, k, _l, rec in brush_popup.entries() if rec]
        self.assertGreaterEqual(len(keys), 4)
        for key in keys:
            for size in (8, 24):
                with self.subTest(key=key, size=size):
                    self.assertAlmostEqual(visible_width(key, size), size, delta=max(2, size * 0.15))


class OverlayRefineTests(CaptureCase):
    def setUp(self):
        super().setUp()
        self.o._set_color(QColor(255, 0, 0))
        self.o._set_opacity(100)
        self.o._set_smoothing(0)

    def tablet(self, kind, x, y, pressure=0.6, xtilt=0.0, ytilt=0.0):
        o = self.o
        etype = {"press": QEvent.Type.TabletPress, "move": QEvent.Type.TabletMove,
                 "release": QEvent.Type.TabletRelease}[kind]
        down = kind != "release"
        glob = QPointF(o.mapToGlobal(QPoint(int(x), int(y))))
        ev = QTabletEvent(etype, PEN, QPointF(x, y), glob, pressure if down else 0.0, xtilt, ytilt, 0.0, 0.0, 0.0,
                          Qt.KeyboardModifier.NoModifier,
                          Qt.MouseButton.LeftButton if kind != "move" else Qt.MouseButton.NoButton,
                          Qt.MouseButton.LeftButton if down else Qt.MouseButton.NoButton)
        o.tabletEvent(ev)

    def pen_stroke(self, pressure=0.3, n=20, **tilt):
        self.tablet("press", 200, 300, pressure, **tilt)
        for i in range(1, n):
            self.tablet("move", 200 + i * 10, 300, pressure, **tilt)
        self.tablet("release", 200 + n * 10, 300)
        return self.o.history.strokes[-1]

    def test_pressure_curves(self):
        o = self.o
        o._set_brush_preset("pen")
        got = {}
        for name in ("soft", "normal", "firm"):
            o._set_pressure_curve(name)
            s = self.pen_stroke(0.3)
            got[name] = s.points[-1][1]
        self.assertGreater(got["soft"], got["normal"])
        self.assertGreater(got["normal"], got["firm"])
        self.assertAlmostEqual(got["normal"], 0.3, delta=0.02)
        o.save_settings_now()
        from crit_overlay.config import AppConfig
        self.assertEqual(AppConfig(o._config.path).load()["pressure_curve"], "firm")

    def test_mouse_ignores_the_curve(self):
        o = self.o
        o._set_brush_preset("pen")
        o._set_pressure_curve("firm")
        self.draw_line((200, 300), (500, 300))
        self.assertEqual(o.history.strokes[-1].points[-1][1], 1.0)

    @unittest.skipUnless(mp.available(), "libmypaint not built")
    def test_tilt_reaches_mypaint_and_replays(self):
        o = self.o
        key = dict((label, k) for k, label in mp.brushes())["Calligraphy"]
        o._set_brush_preset(key)
        s = self.pen_stroke(0.7, xtilt=30.0, ytilt=-15.0)
        self.assertEqual(len(s.tilts), len(s.points))
        self.assertAlmostEqual(s.tilts[-1][0], 0.5, places=3)
        self.assertAlmostEqual(s.tilts[-1][1], -0.25, places=3)
        dpr = o._canvas_dpr()
        self.assertEqual(dab.layer_cache.get(s, dpr)[0].toImage(), mp.stroke_layer(s, dpr)[0].toImage())

    @unittest.skipUnless(mp.available(), "libmypaint not built")
    def test_stabilizer_applies_to_mypaint(self):
        o = self.o
        o._set_brush_preset(mp.brushes()[1][0])
        o._set_smoothing(60)
        self.mouse("press", 200, 300)
        for i in range(1, 60):
            self.mouse("move", 200 + i * 6, 300 + (12 if i % 2 else -12))  # jittery hand
        self.mouse("release", 560, 300)
        ys = [p.y() for p, _ in o.history.strokes[-1].points]
        self.assertLess(max(ys) - min(ys), 12, "the zigzag is smoothed out")

    def test_soft_eraser_erases_partly_at_light_pressure(self):
        o = self.o
        o._set_brush_preset("pen")
        o._set_size(40)
        self.draw_line((150, 300), (650, 300))
        o._set_mode("eraser")
        o._set_eraser_style("soft")
        o._set_size(40)
        self.tablet("press", 400, 200, 0.25)
        for i in range(1, 20):
            self.tablet("move", 400, 200 + i * 10, 0.25)
        self.tablet("release", 400, 400)
        eraser = o.history.strokes[-1]
        self.assertEqual(eraser.brush.preset, "eraser_soft")
        r, _g, _b, a = self.painted(400, 300)
        self.assertGreater(r, 255 * 0.15, "not erased away")
        self.assertLess(self.painted(400, 300)[1], 250)
        full = self.painted(250, 300)
        self.assertEqual(full[:3], (255, 0, 0))
        o._set_eraser_style("hard")
        self.tablet("press", 300, 200, 0.25)
        for i in range(1, 20):
            self.tablet("move", 300, 200 + i * 10, 0.25)
        self.tablet("release", 300, 400)
        self.assertNotEqual(self.painted(300, 300)[:3], (255, 0, 0))

    def test_eraser_popup_and_brush_popup_by_purpose(self):
        o = self.o
        o._set_mode("eraser")
        o._on_eraser_button()
        o._eraser_popup.buttons["soft"].click()
        self.assertEqual(o.eraser_style, "soft")
        o._set_mode("brush")
        o._on_brush_button()
        popup = o._brush_popup
        recommended = [k for _p, k, _l, rec in brush_popup.entries() if rec]
        for key in recommended:
            self.assertTrue(popup.buttons[key].font().bold())
        popup.pressure_buttons["soft"].click()
        self.assertEqual(o.pressure_curve, "soft")
        popup.buttons["soft"].click()
        self.assertTrue(popup.tuning.isHidden(), "fine-tune is opt-in")
        popup.fine_btn.click()
        self.assertFalse(popup.tuning.isHidden())
        popup.close()
        self.assertEqual(brush_popup.label_for("marker"), "Chisel Marker")


if __name__ == "__main__":
    unittest.main()
