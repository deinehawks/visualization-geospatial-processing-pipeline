@echo off
setlocal

REM ============================================================
REM Run from the folder where this .bat file is located
REM ============================================================
cd /d "%~dp0"

REM ============================================================
REM Project Settings
REM ============================================================

set SURVEY=Z:\field-data-2026\sorted\20260616-18\AMS_101_5Ha_A2S_50m_85f75s_3mps

set MAP_NAME=amskarbemco-5ha-A3

set TITLE=AMSKARBEMCO Orthomosaic Map

set LOCATION=Kapalong, Davao del Norte

set REGION=Davao del Norte

REM Main map automatically fits the orthomosaic TIFF.
REM Increase for more surroundings; decrease for a tighter crop.
set MAP_EXPORT_MAIN_BUFFER=0.08

REM Add surrounding context, then round outward to a standard map scale.
set MAP_EXPORT_AUTO_SCALE_MULTIPLIER=2.2

REM Boundary inset padding: 0.35 keeps context without making the site tiny.
set MAP_EXPORT_BOUNDARY_INSET_BUFFER=0.35

REM Upper-right inset: optional fixed area; leave BOTH blank to auto-fit the boundary.
REM Bounds order: min_lon,min_lat,max_lon,max_lat (WGS 84 / EPSG:4326).
REM Fixed bounds override MAP_EXPORT_BOUNDARY_INSET_BUFFER.
set MAP_EXPORT_BOUNDARY_INSET_LABEL=
set MAP_EXPORT_BOUNDARY_INSET_BOUNDS=

REM Independent inset zoom-out: 1.0 keeps the view; 1.5 shows 50%% more width/height.
REM Applies after bounds/automatic fitting. Use values >= 1.0.
set MAP_EXPORT_BOUNDARY_INSET_ZOOM_OUT=1.0
set MAP_EXPORT_OVERVIEW_ZOOM_OUT=1.0

REM Print paper size: A4 or A3. The layout remains landscape.
set MAP_EXPORT_PAPER_SIZE=A3

REM Lower-right overview inset. Set both values together.
REM Bounds order: min_lon,min_lat,max_lon,max_lat (WGS 84 / EPSG:4326).
set MAP_EXPORT_OVERVIEW_LABEL=Davao City
set MAP_EXPORT_OVERVIEW_BOUNDS=125.3063,6.8710,125.9308,7.3282

set LAYOUT=assets/qgis_layouts/client_boundary_map-v6.qpt

set LOGO=assets/logo.png

REM Optional
set DISCLAIMER=Disclaimer: This map is intended for visualization and reference purposes only.

REM ============================================================
REM QGIS install paths
REM ============================================================

set QGIS_ROOT=C:\PROGRA~1\QGIS34~1.7
set QGIS_APP=%QGIS_ROOT%\apps\qgis-ltr
set QGIS_PYTHON=%QGIS_ROOT%\bin\python.exe

REM Clear active virtual environment variables if this is launched from .venv
set VIRTUAL_ENV=
set PYTHONHOME=%QGIS_ROOT%\apps\Python312
set PYTHONUTF8=1

REM Core QGIS/Python/Qt paths
set PATH=%QGIS_ROOT%\apps\qt5\bin;%QGIS_ROOT%\apps\Python312;%QGIS_ROOT%\apps\Python312\Scripts;%QGIS_APP%\bin;%QGIS_ROOT%\bin;%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem
set PYTHONPATH=%QGIS_APP%\python;%QGIS_APP%\python\plugins;%QGIS_ROOT%\apps\Python312\Lib\site-packages
set QGIS_PREFIX_PATH=%QGIS_APP%
set QT_PLUGIN_PATH=%QGIS_ROOT%\apps\Qt5\plugins

REM GDAL / PROJ runtime data
set GDAL_DATA=%QGIS_ROOT%\apps\gdal\share\gdal
set GDAL_DRIVER_PATH=%QGIS_ROOT%\apps\gdal\lib\gdalplugins
set PROJ_DATA=%QGIS_ROOT%\share\proj

echo Testing QGIS Python import...
"%QGIS_PYTHON%" -c "from qgis.core import QgsApplication; from PyQt5.QtCore import QT_VERSION_STR; print('QGIS import OK')"

if errorlevel 1 (
    echo.
    echo QGIS Python import failed. Check QGIS_ROOT and QGIS_APP paths.
    pause
    exit /b 1
)

echo.
echo Running map export...

"%QGIS_PYTHON%" map.py ^
  --source-root "Z:\field-data-2026\sorted" ^
  --survey-root "Z:\surveys" ^
  --survey "%SURVEY%" ^
  --name "%MAP_NAME%" ^
  --title "%TITLE%" ^
  --location "%LOCATION%" ^
  --region "%REGION%" ^
  --disclaimer "%DISCLAIMER%" ^
  --layout-template "%LAYOUT%" ^
  --include-orthomosaic ^
  --export-print ^
  --logo "%LOGO%"

pause
