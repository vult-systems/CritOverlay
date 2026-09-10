"""Direct Win32 extended-window-style manipulation for click-through.

Qt's WA_TransparentForMouseEvents attribute does not reach the native
WS_EX_TRANSPARENT style for this window's configuration (frameless Tool
window plus WA_TranslucentBackground), so clicks kept landing on the
overlay during pass-through. Setting the style bit directly is what makes
Windows route clicks to whatever is underneath.
"""

import ctypes

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020

_user32 = ctypes.windll.user32


def set_click_through(hwnd, enabled):
    style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new_style = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
    if new_style != style:
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
