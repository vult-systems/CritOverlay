"""Data model for strokes and a bounded undo/redo history."""

from collections import deque

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QTransform


class Stroke:
    __slots__ = ("color", "size", "opacity", "eraser", "points", "shape", "text", "font", "erasures")

    def __init__(self, color, size, opacity, eraser=False, shape=None):
        self.color = QColor(color)
        self.size = float(size)
        self.opacity = float(opacity)
        self.eraser = eraser
        self.points = []
        # None for a freehand stroke. Otherwise one of SHAPES, and points
        # holds exactly two entries: the drag's start and (constrained) end.
        self.shape = shape
        # Text annotations (shape "text"): one point, the anchor. size is
        # the font's pixel size; font is a TextStyle.
        self.text = ""
        self.font = None
        # Annotations only: eraser strokes that erased this annotation, each
        # with the transform from where it was drawn to where the annotation
        # is now. They keep erased parts with the annotation when it is moved
        # or resized (see selection.absorb and brush.render_shape_layer).
        self.erasures = ()

    def copy(self):
        dup = Stroke(self.color, self.size, self.opacity, self.eraser, self.shape)
        dup.points = [(QPointF(p), pr) for p, pr in self.points]
        dup.text = self.text
        if self.font is not None:
            dup.font = TextStyle(self.font.family, self.font.bold, self.font.align)
        dup.erasures = tuple((e, QTransform(t)) for e, t in self.erasures)
        return dup


class TextStyle:
    __slots__ = ("family", "bold", "align")

    def __init__(self, family, bold=False, align="left"):
        self.family = family
        self.bold = bool(bold)
        self.align = align


SHAPES = ("rect", "ellipse", "line", "arrow")


class History:
    """The strokes on the canvas plus an undo/redo stack of changes.

    A change removes some strokes (remembering where they were) and appends
    others. That one shape covers drawing a stroke, deleting, cutting,
    pasting, duplicating, and editing an annotation (old version out, new
    version in at the top). Strokes are never mutated once committed;
    edits replace them with a copy.

    The undo stack is capped at max_size changes. Past the cap the oldest
    change stops being undoable; its strokes stay on the canvas.
    """

    def __init__(self, max_size):
        self.strokes = []
        self.undo_stack = deque(maxlen=max_size)
        self.redo_stack = []

    def _index(self, stroke):
        for i, s in enumerate(self.strokes):
            if s is stroke:
                return i
        raise ValueError("stroke not in history")

    def _apply(self, change):
        for i, _stroke in sorted(change.removed, key=lambda e: e[0], reverse=True):
            del self.strokes[i]
        if change.positions is None:
            self.strokes.extend(change.added)
        else:
            for i, stroke in sorted(zip(change.positions, change.added), key=lambda e: e[0]):
                self.strokes.insert(i, stroke)

    def _revert(self, change):
        for stroke in change.added:
            del self.strokes[self._index(stroke)]
        for i, stroke in sorted(change.removed, key=lambda e: e[0]):
            self.strokes.insert(i, stroke)

    def contains(self, stroke):
        return any(s is stroke for s in self.strokes)

    def commit(self, stroke):
        self.change([], [stroke])

    def change(self, removed, added, merge_key=None):
        """Remove `removed` strokes and append `added` as one undo step.
        With merge_key, a change that continues the previous one (same key,
        replacing what it added) folds into it, so dragging a slider over a
        selection is one undo step."""
        last = self.undo_stack[-1] if self.undo_stack else None
        if (merge_key is not None and last is not None and last.merge_key == merge_key
                and len(removed) == 1 and len(last.added) == 1 and last.added[0] is removed[0]):
            del self.strokes[self._index(removed[0])]
            self.strokes.extend(added)
            last.added = list(added)
        else:
            entry = _Change([(self._index(s), s) for s in removed], list(added), merge_key)
            self._apply(entry)
            self.undo_stack.append(entry)
        self.redo_stack.clear()

    def replace(self, old, new, merge_key=None):
        """Swap committed `old` for `new` in the same stacking position, as
        one undo step (merged like change())."""
        i = self._index(old)
        last = self.undo_stack[-1] if self.undo_stack else None
        if (merge_key is not None and last is not None and last.merge_key == merge_key
                and len(last.added) == 1 and last.added[0] is old):
            self.strokes[i] = new
            last.added = [new]
        else:
            entry = _Change([(i, old)], [new], merge_key, positions=[i])
            self._apply(entry)
            self.undo_stack.append(entry)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return False
        entry = self.undo_stack.pop()
        self._revert(entry)
        entry.merge_key = None
        self.redo_stack.append(entry)
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        entry = self.redo_stack.pop()
        self._apply(entry)
        self.undo_stack.append(entry)
        return True

    def clear(self):
        self.strokes.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()

    def __iter__(self):
        return iter(self.strokes)

    def __len__(self):
        return len(self.strokes)


class _Change:
    __slots__ = ("removed", "added", "merge_key", "positions")

    def __init__(self, removed, added, merge_key=None, positions=None):
        self.removed = removed  # [(index before the change, stroke)]
        self.added = added
        self.merge_key = merge_key
        # Where each added stroke goes (indices after the removal), or None
        # to append on top.
        self.positions = positions
