# Changelog

## v1.5.0-rc.2 (pre-release)

Second release candidate for v1.5.0. It adds the drawing region and the
first CI-built release; everything listed under v1.5.0-rc.1 also applies.
v1.4.2 stays the stable release until the acceptance checks in #4 pass.

**Added**

- **Drawing region and annotation margin** (#10). Press R (or use the new
  region button on the toolbar) and drag a rectangle to limit drawing to
  part of the monitor.
  - Strokes start only inside the region and are clipped at its edge. The
    clip is display-only, so going back to the full monitor shows them
    again in full.
  - The backdrop covers only the region.
  - An optional margin around the region (thickness, white/black, opacity,
    independent of the backdrop intensity) takes handwritten notes.
  - F9 then saves exactly the region and margin at device resolution, with
    the margin rendered solid so no surrounding desktop is captured.
  - The region is kept in the overlay's logical coordinates, so it lines up
    at any scale (checked against native screen grabs at 200%, 175% and
    100%), and is moved inside a smaller monitor on F7.
  - Esc now cancels an area selection before it exits the app. Margin
    settings are saved; the region is per session.

**Fixed**

- The brush cursor ring could appear in a screenshot if the mouse moved
  during the capture delay.

**Build**

- GitHub Actions CI on every push and pull request (#7). It runs pyflakes,
  compiles with warnings as errors, and runs the unit tests on Windows
  Server 2022/2025 with Python 3.13, 3.12 (locked PySide6 6.8.3) and 3.9
  (PySide6 6.7.3). It also runs `packaging\build.bat` on both runners and
  uploads the zip and `SHA256SUMS.txt`.
- Pushing a version tag creates a draft release from the CI-built zip, only
  if every check passes and the tag matches `APP_VERSION` and the
  changelog. Publishing stays manual, after hardware acceptance for stable
  releases.

## v1.5.0-rc.1 (pre-release)

Hardening for school and work machines. This is a release candidate:
v1.4.2 stays the stable release until the hardware and lab-machine checks
in docs/COMPATIBILITY.md ("Manual validation required before release") are
done. What was tested where is in the same file.

**Changed**

- The overlay now covers **exactly one monitor**, on every display
  configuration, by design (#2). Qt keeps each monitor's physical origin and
  only scales its size, so one window stretched across monitors with display
  scaling was positioned with one monitor's ratio and landed offset from the
  screens: on a 200% + 175% + 100% setup, x=-2880..16580 px instead of
  0..11120. Strokes and the toolbar didn't line up with the pen or cursor.
  A window inside one monitor lines up exactly. It starts on the monitor
  under the mouse (`--screens primary` for the primary).
- Strokes on scaled monitors are drawn at the monitor's pixel density
  instead of at 100% and upscaled, so they're sharp at 150% or 200%.
- With Qt 6.8, pens are read only through Windows Ink. If a Wacom driver has
  "Use Windows Ink" off, the pen works as a mouse (no pressure) until it's
  turned on for CritOverlay.exe.

**Added**

- A **monitor button** on the toolbar (next to pass-through) that moves the
  overlay to the next monitor; **F7** and the tray do the same. The button
  only appears with more than one monitor, and its tooltip shows which
  monitor you're on. Annotations move with the overlay.
- `--safe-mode` (mouse input, primary monitor) and `--input mouse` for
  misbehaving tablet drivers.
- Pens whose driver reports zero pressure are detected and drawn at default
  width instead of hairlines.
- `--diagnose`: report with Windows build, GPUs, monitors and scales, input
  devices, paths, and policy hints (AppLocker, Controlled Folder Access,
  mark of the web). Also in the tray under Troubleshooting.
- `--selftest`: starts for real, tours every monitor with the monitor button
  (alignment, toolbar placement, canvas scale, drawing on each), clicks
  through the toolbar, runs pass-through on → monitor switch → off, checks
  capture and folder access, and exits 0 or 5.
- Inherited `QT_*` environment variables (often set machine-wide by Maya,
  Houdini, Nuke and similar) are cleared at startup and logged.
  `--keep-qt-env` keeps them.
- Portable mode and machine-wide defaults through `CritOverlay.ini` next to
  the exe.
- `CritOverlay-console.exe` beside the main exe, for diagnostics and
  `--debug` output.
- Every pass-through transition is logged with the Qt and native
  click-through state and the focused window, for reports of an
  unresponsive toolbar (#3).

**Fixed**

- Display hot-plug: unplugging the monitor the overlay is on moves it to the
  primary monitor. Plugging a monitor in leaves it where it is, with a toast
  pointing at F7. Bursts of display events (a projector connecting fires
  several) are handled once, after they settle.
- After any move, the app compares where Windows actually put the window
  with where it belongs, and re-places it natively once if they differ.
- Switching monitors in the middle of a stroke commits the stroke instead
  of carrying it across.
- A failure before the window appeared (missing DLL, broken Qt plugin,
  unwritable log folder) closed the windowed exe with no message at all.
  Startup is now guarded from the first line: a plain-language message box,
  a crash report file, and distinct exit codes, without needing Qt.
- Qt can hang invisibly inside startup when its platform plugin is unusable.
  A native watchdog now ends a startup stuck for 60 s, and the next launch
  says so.
- Screenshots failed outright when Pictures was blocked by Controlled Folder
  Access or redirected to an unavailable share. They now fall back to
  `%LOCALAPPDATA%\CritOverlay\Screenshots` (then `%TEMP%`) and say where they
  went. Known folders are resolved through Windows, so OneDrive/UNC
  redirection is followed.
- Logging, settings and crash reports fall back to other locations when
  `%LOCALAPPDATA%`/`%APPDATA%` aren't writable, instead of crashing.
- A hand-edited or corrupt settings file could stop the app starting
  (e.g. an unknown backdrop value) or give a near-invisible brush (an
  unparseable opacity read as 0). Values are now validated (#1).
- Win32 calls declare their argument and return types (pointer-sized
  `GetWindowLongPtrW`/`SetWindowLongPtrW`), and click-through failures are
  logged instead of ignored.
- `run.bat` failed from network (UNC) paths. It now uses `pushd`, and finds
  whichever installed Python has PySide6.

**Settings**

- Settings files now carry a version (`[meta] settings_version`, #1).
  Files from v1.0–v1.4.2 are stamped in place on first launch; brush
  settings are kept as they were. Files from a newer version are left alone,
  and read-only files still load.

**Build**

- Toolchain on **Python 3.13.17** (from 3.9, which is past end of life and
  was bundled into the app as its runtime), PySide6-Essentials 6.8.3 and
  PyInstaller 6.22.3, hash-pinned in `packaging/requirements-build.txt` and
  installed into a private venv. The build refuses Python versions outside
  3.12–3.13. Two builds of one commit are byte-identical.
- Post-build verification: static audit that every bundled binary only
  imports bundled or stock-Windows DLLs, plus self-tests in a scrubbed
  environment, a deliberately polluted one, and a non-ASCII path.
- Release zip with SHA-256 sums for IT allow-listing; Windows version
  resource on the exes.
- Dropped `Qt6Network.dll` and the TUIO network-input plugin from the
  bundle. Depends on PySide6-Essentials instead of the full PySide6.
- Unit tests under `tests/` (64, on two fake offscreen monitors for the
  monitor logic), run on Qt 6.7 and 6.8.

## v1.4.2

- Pass-through now actually passes clicks through. Qt's
  `WA_TransparentForMouseEvents` never reached the native
  `WS_EX_TRANSPARENT` style for this window's flags, so clicks kept landing
  on the overlay. Set directly via `SetWindowLongW` instead.

## v1.4.1

- Added a badge during pass-through that takes you back to drawing. Once you
  clicked into the app underneath, it took keyboard focus and F8 stopped
  reaching the overlay, which left killing the process as the only way out.
  The badge is its own top-level window, so it stays clickable.

## v1.4.0

- Fixed the stutter when dragging the brush sliders. Every setter forced a
  `QSettings.sync()` disk flush, on every tick of the drag. The write is now
  debounced, with an immediate flush on exit.
- The backdrop is a real layer instead of being baked into the same pixmap as
  your strokes. Erasing now takes strokes off it rather than punching through
  to the desktop.
- Added an eyedropper: toolbar button, `I` for a one-shot pick, or Alt+click
  to sample without leaving Brush or Eraser.

## v1.3.0

- Rewrote the in-progress stroke renderer to work incrementally. It used to
  re-smooth every point in the stroke and reallocate its whole offscreen
  buffer on each frame, so long strokes got slower the longer they got.
- Added a backdrop intensity slider (10 to 100%, defaults to 50%). It was
  hardcoded fully opaque before.
- Switched packaging from onefile to onedir and dropped Qt modules and DLLs
  the app never loads. Smaller, and it no longer unpacks itself to a temp
  directory on every launch.

## v1.2.0

- Fixed the eraser, which had been doing nothing visible. The canvas pixmap
  came back without an alpha channel, so `CompositionMode_Clear` was silently
  discarded.
- Translucent strokes no longer darken where a stroke overlaps itself at
  corners. Each stroke is flattened opaque first, then composited once at its
  own opacity.
- Strokes are resampled through a Catmull-Rom spline, so fast curves no
  longer facet.
- A plain click leaves a dot. It used to draw nothing at all.
- Mouse strokes get width variation from drawing speed, since a mouse reports
  a constant fake pressure.
- Screenshots capture the monitor under the cursor instead of compositing
  every monitor into one image, and open Explorer with the file selected.
- Added the backdrop toggle, a palette popup on the color swatch, and hover
  tips that render above the overlay (native tooltips can end up behind it).
- Default smoothing is now 0%.

## v1.1.1

- Fixed the toolbar ignoring pen taps. Tablet-to-mouse synthesis was disabled
  at startup to prevent duplicate strokes, but that synthesized click was the
  only way a pen tap reached the buttons. Synthesis is back on, and duplicate
  strokes are prevented in the tablet handler instead.

## v1.1.0

- Replaced the full-history redraw with a persistent canvas. Every pointer
  move used to repaint every point of every stroke across the whole
  multi-monitor desktop.
- Repaints are now scoped to the region that actually changed.
- The toolbar shows the active tool, color, and slider values, and stays in
  sync when you change them by hotkey. Undo and redo are greyed out when
  there's nothing to do.
- Renamed the brush size attribute, which was shadowing `QWidget.size()`.
- Rebuilt the toolbar as a compact icon bar, and replaced the pinned status
  banner with a toast.

## v1.0.0

Restructured the original single-file script into a package.

- Undo history is capped instead of growing for the life of the process.
- Replaced the debug logger, which appended to a file on the Desktop and
  flushed on every call, with a rotating logger under `%LOCALAPPDATA%`.
- The overlay covers all monitors and follows monitor hotplug. It used to
  cover only the primary one.
- Event handlers catch and log exceptions instead of taking the app down, and
  an excepthook shows anything that still gets through.
- Added pen eraser-tip detection, saved brush settings, a tray icon, and
  PyInstaller packaging.
