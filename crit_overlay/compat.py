"""Startup compatibility layer: options, environment hygiene, bundle checks.

Standard library only. Everything here runs before PySide6 is imported,
because several of the things it guards against make the Qt import or
QApplication construction itself fail.

Options come from four layers, later ones winning:

1. built-in defaults
2. [compat] in CritOverlay.ini next to the exe (machine/lab-wide defaults,
   for IT or a USB-stick install)
3. [compat] in the user's settings file
4. command-line flags
"""

import argparse
import configparser
import os
import sys
from pathlib import Path

from . import constants as C
from . import paths

INPUT_MODES = ("auto", "mouse")
SCREEN_MODES = ("cursor", "primary")
# Values accepted from early 1.5.0 development-build configs and shortcuts. The overlay always
# covers exactly one monitor now (see choose_monitor), so both map to the
# default.
LEGACY_SCREEN_MODES = {"auto": "cursor", "all": "cursor"}

# Qt reads these from the environment at startup. Lab images often set them
# system-wide for a DCC app (Maya, Houdini, Nuke, Toon Boom, Substance all
# ship their own Qt). Pointed at another Qt build they make ours load
# incompatible plugins (crash or "no Qt platform plugin could be
# initialized"); the scaling ones distort the overlay's geometry so it no
# longer lines up with the screen. None of them are needed by this app.
QT_ENV_VARS_TO_CLEAR = (
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QT_QPA_PLATFORM",
    "QT_QPA_PLATFORMTHEME",
    "QT_QPA_GENERIC_PLUGINS",
    "QT_STYLE_OVERRIDE",
    "QT_SCALE_FACTOR",
    "QT_SCREEN_SCALE_FACTORS",
    "QT_AUTO_SCREEN_SCALE_FACTOR",
    "QT_ENABLE_HIGHDPI_SCALING",
    "QT_SCALE_FACTOR_ROUNDING_POLICY",
    "QT_DEVICE_PIXEL_RATIO",
    "QT_FONT_DPI",
    "QT_USE_PHYSICAL_DPI",
    "QT_OPENGL",
    "QT_ANGLE_PLATFORM",
    "QT_WIDGETS_RHI",
    "QT_WIDGETS_RHI_BACKEND",
    "QT_QUICK_BACKEND",
    "QSG_RHI_BACKEND",
    "QML_IMPORT_PATH",
    "QML2_IMPORT_PATH",
)

# Files the frozen bundle can't start without. Antivirus quarantining one
# DLL, or a partial copy from a USB stick or network share, shows up here as
# a readable message instead of an obscure import error.
REQUIRED_BUNDLE_FILES = (
    "PySide6/Qt6Core.dll",
    "PySide6/Qt6Gui.dll",
    "PySide6/Qt6Widgets.dll",
    "PySide6/QtCore.pyd",
    "PySide6/QtGui.pyd",
    "PySide6/QtWidgets.pyd",
    "PySide6/plugins/platforms/qwindows.dll",
    "shiboken6",
)


class OptionsError(Exception):
    """Bad command-line input. The message includes usage text."""


class HelpRequested(Exception):
    """--help was given. The message is the help text."""


class _Parser(argparse.ArgumentParser):
    # The windowed exe has no stdout/stderr, and argparse's default error()
    # and print_help() write there and call sys.exit(). Raise instead, so
    # the launcher can show the text in a message box.
    def error(self, message):
        raise OptionsError(message + "\n\n" + self.format_usage())

    def print_help(self, file=None):
        raise HelpRequested(self.format_help())


class Options:
    def __init__(self):
        self.debug = False
        self.safe_mode = False
        self.input = "auto"
        self.screens = "cursor"
        self.keep_qt_env = False
        self.portable = False
        self.diagnose = False
        self.selftest = False
        self.dialogs = True
        self.screenshot_dir = None
        # Where each value came from, for the diagnostics report.
        self.sources = {}
        self.warnings = []
        self.qt_args = []

    def as_dict(self):
        keys = ("debug", "safe_mode", "input", "screens", "keep_qt_env", "portable", "diagnose", "selftest",
                "dialogs", "screenshot_dir")
        return {k: getattr(self, k) for k in keys}


