"""Tests needing Qt. Run on the offscreen platform, so no window appears and
they work on a build agent without a desktop."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from tests import qt_env  # must come before other PySide6 imports

from PySide6.QtCore import QPoint, QPointF, QSettings, QSize  # noqa: E402

from crit_overlay import brush  # noqa: E402
from crit_overlay import constants as C  # noqa: E402
from crit_overlay import dab  # noqa: E402
from crit_overlay.config import SETTINGS_VERSION, AppConfig  # noqa: E402
from crit_overlay.models import Stroke  # noqa: E402

# One QApplication per process, on the offscreen platform with fake monitors.
APP = qt_env.app


class LayerTests(unittest.TestCase):
    def test_new_layer_matches_device_pixels(self):
        layer = brush.new_layer(QSize(100, 50), 1.75)
        self.assertEqual((layer.width(), layer.height()), (175, 88))
        self.assertAlmostEqual(layer.devicePixelRatio(), 1.75)
        self.assertEqual(layer.toImage().pixelColor(5, 5).alpha(), 0)

    def test_logical_rect_round_trips(self):
        layer = brush.new_layer(QSize(100, 50), 2.0)
        self.assertEqual(brush.logical_rect(QPoint(10, 20), layer).size(), QSize(100, 50))

    def test_stroke_layer_resolution_scales_with_dpr(self):
        stroke = Stroke("#ff0000", 12, 1.0)
        stroke.points = [(QPointF(10, 10), 0.6), (QPointF(60, 30), 0.6), (QPointF(120, 15), 0.6)]
        segs = brush.stroke_segments(stroke)
        one, origin1 = brush.render_stroke_layer(stroke, segs, 1.0)
        two, origin2 = brush.render_stroke_layer(stroke, segs, 2.0)
        self.assertEqual(origin1, origin2)
        self.assertEqual(brush.logical_rect(origin1, one), brush.logical_rect(origin2, two))
        self.assertEqual(two.width(), one.width() * 2)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.ini"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        cfg = AppConfig(self.path)
        cfg.save(color="#123456", size=20, opacity=0.5, smoothing=0.3, backdrop="black", backdrop_opacity=0.7)
        loaded = AppConfig(self.path).load()
        self.assertEqual(loaded["color"], "#123456")
        self.assertEqual(loaded["size"], 20)
        self.assertEqual(loaded["backdrop"], "black")

    def test_garbage_values_fall_back_to_defaults(self):
        self.path.write_text(
            "[brush]\ncolor=notacolor\nsize=9999\nopacity=abc\nbackdrop=purple\nbackdrop_opacity=-3\n",
            encoding="utf-8",
        )
        loaded = AppConfig(self.path).load()
        self.assertEqual(loaded["color"], C.DEFAULT_COLOR)
        self.assertEqual(loaded["size"], C.MAX_SIZE)
        self.assertEqual(loaded["opacity"], C.DEFAULT_OPACITY)
        self.assertIsNone(loaded["backdrop"])
        self.assertEqual(loaded["backdrop_opacity"], 0.1)

    def test_compat_section_survives_a_save(self):
        self.path.write_text("[compat]\ninput=mouse\n", encoding="utf-8")
        AppConfig(self.path).save(color="#ffffff", size=3, opacity=1, smoothing=0, backdrop=None, backdrop_opacity=0.5)
        self.assertIn("input=mouse", self.path.read_text(encoding="utf-8"))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "Crit Overlay.ini"

    def tearDown(self):
        if self.path.exists():
            os.chmod(self.path, stat.S_IWRITE)
        self.tmp.cleanup()

    def _write_like_v142(self):
        # Exactly the calls v1.0-v1.4.2's AppConfig.save() made.
        q = QSettings(str(self.path), QSettings.Format.IniFormat)
        q.setValue("brush/color", "#4dabf7")
        q.setValue("brush/size", 23)
        q.setValue("brush/opacity", 0.8)
        q.setValue("brush/smoothing", 0.25)
        q.setValue("brush/backdrop", "white")
        q.setValue("brush/backdrop_opacity", 0.65)
        q.sync()

    def test_v142_settings_carry_over(self):
        self._write_like_v142()
        cfg = AppConfig(self.path)
        self.assertEqual(cfg.migrated_from, 1)
        self.assertEqual(cfg.load(), {
            "color": "#4dabf7", "size": 23, "opacity": 0.8, "smoothing": 0.25,
            "backdrop": "white", "backdrop_opacity": 0.65,
            # Added after v1.4.2 (#10): absent from old files, so defaults.
            "margin_enabled": C.DEFAULT_MARGIN_ENABLED, "margin_size": C.DEFAULT_MARGIN_SIZE,
            "margin_color": C.DEFAULT_MARGIN_COLOR, "margin_opacity": C.DEFAULT_MARGIN_OPACITY,
            # Text tool prototype: absent from old files, so defaults.
            "text_family": "IBM Plex Sans", "text_size": C.DEFAULT_TEXT_SIZE, "text_bold": False,
            "text_align": "left",
            # Brush engine prototype: also new, so defaults.
            "brush_preset": dab.DEFAULT_PRESET, "eraser_size": C.DEFAULT_ERASER_SIZE,
            "brush_tuning": {k: (p.hardness, p.spacing) for k, p in dab.PRESETS.items()},
            "pressure_curve": "normal", "eraser_style": "hard",
        })
        q = QSettings(str(self.path), QSettings.Format.IniFormat)
        self.assertEqual(int(q.value("meta/settings_version")), SETTINGS_VERSION)
        self.assertIsNone(AppConfig(self.path).migrated_from, "migration must run once")

    def test_v150_spanning_modes_become_cursor(self):
        for legacy in ("all", "auto"):
            self.path.write_text(f"[brush]\nsize=7\n[compat]\nscreens={legacy}\ninput=mouse\n", encoding="utf-8")
            AppConfig(self.path)
            text = self.path.read_text(encoding="utf-8")
            self.assertIn("screens=cursor", text)
            self.assertIn("input=mouse", text)
            self.assertEqual(AppConfig(self.path).load()["size"], 7)

    def test_newer_file_is_left_alone(self):
        self.path.write_text("[meta]\nsettings_version=9\n[compat]\nscreens=all\n", encoding="utf-8")
        self.assertIsNone(AppConfig(self.path).migrated_from)
        self.assertIn("screens=all", self.path.read_text(encoding="utf-8"))

    def test_read_only_file_still_loads(self):
        self._write_like_v142()
        os.chmod(self.path, stat.S_IREAD)
        cfg = AppConfig(self.path)
        self.assertEqual(cfg.load()["size"], 23)

    def test_missing_file_gets_defaults(self):
        self.assertEqual(AppConfig(self.path).load()["size"], C.DEFAULT_SIZE)


if __name__ == "__main__":
    unittest.main()
