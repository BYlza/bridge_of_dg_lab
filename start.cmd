@echo off
rem Bridge of DG-LAB -- one-click launcher.
rem Double-click  -> preview mode (no output).
rem From terminal -> start.cmd -Arm -Channel B  (arguments are passed through)
cd /d "%~dp0"
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
