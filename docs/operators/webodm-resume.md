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

## Recover a persisted project that was deleted

This is different from empty-project recovery. Use it only when the exact
persisted Task 4 project returns an authenticated WebODM `404`, the binding has
no Task 4 UUID, and no other operation has a durable task UUID.

Start with a local storage/authorization plan. It does not contact WebODM,
create a project, upload images, or update the database:

```powershell
python main.py ... `
  --resume `
  --run-id <run-id> `
  --force-stage webodm_task4 `
  --recover-missing-webodm-project task4 `
  --missing-webodm-project-id <missing-project-id> `
  --missing-webodm-recovery-confirmation "CREATE REPLACEMENT WEBODM PROJECT FOR MISSING PROJECT <missing-project-id> FOR RUN <run-id>" `
  --storage-preflight-only
```

After reviewing the plan, remove `--storage-preflight-only` to execute. The
pipeline authenticates with WebODM and fetches the exact old project first. It
creates one replacement only for a confirmed `404`. An existing project,
authentication/permission failure, timeout, connection failure, server error,
malformed response, conflicting local identity, existing Task 4 UUID, or
durable Task 1/2 UUID stops before project creation.

Before the non-idempotent WebODM create call, the pipeline atomically writes
`webodm_project_recovery_<run-id>.json` in the checkpoint directory. After a
successful response it records the returned project ID in that receipt, then
appends replacement bindings and audit evidence without deleting the old
binding history. Project-only legacy Task 1/2 placeholders are rebound to keep
future resume identity consistent; they never authorize Task 1/2 creation.

If the process stops after receiving the new project ID but before the binding
is durable, repeat the same command. It reuses the receipt only after the exact
replacement project exists, has the expected name, and contains zero tasks. If
creation returns ambiguously, the receipt is marked `creation_ambiguous` and
automatic retries are blocked. Inspect WebODM and the receipt before any
further recovery; never delete a possible replacement or retry project
creation blindly.

After the replacement binding is durable, Task 4 follows the normal guarded
creation path and persists its UUID immediately. Ordinary resume behavior is
unchanged when the missing-project recovery flags are absent. Empty-project
and missing-project recovery flags are mutually exclusive.

## Recover Task 2 when Task 4 already exists

Use this only for a combined run where Task 4 is durably bound and completed,
but the process stopped before Task 2 was created. Start with a plan-only
command:

```powershell
python main.py ... `
  --both-tasks `
  --resume `
  --run-id <run-id> `
  --force-stage webodm_task2 `
  --recover-missing-webodm-task task2 `
  --webodm-task-recovery-project-id <project-id> `
  --webodm-task-recovery-confirmation "CREATE TASK2 IN WEBODM PROJECT <project-id> FOR RUN <run-id>" `
  --storage-preflight-only
```

The plan does not contact WebODM, upload imagery, create a task, or change the
database. After reviewing it, remove `--storage-preflight-only` to execute.

Immediately before upload, the pipeline verifies the exact persisted project
and lists its complete task set. Recovery proceeds only when that set contains
exactly one well-formed task: the same Task 4 UUID and name already bound to
the run, with both local and remote Task 4 status completed. An extra task,
missing Task 4, identity mismatch, non-completed status, malformed response,
or lookup/authentication/network failure stops before upload. The explicit
authorization is appended to binding history before the ordinary Task 2
creation path runs, and the new Task 2 UUID is persisted immediately after a
successful create response. Task 4 is not restarted or replaced.

If task creation returns ambiguously, do not repeat the creation command. Check
the exact WebODM project. If Task 2 exists, use
`tools/repair_webodm_binding.py --operation task2` with its exact UUID; dry-run
is the default. If no Task 2 exists, preserve the failure evidence and diagnose
the create failure before attempting another explicitly authorized recovery.

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
