"""Win32 calls made through ctypes: click-through, and system facts for
diagnostics.

Every function here declares argtypes/restype. Without them ctypes passes
Python ints as 32-bit C ints, which truncates 64-bit handles and pointer-
sized values, and the failure looks like a random crash on some machines
and not others.

Click-through: Qt's WA_TransparentForMouseEvents attribute does not reach
the native WS_EX_TRANSPARENT style for this window's configuration
(frameless Tool window plus WA_TranslucentBackground), so clicks kept
landing on the overlay during pass-through. Setting the style bit directly
is what makes Windows route clicks to whatever is underneath.
"""

import ctypes
import logging
from ctypes import wintypes

from . import constants as C

logger = logging.getLogger(f"{C.ORG_NAME}.native")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
SM_REMOTESESSION = 0x1000
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# GetWindowLongPtrW only exists as an export on 64-bit Windows; 32-bit
# user32 exposes the same thing as GetWindowLongW.
_get_long = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
_set_long = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
_get_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_long.restype = ctypes.c_ssize_t
_set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_long.restype = ctypes.c_ssize_t


def get_ex_style(hwnd):
    ctypes.set_last_error(0)
    return _get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)


def set_click_through(hwnd, enabled):
    """Set or clear WS_EX_TRANSPARENT. Returns True if the window now has
    the requested state. Never raises; failures are logged."""
    try:
        style = get_ex_style(hwnd)
        if style == 0 and ctypes.get_last_error():
            logger.warning("GetWindowLongPtr failed (error %s)", ctypes.get_last_error())
            return False
        new_style = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
        if new_style != style:
            ctypes.set_last_error(0)
            prev = _set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, new_style)
            if prev == 0 and ctypes.get_last_error():
                logger.warning("SetWindowLongPtr failed (error %s)", ctypes.get_last_error())
                return False
        return bool(get_ex_style(hwnd) & WS_EX_TRANSPARENT) == bool(enabled)
    except Exception:
        logger.exception("set_click_through failed")
        return False


def window_rect(hwnd):
    """Native window rect in physical pixels, as (left, top, right, bottom)."""
    rect = wintypes.RECT()
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def set_window_rect(hwnd, rect):
    """Move/resize a window to (left, top, right, bottom) physical pixels
    without changing z-order or activation. Returns True on success."""
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    try:
        fn = _user32.SetWindowPos
        fn.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       wintypes.UINT]
        fn.restype = wintypes.BOOL
        left, top, right, bottom = rect
        return bool(fn(wintypes.HWND(hwnd), None, left, top, right - left, bottom - top,
                       SWP_NOZORDER | SWP_NOACTIVATE))
    except Exception:
        logger.exception("SetWindowPos failed")
        return False


def is_window_visible(hwnd):
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    return bool(_user32.IsWindowVisible(wintypes.HWND(hwnd)))


def trim_working_set():
    """Let Windows page out what this process isn't using right now. Called
    when the overlay hides to the tray: nothing is lost (pages come back on
    demand when it's shown), but an idle tray app stops holding on to RAM
    it touched while drawing. Returns True on success."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
        kernel32.SetProcessWorkingSetSize.restype = wintypes.BOOL
        minus_one = ctypes.c_size_t(-1).value
        return bool(kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), minus_one, minus_one))
    except Exception:
        logger.debug("SetProcessWorkingSetSize failed", exc_info=True)
        return False


def is_remote_session():
    try:
        _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        return bool(_user32.GetSystemMetrics(SM_REMOTESESSION))
    except Exception:
        return None


def dpi_awareness():
    """Process DPI awareness: 0 unaware, 1 system, 2 per-monitor. None if
    the API is missing (Windows 10 before 1607)."""
    try:
        _user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        _user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        ctx = _user32.GetThreadDpiAwarenessContext()
        return _user32.GetAwarenessFromDpiAwarenessContext(ctx)
    except Exception:
        return None


def is_elevated():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


def display_adapters():
    """[(adapter description, attached, primary)]. Shows at a glance whether
    the machine is on a real GPU, the Microsoft Basic Display Adapter (no
    driver), or a remote/virtual display adapter."""
    out = []
    try:
        fn = _user32.EnumDisplayDevicesW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DISPLAY_DEVICEW), wintypes.DWORD]
        i = 0
        while i < 32:
            dev = _DISPLAY_DEVICEW()
            dev.cb = ctypes.sizeof(dev)
            if not fn(None, i, ctypes.byref(dev), 0):
                break
            attached = bool(dev.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)
            primary = bool(dev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE)
            out.append((dev.DeviceString, attached, primary))
            i += 1
    except Exception:
        logger.debug("EnumDisplayDevicesW failed", exc_info=True)
    return out
