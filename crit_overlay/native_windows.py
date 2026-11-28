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


_ASFW_ANY = -1


def allow_foreground_handoff():
    """Let the next window another process opens take the foreground
    (AllowSetForegroundWindow(ASFW_ANY)). Only works while this process
    may set the foreground itself, e.g. it received the last input event,
    which is the case right after the user presses F9. Without it Windows
    may open Explorer behind other windows and only flash its taskbar
    button. Returns True on success."""
    try:
        _user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        _user32.AllowSetForegroundWindow.restype = wintypes.BOOL
        return bool(_user32.AllowSetForegroundWindow(wintypes.DWORD(_ASFW_ANY & 0xFFFFFFFF)))
    except Exception:
        logger.debug("AllowSetForegroundWindow failed", exc_info=True)
        return False


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



_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL
_k32.GetCurrentThreadId.argtypes = []
_k32.GetCurrentThreadId.restype = wintypes.DWORD
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]
_k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

def foreground_info():
    """(foreground hwnd, its process id, its executable name) for logs and
    the input diagnostic. Name is "" when it can't be read."""
    try:
        hwnd = _user32.GetForegroundWindow()
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ""
        handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            try:
                buf = ctypes.create_unicode_buffer(520)
                size = wintypes.DWORD(len(buf))
                if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = buf.value.rsplit("\\", 1)[-1]
            finally:
                _k32.CloseHandle(handle)
        return int(hwnd or 0), pid.value, name
    except Exception:
        logger.debug("foreground_info failed", exc_info=True)
        return 0, 0, ""


def is_foreground(hwnd):
    try:
        return int(_user32.GetForegroundWindow() or 0) == int(hwnd)
    except Exception:
        return False


def claim_foreground(hwnd):
    """Make `hwnd` the foreground window. Tablet drivers (Wacom and others)
    pick Windows Ink or mouse emulation from the foreground application's
    settings, so until this window is foreground a pen may reach it as a
    plain mouse, without pressure.

    SetForegroundWindow is allowed while this process has the user's most
    recent input or was just started or activated by it (hotkey, tray,
    launch); otherwise Windows refuses silently. In that case, briefly
    attaching to the foreground thread's input lets it through. Returns
    True when the window is foreground afterwards."""
    try:
        hwnd = wintypes.HWND(int(hwnd))
        if is_foreground(hwnd.value):
            return True
        if _user32.SetForegroundWindow(hwnd) and is_foreground(hwnd.value):
            return True
        fg = _user32.GetForegroundWindow()
        fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        our_thread = _k32.GetCurrentThreadId()
        attached = bool(fg_thread) and fg_thread != our_thread and \
            bool(_user32.AttachThreadInput(our_thread, fg_thread, True))
        try:
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _user32.AttachThreadInput(our_thread, fg_thread, False)
        return is_foreground(hwnd.value)
    except Exception:
        logger.debug("claim_foreground failed", exc_info=True)
        return False
