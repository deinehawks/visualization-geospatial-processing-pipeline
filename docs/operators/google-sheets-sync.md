# Google Sheets monitoring V1

The optional reporter reads `data/pipeline.db` on this PC and updates three
existing tabs: **Pipeline Runs**, **Stage Execution Log**, and **Processing Time
Statistics**. Launch it manually and leave its console open while monitoring.
Closing it never stops the pipeline. Resume keeps the same run row.

## Setup on the operator PC

1. Use Python 3.10 or later (the normal `.venv310` interpreter is supported).
   Install the optional authentication dependency into that interpreter:

   ```bat
   .venv310\Scripts\python.exe -m pip install -r requirements-sheet-sync.txt
   ```

   `google-auth[requests]` supplies Google's service-account signing and token
   refresh implementation. It is isolated from the production requirements;
   pipeline execution never imports the reporter or authenticates with Google.

2. Enable the Google Sheets API in your Google Cloud project. Create/use a
   dedicated service account and share the workbook with its email as Editor.
   Domain-wide delegation is not needed. If organizational sharing restrictions
   prohibit access, resolve them with the administrator before continuing.

3. Keep the service-account JSON outside this repository, e.g.
   `C:\pipeline-secrets\sheets-service-account.json`. Do not paste it into chat,
   commit it, or use the production `.env` file for this reporter.

4. In the same command prompt used to launch the reporter, configure:

   ```bat
   set "PIPELINE_SHEET_ID=1fDC0UBBOsD6XLrtqcm9FoOyczfMrvBk766mIEyhCAy4"
   set "PIPELINE_SHEETS_CREDENTIALS=C:\pipeline-secrets\sheets-service-account.json"
   ```

   Alternatively, save these two variables in Windows User Environment
   Variables, then open a new command prompt (or launch the BAT after signing
   in again). `PIPELINE_SYNC_PYTHON` optionally selects an absolute interpreter
   path. Otherwise the launcher prefers `.venv310\Scripts\python.exe`, then
   `python` from PATH. The launcher sets the working directory to the repository.

5. Use a **copy of the workbook** first: set `PIPELINE_SHEET_ID` to the copy ID.
   Keep the production ID above for the later live switch.

6. Prepare the stage identifier header, then preview:

   ```bat
   start_pipeline_sheet_sync.bat --prepare
   start_pipeline_sheet_sync.bat --dry-run
   start_pipeline_sheet_sync.bat --once
   start_pipeline_sheet_sync.bat
   ```

   `--prepare` only adds `Stage Attempt ID` after existing columns if the stage
   log has no data, then exits. Existing populated logs without IDs require
   manual reconciliation; the reporter does not invent matches. This technical
   column can be hidden but must remain intact.

   `--dry-run` reads Google and SQLite and reports changed-row counts. It does
   not write Sheet values or synchronization state. It creates/acquires the
   local process lock only. `--once` performs one synchronization and exits.
   With no mode flag, the worker polls every 30 seconds until Ctrl+C/window close.

7. Ensure the status dropdowns in all three tabs allow: `Completed`, `Failed`,
   `Running`, `Paused`, `Pending`, `Skipped`, `Cancelled`, `Aborted`,
   `Partially Completed`, and `Requires Recovery`. The reporter preserves
   existing validation/formatting, so any needed dropdown extensions are an
   operator setup step. It stops on Google validation/access errors.

8. Check a new run, a resumed run, and a completed run on the copy. Stop the
   worker, switch to the live workbook ID, and use a different `--state` path
   (e.g. `--state data/sheet-sync/live.json`). State is bound to both the source
   database path and workbook ID and cannot silently switch destinations.

## Ownership and exact mappings

Columns are identified by exact header text, not fixed letters. The six manual
metadata fields **Organization, Flight Date, Data Type, UAV Model, Dataset Name,
and Survey Code** are never sent to Google, even as empty strings. The same is
true of Notes, manual recovery actions, and unsupported metrics. Existing
formula cells are skipped and counted in the console, even in normally owned
columns. A missing source value must never be used to erase manual metadata.

### Pipeline Runs

| Sheet field | Source / meaning |
| --- | --- |
| Pipeline Run ID | `runs.run_id`, stable row key |
| Survey ID | `runs.survey_id`; independent of manual Survey Code |
| Process Started Time (Raw) | Exact persisted `started_at` string |
| Process Finished Time (Raw) | Exact persisted `finished_at` for terminal states |
| Processing Start Time | Persisted start converted to Asia/Manila display |
| Processing End Time | Terminal finish converted to Asia/Manila display |
| Overall Status | Persisted run status mapped to the display labels above |
| Final Stage Reached | Stage name of the newest stage row by `stages.id` |
| Active Processing Time / Total Retries | Not written; not reliably persisted |

On running/paused runs, finish fields are cleared: existing resume code may
retain the preceding invocation's finish timestamp. First-start semantics stay
as persisted. Timestamps with no timezone are retained in raw fields but no
timezone is guessed for the display fields. Blank survey IDs can be populated
when segregation attaches the survey. No organization or dataset is inferred.

### Stage Execution Log

| Sheet field | Source / meaning |
| --- | --- |
| Stage Attempt ID | `<run_id>:<stages.id>`, durable key within this database |
| Pipeline Run ID / Stage Name | Persisted stage row |
| Attempt Number | Ordinal of persisted invocations per run/stage, ordered by ID |
| Stage Start/End Time | Persisted timestamps converted to Asia/Manila |
| Stage Runtime | Persisted `runtime_seconds`, displayed as `1h 2m 3.5s` |
| Status | Actual persisted stage status |
| Error Message | Persisted stage message, bounded to 1,000 characters |
| Retry Count / Error Code / Recovery Action | Not written |

