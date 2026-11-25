"""Process entry point: everything that has to happen before Qt loads.

Standard library only until the guarded PySide6 import at the end. The order
matters:

1. Options, so we know about --portable, --no-dialogs and --debug.
2. Paths and logging, each with fallbacks, so there's somewhere to write.
3. Crash reporting (faulthandler + native message box), so nothing after
   this point can fail silently, even a native crash in a Qt DLL.
4. Clear inherited Qt environment variables and check the bundle is intact.
5. Import PySide6, reporting a failure in plain language.
6. Hand off to app.run() for everything Qt.

Exit codes: 0 ok, 1 unexpected error, 2 incomplete installation, 3 Qt failed
to load, 4 bad command line, 5 self-test failed. A second launch while a
copy is running shows that copy's overlay and exits with 0.
"""

import logging
import os
import sys
import traceback

from . import compat
from . import constants as C
from . import crash
from . import instance
from . import paths
from .logging_setup import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE_INSTALL = 2
EXIT_QT_LOAD_FAILED = 3
EXIT_BAD_ARGS = 4
EXIT_SELFTEST_FAILED = 5

logger = logging.getLogger(f"{C.ORG_NAME}.launcher")


def _show_text(title, text, error):
    """Print if there's a console, otherwise a message box."""
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        try:
            print(text, file=stream)
            return
        except Exception:
            pass
    crash.native_message_box(f"{C.APP_NAME}: {title}", text, crash.MB_ICONERROR if error else crash.MB_ICONINFORMATION)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(argv)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return EXIT_ERROR
    except BaseException:
        crash.report_fatal(
            "Unexpected error",
            f"{C.APP_NAME} hit an unexpected error and has to close.",
            traceback.format_exc(),
        )
        return EXIT_ERROR
    finally:
        # Reached on every Python-level exit, including reported errors. A
        # native crash skips it, which is what leaves the session marker
        # for the next launch to find.
        crash.end_session()


def _main(argv):
    try:
        opts = compat.parse_options(argv, user_ini_resolver=lambda o: paths.init(portable=o.portable).settings_path)
    except compat.HelpRequested as h:
        _show_text("Help", str(h), error=False)
        return EXIT_OK
    except compat.OptionsError as e:
        _show_text("Invalid command line", str(e), error=True)
        return EXIT_BAD_ARGS

    app_paths = paths.current()
    crash.configure(app_paths.log_dir, dialogs=opts.dialogs)
    _, log_path = setup_logging(debug=opts.debug, app_paths=app_paths)
    fault_log = crash.enable_faulthandler(app_paths.log_dir)
    unclean = crash.begin_session(app_paths.log_dir)

    logger.info(
        "%s v%s starting (python %s, %s, pid %s)",
        C.APP_NAME, C.APP_VERSION, sys.version.split()[0],
        "frozen" if getattr(sys, "frozen", False) else "source", os.getpid(),
    )
    logger.info("Options: %s (sources: %s)", opts.as_dict(), opts.sources or "defaults")
    for w in opts.warnings:
        logger.warning("Option warning: %s", w)
    for note in app_paths.notes:
        logger.warning("Paths: %s", note)
    logger.info("Paths: %s", app_paths.describe())
    logger.info("Native crash log: %s", fault_log or "(disabled, no writable location)")
    for entry in unclean:
        logger.warning(
            "A previous session did not exit cleanly (%s). If it crashed, the stack is in %s",
            entry, fault_log or "faulthandler.log",
        )
    if not (opts.selftest or opts.diagnose):
        # One copy per session (#14). Checked before Qt loads, so a second
        # launch costs a fraction of a second; it just brings the running
        # copy's overlay back. --selftest and --diagnose run alongside.
        running = instance.acquire()
        if running.already_running:
            shown = running.request_show()
            logger.info("Another copy is already running; %s", "asked it to show the overlay" if shown
                        else "couldn't signal it")
            running.close()
            return EXIT_OK

    if crash.previous_startup_hung():
        logger.error("The previous launch got stuck while starting Qt and was stopped by the watchdog")
        crash.native_message_box(
            f"{C.APP_NAME}: Previous start failed",
            f"Last time, {C.APP_NAME} got stuck while starting up and was closed automatically.\n\n"
            "If it happens again, start it in safe mode (\"CritOverlay (Safe Mode).bat\", or "
            "--safe-mode) and send the log folder to whoever supports it:\n"
            f"{app_paths.log_dir}",
            crash.MB_ICONWARNING,
        )

    removed = compat.sanitize_qt_environment(keep=opts.keep_qt_env)
    for name, value in removed:
        if opts.keep_qt_env:
            logger.warning("Keeping inherited %s=%s (--keep-qt-env)", name, value)
        else:
            logger.warning("Cleared inherited %s=%s (set by other software; can break Qt)", name, value)

    missing = compat.validate_bundle()
    if missing:
        crash.report_fatal(
            "Incomplete installation",
            f"{C.APP_NAME} can't start because these files are missing from its folder:\n  "
            + "\n  ".join(missing),
            hint="Re-extract the whole zip to a local folder (not a network drive, and not by "
                 "opening the exe from inside the zip). If the files disappear again, antivirus "
                 "may be quarantining them; ask IT to allow the folder.",
        )
        return EXIT_INCOMPLETE_INSTALL

    spec = compat.apply_platform_spec(opts, opts.qt_args)
    if spec:
        logger.info("Qt platform: %s", spec)

    try:
        import importlib

        for module in ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
            importlib.import_module(module)
    except Exception:
        crash.report_fatal(
            "Qt failed to load",
            f"{C.APP_NAME} couldn't load its Qt libraries.",
            traceback.format_exc(),
            hint="From the standalone build: re-extract the zip to a local folder and check "
                 "antivirus hasn't quarantined anything in the _internal folder.\n"
                 "From source: install dependencies with\n"
                 "  python -m pip install --user -r requirements.txt",
        )
        return EXIT_QT_LOAD_FAILED

    from .app import run

    return run(opts, log_path=log_path, removed_env=removed, unclean_sessions=unclean)
