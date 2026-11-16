@echo off
setlocal DisableDelayedExpansion
call "%~dp0run_app.bat" --action "recover-run" %*
set "FLOW_BASE_EXIT=%ERRORLEVEL%"
echo Review the recovery status and output path above.
pause
exit /b %FLOW_BASE_EXIT%
