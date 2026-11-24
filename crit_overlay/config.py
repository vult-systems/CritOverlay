"""Persisted brush settings, stored as INI via QSettings.

IniFormat keeps this a plain text file you can open and read, instead of
going into the Windows registry (which some lab images reset at logoff).

The file path comes from paths.py rather than QSettings' own lookup, so it
follows folder redirection fallbacks and portable mode. By default it is the
same file QSettings used before, %APPDATA%\\CritOverlay\\Crit Overlay.ini.
The [compat] section in it is read by compat.py before Qt starts.
"""

import logging

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

from . import constants as C
from . import paths

logger = logging.getLogger(f"{C.ORG_NAME}.config")

_BACKDROP_NONE = "none"
_BACKDROPS = (None, "white", "black")


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


# 1: v1.0-v1.4.2 and early 1.5.0 development builds (no version key).
# 2: v1.5.0, overlay always on one monitor.
SETTINGS_VERSION = 2


class AppConfig:
    def __init__(self, path=None):
        self.path = str(path or paths.current().settings_path)
        self._settings = QSettings(self.path, QSettings.Format.IniFormat)
        self._save_failed_logged = False
        if self._settings.status() != QSettings.Status.NoError:
            logger.warning("Settings file %s unreadable (%s); using defaults", self.path, self._settings.status())
        self.migrated_from = self._migrate()

    def _migrate(self):
        """Bring an older settings file up to SETTINGS_VERSION in place.
        Returns the version it started at, or None if nothing changed.

        Brush keys have kept the same names and formats since v1.0, so files
        from any earlier version load as-is. Early 1.5.0 development
        builds also allowed compat/screens = auto|all, which no longer
        exist; those become the default (cursor). A newer file (from a later version) is left alone.
        """
        try:
            version = int(float(self._settings.value("meta/settings_version", 1)))
        except (TypeError, ValueError):
            version = 1
        if version >= SETTINGS_VERSION:
            return None
        screens = self._settings.value("compat/screens", None)
        if isinstance(screens, str) and screens.strip().lower() in ("auto", "all"):
            self._settings.setValue("compat/screens", "cursor")
            logger.info("Settings migration: compat/screens=%s -> cursor", screens)
        self._settings.setValue("meta/settings_version", SETTINGS_VERSION)
        self._settings.sync()
        if self._settings.status() == QSettings.Status.NoError:
            logger.info("Settings migrated from version %s to %s (%s)", version, SETTINGS_VERSION, self.path)
        else:
            logger.warning("Couldn't write migrated settings to %s; using them for this session", self.path)
        return version

    def _value(self, key, default, kind):
        # Read raw and convert here: QSettings' own typed conversion turns
        # unparseable text into 0, which for opacity means an invisible brush.
        raw = self._settings.value(key, None)
        if raw is None:
            return default
        try:
            if kind is int:
                return int(float(raw))
            return kind(raw)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid setting %s=%r", key, raw)
            return default

    def load(self):
        # Values are validated, because a hand-edited or half-written file
        # (roaming profile synced mid-write) shouldn't stop the app from
        # starting.
        backdrop = self._value("brush/backdrop", _BACKDROP_NONE, str)
        backdrop = None if backdrop == _BACKDROP_NONE else backdrop
        if backdrop not in _BACKDROPS:
            backdrop = C.DEFAULT_BACKDROP
        color = self._value("brush/color", C.DEFAULT_COLOR, str)
        if not QColor(color).isValid():
            color = C.DEFAULT_COLOR
        return {
            "color": color,
            "size": int(_clamp(self._value("brush/size", C.DEFAULT_SIZE, int), C.MIN_SIZE, C.MAX_SIZE)),
            "opacity": _clamp(self._value("brush/opacity", C.DEFAULT_OPACITY, float), 0.05, 1.0),
            "smoothing": _clamp(self._value("brush/smoothing", C.DEFAULT_SMOOTHING, float), 0.0, 0.9),
            "backdrop": backdrop,
            "backdrop_opacity": _clamp(
                self._value("brush/backdrop_opacity", C.DEFAULT_BACKDROP_OPACITY, float), 0.1, 1.0
            ),
            **self._load_margin(),
        }

    def _load_margin(self):
        enabled = str(self._value("margin/enabled", C.DEFAULT_MARGIN_ENABLED, str)).strip().lower()
        color = self._value("margin/color", C.DEFAULT_MARGIN_COLOR, str)
        return {
            "margin_enabled": enabled in ("1", "true", "yes", "on"),
            "margin_size": int(_clamp(self._value("margin/size", C.DEFAULT_MARGIN_SIZE, int),
                                      C.MARGIN_MIN_SIZE, C.MARGIN_MAX_SIZE)),
            "margin_color": color if color in ("white", "black") else C.DEFAULT_MARGIN_COLOR,
            "margin_opacity": _clamp(self._value("margin/opacity", C.DEFAULT_MARGIN_OPACITY, float), 0.1, 1.0),
        }

    def save(self, *, color, size, opacity, smoothing, backdrop, backdrop_opacity,
             margin_enabled=C.DEFAULT_MARGIN_ENABLED, margin_size=C.DEFAULT_MARGIN_SIZE,
             margin_color=C.DEFAULT_MARGIN_COLOR, margin_opacity=C.DEFAULT_MARGIN_OPACITY):
        self._settings.setValue("brush/color", QColor(color).name())
        self._settings.setValue("brush/size", int(size))
        self._settings.setValue("brush/opacity", float(opacity))
        self._settings.setValue("brush/smoothing", float(smoothing))
        self._settings.setValue("brush/backdrop", backdrop or _BACKDROP_NONE)
        self._settings.setValue("brush/backdrop_opacity", float(backdrop_opacity))
        self._settings.setValue("margin/enabled", "true" if margin_enabled else "false")
        self._settings.setValue("margin/size", int(margin_size))
        self._settings.setValue("margin/color", margin_color)
        self._settings.setValue("margin/opacity", float(margin_opacity))
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError and not self._save_failed_logged:
            # Logged once: this is called on every debounced slider change.
            self._save_failed_logged = True
            logger.warning("Couldn't save settings to %s (%s)", self.path, self._settings.status())
