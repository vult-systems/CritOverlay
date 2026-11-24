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

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | What |
|---|---|
| lint | pyflakes (hash-pinned in `packaging/requirements-lint.txt`) and a compile with warnings as errors |
| test | Unit tests on windows-2025 and windows-2022: Python 3.13.17 and 3.12 with the locked PySide6 6.8.3, and Python 3.9 with PySide6 6.7.3 (oldest source configuration) |
| build | `packaginguild.bat` itself (with `CRIT_PYTHON` and `CRIT_NO_PAUSE=1`) on windows-2025 and windows-2022: locked toolchain, static DLL audit, self-tests, zip and `SHA256SUMS.txt`, uploaded as artifacts |
| reproducibility | Compares the two runners' hashes. Informational only; it doesn't fail the run. |

The hosted runners are Windows Server 2022 and 2025, single monitor at
100%. CI therefore can't stand in for client Windows 10/11, mixed-DPI
monitors, pens, or managed lab machines. That stays a manual gate (below).

## Releasing

1. Update `APP_VERSION` in `crit_overlay/constants.py` and add a
   `## vX.Y.Z` (or `## vX.Y.Z-rc.N`) section to `CHANGELOG.md`. Commit and
   push to `main`.
2. Tag that commit and push the tag: `git tag -a v1.5.0-rc.2 -m "..."` and
   `git push origin v1.5.0-rc.2`.
3. `.github/workflows/release.yml` runs the full CI. If every job passes,
   it checks that the tag matches `APP_VERSION`, that the tag is `vX.Y.Z`
   or `vX.Y.Z-rc.N`, that the changelog has the section, and the zip's
   checksum. Then it creates a **draft** release with the windows-2025
   zip, `SHA256SUMS.txt`, and the changelog section plus hashes as notes.
   Release candidates are drafted as pre-releases. If any check fails,
   no release is created.
4. Edit the draft notes (Verified / Not yet verified / known issues) and
   publish it by hand.
   - **Release candidate:** publish as a pre-release, without making it
     the latest release.
   - **Stable release:** publish only after the [manual
     validation](COMPATIBILITY.md#manual-validation-required-before-release)
     checklist passes on real hardware and a lab machine.

Release assets come from CI, not from a local build. A local
`packaginguild.bat` is still the way to test the build and to run the
self-test across several real monitors.

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
