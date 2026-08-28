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
