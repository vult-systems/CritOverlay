"""Qt side of startup: QApplication, overlay, tray, and error routing.

Imported only after launcher.py has confirmed PySide6 loads.
"""

import logging
import os
import sys
import time
import traceback

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import autostart
from . import constants as C
from . import crash
from . import diagnostics
from . import hotkeys
from . import instance
from . import paths

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


TOGGLE_HOTKEY_ID = 1


class HotkeyFilter(QAbstractNativeEventFilter):
    """Receives WM_HOTKEY from the thread message queue (see hotkeys.py)
    and calls the handler registered for its id. Called for every native
    message, so it does as little as possible for the rest."""

    def __init__(self):
        super().__init__()
        self._handlers = {}

    def add(self, hotkey_id, handler):
        self._handlers[hotkey_id] = handler

    def remove(self, hotkey_id):
        self._handlers.pop(hotkey_id, None)

    def nativeEventFilter(self, event_type, message):
        hotkey_id = hotkeys.hotkey_id_from_msg(message) if self._handlers else None
        handler = self._handlers.get(hotkey_id) if hotkey_id is not None else None
        if handler is None:
            return False, 0
        # Run it after returning to the event loop, not inside Qt's
        # native-event processing.
        QTimer.singleShot(0, handler)
        return True, 0


class GlobalHotkey:
    """One system-wide hotkey. register() returns None on success, or a
    plain-language reason it isn't available."""

    def __init__(self, event_filter, hotkey_id, text, handler):
        self._filter = event_filter
        self._id = hotkey_id
        self.text = text
        self.display = None
        self._handler = handler

    def register(self):
        try:
            parsed = hotkeys.parse(self.text)
        except hotkeys.HotkeyError as e:
            return str(e)
        if parsed is None:
            logger.info("Global hotkey turned off")
            return None
        mods, vk, name = parsed
        err = hotkeys.register(self._id, mods, vk)
        if err:
            if err == hotkeys.ERROR_HOTKEY_ALREADY_REGISTERED:
                return f"{name} is already used by another program"
            return f"Windows refused {name} (error {err})"
        self._filter.add(self._id, self._handler)
        self.display = name
        logger.info("Global hotkey %s registered", name)
        return None

    def unregister(self):
        if self.display is not None:
            self._filter.remove(self._id)
            hotkeys.unregister(self._id)
            self.display = None


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
    # Quitting is explicit (tray Exit, or Esc when there's no tray). Left
    # on, closing any dialog while the overlay is hidden in the tray would
    # end the app.
    app.setQuitOnLastWindowClosed(False)
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

    def quit_app(source):
        logger.info("Exit requested from %s", source)
        app.quit()

    app.aboutToQuit.connect(overlay.save_settings_now)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayIcon(icon)
        tray.toggle_visibility_requested.connect(overlay.toggle_visibility)
        tray.toggle_pass_through_requested.connect(overlay.toggle_pass_through)
        tray.screenshot_requested.connect(overlay.capture_annotated)
        tray.next_screen_requested.connect(overlay.move_to_next_screen)
        tray.diagnostics_requested.connect(lambda: save_diagnostics(opts, overlay))
        tray.open_logs_requested.connect(lambda: log_path and _open_file(log_path.parent))
        tray.quit_requested.connect(lambda: quit_app("the tray menu"))
        _wire_autostart(tray)
        tray.show()
    else:
        logger.warning("System tray unavailable; running without a tray icon, Esc exits")

    hotkey_filter = HotkeyFilter()
    app.installNativeEventFilter(hotkey_filter)
    hotkey = GlobalHotkey(hotkey_filter, TOGGLE_HOTKEY_ID, opts.hotkey, overlay.toggle_visibility)
    problem = hotkey.register()
    app.aboutToQuit.connect(hotkey.unregister)
    if problem:
        logger.warning("Global hotkey unavailable: %s", problem)
    overlay.set_tray_available(tray is not None, hotkey.display)
    if tray is not None:
        tray.set_hotkey(hotkey.display)
        if problem:
            tray.showMessage(
                f"{C.APP_NAME}: hotkey unavailable",
                f"{problem}. Use the tray icon to show and hide the overlay, or set another "
                "hotkey (hotkey= under [compat] in the settings file).",
                QSystemTrayIcon.MessageIcon.Warning, 10000,
            )
        told = []

        def on_hidden():
            # Once per session: where the overlay went and how to quit.
            if not told:
                told.append(True)
                back = f"{hotkey.display} or the tray icon" if hotkey.display else "The tray icon"
                tray.showMessage(C.APP_NAME, f"Still running. {back} shows the overlay again; "
                                 "Exit is in the tray icon's menu.",
                                 QSystemTrayIcon.MessageIcon.Information, 5000)

        overlay.hidden_to_tray.connect(on_hidden)

    show_notifier = _listen_for_show_requests(overlay)

    if opts.hidden and tray is not None:
        logger.info("Starting hidden in the tray (--hidden)")
    else:
        if opts.hidden:
            logger.warning("--hidden ignored: there is no tray icon to show the overlay from")
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        overlay.setFocus()

    logger.info("Overlay visible=%s geometry=%s", overlay.isVisible(), overlay.geometry())
    logger.info("Entering event loop (startup %.0f ms)", (time.perf_counter() - t0) * 1000)
    code = app.exec()
    del show_notifier
    logger.info("Exited with code %s", code)
    return code


def _listen_for_show_requests(overlay):
    """A second launch signals the single-instance event (instance.py);
    show the overlay when it does."""
    owned = instance.current()
    if owned is None or not owned.show_event:
        return None
    import shiboken6
    from PySide6.QtCore import QWinEventNotifier

    # PySide wants the HANDLE as a pointer, not an int.
    notifier = QWinEventNotifier(shiboken6.VoidPtr(owned.show_event))

    def on_request():
        logger.info("Another launch asked to show the overlay")
        overlay.show_overlay()

    notifier.activated.connect(on_request)
    return notifier


def _wire_autostart(tray):
    command = autostart.launch_command(portable=paths.current().portable)

    def refresh():
        tray.set_autostart_checked(autostart.is_enabled(command=command))

    def toggled(checked):
        try:
            if checked:
                autostart.enable(command=command)
            else:
                autostart.disable()
        except autostart.AutostartError as e:
            logger.warning("Start with Windows: %s", e)
            tray.showMessage(f"{C.APP_NAME}: Start with Windows", str(e), QSystemTrayIcon.MessageIcon.Warning, 8000)
        refresh()

    tray.autostart_toggled.connect(toggled)
    refresh()
