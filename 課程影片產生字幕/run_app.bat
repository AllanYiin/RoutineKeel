@echo off
setlocal DisableDelayedExpansion
title flow_base workflow
if not exist "%~dp0..\flow_base\start.ps1" (
  echo Missing shared flow_base. Extract the flow_base package beside this workflow folder.
  pause
  exit /b 2
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\flow_base\start.ps1" launch --folder "%~dp0." %*
set "FLOW_BASE_EXIT=%ERRORLEVEL%"
if not "%FLOW_BASE_EXIT%"=="0" (
  echo.
  echo Workflow startup failed. Keep the message above and check share_logs\launcher.
  echo Retry by opening this file again.
  pause
)
exit /b %FLOW_BASE_EXIT%
