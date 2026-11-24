"""Crash reporting that works before (and without) Qt.

The windowed exe has no console, so an exception raised before the UI exists
used to vanish, or at best surface as PyInstaller's generic "Unhandled
exception in script" box. This module, standard library only:

- writes a crash report file with the traceback and environment summary,
- shows it in a native Win32 message box (no Qt needed),
- enables faulthandler, so a native crash inside a Qt DLL or plugin still
  leaves a Python stack in faulthandler.log,
- keeps a "session running" marker, so the next launch can tell that the
  previous one died without exiting cleanly.
"""

import ctypes
import faulthandler
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import constants as C

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000

_FAULT_LOG_MAX_BYTES = 512_000
_SESSION_HEADER = "--- session start "
_WATCHDOG_ARMED = "startup watchdog armed"

_state = {
    "dialogs": True,
    "crash_dir": None,
    "fault_file": None,
    "marker": None,
    "previous_startup_hung": False,
}


def configure(crash_dir, dialogs=True):
    _state["crash_dir"] = Path(crash_dir) if crash_dir else None
    _state["dialogs"] = dialogs


def native_message_box(title, text, icon=MB_ICONERROR):
    """Win32 MessageBoxW. Works with no QApplication, no console, and no Qt
    DLLs at all. Returns False if it couldn't be shown."""
    if not _state["dialogs"]:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        user32.MessageBoxW(None, text, title, MB_OK | icon | MB_SETFOREGROUND | MB_TOPMOST)
        return True
    except Exception:
        return False


def environment_summary():
    lines = [
        f"{C.APP_NAME} {C.APP_VERSION}",
        f"Time: {datetime.now().isoformat(timespec='seconds')}",
        f"Python: {sys.version.split()[0]} ({platform.architecture()[0]})",
        f"Frozen: {bool(getattr(sys, 'frozen', False))}",
        f"Executable: {sys.executable}",
        f"Arguments: {sys.argv[1:]}",
        f"Working directory: {_safe(os.getcwd)}",
        f"Windows: {platform.platform()} build={_safe(lambda: sys.getwindowsversion().build)}",
    ]
    qt_vars = {k: v for k, v in os.environ.items() if k.upper().startswith(("QT_", "QML", "PYTHON"))}
    if qt_vars:
        lines.append("Qt/Python environment variables at crash time:")
        lines.extend(f"  {k}={v}" for k, v in sorted(qt_vars.items()))
    return "\n".join(lines)


def _safe(fn):
    try:
        return fn()
    except Exception as exc:
        return f"<unavailable: {exc}>"


