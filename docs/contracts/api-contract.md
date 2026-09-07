# API Contract

## Status Legend

- **Implemented and verified:** Endpoint or schema exists in code and is covered by inspected tests.
- **Partially implemented:** Backing data exists, but endpoint/schema is not implemented.
- **Approved target behavior:** Accepted contract direction.
- **Proposed and awaiting approval:** Candidate API behavior needing approval.
- **Deferred:** Intentionally out of initial API scope.
- **Unresolved:** Known ambiguity that the API must not guess.

## Scope

Status: **Approved target behavior**.

No FastAPI application or endpoint is implemented in this repository at the time of inspection. All endpoints below are proposed read-only contracts unless later implementation verifies them.

The initial API must not start, pause, resume, abort, retry, rerun, publish, recover, clean up, upload, download, execute QGIS/GDAL, or mutate SQLite/filesystem/WebODM state.

## Conventions

Status: **Approved target behavior**.

- Timestamps use UTC ISO-8601 strings.
- Durations use seconds as numbers.
- Storage values use bytes as integers.
- API values are machine-readable; UI formatting belongs in the UI.
- Error categories/codes are separate from bounded human-readable messages.
- List endpoints use stable pagination fields.
- Unknown or unavailable data is returned as `null`, an empty list, or an explicit `availability` value; do not invent defaults.

## Initial Read-Only Endpoints

| Endpoint | Status | Purpose | Current backing data | Dependency |
|---|---|---|---|---|
| `GET /runs` | Proposed and awaiting approval | List pipeline runs | `runs`, derived stage summaries | FastAPI read layer |
| `GET /runs/{run_id}` | Proposed and awaiting approval | Run detail | `runs`, latest/attempt stage state, artifacts/events projections | FastAPI read layer |
| `GET /runs/{run_id}/stages` | Proposed and awaiting approval | Stage attempts | `stages` | Attempt ordering contract |
| `GET /runs/{run_id}/events` | Proposed and awaiting approval | Pipeline events | Text logs today; future `pipeline_events` | Event projection/parser |
| `GET /runs/{run_id}/artifacts` | Proposed and awaiting approval | Artifacts and publication evidence | Stage output JSON, publication manifests | Artifact projection |
| `GET /statistics/overview` | Proposed and awaiting approval | Dashboard summary | Aggregated runs/stages/logs | Metrics definitions |
| `GET /statistics/performance` | Proposed and awaiting approval | Runtime and throughput | Run/stage runtimes, events | Metrics collection |
| `GET /statistics/storage` | Proposed and awaiting approval | Storage use | Publication manifests, workspace scans, future metrics | Metrics collection |
| `GET /statistics/reliability` | Proposed and awaiting approval | Failures, retries, recovery | Stages, logs, publication evidence | Error taxonomy |
| `GET /system/resources` | Proposed and awaiting approval | Read-only system resource view | Preflight/resource probes, future collectors | Safe probe design |

## Deferred Operational Endpoints

Status: **Deferred**.

The following endpoint families must not be implemented or exposed until their semantics are approved and the pipeline supports safe, idempotent behavior:

- Start a run.
- Pause, resume, or abort a run.
- Retry a stage or external operation.
- Force rerun a stage or run.
- Approve, reject, or restart from quality review.
- Stage, activate, recover, publish, or clean artifacts.
- Modify settings that affect production paths, credentials, WebODM, QGIS/GDAL, or storage.

## Example Schemas

Status: **Proposed and awaiting approval** unless noted.

### Pipeline Run

```json
{
  "run_id": "run-001",
  "survey_id": "AH-026002",
  "status": "completed",
  "status_source": "runs.status",
  "source_dir": "D:/field-data/20260414/client/site",
  "surveys_root": "Z:/surveys",
  "year": 2026,
  "started_at": "2026-07-30T10:00:00+00:00",
  "finished_at": "2026-07-30T12:30:00+00:00",
  "total_runtime_seconds": 9000.0,
  "selected_webodm_operations": ["task4"],
  "implementation_status": "partially_implemented"
}
```

### Stage Attempt

```json
{
  "stage_attempt_id": "stage-123",
  "current_table": "stages",
  "current_row_id": 123,
  "run_id": "run-001",
  "stage_name": "webodm",
  "attempt_number": null,
  "status": "completed",
  "started_at": "2026-07-30T10:30:00+00:00",
  "finished_at": "2026-07-30T11:45:00+00:00",
  "runtime_seconds": 4500.0,
  "error_code": null,
  "error_message": null,
  "output_available": true,
  "authoritative_attempt": null,
  "implementation_status": "partially_implemented"
}
```

### WebODM External Operation

```json
{
  "operation_id": "webodm-task4-run-001",
  "run_id": "run-001",
  "survey_id": "AH-026002",
  "operation_type": "webodm_task",
  "task_key": "task4",
  "project_id": 100,
  "task_id": "task-0001",
  "task_name": "orthomosaic--site-T4",
  "remote_status": "completed",
  "raw_remote_status": "40",
  "local_status": "artifact_ready",
  "success": true,
  "options_snapshot": {},
  "runtime_seconds": 3600.0,
  "metrics_available": false,
  "implementation_status": "partially_implemented"
}
```

### Pipeline Event