def build_arg_parser():
    p = _Parser(
        prog="CritOverlay",
        description=f"{C.APP_NAME} {C.APP_VERSION}: transparent drawing overlay.",
        add_help=True,
    )
    p.add_argument("--debug", action="store_true", default=None, help="verbose logging (and console output if there is a console)")
    p.add_argument("--safe-mode", action="store_true", default=None,
                   help="most conservative settings: mouse input only, primary monitor only")
    p.add_argument("--input", choices=INPUT_MODES, default=None,
                   help="auto: pen input through Windows Ink, with pressure (default). mouse: ignore "
                        "tablet events and use plain mouse input, for tablet drivers that cause "
                        "missing, doubled or stuck strokes")
    p.add_argument("--screens", choices=SCREEN_MODES + tuple(LEGACY_SCREEN_MODES), default=None,
                   help="which monitor the overlay starts on: cursor (the one under the mouse, "
                        "default) or primary. It covers one monitor at a time; F7 or the toolbar's "
                        "monitor button moves it. (auto/all are accepted as legacy aliases for cursor.)")
    p.add_argument("--keep-qt-env", action="store_true", default=None,
                   help="don't clear QT_* environment variables inherited from other software")
    p.add_argument("--portable", action="store_true", default=None,
                   help=f"keep settings, logs and screenshots in {paths.PORTABLE_DATA_DIR} beside the app")
    p.add_argument("--screenshot-dir", default=None, help="save screenshots here first")
    p.add_argument("--diagnose", action="store_true", default=None,
                   help="write a compatibility report, open it, and exit")
    p.add_argument("--selftest", action="store_true", default=None,
                   help="start up, exercise the overlay without user input, report, and exit (0 = pass)")
    p.add_argument("--no-dialogs", action="store_true", default=None,
                   help="never show message boxes (for scripted runs)")
    return p


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _read_ini_compat(path):
    """[compat] from an INI file as a dict, or {} if missing/unreadable.

    RawConfigParser with strict=False, because the user settings file is
    written by QSettings: values may contain '%' and keys may repeat.
    """
    if not path or not Path(path).is_file():
        return {}
    parser = configparser.RawConfigParser(strict=False)
    try:
        with open(path, encoding="utf-8-sig") as f:
            parser.read_file(f)
    except Exception:
        return {"__error__": f"could not read {path}"}
    if not parser.has_section("compat"):
        return {}
    return {k.strip().lower(): v.strip() for k, v in parser.items("compat")}


def _apply_ini(opts, values, source):
    if "__error__" in values:
        opts.warnings.append(values["__error__"])
        return
    for key, raw in values.items():
        name = key.replace("-", "_")
        value = raw.strip().strip('"')
        if name in ("debug", "safe_mode", "keep_qt_env", "portable"):
            low = value.lower()
            if low in _TRUE:
                setattr(opts, name, True)
            elif low in _FALSE:
                setattr(opts, name, False)
            else:
                opts.warnings.append(f"{source}: ignoring {key}={raw!r} (expected true/false)")
                continue
        elif name == "input":
            if value.lower() not in INPUT_MODES:
                opts.warnings.append(f"{source}: ignoring input={raw!r} (expected one of {', '.join(INPUT_MODES)})")
                continue
            opts.input = value.lower()
        elif name == "screens":
            low = value.lower()
            if low in LEGACY_SCREEN_MODES:
                opts.warnings.append(
                    f"{source}: screens={low} is from an older version; the overlay always covers "
                    f"one monitor now, using {LEGACY_SCREEN_MODES[low]}"
                )
                low = LEGACY_SCREEN_MODES[low]
            if low not in SCREEN_MODES:
                opts.warnings.append(f"{source}: ignoring screens={raw!r} (expected one of {', '.join(SCREEN_MODES)})")
                continue
            opts.screens = low
        elif name == "screenshot_dir":
            opts.screenshot_dir = os.path.expandvars(value) or None
        else:
            opts.warnings.append(f"{source}: unknown [compat] key {key!r}")
            continue
        opts.sources[name] = source