def write_crash_report(text, kind="crash"):
    """Write text plus an environment summary to a timestamped file.
    Returns the path, or None if nowhere was writable."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    body = f"{environment_summary()}\n\n{text}\n"
    candidates = []
    if _state["crash_dir"]:
        candidates.append(_state["crash_dir"])
    tmp = os.environ.get("TEMP") or os.environ.get("TMP")
    if tmp:
        candidates.append(Path(tmp) / C.ORG_NAME)
    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{kind}_{stamp}_{os.getpid()}.txt"
            path.write_text(body, encoding="utf-8")
            return path
        except Exception:
            continue
    return None


def report_fatal(title, message, exc_text=None, hint=None):
    """Last-resort handler: log what we can, write a report, tell the user."""
    detail = message if exc_text is None else f"{message}\n\n{exc_text}"
    path = write_crash_report(detail)
    try:
        import logging

        logging.getLogger(f"{C.ORG_NAME}.crash").critical("%s\n%s", title, detail)
        for handler in logging.getLogger(C.ORG_NAME).handlers:
            handler.flush()
    except Exception:
        pass

    text = message
    if hint:
        text += f"\n\n{hint}"
    if path:
        text += f"\n\nA crash report was saved to:\n{path}"
    if exc_text:
        # Enough of the traceback to be useful in a screenshot, not so much
        # that the box runs off the screen.
        tail = exc_text.strip().splitlines()[-12:]
        text += "\n\nDetails:\n" + "\n".join(tail)
    if not native_message_box(f"{C.APP_NAME}: {title}", text):
        try:
            print(f"{title}\n{text}", file=sys.stderr)
        except Exception:
            pass
    return path


def format_current_exception():
    return traceback.format_exc()


def enable_faulthandler(crash_dir):
    """Dump Python stacks to faulthandler.log on native crashes (access
    violations in Qt plugins, aborts from qFatal). The windowed exe has no
    stderr, so faulthandler needs an explicit file to work at all."""
    if not crash_dir:
        return None
    try:
        path = Path(crash_dir) / "faulthandler.log"
        _state["previous_startup_hung"] = _last_session_hung(path)
        if path.exists() and path.stat().st_size > _FAULT_LOG_MAX_BYTES:
            path.unlink()
        f = open(path, "a", encoding="utf-8")
        f.write(f"\n{_SESSION_HEADER}{datetime.now().isoformat(timespec='seconds')} pid={os.getpid()} ---\n")
        f.flush()
        faulthandler.enable(file=f, all_threads=True)
        _state["fault_file"] = f
        return path
    except Exception:
        return None


def _last_session_hung(path):
    """True if the previous session in faulthandler.log was ended by the
    startup watchdog."""
    try:
        if not path.exists():
            return False
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - 64_000))
            tail = f.read().decode("utf-8", errors="replace")
        last = tail.rsplit(_SESSION_HEADER, 1)[-1]
        return _WATCHDOG_ARMED in last and "Timeout (" in last
    except Exception:
        return False


def previous_startup_hung():
    return _state["previous_startup_hung"]


def arm_startup_watchdog(seconds):
    """If startup hasn't called disarm_startup_watchdog() within `seconds`,
    dump all thread stacks to faulthandler.log and exit.

    This exists because some Qt failures (seen with an unusable platform
    plugin) block inside the QApplication constructor while holding the
    GIL: no Python thread can run, no message box appears, and the process
    just sits there invisibly. faulthandler's watchdog is a native thread
    and doesn't need the GIL. The next launch sees the dump and tells the
    user (previous_startup_hung)."""
    f = _state.get("fault_file")
    if f is None or seconds <= 0:
        return False
    try:
        f.write(f"{_WATCHDOG_ARMED} ({seconds:g}s)\n")
        f.flush()
        # Dump only; faulthandler's own exit=True calls _exit(), which runs
        # DLL detach code, and in the observed hang that blocks as well.
        faulthandler.dump_traceback_later(seconds, exit=False, file=f)
        _state["terminate_timer"] = _arm_native_terminate(seconds + 3)
        return True
    except Exception:
        return False


def _arm_native_terminate(seconds):
    """Kill this process after `seconds` with TerminateProcess, from a
    native timer-queue thread. No Python code runs when it fires, so it
    works while the main thread holds the GIL, and TerminateProcess skips
    DLL detach, so it can't deadlock in Qt's teardown the way exit() can.

    The timer callback type is (PVOID parameter, BOOLEAN fired), which lines
    up with TerminateProcess(HANDLE process, UINT exit_code): the parameter
    is the current-process pseudo-handle (-1). Returns the timer handle, or
    None if it couldn't be created (Windows only, best effort)."""
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateTimerQueueTimer
        create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.ULONG]
        create.restype = wintypes.BOOL
        terminate_addr = ctypes.cast(kernel32.TerminateProcess, ctypes.c_void_p)
        WT_EXECUTEONLYONCE = 0x8
        WT_EXECUTEINTIMERTHREAD = 0x20
        timer = wintypes.HANDLE()
        current_process = ctypes.c_void_p(-1)
        if not create(ctypes.byref(timer), None, terminate_addr, current_process,
                      int(seconds * 1000), 0, WT_EXECUTEONLYONCE | WT_EXECUTEINTIMERTHREAD):
            return None
        return timer
    except Exception:
        return None


def _cancel_native_terminate():
    timer = _state.pop("terminate_timer", None)
    if timer is None:
        return
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        delete = kernel32.DeleteTimerQueueTimer
        delete.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE]
        delete.restype = wintypes.BOOL
        # INVALID_HANDLE_VALUE: wait for a callback already in flight, so
        # the timer is definitely gone when this returns.
        delete(None, timer, wintypes.HANDLE(-1))
    except Exception:
        pass


def disarm_startup_watchdog():
    try:
        _cancel_native_terminate()
        faulthandler.cancel_dump_traceback_later()
        f = _state.get("fault_file")
        if f is not None:
            f.write("startup completed\n")
            f.flush()
    except Exception:
        pass


def _pid_alive(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def begin_session(crash_dir):
    """Drop a per-process marker for this run. Returns the start times of
    earlier runs whose marker is still there but whose process is gone,
    meaning they died without reaching end_session(). Markers belonging to
    a second instance that's still running are left alone."""
    if not crash_dir:
        return []
    unclean = []
    try:
        for old in Path(crash_dir).glob("session.*.running"):
            try:
                pid = int(old.name.split(".")[1])
            except (IndexError, ValueError):
                pid = -1
            if pid != os.getpid() and _pid_alive(pid):
                continue
            try:
                unclean.append(old.read_text(encoding="utf-8").strip())
            except Exception:
                unclean.append(old.name)
            try:
                old.unlink()
            except Exception:
                pass
        marker = Path(crash_dir) / f"session.{os.getpid()}.running"
        marker.write_text(f"pid={os.getpid()} started={datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
        _state["marker"] = marker
    except Exception:
        pass
    return unclean


def end_session():
    marker = _state.get("marker")
    if marker is not None:
        try:
            marker.unlink()
        except Exception:
            pass
        _state["marker"] = None
    f = _state.get("fault_file")
    if f is not None:
        try:
            _cancel_native_terminate()
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()
            f.close()
        except Exception:
            pass
        _state["fault_file"] = None
