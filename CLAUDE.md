# CLAUDE.md

Crit Overlay is a transparent drawing overlay for Windows 10/11. It is built with PySide6 and shipped as a self-contained PyInstaller onedir build, released as a zip from CI. The global standards apply; this file holds what's specific to this repo and wins where they differ.

## Commands

```
python -m unittest discover -s tests -t .                  # unit tests (offscreen Qt, fake monitors)
python -m pip install --require-hashes --only-binary=:all: -r packaging/requirements-lint.txt
python -m pyflakes crit_overlay packaging tests             # lint, as CI runs it
python -W error -m compileall -q -f crit_overlay packaging tests
python packaging/check_docs.py                              # Markdown links and anchors
python -m crit_overlay --selftest                           # real run from source; exit 0 = pass
packaging\build.bat                                         # locked build + verification + zip + SHA256SUMS
```

- `build.bat` launches on-screen self-tests that move across every monitor. Use `--no-run` when there is no desktop session.
- A local `build.bat` is the only check that tours real multi-monitor and mixed-DPI hardware. CI runners have one monitor at 100%.
- Bound manual runs of the app (hang tests, `--selftest` by hand) with a Job object set to kill on close, and check afterwards that no `CritOverlay`/`python` process is left. Don't use Git Bash `timeout`: on 2026-11-24 it failed to end a hung run and then spun a CPU core for over a day.

## CI

- `.github/workflows/ci.yml` runs on pull requests, pushes to `main` and by hand. The required check is `ci`. What runs depends on the changed files:
  - docs only (`docs/`, top-level `*.md`, `LICENSE`): the docs link check;
  - `tests/` also: lint and the unit tests (windows-2025/2022, Python 3.13.17 and 3.12 with the locked PySide6, Python 3.9 with PySide6 6.7.3);
  - anything else also: `packaging\build.bat` with self-tests on both runners, plus an informational reproducibility comparison.
  - A push to `main` reruns nothing when its pull request fully validated the identical tree (recorded as a `ci-validated-<tree>` artifact).
- `.github/workflows/release.yml` runs on `v*` tags and creates a **draft** release. It reuses a CI build of the same commit if one exists, builds once if the tree was fully validated, and otherwise runs all of CI. Changes to it are dry-run on their PR.
- Workflows call `build.bat`/`build.py` rather than copying build steps into YAML. Actions are pinned to commit SHAs; tool versions are hash-pinned in `packaging/requirements-*.txt`.

## Design decisions to preserve

Don't change these without an issue that discusses the trade-off:

