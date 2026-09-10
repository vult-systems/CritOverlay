"""Rotating file logger.

Set up once, writing to the per-app data directory under %LOCALAPPDATA%
with rotation, so a long session can't fill the disk.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import constants as C


def _log_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / C.ORG_NAME / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(debug=False):
    log_path = _log_dir() / "crit_overlay.log"

    logger = logging.getLogger(C.ORG_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(file_handler)

    if debug:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(console_handler)

    logger.info("Logging to %s", log_path)
    return logger
