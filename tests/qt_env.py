"""Shared Qt setup for the tests: one QApplication per process, on the
offscreen platform (no windows appear, works without a desktop), with two
fake monitors: a 150% laptop and a 100% projector.

Import this before anything else from PySide6.
"""

import os
from pathlib import Path

_config = Path(__file__).with_name("offscreen_screens.json")
# Relative and with forward slashes: Qt splits platform options on ':', so
# an absolute path's drive letter would break it.
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + os.path.relpath(_config).replace("\\", "/")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
