@echo off
setlocal
cd /d "%~dp0"
if defined PIPELINE_SYNC_PYTHON (
    "%PIPELINE_SYNC_PYTHON%" tools\sync_pipeline_sheet.py %*
) else if exist ".venv310\Scripts\python.exe" (
    ".venv310\Scripts\python.exe" tools\sync_pipeline_sheet.py %*
) else (
    python tools\sync_pipeline_sheet.py %*
)
set "SYNC_EXIT=%ERRORLEVEL%"
if not "%SYNC_EXIT%"=="0" pause
exit /b %SYNC_EXIT%
