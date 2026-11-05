@echo off
setlocal
if not exist "%~dp0reports\report.html" (
  echo No report yet. Run setup_auto_update.cmd or update_now.cmd first.
  pause
  exit /b 1
)
start "" "%~dp0reports\report.html"
