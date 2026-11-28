"""Popup for the toolbar's drawing-region button.

Keeps the toolbar to one button for the whole feature: choosing the region
(select a rectangle, or back to the full monitor) and the annotation margin
around it (on/off, thickness, white/black, opacity). Same frameless popup
style as the color palette; as a floating panel it closes on an outside
click.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

from . import constants as C
from .ui_widgets import make_panel, place_popup
from . import icons


class RegionPopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None)
        make_panel(self)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        def text_button(text, parent, checkable=False):
            btn = QToolButton(parent)
            btn.setText(text)
            btn.setCheckable(checkable)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            return btn

        def row():
            w = QFrame(self)
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            layout.addWidget(w)
            return w, h

        caption = QLabel("DRAWING REGION", self)
        caption.setObjectName("SliderCaption")
        layout.addWidget(caption)

        w, h = row()
        self.select_btn = text_button("Capture area (R)", w)
        self.full_btn = text_button("Full monitor", w, checkable=True)
        self.select_btn.clicked.connect(self._select)
        self.full_btn.clicked.connect(self._full)
        h.addWidget(self.select_btn)
        h.addWidget(self.full_btn)

        w, h = row()
        # Says its state in words; a checked text button alone didn't read
        # as an on/off switch (#13).
        self.margin_btn = text_button("Margin: off", w, checkable=True)
        self.margin_btn.toggled.connect(overlay.set_margin_enabled)
        h.addWidget(self.margin_btn)
        h.addStretch(1)
        self.white_btn = QToolButton(w)
        self.black_btn = QToolButton(w)
        for btn, name in ((self.white_btn, "white"), (self.black_btn, "black")):
            btn.setIcon(icons.build_color_swatch_icon("#ffffff" if name == "white" else "#000000"))
            btn.setIconSize(QSize(18, 18))
            btn.setCheckable(True)
            btn.setAutoRaise(True)
            btn.clicked.connect(lambda checked=False, n=name: overlay.set_margin_color(n))
            h.addWidget(btn)

        self._margin_row = w
        size_row, self.size_slider, self.size_label = overlay._build_slider_row(
            self, "MARGIN", C.MARGIN_MIN_SIZE, C.MARGIN_MAX_SIZE, overlay.margin_size,
            lambda v: f"{v}px", overlay.set_margin_size, None,
        )
        layout.addWidget(size_row)
        self._size_row = size_row
        opacity_row, self.opacity_slider, self.opacity_label = overlay._build_slider_row(
            self, "OPACITY", 10, 100, int(round(overlay.margin_opacity * 100)),
            lambda v: f"{v}%", lambda v: overlay.set_margin_opacity(v / 100.0), None,
        )
        layout.addWidget(opacity_row)
        self._opacity_row = opacity_row

        self.note = QLabel("", self)
        self.note.setObjectName("SliderValue")
        layout.addWidget(self.note)

    def _select(self):
        self.close()
        self._overlay.begin_region_selection()

    def _full(self):
        self._overlay.set_region(None)
        self.sync()

    def sync(self):
        """Reflect the overlay's current state (it can change by hotkey or a
        monitor switch while the popup is closed)."""
        o = self._overlay
        has_area = o.region is not None or o.capture is not None
        self.full_btn.setChecked(not has_area)
        self.margin_btn.blockSignals(True)
        self.margin_btn.setChecked(o.margin_enabled)
        self.margin_btn.blockSignals(False)
        self.margin_btn.setText(f"Margin: {'on' if o.margin_enabled else 'off'}")
        # A margin needs an area to surround; don't offer controls that
        # can't show anything yet.
        for widget in (self._margin_row, self._size_row, self._opacity_row):
            widget.setEnabled(has_area)
        self.white_btn.setChecked(o.margin_color == "white")
        self.black_btn.setChecked(o.margin_color == "black")
        for slider, label, value, text in (
            (self.size_slider, self.size_label, o.margin_size, f"{o.margin_size}px"),
            (self.opacity_slider, self.opacity_label, int(round(o.margin_opacity * 100)),
             f"{int(round(o.margin_opacity * 100))}%"),
        ):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
            label.setText(text)
        if o.capture is not None:
            s = o.capture.size()
            shown = "" if o.capture.scale >= 1.0 else f", shown at {round(o.capture.scale * 100)}%"
            self.note.setText(f"Capture {round(s.width())} x {round(s.height())}{shown}")
        elif o.region is None:
            self.note.setText("Select an area to add a margin around it")
        else:
            r = o.region
            self.note.setText(f"Area {r.width()} x {r.height()} at {r.x()}, {r.y()}")

    def show_below(self, widget):
        self.sync()
        self.adjustSize()
        place_popup(self, widget)
