@echo off
setlocal

REM Run from the folder where this .bat file is located
cd /d "%~dp0"

REM QGIS install paths
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
  --survey "Y:\Visualization\field-data\2026\sorted\20260421\Dagaang\DNG-001_36.4Ha_M3C_100m_85f75s_8mps" ^
  --name "dagaang-overall-map" ^
  --title "Whole World Agri-Tourism Farm and Resort" ^
  --location "Del Pilar, New Corella" ^
  --map-scale 15000 ^
  --layout-template "assets/qgis_layouts/client_boundary_map-v6.qpt" ^
  --include-orthomosaic ^
  --export-print ^
  --logo "assets/logo.png"

pause