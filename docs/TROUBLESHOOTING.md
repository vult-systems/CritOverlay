# Troubleshooting

First step for anything: run **`Run Diagnostics.bat`** (standalone build)
or `python -m crit_overlay --selftest` (source). It prints PASS/FAIL per
check and the path of a full report. Send that report with any bug.

Where things are written:

| What | Where |
|---|---|
| Log | `%LOCALAPPDATA%\CritOverlay\logs\crit_overlay.log` |
| Crash reports | `crash_*.txt` in the same folder (or `%TEMP%\CritOverlay\` if that isn't writable) |
| Native crash stacks | `faulthandler.log` in the same folder |
| Diagnostics reports | `diagnostics_*.txt` in the same folder |
| Settings | `%APPDATA%\CritOverlay\Crit Overlay.ini` |

The tray icon's **Troubleshooting** menu can save a diagnostics report and
open the log folder.

## It won't start

| What you see | Cause | Fix |
|---|---|---|
| "This app has been blocked by your system administrator" | AppLocker / WDAC policy | Only IT can allow it. See [DEPLOYMENT.md](DEPLOYMENT.md#application-control-applocker-wdac-software-restriction-policies). |
| "Windows protected your PC" | SmartScreen, file came from the internet | More info > Run anyway, or Unblock the zip before extracting |
| "Incomplete installation ... files are missing" | Partial extraction, or antivirus quarantined a DLL | Re-extract the whole zip to a local folder. If it recurs, check the antivirus quarantine. |
| "Qt failed to load" | Damaged `_internal` folder (standalone) or PySide6 not installed (source) | Re-extract; from source run `python -m pip install --user -r requirements.txt` |
| PyInstaller "Failed to load Python DLL" | Exe started without its `_internal` folder (e.g. from inside the zip) | Extract the whole zip first |
| "Previous start failed ... got stuck" on the next launch | Qt hung while initializing and the 60 s watchdog closed it | Try `--safe-mode`; send `faulthandler.log` and the log |
| Starting it does nothing while a copy is already running | A second launch only asks the running copy to show its overlay (log: "Another copy is already running"). If that copy is hung, nothing appears | End CritOverlay.exe in Task Manager, then start it again |
| Nothing at all, no message | Blocked before our code runs (application control), or a hang | Check Task Manager for CritOverlay.exe; run `CritOverlay-console.exe --debug` from a command prompt to see output |

## Tray, hotkey and startup

| What you see | Cause | Fix |
|---|---|---|
| Esc made the overlay disappear | Esc hides to the tray; your drawing is kept | Ctrl+Alt+D or click the tray icon. To quit, right-click the tray icon > Exit |
| "Ctrl+Alt+D is already used by another program" | Another app registered the same global hotkey first | Set another one, e.g. `hotkey = Ctrl+Shift+F10` under `[compat]` in the settings file, and restart. The tray icon works meanwhile |
| Start with Windows won't stay ticked | The Startup folder isn't writable (policy) or a lab image resets it at logoff | Ask IT, or start it from a shortcut each time. The log names the folder and error |
| Started at logon but nothing shows | Start with Windows starts it hidden | Ctrl+Alt+D or the tray icon |

## Monitors

**"Only one monitor is covered" / "I can't draw on the projector."**
This is by design. The overlay covers exactly one monitor, because Qt can't
place one window accurately across monitors with display scaling (see
[COMPATIBILITY.md](COMPATIBILITY.md#why-one-monitor-at-a-time)). Click the
**monitor button** on the toolbar or press **F7** to move it to the next
monitor (also tray > Move to Next Monitor). Your annotations move with it.

**The toolbar is on the wrong monitor at startup.** It starts on the monitor
under the mouse. Use `--screens primary` (or `screens = primary` in an INI)
to always start on the primary monitor.

**No monitor button on the toolbar.** It only appears when Windows reports
more than one monitor. In Win+P "Duplicate" mode there is only one.

**After unplugging a projector the overlay jumped to another monitor.** If
the monitor it's on disappears, it moves to the primary monitor. Plugging a
monitor in never moves it.

**Strokes appear offset from the pen or cursor, or the toolbar is
off-screen.** This shouldn't happen any more. The app checks where Windows
actually put the window after every move and fixes it once if it's off.
If it does happen:

1. Press F7 twice (away and back) to re-place the window.
2. Check the log for `differs from expected` and send it with a diagnostics
   report.
3. Try `--screens primary`.

## Drawing region

**No margin around the selected area.** The margin is off until you turn it
on in the region menu ("Margin: on") or change its thickness, color or
opacity. Its controls are greyed out until an area is selected. A margin
also can't extend past the monitor edge, so a region touching the edge has
no margin on that side.

**Strokes don't start, or stop partway, in part of the screen.** A drawing
region is set: drawing is limited to it and its margin (the dashed frame).
Press R and click once, or choose *Full monitor* in the toolbar's region
menu. Strokes that were cut off reappear in full.

**Esc didn't close the app.** It was cancelling an area selection. Press
Esc again to exit.

**The screenshot is smaller than the monitor.** With a region set, F9 saves
just the region and its margin. Go back to the full monitor for a
whole-monitor screenshot.

## Pen and tablet problems

| Symptom | Try |
|---|---|
| Pen draws, but no pressure (constant width) | Tablet driver has Windows Ink turned off for this app. In Wacom Tablet Properties, add CritOverlay.exe as an application and tick "Use Windows Ink". The log says `drawing with default pressure` if the driver reports a pen with no pressure. |
| Every stroke is hairline-thin | Driver reports zero pressure. The app detects this after a few events and switches to default width; if not, use `--input mouse`. |
| Doubled strokes, strokes that don't end, or toolbar ignores pen taps | `--input mouse` (ignores tablet events; the pen then acts as a mouse) |
| Flipping to the eraser end doesn't erase | Eraser-tip detection needs Windows Ink; use E to switch to the eraser |

## Screenshots

| Symptom | Cause / fix |
|---|---|
| "Saved to C:\Users\...\AppData\Local\CritOverlay\Screenshots" instead of Pictures | Pictures wasn't writable: Controlled Folder Access, or Pictures redirected to an unavailable share. Set `screenshot_dir` or ask IT to allow the app. |
| "Screenshot failed, see log" | No candidate folder was writable; the log lists every folder tried |
| Explorer doesn't open after saving | Explorer launching is blocked by policy; the file is still saved |
| Screenshot is black | Some screen-capture-blocking or DRM software blanks captures; not fixable in the app |

## Safe mode

`--safe-mode` (or `CritOverlay (Safe Mode).bat`) uses mouse input only and
covers the primary monitor only. If safe mode works and normal mode doesn't,
switch one setting back at a time (`--input auto`, then `--screens cursor`)
to find which one breaks.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Normal exit / self-test passed |
| 1 | Unexpected error (crash report written) |
| 2 | Incomplete installation |
| 3 | Qt failed to load |
| 4 | Invalid command line |
| 5 | Self-test failed |
