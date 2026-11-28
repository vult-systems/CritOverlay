Crit Overlay - standalone build for Windows 10 (1809 or later) and 11, 64-bit
=========================================================================

Run CritOverlay.exe. No installer, no administrator rights, and nothing else
to install: Python, Qt and the Visual C++ runtime are all inside _internal.
Nothing in it uses the network.

Setting it up
  1. Extract the WHOLE zip first. Don't run the exe from inside the zip
     window; Windows only extracts the exe and it can't find _internal.
  2. Put the folder somewhere local you can write to, for example
     %LOCALAPPDATA%\Programs\CritOverlay. Network drives work but start
     slower, and some schools block running programs from them.
  3. If Windows says "Windows protected your PC" (SmartScreen), choose
     More info > Run anyway. To avoid that for every file, right-click the
     zip > Properties > Unblock before extracting.

If it won't start
  * "This app has been blocked by your system administrator": a school
    policy (AppLocker/WDAC) only allows approved programs. There's no
    workaround on your side. Send IT the SHA256SUMS.txt from the release
    page (or the exe's hash: certutil -hashfile CritOverlay.exe SHA256)
    and ask for it to be allowed.
  * Anything else: a message box explains it and names a crash report file.
    Run "Run Diagnostics.bat" and send the report it creates.

If the overlay is offset from the screen, the pen doesn't work, or it
acts up on a projector: run "CritOverlay (Safe Mode).bat". See
TROUBLESHOOTING in the project docs for the specific fixes.

Monitors
  The overlay covers one monitor at a time, starting on the one under the
  mouse. The monitor button on the toolbar (or F7) moves it to the next
  monitor, e.g. from your laptop to the projector. Your drawing moves with
  it. If the monitor it's on is unplugged, it moves to the primary one.

Capturing an area
  When the overlay appears, drag a rectangle around what you want to mark
  up. It becomes a still image centered on the monitor. Click once instead
  to draw over the whole monitor. Hold Space and drag to move the capture.
  The dashed-frame button on the toolbar adds a margin around it for notes.
  F9 saves the image, margin and notes, hides the overlay, opens the image,
  and opens Explorer with the file selected. Esc discards the capture and
  lets you select a new area; R does the same.

Hiding and quitting
  Esc (with no capture) hides the overlay to the notification area (tray)
  and keeps your drawing. Ctrl+Alt+D, from any app, or clicking the tray
  icon brings it back. To quit, right-click the tray icon > Exit. Starting
  it again while it runs just brings it back. Start with Windows is in the
  same menu.

Files it writes
  Logs:        %LOCALAPPDATA%\CritOverlay\logs\
  Settings:    %APPDATA%\CritOverlay\Crit Overlay.ini
  Screenshots: Pictures\CritOverlay\  (falls back to
               %LOCALAPPDATA%\CritOverlay\Screenshots if Pictures is blocked)
  To keep everything inside this folder instead (USB stick), copy
  CritOverlay.ini.example to CritOverlay.ini and set portable = true.
