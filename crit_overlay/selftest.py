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

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QMouseEvent

from . import app as app_module
from . import constants as C
from . import diagnostics
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
    probe = original + 3 if original + 3 <= C.MAX_SIZE else original - 3
    overlay.size_slider.setValue(probe)
    slider_to_model = overlay.brush_size == probe and overlay.size_value_label.text() == f"{probe}px"
    overlay.change_size(-1)
    key_to_slider = overlay.size_slider.value() == probe - 1
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
        steps.append((lambda: results.check("Monitor button cycles back to the starting monitor",
                                            overlay._target_screen is start), 0))

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
            steps = _monitor_tour_steps(overlay, results)
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