def parse_options(argv, app_ini=None, user_ini_resolver=None):
    """Build Options from all layers.

    argv excludes the program name. Unrecognised arguments are kept in
    opts.qt_args and passed on to QApplication, so Qt's own flags still work.

    user_ini_resolver is called with the partially-built Options (portable
    known by then) and returns the user settings path; separate because
    that path depends on portable mode.
    """
    opts = Options()
    cli, unknown = build_arg_parser().parse_known_args(argv)
    opts.qt_args = unknown

    if app_ini is None:
        app_ini = paths.app_dir() / paths.APP_INI_NAME
    _apply_ini(opts, _read_ini_compat(app_ini), str(app_ini))

    if cli.portable is not None:
        opts.portable = cli.portable
        opts.sources["portable"] = "command line"

    if user_ini_resolver is not None:
        user_ini = user_ini_resolver(opts)
        user_values = _read_ini_compat(user_ini)
        # Portable is decided by the app-dir file or the command line only;
        # the user file lives in a location that depends on it.
        user_values.pop("portable", None)
        _apply_ini(opts, user_values, str(user_ini))

    mapping = {
        "debug": cli.debug,
        "safe_mode": cli.safe_mode,
        "input": cli.input,
        "screens": cli.screens,
        "keep_qt_env": cli.keep_qt_env,
        "diagnose": cli.diagnose,
        "selftest": cli.selftest,
        "screenshot_dir": cli.screenshot_dir,
    }
    if cli.screens in LEGACY_SCREEN_MODES:
        opts.warnings.append(
            f"command line: --screens {cli.screens} is from an older version; using "
            f"{LEGACY_SCREEN_MODES[cli.screens]}"
        )
        mapping["screens"] = LEGACY_SCREEN_MODES[cli.screens]
    for name, value in mapping.items():
        if value is not None:
            setattr(opts, name, value)
            opts.sources[name] = "command line"
    if cli.no_dialogs:
        opts.dialogs = False
        opts.sources["dialogs"] = "command line"
    if opts.selftest:
        opts.dialogs = False

    if opts.safe_mode:
        opts.input = "mouse"
        opts.screens = "primary"
        opts.sources["input"] = opts.sources["screens"] = "safe mode"
    return opts


def own_qt_dirs():
    """Directories holding this app's own Qt: the bundle, or the PySide6
    package from source. Found without importing PySide6."""
    dirs = []
    bundle = paths.bundle_dir()
    if bundle is not None:
        dirs.append(bundle)
    else:
        try:
            import importlib.util

            spec = importlib.util.find_spec("PySide6")
            if spec is not None and spec.submodule_search_locations:
                dirs.extend(Path(p) for p in spec.submodule_search_locations)
        except Exception:
            pass
    return dirs


def _points_into(value, dirs):
    parts = [p for p in value.split(os.pathsep) if p.strip()]
    if not parts or not dirs:
        return False
    for part in parts:
        try:
            resolved = Path(part).resolve()
        except Exception:
            return False
        if not any(resolved == d.resolve() or d.resolve() in resolved.parents for d in dirs):
            return False
    return True


def sanitize_qt_environment(keep=False, environ=None, own_dirs=None):
    """Remove inherited Qt variables. Returns [(name, old_value)] removed
    (or that would have been, if keep).

    Values pointing only inside our own Qt are left alone: PyInstaller's
    PySide6 runtime hook sets QT_PLUGIN_PATH and QML2_IMPORT_PATH to the
    bundle before any of our code runs.
    """
    environ = os.environ if environ is None else environ
    own_dirs = own_qt_dirs() if own_dirs is None else own_dirs
    found = []
    for name in QT_ENV_VARS_TO_CLEAR:
        if name in environ:
            if _points_into(environ[name], own_dirs):
                continue
            found.append((name, environ[name]))
            if not keep:
                del environ[name]
    return found


