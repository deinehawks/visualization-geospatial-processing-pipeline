# Storage preflight and durable workspace routing

Status: implemented in `feature/storage-preflight`; pending operator rollout.

## Objective

Prevent a run from starting when its state, cache, workspace, output, or temporary
storage cannot satisfy an estimated write requirement plus an operator reserve.
Keep SQLite state separate from bulky, recoverable cache and workspace data.

## Approved configuration

```ini
UPLOAD_CACHE_ROOT=D:/pipeline-data/cache
QGIS_LOCAL_STAGING_DIR=D:/pipeline-data/qgis-staging
WORKSPACE_ROOT=D:/pipeline-data/workspaces
STORAGE_MIN_FREE_GB=10
STORAGE_MIN_FREE_PERCENT=10
```

The required reserve for each involved volume is the larger of 10 GiB or 10% of
that volume. Requirements that share a volume are summed before the reserve is
applied. These values are configurable; the defaults remain 10 and 10.

## Startup behavior

1. Resolve a fresh run ID, or read an existing resume record through SQLite
   read-only mode.
2. Restore a resumed run's persisted workspace root. Legacy runs with no stored
   value remain pinned to `<base>/data/workspaces` for compatibility.
3. Count source JPG/JPEG files and sum their exact byte sizes.
4. Estimate upload cache writes at 1.2 times source bytes.
5. Estimate QGIS local staging writes at 2 times source bytes.
6. Estimate workspace writes at the larger of 20 GiB or the mode multiplier:
   Task 4 = 2x, Task 2 = 3x, both = 4x.
7. Group database, logs, checkpoints, upload cache, QGIS staging, workspace, survey outputs,
   and system temporary storage by physical volume.
8. Print one per-volume PASS/FAIL report and exit with code 3 before pipeline
   construction if any volume fails.

`--storage-preflight-only` prints the same report and exits without constructing
or running `RGBPipeline`.

## Runtime rechecks

Capacity is checked again immediately before upload-cache writes, QGIS local
staging, QGIS tile copy-back, and workspace-to-published artifact mirrors.
Capacity failures are typed and are never converted into direct-I/O fallbacks.
SQLite full-disk errors that escape the pipeline are reported with the database
path and exit code 4.

## Persistence and compatibility

Migration `003_run_workspace_root` adds nullable `runs.workspace_root`. Fresh
runs persist their selected root. An existing non-null value is not rebound by a
later configuration change. The additive migration is repeatable, and legacy
null rows retain their original workspace location.

## Safe validation and rollout

- Unit and regression tests use pytest-owned files and temporary SQLite only.
- WebODM, QGIS, GDAL, production databases, network shares, and the real pipeline
  are not used during automated validation.
- Do not change the production `.env` while active runs exist.
- After those runs finish, create the D: cache, QGIS staging, and workspace
  directories, update the operator `.env`,
  and run `python main.py --survey <dataset> --storage-preflight-only` before the
  first controlled run.
- Keep the state database, logs, and checkpoints on their current state volume;
  free enough space there to satisfy the same reserve before rollout.
