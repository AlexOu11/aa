@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
py -3 -m stock_report.automation uninstall
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