```json
{
  "event_id": "log-run-001-000001",
  "run_id": "run-001",
  "stage_name": "webodm",
  "event_name": "stage_completed",
  "severity": "INFO",
  "occurred_at": "2026-07-30T11:45:00+00:00",
  "fields": {
    "elapsed_seconds": 4500.0
  },
  "source": "text_log",
  "implementation_status": "partially_implemented"
}
```

### Dataset Metrics

```json
{
  "run_id": "run-001",
  "survey_id": "AH-026002",
  "images_discovered_count": 1200,
  "images_accepted_count": 1180,
  "images_removed_count": 20,
  "input_storage_bytes": 987654321,
  "boundary_area_square_meters": null,
  "collection_status": "partial",
  "implementation_status": "proposed"
}
```

### Artifact

```json
{
  "artifact_id": "run-001:qgis.tiles_dir",
  "run_id": "run-001",
  "survey_id": "AH-026002",
  "logical_name": "qgis.published.tiles_dir",
  "kind": "directory",
  "status": "published",
  "workspace_path": "data/workspaces/run-001/qgis/tiles/round-corners",
  "published_relative_path": "tiles/ortho/round-corners",
  "published_path": "Z:/surveys/2026/AH-026002/rgb/tiles/ortho/round-corners",
  "size_bytes": 123456789,
  "file_count": 1000,
  "implementation_status": "partially_implemented"
}
```

### Paginated List Response

```json
{
  "items": [],
  "page_size": 50,
  "next_cursor": null,
  "total_count": null
}
```

### Structured API Error

```json
{
  "error": {
    "code": "RUN_NOT_FOUND",
    "category": "not_found",
    "message": "Run was not found.",
    "details": {
      "run_id": "run-001"
    },
    "retryable": false
  }
}
```

## WebODM Selection API Contract

Status: **Partially implemented; read-only API projection remains deferred**.

- Current production CLI supports default Task 4, explicit Task 4, explicit Task 2, and canonical `--both-tasks` combined mode.
- Current production CLI does not accept `--task4 --task2`.
- The approved combined mode uses canonical flag `--both-tasks` and operator-facing label `Orthomosaic + 3D`.
- `--both-tasks` is semantically equivalent to selecting Task 4 and Task 2 together; `--task4 --task2` may be accepted as an equivalent spelling only after a deliberate CLI change.
- Pipeline execution and separate operation-stage persistence are implemented; API/UI mutation controls remain deferred.
- The target combined operation is one pipeline run containing two separate WebODM operations.
- Combined execution order is fixed: Task 4 first, then Task 2.
- If Task 4 fails, the combined WebODM stage stops immediately and Task 2 does not run.
- If Task 4 succeeds and Task 2 fails, the run-level target status is `partially_completed`.
- Task 4 output is immediately eligible for publication after a successful Task 4 operation.
- QGIS runs once per successful WebODM operation.
- The quality gate runs once after all selected WebODM tasks complete.
- Each selected operation now has a separate stage record with task identity, status, runtime-bearing task data, workspace/output mappings, and failure evidence. Dedicated operation IDs, complete option snapshots/metrics, and API projection remain future work.
- A successful operation must remain visible and untouched by default when retrying a failed operation.
- Task 1, Task 2, and Task 4 persistence now use canonical run/operation/project/task bindings. Task 4 operator repair is append-preserving. A read projection should expose canonical binding identity separately from historical rows.
- Task 4 pause is local detach: remote status and local attempt/artifact status are independent fields. A remotely running or completed task can coexist with a locally paused attempt.
- Normal WebODM resume reuses each exact canonical task UUID; `--force-stage webodm_task4` specifically reconciles Task 4. Missing/conflicting bindings project as recovery-required evidence; they must not be represented as permission to create a replacement.
- The CLI-only Task 4 empty-project recovery is implemented as an explicit
  exception for a persisted project that WebODM verifies contains zero tasks.
  It requires the exact run/project confirmation and emits durable
  authorization evidence before normal task creation. It is not an API/UI
  mutation contract; API/UI recovery controls remain deferred.
- A nonempty or indeterminate project never authorizes creation. Existing tasks
  continue through exact-UUID repair, not name-based adoption or replacement.
## Publication Activation API Contract

Status: **Partially implemented with deferred operational endpoints**.

- Initial API remains read-only and must not activate publication, recover locks, or execute cleanup.
- Runtime activation uses one formal pipeline stage with machine name `activate_publication` and operator-facing label `Activate Publication` when explicit confirmation is supplied.
- The production CLI exposes this as `--activate-publication --publication-confirmation "PUBLISH <survey_id> <run_id>"`; missing either flag fails before pipeline construction.
- CLI/runtime authorization uses exact phrase `PUBLISH <survey_id> <run_id>` for now.
- UI/API approval is a future control contract and must not be exposed as live until actor, authorization, idempotency, and recovery semantics are implemented.
- Missing staged manifest, missing authorization, or existing publication lock before visible artifact mutation maps to `failed`.
- Failure after visible artifact mutation maps to target status `requires_recovery`.
- Successful activation marks the activation stage completed; existing run-status behavior decides final run status.
- Stale-lock recovery and cleanup belong to the `Activate Publication` flow rather than separate default stages, but they remain guarded subflows requiring explicit authorization/evidence before mutation.
