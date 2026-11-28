"""Esc backs out one step at a time and otherwise hides; it never discards a
capture or switches to full-monitor drawing. Clear all is on the toolbar
and undoable. Plus the responsiveness changes: live layer growth, stroke
timing log, pen gestures off."""

import unittest
from unittest import mock

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QTabletEvent  # noqa: E402

from crit_overlay import brush, native_windows  # noqa: E402
from tests.test_capture import PEN, CaptureCase  # noqa: E402

APP = qt_env.app


class EscClearTests(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_smoothing(0)

    def drag(self, a, b, steps=4):
        self.mouse("press", *a)
        for i in range(1, steps + 1):
            self.mouse("move", a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)

    # -- Esc ----------------------------------------------------------------------

    def test_esc_cancels_a_move_in_progress(self):
        o = self.o
        o._set_mode("rect")
        self.drag((200, 200), (400, 300))
        self.mouse("release", 400, 300)
        rect = o.history.strokes[-1]
        o._set_mode("select")
        self.drag((300, 200), (360, 260))  # mid-drag, not released
        self.assertIsNotNone(o._floating)
        undo_depth = len(o.history.undo_stack)
        o._escape()
        self.mouse("release", 360, 260)
        self.assertIs(o.history.strokes[-1], rect, "back where it was")
        self.assertEqual(len(o.history.undo_stack), undo_depth, "nothing recorded")
        self.assertIsNone(o._floating)

    def test_esc_deselects_before_anything_else(self):
        o = self.o
        o._set_mode("rect")
        self.drag((200, 200), (400, 300))
        self.mouse("release", 400, 300)
        o._set_mode("select")
        self.mouse("press", 300, 200)
        self.mouse("release", 300, 200)
        with mock.patch.object(o, "dismiss") as dismiss:
            o._escape()
            self.assertIsNone(o.selection)
            dismiss.assert_not_called()
            o._escape()
            dismiss.assert_called_once()

    def test_esc_hides_keeping_capture_and_history_and_show_restores(self):
        o = self.o
        o.select_on_show = True
        o.set_tray_available(True)
        self.capture(QRect(50, 60, 400, 300))
        self.draw_line((900, 600), (1000, 600))
        cap, strokes = o.capture, list(o.history.strokes)
        o.show()
        o._escape()
        self.assertFalse(o.isVisible())
        self.assertIs(o.capture, cap)
        o.show_overlay()
        self.addCleanup(o.hide)
        APP.processEvents()
        self.assertIs(o.capture, cap)
        self.assertEqual(o.history.strokes, strokes)
        self.assertFalse(o._selecting, "the session resumes; no new selection")
        o.undo()
        self.assertEqual(len(o.history), len(strokes) - 1, "undo history kept")

    def test_r_then_esc_returns_to_the_capture(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        self.draw_line((900, 600), (1000, 600))
        cap, strokes = o.capture, list(o.history.strokes)
        o.begin_region_selection()
        self.assertIsNone(o.capture)
        with mock.patch.object(o, "dismiss") as dismiss:
            o._escape()
        dismiss.assert_called_once()
        self.assertIs(o.capture, cap)
        self.assertEqual(o.history.strokes, strokes)
        self.assertFalse(o._selecting)

    def test_esc_never_switches_to_full_monitor_drawing(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        with mock.patch.object(o, "dismiss"):
            for _ in range(4):
                o._escape()
        self.assertIsNotNone(o.capture)
        self.assertFalse(o._selecting)

    def test_a_new_capture_replaces_the_old_one_deliberately(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        first = o.capture
        self.capture(QRect(100, 100, 300, 200))
        self.assertIsNot(o.capture, first)
        self.assertIsNone(o._stashed_session)
        self.assertEqual(len(o.history), 0)

    def test_a_deliberate_click_still_means_full_monitor(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        o.begin_region_selection()
        self.mouse("press", 500, 500)
        self.mouse("release", 500, 500)
        self.assertIsNone(o.capture)
        self.assertIsNone(o._stashed_session)

    def test_text_editing_esc_still_cancels_the_edit(self):
        o = self.o
        o._set_mode("text")
        self.mouse("press", 600, 600)
        self.mouse("release", 600, 600)
        o.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, "a"))
        with mock.patch.object(o, "dismiss") as dismiss:
            o.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        dismiss.assert_not_called()
        self.assertIsNone(o._text_edit)
        self.assertEqual(len(o.history), 0)

    # -- Clear all -------------------------------------------------------------------

    def test_clear_all_is_on_the_toolbar_and_undoable(self):
        o = self.o
        self.assertIs(o.clear_btn.parentWidget(), o.panel)
        self.assertIn("Clear all", o.clear_btn.toolTip())
        self.assertFalse(o.clear_btn.icon().isNull())
        top = o.panel.layout().itemAt(0).layout()
        order = [top.itemAt(i).widget() for i in range(top.count())]
        self.assertLess(order.index(o.redo_btn), order.index(o.clear_btn))
        self.assertLess(order.index(o.clear_btn), order.index(o.capture_btn))
        self.capture(QRect(50, 60, 400, 300))
        cap = o.capture
        self.draw_line((900, 600), (1000, 600))
        self.draw_line((900, 650), (1000, 650))
        o.clear_btn.click()
        self.assertEqual(len(o.history), 0)
        self.assertIs(o.capture, cap, "the captured image stays")
        o.undo()
        self.assertEqual(len(o.history), 2, "one undo brings everything back")
        self.assertEqual(self.painted(950, 600)[:3], (255, 0, 0))

    # -- responsiveness ----------------------------------------------------------------

    def tablet(self, kind, x, y, p=0.6):
        o = self.o
        etype = {"press": QEvent.Type.TabletPress, "move": QEvent.Type.TabletMove,
                 "release": QEvent.Type.TabletRelease}[kind]
        down = kind != "release"
        ev = QTabletEvent(etype, PEN, QPointF(x, y), QPointF(o.mapToGlobal(QPoint(int(x), int(y)))),
                          p if down else 0.0, 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier,
                          Qt.MouseButton.LeftButton if kind != "move" else Qt.MouseButton.NoButton,
                          Qt.MouseButton.LeftButton if down else Qt.MouseButton.NoButton)
        o.tabletEvent(ev)

    def test_live_layer_grows_in_steps_not_every_event(self):
        calls = []
        real = brush.new_layer
        with mock.patch.object(brush, "new_layer", side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)):
            self.tablet("press", 100, 600)
            for i in range(1, 300):
                self.tablet("move", 100 + i * 5, 600)
            # Count only the live layer's growth (the merge), not the
            # canvas or piece reads.
            grow = len(calls)
            self.tablet("release", 1600, 600)
        self.assertLess(grow, 40, f"{grow} reallocations for 300 events")

    def test_stroke_log_reports_input_timing(self):
        with mock.patch("crit_overlay.overlay.logger") as log:
            self.tablet("press", 200, 300)
            for i in range(1, 10):
                self.tablet("move", 200 + i * 10, 300)
            self.tablet("release", 300, 300)
        line = [c for c in log.info.call_args_list if str(c.args[0]).startswith("Stroke via")][0]
        text = line.args[0] % line.args[1:]
        self.assertIn("first move after", text)
        self.assertIn("events/s", text)

    def test_pen_gestures_are_turned_off_when_shown(self):
        o = self.o
        with mock.patch.object(QGuiApplication, "platformName", return_value="windows"), \
                mock.patch.object(native_windows, "disable_pen_gestures", return_value=True) as off:
            o.show()
        self.addCleanup(o.hide)
        off.assert_called_with(int(o.winId()))


if __name__ == "__main__":
    unittest.main()
