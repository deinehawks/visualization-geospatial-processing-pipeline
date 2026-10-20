# Desktop Map Editor V2

Launch from the repository in PowerShell:

```powershell
.\run_map_editor.bat
```

The launcher uses the same QGIS installation/runtime settings as the map BATs.
If your installation moves, change `QGIS_ROOT` in this launcher (or set it before
launching). The normal virtual environment does not contain QGIS/Qt. No new
Python packages, web server, production database, or `.env` loading are needed.

## Generate a new map in the editor

Launching `run_map_editor.bat` without arguments now starts on **Generate Map**.
Existing `--package` and `--design` arguments still open the Editor directly.
The Editor tab keeps your current design while generation runs.

1. Choose **Add survey folder** and select a processed `AH-026xxx` folder or its
   `rgb` folder. Alternatively enter its survey ID and click **Add ID**.
2. For ID lookup, enter explicitly chosen survey roots, one per line, in the
   desired order. **Add survey root** opens a folder picker. Roots are remembered
   locally when Check Inputs/generation starts; startup never scans those roots.
3. Click **Check Inputs**. Select each survey row and review the exact folder,
   boundary, and TIFF paths. Multiple folder matches require an explicit choice.
   Task 4 TIFFs are preferred; choose a different candidate if needed. Published
   boundaries are preferred, with manifest/legacy boundary fallback.
4. Add/remove/reorder surveys as needed, then check inputs again. Multiple rows
   create one package with multiple raster layers, not a newly mosaicked TIFF.
5. Fill in map name, title, optional location, and output directory. Leave
   **Orthomosaic + boundary** selected unless you want **Boundary only**.
6. Click **Generate and open in editor**. Paper, styling, inset presets, and the
   final PDF/PNG export remain Editor controls.

Lookup is database-free: only the selected survey folders, direct root children,
and immediate four-digit year folders are examined. It does not search raw
dataset names, recursively scan drives, read `.env`, or run the processing
pipeline. Boundary candidates come from publication/manifest references and the
direct `rgb/boundary` directory; fallback TIFFs come from
`rgb/qgis/clipped/ortho`. References escaping a selected survey are ignored with
visible warnings. Malformed manifests fail explicitly rather than being hidden.

Every package gets a unique `<map-name>-<UTC timestamp>-<UUID>` folder under the
chosen output root (default `exports/maps`). Filesystem/share roots, repository
roots, and locations overlapping selected surveys are refused. Only small
boundary files are copied; original TIFFs are referenced by absolute path.
**Keep the original storage/mapped drive available** for editing and exporting.
Missing TIFFs never silently downgrade or omit an orthomosaic survey. Existing
CLI/BAT generation retains its default TIFF-copy behavior.

Only one discovery, generation, or export helper can run at a time. The generator
shows real stage logs and an indeterminate busy indicator. Cancellation stops
only its owned helper; a retry always creates a new attempt/package. Requests,
status, results, and logs are retained under
`exports/map_editor/generator/attempts/<timestamp>-<UUID>`; explicitly entered
roots live in `exports/map_editor/generator/roots.json`. A crashed attempt may
remain `incomplete`; it is evidence, not a resumable or completed package.

Successful generation prompts before replacing unsaved work. Cancelling that
prompt leaves the design unchanged: use **Open Generated Map** later. If editor
loading fails, the completed package remains at the displayed path. Failed or
cancelled generation never automatically replaces the current design, deletes
its evidence, or overwrites previous maps. A cancellation racing completion may
leave a valid package listed in its attempt receipt, but never auto-opens it.

### Generator validation and operator rollout

Automated validation uses pytest-owned tiny KML/KMZ and placeholder TIFF files,
fake Qt/process boundaries, and mocked editor loading. It does not prove real
TIFF readability or visual rendering. Acceptance coverage:

| Criteria | Evidence |
| --- | --- |
| Folder/rgb/ID lookup, ordered roots, manifest identity, ambiguity, bounded scans | `test_map_editor_generator.py` |
| Task 4 preference/override, publication KMZ, duplicates, missing/corrupt inputs | `test_map_editor_generator.py` |
| Single/combined/reference/boundary packages; unchanged legacy copying | `test_map_editor_generator.py` |
| Exclusive/contained outputs, immutable requests, failure/cancellation evidence, helper exclusion | `test_map_editor_generator.py` |
| Navigation, row ordering, exact choices, unsaved prompt, handoff/load failure | `test_map_editor_window.py` |
| Existing V2 rendering/history/recovery/presets/export compatibility | Existing model/session/workflow/renderer regressions |

Commands run for this integration (from the repository):

