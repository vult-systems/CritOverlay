"""Toolbar, context bar, More menu and Tab (UI refinement prototype)."""

import unittest

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QToolButton  # noqa: E402

from crit_overlay import icons  # noqa: E402
from tests.test_capture import CaptureCase  # noqa: E402

APP = qt_env.app


class ChromeTests(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_smoothing(0)

    def shown_sections(self):
        return {n for n, w in self.o._props_sections.items() if not w.isHidden()}

    def test_primary_toolbar_is_tools_undo_redo_more(self):
        o = self.o
        buttons = [w for w in o.panel.findChildren(QToolButton) if not w.isHidden()]
        expected = [o.select_btn, o.brush_btn, o.eraser_btn, o.shape_btn, o.text_btn, o.eyedropper_btn,
                    o.undo_btn, o.redo_btn, o.capture_btn, o.snap_btn, o.more_btn]
        buttons = [b for b in buttons if not o.props_bar.isAncestorOf(b)]
        self.assertEqual(set(buttons), set(expected))
        for b in expected:
            self.assertTrue(b.toolTip(), "every button explains itself")
        self.assertIn("(B)", o.brush_btn.toolTip())
        self.assertIn("Ctrl+Z", o.undo_btn.toolTip())

    def test_context_bar_follows_the_tool(self):
        o = self.o
        cases = {
            "brush": {"preset", "size", "opacity", "color"},
            "eraser": {"eraser_style", "size", "opacity"},
            "rect": {"shape_type", "size", "opacity", "color"},
            "text": {"text", "text_size", "opacity", "color"},
            "select": {"hint"},
            "eyedropper": {"color", "hint"},
        }
        for mode, sections in cases.items():
            o._set_mode(mode)
            with self.subTest(mode=mode):
                self.assertEqual(self.shown_sections(), sections)
        o._set_mode("rect")
        self.assertEqual(o.size_value_label.caption, "Width")
        self.assertTrue(o.shape_type_btns["rect"].isChecked())

    def test_selection_shows_its_own_properties(self):
        o = self.o
        o._set_mode("rect")
        o._set_size(10)
        o._set_opacity(60)
        self.drag((200, 200), (400, 300))
        o._set_mode("brush")
        o._set_size(30)
        o._set_opacity(100)
        o._set_mode("select")
        self.mouse("press", 300, 200)
        self.mouse("release", 300, 200)
        self.assertIsNotNone(o.selection)
        self.assertEqual(self.shown_sections(), {"size", "opacity", "color"})
        self.assertEqual(o.size_value_label.value(), 10, "the shape's width, not the brush size")
        self.assertEqual(o.opacity_value_label.value(), 60)
        o.size_value_label.setValue(16)
        self.assertAlmostEqual(o.selection.size, 16, places=2)

    def drag(self, a, b, steps=4):
        self.mouse("press", *a)
        for i in range(1, steps + 1):
            self.mouse("move", a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
        self.mouse("release", *b)

    def test_tab_hides_and_restores_the_interface(self):
        o = self.o
        o.show()
        self.addCleanup(o.hide)
        o._set_mode("brush")
        self.assertFalse(o.panel.isHidden())
        self.assertFalse(o.props_bar.isHidden())
        o.toggle_interface()
        self.assertTrue(o.panel.isHidden())
        self.assertTrue(o.props_bar.isHidden())
        self.draw_line((200, 400), (500, 400))
        self.assertEqual(len(o.history), 1, "drawing works with the interface hidden")
        o.toggle_pass_through()
        o.toggle_pass_through()
        self.assertTrue(o.panel.isHidden(), "pass-through doesn't bring it back")
        o.toggle_interface()
        self.assertFalse(o.panel.isHidden())
        self.assertFalse(o.props_bar.isHidden())
        self.assertTrue(any(a.shortcut().toString() == "Tab" for a in o.actions()))

    def test_size_field_types_and_steps_logarithmically(self):
        o = self.o
        o._set_mode("brush")
        field = o.size_value_label
        field.entry.setText("7.5")
        field._apply_entry()
        self.assertEqual(o.brush_size, 7.5)
        self.assertEqual(field.text(), "7.5px")
        field.stepBy(1)
        self.assertAlmostEqual(o.brush_size, 7.5 * 1.12, places=2)
        o.opacity_value_label.setValue(40)
        self.assertAlmostEqual(o.opacity, 0.4)

    def test_eraser_opacity_is_its_own_and_erases_partly(self):
        o = self.o
        o._set_mode("brush")
        o._set_size(30)
        self.draw_line((150, 300), (650, 300))
        o._set_mode("eraser")
        o._set_size(40)
        o._set_opacity(50)
        self.assertEqual(o.eraser_opacity, 0.5)
        self.assertEqual(o.opacity, 1.0, "brush opacity untouched")
        self.draw_line((400, 220), (400, 380))
        r, g, b, a = self.painted(400, 300)
        self.assertTrue(60 < a < 200, f"about half erased: alpha {a}")
        o._set_mode("brush")
        self.assertEqual(o.opacity_value_label.value(), 100)

    def test_more_menu_holds_the_secondary_controls(self):
        o = self.o
        m = o.more_menu
        for w in (o.snap_menu_btn, o.pass_btn, o.hide_ui_btn, o.capture_menu_btn, o.region_btn, o.backdrop_btn,
                  o.backdrop_opacity_slider, o.clear_btn, o.exit_btn, o.folder_btn, o.prefs_btn):
            self.assertIs(w.window(), m)
        for w in (o.smoothing_slider, o.autostart_btn, *o.pressure_buttons.values()):
            self.assertIs(w.window(), o.prefs_popup, "preferences live in their own popup")
        self.assertTrue(o.autostart_btn.isHidden(), "until app.py wires autostart")
        got = []
        o.autostart_toggled.connect(got.append)
        o.set_autostart_checked(False)
        self.assertFalse(o.autostart_btn.isHidden())
        o.autostart_btn.click()
        self.assertEqual(got, [True])
        o.pressure_buttons["firm"].click()
        self.assertEqual(o.pressure_curve, "firm")
        o.backdrop_btn.click()
        self.assertIn("Backdrop:", o.backdrop_btn.text())

    def test_chrome_never_reaches_exports(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        o._set_mode("brush")
        self.assertFalse(o.props_bar.isHidden())
        clean = o.capture_export().toImage()
        o.toggle_interface()
        self.assertEqual(o.capture_export().toImage(), clean)
        o.toggle_interface()
        o.panel.hide()  # as the F9 path does
        self.assertTrue(o.props_bar.isHidden(), "the context bar goes with the toolbar")

    def test_lucide_icons_render(self):
        for name in icons.LUCIDE:
            with self.subTest(name=name):
                pm = icons.build_icon(name).pixmap(20, 20)
                img = pm.toImage()
                self.assertTrue(any(img.pixelColor(x, y).alpha() > 0 for x in range(20) for y in range(20)))

    def test_tab_key_reaches_the_shortcut_not_text_editing(self):
        o = self.o
        o._set_mode("text")
        self.mouse("press", 600, 600)
        self.mouse("release", 600, 600)
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
        o.event(ev)
        self.assertTrue(ev.isAccepted(), "while typing, Tab doesn't hide the interface")
        self.assertIsNotNone(QPointF)


if __name__ == "__main__":
    unittest.main()
