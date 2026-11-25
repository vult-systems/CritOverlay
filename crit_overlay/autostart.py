"""Optional "Start with Windows", off by default.

A shortcut in the user's Startup folder, not a Run registry value: the app
writes nothing to the registry (see CLAUDE.md), the user can see and delete
it in Explorer, and lab images that reset the registry at logoff leave the
profile's Startup folder alone more often than not. The shortcut starts the
app with --hidden, so it waits in the tray instead of covering the screen
at logon.

The .lnk is written through the Shell's IShellLinkW and IPersistFile COM
interfaces, called through ctypes vtables, so no extra package is needed.
"""

import ctypes
import logging
import sys
from ctypes import wintypes
from pathlib import Path

from . import constants as C
from . import paths

logger = logging.getLogger(f"{C.ORG_NAME}.autostart")

FOLDERID_STARTUP = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"
SHORTCUT_NAME = f"{C.APP_NAME}.lnk"
START_HIDDEN_ARG = "--hidden"

_CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
_IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"
_CLSCTX_INPROC_SERVER = 0x1
_STGM_READ = 0x0
_MAX_PATH = 32768

# vtable slots
_RELEASE = 2
_SL_GET_PATH, _SL_SET_DESCRIPTION, _SL_SET_WORKING_DIR = 3, 7, 9
_SL_GET_ARGUMENTS, _SL_SET_ARGUMENTS, _SL_SET_ICON, _SL_SET_PATH = 10, 11, 17, 20
_PF_LOAD, _PF_SAVE = 5, 6


class AutostartError(Exception):
    pass


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(wintypes.LPCWSTR(text), ctypes.byref(g))
    return g


def _call(obj, slot, *args, argtypes=()):
    """Call vtable slot `slot` on COM pointer `obj`. HRESULT failures raise
    OSError (WINFUNCTYPE with HRESULT restype)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    return proto(vtbl[slot])(obj, *args)


def _release(obj):
    if obj:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[_RELEASE])(obj)


class _ShellLink:
    def __enter__(self):
        ole32 = ctypes.oledll.ole32
        # Qt has already initialized COM on this (GUI) thread as an STA;
        # this just takes another reference, or reports the existing mode.
        try:
            ole32.CoInitializeEx(None, 0x2)
            self._uninit = True
        except OSError:
            self._uninit = False
        self.link = ctypes.c_void_p()
        self.persist = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_SHELLLINK)), None, _CLSCTX_INPROC_SERVER,
                               ctypes.byref(_guid(_IID_ISHELLLINKW)), ctypes.byref(self.link))
        vtbl = ctypes.cast(self.link, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        qi = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
        qi(vtbl[0])(self.link, ctypes.byref(_guid(_IID_IPERSISTFILE)), ctypes.byref(self.persist))
        return self

    def __exit__(self, *exc):
        _release(self.persist)
        _release(self.link)
        if self._uninit:
            ctypes.oledll.ole32.CoUninitialize()
        return False

    def set(self, slot, text):
        _call(self.link, slot, text, argtypes=(wintypes.LPCWSTR,))

    def save(self, path):
        _call(self.persist, _PF_SAVE, str(path), True, argtypes=(wintypes.LPCWSTR, wintypes.BOOL))

    def load(self, path):
        _call(self.persist, _PF_LOAD, str(path), _STGM_READ, argtypes=(wintypes.LPCWSTR, wintypes.DWORD))

    def get_path(self):
        buf = ctypes.create_unicode_buffer(_MAX_PATH)
        _call(self.link, _SL_GET_PATH, buf, _MAX_PATH, None, 0,
              argtypes=(wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD))
        return buf.value

    def get_arguments(self):
        buf = ctypes.create_unicode_buffer(_MAX_PATH)
        _call(self.link, _SL_GET_ARGUMENTS, buf, _MAX_PATH, argtypes=(wintypes.LPWSTR, ctypes.c_int))
        return buf.value


def startup_folder():
    folder = paths.known_folder(FOLDERID_STARTUP)
    return Path(folder) if folder else None


def shortcut_path(folder=None):
    folder = folder or startup_folder()
    return folder / SHORTCUT_NAME if folder else None


def launch_command(portable=False):
    """(target, arguments, working directory) that starts this copy in the
    tray. The windowed exe, even if this run is the console one; from
    source, pythonw so no console window opens at logon."""
    app_dir = paths.app_dir()
    args = [START_HIDDEN_ARG] + (["--portable"] if portable else [])
    if getattr(sys, "frozen", False):
        windowed = app_dir / "CritOverlay.exe"
        target = windowed if windowed.is_file() else Path(sys.executable)
    else:
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        target = pythonw if pythonw.is_file() else exe
        args = ["-m", "crit_overlay"] + args
    return str(target), " ".join(args), str(app_dir)


def read_shortcut(path):
    """(target, arguments) of an existing shortcut, or None."""
    if path is None or not Path(path).is_file():
        return None
    with _ShellLink() as link:
        link.load(path)
        return link.get_path(), link.get_arguments()


def is_enabled(path=None, command=None):
    """True if the Startup shortcut exists and starts this copy. A shortcut
    left behind by a copy in another folder counts as off, so turning the
    option on repoints it here."""
    path = path or shortcut_path()
    try:
        found = read_shortcut(path)
    except OSError:
        logger.warning("Couldn't read %s", path, exc_info=True)
        return False
    if found is None:
        return False
    target = (command or launch_command())[0]
    same = _same_file(found[0], target)
    if not same:
        logger.info("Startup shortcut %s points to %s, not this copy (%s)", path, found[0], target)
    return same


def _same_file(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except Exception:
        return str(a).lower() == str(b).lower()


def enable(path=None, command=None):
    path = path or shortcut_path()
    if path is None:
        raise AutostartError("Windows didn't report a Startup folder for this account")
    target, arguments, workdir = command or launch_command()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _ShellLink() as link:
            link.set(_SL_SET_PATH, target)
            link.set(_SL_SET_ARGUMENTS, arguments)
            link.set(_SL_SET_WORKING_DIR, workdir)
            link.set(_SL_SET_DESCRIPTION, f"Start {C.APP_NAME} in the notification area")
            _call(link.link, _SL_SET_ICON, target, 0, argtypes=(wintypes.LPCWSTR, ctypes.c_int))
            link.save(path)
    except OSError as e:
        raise AutostartError(f"Couldn't create {path}: {e}") from e
    logger.info("Start with Windows on: %s -> %s %s", path, target, arguments)


def disable(path=None):
    path = path or shortcut_path()
    if path is None or not path.exists():
        return
    try:
        path.unlink()
    except OSError as e:
        raise AutostartError(f"Couldn't remove {path}: {e}") from e
    logger.info("Start with Windows off: removed %s", path)
