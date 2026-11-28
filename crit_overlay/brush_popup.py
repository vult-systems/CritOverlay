"""Brush presets popup, opened by clicking the Brush button while the brush
is active. Four curated presets, the classic engine for comparison, and the
two tuning controls (hardness and spacing) for the current preset."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget

from . import constants as C
from . import dab
from . import mypaint_engine


class BrushPopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.buttons = {}
        for key in dab.PRESET_KEYS:
            btn = QToolButton(self)
            btn.setText(dab.LABELS[key])
            btn.setAutoRaise(True)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            if key == dab.CLASSIC:
                btn.setToolTip("The previous brush engine, for comparison")
            btn.clicked.connect(lambda checked=False, k=key: self._pick(k))
            row.addWidget(btn)
            self.buttons[key] = btn
        outer.addLayout(row)

        # libmypaint brushes, when the engine is built (comparison).
        mp = mypaint_engine.brushes()
        if mp:
            cap = QLabel("MYPAINT (libmypaint)", self)
            cap.setObjectName("SliderCaption")
            outer.addWidget(cap)
            for start in range(0, len(mp), 5):
                mrow = QHBoxLayout()
                mrow.setSpacing(2)
                for key, label in mp[start:start + 5]:
                    btn = QToolButton(self)
                    btn.setText(label)
                    btn.setAutoRaise(True)
                    btn.setCheckable(True)
                    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                    btn.clicked.connect(lambda checked=False, k=key: self._pick(k))
                    mrow.addWidget(btn)
                    self.buttons[key] = btn
                mrow.addStretch(1)
                outer.addLayout(mrow)

        self.tuning = QWidget(self)
        tl = QVBoxLayout(self.tuning)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        self.hardness, self.hardness_label = self._slider(tl, "HARDNESS", 0, 100, self._on_hardness)
        self.spacing, self.spacing_label = self._slider(tl, "SPACING", 2, 50, self._on_spacing)
        outer.addWidget(self.tuning)

    def _slider(self, layout, caption, lo, hi, slot):
        row = QWidget(self.tuning)
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 0, 2, 0)
        h.setSpacing(6)
        cap = QLabel(caption, row)
        cap.setObjectName("SliderCaption")
        cap.setFixedWidth(64)
        h.addWidget(cap)
        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(lo, hi)
        slider.setFixedWidth(140)
        h.addWidget(slider)
        label = QLabel("", row)
        label.setObjectName("SliderValue")
        label.setFixedWidth(36)
        h.addWidget(label)
        slider.valueChanged.connect(slot)
        layout.addWidget(row)
        return slider, label

    def _pick(self, key):
        self._overlay._set_brush_preset(key)
        self.sync()

    def _on_hardness(self, v):
        self.hardness_label.setText(f"{v}%")
        self._overlay._set_brush_tuning(hardness=v / 100.0)

    def _on_spacing(self, v):
        self.spacing_label.setText(f"{v}%")
        self._overlay._set_brush_tuning(spacing=v / 100.0)

    def sync(self):
        key = self._overlay.brush_preset
        for k, btn in self.buttons.items():
            btn.setChecked(k == key)
        self.tuning.setVisible(key in dab.PRESETS)
        if key in dab.PRESETS:
            h, s = self._overlay._tuning_for(key)
            for slider, value in ((self.hardness, h), (self.spacing, s)):
                slider.blockSignals(True)
                slider.setValue(int(round(value * 100)))
                slider.blockSignals(False)
            self.hardness_label.setText(f"{int(round(h * 100))}%")
            self.spacing_label.setText(f"{int(round(s * 100))}%")
        self.adjustSize()

    def show_below(self, widget):
        self.sync()
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        self.move(pos.x(), pos.y() + 6)
        self.show()
