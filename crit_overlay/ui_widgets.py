"""Small reusable controls for the floating toolbar and its popups.

ScrubField is the pattern from Blender, Figma and Photoshop's scrubby
sliders: a compact value you drag sideways to change, scroll to step, or
click to open a popup with a slider and a text entry. It takes far less room
than a permanent slider and suits a pen (one drag, no tiny handle).

Built on plain Qt; see the UI notes in the commit for why no widget library.
"""

import re
import weakref

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QValidator
from PySide6.QtWidgets import (QAbstractSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from . import constants as C
from . import brushes


# -- popup placement --------------------------------------------------------

def clamp_popup(size, anchor, avail, gap=6, align="left"):
    """Top-left for a popup of `size` under the global rect `anchor`, kept
    inside `avail` (the anchor monitor's available area): flipped above the
    anchor when there's no room below, and slid sideways to stay on screen."""
    x = anchor.left() if align == "left" else anchor.right() - size.width() + 1
    y = anchor.bottom() + 1 + gap
    if y + size.height() > avail.bottom() + 1 and anchor.top() - gap - size.height() >= avail.top():
        y = anchor.top() - gap - size.height()
    x = max(avail.left() + 4, min(x, avail.right() + 1 - size.width() - 4))
    y = max(avail.top() + 4, min(y, avail.bottom() + 1 - size.height() - 4))
    return QPoint(x, y)


# -- floating panels ------------------------------------------------------------
#
# The toolbar's panels (brush picker, colors, menus, sliders) are tool
# windows, not Qt popups. While a Qt popup is open, Qt's Windows backend
# turns pen contact into mouse input, so the stroke that dismissed a popup
# (the first after picking a brush) drew without pressure. A panel closes
# on Esc, when it loses activation, or when the canvas is pressed
# (close_panels), and the press still reaches the canvas as a pen.

PANEL_FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
_panels = weakref.WeakSet()


class _PanelFilter(QObject):
    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            obj.close()
            return True
        if t == QEvent.Type.WindowDeactivate and obj.isVisible():
            # Deferred: activation can pass between the panel's own widgets.
            QTimer.singleShot(0, lambda o=obj: o.isVisible() and not o.isActiveWindow() and o.close())
        return False


_filter = _PanelFilter()


def make_panel(frame):
    """Give a top-level QFrame the floating-panel behavior."""
    frame.setWindowFlags(PANEL_FLAGS)
    frame.installEventFilter(_filter)
    _panels.add(frame)
    return frame


def close_panels(keep=None):
    """Close every open panel (except `keep`). Returns True if any was open."""
    closed = False
    for p in list(_panels):
        if p is not keep and p.isVisible():
            p.close()
            closed = True
    return closed


def place_popup(popup, anchor_widget, align="left", gap=6):
    """Show `popup` below `anchor_widget`, entirely on the anchor's monitor
    (popups are top-level windows; with monitors at different scales Qt
    would otherwise let them spill onto a neighbour). One panel at a time."""
    if popup not in _panels:
        make_panel(popup)
    close_panels(keep=popup)
    popup.adjustSize()
    top_left = anchor_widget.mapToGlobal(QPoint(0, 0))
    anchor = QRect(top_left, anchor_widget.size())
    screen = QGuiApplication.screenAt(anchor.center()) or anchor_widget.screen() or QGuiApplication.primaryScreen()
    popup.move(clamp_popup(popup.sizeHint().expandedTo(popup.size()), anchor, screen.availableGeometry(),
                           gap, align))
    popup.show()
    popup.raise_()
    popup.activateWindow()


def popup_frame():
    frame = make_panel(QFrame(None))
    frame.setObjectName("Toolbar")
    frame.setStyleSheet(C.TOOLBAR_STYLE + C.FIELD_STYLE)
    return frame


# -- scrubbable numeric field ------------------------------------------------

class ScrubField(QWidget):
    """A value with a caption. Drag left/right to scrub (Shift for fine),
    wheel to step, click to open a slider and an entry box.

    The value maps to integer slider positions through to_pos/from_pos, so
    size can be logarithmic and opacity linear with the same widget.
    """

    valueChanged = Signal(float)
    DRAG_THRESHOLD = 3
    PX_PER_RANGE = 260  # dragging this far covers the whole range

    def __init__(self, caption, lo, hi, to_pos, from_pos, fmt, parse, step, tooltip="", parent=None,
                 positions=1000, width=88):
        super().__init__(parent)
        self.caption = caption
        self.lo, self.hi = lo, hi
        self._to_pos, self._from_pos = to_pos, from_pos
        self._fmt, self._parse, self._step = fmt, parse, step
        self._value = lo
        self._press = None
        self._scrubbing = False
        self._hover = False
        self.setFixedSize(width, 30)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        # The popup: slider plus entry. Built now so its slider exists for
        # callers that drive it directly (self-test, tests).
        self.popup = popup_frame()
        lay = QVBoxLayout(self.popup)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        cap = QLabel(caption.upper(), self.popup)
        cap.setObjectName("SliderCaption")
        head.addWidget(cap)
        head.addStretch(1)
        self.entry = QLineEdit(self.popup)
        self.entry.setFixedWidth(70)
        self.entry.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.entry.returnPressed.connect(self._apply_entry)
        head.addWidget(self.entry)
        lay.addLayout(head)
        self.slider = QSlider(Qt.Orientation.Horizontal, self.popup)
        self.slider.setRange(to_pos(lo), to_pos(hi))
        self.slider.setFixedWidth(200)
        self.slider.valueChanged.connect(lambda p: self.setValue(self._from_pos(p), emit=True))
        lay.addWidget(self.slider)

    # value
    def value(self):
        return self._value

    def text(self):
        return self._fmt(self._value)

    def setValue(self, value, emit=True):
        value = max(self.lo, min(self.hi, value))
        changed = value != self._value
        self._value = value
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_pos(value))
        self.slider.blockSignals(False)
        if not self.entry.hasFocus():
            self.entry.setText(self._fmt(value))
        self.update()
        if changed and emit and not self.signalsBlocked():
            self.valueChanged.emit(value)

    def stepBy(self, steps):
        self.setValue(self._step(self._value, steps))

    def _apply_entry(self):
        v = self._parse(self.entry.text())
        if v is not None:
            self.setValue(v)
        self.popup.close()

    def open_popup(self):
        self.entry.setText(self._fmt(self._value))
        place_popup(self.popup, self)
        self.entry.setFocus()
        self.entry.selectAll()

    # interaction
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = (e.position().x(), self._to_pos(self._value))
            self._scrubbing = False
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        x0, pos0 = self._press
        dx = e.position().x() - x0
        if not self._scrubbing and abs(dx) < self.DRAG_THRESHOLD:
            return
        self._scrubbing = True
        span = self.slider.maximum() - self.slider.minimum()
        scale = span / self.PX_PER_RANGE * (0.2 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0)
        pos = max(self.slider.minimum(), min(self.slider.maximum(), round(pos0 + dx * scale)))
        self.setValue(self._from_pos(pos))
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._press is not None and not self._scrubbing and e.button() == Qt.MouseButton.LeftButton:
            self.open_popup()
        self._press = None
        self._scrubbing = False
        e.accept()

    def wheelEvent(self, e):
        steps = 1 if e.angleDelta().y() > 0 else -1 if e.angleDelta().y() < 0 else 0
        if steps:
            self.stepBy(steps)
        e.accept()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QColor(255, 255, 255, 46 if (self._hover or self._scrubbing) else 26))
        p.setBrush(QColor(255, 255, 255, 34 if (self._hover or self._scrubbing) else 20))
        p.drawRoundedRect(r, 7, 7)
        p.setPen(Qt.PenStyle.NoPen)
        # A hairline showing where the value sits in its range.
        span = max(1, self.slider.maximum() - self.slider.minimum())
        frac = (self._to_pos(self._value) - self.slider.minimum()) / span
        p.setBrush(QColor(255, 255, 255, 110))
        p.drawRoundedRect(QRectF(r.left() + 6, r.bottom() - 4, (r.width() - 12) * frac, 2), 1, 1)
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor("#a9a9b2"))
        p.drawText(r.adjusted(8, 0, 0, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.caption)
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#f4f4f6"))
        p.drawText(r.adjusted(0, 0, -8, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   self.text())
        p.end()


def size_field(tooltip, parent=None):
    def parse(text):
        try:
            return float(text.lower().replace("px", "").strip())
        except ValueError:
            return None
    return ScrubField("Size", C.MIN_SIZE, C.MAX_SIZE, brushes.size_to_slider, brushes.slider_to_size, brushes.format_size,
                      parse, brushes.step_size, tooltip, parent)


def percent_field(caption, lo, hi, tooltip, parent=None):
    def parse(text):
        try:
            return int(round(float(text.replace("%", "").strip())))
        except ValueError:
            return None
    return ScrubField(caption, lo, hi, lambda v: int(round(v)), lambda p: int(p), lambda v: f"{int(round(v))}%",
                      parse, lambda v, s: int(round(v)) + s * 5, tooltip, parent)


def px_field(caption, lo, hi, tooltip, parent=None):
    def parse(text):
        try:
            return int(round(float(text.lower().replace("px", "").strip())))
        except ValueError:
            return None
    return ScrubField(caption, lo, hi, lambda v: int(round(v)), lambda p: int(p), lambda v: f"{int(round(v))}px",
                      parse, lambda v, s: int(round(v)) + s * 2, tooltip, parent)


# -- collapsible section -------------------------------------------------------

class Collapsible(QWidget):
    """A header you click to show or hide its content."""

    toggled = Signal(bool)

    def __init__(self, title, content, expanded=False, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._title = title
        self.header = QToolButton(self)
        self.header.setObjectName("Disclosure")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setIconSize(QSize(14, 14))
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.clicked.connect(self._toggle)
        lay.addWidget(self.header)
        self.content = content
        content.setParent(self)
        lay.addWidget(content)
        self._apply(expanded)

    def _apply(self, expanded):
        from . import icons
        self.header.setIcon(icons.build_icon("chevron_down" if expanded else "chevron_right", C.TEXT_DIM))
        self.header.setText(self._title)
        self.content.setVisible(expanded)

    def _toggle(self, checked):
        self._apply(checked)
        self.toggled.emit(checked)
        w = self.window()
        if w is not None:
            w.adjustSize()

    def expanded(self):
        return self.header.isChecked()


# -- kept for the numeric needs elsewhere ------------------------------------------

_NUMBER = re.compile(r"^\s*\d*(\.\d*)?\s*(px)?\s*$", re.IGNORECASE)


class IntField(QSpinBox):
    """A compact integer field with a unit suffix (%, px)."""

    def __init__(self, lo, hi, suffix, parent=None, width=50):
        super().__init__(parent)
        self.setRange(lo, hi)
        self.setSuffix(suffix)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setKeyboardTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFixedWidth(width)


class HexValidator(QValidator):
    def validate(self, text, pos):
        t = text.strip()
        if re.fullmatch(r"#?[0-9a-fA-F]{0,6}", t):
            ok = re.fullmatch(r"#?[0-9a-fA-F]{6}", t)
            return (QValidator.State.Acceptable if ok else QValidator.State.Intermediate), text, pos
        return QValidator.State.Invalid, text, pos




class MenuRow(QPushButton):
    """A menu line: icon, label, and the keyboard shortcut right-aligned in
    a dimmer color, the way desktop menus show them."""

    def __init__(self, parent, icon, text, shortcut=""):
        super().__init__(text, parent)
        self.setObjectName("MenuRow")
        if icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(16, 16))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._shortcut = shortcut

    def set_shortcut(self, text):
        self._shortcut = text
        self.update()

    def shortcut_text(self):
        return self._shortcut

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._shortcut:
            p = QPainter(self)
            p.setPen(QColor(C.TEXT_DIM))
            p.drawText(self.rect().adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self._shortcut)
            p.end()


class ToolbarGrip(QWidget):
    """The toolbar's drag handle: drag to move the toolbar, double-click to
    put it back."""

    def __init__(self, parent, on_move, on_release, on_reset):
        super().__init__(parent)
        self._on_move, self._on_release, self._on_reset = on_move, on_release, on_reset
        self._grab = None
        self.setFixedSize(14, 30)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 90))
        for row in range(3):
            for col in range(2):
                p.drawEllipse(QRectF(3.5 + col * 5, 8 + row * 5, 2.4, 2.4))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            # Where in the toolbar the press landed, so it doesn't jump.
            self._grab = self.mapTo(self.parentWidget(), e.position().toPoint())
        e.accept()

    def mouseMoveEvent(self, e):
        if self._grab is not None:
            self._on_move(e.globalPosition().toPoint(), self._grab)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._grab is not None:
            self._grab = None
            self._on_release()
        e.accept()

    def mouseDoubleClickEvent(self, e):
        self._grab = None
        self._on_reset()
        e.accept()
