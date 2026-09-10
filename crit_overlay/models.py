"""Data model for strokes and a bounded undo/redo history."""

from collections import deque

from PySide6.QtGui import QColor


class Stroke:
    __slots__ = ("color", "size", "opacity", "eraser", "points")

    def __init__(self, color, size, opacity, eraser=False):
        self.color = QColor(color)
        self.size = float(size)
        self.opacity = float(opacity)
        self.eraser = eraser
        self.points = []


class History:
    """Undo/redo stack capped at max_size strokes.

    Past the cap the oldest stroke is dropped. It stays on screen, it just
    stops being undoable, which keeps memory flat over a long session.
    """

    def __init__(self, max_size):
        self.strokes = deque(maxlen=max_size)
        self.redo_stack = []

    def commit(self, stroke):
        self.strokes.append(stroke)
        self.redo_stack.clear()

    def undo(self):
        if not self.strokes:
            return False
        self.redo_stack.append(self.strokes.pop())
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        self.strokes.append(self.redo_stack.pop())
        return True

    def clear(self):
        self.strokes.clear()
        self.redo_stack.clear()

    def __iter__(self):
        return iter(self.strokes)

    def __len__(self):
        return len(self.strokes)
