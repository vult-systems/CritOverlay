@echo off
title Crit Overlay (debug)
cd /d "%~dp0"
python -m crit_overlay --debug
echo.
echo Exit code: %ERRORLEVEL%
pause
