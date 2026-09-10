"""The small badge shown while pass-through mode is on.

WA_TransparentForMouseEvents applies to a whole top-level window, so during
pass-through the main overlay (toolbar included) stops taking clicks, and
clicking into the app underneath moves keyboard focus away, which stops F8
from reaching us. This badge is a separate top-level window, so it stays
clickable regardless and gives you a way back to drawing.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel

from . import constants as C


class PassThroughBadge(QLabel):
    clicked = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.setText("Pass-through ON. Click to return to drawing")
        self.setStyleSheet(C.PASS_THROUGH_BADGE_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
