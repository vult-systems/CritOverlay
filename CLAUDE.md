# CLAUDE.md

Crit Overlay is a transparent drawing overlay for Windows 10/11. It is built with PySide6 and shipped as a self-contained PyInstaller onedir build. These standards are the default workflow for all work in this repo.

## Commands

```
python -m unittest discover -s tests -t .   # unit tests (offscreen Qt, fake monitors)
python -m pyflakes crit_overlay packaging tests
python -m crit_overlay --selftest           # real run from source; exit 0 = pass
packaging\build.bat                         # locked build + verification + zip + SHA256SUMS
```

- Run the unit tests on both supported combinations: Python 3.9 + PySide6 6.7 (`python`) and Python 3.13 + PySide6 6.8 (`py -3.13`).
- `build.bat` launches on-screen self-tests that move across every monitor. Use `--no-run` when there is no desktop session.
- CI (`.github/workflows/ci.yml`) runs lint, the test matrix and `build.bat` on every push and PR. Keep it green, and reuse `build.bat`/`build.py` in workflows rather than copying build steps into YAML.
- Pin workflow actions to commit SHAs with the version in a comment.

## Design decisions to preserve

Don't change these without an issue that discusses the trade-off:

- **One monitor at a time.** The overlay covers exactly one monitor. Qt can't place one window accurately across monitors with different scaling (see `docs/COMPATIBILITY.md#why-one-monitor-at-a-time`). F7, the toolbar monitor button and the tray switch monitors.
- **Pens through Windows Ink only.** Qt 6.8 removed Wintab. `--input mouse` is the fallback.
- **Portable and self-contained.** Standard user, no installer, no registry writes, no network access, no runtimes to install. Onedir, not onefile, and no UPX.
- **Startup fails loudly.** `launcher.py` stays stdlib-only, so errors before Qt loads still show a message, write a crash report and return a distinct exit code.
- **Settings carry over.** Bump `SETTINGS_VERSION` and add a migration in `config.py` rather than resetting user settings.

## Change discipline

- Keep changes small and on-task. Don't refactor unrelated code, add dependencies, or build abstractions for hypothetical needs.
- Existing behaviour and UI stay unless an issue says otherwise.
- Every new dependency must justify itself. Runtime dependencies are PySide6-Essentials only.
- Keep docs in sync with the code in the same change. That covers `README.md`, `docs/*.md`, `CHANGELOG.md`, and `packaging/dist_extras/` (shipped to users; CRLF).
- In `docs/COMPATIBILITY.md`, a row moves from **Assumed** to **Tested** only after someone actually runs it, and the row records the configuration.

## Issues and commits

- Track bugs, features and tech debt as GitHub Issues. Search existing issues first to avoid duplicates.
  - Labels: `bug`, `enhancement`, `compatibility` (Windows/hardware/managed workstations), `documentation`.
- When work touches an issue, comment with:
  - the root cause, as checked against the code (correct the issue's theory if it was wrong);
  - what was implemented, with commit hashes;
  - what was verified and how;
  - what is still unverified.
- **Never close an issue, or mark a fix resolved, until the fix is verified** in the environment where it was reported. Hardware or lab-machine checks still outstanding mean it stays open and the check goes into the acceptance issue.
- Commits are focused (one concern each), descriptive (what and why), and reference issues with `Refs #N`.
  - Use `Fixes #N` only when the fix is verified: on push to `main` it closes the issue.
  - Each commit should leave the app importable and runnable.

## Versioning and changelog

- Semantic versioning. The version lives only in `APP_VERSION` in `crit_overlay/constants.py`. The spec and `build.py` read it from there.
- Tags are `vX.Y.Z`, with release candidates as `vX.Y.Z-rc.N`.
- `CHANGELOG.md` has a newest-first `## vX.Y.Z` section per release, grouped under **Changed / Added / Fixed / Settings / Build** as needed.
  - Entries are concise and technical: user-visible effect plus root cause, with issue numbers.
  - Unreleased versions are folded into the release that actually ships.

## Releasing

Full procedure: `docs/BUILDING.md#releasing`.

1. Run the unit tests, pyflakes, and a local `packaging\build.bat`; the local build is the only one that tours real multi-monitor/mixed-DPI hardware. Read its log for warnings.
2. In the final commit before the tag, bump `APP_VERSION` and turn `## Unreleased` into `## vX.Y.Z` (or `-rc.N`) in `CHANGELOG.md`. Push `main`.
3. Push an annotated tag `vX.Y.Z` or `vX.Y.Z-rc.N`.
   - `release.yml` reruns all of CI and creates a **draft** release only if everything passes and the tag matches `APP_VERSION` and the changelog.
   - Assets are the CI-built `CritOverlay-vX.Y.Z-win64.zip` and `SHA256SUMS.txt`. Never upload locally built zips to a release.
4. Edit the draft notes (changes, **Verified**, **Not yet verified**, known issues, SHA-256), then publish it by hand.
   - **Use a release candidate (pre-release, not Latest) whenever hardware or environment validation is outstanding.**
   - Publish a stable release only after the manual acceptance checklist passes (`docs/COMPATIBILITY.md` > "Manual validation required before release", tracked as an issue). CI cannot substitute for it.
5. Build reproducibility must hold. The toolchain is hash-locked in `packaging/requirements-build.txt`; change it only via `packaging/update_lock.py`. Builds use Python 3.12–3.13 (`SUPPORTED_BUILD_PYTHONS`; CI pins `BUILD_PYTHON`).

Current state: v1.4.2 is stable. v1.5.0-rc.1 is pending acceptance in #4.
