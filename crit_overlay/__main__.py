import logging
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import constants as C
from .icon import build_app_icon
from .logging_setup import setup_logging
from .overlay import Overlay
from .tray import TrayIcon

logger = logging.getLogger(f"{C.ORG_NAME}.main")


def _install_excepthook():
    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.critical("Unhandled exception:\n%s", text)
        try:
            QMessageBox.critical(None, f"{C.APP_NAME}: Error", text)
        except Exception:
            pass

    sys.excepthook = hook


def main():
    debug = "--debug" in sys.argv
    setup_logging(debug=debug)
    _install_excepthook()
    logger.info("%s v%s starting (python %s)", C.APP_NAME, C.APP_VERSION, sys.version.split()[0])

    # Leave Qt's tablet-to-mouse synthesis on (its default). Toolbar buttons
    # and sliders don't override tabletEvent, so a pen tap only reaches them
    # as a synthesized click. Overlay.tabletEvent accepts the events it
    # draws with, which suppresses synthesis for those.
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    icon = build_app_icon()
    app.setWindowIcon(icon)

    overlay = Overlay()
    overlay.setWindowIcon(icon)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayIcon(icon)
        tray.toggle_visibility_requested.connect(overlay.toggle_visibility)
        tray.toggle_pass_through_requested.connect(overlay.toggle_pass_through)
        tray.screenshot_requested.connect(overlay.capture_annotated)
        tray.quit_requested.connect(app.quit)
        tray.show()
    else:
        logger.warning("System tray unavailable; running without a tray icon")

    overlay.show()
    overlay.raise_()
    overlay.activateWindow()
    overlay.setFocus()

    logger.info("Overlay visible=%s geometry=%s", overlay.isVisible(), overlay.geometry())
    logger.info("Entering event loop")
    code = app.exec()
    logger.info("Exited with code %s", code)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        err = traceback.format_exc()
        logger.critical("FATAL ERROR:\n%s", err)
        try:
            QMessageBox.critical(None, f"{C.APP_NAME}: Fatal Error", err)
        except Exception:
            pass
        raise SystemExit(1)
