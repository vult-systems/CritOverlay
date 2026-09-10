"""Persisted brush settings, stored as INI via QSettings.

IniFormat keeps this a plain text file you can open and read, instead of
going into the Windows registry.
"""

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

from . import constants as C

_BACKDROP_NONE = "none"


class AppConfig:
    def __init__(self):
        self._settings = QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            C.ORG_NAME,
            C.APP_NAME,
        )

    def load(self):
        backdrop = self._settings.value("brush/backdrop", _BACKDROP_NONE, str)
        return {
            "color": self._settings.value("brush/color", C.DEFAULT_COLOR, str),
            "size": int(self._settings.value("brush/size", C.DEFAULT_SIZE, int)),
            "opacity": float(self._settings.value("brush/opacity", C.DEFAULT_OPACITY, float)),
            "smoothing": float(self._settings.value("brush/smoothing", C.DEFAULT_SMOOTHING, float)),
            "backdrop": None if backdrop == _BACKDROP_NONE else backdrop,
            "backdrop_opacity": float(
                self._settings.value("brush/backdrop_opacity", C.DEFAULT_BACKDROP_OPACITY, float)
            ),
        }

    def save(self, *, color, size, opacity, smoothing, backdrop, backdrop_opacity):
        self._settings.setValue("brush/color", QColor(color).name())
        self._settings.setValue("brush/size", int(size))
        self._settings.setValue("brush/opacity", float(opacity))
        self._settings.setValue("brush/smoothing", float(smoothing))
        self._settings.setValue("brush/backdrop", backdrop or _BACKDROP_NONE)
        self._settings.setValue("brush/backdrop_opacity", float(backdrop_opacity))
        self._settings.sync()
