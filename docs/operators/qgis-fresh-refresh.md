# Fresh Task 4 QGIS refresh

Use this explicit CLI operation after a restarted Task 4 has completed and the
pipeline has downloaded its updated orthomosaic. It renders a new clip and new
round-corners tiles even when the previous QGIS stage completed successfully.
It does not restart WebODM, upload imagery, download artifacts, run Task 2, or
repeat quality review. Inspect the updated orthomosaic before proceeding.

Ordinary resume and `--force-stage qgis` retain their previous reuse behavior.
The existing `--rebuild-qgis-tiles` remains recovery for unreadable partial tiles;
it is not the fresh-clip workflow.

## Before running

- Pause/finish all other local invocations and wait for owned children to exit.
  Refresh is exclusive even when `max_runs` is three. Do not bypass admission.
- Resume the exact original run/survey/year with Task 4-only selection.
- Latest data-segregation, boundary, aggregate WebODM and Task 4 stages must be
  completed; the durable project/task binding must match saved Task 4 state.
- The source TIFF, saved boundary and original source photos must be available.
  Current storage preflight still inventories the original JPG/JPEG inputs.
- Existing QGIS output must establish Task 4 ownership. Combined-run output is
  supported: only its task4 subtree is refreshed, not task2 or soft-corners.
- This initial operation supports legacy `manifest.json` publication only. An
  authoritative `publication.json` set is refused without changing it. Do not
  remove that file to bypass the check; complete-set refresh needs separate work.

The code has been validated with fake GIS/services and temporary databases, not
against AH-026125 or real QGIS. Real storage/GIS testing needs operator approval.

## AH-026125: check storage first

From the repository in the pipeline Python environment:

```powershell
python main.py `
  --survey "TLW-001_6.3Ha_M3C_50m_85f75s_4mps" `
  --survey-id "AH-026125" `
  --year 2026 `
  --task4 `
  --resume `
  --run-id "e31764a0-0576-48a3-8955-db09dc0c4e8d" `
  --refresh-qgis task4 `
  --qgis-refresh-confirmation "REFRESH QGIS task4 FOR RUN e31764a0-0576-48a3-8955-db09dc0c4e8d" `
  --storage-preflight-only
