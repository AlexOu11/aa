@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 goto no_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 goto no_python
py -3 -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Taipei')" >nul 2>&1
if errorlevel 1 (
  py -3 -m pip install tzdata
  if errorlevel 1 goto failed
)
py -3 -m stock_report.automation install %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo See logs\update.log. A task may already be installed even if the first update failed.
if exist "reports\report.html" start "" "%~dp0reports\report.html"
pause
exit /b %RESULT%
:no_python
echo Install Python 3.12 or newer with the Python launcher, then run this file again.
pause
exit /b 1
:failed
echo Timezone setup failed. Check your network connection and Python installation.
pause
exit /b 1
