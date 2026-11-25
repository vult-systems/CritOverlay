"""Hide to tray, global hotkey, single instance and Start with Windows (#14)."""

import ctypes
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import qt_env  # must come before other PySide6 imports

from PySide6.QtCore import QPointF, QRect, QTimer  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from crit_overlay import app as app_module  # noqa: E402
from crit_overlay import autostart  # noqa: E402
from crit_overlay import compat  # noqa: E402
from crit_overlay import hotkeys  # noqa: E402
from crit_overlay import instance  # noqa: E402
from crit_overlay import overlay as overlay_module  # noqa: E402
from crit_overlay.config import AppConfig  # noqa: E402

APP = qt_env.app


def _process_until(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class HotkeyParseTests(unittest.TestCase):
    def test_default_and_spelling_variants(self):
        mods, vk, name = hotkeys.parse("Ctrl+Alt+D")
        self.assertEqual(mods, hotkeys.MOD_CONTROL | hotkeys.MOD_ALT)
        self.assertEqual(vk, ord("D"))
        self.assertEqual(name, "Ctrl+Alt+D")
        self.assertEqual(hotkeys.parse(" alt + control + d ")[2], "Ctrl+Alt+D")
        self.assertEqual(hotkeys.parse("win+shift+f12")[:2], (hotkeys.MOD_WIN | hotkeys.MOD_SHIFT, 0x7B))
        self.assertEqual(hotkeys.parse("Ctrl+Alt+PgDn")[1:], (0x22, "Ctrl+Alt+PageDown"))
        self.assertEqual(hotkeys.parse("Ctrl+7")[1], ord("7"))

    def test_disabled(self):
        for text in ("none", "", "  ", "Off", None):
            self.assertIsNone(hotkeys.parse(text))
        self.assertIsNone(hotkeys.describe("none"))

    def test_rejects_combinations_that_would_block_typing_or_are_malformed(self):
        for text in ("D", "Shift+D", "Ctrl+Alt", "Ctrl++D", "Hyper+D", "Ctrl+Alt+Tab", "Ctrl+F25", "Ctrl+é"):
            with self.subTest(text=text), self.assertRaises(hotkeys.HotkeyError):
                hotkeys.parse(text)
        self.assertIsNone(hotkeys.describe("Shift+D"))


class OptionTests(unittest.TestCase):
    def _parse(self, argv, ini_text=None):
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "CritOverlay.ini"
            if ini_text:
                ini.write_text(ini_text, encoding="utf-8")
            return compat.parse_options(argv, app_ini=ini)

    def test_defaults(self):
        opts = self._parse([])
        self.assertEqual(opts.hotkey, "Ctrl+Alt+D")
        self.assertFalse(opts.hidden)

    def test_ini_and_command_line(self):
        opts = self._parse(["--hidden"], "[compat]\nhotkey = Ctrl+Shift+F10\n")
        self.assertEqual(opts.hotkey, "Ctrl+Shift+F10")
        self.assertTrue(opts.hidden)
        self.assertEqual(self._parse(["--hotkey", "none"], "[compat]\nhotkey = Ctrl+Shift+F10\n").hotkey, "none")

    def test_invalid_ini_hotkey_keeps_default_with_warning(self):
        opts = self._parse([], "[compat]\nhotkey = Shift+D\n")
        self.assertEqual(opts.hotkey, "Ctrl+Alt+D")
        self.assertTrue(any("hotkey" in w for w in opts.warnings))

    def test_invalid_command_line_hotkey_is_an_error(self):
        with self.assertRaises(compat.OptionsError):
            self._parse(["--hotkey", "D"])


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        tag = uuid.uuid4().hex
        self.names = (f"Local\\CritOverlayTest.{tag}.Instance", f"Local\\CritOverlayTest.{tag}.Show")
        saved = instance._current
        self.addCleanup(setattr, instance, "_current", saved)

    def test_second_acquire_sees_the_first_and_can_signal_it(self):
        first = instance.acquire(*self.names)
        self.addCleanup(first.close)
        self.assertFalse(first.already_running)
        self.assertIs(instance.current(), first)
        second = instance.acquire(*self.names)
        self.assertTrue(second.already_running)
        self.assertIs(instance.current(), first)
        WAIT_OBJECT_0 = 0
        wait = ctypes.windll.kernel32.WaitForSingleObject
        wait.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.assertNotEqual(wait(first.show_event, 0), WAIT_OBJECT_0)
        self.assertTrue(second.request_show())
        second.close()
        self.assertEqual(wait(first.show_event, 0), WAIT_OBJECT_0)

    def test_released_when_the_owner_closes(self):
        first = instance.acquire(*self.names)
        first.close()
        again = instance.acquire(*self.names)
        self.addCleanup(again.close)
        self.assertFalse(again.already_running)

    def test_show_request_reaches_the_running_overlay(self):
        owner = instance.acquire(*self.names)
        self.addCleanup(owner.close)
        overlay = mock.Mock()
        notifier = app_module._listen_for_show_requests(overlay)
        self.assertIsNotNone(notifier)
        instance.acquire(*self.names).request_show()
        self.assertTrue(_process_until(lambda: overlay.show_overlay.called))
        notifier.setEnabled(False)


class GlobalHotkeyTests(unittest.TestCase):
    def test_wm_hotkey_reaches_the_handler(self):
        hits = []
        event_filter = app_module.HotkeyFilter()
        APP.installNativeEventFilter(event_filter)
        self.addCleanup(APP.removeNativeEventFilter, event_filter)
        hk = app_module.GlobalHotkey(event_filter, 0xBEE, "Ctrl+Alt+Shift+F24", lambda: hits.append(1))
        problem = hk.register()
        if problem:
            self.skipTest(problem)
        self.addCleanup(hk.unregister)
        self.assertEqual(hk.display, "Ctrl+Alt+Shift+F24")
        self.assertTrue(hotkeys.post_test_hotkey(0xBEE))
        self.assertTrue(_process_until(lambda: hits == [1]))
        hk.unregister()
        hotkeys.post_test_hotkey(0xBEE)
        _process_until(lambda: False, timeout=0.2)
        self.assertEqual(hits, [1])

    def test_conflict_is_reported_not_raised(self):
        event_filter = app_module.HotkeyFilter()
        first = app_module.GlobalHotkey(event_filter, 0xBEF, "Ctrl+Alt+Shift+F23", lambda: None)
        if first.register():
            self.skipTest("Ctrl+Alt+Shift+F23 not available")
        self.addCleanup(first.unregister)
        second = app_module.GlobalHotkey(event_filter, 0xBF0, "Ctrl+Alt+Shift+F23", lambda: None)
        problem = second.register()
        self.assertIn("already used by another program", problem)
        self.assertIsNone(second.display)

    def test_disabled_and_invalid(self):
        event_filter = app_module.HotkeyFilter()
        self.assertIsNone(app_module.GlobalHotkey(event_filter, 0xBF1, "none", lambda: None).register())
        self.assertIn("doesn't block", app_module.GlobalHotkey(event_filter, 0xBF2, "Shift+X", lambda: None).register())


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = autostart.shortcut_path(Path(self.tmp.name) / "Startup")

    def test_off_by_default_then_on_then_off(self):
        command = autostart.launch_command()
        self.assertFalse(autostart.is_enabled(self.path, command))
        autostart.enable(self.path, command)
        target, args = autostart.read_shortcut(self.path)
        self.assertEqual(Path(target).resolve(), Path(command[0]).resolve())
        self.assertIn("--hidden", args)
        self.assertTrue(autostart.is_enabled(self.path, command))
        autostart.disable(self.path)
        self.assertFalse(self.path.exists())
        self.assertFalse(autostart.is_enabled(self.path, command))
        autostart.disable(self.path)  # already off: no error

    def test_shortcut_to_another_copy_counts_as_off(self):
        autostart.enable(self.path, autostart.launch_command())
        other = (r"C:\Elsewhere\CritOverlay.exe", "--hidden", r"C:\Elsewhere")
        self.assertFalse(autostart.is_enabled(self.path, other))

    def test_portable_copies_keep_portable(self):
        self.assertIn("--portable", autostart.launch_command(portable=True)[1])


class TrayMenuTests(unittest.TestCase):
    def test_start_with_windows_item_reports_its_new_state(self):
        from PySide6.QtGui import QIcon

        from crit_overlay.tray import TrayIcon

        tray = TrayIcon(QIcon())
        seen = []
        tray.autostart_toggled.connect(seen.append)
        self.assertFalse(tray.autostart_action.isChecked())
        tray.autostart_action.trigger()
        tray.autostart_action.trigger()
        self.assertEqual(seen, [True, False])
        tray.set_hotkey("Ctrl+Alt+D")
        self.assertEqual(tray.show_hide_action.text(), "Show/Hide (Ctrl+Alt+D)")
        tray.set_hotkey(None)
        self.assertEqual(tray.show_hide_action.text(), "Show/Hide")


class HideToTrayTests(unittest.TestCase):
    """The overlay is shown here (unlike the region tests): hiding is what's
    under test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Path(self.tmp.name) / "settings.ini"
        patcher = mock.patch.object(overlay_module, "AppConfig", lambda: AppConfig(self.settings))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.o = overlay_module.Overlay(screen_mode="primary")
        self.o.set_tray_available(True, "Ctrl+Alt+D")
        self.o.show()
        APP.processEvents()

    def tearDown(self):
        self.o._settings_save_timer.stop()
        self.o._pass_through_badge.close()
        if self.o._region_popup is not None:
            self.o._region_popup.close()
        self.o.hide()
        self.o.deleteLater()
        APP.processEvents()
        self.tmp.cleanup()

    def stroke(self, y=300):
        self.o._begin_stroke(QPointF(450, y), 0.7)
        for i in range(1, 15):
            self.o._add_point(QPointF(450 + i * 8, y), 0.7)
        self.o._end_stroke()

    def test_escape_hides_and_keeps_everything(self):
        o = self.o
        o.set_margin_size(80)
        o.set_region(QRect(400, 250, 500, 300))
        o._set_backdrop("black")
        self.stroke()
        hidden = []
        o.hidden_to_tray.connect(lambda: hidden.append(1))
        canvas_before = o._canvas.toImage()
        with mock.patch.object(QApplication, "quit") as quit_:
            o._escape()
        quit_.assert_not_called()
        self.assertFalse(o.isVisible())
        self.assertEqual(hidden, [1])
        o.show_overlay()
        self.assertTrue(o.isVisible())
        self.assertEqual(len(o.history.strokes), 1)
        self.assertEqual(o.region, QRect(400, 250, 500, 300))
        self.assertTrue(o.margin_enabled)
        self.assertEqual(o.margin_size, 80)
        self.assertEqual(o.backdrop, "black")
        self.assertEqual(o._canvas.toImage(), canvas_before)
        o.undo()
        self.assertEqual(len(o.history.strokes), 0)

    def test_toolbar_button_hides_too(self):
        self.o.exit_btn.click()
        self.assertFalse(self.o.isVisible())
        self.assertIn("Hide (Esc)", self.o.exit_btn.toolTip())
        self.assertIn("Ctrl+Alt+D", self.o.exit_btn.toolTip())

    def test_without_a_tray_escape_exits(self):
        self.o.set_tray_available(False)
        self.assertEqual(self.o.exit_btn.toolTip(), "Exit (Esc)")
        with mock.patch.object(QApplication, "quit") as quit_:
            self.o._escape()
        quit_.assert_called_once()

    def test_toggle(self):
        self.o.toggle_visibility()
        self.assertFalse(self.o.isVisible())
        self.o.toggle_visibility()
        self.assertTrue(self.o.isVisible())

    def test_hiding_mid_stroke_keeps_the_stroke(self):
        o = self.o
        o._begin_stroke(QPointF(450, 300), 0.7)
        for i in range(1, 10):
            o._add_point(QPointF(450 + i * 8, 300), 0.7)
        o.hide_to_tray()
        self.assertIsNone(o.current)
        self.assertEqual(len(o.history.strokes), 1)

    def test_hiding_cancels_a_selection_and_closes_popups(self):
        o = self.o
        o._show_region_popup()
        o.begin_region_selection()
        o.hide_to_tray()
        self.assertFalse(o._selecting)
        self.assertFalse(o._region_popup.isVisible())

    def test_pass_through_survives_hide_and_show(self):
        o = self.o
        o.toggle_pass_through()
        self.assertTrue(o._pass_through_badge.isVisible())
        o.hide_to_tray()
        self.assertFalse(o._pass_through_badge.isVisible())
        o.show_overlay()
        self.assertTrue(o.pass_through)
        self.assertTrue(o._pass_through_badge.isVisible())
        o.toggle_pass_through()

    def test_pass_through_from_the_tray_while_hidden_shows_the_overlay(self):
        o = self.o
        o.hide_to_tray()
        o.toggle_pass_through()
        self.assertTrue(o.isVisible())
        self.assertTrue(o.pass_through)
        o.toggle_pass_through()

    def test_hiding_saves_settings(self):
        self.o._set_size(33)
        self.assertTrue(self.o._settings_save_timer.isActive())
        self.o.hide_to_tray()
        self.assertFalse(self.o._settings_save_timer.isActive())
        self.assertEqual(AppConfig(self.settings).load()["size"], 33)

    def test_nothing_ticks_while_hidden(self):
        o = self.o
        o._toast("hello")
        o.hide_to_tray()
        active = [t for t in o.findChildren(QTimer) if t.isActive()]
        self.assertEqual(active, [])

    def test_alt_f4_hides_but_quit_closes(self):
        o = self.o
        spontaneous = QCloseEvent()
        with mock.patch.object(QCloseEvent, "spontaneous", return_value=True):
            o.closeEvent(spontaneous)
        self.assertFalse(spontaneous.isAccepted())
        self.assertFalse(o.isVisible())
        o.show_overlay()
        from_quit = QCloseEvent()
        o.closeEvent(from_quit)
        self.assertTrue(from_quit.isAccepted())


if __name__ == "__main__":
    unittest.main()
