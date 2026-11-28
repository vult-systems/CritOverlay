"""Shape tools (exp/shape-tools prototype): rectangle, ellipse, line, arrow."""

import math
import unittest

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from crit_overlay import brush  # noqa: E402
from crit_overlay.models import SHAPES, Stroke  # noqa: E402
from tests.test_capture import GRAB_COLOR, CaptureCase  # noqa: E402


class ConstrainTests(unittest.TestCase):
    def test_rect_becomes_square_in_drag_direction(self):
        end = brush.constrain_end("rect", QPointF(10, 10), QPointF(-30, 20))
        self.assertEqual((end.x(), end.y()), (-30, 50))

    def test_line_snaps_to_45_degrees(self):
        end = brush.constrain_end("line", QPointF(0, 0), QPointF(100, 70))
        self.assertAlmostEqual(math.degrees(math.atan2(end.y(), end.x())), 45, places=6)
        end = brush.constrain_end("arrow", QPointF(0, 0), QPointF(100, 10))
        self.assertAlmostEqual(end.y(), 0, places=6)

    def test_every_shape_renders(self):
        for shape in SHAPES:
            s = Stroke(QColor(255, 0, 0), 6, 1.0, shape=shape)
            s.points = [(QPointF(0, 0), 0.65), (QPointF(80, 50), 0.65)]
            self.assertIsNotNone(brush.render_shape_layer(s, 1.5), shape)


class ShapeOverlayTests(CaptureCase):
    def drag(self, a, b, shift=False):
        o = self.o
        self.mouse("press", *a)
        self.mouse("move", (a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if shift:
            from unittest import mock
            with mock.patch.object(type(qt_env.app), "keyboardModifiers",
                                   staticmethod(lambda: Qt.KeyboardModifier.ShiftModifier)):
                self.mouse("move", *b)
        else:
            self.mouse("move", *b)
        self.assertIsNotNone(o._live_layer, "live preview while dragging")
        self.mouse("release", *b)

    def ink(self, x, y):
        return self.painted(x, y)

    def setUp(self):
        super().setUp()
        self.o._set_color(QColor(255, 0, 0))
        self.o._set_opacity(100)
        self.o._set_size(8)

    def test_each_shape_commits_one_undoable_stroke(self):
        o = self.o
        for i, shape in enumerate(SHAPES):
            o._set_mode(shape)
            self.drag((200, 200 + i * 120), (400, 300 + i * 120))
            self.assertEqual(len(o.history), i + 1)
            self.assertEqual(o.history.strokes[-1].shape, shape)
        # Rectangle edge is inked, its middle isn't (outline only).
        self.assertGreater(self.ink(300, 200)[0], 200)
        self.assertNotEqual(self.ink(300, 250)[:3], (255, 0, 0))
        o.undo()
        self.assertEqual(len(o.history), 3)
        o.redo()
        self.assertEqual(len(o.history), 4)

    def test_click_without_drag_commits_nothing(self):
        o = self.o
        o._set_mode("ellipse")
        self.mouse("press", 300, 300)
        self.mouse("release", 300, 300)
        self.assertEqual(len(o.history), 0)

    def test_shift_constrains_square(self):
        o = self.o
        o._set_mode("rect")
        self.drag((200, 200), (300, 260), shift=True)
        (a, _), (b, _) = o.history.strokes[-1].points
        self.assertAlmostEqual(abs(b.x() - a.x()), abs(b.y() - a.y()))

    def test_eraser_erases_a_shape(self):
        o = self.o
        o._set_mode("line")
        self.drag((200, 300), (500, 300))
        self.assertGreater(self.ink(350, 300)[0], 200)
        o._set_mode("eraser")
        o._set_size(40)
        o._set_smoothing(0)
        self.draw_line((350, 250), (350, 350))
        self.assertLess(self.ink(350, 300)[3], 10)

    def test_shape_button_selects_last_shape_and_u_cycles(self):
        o = self.o
        o.shape_btn.click()
        self.assertEqual(o.mode, "rect")
        o._cycle_shape()
        self.assertEqual(o.mode, "ellipse")
        o._set_mode("brush")
        self.assertFalse(o.shape_btn.isChecked())
        o._cycle_shape()
        self.assertEqual(o.mode, "ellipse")

    def test_shapes_follow_capture_and_export_full_resolution(self):
        o = self.o
        o.set_margin_size(100)
        o.set_margin_color("white")
        self.capture(QRect(0, 0, 1920, 1200))  # fitted, scale < 1
        self.assertLess(o.capture.scale, 1.0)
        o._set_mode("line")
        c = o.capture.to_view(QPointF(960, 600))
        self.drag((c.x() - 80, c.y()), (c.x() + 80, c.y()))
        stroke = o.history.strokes[-1]
        self.assertAlmostEqual(stroke.points[0][0].y(), 600, delta=1, msg="stored in image coordinates")
        shot = o.capture_export().toImage()
        m = round(100 * self.dpr)
        mid = shot.pixelColor(m + 1440, m + 900)
        self.assertGreater(mid.red(), 200)
        self.assertLess(mid.green(), 80)
        self.assertEqual(shot.pixelColor(m + 10, m + 10).getRgb()[:3], GRAB_COLOR.getRgb()[:3])
        # Pan: the shape moves with the image.
        before = o.capture.to_view(stroke.points[0][0])
        self.key("press", Qt.Key.Key_Space)
        self.mouse("press", 960, 600)
        self.mouse("move", 1010, 640)
        self.mouse("release", 1010, 640)
        self.key("release", Qt.Key.Key_Space)
        after = o.capture.to_view(stroke.points[0][0])
        self.assertAlmostEqual(after.x() - before.x(), 50, delta=1)
        self.assertEqual(len(o.history), 1)


if __name__ == "__main__":
    unittest.main()
