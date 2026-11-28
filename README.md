# Crit Overlay

A transparent, always-on-top drawing overlay for Windows. It puts a drawing
surface over everything else on screen so you can mark up a student's work
during a live crit, whatever it happens to be running in: a render, a
Photoshop file, Maya, a video, a browser tab. Drag a rectangle around the
work to capture it into a centered drawing workspace (or click once to draw
over the whole monitor), mark it up with a tablet or a mouse, then hit F9
to save an annotated image.

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
- Centered capture: it starts by asking for an area, like a snipping tool.
  The area becomes a still image centered on the monitor, with an optional
  annotation margin around it for handwritten notes. Hold Space and drag to
  move it. F9 saves the image, margin and notes at the image's full
  resolution. See [Centered capture and margin](#centered-capture-and-margin).
- Undo and redo.
- Pass-through mode (F8) to click into whatever is underneath without
  closing the overlay.
- Screenshots saved to `Pictures\CritOverlay\`. After saving, the overlay
  hides to the tray, the image opens in your default viewer, and Explorer
  opens with the file selected, ready to drag into a chat or email.
- Lives in the tray: Esc hides the overlay, **Ctrl+Alt+D** (from any app)
  or the tray icon brings it back with your drawing intact, and Exit is in
  the tray menu. Only one copy runs at a time. Optional Start with Windows.
- Brush and margin settings persist between sessions.

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
| R | Capture a new area (drag a rectangle; a plain click draws on the full monitor instead). Replaces the current capture |
| Space + drag | Move the capture, with its margin and notes |
| F7 | Move the overlay to the next monitor (same as the toolbar's monitor button) |
| F8 | Toggle pass-through |
| F9 | Save: the capture with its margin and notes, otherwise the monitor under the cursor. Then hides to the tray and opens the image |
| Esc | With a capture: discard it and select a new area. Otherwise hide the overlay to the tray (full-monitor drawings are kept) |
| Ctrl+Alt+D | Show or hide the overlay from any app (global; [configurable](#tray-hotkey-and-startup)) |

F8 and F9 are Qt shortcuts, so they need the overlay to have keyboard
focus. Once you click into another app during pass-through, use the badge at
the top of the screen to get back.

Left-clicking the tray icon hides and shows the overlay. Its right-click
menu has pass-through, screenshot, move to next monitor, Start with
Windows, a Troubleshooting submenu (diagnostics report, log folder), and
Exit.

### Tray, hotkey and startup

- **Hiding isn't quitting.** Esc (with no capture), the toolbar's last
  button, Alt+F4 and a saved screenshot hide the overlay; strokes, undo
  history, the capture, margin, backdrop and pass-through are all still
  there when it comes back. With a capture, showing it again comes back to
  that capture; without one, it starts a new area selection.
  While hidden it uses no CPU and gives back the memory it isn't using.
- **Exit** from the tray menu ends the app. On a machine with no
  notification area (some locked-down kiosks), Esc exits instead, since
  there'd be no way back.
- **Ctrl+Alt+D** toggles the overlay from anywhere. To change it, set
  `hotkey` in `[compat]` (`CritOverlay.ini` next to the exe, or your own
  settings file) or pass `--hotkey`, e.g. `hotkey = Ctrl+Shift+F10`. Ctrl,
  Alt or Win is required; `none` turns it off. If another program already
  uses the combination, Crit Overlay still starts, says so in a tray
  notification, and the tray icon works as usual.
- **One copy at a time.** Starting it again while it runs (even hidden)
  just brings the running overlay to the front.
- **Start with Windows** (tray menu, off by default) puts a shortcut in
  your Startup folder that starts it hidden in the tray at logon. No
  registry changes; untick it, or delete the shortcut, to undo.

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
- With a capture, F7 moves it to the next monitor and centers it there.
- Full-monitor screenshots (F9) capture the monitor under the cursor, as
  before.

## Centered capture and margin

Each time the overlay appears it asks for an area, the way a snipping tool
does:

- **Drag a rectangle** with the pen or mouse around what you want to mark
  up, such as a render in a viewer or a reference image. It's captured as a
  still image and centered on the monitor, with the desktop dimmed around
  it. Where you selected it doesn't matter.
- **Click once** instead of dragging to draw live over the whole monitor, as
  in earlier versions. **Esc** during the selection hides the overlay.
  Capturing an area discards drawings made on the full monitor, so save
  those with F9 first.
- **Space + drag** moves the whole capture: image, margin and notes
  together.
- A capture too big for the monitor with its margin is shrunk to fit on
  screen. That's display only: the image keeps its full resolution, and so
  does the saved file. The brush stays the same size on screen.
- The **backdrop** (and its intensity slider) tints the captured image,
  under your strokes.
- **Annotation margin**: click the **region button** on the toolbar (the
  dashed frame next to the backdrop button). It sets a band around the
  capture for handwritten notes, with its own thickness, white or black
  color and opacity, separate from the backdrop intensity. It's off until
  you turn it on ("Margin: on") or adjust any of its controls. Changing the
  thickness recenters the capture. You can draw in it, and the eraser takes
  strokes off it the same way it does on the image.
- **F9** saves the image, margin and notes, at the resolution the image was
  captured at. Nothing else goes in: no toolbar, cursor or desktop. The
  margin is saved solid in its color, even if it's translucent on screen.
  The overlay then hides to the tray, the image opens in your default
  viewer, and Explorer opens with the file selected. **Ctrl+Alt+D** brings
  back the same capture with its notes and undo history.
- **Esc** discards the capture and its notes and starts a new selection
  straight away. **R** does the same at any time. *Full monitor* in the
  region menu discards it and goes to live full-monitor drawing.
- **Pass-through (F8)** with a capture hides the image and margin and
  shows only your strokes, so you can see what you click on underneath.
- Image and margin edges line up on whole screen pixels at any display
  scaling and any fit, so there's no seam between them.
- Margin settings are saved. A capture lasts until you discard it or quit.

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
| `--hotkey KEYS` | Global show/hide hotkey (default `Ctrl+Alt+D`; `none` turns it off) |
| `--hidden` | Start hidden in the tray (what Start with Windows uses; command line only) |
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
page](https://github.com/vult-systems/crit-overlay/releases). v1.4.2 is the
current stable release. v1.5.0 release candidates (`v1.5.0-rc.N`) are
marked as pre-releases until they pass the hardware and lab-machine
checks in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#manual-validation-required-before-release).
v1.5.0 is feature-frozen at rc.4; what it ships with is under
[Known limitations](docs/COMPATIBILITY.md#known-limitations-v150).

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
  capture.py            centered capture: crop, fit, pixel-aligned geometry, export
  region.py             drawing region and margin geometry, region screenshots
  region_popup.py       the region and margin popup
  models.py             strokes and undo history
  config.py             saved settings
  logging_setup.py      rotating file logger
  tray.py               tray icon
  hotkeys.py            global hotkey parsing and registration
  instance.py           one running copy per session
  autostart.py          Start with Windows (Startup-folder shortcut)
  pass_through_badge.py the way back out of pass-through
  native_windows.py     Win32 calls (click-through, working set, foreground, system info)
  icon.py               app icon, drawn in code
packaging/              build script, PyInstaller spec, lock file, verifier
docs/                   compatibility, deployment, troubleshooting, building
tests/                  unit tests: python -m unittest discover -s tests -t .
```

## License

MIT. See [LICENSE](LICENSE).
