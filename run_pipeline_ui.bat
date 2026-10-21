@echo off
setlocal
cd /d "%~dp0"
set "UI_PYTHON=%~dp0.venv-ui\Scripts\python.exe"
if not exist "%UI_PYTHON%" (
  echo UI environment is missing. See docs\operators\pipeline-web-console.md
  pause
  exit /b 1
)
"%UI_PYTHON%" "%~dp0run_pipeline_ui.py"
if errorlevel 1 pause
endlocal
