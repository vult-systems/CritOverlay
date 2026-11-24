@echo off
rem Runs Crit Overlay from source with verbose console logging.
setlocal
title Crit Overlay (debug)
pushd "%~dp0" || (echo Can't access %~dp0 & pause & exit /b 1)
set "PY="
python -c "import PySide6" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import PySide6" >nul 2>nul && set "PY=py -3"
if not defined PY set "PY=python"
%PY% -m crit_overlay --debug %*
echo.
echo Exit code: %ERRORLEVEL%
popd
pause
