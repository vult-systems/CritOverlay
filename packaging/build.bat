@echo off
rem Builds a standalone CritOverlay folder under dist\. Requires requirements-dev.txt.
cd /d "%~dp0\.."

python packaging\build_icon.py || exit /b 1
pyinstaller packaging\crit_overlay.spec --distpath dist --workpath build --noconfirm
echo.
echo Built dist\CritOverlay\CritOverlay.exe
pause
