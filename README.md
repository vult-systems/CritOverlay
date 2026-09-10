# Crit Overlay

A transparent, always-on-top drawing overlay for Windows. It puts a drawing
surface over everything else on screen so you can mark up a student's work
during a live crit, whatever it happens to be running in: a render, a
Photoshop file, Maya, a video, a browser tab. Draw with a tablet or a mouse,
then hit F9 to save an annotated screenshot.

I wrote it for the crit sessions I run in our animation program, where
screen-sharing a paint-over is faster than describing what I mean.

## What it does

- Covers every connected monitor, not just the primary one.
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

Windows, Python 3.10 or newer, and PySide6. Pressure sensitivity assumes a
tablet with Windows Ink or Wacom drivers installed. A mouse works fine
without one.

## Running

```
pip install -r requirements.txt
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
| F8 | Toggle pass-through |
| F9 | Screenshot the monitor under the cursor |
| Esc | Exit |

F8 and F9 are Qt shortcuts, so they need the overlay to have keyboard
focus. Once you click into another app during pass-through, use the badge at
the top of the screen to get back.

Left-clicking the tray icon hides and shows the overlay. Its right-click
menu has pass-through, screenshot, and exit.

## Building a standalone exe

```
pip install -r requirements-dev.txt
packaging\build.bat
```

This gives you `dist\CritOverlay\CritOverlay.exe` with its dependencies
beside it. It's a folder rather than a single file on purpose: a onefile
build unpacks itself to a temp directory every time it starts, which is a
noticeable wait for something you launch and close all day.

Prebuilt versions are on the [releases
page](https://github.com/vult-systems/CritOverlay/releases).

## Logs and settings

- Logs: `%LOCALAPPDATA%\CritOverlay\logs\crit_overlay.log`, rotated and
  capped around 4 MB.
- Settings: `%APPDATA%\CritOverlay\Crit Overlay.ini`.

## Layout

```
crit_overlay/
  __main__.py           entry point, logging and tray wiring
  overlay.py            the drawing widget
  brush.py              smoothing, stroke dynamics, compositing
  icons.py              toolbar icons, drawn in code
  color_popup.py        the color swatch popup
  models.py             strokes and undo history
  config.py             saved settings
  logging_setup.py      rotating file logger
  tray.py               tray icon
  pass_through_badge.py the way back out of pass-through
  native_windows.py     Win32 click-through
  icon.py               app icon, drawn in code
packaging/              PyInstaller spec and build script
```

## License

MIT. See [LICENSE](LICENSE).
