"""`--selftest`: start for real, exercise the overlay without user input,
report, exit.

Used by the build script against the freshly built exe, and useful on a
target machine ("does it work here?") without anyone having to draw. Shows
the overlay for a few seconds and, with several monitors, moves it across
each one in turn. Never writes screenshots; brush settings are restored to
what they were. (An older settings file does get its one-time version
migration, as on any launch.)

What it can't check: real pen input, how things look, and real monitor
hot-plugging. Those are on the manual checklist in docs/COMPATIBILITY.md.

Exit code 0 when every check passes, 5 otherwise. The report goes to stdout
(console exe) and to a diagnostics file in the log folder.
"""

import logging
import sys

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QMouseEvent

from . import app as app_module
from . import constants as C
from . import dab
from . import diagnostics
from . import hotkeys
from . import native_windows
from . import paths

logger = logging.getLogger(f"{C.ORG_NAME}.selftest")

EXIT_SELFTEST_FAILED = 5


_USER_INPUT_EVENTS = {
    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
    QEvent.Type.MouseMove, QEvent.Type.TabletPress, QEvent.Type.TabletMove, QEvent.Type.TabletRelease,
    QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd, QEvent.Type.KeyPress,
    QEvent.Type.KeyRelease, QEvent.Type.Wheel, QEvent.Type.ShortcutOverride,
}


class _InputBlocker(QObject):
    """Swallows real mouse/pen/keyboard input while the self-test runs.
    Someone moving the mouse over the overlay (or pressing Esc) mid-test
    would otherwise add strokes or close it and fail the checks."""

    def eventFilter(self, obj, event):
        return event.type() in _USER_INPUT_EVENTS


class _Results:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        logger.info("selftest %s: %s %s", "PASS" if ok else "FAIL", name, detail)
        return ok

    def warn(self, name, detail):
        self.rows.append((name, None, detail))
        logger.warning("selftest WARN: %s %s", name, detail)

    @property
    def passed(self):
        return all(ok is not False for _, ok, _ in self.rows)

    def lines(self):
        label = {True: "PASS", False: "FAIL", None: "WARN"}
        return [f"[{label[ok]}] {name}{': ' + detail if detail else ''}" for name, ok, detail in self.rows]


