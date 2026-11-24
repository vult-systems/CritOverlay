"""Environment report for troubleshooting on machines you can't sit at.

Logged in condensed form at every startup, and written in full by
`--diagnose` (and by the tray's "Save diagnostics report"). Everything is
read-only and best-effort: a probe that fails reports "unknown" rather than
raising, since this runs on exactly the machines where things fail.
"""

import json
import os
import platform
import sys
from datetime import datetime

from . import compat
from . import constants as C
from . import native_windows
from . import paths

_DPI_AWARENESS_NAMES = {0: "unaware", 1: "system", 2: "per-monitor"}


def _reg_value(root_name, key, value):
    try:
        import winreg

        root = getattr(winreg, root_name)
        with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return winreg.QueryValueEx(k, value)[0]
    except Exception:
        return None


def _reg_key_exists(root_name, key):
    try:
        import winreg

        root = getattr(winreg, root_name)
        with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return None


def windows_version():
    nt = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    product = _reg_value("HKEY_LOCAL_MACHINE", nt, "ProductName") or platform.platform()
    build = _reg_value("HKEY_LOCAL_MACHINE", nt, "CurrentBuildNumber")
    ubr = _reg_value("HKEY_LOCAL_MACHINE", nt, "UBR")
    display = _reg_value("HKEY_LOCAL_MACHINE", nt, "DisplayVersion") or _reg_value("HKEY_LOCAL_MACHINE", nt, "ReleaseId")
    edition = _reg_value("HKEY_LOCAL_MACHINE", nt, "EditionID")
    try:
        # ProductName still says "Windows 10" on Windows 11; the build
        # number is the reliable signal.
        if build and int(build) >= 22000 and "Windows 10" in str(product):
            product = str(product).replace("Windows 10", "Windows 11")
    except ValueError:
        pass
    return f"{product} {display or ''} (build {build}.{ubr}, edition {edition}, {platform.machine()})".replace("  ", " ")


