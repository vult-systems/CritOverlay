"""Where Crit Overlay reads and writes files, with fallbacks.

Standard library only, because logging and crash reporting depend on it and
both have to work before Qt is importable.

Managed machines break the obvious paths in several ways:

- Known folders (Pictures, AppData) get redirected to OneDrive or a UNC
  share, so `~/Pictures` may not be the real Pictures folder, or may not
  exist at all.
- Controlled Folder Access (Defender ransomware protection) blocks unknown
  apps from writing into Pictures and Documents. The write fails, often with
  no error beyond a toast from Defender.
- Roaming AppData on a network share can be offline or read-only.

So every write location is an ordered list of candidates, and callers take
the first one that works instead of assuming one path.
"""

import ctypes
import os
import sys
import tempfile
import uuid
from ctypes import wintypes
from pathlib import Path

from . import constants as C

# Known folder IDs, from KnownFolders.h.
FOLDERID_LOCAL_APPDATA = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"
FOLDERID_ROAMING_APPDATA = "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}"
FOLDERID_PICTURES = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"

# A file next to the exe (or the repo root, from source). Its [compat]
# section holds machine-wide defaults; `portable = true` in it keeps all
# data beside the app instead of in the user profile.
APP_INI_NAME = "CritOverlay.ini"
PORTABLE_DATA_DIR = "CritOverlay-data"
SETTINGS_FILE_NAME = f"{C.APP_NAME}.ini"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, text):
        u = uuid.UUID(text)
        guid = cls()
        guid.Data1, guid.Data2, guid.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            guid.Data4[i] = b
        return guid


def known_folder(folder_id):
    """Resolve a Windows known folder, following redirection. None on failure."""
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        out = ctypes.c_wchar_p()
        guid = _GUID.from_string(folder_id)
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        try:
            if hr != 0 or not out.value:
                return None
            return Path(out.value)
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:
        return None


def app_dir():
    """Directory holding the exe when frozen, or the repo root from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir():
    """PyInstaller's extracted-bundle dir (`_internal`), or None from source."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def is_writable_dir(path):
    """Create path if needed and prove a file can be written and removed."""
    try:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        probe.unlink()
        return True
    except Exception:
        return False


def first_writable(candidates):
    for candidate in candidates:
        if candidate is not None and is_writable_dir(candidate):
            return Path(candidate)
    return None


def _env_path(name):
    value = os.environ.get(name)
    return Path(value) if value else None


def _dedupe(paths):
    seen = set()
    out = []
    for p in paths:
        if p is None:
            continue
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(Path(p))
    return out


def temp_root():
    try:
        return Path(tempfile.gettempdir())
    except Exception:
        return None


class AppPaths:
    """Resolved locations for one run. Built once at startup by init()."""

    def __init__(self, portable=False):
        self.portable = portable
        self.app_dir = app_dir()
        self.local_appdata = known_folder(FOLDERID_LOCAL_APPDATA) or _env_path("LOCALAPPDATA")
        self.roaming_appdata = known_folder(FOLDERID_ROAMING_APPDATA) or _env_path("APPDATA")
        self.pictures = known_folder(FOLDERID_PICTURES)
        self.notes = []

        portable_root = self.app_dir / PORTABLE_DATA_DIR
        if portable and not is_writable_dir(portable_root):
            self.notes.append(f"Portable mode requested but {portable_root} is not writable; using the user profile")
            self.portable = False

        self.data_dir = self._resolve_data_dir(portable_root)
        self.log_dir = first_writable(
            _dedupe([self.data_dir / "logs" if self.data_dir else None] + [d / "logs" for d in self._fallback_data_roots()])
        )
        self.settings_path = self._resolve_settings_path(portable_root)

    def _fallback_data_roots(self):
        tmp = temp_root()
        return _dedupe([
            self.local_appdata / C.ORG_NAME if self.local_appdata else None,
            tmp / C.ORG_NAME if tmp else None,
            Path.home() / f".{C.ORG_NAME}",
        ])

    def _resolve_data_dir(self, portable_root):
        if self.portable:
            return portable_root
        found = first_writable(self._fallback_data_roots())
        if found is None:
            self.notes.append("No writable data directory found; logs and crash reports are disabled")
        return found

    def _resolve_settings_path(self, portable_root):
        if self.portable:
            return portable_root / SETTINGS_FILE_NAME
        # Same file QSettings(IniFormat, UserScope, ORG, APP) used before
        # 1.5.0, so existing settings carry over.
        candidates = []
        if self.roaming_appdata:
            candidates.append(self.roaming_appdata / C.ORG_NAME)
        if self.data_dir:
            candidates.append(self.data_dir)
        for folder in _dedupe(candidates):
            if is_writable_dir(folder):
                return folder / SETTINGS_FILE_NAME
        self.notes.append("No writable settings location; brush settings will not persist")
        fallback = candidates[0] if candidates else (temp_root() or Path.cwd())
        return fallback / SETTINGS_FILE_NAME

    def screenshot_dir_candidates(self, override=None):
        """Folders to try, in order, when saving a screenshot.

        Not probed up front: Controlled Folder Access raises a Defender
        notification on every blocked write, so we only find out by trying
        to save, and move on to the next candidate if that fails.
        """
        home_pictures = Path.home() / "Pictures"
        tmp = temp_root()
        return _dedupe([
            Path(override) if override else None,
            self.data_dir / "Screenshots" if self.portable and self.data_dir else None,
            self.pictures / C.ORG_NAME if self.pictures else None,
            home_pictures / C.ORG_NAME,
            self.local_appdata / C.ORG_NAME / "Screenshots" if self.local_appdata else None,
            tmp / C.ORG_NAME / "Screenshots" if tmp else None,
        ])

    def describe(self):
        return {
            "app_dir": str(self.app_dir),
            "bundle_dir": str(bundle_dir()) if bundle_dir() else "(source)",
            "portable": self.portable,
            "data_dir": str(self.data_dir) if self.data_dir else "(none)",
            "log_dir": str(self.log_dir) if self.log_dir else "(none)",
            "settings_path": str(self.settings_path),
            "local_appdata": str(self.local_appdata),
            "roaming_appdata": str(self.roaming_appdata),
            "pictures_known_folder": str(self.pictures),
        }


_current = None


def init(portable=False):
    global _current
    _current = AppPaths(portable=portable)
    return _current


def current():
    """The AppPaths built at startup, building a default one if needed
    (tests and tools that skip the launcher)."""
    global _current
    if _current is None:
        _current = AppPaths()
    return _current
