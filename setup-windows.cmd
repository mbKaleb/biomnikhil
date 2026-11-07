@echo off
rem Double-click to install and start biomnikhil (runs app\setup.ps1 in this window).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\setup.ps1" %*
if errorlevel 1 (
  echo.
  echo biomnikhil setup did not finish. See above, or setup.log in the install folder.
  pause
)