def policy_hints():
    """Signals that enterprise policy may interfere. Presence only; most of
    these keys can't be read in detail by a standard user."""
    hints = {}
    hints["applocker_policy_present"] = _reg_key_exists("HKEY_LOCAL_MACHINE", r"SOFTWARE\Policies\Microsoft\Windows\SrpV2")
    # The Safer key exists on many stock installs; DefaultLevel is what
    # matters (0x40000 unrestricted, 0x20000 basic user, 0 disallowed).
    srp = _reg_value("HKEY_LOCAL_MACHINE", r"SOFTWARE\Policies\Microsoft\Windows\Safer\CodeIdentifiers", "DefaultLevel")
    hints["software_restriction_default_level"] = {
        None: "not configured", 0x40000: "unrestricted", 0x20000: "basic user", 0x10000: "untrusted", 0: "disallowed"
    }.get(srp, hex(srp) if isinstance(srp, int) else str(srp))
    cfa = _reg_value(
        "HKEY_LOCAL_MACHINE",
        r"SOFTWARE\Policies\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access",
        "EnableControlledFolderAccess",
    )
    if cfa is None:
        cfa = _reg_value(
            "HKEY_LOCAL_MACHINE",
            r"SOFTWARE\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access",
            "EnableControlledFolderAccess",
        )
    hints["controlled_folder_access"] = {None: "unknown", 0: "off", 1: "on", 2: "audit"}.get(cfa, str(cfa))
    hints["pen_wintab32_installed"] = os.path.exists(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Wintab32.dll"))
    hints["mark_of_the_web_zone"] = compat.mark_of_the_web()
    return hints


def build_info():
    root = paths.bundle_dir()
    if root is None:
        return None
    try:
        with open(root / "build_info.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def qt_section(overlay=None):
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, qVersion
    from PySide6.QtGui import QGuiApplication, QInputDevice

    lines = [f"PySide6 {pyside_version}, Qt runtime {qVersion()}"]
    app = QGuiApplication.instance()
    if app is None:
        lines.append("No QApplication")
        return lines
    lines.append(f"Platform plugin: {QGuiApplication.platformName()}")
    lines.append(f"QT_QPA_PLATFORM: {os.environ.get('QT_QPA_PLATFORM', '(default)')}")
    lines.append(f"Plugin path: {QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)}")
    lines.append(f"Library paths: {QCoreApplication.libraryPaths()}")
    try:
        lines.append(f"Style: {app.style().name()}")
    except Exception:
        pass
    awareness = native_windows.dpi_awareness()
    lines.append(f"Process DPI awareness: {_DPI_AWARENESS_NAMES.get(awareness, awareness)}")

    primary = QGuiApplication.primaryScreen()
    dprs = []
    lines.append("Monitors:")
    for s in QGuiApplication.screens():
        g = s.geometry()
        dprs.append(s.devicePixelRatio())
        lines.append(
            f"  {'*' if s is primary else ' '} {s.name()!s:24} {s.manufacturer()} {s.model()} | "
            f"logical {g.x()},{g.y()} {g.width()}x{g.height()} | scale {round(s.devicePixelRatio() * 100)}% | "
            f"{round(s.refreshRate())} Hz"
        )
    if len(set(round(d, 3) for d in dprs)) > 1:
        lines.append("Mixed display scaling across monitors")

    lines.append("Input devices:")
    try:
        devices = QInputDevice.devices()
        if not devices:
            lines.append("    (none registered yet; Qt adds pens and touch screens on their first event)")
        for d in devices:
            lines.append(f"    {d.name()!s:32} type={d.type().name} caps={_caps(d)} seat={d.seatName()}")
    except Exception as exc:
        lines.append(f"    unknown ({exc})")

    if overlay is not None:
        lines.extend(overlay.diagnostic_lines())
    return lines


def _caps(device):
    try:
        caps = device.capabilities()
        from PySide6.QtGui import QInputDevice

        names = [c.name for c in QInputDevice.Capability if c.value and (caps & c) == c and c.name not in ("All", "None")]
        return "+".join(names) or "none"
    except Exception:
        return "?"


def collect(opts=None, overlay=None, extra=None):
    """Full report as a list of lines."""
    lines = [
        f"{C.APP_NAME} {C.APP_VERSION} diagnostics, {datetime.now().isoformat(timespec='seconds')}",
        "=" * 72,
        f"Windows: {windows_version()}",
        f"Python: {sys.version.split()[0]} {platform.architecture()[0]}, frozen={bool(getattr(sys, 'frozen', False))}",
        f"Executable: {sys.executable}",
        f"Remote desktop session: {native_windows.is_remote_session()}",
        f"Running elevated: {native_windows.is_elevated()}",
    ]
    info = build_info()
    if info:
        lines.append(f"Build: {json.dumps(info, sort_keys=True)}")

    lines.append("Display adapters:")
    for name, attached, primary in native_windows.display_adapters():
        if attached:
            lines.append(f"    {name}{' (primary)' if primary else ''}")

    lines.append("Paths:")
    for k, v in paths.current().describe().items():
        lines.append(f"    {k}: {v}")
    for k, v in policy_hints().items():
        lines.append(f"    {k}: {v}")
    for note in paths.current().notes:
        lines.append(f"    note: {note}")

    if opts is not None:
        lines.append(f"Options: {opts.as_dict()}")
        if opts.sources:
            lines.append(f"Option sources: {opts.sources}")
        for w in opts.warnings:
            lines.append(f"Option warning: {w}")
        if opts.qt_args:
            lines.append(f"Arguments passed to Qt: {opts.qt_args}")

    if extra:
        lines.extend(extra)

    try:
        lines.extend(qt_section(overlay))
    except Exception as exc:
        lines.append(f"Qt section unavailable: {exc!r}")
    return lines


def write_report(lines):
    """Write to the log dir (or temp). Returns the path or None."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folders = [paths.current().log_dir, paths.temp_root()]
    for folder in folders:
        if folder is None:
            continue
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"diagnostics_{stamp}.txt"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return path
        except Exception:
            continue
    return None
