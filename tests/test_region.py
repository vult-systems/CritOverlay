"""Drawing region and annotation margin (#10).

Geometry and screenshot composition are tested as functions. The overlay
runs on the fake 150% laptop + 100% projector monitors (qt_env.py); its
painted output is checked through QWidget.grab(), which renders the same
paintEvent the screen shows. Real screen grabs on real monitors are
covered by --selftest.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import qt_env  # must come before other PySide6 imports

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QMouseEvent, QPainter, QPixmap  # noqa: E402

from crit_overlay import brush  # noqa: E402
from crit_overlay import constants as C  # noqa: E402
from crit_overlay import overlay as overlay_module  # noqa: E402
from crit_overlay import region  # noqa: E402
from crit_overlay.config import AppConfig  # noqa: E402

APP = qt_env.app
BOUNDS = QRect(0, 0, 1920, 1080)


class GeometryTests(unittest.TestCase):
    def test_selection_rect_any_drag_direction(self):
        expected = QRect(100, 50, 300, 200)
        for a, b in ((QPointF(100, 50), QPointF(400, 250)), (QPointF(400, 250), QPointF(100, 50)),
                     (QPointF(400, 50), QPointF(100, 250))):
            self.assertEqual(region.selection_rect(a, b, BOUNDS), expected)

    def test_selection_rect_clipped_to_monitor(self):
        r = region.selection_rect(QPointF(-50, -50), QPointF(5000, 300), BOUNDS)
        self.assertEqual(r, QRect(0, 0, 1920, 300))

    def test_tiny_drag_is_not_usable(self):
        self.assertFalse(region.is_usable(region.selection_rect(QPointF(10, 10), QPointF(20, 300), BOUNDS)))
        self.assertFalse(region.is_usable(None))
        self.assertTrue(region.is_usable(QRect(0, 0, C.MIN_REGION_SIZE, C.MIN_REGION_SIZE)))

    def test_clamp_moves_region_onto_smaller_monitor(self):
        # Selected near the bottom of a 1200-high monitor, moved to 1080.
        r = region.clamp_region(QRect(100, 1000, 400, 180), BOUNDS)
        self.assertEqual(r, QRect(100, 900, 400, 180))
        self.assertTrue(BOUNDS.contains(r))

    def test_clamp_shrinks_oversized_region(self):
        self.assertEqual(region.clamp_region(QRect(0, 0, 3000, 2000), BOUNDS), BOUNDS)

    def test_outer_rect_and_bands(self):
        reg = QRect(100, 100, 400, 300)
        outer = region.outer_rect(reg, 120, BOUNDS)
        self.assertEqual(outer, QRect(0, 0, 620, 520), "clipped at the monitor edge")
        bands = region.margin_bands(reg, outer)
        area = sum(b.width() * b.height() for b in bands)
        self.assertEqual(area, outer.width() * outer.height() - reg.width() * reg.height())
        for b in bands:
            self.assertFalse(b.intersects(reg))
        self.assertEqual(region.outer_rect(None, 120, BOUNDS), BOUNDS)

    def test_margin_color(self):
        self.assertEqual(region.margin_color("black", 0.5).getRgb(), (0, 0, 0, 128))
        self.assertEqual(region.margin_color("white").getRgb(), (255, 255, 255, 255))


class ComposeCaptureTests(unittest.TestCase):
    """The screenshot is the region's screen grab, inside a margin rebuilt
    from the margin color and the stroke canvas: no desktop content."""

    def setUp(self):
        self.dpr = 1.5
        self.region = QRect(200, 100, 400, 300)
        self.outer = QRect(140, 40, 520, 420)
        grab = QPixmap(round(400 * self.dpr), round(300 * self.dpr))
        grab.fill(QColor(10, 200, 10))
        grab.setDevicePixelRatio(self.dpr)
        self.grab = grab
        canvas = brush.new_layer(BOUNDS.size(), self.dpr)
        p = QPainter(canvas)
        p.fillRect(QRect(150, 200, 20, 20), QColor(255, 0, 0))  # a stroke in the left margin
        p.end()
        self.canvas = canvas

    def pixel(self, image, x, y):
        """Color at a logical point relative to the outer rect."""
        return image.pixelColor(int(x * self.dpr), int(y * self.dpr)).getRgb()[:3]

    def test_region_and_margin_at_device_resolution(self):
        out = region.compose_capture(self.grab, self.canvas, self.region, self.outer,
                                     region.margin_color("white", 0.3)).toImage()
        self.assertEqual((out.width(), out.height()), (round(520 * self.dpr), round(420 * self.dpr)))
        self.assertEqual(self.pixel(out, 5, 5), (255, 255, 255), "margin is opaque, not desktop")
        self.assertEqual(self.pixel(out, 260, 210), (10, 200, 10), "region comes from the screen grab")
        self.assertEqual(self.pixel(out, 20, 170), (255, 0, 0), "strokes in the margin are kept")

    def test_no_margin_returns_the_grab(self):
        self.assertIs(region.compose_capture(self.grab, self.canvas, self.region, self.region, None), self.grab)


class MarginSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.ini"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_absent(self):
        loaded = AppConfig(self.path).load()
        self.assertEqual(
            (loaded["margin_enabled"], loaded["margin_size"], loaded["margin_color"], loaded["margin_opacity"]),
            (C.DEFAULT_MARGIN_ENABLED, C.DEFAULT_MARGIN_SIZE, C.DEFAULT_MARGIN_COLOR, C.DEFAULT_MARGIN_OPACITY),
        )

    def test_round_trip(self):
        AppConfig(self.path).save(color="#ff0000", size=10, opacity=1.0, smoothing=0.0, backdrop=None,
                                  backdrop_opacity=0.5, margin_enabled=True, margin_size=64,
                                  margin_color="black", margin_opacity=0.4)
        loaded = AppConfig(self.path).load()
        self.assertEqual((loaded["margin_enabled"], loaded["margin_size"], loaded["margin_color"]),
                         (True, 64, "black"))
        self.assertAlmostEqual(loaded["margin_opacity"], 0.4)

    def test_invalid_values_fall_back(self):
        self.path.write_text("[margin]\nenabled=maybe\nsize=99999\ncolor=purple\nopacity=abc\n", encoding="utf-8")
        loaded = AppConfig(self.path).load()
        self.assertEqual((loaded["margin_enabled"], loaded["margin_size"], loaded["margin_color"]),
                         (False, C.MARGIN_MAX_SIZE, C.DEFAULT_MARGIN_COLOR))
        self.assertEqual(loaded["margin_opacity"], C.DEFAULT_MARGIN_OPACITY)


class OverlayCase(unittest.TestCase):
    """Overlay on the fake 150% laptop, with painting helpers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = Path(self.tmp.name) / "settings.ini"
        patcher = mock.patch.object(overlay_module, "AppConfig", lambda: AppConfig(settings))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.screens = {s.name(): s for s in QGuiApplication.screens()}
        # Not shown: the offscreen platform reports a 1.0 pixel ratio for
        # every shown window, while a hidden widget gets its monitor's real
        # ratio (as on Windows). grab() renders the same paintEvent either way.
        self.o = overlay_module.Overlay(screen_mode="primary")  # the 150% laptop, 1920x1200
        self.toasts = []
        self.o.status_changed.connect(self.toasts.append)

    def tearDown(self):
        self.o._settings_save_timer.stop()
        self.o._pass_through_badge.close()
        if self.o._region_popup is not None:
            self.o._region_popup.close()
        self.o.deleteLater()
        APP.processEvents()
        self.tmp.cleanup()

    def stroke(self, start, dx=6, n=20):
        o = self.o
        o._begin_stroke(QPointF(*start), 0.7)
        for i in range(1, n):
            o._add_point(QPointF(start[0] + i * dx, start[1]), 0.7)
        o._end_stroke()

    def painted(self, x, y):
        """RGBA of the overlay's painted output at a logical point."""
        pm = self.o.grab()
        dpr = pm.devicePixelRatio()
        return pm.toImage().pixelColor(int(x * dpr), int(y * dpr)).getRgb()

    def set_area(self, margin=False):
        self.o.set_margin_size(60)  # (turns the margin on, #13)
        self.o.set_margin_enabled(margin)
        self.o.set_region(QRect(400, 400, 600, 400))