```powershell
python -m py_compile map_editor.py modules/map_export/editor_generator.py modules/map_export/editor_generator_page.py modules/map_export/map_package_exporter.py modules/map_export/editor_session.py modules/map_export/editor_window.py tests/test_map_editor_generator.py tests/test_map_editor_window.py
python -m pytest tests/test_map_editor_generator.py tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_map_editor_v2_workflow.py tests/test_map_export_manifest_resolution.py tests/test_map_export_multi_root.py tests/test_qgis_print_exporter_config.py -q
python -m pytest -q
python -m pytest --collect-only -q
git diff --check -- map_editor.py modules/map_export/map_package_exporter.py modules/map_export/editor_session.py modules/map_export/editor_window.py tests/test_map_editor_window.py docs/operators/map-editor.md docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md
git diff --no-index --check -- NUL modules/map_export/editor_generator.py
git diff --no-index --check -- NUL modules/map_export/editor_generator_page.py
git diff --no-index --check -- NUL tests/test_map_editor_generator.py
```

The three new-file whitespace commands normally return 1 for differing content;
they must report no whitespace errors. Final validation: 320 focused checks and
all 773 guarded default tests passed; 773 tests collected safely. Syntax and
scoped whitespace checks passed (Windows LF/CRLF warnings only).

This milestone creates two generator
modules and one regression module; modifies four existing application modules,
the window test fixture/tests, and four documentation files. Nothing is moved
or deleted; unrelated local changes and the existing V2 work are preserved.

Separately authorize an operator check before real survey discovery/generation:
name the read-only survey folders/roots and the new output root. Expected writes
are new package folders and editor-owned attempt/preferences/design files only.
No processing, source writes, database, credentials, or WebODM operations are
needed. Retain failed evidence; rollback is reopening the untouched prior design
or package, not deleting original data. Check single/combined generation, Task 4
and manual choice, both boundary formats, cancellation, unsaved handoff, mapped
drive loss, page fit/display scaling, and preview/PDF alignment in real QGIS.
No such live/source-drive check has been run by the coding agent.

## V2 editing workspace

Restart the editor with the same launcher to use V2. The light interface has a
page-object list on the left, page preview in the center, and Map / Text / Style /
Page tabs on the right. Click an editable map or label to select it. The blue
selection outline is drawn only in the view and is never exported. Positions,
frame sizes, logo, and page composition remain locked.

Drag inside a map to pan its contents; scroll over a map to zoom that map.
**Fit Page** and the Page tab's page-view zoom buttons change only your view of
the sheet, not the map scale. Map controls always identify the selected frame;
the status bar shows CRS, cartographic scale, source package, design, and dirty
state. Both insets can use project CRS or EPSG:4326. Switching CRS transforms
saved extents instead of interpreting projected coordinates as degrees.

Use **Style** for independent basemap opacity, boundary outline/fill/width/
opacity, and inset pin color/size. Opacity is displayed as a percentage. Main
grid lines and coordinate labels can be shown independently; line styling and
coordinate font size retain automatic interval/precision updates. Existing
defaults remain 4 mm red pins, 0.25 mm gray grid at 60% opacity, and the original
boundary styling. Native Windows export fonts remain enabled.

**Undo**: Ctrl+Z. **Redo**: Ctrl+Y or Ctrl+Shift+Z. Up to 100 editing steps are
kept in memory. A complete drag is one step; typing is grouped until 750 ms of
inactivity or another operation. Selection/page zoom do not enter history.
Failed edits roll back without adding a step. Save does not erase history;
undoing away from the saved state marks the design unsaved again.

### Presets

The Map tab includes built-in Davao City, Kapalong, and Philippines location
presets, plus custom location/style presets. Location presets apply only to the
selected inset and include its label, bounds, CRS, and marker mode. Styles
include per-frame styling, grid, text formatting, paper, and DPI, but never
survey references, wording, or map extents. Applying either type is undoable and
does not auto-fit the main map. Custom JSON presets use unique filenames under
`exports/map_editor/presets/`; malformed entries are reported, not executed.

### Recovery and export

Dirty designs autosave every 30 seconds into per-design/per-session recovery
files under their editor workspace. This never overwrites the named design or
copies TIFFs. Reopening offers recovery explicitly; No dismisses the available
snapshots without deleting them. Save, explicit discard, and undo back to the
saved state resolve the relevant recovery evidence. Recovery files remain for
diagnosis; there is no automatic cleanup. A write failure stays visible and
does not mark the design saved. Save manually when the workspace is read-only.

Exports still run one at a time from immutable snapshots into unique attempt
folders. The Page tab shows real log stages and an indeterminate busy indicator,
not an estimated percentage. After a successful export, use **Open PDF**,
**Open PNG**, or **Open Folder**. Export contains the captured extents, CRS,
text, and styles, not later edits, selection outlines, or automatic reframing.

## Open, edit, save

1. Click **Open map** and select an existing `exports/maps/<name>` folder.
   Its boundaries, metadata, and referenced TIFFs must be accessible. Packages
   containing multiple orthomosaics use all their referenced TIFFs.
