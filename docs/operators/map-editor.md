# Desktop Map Editor

Launch from the repository in PowerShell:

```powershell
.\run_map_editor.bat
```

The launcher uses the same QGIS installation/runtime settings as the map BATs.
If your installation moves, change `QGIS_ROOT` in this launcher (or set it before
launching). The normal virtual environment does not contain QGIS/Qt. No new
Python packages, web server, production database, or `.env` loading are needed.

## Open, edit, save

1. Click **Open map folder** and select an existing `exports/maps/<name>` folder.
   Its boundaries, metadata, and referenced TIFFs must be accessible. Packages
   containing multiple orthomosaics use all their referenced TIFFs.
2. For an older map, optionally click **Import BAT settings** and select its BAT.
   This reads whitelisted literal settings only; it never executes the script.
   Shell expansions and unsupported/malformed values are reported and ignored.
   Import reapplies framing settings, resetting manually edited map extents.
3. Select **Main Map**, **Upper Inset**, or **Lower Inset**. Drag inside that map
   to pan; scroll or use the zoom buttons to zoom. Scale and WGS 84 bounds can
   also be applied directly. Bounds use `min_lon,min_lat,max_lon,max_lat`.
   Frame proportions can require extra surrounding area.
4. Select a label and type. Font family, point size, bold, and color update live.
   Frames, label positions, logo, and other layout elements stay fixed.
5. Choose A4/A3 landscape and output DPI. Each inset can use a boundary or pin.
   The main map's coordinate grid, scale text, and scale bar follow navigation.
   Only **Auto-fit main map** reapplies standard automatic scale selection.
   **Main-map coordinates (pt)** changes the coordinate-label font size live
   (1–72 pt, default 6 pt). The size follows zoom/pan, paper switching, saved
   designs, and final export. Older designs automatically retain the 6 pt default.
6. Click **Save design**. The suggested location is
   `exports/map_editor/<package-name>/design.mapedit.json`, outside the source.
   The design references inputs; it does not copy TIFFs or boundaries. Moving
   inputs or disconnecting their drive makes them unavailable until restored.

When opening a package, a saved design in its suggested workspace takes
precedence. Otherwise saved rendering metadata seeds the controls; imported BAT
settings override it explicitly. Defaults fill missing settings. **Open design**
loads a design saved elsewhere. Design version 1 stores extents and CRS exactly,
plus text, text styling, marker modes, paper size, and DPI. Save uses same-folder
atomic replacement. Unsupported versions, missing inputs, or invalid values
produce an error. Unsaved edits prompt before closing or replacing the map.

Defaults for a new editor design are A3, PNG 300 DPI, PDF 450 DPI, inset 800 DPI.
Existing saved/imported settings take precedence. Previews use a 96-DPI layout
context, with drag updates coalesced at 40 ms. Large rasters and remote basemap
tiles can still take time to load; text does not trigger high-resolution exports.

## Export and cancellation

**Export PDF + PNG** captures an immutable design snapshot and starts one owned
QGIS helper process. Continue editing while it exports; those later edits do not
change the captured export. A second export cannot start until the first ends.

Each attempt receives a new directory under
`exports/map_editor/<package-name>/exports/<UTC timestamp>-<UUID>/` containing:

- `snapshot.mapedit.json`: exact design and source references;
- `attempt.json`: incomplete/failed/cancelled/complete status;
- `export.log`: helper output;
- successful `preview.png`, `print_map.pdf`, inset images, and rendering metadata.

The source package and previous exports are not overwritten. **Cancel export**
terminates only this editor's helper, escalating to its owned process after five
seconds if necessary. Partial files remain in the failed/cancelled attempt for
diagnosis. A hard application crash may leave an `incomplete` attempt; never
retry into that directory. Start a new export instead.

You can reopen an export's snapshot, including by opening its attempt folder,
but must save subsequent edits elsewhere. An export attempt is not a standalone
copy of its source package: source TIFF/boundary references must remain available.

The normal map BATs retain their existing defaults and export behavior. The
editor does not start WebODM, process a survey, or modify pipeline state.

## Manual rollout check (requires separate operator authorization)