def platform_spec(opts):
    """QT_QPA_PLATFORM value for the chosen options, or None for Qt's
    default. Qt separates platform options with ':' (a ',' is silently
    ignored; checked against Qt 6.7).

    Nothing needs one at the moment. Qt 6.7 had a `nowmpointer` option that
    switched pens from Windows Ink to Wintab; Qt 6.8 removed it (it now
    logs "Unknown option"), and the replacement is a C++-only native
    interface PySide6 doesn't expose. So pens go through Windows Ink, and
    the fallback for a misbehaving tablet is input=mouse.
    """
    options = []
    if not options:
        return None
    return "windows:" + ":".join(options)


def apply_platform_spec(opts, argv):
    """Set QT_QPA_PLATFORM unless the user passed -platform themselves."""
    if any(a in ("-platform", "--platform") or a.startswith("-platform=") for a in argv):
        return None
    spec = platform_spec(opts)
    if spec:
        os.environ["QT_QPA_PLATFORM"] = spec
    return spec


def validate_bundle():
    """For the frozen build, list required files that are missing."""
    root = paths.bundle_dir()
    if root is None:
        return []
    return [rel for rel in REQUIRED_BUNDLE_FILES if not (root / rel).exists()]


def mark_of_the_web():
    """ZoneId from the exe's Zone.Identifier stream (3 = Internet), or None.
    Files extracted from a downloaded zip carry it, which is what triggers
    SmartScreen and some 'blocked by policy' behaviour."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        with open(sys.executable + ":Zone.Identifier", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().lower().startswith("zoneid="):
                    return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        return None
    return None


def choose_monitor(screens, primary, cursor, mode, previous=None, previous_lost=False, user_chose=False):
    """Pick the one monitor the overlay should cover.

    The overlay is always a single window inside a single monitor. Qt 6
    keeps each monitor's physical top-left corner as its logical origin and
    only scales its size, so with any scaling above 100% the logical layout
    has gaps or overlaps, and one window stretched across monitors gets
    positioned with one monitor's ratio and lands offset from the real
    screens (seen on a 200% + 175% + 100% setup: Qt geometry 0..11120 px,
    native window -2880..16580 px). A window inside one monitor gets that
    monitor's scale and lines up exactly, on every configuration.

    Works on any objects (QScreens in the app, strings in tests).
    screens: currently connected monitors. primary/cursor: the primary
    monitor and the one under the mouse (either may be None). mode:
    "cursor" or "primary". previous: the monitor currently covered, if any.
    previous_lost: it was just disconnected. user_chose: the user moved
    there with F7 or the toolbar.

    Rules:
    - Display changes (resolution, scale, another monitor plugged in) never
      move the overlay off the monitor it's on.
    - If that monitor is disconnected (projector unplugged), go to the
      primary monitor, which is predictable and where the toolbar is
      easiest to find.
    - Otherwise start on the monitor under the mouse (cursor mode) or the
      primary (primary mode).
    """
    if not screens:
        return None
    home = primary if primary in screens else screens[0]
    if previous_lost:
        return home
    if previous is not None and previous in screens:
        if mode == "cursor" or user_chose:
            return previous
    if mode == "primary":
        return home
    return cursor if cursor in screens else home


def next_monitor(ordered_screens, current):
    """The monitor after current, left to right, wrapping. None if there's
    nowhere else to go."""
    if len(ordered_screens) < 2:
        return None
    try:
        idx = ordered_screens.index(current)
    except ValueError:
        idx = -1
    return ordered_screens[(idx + 1) % len(ordered_screens)]
