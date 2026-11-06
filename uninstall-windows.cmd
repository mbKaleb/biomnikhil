@echo off
rem Double-click to remove biomnikhil from this PC. It runs app\uninstall.ps1 in
rem this window, which lists everything first and asks before deleting.
rem Leaves this folder first, because a window's current folder can't be deleted.
rem The final block is read whole before it runs, so cmd doesn't need this file
rem any more once the uninstaller has deleted it.
setlocal
set "LAUNCHER_FOLDER=%~dp0"
cd /d "%TEMP%"
(
  powershell -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER_FOLDER%app\uninstall.ps1"
  if errorlevel 3 (
    pause
    exit /b 3
  )
  if errorlevel 2 (
    pause
    exit /b 2
  )
  if errorlevel 1 (
    pause
    exit /b 1
  )
  pause
  exit /b 0
)
