@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
py -3 -m stock_report.automation run
set "RESULT=%ERRORLEVEL%"
if exist "reports\report.html" start "" "%~dp0reports\report.html"
pause
exit /b %RESULT%
