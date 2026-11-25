import logging
import os
import subprocess
import time
from datetime import datetime

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QEvent
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPen,
    QInputDevice,
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
from . import compat
from . import constants as C
from . import icons
from . import native_windows
from . import paths
from . import region as region_mod
from .color_popup import ColorPopup
from .config import AppConfig
from .models import History, Stroke
from .native_windows import set_click_through
from .pass_through_badge import PassThroughBadge
from .region_popup import RegionPopup

logger = logging.getLogger(f"{C.ORG_NAME}.overlay")


# Above this many device pixels the canvas falls back to 1x resolution
# instead of matching the monitor's scale. Only reachable with
# --screens all across scaled monitors; one 4K monitor at 200% is ~8M.
_MAX_CANVAS_DEVICE_PIXELS = 48_000_000

# Consecutive pen contact events reporting exactly zero pressure before a
# device is treated as having no pressure sensor. Some drivers (and pen
# input forwarded over Remote Desktop) report 0 for the whole stroke, which
# would otherwise draw every line at minimum width.
_ZERO_PRESSURE_EVENTS = 6


class Overlay(QWidget):
    status_changed = Signal(str)
    # Emitted when Esc or the toolbar hides the overlay to the tray, so the
    # tray can say once where it went.
    hidden_to_tray = Signal()

    def __init__(self, screen_mode="cursor", input_mode="auto", screenshot_dir=None):
        super().__init__()
        logger.debug("Overlay init start")

        self._requested_screen_mode = screen_mode
        self._target_screen = None
        self._target_lost = False
        self._user_chose_screen = False
        self._screen_count = len(QGuiApplication.screens())
        # Display changes arrive in bursts (a projector plugging in fires
        # screenAdded, several geometryChanged and a primaryScreenChanged).
        # Handle the burst once, after it settles.
        self._screen_change_timer = QTimer(self)
        self._screen_change_timer.setSingleShot(True)
        self._screen_change_timer.timeout.connect(self._apply_screen_change)
        self._alignment_timer = QTimer(self)
        self._alignment_timer.setSingleShot(True)
        self._alignment_timer.timeout.connect(self._check_alignment)
        self._alignment_retries = 0
        self._input_mode = input_mode
        self._screenshot_dir_override = screenshot_dir
        self._last_good_screenshot_dir = None
        self._no_pressure_devices = set()
        self._zero_pressure_run = 0
        self._click_through_native_ok = None

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

        # Drawing region (region.py): None is the full monitor. Per session;
        # the margin settings are saved.
        self.region = None
        self.margin_enabled = saved["margin_enabled"]
        self.margin_size = saved["margin_size"]
        self.margin_color = saved["margin_color"]
        self.margin_opacity = saved["margin_opacity"]
        self._selecting = False
        self._selection_anchor = None
        self._selection_point = None
        self._capturing = False
        self._region_popup = None
        # Set by app.py when there is a tray icon to come back from. Without
        # one, Esc exits: a hidden overlay with no tray would be an
        # invisible process nobody can reach (#14).
        self._tray_available = False

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
        self._select_monitor()
        n, total = self._monitor_position()
        logger.info(
            "Covering monitor %s (%d of %d, start mode %s)",
            self._target_screen.name() if self._target_screen else "?", n, total, self._requested_screen_mode,
        )
        self._update_geometry()
        self._rebuild_canvas()

        app = QGuiApplication.instance()
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        app.primaryScreenChanged.connect(self._on_screens_changed)
        for screen in QGuiApplication.screens():
            self._watch_screen(screen)

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
        self._refresh_monitor_button()
        self.panel.adjustSize()
        self._refresh_history_buttons()

        self.toast_label = QLabel("", self)
        self.toast_label.setStyleSheet(C.TOAST_STYLE)
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)
        self._toast_anchor = (self._anchor_local_rect(), C.PANEL_MARGIN_TOP)

        self._place_chrome()

        # A separate top-level window, so it stays clickable while self is
        # WA_TransparentForMouseEvents and has lost focus. Only shown during
        # pass-through.
        self._pass_through_badge = PassThroughBadge()
        self._pass_through_badge.clicked.connect(self.toggle_pass_through)

        self.status_changed.connect(self._toast)
        self._install_shortcuts()

        hint = "B Brush • E Eraser • F8 Pass-through • F9 Snapshot"
        if len(QGuiApplication.screens()) > 1:
            hint += " • F7 Next monitor"
        self._toast(hint, ms=C.STARTUP_HINT_MS)

        logger.debug("Overlay init complete")

    # -- monitors -------------------------------------------------------
    #
    # The overlay is one window covering exactly one monitor, on every
    # display configuration (see compat.choose_monitor for why spanning
    # monitors can't be made accurate). F7, the toolbar's monitor button or
    # the tray move it to the next monitor. Annotations stay in the window,
    # so they move with it.

    def _ordered_screens(self):
        return sorted(QGuiApplication.screens(), key=lambda s: (s.geometry().x(), s.geometry().y()))

    def _monitor_position(self, screen=None):
        """(1-based position left to right, total), or (0, total)."""
        screen = screen or self._target_screen
        ordered = self._ordered_screens()
        n = next((i + 1 for i, s in enumerate(ordered) if s is screen), 0)
        return n, len(ordered)

    def _select_monitor(self, previous_lost=False):
        target = compat.choose_monitor(
            QGuiApplication.screens(),
            QGuiApplication.primaryScreen(),
            QGuiApplication.screenAt(QCursor.pos()),
            self._requested_screen_mode,
            previous=self._target_screen,
            previous_lost=previous_lost,
            user_chose=self._user_chose_screen,
        )
        if previous_lost:
            self._user_chose_screen = False
        changed = target is not self._target_screen
        self._target_screen = target
        return changed

    def _apply_geometry(self):
        target = self._target_screen or QGuiApplication.primaryScreen()
        if target is None:
            # No monitors at all, e.g. briefly while a docking station
            # reconnects. The next screenAdded puts us back.
            logger.warning("No monitors reported; leaving the overlay where it is")
            return
        # Tell Qt which monitor this window belongs to before moving it, so
        # it picks up that monitor's scale factor instead of the one it was
        # last on.
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(target)
        elif hasattr(self, "setScreen"):
            self.setScreen(target)
        self.setGeometry(target.geometry())
        # The resize above can rebuild the canvas before the new monitor's
        # scale has reached the widget; match it now rather than on the
        # next paint.
        if self._canvas is not None and abs(self._canvas.devicePixelRatio() - self._target_dpr()) > 1e-3:
            self._rebuild_canvas()
        # Keep a drawing region on the monitor after a switch or a
        # resolution change (same logical coordinates where they fit).
        if getattr(self, "region", None) is not None:
            clamped = region_mod.clamp_region(self.region, self.rect())
            if clamped != self.region:
                logger.info("Drawing region adjusted to the new monitor size")
                self.set_region(clamped)

    def _update_geometry(self):
        self._apply_geometry()
        self._alignment_retries = 1
        self._alignment_timer.start(250)

    def showEvent(self, event):
        super().showEvent(event)
        self._alignment_retries = 1
        self._alignment_timer.start(250)

    def _check_alignment(self):
        """Compare where Windows actually put the window with where it
        should be. Moving between monitors of different scale goes through
        a WM_DPICHANGED round trip that can, rarely, leave the window sized
        for the old scale; one re-apply fixes that."""
        if not self.isVisible():
            return
        try:
            ok, actual, expected = self.native_geometry_ok()
        except Exception:
            logger.exception("Couldn't check native overlay geometry")
            return
        if ok is None:
            return
        if ok:
            logger.info("Overlay native rect %s matches the monitor", actual)
            return
        if self._alignment_retries > 0:
            self._alignment_retries -= 1
            logger.info("Overlay native rect %s, expected %s; re-applying geometry", actual, expected)
            # Natively, not via setGeometry: when Qt's cached geometry
            # already equals the target (it hasn't caught up with what
            # Windows did), setGeometry is a no-op. Qt syncs from the
            # resulting window messages.
            if not native_windows.set_window_rect(int(self.winId()), expected):
                self._apply_geometry()
            self._alignment_timer.start(250)
            return
        logger.warning(
            "Overlay native rect %s differs from expected %s. Drawing may be offset from the "
            "screen; press F7 to move monitors, or see TROUBLESHOOTING.md", actual, expected,
        )

    def log_native_geometry(self):
        # Kept for callers outside this class; same as an immediate check.
        self._check_alignment()

    def _watch_screen(self, screen):
        # A display-scale or resolution change in Settings while running
        # changes geometry without adding or removing a screen.
        screen.geometryChanged.connect(self._on_screens_changed)
        screen.logicalDotsPerInchChanged.connect(self._on_screens_changed)

    def _on_screen_added(self, screen):
        logger.info("Monitor connected: %s", screen.name())
        self._watch_screen(screen)
        self._screen_change_timer.start(200)

    def _on_screen_removed(self, screen):
        logger.info("Monitor disconnected: %s", screen.name())
        if screen is self._target_screen:
            # Drop the reference now: Qt deletes this QScreen right after
            # the signal, and touching it later would raise.
            self._target_screen = None
            self._target_lost = True
        self._screen_change_timer.start(200)

    def _on_screens_changed(self, *_args):
        self._screen_change_timer.start(200)

    def _apply_screen_change(self):
        lost, self._target_lost = self._target_lost, False
        before_count, self._screen_count = self._screen_count, len(QGuiApplication.screens())
        self._select_monitor(previous_lost=lost)
        n, total = self._monitor_position()
        logger.info(
            "Display configuration changed: %d monitor(s), covering %s (%d of %d)",
            total, self._target_screen.name() if self._target_screen else "none", n, total,
        )
        self._update_geometry()
        self._refresh_monitor_button()
        self._place_chrome()
        if lost:
            self.status_changed.emit(f"Monitor disconnected. Now on monitor {n} of {total}")
        elif total > before_count:
            self.status_changed.emit(f"Monitor connected. F7 switches monitors ({n} of {total})")

    def move_to_next_screen(self):
        target = compat.next_monitor(self._ordered_screens(), self._target_screen)
        if target is None:
            self.status_changed.emit("Only one monitor connected")
            return
        if self.current is not None:
            # Don't carry a half-drawn stroke across monitors.
            self._end_stroke()
        if self._selecting:
            self._cancel_selection_state()
        self._target_screen = target
        # Stay on the chosen monitor across display changes, even with
        # --screens primary.
        self._user_chose_screen = True
        n, total = self._monitor_position()
        logger.info("Moving overlay to %s (%d of %d)", target.name(), n, total)
        self._update_geometry()
        self._refresh_monitor_button()
        self._place_chrome()
        if self.pass_through:
            self._pass_through_badge.show()
            self._pass_through_badge.raise_()
        else:
            self.raise_()
            self.activateWindow()
        self.status_changed.emit(f"Monitor {n} of {total}")

    def _refresh_monitor_button(self):
        if not hasattr(self, "monitor_btn"):
            return
        n, total = self._monitor_position()
        self.monitor_btn.setVisible(total > 1)
        self.monitor_btn.setToolTip(f"Next monitor (F7). On {n} of {total}")
        self.panel.adjustSize()

    def expected_native_rect(self):
        """Where the window should be in physical pixels. Qt keeps each
        monitor's physical top-left as its logical origin and scales only
        the size, which is what makes this computable from QScreen."""
        s = self._target_screen or QGuiApplication.primaryScreen()
        g, dpr = s.geometry(), s.devicePixelRatio()
        return (g.x(), g.y(), g.x() + round(g.width() * dpr), g.y() + round(g.height() * dpr))

    def native_geometry_ok(self, tolerance=3):
        actual = native_windows.window_rect(int(self.winId()))
        expected = self.expected_native_rect()
        if actual is None:
            return None, actual, expected
        ok = all(abs(a - e) <= tolerance for a, e in zip(actual, expected))
        return ok, actual, expected

    def diagnostic_lines(self):
        n, total = self._monitor_position()
        covers = self._target_screen.name() if self._target_screen else "none"
        lines = [
            f"Overlay covers: {covers} (monitor {n} of {total}; start mode {self._requested_screen_mode}"
            f"{'; moved by user' if self._user_chose_screen else ''}), logical {self.geometry().getRect()}",
            f"Overlay input mode: {self._input_mode}",
        ]
        if self._canvas is not None:
            null_note = " (NULL, allocation failed)" if self._canvas.isNull() else ""
            lines.append(
                f"Canvas: {self._canvas.width()}x{self._canvas.height()} device px "
                f"at {self._canvas.devicePixelRatio():g}x{null_note}"
            )
        if self.isVisible():
            ok, actual, expected = self.native_geometry_ok()
            lines.append(f"Native window rect: {actual}, expected {expected}, ok={ok}")
        if self._no_pressure_devices:
            lines.append(f"Pen devices treated as pressure-less: {sorted(self._no_pressure_devices)}")
        return lines

    def resizeEvent(self, event):
        # Guarded because Qt can fire an initial resizeEvent from within
        # QWidget's own constructor, before __init__ below has created
        # self.history to rebuild the canvas from.
        if hasattr(self, "history"):
            self._rebuild_canvas()
        super().resizeEvent(event)

    def _anchor_screen(self):
        # The monitor the toolbar, toast and pass-through badge dock to.
        return self._target_screen or QGuiApplication.primaryScreen()

    def _anchor_local_rect(self):
        return self._anchor_screen().geometry().translated(-self.geometry().topLeft())

    def _place_chrome(self):
        local = self._anchor_local_rect()
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
        anchor = self._anchor_screen().geometry()
        badge = self._pass_through_badge
        x = anchor.x() + max(16, (anchor.width() - badge.width()) // 2)
        y = anchor.y() + C.PANEL_MARGIN_TOP
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
        if tooltip:
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

        # Checked while a custom region is set; clicking opens the region
        # and margin popup.
        self.region_btn = tool_button("region", "Drawing region and margin (R to select)", checkable=True)
        self.region_btn.clicked.connect(self._show_region_popup)

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

        self.monitor_btn = tool_button("monitor", "Next monitor (F7)")
        self.monitor_btn.clicked.connect(self.move_to_next_screen)
        self.pass_btn = tool_button("pass_through", "Pass-through (F8)", checkable=True)
        self.snap_btn = tool_button("snapshot", "Screenshot (F9)")
        self.exit_btn = tool_button("exit", "Exit (Esc)")
        self.pass_btn.clicked.connect(self.toggle_pass_through)
        self.snap_btn.clicked.connect(self.capture_annotated)
        self.exit_btn.clicked.connect(self.dismiss)

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
        add("F7", self.move_to_next_screen)
        add("F8", self.toggle_pass_through)
        add("F9", self.capture_annotated)
        add("[", lambda: self.change_size(-1))
        add("]", lambda: self.change_size(1))
        add("R", self.begin_region_selection)
        add("Esc", self._escape)

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

    def save_settings_now(self):
        """Write pending settings immediately (hide, quit)."""
        self._settings_save_timer.stop()
        self._flush_settings()

    def _flush_settings(self):
        self._config.save(
            color=self.color,
            size=self.brush_size,
            opacity=self.opacity,
            smoothing=self.smoothing,
            backdrop=self.backdrop,
            backdrop_opacity=self.backdrop_opacity,
            margin_enabled=self.margin_enabled,
            margin_size=self.margin_size,
            margin_color=self.margin_color,
            margin_opacity=self.margin_opacity,
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

    # -- drawing region and margin -----------------------------------------
    #
    # Everything is in this window's logical coordinates, which are the
    # covered monitor's own (the window is exactly one monitor), so painting,
    # input and screen grabs all map through that one monitor's scale.

    def drawable_rect(self):
        """Where strokes can start and are shown: the region plus its
        margin, or the whole window with no region."""
        margin = self.margin_size if self.margin_enabled else 0
        return region_mod.outer_rect(self.region, margin, self.rect())

    def set_region(self, rect):
        if self.current is not None:
            self._end_stroke()
        rect = region_mod.clamp_region(rect, self.rect()) if rect is not None else None
        changed = rect != self.region
        self.region = rect
        self.region_btn.setChecked(rect is not None)
        if rect is None:
            self.region_btn.setToolTip("Drawing region: full monitor (R to select an area)")
        else:
            self.region_btn.setToolTip(f"Drawing region: {rect.width()} x {rect.height()} (R to select again)")
        if changed:
            logger.info("Drawing region %s", "full monitor" if rect is None else
                        f"{rect.x()},{rect.y()} {rect.width()}x{rect.height()} on {self._anchor_screen().name()}")
        self._sync_region_popup()
        self.update()

    def begin_region_selection(self):
        if self.pass_through:
            return
        if self.current is not None:
            self._end_stroke()
        self._selecting = True
        self._selection_anchor = None
        self._selection_point = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.status_changed.emit("Drag to select the drawing area. Click for full monitor, Esc to cancel")

    def _finish_region_selection(self, end_pos):
        anchor = self._selection_anchor
        self._cancel_selection_state()
        if anchor is None:
            return
        rect = region_mod.selection_rect(anchor, end_pos, self.rect())
        if region_mod.is_usable(rect):
            self.set_region(rect)
            self.status_changed.emit(f"Drawing area {rect.width()} x {rect.height()}")
        else:
            self.set_region(None)
            self.status_changed.emit("Drawing area: full monitor")

    def _cancel_selection_state(self):
        self._selecting = False
        self._selection_anchor = None
        self._selection_point = None
        self.unsetCursor()
        self.update()

    def _escape(self):
        # Esc cancels an area selection first; otherwise it hides the overlay.
        if self._selecting:
            self._cancel_selection_state()
            self.status_changed.emit("Selection cancelled")
        else:
            self.dismiss()

    def _selection_input(self, kind, pos):
        """Mouse or pen input while selecting an area. kind: press, move,
        release."""
        pos = QPointF(max(0.0, min(pos.x(), self.width())), max(0.0, min(pos.y(), self.height())))
        before = self._selection_dirty_rect()
        if kind == "press":
            self._selection_anchor = pos
            self._selection_point = pos
        elif kind == "move" and self._selection_anchor is not None:
            self._selection_point = pos
        elif kind == "release" and self._selection_anchor is not None:
            self._finish_region_selection(pos)
            return
        # Repaint only around the old and new outline, not the whole monitor.
        after = self._selection_dirty_rect()
        dirty = before.united(after) if before is not None and after is not None else (before or after)
        if dirty is not None:
            self.update(dirty)

    def _selection_dirty_rect(self):
        if self._selection_anchor is None:
            return None
        rect = region_mod.selection_rect(self._selection_anchor, self._selection_point, self.rect())
        return rect.adjusted(-3, -3, 3, 3)

    def set_margin_enabled(self, enabled):
        before = self.drawable_rect()
        self.margin_enabled = bool(enabled)
        self._region_settings_changed(before)

    # Adjusting thickness, color or opacity turns the margin on: those
    # controls are only ever touched to see a margin, and leaving it off
    # made them look broken (#13).

    def set_margin_size(self, value):
        before = self.drawable_rect()
        self.margin_size = max(C.MARGIN_MIN_SIZE, min(C.MARGIN_MAX_SIZE, int(round(value))))
        self.margin_enabled = True
        self._region_settings_changed(before)

    def set_margin_color(self, name):
        before = self.drawable_rect()
        self.margin_color = name if name in region_mod.MARGIN_COLORS else C.DEFAULT_MARGIN_COLOR
        self.margin_enabled = True
        self._region_settings_changed(before)

    def set_margin_opacity(self, value):
        before = self.drawable_rect()
        self.margin_opacity = max(0.1, min(1.0, float(value)))
        self.margin_enabled = True
        self._region_settings_changed(before)

    def _region_settings_changed(self, before):
        self._save_settings()
        self._sync_region_popup()
        # Repaint the old and new margin area now. A restarting debounce
        # timer here meant no redraw at all while a slider was being
        # dragged (#13); Qt already merges update() calls into one paint
        # per event-loop pass. Fills and the clip only; the canvas is
        # untouched.
        if self.region is not None:
            self.update(before.united(self.drawable_rect()).adjusted(-2, -2, 2, 2))

    def _sync_region_popup(self):
        if self._region_popup is not None and self._region_popup.isVisible():
            self._region_popup.sync()

    def _show_region_popup(self):
        self.region_btn.setChecked(self.region is not None)
        if self._region_popup is None:
            self._region_popup = RegionPopup(self)
        self._region_popup.show_below(self.region_btn)

    # -- window / mode actions ---------------------------------------------

    def set_tray_available(self, available, hotkey_text=None):
        """Called by app.py once the tray icon exists. Esc and the toolbar's
        last button then hide to the tray instead of exiting."""
        self._tray_available = bool(available)
        if self._tray_available:
            shows = f"{hotkey_text} or the tray icon" if hotkey_text else "the tray icon"
            self.exit_btn.setIcon(icons.build_icon("hide"))
            self.exit_btn.setToolTip(f"Hide (Esc). {shows} shows it again")
        else:
            self.exit_btn.setIcon(icons.build_icon("exit"))
            self.exit_btn.setToolTip("Exit (Esc)")

    def dismiss(self):
        """Esc / toolbar: hide to the tray, or exit when there's no tray."""
        if self._tray_available:
            self.hide_to_tray()
            self.hidden_to_tray.emit()
        else:
            logger.info("Exit requested from the overlay (no tray icon)")
            QApplication.quit()

    def hide_to_tray(self):
        """Hide everything this window shows, keeping all state: strokes,
        undo history, region, margin, backdrop and pass-through all come
        back as they were. Nothing repaints or polls while hidden."""
        if not self.isVisible():
            return
        if self._selecting:
            self._cancel_selection_state()
        if self.current is not None:
            self._end_stroke()
        for popup in (self._region_popup, self._color_popup):
            if popup is not None and popup.isVisible():
                popup.close()
        self._pass_through_badge.hide()
        self._hover_tip_timer.stop()
        self.hover_tip.hide()
        self._toast_timer.stop()
        self.toast_label.hide()
        self._alignment_timer.stop()
        self.save_settings_now()
        self.hide()
        # After the hide has been processed, so the window's buffers are
        # released first.
        QTimer.singleShot(0, native_windows.trim_working_set)
        logger.info("Overlay hidden")

    def show_overlay(self):
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        self.show()
        if self.pass_through:
            self._position_pass_through_badge()
            self._pass_through_badge.show()
            self._pass_through_badge.raise_()
        else:
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        logger.info("Overlay shown")

    def toggle_visibility(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_overlay()

    def toggle_pass_through(self):
        if not self.isVisible():
            # From the tray while hidden: come back first, so the badge
            # isn't left floating over nothing.
            self.show_overlay()
        if self._selecting:
            self._cancel_selection_state()
        self.pass_through = not self.pass_through

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.pass_through)
        # The Qt attribute above doesn't reach the native WS_EX_TRANSPARENT
        # style for this window's flags, so clicks keep landing here. Set
        # the native style directly. See native_windows.py.
        ok = set_click_through(int(self.winId()), self.pass_through)
        if not ok and self._click_through_native_ok is not False:
            logger.warning("Couldn't set native click-through; relying on Qt's attribute only")
        self._click_through_native_ok = ok
        self.panel.setVisible(not self.pass_through)
        self.pass_btn.setChecked(self.pass_through)
        focus = QApplication.focusWindow()
        # One line per transition, so a report of "toolbar stopped
        # responding" (#3) comes with the click-through state in the log.
        logger.info(
            "Pass-through %s: Qt transparent=%s, native WS_EX_TRANSPARENT=%s, focus=%s",
            "on" if self.pass_through else "off",
            self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents),
            bool(native_windows.get_ex_style(int(self.winId())) & native_windows.WS_EX_TRANSPARENT),
            focus.objectName() or type(focus).__name__ if focus is not None else None,
        )

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
        if self.region is not None and not self.drawable_rect().contains(QPointF(pos).toPoint()):
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
        dpr = self._canvas_dpr()
        layer = brush.render_stroke_layer(stroke, segments, dpr)
        if layer is None:
            return
        new_pixmap, new_origin = layer
        new_rect = brush.logical_rect(new_origin, new_pixmap)

        if self._live_layer is None:
            self._live_layer, self._live_layer_origin = new_pixmap, new_origin
        else:
            old_rect = brush.logical_rect(self._live_layer_origin, self._live_layer)
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
                merged = brush.new_layer(union.size(), dpr)
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
        if self._input_mode == "mouse":
            # Mouse-only fallback for misbehaving tablet drivers. Ignoring
            # the tablet event makes Qt deliver it again as an ordinary
            # synthesized mouse event, which the mouse handlers draw with
            # (no pressure, but speed still varies the width).
            event.ignore()
            return
        try:
            self._handle_tablet_event(event)
        except Exception:
            logger.exception("Error handling tablet event")
            event.accept()

    def _device_key(self, event):
        try:
            dev = event.pointingDevice()
            return f"{dev.name()}#{dev.uniqueId().numericId()}" if dev is not None else "unknown"
        except Exception:
            return "unknown"

    def _tablet_pressure(self, event):
        """Pen pressure in 0..1, or the mouse default for devices without a
        working pressure sensor."""
        raw = float(event.pressure())
        key = self._device_key(event)
        if key in self._no_pressure_devices:
            return C.DEFAULT_PRESSURE
        dev = event.pointingDevice()
        if dev is not None and not (dev.capabilities() & QInputDevice.Capability.Pressure):
            self._mark_no_pressure(key, "driver reports no pressure capability")
            return C.DEFAULT_PRESSURE
        if event.type() == QEvent.Type.TabletPress:
            self._zero_pressure_run = 0
        if self._tablet_contact or event.type() == QEvent.Type.TabletPress:
            if raw <= 0.0:
                self._zero_pressure_run += 1
                if self._zero_pressure_run >= _ZERO_PRESSURE_EVENTS:
                    self._mark_no_pressure(key, "pressure stayed at 0 while drawing")
                    return C.DEFAULT_PRESSURE
            else:
                self._zero_pressure_run = 0
        return max(0.01, raw)

    def _mark_no_pressure(self, key, why):
        if key not in self._no_pressure_devices:
            self._no_pressure_devices.add(key)
            logger.warning("Pen %s: %s; drawing with default pressure. If this tablet normally has "
                           "pressure, enable Windows Ink for this app in the tablet driver settings.", key, why)

    def _handle_tablet_event(self, event):
        event_type = event.type()
        pressure = self._tablet_pressure(event)
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

        if self._selecting:
            kind = {QEvent.Type.TabletPress: "press", QEvent.Type.TabletMove: "move",
                    QEvent.Type.TabletRelease: "release"}.get(event_type)
            if kind == "press":
                self._tablet_contact = True
            elif kind == "release":
                self._tablet_contact = False
            if kind is not None and (kind != "move" or self._tablet_contact):
                self._selection_input(kind, pos)
            event.accept()
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
                if self._selecting:
                    self._selection_input("press", pos)
                    event.accept()
                    return
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
            if self._selecting:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self._selection_input("move", pos)
            elif event.buttons() & Qt.MouseButton.LeftButton and not self._point_over_ui(pos):
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
                if self._selecting:
                    self._selection_input("release", self._overlay_pos(event))
                else:
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
        layer = brush.render_stroke_layer(stroke, segments, self._canvas_dpr())
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
        #
        # With a drawing region, the backdrop covers only the region and the
        # margin band gets its own color and opacity; both stay layers under
        # the canvas, so the eraser works the same on them.
        floor = QColor(0, 0, 0, 1)
        if self.pass_through or self.backdrop is None:
            fill = None
        elif self.backdrop == "white":
            fill = QColor(255, 255, 255, int(self.backdrop_opacity * 255))
        else:
            fill = QColor(0, 0, 0, int(self.backdrop_opacity * 255))
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if self.region is None:
            painter.fillRect(self.rect(), fill or floor)
        else:
            painter.fillRect(self.rect(), floor)
            if fill is not None:
                painter.fillRect(self.region, fill)
            if self.margin_enabled and not self.pass_through:
                band = region_mod.margin_color(self.margin_color, self.margin_opacity)
                for rect in region_mod.margin_bands(self.region, self.drawable_rect()):
                    painter.fillRect(rect, band)
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
        # Holds strokes only, at the monitor's pixel density (see
        # _target_dpr), transparent so the eraser's CompositionMode_Clear
        # works.
        dpr = self._target_dpr()
        canvas = brush.new_layer(self.size(), dpr)
        if canvas.isNull() and dpr > 1.0:
            logger.warning("Couldn't allocate a %gx canvas for %s; falling back to 1x", dpr, self.size())
            canvas = brush.new_layer(self.size(), 1.0)
        if canvas.isNull():
            logger.error("Couldn't allocate the drawing canvas (%s). Try --screens primary.", self.size())
        self._canvas = canvas
        painter = QPainter(self._canvas)
        try:
            for stroke in self.history:
                self._draw_stroke(painter, stroke)
        finally:
            painter.end()

    def _target_dpr(self):
        """Pixel density for the canvas: the monitor's scale factor, unless
        that would make the canvas unreasonably large."""
        dpr = max(1.0, float(self.devicePixelRatioF()))
        size = self.size()
        if size.width() * size.height() * dpr * dpr > _MAX_CANVAS_DEVICE_PIXELS:
            return 1.0
        return dpr

    def _canvas_dpr(self):
        return self._canvas.devicePixelRatio() if self._canvas is not None else self._target_dpr()

    def paintEvent(self, event):
        if self._canvas is not None and abs(self._canvas.devicePixelRatio() - self._target_dpr()) > 1e-3:
            # Moved to a monitor with a different scale.
            self._rebuild_canvas()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_backdrop(painter)
            if self.region is not None:
                painter.save()
                painter.setClipRect(self.drawable_rect())

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
                temp = brush.new_layer(dirty.size(), self._canvas_dpr())
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

            if self.region is not None:
                painter.restore()
                if not self._capturing and not self.pass_through:
                    self._paint_region_frame(painter, self.region)
            if self._selecting and self._selection_anchor is not None:
                rect = region_mod.selection_rect(self._selection_anchor, self._selection_point, self.rect())
                self._paint_region_frame(painter, rect, selecting=True)

            # Not while capturing: a real mouse move during the capture delay
            # sets cursor_pos again and would put the ring in the screenshot.
            if not self.pass_through and self.cursor_pos and not self._selecting and not self._capturing:
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

    def _paint_region_frame(self, painter, rect, selecting=False):
        # Dashed light-on-dark outline, visible over any content. Not part of
        # screenshots (hidden while capturing).
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        outline = QRectF(rect).adjusted(-0.5, -0.5, 0.5, 0.5)
        painter.setPen(QPen(QColor(0, 0, 0, 170), 1))
        painter.drawRect(outline)
        dash = QPen(QColor(255, 255, 255, 230), 1, Qt.PenStyle.DashLine)
        painter.setPen(dash)
        painter.drawRect(outline)
        if selecting:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 28))
            painter.drawRect(QRectF(rect))
        painter.restore()

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
            self._capturing = True
            self.update()
            QApplication.processEvents()
            QTimer.singleShot(100, lambda: self._finish_capture(panel_vis, toast_vis, saved_cursor, cursor_pos))
        except Exception:
            logger.exception("Failed to start screenshot capture")

    def capture_region_pixmap(self):
        """Screenshot of the drawing region plus margin, from the covered
        monitor. Region coordinates are this window's, which are that
        monitor's logical coordinates."""
        screen = self._target_screen or QGuiApplication.primaryScreen()
        r = self.region.translated(self.geometry().topLeft() - screen.geometry().topLeft())
        grab = screen.grabWindow(0, r.x(), r.y(), r.width(), r.height())
        if grab.isNull():
            return grab
        margin = region_mod.margin_color(self.margin_color) if self.margin_enabled else None
        return region_mod.compose_capture(grab, self._canvas, self.region, self.drawable_rect(), margin)

    def _finish_capture(self, panel_vis, toast_vis, saved_cursor, cursor_pos):
        try:
            if self.region is not None:
                screen = self._target_screen or QGuiApplication.primaryScreen()
                pixmap = self.capture_region_pixmap()
            else:
                screen = self._active_screen(cursor_pos)
                pixmap = screen.grabWindow(0)
            if pixmap.isNull():
                logger.error("Screen grab returned nothing for %s", screen.name())
                self.status_changed.emit("Screenshot failed, see log")
                return
            out = self._save_screenshot(pixmap, f"crit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
            if out is not None:
                logger.info("Saved screenshot to %s", out)
                preferred = paths.current().screenshot_dir_candidates(self._screenshot_dir_override)[0]
                if out.parent == preferred:
                    self.status_changed.emit(f"Saved {out.name}")
                else:
                    self.status_changed.emit(f"Saved to {out.parent}")
                self._reveal_in_explorer(out)
            else:
                self.status_changed.emit("Screenshot failed, see log")
        except Exception:
            logger.exception("Error finishing screenshot capture")
            self.status_changed.emit("Screenshot failed, see log")
        finally:
            self._capturing = False
            self.cursor_pos = saved_cursor
            if panel_vis and not self.pass_through:
                self.panel.show()
            if toast_vis:
                self.toast_label.show()
            self.update()

    def _save_screenshot(self, pixmap, name):
        """Try each candidate folder in turn (Pictures first). On managed
        machines Pictures may be redirected to an unavailable share or
        blocked by Controlled Folder Access; a write that fails there falls
        through to %LOCALAPPDATA% and then %TEMP%. The folder that worked is
        tried first next time, so a blocked one doesn't raise a Defender
        notification on every screenshot."""
        candidates = paths.current().screenshot_dir_candidates(self._screenshot_dir_override)
        if self._last_good_screenshot_dir in candidates:
            candidates.remove(self._last_good_screenshot_dir)
            candidates.insert(0, self._last_good_screenshot_dir)
        for folder in candidates:
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                logger.warning("Can't use screenshot folder %s: %s", folder, exc)
                continue
            out = folder / name
            if pixmap.save(str(out), "PNG"):
                self._last_good_screenshot_dir = folder
                return out
            logger.warning(
                "Couldn't write %s (folder redirected/offline, read-only, or blocked by "
                "Controlled Folder Access); trying the next location", out,
            )
        logger.error("No screenshot location was writable. Tried: %s", [str(c) for c in candidates])
        return None

    def _reveal_in_explorer(self, path):
        explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
        try:
            subprocess.Popen([explorer, f"/select,{path}"])
        except Exception:
            logger.warning("Couldn't open Explorer for %s (blocked by policy?)", path, exc_info=True)

    # -- lifecycle -----------------------------------------------------------

    def closeEvent(self, event):
        # Alt+F4 (a spontaneous close from Windows) hides like Esc when
        # there's a tray. Closes Qt sends while quitting are not
        # spontaneous; ignoring those would cancel the quit.
        if self._tray_available and event.spontaneous():
            event.ignore()
            self.hide_to_tray()
            return
        # Flush now rather than through the debounced _save_settings().
        # There's no later tick to save on.
        self._settings_save_timer.stop()
        self._flush_settings()
        self._pass_through_badge.close()
        if self._region_popup is not None:
            self._region_popup.close()
        logger.info("Overlay closing")
        super().closeEvent(event)