**A stage row is not necessarily an internal retry.** `StageRunner.run()` can
retry its callable several times while updating the same database row. Those
internal retries and their sleep time are included in that row's duration.
Additional resume/forced invocations create separate rows. Error text is sent
as a literal string, never evaluated as a formula. The workbook's sharing
settings should be appropriate for pipeline diagnostic messages.

### Processing Time Statistics

One row per `run_id` (with Survey ID and persisted run Status).

| Sheet field | Source |
| --- | --- |
| Data Preparation | `data_segregation` |
| Cross-run Image Filtering | `cross_run_filter` |
| KML Boundary Setter | `kml_boundary` |
| WebODM Orthomosaic (T4) | `webodm_task4` |
| WebODM 3D (T2) | `webodm_task2` |
| Quality Inspection | `quality_gate` |
| WebODM stage total | Aggregate `webodm` rows only |
| QGIS stage total | Aggregate `qgis` rows only |
| Total Runtime | `runs.total_runtime_seconds` for terminal runs |

Stage buckets sum measured persisted invocations across the run, including
failed/paused invocations. A running invocation leaves its bucket blank until
the measurement is available. Missing measurements do not become zero.
Unmeasured legacy buckets remain untouched. Parent `webodm` timing includes
its child operations: **never add the Task 4/Task 2 columns to the parent
total**. QGIS clipping/tile generation, upload-cache timing, timing variance,
and unclassified/retry time remain untouched until separately persisted.

**Total Runtime is the last persisted invocation's elapsed time, not cumulative
active processing time across resumes.** The reporter exposes the current
database definition rather than changing pipeline timing. It clears this value
while running/paused to avoid showing the old invocation. Timing buckets may
therefore exceed Total Runtime after multiple attempts. Inter-run downtime,
human wait, retry sleep, and remote/local timing are not interchangeable.

## Scope, durability, and limits

- First synchronization includes currently running/paused runs and historical
  runs whose exact run IDs already occur in either summary tab. Unmatched
  terminal history is left alone. Rows with missing historical IDs stay intact.
- After initialization, new IDs and changes to stored run/stage projections
  select the run, including a resume that finishes between polls. Selected runs
  remain selected across restarts. Keep the state file when stopping the BAT.
- On a first-ever launch after a run already finished, an unmatched historical
  run cannot be distinguished from older history: enter its known run ID in a
  summary tab if it should be included. No name/date-based historical guessing.
- Bookkeeping lives in `data/sheet-sync/state.json`, bound to the database path
  and workbook ID. Selection is saved atomically **before** delivery. Deleting
  state resets the historical baseline; corrupt/mismatched state stops safely.
- A process lock at the source database's sibling `sheet-sync/worker.lock`
  prevents two local workers for that database. OS locks release on exit/crash.
  Use one reporting source/worker for this workbook; multi-PC writing is outside V1.
- Google is re-read each pass. The worker writes only changed cells, with each
  record's identity and values in an atomic batch. After response loss or a
  partial sequence of batches it re-reads and reconciles rather than blindly
  appending. Duplicate IDs stop synchronization for manual investigation.
- Use filter views while the worker runs. Stop it before inserting/deleting or
  physically sorting rows/columns. Sheets has no compare-and-swap against human
  edits between a read and write. The next pass reindexes moved rows, but cannot
  guarantee identity during a simultaneous structural edit.
- HTTP 429/5xx, connection timeouts, and SQLite busy/locked errors retry with
  capped exponential backoff and jitter. Access/schema/unknown-status errors
  stop for correction. `--once` returns nonzero on failure. Restart after fixing
  the cause to catch up. The source database is always read-only.
- Status means **last persisted status**, not process liveness. No heartbeat or
  remote WebODM polling is added. Brief transitions can be missed. A locally
  paused run can still have a remotely running WebODM task.
- Existing cancellation/abort encodings are preserved (some cancel paths store
  `paused`; some abort paths store `failed`). The reporter does not reinterpret
  those as canonical new states.
- Google reads use bounded 500-row pages; V1 caps source rows and destination
  dimensions. At the current workbook size there are only a few read requests
  per pass; shorten grids/archive history before scaling up to very large logs.
- Dashboard formulas remain unchanged. Some currently filter by manual Flight
  Date or require Dataset Name, so new rows may not appear in those metrics
  until you fill the manual metadata. Dashboard redesign is a separate task.

## Rollout / rollback / future interface

No production database migration, metadata column, pipeline hook, or manifest
change is needed. Operator metadata input remains deferred. The Google-independent
projection functions can be reused by the future read-only API. Later durable
events can be added when every transition must be retained.

Stop the BAT to disable the integration. The pipeline continues independently.
Keep the sync state for catch-up, and use the workbook copy/version history to
recover any test/live spreadsheet changes. Keep service-account credentials out
of Git. No schema down-migration or artifact recovery is involved.

## Validation

Run `python -m pytest -q tests/test_sheet_sync.py` using the normal safety
fixtures. Tests use temporary SQLite/state paths and fake Google sessions; they
cover manual/formula preservation, historical matching, resume, identity,
unknown timing, duplicate detection, request construction, and lost responses.
No production DB, Google write, WebODM task, or geospatial command is required.

Real service-account access, Windows BAT execution, status validation, and
workbook-copy behavior must be verified on the operator PC during rollout.
