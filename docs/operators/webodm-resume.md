# WebODM Pause, Resume, and Binding Repair

## Pause behavior

`tools/pause_run.py` pauses local pipeline orchestration only. A queued or running WebODM task continues in WebODM. The pipeline records active stage attempts as `paused` and retains the project/task binding.

Do not manually create a replacement project or task. If the remote task completes while the pipeline is paused, resume downloads and validates its orthomosaic.

## Normal Task 4 resume

Use the original run ID:

```powershell
python main.py ... --resume --run-id <run-id> --force-stage webodm_task4
```

`webodm_task4` reconciles the exact persisted project ID and task UUID. It does not upload a replacement. Remote states are handled as follows:

- queued/running: wait on the same UUID;
- completed: export and validate the orthomosaic;
- canceled: stop; restart that same task in WebODM if appropriate, then resume again;
- failed: stop and preserve the binding;
- confirmed missing or conflicting identity: require repair;
- network/authentication/server lookup failure: fail against the same binding without creating a task.

## Repair a historical binding

Dry-run is the default:

```powershell
python tools/repair_webodm_binding.py `
  --run-id <run-id> `
  --operation task4 `
  --project-id <project-id> `
  --task-id <task-uuid>
```

The dry run validates the run, fetches the exact WebODM task, checks its name and status, reports conflicts, and lists the local changes required.

After reviewing the report, apply explicitly:

```powershell
python tools/repair_webodm_binding.py `
  --run-id <run-id> `
  --operation task4 `
  --project-id <project-id> `
  --task-id <task-uuid> `
  --apply
```

`--apply` records the validated binding and audit history and atomically updates the compatibility checkpoint. It does not restart/cancel a task, upload/download data, or delete duplicate projects. Review duplicate projects separately before any manual cleanup.

## Recover a persisted project with no tasks

Use this only when an earlier process persisted a Task 4 project but stopped
before WebODM created any task. The guarded resume requires the original run,
the persisted project ID, `--force-stage webodm_task4`, and the exact
confirmation phrase:

```powershell
python main.py ... `
  --resume `
  --run-id <run-id> `
  --force-stage webodm_task4 `
  --recover-empty-webodm-project task4 `
  --webodm-recovery-project-id <project-id> `
  --webodm-recovery-confirmation "CREATE TASK4 IN EMPTY WEBODM PROJECT <project-id> FOR RUN <run-id>"
```

Immediately before upload, the pipeline lists every task in the exact persisted
project. It creates Task 4 only when WebODM returns a well-formed, complete list
containing zero tasks. Any existing task, identity mismatch, malformed response,
or lookup/authentication/network failure stops without upload. If a task exists,
use the exact UUID repair workflow instead.

Authorization is appended to binding history before upload. The normal Task 4
path then stages imagery, creates one task, and persists its UUID. If task
creation returns ambiguously, do not repeat ordinary creation or delete the
project; inspect WebODM and repair the exact UUID if a task appeared.

## Download the complete Task 4 ODM ZIP

Task 4 honors the same export controls as Task 2:

```ini
EXPORTS_ENABLED=true
EXPORT_ALL_ASSETS_ZIP=true
ALL_ASSETS_OUT_DIR_KEY=odm
ALL_ASSETS_FILENAME_TEMPLATE={survey_id}-RGB-{flag}-all.zip
```

After a successful Task 4 operation, the pipeline downloads WebODM's
`all.zip` into the run workspace first and then mirrors the completed file to
`<survey>/rgb/odm`. The default filename is
`<survey-id>-RGB-<task4-flag>-all.zip`.

Startup preflight reserves one additional source-image-size estimate on both
the workspace and published-output destinations. The Task 4 download performs
another capacity check immediately before writing, and the existing file
mirror checks the actual completed ZIP size before changing the published
file.

The ZIP is an optional export, matching Task 2 compatibility behavior. A
missing endpoint or failed download logs a warning and does not replace an
existing published ZIP. The required orthomosaic remains authoritative for
Task 4 success.

An ordinary resume skips an already completed `webodm_task4` stage. To
backfill the ZIP from a completed Task 4, use the original run ID with
`--resume --force-stage webodm_task4` only after reviewing storage preflight.
The forced operation reconciles the exact persisted task UUID; it must not
create a replacement task.
