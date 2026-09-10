import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QSize, Qt, QTimer, Signal, QEvent
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPointingDevice,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QWidget,
)

from . import brush
from . import constants as C
from . import icons
from .color_popup import ColorPopup
from .config import AppConfig
from .models import History, Stroke
from .native_windows import set_click_through
from .pass_through_badge import PassThroughBadge

logger = logging.getLogger(f"{C.ORG_NAME}.overlay")


class Overlay(QWidget):
    status_changed = Signal(str)

    def __init__(self):
        super().__init__()
        logger.debug("Overlay init start")

        self._config = AppConfig()
        saved = self._config.load()

        self.setWindowTitle(f"{C.APP_NAME} v{C.APP_VERSION}")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.history = History(C.MAX_STROKE_HISTORY)
        self.current = None
        # Incremental buffer for the stroke being drawn. Rebuilding the
        # whole stroke's smoothed path and offscreen layer on every
        # paintEvent cost more per frame the longer the stroke got. See
        # _extend_live_layer().
        self._live_layer = None
        self._live_layer_origin = None
        self._live_baked_index = 0

        self.mode = "brush"
        self._pre_eyedropper_mode = "brush"
        self.color = QColor(saved["color"])
        # Named brush_size (not `size`) so it never shadows QWidget.size().
        self.brush_size = saved["size"]
        self.opacity = saved["opacity"]
        self.smoothing = saved["smoothing"]
        self.backdrop = saved["backdrop"]
        self.backdrop_opacity = saved["backdrop_opacity"]
        self.pass_through = False

        self.raw_pos = None
        self.lazy_pos = None
        self.cursor_pos = None
        self.pressure = C.DEFAULT_PRESSURE
        self._last_effective_pressure = C.DEFAULT_PRESSURE
        self._last_point_time = 0.0
        self._last_tablet_time = 0.0
        self._tablet_contact = False

        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.timeout.connect(self._flush_settings)

        self._canvas = None
        self._update_geometry()
        self._rebuild_canvas()

        app = QGuiApplication.instance()
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)

        self.hover_tip = QLabel("", self)
        self.hover_tip.setStyleSheet(C.TOAST_STYLE)
        self.hover_tip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hover_tip.hide()
        self._hover_tip_timer = QTimer(self)
        self._hover_tip_timer.setSingleShot(True)
        self._hover_tip_timer.timeout.connect(self._commit_hover_tip)
        self._pending_hover = None

        self._backdrop_rebuild_timer = QTimer(self)
        self._backdrop_rebuild_timer.setSingleShot(True)
        self._backdrop_rebuild_timer.timeout.connect(self._apply_debounced_backdrop_rebuild)

        self._color_popup = None
        self.panel = self._build_panel()
        self.panel.adjustSize()
        self._refresh_history_buttons()

        self.toast_label = QLabel("", self)
        self.toast_label.setStyleSheet(C.TOAST_STYLE)
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)
        self._toast_anchor = (self._primary_local_rect(), C.PANEL_MARGIN_TOP)

        self._place_chrome()

        # A separate top-level window, so it stays clickable while self is
        # WA_TransparentForMouseEvents and has lost focus. Only shown during
        # pass-through.
        self._pass_through_badge = PassThroughBadge()
        self._pass_through_badge.clicked.connect(self.toggle_pass_through)

        self.status_changed.connect(self._toast)
        self._install_shortcuts()

        self._toast("B Brush • E Eraser • F8 Pass-through • F9 Snapshot", ms=C.STARTUP_HINT_MS)

        logger.debug("Overlay init complete")

    # -- geometry -----------------------------------------------------

    def _update_geometry(self):
        screens = QGuiApplication.screens()
        geo = screens[0].geometry()
        for s in screens[1:]:
            geo = geo.united(s.geometry())
        logger.debug("Virtual desktop geometry: %s", geo)
        self.setGeometry(geo)

    def _on_screens_changed(self, *_args):
        logger.info("Screen configuration changed; recomputing overlay geometry")
        self._update_geometry()
        self._place_chrome()

    def resizeEvent(self, event):
        # Guarded because Qt can fire an initial resizeEvent from within
        # QWidget's own constructor, before __init__ below has created
        # self.history to rebuild the canvas from.
        if hasattr(self, "history"):
            self._rebuild_canvas()
        super().resizeEvent(event)

    def _primary_local_rect(self):
        # Toolbar/toast are docked to the primary monitor, not centered
        # across the full multi-monitor virtual desktop (which could place
        # them in the gap between two screens).
        primary = QGuiApplication.primaryScreen().geometry()
        return primary.translated(-self.geometry().topLeft())

    def _place_chrome(self):
        local = self._primary_local_rect()
        panel_x = local.x() + max(16, (local.width() - self.panel.width()) // 2)
        panel_y = local.y() + C.PANEL_MARGIN_TOP
        self.panel.move(panel_x, panel_y)
        self._toast_anchor = (local, panel_y + self.panel.height() + C.TOAST_GAP)
        if self.toast_label.isVisible():
            self._position_toast()
        if hasattr(self, "_pass_through_badge"):
            self._position_pass_through_badge()

    def _position_pass_through_badge(self):
        # A separate top-level window, so this uses global screen
        # coordinates directly rather than the overlay-local coordinates
        # _place_chrome's other targets use.
        primary = QGuiApplication.primaryScreen().geometry()
        badge = self._pass_through_badge
        x = primary.x() + max(16, (primary.width() - badge.width()) // 2)
        y = primary.y() + C.PANEL_MARGIN_TOP
        badge.move(x, y)

    def _position_toast(self):
        local, y = self._toast_anchor
        x = local.x() + max(16, (local.width() - self.toast_label.width()) // 2)
        self.toast_label.move(x, y)

    # -- UI construction -----------------------------------------------

    def _build_slider_row(self, parent, caption, minimum, maximum, initial, formatter, on_change, tooltip):
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 0, 2, 0)
        layout.setSpacing(6)

        cap = QLabel(caption, row)
        cap.setObjectName("SliderCaption")
        layout.addWidget(cap)

        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(minimum, maximum)
        slider.setValue(initial)
        slider.setFixedWidth(70)
        layout.addWidget(slider)

        value_label = QLabel(formatter(initial), row)
        value_label.setObjectName("SliderValue")
        layout.addWidget(value_label)

        slider.valueChanged.connect(on_change)
        self._hoverable(row, tooltip)
        return row, slider, value_label

    def _hoverable(self, widget, tooltip):
        # Native QToolTip popups can render behind this window, since it's
        # WindowStaysOnTopHint. Track the text ourselves and draw our own
        # tip (self.hover_tip, a child of this window) from eventFilter()
        # instead.
        widget.setToolTip(tooltip)
        widget.installEventFilter(self)
        return widget

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter:
            tip = obj.toolTip()
            if tip:
                self._pending_hover = (obj, tip)
                self._hover_tip_timer.start(400)
        elif event.type() == QEvent.Type.Leave:
            self._hover_tip_timer.stop()
            self._pending_hover = None
            self.hover_tip.hide()
        return super().eventFilter(obj, event)

    def _commit_hover_tip(self):
        if self._pending_hover:
            widget, text = self._pending_hover
            self._show_hover_tip(widget, text)

    def _show_hover_tip(self, widget, text):
        self.hover_tip.setText(text)
        self.hover_tip.adjustSize()
        anchor = widget.mapTo(self, widget.rect().bottomLeft())
        x = anchor.x() + (widget.width() - self.hover_tip.width()) // 2
        y = anchor.y() + 8
        self.hover_tip.move(x, y)
        self.hover_tip.show()
        self.hover_tip.raise_()

    def _build_panel(self):
        panel = QFrame(self)
        panel.setObjectName("Toolbar")
        panel.setStyleSheet(C.TOOLBAR_STYLE)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        def sep():
            line = QFrame(panel)
            line.setObjectName("Sep")
            line.setFixedWidth(1)
            line.setFixedHeight(26)
            layout.addWidget(line)

        def tool_button(icon_name, tooltip, checkable=False):
            btn = QToolButton(panel)
            btn.setIcon(icons.build_icon(icon_name))
            btn.setIconSize(QSize(20, 20))
            btn.setCheckable(checkable)
            btn.setAutoRaise(True)
            self._hoverable(btn, tooltip)
            layout.addWidget(btn)
            return btn

        self.brush_btn = tool_button("brush", "Brush (B)", checkable=True)
        self.eraser_btn = tool_button("eraser", "Eraser (E)", checkable=True)
        self.eyedropper_btn = tool_button("eyedropper", "Eyedropper (Alt+click)", checkable=True)
        self.brush_btn.setChecked(self.mode == "brush")
        self.eraser_btn.setChecked(self.mode == "eraser")
        self.eyedropper_btn.setChecked(self.mode == "eyedropper")
        self.brush_btn.clicked.connect(lambda: self._set_mode("brush"))
        self.eraser_btn.clicked.connect(lambda: self._set_mode("eraser"))
        self.eyedropper_btn.clicked.connect(self._toggle_eyedropper)

        sep()

        self.color_btn = QToolButton(panel)
        self.color_btn.setIcon(icons.build_color_swatch_icon(self.color.name()))
        self.color_btn.setIconSize(QSize(20, 20))
        self.color_btn.setAutoRaise(True)
        self.color_btn.clicked.connect(self._show_color_popup)
        self._hoverable(self.color_btn, "Brush color")
        layout.addWidget(self.color_btn)

        self.backdrop_btn = QToolButton(panel)
        self.backdrop_btn.setIcon(self._backdrop_icon(self.backdrop))
        self.backdrop_btn.setIconSize(QSize(20, 20))
        self.backdrop_btn.setAutoRaise(True)
        self.backdrop_btn.clicked.connect(self._cycle_backdrop)
        self._hoverable(self.backdrop_btn, f"Backdrop: {self._BACKDROP_LABEL[self.backdrop]} (click to cycle)")
        layout.addWidget(self.backdrop_btn)

        sep()

        size_row, self.size_slider, self.size_value_label = self._build_slider_row(
            panel, "SIZE", C.MIN_SIZE, C.MAX_SIZE, self.brush_size, lambda v: f"{v}px",
            self._on_size_slider_changed, "Brush size ([ / ])"
        )
        layout.addWidget(size_row)

        opacity_row, self.opacity_slider, self.opacity_value_label = self._build_slider_row(
            panel, "OPACITY", 5, 100, int(self.opacity * 100), lambda v: f"{v}%",
            self._on_opacity_slider_changed, "Stroke opacity"
        )
        layout.addWidget(opacity_row)

        smoothing_row, self.smoothing_slider, self.smoothing_value_label = self._build_slider_row(
            panel, "SMOOTH", 0, 90, int(self.smoothing * 100), lambda v: f"{v}%",
            self._on_smoothing_slider_changed, "Stroke smoothing"
        )
        layout.addWidget(smoothing_row)

        backdrop_opacity_row, self.backdrop_opacity_slider, self.backdrop_opacity_value_label = self._build_slider_row(
            panel, "INTENSITY", 10, 100, int(self.backdrop_opacity * 100), lambda v: f"{v}%",
            self._on_backdrop_opacity_slider_changed, "Backdrop intensity"
        )
        layout.addWidget(backdrop_opacity_row)

        sep()

        self.undo_btn = tool_button("undo", "Undo (Ctrl+Z)")
        self.redo_btn = tool_button("redo", "Redo (Ctrl+Shift+Z)")
        self.clear_btn = tool_button("clear", "Clear all")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)
        self.clear_btn.clicked.connect(self.clear)

        sep()

        self.pass_btn = tool_button("pass_through", "Pass-through (F8)", checkable=True)
        self.snap_btn = tool_button("snapshot", "Screenshot (F9)")
        self.exit_btn = tool_button("exit", "Exit (Esc)")
        self.pass_btn.clicked.connect(self.toggle_pass_through)
        self.snap_btn.clicked.connect(self.capture_annotated)
        self.exit_btn.clicked.connect(self.close)

        panel.adjustSize()
        return panel

    def _install_shortcuts(self):
        def add(seq, fn):
            a = QAction(self)
            a.setShortcut(QKeySequence(seq))
            a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            a.triggered.connect(fn)
            self.addAction(a)

        add("B", lambda: self._set_mode("brush"))
        add("E", lambda: self._set_mode("eraser"))
        add("I", self._toggle_eyedropper)
        add("Ctrl+Z", self.undo)
        add("Ctrl+Shift+Z", self.redo)
        add("F8", self.toggle_pass_through)
        add("F9", self.capture_annotated)
        add("[", lambda: self.change_size(-1))
        add("]", lambda: self.change_size(1))
        add("Esc", self.close)

    def _toast(self, text, ms=C.TOAST_DURATION_MS):
        self.toast_label.setText(text)
        self.toast_label.adjustSize()
        self._position_toast()
        self.toast_label.show()
        self.toast_label.raise_()
        self._toast_timer.start(ms)

    # -- settings persistence -------------------------------------------

    def _save_settings(self):
        # Debounced. Every setter calls this, including on each valueChanged
        # tick while a slider is dragged, and QSettings.sync() forces a disk
        # flush that stutters the UI at that rate. In-memory state is
        # already current; only the write is deferred.
        self._settings_save_timer.start(300)

    def _flush_settings(self):
        self._config.save(
            color=self.color,
            size=self.brush_size,
            opacity=self.opacity,
            smoothing=self.smoothing,
            backdrop=self.backdrop,
            backdrop_opacity=self.backdrop_opacity,
        )

    # State setters. Every mutable tool property goes through one of these,
    # which updates the model and the widget showing it together, so a
    # hotkey and a toolbar control can't disagree about what's selected.

    def _set_mode(self, mode):
        self.mode = mode
        self.brush_btn.setChecked(mode == "brush")
        self.eraser_btn.setChecked(mode == "eraser")
        self.eyedropper_btn.setChecked(mode == "eyedropper")
        self.update()

    def _toggle_eyedropper(self):
        if self.mode == "eyedropper":
            self._set_mode(self._pre_eyedropper_mode)
        else:
            self._pre_eyedropper_mode = self.mode
            self._set_mode("eyedropper")
            self.status_changed.emit("Click anywhere to sample a color")

    def _pick_color_at(self, global_pos):
        point = global_pos.toPoint()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        local = point - screen.geometry().topLeft()
        pixmap = screen.grabWindow(0, local.x(), local.y(), 1, 1)
        if pixmap.isNull():
            return
        color = pixmap.toImage().pixelColor(0, 0)
        if color.isValid():
            self._set_color(color)
            self.status_changed.emit(f"Picked {color.name()}")

    def _pick_color_and_restore_mode(self, global_pos):
        # Shared by mousePressEvent and _handle_tablet_event. Sample the
        # color, then if the pick came from the Eyedropper tool rather than
        # a momentary Alt+click, go back to whatever tool was active before
        # it.
        self._pick_color_at(global_pos)
        if self.mode == "eyedropper":
            self._set_mode(self._pre_eyedropper_mode)

    def _set_size(self, value):
        value = max(C.MIN_SIZE, min(C.MAX_SIZE, int(round(value))))
        self.brush_size = value
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(value)
        self.size_slider.blockSignals(False)
        self.size_value_label.setText(f"{value}px")
        self._save_settings()

    def _on_size_slider_changed(self, v):
        self._set_size(v)

    def change_size(self, delta):
        self._set_size(self.brush_size + delta)

    def _set_opacity(self, percent):
        percent = max(5, min(100, int(round(percent))))
        self.opacity = percent / 100.0
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(percent)
        self.opacity_slider.blockSignals(False)
        self.opacity_value_label.setText(f"{percent}%")
        self._save_settings()

    def _on_opacity_slider_changed(self, v):
        self._set_opacity(v)

    def _set_smoothing(self, percent):
        percent = max(0, min(90, int(round(percent))))
        self.smoothing = percent / 100.0
        self.smoothing_slider.blockSignals(True)
        self.smoothing_slider.setValue(percent)
        self.smoothing_slider.blockSignals(False)
        self.smoothing_value_label.setText(f"{percent}%")
        self._save_settings()

    def _on_smoothing_slider_changed(self, v):
        self._set_smoothing(v)

    def _set_color(self, color):
        self.color = QColor(color)
        self.color_btn.setIcon(icons.build_color_swatch_icon(self.color.name()))
        self._save_settings()
        self.update()

    def pick_color(self):
        c = QColorDialog.getColor(self.color, self, "Brush Color")
        if c.isValid():
            self._set_color(c)

    def _show_color_popup(self):
        if self._color_popup is None:
            self._color_popup = ColorPopup(self)
        self._color_popup.show_below(self.color_btn)

    _BACKDROP_CYCLE = [None, "white", "black"]
    _BACKDROP_LABEL = {None: "Off", "white": "White", "black": "Black"}

    def _backdrop_icon(self, value):
        if value is None:
            return icons.build_icon("backdrop_off")
        return icons.build_color_swatch_icon("#ffffff" if value == "white" else "#000000")

    def _set_backdrop(self, value):
        self.backdrop = value
        self.backdrop_btn.setIcon(self._backdrop_icon(value))
        self.backdrop_btn.setToolTip(f"Backdrop: {self._BACKDROP_LABEL[value]} (click to cycle)")
        self._save_settings()
        # The backdrop is its own layer, painted underneath the canvas on
        # every paintEvent (see _paint_backdrop), so this only needs a
        # repaint.
        self.update()

    def _cycle_backdrop(self):
        idx = (self._BACKDROP_CYCLE.index(self.backdrop) + 1) % len(self._BACKDROP_CYCLE)
        self._set_backdrop(self._BACKDROP_CYCLE[idx])

    def _set_backdrop_opacity(self, percent):
        percent = max(10, min(100, int(round(percent))))
        self.backdrop_opacity = percent / 100.0
        self.backdrop_opacity_slider.blockSignals(True)
        self.backdrop_opacity_slider.setValue(percent)
        self.backdrop_opacity_slider.blockSignals(False)
        self.backdrop_opacity_value_label.setText(f"{percent}%")
        self._save_settings()
        # Still debounced. The backdrop covers the whole virtual desktop, so
        # a full-window repaint per slider tick is more than needed.
        self._backdrop_rebuild_timer.start(60)

    def _on_backdrop_opacity_slider_changed(self, v):
        self._set_backdrop_opacity(v)

    def _apply_debounced_backdrop_rebuild(self):
        self.update()

    # -- window / mode actions ---------------------------------------------

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def toggle_pass_through(self):
        self.pass_through = not self.pass_through

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.pass_through)
        # The Qt attribute above doesn't reach the native WS_EX_TRANSPARENT
        # style for this window's flags, so clicks keep landing here. Set
        # the native style directly. See native_windows.py.
        set_click_through(int(self.winId()), self.pass_through)
        self.panel.setVisible(not self.pass_through)
        self.pass_btn.setChecked(self.pass_through)

        if self.pass_through:
            self.status_changed.emit("Pass-through ON. Click the badge or F8 to return")
            self._position_pass_through_badge()
            self._pass_through_badge.show()
            self._pass_through_badge.raise_()
        else:
            self._pass_through_badge.hide()
            self.status_changed.emit("Draw mode")
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

        # The backdrop layer checks self.pass_through itself every repaint
        # (see _paint_backdrop), so no canvas rebuild is needed here either.
        self.update()
        QApplication.processEvents()

    def undo(self):
        if self.history.undo():
            self._rebuild_canvas()
            self._refresh_history_buttons()
            self.update()

    def redo(self):
        if self.history.redo():
            self._rebuild_canvas()
            self._refresh_history_buttons()
            self.update()

    def clear(self):
        if len(self.history) == 0 and self.current is None:
            return
        self.history.clear()
        self.current = None
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self.update()

    def _refresh_history_buttons(self):
        self.undo_btn.setEnabled(len(self.history) > 0)
        self.redo_btn.setEnabled(len(self.history.redo_stack) > 0)

    # -- stroke lifecycle --------------------------------------------------

    def _begin_stroke(self, pos, pressure, eraser=None):
        if self.pass_through:
            return
        self.raw_pos = QPointF(pos)
        self.lazy_pos = QPointF(pos)
        self.cursor_pos = QPointF(pos)
        self.pressure = pressure if pressure > 0 else C.DEFAULT_PRESSURE
        use_eraser = self.mode == "eraser" if eraser is None else (eraser or self.mode == "eraser")
        self.current = Stroke(self.color, self.brush_size, self.opacity, use_eraser)
        self._live_layer = None
        self._live_layer_origin = None
        self._live_baked_index = 0
        # Seed the first point immediately (rather than waiting for enough
        # movement to pass the lazy-brush radius) so a plain tap still
        # leaves a dot instead of silently drawing nothing.
        self._last_effective_pressure = brush.effective_pressure(self.pressure, 0.0)
        self.current.points.append((QPointF(self.lazy_pos), self._last_effective_pressure))
        self._last_point_time = time.monotonic()
        self.history.redo_stack.clear()
        self._refresh_history_buttons()

    def _add_point(self, pos, pressure):
        if self.pass_through or not self.current:
            return
        raw = QPointF(pos)
        prev_cursor = self.cursor_pos
        prev_lazy = QPointF(self.lazy_pos)
        self.cursor_pos = raw
        self.pressure = pressure if pressure > 0 else C.DEFAULT_PRESSURE

        radius = 2 + self.smoothing * 44
        dx = raw.x() - self.lazy_pos.x()
        dy = raw.y() - self.lazy_pos.y()
        dist = (dx * dx + dy * dy) ** 0.5

        if dist <= radius:
            self._mark_dirty(prev_cursor, raw)
            return

        pull = (dist - radius) / dist
        target = QPointF(self.lazy_pos.x() + dx * pull, self.lazy_pos.y() + dy * pull)
        ease = 0.72 - self.smoothing * 0.22
        self.lazy_pos = QPointF(
            self.lazy_pos.x() + (target.x() - self.lazy_pos.x()) * ease,
            self.lazy_pos.y() + (target.y() - self.lazy_pos.y()) * ease,
        )

        last_point, _ = self.current.points[-1]
        moved = ((last_point.x() - self.lazy_pos.x()) ** 2 + (last_point.y() - self.lazy_pos.y()) ** 2) ** 0.5
        if moved >= C.MIN_POINT_SPACING:
            now = time.monotonic()
            dt = max(1e-3, now - self._last_point_time)
            speed = moved / dt
            self._last_effective_pressure = brush.effective_pressure(self.pressure, speed)
            self.current.points.append((QPointF(self.lazy_pos), self._last_effective_pressure))
            self._last_point_time = now
            self._extend_live_layer()
        self._mark_dirty(prev_cursor, raw, prev_lazy, self.lazy_pos)

    # How many already-baked trailing points to recompute with each new one.
    # Catmull-Rom segment i depends only on points i-1..i+2, so a small
    # overlap reproduces the same curve as a full-stroke recompute.
    _LIVE_LAYER_OVERLAP = 3

    def _extend_live_layer(self):
        stroke = self.current
        points = stroke.points
        n = len(points)
        start = max(0, self._live_baked_index - self._LIVE_LAYER_OVERLAP)
        window = points[start:]
        if len(window) < 2:
            return
        # The true point preceding the window, so its first segment's
        # curvature matches what a full-stroke recompute would produce
        # instead of being (wrongly) treated as the start of the stroke.
        extra_before = points[start - 1][0] if start > 0 else None
        segments = brush.segments_for_points(window, stroke.size, extra_before=extra_before)
        layer = brush.render_stroke_layer(stroke, segments)
        if layer is None:
            return
        new_pixmap, new_origin = layer
        new_rect = QRect(new_origin, new_pixmap.size())

        if self._live_layer is None:
            self._live_layer, self._live_layer_origin = new_pixmap, new_origin
        else:
            old_rect = QRect(self._live_layer_origin, self._live_layer.size())
            if old_rect.contains(new_rect):
                # Fits in the existing buffer, so draw in place. The buffer
                # is always alpha=255 internally and opacity is applied once
                # at final composite, so painting opaque over opaque here
                # can't double-blend.
                painter = QPainter(self._live_layer)
                try:
                    painter.drawPixmap(new_origin - self._live_layer_origin, new_pixmap)
                finally:
                    painter.end()
            else:
                union = old_rect.united(new_rect)
                merged = QPixmap(union.size())
                merged.fill(Qt.GlobalColor.transparent)
                painter = QPainter(merged)
                try:
                    painter.drawPixmap(old_rect.topLeft() - union.topLeft(), self._live_layer)
                    painter.drawPixmap(new_rect.topLeft() - union.topLeft(), new_pixmap)
                finally:
                    painter.end()
                self._live_layer, self._live_layer_origin = merged, union.topLeft()
        self._live_baked_index = n

    def _end_stroke(self):
        if self.current:
            finished = self.current
            self.current = None
            self.lazy_pos = None
            self._live_layer = None
            self._live_layer_origin = None
            self._live_baked_index = 0
            if finished.points:
                self.history.commit(finished)
                self._commit_stroke_to_canvas(finished)
                self._refresh_history_buttons()
                self._mark_dirty(*(p for p, _ in finished.points))
            else:
                self.update()

    def _point_over_ui(self, pos):
        # Don't let input create hidden zero-length strokes while the user
        # is working the toolbar, toast, or hover tip. hover_tip is itself
        # WA_TransparentForMouseEvents, so without this a click on a visible
        # tip would start a stroke on the canvas underneath it.
        if self.panel.isVisible() and self.panel.geometry().contains(pos.toPoint()):
            return True
        if self.toast_label.isVisible() and self.toast_label.geometry().contains(pos.toPoint()):
            return True
        if self.hover_tip.isVisible() and self.hover_tip.geometry().contains(pos.toPoint()):
            return True
        return False

    # -- input events -----------------------------------------------------

    def _overlay_pos(self, event):
        # position() is relative to whichever widget originally received the
        # event; if a child toolbar/control receives it, coordinates look
        # "stuck" near the toolbar. globalPosition() is desktop-relative, so
        # translate that into this overlay's local coordinate system.
        gp = event.globalPosition()
        geo = self.geometry()
        return QPointF(gp.x() - geo.x(), gp.y() - geo.y())

    def tabletEvent(self, event):
        try:
            self._handle_tablet_event(event)
        except Exception:
            logger.exception("Error handling tablet event")
            event.accept()

    def _handle_tablet_event(self, event):
        event_type = event.type()
        pressure = max(0.01, float(event.pressure()))
        pos = self._overlay_pos(event)
        is_eraser_tip = event.pointerType() == QPointingDevice.PointerType.Eraser

        self._last_tablet_time = time.monotonic()

        if self.pass_through:
            event.ignore()
            return

        # Tablet input on our own controls belongs to the controls, not the
        # drawing engine.
        if self._point_over_ui(pos):
            if event_type == QEvent.Type.TabletRelease:
                self._tablet_contact = False
                if self.current is not None:
                    self.current = None
                    self.lazy_pos = None
                    self.update()
            event.ignore()
            return

        if event_type == QEvent.Type.TabletPress:
            is_alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            if self.mode == "eyedropper" or is_alt:
                self._pick_color_and_restore_mode(event.globalPosition())
                event.accept()
                return
            self._tablet_contact = True
            self._begin_stroke(pos, pressure, eraser=is_eraser_tip)
        elif event_type == QEvent.Type.TabletMove:
            if self._tablet_contact and self.current is not None:
                self._add_point(pos, pressure)
        elif event_type == QEvent.Type.TabletRelease:
            self._tablet_contact = False
            self._end_stroke()

        event.accept()

    def _is_tablet_synthesized(self):
        # Some Wacom/Windows configurations emit a compatibility mouse event
        # immediately after the real tablet event. Ignore it so one pen
        # stroke can't become two history entries.
        return self._tablet_contact or (time.monotonic() - self._last_tablet_time) < C.TABLET_MOUSE_GUARD_S

    def mousePressEvent(self, event):
        try:
            if self._is_tablet_synthesized():
                event.accept()
                return
            pos = self._overlay_pos(event)
            if self.pass_through:
                event.ignore()
                return
            if event.button() == Qt.MouseButton.LeftButton and not self._point_over_ui(pos):
                is_alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                if self.mode == "eyedropper" or is_alt:
                    self._pick_color_and_restore_mode(event.globalPosition())
                    event.accept()
                    return
                self._begin_stroke(pos, 0.65)
                event.accept()
        except Exception:
            logger.exception("Error handling mouse press")
            event.accept()

    def mouseMoveEvent(self, event):
        try:
            if self._is_tablet_synthesized():
                event.accept()
                return
            if self.pass_through:
                event.ignore()
                return
            pos = self._overlay_pos(event)
            if event.buttons() & Qt.MouseButton.LeftButton and not self._point_over_ui(pos):
                self._add_point(pos, 0.65)
            else:
                prev_cursor = self.cursor_pos
                self.cursor_pos = pos
                self._mark_dirty(prev_cursor, pos)
        except Exception:
            logger.exception("Error handling mouse move")
            event.accept()

    def mouseReleaseEvent(self, event):
        try:
            if self._is_tablet_synthesized():
                event.accept()
                return
            if self.pass_through:
                event.ignore()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                self._end_stroke()
                event.accept()
        except Exception:
            logger.exception("Error handling mouse release")
            event.accept()

    # -- painting -----------------------------------------------------------

    def _dirty_margin(self):
        # Generously covers stroke width, the brush-size cursor ring, and
        # the smoothing guide line so a dirty rect never clips live drawing.
        return self.brush_size * 1.6 + 12

    def _mark_dirty(self, *points):
        pts = [p for p in points if p is not None]
        if not pts:
            return
        margin = self._dirty_margin()
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        rect = QRect(
            int(min(xs) - margin),
            int(min(ys) - margin),
            int(max(xs) - min(xs) + margin * 2),
            int(max(ys) - min(ys) + margin * 2),
        )
        self.update(rect)

    def _draw_stroke(self, painter, stroke):
        segments = brush.stroke_segments(stroke)
        if not segments:
            return

        if stroke.eraser:
            # CompositionMode_Clear sets destination alpha to 0, which is
            # idempotent, so overlapping segments never build up. No
            # opaque-layer step needed.
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            brush.paint_segments(painter, segments, QColor(0, 0, 0, 255))
            painter.restore()
            return

        # Flatten the stroke to an opaque offscreen layer, then blend that
        # onto the target once at stroke.opacity. Blending each segment
        # separately would double up wherever the stroke overlaps itself.
        layer = brush.render_stroke_layer(stroke, segments)
        if layer is None:
            return
        pixmap, origin = layer
        painter.save()
        painter.setOpacity(max(0.01, min(1.0, stroke.opacity)))
        painter.drawPixmap(origin, pixmap)
        painter.restore()

    def _paint_backdrop(self, painter):
        # Its own layer, painted under the canvas each paintEvent rather
        # than baked into the canvas pixmap, so the eraser reveals it the
        # way erasing a layer works in any editor, and toggling it is a
        # fill instead of a full canvas rebuild.
        #
        # Every pixel needs nonzero alpha regardless: Windows
        # layered-window hit testing can ignore fully transparent pixels
        # and let clicks fall through the window. With no backdrop this
        # paints a 1/255 alpha fill for that. Suppressed during
        # pass-through.
        if self.pass_through:
            fill = QColor(0, 0, 0, 1)
        elif self.backdrop == "white":
            fill = QColor(255, 255, 255, int(self.backdrop_opacity * 255))
        elif self.backdrop == "black":
            fill = QColor(0, 0, 0, int(self.backdrop_opacity * 255))
        else:
            fill = QColor(0, 0, 0, 1)
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), fill)
        painter.restore()

    def _commit_stroke_to_canvas(self, stroke):
        painter = QPainter(self._canvas)
        try:
            self._draw_stroke(painter, stroke)
        finally:
            painter.end()

    def _rebuild_canvas(self):
        # The persistent canvas keeps repainting cheap: committed strokes
        # are baked in once instead of the whole history being replayed
        # every paintEvent. Undo/redo/clear/resize replace the stroke set
        # and rebuild; a new stroke just paints on top.
        #
        # Holds strokes only. Filled transparent explicitly because
        # QPixmap(size) can come back in the screen's opaque format, which
        # turns the eraser's CompositionMode_Clear into a no-op.
        self._canvas = QPixmap(self.size())
        self._canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self._canvas)
        try:
            for stroke in self.history:
                self._draw_stroke(painter, stroke)
        finally:
            painter.end()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_backdrop(painter)

            live_eraser = (
                self.current is not None and self.current.eraser and self._live_layer is not None
            )
            if live_eraser:
                # The live eraser preview should reveal the backdrop through
                # the drawing layer only. A plain DestinationOut against
                # this shared painter would punch through the backdrop too,
                # since by then both are pixels on the same surface.
                # Composite canvas plus live mask in an isolated buffer
                # scoped to the dirty rect, then draw that over the
                # backdrop.
                dirty = event.rect()
                temp = QPixmap(dirty.size())
                temp.fill(Qt.GlobalColor.transparent)
                tp = QPainter(temp)
                try:
                    tp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    tp.translate(-dirty.topLeft())
                    tp.drawPixmap(0, 0, self._canvas)
                    tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
                    tp.drawPixmap(self._live_layer_origin, self._live_layer)
                finally:
                    tp.end()
                painter.drawPixmap(dirty.topLeft(), temp)
            else:
                painter.drawPixmap(0, 0, self._canvas)
                if self.current and self._live_layer is not None:
                    # Brush preview: SourceOver only adds color on top, so
                    # it can't reach through to the backdrop the way the
                    # eraser does. No buffer needed.
                    painter.save()
                    painter.setOpacity(max(0.01, min(1.0, self.current.opacity)))
                    painter.drawPixmap(self._live_layer_origin, self._live_layer)
                    painter.restore()

            if not self.pass_through and self.cursor_pos:
                painter.save()
                d = brush.width_for(self.brush_size, self._last_effective_pressure)
                if self.mode == "eraser":
                    ring_color = QColor(255, 255, 255, 210)
                else:
                    ring_color = QColor(self.color)
                    ring_color.setAlpha(220)
                painter.setPen(QPen(ring_color, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(self.cursor_pos, d / 2, d / 2)
                if self.lazy_pos and self.smoothing > 0.02:
                    painter.setPen(QPen(QColor(255, 255, 255, 95), 1))
                    painter.drawLine(self.lazy_pos, self.cursor_pos)
                painter.restore()
        finally:
            painter.end()

    # -- screenshot -----------------------------------------------------------

    def _active_screen(self, cursor_pos):
        # The overlay spans every monitor, but a screenshot should be one
        # picture of the monitor the cursor is on, not a composite of all of
        # them.
        return QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()

    def capture_annotated(self):
        try:
            panel_vis = self.panel.isVisible()
            toast_vis = self.toast_label.isVisible()
            cursor_pos = QCursor.pos()
            self.panel.hide()
            self.toast_label.hide()
            saved_cursor = self.cursor_pos
            self.cursor_pos = None
            self.update()
            QApplication.processEvents()
            QTimer.singleShot(100, lambda: self._finish_capture(panel_vis, toast_vis, saved_cursor, cursor_pos))
        except Exception:
            logger.exception("Failed to start screenshot capture")

    def _finish_capture(self, panel_vis, toast_vis, saved_cursor, cursor_pos):
        try:
            screen = self._active_screen(cursor_pos)
            pixmap = screen.grabWindow(0)
            folder = Path.home() / "Pictures" / C.ORG_NAME
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"crit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            if pixmap.save(str(out), "PNG"):
                logger.info("Saved screenshot to %s", out)
                self.status_changed.emit(f"Saved {out.name}")
                self._reveal_in_explorer(out)
            else:
                logger.error("Failed to save screenshot to %s", out)
                self.status_changed.emit("Screenshot failed, see log")
        except Exception:
            logger.exception("Error finishing screenshot capture")
            self.status_changed.emit("Screenshot failed, see log")
        finally:
            self.cursor_pos = saved_cursor
            if panel_vis and not self.pass_through:
                self.panel.show()
            if toast_vis:
                self.toast_label.show()
            self.update()

    def _reveal_in_explorer(self, path):
        try:
            subprocess.Popen(["explorer", f"/select,{path}"])
        except Exception:
            logger.exception("Failed to open Explorer for %s", path)

    # -- lifecycle -----------------------------------------------------------

    def closeEvent(self, event):
        # Flush now rather than through the debounced _save_settings().
        # There's no later tick to save on.
        self._settings_save_timer.stop()
        self._flush_settings()
        self._pass_through_badge.close()
        logger.info("Overlay closing")
        super().closeEvent(event)
