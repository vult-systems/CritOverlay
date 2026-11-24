# Crit Overlay

A transparent, always-on-top drawing overlay for Windows. It puts a drawing
surface over everything else on screen so you can mark up a student's work
during a live crit, whatever it happens to be running in: a render, a
Photoshop file, Maya, a video, a browser tab. Draw with a tablet or a mouse,
then hit F9 to save an annotated screenshot.

I wrote it for the crit sessions I run in our animation program, where
screen-sharing a paint-over is faster than describing what I mean.

## What it does

- Covers one monitor at a time, lined up exactly and drawn at that
  monitor's full resolution, whatever the display scaling. The monitor
  button on the toolbar (or F7) moves it to the next one, e.g. from your
  laptop to the projector. See [Monitors](#monitors).
- Takes tablet (Wacom / Windows Ink) and mouse input. Stroke width responds
  to pen pressure and to how fast you draw, so fast strokes taper. Flipping
  a pen to its eraser end switches to erase mode on its own.
- Brush, eraser, and eyedropper, with size, opacity, and smoothing sliders.
- Optional white or black backdrop behind your strokes with an intensity
  slider, for when the page underneath is too busy to draw over. It's a real
  layer, so erasing takes your strokes off it instead of punching through to
  the desktop.
- Undo and redo.
- Pass-through mode (F8) to click into whatever is underneath without
  closing the overlay.
- Screenshots of the monitor your cursor is on, saved to
  `Pictures\CritOverlay\` and opened in Explorer.
- Tray icon to hide and show it without quitting.
- Brush settings persist between sessions.

## Requirements

Windows 10 (1809 or later) or Windows 11, 64-bit. The standalone build needs
nothing else: no admin rights, no Python, no runtimes. Pressure sensitivity
needs a pen that works through Windows Ink (Wacom and most others do by
default). A mouse works fine without one.

What has and hasn't been tested is in
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). For school or work machines
(blocked apps, redirected folders, antivirus), see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Running

Standalone: extract the release zip and run `CritOverlay.exe`.

From source (Python 3.12 or 3.13 recommended; it also still runs on older
lab installs back to 3.9, which no longer get security fixes):

```
python -m pip install --user -r requirements.txt
python -m crit_overlay
```

Or double-click `run.bat`. Use `run_debug.bat` if you want console logging.

## Controls

| Key | Action |
|---|---|
| B | Brush |
| E | Eraser |
| I | Eyedropper (one pick, then back to your previous tool) |
| Alt+click | Sample a color without leaving Brush or Eraser |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z | Redo |
| [ / ] | Brush size down / up |
| F7 | Move the overlay to the next monitor (same as the toolbar's monitor button) |
| F8 | Toggle pass-through |
| F9 | Screenshot the monitor under the cursor |
| Esc | Exit |

F8 and F9 are Qt shortcuts, so they need the overlay to have keyboard
focus. Once you click into another app during pass-through, use the badge at
the top of the screen to get back.

Left-clicking the tray icon hides and shows the overlay. Its right-click
menu has pass-through, screenshot, move to next monitor, a Troubleshooting
submenu (diagnostics report, log folder), and exit.

## Monitors

The overlay always covers exactly one monitor. This is a deliberate design
choice. Before v1.5, one window was stretched across every monitor. On any
setup with display scaling (a 150% laptop plus a projector, a 4K tablet
display at 200%), Qt can't place a stretched window accurately, so the
toolbar and strokes ended up offset from the screen. A window inside one
monitor always lines up and renders at that monitor's real resolution.

- It starts on the monitor under the mouse (`--screens primary` to always
  start on the primary monitor).
- The **monitor button** on the toolbar, **F7**, or the tray menu moves it
  to the next monitor, left to right. The button only appears when more
  than one monitor is connected; its tooltip shows which one you're on.
- Your annotations move with it, so you can mark up on the laptop and then
  move the drawing to the projector.
- Plugging a projector in leaves the overlay where it is, and a toast says F7
  switches. If the monitor the overlay is on is unplugged, it moves to the
  primary monitor. Resolution or scaling changes keep it on the same monitor.
- Screenshots (F9) capture the monitor under the cursor, as before.

## Options

Command-line flags, or the same keys in a `[compat]` section of
`CritOverlay.ini` next to the exe (see `CritOverlay.ini.example`):

| Flag | |
|---|---|
| `--safe-mode` | Mouse input and primary monitor only. First thing to try when something's wrong. |
| `--input mouse` | Ignore tablet events; for drivers that cause doubled or stuck strokes |
| `--screens cursor/primary` | Which monitor to start on (default: the one under the mouse) |
| `--portable` | Keep settings, logs and screenshots beside the exe |
| `--screenshot-dir DIR` | Where screenshots go first |
| `--debug` | Verbose logging |
| `--diagnose` | Write a compatibility report and open it |
| `--selftest` | Start, exercise the overlay automatically, report, exit (0 = pass) |

`CritOverlay-console.exe --help` lists everything.

## Building a standalone exe

```
packaging\build.bat
```

Needs 64-bit Python 3.13 (3.12 also works); a per-user install with no admin
rights is fine: `winget install Python.Python.3.13 --scope user`. The build
sets up its own venv with a hash-pinned toolchain, builds
`dist\CritOverlay\`, verifies it (static DLL audit plus self-tests in a
clean and a deliberately polluted environment), and packages a zip with
SHA-256 sums. Details, offline builds and dependency updates are in
[docs/BUILDING.md](docs/BUILDING.md).

It's a folder rather than a single file on purpose: a onefile build unpacks
itself to a temp directory every time it starts, which is a noticeable wait
for something you launch and close all day, and is blocked outright on
machines that don't allow running programs from `%TEMP%`.

Prebuilt versions are on the [releases
page](https://github.com/vult-systems/CritOverlay/releases). v1.4.2 is the
current stable release. v1.5.0 release candidates (`v1.5.0-rc.N`) are
marked as pre-releases until they pass the hardware and lab-machine
checks in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#manual-validation-required-before-release).

## Logs and settings

- Logs: `%LOCALAPPDATA%\CritOverlay\logs\crit_overlay.log`, rotated and
  capped around 4 MB. Crash reports, `faulthandler.log` and diagnostics
  reports go in the same folder.
- Settings: `%APPDATA%\CritOverlay\Crit Overlay.ini`.
- Screenshots: `Pictures\CritOverlay\`, or
  `%LOCALAPPDATA%\CritOverlay\Screenshots\` if Pictures can't be written to.

Each falls back to another location if the usual one isn't writable. Problems
and fixes are in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Layout

```
crit_overlay/
  __main__.py           python -m entry point
  launcher.py           pre-Qt startup: options, logging, crash handling, checks
  app.py                QApplication, overlay and tray wiring
  compat.py             options, Qt environment cleanup, monitor-mode choice
  paths.py              where files go, with fallbacks
  crash.py              crash reports, faulthandler, startup watchdog
  diagnostics.py        --diagnose report
  selftest.py           --selftest
  overlay.py            the drawing widget
  brush.py              smoothing, stroke dynamics, compositing
  icons.py              toolbar icons, drawn in code
  color_popup.py        the color swatch popup
  models.py             strokes and undo history
  config.py             saved settings
  logging_setup.py      rotating file logger
  tray.py               tray icon
  pass_through_badge.py the way back out of pass-through
  native_windows.py     Win32 calls (click-through, system info)
  icon.py               app icon, drawn in code
packaging/              build script, PyInstaller spec, lock file, verifier
docs/                   compatibility, deployment, troubleshooting, building
tests/                  unit tests: python -m unittest discover -s tests -t .
```

## License

MIT. See [LICENSE](LICENSE).