Automated tests mock QGIS, Qt, and process boundaries. Before operational use,
launch the editor with an approved sample map and verify:

- all three frames, template images, and basemap layers render;
- drag/wheel, scale/bounds, reset, and auto-fit affect the intended map;
- repeated navigation updates grid/scale furniture without changing frame size;
- text styles, both marker modes, A3/A4 switching, and save/reopen agree;
- the exported PDF/PNG match the live page at their selected DPI, especially
  inset basemap/overlay alignment and the pin tip;
- cancellation preserves previous exports and a later new attempt succeeds.

No QGIS/GDAL execution or real-data visual check is part of automated validation.

## Black text blocks in older editor exports

An operator export log showed `QFontDatabase: Cannot find font directory /fonts`.
The original helper forced Qt's `offscreen` backend on Windows, which uses a
different font database from the editor window. Windows helpers now explicitly
use the native `windows` backend. No visible helper window is created. Qt's
[offscreen implementation](https://github.com/qt/qtbase/blob/5.15/src/plugins/platforms/offscreen/qoffscreenintegration.cpp)
documents that separate Windows FreeType font database.

Save the design, close and reopen the editor through `run_map_editor.bat`, and
export again. Existing exports are preserved. If the helper has no available
fonts, it now stops with a clear error before building the map or writing images,
rather than completing an unreadable export. The source TIFFs are unaffected.

The font fix and coordinate-label control passed 185 focused checks and the
637-test safe suite; collection found 637 tests. Syntax/whitespace checks passed.
This validation used mocked Qt/QGIS, not a real map regeneration. The supplied
export log and snapshot were inspected read-only; the supplied images/PDF and
all existing output files were left unchanged. No files were created or deleted
as part of this follow-up; editor/exporter code, tests, and documentation changed.

Follow-up validation commands:

```text
python -m py_compile modules/map_export/editor_model.py modules/map_export/editor_session.py modules/map_export/editor_window.py modules/map_export/qgis_print_exporter.py tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_qgis_print_exporter_config.py
python -m pytest tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_qgis_print_exporter_config.py tests/test_map_export_multi_root.py tests/test_map_export_manifest_resolution.py tests/test_import_safety.py -q
python -m pytest -q
python -m pytest --collect-only -q | Select-Object -Last 2
git diff --check -- modules/map_export/qgis_print_exporter.py docs/refactor/CURRENT_STATUS.md docs/refactor/TEST_STRATEGY.md
```

## Pins for the Davao City upper inset

New editor designs default the upper inset to a fixed-size location pin. Existing
designs and package metadata keep their explicit marker choice: select **Upper
Inset -> Inset marker -> Pin**, save, and export again. This does not change map
bounds, zoom, CRS, the main map boundary, or lower inset. Restart the editor after
updating the code. To revert, select **Boundary** instead.

The experiment `stm_50m_A3_V2.bat` now includes:

```bat
set MAP_EXPORT_BOUNDARY_INSET_OVERLAY=pin
```

Normal BAT exports without this setting keep their existing boundary default.
The setting accepts `pin` or `boundary`, is supported by safe BAT importing, and
is recorded in rendering metadata. Invalid batch values report a warning and
fall back to boundary. The pin reuses the existing 4 mm print-size marker at the
survey location, not an enlarged or relocated survey polygon.

Validation used only mocked rendering: 220 focused checks and all 672 safe tests
passed; 672 tests collected safely. No existing output or source dataset was
changed, and actual QGIS rendering was not executed. The changed files are the
editor model, shared renderer, experiment BAT, model/renderer test modules, this
guide, refactor status, and test strategy; none were created, moved, or deleted.
Exact checks executed:

```powershell
python -m py_compile modules/map_export/editor_model.py modules/map_export/qgis_print_exporter.py tests/test_map_editor_model.py tests/test_qgis_print_exporter_config.py
python -m pytest tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_qgis_print_exporter_config.py tests/test_map_export_multi_root.py tests/test_map_export_manifest_resolution.py tests/test_import_safety.py -q
python -m pytest -q
python -m pytest --collect-only -q | Select-Object -Last 2
git diff --check -- modules/map_export/qgis_print_exporter.py docs/refactor/CURRENT_STATUS.md docs/refactor/TEST_STRATEGY.md
$pinFiles = @('modules/map_export/editor_model.py', 'stm_50m_A3_V2.bat', 'tests/test_map_editor_model.py', 'tests/test_qgis_print_exporter_config.py', 'docs/operators/map-editor.md')
foreach ($pinFile in $pinFiles) { git diff --no-index --check -- NUL $pinFile; if ($LASTEXITCODE -gt 1) { exit $LASTEXITCODE } }
git status --short
```

## Philippines overview and WGS 84

Save your work, close the editor, and relaunch `run_map_editor.bat`. Reopen the
saved design and set **Lower inset CRS** to **WGS 84 / EPSG:4326**. Select
**Lower Inset** to adjust its bounds/scale as usual, then save and export a new
attempt. The main orthomosaic and upper inset keep their existing CRS. New editor
designs default the lower frame to EPSG:4326; settings explicitly loaded from
package metadata, a design, or a BAT can retain `project` instead.

Old saved overview extents are transformed in memory, with aspect-ratio fitting
to the fixed frame. Each snapshot now stores the actual CRS for each map. Pin
geometry keeps its source CRS and is transformed by QGIS for display. Source
TIFFs, boundaries, and existing export attempts are not rewritten. Select
**Same as main map** to revert the frame CRS without changing source data.

`stm_50m_A3_V2.bat` now includes the following explicit setting (also supported
by literal-only BAT importing). Other batch defaults are unchanged:

```bat
set MAP_EXPORT_OVERVIEW_CRS=EPSG:4326
```

This targets possible local-projection clipping in the national inset. A
successful real export is still needed to confirm the white strip disappears;
failed/missing basemap tiles can also create blank areas. Automated validation
did not run QGIS, load real maps, or contact tile services.

This follow-up modified four source modules, the experiment BAT, four test
modules, and four documentation files; no files were added, moved, or deleted.
Syntax checks, 207 focused checks, all 659 safe default tests, collection of
659 tests, and scoped whitespace checks passed. Exact validation commands:

```powershell
python -m py_compile modules/map_export/editor_model.py modules/map_export/editor_session.py modules/map_export/editor_window.py modules/map_export/qgis_print_exporter.py tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_qgis_print_exporter_config.py
python -m pytest tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_qgis_print_exporter_config.py tests/test_map_export_multi_root.py tests/test_map_export_manifest_resolution.py tests/test_import_safety.py -q
python -m pytest -q
python -m pytest --collect-only -q | Select-Object -Last 2
git diff --check -- modules/map_export/qgis_print_exporter.py docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md
$insetFiles = @('modules/map_export/editor_model.py', 'modules/map_export/editor_session.py', 'modules/map_export/editor_window.py', 'stm_50m_A3_V2.bat', 'tests/test_map_editor_model.py', 'tests/test_map_editor_session.py', 'tests/test_map_editor_window.py', 'tests/test_qgis_print_exporter_config.py', 'docs/operators/map-editor.md')
foreach ($insetFile in $insetFiles) { git diff --no-index --check -- NUL $insetFile; if ($LASTEXITCODE -gt 1) { exit $LASTEXITCODE } }
git status --short
```

## Implementation validation commands

These checks passed during implementation (167 focused checks, 619 full-suite
checks, and 619 safely collected tests):

```text
python -m py_compile map_editor.py modules/map_export/editor_model.py modules/map_export/editor_session.py modules/map_export/editor_window.py modules/map_export/qgis_print_exporter.py tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py
python -m pytest tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_qgis_print_exporter_config.py tests/test_map_export_multi_root.py tests/test_map_export_manifest_resolution.py tests/test_import_safety.py -q
python -m pytest -q
python -m pytest --collect-only -q | Select-Object -Last 2
git diff --check -- modules/map_export/qgis_print_exporter.py docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md
```

Each new file also received `git diff --no-index --check -- NUL <file>`;
Git reported only LF/CRLF conversion warnings. The new files are the entry
point, launcher, three editor modules, three regression-test modules, and this
operator guide. No dependencies were added, and no files were moved/deleted.
