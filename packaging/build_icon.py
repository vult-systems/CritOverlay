"""Bake crit_overlay/icon.py's procedural icon to assets/icon.ico.

Run once (or whenever the icon design changes) before packaging.exe build:
    python packaging/build_icon.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QGuiApplication

from crit_overlay.icon import build_app_icon


def main():
    app = QGuiApplication(sys.argv)
    icon = build_app_icon(256)

    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    assets_dir.mkdir(exist_ok=True)
    out = assets_dir / "icon.ico"

    pixmap = icon.pixmap(256, 256)
    ok = pixmap.save(str(out), "ICO")
    if not ok:
        print("Failed to save icon", file=sys.stderr)
        return 1
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