- **One monitor at a time.** The overlay covers exactly one monitor. Qt can't place one window accurately across monitors with different scaling (see `docs/COMPATIBILITY.md#why-one-monitor-at-a-time`). F7, the toolbar monitor button and the tray switch monitors.
- **Pens through Windows Ink only.** Qt 6.8 removed Wintab. `--input mouse` is the fallback.
- **Portable and self-contained.** Standard user, no installer, no registry writes, no network access, no runtimes to install. Onedir, not onefile, and no UPX. Every dependency ships inside the build (see the dependency policy under Project rules).
- **Startup fails loudly.** `launcher.py` stays stdlib-only, so errors before Qt loads still show a message, write a crash report and return a distinct exit code.
- **Lives in the tray.** Esc hides when there's no capture (with a capture it discards it and starts a new selection); only the tray's Exit quits (Esc exits only when there's no tray). A saved F9 screenshot also hides, and the next show resumes that session. One copy per session. Start with Windows is a Startup-folder shortcut, not a registry value (#14).
- **Captures are pixel-aligned.** A capture is cropped from a full-monitor grab at its device-pixel edges (`QScreen.grabWindow` with a sub-rect rounds the size up at fractional scales), the margin is snapped to whole image pixels, and image and margin are painted into integer device rects from one edge-rounding rule in `capture.py`. Strokes are stored in the image's own coordinates, so fit scaling never lowers export resolution.
- **Settings carry over.** Bump `SETTINGS_VERSION` and add a migration in `config.py` rather than resetting user settings.

## Project rules

- **Dependencies:** prefer a mature, well-maintained open-source library to reinventing established functionality when it meaningfully improves quality, performance or maintainability. Judge each one on licensing, portability (it must ship inside the portable build), security, maintenance, integration complexity and practical benefit, not on keeping the dependency count low. Consequential dependency decisions still need the owner's approval.
- Docs to keep in sync: `README.md`, `docs/*.md`, `CHANGELOG.md`, `CLAUDE.md`, and `packaging/dist_extras/` (shipped to users; CRLF).
- In `docs/COMPATIBILITY.md`, a row moves from **Assumed** to **Tested** only after someone actually runs it, and the row records the configuration.
- Issue labels: `bug`, `enhancement`, `compatibility` (Windows/hardware/managed workstations), `documentation`, `tech-debt`, `release-blocker`, `awaiting-acceptance`.
- An issue stays open until its fix is verified where it was reported. Outstanding hardware or lab-machine checks go into the acceptance issue (#4).

## Versioning and releases

- The version lives only in `APP_VERSION` in `crit_overlay/constants.py`. `CHANGELOG.md` groups entries under **Changed / Added / Fixed / Settings / Build**; the release commit renames `## Unreleased` to `## vX.Y.Z` or `## vX.Y.Z-rc.N`, and the release workflow takes the draft notes from that section.
- Assets are the CI-built `CritOverlay-vX.Y.Z-win64.zip` and `SHA256SUMS.txt`. Never upload locally built zips: they differ from CI builds in the host-copied UCRT DLLs.
- Use a release candidate whenever hardware or environment validation is outstanding. Publish a stable release only after the manual checklist (`docs/COMPATIBILITY.md#manual-validation-required-before-release`, tracked as an issue) passes. Before any release, run a local `packaging\build.bat` on multi-monitor hardware and read its log.
- The toolchain is hash-locked in `packaging/requirements-build.txt`; change it only via `packaging/update_lock.py`. Builds use Python 3.12–3.13 (`SUPPORTED_BUILD_PYTHONS`; CI pins `BUILD_PYTHON`).
- Procedure: `docs/BUILDING.md#releasing`.

## Feature freeze: v1.5.0

In effect from 2026-11-25 until v1.5.0 is tagged stable after #4 passes. The acceptance baseline is **v1.5.0-rc.5** (commit c889c81).

The owner reopened the freeze once, on 2026-11-27, for the centered capture workflow (#23), which shipped in rc.5. The freeze resumed with rc.5 as the baseline; a further exception needs the owner's explicit approval.

- **Allowed:** fixes for confirmed bugs, regressions (against rc.5, rc.4 or v1.4.2), compatibility failures, and release blockers. Each needs an issue with a reproduction and the `release-blocker` label or a reason it's needed now. Docs corrections are allowed.
- **Not allowed:** new features or options, UI changes, refactoring, dependency or toolchain upgrades (#8). Non-blocking concerns get the `tech-debt` label (tracked in #19) instead of a change, including the unreachable live-region code (#26).
- **Releases:** no new RC without a validated reason: a fix that was reproduced and then verified. Without one, v1.5.0 is rc.5's code with only the version and changelog changed. Never re-tag, re-upload or delete rc.5, rc.4 or v1.4.2.
- Fixes shipped in an RC whose check is pending in #4 have the `awaiting-acceptance` label and close when their #4 item passes.
- Limitations shipping with v1.5.0 are listed in `docs/COMPATIBILITY.md#known-limitations-v150`.

## Current state

- **Repository settings:** `main` is protected: PR required (0 approvals), the `ci` check required, conversations resolved, no force-push or deletion. Merged branches are deleted automatically. Private vulnerability reporting is on (`SECURITY.md`). Renaming the `ci` job means updating the required check.
- **Releases:** v1.4.2 is the stable release. v1.5.0-rc.5 is the current pre-release and the frozen acceptance baseline, pending #4. It supersedes rc.4 (tray, hotkey, single instance, #14), rc.3, rc.2 (annotation margin fix, #13) and rc.1 (built locally before CI existed).
