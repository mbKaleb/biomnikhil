@echo off
rem Double-click to set up and start biomnikhil (runs app\run.ps1 in this window).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\run.ps1" %*
if errorlevel 1 pause
