"""One running copy per Windows session.

Standard library only: the launcher checks this before loading Qt, so a
second launch exits in a fraction of a second instead of starting a second
overlay (#14).

A named mutex marks the running copy. Windows releases it when that process
ends for any reason, including a crash, so there is no stale lock to clean
up. A named auto-reset event is how a second launch asks the running copy
to show itself; app.py waits on it with QWinEventNotifier.

Names are in the Local\\ namespace: per logon session, so users on a shared
or remote-desktop machine each get their own copy.
"""

import ctypes
import logging
from ctypes import wintypes

from . import constants as C

logger = logging.getLogger(f"{C.ORG_NAME}.instance")

MUTEX_NAME = f"Local\\{C.ORG_NAME}.Instance"
SHOW_EVENT_NAME = f"Local\\{C.ORG_NAME}.ShowRequest"
ERROR_ALREADY_EXISTS = 183
ASFW_ANY = 0xFFFFFFFF

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.SetEvent.argtypes = [wintypes.HANDLE]
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


class Instance:
    """Result of acquire(). Keep it alive for the life of the process."""

    def __init__(self, mutex, show_event, already_running, error=None):
        self.mutex = mutex
        self.show_event = show_event
        self.already_running = already_running
        self.error = error

    def request_show(self):
        """From a second launch: ask the running copy to show its overlay.
        Also lets it take the foreground, which Windows otherwise refuses to
        a background process."""
        if not self.show_event:
            return False
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(wintypes.DWORD(ASFW_ANY))
        except Exception:
            pass
        return bool(_kernel32.SetEvent(self.show_event))

    def close(self):
        for name in ("show_event", "mutex"):
            handle = getattr(self, name)
            if handle:
                _kernel32.CloseHandle(handle)
                setattr(self, name, None)


_current = None


def acquire(mutex_name=MUTEX_NAME, event_name=SHOW_EVENT_NAME):
    """Claim the single-instance mutex. If it can't be created at all (a
    locked-down policy), report not-running and let this copy start: a
    possible duplicate is better than refusing to run."""
    global _current
    # Event first, so a second launch can always find it once the mutex
    # exists.
    event = _kernel32.CreateEventW(None, False, False, event_name)
    ctypes.set_last_error(0)
    mutex = _kernel32.CreateMutexW(None, False, mutex_name)
    err = ctypes.get_last_error()
    if not mutex:
        logger.warning("Couldn't create the single-instance mutex (error %s); not checking for another copy", err)
        result = Instance(None, event, False, error=err)
    else:
        result = Instance(mutex, event, err == ERROR_ALREADY_EXISTS)
    if not result.already_running:
        _current = result
    return result


def current():
    """The Instance this process owns, or None (self-test, diagnose, or a
    second launch)."""
    return _current
