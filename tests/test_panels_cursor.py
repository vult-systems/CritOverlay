"""Panels never turn the pen into a mouse (first stroke after picking a
brush keeps its pressure); the brush cursor is a single transient overlay
that never leaves ghosts; cursor style preference."""

import unittest
from unittest import mock

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent, QTabletEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from crit_overlay import ui_widgets  # noqa: E402
from tests.test_capture import PEN, CaptureCase  # noqa: E402

APP = qt_env.app


class PanelsCursorTests(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_smoothing(0)

    def tablet(self, kind, x, y, p=0.4):
        o = self.o
        etype = {"press": QEvent.Type.TabletPress, "move": QEvent.Type.TabletMove,
                 "release": QEvent.Type.TabletRelease, "hover": QEvent.Type.TabletMove}[kind]
        down = kind in ("press", "move")
        ev = QTabletEvent(etype, PEN, QPointF(x, y), QPointF(o.mapToGlobal(QPoint(int(x), int(y)))),
                          p if down else 0.0, 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier,
                          Qt.MouseButton.LeftButton if kind in ("press", "release") else Qt.MouseButton.NoButton,
                          Qt.MouseButton.LeftButton if down else Qt.MouseButton.NoButton)
        o.tabletEvent(ev)

    # -- panels ------------------------------------------------------------------

    def test_no_panel_is_a_qt_popup(self):
        o = self.o
        o._set_mode("brush")
        o._show_brush_popup(o.preset_btn)
        o._show_color_popup()
        o._show_more_menu()
        o._show_prefs()
        o.size_value_label.open_popup()
        o._show_region_popup()
        panels = [w for w in QApplication.topLevelWidgets()
                  if w is not o and w.windowType() not in (Qt.WindowType.Window, Qt.WindowType.Widget)
                  and isinstance(w, QWidget) and w.objectName() == "Toolbar"]
        self.assertGreaterEqual(len(panels), 5)
        for w in panels:
            with self.subTest(panel=type(w).__name__):
                self.assertNotEqual(w.windowType(), Qt.WindowType.Popup)
                self.assertTrue(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        ui_widgets.close_panels()
        self.assertFalse(any(w.isVisible() for w in panels))

    def test_first_stroke_after_picking_a_brush_keeps_pressure(self):
        o = self.o
        o._set_mode("brush")
        o._show_brush_popup(o.preset_btn)
        o._brush_popup.buttons["ink"].click()
        self.assertTrue(o._brush_popup.isVisible(), "the picker stays open after a pick")
        self.tablet("press", 300, 600, 0.3)
        self.assertFalse(o._brush_popup.isVisible(), "the canvas press closed it")
        for i in range(1, 12):
            self.tablet("move", 300 + i * 10, 600, 0.3)
        self.tablet("release", 420, 600)
        s = o.history.strokes[-1]
        self.assertEqual(s.brush.brush, "kaerhon_v1_inkster_l.myb")
        self.assertAlmostEqual(s.points[-1][1], 0.3, delta=0.01, msg="pen pressure, not mouse")

    def test_only_one_panel_at_a_time_and_esc_closes(self):
        o = self.o
        o._show_color_popup()
        o._show_more_menu()
        self.assertFalse(o._color_popup.isVisible())
        self.assertTrue(o.more_menu.isVisible())
        # Esc closes the panel, through the panel's own filter or the
        # overlay's Esc shortcut, and nothing else happens.
        with mock.patch.object(o, "dismiss") as dismiss:
            o._escape()
        self.assertFalse(o.more_menu.isVisible())
        dismiss.assert_not_called()
        o._show_prefs()
        # The panel's own Esc handling, when it has the key (called directly:
        # in the test suite other overlays' Esc shortcuts are still alive).
        ui_widgets._filter.eventFilter(o.prefs_popup, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                                                Qt.KeyboardModifier.NoModifier))
        self.assertFalse(o.prefs_popup.isVisible(), "the panel's own Esc")

    def test_hiding_closes_panels(self):
        o = self.o
        o.show()
        o._show_prefs()
        o.hide_to_tray()
        self.assertFalse(o.prefs_popup.isVisible())

    # -- cursor ---------------------------------------------------------------------

    def paint(self):
        self.o.grab()  # runs paintEvent, recording where the cursor went

    def test_moving_the_cursor_repaints_where_it_was(self):
        o = self.o
        o._set_mode("brush")
        o._set_size(30)
        self.tablet("hover", 300, 300)
        self.paint()
        old = o._cursor_drawn
        self.assertIsNotNone(old)
        with mock.patch.object(o, "update") as update:
            self.tablet("hover", 900, 700)
        rects = [c.args[0] for c in update.call_args_list if c.args]
        self.assertTrue(any(r.contains(old) for r in rects), "the old cursor area is repainted")
        self.assertTrue(any(r.contains(QPoint(900, 700)) for r in rects))

    def test_pen_down_far_from_the_cursor_clears_it(self):
        o = self.o
        o._set_mode("brush")
        self.tablet("hover", 200, 200)
        self.paint()
        old = o._cursor_drawn
        with mock.patch.object(o, "update") as update:
            self.tablet("press", 1200, 800)
        rects = [c.args[0] for c in update.call_args_list if c.args and isinstance(c.args[0], QRect)]
        self.assertTrue(any(r.contains(old) for r in rects))
        self.tablet("release", 1200, 800)

    def test_fast_strokes_always_clear_the_previous_cursor(self):
        o = self.o
        o._set_mode("brush")
        o._set_size(40)
        self.tablet("press", 100, 400, 0.9)
        self.paint()
        for i in range(1, 30):
            old = o._cursor_drawn
            with mock.patch.object(o, "update") as update:
                self.tablet("move", 100 + i * 45, 400 + (i % 3) * 30, 0.9 - i * 0.02)
            rects = [c.args[0] for c in update.call_args_list if c.args and isinstance(c.args[0], QRect)]
            self.assertTrue(any(r.contains(old) for r in rects), f"event {i}")
            self.paint()
        self.tablet("release", 1400, 400)

    def test_cursor_styles(self):
        o = self.o
        o._set_mode("brush")
        self.tablet("hover", 500, 500)
        o._set_brush_cursor("hidden")
        self.paint()
        self.assertIsNone(o._cursor_drawn)
        o._set_brush_cursor("crosshair")
        self.paint()
        self.assertIsNotNone(o._cursor_drawn)
        self.assertLessEqual(o._cursor_drawn.width(), o._CROSSHAIR + 20, "a crosshair, whatever the size")
        o._set_brush_cursor("outline")
        o.save_settings_now()
        from crit_overlay.config import AppConfig
        self.assertEqual(AppConfig(o._config.path).load()["brush_cursor"], "outline")
        self.assertTrue(o.cursor_buttons["outline"].isChecked())

    def test_the_cursor_never_reaches_exports(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        clean = o.capture_export().toImage()
        self.tablet("hover", 960, 600)
        self.paint()
        self.assertEqual(o.capture_export().toImage(), clean)
        o._capturing = True
        self.paint()
        self.assertIsNone(o._cursor_drawn, "not drawn during a screenshot")
        o._capturing = False


if __name__ == "__main__":
    unittest.main()
