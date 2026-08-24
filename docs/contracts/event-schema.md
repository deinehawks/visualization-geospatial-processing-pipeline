# Event Schema

## Status Legend

- **Implemented and verified:** Present in code and covered by inspected safe tests.
- **Partially implemented:** Present, but incomplete or log-only.
- **Approved target behavior:** Accepted direction, but not necessarily implemented.
- **Proposed and awaiting approval:** Candidate behavior needing approval.
- **Deferred:** Not part of the initial API/UI slice.
- **Unresolved:** Known ambiguity that must not be inferred.

## Current Event Source

Status: **Partially implemented**.

Current events are written to text logs by `log_event()` in [shared/logging.py](../../shared/logging.py). The log record keeps the existing columns:

```text
time | level | logger | run_id | stage | message
```

The parseable message format is:

```text
event=<event_name> key=value key="value with spaces"
```

There is no `pipeline_events` database table yet.

## Current Event Conventions

Status: **Implemented and verified** for the core event helper and covered event families.

- `event` is required inside the log message.
- `run_id` is carried by the log column when a run-owned logger is used.
- `stage_name` is carried by the log column for stage-scoped events.
- `elapsed_seconds` is a numeric duration in seconds, currently emitted as formatted text in logs.
- `error_type` and bounded `error_message` are separate fields where implemented.
- Human-readable banners may remain free-form, but should not be the only source for lifecycle facts.

## Event Catalog

| Event name | Current source | Status | Required/important fields | UI/API impact | Dependency |
|---|---|---|---|---|---|
| `run_started` | `RGBPipeline.run()` log | Implemented and verified | `run_id`, `selected_stages`, `resume` | Run timeline start | None |
| `run_completed` | `RGBPipeline.run()` log | Implemented and verified | `run_id`, `survey_id`, `elapsed_seconds` | Run success summary | None |
| `run_failed` | `RGBPipeline.run()` log | Implemented and verified | `run_id`, `survey_id`, `elapsed_seconds`, `error_type`, `error_message` | Failure display | Error taxonomy |
| `run_paused` | `RGBPipeline.run()` log | Implemented and verified | `reason`, `after_stage` | Show paused state | Control semantics |
| `run_aborted` | `RGBPipeline.run()` log | Implemented and verified | `reason`, `elapsed_seconds` | Show aborted/failed state | Abort semantics |
| `run_canceled` | WebODM UI cancel path | Implemented and verified | `reason`, `after_stage` | Show external cancellation | Cancellation semantics |
| `workspace_cleanup_completed` | `RGBPipeline.run()` post-success cleanup | Implemented and verified | `status`, `file_count`, `total_bytes` | Completed-run storage/audit outcome | Completed run and verified outputs |
| `workspace_cleanup_skipped` | `RGBPipeline.run()` cleanup policy gate | Implemented and verified | `status`, `reason` | Explain retained workspace | Run status, selected stages, or `--keep-workspace` |
| `workspace_cleanup_failed` | `RGBPipeline.run()` post-success cleanup | Implemented and verified | `status`, `error_type`, bounded `error_message` | Warn while preserving completed run status | Cleanup audit and ownership validation |
| `stage_started` | `StageRunner` log | Implemented and verified | stage column | Stage timeline | None |
| `stage_completed` | `StageRunner` log | Implemented and verified | `elapsed_seconds` | Stage success | None |
| `stage_failed` | `StageRunner` log | Implemented and verified | `elapsed_seconds`; error fields may appear in surrounding events/logs | Stage failure | Error taxonomy |
| `stage_retrying` | `StageRunner` log | Implemented and verified | `attempt`, `max_attempts`, `retry_delay_seconds`, `error_type`, `error_message` | Retry timeline | Retry semantics |
| `stage_skipped` | `StageRunner` log | Implemented and verified | `reason` | Resume display | Attempt authority |
| `stage_output_loaded` | `StageRunner` log | Implemented and verified | `output_key` | Resume hydration trace | None |
| `stage_stale` | `StageRunner` log | Implemented and verified | stage column | Recovery warning | Stale attempt semantics |
| `stage_canceled` | WebODM UI cancel in `StageRunner` | Implemented and verified | `reason`, `elapsed_seconds` | External cancel timeline | Cancellation semantics |
| `webodm_project_created` | Task 2 create path | Partially implemented | `project_id` | External operation timeline | Remaining WebODM branches |
| `webodm_task_created` | Task 2 create path | Partially implemented | `project_id`, `task_key`, `task_id`, `task_name` | WebODM operation row | Operation model |
| `webodm_task_status` | Task 2 wait path | Partially implemented | `project_id`, `task_key`, `task_id`, `status`, `success`, `elapsed_seconds` | Operation status | Remaining WebODM branches |
| `qgis_command_started` | QGIS tool boundary | Partially implemented | `tool`, executable basename | Command timeline | QGIS branch coverage |
| `qgis_command_completed` | QGIS tool boundary | Partially implemented | `tool`, executable basename, `elapsed_seconds`, `returncode` | Performance and audit | None |
| `qgis_command_failed` | QGIS tool boundary | Partially implemented | `tool`, executable basename, `elapsed_seconds`, `returncode`, `error_type`, `error_message` | Failure display | Error taxonomy |

Stage lifecycle events now use `webodm_task4` and `webodm_task2` as distinct
stage names. Dedicated operation-level event IDs and complete WebODM branch
instrumentation remain deferred.

## WebODM Operation Event Collection

Status: **Implemented and verified** for Task 4 and Task 2 using the current
text-log event sink.

- `webodm_task_created` is emitted immediately after each Task 4 or Task 2
  external task creation with `project_id`, `task_key`, `task_id`, and
  `task_name`.
- `webodm_task_status` is emitted whenever the pipeline observes a terminal
  Task 4 or Task 2 outcome with `project_id`, `task_key`, `task_id`, `status`,
  and `success`.
- `elapsed_seconds` is the WebODM-reported processing duration. It is omitted
  when a reused task has no trustworthy duration; zero must not represent an
  unknown duration.
- Failures after task creation include the exception class as `error_type` and
  an `error_message` bounded to 240 characters before the original exception
  continues through existing stage failure handling.
- Resume does not emit another terminal operation event when StageRunner skips
  an already-completed operation. Re-observation of the same external task may
  produce another status event, but metric consumers deduplicate by
  `(project_id, task_id)`.
- A replacement or retry that creates a new `task_id` is a distinct external
  operation.

This collection contract does not add durable event storage, event IDs,
idempotency keys, API aggregation, or UI mutation behavior.

## Target Durable Event Schema

Status: **Approved target behavior**; **not implemented**.

```json
{
  "event_id": "evt-001",
  "run_id": "run-001",
  "survey_id": "AH-026002",
  "stage_name": "webodm",
  "attempt_id": "stage-123",
  "external_operation_id": "webodm-task4-run-001",
  "event_name": "webodm_task_status",
  "severity": "INFO",
  "occurred_at": "2026-07-30T11:45:00+00:00",
  "fields": {
    "task_key": "task4",
    "status": "completed",
    "elapsed_seconds": 3600.0
  },
  "message": "bounded human-readable message"
}
```

Compatibility requirement:

- Future durable events should preserve the current event names where practical.
- API projections may parse current logs, but must label log-derived data as `source: "text_log"`.
- UI must tolerate missing events for branches that are not yet instrumented.

## Unresolved

- Exact durable event storage location and migration strategy.
- Complete WebODM branch instrumentation.
- Operation-level event IDs and idempotency keys.
- Bounded error-code taxonomy.
- Whether pause/abort/cancel become typed events backed by typed exceptions.
