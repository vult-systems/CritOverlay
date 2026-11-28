# Compatibility

This page separates what has actually been run from what is expected to
work. **Tested** means the app was run in that configuration and the stated
result observed. **Assumed** means it follows from vendor documentation or
from the code, but nobody has run it. Move a row from Assumed to Tested only
after running the [manual checklist](#manual-validation-required-before-release)
there.

Last updated for **v1.5.0-rc.5**: PySide6 / Qt 6.8.3, PyInstaller 6.22.3,
bundled Python **3.13.17**.

## Supported platform

| | |
|---|---|
| OS | Windows 10 1809 or later, Windows 11. 64-bit only. (Qt 6.8's own minimum is Windows 10 1809; Python 3.13 supports Windows 10+.) |
| Privileges | Standard user. Never requests elevation (`asInvoker` manifest). |
| Runtimes | None to install. Python, Qt and the MSVC runtime (`VCRUNTIME140*.dll`, `MSVCP140*.dll`) are bundled. |
| Network | None used. Qt's network module and the TUIO network-input plugin are stripped from the bundle. |
| Rendering | QWidget/QPainter raster painting on the CPU. No OpenGL, Direct3D or Vulkan context is created, so GPU drivers only matter for desktop composition (DWM). |
| Monitors | The overlay covers **one monitor at a time** on every configuration (see below). Any number of monitors at any mix of scale factors. |

## Why one monitor at a time

Before v1.5, the overlay was a single window stretched over every monitor.
Qt 6 keeps each monitor's physical top-left corner as its logical origin and
only scales its size, so as soon as any monitor is scaled above 100%, the
logical layout has gaps or overlaps. A stretched window then gets placed with
one monitor's scale factor and lands offset from the real screens. Measured
on the test machine (200% + 175% + 100%), Qt intended 0..11120 px and Windows
placed the window at -2880..16580 px. No DPI-awareness mode fixes this (all
three were tried). A window inside one monitor takes that monitor's scale and
lines up exactly, on every configuration. Early 1.5.0 development builds
still spanned all monitors when every one was at 100%; that special case
is gone, so behaviour is the same everywhere. Tracked in #2.

## Tested

All on one machine: a university-managed laptop, standard (non-admin)
account. Automated runs use `--selftest`, which moves the overlay across
every monitor with the toolbar's monitor button, draws on each, and
exercises the toolbar and pass-through (see `crit_overlay/selftest.py`).

| Area | Configuration | Result |
|---|---|---|
| OS | Windows 11 Pro 26H2, build 26300.9911, x64 | Pass |
| GPU | Hybrid Intel UHD + NVIDIA GeForce RTX 3080 Laptop | Pass |
| Mixed-DPI monitors | Wacom Cintiq Pro 24 3840x2160 @ 200% (primary), laptop panel 3840x2160 @ 175%, Samsung 3440x1440 @ 100% | Pass. On each monitor the native window rect matches the monitor exactly, the toolbar sits on that monitor, the canvas renders at its scale (2x / 1.75x / 1x), and drawing works. v1.4.2 fails here (offset window, see above). |
| Monitor switching | Toolbar monitor button through all 3 monitors and back; F7; switching mid-stroke; switching during pass-through | Pass |
| Alignment repair | Window forced off its monitor with a native `SetWindowPos` | Pass: detected and put back by the one-time re-apply |
| Toolbar | Brush/Eraser buttons, size slider vs `[`/`]` keys, Undo/Redo/Clear enable states, clicking the toolbar doesn't draw, monitor button hidden with one monitor (fake-monitor test) | Pass |
| Pass-through | On via toolbar: click-through set, toolbar hidden, badge on the covered monitor. Monitor switch while on: badge follows, click-through kept. Off via badge: input, toolbar and button state restored | Pass |
| Drawing region and margin (#10) | Self-test on each of the 3 monitors (200% / 175% / 100%): black backdrop in a region with a white margin, then a native screen grab | Pass: 1 logical pixel inside every region edge is region and 2 outside is margin at each scale; grab pixel ratio equals the monitor's; region screenshot is exactly region plus margin at device resolution; strokes start only inside region or margin. Unit tests (fake 150% + 100% monitors): clipping, backdrop/margin restriction and independent opacity, eraser over the margin, selection by mouse and pen, Esc, monitor switch with clamping, settings. Repaint cost unchanged without a region; about 5% more per point with one. |
| Tray, hotkey, one instance (#14) | Packaged exe (local build of the #14 branch), real keyboard and mouse input via `SendInput`, Windows 11 26300, 200% Cintiq primary | Pass: Ctrl+Alt+D hides and shows with a stroke intact; Esc hides, process stays; a second launch exits in 0.1–0.2 s (about 20 MB peak) and brings the running overlay back with keyboard focus; tray menu Exit ends the process with code 0 and no session marker. With Ctrl+Alt+D held by another process: starts anyway, logs "already used by another program", tray menu shows plain "Show/Hide", tray click still shows the overlay. Hidden: 0 ms CPU over 50–60 s, working set 3–9 MB after the trim (once it rose back to about 108 MB within 10 s, cause not identified), private memory unchanged at about 95 MB (the drawing canvas). Start with Windows ticked: shortcut in the Startup folder targeting this copy with `--hidden --portable`; launching that shortcut starts hidden; unticking removes it. The tray notifications themselves were not seen on screen (Windows notification settings); the log records the conflict. The tray right-click was simulated by posting the shell's `WM_CONTEXTMENU` notification, because Windows 11 had the icon in the hidden-icons overflow; the menu clicks were real. |
| Hot-plug logic | Monitor added; monitor removed (the covered one, and another one); bursts of display events; resolution/scale change | Pass in unit tests on two fake offscreen monitors (150% + 100%), driving the overlay's own `screenAdded`/`screenRemoved`/`geometryChanged` handlers. **Not** with real hardware; see manual checklist. |
| Settings migration | v1.0–v1.4.2 settings file; 1.5.0 development-build `screens = all/auto` (user file and lab-wide INI, CLI flag); newer-version file; read-only file; missing file | Pass: brush settings carry over unchanged, legacy screen modes become `cursor` with a logged note, newer files untouched, read-only files still load |
| Builds | Frozen exe built with Python 3.13.17 + PySide6 6.8.3; source runs with Python 3.9.13 + PySide6 6.7.3 and Python 3.13 + 6.8.3 | Pass: `--selftest`, 127 unit tests on both Qt versions |
| Clean environment | Frozen self-test with PATH reduced to System32 and no Python/Qt variables | Pass |
| Polluted environment | `QT_QPA_PLATFORM=xcb`, `QT_PLUGIN_PATH`/`QT_QPA_PLATFORM_PLUGIN_PATH` to a dir with a fake `qwindows.dll`, `QT_SCALE_FACTOR=1.37`, `PYTHONHOME`/`PYTHONPATH` to junk, fake `python313.dll` first on PATH | Pass (variables cleared and logged) |
| Qt 5 on the machine | PySide2 5.15 installed; Qt 5 plugin paths in the environment | Pass |
| Paths | Run from a folder named `Crit Övërlay tëst (ünïcode)` | Pass |
| Portable mode | `CritOverlay.ini` with `portable = true` next to the exe | Pass; settings, logs, screenshots stay in `CritOverlay-data\` |
| Startup recovery | Qt hang (`QT_QPA_PLATFORM=xcb` kept): watchdog ends it after timeout + 3 s; next launch reports it once. Deleted `Qt6Widgets.dll`/`qwindows.dll`; deleted shiboken module; invalid CLI flag; forced native segfault | Pass: plain-language messages, crash report files, exit codes 2 / 3 / 4, faulthandler stack, unclean-exit note on next launch |
| Startup watchdog, re-run 2026-11-25 | Forced Qt hang with `CRITOVERLAY_STARTUP_TIMEOUT=5`: packaged rc.3 exe, and source on Python 3.9 + PySide6 6.7.3 and 3.13 + 6.8.3, each in a Job object with a 40 s hard limit | Pass: the process ends itself at 8.5 s in every case (5 s + 3 s), one process, nothing left running, about 21 MB peak commit, stack dumped to `faulthandler.log`. The next launch logs "got stuck ... stopped by the watchdog" and the unclean-exit note once; the launch after that is quiet. The exit code is not meaningful (low byte 1, upper bits arbitrary): the native timer calls `TerminateProcess` with whatever is in the argument register. |
| Static audit | Every bundled .exe/.dll/.pyd imports only bundled DLLs or DLLs that ship with Windows 10 1809+ | Pass (94 binaries) |
| Reproducibility | Two consecutive clean builds on the same machine (Python 3.13) | Byte-identical exes and zip |
| Performance | 400-point strokes with forced repaints, on the 200% monitor | v1.5.0-rc.1 2.53 ms/point at full 2x resolution vs v1.4.2 2.81 ms/point at 1x. Startup to ready about 0.5 s warm, 1.6 s first launch. |

## Assumed (not yet tested)

| Area | Configuration | Expectation and basis |
|---|---|---|
| Windows 10 | 1809 / LTSC 2019, 21H2 / LTSC 2021, 22H2 | Should work: inside Qt 6.8's and Python 3.13's supported range, and the bundle needs nothing newer (static audit). Windows 10 before 1809 (e.g. LTSB 2016) is **not** supported. |
| Windows 11 | 23H2, 24H2, 25H2 release builds | Should work. Tested build 26300 is newer. |
| Windows on ARM | Windows 11 ARM64 | Likely works under x64 emulation. Pen latency unknown. No native ARM64 build. |
| Single monitor | any scale | Same code path as each monitor of the tested setup |
| Real projector / TV hot-plug | HDMI/USB-C projector plugged or unplugged while running; "Duplicate" vs "Extend" display mode | Handled by the tested handler logic; never done with a real cable. In Duplicate mode Windows reports one monitor, so the overlay covers it and appears on both. |
| Display scale changed in Settings while running | | Handled via `geometryChanged`/`logicalDotsPerInchChanged` (tested with fake monitors); canvas re-renders at the new scale |
| Docking stations / sleep-resume | All monitors briefly disappear | If no monitor is reported the overlay stays put and waits; the next `screenAdded` places it. Untested. |
| Remote Desktop / Citrix / VDI | | Should start (no GPU dependency). Transparency depends on the remote session composing layered windows. Pen pressure over RDP may read as 0; the zero-pressure fallback then draws at default width. |
| No GPU driver | Microsoft Basic Display Adapter | Should work: CPU rendering, DWM runs on the basic adapter |
| AMD GPUs | | Same reasoning |
| Wacom with "Use Windows Ink" off | | Qt 6.8 reads pens only through Windows Ink, so the pen acts as a mouse: draws, no pressure. Fix: enable Windows Ink for CritOverlay.exe in the Wacom per-application settings. |
| Huion, XP-Pen, Surface Pen, touch screens | | Should work through Windows Ink; `--input mouse` is the fallback |
| AppLocker / WDAC enforced | | Blocks the exe unless IT allows it; see [DEPLOYMENT.md](DEPLOYMENT.md) |
| Controlled Folder Access on | | Screenshot falls back to `%LOCALAPPDATA%\CritOverlay\Screenshots` (fallback chain unit-tested with simulated unwritable folders) |
| Redirected folders | Pictures/AppData on OneDrive or a UNC share; share offline | Paths resolve through `SHGetKnownFolderPath` with local fallbacks (unit-tested with simulated failures) |
| Running from a network share | | Should work, with slower startup |
| Qt 6 DCC apps on the lab image | Maya 2025, Houdini 20.5, Nuke 15 setting `QT_PLUGIN_PATH` | Variables are cleared at startup; tested with a fake plugin, not a real Qt 6 install |
| Drawing region with a real pen | | Selection and drawing go through the same input paths as normal strokes (tested with synthetic events); real pen use is on the manual checklist |
| Region screenshot on Windows 10 | | Uses the same `QScreen.grabWindow` call as the existing full-monitor screenshot, with a sub-rectangle |
| Centered capture with a real pen | | Selection, Space+drag and strokes go through the same input paths as before (unit-tested with synthetic mouse and pen events on the fake 150% + 100% monitors, including Space+pen panning and a pen release over the toolbar); real pen use is manual checklist item 14 |
| Capture edges at fractional scaling | | Unit-tested at 150% with an odd-sized selection, at a fit scale below 100%, with a 125% capture on a 150% monitor, and in the export: every pixel either side of each edge is image or margin. `QScreen.grabWindow` rounding up a sub-rect's size was measured on a real 150% monitor (Qt 6.8.3); the capture crops a full-monitor grab instead. Not yet checked visually on 175%/200% hardware |
| F9 opens the viewer, then Explorer in front | | From the code: `os.startfile`, then `explorer /select` 1.2 s later, with `AllowSetForegroundWindow` before each. Unit tests check the order with the launches mocked; whether Explorer actually ends up in front is manual checklist item 14 |
| Building with Python 3.12 | | Lock installs (all wheels pure Python or abi3); only 3.13 actually built |

## Known limitations (v1.5.0)

v1.5.0 is feature-frozen at v1.5.0-rc.5 (see `CLAUDE.md`). These ship as
they are; each is either by design or tracked for after v1.5.0 (#19).
"From the code" means it follows from reading the code but hasn't been run.

| Limitation | Effect and workaround | Tracking |
|---|---|---|
| One monitor at a time | By design: Qt can't place one window accurately across monitors with different scaling. F7, the toolbar's monitor button or the tray moves it. | [Why one monitor at a time](#why-one-monitor-at-a-time) |
| Pens only through Windows Ink | With a Wacom driver's "Use Windows Ink" off, strokes have no pressure and the eraser tip isn't detected. Turn Windows Ink on for `CritOverlay.exe` in the driver's per-application settings. | #5 |
| Unsigned executables | SmartScreen warns on downloaded copies; AppLocker/WDAC need a hash or path rule, redone for each release. | #6 |
| Old settings pin defaults | Every exit (and now every hide to the tray) writes all brush and margin values, so changed defaults never reach anyone who has run the app before. | #1 |
| Tray icon hidden on Windows 11 | New tray icons go to the hidden-icons overflow (^) until dragged onto the taskbar. Ctrl+Alt+D works either way. Seen on the test machine. | #19 |
| Tray notifications can be suppressed | The "hotkey already in use" and "still running" notices are Windows notifications; with notifications or Do Not Disturb off, only the log records a hotkey conflict. The notices weren't seen on screen during testing. | #19 |
| Upgrading while it runs | Starting a new version while an older copy is running only brings the old copy back. Exit from the tray first. From the code. | #19 |
| Tray > Screenshot while hidden | With a capture, the saved image includes the annotations (it's composed from the layers). Without one, it saves the monitor without them, since the overlay isn't on screen; show the overlay first. From the code; same as v1.4.2 for the full-monitor case. | #19 |
| Capturing discards full-monitor drawings | Dragging a new area replaces whatever was drawn, including drawings made on the full monitor, with no undo. Save with F9 first. By design (#23). | #23 |
| No live drawing region | Area selection always captures; the live region from rc.2 (#10) can't be set from the UI. Its code remains, and `--selftest` still checks that path on every monitor rather than the capture. | #26 |
| Hotkey while an elevated app has focus | Untested. Use the tray icon if Ctrl+Alt+D doesn't respond there. | #4 item 15 |
| Startup watchdog exit code | A launch stopped by the watchdog exits with an arbitrary code (low byte 1). The next launch's message and `faulthandler.log` are what to go by. | #19 |
| Python 3.9 source runs | Still work for old lab installs, but 3.9 gets no security fixes. The release build bundles 3.13. | #8 |

## Manual validation required before release

The automated checks can't cover these. None are done for v1.5.0-rc.5
(tracked in #4). Each needs
a person at the machine, and they're the release gate. Record the machine
(Windows build, GPU, monitors with scale, pen model) next to each result.

**Real input** (the automated tests only draw synthetic strokes)
1. Draw with a pen on the Cintiq (or any Windows Ink tablet). Pressure
   varies width, the eraser tip erases, strokes start exactly under the pen
   tip near all four edges of each monitor.
2. Draw with a mouse. No doubled strokes, a click leaves a dot.
3. Pen-tap every toolbar button and drag every slider with the pen.
4. Alt+click and the eyedropper pick colours from the app underneath.

**Monitors**
5. On a laptop + projector (or second monitor) with **different scaling**:
   start the app, check the toolbar appears on the monitor under the mouse,
   then use the toolbar's monitor button and F7 to reach every monitor.
   Strokes must land exactly under the cursor on each.
6. **Hot-plug a real projector** while running: plug in (toast says F7
   switches, overlay stays), move to it, unplug it (overlay returns to the
   primary monitor; no crash; log shows "Monitor disconnected").
7. Switch the projector between Duplicate and Extend (Win+P) while running.
8. Change a monitor's scale in Settings while the overlay is on it.
9. Sleep and resume, or undock and redock, with the app running.

**Pass-through**
10. F8 on, click and type in the app underneath, click the badge to return.
    Repeat on a second monitor, and with the monitor switched while pass-
    through is on.

**Release build on a clean machine**
11. A Windows 10 22H2 machine that has never had Python or Qt installed:
    extract the zip, run `Run Diagnostics.bat`, then use the app.
12. A managed lab machine: the same, plus F9 screenshot (check where it
    lands if Pictures is protected) and settings persisting across a
    logoff.
13. Upgrade path: on a machine with v1.4.2 settings, confirm brush
    settings carry over.

**Centered capture** (#23; replaces the drawing-region check from rc.2)
14. On each monitor (including a scaled one), with the pen: drag an area
    over an image. It's centered with the desktop dimmed around it. Draw
    on the image and in the margin, and check pressure. Changing the
    margin's thickness, color or opacity turns it on and updates it while
    dragging (#13); thickness recenters it, and there's no seam along any
    edge or corner, also with the margin translucent. Hold Space and drag
    with the pen and with the mouse: image, margin and strokes move
    together. Capture nearly the whole monitor with a thick margin: it
    shrinks to fit, and the saved PNG is still full resolution. F8 shows
    only the strokes; F8 again brings the capture back. F9: the overlay
    hides, the image opens in the default viewer, then Explorer opens in
    front with the file selected. Ctrl+Alt+D brings back the same capture
    with undo working. Esc discards it and starts a new selection; Esc
    again hides. F7 with a capture centers it on the next monitor.

**Tray and hotkey** (#14)
15. On a managed lab machine: Esc hides, Ctrl+Alt+D brings it back from
    inside the lab's main apps (including any running as administrator),
    and tray > Exit quits. Tick Start with Windows, log off and on: it
    starts hidden in the tray (or record that the profile resets the
    Startup folder). Check the tray notification when another app owns
    Ctrl+Alt+D. With the overlay hidden and no capture, tray > Screenshot,
    then show it again: the toolbar is there (#25).

## Automated release check

`packaging\build.bat` runs the static audit and `--selftest` in clean,
polluted and non-ASCII-path environments, and fails the build otherwise.
On a machine with several monitors, the build's self-test also tours every
monitor.

CI (`.github/workflows/ci.yml`) runs lint, the unit tests, and
`build.bat` on Windows Server 2022 and 2025 runners for every pull request
that changes code or packaging. A version tag gets a draft release only if
that commit's tree passed all of it (see
[BUILDING.md](BUILDING.md#releasing)). CI runners are single-monitor
servers without pens, so they don't replace the manual checklist above.
That checklist remains the gate for a stable release.
