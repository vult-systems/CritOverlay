"""Pen pressure from the very first stroke: foreground at show, capture,
pass-through and monitor switches; no state carried between strokes; light
pressure kept; stroke logging that tells a no-pressure stroke apart."""

import unittest
from unittest import mock

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QTabletEvent  # noqa: E402

from crit_overlay import brushes  # noqa: E402
from crit_overlay import native_windows  # noqa: E402
from crit_overlay import mypaint_engine as mp  # noqa: E402
from tests.test_capture import PEN, CaptureCase  # noqa: E402

APP = qt_env.app


class PenLifecycleTests(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_smoothing(0)
        o._set_brush_preset("studio_pen")

    def tablet(self, kind, x, y, pressure=0.6):
        o = self.o
        etype = {"press": QEvent.Type.TabletPress, "move": QEvent.Type.TabletMove,
                 "release": QEvent.Type.TabletRelease}[kind]
        down = kind != "release"
        glob = QPointF(o.mapToGlobal(QPoint(int(x), int(y))))
        ev = QTabletEvent(etype, PEN, QPointF(x, y), glob, pressure if down else 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                          Qt.KeyboardModifier.NoModifier,
                          Qt.MouseButton.LeftButton if kind != "move" else Qt.MouseButton.NoButton,
                          Qt.MouseButton.LeftButton if down else Qt.MouseButton.NoButton)
        o.tabletEvent(ev)

    def pen_stroke(self, pressure=0.3, x=200, y=300, n=15):
        self.tablet("press", x, y, pressure)
        for i in range(1, n):
            self.tablet("move", x + i * 10, y, pressure)
        self.tablet("release", x + n * 10, y)
        return self.o.history.strokes[-1]

    def windows_platform(self):
        return mock.patch.object(QGuiApplication, "platformName", return_value="windows")

    # -- foreground ------------------------------------------------------------

    def test_overlay_takes_the_foreground_when_shown_and_on_new_captures(self):
        o = self.o
        o.show()
        self.addCleanup(o.hide)
        with self.windows_platform(), mock.patch.object(native_windows, "claim_foreground",
                                                        return_value=True) as claim:
            o.show_overlay()
            self.capture(QRect(50, 60, 400, 300))
            o.toggle_pass_through()
            o.toggle_pass_through()
        reasons = [c for c in claim.call_args_list]
        self.assertGreaterEqual(len(reasons), 3, "show, new capture, pass-through off")
        for call in reasons:
            self.assertEqual(call.args[0], int(o.winId()))

    def test_a_refused_foreground_is_logged(self):
        o = self.o
        o.show()
        self.addCleanup(o.hide)
        with self.windows_platform(), \
                mock.patch.object(native_windows, "claim_foreground", return_value=False), \
                mock.patch.object(native_windows, "foreground_info", return_value=(1, 2, "Photoshop.exe")), \
                mock.patch("crit_overlay.overlay.logger") as log:
            self.assertFalse(o.claim_foreground("show"))
        self.assertIn("Photoshop.exe", str(log.warning.call_args))

    # -- first strokes ------------------------------------------------------------

    def test_the_first_pen_stroke_has_pressure(self):
        s = self.pen_stroke(0.3)
        self.assertTrue(mp.is_spec(s.brush))
        self.assertEqual(len(self.o.history), 1)
        self.assertTrue(all(abs(pr - 0.3) < 0.01 for _p, pr in s.points), "pressure from the first event on")

    def test_light_first_contact_is_pressure_not_a_missing_sensor(self):
        o = self.o
        s = self.pen_stroke(0.03)
        self.assertFalse(o._no_pressure_devices)
        self.assertAlmostEqual(s.points[0][1], 0.03, delta=0.005)

    def test_strokes_carry_no_state_between_them(self):
        o = self.o
        first = self.pen_stroke(0.5, y=300)
        o.undo()
        second = self.pen_stroke(0.5, y=300)
        dpr = o._canvas_dpr()
        self.assertEqual(brushes.freehand_layer(first, dpr, cache=False)[0].toImage(),
                         brushes.freehand_layer(second, dpr, cache=False)[0].toImage())

    def test_pressure_after_hide_show_capture_and_monitor_switch(self):
        o = self.o
        o.show()
        self.addCleanup(o.hide)
        o.hide_to_tray()
        o.show_overlay()
        self.assertLess(self.pen_stroke(0.3).points[-1][1], 0.5)
        self.capture(QRect(50, 60, 400, 300))
        c = o.capture.to_view(o.capture.image_rect().center())
        self.assertLess(self.pen_stroke(0.3, x=c.x() - 70, y=c.y()).points[-1][1], 0.5)
        o.end_capture()
        o.move_to_next_screen()
        self.assertLess(self.pen_stroke(0.3).points[-1][1], 0.5)

    def test_a_pen_wrongly_marked_pressureless_recovers(self):
        o = self.o
        self.pen_stroke(0.0, n=12)  # a whole stroke at zero: marked pressure-less
        self.assertEqual(len(o._no_pressure_devices), 1)
        s = self.pen_stroke(0.4)
        self.assertFalse(o._no_pressure_devices, "recovered on the next stroke with pressure")
        self.assertAlmostEqual(s.points[-1][1], 0.4, delta=0.01)

    # -- logging -----------------------------------------------------------------

    def test_strokes_are_logged_and_a_pressureless_one_with_its_events(self):
        with mock.patch("crit_overlay.overlay.logger") as log:
            self.pen_stroke(0.3)
            self.draw_line((200, 500), (500, 500))  # mouse, after a pen was seen
        lines = [c.args[0] % c.args[1:] if len(c.args) > 1 else str(c.args[0]) for c in log.info.call_args_list]
        self.assertTrue(any("Stroke via tablet" in line for line in lines))
        flagged = [line for line in lines if "Stroke via mouse" in line]
        self.assertTrue(flagged and "pen seen, but no pressure" in flagged[0])
        self.assertTrue(any("MouseButtonPress" in line for line in lines), "the event sequence follows")

    def test_only_the_first_strokes_after_a_show_are_summarized(self):
        with mock.patch("crit_overlay.overlay.logger") as log:
            for i in range(8):
                self.pen_stroke(0.3, y=200 + i * 20, n=4)
        summaries = [c for c in log.info.call_args_list if str(c.args[0]).startswith("Stroke via")]
        self.assertEqual(len(summaries), 5)


if __name__ == "__main__":
    unittest.main()
