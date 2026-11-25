"""Global hotkey: parsing and the Win32 RegisterHotKey calls.

Standard library only, so compat.py can validate the configured hotkey
before Qt loads. The Qt side (receiving WM_HOTKEY) is in app.py.

RegisterHotKey is used with no window: Windows posts WM_HOTKEY to the
registering thread's message queue, and Qt's event dispatcher passes every
message it pulls from that queue to native event filters before
dispatching it. That keeps working while the overlay is hidden, and
doesn't depend on a window handle that Qt may recreate.
"""

import ctypes
import logging
from ctypes import wintypes

from . import constants as C

logger = logging.getLogger(f"{C.ORG_NAME}.hotkeys")

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

DISABLED = "none"

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "meta": MOD_WIN,
}
_MODIFIER_NAMES = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))

_NAMED_KEYS = {
    "space": (0x20, "Space"), "pause": (0x13, "Pause"), "insert": (0x2D, "Insert"), "ins": (0x2D, "Insert"),
    "delete": (0x2E, "Delete"), "del": (0x2E, "Delete"), "home": (0x24, "Home"), "end": (0x23, "End"),
    "pageup": (0x21, "PageUp"), "pgup": (0x21, "PageUp"), "pagedown": (0x22, "PageDown"),
    "pgdown": (0x22, "PageDown"), "pgdn": (0x22, "PageDown"),
}


class HotkeyError(ValueError):
    pass


def parse(text):
    """'Ctrl+Alt+D' -> (modifiers, virtual-key code, normalized text).
    Returns None for 'none' or empty (hotkey disabled). Raises HotkeyError
    with a plain-language reason otherwise.

    At least one of Ctrl, Alt or Win is required: a global Shift+letter or
    bare letter would stop that character from being typed anywhere.
    """
    raw = (text or "").strip()
    if raw.lower() in ("", DISABLED, "off", "disabled"):
        return None
    parts = [p.strip() for p in raw.replace(" ", "").split("+")]
    if any(not p for p in parts):
        raise HotkeyError(f"{raw!r} is not a key combination like Ctrl+Alt+D")
    *mod_parts, key_part = parts
    mods = 0
    for part in mod_parts:
        bit = _MODIFIERS.get(part.lower())
        if bit is None:
            raise HotkeyError(f"{part!r} in {raw!r} is not a modifier (use Ctrl, Alt, Shift or Win)")
        mods |= bit
    vk, key_name = _key(key_part, raw)
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise HotkeyError(f"{raw!r} needs Ctrl, Alt or Win, so it doesn't block normal typing")
    name = "+".join([n for bit, n in _MODIFIER_NAMES if mods & bit] + [key_name])
    return mods, vk, name


def _key(part, raw):
    low = part.lower()
    if len(part) == 1 and (part.isascii() and part.isalnum()):
        return ord(part.upper()), part.upper()
    if low[:1] == "f" and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        n = int(low[1:])
        return 0x6F + n, f"F{n}"
    if low in _NAMED_KEYS:
        return _NAMED_KEYS[low]
    if low in _MODIFIERS:
        raise HotkeyError(f"{raw!r} has no key after the modifiers")
    raise HotkeyError(f"{part!r} in {raw!r} isn't a supported key (A-Z, 0-9, F1-F24, Space, Insert, ...)")


def describe(text):
    """Display form of a configured hotkey, or None if disabled/invalid."""
    try:
        parsed = parse(text)
    except HotkeyError:
        return None
    return parsed[2] if parsed else None


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL


def register(hotkey_id, mods, vk):
    """Register for the calling thread. Returns 0 on success, otherwise
    the Win32 error code (ERROR_HOTKEY_ALREADY_REGISTERED when another app
    owns the combination)."""
    ctypes.set_last_error(0)
    if _user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
        return 0
    return ctypes.get_last_error() or -1


def unregister(hotkey_id):
    return bool(_user32.UnregisterHotKey(None, hotkey_id))


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


def hotkey_id_from_msg(address):
    """The hotkey id if the MSG at `address` is a WM_HOTKEY, else None."""
    try:
        msg = _MSG.from_address(int(address))
    except (TypeError, ValueError):
        return None
    if msg.message != WM_HOTKEY:
        return None
    return int(msg.wParam)


def post_test_hotkey(hotkey_id):
    """Post a WM_HOTKEY to this thread's own queue, as Windows does when the
    hotkey is pressed. Used by the self-test to check delivery end to end
    without pressing (or stealing) a real key combination."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    return bool(_user32.PostThreadMessageW(kernel32.GetCurrentThreadId(), WM_HOTKEY, hotkey_id, 0))
