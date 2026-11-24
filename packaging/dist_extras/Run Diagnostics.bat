@echo off
rem Checks that Crit Overlay works on this machine and writes a report.
rem The overlay appears for a few seconds, visiting each monitor.
title Crit Overlay diagnostics
"%~dp0CritOverlay-console.exe" --selftest
echo.
echo Send the report file named above when asking for help.
pause
