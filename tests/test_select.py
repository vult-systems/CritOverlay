"""Select/Move tool and in-place text editing (exp/shape-tools prototype)."""

import unittest
from unittest import mock

from tests import qt_env  # noqa: F401  must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent  # noqa: E402

from crit_overlay import brush  # noqa: E402
from crit_overlay import overlay as overlay_mod  # noqa: E402
from crit_overlay import selection as sel_mod  # noqa: E402
from crit_overlay.models import History, Stroke  # noqa: E402
from tests.test_capture import CaptureCase  # noqa: E402

APP = qt_env.app
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


class HistoryTests(unittest.TestCase):
    def test_replace_undo_restores_position(self):
        h = History(10)
        a, b, c = (Stroke(QColor("red"), 1, 1) for _ in range(3))
        for s in (a, b, c):
            h.commit(s)
        a2 = a.copy()
        h.change([a], [a2])
        self.assertEqual(h.strokes, [b, c, a2])
        h.undo()
        self.assertEqual([id(s) for s in h.strokes], [id(a), id(b), id(c)])
        h.redo()
        self.assertIs(h.strokes[-1], a2)

    def test_merge_folds_into_one_step(self):
        h = History(10)
        a = Stroke(QColor("red"), 1, 1)
        h.commit(a)
        cur = a
        for _ in range(5):
            nxt = cur.copy()
            h.change([cur], [nxt], merge_key="opacity")
            cur = nxt
        self.assertEqual(len(h.undo_stack), 2)
        h.undo()
        self.assertIs(h.strokes[0], a)

    def test_cap_keeps_strokes_on_canvas(self):
        h = History(3)
        for _ in range(5):
            h.commit(Stroke(QColor("red"), 1, 1))
        self.assertEqual(len(h), 5)
        self.assertEqual(len(h.undo_stack), 3)


class SelectCase(CaptureCase):
    def setUp(self):
        super().setUp()
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_opacity(100)
        o._set_size(6)

    def drag(self, a, b, steps=4):
        self.mouse("press", *a)
        for i in range(1, steps + 1):
            self.mouse("move", a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
        self.mouse("release", *b)

    def click(self, x, y):
        self.mouse("press", x, y)
        self.mouse("release", x, y)

    def keypress(self, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
        self.o.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, text))

    def type(self, text):
        for ch in text:
            self.keypress(0, text=ch)

    def make_rect(self):
        self.o._set_mode("rect")
        self.drag((200, 200), (400, 300))
        self.o._set_mode("select")
        return self.o.history.strokes[-1]

    def make_text(self, text="hello", at=(600, 600)):
        o = self.o
        o._set_mode("text")
        self.click(*at)
        self.type(text)
        self.keypress(Qt.Key.Key_Return, CTRL)
        o._set_mode("select")
        return o.history.strokes[-1]


