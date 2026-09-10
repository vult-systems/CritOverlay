"""A small palette popup for the toolbar's color swatch button.

Quicker than the native QColorDialog for the common case of grabbing one of
a few crit colors, and it doesn't look out of place against the dark
toolbar. Qt.WindowType.Popup handles click-outside-to-dismiss.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QToolButton

from . import constants as C
from . import icons

_COLUMNS = 5


class ColorPopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE)

        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        for i, hex_color in enumerate(C.COLOR_PALETTE):
            btn = QToolButton(self)
            btn.setIcon(icons.build_color_swatch_icon(hex_color))
            btn.setIconSize(QSize(20, 20))
            btn.setAutoRaise(True)
            btn.setToolTip(hex_color)
            btn.clicked.connect(lambda checked=False, c=hex_color: self._pick(c))
            layout.addWidget(btn, i // _COLUMNS, i % _COLUMNS)

        more_btn = QToolButton(self)
        more_btn.setIcon(icons.build_icon("more"))
        more_btn.setIconSize(QSize(20, 20))
        more_btn.setAutoRaise(True)
        more_btn.setToolTip("Custom color…")
        more_btn.clicked.connect(self._pick_custom)
        last = len(C.COLOR_PALETTE)
        layout.addWidget(more_btn, last // _COLUMNS, last % _COLUMNS)

    def _pick(self, hex_color):
        self._overlay._set_color(hex_color)
        self.close()

    def _pick_custom(self):
        self.close()
        self._overlay.pick_color()

    def show_below(self, widget):
        self.adjustSize()
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        self.move(pos.x(), pos.y() + 6)
        self.show()
