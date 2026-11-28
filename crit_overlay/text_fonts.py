"""Fonts bundled for the text tool (crit_overlay/fonts, SIL OFL 1.1, each
family's license beside it). Registered with Qt for this process only, so
nothing is installed on the workstation."""

import logging
from pathlib import Path

from PySide6.QtGui import QFontDatabase

from . import constants as C

logger = logging.getLogger(f"{C.ORG_NAME}.text_fonts")

FONT_DIR = Path(__file__).with_name("fonts")
FAMILIES = ("IBM Plex Sans", "Inter", "IBM Plex Mono", "Caveat")
DEFAULT_FAMILY = FAMILIES[0]

_loaded = False


def load():
    """Register the bundled fonts once. Missing files are logged; Qt then
    substitutes a system font, so text still draws."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    files = sorted(FONT_DIR.glob("*/*.ttf"))
    if not files:
        logger.warning("No bundled fonts found in %s", FONT_DIR)
    for path in files:
        if QFontDatabase.addApplicationFont(str(path)) < 0:
            logger.warning("Couldn't load font %s", path)
