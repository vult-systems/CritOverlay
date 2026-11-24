@echo off
rem Runs Crit Overlay from source. Extra arguments are passed through,
rem e.g.  run.bat --safe-mode
setlocal
title Crit Overlay
rem pushd, not cd /d: cd can't enter a UNC path (\server\share\...), which is
rem where redirected home folders often put this; pushd maps a drive letter.
pushd "%~dp0" || (echo Can't access %~dp0 & pause & exit /b 1)

rem Prefer whichever interpreter already has PySide6. The py launcher may
rem pick a newer Python than the one the lab installed packages into.
set "PY="
python -c "import PySide6" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import PySide6" >nul 2>nul && set "PY=py -3"
if not defined PY (
    echo Couldn't find a Python with PySide6 installed.
    echo Install it for your user only ^(no admin needed^):
    echo     python -m pip install --user -r requirements.txt
    echo Or use the standalone build, which needs no Python at all.
    popd
    pause
    exit /b 1
)
%PY% -m crit_overlay %*
set "ERR=%ERRORLEVEL%"
popd
exit /b %ERR%
