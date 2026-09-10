# Changelog

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
