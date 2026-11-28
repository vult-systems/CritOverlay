"""Small reusable controls for the toolbar and context bar.

Built on Qt's own spin boxes: compact numeric fields you can type into,
scroll with the wheel, or step with the arrow keys, with no spin buttons
(they're too small to hit with a pen; the slider beside each field is the
pen-friendly control).
"""

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QSpinBox

from . import constants as C
from . import dab

_NUMBER = re.compile(r"^\s*\d*(\.\d*)?\s*(px)?\s*$", re.IGNORECASE)


def _compact(box, width):
    box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    box.setKeyboardTracking(False)          # apply on Enter / focus out, not per keystroke
    box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    box.setFixedWidth(width)
    box.setAccelerated(True)


class SizeField(QDoubleSpinBox):
    """Brush size in px. Shown like the rest of the app (one decimal below
    10 px), and the wheel and arrow keys step by ratio, matching [ and ] and
    the logarithmic slider."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(C.MIN_SIZE, C.MAX_SIZE)
        self.setDecimals(2)
        _compact(self, 54)

    def textFromValue(self, value):
        return dab.format_size(value)

    def valueFromText(self, text):
        try:
            return float(text.lower().replace("px", "").strip())
        except ValueError:
            return self.value()

    def validate(self, text, pos):
        if not _NUMBER.match(text):
            return QValidator.State.Invalid, text, pos
        stripped = text.lower().replace("px", "").strip()
        if stripped in ("", "."):
            return QValidator.State.Intermediate, text, pos
        value = float(stripped)
        state = QValidator.State.Acceptable if C.MIN_SIZE <= value <= C.MAX_SIZE else QValidator.State.Intermediate
        return state, text, pos

    def fixup(self, text):
        return self.textFromValue(self.value())

    def stepBy(self, steps):
        self.setValue(dab.step_size(self.value(), steps))


class IntField(QSpinBox):
    """A compact integer field with a unit suffix (%, px)."""

    def __init__(self, lo, hi, suffix, parent=None, width=50):
        super().__init__(parent)
        self.setRange(lo, hi)
        self.setSuffix(suffix)
        _compact(self, width)
