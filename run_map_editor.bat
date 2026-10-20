@echo off
setlocal
cd /d "%~dp0"
REM Match this to the QGIS_ROOT used by your existing map BAT files.
if not defined QGIS_ROOT set QGIS_ROOT=C:\PROGRA~1\QGIS34~1.7
set QGIS_APP=%QGIS_ROOT%\apps\qgis-ltr
set QGIS_PYTHON=%QGIS_ROOT%\bin\python.exe
if not exist "%QGIS_PYTHON%" (
  echo QGIS Python not found. Update QGIS_ROOT in run_map_editor.bat.
  pause
  exit /b 1
)
set VIRTUAL_ENV=
set PYTHONHOME=%QGIS_ROOT%\apps\Python312
set PYTHONUTF8=1
set QT_QPA_PLATFORM=
set PATH=%QGIS_ROOT%\apps\qt5\bin;%QGIS_ROOT%\apps\Python312;%QGIS_ROOT%\apps\Python312\Scripts;%QGIS_APP%\bin;%QGIS_ROOT%\bin;%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem
set PYTHONPATH=%QGIS_APP%\python;%QGIS_APP%\python\plugins;%QGIS_ROOT%\apps\Python312\Lib\site-packages
set QGIS_PREFIX_PATH=%QGIS_APP%
set QT_PLUGIN_PATH=%QGIS_ROOT%\apps\Qt5\plugins
set GDAL_DATA=%QGIS_ROOT%\apps\gdal\share\gdal
set GDAL_DRIVER_PATH=%QGIS_ROOT%\apps\gdal\lib\gdalplugins
set PROJ_DATA=%QGIS_ROOT%\share\proj
"%QGIS_PYTHON%" "%~dp0map_editor.py" %*
if errorlevel 1 pause
