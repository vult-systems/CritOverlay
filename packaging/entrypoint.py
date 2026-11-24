"""PyInstaller entry point.

Pointing Analysis straight at crit_overlay/__main__.py runs it as a bare
top-level script with no parent package, which breaks its relative imports.
Importing the package here does what `python -m crit_overlay` does.

The import itself is guarded: if the bundle is damaged badly enough that
the package can't even be imported, the windowed exe would otherwise exit
with no message at all. Only ctypes is used for that report.
"""

import sys
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fallback_report(text):
    try:
        import os
        import tempfile

        path = os.path.join(tempfile.gettempdir(), "CritOverlay_startup_error.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        path = None
    try:
        import ctypes

        msg = "Crit Overlay couldn't start because part of the program is missing or damaged.\n\n"
        msg += "Re-extract the whole zip to a local folder and try again.\n\n"
        if path:
            msg += f"Details were saved to:\n{path}\n\n"
        msg += text.strip().splitlines()[-1] if text.strip() else ""
        ctypes.windll.user32.MessageBoxW(None, msg, "Crit Overlay: Startup error", 0x10 | 0x10000 | 0x40000)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from crit_overlay.launcher import main
    except Exception:
        _fallback_report(traceback.format_exc())
        raise SystemExit(1)
    raise SystemExit(main())
