"""Qt side of startup: QApplication, overlay, tray, and error routing.

Imported only after launcher.py has confirmed PySide6 loads.
"""

import logging
import os
import sys
import time
import traceback

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import constants as C
from . import crash
from . import diagnostics

logger = logging.getLogger(f"{C.ORG_NAME}.main")
qt_logger = logging.getLogger(f"{C.ORG_NAME}.qt")

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}

# Shared with selftest, which fails if anything got through to the hook.
unhandled_exceptions = []

# Generous: a first launch from a network share with antivirus scanning
# every DLL can be slow. Normal startup is well under a second.
# CRITOVERLAY_STARTUP_TIMEOUT overrides it (seconds; 0 disables).
_DEFAULT_STARTUP_TIMEOUT_S = 60


def _startup_timeout():
    try:
        return float(os.environ.get("CRITOVERLAY_STARTUP_TIMEOUT", _DEFAULT_STARTUP_TIMEOUT_S))
    except ValueError:
        return _DEFAULT_STARTUP_TIMEOUT_S


def _qt_message_handler(msg_type, context, message):
    """Route Qt's own warnings into our log. Without this they go to a
    stderr the windowed exe doesn't have, including the one message that
    matters most: why the platform plugin failed to load."""
    category = getattr(context, "category", None) or "qt"
    qt_logger.log(_QT_LEVELS.get(msg_type, logging.WARNING), "[%s] %s", category, message)
    if msg_type == QtMsgType.QtFatalMsg:
        # Qt calls abort() right after this returns, so this is the last
        # chance to leave a trace.
        crash.report_fatal(
            "Qt fatal error",
            f"Qt stopped with a fatal error:\n{message}",
            hint="If this mentions a platform plugin, re-extract the app to a local folder, or "
                 "run it once with --debug and send the log.",
        )


def _install_excepthook():
    shown = set()

    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.critical("Unhandled exception:\n%s", text)
        unhandled_exceptions.append(text)
        # One dialog per distinct error. The same exception repeating in a
        # paint or input handler would otherwise bury the screen in
        # modal boxes and make the app impossible to close.
        frames = traceback.extract_tb(exc_tb)
        key = (exc_type.__name__, frames[-1].filename if frames else "", frames[-1].lineno if frames else 0)
        if key in shown or len(shown) >= 3 or not crash._state["dialogs"]:
            return
        shown.add(key)
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(None, f"{C.APP_NAME}: Error", text)
            else:
                crash.native_message_box(f"{C.APP_NAME}: Error", text)
        except Exception:
            pass

    sys.excepthook = hook


def _open_file(path):
    try:
        os.startfile(str(path))  # noqa: S606 - user-initiated, our own report file
        return True
    except Exception:
        logger.warning("Couldn't open %s with the default app", path, exc_info=True)
        return False


def save_diagnostics(opts, overlay=None, open_after=True):
    lines = diagnostics.collect(opts, overlay)
    path = diagnostics.write_report(lines)
    logger.info("Diagnostics report written to %s", path)
    if open_after and path and not _open_file(path):
        crash.native_message_box(f"{C.APP_NAME}: Diagnostics", f"Report saved to:\n{path}", crash.MB_ICONINFORMATION)
    return path, lines


def run(opts, log_path=None, removed_env=(), unclean_sessions=()):
    qInstallMessageHandler(_qt_message_handler)
    _install_excepthook()

    t0 = time.perf_counter()
    crash.arm_startup_watchdog(_startup_timeout())
    # Tablet-to-mouse synthesis stays on (Qt's default). Toolbar buttons
    # and sliders don't override tabletEvent, so a pen tap only reaches them
    # as a synthesized click. Overlay.tabletEvent accepts the events it draws
    # with, which suppresses synthesis for those.
    app = QApplication([sys.argv[0]] + list(opts.qt_args))
    app.setQuitOnLastWindowClosed(True)
    logger.info("QApplication up in %.0f ms, platform=%s", (time.perf_counter() - t0) * 1000, QGuiApplication.platformName())
    if QGuiApplication.platformName() != "windows":
        logger.warning("Running on the %r platform plugin, not 'windows'", QGuiApplication.platformName())

    from .icon import build_app_icon
    from .overlay import Overlay
    from .tray import TrayIcon

    icon = build_app_icon()
    app.setWindowIcon(icon)

    overlay = Overlay(screen_mode=opts.screens, input_mode=opts.input, screenshot_dir=opts.screenshot_dir)
    overlay.setWindowIcon(icon)

    extra = [f"Cleared env: {name}={value}" for name, value in removed_env]
    extra += [f"Unclean previous session: {u}" for u in unclean_sessions]
    for line in diagnostics.collect(opts, overlay, extra=extra):
        logger.info("diag: %s", line)

    crash.disarm_startup_watchdog()

    if opts.diagnose:
        path, _ = save_diagnostics(opts, overlay, open_after=opts.dialogs)
        if path is not None and getattr(sys, "stdout", None) is not None:
            print(f"Diagnostics report: {path}")
        overlay.deleteLater()
        return 0

    if opts.selftest:
        from .selftest import run_selftest

        return run_selftest(app, overlay, opts)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayIcon(icon)
        tray.toggle_visibility_requested.connect(overlay.toggle_visibility)
        tray.toggle_pass_through_requested.connect(overlay.toggle_pass_through)
        tray.screenshot_requested.connect(overlay.capture_annotated)
        tray.next_screen_requested.connect(overlay.move_to_next_screen)
        tray.diagnostics_requested.connect(lambda: save_diagnostics(opts, overlay))
        tray.open_logs_requested.connect(lambda: log_path and _open_file(log_path.parent))
        tray.quit_requested.connect(app.quit)
        tray.show()
    else:
        logger.warning("System tray unavailable; running without a tray icon")

    overlay.show()
    overlay.raise_()
    overlay.activateWindow()
    overlay.setFocus()

    logger.info("Overlay visible=%s geometry=%s", overlay.isVisible(), overlay.geometry())
    logger.info("Entering event loop (startup %.0f ms)", (time.perf_counter() - t0) * 1000)
    code = app.exec()
    logger.info("Exited with code %s", code)
    return code
