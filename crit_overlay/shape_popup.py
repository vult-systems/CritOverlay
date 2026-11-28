"""A one-row popup for picking the shape tool, opened from the toolbar's
shape button. Same pattern as ColorPopup."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton

from . import constants as C
from .ui_widgets import place_popup
from . import icons

SHAPE_LABELS = {
    "rect": "Rectangle",
    "ellipse": "Ellipse",
    "line": "Line",
    "arrow": "Arrow",
}


class ShapePopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        self.buttons = {}
        for shape, label in SHAPE_LABELS.items():
            btn = QToolButton(self)
            btn.setIcon(icons.build_icon(shape))
            btn.setIconSize(QSize(20, 20))
            btn.setAutoRaise(True)
            btn.setCheckable(True)
            btn.setToolTip(f"{label} (Shift constrains)")
            btn.clicked.connect(lambda checked=False, s=shape: self._pick(s))
            layout.addWidget(btn)
            self.buttons[shape] = btn

    def _pick(self, shape):
        self._overlay._set_mode(shape)
        self.close()

    def show_below(self, widget):
        for shape, btn in self.buttons.items():
            btn.setChecked(shape == self._overlay.mode)
        self.adjustSize()
        place_popup(self, widget)