class SelectTests(SelectCase):
    def test_click_selects_outline_and_empty_click_deselects(self):
        o = self.o
        rect = self.make_rect()
        self.click(300, 200)  # on the top edge
        self.assertIs(o.selection, rect)
        self.click(300, 250)  # inside an outline-only rect: nothing
        self.assertIsNone(o.selection)
        self.click(300, 300)
        self.assertIs(o.selection, rect)
        o._escape()
        self.assertIsNone(o.selection)
        self.assertTrue(o.isVisible() is False or o.selection is None)

    def test_drag_moves_and_undo_restores(self):
        o = self.o
        rect = self.make_rect()
        self.drag((300, 200), (350, 260))
        moved = o.history.strokes[-1]
        self.assertIsNot(moved, rect)
        self.assertIs(o.selection, moved)
        self.assertAlmostEqual(moved.points[0][0].x(), 250, delta=1)
        self.assertAlmostEqual(moved.points[0][0].y(), 260, delta=1)
        self.assertGreater(self.painted(350, 260)[0], 200)
        self.assertNotEqual(self.painted(300, 200)[:3], (255, 0, 0), "old position cleared")
        o.undo()
        self.assertIs(o.history.strokes[-1], rect)
        self.assertIsNone(o.selection)
        self.assertGreater(self.painted(300, 200)[0], 200)

    def test_resize_handle(self):
        o = self.o
        self.make_rect()
        self.click(300, 200)
        self.drag((400, 300), (500, 350))  # bottom-right handle
        r = sel_mod.bounds(o.selection)
        self.assertAlmostEqual(r.right(), 500 + 0, delta=8)
        self.assertAlmostEqual(r.bottom(), 350, delta=8)
        self.assertAlmostEqual(r.left(), 200, delta=8)

    def test_line_endpoint_resize(self):
        o = self.o
        o._set_mode("arrow")
        self.drag((200, 500), (400, 500))
        o._set_mode("select")
        self.click(300, 500)
        self.drag((400, 500), (400, 650))
        self.assertAlmostEqual(o.selection.points[-1][0].y(), 650, delta=1)

    def test_recolor_opacity_size_after_placement(self):
        o = self.o
        self.make_rect()
        self.click(300, 200)
        o._set_color(QColor(0, 0, 255))
        for v in (80, 60, 40):
            o._set_opacity(v)
        o._set_size(20)
        sel = o.selection
        self.assertEqual(sel.color.name(), "#0000ff")
        self.assertAlmostEqual(sel.opacity, 0.4)
        self.assertEqual(len(o.history), 1)
        steps = len(o.history.undo_stack)
        self.assertEqual(steps, 4, "draw, color, opacity (merged), size")

    def test_copy_cut_paste_duplicate_delete_nudge(self):
        o = self.o
        rect = self.make_rect()
        self.click(300, 200)
        o.copy_selection()
        o.paste()
        self.assertEqual(len(o.history), 2)
        pasted = o.selection
        self.assertGreater(pasted.points[0][0].x(), rect.points[0][0].x())
        o.duplicate_selection()
        self.assertEqual(len(o.history), 3)
        dup = o.selection
        self.keypress(Qt.Key.Key_Right)
        self.keypress(Qt.Key.Key_Down, SHIFT)
        nudged = o.selection
        self.assertAlmostEqual(nudged.points[0][0].x() - dup.points[0][0].x(), 1, places=3)
        self.assertAlmostEqual(nudged.points[0][0].y() - dup.points[0][0].y(), 10, places=3)
        o.cut_selection()
        self.assertEqual(len(o.history), 2)
        self.assertIsNone(o.selection)
        o.paste()
        self.assertEqual(len(o.history), 3)
        o.delete_selection()
        self.assertEqual(len(o.history), 2)

    def test_paste_plain_text_from_windows_clipboard(self):
        o = self.o
        APP.clipboard().setText("from another app\nline two")
        o.cursor_pos = QPointF(500, 500)
        o.paste()
        sel = o.selection
        self.assertEqual(sel.shape, "text")
        self.assertEqual(sel.text, "from another app\nline two")
        self.assertEqual(o.mode, "select")

    def test_double_click_edits_text_and_editor_shortcuts(self):
        o = self.o
        text = self.make_text("hello world")
        rect = brush.text_rect(text)
        c = rect.center()
        self.click(c.x(), c.y())
        self.assertIs(o.selection, text)
        self.assertFalse(o.text_bar.isHidden() and o.text_bar.testAttribute(
            Qt.WidgetAttribute.WA_WState_ExplicitShowHide), "text bar for a selected text")
        self.click(c.x(), c.y())
        self.assertIsNotNone(o._text_edit, "double-click edits")
        ed = o._editor
        self.keypress(Qt.Key.Key_A, CTRL)
        self.assertEqual(ed.selected_text(), "hello world")
        self.keypress(Qt.Key.Key_End, CTRL)
        self.keypress(Qt.Key.Key_Left, CTRL | SHIFT)
        self.assertEqual(ed.selected_text(), "world")
        self.keypress(Qt.Key.Key_C, CTRL)
        self.assertEqual(APP.clipboard().text(), "world")
        self.type("there")
        self.keypress(Qt.Key.Key_Home)
        self.keypress(Qt.Key.Key_V, CTRL)
        self.assertEqual(o._text_edit.text, "worldhello there")
        self.keypress(Qt.Key.Key_Return, CTRL)
        self.assertIsNone(o._text_edit)
        self.assertEqual(len(o.history), 1)
        self.assertEqual(o.history.strokes[0].text, "worldhello there")
        self.assertIs(o.selection, o.history.strokes[0])
        o.undo()
        self.assertEqual(o.history.strokes[0].text, "hello world")

    def test_editing_esc_restores_original(self):
        o = self.o
        text = self.make_text("keep me")
        c = brush.text_rect(text).center()
        self.click(c.x(), c.y())
        self.click(c.x(), c.y())
        self.keypress(Qt.Key.Key_A, CTRL)
        self.keypress(Qt.Key.Key_Delete)
        self.keypress(Qt.Key.Key_Escape)
        self.assertIs(o.history.strokes[0], text)
        self.assertIsNone(o._hidden_stroke)

    def test_text_style_change_after_placement(self):
        o = self.o
        text = self.make_text("abc")
        c = brush.text_rect(text).center()
        self.click(c.x(), c.y())
        o.font_combo.setCurrentText("IBM Plex Mono")
        o._set_text_align("right")
        o._set_text_size(64)
        sel = o.selection
        self.assertEqual((sel.font.family, sel.font.align), ("IBM Plex Mono", "right"))
        self.assertAlmostEqual(sel.size, 64)

    def test_text_corner_handle_scales_font(self):
        o = self.o
        text = self.make_text("Scale")
        r = brush.text_rect(text)
        self.click(r.center().x(), r.center().y())
        self.drag((r.right(), r.bottom()), (r.right() + 40, r.bottom() + r.height()))
        self.assertAlmostEqual(o.selection.size / text.size, 2.0, delta=0.1)
        self.assertAlmostEqual(brush.text_rect(o.selection).left(), r.left(), delta=1)

    def test_freehand_is_not_selectable(self):
        o = self.o
        o._set_mode("brush")
        o._set_smoothing(0)
        self.draw_line((200, 700), (400, 700))
        o._set_mode("select")
        self.click(300, 700)
        self.assertIsNone(o.selection)

    def test_selection_never_in_export_or_screenshot(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        o._set_mode("rect")
        a = o.capture.to_view(QPointF(100, 100))
        b = o.capture.to_view(QPointF(300, 200))
        self.drag((a.x(), a.y()), (b.x(), b.y()))
        o._set_mode("select")
        clean = o.capture_export().toImage()
        self.click(a.x() + 50, a.y())
        self.assertIsNotNone(o.selection)
        self.assertEqual(o.capture_export().toImage(), clean, "export ignores the selection")
        handle = o.capture.to_view(QPointF(100, 100))
        shown = self.painted(handle.x() - 2, handle.y() - 2)
        o._capturing = True
        hidden = self.painted(handle.x() - 2, handle.y() - 2)
        o._capturing = False
        self.assertEqual(shown[:3], (255, 255, 255), "handle on screen")
        self.assertNotEqual(hidden[:3], (255, 255, 255), "no handle while capturing")

    def test_moved_shape_follows_capture_pan(self):
        o = self.o
        self.capture(QRect(50, 60, 400, 300))
        o._set_mode("line")
        a = o.capture.to_view(QPointF(100, 100))
        self.drag((a.x(), a.y()), (a.x() + 100, a.y()))
        o._set_mode("select")
        self.drag((a.x() + 50, a.y()), (a.x() + 50, a.y() + 40))
        doc = o.selection.points[0][0]
        self.assertAlmostEqual(doc.y(), 100 + 40 / o.capture.scale, delta=1.5)
        before = o.capture.to_view(doc)
        self.key("press", Qt.Key.Key_Space)
        self.mouse("press", 960, 600)
        self.mouse("move", 1000, 600)
        self.mouse("release", 1000, 600)
        self.key("release", Qt.Key.Key_Space)
        self.assertAlmostEqual(o.capture.to_view(doc).x() - before.x(), 40, delta=1)


class RefineTests(SelectCase):
    """Stacking order, erasures that travel, and Shift constraints."""

    def test_history_replace_keeps_position_and_merges(self):
        h = History(10)
        a, b, c = (Stroke(QColor("red"), 1, 1) for _ in range(3))
        for s in (a, b, c):
            h.commit(s)
        a2 = a.copy()
        h.replace(a, a2)
        self.assertEqual([id(s) for s in h.strokes], [id(a2), id(b), id(c)])
        a3 = a2.copy()
        h.replace(a2, a3, merge_key="k")
        a4 = a3.copy()
        h.replace(a3, a4, merge_key="k")
        self.assertIs(h.strokes[0], a4)
        self.assertEqual(len(h.undo_stack), 5)
        h.undo()
        self.assertIs(h.strokes[0], a2)
        h.undo()
        self.assertEqual([id(s) for s in h.strokes], [id(a), id(b), id(c)])
        h.redo()
        self.assertIs(h.strokes[0], a2)

    def test_manipulation_keeps_stacking_order(self):
        o = self.o
        first = self.make_rect()
        o._set_mode("ellipse")
        self.drag((250, 250), (450, 350))
        o._set_mode("select")
        self.assertEqual(o.history.strokes.index(first), 0)
        self.drag((300, 200), (300, 220))            # move
        self.assertIs(o.history.strokes[0], o.selection)
        o._set_color(QColor(0, 0, 255))              # recolor
        self.assertIs(o.history.strokes[0], o.selection)
        self.drag((400, 320), (420, 340))            # resize bottom-right
        self.assertIs(o.history.strokes[0], o.selection)
        self.keypress(Qt.Key.Key_Left)               # nudge
        self.assertIs(o.history.strokes[0], o.selection)
        text = self.make_text("abc", at=(600, 600))
        self.make_rect()
        r = brush.text_rect(text)
        self.click(r.center().x(), r.center().y())
        self.click(r.center().x(), r.center().y())   # double-click: edit
        self.keypress(Qt.Key.Key_End)
        self.type("d")
        self.keypress(Qt.Key.Key_Return, CTRL)
        edited = [s for s in o.history.strokes if s.shape == "text"][0]
        self.assertEqual(edited.text, "abcd")
        self.assertEqual(o.history.strokes.index(edited), len(o.history.strokes) - 2)

    def erase_across_top_edge(self, x=300):
        o = self.o
        o._set_mode("eraser")
        o._set_size(20)
        o._set_smoothing(0)
        self.draw_line((x, 170), (x, 230))

    def test_erased_gap_moves_with_shape(self):
        o = self.o
        self.make_rect()
        self.erase_across_top_edge(300)
        self.assertNotEqual(self.painted(300, 200)[:3], (255, 0, 0))
        o._set_mode("select")
        self.click(250, 200)
        self.drag((250, 200), (310, 200))  # right by 60
        # The gap travelled with the shape...
        self.assertNotEqual(self.painted(360, 200)[:3], (255, 0, 0), "gap moved")
        # ...and the old eraser no longer cuts the shape where it now lies.
        self.assertEqual(self.painted(300, 200)[:3], (255, 0, 0), "not erased twice")
        self.assertEqual(self.painted(420, 200)[:3], (255, 0, 0))
        o.undo()
        self.assertNotEqual(self.painted(300, 200)[:3], (255, 0, 0), "undo restores the gap")
        self.assertEqual(self.painted(360, 200)[:3], (255, 0, 0))

    def test_erased_gap_survives_resize_duplicate_and_export(self):
        o = self.o
        self.capture(QRect(50, 60, 600, 500))
        o._set_mode("rect")
        a = o.capture.to_view(QPointF(100, 100))
        b = o.capture.to_view(QPointF(300, 200))
        self.drag((a.x(), a.y()), (b.x(), b.y()))
        gap = o.capture.to_view(QPointF(200, 100))
        o._set_mode("eraser")
        o._set_size(20)
        o._set_smoothing(0)
        self.draw_line((gap.x(), gap.y() - 20), (gap.x(), gap.y() + 20))
        o._set_mode("select")
        self.click(a.x() + 20, a.y())
        self.drag((b.x(), b.y()), (b.x() + (b.x() - a.x()), b.y()))  # double the width
        self.assertEqual(len(o.selection.erasures), 1)
        img = o.capture_export().toImage()

        def red(doc_x, doc_y):
            c = img.pixelColor(round(doc_x * o.capture.dpr), round(doc_y * o.capture.dpr))
            return c.red() > 200 and c.green() < 60

        self.assertFalse(red(300, 100), "gap scaled with the shape, in the export")
        self.assertTrue(red(200, 100), "old eraser spot is shape again")
        o.duplicate_selection()
        dup = o.selection
        self.assertEqual(len(dup.erasures), 1)
        off = dup.points[0][0].x() - 100
        img = o.capture_export().toImage()
        self.assertFalse(red(300 + off, 100 + off), "duplicate keeps the gap")
        self.assertTrue(red(250 + off, 100 + off))

    def test_shift_resize_keeps_proportions(self):
        o = self.o
        rect = self.make_rect()  # 200x100
        r = sel_mod.resized(rect, "br", QPointF(600, 320), constrain=True)
        b = QRectF_of(r)
        self.assertAlmostEqual(b.width() / b.height(), 2.0, places=3)
        self.assertAlmostEqual(b.width(), 400, places=3)
        r = sel_mod.resized(rect, "tl", QPointF(190, 100), constrain=True)
        b = QRectF_of(r)
        self.assertAlmostEqual(b.width() / b.height(), 2.0, places=3)
        self.assertAlmostEqual(b.right(), 400, places=3)
        self.assertAlmostEqual(b.bottom(), 300, places=3)
        r = sel_mod.resized(rect, "r", QPointF(600, 250), constrain=True)
        b = QRectF_of(r)
        self.assertAlmostEqual(b.height(), 200, places=3)
        self.assertAlmostEqual(b.center().y(), 250, places=3)
        # Through the overlay, with Shift held.
        self.click(300, 200)
        with mock.patch.object(overlay_mod.QApplication, "keyboardModifiers", return_value=SHIFT):
            self.drag((400, 300), (600, 320))
        b = QRectF_of(o.selection)
        self.assertAlmostEqual(b.width() / b.height(), 2.0, delta=0.02)

    def test_shift_endpoint_snaps_to_45_degrees(self):
        o = self.o
        o._set_mode("line")
        self.drag((200, 500), (400, 500))
        o._set_mode("select")
        self.click(300, 500)
        with mock.patch.object(overlay_mod.QApplication, "keyboardModifiers", return_value=SHIFT):
            self.drag((400, 500), (390, 690))
        p0, p1 = o.selection.points[0][0], o.selection.points[-1][0]
        self.assertEqual((p0.x(), p0.y()), (200, 500), "other end stays put")
        self.assertAlmostEqual(p1.x() - p0.x(), p1.y() - p0.y(), delta=0.5)


def QRectF_of(stroke):
    from PySide6.QtCore import QRectF
    return QRectF(stroke.points[0][0], stroke.points[-1][0]).normalized()


if __name__ == "__main__":
    unittest.main()
