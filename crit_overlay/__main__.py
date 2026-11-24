"""`python -m crit_overlay` entry point.

Kept free of Qt imports so a broken or missing PySide6 is reported by the
launcher instead of failing at import time. See launcher.py.
"""

from .launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
