"""Color popup for the swatch in the floating toolbar.

A large swatch of the current color with a HEX entry and the eyedropper,
a saturation/value field with a hue strip for any color,
the crit palette, the colors you used last, and favorites you pin (click
+ to pin the current color, right-click a favorite to remove it). The native
QColorDialog is one click away for anything else. Qt.WindowType.Popup
handles click-outside-to-dismiss.
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
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


class SVPicker(QWidget):
    """Saturation (left to right) and value (bottom to top) for one hue.
    Drag to choose; emits while dragging and once more on release."""

    changed = Signal(QColor, bool)  # color, final (mouse released)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(196, 120)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.h, self.s, self.v = 0.0, 1.0, 1.0

    def set_hsv(self, h, s, v):
        self.h, self.s, self.v = h, s, v
        self.update()

    def color(self):
        return QColor.fromHsvF(self.h, self.s, self.v)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.setClipPath(path)
        horiz = QLinearGradient(r.topLeft(), r.topRight())
        horiz.setColorAt(0, QColor(255, 255, 255))
        horiz.setColorAt(1, QColor.fromHsvF(self.h, 1, 1))
        p.fillRect(r, horiz)
        vert = QLinearGradient(r.topLeft(), r.bottomLeft())
        vert.setColorAt(0, QColor(0, 0, 0, 0))
        vert.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(r, vert)
        p.setClipping(False)
        x = r.left() + self.s * r.width()
        y = r.top() + (1 - self.v) * r.height()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 160), 3))
        p.drawEllipse(QPointF(x, y), 6, 6)
        p.setPen(QPen(QColor(255, 255, 255), 1.6))
        p.drawEllipse(QPointF(x, y), 6, 6)
        p.end()

    def _at(self, pos, final):
        self.s = max(0.0, min(1.0, pos.x() / max(1, self.width() - 1)))
        self.v = max(0.0, min(1.0, 1 - pos.y() / max(1, self.height() - 1)))
        self.update()
        self.changed.emit(self.color(), final)

    def mousePressEvent(self, e):
        self._at(e.position(), False)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._at(e.position(), False)

    def mouseReleaseEvent(self, e):
        self._at(e.position(), True)


class HueStrip(QWidget):
    """The hue spectrum; drag along it."""

    changed = Signal(float, bool)  # hue 0..1, final

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(196, 14)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.h = 0.0

    def set_hue(self, h):
        self.h = h
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 2.5, -0.5, -2.5)
        g = QLinearGradient(r.topLeft(), r.topRight())
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsvF(min(i / 6, 0.9999), 1, 1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 4, 4)
        x = r.left() + self.h * r.width()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 160), 3))
        p.drawRoundedRect(QRectF(x - 3, 0.5, 6, self.height() - 1), 2, 2)
        p.setPen(QPen(QColor(255, 255, 255), 1.6))
        p.drawRoundedRect(QRectF(x - 3, 0.5, 6, self.height() - 1), 2, 2)
        p.end()

    def _at(self, pos, final):
        self.h = max(0.0, min(0.9999, pos.x() / max(1, self.width() - 1)))
        self.update()
        self.changed.emit(self.h, final)

    def mousePressEvent(self, e):
        self._at(e.position(), False)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._at(e.position(), False)

    def mouseReleaseEvent(self, e):
        self._at(e.position(), True)


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

        self.sv = SVPicker(self)
        self.hue = HueStrip(self)
        self.sv.changed.connect(self._sv_changed)
        self.hue.changed.connect(self._hue_changed)
        outer.addWidget(self.sv)
        outer.addWidget(self.hue)

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
        h, sat, val = c.hsvHueF(), c.hsvSaturationF(), c.valueF()
        h = self.hue.h if h < 0 else h  # greys keep the strip where it was
        self.sv.set_hsv(h, sat, val)
        self.hue.set_hue(h)
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
    def _live(self, color, final):
        # Live while dragging; only the color you let go on goes to recent.
        self._overlay._set_color(color, remember=final)
        self.hex.setText(QColor(color).name().upper())
        self._paint_big(QColor(color))

    def _sv_changed(self, color, final):
        self._live(color, final)

    def _hue_changed(self, h, final):
        self.sv.set_hsv(h, self.sv.s, self.sv.v)
        self._live(self.sv.color(), final)

    def _paint_big(self, c):
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