class OverlayRegionTests(OverlayCase):
    # -- input and display restriction ------------------------------------

    def test_full_monitor_by_default(self):
        self.assertIsNone(self.o.region)
        self.assertEqual(self.o.drawable_rect(), self.o.rect())
        self.assertFalse(self.o.region_btn.isChecked())

    def test_strokes_start_only_inside_the_region(self):
        self.set_area()
        self.stroke((100, 100))
        self.assertEqual(len(self.o.history), 0, "no stroke outside the region")
        self.stroke((500, 500))
        self.assertEqual(len(self.o.history), 1)
        self.assertTrue(self.o.region_btn.isChecked())

    def test_margin_accepts_handwriting(self):
        self.set_area(margin=True)
        self.stroke((370, 450), dx=1, n=10)  # 30 px left of the region, inside the 60 px margin
        self.assertEqual(len(self.o.history), 1)
        self.stroke((300, 450), dx=1, n=10)  # beyond the margin
        self.assertEqual(len(self.o.history), 1)

    def test_strokes_are_clipped_at_the_drawable_edge(self):
        self.set_area()
        self.stroke((900, 600), dx=20, n=20)  # runs from inside to well past the right edge
        self.assertGreater(self.painted(950, 600)[3], 200, "ink inside")
        self.assertLessEqual(self.painted(1200, 600)[3], 1, "nothing painted outside")
        self.o.set_region(None)
        self.assertGreater(self.painted(1200, 600)[3], 200, "clipping is display-only; stroke data is kept")

    def test_backdrop_and_margin_restricted_and_independent(self):
        o = self.o
        o._set_backdrop("black")
        o._set_backdrop_opacity(100)
        o.set_margin_color("white")
        o.set_margin_opacity(0.5)
        self.set_area(margin=True)
        self.assertEqual(self.painted(700, 600), (0, 0, 0, 255), "backdrop fills the region")
        r, g, b, a = self.painted(370, 600)
        self.assertEqual((r, g, b), (255, 255, 255))
        self.assertAlmostEqual(a, 128, delta=1, msg="margin has its own opacity")
        self.assertLessEqual(self.painted(100, 100)[3], 1, "backdrop doesn't extend outside")
        o._set_backdrop_opacity(40)
        self.assertAlmostEqual(self.painted(700, 600)[3], 102, delta=1)
        self.assertAlmostEqual(self.painted(370, 600)[3], 128, delta=1, msg="backdrop intensity leaves the margin alone")

    def test_eraser_reveals_margin_layer(self):
        o = self.o
        o.set_margin_opacity(1.0)
        self.set_area(margin=True)
        o._set_color("#ff0000")
        self.stroke((350, 600), dx=1, n=30)
        self.assertEqual(self.painted(360, 600)[:3], (255, 0, 0))
        o._set_mode("eraser")
        self.stroke((350, 600), dx=1, n=30)
        self.assertEqual(self.painted(360, 600), (255, 255, 255, 255), "erasing shows the margin, not the desktop")

    def test_pass_through_hides_fills_keeps_strokes(self):
        o = self.o
        o._set_backdrop("white")
        o._set_backdrop_opacity(100)
        self.set_area(margin=True)
        self.stroke((500, 500))
        o.toggle_pass_through()
        try:
            self.assertLessEqual(self.painted(700, 700)[3], 1)
            self.assertLessEqual(self.painted(370, 600)[3], 1)
            self.assertGreater(self.painted(520, 500)[3], 200)
        finally:
            o.toggle_pass_through()

    def test_capture_hides_frame_and_cursor_ring(self):
        o = self.o
        self.set_area()
        o.cursor_pos = QPointF(700, 600)
        d = brush.width_for(o.brush_size, o._last_effective_pressure) / 2
        ring = (700 + d, 600)
        self.assertGreater(self.painted(*ring)[3], 1, "ring shown normally")
        frame_alpha = lambda: max(self.painted(x / 4, 600)[3] for x in range(1590, 1610))  # noqa: E731
        self.assertGreater(frame_alpha(), 1, "region frame shown normally")
        o._capturing = True
        try:
            self.assertLessEqual(self.painted(*ring)[3], 1, "no cursor ring in screenshots")
            self.assertLessEqual(frame_alpha(), 1, "no region frame in screenshots")
        finally:
            o._capturing = False

    # -- selection tool -------------------------------------------------------

    def mouse(self, kind, x, y):
        o = self.o
        local = QPointF(x, y)
        glob = QPointF(o.mapToGlobal(QPoint(x, y)))
        etype, handler, button, buttons = {
            "press": (QEvent.Type.MouseButtonPress, o.mousePressEvent, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            "move": (QEvent.Type.MouseMove, o.mouseMoveEvent, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            "release": (QEvent.Type.MouseButtonRelease, o.mouseReleaseEvent, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        }[kind]
        o._last_tablet_time = 0.0
        handler(QMouseEvent(etype, local, glob, button, buttons, Qt.KeyboardModifier.NoModifier))

    def drag(self, a, b):
        self.mouse("press", *a)
        self.mouse("move", (a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        self.mouse("move", *b)
        self.mouse("release", *b)

    def test_drag_selects_area_without_drawing(self):
        self.o.begin_region_selection()
        self.drag((800, 700), (300, 300))
        self.assertEqual(self.o.region, QRect(300, 300, 500, 400))
        self.assertEqual(len(self.o.history), 0)
        self.assertFalse(self.o._selecting)

    def test_click_without_drag_means_full_monitor(self):
        self.set_area()
        self.o.begin_region_selection()
        self.drag((500, 500), (505, 505))
        self.assertIsNone(self.o.region)
        self.assertIn("full monitor", self.toasts[-1])

    def test_escape_cancels_selection_before_hiding(self):
        o = self.o
        with mock.patch.object(o, "dismiss") as dismiss:
            o.begin_region_selection()
            self.mouse("press", 300, 300)
            o._escape()
            dismiss.assert_not_called()
            self.assertFalse(o._selecting)
            self.assertIsNone(o.region)
            o._escape()
            dismiss.assert_called_once()

    def test_selection_by_pen(self):
        o = self.o
        o.begin_region_selection()
        for kind, (x, y) in (("press", (300, 300)), ("move", (600, 500)), ("release", (700, 600))):
            o._selection_input(kind, QPointF(x, y))
        self.assertEqual(o.region, QRect(300, 300, 400, 300))
        self.assertEqual(len(o.history), 0)

    def test_tray_screenshot_while_hidden_keeps_the_toolbar(self):
        # #25: tray > Screenshot with the overlay hidden (as here: never
        # shown) hid the toolbar for the grab and never brought it back.
        o = self.o
        o.panel.show()
        self.assertFalse(o.isVisible())
        screen = o._target_screen
        pm = QPixmap(100, 100)
        pm.fill(QColor(10, 200, 10))
        with mock.patch.object(overlay_module.QTimer, "singleShot") as later, \
                mock.patch.object(overlay_module.QGuiApplication, "screenAt", return_value=screen), \
                mock.patch.object(type(screen), "grabWindow", lambda *_a: pm), \
                mock.patch.object(o, "_save_screenshot", return_value=None):
            o.capture_annotated()
            self.assertTrue(o.panel.isHidden(), "hidden for the grab")
            later.call_args[0][1]()
        self.assertFalse(o.panel.isHidden(), "toolbar back afterwards")

    def test_toolbar_click_while_selecting_is_ignored(self):
        o = self.o
        o.show()  # the toolbar only counts as visible on a shown overlay
        APP.processEvents()
        o.begin_region_selection()
        c = o.panel.geometry().center()
        self.mouse("press", c.x(), c.y())
        self.assertIsNone(o._selection_anchor)

    # -- monitors -----------------------------------------------------------

    def test_region_follows_monitor_switch_with_exact_scale(self):
        o = self.o
        o.set_margin_enabled(True)
        o.set_margin_size(60)
        o.set_region(QRect(200, 1000, 500, 180))  # bottom of the 1200-high laptop
        self.stroke((300, 1050))
        o.move_to_next_screen()  # 1920x1080 projector at 100%
        self.assertIs(o._target_screen, self.screens["projector"])
        self.assertEqual(o.region, QRect(200, 900, 500, 180), "moved inside the shorter monitor")
        self.assertTrue(o.rect().contains(o.drawable_rect()))
        self.assertEqual(o._canvas.devicePixelRatio(), 1.0)
        pm = o.grab()
        self.assertEqual(pm.devicePixelRatio(), 1.0)
        o.move_to_next_screen()  # back to 150%
        self.assertEqual(o._canvas.devicePixelRatio(), 1.5)
        self.assertEqual(o.region, QRect(200, 900, 500, 180), "unchanged where it already fits")
        self.assertEqual(len(o.history), 1)

    def test_switch_cancels_selection(self):
        o = self.o
        o.begin_region_selection()
        self.mouse("press", 300, 300)
        o.move_to_next_screen()
        self.assertFalse(o._selecting)

    def test_margin_settings_persist(self):
        o = self.o
        o.set_margin_enabled(True)
        o.set_margin_size(48)
        o.set_margin_color("black")
        o.set_margin_opacity(0.6)
        o._flush_settings()
        loaded = o._config.load()
        self.assertEqual((loaded["margin_enabled"], loaded["margin_size"], loaded["margin_color"]),
                         (True, 48, "black"))

    def test_region_popup_reflects_state(self):
        o = self.o
        self.set_area(margin=True)
        o._show_region_popup()
        popup = o._region_popup
        self.assertFalse(popup.full_btn.isChecked())
        self.assertTrue(popup.margin_btn.isChecked())
        self.assertEqual(popup.margin_btn.text(), "Margin: on")
        popup.size_slider.setValue(90)
        self.assertEqual((o.margin_size, popup.size_label.text()), (90, "90px"))
        popup.black_btn.click()
        self.assertEqual(o.margin_color, "black")
        popup.full_btn.click()
        self.assertIsNone(o.region)
        self.assertTrue(popup.full_btn.isChecked())
        popup.close()


class MarginVisibilityTests(OverlayCase):
    """#13: in rc.2 the margin stayed off after selecting a region, while
    thickness/color/opacity looked active and did nothing; slider drags
    didn't redraw until the mouse paused."""

    def region_only(self):
        o = self.o
        o.set_margin_enabled(False)
        o.set_region(QRect(400, 400, 600, 400))
        self.assertFalse(o.margin_enabled)
        self.assertLessEqual(self.painted(390, 600)[3], 1, "no margin yet")

    def open_popup(self):
        self.o._show_region_popup()
        self.addCleanup(self.o._region_popup.close)
        return self.o._region_popup

    def test_each_margin_control_turns_the_margin_on(self):
        for adjust in (lambda p: p.size_slider.setValue(80),
                       lambda p: p.opacity_slider.setValue(60),
                       lambda p: p.black_btn.click(),
                       lambda p: p.white_btn.click()):
            self.region_only()
            popup = self.open_popup()
            adjust(popup)
            self.assertTrue(self.o.margin_enabled)
            self.assertTrue(popup.margin_btn.isChecked())
            self.assertEqual(popup.margin_btn.text(), "Margin: on")
            self.assertGreater(self.painted(390, 600)[3], 1, "margin visible outside the region")
            popup.close()

    def test_reported_flow_margin_visible_and_in_screenshot(self):
        # Select an area, open the popup, drag the thickness and opacity
        # sliders (the steps from #13), then F9.
        o = self.o
        o.set_margin_enabled(False)
        o.begin_region_selection()
        for kind, (x, y) in (("press", (400, 400)), ("move", (700, 600)), ("release", (1000, 800))):
            o._selection_input(kind, QPointF(x, y))
        popup = self.open_popup()
        popup.size_slider.setValue(100)
        popup.opacity_slider.setValue(50)
        self.assertEqual((o.margin_size, round(o.margin_opacity, 2)), (100, 0.5))
        r, g, b, a = self.painted(330, 600)  # 70 px left of the region, inside the 100 px margin
        self.assertEqual((r, g, b), (255, 255, 255))
        self.assertAlmostEqual(a, 128, delta=1)
        self.assertLessEqual(self.painted(250, 600)[3], 1, "nothing beyond the margin")

        screen = o._target_screen
        dpr = screen.devicePixelRatio()

        def fake_grab(_screen, _wid, x, y, w, h):
            pm = QPixmap(round(w * dpr), round(h * dpr))
            pm.fill(QColor(10, 200, 10))
            return pm

        with mock.patch.object(type(screen), "grabWindow", fake_grab):
            shot = o.capture_region_pixmap().toImage()
        outer = o.drawable_rect()
        self.assertEqual(outer, QRect(300, 300, 800, 600))
        self.assertEqual((shot.width(), shot.height()), (round(800 * dpr), round(600 * dpr)))
        self.assertEqual(shot.pixelColor(int(50 * dpr), int(300 * dpr)).getRgb()[:3], (255, 255, 255),
                         "margin in the screenshot")
        self.assertEqual(shot.pixelColor(int(400 * dpr), int(300 * dpr)).getRgb()[:3], (10, 200, 10),
                         "region in the screenshot")

    def test_margin_is_solid_at_fractional_scale(self):
        # The margin used to be four bands, each rounding its own edges at
        # 150%, which left part-filled pixels along the lines where they met.
        o = self.o
        o.set_margin_color("white")
        o.set_margin_opacity(1.0)
        o.set_margin_size(37)
        region = QRect(401, 303, 517, 389)
        o.set_region(region)
        outer = o.drawable_rect()
        img = o.grab().toImage()
        d = img.devicePixelRatio()
        self.assertEqual(d, 1.5)
        # Device pixels 2 px clear of the margin's inner and outer edges
        # (and of the dashed region frame).
        ox0, oy0 = int(outer.left() * d) + 2, int(outer.top() * d) + 2
        ox1, oy1 = int((outer.right() + 1) * d) - 2, int((outer.bottom() + 1) * d) - 2
        rx0, ry0 = int(region.left() * d) - 2, int(region.top() * d) - 2
        rx1, ry1 = int((region.right() + 1) * d) + 2, int((region.bottom() + 1) * d) + 2
        bad = [(x, y) for y in range(oy0, oy1) for x in range(ox0, ox1)
               if not (rx0 <= x < rx1 and ry0 <= y < ry1) and img.pixel(x, y) != 0xFFFFFFFF]
        self.assertEqual(bad[:5], [], f"{len(bad)} margin pixels not solid")

    def test_margin_controls_disabled_without_an_area(self):
        o = self.o
        popup = self.open_popup()
        for widget in (popup.margin_btn, popup.size_slider, popup.opacity_slider, popup.white_btn):
            self.assertFalse(widget.isEnabled())
        self.assertIn("Select an area", popup.note.text())
        o.set_region(QRect(400, 400, 600, 400))
        for widget in (popup.margin_btn, popup.size_slider, popup.opacity_slider, popup.white_btn):
            self.assertTrue(widget.isEnabled())

    def test_toggle_label_tracks_state(self):
        self.region_only()
        popup = self.open_popup()
        self.assertEqual(popup.margin_btn.text(), "Margin: off")
        popup.margin_btn.click()
        self.assertTrue(self.o.margin_enabled)
        self.assertEqual(popup.margin_btn.text(), "Margin: on")
        popup.margin_btn.click()
        self.assertFalse(self.o.margin_enabled)
        self.assertEqual(popup.margin_btn.text(), "Margin: off")

    def test_margin_changes_repaint_immediately(self):
        o = self.o
        self.region_only()
        o.set_margin_size(40)
        before = o.drawable_rect()
        with mock.patch.object(o, "update") as update:
            o.set_margin_size(120)
        update.assert_called_once()
        (dirty,), _ = update.call_args
        self.assertTrue(dirty.contains(before) and dirty.contains(o.drawable_rect()))
        self.assertFalse(o._backdrop_rebuild_timer.isActive(), "no restarting debounce")

    def test_draw_and_erase_in_margin_keep_backdrop_and_margin(self):
        o = self.o
        o._set_backdrop("black")
        o._set_backdrop_opacity(100)
        o.set_margin_opacity(1.0)
        o.set_margin_color("white")
        self.set_area(margin=True)
        o._set_color("#ff0000")
        self.stroke((350, 600), dx=10, n=12)  # from the margin into the region
        self.assertEqual(self.painted(360, 600)[:3], (255, 0, 0))
        self.assertEqual(self.painted(440, 600)[:3], (255, 0, 0))
        o._set_mode("eraser")
        self.stroke((350, 600), dx=10, n=12)
        self.assertEqual(self.painted(360, 600), (255, 255, 255, 255), "margin layer intact")
        self.assertEqual(self.painted(440, 600), (0, 0, 0, 255), "backdrop layer intact")


if __name__ == "__main__":
    unittest.main()
