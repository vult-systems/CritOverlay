# Deploying on managed workstations

Notes for running Crit Overlay on university or corporate machines, and for
the IT staff who manage them.

## What the app needs

- Windows 10 1809+ or Windows 11, x64.
- A standard user account. It never asks for admin rights.
- Write access to *one* of: `%LOCALAPPDATA%`, `%TEMP%`, or its own folder
  (portable mode). Logs, crash reports and fallback screenshots go there.
- No network access, no installer, no services, no registry writes, no
  drivers, no runtimes. Python, Qt and the Visual C++ runtime are inside
  `_internal\`.

## Getting it onto a machine

1. Download the release zip and check its SHA-256 against `SHA256SUMS.txt`:
   `certutil -hashfile CritOverlay-v1.5.0-rc.5-win64.zip SHA256`
2. Right-click the zip > Properties > **Unblock**, then extract it. Files
   extracted from a downloaded zip carry the "mark of the web", which makes
   SmartScreen prompt for the exe (and is reported by `--diagnose` as
   `mark_of_the_web_zone: 3`).
3. Extract to a local folder, e.g. `%LOCALAPPDATA%\Programs\CritOverlay`.
   - Don't launch the exe from inside the zip window. Explorer then extracts
     only the exe, `_internal` is missing, and it fails immediately.
   - A network share works but starts slower, and antivirus scans every DLL
     on first load.

## Things that commonly get in the way

### Application control (AppLocker, WDAC, Software Restriction Policies)

**Symptom:** "This app has been blocked by your system administrator", or
nothing happens at all.

Many lab images only allow programs under `C:\Program Files` and
`C:\Windows`. Nothing the app does can get around that, and it shouldn't.
Options for IT:

- **Hash rule** for `CritOverlay.exe` and `CritOverlay-console.exe` (hashes
  in `SHA256SUMS.txt`, or `Get-AppLockerFileInformation`). AppLocker DLL
  rules are rarely enabled; if they are, the files in `_internal` need
  allowing too.
- **Path rule**: install the folder under `C:\Program Files\CritOverlay`
  (copying it there is the whole install; it never writes to its own folder
  unless portable mode is on).
- The release is not code-signed, so a publisher rule isn't possible.

If running `python.exe` from the lab's own Python install is allowed, the
source version (`run.bat`) is an alternative that needs no new executable.

### SmartScreen and Defender

- "Windows protected your PC": More info > Run anyway, or Unblock the zip
  before extracting.
- Defender ASR rule "Block executable files unless they meet a prevalence,
  age, or trusted list criterion" can block a new unsigned exe. IT can add an
  exclusion by path or hash.
- The build doesn't use UPX packing or onefile self-extraction, both common
  causes of antivirus false positives. If a scanner does quarantine part of
  `_internal`, the app names the missing file on startup instead of failing
  silently.

### Controlled Folder Access (ransomware protection)

Blocks unknown apps from writing to Pictures and Documents. Screenshots then
can't go to `Pictures\CritOverlay`. The app falls back to
`%LOCALAPPDATA%\CritOverlay\Screenshots` (and then `%TEMP%`), says where it
saved, and remembers the working folder so Defender's "unauthorized changes
blocked" notice doesn't repeat. To restore the Pictures location, IT can
allow `CritOverlay.exe` through Controlled Folder Access, or set
`screenshot_dir` (below) to a writable folder.

### Folder redirection, OneDrive, roaming profiles

- Pictures, AppData and Desktop are resolved through Windows' known-folder
  API, so redirection to OneDrive or a network share is followed rather than
  assuming `C:\Users\<name>\Pictures`.
- If the roaming settings folder isn't writable (offline share), settings go
  to `%LOCALAPPDATA%\CritOverlay\` instead. If nothing is writable, the app
  still runs; settings just don't persist.
- Mandatory or reset-at-logoff profiles lose settings at logoff. Use a
  machine-wide `CritOverlay.ini` (below) for anything that should stick.

### Restricted networks

The app makes no network connections: no update checks, telemetry, license
checks or font downloads. It works fully offline and behind any proxy.
Building it does need PyPI access once, or an offline wheelhouse; see
[BUILDING.md](BUILDING.md).

### Other software's Qt settings

DCC apps (Maya, Houdini, Nuke, Substance, Toon Boom) often set `QT_*`
environment variables machine-wide. Pointed at a different Qt build, those
crash other Qt apps or misplace their windows. Crit Overlay clears them at
startup and logs what it removed. `--keep-qt-env` turns that off for
debugging.

## Machine-wide configuration

Put a `CritOverlay.ini` next to `CritOverlay.exe` (start from
`CritOverlay.ini.example`). Its `[compat]` section sets defaults for
everyone using that copy; a user's own settings file and command-line flags
override it.

```ini
[compat]
portable = false        ; true: settings/logs/screenshots in .\CritOverlay-data
input = auto            ; auto | mouse
screens = cursor        ; cursor | primary: which monitor it starts on
safe_mode = false       ; true = input mouse + screens primary
screenshot_dir =        ; e.g. %USERPROFILE%\OneDrive\Crits
```

The overlay covers one monitor at a time (by design; see
[COMPATIBILITY.md](COMPATIBILITY.md#why-one-monitor-at-a-time)). Users move it
with the toolbar's monitor button or F7. Configs from early 1.5.0
development builds with `screens = auto` or `all` still work; they're treated as `cursor`, and a
user's own settings file is rewritten to say so.

Command-line flags (for shortcuts): `--safe-mode`, `--input mouse`,
`--screens primary`, `--portable`, `--screenshot-dir <dir>`, `--debug`,
`--diagnose`, `--selftest`, `--no-dialogs`. `CritOverlay-console.exe --help`
lists them.

### Hotkey and Start with Windows

- The global show/hide hotkey defaults to Ctrl+Alt+D. If the lab image
  uses that combination for something else, set `hotkey = ...` (or
  `none`) in the machine-wide `[compat]` below.
- Start with Windows is per user and off by default. It writes only a
  shortcut to the user's Startup folder (`shell:startup`), never the
  registry, and points at the copy that enabled it. Mandatory or reset
  profiles lose it at logoff, like other per-user settings.
- One copy runs per logon session (users on a shared or remote-desktop
  machine each get their own).

## Portable / USB use

Set `portable = true` in `CritOverlay.ini` (or pass `--portable`). Settings,
logs and screenshots then live in `CritOverlay-data\` beside the exe, and
nothing is written to the user profile. If that folder isn't writable (a
read-only share), it falls back to the profile and logs why.

## Verifying a deployment

`Run Diagnostics.bat` (= `CritOverlay-console.exe --selftest`) starts the
app, shows the overlay for a few seconds (visiting each monitor), checks
window placement, the toolbar, drawing, undo, pass-through, screen capture
and folder access, and writes a report to the log folder. Exit code 0 means
everything passed. It saves no screenshots and leaves brush settings as they
were (an older settings file gets its one-time version stamp, as on any
launch), so it's safe to run from a deployment script.
