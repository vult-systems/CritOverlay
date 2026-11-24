"""Single-monitor overlay behaviour.

choose_monitor/next_monitor are tested as pure functions, including the
hot-plug rules. The Overlay itself runs on two fake offscreen monitors (a
150% laptop and a 100% projector, see qt_env.py). Real hot-plugging can't
be faked from Python, so those tests drive the overlay's own screenAdded/
screenRemoved handlers directly; plugging a real projector is on the
manual checklist.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import qt_env  # noqa: F401  (must come before other PySide6 imports)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from crit_overlay import compat  # noqa: E402
from crit_overlay import overlay as overlay_module  # noqa: E402
from crit_overlay.config import AppConfig  # noqa: E402


class ChooseMonitorTests(unittest.TestCase):
    S = ["laptop", "projector", "tv"]

    def choose(self, **kw):
        args = dict(screens=self.S, primary="laptop", cursor="projector", mode="cursor")
        args.update(kw)
        return compat.choose_monitor(**args)

    def test_starts_under_cursor_or_on_primary(self):
        self.assertEqual(self.choose(), "projector")
        self.assertEqual(self.choose(mode="primary"), "laptop")

    def test_no_cursor_screen_falls_back_to_primary(self):
        self.assertEqual(self.choose(cursor=None), "laptop")

    def test_display_change_keeps_current_monitor(self):
        self.assertEqual(self.choose(previous="tv", cursor="laptop"), "tv")

    def test_primary_mode_keeps_a_monitor_the_user_chose(self):
        self.assertEqual(self.choose(mode="primary", previous="tv", user_chose=True), "tv")
        self.assertEqual(self.choose(mode="primary", previous="tv", user_chose=False), "laptop")

    def test_disconnected_monitor_returns_to_primary(self):
        self.assertEqual(self.choose(previous=None, previous_lost=True, cursor="tv"), "laptop")

    def test_primary_gone_uses_first_monitor(self):
        self.assertEqual(self.choose(primary=None, cursor=None), "laptop")
        self.assertEqual(compat.choose_monitor(["tv"], "laptop", None, "primary"), "tv")

    def test_no_monitors(self):
        self.assertIsNone(compat.choose_monitor([], None, None, "cursor"))

    def test_next_monitor_wraps(self):
        self.assertEqual(compat.next_monitor(self.S, "laptop"), "projector")
        self.assertEqual(compat.next_monitor(self.S, "tv"), "laptop")
        self.assertEqual(compat.next_monitor(self.S, None), "laptop")
        self.assertIsNone(compat.next_monitor(["laptop"], "laptop"))


class LegacyScreenOptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_cursor(self):
        self.assertEqual(compat.parse_options([], app_ini=self.dir / "none.ini").screens, "cursor")

    def test_v150_values_map_to_cursor_with_a_warning(self):
        for legacy in ("auto", "all"):
            ini = self.dir / "app.ini"
            ini.write_text(f"[compat]\nscreens = {legacy}\n", encoding="utf-8")
            opts = compat.parse_options([], app_ini=ini)
            self.assertEqual(opts.screens, "cursor")
            self.assertTrue(any("older version" in w for w in opts.warnings))
            opts = compat.parse_options(["--screens", legacy], app_ini=self.dir / "none.ini")
            self.assertEqual(opts.screens, "cursor")
            self.assertTrue(any("older version" in w for w in opts.warnings))


class OverlayMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = Path(self.tmp.name) / "settings.ini"
        patcher = mock.patch.object(overlay_module, "AppConfig", lambda: AppConfig(settings))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.screens = {s.name(): s for s in QGuiApplication.screens()}
        self.assertEqual(set(self.screens), {"laptop", "projector"}, "fake monitors not configured")
        self.overlay = overlay_module.Overlay(screen_mode="primary")
        self.toasts = []
        self.overlay.status_changed.connect(self.toasts.append)

    def tearDown(self):
        self.overlay._settings_save_timer.stop()
        self.overlay._pass_through_badge.close()
        self.overlay.deleteLater()
        qt_env.app.processEvents()
        self.tmp.cleanup()

    def on(self, name):
        o = self.overlay
        screen = self.screens[name]
        panel = o.panel.geometry().translated(o.geometry().topLeft())
        return (
            o._target_screen is screen
            and o.geometry() == screen.geometry()
            and screen.geometry().contains(panel)
            and abs(o._canvas.devicePixelRatio() - screen.devicePixelRatio()) < 1e-6
        )

    def stroke(self):
        o = self.overlay
        o._begin_stroke(QPointF(200, 200), 0.7)
        for i in range(20):
            o._add_point(QPointF(200 + i * 5, 200 + i * 2), 0.7)
        o._end_stroke()

    def test_starts_on_exactly_one_monitor_at_its_scale(self):
        self.assertTrue(self.on("laptop"))
        self.assertTrue(self.overlay.monitor_btn.isVisibleTo(self.overlay.panel))

    def test_toolbar_button_and_f7_cycle_monitors(self):
        o = self.overlay
        self.stroke()
        o.monitor_btn.click()
        self.assertTrue(self.on("projector"))
        self.assertEqual(len(o.history), 1, "annotations move with the overlay")
        self.assertIn("2 of 2", o.monitor_btn.toolTip())
        o.move_to_next_screen()
        self.assertTrue(self.on("laptop"))
        self.assertEqual(self.toasts[-2:], ["Monitor 2 of 2", "Monitor 1 of 2"])

    def test_switching_mid_stroke_commits_the_stroke(self):
        o = self.overlay
        o._begin_stroke(QPointF(100, 100), 0.7)
        o._add_point(QPointF(160, 130), 0.7)
        o.move_to_next_screen()
        self.assertIsNone(o.current)
        self.assertEqual(len(o.history), 1)

    def test_display_change_keeps_chosen_monitor(self):
        o = self.overlay
        o.move_to_next_screen()
        o._on_screens_changed()
        o._apply_screen_change()
        self.assertTrue(self.on("projector"))

    def test_unplugging_current_monitor_returns_to_primary(self):
        o = self.overlay
        o.move_to_next_screen()
        o._on_screen_removed(self.screens["projector"])
        self.assertIsNone(o._target_screen, "must drop the reference before Qt deletes the QScreen")
        o._apply_screen_change()
        self.assertTrue(self.on("laptop"))
        self.assertFalse(o._user_chose_screen)
        self.assertTrue(self.toasts[-1].startswith("Monitor disconnected"))

    def test_unplugging_another_monitor_stays_put(self):
        o = self.overlay
        o._on_screen_removed(self.screens["projector"])
        o._apply_screen_change()
        self.assertTrue(self.on("laptop"))

    def test_plugging_in_announces_switching(self):
        o = self.overlay
        o._screen_count = 1
        o._on_screen_added(self.screens["projector"])
        o._apply_screen_change()
        self.assertTrue(self.on("laptop"))
        self.assertTrue(self.toasts[-1].startswith("Monitor connected"))

    def test_bursts_of_display_events_are_coalesced(self):
        o = self.overlay
        with mock.patch.object(o, "_apply_screen_change") as apply:
            for _ in range(5):
                o._on_screens_changed()
            self.assertTrue(o._screen_change_timer.isActive())
            apply.assert_not_called()

    def test_pass_through_round_trip_and_monitor_switch(self):
        o = self.overlay
        o.show()
        o.pass_btn.click()
        self.assertTrue(o.pass_through)
        self.assertFalse(o.panel.isVisible())
        self.assertTrue(o._pass_through_badge.isVisible())
        o.move_to_next_screen()
        badge = o._pass_through_badge.frameGeometry()
        self.assertTrue(self.screens["projector"].geometry().contains(badge))
        o._pass_through_badge.clicked.emit()
        self.assertFalse(o.pass_through)
        self.assertTrue(o.panel.isVisible())
        self.assertFalse(o._pass_through_badge.isVisible())
        self.assertFalse(o.pass_btn.isChecked())
        o.hide()

    def test_cursor_mode_starts_under_the_mouse(self):
        with mock.patch.object(QGuiApplication, "screenAt", return_value=self.screens["projector"]):
            o = overlay_module.Overlay(screen_mode="cursor")
        try:
            self.assertIs(o._target_screen, self.screens["projector"])
        finally:
            o._settings_save_timer.stop()
            o._pass_through_badge.close()
            o.deleteLater()


if __name__ == "__main__":
    unittest.main()
