import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crit_overlay import paths


class WritableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_writable_creates_and_cleans_up(self):
        target = self.dir / "a" / "b"
        self.assertTrue(paths.is_writable_dir(target))
        self.assertEqual(list(target.iterdir()), [])

    def test_a_file_in_the_way_is_not_writable(self):
        blocker = self.dir / "blocker"
        blocker.write_text("x")
        self.assertFalse(paths.is_writable_dir(blocker / "sub"))

    def test_first_writable_skips_bad_candidates(self):
        blocker = self.dir / "blocker"
        blocker.write_text("x")
        good = self.dir / "good"
        self.assertEqual(paths.first_writable([None, blocker / "sub", good]), good)

    def test_known_folder_resolves_local_appdata(self):
        result = paths.known_folder(paths.FOLDERID_LOCAL_APPDATA)
        self.assertIsNotNone(result)
        self.assertTrue(result.is_dir())


class AppPathsTests(unittest.TestCase):
    """AppPaths with the user profile replaced by temp dirs, including the
    'redirected folders unavailable' case."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.local = self.dir / "Local"
        self.roaming = self.dir / "Roaming"
        self.pics = self.dir / "Pictures"

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, folders, portable=False, app_dir=None):
        def fake_known(fid):
            return folders.get(fid)

        with mock.patch.object(paths, "known_folder", side_effect=fake_known), \
                mock.patch.object(paths, "app_dir", return_value=app_dir or (self.dir / "app")), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": "", "APPDATA": ""}):
            return paths.AppPaths(portable=portable)

    def test_normal_profile(self):
        p = self._build({
            paths.FOLDERID_LOCAL_APPDATA: self.local,
            paths.FOLDERID_ROAMING_APPDATA: self.roaming,
            paths.FOLDERID_PICTURES: self.pics,
        })
        self.assertEqual(p.log_dir, self.local / "CritOverlay" / "logs")
        self.assertEqual(p.settings_path, self.roaming / "CritOverlay" / "Crit Overlay.ini")
        cands = p.screenshot_dir_candidates()
        self.assertEqual(cands[0], self.pics / "CritOverlay")
        self.assertIn(self.local / "CritOverlay" / "Screenshots", cands)

    def test_unwritable_roaming_falls_back_to_local(self):
        blocker = self.dir / "Roaming"
        blocker.write_text("not a dir")  # Roaming 'share' is unusable
        p = self._build({paths.FOLDERID_LOCAL_APPDATA: self.local, paths.FOLDERID_ROAMING_APPDATA: blocker})
        self.assertEqual(p.settings_path, self.local / "CritOverlay" / "Crit Overlay.ini")

    def test_no_known_folders_still_gets_a_log_dir(self):
        p = self._build({})
        self.assertIsNotNone(p.log_dir)
        self.assertIsNotNone(p.settings_path)

    def test_override_comes_first(self):
        p = self._build({paths.FOLDERID_PICTURES: self.pics})
        self.assertEqual(p.screenshot_dir_candidates(self.dir / "mine")[0], self.dir / "mine")

    def test_portable_keeps_everything_beside_the_app(self):
        app = self.dir / "usb" / "CritOverlay"
        app.mkdir(parents=True)
        p = self._build({paths.FOLDERID_LOCAL_APPDATA: self.local}, portable=True, app_dir=app)
        self.assertTrue(p.portable)
        root = app / paths.PORTABLE_DATA_DIR
        self.assertEqual(p.data_dir, root)
        self.assertEqual(p.log_dir, root / "logs")
        self.assertEqual(p.settings_path, root / "Crit Overlay.ini")
        self.assertEqual(p.screenshot_dir_candidates()[0], root / "Screenshots")

    def test_read_only_portable_location_falls_back(self):
        app = self.dir / "ro"
        app.mkdir()
        (app / paths.PORTABLE_DATA_DIR).write_text("blocked")
        p = self._build({paths.FOLDERID_LOCAL_APPDATA: self.local}, portable=True, app_dir=app)
        self.assertFalse(p.portable)
        self.assertTrue(any("Portable mode" in n for n in p.notes))
        self.assertEqual(p.log_dir, self.local / "CritOverlay" / "logs")


if __name__ == "__main__":
    unittest.main()
