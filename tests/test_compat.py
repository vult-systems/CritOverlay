import os
import tempfile
import unittest
from pathlib import Path

from crit_overlay import compat


def _write(path, text):
    Path(path).write_text(text, encoding="utf-8")
    return path


class ParseOptionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.missing = self.dir / "missing.ini"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        opts = compat.parse_options([], app_ini=self.missing)
        self.assertEqual((opts.input, opts.screens, opts.safe_mode, opts.portable), ("auto", "cursor", False, False))
        self.assertTrue(opts.dialogs)

    def test_layers_app_ini_then_user_ini_then_cli(self):
        app_ini = _write(self.dir / "app.ini", "[compat]\ninput = mouse\nscreens = primary\nportable = true\n")
        user_ini = _write(self.dir / "user.ini", "[compat]\nscreens = all\n")
        seen = {}

        def resolver(o):
            seen["portable"] = o.portable
            return user_ini

        opts = compat.parse_options(["--input", "auto"], app_ini=app_ini, user_ini_resolver=resolver)
        self.assertTrue(seen["portable"], "portable must be known before the user file is located")
        self.assertEqual(opts.input, "auto")
        self.assertEqual(opts.sources["input"], "command line")
        self.assertEqual(opts.screens, "cursor")
        self.assertEqual(opts.sources["screens"], str(user_ini))

    def test_user_ini_cannot_set_portable(self):
        user_ini = _write(self.dir / "user.ini", "[compat]\nportable = true\n")
        opts = compat.parse_options([], app_ini=self.missing, user_ini_resolver=lambda o: user_ini)
        self.assertFalse(opts.portable)

    def test_qsettings_style_file_is_readable(self):
        # QSettings writes '%' escapes and can repeat keys; neither may break
        # startup.
        user_ini = _write(
            self.dir / "user.ini",
            "[General]\nx=%U4e2d\n[brush]\ncolor=#ff4b4b\ncolor=#ff4b4c\n[compat]\ninput=mouse\n",
        )
        opts = compat.parse_options([], app_ini=self.missing, user_ini_resolver=lambda o: user_ini)
        self.assertEqual(opts.input, "mouse")

    def test_invalid_ini_values_are_warnings_not_errors(self):
        app_ini = _write(self.dir / "app.ini", "[compat]\ninput = pen\nsafe_mode = maybe\nwhatever = 1\n")
        opts = compat.parse_options([], app_ini=app_ini)
        self.assertEqual(opts.input, "auto")
        self.assertFalse(opts.safe_mode)
        self.assertEqual(len(opts.warnings), 3)

    def test_unreadable_ini_is_a_warning(self):
        bad = self.dir / "bad.ini"
        bad.write_bytes(b"[compat\n\xff\xfe garbage")
        opts = compat.parse_options([], app_ini=bad)
        self.assertTrue(opts.warnings)

    def test_safe_mode_forces_conservative_choices(self):
        opts = compat.parse_options(["--safe-mode", "--screens", "cursor"], app_ini=self.missing)
        self.assertEqual((opts.input, opts.screens), ("mouse", "primary"))

    def test_selftest_disables_dialogs(self):
        self.assertFalse(compat.parse_options(["--selftest"], app_ini=self.missing).dialogs)

    def test_unknown_args_pass_through_to_qt(self):
        opts = compat.parse_options(["-platform", "windows:darkmode=1", "--debug"], app_ini=self.missing)
        self.assertTrue(opts.debug)
        self.assertEqual(opts.qt_args, ["-platform", "windows:darkmode=1"])

    def test_bad_choice_raises_options_error(self):
        with self.assertRaises(compat.OptionsError):
            compat.parse_options(["--input", "pen"], app_ini=self.missing)

    def test_help_raises_help_requested(self):
        with self.assertRaises(compat.HelpRequested) as cm:
            compat.parse_options(["--help"], app_ini=self.missing)
        self.assertIn("--safe-mode", str(cm.exception))

    def test_screenshot_dir_expands_env(self):
        os.environ["CRIT_TEST_DIR"] = str(self.dir)
        try:
            app_ini = _write(self.dir / "app.ini", "[compat]\nscreenshot_dir = %CRIT_TEST_DIR%\\shots\n")
            opts = compat.parse_options([], app_ini=app_ini)
            self.assertEqual(opts.screenshot_dir, f"{self.dir}\\shots")
        finally:
            del os.environ["CRIT_TEST_DIR"]


class EnvironmentTests(unittest.TestCase):
    def test_sanitize_removes_only_known_qt_vars(self):
        env = {"QT_PLUGIN_PATH": "C:/maya/qt-plugins", "QT_SCALE_FACTOR": "1.5", "QT_LOGGING_RULES": "x", "PATH": "p"}
        removed = compat.sanitize_qt_environment(environ=env, own_dirs=[])
        self.assertEqual({n for n, _ in removed}, {"QT_PLUGIN_PATH", "QT_SCALE_FACTOR"})
        self.assertEqual(set(env), {"QT_LOGGING_RULES", "PATH"})

    def test_keep_reports_without_removing(self):
        env = {"QT_PLUGIN_PATH": "x"}
        removed = compat.sanitize_qt_environment(keep=True, environ=env, own_dirs=[])
        self.assertEqual(removed, [("QT_PLUGIN_PATH", "x")])
        self.assertIn("QT_PLUGIN_PATH", env)

    def test_own_bundle_paths_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "_internal"
            env = {
                "QT_PLUGIN_PATH": str(own / "PySide6" / "plugins"),
                "QML2_IMPORT_PATH": f"{own / 'qml'}{os.pathsep}C:/maya/qml",
            }
            removed = compat.sanitize_qt_environment(environ=env, own_dirs=[own])
            self.assertEqual([n for n, _ in removed], ["QML2_IMPORT_PATH"])
            self.assertIn("QT_PLUGIN_PATH", env)

    def test_wintab_is_no_longer_an_input_mode(self):
        # Qt 6.8 removed the platform option behind it; see platform_spec.
        with self.assertRaises(compat.OptionsError):
            compat.parse_options(["--input", "wintab"], app_ini=Path(tempfile.gettempdir()) / "none.ini")

    def test_no_platform_spec_needed_by_default(self):
        self.assertIsNone(compat.platform_spec(compat.Options()))

    def test_explicit_platform_arg_wins(self):
        opts = compat.Options()
        before = os.environ.pop("QT_QPA_PLATFORM", None)
        try:
            self.assertIsNone(compat.apply_platform_spec(opts, ["-platform", "offscreen"]))
            self.assertNotIn("QT_QPA_PLATFORM", os.environ)
        finally:
            if before is not None:
                os.environ["QT_QPA_PLATFORM"] = before


if __name__ == "__main__":
    unittest.main()
