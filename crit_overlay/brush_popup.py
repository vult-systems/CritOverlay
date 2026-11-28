"""Brush and eraser popups, opened by clicking the Brush or Eraser button
while that tool is active (or the brush chip in the toolbar).

Five brushes, each shown by a stroke libmypaint actually painted, and the
two pressure controls (size, opacity) for the selected brush. The pressure
response curve and the stabilizer apply to every brush and live in
Preferences.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

from . import brush_preview
from . import brushes
from . import constants as C
from .ui_widgets import make_panel, place_popup

TILE_INK = "#ececef"  # previews in a neutral ink, so brushes compare on shape and texture


def label_for(key):
    return brushes.LABELS.get(key, key)


def _button(parent, text, tooltip, slot):
    btn = QToolButton(parent)
    btn.setText(text)
    btn.setAutoRaise(True)
    btn.setCheckable(True)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    btn.setToolTip(tooltip)
    btn.clicked.connect(slot)
    return btn


class BrushPopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None)
        make_panel(self)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE)
        dpr = max(1.0, float(overlay.devicePixelRatioF()))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)
        self.buttons = {}
        cap = QLabel("BRUSHES", self)
        cap.setObjectName("SliderCaption")
        outer.addWidget(cap)
        row = QHBoxLayout()
        row.setSpacing(4)
        for key, preset in brushes.PRESETS.items():
            row.addWidget(self._tile(key, preset.label, 136, 44, dpr))
        outer.addLayout(row)

        prow = QHBoxLayout()
        prow.setSpacing(2)
        cap = QLabel("PRESSURE CONTROLS", self)
        cap.setObjectName("SliderCaption")
        prow.addWidget(cap)
        self.size_pressure_btn = _button(self, "Size", "Pen pressure changes the brush size",
                                         lambda checked=False: self._toggle(size=True))
        self.opacity_pressure_btn = _button(self, "Opacity", "Pen pressure changes the opacity",
                                            lambda checked=False: self._toggle(opacity=True))
        for b in (self.size_pressure_btn, self.opacity_pressure_btn):
            b.setObjectName("Seg")
            prow.addWidget(b)
        prow.addStretch(1)
        outer.addLayout(prow)

    def _tile(self, key, label, w, h, dpr):
        btn = QToolButton(self)
        btn.setObjectName("Tile")
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setIcon(QIcon(brush_preview.preview(key, TILE_INK, w, h, dpr)))
        btn.setIconSize(QSize(w, h))
        btn.setText(label)
        btn.setToolTip(label)
        f = QFont(btn.font())
        f.setBold(True)
        btn.setFont(f)
        btn.clicked.connect(lambda checked=False, k=key: self._pick(k))
        self.buttons[key] = btn
        return btn

    def _pick(self, key):
        self._overlay._set_brush_preset(key)
        self.sync()

    def _toggle(self, size=False, opacity=False):
        s, o = self._overlay.pressure_flags()
        self._overlay.set_brush_pressure(size=(not s) if size else None, opacity=(not o) if opacity else None)
        self.sync()

    def sync(self):
        key = self._overlay.brush_preset
        for k, btn in self.buttons.items():
            btn.setChecked(k == key)
        s, o = self._overlay.pressure_flags()
        self.size_pressure_btn.setChecked(s)
        self.opacity_pressure_btn.setChecked(o)
        self.adjustSize()

    def show_below(self, widget):
        self.sync()
        place_popup(self, widget)


class EraserPopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None)
        make_panel(self)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        self.buttons = {}
        for style, label, tip in (("hard", "Hard", "Clean, solid edge"),
                                  ("soft", "Soft", "Feathered edge; light pressure erases partly")):
            btn = _button(self, label, tip, lambda checked=False, s=style: self._pick(s))
            layout.addWidget(btn)
            self.buttons[style] = btn

    def _pick(self, style):
        self._overlay._set_eraser_style(style)
        self.close()

    def show_below(self, widget):
        for style, btn in self.buttons.items():
            btn.setChecked(style == self._overlay.eraser_style)
        place_popup(self, widget)
