"""Second UI pass: scrub fields, one fixed-width toolbar, brush previews,
color popup, popups kept on screen."""

import unittest

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent, QWheelEvent  # noqa: E402

from crit_overlay import brush_preview, brushes  # noqa: E402
from crit_overlay.ui_widgets import clamp_popup  # noqa: E402
from tests.test_capture import CaptureCase  # noqa: E402

APP = qt_env.app


def mouse(widget, kind, x, mods=Qt.KeyboardModifier.NoModifier):
    etype = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease}[kind]
    pos = QPointF(x, widget.height() / 2)
    buttons = Qt.MouseButton.NoButton if kind == "release" else Qt.MouseButton.LeftButton
    ev = QMouseEvent(etype, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton, buttons, mods)
    {"press": widget.mousePressEvent, "move": widget.mouseMoveEvent, "release": widget.mouseReleaseEvent}[kind](ev)


class PlacementTests(unittest.TestCase):
    def test_popup_stays_on_its_monitor(self):
        avail = QRect(1920, 0, 1280, 680)        # e.g. a 2560x1440 monitor at 200%, minus the taskbar
        size = QSize(300, 400)
        # Near the right edge: slides left to stay inside.
        p = clamp_popup(size, QRect(3150, 40, 30, 30), avail)
        self.assertLessEqual(p.x() + size.width(), avail.right() + 1)
        self.assertGreaterEqual(p.x(), avail.left())
        # Near the bottom: flips above the anchor.
        p = clamp_popup(size, QRect(2000, 600, 30, 30), avail)
        self.assertLess(p.y() + size.height(), 600)
        # Taller than the screen: pinned to the top, never off it.
        p = clamp_popup(QSize(300, 900), QRect(2000, 40, 30, 30), avail)
        self.assertGreaterEqual(p.y(), avail.top())
        # Right-aligned to the anchor when asked (More menu under its button).
        p = clamp_popup(size, QRect(2500, 40, 30, 30), avail, align="right")
        self.assertEqual(p.x() + size.width() - 1, 2529)


class PreviewTests(unittest.TestCase):
    def test_every_brush_has_a_rendered_preview(self):
        for key in list(brushes.PRESETS) + list(brushes.ERASERS):
            with self.subTest(key=key):
                img = brush_preview.preview(key, "#ffffff", 84, 28, 1.0).toImage()
                inked = sum(1 for x in range(0, 84, 2) for y in range(0, 28, 2) if img.pixelColor(x, y).alpha() > 10)
                self.assertGreater(inked, 8)

    def test_previews_are_cached(self):
        a = brush_preview.preview("studio_pen", "#ffffff", 60, 20, 1.0)
        self.assertIs(brush_preview.preview("studio_pen", "#ffffff", 60, 20, 1.0), a)


class PolishTests(CaptureCase):
    def setUp(self):
        super().setUp()
        self.o._set_opacity(100)

    def test_scrub_drag_wheel_and_entry(self):
        o = self.o
        o._set_mode("brush")
        f = o.size_value_label
        start = o.brush_size
        mouse(f, "press", 10)
        mouse(f, "move", 70)
        mouse(f, "release", 70)
        self.assertGreater(o.brush_size, start, "dragging right grows the size")
        self.assertFalse(f.popup.isVisible(), "a drag doesn't open the popup")
        mid = o.brush_size
        mouse(f, "press", 70)
        mouse(f, "move", 10, Qt.KeyboardModifier.ShiftModifier)
        mouse(f, "release", 10, Qt.KeyboardModifier.ShiftModifier)
        self.assertLess(o.brush_size, mid)
        self.assertGreater(o.brush_size, start * 0.9, "Shift scrubs finely")
        before = o.brush_size
        f.wheelEvent(QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                                 Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
        self.assertAlmostEqual(o.brush_size, brushes.step_size(before, 1), places=2)
        mouse(f, "press", 20)
        mouse(f, "release", 20)
        self.assertTrue(f.popup.isVisible(), "a click opens slider and entry")
        f.entry.setText("3.5")
        f._apply_entry()
        self.assertEqual(o.brush_size, 3.5)
        self.assertFalse(f.popup.isVisible())
        o.opacity_slider.setValue(40)
        self.assertAlmostEqual(o.opacity, 0.4)
        self.assertEqual(o.opacity_value_label.text(), "40%")

    def test_toolbar_never_changes_size_between_tools(self):
        o = self.o
        o.show()
        self.addCleanup(o.hide)
        sizes = set()
        for mode in ("brush", "eraser", "rect", "line", "text", "select", "eyedropper"):
            o._set_mode(mode)
            APP.processEvents()
            sizes.add((o.panel.width(), o.panel.height(), o.panel.x(), o.panel.y()))
        self.assertEqual(len(sizes), 1, sizes)
        w, h, _x, _y = sizes.pop()
        self.assertLessEqual(w, 640, "fits a 1280-wide monitor at 200%")
        self.assertLessEqual(h, 110)

    def test_color_popup_recent_favorites_hex(self):
        o = self.o
        o._set_color(QColor("#112233"))
        o._set_color(QColor("#445566"))
        self.assertEqual(o.recent_colors[:2], ["#445566", "#112233"])
        o._show_color_popup()
        pop = o._color_popup
        self.assertEqual(pop.hex.text(), "#445566")
        pop.hex.setText("abcdef")
        pop._apply_hex()
        self.assertEqual(o.color.name(), "#abcdef")
        o._show_color_popup()
        pop._pin()
        self.assertIn("#abcdef", o.favorite_colors)
        pop._unpin("#abcdef")
        self.assertNotIn("#abcdef", o.favorite_colors)
        o.add_favorite_color("#00ff00")
        o.save_settings_now()
        from crit_overlay.config import AppConfig
        loaded = AppConfig(o._config.path).load()
        self.assertEqual(loaded["favorite_colors"], ["#00ff00"])
        self.assertEqual(loaded["recent_colors"][0], "#abcdef")
        o._show_color_popup()
        pop._eyedropper()
        self.assertEqual(o.mode, "eyedropper")
        pop.close()

    def test_brush_picker_tiles(self):
        o = self.o
        o._set_mode("brush")
        o._show_brush_popup(o.preset_btn)
        pop = o._brush_popup
        self.assertEqual(list(pop.buttons), list(brushes.PRESETS))
        for key in brushes.PRESETS:
            self.assertFalse(pop.buttons[key].icon().isNull(), "a rendered stroke, not just a name")
        pop.buttons["marker"].click()
        self.assertEqual(o.brush_preset, "marker")
        self.assertIn("Marker", o.preset_btn.text())
        pop.close()

    def test_more_menu_and_preferences(self):
        o = self.o
        self.assertIs(o.snap_btn.parent().window(), o, "screenshot stays one click away on the toolbar")
        self.assertIs(o.capture_btn.parent().window(), o)
        o._show_prefs()
        self.assertTrue(o.prefs_popup.isVisible())
        o.pressure_buttons["soft"].click()
        self.assertEqual(o.pressure_curve, "soft")
        self.assertTrue(o.pressure_buttons["soft"].isChecked())
        o.prefs_popup.close()
        self.assertTrue(o.monitor_btn.toolTip())


if __name__ == "__main__":
    unittest.main()
