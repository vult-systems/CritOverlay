@echo off
rem Reproducible build of dist\CritOverlay and its release zip.
rem
rem   packaging\build.bat                 full build + verification
rem   packaging\build.bat --no-run        verify statically only (no window shown)
rem
rem Needs 64-bit Python 3.13 (3.12 also accepted); see docs\BUILDING.md.
rem Default interpreter is "py -3.13" (the Python launcher). To use another,
rem set CRIT_PYTHON to the full path of its python.exe. A per-user install
rem ("winget install Python.Python.3.13 --scope user") needs no admin rights.
rem
rem The toolchain goes into a private venv (.venv-build) from the hash-pinned
rem packaging\requirements-build.txt; packaging\venv_setup.py creates it,
rem recreates it if a different Python made it, and installs offline from
rem wheelhouse\ if that folder exists. Set CRIT_NO_PAUSE=1 to skip the pause.

setlocal
pushd "%~dp0\.." || (echo Can't access the repository folder. & exit /b 1)

if defined CRIT_PYTHON (
    "%CRIT_PYTHON%" packaging\venv_setup.py || goto :fail
) else (
    py -3.13 packaging\venv_setup.py || goto :fail
)

".venv-build\Scripts\python.exe" packaging\build.py %* || goto :fail

popd
echo.
echo Build OK: dist\CritOverlay\CritOverlay.exe
if not "%CRIT_NO_PAUSE%"=="1" pause
exit /b 0

:fail
set "ERR=%ERRORLEVEL%"
popd
echo.
echo BUILD FAILED (exit code %ERR%)
if not "%CRIT_NO_PAUSE%"=="1" pause
exit /b %ERR%
