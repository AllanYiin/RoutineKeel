@echo off
setlocal
pushd "%~dp0..\.."
set "STUDIO_PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%STUDIO_PYTHON%" (
  echo Studio development environment is not installed.
  echo Run: uv sync --project routinekeel/workflow_studio --group dev
  pause
  popd
  exit /b 1
)
start "" "%STUDIO_PYTHON%" -m workflow_studio --workspace "%CD%" ui
popd
endlocal
