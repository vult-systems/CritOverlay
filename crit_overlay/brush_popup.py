"""Brush and eraser popups, opened by clicking the Brush or Eraser button
while that tool is active.

Brushes are grouped by what they are for. The first brush in each row is
the recommended one (the candidate final set); the others stay available
while the engines are being compared. Which engine draws a brush is only in
its tooltip. Pressure response applies to every brush.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget

from . import constants as C
from . import dab
from . import mypaint_engine

MP = mypaint_engine.PREFIX

# (purpose, [(preset key, label)]): the first entry is the recommendation.
CATALOG = [
    ("Pen", [("pen", "Studio Pen"), (MP + "deevad_liner.myb", "Liner"), (MP + "deevad_ballpen.myb", "Ballpoint")]),
    ("Pencil", [(MP + "tanda_pencil-2b.myb", "Pencil 2B"), ("pencil", "Grain Pencil"),
                (MP + "deevad_4H_pencil.myb", "Pencil 4H")]),
    ("Marker", [("marker", "Chisel Marker"), (MP + "tanda_marker-01.myb", "Felt Marker")]),
    ("Soft", [("soft", "Soft Brush"), (MP + "deevad_airbrush.myb", "Airbrush")]),
    ("Brush", [(MP + "classic_calligraphy.myb", "Brush Pen"), (MP + "classic_textured_ink.myb", "Textured Ink"),
               (MP + "tanda_charcoal-01.myb", "Charcoal")]),
    ("Legacy", [(dab.CLASSIC, "Classic")]),
]


def _available(key):
    if key.startswith(MP):
        return any(k == key for k, _l in mypaint_engine.brushes())
    return key in dab.PRESET_KEYS


def entries():
    """[(purpose, key, label, recommended)] for the brushes available here."""
    out = []
    for purpose, items in CATALOG:
        avail = [(k, label) for k, label in items if _available(k)]
        for i, (k, label) in enumerate(avail):
            out.append((purpose, k, label, i == 0 and purpose != "Legacy"))
    return out


def label_for(key):
    for _purpose, k, label, _rec in entries():
        if k == key:
            return label
    return dab.LABELS.get(key, key)


def _engine(key):
    if key.startswith(MP):
        return "libmypaint"
    return "Crit Overlay classic engine" if key == dab.CLASSIC else "Crit Overlay dab engine"


def _row(parent, caption):
    row = QHBoxLayout()
    row.setSpacing(2)
    cap = QLabel(caption.upper(), parent)
    cap.setObjectName("SliderCaption")
    cap.setFixedWidth(64)
    row.addWidget(cap)
    return row


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
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._overlay = overlay
        self.setObjectName("Toolbar")
        self.setStyleSheet(C.TOOLBAR_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(3)
        self.buttons = {}
        rows = {}
        for purpose, key, label, recommended in entries():
            if purpose not in rows:
                rows[purpose] = _row(self, purpose)
                outer.addLayout(rows[purpose])
            tip = f"{label}{' (recommended)' if recommended else ''}. Engine: {_engine(key)}"
            btn = _button(self, label, tip, lambda checked=False, k=key: self._pick(k))
            if recommended:
                f = QFont(btn.font())
                f.setBold(True)
                btn.setFont(f)
            rows[purpose].addWidget(btn)
            self.buttons[key] = btn
        for row in rows.values():
            row.addStretch(1)

        prow = _row(self, "Pressure")
        self.pressure_buttons = {}
        for name in C.PRESSURE_CURVES:
            btn = _button(self, name.capitalize(),
                          {"soft": "Full strength with a light touch", "normal": "Linear",
                           "firm": "Needs more force for full strength"}[name],
                          lambda checked=False, n=name: self._pick_curve(n))
            prow.addWidget(btn)
            self.pressure_buttons[name] = btn
        prow.addStretch(1)
        self.fine_btn = _button(self, "Fine-tune", "Hardness and spacing for this brush", self._toggle_fine)
        prow.addWidget(self.fine_btn)
        outer.addLayout(prow)

        self.tuning = QWidget(self)
        tl = QVBoxLayout(self.tuning)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        self.hardness, self.hardness_label = self._slider(tl, "HARDNESS", 0, 100, self._on_hardness)
        self.spacing, self.spacing_label = self._slider(tl, "SPACING", 2, 50, self._on_spacing)
        outer.addWidget(self.tuning)
        self.tuning.hide()

    def _slider(self, layout, caption, lo, hi, slot):
        row = QWidget(self.tuning)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 2, 0)
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
        h.addStretch(1)
        slider.valueChanged.connect(slot)
        layout.addWidget(row)
        return slider, label

    def _pick(self, key):
        self._overlay._set_brush_preset(key)
        self.sync()

    def _pick_curve(self, name):
        self._overlay._set_pressure_curve(name)
        self.sync()

    def _toggle_fine(self):
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
        for name, btn in self.pressure_buttons.items():
            btn.setChecked(name == self._overlay.pressure_curve)
        tunable = key in dab.PRESETS
        self.fine_btn.setEnabled(tunable)
        self.tuning.setVisible(tunable and self.fine_btn.isChecked())
        if tunable:
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


class EraserPopup(QFrame):
    def __init__(self, overlay):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
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
        self.adjustSize()
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        self.move(pos.x(), pos.y() + 6)
        self.show()
