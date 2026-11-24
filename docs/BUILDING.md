# Building

## Quick build

```
packaging\build.bat
```

Produces:

- `dist\CritOverlay\`: the app folder (`CritOverlay.exe`,
  `CritOverlay-console.exe`, `_internal\`, plus README-FIRST.txt, an
  example INI and two helper .bat files)
- `dist\CritOverlay-v<version>-win64.zip`: the release archive
- `dist\SHA256SUMS.txt`: hashes of the zip and both exes

Requirements on the build machine:

- **64-bit Python 3.13** (3.12 is also accepted). The build Python is
  bundled into the app as its runtime, so the build refuses versions that no
  longer get security fixes (3.9 and 3.10 are past end of life;
  `--allow-unsupported-python` overrides, for experiments only). A per-user
  install needs no admin rights:
  `winget install Python.Python.3.13 --scope user`.
  `build.bat` uses `py -3.13`; set `CRIT_PYTHON` to a `python.exe` path to
  use a different one.
- PyPI access, or a wheelhouse (below). No compiler, no admin rights.

`packaging\venv_setup.py` puts the toolchain into `.venv-build\`, separate
from any other Python packages on the machine. It recreates the venv when a
different Python made it (e.g. after upgrading from the old 3.9 toolchain).

`build.bat` launches `CritOverlay-console.exe --selftest` three times, which
shows the overlay for a few seconds each time (moving it across every monitor). Use
`packaging\build.bat --no-run` on a machine without a desktop session; that
still runs the static checks.

## What makes it reproducible

- **Hash-pinned toolchain.** `packaging\requirements-build.txt` pins every
  package (PySide6-Essentials, shiboken6, PyInstaller and its dependencies)
  to an exact version and SHA-256. pip installs with `--require-hashes
  --only-binary=:all:`, so the build can only use those exact wheel files,
  and nothing is compiled locally.
- **Toolchain check.** `build.py` refuses to build if the installed versions
  don't match the lock.
- **Deterministic inputs.** `PYTHONHASHSEED=0`, and `SOURCE_DATE_EPOCH` set
  to the last commit's time. The zip uses fixed timestamps and file order.
- **Recorded provenance.** `build_info.json` in the bundle records version,
  commit, whether the tree was dirty, and the toolchain versions. It shows up
  in every diagnostics report and log.

Two consecutive clean builds on the same machine produced byte-identical
exes and zip. Cross-machine reproducibility (different Windows build or
Python patch version) hasn't been checked. Python patch versions in
particular can differ.

## Verification (`packaging\verify_build.py`)

Runs automatically at the end of the build:

1. Required files present; stripped files (`opengl32sw.dll`,
   `Qt6Network.dll`, the TUIO network-input plugin, QML/Quick) absent.
2. **Static DLL audit**: every DLL imported by any bundled .exe/.dll/.pyd
   must be in the bundle or be a DLL that ships with every Windows 10
   1809+ install. This catches the "works on my machine because the VC++
   redistributable happens to be installed" class of failure without a
   clean VM. Also checks everything is x64.
3. Self-test in three environments: scrubbed (minimal PATH, no Python or
   Qt variables), polluted (junk `QT_*`/`PYTHON*` variables and a fake
   `qwindows.dll` and Python DLL on PATH), and from a folder whose name
   has spaces and non-ASCII characters.

## Offline / restricted-network builds

On a machine with internet access:

```
py -3.13 -m pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 ^
    -r packaging\requirements-build.txt -d wheelhouse
```

(Match `--python-version` to the build Python.) Copy
`wheelhouse\` into the repo root on the offline machine. `build.bat` notices
it and installs with `--no-index`. The hashes still have to match.

## Updating dependencies

1. Edit `TOP_LEVEL` in `packaging\update_lock.py` (PySide6-Essentials,
   PyInstaller, setuptools).
2. `py -3.13 packaging\update_lock.py` regenerates the lock with hashes.
3. Run `packaging\build.bat` (it reinstalls the venv from the new lock).
4. Run the [release checklist](COMPATIBILITY.md#release-checklist) and
   update the Tested table.

The build Python range is `SUPPORTED_BUILD_PYTHONS` in
`packaging\venv_setup.py`. Moving to Python 3.14 also needs a PySide6
release that supports it (6.8.x stops at 3.13), which is a Qt upgrade.

Qt minor-version upgrades change platform behaviour. 6.8 removed the
`nowmpointer` (Wintab) platform option that 6.7 had, for example. The
self-test catches a lot of this, but not pen behaviour.

## Unit tests

```
python -m unittest discover -s tests -t .
```

Standard library `unittest` only. Qt tests use the offscreen platform, so no
window appears. They cover option layering, environment sanitizing,
screen-mode decisions, path fallbacks with simulated unwritable/redirected
folders, settings validation, crash-marker bookkeeping, and HiDPI layer
sizing.

## Running from source

```
python -m pip install --user -r requirements.txt
python -m crit_overlay
```

or `run.bat` (`run_debug.bat` for console logging). `--user` installs need
no admin rights. `requirements.txt` asks for `PySide6-Essentials`; an
existing full `PySide6` install also satisfies it.
