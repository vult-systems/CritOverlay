"""Centered capture workflow (#23): select an area, work on a still image
of it centered on the monitor.

Runs on the fake 150% laptop (qt_env.py), with the screen grab replaced by a
solid image so pixel checks are exact.
"""

import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import qt_env  # must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFocusEvent, QInputDevice, QKeyEvent, QMouseEvent, QPainter, QPixmap, QPointingDevice, QShowEvent,
    QTabletEvent,
)

from crit_overlay import capture as capture_mod  # noqa: E402
from crit_overlay import overlay as overlay_module  # noqa: E402
from crit_overlay.config import AppConfig  # noqa: E402

APP = qt_env.app
GRAB_COLOR = QColor(10, 200, 10)
PEN = QPointingDevice("test pen", 4242, QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Pen,
                      QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 2)
DESKTOP_COLOR = QColor(200, 30, 160)  # around the selection


class FitTests(unittest.TestCase):
    def test_small_capture_is_centered_unscaled(self):
        scale, offset = capture_mod.fit(QRectF(0, 0, 400, 300), QRectF(0, 0, 1920, 1200), 1.5)
        self.assertEqual(scale, 1.0)
        self.assertAlmostEqual(offset.x() + 200, 960, delta=1)
        self.assertAlmostEqual(offset.y() + 150, 600, delta=1)

    def test_large_capture_is_shrunk_to_fit(self):
        outer = QRectF(-120, -120, 1920 + 240, 1200 + 240)
        scale, offset = capture_mod.fit(outer, QRectF(0, 0, 1920, 1200), 1.5)
        self.assertLess(scale, 1.0)
        self.assertLessEqual(outer.width() * scale, 1920 - 2 * capture_mod.FIT_PADDING + 0.01)
        self.assertLessEqual(outer.height() * scale, 1200 - 2 * capture_mod.FIT_PADDING + 0.01)

    def test_offset_snaps_to_device_pixels(self):
        _scale, offset = capture_mod.fit(QRectF(0, 0, 401, 301), QRectF(0, 0, 1920, 1200), 1.75)
        self.assertAlmostEqual(offset.x() * 1.75, round(offset.x() * 1.75), places=6)


class CaptureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = Path(self.tmp.name) / "settings.ini"
        patcher = mock.patch.object(overlay_module, "AppConfig", lambda: AppConfig(settings))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.o = overlay_module.Overlay(screen_mode="primary")  # 150% laptop, 1920x1200
        self.dpr = self.o._target_screen.devicePixelRatio()

    def tearDown(self):
        self.o._settings_save_timer.stop()
        self.o._pass_through_badge.close()
        if self.o._region_popup is not None:
            self.o._region_popup.close()
        self.o.deleteLater()
        APP.processEvents()
        self.tmp.cleanup()

    def fake_grab(self, selection):
        """Stand-in for QScreen.grabWindow on a desktop that is GRAB_COLOR
        inside `selection` and DESKTOP_COLOR around it. Behaves like the
        real one measured on a 150% monitor: a sub-rect grab rounds its
        origin but rounds its size up, so 401 x 301 logical px at
        (101, 77) comes back 602 x 452 and takes in one column and row of
        desktop beyond the selection's right and bottom edges."""
        dpr = self.dpr
        geo = self.o._target_screen.geometry()
        full = QPixmap(round(geo.width() * dpr), round(geo.height() * dpr))
        full.fill(DESKTOP_COLOR)
        def edge(v):
            return math.floor(v * dpr + 0.5)
        sel = QRect(edge(selection.x()), edge(selection.y()),
                    edge(selection.x() + selection.width()) - edge(selection.x()),
                    edge(selection.y() + selection.height()) - edge(selection.y()))
        painter = QPainter(full)
        painter.fillRect(sel, GRAB_COLOR)
        painter.end()

        def grab(_screen, _wid=0, x=0, y=0, w=-1, h=-1):
            if w < 0:
                pm = full.copy()
            else:
                pm = full.copy(math.floor(x * dpr + 0.5), math.floor(y * dpr + 0.5),
                               math.ceil(w * dpr), math.ceil(h * dpr))
            pm.setDevicePixelRatio(dpr)
            return pm
        return grab

    def capture(self, rect):
        """Select `rect` with the mouse and complete the grab."""
        o = self.o
        o.begin_region_selection()
        self.mouse("press", rect.left(), rect.top())
        self.mouse("move", rect.right(), rect.bottom())
        with mock.patch.object(overlay_module.QTimer, "singleShot") as later:
            self.mouse("release", rect.right() + 1, rect.bottom() + 1)
        self.assertTrue(o._grabbing, "our own paint is cleared before the grab")
        later.assert_called_once()
        screen = o._target_screen
        with mock.patch.object(type(screen), "grabWindow", self.fake_grab(rect)):
            later.call_args[0][1]()
        self.assertFalse(o._grabbing)
        self.assertIsNotNone(o.capture)

    def mouse(self, kind, x, y):
        o = self.o
        local = QPointF(x, y)
        glob = QPointF(o.mapToGlobal(QPoint(int(x), int(y))))
        etype, handler, button, buttons = {
            "press": (QEvent.Type.MouseButtonPress, o.mousePressEvent, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            "move": (QEvent.Type.MouseMove, o.mouseMoveEvent, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            "release": (QEvent.Type.MouseButtonRelease, o.mouseReleaseEvent, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        }[kind]
        o._last_tablet_time = 0.0
        handler(QMouseEvent(etype, local, glob, button, buttons, Qt.KeyboardModifier.NoModifier))

    def pen(self, kind, x, y, pressure=0.6):
        """A synthetic Windows Ink pen event through the overlay's tablet
        handler (contact for press/move, hover for 'hover')."""
        o = self.o
        etype = {"press": QEvent.Type.TabletPress, "move": QEvent.Type.TabletMove,
                 "hover": QEvent.Type.TabletMove, "release": QEvent.Type.TabletRelease}[kind]
        down = kind in ("press", "move")
        glob = QPointF(o.mapToGlobal(QPoint(int(x), int(y))))
        ev = QTabletEvent(etype, PEN, QPointF(x, y), glob, pressure if down else 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                          Qt.KeyboardModifier.NoModifier,
                          Qt.MouseButton.LeftButton if kind in ("press", "release") else Qt.MouseButton.NoButton,
                          Qt.MouseButton.LeftButton if down else Qt.MouseButton.NoButton)
        o.tabletEvent(ev)

    def key(self, kind, key):
        etype = QEvent.Type.KeyPress if kind == "press" else QEvent.Type.KeyRelease
        ev = QKeyEvent(etype, key, Qt.KeyboardModifier.NoModifier)
        (self.o.keyPressEvent if kind == "press" else self.o.keyReleaseEvent)(ev)

    def painted(self, x, y):
        pm = self.o.grab()
        d = pm.devicePixelRatio()
        return pm.toImage().pixelColor(int(x * d), int(y * d)).getRgb()

    def draw_line(self, a, b, n=12):
        self.mouse("press", *a)
        for i in range(1, n + 1):
            self.mouse("move", a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
        self.mouse("release", *b)


class CaptureTests(CaptureCase):
    def test_selection_becomes_centered_capture(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        center = o.capture.to_view(QPointF(200, 150))
        self.assertAlmostEqual(center.x(), 960, delta=1)
        self.assertAlmostEqual(center.y(), 600, delta=1)
        self.assertEqual(o.capture.scale, 1.0)
        self.assertEqual((o.capture.image.width(), o.capture.image.height()), (600, 450))
        self.assertEqual(self.painted(960, 600)[:3], GRAB_COLOR.getRgb()[:3])
        self.assertEqual(len(o.history), 0)

    def test_click_without_drag_is_full_monitor_drawing(self):
        o = self.o
        o.begin_region_selection()
        self.mouse("press", 100, 100)
        self.mouse("release", 102, 101)
        self.assertIsNone(o.capture)
        self.assertIsNone(o.region)
        self.assertFalse(o._selecting)

    def test_strokes_follow_the_canvas_when_panned(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o._set_smoothing(0)
        self.draw_line((900, 600), (1000, 600))
        self.assertEqual(len(o.history), 1)
        doc_point = o.history.strokes[0].points[0][0]
        before = o.capture.to_view(doc_point)
        self.key("press", Qt.Key.Key_Space)
        self.mouse("press", 960, 600)
        self.mouse("move", 1060, 650)
        self.mouse("release", 1060, 650)
        self.key("release", Qt.Key.Key_Space)
        after = o.capture.to_view(doc_point)
        self.assertAlmostEqual(after.x() - before.x(), 100, delta=1)
        self.assertAlmostEqual(after.y() - before.y(), 50, delta=1)
        self.assertEqual(len(o.history), 1, "panning drew nothing")

    def test_margin_change_recenters(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o.capture.offset = QPointF(10, 10)
        o.set_margin_size(80)
        center = o.capture.to_view(QPointF(200, 150))
        self.assertAlmostEqual(center.x(), 960, delta=1)
        self.assertAlmostEqual(center.y(), 600, delta=1)

    def test_margin_corners_have_no_seams(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o.set_margin_color("black")
        o.set_margin_size(37)  # odd thickness: fractional device edges at 150%
        o.set_margin_opacity(1.0)
        top_left = o.capture.to_view(QPointF(0, 0))
        img = o.grab().toImage()
        d = img.devicePixelRatio()
        # Every device pixel in the margin square at the image's top-left
        # corner is margin colored.
        for dy in range(int(-36 * d), 0):
            for dx in range(int(-36 * d), 0):
                c = img.pixelColor(int(top_left.x() * d) + dx, int(top_left.y() * d) + dy)
                self.assertEqual((c.red(), c.green(), c.blue(), c.alpha()), (0, 0, 0, 255))

    def test_export_is_full_resolution_with_margin_and_strokes(self):
        o = self.o
        o.set_margin_size(100)
        o.set_margin_color("white")
        self.capture(QRect(0, 0, 1920, 1200))  # bigger than the screen with its margin
        self.assertLess(o.capture.scale, 1.0)
        o.mode = "brush"
        o._set_color(QColor(255, 0, 0))
        o._set_smoothing(0)
        o._set_opacity(100)
        center = o.capture.to_view(QPointF(960, 600))
        self.draw_line((center.x() - 50, center.y()), (center.x() + 50, center.y()))
        shot = o.capture_export().toImage()
        m = round(100 * self.dpr)
        self.assertEqual((shot.width(), shot.height()), (2880 + 2 * m, 1800 + 2 * m))
        self.assertEqual(shot.pixelColor(2, 2).getRgb(), (255, 255, 255, 255), "margin, opaque")
        self.assertEqual(shot.pixelColor(m + 10, m + 10).getRgb()[:3], GRAB_COLOR.getRgb()[:3])
        mid = shot.pixelColor(m + 1440, m + 900)
        self.assertGreater(mid.red(), 200)
        self.assertLess(mid.green(), 80, "stroke at the image's center")

    def test_eraser_reveals_image(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_smoothing(0)
        o._set_opacity(100)
        self.draw_line((900, 600), (1020, 600))
        o._set_mode("eraser")
        o._set_size(40)
        self.draw_line((900, 600), (1020, 600))
        self.assertEqual(self.painted(960, 600)[:3], GRAB_COLOR.getRgb()[:3])

    def test_esc_with_capture_returns_to_selection(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        with mock.patch.object(o, "dismiss") as dismiss:
            o._escape()
        dismiss.assert_not_called()
        self.assertIsNone(o.capture)
        self.assertTrue(o._selecting)

    def test_esc_without_capture_hides(self):
        o = self.o
        o.begin_region_selection()
        with mock.patch.object(o, "dismiss") as dismiss:
            o._escape()
        dismiss.assert_called_once()
        self.assertFalse(o._selecting)

    def test_full_monitor_button_leaves_capture(self):
        self.capture(QRect(50, 60, 400, 300))
        self.o.set_region(None)
        self.assertIsNone(self.o.capture)

    # -- seams between image and margin ------------------------------------

    MARGIN_RGBA = (0, 0, 0, 255)

    def assert_no_seams(self, img, image_dev, label):
        """Every device pixel on the rows and columns either side of each
        image edge is exactly image color or exactly margin color, and the
        boundary falls exactly on image_dev's edges."""
        green = GRAB_COLOR.getRgb()
        img = img.convertToFormat(img.Format.Format_ARGB32)

        def px(x, y):
            return img.pixelColor(x, y).getRgb()
        left, top = image_dev.x(), image_dev.y()
        right, bottom = left + image_dev.width(), top + image_dev.height()  # exclusive
        for x in range(left, right):
            self.assertEqual(px(x, bottom - 1), green, f"{label}: last image row at x={x}")
            self.assertEqual(px(x, bottom), self.MARGIN_RGBA, f"{label}: margin below at x={x}")
            self.assertEqual(px(x, top), green, f"{label}: first image row at x={x}")
            self.assertEqual(px(x, top - 1), self.MARGIN_RGBA, f"{label}: margin above at x={x}")
        for y in range(top, bottom):
            self.assertEqual(px(right - 1, y), green, f"{label}: last image column at y={y}")
            self.assertEqual(px(right, y), self.MARGIN_RGBA, f"{label}: margin right at y={y}")
            self.assertEqual(px(left, y), green, f"{label}: first image column at y={y}")
            self.assertEqual(px(left - 1, y), self.MARGIN_RGBA, f"{label}: margin left at y={y}")
        for cx, cy in ((left - 1, top - 1), (right, top - 1), (left - 1, bottom), (right, bottom)):
            self.assertEqual(px(cx, cy), self.MARGIN_RGBA, f"{label}: corner {cx},{cy}")

    def black_margin(self, size):
        o = self.o
        o.set_margin_color("black")
        o.set_margin_size(size)
        o.set_margin_opacity(1.0)

    def painted_device_rects(self):
        o = self.o
        return o.grab().toImage(), o.capture.view_device_rects(o._margin_px(), o._window_dpr())[1]

    def test_odd_selection_at_150_percent_has_seamless_edges(self):
        # 401 x 301 logical at 150% is 601.5 x 451.5 device px: the case
        # that left a seam along the bottom and right edges.
        self.black_margin(37)
        self.capture(QRect(101, 77, 401, 301))
        o = self.o
        self.assertEqual(o.capture.dpr, 1.5, "the screen's ratio, not one skewed by the grab's rounding")
        self.assertEqual((o.capture.image.width(), o.capture.image.height()), (601, 451))
        image = o.capture.image.toImage()
        outside = [(x, y) for x in range(image.width()) for y in (0, image.height() - 1)
                   if image.pixelColor(x, y).rgb() != GRAB_COLOR.rgb()]
        outside += [(x, y) for y in range(image.height()) for x in (0, image.width() - 1)
                    if image.pixelColor(x, y).rgb() != GRAB_COLOR.rgb()]
        self.assertEqual(outside, [], "no desktop from beyond the selection in the image")
        img, image_dev = self.painted_device_rects()
        self.assertEqual(image_dev.size(), o.capture.image.size(), "unscaled: drawn 1:1")
        self.assert_no_seams(img, image_dev, "150%, unscaled")

    def test_fitted_canvas_has_seamless_edges(self):
        self.black_margin(150)
        self.capture(QRect(3, 5, 1913, 1191))
        o = self.o
        self.assertLess(o.capture.scale, 1.0)
        img, image_dev = self.painted_device_rects()
        self.assert_no_seams(img, image_dev, f"fit scale {o.capture.scale:.4f}")

    def test_capture_from_another_scale_has_seamless_edges(self):
        # A 125% capture shown on the 150% monitor (as after F7), panned to
        # an arbitrary offset.
        self.black_margin(29)
        pm = QPixmap(503, 377)
        pm.fill(GRAB_COLOR)
        pm.setDevicePixelRatio(1.25)
        o = self.o
        o._enter_capture(capture_mod.Capture(pm, QRect(0, 0, 402, 302)))
        o.capture.offset = QPointF(333.37, 211.71)
        img, image_dev = self.painted_device_rects()
        self.assert_no_seams(img, image_dev, "125% capture on 150% window")

    def test_export_has_seamless_edges(self):
        self.black_margin(37)
        self.capture(QRect(101, 77, 401, 301))
        o = self.o
        shot = o.capture_export().toImage()
        m = o.capture.margin_px(37)
        self.assertEqual((shot.width(), shot.height()), (601 + 2 * m, 451 + 2 * m))
        self.assert_no_seams(shot, QRect(m, m, 601, 451), "export")

    # -- F9: save, open, hide, resume ---------------------------------------

    def f9_patches(self, saved_path):
        o = self.o
        o.set_tray_available(True, "Ctrl+Alt+D")
        o.select_on_show = True
        save = mock.patch.object(o, "_save_screenshot", return_value=saved_path)
        startfile = mock.patch.object(overlay_module.os, "startfile", create=True)
        hide = mock.patch.object(o, "hide_to_tray")
        # Never launch a real Explorer from the tests.
        reveal = mock.patch.object(o, "_reveal_in_explorer")
        reveal.start()
        self.addCleanup(reveal.stop)
        return save, startfile, hide

    def test_f9_on_capture_saves_opens_viewer_then_explorer_hides_and_resumes(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        self.draw_line((900, 600), (1000, 600))
        cap = o.capture
        out = Path(self.tmp.name) / "crit_test.png"
        save, startfile, hide = self.f9_patches(out)
        with save as saved, startfile as opened, hide as hid, \
                mock.patch.object(overlay_module.QTimer, "singleShot") as later:
            o.capture_annotated()
            saved.assert_called_once()
            image = saved.call_args[0][0]
            self.assertEqual((image.width(), image.height()), (600, 450), "native resolution, no chrome")
            hid.assert_called_once()
            opened.assert_called_once_with(str(out))
            # Explorer comes after the viewer, on a delay, so it lands in front.
            o._reveal_in_explorer.assert_not_called()
            delay, then = later.call_args[0]
            self.assertEqual(delay, o.EXPLORER_AFTER_VIEWER_MS)
            then()
        o._reveal_in_explorer.assert_called_once_with(out)
        self.assertIs(o.capture, cap, "capture kept")
        self.assertEqual(len(o.history), 1, "strokes and undo history kept")
        # The hotkey's show resumes the session rather than selecting again.
        with mock.patch.object(overlay_module.QTimer, "singleShot") as later:
            o.showEvent(QShowEvent())
        later.assert_not_called()
        self.assertFalse(o._selecting)

    def test_f9_failure_keeps_overlay_and_reports(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        toasts = []
        o.status_changed.connect(toasts.append)
        save, startfile, hide = self.f9_patches(None)
        with save, startfile as opened, hide as hid:
            o.capture_annotated()
        hid.assert_not_called()
        opened.assert_not_called()
        o._reveal_in_explorer.assert_not_called()
        self.assertIn("Screenshot failed, see log", toasts)
        self.assertFalse(o._resume_on_show)

    def test_f9_export_error_keeps_overlay(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        save, startfile, hide = self.f9_patches(Path(self.tmp.name) / "x.png")
        with save, startfile as opened, hide as hid, \
                mock.patch.object(o, "capture_export", side_effect=RuntimeError("boom")):
            o.capture_annotated()
        hid.assert_not_called()
        opened.assert_not_called()

    def test_f9_without_tray_stays_visible(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        out = Path(self.tmp.name) / "crit_test.png"
        save, startfile, hide = self.f9_patches(out)
        o.set_tray_available(False)
        with save, startfile as opened, hide as hid, mock.patch.object(overlay_module.QTimer, "singleShot"):
            o.capture_annotated()
        hid.assert_not_called()
        opened.assert_called_once_with(str(out))

    def test_viewer_failure_opens_explorer_immediately(self):
        o = self.o
        o.set_tray_available(True)
        out = Path(self.tmp.name) / "x.png"
        with mock.patch.object(overlay_module.os, "startfile", side_effect=OSError("blocked"), create=True), \
                mock.patch.object(o, "hide_to_tray"), \
                mock.patch.object(o, "_reveal_in_explorer") as reveal, \
                mock.patch.object(overlay_module.QTimer, "singleShot") as later:
            o._after_screenshot_saved(out)
        reveal.assert_called_once_with(out)
        later.assert_not_called()

    def test_explorer_gets_foreground_handoff(self):
        o = self.o
        with mock.patch.object(overlay_module.native_windows, "allow_foreground_handoff") as allow, \
                mock.patch.object(overlay_module.subprocess, "Popen") as popen:
            o._reveal_in_explorer(Path(r"C:\shots\crit_1.png"))
        allow.assert_called_once()
        args = popen.call_args[0][0]
        self.assertTrue(args[0].lower().endswith("explorer.exe"))
        self.assertEqual(args[1], r"/select,C:\shots\crit_1.png")

    def test_f9_live_mode_hides_chrome_during_grab_and_restores_it(self):
        o = self.o
        o.panel.show()
        o.hover_tip.show()
        out = Path(self.tmp.name) / "crit_live.png"
        save, startfile, hide = self.f9_patches(out)
        seen = {}
        screen = o._target_screen

        def grab(_screen, _wid=0, *args):
            seen.update(panel=not o.panel.isHidden(), tip=not o.hover_tip.isHidden(),
                        toast=o.toast_label.isVisible(), capturing=o._capturing)
            pm = QPixmap(100, 100)
            pm.fill(GRAB_COLOR)
            return pm
        with save, startfile as opened, hide as hid, \
                mock.patch.object(overlay_module.QTimer, "singleShot") as later, \
                mock.patch.object(overlay_module.QGuiApplication, "screenAt", return_value=screen), \
                mock.patch.object(type(screen), "grabWindow", grab):
            o.capture_annotated()
            later.call_args[0][1]()  # the grab
            later.call_args[0][1]()  # Explorer, after the viewer
        self.assertEqual(seen, {"panel": False, "tip": False, "toast": False, "capturing": True})
        self.assertFalse(o.panel.isHidden(), "toolbar back for the resumed session")
        hid.assert_called_once()
        opened.assert_called_once_with(str(out))
        o._reveal_in_explorer.assert_called_once_with(out)

    # -- pen, panning edge cases, monitors, history ---------------------------

    def test_pen_strokes_land_in_image_coordinates_with_pressure(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o._set_smoothing(0)
        start = QPointF(900, 600)
        self.pen("press", start.x(), start.y(), 0.9)
        for i in range(1, 15):
            self.pen("move", 900 + i * 6, 600, 0.9)
        self.pen("release", 990, 600)
        self.assertEqual(len(o.history), 1)
        stroke = o.history.strokes[0]
        first = stroke.points[0][0]
        expected = o.capture.to_doc(start)
        self.assertAlmostEqual(first.x(), expected.x(), delta=0.01)
        self.assertAlmostEqual(first.y(), expected.y(), delta=0.01)
        self.assertGreater(max(p for _, p in stroke.points), 0.5, "pen pressure used")

    def test_fitted_capture_keeps_on_screen_brush_size(self):
        o = self.o
        o.set_margin_size(150)
        self.capture(QRect(0, 0, 1920, 1200))
        scale = o.capture.scale
        self.assertLess(scale, 1.0)
        self.draw_line((900, 600), (980, 600))
        self.assertAlmostEqual(o.history.strokes[0].size, o.brush_size / scale, places=6)

    def test_space_pen_pan_released_over_toolbar_ends_the_pan(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o.show()  # the toolbar only counts as visible on a shown overlay
        APP.processEvents()
        self.assertTrue(o.panel.isVisible())
        self.key("press", Qt.Key.Key_Space)
        self.pen("press", 900, 600)
        self.assertIsNotNone(o._panning)
        self.pen("move", 950, 620)
        bar = o.panel.geometry().center()
        self.key("release", Qt.Key.Key_Space)
        self.pen("release", bar.x(), bar.y())
        self.assertIsNone(o._panning, "pan ended by a release over the toolbar")
        offset = QPointF(o.capture.offset)
        self.pen("hover", 700, 400)
        self.assertEqual(o.capture.offset, offset, "pen hover doesn't move the capture")
        self.assertEqual(len(o.history), 0)

    def test_focus_loss_forgets_space(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        self.key("press", Qt.Key.Key_Space)
        self.assertTrue(o._space_held)
        o.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        self.assertFalse(o._space_held)
        self.draw_line((900, 600), (960, 600))
        self.assertEqual(len(o.history), 1, "clicks draw again instead of panning")

    def test_pass_through_mid_pan_ends_pan_and_shows_the_desktop(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_smoothing(0)
        o._set_opacity(100)
        self.draw_line((900, 650), (1000, 650))
        self.key("press", Qt.Key.Key_Space)
        self.mouse("press", 900, 600)
        self.mouse("move", 920, 600)
        image_point = o.capture.to_view(QPointF(10, 10))
        stroke_point = o.capture.to_view(o.history.strokes[0].points[3][0])
        o.toggle_pass_through()
        try:
            self.assertIsNone(o._panning)
            self.assertFalse(o._space_held)
            self.assertLessEqual(self.painted(image_point.x(), image_point.y())[3], 1,
                                 "image not painted while clicks pass through")
            self.assertGreater(self.painted(stroke_point.x(), stroke_point.y())[3], 200, "strokes stay")
        finally:
            o.toggle_pass_through()
        self.assertEqual(self.painted(image_point.x(), image_point.y())[:3], GRAB_COLOR.getRgb()[:3])

    def test_export_keeps_backdrop_during_pass_through(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o._set_backdrop("white")
        o._set_backdrop_opacity(100)
        o.toggle_pass_through()
        try:
            shot = o.capture_export().toImage()
        finally:
            o.toggle_pass_through()
        self.assertEqual(shot.pixelColor(10, 10).getRgb()[:3], (255, 255, 255))

    def test_monitor_switch_recenters_and_keeps_resolution(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        self.draw_line((900, 600), (960, 600))
        o.move_to_next_screen()  # 1920 x 1080 projector at 100%
        try:
            self.assertEqual(o.rect().size().height(), 1080)
            center = o.capture.to_view(QPointF(200, 150))
            self.assertAlmostEqual(center.x(), 960, delta=1)
            self.assertAlmostEqual(center.y(), 540, delta=1)
            self.assertEqual(o.capture.dpr, 1.5, "image keeps its resolution")
            self.assertEqual(o._canvas.devicePixelRatio(), 1.5, "strokes stay at export resolution")
            self.assertEqual(len(o.history), 1)
            corner = o.capture.to_view(QPointF(20, 20))  # image, away from the stroke
            self.assertEqual(self.painted(corner.x(), corner.y())[:3], GRAB_COLOR.getRgb()[:3])
            stroke = o.capture.to_view(o.history.strokes[0].points[2][0])
            self.assertAlmostEqual(stroke.y(), 540, delta=2, msg="the stroke moved with the capture")
        finally:
            o.move_to_next_screen()

    def test_undo_and_redo_on_a_capture(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o._set_color(QColor(255, 0, 0))
        o._set_smoothing(0)
        o._set_opacity(100)
        self.draw_line((900, 600), (1000, 600))
        self.assertEqual(self.painted(950, 600)[:3], (255, 0, 0))
        o.undo()
        self.assertEqual(self.painted(950, 600)[:3], GRAB_COLOR.getRgb()[:3])
        o.redo()
        self.assertEqual(self.painted(950, 600)[:3], (255, 0, 0))

    def test_live_eraser_preview_on_a_fitted_capture(self):
        o = self.o
        o.set_margin_size(150)
        self.capture(QRect(0, 0, 1920, 1200))
        self.assertLess(o.capture.scale, 1.0)
        o._set_color(QColor(255, 0, 0))
        o._set_smoothing(0)
        o._set_opacity(100)
        self.draw_line((850, 600), (1050, 600))
        self.assertEqual(self.painted(950, 600)[:3], (255, 0, 0))
        o._set_mode("eraser")
        o._set_size(40)
        self.mouse("press", 850, 600)
        for i in range(1, 21):
            self.mouse("move", 850 + i * 10, 600)
        try:
            self.assertIsNotNone(o._live_layer, "erasing in progress")
            self.assertEqual(self.painted(950, 600)[:3], GRAB_COLOR.getRgb()[:3], "preview reveals the image")
        finally:
            self.mouse("release", 1050, 600)

    def test_failed_capture_is_reported_after_the_selection_prompt(self):
        o = self.o
        toasts = []
        o.status_changed.connect(toasts.append)
        o.begin_region_selection()
        self.mouse("press", 100, 100)
        self.mouse("move", 300, 300)
        with mock.patch.object(overlay_module.QTimer, "singleShot") as later:
            self.mouse("release", 300, 300)
        screen = o._target_screen
        with mock.patch.object(type(screen), "grabWindow", lambda *_a: QPixmap()), \
                self.assertLogs("CritOverlay.overlay", "ERROR"):
            later.call_args[0][1]()
        self.assertIsNone(o.capture)
        self.assertTrue(o._selecting, "back to selecting")
        self.assertTrue(toasts[-1].startswith("Capture failed"), toasts[-1])

    def test_discarding_a_capture_clears_the_cursor_position(self):
        self.capture(QRect(50, 60, 400, 300))
        o = self.o
        o._last_tablet_time = 0.0
        o.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(900, 600), QPointF(900, 600),
                                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertIsNotNone(o.cursor_pos)
        with mock.patch.object(o, "dismiss"):
            o._escape()
        self.assertIsNone(o.cursor_pos)

    def test_select_on_show(self):
        o = self.o
        o.select_on_show = True
        with mock.patch.object(overlay_module.QTimer, "singleShot") as later:
            o.showEvent(QShowEvent())
        later.assert_called_with(0, o.begin_region_selection)


if __name__ == "__main__":
    unittest.main()