def _canvas_has_ink(overlay):
    image = overlay._canvas.toImage()
    w, h = image.width(), image.height()
    step = max(1, min(w, h) // 200)
    for y in range(0, h, step):
        for x in range(0, w, step):
            if image.pixelColor(x, y).alpha() > 0:
                return True
    return False


def _draw_test_stroke(overlay):
    rect = overlay._anchor_local_rect()
    cx, cy = rect.center().x(), rect.center().y()
    overlay._begin_stroke(QPointF(cx, cy), 0.8)
    for i in range(1, 40):
        overlay._add_point(QPointF(cx + i * 6, cy + (i % 7) * 3), 0.8)
    overlay._end_stroke()


def _run_checks(app, overlay, opts, results):
    results.check("Qt platform plugin is 'windows'", QGuiApplication.platformName() == "windows",
                  QGuiApplication.platformName())
    results.check("At least one monitor detected", len(QGuiApplication.screens()) > 0,
                  f"{len(QGuiApplication.screens())} monitor(s)")

    ok, actual, expected = overlay.native_geometry_ok()
    results.check("Overlay window lines up with the covered monitor(s)", ok,
                  f"native {actual}, expected {expected}, monitor {overlay._target_screen.name()}")
    results.check("Canvas allocated", overlay._canvas is not None and not overlay._canvas.isNull(),
                  f"{overlay._canvas.width()}x{overlay._canvas.height()} @ {overlay._canvas.devicePixelRatio():g}x")
    style = native_windows.get_ex_style(int(overlay.winId()))
    results.check("Overlay is a layered (translucent) window", style & native_windows.WS_EX_LAYERED,
                  hex(style & 0xFFFFFFFF))

    # Draw, undo, redo, clear. Saved history is untouched: the self-test
    # starts with an empty history and clears it before exiting.
    _draw_test_stroke(overlay)
    results.check("Drawing a stroke reaches the canvas", len(overlay.history) == 1 and _canvas_has_ink(overlay))
    overlay.undo()
    results.check("Undo removes it", len(overlay.history) == 0 and not _canvas_has_ink(overlay))
    overlay.redo()
    results.check("Redo restores it", len(overlay.history) == 1 and _canvas_has_ink(overlay))
    overlay._set_mode("eraser")
    _draw_test_stroke(overlay)
    overlay._set_mode("brush")
    results.check("Eraser stroke commits", len(overlay.history) == 2)
    overlay.clear()
    results.check("Clear empties the canvas", len(overlay.history) == 0 and not _canvas_has_ink(overlay))

    _toolbar_checks(overlay, results)

    screen = QGuiApplication.primaryScreen()
    grab = screen.grabWindow(0, 0, 0, 4, 4)
    results.check("Screen capture works", not grab.isNull())

    candidates = paths.current().screenshot_dir_candidates(opts.screenshot_dir)
    writable = next((c for c in candidates if paths.is_writable_dir(c)), None)
    if writable is None:
        results.check("A screenshot folder is writable", False, f"tried {[str(c) for c in candidates]}")
    elif writable != candidates[0]:
        results.warn("Preferred screenshot folder not writable", f"{candidates[0]} -> will use {writable}")
    else:
        results.check("Screenshot folder writable", True, str(writable))

    results.check("Log file location available", paths.current().log_dir is not None, str(paths.current().log_dir))


def _click_at(overlay, local_point):
    """Deliver a left click to the overlay's own handlers at an overlay-local
    point, bypassing the input blocker (which only stops real input)."""
    local = QPointF(local_point)
    glob = QPointF(overlay.mapToGlobal(local_point))
    for etype, handler in ((QEvent.Type.MouseButtonPress, overlay.mousePressEvent),
                           (QEvent.Type.MouseButtonRelease, overlay.mouseReleaseEvent)):
        buttons = Qt.MouseButton.LeftButton if etype == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
        handler(QMouseEvent(etype, local, glob, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier))


def _toolbar_checks(overlay, results):
    overlay.eraser_btn.click()
    eraser = overlay.mode == "eraser" and overlay.eraser_btn.isChecked() and not overlay.brush_btn.isChecked()
    overlay.brush_btn.click()
    brush = overlay.mode == "brush" and overlay.brush_btn.isChecked() and not overlay.eraser_btn.isChecked()
    results.check("Toolbar Brush/Eraser buttons switch tools", eraser and brush)

    original = overlay.brush_size
    start = overlay.size_slider.value()
    probe = start + 60 if start + 60 <= overlay.size_slider.maximum() else start - 60
    overlay.size_slider.setValue(probe)
    slider_to_model = (abs(overlay.brush_size - dab.slider_to_size(probe)) < 0.01
                       and overlay.size_value_label.text() == dab.format_size(overlay.brush_size))
    overlay.change_size(-1)
    key_to_slider = overlay.size_slider.value() < probe
    overlay._set_size(original)
    results.check("Size slider and [ / ] keys stay in sync", slider_to_model and key_to_slider)

    _draw_test_stroke(overlay)
    enabled = overlay.undo_btn.isEnabled() and not overlay.redo_btn.isEnabled()
    overlay.undo_btn.click()
    after_undo = len(overlay.history) == 0 and overlay.redo_btn.isEnabled()
    overlay.redo_btn.click()
    after_redo = len(overlay.history) == 1
    overlay.clear_btn.click()
    cleared = len(overlay.history) == 0 and not overlay.undo_btn.isEnabled()
    results.check("Undo / Redo / Clear buttons", enabled and after_undo and after_redo and cleared)

    _click_at(overlay, overlay.panel.geometry().center())
    results.check("Clicking the toolbar doesn't draw", len(overlay.history) == 0 and overlay.current is None)

    total = len(QGuiApplication.screens())
    results.check("Monitor button shown only with more than one monitor",
                  overlay.monitor_btn.isVisibleTo(overlay.panel) == (total > 1), f"{total} monitor(s)")


def _on_covered_monitor(overlay, widget_rect_global):
    return overlay._target_screen.geometry().contains(widget_rect_global)


def _panel_global_rect(overlay):
    r = overlay.panel.geometry()
    return r.translated(overlay.geometry().topLeft())


def _transparent(overlay):
    return bool(native_windows.get_ex_style(int(overlay.winId())) & native_windows.WS_EX_TRANSPARENT)


def _region_steps(overlay, results, label):
    """Drawing region on the real monitor (#10): paint a black backdrop in a
    region with a white margin, grab the screen natively, and check the
    edges land where they were painted, at this monitor's scale. Fields are
    set directly (not through the setters), so nothing is saved."""
    state = {}
    margin = 40

    def setup():
        state.update(
            backdrop=overlay.backdrop, backdrop_opacity=overlay.backdrop_opacity, region=overlay.region,
            margin=(overlay.margin_enabled, overlay.margin_size, overlay.margin_color, overlay.margin_opacity),
            panel=overlay.panel.isVisible(),
        )
        local = overlay.rect()
        w, h = local.width() // 3, local.height() // 4
        overlay.region = QRect((local.width() - w) // 2, local.height() // 2, w, h)
        overlay.backdrop, overlay.backdrop_opacity = "black", 1.0
        overlay.margin_enabled, overlay.margin_size, overlay.margin_color, overlay.margin_opacity = (
            True, margin, "white", 1.0)
        overlay._capturing = True
        overlay.cursor_pos = None
        overlay.panel.hide()
        overlay.toast_label.hide()
        overlay.update()

    def check():
        screen = overlay._target_screen
        region = overlay.region
        outer = overlay.drawable_rect()
        g = outer.translated(overlay.geometry().topLeft() - screen.geometry().topLeft())
        image = screen.grabWindow(0, g.x(), g.y(), g.width(), g.height()).toImage()
        dpr = image.width() / max(1, outer.width())

        def lightness(x, y):
            return image.pixelColor(int((x - outer.x()) * dpr), int((y - outer.y()) * dpr)).lightness()

        cx, cy = region.center().x(), region.center().y()
        # One logical pixel in from each edge must be region, two out must
        # be margin: alignment to about a pixel at this monitor's scale.
        inside = [(region.left() + 1, cy), (region.right() - 1, cy), (cx, region.top() + 1), (cx, region.bottom() - 1)]
        outside = [(region.left() - 2, cy), (region.right() + 2, cy), (cx, region.top() - 2), (cx, region.bottom() + 2)]
        dark = [lightness(x, y) for x, y in inside]
        light = [lightness(x, y) for x, y in outside]
        aligned = all(v < 60 for v in dark) and all(v > 200 for v in light)
        results.check(
            f"{label}: drawing region and margin line up with a native screen grab",
            aligned and abs(dpr - screen.devicePixelRatio()) < 0.02,
            f"{screen.name()} @ {round(screen.devicePixelRatio() * 100)}%, grab {dpr:.3f}x, "
            f"inside lightness {dark}, margin lightness {light}",
        )

        shot = overlay.capture_region_pixmap()
        expected = (round(outer.width() * dpr), round(outer.height() * dpr))
        results.check(f"{label}: region screenshot covers exactly region plus margin",
                      abs(shot.width() - expected[0]) <= 1 and abs(shot.height() - expected[1]) <= 1,
                      f"{shot.width()}x{shot.height()}, expected {expected[0]}x{expected[1]}")

        overlay.history.clear()
        overlay._rebuild_canvas()
        overlay._begin_stroke(QPointF(outer.left() - 10, cy), 0.8)
        overlay._end_stroke()
        rejected = len(overlay.history) == 0
        overlay._begin_stroke(QPointF(region.left() - margin // 2, cy), 0.8)
        overlay._add_point(QPointF(region.left() + 20, cy), 0.8)
        overlay._end_stroke()
        accepted = len(overlay.history) == 1
        overlay.clear()
        results.check(f"{label}: strokes start inside region or margin only", rejected and accepted)

    def restore():
        overlay.region = state["region"]
        overlay.backdrop, overlay.backdrop_opacity = state["backdrop"], state["backdrop_opacity"]
        overlay.margin_enabled, overlay.margin_size, overlay.margin_color, overlay.margin_opacity = state["margin"]
        overlay._capturing = False
        if state["panel"]:
            overlay.panel.show()
        overlay.update()

    return [(setup, 400), (check, 0), (restore, 100)]


def _monitor_tour_steps(overlay, results):
    """Async steps: (action, wait_ms) pairs. Each action runs, then the event
    loop gets wait_ms so Windows can finish moving the window between
    monitors (including the DPI-change round trip) before the next check."""
    ordered = overlay._ordered_screens()
    total = len(ordered)
    start = overlay._target_screen
    steps = []

    def check_here(label):
        def run():
            screen = overlay._target_screen
            ok, actual, expected = overlay.native_geometry_ok()
            dpr_ok = abs(overlay._canvas.devicePixelRatio() - overlay._target_dpr()) < 1e-3
            toolbar_ok = _on_covered_monitor(overlay, _panel_global_rect(overlay))
            overlay.history.clear()
            overlay._rebuild_canvas()
            _draw_test_stroke(overlay)
            ink = len(overlay.history) == 1 and _canvas_has_ink(overlay)
            overlay.clear()
            results.check(
                f"{label}: aligned, toolbar on it, canvas at its scale, drawing works",
                ok and dpr_ok and toolbar_ok and ink,
                f"{screen.name()} @ {round(screen.devicePixelRatio() * 100)}%: native {actual}, expected {expected}, "
                f"toolbar_on_monitor={toolbar_ok}, canvas_dpr_ok={dpr_ok}, ink={ink}",
            )
        return run

    if total > 1:
        for i in range(total):
            steps.append((overlay.monitor_btn.click, 900))
            steps.append((check_here(f"Monitor button move {i + 1}/{total}"), 0))
            steps += _region_steps(overlay, results, f"Monitor {i + 1}/{total}")
        steps.append((lambda: results.check("Monitor button cycles back to the starting monitor",
                                            overlay._target_screen is start), 0))

    if total == 1:
        steps += _region_steps(overlay, results, "Monitor 1/1")

    def pass_on():
        overlay.pass_btn.click()

    def check_pass_on():
        badge = overlay._pass_through_badge
        results.check(
            "Pass-through on: click-through set, toolbar hidden, badge on this monitor",
            overlay.pass_through and _transparent(overlay) and not overlay.panel.isVisible()
            and badge.isVisible() and _on_covered_monitor(overlay, badge.frameGeometry()),
        )

    steps += [(pass_on, 300), (check_pass_on, 0)]
    if total > 1:
        def check_pass_moved():
            badge = overlay._pass_through_badge
            ok, actual, expected = overlay.native_geometry_ok()
            results.check(
                "Monitor switch during pass-through keeps click-through and moves the badge",
                overlay.pass_through and _transparent(overlay) and badge.isVisible()
                and _on_covered_monitor(overlay, badge.frameGeometry()) and ok,
                f"{overlay._target_screen.name()}: native {actual}, expected {expected}",
            )
        steps += [(overlay.move_to_next_screen, 900), (check_pass_moved, 0)]

    def badge_click():
        overlay._pass_through_badge.clicked.emit()

    def check_pass_off():
        results.check(
            "Badge click leaves pass-through: input restored, toolbar back, badge hidden",
            not overlay.pass_through and not _transparent(overlay) and overlay.panel.isVisible()
            and not overlay._pass_through_badge.isVisible() and not overlay.pass_btn.isChecked(),
        )

    steps += [(badge_click, 300), (check_pass_off, 0)]

    if total > 1:
        def go_home():
            guard = 0
            while overlay._target_screen is not start and guard < total:
                overlay.move_to_next_screen()
                guard += 1
        steps.append((go_home, 900))
    return steps


def _lifecycle_steps(app, overlay, results):
    """Global hotkey delivery and hide/show keeping state (#14). Uses its
    own unlikely hotkey, never the configured one, and posts the WM_HOTKEY
    itself, so it doesn't depend on (or take) a key someone else uses."""
    steps = []
    hits = []
    event_filter = app_module.HotkeyFilter()
    hotkey = app_module.GlobalHotkey(event_filter, 0x5E1F, "Ctrl+Alt+Shift+F24", lambda: hits.append(1))
    state = {}

    def post():
        app.installNativeEventFilter(event_filter)
        problem = hotkey.register()
        state["problem"] = problem
        if problem is None:
            hotkeys.post_test_hotkey(0x5E1F)

    def check_hotkey():
        hotkey.unregister()
        app.removeNativeEventFilter(event_filter)
        if state["problem"]:
            results.warn("Global hotkey test skipped", state["problem"])
        else:
            results.check("Global hotkey message reaches the app", hits == [1], f"{len(hits)} delivered")

    def hide():
        _draw_test_stroke(overlay)
        overlay.region = QRect(overlay._anchor_local_rect().adjusted(40, 40, -40, -40))
        state["before"] = (len(overlay.history), QRect(overlay.region), overlay._canvas.toImage())
        # Nothing to persist in a self-test; hide_to_tray would save.
        overlay.save_settings_now = lambda: None
        try:
            overlay.hide_to_tray()
        finally:
            del overlay.save_settings_now

    def show():
        results.check("Hide to tray hides the window", not overlay.isVisible()
                      and not native_windows.is_window_visible(int(overlay.winId())))
        overlay.show_overlay()

    def check_shown():
        count, region, image = state["before"]
        ok, actual, expected = overlay.native_geometry_ok()
        results.check("Showing again keeps strokes, region and placement",
                      overlay.isVisible() and len(overlay.history) == count and overlay.region == region
                      and overlay._canvas.toImage() == image and ok,
                      f"native {actual}, expected {expected}")
        overlay.region = None
        overlay.clear()

    steps += [(post, 300), (check_hotkey, 0), (hide, 400), (show, 600), (check_shown, 0)]
    return steps


def run_selftest(app, overlay, opts):
    results = _Results()
    state = {"code": EXIT_SELFTEST_FAILED}

    def report():
        results.check("No unhandled Python exceptions", not app_module.unhandled_exceptions,
                      f"{len(app_module.unhandled_exceptions)} seen")
        lines = [f"{C.APP_NAME} {C.APP_VERSION} self-test: {'PASSED' if results.passed else 'FAILED'}"]
        lines += results.lines()
        lines += ["", "-" * 72] + diagnostics.collect(opts, overlay)
        path = diagnostics.write_report(lines)
        if getattr(sys, "stdout", None) is not None:
            try:
                print("\n".join(lines[: len(results.rows) + 1]))
                print(f"Full report: {path}")
                sys.stdout.flush()
            except Exception:
                pass
        state["code"] = 0 if results.passed else EXIT_SELFTEST_FAILED
        # Nothing to persist: brush settings were restored as they were.
        overlay._settings_save_timer.stop()
        overlay.hide()
        app.quit()

    def run_steps(steps):
        if not steps:
            report()
            return
        action, wait_ms = steps[0]
        try:
            action()
        except Exception as exc:
            logger.exception("Self-test step failed")
            results.check("Self-test step ran", False, repr(exc))
        QTimer.singleShot(wait_ms, lambda: run_steps(steps[1:]))

    def first_phase():
        try:
            _run_checks(app, overlay, opts, results)
            steps = _monitor_tour_steps(overlay, results) + _lifecycle_steps(app, overlay, results)
        except Exception as exc:
            logger.exception("Self-test crashed")
            results.check("Self-test ran to completion", False, repr(exc))
            steps = []
        run_steps(steps)

    # Start empty so the stroke checks are meaningful and nothing the user
    # drew is affected (history isn't persisted across runs anyway).
    overlay.history.clear()
    overlay._rebuild_canvas()
    blocker = _InputBlocker(app)
    app.installEventFilter(blocker)
    overlay.show()
    overlay.raise_()
    # Give the window manager a moment to place and composite the window.
    QTimer.singleShot(800, first_phase)
    app.exec()
    logger.info("Self-test exit code %s", state["code"])
    return state["code"]
