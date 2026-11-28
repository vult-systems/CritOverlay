"""Caret and selection for text being typed on the canvas.

A plain string with a caret and an anchor index (the selection runs between
them). Layout comes from brush.text_layout(), the same one that draws the
text, so the caret and highlight always line up with the glyphs.
"""

import re

from PySide6.QtCore import QPointF, QRectF

from . import brush

_WORD = re.compile(r"\w+|[^\w\s]+")


class TextEditor:
    def __init__(self, stroke):
        self.stroke = stroke
        self.caret = self.anchor = len(stroke.text)

    @property
    def text(self):
        return self.stroke.text

    # -- selection --------------------------------------------------------

    def has_selection(self):
        return self.caret != self.anchor

    def selection(self):
        return min(self.caret, self.anchor), max(self.caret, self.anchor)

    def selected_text(self):
        lo, hi = self.selection()
        return self.text[lo:hi]

    def select_all(self):
        self.anchor, self.caret = 0, len(self.text)

    def _set_caret(self, pos, extend):
        self.caret = max(0, min(len(self.text), pos))
        if not extend:
            self.anchor = self.caret

    # -- editing ----------------------------------------------------------

    def insert(self, s):
        lo, hi = self.selection()
        self.stroke.text = self.text[:lo] + s + self.text[hi:]
        self.caret = self.anchor = lo + len(s)

    def backspace(self):
        if self.has_selection():
            self.insert("")
        elif self.caret > 0:
            self.anchor = self.caret - 1
            self.insert("")

    def delete(self):
        if self.has_selection():
            self.insert("")
        elif self.caret < len(self.text):
            self.anchor = self.caret + 1
            self.insert("")

    # -- layout -----------------------------------------------------------

    def _line_col(self, pos):
        before = self.text[:pos]
        line = before.count("\n")
        return line, pos - (before.rfind("\n") + 1)

    def _pos(self, lines, line, col):
        return sum(len(s) + 1 for s in lines[:line]) + max(0, min(col, len(lines[line])))

    def _x(self, layout, line, col):
        fm, lines, widths, rect = layout
        return brush.line_x(rect, widths[line], self.stroke.font.align) + fm.horizontalAdvance(lines[line][:col])

    def _col_at(self, layout, line, x):
        fm, lines, widths, rect = layout
        best, best_d = 0, None
        for col in range(len(lines[line]) + 1):
            d = abs(self._x(layout, line, col) - x)
            if best_d is None or d < best_d:
                best, best_d = col, d
        return best

    def move(self, kind, extend=False):
        layout = brush.text_layout(self.stroke)
        lines = layout[1]
        line, col = self._line_col(self.caret)
        if kind == "left":
            if self.has_selection() and not extend:
                pos = self.selection()[0]
            else:
                pos = self.caret - 1
        elif kind == "right":
            if self.has_selection() and not extend:
                pos = self.selection()[1]
            else:
                pos = self.caret + 1
        elif kind in ("up", "down"):
            target = line + (-1 if kind == "up" else 1)
            if target < 0:
                pos = 0
            elif target >= len(lines):
                pos = len(self.text)
            else:
                pos = self._pos(lines, target, self._col_at(layout, target, self._x(layout, line, col)))
        elif kind == "home":
            pos = self._pos(lines, line, 0)
        elif kind == "end":
            pos = self._pos(lines, line, len(lines[line]))
        elif kind == "start":
            pos = 0
        elif kind == "finish":
            pos = len(self.text)
        elif kind == "word_left":
            starts = [m.start() for m in _WORD.finditer(self.text) if m.start() < self.caret]
            pos = starts[-1] if starts else 0
        elif kind == "word_right":
            ends = [m.end() for m in _WORD.finditer(self.text) if m.end() > self.caret]
            pos = ends[0] if ends else len(self.text)
        else:
            raise ValueError(kind)
        self._set_caret(pos, extend)

    def place(self, point, extend=False):
        """Caret to the character boundary nearest `point` (stroke coords)."""
        layout = brush.text_layout(self.stroke)
        fm, lines, _widths, rect = layout
        line = int((point.y() - rect.top()) // max(fm.lineSpacing(), 1e-6))
        line = max(0, min(len(lines) - 1, line))
        self._set_caret(self._pos(lines, line, self._col_at(layout, line, point.x())), extend)

    def caret_line(self):
        layout = brush.text_layout(self.stroke)
        fm, _lines, _widths, rect = layout
        line, col = self._line_col(self.caret)
        x = self._x(layout, line, col)
        top = rect.top() + line * fm.lineSpacing()
        return QPointF(x, top), QPointF(x, top + fm.lineSpacing())

    def selection_rects(self):
        if not self.has_selection():
            return []
        layout = brush.text_layout(self.stroke)
        fm, lines, _widths, rect = layout
        lo, hi = self.selection()
        (l0, c0), (l1, c1) = self._line_col(lo), self._line_col(hi)
        out = []
        for line in range(l0, l1 + 1):
            a = c0 if line == l0 else 0
            b = c1 if line == l1 else len(lines[line])
            x0, x1 = self._x(layout, line, a), self._x(layout, line, b)
            if line != l1:
                x1 += fm.averageCharWidth() * 0.4  # show the selected line break
            out.append(QRectF(x0, rect.top() + line * fm.lineSpacing(), max(1.0, x1 - x0), fm.lineSpacing()))
        return out