2. For an older map, optionally click **Import BAT** and select its BAT.
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
6. Click **Save**. The suggested V2 location is
   `exports/map_editor/<package-name>/design.v2.mapedit.json`, outside the source.
   The design references inputs; it does not copy TIFFs or boundaries. Moving
   inputs or disconnecting their drive makes them unavailable until restored.

When opening a package, its saved V2 design takes precedence, followed by the
legacy `design.mapedit.json`. Otherwise saved rendering metadata seeds the controls; imported BAT
settings override it explicitly. Defaults fill missing settings. **Open design**
loads a design saved elsewhere. V1 designs remain readable with their original
appearance. First Save opens Save As for a separate V2 copy; overwriting V1
files is refused. Version 2 adds per-map and grid styles to the existing exact
extents/CRS, text, marker modes, paper, and DPI. Save uses same-folder
atomic replacement. Unsupported versions, missing inputs, or invalid values
produce an error. Unsaved edits prompt before closing or replacing the map.

Shared rendering accepts optional `map_styles` and `grid_style` arguments;
ordinary CLI/BAT exports keep their existing defaults. The editor uses separate
basemap instances for each frame, supported by the QGIS 3.34
[raster layer clone/opacity API](https://api.qgis.org/api/3.34/classQgsRasterLayer.html),
and a parameterized pin using the
[SVG symbol fill API](https://api.qgis.org/api/3.34/classQgsSvgMarkerSymbolLayer.html).

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
saved design, select **Lower Inset**, and set **Selected inset CRS** to
**WGS 84 / EPSG:4326**. Adjust its bounds/scale as usual, then save and export a new
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

### V2 implementation (2026-10-20)

V2 passed 273 focused checks, all 725 safe default tests, collection of 725
tests, changed-file compilation, and scoped whitespace checks. No live GUI,
QGIS/GDAL, tile service, production DB, `.env`, source drive, or generated map
was accessed or changed. Only public API documentation was consulted externally.

Acceptance mapping:

- `test_map_editor_window.py`: contextual selection, non-exported outline,
  page-vs-map zoom, action state, grouped typing/drag, failed-edit rollback,
  recovery consent/write errors, separate V1 saves, V2-first loading, output actions.
- `test_map_editor_v2_workflow.py`: 100-step bounded history, dirty/save/redo
  semantics, V1/V2 compatibility, validation, typed preset isolation, and
  recovery ownership/atomicity/evidence/source-attempt protection.
- `test_map_editor_session.py`: incremental restoration, failed rebuild recovery,
  independent layers, preset application, both inset CRS conversions, grid
  reuse/precision, and styling persistence through paper/CRS changes.
- Model/renderer regressions: styled helper snapshots preserve source/previous
  outputs on failure; final/live inset styles, CRS, and basemaps align; pin
  color/size remain print-unit based with the original geometry/tip.

Created `modules/map_export/editor_workflow.py` and
`tests/test_map_editor_v2_workflow.py`. Modified the four existing editor/shared
renderer modules, pin SVG, four existing editor/renderer test modules, this
guide, and refactor status/decisions/test strategy. Nothing moved or deleted by
V2; pre-existing deleted BATs and local notes/query defaults were untouched.
Existing production source code changed, not production datasets or state.
No commit or push was performed.

Exact validation commands:

```powershell
python -m py_compile modules/map_export/editor_model.py modules/map_export/editor_session.py modules/map_export/editor_workflow.py modules/map_export/editor_window.py modules/map_export/qgis_print_exporter.py tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_map_editor_v2_workflow.py tests/test_qgis_print_exporter_config.py
python -m pytest tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_map_editor_v2_workflow.py tests/test_qgis_print_exporter_config.py tests/test_map_export_multi_root.py tests/test_map_export_manifest_resolution.py tests/test_import_safety.py -q
python -m pytest -q
python -m pytest --collect-only -q | Select-Object -Last 2
git diff --check -- modules/map_export/editor_model.py modules/map_export/editor_session.py modules/map_export/editor_window.py modules/map_export/qgis_print_exporter.py assets/map_locator_pin.svg tests/test_map_editor_model.py tests/test_map_editor_session.py tests/test_map_editor_window.py tests/test_qgis_print_exporter_config.py docs/operators/map-editor.md docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md
$v2Files = @('modules/map_export/editor_workflow.py', 'tests/test_map_editor_v2_workflow.py')
foreach ($v2File in $v2Files) { git diff --no-index --check -- NUL $v2File; if ($LASTEXITCODE -gt 1) { exit $LASTEXITCODE } }
git status --short
```

Before visual rollout, use an explicitly approved sample to check the light
workspace at Windows 100%/150%/200% display scaling; selection and fixed frames;
independent faint/strong basemaps; pin recoloring/white outlines; grid visibility;
both inset CRS switches; grouped undo/redo; save/reopen/recovery; and exported
PDF/PNG appearance against the page preview. Cancellation must still preserve
earlier exports. This live check is pending, not silently treated as passed.

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
