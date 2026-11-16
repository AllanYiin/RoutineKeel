@echo off
setlocal DisableDelayedExpansion
title flow_base
echo flow_base: preparing environment and starting...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "FLOW_BASE_EXIT=%ERRORLEVEL%"
if not "%FLOW_BASE_EXIT%"=="0" (
  echo.
  echo Startup failed. Keep the error above and check share_logs\launcher.
  echo You can retry by opening this file again.
  pause
)
exit /b %FLOW_BASE_EXIT%
