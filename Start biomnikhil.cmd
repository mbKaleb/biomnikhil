@echo off
rem Double-click to start biomnikhil: runs run.ps1 in this window.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if errorlevel 1 pause
