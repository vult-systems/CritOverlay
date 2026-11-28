# Crit Overlay

Visual critique and annotation for artists, educators and creative teams. Capture any part of the screen, paint over it with a pen or mouse, and save the marked-up image at full resolution. It runs on top of whatever you're reviewing: a render, a Photoshop file, Maya, a video, a browser.

- Portable: no installer, no admin rights, nothing else to install.
- Windows 10 (1809 or later) or Windows 11, 64-bit. Pen pressure through Windows Ink.

## Download and quick start

Download the latest zip from [Releases](https://github.com/vult-systems/crit-overlay/releases), extract it and run `CritOverlay.exe`. v1.4.2 is the current stable release; v1.5.0 release candidates are pre-releases until hardware acceptance passes.

1. Drag a rectangle around the work to capture it, or click once to draw over the whole monitor.
2. Mark it up with a pen or mouse.
3. Press **F9** to save. The image opens, and Explorer shows the file ready to share.
4. Press **Esc** to hide to the tray. **Ctrl+Alt+D** brings back the same session.

## What it does

**Capture and critique.** A captured area becomes a still image centered on the monitor, with an optional margin for written notes and a backdrop for busy pages. Exports keep the image's own resolution.

**Draw.** Brush, eraser and eyedropper with pressure-sensitive strokes, undo and redo. Pass-through (F8) lets you click into the app underneath without closing anything.

**Fit the room.** One monitor at a time, lined up at its own scaling; F7 moves to the next, from laptop to projector. Lives in the tray, with a global hotkey and optional Start with Windows.

## In development

The [`exp/shape-tools`](https://github.com/vult-systems/crit-overlay/pull/32) prototype isn't released yet. It adds:

- Editable shapes and text, with a Select tool to move, resize and restyle them.
- libmypaint brushes: Studio Pen, Pencil, Ink, Marker and Soft Brush, hard and soft erasers, pressure-driven size and opacity, and tilt.
- One floating toolbar with context properties, cursor-centered zoom and panning, and on-canvas brush size.

## Keyboard shortcuts

| Key | Action |
|---|---|
| B / E / I | Brush, eraser, eyedropper |
| [ / ] | Brush size |
| Ctrl+Z / Ctrl+Shift+Z | Undo, redo |
| R | Capture a new area |
| Space + drag | Move the capture |
| F7 | Next monitor |
| F8 | Pass-through |
| F9 | Save the annotated image |
| Esc | Hide to the tray (with a capture in v1.5.0: discard it and select again) |
| Ctrl+Alt+D | Show or hide from any app |

All shortcuts, monitor behavior, capture and margin details, and command-line options are in the [usage guide](docs/USAGE.md).

## Documentation

- [Usage](docs/USAGE.md): controls, tray and hotkey, monitors, capture and margin, options, file locations
- [Compatibility](docs/COMPATIBILITY.md): tested hardware and known limitations
- [Deployment](docs/DEPLOYMENT.md): school and work machines
- [Troubleshooting](docs/TROUBLESHOOTING.md): problems and fixes
- [Building](docs/BUILDING.md): standalone builds, CI, releases, running from source

## License

MIT. See [LICENSE](LICENSE).

Built with [Qt for Python](https://doc.qt.io/qtforpython/) (PySide6, LGPLv3) and packaged with [PyInstaller](https://pyinstaller.org/). The prototype also bundles [libmypaint](https://github.com/mypaint/libmypaint) (ISC), [json-c](https://github.com/json-c/json-c) (MIT), [mypaint-brushes](https://github.com/mypaint/mypaint-brushes) (CC0), [Lucide](https://lucide.dev) icons (ISC), and IBM Plex Sans, IBM Plex Mono, Inter and Caveat fonts (SIL Open Font License); each license ships alongside it.
