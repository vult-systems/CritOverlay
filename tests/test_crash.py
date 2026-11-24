import os
import tempfile
import unittest
from pathlib import Path

from crit_overlay import crash


class SessionMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        crash._state["marker"] = None
        self.tmp.cleanup()

    def test_dead_process_marker_is_reported_once(self):
        # PIDs are multiples of 4 on Windows, so 3 can never be alive.
        (self.dir / "session.3.running").write_text("pid=3 started=yesterday", encoding="utf-8")
        self.assertEqual(crash.begin_session(self.dir), ["pid=3 started=yesterday"])
        crash.end_session()
        self.assertEqual(crash.begin_session(self.dir), [])
        crash.end_session()
        self.assertEqual(list(self.dir.glob("session.*.running")), [])

    def test_live_second_instance_is_not_unclean(self):
        # Our own parent process stands in for a second running instance.
        other = os.getppid()
        (self.dir / f"session.{other}.running").write_text("other instance", encoding="utf-8")
        self.assertEqual(crash.begin_session(self.dir), [])
        crash.end_session()
        self.assertTrue((self.dir / f"session.{other}.running").exists())


class WatchdogLogTests(unittest.TestCase):
    def _log(self, text):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def test_detects_watchdog_timeout_in_last_session(self):
        path = self._log(
            "--- session start a ---\nstartup watchdog armed (60s)\nstartup completed\n"
            "--- session start b ---\nstartup watchdog armed (60s)\nTimeout (0:01:00)!\nThread ...\n"
        )
        self.assertTrue(crash._last_session_hung(path))

    def test_old_timeouts_do_not_count(self):
        path = self._log(
            "--- session start a ---\nstartup watchdog armed (60s)\nTimeout (0:01:00)!\n"
            "--- session start b ---\nstartup watchdog armed (60s)\nstartup completed\n"
        )
        self.assertFalse(crash._last_session_hung(path))

    def test_missing_log(self):
        self.assertFalse(crash._last_session_hung(Path(tempfile.gettempdir()) / "no-such-faulthandler.log"))


if __name__ == "__main__":
    unittest.main()
