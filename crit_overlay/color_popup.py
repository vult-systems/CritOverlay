"""Color popup for the swatch in the floating toolbar.

A large swatch of the current color with a HEX entry and the eyedropper,
the crit palette, the colors you used last, and favorites you pin (click
+ to pin the current color, right-click a favorite to remove it). The native
QColorDialog is one click away for anything else. Qt.WindowType.Popup
handles click-outside-to-dismiss.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from . import constants as C
from . import icons
from .ui_widgets import HexValidator, place_popup

_COLUMNS = 8
SWATCH = 18


def swatch_icon(color, size=SWATCH, ring=False):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QColor(255, 255, 255, 60) if not ring else QColor(255, 255, 255, 200))
    p.setBrush(QColor(color))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 4, 4)
    p.end()
    return QIcon(pm)


class ColorPopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE + C.FIELD_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.big = QLabel(self)
        self.big.setFixedSize(44, 44)
        top.addWidget(self.big)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.hex = QLineEdit(self)
        self.hex.setValidator(HexValidator(self.hex))
        self.hex.setFixedWidth(92)
        self.hex.setPlaceholderText("#RRGGBB")
        self.hex.setToolTip("HEX color: type and press Enter")
        self.hex.returnPressed.connect(self._apply_hex)
        col.addWidget(self.hex)
        tools = QHBoxLayout()
        tools.setSpacing(2)
        self.pick_btn = self._icon_button("eyedropper", "Pick from the screen (I)", self._eyedropper)
        tools.addWidget(self.pick_btn)
        self.custom_btn = self._icon_button("more", "More colors…", self._pick_custom)
        tools.addWidget(self.custom_btn)
        tools.addStretch(1)
        col.addLayout(tools)
        top.addLayout(col)
        top.addStretch(1)
        outer.addLayout(top)

        self.palette_grid = self._grid(outer, "PALETTE")
        for i, hex_color in enumerate(C.COLOR_PALETTE):
            self.palette_grid.addWidget(self._swatch(hex_color), i // _COLUMNS, i % _COLUMNS)

        self.recent_label, self.recent_row = self._row(outer, "RECENT")
        self.fav_label, self.fav_row = self._row(outer, "FAVORITES")

    # building blocks
    def _icon_button(self, name, tip, slot):
        b = QToolButton(self)
        b.setIcon(icons.build_icon(name))
        b.setIconSize(QSize(16, 16))
        b.setAutoRaise(True)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    def _grid(self, outer, title):
        cap = QLabel(title, self)
        cap.setObjectName("SliderCaption")
        outer.addWidget(cap)
        grid = QGridLayout()
        grid.setSpacing(3)
        outer.addLayout(grid)
        return grid

    def _row(self, outer, title):
        cap = QLabel(title, self)
        cap.setObjectName("SliderCaption")
        outer.addWidget(cap)
        host = QWidget(self)
        h = QHBoxLayout(host)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        outer.addWidget(host)
        return cap, h

    def _swatch(self, color, favorite=False):
        b = QToolButton(self)
        b.setObjectName("Swatch")
        b.setIcon(swatch_icon(color))
        b.setIconSize(QSize(SWATCH, SWATCH))
        b.setAutoRaise(True)
        b.setToolTip(f"{QColor(color).name()}{'  (right-click to remove)' if favorite else ''}")
        b.clicked.connect(lambda checked=False, c=color: self._pick(c))
        if favorite:
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, c=color: self._unpin(c))
        return b

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def sync(self):
        o = self._overlay
        c = QColor(o.color)
        pm = QPixmap(88, 88)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QColor(255, 255, 255, 70))
        p.setBrush(c)
        p.drawRoundedRect(1, 1, 42, 42, 8, 8)
        p.end()
        self.big.setPixmap(pm)
        self.hex.setText(c.name().upper())
        self._clear(self.recent_row)
        for color in o.recent_colors:
            self.recent_row.addWidget(self._swatch(color))
        self.recent_row.addStretch(1)
        self.recent_label.setVisible(bool(o.recent_colors))
        self.recent_row.parentWidget().setVisible(bool(o.recent_colors))
        self._clear(self.fav_row)
        for color in o.favorite_colors:
            self.fav_row.addWidget(self._swatch(color, favorite=True))
        add = QToolButton(self)
        add.setText("+")
        add.setAutoRaise(True)
        add.setToolTip("Add the current color to favorites")
        add.clicked.connect(self._pin)
        self.fav_row.addWidget(add)
        self.fav_row.addStretch(1)
        self.adjustSize()

    # actions
    def _pick(self, color):
        self._overlay._set_color(color)
        self.close()

    def _apply_hex(self):
        text = self.hex.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        c = QColor(text)
        if c.isValid() and len(text) == 7:
            self._pick(c.name())

    def _pin(self):
        self._overlay.add_favorite_color(self._overlay.color)
        self.sync()

    def _unpin(self, color):
        self._overlay.remove_favorite_color(color)
        self.sync()

    def _eyedropper(self):
        self.close()
        if self._overlay.mode != "eyedropper":
            self._overlay._toggle_eyedropper()

    def _pick_custom(self):
        self.close()
        self._overlay.pick_color()

    def show_below(self, widget):
        self.sync()
        place_popup(self, widget)
