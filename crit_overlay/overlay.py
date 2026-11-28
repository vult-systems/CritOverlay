import logging
import os
import subprocess
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QEvent
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QIcon,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QInputDevice,
    QPointingDevice,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import brush
from . import brushes
from . import mypaint_engine
from . import capture as capture_mod
from . import compat
from . import constants as C
from . import icons
from . import native_windows
from . import paths
from . import region as region_mod
from . import selection as sel_mod
from . import text_fonts
from .brush_popup import BrushPopup
from .color_popup import ColorPopup
from .config import AppConfig
from .models import SHAPES, History, Stroke, TextStyle
from .native_windows import set_click_through
from .pass_through_badge import PassThroughBadge
from .region_popup import RegionPopup
from .shape_popup import SHAPE_LABELS, ShapePopup
from .text_edit import TextEditor
from .ui_widgets import (MenuRow, ToolbarGrip, close_panels, make_panel, percent_field, place_popup, px_field,
                         size_field)
from .color_popup import swatch_icon
from . import brush_preview

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
    autostart_toggled = Signal(bool)
    # Emitted when Esc or the toolbar hides the overlay to the tray, so the
    # tray can say once where it went.
    hidden_to_tray = Signal()

    def __init__(self, screen_mode="cursor", input_mode="auto", screenshot_dir=None, select_on_show=False):
        super().__init__()
        logger.debug("Overlay init start")
        # Start every show in area selection (the centered capture
        # workflow). Off for self-test and unit tests, which draw directly.
        self.select_on_show = select_on_show

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
        # Live preview of the stroke being drawn: libmypaint's surface so far
        # (_feed_mypaint), composited by paintEvent at the stroke's opacity.
        self._live_layer = None
        self._live_layer_origin = None

        self.mode = "brush"
        self._pre_eyedropper_mode = "brush"
        # The shape the toolbar's shape button selects (see _set_mode).
        self._last_shape = "rect"
        self._shape_popup = None
        # Unconstrained drag end of the shape being drawn, so pressing or
        # releasing Shift mid-drag can re-apply the constraint.
        self._shape_raw_end = None
        # Text tool. _text_edit is the text being typed (a Stroke with shape
        # "text"), drawn live and committed to history like any stroke.
        self.text_family = saved["text_family"]
        self.text_size = saved["text_size"]
        self.text_bold = saved["text_bold"]
        self.text_align = saved["text_align"]
        self._text_edit = None
        self._editor = None          # TextEditor for _text_edit
        self._text_original = None   # committed text being re-edited
        self._text_dragging = False  # dragging a text selection
        # Select tool. selection is a committed shape or text annotation.
        # While it is dragged, _floating is its moved/resized preview and
        # the canvas is rebuilt without _hidden_stroke (the original).
        self.selection = None
        self._floating = None
        self._hidden_stroke = None
        self._drag = None
        self._last_select_press = (0.0, None)
        self._annotation_clipboard = None
        self._paste_count = 0
        self.color = QColor(saved["color"])
        # Named brush_size (not `size`) so it never shadows QWidget.size().
        self.brush_size = saved["size"]
        self.opacity = saved["opacity"]
        self.smoothing = saved["smoothing"]
        # Brushes (brushes.py; libmypaint draws every freehand stroke): the
        # preset, per-preset pressure controls, a separate eraser size.
        self.eraser_size = saved["eraser_size"]
        self.brush_preset = saved["brush_preset"]
        self.brush_pressure = dict(saved["brush_pressure"])  # key -> (size, opacity)
        self._brush_popup = None
        self._input_has_pressure = False
        self._size_preview_until = 0.0
        self.pressure_curve = saved["pressure_curve"]
        self.eraser_opacity = saved["eraser_opacity"]
        self.recent_colors = list(saved["recent_colors"])
        self.favorite_colors = list(saved["favorite_colors"])
        self.eraser_style = saved["eraser_style"]
        self._eraser_popup = None
        self._tilt = (0.0, 0.0)      # last pen tilt, libmypaint units
        self._mp = None              # mypaint_engine.StrokePainter while drawing
        self._mp_last_time = 0.0
        self._mp_has_pressure = False
        self._stroke_pressure = 1.0  # pressure at the latest point of the stroke being drawn
        # Input diagnostic (Ctrl+Shift+I): what the last input event really
        # carried, to check a pen end to end.
        self.show_input_diag = False
        self._diag = {}
        self._seen_devices = set()
        # Recent input events, for logging the sequence of a stroke that
        # came without pressure next to one that had it.
        self._input_log = deque(maxlen=96)
        self._pen_seen = False
        self._strokes_to_log = 5      # summaries for the first strokes after each show
        self._stroke_log_start = 0
        self._stroke_foreground = None
        self._pen_gestures_logged = False
        # The brush cursor is a transient overlay: where it was last painted
        # (window rect), so every move clears exactly that and nothing else
        # lingers. Style: outline (the brush's size), crosshair, hidden.
        self.brush_cursor = saved["brush_cursor"]
        self._cursor_drawn = None
        # The capture and history set aside while selecting a new area, so
        # Esc (or a failed grab) can bring them back; dropped when the new
        # capture or full-monitor drawing actually starts.
        self._stashed_session = None
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
        # Centered capture (capture.py): a still image of a selected area,
        # centered as a drawing workspace. While set, strokes are in its
        # document coordinates and self.region is None.
        self.capture = None
        self._canvas_origin = QPointF(0, 0)
        self._grabbing = False
        self._space_held = False
        self._panning = None  # (press pos, offset at press) during Space+drag
        # Set when F9 hides the overlay after saving: the next show resumes
        # the session instead of starting a new selection.
        self._resume_on_show = False
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
        text_fonts.load()
        self._ui_hidden = False
        self._adjust = None          # Alt+right-drag size/opacity, {"anchor", "size", "opacity"}
        self._peek = False           # H held: annotations hidden (before/after)
        self._alt_pick = False       # Alt held: Alt+click picks a color
        self._mid_pan = False        # middle-button pan in progress
        self._toolbar_positions = dict(saved.get("toolbar_positions", {}))
        self.panel = self._build_panel()
        self.prefs_popup = self._build_prefs()
        self.more_menu = self._build_more_menu()
        self._fix_panel_width()
        self._sync_text_bar()
        self.panel.installEventFilter(self)
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
        # A capture is recentered and refitted on the new monitor.
        self._fit_capture()

    def _update_geometry(self):
        self._apply_geometry()
        self._alignment_retries = 1
        self._alignment_timer.start(250)

    def showEvent(self, event):
        super().showEvent(event)
        if QGuiApplication.platformName() == "windows":
            if native_windows.disable_pen_gestures(int(self.winId())) and not self._pen_gestures_logged:
                self._pen_gestures_logged = True
                logger.info("Pen press-and-hold, flicks and pen feedback off for the overlay")
        self._alignment_retries = 1
        self._alignment_timer.start(250)
        if self._resume_on_show:
            # Back from a screenshot: the session as it was, no new selection.
            self._resume_on_show = False
        elif self.select_on_show and self.capture is None and not self._selecting and not self.pass_through:
            QTimer.singleShot(0, self.begin_region_selection)

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
        self._commit_text()
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
            self.claim_foreground("monitor switch")
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
            self._fit_capture()
        super().resizeEvent(event)

    def _anchor_screen(self):
        # The monitor the toolbar, toast and pass-through badge dock to.
        return self._target_screen or QGuiApplication.primaryScreen()

    def _anchor_local_rect(self):
        return self._anchor_screen().geometry().translated(-self.geometry().topLeft())

    def _monitor_key(self):
        s = self._anchor_screen()
        g = s.geometry()
        return f"{s.name()}@{g.x()},{g.y()},{g.width()}x{g.height()}"

    def _clamp_panel(self, x, y, local):
        x = max(local.x() + 4, min(x, local.x() + local.width() - self.panel.width() - 4))
        y = max(local.y() + 4, min(y, local.y() + local.height() - self.panel.height() - 4))
        return x, y

    def _place_chrome(self):
        local = self._anchor_local_rect()
        saved = self._toolbar_positions.get(self._monitor_key())
        if saved is not None:
            panel_x, panel_y = self._clamp_panel(local.x() + saved[0], local.y() + saved[1], local)
        else:
            panel_x = local.x() + max(16, (local.width() - self.panel.width()) // 2)
            panel_y = local.y() + C.PANEL_MARGIN_TOP
        self.panel.move(panel_x, panel_y)
        below = panel_y + self.panel.height()
        self._toast_anchor = (local, below + C.TOAST_GAP)
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
        """Under the toolbar, unless that would cover the captured artwork;
        then above the toolbar or at the bottom of the monitor, whichever
        stays clear of it."""
        local, y_below = self._toast_anchor
        t = self.toast_label
        x = local.x() + max(16, (local.width() - t.width()) // 2)
        panel = self.panel.geometry()
        candidates = [
            QPoint(panel.x() + (panel.width() - t.width()) // 2, y_below),
            QPoint(panel.x() + (panel.width() - t.width()) // 2, panel.y() - C.TOAST_GAP - t.height()),
            QPoint(x, local.y() + local.height() - t.height() - 24),
        ]
        art = self._artwork_view_rect()
        chosen = candidates[0]
        if art is not None:
            def overlap(c):
                r = QRect(c, t.size())
                if not local.contains(r):
                    return 1 << 30
                i = r.intersected(art)
                return 0 if i.isEmpty() else i.width() * i.height()
            chosen = min(candidates, key=overlap)  # first wins ties
        chosen.setX(max(local.x() + 8, min(chosen.x(), local.x() + local.width() - t.width() - 8)))
        t.move(chosen)

    def _artwork_view_rect(self):
        """The captured image on screen (the margin around it is fine to
        cover)."""
        if getattr(self, "capture", None) is None:
            return None
        return self.capture.transform().mapRect(QRectF(self.capture.image_rect())).toAlignedRect()

    def _grip_moved(self, global_pos, grab_offset):
        local = self._anchor_local_rect()
        p = self.mapFromGlobal(global_pos) - grab_offset
        x, y = self._clamp_panel(p.x(), p.y(), local)
        self.panel.move(x, y)
        self._toast_anchor = (local, y + self.panel.height() + C.TOAST_GAP)
        if self.toast_label.isVisible():
            self._position_toast()

    def _grip_released(self):
        local = self._anchor_local_rect()
        self._toolbar_positions[self._monitor_key()] = (self.panel.x() - local.x(), self.panel.y() - local.y())
        self._save_settings()

    def _grip_reset(self):
        self._toolbar_positions.pop(self._monitor_key(), None)
        self._place_chrome()
        self._save_settings()

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
        if obj is getattr(self, "panel", None) and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self._sync_text_bar()
            return False
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

    # -- chrome: one floating toolbar ------------------------------------------
    #
    # One component: tools on top, the current tool's (or selection's)
    # properties underneath, at a fixed width so switching tools never moves
    # or resizes anything. Values are scrub fields (drag, scroll, or click
    # for a slider and entry). Canvas, export and app commands are in the
    # More menu; persistent preferences in a small Preferences popup. Tab
    # hides the toolbar.

    def _tool_button(self, parent, icon_name, tooltip, checkable=False, layout=None):
        btn = QToolButton(parent)
        btn.setIcon(icons.build_icon(icon_name))
        btn.setIconSize(QSize(C.ICON_SIZE, C.ICON_SIZE))
        btn.setCheckable(checkable)
        btn.setAutoRaise(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hoverable(btn, tooltip)
        if layout is not None:
            layout.addWidget(btn)
        return btn

    @staticmethod
    def _separator(parent, layout):
        line = QFrame(parent)
        line.setObjectName("Sep")
        line.setFixedWidth(1)
        line.setFixedHeight(20)
        layout.addWidget(line)

    def _build_panel(self):
        panel = QFrame(self)
        panel.setObjectName("Toolbar")
        panel.setStyleSheet(C.TOOLBAR_STYLE + C.FIELD_STYLE)
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(1)
        v.addLayout(top)

        def tool(icon_name, tooltip, checkable=False):
            return self._tool_button(panel, icon_name, tooltip, checkable, top)

        self.grip = ToolbarGrip(panel, self._grip_moved, self._grip_released, self._grip_reset)
        self._hoverable(self.grip, "Drag to move the toolbar (remembered per monitor). Double-click to reset")
        top.addWidget(self.grip)
        self.select_btn = tool("select", "Select and move (V)", True)
        self.brush_btn = tool("brush", "Brush (B). Click again to choose a brush", True)
        self.eraser_btn = tool("eraser", "Eraser (E). Click again for hard or soft", True)
        self.shape_btn = tool(self._last_shape, "Shapes (U cycles). Click again to choose", True)
        self.text_btn = tool("text", "Text (T). Ctrl+Enter commits, Esc cancels", True)
        self.eyedropper_btn = tool("eyedropper", "Eyedropper (I, or Alt+click while drawing)", True)
        self.select_btn.clicked.connect(lambda: self._set_mode("select"))
        self.brush_btn.clicked.connect(self._on_brush_button)
        self.eraser_btn.clicked.connect(self._on_eraser_button)
        self.shape_btn.clicked.connect(self._on_shape_button)
        self.text_btn.clicked.connect(lambda: self._set_mode("text"))
        self.eyedropper_btn.clicked.connect(self._toggle_eyedropper)
        for btn, mode in ((self.select_btn, "select"), (self.brush_btn, "brush"), (self.eraser_btn, "eraser"),
                          (self.text_btn, "text"), (self.eyedropper_btn, "eyedropper")):
            btn.setChecked(self.mode == mode)
        self.shape_btn.setChecked(self.mode in SHAPES)

        self._separator(panel, top)
        self.undo_btn = tool("undo", "Undo (Ctrl+Z)")
        self.redo_btn = tool("redo", "Redo (Ctrl+Shift+Z)")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)
        self._separator(panel, top)
        self.clear_btn = tool("clear", "Clear all annotations (undo brings them back)")
        self.clear_btn.clicked.connect(self.clear)
        top.addStretch(1)
        self.capture_btn = tool("region", "Capture an area to annotate (R)")
        self.snap_btn = tool("snapshot", "Screenshot with annotations (F9)")
        self.capture_btn.clicked.connect(self.begin_region_selection)
        self.snap_btn.clicked.connect(self.capture_annotated)
        self.more_btn = tool("more", "More: canvas, export, app")
        self.more_btn.clicked.connect(self._show_more_menu)

        rule = QFrame(panel)
        rule.setObjectName("Rule")
        rule.setFixedHeight(1)
        v.addWidget(rule)
        self.props_bar = self._build_props_bar(panel)
        v.addWidget(self.props_bar)
        return panel

    # -- properties row ----------------------------------------------------------

    _PROPS = {
        "brush": ("preset", "size", "opacity", "color"),
        "eraser": ("eraser_style", "size", "opacity"),
        "shape": ("shape_type", "size", "opacity", "color"),
        "text": ("text", "text_size", "opacity", "color"),
        "select_text": ("text", "text_size", "opacity", "color"),
        "select_shape": ("size", "opacity", "color"),
        "select_none": ("hint",),
        "eyedropper": ("color", "hint"),
    }
    _HINTS = {
        "select_none": "Click an annotation to select it",
        "eyedropper": "Click anywhere to pick a color",
    }

    def _build_props_bar(self, parent):
        """The properties row. Nothing in it takes keyboard focus, so typing
        text annotations and tool shortcuts keep working."""
        bar = QWidget(parent)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(6)
        bar.setFixedHeight(32)
        sections = {}

        def section(name):
            w = QWidget(bar)
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(2)
            layout.addWidget(w)
            sections[name] = w
            return w, h

        w, h = section("preset")
        self.preset_btn = QToolButton(w)
        self.preset_btn.setObjectName("Chip")
        self.preset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preset_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.preset_btn.setIconSize(QSize(44, 18))
        self.preset_btn.setFixedWidth(150)
        self.preset_btn.clicked.connect(lambda: self._show_brush_popup(self.preset_btn))
        self._hoverable(self.preset_btn, "Choose a brush (or click the Brush tool again)")
        h.addWidget(self.preset_btn)

        w, h = section("eraser_style")
        self.eraser_style_btns = {}
        for style, label, tip in (("hard", "Hard", "Hard eraser: clean, solid edge"),
                                  ("soft", "Soft", "Soft eraser: feathered edge, light pressure erases partly")):
            b = QToolButton(w)
            b.setObjectName("Seg")
            b.setText(label)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda checked=False, s=style: self._set_eraser_style(s))
            self._hoverable(b, tip)
            h.addWidget(b)
            self.eraser_style_btns[style] = b

        w, h = section("shape_type")
        self.shape_type_btns = {}
        for shape in SHAPES:
            b = self._tool_button(w, shape, f"{SHAPE_LABELS[shape]} (U cycles, Shift constrains)", True, h)
            b.setIconSize(QSize(18, 18))
            b.clicked.connect(lambda checked=False, s=shape: self._set_mode(s))
            self.shape_type_btns[shape] = b

        w, h = section("text")
        self.font_combo = QComboBox(w)
        self.font_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.font_combo.setFixedWidth(118)
        for i, family in enumerate(text_fonts.FAMILIES):
            self.font_combo.addItem(family)
            self.font_combo.setItemData(i, QFont(family, 11), Qt.ItemDataRole.FontRole)
        self.font_combo.setCurrentText(self.text_family)
        self.font_combo.currentTextChanged.connect(self._set_text_family)
        self._hoverable(self.font_combo, "Font")
        h.addWidget(self.font_combo)
        self.bold_btn = self._tool_button(w, "bold", "Bold", True, h)
        self.bold_btn.setIconSize(QSize(16, 16))
        self.bold_btn.setChecked(self.text_bold)
        self.bold_btn.clicked.connect(lambda checked: self._set_text_bold(checked))
        self.align_btns = {}
        for align in C.TEXT_ALIGNS:
            b = self._tool_button(w, f"align_{align}", f"Align {align}", True, h)
            b.setIconSize(QSize(16, 16))
            b.setChecked(align == self.text_align)
            b.clicked.connect(lambda checked=False, a=align: self._set_text_align(a))
            self.align_btns[align] = b
        self.text_bar = w

        w, h = section("size")
        self.size_value_label = size_field("Size: drag, scroll or click. [ and ] step", w)
        self.size_value_label.setValue(self.brush_size, emit=False)
        self.size_value_label.valueChanged.connect(self._set_size)
        self.size_slider = self.size_value_label.slider
        h.addWidget(self.size_value_label)

        w, h = section("text_size")
        self.text_size_label = px_field("Size", C.MIN_TEXT_SIZE, C.MAX_TEXT_SIZE, "Text size: drag, scroll or click", w)
        self.text_size_label.setValue(self.text_size, emit=False)
        self.text_size_label.valueChanged.connect(self._set_text_size)
        self.text_size_slider = self.text_size_label.slider
        h.addWidget(self.text_size_label)

        w, h = section("opacity")
        self.opacity_value_label = percent_field("Opacity", 5, 100, "Opacity: drag, scroll or click", w)
        self.opacity_value_label.setFixedWidth(110)
        self.opacity_value_label.setValue(int(self.opacity * 100), emit=False)
        self.opacity_value_label.valueChanged.connect(self._set_opacity)
        self.opacity_slider = self.opacity_value_label.slider
        h.addWidget(self.opacity_value_label)

        w, h = section("color")
        self.color_btn = QToolButton(w)
        self.color_btn.setObjectName("Swatch")
        self.color_btn.setIconSize(QSize(22, 22))
        self.color_btn.setAutoRaise(True)
        self.color_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.color_btn.clicked.connect(self._show_color_popup)
        self._hoverable(self.color_btn, "Color: palette, recent, favorites, HEX, eyedropper (I)")
        h.addWidget(self.color_btn)

        w, h = section("hint")
        self.props_hint = QLabel("", w)
        self.props_hint.setObjectName("Hint")
        self.props_hint.setMinimumWidth(260)
        h.addWidget(self.props_hint)

        layout.addStretch(1)
        self._props_sections = sections
        return bar

    def _fix_panel_width(self):
        """One width for every tool: the widest properties row (or the tool
        row), measured once, so the toolbar never shifts."""
        widest = 0
        for ctx, names in self._PROPS.items():
            total = 0
            shown = [self._props_sections[n] for n in names]
            for w in shown:
                total += w.sizeHint().width()
            total += 6 * max(0, len(shown) - 1) + 4
            widest = max(widest, total)
        top = self.panel.layout().itemAt(0).layout().sizeHint().width()
        self.panel.setFixedWidth(max(widest, top) + 12)
        # And one height: the properties row is always shown with the bar.
        was_hidden = self.props_bar.isHidden()
        self.props_bar.setVisible(True)
        self.panel.setFixedHeight(self.panel.layout().sizeHint().height())
        self.props_bar.setVisible(not was_hidden)

    def _return_focus(self):
        self.setFocus()

    def _props_context(self):
        if self._text_edit is not None or self.mode == "text":
            return "text"
        if self.mode == "select":
            s = self.selection
            if s is None:
                return "select_none"
            return "select_text" if s.shape == "text" else "select_shape"
        if self.mode in SHAPES:
            return "shape"
        return self.mode

    def _update_preset_chip(self):
        key = self.brush_preset
        dpr = max(1.0, float(self.devicePixelRatioF()))
        self.preset_btn.setIcon(QIcon(brush_preview.preview(key, self.color, 44, 18, dpr, size=5.0)))
        self.preset_btn.setText(self._preset_label(key))

    def _sync_text_bar(self):
        """Show the properties for the current tool or selection, and none
        while the toolbar is hidden on purpose (Tab, screenshots,
        pass-through)."""
        bar = getattr(self, "props_bar", None)
        if bar is None or not hasattr(self, "_props_sections"):
            return
        panel_off = self.panel.isHidden() and self.panel.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)
        ctx = self._props_context()
        wanted = set() if panel_off else set(self._PROPS.get(ctx, ()))
        for name, w in self._props_sections.items():
            w.setVisible(name in wanted)
        if wanted:
            self._update_preset_chip()
            for style, b in self.eraser_style_btns.items():
                b.setChecked(style == self.eraser_style)
            for shape, b in self.shape_type_btns.items():
                b.setChecked(shape == self.mode)
            self.size_value_label.caption = "Width" if ctx in ("shape", "select_shape") else "Size"
            self.props_hint.setText(self._HINTS.get(ctx, ""))
            self._sync_size_slider()
            self._sync_opacity_slider()
            self._sync_color_swatch()
        bar.setVisible(bool(wanted))
        if hasattr(self, "toast_label"):
            self._place_chrome()

    # -- More menu and Preferences ----------------------------------------------

    def _menu_popup(self):
        menu = make_panel(QFrame(None))
        menu.setObjectName("Toolbar")
        menu.setStyleSheet(C.TOOLBAR_STYLE + C.FIELD_STYLE + C.MENU_STYLE)
        v = QVBoxLayout(menu)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(1)
        return menu, v

    def _menu_item(self, menu, layout, icon_name, text, shortcut, tooltip, slot, checkable=False, close=True):
        b = MenuRow(menu, icons.build_icon(icon_name) if icon_name else None, text, shortcut)
        b.setCheckable(checkable)
        b.setToolTip(tooltip)

        def run(checked=False, slot=slot):
            if close and menu.isVisible():
                # Close first: a screenshot or selection must not see the
                # menu, and the next popup opens from the toolbar.
                menu.close()
                QTimer.singleShot(0, slot)
            else:
                slot()
        b.clicked.connect(run)
        layout.addWidget(b)
        return b

    @staticmethod
    def _menu_heading(menu, layout, text, first=False):
        if not first:
            rule = QFrame(menu)
            rule.setObjectName("Rule")
            rule.setFixedHeight(1)
            layout.addSpacing(3)
            layout.addWidget(rule)
            layout.addSpacing(3)
        label = QLabel(text, menu)
        label.setObjectName("MenuHeading")
        layout.addWidget(label)

    def _build_more_menu(self):
        """Canvas, export and application commands, menu style: icon, name,
        shortcut on the right."""
        menu, v = self._menu_popup()
        item = lambda *a, **k: self._menu_item(menu, v, *a, **k)  # noqa: E731

        self._menu_heading(menu, v, "CANVAS", first=True)
        self.capture_menu_btn = item("region", "Capture area", "R", "Select an area to annotate",
                                     self.begin_region_selection)
        self.region_btn = item("margin", "Margin and area…", "", "Annotation margin and capture area",
                               self._show_region_popup, checkable=True)
        self.backdrop_btn = item(None, "", "", "Backdrop behind your annotations (click to cycle)",
                                 self._cycle_backdrop, close=False)
        self.backdrop_btn.setIcon(self._backdrop_icon(self.backdrop))
        self.backdrop_btn.setText(f"Backdrop: {self._BACKDROP_LABEL[self.backdrop]}")
        h = QHBoxLayout()
        h.setContentsMargins(34, 0, 10, 2)
        h.setSpacing(6)
        self.backdrop_opacity_slider = QSlider(Qt.Orientation.Horizontal, menu)
        self.backdrop_opacity_slider.setRange(10, 100)
        self.backdrop_opacity_slider.setValue(int(self.backdrop_opacity * 100))
        self.backdrop_opacity_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.backdrop_opacity_slider.setToolTip("Backdrop intensity")
        self.backdrop_opacity_slider.valueChanged.connect(self._on_backdrop_opacity_slider_changed)
        h.addWidget(self.backdrop_opacity_slider, 1)
        self.backdrop_opacity_value_label = QLabel(f"{int(self.backdrop_opacity * 100)}%", menu)
        self.backdrop_opacity_value_label.setObjectName("SliderValue")
        h.addWidget(self.backdrop_opacity_value_label)
        v.addLayout(h)
        self.fit_btn = item("fit", "Fit capture to screen", "Ctrl+0", "Frame the whole capture", self.frame_capture)
        self.actual_btn = item("actual", "Actual pixels", "Ctrl+1", "One image pixel per screen pixel",
                               self.actual_pixels)

        self._menu_heading(menu, v, "EXPORT")
        self.snap_menu_btn = item("snapshot", "Screenshot", "F9", "Save a screenshot with your annotations",
                                  self.capture_annotated)
        self.folder_btn = item("folder", "Screenshots folder", "", "Open the folder screenshots are saved to",
                               self.open_screenshot_folder)

        self._menu_heading(menu, v, "APPLICATION")
        self.pass_btn = item("pass_through", "Pass-through", "F8", "Click through to the desktop",
                             self.toggle_pass_through, checkable=True)
        self.hide_ui_btn = item("hide_ui", "Hide interface", "Tab", "Hide the toolbar", self.toggle_interface)
        self.monitor_btn = item("monitor", "Next monitor", "F7", "Move to the next monitor", self.move_to_next_screen)
        self.prefs_btn = item("settings", "Preferences…", "", "Pen pressure, stabilizer, startup",
                              self._show_prefs)
        self.exit_btn = item("exit", "Exit", "Esc", "Exit (Esc)", self.dismiss)
        menu.setFixedWidth(250)
        return menu

    def _build_prefs(self):
        """Persistent preferences: how the pen feels and how the app starts."""
        pop, v = self._menu_popup()
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        self._menu_heading(pop, v, "PRESSURE RESPONSE", first=True)
        r = QHBoxLayout()
        r.setSpacing(2)
        self.pressure_buttons = {}
        for name, tip in (("soft", "Full strength with a light touch"), ("normal", "Linear"),
                          ("firm", "Needs more force for full strength")):
            b = QToolButton(pop)
            b.setObjectName("Seg")
            b.setText(name.capitalize())
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(f"Pen pressure response: {tip}")
            b.clicked.connect(lambda checked=False, n=name: (self._set_pressure_curve(n), self._sync_prefs()))
            r.addWidget(b)
            self.pressure_buttons[name] = b
        r.addStretch(1)
        v.addLayout(r)
        self._menu_heading(pop, v, "BRUSH CURSOR")
        r = QHBoxLayout()
        r.setSpacing(2)
        self.cursor_buttons = {}
        for name, tip in (("outline", "A circle the size of the brush"), ("crosshair", "A small crosshair"),
                          ("hidden", "No brush cursor")):
            b = QToolButton(pop)
            b.setObjectName("Seg")
            b.setText(name.capitalize())
            b.setCheckable(True)
            b.setChecked(name == self.brush_cursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(tip)
            b.clicked.connect(lambda checked=False, n=name: self._set_brush_cursor(n))
            r.addWidget(b)
            self.cursor_buttons[name] = b
        r.addStretch(1)
        v.addLayout(r)
        self._menu_heading(pop, v, "STABILIZER")
        r = QHBoxLayout()
        self.smoothing_slider = QSlider(Qt.Orientation.Horizontal, pop)
        self.smoothing_slider.setRange(0, 90)
        self.smoothing_slider.setValue(int(self.smoothing * 100))
        self.smoothing_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.smoothing_slider.setToolTip("Steadier lines that trail the pen (all brushes)")
        self.smoothing_slider.valueChanged.connect(self._on_smoothing_slider_changed)
        r.addWidget(self.smoothing_slider, 1)
        self.smoothing_value_label = QLabel(f"{int(self.smoothing * 100)}%", pop)
        self.smoothing_value_label.setObjectName("SliderValue")
        r.addWidget(self.smoothing_value_label)
        v.addLayout(r)
        self._menu_heading(pop, v, "DIAGNOSTICS")
        self.diag_btn = MenuRow(pop, None, "Show input diagnostic", "Ctrl+Shift+I")
        self.diag_btn.setCheckable(True)
        self.diag_btn.setToolTip("Raw and processed pen pressure, tilt, device and brush diameter")
        self.diag_btn.clicked.connect(lambda checked: self.toggle_input_diag())
        v.addWidget(self.diag_btn)
        self.startup_heading = QWidget(pop)
        sh = QVBoxLayout(self.startup_heading)
        sh.setContentsMargins(0, 0, 0, 0)
        self._menu_heading(self.startup_heading, sh, "STARTUP")
        v.addWidget(self.startup_heading)
        self.autostart_btn = MenuRow(pop, None, "Start with Windows", "")
        self.autostart_btn.setCheckable(True)
        self.autostart_btn.setToolTip("Start Crit Overlay when you sign in")
        self.autostart_btn.clicked.connect(lambda checked: self.autostart_toggled.emit(checked))
        v.addWidget(self.autostart_btn)
        self.autostart_btn.hide()  # until app.py says autostart is available
        self.startup_heading.hide()
        pop.setFixedWidth(250)
        return pop

    def _sync_prefs(self):
        for name, b in self.pressure_buttons.items():
            b.setChecked(name == self.pressure_curve)

    def _show_prefs(self):
        self._sync_prefs()
        place_popup(self.prefs_popup, self.more_btn, align="right")

    def _show_more_menu(self):
        # Esc discards a capture rather than hiding, so only show it as
        # the shortcut when it really does what the item says.
        self.exit_btn.set_shortcut("" if self.capture is not None else "Esc")
        for b in (self.fit_btn, self.actual_btn):
            b.setEnabled(self.capture is not None)
        self.pass_btn.setChecked(self.pass_through)
        self._refresh_region_button()
        place_popup(self.more_menu, self.more_btn, align="right")

    def set_autostart_checked(self, checked):
        """Called by app.py: offer Start with Windows in Preferences with its
        current state."""
        self.startup_heading.show()
        self.autostart_btn.show()
        self.autostart_btn.setChecked(bool(checked))

    def open_screenshot_folder(self):
        folder = self._last_good_screenshot_dir or paths.current().screenshot_dir_candidates(
            self._screenshot_dir_override)[0]
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # noqa: S606 - user action, our own folder
        except Exception:
            logger.warning("Couldn't open %s", folder, exc_info=True)
            self.status_changed.emit(f"Couldn't open {folder}")

    def toggle_interface(self):
        """Tab: hide or show the toolbar. Drawing and every shortcut keep
        working while hidden."""
        if self.pass_through or self.capture_pending():
            return
        self._ui_hidden = not self._ui_hidden
        self.hover_tip.hide()
        self.panel.setVisible(not self._ui_hidden)
        self._sync_text_bar()
        if self._ui_hidden:
            self.status_changed.emit("Interface hidden. Tab shows it")

    def capture_pending(self):
        return self._grabbing or self._capturing

    def _text_style_changed(self, what=None):
        te = self._text_edit
        style = TextStyle(self.text_family, self.text_bold, self.text_align)
        size = self.text_size / self._view_scale()
        if te is not None:
            te.font = style
            te.size = size
            self.setFocus()
            self.update()
        elif self.selection is not None and self.selection.shape == "text" and what is not None:
            def apply(s):
                if what == "size":
                    s.size = size
                else:
                    s.font = style
            self._modify_selection(apply, merge_key="text_" + what)
        self._save_settings()

    def _set_text_family(self, family):
        self.text_family = family
        self._text_style_changed("family")

    def _set_text_size(self, value):
        self.text_size = int(value)
        self._sync_text_size_widgets()
        self._text_style_changed("size")

    def _sync_text_size_widgets(self):
        for widget in (self.text_size_slider, self.text_size_label):
            widget.blockSignals(True)
            widget.setValue(self.text_size)
            widget.blockSignals(False)

    def _set_text_bold(self, bold):
        self.text_bold = bool(bold)
        self.bold_btn.setChecked(self.text_bold)
        self._text_style_changed("bold")

    def _set_text_align(self, align):
        self.text_align = align
        for a, btn in self.align_btns.items():
            btn.setChecked(a == align)
        self._text_style_changed("align")

    # -- text editing -------------------------------------------------------

    def _in_drawable(self, pos):
        if self.capture is not None:
            return self._doc_outer().contains(QPointF(pos))
        return self.region is None or self.drawable_rect().contains(QPointF(pos).toPoint())

    def _begin_text(self, stroke, original=None, caret_at=None):
        """Start typing into `stroke`. With `original`, it is a copy of that
        committed annotation, which is hidden from the canvas meanwhile."""
        self._text_edit = stroke
        self._editor = TextEditor(stroke)
        self._text_original = original
        if original is not None:
            self._hidden_stroke = original
            self._rebuild_canvas()
            if caret_at is not None:
                self._editor.place(caret_at)
        self.history.redo_stack.clear()
        self._refresh_history_buttons()
        self.activateWindow()
        self.setFocus()
        self.update()

    def _end_text_edit(self):
        te, original = self._text_edit, self._text_original
        self._text_edit = self._editor = self._text_original = None
        self._text_dragging = False
        if self._hidden_stroke is not None:
            self._hidden_stroke = None
            self._rebuild_canvas()
        return te, original

    def _commit_text(self):
        if self._text_edit is None:
            return
        te, original = self._end_text_edit()
        keep = bool(te.text.strip())
        if original is not None:
            if keep:
                self.history.replace(original, te)
            else:
                self.history.change([original], [])
            self._rebuild_canvas()
            self.selection = te if keep and self.mode == "select" else None
        elif keep:
            self.history.commit(te)
            self._commit_stroke_to_canvas(te)
        self._refresh_history_buttons()
        self._sync_text_bar()
        self.update()

    def _cancel_text(self):
        if self._text_edit is not None:
            self._end_text_edit()
            self.update()

    def _annotation_press(self, pos, modifiers=Qt.KeyboardModifier.NoModifier):
        """A press in stroke coordinates for the text and select tools.
        Returns True if handled (the press must not start a stroke)."""
        te = self._text_edit
        if te is not None:
            if brush.text_rect(te).adjusted(-8, -8, 8, 8).contains(pos):
                self._editor.place(pos, extend=bool(modifiers & Qt.KeyboardModifier.ShiftModifier))
                self._text_dragging = True
                self.update()
                return True
            self._commit_text()
            if self.mode != "select":
                return True  # a click outside only commits
        if self.mode == "select":
            self._select_press(pos)
            return True
        if self.mode != "text":
            return False
        if self.pass_through or not self._in_drawable(pos):
            return True
        stroke = Stroke(self.color, self.text_size / self._view_scale(), self.opacity, shape="text")
        stroke.font = TextStyle(self.text_family, self.text_bold, self.text_align)
        stroke.points = [(QPointF(pos), brush.SHAPE_PRESSURE)]
        self._begin_text(stroke)
        return True

    def _text_key(self, event):
        ed = self._editor
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        moves = {
            Qt.Key.Key_Left: "word_left" if ctrl else "left",
            Qt.Key.Key_Right: "word_right" if ctrl else "right",
            Qt.Key.Key_Up: "up",
            Qt.Key.Key_Down: "down",
            Qt.Key.Key_Home: "start" if ctrl else "home",
            Qt.Key.Key_End: "finish" if ctrl else "end",
        }
        clipboard = QApplication.clipboard()
        if key == Qt.Key.Key_Escape:
            self._cancel_text()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if ctrl:
                self._commit_text()
            else:
                ed.insert("\n")
        elif key == Qt.Key.Key_Backspace:
            ed.backspace()
        elif key == Qt.Key.Key_Delete:
            ed.delete()
        elif key in moves:
            ed.move(moves[key], extend=shift)
        elif ctrl and key == Qt.Key.Key_A:
            ed.select_all()
        elif ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_X):
            if ed.has_selection():
                clipboard.setText(ed.selected_text())
                if key == Qt.Key.Key_X:
                    ed.insert("")
        elif ctrl and key == Qt.Key.Key_V:
            pasted = clipboard.text().replace("\r\n", "\n").replace("\r", "\n")
            ed.insert("".join(ch for ch in pasted if ch == "\n" or ch.isprintable()))
        else:
            typed = event.text()
            if typed and typed.isprintable():
                ed.insert(typed)
        event.accept()
        self.update()

    # Keys that keep their shortcut while typing: they commit the text
    # first (see the methods they call).
    _TEXT_PASSTHROUGH_KEYS = (Qt.Key.Key_F7, Qt.Key.Key_F8, Qt.Key.Key_F9)

    def event(self, event):
        # Trackpad pinch zooms a capture around the fingers.
        if (event.type() == QEvent.Type.NativeGesture and getattr(self, "capture", None) is not None
                and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
            self.zoom_at(event.position(), 1.0 + event.value())
            return True
        # While typing, claim every key before the application shortcuts
        # see it, so B, E, [ and the rest type instead of switching tools.
        if (event.type() == QEvent.Type.ShortcutOverride and getattr(self, "_text_edit", None) is not None
                and event.key() not in self._TEXT_PASSTHROUGH_KEYS):
            event.accept()
            return True
        return super().event(event)

    def _paint_live_text(self, painter):
        te = self._text_edit
        layer = brush.render_shape_layer(te, self._canvas_dpr())
        painter.save()
        if layer is not None:
            pixmap, origin = layer
            painter.setOpacity(max(0.01, min(1.0, te.opacity)))
            painter.drawPixmap(origin, pixmap)
            painter.setOpacity(1.0)
        frame = QPen(QColor(255, 255, 255, 170), 1, Qt.PenStyle.DashLine)
        frame.setCosmetic(True)
        painter.setPen(frame)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pad = 4 / self._view_scale()
        painter.drawRect(brush.text_rect(te).adjusted(-pad, -pad, pad, pad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(77, 171, 247, 90))
        for rect in self._editor.selection_rects():
            painter.drawRect(rect)
        caret = QPen(QColor(te.color), 2)
        caret.setCosmetic(True)
        painter.setPen(caret)
        painter.drawLine(*self._editor.caret_line())
        painter.restore()

    def _paint_floating(self, painter, stroke):
        layer = brush.render_shape_layer(stroke, self._canvas_dpr())
        if layer is None:
            return
        pixmap, origin = layer
        painter.save()
        painter.setOpacity(max(0.01, min(1.0, stroke.opacity)))
        painter.drawPixmap(origin, pixmap)
        painter.restore()

    _HANDLE_PX = 7

    def _paint_selection(self, painter):
        """Outline and handles, in window coordinates at a fixed on-screen
        size. Painted on the window only, never into the canvas, and not
        while capturing, so they never reach a screenshot."""
        stroke = self._floating or self.selection
        rect = sel_mod.bounds(stroke)
        tl, br = self._to_view(rect.topLeft()), self._to_view(rect.bottomRight())
        view_rect = QRectF(tl, br).normalized().adjusted(-3, -3, 3, 3)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 140), 1))
        painter.drawRect(view_rect)
        painter.setPen(QPen(QColor(77, 171, 247, 230), 1, Qt.PenStyle.DashLine))
        painter.drawRect(view_rect)
        h = self._HANDLE_PX
        painter.setPen(QPen(QColor(77, 171, 247), 1))
        painter.setBrush(QColor(255, 255, 255))
        for point in sel_mod.handles(stroke).values():
            v = self._to_view(point)
            painter.drawRect(QRectF(v.x() - h / 2, v.y() - h / 2, h, h))
        painter.restore()

    # -- select tool ----------------------------------------------------------

    def _select_tolerance(self):
        return 6 / self._view_scale()

    def _set_selection(self, stroke):
        if stroke is self.selection:
            return
        self.selection = stroke
        if stroke is not None and stroke.shape == "text":
            self._show_text_style(stroke)
        self._sync_text_bar()
        self.update()

    def _show_text_style(self, stroke):
        """Point the text bar at a selected text, without editing it."""
        self.text_family = stroke.font.family
        self.text_bold = stroke.font.bold
        self.text_align = stroke.font.align
        self.text_size = int(max(C.MIN_TEXT_SIZE, min(C.MAX_TEXT_SIZE, round(stroke.size * self._view_scale()))))
        for widget in (self.font_combo, self.text_size_slider, self.text_size_label, self.bold_btn,
                       *self.align_btns.values()):
            widget.blockSignals(True)
        self.font_combo.setCurrentText(self.text_family)
        self.text_size_slider.setValue(self.text_size)
        self.text_size_label.setValue(self.text_size)
        self.bold_btn.setChecked(self.text_bold)
        for a, btn in self.align_btns.items():
            btn.setChecked(a == self.text_align)
        for widget in (self.font_combo, self.text_size_slider, self.text_size_label, self.bold_btn,
                       *self.align_btns.values()):
            widget.blockSignals(False)

    def _modify_selection(self, fn, merge_key=None):
        old = self.selection
        new = old.copy()
        fn(new)
        self._replace_selection(new, merge_key)

    def _replace_selection(self, new, merge_key=None):
        self.history.replace(self.selection, new, merge_key)
        self.selection = new
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self.update()

    def _select_press(self, pos):
        now = time.monotonic()
        last_time, last_hit = self._last_select_press
        tol = self._select_tolerance()
        if self.selection is not None:
            handle = sel_mod.handle_at(self.selection, pos, tol)
            if handle is not None:
                self._drag = {"kind": "resize", "handle": handle, "start": QPointF(pos),
                              "orig": self.selection, "moved": False}
                return
        hit = sel_mod.topmost_hit(self.history, pos, tol)
        self._last_select_press = (now, hit)
        self._set_selection(hit)
        if hit is None:
            return
        double = hit is last_hit and now - last_time < QApplication.doubleClickInterval() / 1000.0
        if double and hit.shape == "text":
            self._last_select_press = (0.0, None)
            self._begin_text(sel_mod.absorb(self.history, hit), original=hit, caret_at=pos)
            return
        self._drag = {"kind": "move", "handle": None, "start": QPointF(pos), "orig": hit, "moved": False}

    def _annotation_move(self, pos):
        if self._text_dragging and self._editor is not None:
            self._editor.place(pos, extend=True)
            self.update()
            return
        drag = self._drag
        if drag is None:
            return
        drag["pos"] = QPointF(pos)
        dx, dy = pos.x() - drag["start"].x(), pos.y() - drag["start"].y()
        if not drag["moved"]:
            if max(abs(dx), abs(dy)) * self._view_scale() < 3:
                return
            drag["moved"] = True
            drag["src"] = sel_mod.absorb(self.history, drag["orig"])
            self._hidden_stroke = drag["orig"]
            self._rebuild_canvas()
        if drag["kind"] == "move":
            self._floating = sel_mod.translated(drag["src"], dx, dy)
        else:
            shift = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._floating = sel_mod.resized(drag["src"], drag["handle"], pos, constrain=shift)
        self.update()

    def _annotation_release(self):
        self._text_dragging = False
        drag, self._drag = self._drag, None
        if drag is None or not drag["moved"]:
            return
        floating, self._floating = self._floating, None
        self._hidden_stroke = None
        if floating is not None:
            self._replace_selection(floating)
        else:
            self._rebuild_canvas()
            self.update()

    def _offset_copy(self, stroke):
        self._paste_count += 1
        step = 16 * self._paste_count / self._view_scale()
        return sel_mod.translated(stroke, step, step)

    def _add_annotation(self, stroke):
        self.history.commit(stroke)
        self._commit_stroke_to_canvas(stroke)
        if self.mode != "select":
            self._set_mode("select")
        self._set_selection(stroke)
        self._refresh_history_buttons()
        self.update()

    _MIME = "application/x-crit-overlay-annotation"

    def copy_selection(self):
        if self.selection is None:
            return
        self._annotation_clipboard = sel_mod.absorb(self.history, self.selection)
        self._paste_count = 0
        mime = QMimeData()
        mime.setData(self._MIME, b"1")
        if self.selection.shape == "text":
            mime.setText(self.selection.text)
        QApplication.clipboard().setMimeData(mime)

    def cut_selection(self):
        if self.selection is not None:
            self.copy_selection()
            self.delete_selection()

    def paste(self):
        """Annotations copied here paste as annotations; any other text on
        the Windows clipboard becomes a new text annotation at the cursor."""
        if self._text_edit is not None:
            return
        mime = QApplication.clipboard().mimeData()
        if mime is not None and mime.hasFormat(self._MIME) and self._annotation_clipboard is not None:
            self._add_annotation(self._offset_copy(self._annotation_clipboard))
            return
        text = QApplication.clipboard().text().replace("\r\n", "\n").replace("\r", "\n")
        text = "".join(ch for ch in text if ch == "\n" or ch.isprintable()).strip("\n")
        if not text.strip():
            return
        pos = self.cursor_pos or self._to_doc(QPointF(self.width() / 2, self.height() / 2))
        stroke = Stroke(self.color, self.text_size / self._view_scale(), self.opacity, shape="text")
        stroke.font = TextStyle(self.text_family, self.text_bold, self.text_align)
        stroke.points = [(QPointF(pos), brush.SHAPE_PRESSURE)]
        stroke.text = text
        self._add_annotation(stroke)

    def duplicate_selection(self):
        if self.selection is not None:
            self._paste_count = 0
            self._add_annotation(self._offset_copy(sel_mod.absorb(self.history, self.selection)))

    def delete_selection(self):
        if self.selection is None or self._drag is not None:
            return
        self.history.change([self.selection], [])
        self.selection = None
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self._sync_text_bar()
        self.update()

    def _nudge(self, key, big):
        step = (10 if big else 1) / self._view_scale()
        dx, dy = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                  Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}[key]
        moved = sel_mod.translated(sel_mod.absorb(self.history, self.selection), dx, dy)
        self._replace_selection(moved, merge_key="nudge")

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
        add("U", self._cycle_shape)
        add("T", lambda: self._set_mode("text"))
        add("V", lambda: self._set_mode("select"))
        add("Ctrl+C", self.copy_selection)
        add("Ctrl+X", self.cut_selection)
        add("Ctrl+V", self.paste)
        add("Ctrl+D", self.duplicate_selection)
        add("Delete", self.delete_selection)
        add("Tab", self.toggle_interface)
        add("Ctrl+Shift+I", self.toggle_input_diag)
        add("Ctrl+0", self.frame_capture)
        add("Ctrl+1", self.actual_pixels)
        add("Ctrl+=", lambda: self.zoom_by(1.25))
        add("Ctrl++", lambda: self.zoom_by(1.25))
        add("Ctrl+-", lambda: self.zoom_by(0.8))
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
            text_family=self.text_family,
            text_size=self.text_size,
            text_bold=self.text_bold,
            text_align=self.text_align,
            eraser_size=self.eraser_size,
            brush_preset=self.brush_preset,
            brush_pressure=self.brush_pressure,
            pressure_curve=self.pressure_curve,
            eraser_style=self.eraser_style,
            eraser_opacity=self.eraser_opacity,
            recent_colors=self.recent_colors,
            favorite_colors=self.favorite_colors,
            toolbar_positions=self._toolbar_positions,
            brush_cursor=self.brush_cursor,
        )

    # State setters. Every mutable tool property goes through one of these,
    # which updates the model and the widget showing it together, so a
    # hotkey and a toolbar control can't disagree about what's selected.

    def _set_mode(self, mode):
        if mode != "text":
            self._commit_text()
        if mode != "select":
            self._set_selection(None)
        self.mode = mode
        self.text_btn.setChecked(mode == "text")
        self.select_btn.setChecked(mode == "select")
        if mode == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif mode == "select":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif not self._wants_pan() and not self._selecting:
            self.unsetCursor()
        self._sync_text_bar()
        self.brush_btn.setChecked(mode == "brush")
        self.eraser_btn.setChecked(mode == "eraser")
        self.eyedropper_btn.setChecked(mode == "eyedropper")
        if mode in SHAPES and mode != self._last_shape:
            self._last_shape = mode
            self.shape_btn.setIcon(icons.build_icon(mode))
        self.shape_btn.setChecked(mode in SHAPES)
        if getattr(self, "size_slider", None) is not None:
            self._sync_size_slider()
        self.update()

    def _on_brush_button(self):
        was_brush = self.mode == "brush"
        self._set_mode("brush")
        if was_brush:
            self._show_brush_popup(self.brush_btn)

    def _show_brush_popup(self, anchor):
        if self._brush_popup is None:
            self._brush_popup = BrushPopup(self)
        self._brush_popup.show_below(anchor)

    def _on_eraser_button(self):
        was_eraser = self.mode == "eraser"
        self._set_mode("eraser")
        if was_eraser:
            if self._eraser_popup is None:
                from .brush_popup import EraserPopup
                self._eraser_popup = EraserPopup(self)
            self._eraser_popup.show_below(self.eraser_btn)

    def _set_eraser_style(self, style):
        if style not in C.ERASER_STYLES:
            return
        self.eraser_style = style
        self.status_changed.emit("Soft eraser" if style == "soft" else "Hard eraser")
        self._sync_text_bar()
        self._show_size_preview()
        self._save_settings()

    def _set_pressure_curve(self, name):
        if name in C.PRESSURE_CURVES:
            self.pressure_curve = name
            self.status_changed.emit(f"Pressure: {name}")
            self._save_settings()

    def _curved(self, pressure):
        return max(0.0, min(1.0, pressure)) ** C.PRESSURE_CURVES.get(self.pressure_curve, 1.0)

    def _preset_label(self, key):
        return brushes.LABELS.get(key, key)

    def _set_brush_preset(self, key):
        if key not in brushes.PRESETS:
            return
        self.brush_preset = key
        if self.mode != "brush":
            self._set_mode("brush")
        self.status_changed.emit(self._preset_label(key))
        self._show_size_preview()
        self._sync_text_bar()
        self._save_settings()

    def pressure_flags(self, key=None):
        """(pressure drives size, pressure drives opacity) for a preset."""
        key = key or self.brush_preset
        p = brushes.PRESETS[key]
        return self.brush_pressure.get(key, (p.size_pressure, p.opacity_pressure))

    def set_brush_pressure(self, size=None, opacity=None):
        """Turn pressure-driven size or opacity on or off for the current
        brush (each brush remembers its own)."""
        s, o = self.pressure_flags()
        self.brush_pressure[self.brush_preset] = (s if size is None else bool(size),
                                                  o if opacity is None else bool(opacity))
        self._save_settings()

    def _stroke_spec(self, eraser):
        if eraser:
            return brushes.spec_for(brushes.ERASERS[self.eraser_style])
        size_p, opacity_p = self.pressure_flags()
        return brushes.spec_for(brushes.PRESETS[self.brush_preset], size_p, opacity_p)

    def _show_size_preview(self):
        """Show the brush at its real size and softness for a moment, at
        the last canvas position (or the center), while size or tuning
        changes."""
        self._size_preview_until = time.monotonic() + 0.9
        QTimer.singleShot(950, self.update)
        self.update()

    def _on_shape_button(self):
        # clicked fires after the checkable button toggles itself, so read
        # the mode rather than the button state.
        was_shape = self.mode in SHAPES
        self._set_mode(self._last_shape)
        if was_shape:
            if self._shape_popup is None:
                self._shape_popup = ShapePopup(self)
            self._shape_popup.show_below(self.shape_btn)

    def _cycle_shape(self):
        if self.mode in SHAPES:
            nxt = SHAPES[(SHAPES.index(self.mode) + 1) % len(SHAPES)]
        else:
            nxt = self._last_shape
        self._set_mode(nxt)
        self.status_changed.emit(SHAPE_LABELS[nxt])

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

    def _selected_shape(self):
        s = self.selection if self.mode == "select" else None
        return s if s is not None and s.shape not in (None, "text") else None

    def _active_size(self):
        if self.mode == "eraser":
            return self.eraser_size
        shape = self._selected_shape()
        if shape is not None:
            return round(max(C.MIN_SIZE, min(C.MAX_SIZE, shape.size * self._view_scale())), 2)
        return self.brush_size

    def _sync_size_slider(self):
        value = self._active_size()
        for widget, v in ((self.size_slider, brushes.size_to_slider(value)), (self.size_value_label, value)):
            widget.blockSignals(True)
            widget.setValue(v)
            widget.blockSignals(False)

    def _active_opacity(self):
        if self.mode == "eraser":
            return self.eraser_opacity
        if self.mode == "select" and self.selection is not None:
            return self.selection.opacity
        return self.opacity

    def _sync_opacity_slider(self):
        percent = int(round(self._active_opacity() * 100))
        for widget in (self.opacity_slider, self.opacity_value_label):
            widget.blockSignals(True)
            widget.setValue(percent)
            widget.blockSignals(False)

    def _sync_color_swatch(self):
        color = self.selection.color if (self.mode == "select" and self.selection is not None) else self.color
        self.color_btn.setIcon(swatch_icon(color, 22))

    def _set_size(self, value):
        value = round(max(C.MIN_SIZE, min(C.MAX_SIZE, float(value))), 2)
        if self.mode == "eraser":
            self.eraser_size = value
        else:
            self.brush_size = value
        if self.mode in ("brush", "eraser"):
            self._show_size_preview()
        if self.selection is not None and self.selection.shape != "text":
            size = value / self._view_scale()
            self._modify_selection(lambda s: setattr(s, "size", size), merge_key="size")
        self._sync_size_slider()
        self._save_settings()

    def _on_size_slider_changed(self, v):
        self._set_size(brushes.slider_to_size(v))

    def change_size(self, delta):
        self._set_size(brushes.step_size(self._active_size(), delta))

    def _set_opacity(self, percent):
        percent = max(5, min(100, int(round(percent))))
        if self.mode == "eraser":
            self.eraser_opacity = percent / 100.0
            self._sync_opacity_slider()
            self._save_settings()
            return
        self.opacity = percent / 100.0
        self._sync_opacity_slider()
        if self._text_edit is not None:
            self._text_edit.opacity = self.opacity
            self.setFocus()
            self.update()
        elif self.selection is not None:
            opacity = self.opacity
            self._modify_selection(lambda s: setattr(s, "opacity", opacity), merge_key="opacity")
            self._sync_opacity_slider()
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

    def _set_color(self, color, remember=True):
        self.color = QColor(color)
        if remember:
            self._remember_color(self.color)
        self._sync_color_swatch()
        if hasattr(self, "preset_btn"):
            self._update_preset_chip()
        if self._text_edit is not None:
            self._text_edit.color = QColor(self.color)
            self.setFocus()
        elif self.selection is not None:
            color = QColor(self.color)
            self._modify_selection(lambda s: setattr(s, "color", QColor(color)), merge_key="color")
        self._save_settings()
        self.update()

    def _remember_color(self, color):
        name = QColor(color).name()
        recent = [c for c in self.recent_colors if c != name]
        self.recent_colors = [name] + recent[:C.RECENT_COLORS - 1]

    def add_favorite_color(self, color):
        name = QColor(color).name()
        if name not in self.favorite_colors:
            self.favorite_colors = (self.favorite_colors + [name])[-C.FAVORITE_COLORS:]
            self._save_settings()

    def remove_favorite_color(self, color):
        name = QColor(color).name()
        self.favorite_colors = [c for c in self.favorite_colors if c != name]
        self._save_settings()

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
        self.backdrop_btn.setText(f"Backdrop: {self._BACKDROP_LABEL[value]}")
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
        margin, or the whole window with no region. During a capture, the
        image plus margin in document coordinates (a QRectF)."""
        if self.capture is not None:
            return self._doc_outer()
        margin = self.margin_size if self.margin_enabled else 0
        return region_mod.outer_rect(self.region, margin, self.rect())

    def _refresh_region_button(self):
        if self.capture is not None:
            s = self.capture.size()
            self.region_btn.setChecked(True)
            self.region_btn.setToolTip(f"Capture: {round(s.width())} x {round(s.height())} (R to capture again)")
        elif self.region is None:
            self.region_btn.setChecked(False)
            self.region_btn.setToolTip("Drawing region: full monitor (R to capture an area)")
        else:
            self.region_btn.setChecked(True)
            self.region_btn.setToolTip(
                f"Drawing region: {self.region.width()} x {self.region.height()} (R to capture an area)")

    def set_region(self, rect):
        if self.current is not None:
            self._end_stroke()
        if self.capture is not None:
            self.end_capture()
        rect = region_mod.clamp_region(rect, self.rect()) if rect is not None else None
        changed = rect != self.region
        self.region = rect
        self._refresh_region_button()
        if changed:
            logger.info("Drawing region %s", "full monitor" if rect is None else
                        f"{rect.x()},{rect.y()} {rect.width()}x{rect.height()} on {self._anchor_screen().name()}")
        self._sync_region_popup()
        self.update()

    def begin_region_selection(self):
        self._commit_text()
        if self.pass_through or self._grabbing or self._selecting:
            return
        if self.current is not None:
            self._end_stroke()
        # Selecting again replaces the capture, like a new snip, once the new
        # area is actually captured. Until then the old capture and its
        # history are kept aside, so Esc can return to them.
        if self.capture is not None:
            self._stash_session()
        self._selecting = True
        self._selection_anchor = None
        self._selection_point = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.status_changed.emit("Drag to capture an area. Click to draw on the full monitor, Esc to go back")

    def _finish_region_selection(self, end_pos):
        anchor = self._selection_anchor
        self._cancel_selection_state()
        if anchor is None:
            return
        rect = region_mod.selection_rect(anchor, end_pos, self.rect())
        if region_mod.is_usable(rect):
            self._start_capture(rect)
        else:
            self._stashed_session = None  # a deliberate click: full-monitor drawing
            self.set_region(None)
            self.status_changed.emit("Drawing on the full monitor. R to capture an area")

    # -- centered capture ---------------------------------------------------

    def _margin_px(self):
        return self.margin_size if self.margin_enabled else 0

    def _doc_outer(self):
        return self.capture.outer_rect(self._margin_px())

    def _to_doc(self, pos):
        """Window position to stroke coordinates."""
        return self.capture.to_doc(pos) if self.capture is not None else QPointF(pos)

    def _to_view(self, pos):
        """Stroke coordinates to window position."""
        return self.capture.to_view(pos) if self.capture is not None else pos

    def _view_scale(self):
        return self.capture.scale if self.capture is not None else 1.0

    def _fit_capture(self):
        """Center the capture (image plus margin) on the monitor, shrunk
        only if it doesn't fit. The image keeps its own resolution."""
        if getattr(self, "capture", None) is None:
            return
        scale, offset = capture_mod.fit(self._doc_outer(), QRectF(self.rect()),
                                        max(1.0, float(self.devicePixelRatioF())))
        self.capture.scale = scale
        self.capture.offset = offset
        self.update()

    def _start_capture(self, rect):
        """Grab `rect` (window coordinates) from the screen once nothing of
        ours is painted over it, then enter the capture."""
        panel_vis = not self.panel.isHidden()  # its own flag; see #25
        self._grabbing = True
        self.panel.hide()
        self.toast_label.hide()
        self.hover_tip.hide()
        self.cursor_pos = None
        self.repaint()
        QApplication.processEvents()
        QTimer.singleShot(capture_mod.GRAB_DELAY_MS, lambda: self._finish_start_capture(rect, panel_vis))

    def _finish_start_capture(self, rect, panel_vis):
        grab = None
        try:
            screen = self._target_screen or QGuiApplication.primaryScreen()
            r = rect.translated(self.geometry().topLeft() - screen.geometry().topLeft())
            full = screen.grabWindow(0)
            if full.isNull():
                logger.error("Capture: screen grab returned nothing for %s", screen.name())
            else:
                # The screen's own ratio: full width / logical width is off
                # at 175% when the logical width was rounded (1097 for 1920).
                grab = capture_mod.crop_grab(full, r, screen.devicePixelRatio())
                if grab.isNull():
                    logger.error("Capture: %s is outside the grab of %s", r, screen.name())
        except Exception:
            logger.exception("Capture grab failed")
        finally:
            self._grabbing = False
            if panel_vis and not self.pass_through:
                self.panel.show()
            self.update()
            self.claim_foreground("new capture")
        if grab is None or grab.isNull():
            self.begin_region_selection()
            # After the selection prompt, so it isn't replaced by it.
            self.status_changed.emit("Capture failed, see log. Drag to try again")
            return
        self._enter_capture(capture_mod.Capture(grab, rect))

    def _stash_session(self):
        self._cancel_text()
        if self.current is not None:
            self._end_stroke()
        self._set_selection(None)
        self._stashed_session = (self.capture, self.history)
        self.history = History(C.MAX_STROKE_HISTORY)
        self.capture = None
        self._live_layer = None
        self._panning = None
        self.cursor_pos = None
        self.lazy_pos = None
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self._refresh_region_button()
        self.update()

    def _restore_stashed_session(self):
        """Back to the capture and history that were set aside when a new
        selection started. Returns True if there was one."""
        if self._stashed_session is None:
            return False
        self.capture, self.history = self._stashed_session
        self._stashed_session = None
        self.region = None
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self._refresh_region_button()
        self._sync_region_popup()
        self.update()
        return True

    def _enter_capture(self, cap):
        self._stashed_session = None
        if self.current is not None:
            self._end_stroke()
        self.region = None
        self.capture = cap
        self.history.clear()
        self.current = None
        self.cursor_pos = None  # window coordinates until the next move
        self.lazy_pos = None
        self._fit_capture()
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self._refresh_region_button()
        self._sync_region_popup()
        self.update()
        s = cap.size()
        logger.info("Captured %dx%d at %gx from %s", round(s.width()), round(s.height()), cap.dpr,
                    f"{cap.source_rect.x()},{cap.source_rect.y()}")
        self.status_changed.emit("Space+drag moves it. F9 saves, R captures again, Esc hides")

    def end_capture(self):
        """Discard the capture and its strokes."""
        self._cancel_text()
        if self.capture is None:
            return
        self.current = None
        self._live_layer = None
        self._panning = None
        # In the capture's coordinates; meaningless once it's gone.
        self.cursor_pos = None
        self.lazy_pos = None
        self.capture = None
        self.history.clear()
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self._refresh_region_button()
        self._sync_region_popup()
        self.update()

    def capture_export(self):
        margin_fill = region_mod.margin_color(self.margin_color) if self.margin_enabled else None
        # The backdrop as set, also during pass-through (which only hides
        # it on screen).
        backdrop = self._backdrop_fill(ignore_pass_through=True)
        return capture_mod.compose_export(self.capture, self._canvas, self._canvas_origin,
                                          self._margin_px(), margin_fill, backdrop)

    def _stop_panning(self):
        """Forget Space and any drag in progress. Called when the key or
        mouse release may never arrive here: focus loss, pass-through, a
        release over the toolbar."""
        self._space_held = False
        if self._panning is not None:
            self._panning = None
        if not self._selecting:
            self.unsetCursor()

    def focusOutEvent(self, event):
        self.focusOutEvent_reset_temporary()
        # Space released after Alt+Tab never reaches us; without this every
        # click would keep panning.
        if self._space_held or self._panning is not None:
            self._stop_panning()
        super().focusOutEvent(event)

    def changeEvent(self, event):
        if (event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow()
                and (self._space_held or self._panning is not None)):
            self._stop_panning()
        super().changeEvent(event)

    def _pan_input(self, kind, pos):
        """Space+drag moves the whole capture: image, margin and strokes."""
        if kind == "press":
            self._panning = (QPointF(pos), QPointF(self.capture.offset))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif kind == "move" and self._panning is not None:
            start, offset = self._panning
            dpr = max(1.0, float(self.devicePixelRatioF()))
            self.capture.offset = QPointF(capture_mod.snap(offset.x() + pos.x() - start.x(), dpr),
                                          capture_mod.snap(offset.y() + pos.y() - start.y(), dpr))
            self.update()
        elif kind == "release":
            self._panning = None
            if self._space_held:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()

    def _wants_pan(self):
        return self.capture is not None and (self._space_held or self._panning is not None)

    def keyPressEvent(self, event):
        if self._text_edit is not None:
            self._text_key(event)
            return
        if (self.selection is not None and self._drag is None and event.key() in
                (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)):
            self._nudge(event.key(), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            event.accept()
            return
        if event.key() == Qt.Key.Key_Shift:
            self._shift_changed()
        if event.key() == Qt.Key.Key_Alt and not event.isAutoRepeat():
            self._set_alt_pick(True)
        if event.key() == Qt.Key.Key_H and not event.modifiers() and not event.isAutoRepeat():
            self._set_peek(True)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat() and self.capture is not None and not self._selecting:
                self._space_held = True
                if self._panning is None:
                    self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def _shift_changed(self):
        """Shift went down or up: re-apply or drop the constraint on a shape
        being drawn or a handle being dragged, without waiting for a move."""
        if self.current is not None and self.current.shape is not None:
            self._update_shape_end()
        drag = self._drag
        if drag is not None and drag["kind"] == "resize" and drag["moved"] and "pos" in drag:
            self._annotation_move(drag["pos"])

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Shift:
            self._shift_changed()
        if event.key() == Qt.Key.Key_Alt and not event.isAutoRepeat():
            self._set_alt_pick(False)
        if event.key() == Qt.Key.Key_H and not event.isAutoRepeat():
            self._set_peek(False)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self._space_held = False
                if self._panning is None and not self._selecting:
                    self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _cancel_selection_state(self):
        self._selecting = False
        self._selection_anchor = None
        self._selection_point = None
        self.unsetCursor()
        self.update()

    # -- canvas view: zoom, frame, actual pixels -----------------------------
    #
    # Only the view changes (capture.scale and offset). Strokes stay in the
    # image's own coordinates and the canvas stays at the image's pixel
    # density, so exports are full resolution at any zoom.

    ZOOM_MIN, ZOOM_MAX = 0.05, 16.0

    def _zoom_anchor(self):
        p = self.mapFromGlobal(QCursor.pos())
        return QPointF(p) if self.rect().contains(p) else QPointF(self.width() / 2, self.height() / 2)

    def zoom_percent(self):
        """Zoom relative to actual pixels (one image pixel per screen pixel)."""
        if self.capture is None:
            return 100.0
        return self.capture.scale * self._window_dpr() / max(1e-6, self.capture.dpr) * 100.0

    def zoom_at(self, view_pos, factor):
        cap = self.capture
        if cap is None or self._grabbing or self._capturing:
            return
        doc = cap.to_doc(view_pos)
        scale = max(self.ZOOM_MIN, min(self.ZOOM_MAX, cap.scale * factor))
        self._set_view(scale, QPointF(view_pos.x() - doc.x() * scale, view_pos.y() - doc.y() * scale))

    def zoom_by(self, factor):
        self.zoom_at(self._zoom_anchor(), factor)

    def _set_view(self, scale, offset):
        cap = self.capture
        if cap is None:
            return
        cap.scale = scale
        cap.offset = offset
        self._toast(f"{self.zoom_percent():.0f}%", 700)
        self.update()

    def frame_capture(self):
        """Ctrl+0: the whole capture centered on the monitor."""
        if self.capture is None:
            return
        self._fit_capture()
        self._toast(f"Fit: {self.zoom_percent():.0f}%", 700)

    def actual_pixels(self):
        """Ctrl+1: one image pixel per screen pixel, around the cursor."""
        cap = self.capture
        if cap is None:
            return
        anchor = self._zoom_anchor()
        doc = cap.to_doc(anchor)
        scale = cap.dpr / self._window_dpr()
        d = self._window_dpr()
        self._set_view(scale, QPointF(capture_mod.snap(anchor.x() - doc.x() * scale, d),
                                      capture_mod.snap(anchor.y() - doc.y() * scale, d)))

    def wheelEvent(self, event):
        # Wheel zooms around the cursor, on a capture (a live screen can't
        # be zoomed). Trackpad pixel deltas zoom smoothly.
        pos = self._overlay_pos(event)
        if self.capture is None or self.pass_through or self._point_over_ui(pos):
            event.ignore()
            return
        dy = event.angleDelta().y()
        if dy:
            self.zoom_at(pos, 1.25 ** (dy / 120.0))
        event.accept()

    # -- on-canvas size and opacity (Alt+right-drag), like Photoshop ------------

    def _adjust_input(self, kind, view_pos):
        if kind == "press":
            if self.mode not in ("brush", "eraser") and self.mode not in SHAPES:
                return
            self._adjust = {"anchor": QPointF(view_pos), "size": self._active_size(),
                            "opacity": self._active_opacity()}
            self._move_cursor(self._to_doc(view_pos))
            self.setCursor(Qt.CursorShape.BlankCursor)
            self._show_size_preview()
        elif kind == "move" and self._adjust is not None:
            a = self._adjust
            dx = view_pos.x() - a["anchor"].x()
            dy = view_pos.y() - a["anchor"].y()
            # Sideways: size, by ratio (100 px doubles it). Up: more opaque.
            self._set_size(a["size"] * 2 ** (dx / 100.0))
            self._set_opacity(a["opacity"] * 100 - dy / 2.0)
            self._show_size_preview()
        elif kind == "release":
            self._adjust = None
            self.unsetCursor()
            self._size_preview_until = 0.0
            self.update()

    def _paint_adjust_hud(self, painter):
        a = self._adjust
        painter.save()
        text = f"{brushes.format_size(self._active_size())}   {int(round(self._active_opacity() * 100))}%"
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        painter.setFont(f)
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(text) + 16
        r = QRectF(a["anchor"].x() - w / 2, a["anchor"].y() + self._ring_diameter() / 2 + 10, w, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 23, 225))
        painter.drawRoundedRect(r, 6, 6)
        painter.setPen(QColor(C.TEXT))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    # -- temporary tools ------------------------------------------------------------

    def _set_alt_pick(self, held):
        """Holding Alt in a drawing tool turns the cursor into a picker:
        Alt+click samples the screen color and the tool stays."""
        held = held and self.mode in ("brush", "text") + SHAPES and self._text_edit is None
        if held == self._alt_pick:
            return
        self._alt_pick = held
        if held:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif not self._wants_pan() and not self._selecting:
            self._set_mode(self.mode)  # restores the tool's own cursor
        self.update()

    def _set_peek(self, held):
        """Hold H to see the artwork without annotations (before/after).
        View only: nothing changes and exports are unaffected."""
        if held == self._peek:
            return
        self._peek = held
        if held:
            self._toast("Annotations hidden while H is held", 60000)
        else:
            self.toast_label.hide()
        self.update()

    def focusOutEvent_reset_temporary(self):
        self._set_peek(False)
        self._set_alt_pick(False)
        if self._adjust is not None:
            self._adjust_input("release", QPointF())

    def _escape(self):
        """Esc backs out of whatever is in progress, one thing at a time,
        and otherwise hides to the tray. It never discards a capture or
        switches to full-monitor drawing; those take a deliberate action.
        (Text editing handles its own Esc: cancel the edit.)"""
        if self._grabbing:
            return
        if close_panels():
            return  # an open panel (brush picker, menu, colors) closes first
        if self._adjust is not None:
            self._adjust_input("release", QPointF())
            return
        if self._drag is not None:
            self._cancel_manipulation()
            return
        if self.current is not None and self.current.shape is not None:
            self.current = None
            self._live_layer = None
            self._shape_raw_end = None
            self.update()
            return
        if self.selection is not None:
            self._set_selection(None)
            return
        if self._selecting:
            self._cancel_selection_state()
            self._restore_stashed_session()
        self.dismiss()

    def _cancel_manipulation(self):
        """Put a moved or resized annotation back where it was."""
        self._drag = None
        self._floating = None
        self._text_dragging = False
        if self._hidden_stroke is not None:
            self._hidden_stroke = None
            self._rebuild_canvas()
        self.update()

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
        if self.capture is not None:
            # Thickness changes the canvas extent: rebuild it and recenter.
            # Color and opacity are fills only.
            if before != self._doc_outer():
                self._rebuild_canvas()
                self._fit_capture()
            self.update()
        elif self.region is not None:
            self.update(before.united(self.drawable_rect()).adjusted(-2, -2, 2, 2))

    def _sync_region_popup(self):
        if self._region_popup is not None and self._region_popup.isVisible():
            self._region_popup.sync()

    def _show_region_popup(self):
        self._refresh_region_button()
        if self._region_popup is None:
            self._region_popup = RegionPopup(self)
        self._region_popup.show_below(self.more_btn)  # the menu closes first

    # -- window / mode actions ---------------------------------------------

    def set_tray_available(self, available, hotkey_text=None):
        """Called by app.py once the tray icon exists. Esc and the toolbar's
        last button then hide to the tray instead of exiting."""
        self._tray_available = bool(available)
        if self._tray_available:
            shows = f"{hotkey_text} or the tray icon" if hotkey_text else "the tray icon"
            self.exit_btn.setIcon(icons.build_icon("hide"))
            self.exit_btn.setText("Hide to tray")
            self.exit_btn.setToolTip(f"Hide (Esc). {shows} shows it again")
        else:
            self.exit_btn.setIcon(icons.build_icon("exit"))
            self.exit_btn.setText("Exit")
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
        close_panels()
        self._commit_text()
        if not self.isVisible():
            return
        if self._selecting:
            self._cancel_selection_state()
        if self.current is not None:
            self._end_stroke()
        self._panning = None
        self._space_held = False
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

    def claim_foreground(self, reason):
        """Become the foreground window, and log whether that worked. Tablet
        drivers choose Windows Ink (pressure) or mouse emulation from the
        foreground application's settings, so until the overlay is
        foreground a pen can reach it as a plain mouse: the first strokes
        after showing it came without pressure until a click activated it."""
        if not self.isVisible() or self.pass_through:
            return False
        self._strokes_to_log = 5
        if QGuiApplication.platformName() != "windows":
            return False  # offscreen tests: no real window to bring forward
        ok = native_windows.claim_foreground(int(self.winId()))
        if ok:
            logger.info("Foreground: overlay (%s)", reason)
        else:
            _hwnd, _pid, name = native_windows.foreground_info()
            logger.warning("Foreground: still %s after %s; a pen may draw without pressure until the overlay is "
                           "clicked", name or "another window", reason)
        return ok

    def show_overlay(self):
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            self.claim_foreground("show")
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
            self.claim_foreground("show")
        logger.info("Overlay shown")

    def toggle_visibility(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_overlay()

    def toggle_pass_through(self):
        self._commit_text()
        if not self.isVisible():
            # From the tray while hidden: come back first, so the badge
            # isn't left floating over nothing.
            self.show_overlay()
        if self._selecting:
            self._cancel_selection_state()
        # A pan's release is ignored once clicks pass through.
        self._stop_panning()
        self.pass_through = not self.pass_through

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.pass_through)
        # The Qt attribute above doesn't reach the native WS_EX_TRANSPARENT
        # style for this window's flags, so clicks keep landing here. Set
        # the native style directly. See native_windows.py.
        ok = set_click_through(int(self.winId()), self.pass_through)
        if not ok and self._click_through_native_ok is not False:
            logger.warning("Couldn't set native click-through; relying on Qt's attribute only")
        self._click_through_native_ok = ok
        self.panel.setVisible(not self.pass_through and not self._ui_hidden)
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
            self.claim_foreground("pass-through off")

        # The backdrop layer checks self.pass_through itself every repaint
        # (see _paint_backdrop), so no canvas rebuild is needed here either.
        self.update()
        QApplication.processEvents()

    def undo(self):
        self._commit_text()
        if self.history.undo():
            self._rebuild_canvas()
            self._refresh_history_buttons()
            self.update()

    def redo(self):
        self._commit_text()
        if self.history.redo():
            self._rebuild_canvas()
            self._refresh_history_buttons()
            self.update()

    def clear(self):
        """Remove every annotation as one undoable step. The captured image,
        margin and backdrop stay."""
        self._cancel_text()
        if self.current is not None:
            self.current = None
            self._live_layer = None
        if len(self.history) == 0:
            self.update()
            return
        self._set_selection(None)
        self.history.change(list(self.history.strokes), [])
        self._rebuild_canvas()
        self._refresh_history_buttons()
        self.update()
        self.status_changed.emit("Cleared. Ctrl+Z brings it back")

    def _refresh_history_buttons(self):
        if self.selection is not None and not self.history.contains(self.selection):
            self.selection = None
            self._sync_text_bar()
        self.undo_btn.setEnabled(len(self.history.undo_stack) > 0)
        self.redo_btn.setEnabled(len(self.history.redo_stack) > 0)

    # -- stroke lifecycle --------------------------------------------------

    def _begin_stroke(self, pos, pressure, eraser=None):
        # pos is in stroke coordinates (see _to_doc).
        if self.pass_through:
            return
        if self.capture is not None:
            if not self._doc_outer().contains(QPointF(pos)):
                return
        elif self.region is not None and not self.drawable_rect().contains(QPointF(pos).toPoint()):
            return
        self.raw_pos = QPointF(pos)
        self.lazy_pos = QPointF(pos)
        self._move_cursor(QPointF(pos))
        self.pressure = pressure if pressure > 0 else C.DEFAULT_PRESSURE
        use_eraser = self.mode == "eraser" if eraser is None else (eraser or self.mode == "eraser")
        # Brush size is on-screen size; a fitted (shrunk) capture gets a
        # proportionally larger stroke in image pixels.
        shape = self.mode if self.mode in SHAPES and not use_eraser else None
        base = self.eraser_size if use_eraser else self.brush_size
        strength = self.eraser_opacity if use_eraser else self.opacity
        self.current = Stroke(self.color, base / self._view_scale(), strength, use_eraser, shape)
        self._live_layer = None
        self._live_layer_origin = None
        if shape is not None:
            # Two points, start and end, both at the press. No lazy brush.
            self.lazy_pos = None
            self._shape_raw_end = QPointF(pos)
            self._last_effective_pressure = brush.SHAPE_PRESSURE
            self.current.points = [(QPointF(pos), brush.SHAPE_PRESSURE), (QPointF(pos), brush.SHAPE_PRESSURE)]
            self.history.redo_stack.clear()
            self._refresh_history_buttons()
            return
        # Freehand, brush or eraser: libmypaint gets every input event with
        # its time step, pressure and tilt, and does its own dynamics. A
        # mouse (no pressure sensor) paints at full pressure.
        self.current.brush = self._stroke_spec(use_eraser)
        self.current.dtimes = [0.0]
        self.current.tilts = [self._tilt]
        self._mp_has_pressure = self._input_has_pressure
        first = self.pressure if self._mp_has_pressure else mypaint_engine.MOUSE_PRESSURE
        self._stroke_pressure = first
        self.current.points.append((QPointF(pos), first))
        self._mp_last_time = time.monotonic()
        self._mp = mypaint_engine.StrokePainter(self.current, self._canvas_dpr())
        self._feed_mypaint([(pos.x(), pos.y(), first, 0.0, *self._tilt)])
        self._note_stroke_pressure(first)
        self._stroke_log_start = max(0, len(self._input_log) - 4)
        self._stroke_foreground = (native_windows.is_foreground(int(self.winId()))
                                   if self.isVisible() and QGuiApplication.platformName() == "windows" else None)
        self.history.redo_stack.clear()
        self._refresh_history_buttons()

    def _add_point(self, pos, pressure):
        if self.pass_through or not self.current:
            return
        if self.current.shape is not None:
            self._move_cursor(QPointF(pos))
            self._shape_raw_end = QPointF(pos)
            self._update_shape_end()
            return
        if self._mp is None:
            return
        # The stabilizer (SMOOTH) moves a lazy point after the pen; every
        # event still goes to libmypaint with its time step, so its own
        # dynamics see a steady stream even while the lazy point rests.
        prev_cursor = self.cursor_pos
        self._move_cursor(QPointF(pos))
        if self.smoothing > 0.02:
            self._advance_lazy(QPointF(pos))
        else:
            self.lazy_pos = QPointF(pos)
        at = QPointF(self.lazy_pos)
        now = time.monotonic()
        dt = max(1e-4, now - self._mp_last_time)
        self._mp_last_time = now
        pr = (pressure if pressure > 0 else 0.0) if self._mp_has_pressure else mypaint_engine.MOUSE_PRESSURE
        self._stroke_pressure = pr
        self.current.points.append((at, pr))
        self.current.dtimes.append(dt)
        self.current.tilts.append(self._tilt)
        self._feed_mypaint([(at.x(), at.y(), pr, dt, *self._tilt)])
        self._note_stroke_pressure(pr)
        self._mark_dirty(prev_cursor, self.cursor_pos, at)

    def _advance_lazy(self, raw):
        """The stabilizer shared by all engines: a lazy point trails the pen
        on a string whose length grows with SMOOTH, and eases toward it.
        Returns False while the pen is still within the string's slack."""
        radius = (2 + self.smoothing * 44) / self._view_scale()
        dx = raw.x() - self.lazy_pos.x()
        dy = raw.y() - self.lazy_pos.y()
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= radius:
            return False
        pull = (dist - radius) / dist
        target = QPointF(self.lazy_pos.x() + dx * pull, self.lazy_pos.y() + dy * pull)
        ease = 0.72 - self.smoothing * 0.22
        self.lazy_pos = QPointF(
            self.lazy_pos.x() + (target.x() - self.lazy_pos.x()) * ease,
            self.lazy_pos.y() + (target.y() - self.lazy_pos.y()) * ease,
        )
        return True

    def _update_shape_end(self):
        """Re-place the live shape's end from the raw drag point and the
        current Shift state, and refresh its preview layer."""
        stroke = self.current
        if stroke is None or stroke.shape is None or self._shape_raw_end is None:
            return
        old = self._shape_dirty_rect()
        start = stroke.points[0][0]
        end = self._shape_raw_end
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            end = brush.constrain_end(stroke.shape, start, end)
        stroke.points[-1] = (QPointF(end), brush.SHAPE_PRESSURE)
        layer = brush.render_shape_layer(stroke, self._canvas_dpr())
        self._live_layer, self._live_layer_origin = layer if layer else (None, None)
        self.update(old.united(self._shape_dirty_rect()))

    def _shape_dirty_rect(self):
        """Window rect covering the live shape (arrowheads can reach past
        the dirty margin around its two points)."""
        if self.current is None or self.current.shape is None:
            return QRect()
        bounds = QRectF(brush.shape_bounds(self.current))
        if self.capture is not None:
            bounds = self.capture.transform().mapRect(bounds)
        return bounds.toAlignedRect().adjusted(-2, -2, 2, 2)

    def _feed_mypaint(self, events):
        changed = self._mp.add(events)
        if changed is None:
            return
        piece = self._mp.read(changed)
        if piece is not None:
            # The surface holds the stroke so far; a piece replaces what the
            # live layer had there.
            self._merge_live(*piece, replace=True)
            self._update_doc_rect(QRectF(brush.logical_rect(piece[1], piece[0])))

    def _update_doc_rect(self, rect):
        """Repaint a stroke-coordinate rect."""
        if self.capture is not None:
            rect = self.capture.transform().mapRect(rect)
        self.update(rect.toAlignedRect().adjusted(-2, -2, 2, 2))

    def _paint_live(self, painter):
        if self._live_layer is not None:
            painter.drawPixmap(self._live_layer_origin, self._live_layer)

    def _merge_live(self, new_pixmap, new_origin, replace=False):
        """Composite a freshly rendered piece onto the live stroke layer,
        growing the layer when the piece falls outside it. With replace the
        piece overwrites that area instead of blending over it."""
        dpr = self._canvas_dpr()
        new_rect = brush.logical_rect(new_origin, new_pixmap)
        if self._live_layer is None:
            self._live_layer, self._live_layer_origin = new_pixmap, new_origin
            return
        mode = (QPainter.CompositionMode.CompositionMode_Source if replace
                else QPainter.CompositionMode.CompositionMode_SourceOver)
        old_rect = brush.logical_rect(self._live_layer_origin, self._live_layer)
        if old_rect.contains(new_rect):
            painter = QPainter(self._live_layer)
            try:
                painter.setCompositionMode(mode)
                painter.drawPixmap(new_origin - self._live_layer_origin, new_pixmap)
            finally:
                painter.end()
        else:
            # Grow with headroom, so a stroke heading off the layer doesn't
            # reallocate and copy the whole layer on every event. On the
            # 4 px grid that keeps copies on whole device pixels.
            union = old_rect.united(new_rect)
            pad = 192
            union = QRect(union.left() - pad, union.top() - pad, union.width() + 2 * pad, union.height() + 2 * pad)
            union = QRect(union.left() // 4 * 4, union.top() // 4 * 4,
                          (union.width() + 7) // 4 * 4, (union.height() + 7) // 4 * 4)
            merged = brush.new_layer(union.size(), dpr)
            painter = QPainter(merged)
            try:
                painter.drawPixmap(old_rect.topLeft() - union.topLeft(), self._live_layer)
                painter.setCompositionMode(mode)
                painter.drawPixmap(new_rect.topLeft() - union.topLeft(), new_pixmap)
            finally:
                painter.end()
            self._live_layer, self._live_layer_origin = merged, union.topLeft()

    def _end_stroke(self):
        if self.current:
            finished = self.current
            if self._mp is not None:
                self._log_stroke(finished)
                # The live surface is the final stroke: cache it rather than
                # replaying every event on pen-up.
                layer = self._mp.layer()
                if layer is not None:
                    brushes.layer_cache.put(finished, self._canvas_dpr(), layer)
                else:
                    finished.points = []  # libmypaint painted nothing
                self._mp.close()
                self._mp = None
            shape_rect = self._shape_dirty_rect()
            self._shape_raw_end = None
            if finished.shape is not None and brush.shape_is_degenerate(finished, 2.0 / self._view_scale()):
                # A click without a drag: nothing to commit.
                finished.points = []
            self.current = None
            self.lazy_pos = None
            self._live_layer = None
            self._live_layer_origin = None
            if finished.points:
                self.history.commit(finished)
                self._commit_stroke_to_canvas(finished)
                self._refresh_history_buttons()
                self._mark_dirty(*(p for p, _ in finished.points))
                if finished.shape is not None:
                    self.update(shape_rect)
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
            if raw > 0.0 and (self._tablet_contact or event.type() == QEvent.Type.TabletPress):
                self._no_pressure_devices.discard(key)
                logger.info("Pen %s reports pressure again; using it", key)
            else:
                return C.DEFAULT_PRESSURE
        dev = event.pointingDevice()
        self._input_has_pressure = False
        if dev is not None and not (dev.capabilities() & QInputDevice.Capability.Pressure):
            self._mark_no_pressure(key, "driver reports no pressure capability")
            return C.DEFAULT_PRESSURE
        # A pen with a dead pressure sensor reports 0 for a whole stroke;
        # only then fall back to fixed pressure. Zeros while the tip lands
        # (common with Windows Ink) don't count, as long as the stroke
        # reports pressure at some point.
        etype = event.type()
        if etype == QEvent.Type.TabletPress:
            self._zero_pressure_run = 0
            self._stroke_events = 0
        if self._tablet_contact or etype == QEvent.Type.TabletPress:
            self._stroke_events = getattr(self, "_stroke_events", 0) + 1
            if raw <= 0.0:
                self._zero_pressure_run += 1
        if etype == QEvent.Type.TabletRelease:
            if self._zero_pressure_run >= _ZERO_PRESSURE_EVENTS and                     self._zero_pressure_run >= getattr(self, "_stroke_events", 0):
                self._mark_no_pressure(key, "pressure stayed at 0 for a whole stroke")
            self._zero_pressure_run = 0
        self._input_has_pressure = True
        return max(0.01, self._curved(raw))

    def _mark_no_pressure(self, key, why):
        if key not in self._no_pressure_devices:
            self._no_pressure_devices.add(key)
            logger.warning("Pen %s: %s; drawing with default pressure. If this tablet normally has "
                           "pressure, enable Windows Ink for this app in the tablet driver settings.", key, why)

    def _handle_tablet_event(self, event):
        event_type = event.type()
        raw_pressure = float(event.pressure())
        if event.type() == QEvent.Type.TabletPress:
            close_panels()
        pressure = self._tablet_pressure(event)
        self._tilt = mypaint_engine.tilt(event.xTilt(), event.yTilt())
        self._diag_event("tablet", event, raw_pressure, pressure)
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
                if self._panning is not None:
                    # A pen pan that ended over the toolbar: end it here, or
                    # pen hover would keep moving the capture.
                    self._pan_input("release", pos)
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

        if self._wants_pan() and self.current is None:
            kind = {QEvent.Type.TabletPress: "press", QEvent.Type.TabletMove: "move",
                    QEvent.Type.TabletRelease: "release"}.get(event_type)
            if kind == "press":
                self._tablet_contact = True
            elif kind == "release":
                self._tablet_contact = False
            if kind is not None and (kind == "press" or self._panning is not None):
                self._pan_input(kind, pos)
            event.accept()
            return

        if self._adjust is not None or (event_type == QEvent.Type.TabletPress
                                        and event.button() == Qt.MouseButton.RightButton
                                        and event.modifiers() & Qt.KeyboardModifier.AltModifier):
            kind = {QEvent.Type.TabletPress: "press", QEvent.Type.TabletMove: "move",
                    QEvent.Type.TabletRelease: "release"}.get(event_type)
            if kind is not None:
                self._adjust_input(kind, pos)
            event.accept()
            return

        pos = self._to_doc(pos)
        if event_type == QEvent.Type.TabletPress:
            is_alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            if self.mode == "eyedropper" or is_alt:
                self._pick_color_and_restore_mode(event.globalPosition())
                event.accept()
                return
            if is_eraser_tip:
                self._commit_text()
            elif self._annotation_press(pos, event.modifiers()):
                self._tablet_contact = True
                event.accept()
                return
            self._tablet_contact = True
            self._begin_stroke(pos, pressure, eraser=is_eraser_tip)
        elif event_type == QEvent.Type.TabletMove:
            if self._tablet_contact and self.current is not None:
                self._add_point(pos, pressure)
            elif self._tablet_contact and (self._drag is not None or self._text_dragging):
                self._annotation_move(pos)
            elif not self._tablet_contact and self.current is None:
                self._move_cursor(pos)
        elif event_type == QEvent.Type.TabletRelease:
            self._tablet_contact = False
            self._annotation_release()
            self._end_stroke()

        event.accept()

    def _is_tablet_synthesized(self):
        # Some Wacom/Windows configurations emit a compatibility mouse event
        # immediately after the real tablet event. Ignore it so one pen
        # stroke can't become two history entries.
        return self._tablet_contact or (time.monotonic() - self._last_tablet_time) < C.TABLET_MOUSE_GUARD_S

    def mousePressEvent(self, event):
        self._diag_event("mouse", event)
        close_panels()
        try:
            if self._is_tablet_synthesized():
                event.accept()
                return
            pos = self._overlay_pos(event)
            if self.pass_through:
                event.ignore()
                return
            if (event.button() == Qt.MouseButton.RightButton and event.modifiers() & Qt.KeyboardModifier.AltModifier
                    and not self._point_over_ui(pos)):
                self._adjust_input("press", pos)
                event.accept()
                return
            if event.button() == Qt.MouseButton.MiddleButton and self.capture is not None:
                self._mid_pan = True
                self._pan_input("press", pos)
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton and not self._point_over_ui(pos):
                if self._selecting:
                    self._selection_input("press", pos)
                    event.accept()
                    return
                if self._wants_pan():
                    self._pan_input("press", pos)
                    event.accept()
                    return
                is_alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                if self.mode == "eyedropper" or is_alt:
                    self._pick_color_and_restore_mode(event.globalPosition())
                    event.accept()
                    return
                if self._annotation_press(self._to_doc(pos), event.modifiers()):
                    event.accept()
                    return
                self._input_has_pressure = False
                self._tilt = (0.0, 0.0)
                self._begin_stroke(self._to_doc(pos), 0.65)
                event.accept()
        except Exception:
            logger.exception("Error handling mouse press")
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons():
            self._diag_event("mouse", event)
        try:
            if self._is_tablet_synthesized():
                event.accept()
                return
            if self.pass_through:
                event.ignore()
                return
            pos = self._overlay_pos(event)
            if self._adjust is not None:
                self._adjust_input("move", pos)
            elif self._selecting:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self._selection_input("move", pos)
            elif self._panning is not None:
                self._pan_input("move", pos)
            elif self._drag is not None or self._text_dragging:
                self._annotation_move(self._to_doc(pos))
            elif event.buttons() & Qt.MouseButton.LeftButton and not self._point_over_ui(pos):
                self._add_point(self._to_doc(pos), 0.65)
            else:
                self._move_cursor(self._to_doc(pos))
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
            if self._adjust is not None and event.button() == Qt.MouseButton.RightButton:
                self._adjust_input("release", self._overlay_pos(event))
                event.accept()
                return
            if self._mid_pan and event.button() == Qt.MouseButton.MiddleButton:
                self._mid_pan = False
                self._pan_input("release", self._overlay_pos(event))
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                if self._selecting:
                    self._selection_input("release", self._overlay_pos(event))
                elif self._panning is not None:
                    self._pan_input("release", self._overlay_pos(event))
                else:
                    self._annotation_release()
                    self._end_stroke()
                event.accept()
        except Exception:
            logger.exception("Error handling mouse release")
            event.accept()

    # -- painting -----------------------------------------------------------

    def _dirty_margin(self):
        # Generously covers stroke width, the brush-size cursor ring, and
        # the smoothing guide line so a dirty rect never clips live drawing.
        return max(self.brush_size, self.eraser_size) * 1.6 + 12

    def _mark_dirty(self, *points):
        # Points are in stroke coordinates; repaint where they show.
        pts = [self._to_view(p) for p in points if p is not None]
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
        if stroke.shape is not None:
            layer = brush.render_shape_layer(stroke, self._canvas_dpr())
            if layer is not None:
                pixmap, origin = layer
                painter.save()
                painter.setOpacity(max(0.01, min(1.0, stroke.opacity)))
                painter.drawPixmap(origin, pixmap)
                painter.restore()
            return
        # Either engine flattens the stroke on its own layer (cached for dab
        # strokes), blended once at stroke.opacity so it never doubles up
        # where it overlaps itself. An eraser's layer is its coverage mask,
        # removed from what is below with DestinationOut.
        layer = brushes.freehand_layer(stroke, self._canvas_dpr())
        if layer is None:
            return
        pixmap, origin = layer
        if stroke.eraser:
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            painter.setOpacity(max(0.01, min(1.0, stroke.opacity)))
            painter.drawPixmap(origin, pixmap)
            painter.restore()
            return
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
        fill = self._backdrop_fill()
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if self.region is None:
            painter.fillRect(self.rect(), fill or floor)
        else:
            painter.fillRect(self.rect(), floor)
            if fill is not None:
                painter.fillRect(self.region, fill)
            if self.margin_enabled and not self.pass_through:
                # One shape (outer minus region), not four bands: separate
                # rects each round their own edges at fractional scales and
                # left empty pixel seams where they met at the corners.
                band = region_mod.margin_color(self.margin_color, self.margin_opacity)
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.OddEvenFill)
                path.addRect(QRectF(self.drawable_rect()))
                path.addRect(QRectF(self.region))
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                painter.fillPath(path, band)
        painter.restore()

    def _backdrop_fill(self, ignore_pass_through=False):
        if self.backdrop is None or (self.pass_through and not ignore_pass_through):
            return None
        if self.backdrop == "white":
            return QColor(255, 255, 255, int(self.backdrop_opacity * 255))
        return QColor(0, 0, 0, int(self.backdrop_opacity * 255))

    def _paint_capture_layers(self, painter):
        """Everything under the strokes during a capture, each its own
        layer: dimmed surround, margin, image, backdrop wash.

        The margin and image are painted into whole device-pixel rects from
        one edge-rounding rule (capture.view_device_rects), with
        antialiasing off. They share their edges exactly, so no pixel along
        an edge or corner is part-image, part-margin or empty, at any fit
        scale or monitor scale."""
        cap = self.capture
        wdpr = self._window_dpr()
        outer_dev, image_dev = cap.view_device_rects(self._margin_px(), wdpr)
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if self.pass_through:
            # Clicks go to the apps underneath, so show them: no surround,
            # image or margin, only the strokes (as with a drawing region).
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
            painter.restore()
            return
        painter.fillRect(self.rect(), capture_mod.SURROUND)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.scale(1.0 / wdpr, 1.0 / wdpr)  # device pixels from here
        resampled = image_dev.size() != cap.image.size()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, resampled)
        if self.margin_enabled:
            painter.fillRect(QRectF(outer_dev), region_mod.margin_color(self.margin_color, self.margin_opacity))
        painter.drawPixmap(QRectF(image_dev), cap.image, QRectF(cap.image.rect()))
        fill = self._backdrop_fill()
        if fill is not None:
            painter.fillRect(QRectF(image_dev), fill)
        painter.restore()

    def _window_dpr(self):
        return max(1.0, float(self.devicePixelRatioF()))

    def _capture_clip(self):
        """Window-coordinate clip for strokes: the margin's device rect."""
        outer_dev, _ = self.capture.view_device_rects(self._margin_px(), self._window_dpr())
        d = self._window_dpr()
        return QRectF(outer_dev.x() / d, outer_dev.y() / d, outer_dev.width() / d, outer_dev.height() / d)

    def _commit_stroke_to_canvas(self, stroke):
        painter = QPainter(self._canvas)
        try:
            painter.translate(-self._canvas_origin)
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
        #
        # During a capture it covers the image plus margin in document
        # coordinates, at the image's pixel density (the export resolution).
        dpr = self._target_dpr()
        if getattr(self, "capture", None) is not None:
            outer = self._doc_outer()
            size = capture_mod.canvas_size(outer)
            self._canvas_origin = outer.topLeft()
        else:
            size = self.size()
            self._canvas_origin = QPointF(0, 0)
        canvas = brush.new_layer(size, dpr)
        if canvas.isNull() and dpr > 1.0:
            logger.warning("Couldn't allocate a %gx canvas for %s; falling back to 1x", dpr, size)
            canvas = brush.new_layer(size, 1.0)
        if canvas.isNull():
            logger.error("Couldn't allocate the drawing canvas (%s). Try --screens primary.", size)
        self._canvas = canvas
        painter = QPainter(self._canvas)
        try:
            painter.translate(-self._canvas_origin)
            hidden = getattr(self, "_hidden_stroke", None)
            absorbers = {}
            for stroke in self.history:
                for eraser, _t in stroke.erasures:
                    absorbers.setdefault(id(eraser), []).append(stroke)
            drawn = set()
            for stroke in self.history:
                if stroke is hidden:
                    continue
                self._draw_stroke(painter, stroke)
                drawn.add(id(stroke))
                if stroke.eraser and id(stroke) in absorbers:
                    below = [a for a in absorbers[id(stroke)] if id(a) in drawn]
                    if below:
                        self._restore_under_eraser(painter, stroke, below)
        finally:
            painter.end()

    def _restore_under_eraser(self, painter, eraser, annotations):
        """Inside `eraser`, put back the annotations below it that carry it
        in their own erasures (selection.absorb). They draw their erased
        parts where they are now, so this eraser must not also clear them
        where it was drawn. Exact under antialiasing: Clear left
        dst * (1 - coverage), and this adds annotation * coverage."""
        dpr = self._canvas_dpr()
        found = brushes.freehand_layer(eraser, dpr)
        if found is None:
            return
        mask, origin = found
        bounds = brush.logical_rect(origin, mask)
        layer = brush.new_layer(bounds.size(), dpr)
        p = QPainter(layer)
        try:
            p.translate(-QPointF(bounds.topLeft()))
            for stroke in annotations:
                self._draw_stroke(p, stroke)
            p.resetTransform()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            p.setOpacity(max(0.01, min(1.0, eraser.opacity)))
            p.drawPixmap(0, 0, mask)
        finally:
            p.end()
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        painter.drawPixmap(bounds.topLeft(), layer)
        painter.restore()

    def _target_dpr(self):
        """Pixel density for the canvas: the monitor's scale factor, unless
        that would make the canvas unreasonably large."""
        if getattr(self, "capture", None) is not None:
            dpr = max(1.0, self.capture.dpr)
            size = capture_mod.canvas_size(self._doc_outer())
        else:
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
            if self._grabbing:
                # Nothing of ours in the screen grab for a new capture; the
                # 1/255 floor keeps the window hit-testable.
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
                return
            clipped = self.capture is not None or self.region is not None
            if self.capture is not None:
                self._paint_capture_layers(painter)
                painter.save()
                # Clip in window space to the margin's own device rect, then
                # switch to document coordinates for the strokes.
                painter.setClipRect(self._capture_clip())
                painter.setTransform(self.capture.transform(), combine=True)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.capture.scale != 1.0)
            else:
                self._paint_backdrop(painter)
                if self.region is not None:
                    painter.save()
                    painter.setClipRect(self.drawable_rect())

            live_eraser = (
                self.current is not None and self.current.eraser
                and self._live_layer is not None
            )
            if self._peek:
                pass  # H held: the artwork alone, for a before/after look
            elif live_eraser:
                # The live eraser preview should reveal the backdrop through
                # the drawing layer only. A plain DestinationOut against
                # this shared painter would punch through the backdrop too,
                # since by then both are pixels on the same surface.
                # Composite canvas plus live mask in an isolated buffer
                # scoped to the dirty rect, then draw that over the
                # backdrop. Stroke coordinates throughout (the painter
                # carries any capture transform).
                dirty = event.rect()
                if self.capture is not None:
                    inverse, _ok = self.capture.transform().inverted()
                    dirty = inverse.mapRect(QRectF(dirty)).toAlignedRect().adjusted(-1, -1, 1, 1)
                temp = brush.new_layer(dirty.size(), self._canvas_dpr())
                tp = QPainter(temp)
                try:
                    tp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    tp.translate(-dirty.topLeft())
                    tp.drawPixmap(self._canvas_origin, self._canvas)
                    tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
                    tp.setOpacity(max(0.01, min(1.0, self.current.opacity)))
                    self._paint_live(tp)
                finally:
                    tp.end()
                painter.drawPixmap(dirty.topLeft(), temp)
            else:
                painter.drawPixmap(self._canvas_origin, self._canvas)
                if self.current and self._live_layer is not None:
                    # Brush preview: SourceOver only adds color on top, so
                    # it can't reach through to the backdrop the way the
                    # eraser does. No buffer needed.
                    painter.save()
                    painter.setOpacity(max(0.01, min(1.0, self.current.opacity)))
                    self._paint_live(painter)
                    painter.restore()

            if self._floating is not None and not self._peek:
                self._paint_floating(painter, self._floating)
            if self._text_edit is not None and not self._capturing and not self._peek:
                self._paint_live_text(painter)

            if clipped:
                painter.restore()
            if (self.selection is not None and self._text_edit is None and not self._capturing
                    and not self.pass_through and not self._peek):
                self._paint_selection(painter)
            if self._adjust is not None and not self._capturing:
                self._paint_adjust_hud(painter)
            if self.show_input_diag and not self._capturing:
                self._paint_input_diag(painter)
            if self.region is not None and not self._capturing and not self.pass_through:
                self._paint_region_frame(painter, self.region)
            if self._selecting and self._selection_anchor is not None and not self._capturing:
                rect = region_mod.selection_rect(self._selection_anchor, self._selection_point, self.rect())
                self._paint_region_frame(painter, rect, selecting=True)

            # Not while capturing: a real mouse move during the capture delay
            # sets cursor_pos again and would put the ring in the screenshot.
            self._cursor_drawn = None
            if (not self.pass_through and self.cursor_pos and not self._selecting and not self._capturing
                    and not self._wants_pan() and self.mode not in ("text", "select")
                    and self.brush_cursor != "hidden"):
                self._cursor_drawn = self._cursor_rect()
                painter.save()
                # Ring and guide in window coordinates, at on-screen size.
                cursor_view = self._to_view(self.cursor_pos)
                lazy_view = self._to_view(self.lazy_pos) if self.lazy_pos else None
                d = self._ring_diameter()
                if (time.monotonic() < self._size_preview_until and self.current is None
                        and self.mode in ("brush", "eraser")):
                    self._paint_size_preview(painter, cursor_view, d)
                if self.mode == "eraser":
                    ring_color = QColor(255, 255, 255, 210)
                else:
                    ring_color = QColor(self.color)
                    ring_color.setAlpha(220)
                painter.setPen(QPen(ring_color, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if self.brush_cursor == "crosshair":
                    arm = self._CROSSHAIR / 2
                    painter.setPen(QPen(QColor(0, 0, 0, 150), 3))
                    painter.drawLine(QPointF(cursor_view.x() - arm, cursor_view.y()),
                                     QPointF(cursor_view.x() + arm, cursor_view.y()))
                    painter.drawLine(QPointF(cursor_view.x(), cursor_view.y() - arm),
                                     QPointF(cursor_view.x(), cursor_view.y() + arm))
                    painter.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
                    painter.drawLine(QPointF(cursor_view.x() - arm, cursor_view.y()),
                                     QPointF(cursor_view.x() + arm, cursor_view.y()))
                    painter.drawLine(QPointF(cursor_view.x(), cursor_view.y() - arm),
                                     QPointF(cursor_view.x(), cursor_view.y() + arm))
                else:
                    painter.drawEllipse(cursor_view, d / 2, d / 2)
                if lazy_view is not None and self.smoothing > 0.02:
                    painter.setPen(QPen(QColor(255, 255, 255, 95), 1))
                    painter.drawLine(lazy_view, cursor_view)
                painter.restore()
        finally:
            painter.end()

    _CROSSHAIR = 16

    def _cursor_rect(self):
        """Window rect the brush cursor covers now: the ring (or crosshair),
        the size preview, and the stabilizer guide to the lazy point."""
        if self.cursor_pos is None:
            return None
        c = self._to_view(self.cursor_pos)
        r = max(self._ring_diameter(), self._CROSSHAIR) / 2 + 4
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        if self.lazy_pos is not None and self.smoothing > 0.02:
            lz = self._to_view(self.lazy_pos)
            rect = rect.united(QRectF(lz.x() - 2, lz.y() - 2, 4, 4))
        return rect.toAlignedRect()

    def _move_cursor(self, doc_pos):
        """Move the brush cursor: repaint where it was last drawn and where
        it goes, so only one is ever visible."""
        old = self._cursor_drawn
        self.cursor_pos = doc_pos
        if old is not None:
            self.update(old.adjusted(-2, -2, 2, 2))
        new = self._cursor_rect()
        if new is not None:
            self.update(new.adjusted(-2, -2, 2, 2))

    def _set_brush_cursor(self, style):
        if style in C.BRUSH_CURSORS:
            self.brush_cursor = style
            if hasattr(self, "cursor_buttons"):
                for name, b in self.cursor_buttons.items():
                    b.setChecked(name == style)
            self._save_settings()
            self.update()

    def _ring_diameter(self):
        """On-screen diameter of the brush ring: the size under the pen
        while drawing (with pressure-driven size), full size hovering."""
        size = self._active_size()
        stroke = self.current
        if stroke is not None and mypaint_engine.is_spec(stroke.brush):
            return brushes.diameter(stroke.brush, size, self._stroke_pressure)
        if self.mode in SHAPES:
            return brush.width_for(size, self._last_effective_pressure)
        return size

    def _paint_size_preview(self, painter, center, d):
        """The brush as it will paint at full pressure: a real libmypaint
        dab, at its size."""
        painter.save()
        spec = self._stroke_spec(self.mode == "eraser")
        color = QColor(255, 255, 255) if self.mode == "eraser" else self.color
        pm = brush_preview.dab(spec, color, d, self._window_dpr())
        if pm is not None:
            painter.setOpacity(0.6 if self.mode == "eraser" else self.opacity)
            r = pm.deviceIndependentSize()
            painter.drawPixmap(QPointF(center.x() - r.width() / 2, center.y() - r.height() / 2), pm)
        painter.restore()

    # -- input diagnostic -------------------------------------------------------------

    def toggle_input_diag(self):
        self.show_input_diag = not self.show_input_diag
        if hasattr(self, "diag_btn"):
            self.diag_btn.setChecked(self.show_input_diag)
        self.status_changed.emit("Input diagnostic on (Ctrl+Shift+I)" if self.show_input_diag
                                 else "Input diagnostic off")
        self.update()

    def _note_device(self, event, via):
        """Log each input device once, as Qt reports it: the first thing to
        look at when a pen draws without pressure."""
        try:
            dev = event.pointingDevice()
            name = dev.name() if dev is not None else "?"
            key = (via, name, dev.type().name if dev else "?", dev.pointerType().name if dev else "?")
        except Exception:
            return
        if key in self._seen_devices:
            return
        self._seen_devices.add(key)
        caps = []
        try:
            c = dev.capabilities()
            for flag in ("Pressure", "XTilt", "YTilt", "Rotation", "TangentialPressure", "MouseEmulation"):
                if c & getattr(QInputDevice.Capability, flag):
                    caps.append(flag)
        except Exception:
            pass
        logger.info("Input seen via %s events: device %r type=%s pointer=%s caps=%s", via, key[1], key[2], key[3],
                    "+".join(caps) or "none")

    def _diag_event(self, via, event, raw=None, processed=None):
        self._note_device(event, via)
        try:
            dev = event.pointingDevice()
            dev_name = dev.name() if dev is not None else "?"
            pointer = dev.pointerType().name if dev is not None else "?"
            if pointer in ("Pen", "Eraser"):
                self._pen_seen = True
            self._input_log.append((time.monotonic(), via, event.type().name, dev_name, pointer,
                                    raw, event.source().name if via == "mouse" else ""))
        except Exception:
            pass
        if not self.show_input_diag:
            return
        try:
            dev = event.pointingDevice()
            caps = dev.capabilities() if dev is not None else 0
            d = self._diag
            d["via"] = via
            d["device"] = f"{dev.name() if dev else '?'} ({dev.type().name if dev else '?'}, " \
                          f"{dev.pointerType().name if dev else '?'})"
            d["pressure_cap"] = bool(caps & QInputDevice.Capability.Pressure) if dev is not None else False
            d["event"] = event.type().name
            if via == "mouse":
                d["source"] = event.source().name
            if raw is not None:
                d["raw"] = raw
                d["processed"] = processed
                d["tilt"] = (event.xTilt(), event.yTilt())
            d[via + "_count"] = d.get(via + "_count", 0) + 1
            self.update(self._diag_rect())
        except Exception:
            logger.debug("input diagnostic", exc_info=True)

    def _log_stroke(self, stroke):
        """One line per stroke for the first strokes after each show, and
        for any stroke that came as mouse input after a pen has been seen
        (the case where pressure was missing), with its event sequence."""
        if not stroke.points:
            return
        via = "tablet" if self._mp_has_pressure else "mouse"
        pressures = [pr for _p, pr in stroke.points]
        suspicious = via == "mouse" and self._pen_seen
        if not suspicious and self._strokes_to_log <= 0:
            return
        self._strokes_to_log -= 1
        fg = {True: "yes", False: "NO", None: "?"}[self._stroke_foreground]
        logger.info("Stroke via %s: %d points, pressure %.2f..%.2f, overlay foreground=%s, %s%s", via,
                    len(pressures), min(pressures), max(pressures), fg, self._stroke_timing(),
                    "  [pen seen, but no pressure]" if suspicious else "")
        if suspicious or self.show_input_diag:
            events = list(self._input_log)[self._stroke_log_start:]
            t0 = events[0][0] if events else 0
            for t, v, kind, dev, pointer, raw, src in events[:24]:
                logger.info("    %+6.0f ms  %-6s %-22s %-14s %-7s %s %s", (t - t0) * 1000, v, kind, dev, pointer,
                            "" if raw is None else f"p={raw:.3f}", src)
        if self.show_input_diag:
            self._diag["last_stroke"] = f"via {via}, pressure {min(pressures):.2f}..{max(pressures):.2f}, " \
                                        f"foreground {fg}"

    def _stroke_timing(self):
        """How the input arrived during the stroke: delay from press to the
        first movement, and the largest gap between events. A long first
        delay or gap with fast drawing means input was held back before it
        reached the app."""
        events = [e for e in list(self._input_log)[self._stroke_log_start:]
                  if e[2] in ("TabletPress", "TabletMove", "MouseButtonPress", "MouseMove")]
        if len(events) < 2:
            return "timing n/a"
        times = [e[0] for e in events]
        gaps = [b - a for a, b in zip(times, times[1:])]
        rate = (len(times) - 1) / max(1e-6, times[-1] - times[0])
        return (f"first move after {gaps[0] * 1000:.0f} ms, largest gap {max(gaps) * 1000:.0f} ms, "
                f"{rate:.0f} events/s")

    def _note_stroke_pressure(self, pr):
        if self.show_input_diag:
            self._diag["stroke_pressure"] = pr
            self.update(self._diag_rect())

    def _foreground_text(self):
        if native_windows.is_foreground(int(self.winId())):
            return "this overlay"
        _hwnd, _pid, name = native_windows.foreground_info()
        return f"{name or 'another window'}  (the tablet driver follows that app's pen settings)"

    def _diag_rect(self):
        local = self._anchor_local_rect()
        return QRect(local.x() + 16, local.y() + local.height() - 240, 560, 224)

    def _paint_input_diag(self, painter):
        d = self._diag
        size = self._active_size()
        spec = self.current.brush if (self.current is not None and self.current.brush is not None) \
            else self._stroke_spec(self.mode == "eraser")
        pr = d.get("stroke_pressure", 1.0)
        size_p = getattr(spec, "size_pressure", False)
        lines = [
            "INPUT DIAGNOSTIC  (Ctrl+Shift+I)",
            f"Last event   {d.get('event', '-')} via {d.get('via', '-')}"
            + (f"  [{d.get('source')}]" if d.get('via') == 'mouse' else ""),
            f"Device       {d.get('device', '-')}",
            f"Pressure cap {'yes' if d.get('pressure_cap') else 'NO'}    tablet events {d.get('tablet_count', 0)}"
            f"   mouse events {d.get('mouse_count', 0)}",
            f"Raw pressure {d['raw']:.3f}   processed {d['processed']:.3f} ({self.pressure_curve})"
            if "raw" in d else "Raw pressure -   (no tablet events yet)",
            f"Tilt         x {d['tilt'][0]:+.0f}°  y {d['tilt'][1]:+.0f}°" if "tilt" in d else "Tilt         -",
            f"Stroke       pressure {pr:.3f}  ->  diameter {brushes.diameter(spec, size, pr):.1f}px of {size:g}px"
            f"   (size by pressure {'on' if size_p else 'off'})",
            f"Last stroke  {d.get('last_stroke', '-')}",
            f"Foreground   {self._foreground_text()}",
        ]
        if d.get("mouse_count", 0) and not d.get("tablet_count", 0):
            lines.append("Pen arriving as a mouse: enable Windows Ink for this app in the tablet driver.")
        r = QRectF(self._diag_rect())
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 23, 230))
        painter.drawRoundedRect(r, 8, 8)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(12)
        painter.setFont(f)
        painter.setPen(QColor(C.TEXT))
        y = r.top() + 20
        for i, line in enumerate(lines):
            if i == 0:
                painter.setPen(QColor(C.TEXT_DIM))
            painter.drawText(QPointF(r.left() + 12, y), line)
            painter.setPen(QColor(C.TEXT))
            y += 21
        painter.restore()

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
        """F9. A saved screenshot opens in the default image viewer and the
        overlay hides to the tray with everything kept; the hotkey brings
        the session back. A failed one leaves the overlay up and says so."""
        self._commit_text()
        if self._capturing or self._grabbing:
            return
        if self.capture is not None:
            # Rendered from the layers at the image's resolution, not
            # grabbed from the screen: no desktop, no fit scaling, and no
            # toolbar or other chrome (they're never part of the layers).
            out = None
            try:
                out = self._save_and_report(self.capture_export())
            except Exception:
                logger.exception("Error exporting capture")
                self.status_changed.emit("Screenshot failed, see log")
            if out is not None:
                self._after_screenshot_saved(out)
            return
        try:
            # Everything of ours that isn't the drawing: toolbar, toast, hover
            # tip, popups, and the pass-through badge (its own window).
            # The toolbar's own hidden flag, not isVisible(): from the tray
            # while the overlay is hidden, isVisible() is False and the
            # toolbar would never come back.
            chrome = {
                "panel": not self.panel.isHidden(),
                "toast": self.toast_label.isVisible(),
                "badge": self._pass_through_badge.isVisible(),
            }
            cursor_pos = QCursor.pos()
            self.panel.hide()
            self.toast_label.hide()
            self._hover_tip_timer.stop()
            self.hover_tip.hide()
            self._pass_through_badge.hide()
            for popup in (self._region_popup, self._color_popup):
                if popup is not None and popup.isVisible():
                    popup.close()
            saved_cursor = self.cursor_pos
            self.cursor_pos = None
            self._capturing = True
            self.update()
            QApplication.processEvents()
            QTimer.singleShot(100, lambda: self._finish_capture(chrome, saved_cursor, cursor_pos))
        except Exception:
            logger.exception("Failed to start screenshot capture")
            self._capturing = False
            self.status_changed.emit("Screenshot failed, see log")

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

    def _finish_capture(self, chrome, saved_cursor, cursor_pos):
        out = None
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
            out = self._save_and_report(pixmap)
        except Exception:
            logger.exception("Error finishing screenshot capture")
            self.status_changed.emit("Screenshot failed, see log")
        finally:
            # Put the chrome back first, so a restored session has it.
            self._capturing = False
            self.cursor_pos = saved_cursor
            if chrome["panel"] and not self.pass_through:
                self.panel.show()
            if chrome["toast"] and out is None:
                self.toast_label.show()
            if chrome["badge"] and self.pass_through:
                self._position_pass_through_badge()
                self._pass_through_badge.show()
            self.update()
            if out is not None:
                self._after_screenshot_saved(out)

    def _save_and_report(self, pixmap):
        """Save; returns the path, or None after reporting the failure."""
        if pixmap is None or pixmap.isNull():
            self.status_changed.emit("Screenshot failed, see log")
            return None
        out = self._save_screenshot(pixmap, f"crit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        if out is not None:
            logger.info("Saved screenshot to %s", out)
            preferred = paths.current().screenshot_dir_candidates(self._screenshot_dir_override)[0]
            if out.parent == preferred:
                self.status_changed.emit(f"Saved {out.name}")
            else:
                self.status_changed.emit(f"Saved to {out.parent}")
        else:
            self.status_changed.emit("Screenshot failed, see log")
        return out

    # How long after opening the viewer to open Explorer, so Explorer's
    # window arrives last and ends up in front for drag-and-drop. Covers a
    # typical viewer start; a slower cold start can still land on top.
    EXPLORER_AFTER_VIEWER_MS = 1200

    def _after_screenshot_saved(self, out):
        """Hide to the tray (keeping the canvas, strokes, margin and undo
        history), open the image in the default viewer, then Explorer with
        it selected, in front. The next show resumes this session instead
        of starting a new selection. Without a tray the overlay stays up:
        there'd be no way back to it."""
        if self._tray_available:
            was_visible = self.isVisible()
            self._resume_on_show = True
            self.hide_to_tray()
            if was_visible:
                self.hidden_to_tray.emit()
        if self._open_in_viewer(out):
            QTimer.singleShot(self.EXPLORER_AFTER_VIEWER_MS, lambda: self._reveal_in_explorer(out))
        else:
            self._reveal_in_explorer(out)

    def _open_in_viewer(self, path):
        """The default app for .png, through the shell (os.startfile), the
        same way app.py opens diagnostics reports. True if it launched."""
        native_windows.allow_foreground_handoff()
        try:
            os.startfile(str(path))  # noqa: S606 - user-initiated, our own screenshot
            return True
        except Exception:
            logger.warning("Couldn't open %s in the default viewer", path, exc_info=True)
            return False

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
        # Still allowed to hand over the foreground: F9 was the last input
        # this session received, unless the user has clicked elsewhere
        # since, in which case Explorer opens behind and flashes.
        native_windows.allow_foreground_handoff()
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
