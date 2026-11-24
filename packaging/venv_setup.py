"""Create or refresh .venv-build and install the hash-locked toolchain.

Run with the build Python (build.bat does this):
    py -3.13 packaging/venv_setup.py

Refuses unsupported Pythons: the build Python is bundled into the app as its
runtime, so it has to be one that still gets security fixes. Recreates the
venv when it was made by a different Python version. Installs from
wheelhouse/ with --no-index when that folder exists (offline builds).
"""

import platform
import subprocess
import sys
import venv
from pathlib import Path

# In upstream support and supported by the pinned PySide6 6.8.x (which
# stops at 3.13). 3.13 is the reference: it resolved the lock and is what
# the release is tested with.
SUPPORTED_BUILD_PYTHONS = ((3, 12), (3, 13))

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv-build"
VENV_PY = VENV / "Scripts" / "python.exe"
LOCK = ROOT / "packaging" / "requirements-build.txt"
WHEELHOUSE = ROOT / "wheelhouse"


def supported_text():
    return " or ".join(f"{a}.{b}" for a, b in SUPPORTED_BUILD_PYTHONS)


def main():
    if sys.version_info[:2] not in SUPPORTED_BUILD_PYTHONS or sys.maxsize <= 2**32:
        print(f"Need 64-bit Python {supported_text()} to build; this is {platform.python_version()} "
              f"({platform.architecture()[0]}) at {sys.executable}.")
        print("Install it per-user (no admin):  winget install Python.Python.3.13 --scope user")
        print('or point CRIT_PYTHON at one:     set "CRIT_PYTHON=C:\\path\\to\\python.exe"')
        return 1

    want = platform.python_version()
    have = None
    if VENV_PY.exists():
        try:
            have = subprocess.run([str(VENV_PY), "-c", "import platform; print(platform.python_version())"],
                                  capture_output=True, text=True, timeout=60).stdout.strip() or None
        except Exception:
            have = None
    if have != want:
        print(f".venv-build uses Python {have}; recreating it with {want}" if have
              else f"Creating .venv-build with Python {want}")
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)

    cmd = [str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check", "--require-hashes",
           "--only-binary=:all:", "-r", str(LOCK)]
    if WHEELHOUSE.is_dir():
        print("Installing build tools offline from wheelhouse\\")
        cmd += ["--no-index", "--find-links", str(WHEELHOUSE)]
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
