"""Text tool (exp/shape-tools prototype)."""

import unittest

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontInfo, QKeyEvent  # noqa: E402

from crit_overlay import brush, text_fonts  # noqa: E402
from crit_overlay.config import AppConfig  # noqa: E402
from tests.test_capture import GRAB_COLOR, CaptureCase  # noqa: E402

APP = qt_env.app


class TextCase(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_mode("text")

    def type(self, text):
        for ch in text:
            if ch == "\n":
                self.press_key(Qt.Key.Key_Return)
            else:
                self.o.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, ch))

    def press_key(self, key, mods=Qt.KeyboardModifier.NoModifier):
        self.o.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))

    def shortcut_override(self, key, text=""):
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, key, Qt.KeyboardModifier.NoModifier, text)
        ev.ignore()
        self.o.event(ev)
        return ev.isAccepted()


class TextTests(TextCase):
    def test_fonts_are_bundled_and_load(self):
        text_fonts.load()
        from PySide6.QtGui import QFont
        for family in text_fonts.FAMILIES:
            self.assertEqual(QFontInfo(QFont(family)).family(), family)
            self.assertTrue((text_fonts.FONT_DIR / family.replace(" ", "") / "OFL.txt").exists())

    def test_type_multiline_and_commit_with_ctrl_enter(self):
        o = self.o
        self.mouse("press", 300, 300)
        self.mouse("release", 300, 300)
        self.assertIsNotNone(o._text_edit)
        self.type("Hi\nthere")
        self.press_key(Qt.Key.Key_Backspace)
        self.assertEqual(o._text_edit.text, "Hi\nther")
        self.assertEqual(len(o.history), 0, "nothing committed while typing")
        self.press_key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self.assertIsNone(o._text_edit)
        self.assertEqual(len(o.history), 1)
        self.assertEqual(o.history.strokes[0].text, "Hi\nther")
        o.undo()
        self.assertEqual(len(o.history), 0)
        o.redo()
        self.assertEqual(len(o.history), 1)

    def test_click_outside_commits_and_esc_cancels(self):
        o = self.o
        self.mouse("press", 300, 300)
        self.type("one")
        self.mouse("press", 900, 900)
        self.assertEqual(len(o.history), 1)
        self.assertIsNone(o._text_edit, "the outside click only commits")
        self.mouse("press", 600, 600)
        self.type("two")
        self.press_key(Qt.Key.Key_Escape)
        self.assertIsNone(o._text_edit)
        self.assertEqual(len(o.history), 1)

    def test_empty_text_commits_nothing(self):
        o = self.o
        self.mouse("press", 300, 300)
        self.mouse("press", 900, 900)
        self.assertEqual(len(o.history), 0)

    def test_shortcuts_blocked_while_typing(self):
        o = self.o
        self.assertFalse(self.shortcut_override(Qt.Key.Key_B, "b"), "shortcuts work when not typing")
        self.mouse("press", 300, 300)
        self.assertTrue(self.shortcut_override(Qt.Key.Key_B, "b"))
        self.assertTrue(self.shortcut_override(Qt.Key.Key_Escape))
        self.assertFalse(self.shortcut_override(Qt.Key.Key_F9), "F9 still screenshots")
        self.type("be")
        self.assertEqual(o.mode, "text")
        self.assertEqual(o._text_edit.text, "be")

    def test_text_is_drawn_and_erasable(self):
        o = self.o
        o._set_text_size(80)
        o._set_text_bold(True)
        self.mouse("press", 300, 300)
        self.type("IIII")
        rect = brush.text_rect(o._text_edit)
        self.press_key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        img = o._canvas.toImage()
        d = img.devicePixelRatio()
        inked = [img.pixelColor(int(x * d), int(rect.center().y() * d)).alpha()
                 for x in range(int(rect.left()), int(rect.right()))]
        self.assertGreater(max(inked), 200)
        o._set_mode("eraser")
        o._set_size(80)
        o._set_smoothing(0)
        self.draw_line((rect.left() - 20, rect.center().y()), (rect.right() + 20, rect.center().y()), n=30)
        img = o._canvas.toImage()
        inked = [img.pixelColor(int(x * d), int(rect.center().y() * d)).alpha()
                 for x in range(int(rect.left()), int(rect.right()))]
        self.assertLess(max(inked), 10)

    def test_style_changes_apply_live_and_persist(self):
        o = self.o
        self.mouse("press", 300, 300)
        self.type("x")
        o.font_combo.setCurrentText("Caveat")
        o._set_text_align("center")
        o._set_text_size(50)
        te = o._text_edit
        self.assertEqual((te.font.family, te.font.align, te.size), ("Caveat", "center", 50))
        self.assertEqual(brush.text_rect(te).center().x(), 300)
        o.save_settings_now()
        loaded = AppConfig(o._config.path).load()
        self.assertEqual((loaded["text_family"], loaded["text_size"], loaded["text_align"]), ("Caveat", 50, "center"))

    def test_text_bar_only_with_text_tool(self):
        o = self.o
        o.show()  # Show/Hide events on the toolbar only fire while visible
        self.addCleanup(o.hide)
        self.assertFalse(o.text_bar.isHidden())
        o._set_mode("brush")
        self.assertTrue(o.text_bar.isHidden())
        o._set_mode("text")
        o.panel.hide()
        self.assertTrue(o.text_bar.isHidden(), "follows the toolbar (screenshots, pass-through)")
        o.panel.show()
        self.assertFalse(o.text_bar.isHidden())

    def test_switching_tool_commits(self):
        o = self.o
        self.mouse("press", 300, 300)
        self.type("keep")
        o._set_mode("brush")
        self.assertEqual(len(o.history), 1)

    def test_text_on_fitted_capture_exports_full_resolution(self):
        o = self.o
        o.set_margin_size(100)
        o.set_margin_color("white")
        self.capture(QRect(0, 0, 1920, 1200))
        o._set_mode("text")
        scale = o.capture.scale
        self.assertLess(scale, 1.0)
        o._set_text_size(60)
        o._set_text_align("center")
        c = o.capture.to_view(QPointF(960, 560))
        self.mouse("press", c.x(), c.y())
        self.type("MMMM")
        te = o._text_edit
        self.assertAlmostEqual(te.size, 60 / scale, delta=0.01, msg="on-screen size, image-pixel storage")
        self.assertAlmostEqual(te.points[0][0].x(), 960, delta=2)
        rect = brush.text_rect(te)
        self.press_key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        shot = o.capture_export().toImage()
        m = round(100 * self.dpr)
        d = self.dpr
        row = int(m + rect.center().y() * d)
        reds = [shot.pixelColor(int(m + x * d), row) for x in range(int(rect.left()), int(rect.right()))]
        self.assertTrue(any(p.red() > 200 and p.green() < 80 for p in reds), "text in the export")
        self.assertEqual(shot.pixelColor(m + 10, m + 10).getRgb()[:3], GRAB_COLOR.getRgb()[:3])


if __name__ == "__main__":
    unittest.main()
