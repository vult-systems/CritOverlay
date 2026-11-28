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
exes and zip. Across machines:

- **CI runner images** (Windows Server 2022 and 2025, same Python 3.13.17):
  every bundled file is identical except `build_info.json`, which records
  the build host.
- **CI vs a Windows 11 workstation:** the exes and the Universal CRT DLLs
  (`ucrtbase.dll`, `api-ms-win-*.dll`) differ. PyInstaller copies the UCRT
  from the build host, and CI bundles 3 more `api-ms-win-*` forwarders.
  Both bundles pass the same audit and self-tests.

So release assets come from CI (see [Releasing](#releasing)). A local
build is for testing.

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

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, and by
hand (Actions > CI > Run workflow, which runs everything). Jobs run only when
the changed files can affect them:

| Job | What | Runs when the change touches |
|---|---|---|
| docs | `packaging/check_docs.py`: relative links and `#anchors` in every Markdown file | anything |
| lint | pyflakes (hash-pinned in `packaging/requirements-lint.txt`) and a compile with warnings as errors | anything outside `docs/`, top-level `*.md` and `LICENSE` |
| test | Unit tests on windows-2025 and windows-2022: Python 3.13.17 and 3.12 with the locked PySide6 6.8.3, and Python 3.9 with PySide6 6.7.3 (oldest source configuration) | as lint |
| build | `packaging\build.bat` itself (with `CRIT_PYTHON` and `CRIT_NO_PAUSE=1`) on windows-2025 and windows-2022: locked toolchain, static DLL audit, self-tests, zip and `SHA256SUMS.txt`, uploaded as artifacts. Starts after lint and tests pass. | as lint, except changes only under `tests/` |
| reproducibility | Compares every file in the two runners' zips; only `build_info.json` (build host) should differ. Informational only; it doesn't fail the run. | after both builds |
| ci | The required check. Fails if change detection or any job that ran failed or was cancelled. | always |

Pull requests are compared with their base, and pushes to `main` with the
previous `main` commit. Unrecognised paths count as code, so they get the
full run.

When lint, every test and both builds pass, `ci` uploads a small
`ci-validated-<tree hash>` artifact (kept 90 days). A push to `main` whose
tree has one (the normal case for a pull request merged without `main`
moving underneath it) runs nothing further, because those exact files were
already tested. Only records from this repository's own branches count.

pip downloads are cached per lock file. The cache only saves downloads:
locked installs still check every wheel against its hash. A newer push to a
pull request cancels the older run; runs on `main` are never cancelled.

The hosted runners are Windows Server 2022 and 2025, single monitor at
100%. CI therefore can't stand in for client Windows 10/11, mixed-DPI
monitors, pens, or managed lab machines. That stays a manual gate (below).

## Releasing

1. In one release commit, update `APP_VERSION` in
   `crit_overlay/constants.py` and rename `## Unreleased` in
   `CHANGELOG.md` to `## vX.Y.Z` (or `## vX.Y.Z-rc.N`). A stable release
   after release candidates gets its own `## vX.Y.Z` section. Merge the
   commit to `main` through a pull request with CI green.
2. Tag that commit on `main` and push the tag:
   `git tag -a v1.5.0-rc.2 -m "..."` and `git push origin v1.5.0-rc.2`.
3. `.github/workflows/release.yml` first checks that the tag matches
   `APP_VERSION`, that it is `vX.Y.Z` or `vX.Y.Z-rc.N`, that the tagged
   commit is on `main`, and that the changelog has the section. It then
   gets the windows-2025 release zip from the first of:
   - **reuse:** a successful CI run on `main` (a push that wasn't skipped,
     or a manual run) already built that exact commit, so source, version
     and toolchain lock are identical;
   - **build:** a pull request fully validated the commit's tree, so it
     runs `build.bat` once on windows-2025, with its audit and self-tests;
   - **full:** otherwise, all of CI.

   It checks that the zip was built as that version and verifies its
   checksum, then creates a **draft** release with the zip,
   `SHA256SUMS.txt`, and the changelog section plus hashes as notes.
   Release candidates are drafted as pre-releases. If any check fails, no
   release is created. A pull request that changes `release.yml` runs it as
   a dry run: the build and the checks, without a draft.
4. Edit the draft notes (Verified / Not yet verified / known issues) and
   publish it by hand.
   - **Release candidate:** publish as a pre-release, without making it
     the latest release.
   - **Stable release:** publish only after the [manual
     validation](COMPATIBILITY.md#manual-validation-required-before-release)
     checklist passes on real hardware and a lab machine.

Release assets come from CI, not from a local build. A local
`packaging\build.bat` is still the way to test the build and to run the
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
4. Run the [release checklist](COMPATIBILITY.md#manual-validation-required-before-release) and
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
