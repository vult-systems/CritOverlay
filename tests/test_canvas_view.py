"""Canvas navigation and the targeted UI refinements: zoom, frame, actual
pixels, panning, on-canvas size/opacity, temporary eyedropper, before/after,
toolbar handle, toast placement, labels, color picker."""

import unittest
from unittest import mock

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QWheelEvent  # noqa: E402

from crit_overlay import overlay as overlay_mod  # noqa: E402
from tests.test_capture import CaptureCase  # noqa: E402

APP = qt_env.app
ALT = Qt.KeyboardModifier.AltModifier


class CanvasViewTests(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_smoothing(0)

    def wheel(self, x, y, dy, mods=Qt.KeyboardModifier.NoModifier):
        o = self.o
        pos = QPointF(x, y)
        ev = QWheelEvent(pos, QPointF(o.mapToGlobal(QPoint(int(x), int(y)))), QPoint(0, 0), QPoint(0, dy),
                         Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
        o.wheelEvent(ev)
        return ev

    def button(self, kind, x, y, button, mods=Qt.KeyboardModifier.NoModifier):
        o = self.o
        etype = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
                 "release": QEvent.Type.MouseButtonRelease}[kind]
        buttons = Qt.MouseButton.NoButton if kind == "release" else button
        o._last_tablet_time = 0.0
        ev = QMouseEvent(etype, QPointF(x, y), QPointF(o.mapToGlobal(QPoint(int(x), int(y)))),
                         button if kind != "move" else Qt.MouseButton.NoButton, buttons, mods)
        {"press": o.mousePressEvent, "move": o.mouseMoveEvent, "release": o.mouseReleaseEvent}[kind](ev)

    def key(self, kind, key, mods=Qt.KeyboardModifier.NoModifier):
        etype = QEvent.Type.KeyPress if kind == "press" else QEvent.Type.KeyRelease
        ev = QKeyEvent(etype, key, mods)
        (self.o.keyPressEvent if kind == "press" else self.o.keyReleaseEvent)(ev)

    # -- zoom and navigation ----------------------------------------------------

    def test_wheel_zoom_is_cursor_centered_and_export_unchanged(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        self.draw_line((900, 600), (1000, 600))
        export = o.capture_export().toImage()
        anchor = QPointF(930, 590)
        doc = o.capture.to_doc(anchor)
        self.wheel(anchor.x(), anchor.y(), 120)
        self.assertAlmostEqual(o.capture.scale, 1.25, places=4)
        here = o.capture.to_view(doc)
        self.assertAlmostEqual(here.x(), anchor.x(), places=3)
        self.assertAlmostEqual(here.y(), anchor.y(), places=3)
        for _ in range(40):
            self.wheel(anchor.x(), anchor.y(), 120)
        self.assertEqual(o.capture.scale, o.ZOOM_MAX)
        self.assertEqual(o.capture_export().toImage(), export, "zoom never changes the export")
        o.frame_capture()
        self.assertEqual(o.capture.scale, 1.0)

    def test_wheel_does_nothing_without_a_capture(self):
        ev = self.wheel(500, 500, 120)
        self.assertFalse(ev.isAccepted())

    def test_actual_pixels_and_keyboard_zoom(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        o.zoom_by(0.5)
        self.assertAlmostEqual(o.zoom_percent(), 50, places=3)
        o.actual_pixels()
        self.assertAlmostEqual(o.zoom_percent(), 100, places=3)
        shortcuts = {a.shortcut().toString() for a in o.actions()}
        self.assertTrue({"Ctrl+0", "Ctrl+1", "Ctrl+=", "Ctrl+-"} <= shortcuts)

    def test_strokes_keep_on_screen_size_at_any_zoom(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        o._set_size(10)
        center = o.capture.to_view(o.capture.image_rect().center())
        o.zoom_at(center, 2.0)
        self.draw_line((center.x() - 50, center.y()), (center.x() + 50, center.y()))
        self.assertAlmostEqual(o.history.strokes[-1].size, 10 / o.capture.scale, places=4)

    def test_middle_button_and_space_pan(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        start = QPointF(o.capture.offset)
        self.button("press", 900, 600, Qt.MouseButton.MiddleButton)
        self.button("move", 960, 640, Qt.MouseButton.MiddleButton)
        self.button("release", 960, 640, Qt.MouseButton.MiddleButton)
        self.assertAlmostEqual(o.capture.offset.x() - start.x(), 60, delta=1)
        self.assertEqual(len(o.history), 0, "panning never draws")

    # -- on-canvas adjustment, temporary tools -------------------------------------

    def test_alt_right_drag_sets_size_and_opacity(self):
        o = self.o
        o._set_mode("brush")
        o._set_size(10)
        o._set_opacity(50)
        self.button("press", 600, 600, Qt.MouseButton.RightButton, ALT)
        self.button("move", 700, 560, Qt.MouseButton.RightButton, ALT)
        self.assertIsNotNone(o._adjust)
        self.assertAlmostEqual(o.brush_size, 20, delta=0.5, msg="100 px right doubles the size")
        self.assertAlmostEqual(o.opacity, 0.70, delta=0.02, msg="up is more opaque")
        self.button("release", 700, 560, Qt.MouseButton.RightButton, ALT)
        self.assertIsNone(o._adjust)
        self.assertEqual(len(o.history), 0, "adjusting never draws")

    def test_alt_held_turns_the_cursor_into_a_picker(self):
        o = self.o
        o._set_mode("brush")
        self.key("press", Qt.Key.Key_Alt, ALT)
        self.assertTrue(o._alt_pick)
        self.assertEqual(o.cursor().shape(), Qt.CursorShape.CrossCursor)
        with mock.patch.object(o, "_pick_color_at") as pick:
            self.button("press", 500, 500, Qt.MouseButton.LeftButton, ALT)
            self.button("release", 500, 500, Qt.MouseButton.LeftButton, ALT)
        pick.assert_called_once()
        self.assertEqual(o.mode, "brush", "the tool stays")
        self.key("release", Qt.Key.Key_Alt)
        self.assertFalse(o._alt_pick)

    def test_hold_h_hides_annotations_on_screen_only(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        o._set_size(20)
        self.draw_line((900, 600), (1000, 600))
        export = o.capture_export().toImage()
        self.assertEqual(self.painted(950, 600)[:3], (255, 0, 0))
        self.key("press", Qt.Key.Key_H)
        self.assertNotEqual(self.painted(950, 600)[:3], (255, 0, 0))
        self.assertEqual(o.capture_export().toImage(), export)
        self.key("release", Qt.Key.Key_H)
        self.assertEqual(self.painted(950, 600)[:3], (255, 0, 0))

    # -- chrome refinements --------------------------------------------------------

    def test_toolbar_handle_moves_and_remembers_per_monitor(self):
        o = self.o
        o.show()
        self.addCleanup(o.hide)
        default = o.panel.pos()
        target = o.mapToGlobal(QPoint(300, 700))
        o._grip_moved(target, QPoint(10, 10))
        o._grip_released()
        moved = o.panel.pos()
        self.assertNotEqual(moved, default)
        key = o._monitor_key()
        self.assertIn(key, o._toolbar_positions)
        o._place_chrome()
        self.assertEqual(o.panel.pos(), moved, "kept across re-layout")
        o.save_settings_now()
        from crit_overlay.config import AppConfig
        self.assertIn(key, AppConfig(o._config.path).load()["toolbar_positions"])
        o._grip_moved(o.mapToGlobal(QPoint(-5000, -5000)), QPoint(0, 0))
        local = o._anchor_local_rect()
        self.assertTrue(local.contains(o.panel.geometry()), "never off the monitor")
        o._grip_reset()
        self.assertEqual(o.panel.pos(), default)

    def test_toast_avoids_the_captured_artwork(self):
        o = self.o
        o.set_margin_size(150)
        self.capture(QRect(0, 0, 1920, 1200))  # fills the monitor: fitted, top near the toolbar
        o._toast("Saved")
        art = o._artwork_view_rect()
        t = o.toast_label.geometry()
        self.assertTrue(o._anchor_local_rect().contains(t))
        self.assertFalse(t.intersects(art), f"toast {t} covers the image {art}")

    def test_exit_item_says_what_it_does(self):
        o = self.o
        o.set_tray_available(True, "Ctrl+Alt+D")
        self.assertEqual(o.exit_btn.text(), "Hide to tray")
        self.assertIn("Hide (Esc)", o.exit_btn.toolTip())
        o._show_more_menu()
        self.assertEqual(o.exit_btn.shortcut_text(), "Esc")
        o.more_menu.close()
        self.capture(QRect(50, 60, 400, 300))
        o._show_more_menu()
        self.assertEqual(o.exit_btn.shortcut_text(), "", "with a capture, Esc discards it instead")
        self.assertTrue(o.fit_btn.isEnabled())
        o.more_menu.close()
        o.set_tray_available(False)
        self.assertEqual(o.exit_btn.text(), "Exit")

    def test_no_duplicate_shortcuts(self):
        keys = [a.shortcut().toString() for a in self.o.actions() if not a.shortcut().isEmpty()]
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertNotIn("H", keys, "H is a hold key, handled on press and release")

    def test_color_picker_drags_live_and_remembers_the_final_color(self):
        o = self.o
        o._show_color_popup()
        pop = o._color_popup
        recent_before = list(o.recent_colors)
        pop.hue._at(QPointF(pop.hue.width() / 3, 5), False)       # toward green
        pop.sv._at(QPointF(pop.sv.width() - 1, 0), False)          # full saturation and value
        self.assertEqual(o.recent_colors, recent_before, "not remembered mid-drag")
        self.assertGreater(o.color.green(), 200)
        pop.sv._at(QPointF(pop.sv.width() - 1, 0), True)
        self.assertEqual(o.recent_colors[0], o.color.name())
        self.assertEqual(pop.hex.text(), o.color.name().upper())
        pop.close()

    def test_scrub_field_is_legible(self):
        f = self.o.size_value_label
        self.assertGreaterEqual(f.height(), 30)
        img = f.grab().toImage()
        self.assertFalse(img.isNull())
        self.assertIs(overlay_mod.ToolbarGrip, type(self.o.grip))


if __name__ == "__main__":
    unittest.main()
