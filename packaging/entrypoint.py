"""PyInstaller entry point.

Pointing Analysis straight at crit_overlay/__main__.py runs it as a bare
top-level script with no parent package, which breaks its relative imports.
Importing the package here does what `python -m crit_overlay` does.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crit_overlay.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
