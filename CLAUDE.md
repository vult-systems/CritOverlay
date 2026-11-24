# CLAUDE.md

Crit Overlay is a transparent drawing overlay for Windows 10/11. It is built with PySide6 and shipped as a self-contained PyInstaller onedir build. These standards are the default workflow for all work in this repo.

## Commands

```
python -m unittest discover -s tests -t .                  # unit tests (offscreen Qt, fake monitors)
python -m pip install --require-hashes --only-binary=:all: -r packaging/requirements-lint.txt
python -m pyflakes crit_overlay packaging tests             # lint, as CI runs it
python -W error -m compileall -q -f crit_overlay packaging tests
python -m crit_overlay --selftest                           # real run from source; exit 0 = pass
packaging\build.bat                                         # locked build + verification + zip + SHA256SUMS
```

- `build.bat` launches on-screen self-tests that move across every monitor. Use `--no-run` when there is no desktop session.
- A local `build.bat` is the only check that tours real multi-monitor and mixed-DPI hardware. CI runners have one monitor at 100%.

## CI

- `.github/workflows/ci.yml` runs on every push and PR:
  - lint (pyflakes and compileall with warnings as errors);
  - unit tests on windows-2025/2022 with Python 3.13.17 and 3.12 against the locked PySide6, and Python 3.9 with PySide6 6.7.3;
  - `packaging\build.bat` on both runners, including self-tests, with artifacts uploaded;
  - an informational file-by-file reproducibility comparison.
- `.github/workflows/release.yml` runs on `v*` tags. It reruns CI, then creates a **draft** release. Changes to it are dry-run on their PR.
- Workflows call `build.bat`/`build.py` rather than copying build steps into YAML.
- Actions are pinned to commit SHAs, with the version in a comment. Tool versions are hash-pinned in `packaging/requirements-*.txt`.

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
- Keep docs in sync with the code in the same change. That covers `README.md`, `docs/*.md`, `CHANGELOG.md`, `CLAUDE.md`, and `packaging/dist_extras/` (shipped to users; CRLF).
- In `docs/COMPATIBILITY.md`, a row moves from **Assumed** to **Tested** only after someone actually runs it, and the row records the configuration.

## Issues, branches and commits

- Track bugs, features and tech debt as GitHub Issues. Search existing issues first to avoid duplicates.
  - Labels: `bug`, `enhancement`, `compatibility` (Windows/hardware/managed workstations), `documentation`.
- When work touches an issue, comment with:
  - the root cause, as checked against the code (correct the issue's theory if it was wrong);
  - what was implemented, with commit hashes;
  - what was verified and how;
  - what is still unverified.
- **Never close an issue, or mark a fix resolved, until the fix is verified** in the environment where it was reported. Hardware or lab-machine checks still outstanding mean it stays open and the check goes into the acceptance issue.
- Work on a branch and open a PR that references its issues.
  - Merge only when every CI check is green, using a merge commit so the tested, focused commits land unchanged.
  - Confirm CI passes on `main` after merging.
- Commits are focused (one concern each), descriptive (what and why), and reference issues with `Refs #N`.
  - Use `Fixes #N` only when the fix is verified: merging to `main` closes the issue.
  - Each commit should leave the app importable and runnable.

## Versioning and changelog

- Semantic versioning. The version lives only in `APP_VERSION` in `crit_overlay/constants.py`. The spec and `build.py` read it from there.
- Tags are `vX.Y.Z`, with release candidates as `vX.Y.Z-rc.N`. Only tag commits on `main`. Don't move or delete published tags.
- `CHANGELOG.md` is newest first. New work goes under `## Unreleased`, grouped under **Changed / Added / Fixed / Settings / Build** as needed.
  - The release commit renames that section to `## vX.Y.Z` or `## vX.Y.Z-rc.N`.
  - A stable release after RCs gets its own `## vX.Y.Z` section. The release workflow takes the draft notes from it and fails without it.
  - Entries are concise and technical: user-visible effect plus root cause, with issue numbers.

## Releasing

Full procedure: `docs/BUILDING.md#releasing`.

1. Before release: CI is green on `main`. Run a local `packaging\build.bat` on multi-monitor hardware and read its log for warnings.
2. The release commit (via PR) bumps `APP_VERSION` and renames the changelog section.
3. Push an annotated tag `vX.Y.Z` or `vX.Y.Z-rc.N` on that commit.
   - `release.yml` reruns all of CI. It creates a **draft** only if everything passes, the tag matches `APP_VERSION` and the format, the changelog section exists, and the checksum verifies.
   - Assets are the CI-built `CritOverlay-vX.Y.Z-win64.zip` and `SHA256SUMS.txt`. Never upload locally built zips, because they differ from CI builds in the host-copied UCRT DLLs.
4. Edit the draft notes (changes, **Verified**, **Not yet verified**, known issues, SHA-256), then publish it by hand.
   - **Use a release candidate (pre-release, not Latest) whenever hardware or environment validation is outstanding.**
   - Publish a stable release only after the manual acceptance checklist passes (`docs/COMPATIBILITY.md` > "Manual validation required before release", tracked as an issue). CI cannot substitute for it.
5. Reproducible builds: the toolchain is hash-locked in `packaging/requirements-build.txt`; change it only via `packaging/update_lock.py`. Builds use Python 3.12–3.13 (`SUPPORTED_BUILD_PYTHONS`; CI pins `BUILD_PYTHON`).

## Current state

- **Releases:** v1.4.2 is the stable release. v1.5.0-rc.2 (the first CI-built release) is the current pre-release, pending acceptance in #4. It supersedes v1.5.0-rc.1, which was built locally before CI existed.
- **#7:** stays open until the first tag-driven draft release succeeds.
