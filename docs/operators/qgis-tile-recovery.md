# Guarded QGIS Tile Recovery

Use this workflow only when a resumed QGIS stage has failed because an existing
local PNG tile is unreadable. Ordinary resumes should continue without these
flags.

## Safety model

The recovery is disabled by default. It requires resume mode, the original run
ID, a failed or incomplete QGIS stage, local QGIS staging, a supported task key,
and an exact run/task-bound confirmation.

Immediately before tiling, the pipeline resolves the active directory to this
exact shape beneath `QGIS_LOCAL_STAGING_DIR`:

```text
tiles/<run-id>/<task-key>/tiles/<tile-mode>
```

It rejects path mismatches and symlinks. The existing tree is inventoried and
moved on the same volume to:

```text
quarantine/<run-id>/<task-key>/<UTC timestamp>/<tile-mode>
```

The quarantine parent contains `qgis-tile-rebuild.json` with the authorization,
paths, file count, byte count, run, task, mode, and timestamp. Nothing in
quarantine is deleted automatically. The active tile target is then regenerated
from empty while the already verified clipped orthomosaic is reused.

Quarantine temporarily increases storage use because the old and replacement
tile sets coexist. Confirm D: capacity before the real recovery.

## BCO-102 report-only check

This validates the request and storage policy but does not move tiles or start
QGIS/GDAL:

```powershell
python main.py `
  --survey 'BCO-102_32Ha_M3M_70m_85f75s_5mps' `
  --year 2026 `
  --resume `
  --run-id 47cd8922-630c-4a22-90d0-8494669ecb80 `
  --rebuild-qgis-tiles task4 `
  --qgis-tile-rebuild-confirmation 'REBUILD QGIS TILES task4 FOR RUN 47cd8922-630c-4a22-90d0-8494669ecb80' `
  --storage-preflight-only
```

## BCO-102 guarded recovery

After the report-only check passes and its capacity is acceptable, run:

```powershell
python main.py `
  --survey 'BCO-102_32Ha_M3M_70m_85f75s_5mps' `
  --year 2026 `
  --resume `
  --run-id 47cd8922-630c-4a22-90d0-8494669ecb80 `
  --rebuild-qgis-tiles task4 `
  --qgis-tile-rebuild-confirmation 'REBUILD QGIS TILES task4 FOR RUN 47cd8922-630c-4a22-90d0-8494669ecb80'
```

Expected log evidence includes `event=qgis_tile_rebuild_quarantined` followed by
tile generation with no existing active tiles. If fresh tiling fails, the stage
records the quarantine metadata and does not repeat the quarantine automatically.

## Recovery and cleanup

Do not delete quarantine evidence merely because the new command started. First
confirm the QGIS stage completed, inspect the regenerated tiles, and verify the
pipeline copied and published the expected outputs. Retention or deletion of the
quarantined tree requires a separate, explicit operator decision.

If metadata persistence fails, the implementation attempts to move the original
tree back to its active path and fails closed. If it reports that rollback also
failed, stop and use the reported quarantine path for manual reconciliation; do
not rerun the guarded command until ownership is understood.
