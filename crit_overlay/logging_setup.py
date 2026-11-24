"""Rotating file logger.

Writes to the first writable log directory paths.py finds (normally
%LOCALAPPDATA%\\CritOverlay\\logs), rotated so a long session can't fill the
disk. Never raises: if no file can be opened the app still runs, logging to
the console when there is one, and nowhere otherwise.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import constants as C
from . import paths

LOG_FILE_NAME = "crit_overlay.log"


def setup_logging(debug=False, app_paths=None):
    """Returns (logger, log_file_path or None)."""
    app_paths = app_paths or paths.current()
    logger = logging.getLogger(C.ORG_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    log_path = None
    if app_paths.log_dir is not None:
        candidate = app_paths.log_dir / LOG_FILE_NAME
        try:
            # Opened now (delay=False) so a locked or read-only file fails
            # here, where it's caught, rather than on the first record.
            handler = RotatingFileHandler(candidate, maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=False)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            logger.addHandler(handler)
            log_path = candidate
        except Exception:
            log_path = None

    # The windowed exe has sys.stderr = None. A StreamHandler on None
    # fails on every record, so only add one when there's a real stream.
    if debug and getattr(sys, "stderr", None) is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(console)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    logger.info("Logging to %s", log_path or "(no writable log location)")
    return logger, log_path
