"""Optional libmypaint engine (comparison prototype). Skipped unless
crit_overlay/mypaint/ has been built by third_party/libmypaint."""

import unittest

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from crit_overlay import dab  # noqa: E402
from crit_overlay import mypaint_engine as mp  # noqa: E402
from tests.test_capture import CaptureCase  # noqa: E402

APP = qt_env.app


@unittest.skipUnless(mp.available(), "libmypaint not built")
class MypaintTests(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_smoothing(0)
        o._set_size(14)
        self.key = dict((label, k) for k, label in mp.brushes())["Liner"]
        o._set_brush_preset(self.key)

    def red(self, x, y):
        r, g, b, _a = self.painted(x, y)
        return r > 150 and g < 120

    def test_stroke_paints_caches_and_replays(self):
        o = self.o
        self.draw_line((200, 300), (600, 300), n=40)
        s = o.history.strokes[-1]
        self.assertTrue(mp.is_spec(s.brush))
        self.assertEqual(len(s.dtimes), len(s.points))
        self.assertTrue(self.red(400, 300))
        dpr = o._canvas_dpr()
        cached = dab.layer_cache.get(s, dpr)
        self.assertIsNotNone(cached, "live surface becomes the committed layer")
        replay = mp.stroke_layer(s, dpr)
        self.assertEqual(cached[1], replay[1])
        self.assertEqual(cached[0].toImage(), replay[0].toImage(), "replay is exact")

    def test_undo_redo_eraser_and_rebuild(self):
        o = self.o
        self.draw_line((200, 300), (600, 300), n=40)
        o.undo()
        self.assertFalse(self.red(400, 300))
        o.redo()
        self.assertTrue(self.red(400, 300))
        o._set_mode("eraser")
        o._set_size(30)
        self.draw_line((400, 250), (400, 350))
        self.assertTrue(isinstance(o.history.strokes[-1].brush, dab.BrushSpec), "eraser stays a dab eraser")
        self.assertFalse(self.red(400, 300))
        dab.layer_cache.clear()
        o._rebuild_canvas()  # cold: replays the MyPaint stroke
        self.assertTrue(self.red(300, 300))
        self.assertFalse(self.red(400, 300))

    def test_export_and_popup(self):
        o = self.o
        self.capture(QRect(50, 60, 600, 400))
        a = o.capture.to_view(o._to_doc(o.capture.to_view(o._doc_outer().center())))
        self.draw_line((a.x() - 100, a.y()), (a.x() + 100, a.y()), n=30)
        img = o.capture_export().toImage()
        reds = sum(1 for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)
                   if img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() < 80)
        self.assertGreater(reds, 20)
        o._set_mode("brush")
        o._on_brush_button()
        self.assertIn(self.key, o._brush_popup.buttons)
        self.assertTrue(o._brush_popup.tuning.isHidden())
        o._brush_popup.close()


if __name__ == "__main__":
    unittest.main()