```

This reports storage only: no execution lease, GIS, publication, or database
writes. It is not proof that later raster/binding/publication checks will pass.
After reviewing PASS and confirming the installation is idle, repeat the command
without `--storage-preflight-only` to authorize the real refresh. No extra force
flag is necessary. Other forced/recovery/rebinding operations cannot be combined.

Do not add `--both-tasks`, `--task2` or a WebODM force flag. A fresh QGIS render
cannot repair distortions already present in the downloaded source TIFF.

## Outputs and failure recovery

Each attempt has a new `<run-workspace>/qgis-refresh/<UUID>/refresh.json` journal.
Fresh clipping and tiling run there with resume/clean disabled. Source/boundary
SHA-256 values, raster metadata, PNG chunk/CRC/compression checks, and a tile
inventory are recorded or checked before publication. Storage is conservatively
budgeted and rechecked before rendering and large copies; GDAL can still exhaust
space if real outputs exceed estimates, without replacing the previous outputs.

All pending copies are prepared and verified before active paths change. The
survey publication lock and host-exclusive ownership protect replacement. These
are individual same-parent renames, not a filesystem-wide atomic transaction;
avoid consuming outputs during the short activation window.

Previous workspace and published Task 4 clip/tile targets are renamed to unique
`.qgis-refresh-<UUID>.old` siblings and retained. The task4 local staging subtree
is archived the same way so stale size-matched clips/tiles cannot be reused.
Task 2's geometry, TIFFs, tiles, caches and manifest entries are unchanged.
`manifest.json` is merged and installed last. The downloaded Task 4 TIFF is
referenced, never replaced by refresh. No old output is deleted automatically.
Later ordinary resumes also retain workspaces containing refresh evidence.

On an ordinary activation failure, installed replacements move to `.failed`
evidence and previous targets are restored. Copies, partial generations and
journals remain for diagnosis; a retry uses another UUID. Unknown external changes
or a rollback failure are not overwritten: they require manual reconciliation.

Journal states:

- `building` / `preparing`: no active rename has begun; retained incomplete work.
- `publishing`: rename intent recorded; a crash here is uncertain.
- `published`: data/metadata installed, waiting for durable stage completion.
- `completed`: stage output committed and receipt acknowledged.
- `failed` / `rolled_back`: failed work retained, active outputs unchanged/restored.
- `requires_recovery`: rollback/ownership uncertain; preserve every path.

A crash during activation, published-but-uncommitted result, unreadable journal,
or failed completion receipt blocks fresh and ordinary resumes. GIS is never
automatically replayed after a stage-persistence failure. Clearing only the console
reservation is insufficient. Review the exact stage attempt, journal entries,
active/backup inventories and original source hashes under exclusive ownership
before explicitly reconciling; no automatic journal reset/cleanup is provided.
No database migration, status clearing, or remote task adoption is performed.

## Validation commands and scope

Results: 48 new tests pass on both Python 3.10 and the Python 3.13 safe-suite
interpreter; 237 focused and all 1235 default tests pass, with 1235 safely collected.
In-memory compilation and scoped tracked/new-file whitespace checks pass.

```powershell
py -3.13 -m pytest tests/test_qgis_refresh.py tests/test_qgis_refresh_pipeline.py tests/test_qgis_refresh_cli.py -q --tb=short
.\.venv310\Scripts\python.exe -m pytest tests/test_qgis_refresh.py tests/test_qgis_refresh_pipeline.py tests/test_qgis_refresh_cli.py -q --tb=short
py -3.13 -m pytest tests/test_qgis_refresh.py tests/test_qgis_refresh_pipeline.py tests/test_qgis_refresh_cli.py tests/test_qgis_tile_recovery.py tests/test_qgis_tools_observability.py tests/test_rgb_pipeline_single_stage_execution.py tests/test_main_publication_activation_cli.py tests/test_storage_preflight.py tests/test_stage_runner_orchestration.py -q --tb=short
py -3.13 -m pytest -q --tb=short
py -3.13 -m pytest --collect-only -q
git diff --check -- main.py pipelines/rgb_pipeline.py shared/storage_preflight.py docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md
```

New files also receive explicit whitespace/EOF checks because untracked files are
not covered by `git diff --check`. In-memory Python 3.10 compilation covers the
three changed runtime files, two new helpers and three new test modules. No
frontend, schema or dependency changes are involved. Unrelated worktree changes
and deleted BAT files are preserved; nothing is staged, committed or pushed.

The syntax probe was passed to `py -3.10 -B -c` without executing imports:

```python
from pathlib import Path
files = ('main.py', 'pipelines/rgb_pipeline.py', 'shared/storage_preflight.py', 'shared/qgis_refresh.py', 'pipelines/rgb_helpers/qgis_refresh.py', 'tests/test_qgis_refresh.py', 'tests/test_qgis_refresh_pipeline.py', 'tests/test_qgis_refresh_cli.py')
for name in files:
    compile(Path(name).read_text(encoding='utf-8-sig'), name, 'exec')
new_files = ('shared/qgis_refresh.py', 'pipelines/rgb_helpers/qgis_refresh.py', 'tests/test_qgis_refresh.py', 'tests/test_qgis_refresh_pipeline.py', 'tests/test_qgis_refresh_cli.py', 'docs/operators/qgis-fresh-refresh.md')
for name in new_files:
    value = Path(name).read_text(encoding='utf-8-sig')
    assert value.endswith('\n') and not value.endswith('\n\n'), name
    assert all(line == line.rstrip() for line in value.splitlines()), name
print('Python 3.10 compile passed: 8 files; whitespace/EOF passed: 6 new files')
```

Real Windows/network rename behavior, disk capacity, source alignment and GIS
quality remain separate operator checks. Keep retained backups/journals until
the refreshed map/tiles have been verified; cleanup or rollback requires explicit
review and authorization, never broad recursive deletion.
