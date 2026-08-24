# Metrics Dictionary

## Status Legend

- **Implemented and verified:** Metric is collected in code and covered by inspected tests.
- **Partially implemented:** Data exists in some rows, logs, manifests, or stage outputs, but collection is incomplete.
- **Approved target behavior:** Metric belongs in the contract, but collection may not exist yet.
- **Proposed and awaiting approval:** Candidate metric needing approval.
- **Deferred:** Not part of the initial read-only API/UI slice.
- **Unresolved:** Definition, source, or aggregation is ambiguous.

## Conventions

Status: **Approved target behavior**.

- Storage values use bytes as integers.
- Durations use seconds as numbers.
- Timestamps use UTC ISO-8601 strings.
- Counts are integers.
- Rates are derived by the API or UI and must state numerator, denominator, and time window.
- API values remain machine-readable; UI formatting is done by the UI.
- Null means unavailable or not collected, not zero.

## Metrics

| Metric name | Description | Source entity/event | Unit | Collection point | Aggregation rule | Filters/dimensions | Null/unavailable behavior | Current status |
|---|---|---|---|---|---|---|---|---|
| `pipeline_runs.count` | Number of runs | `runs` | count | Run query | Count rows | status, year, survey_id | 0 when no rows | Partially implemented |
| `pipeline_runs.completed_count` | Completed runs | `runs.status` | count | Run query | Count `completed` | year, survey_id | 0 | Partially implemented |
| `pipeline_runs.failed_count` | Failed runs | `runs.status` | count | Run query | Count `failed` | year, survey_id | 0 | Partially implemented |
| `pipeline_runs.paused_count` | Paused runs | `runs.status` | count | Run query | Count `paused` | year, survey_id | 0 | Partially implemented |
| `pipeline_runs.total_runtime_seconds` | End-to-end runtime | `runs.total_runtime_seconds` | seconds | Run finish | Sum, avg, min, max, p50/p95 | status, year, survey_id | Null until finished | Implemented and verified |
| `dataset.images_discovered_count` | Source images discovered | Stage output/logs | count | Data segregation/cross-run filter | Sum per run; do not double count retries unless attempt-scoped | run_id, survey_id | Null when not collected | Partially implemented |
| `dataset.images_accepted_count` | Images accepted for processing | Cross-run filter output | count | Cross-run filter completion | Latest authoritative attempt or attempt-level count | run_id, filter mode | Null when not collected | Partially implemented |
| `dataset.images_removed_count` | Images excluded/removed from processing | Cross-run filter output | count | Cross-run filter completion | Latest authoritative attempt or attempt-level count | run_id, reason | Null when not collected | Partially implemented |
| `dataset.images_processed_count` | Images uploaded or processed by WebODM | WebODM operation | count | Upload/task creation | Per operation, then sum by run | task_key, run_id | Null when not collected | Proposed and awaiting approval |
| `dataset.boundary_area_square_meters` | Survey boundary area | Boundary/geometry stage | square meters | Boundary processing | One value per run/survey | survey_id | Null when unavailable | Proposed and awaiting approval |
| `stage_attempts.count` | Stage attempt rows | `stages` | count | Stage query | Count rows | stage_name, status, run_id | 0 | Implemented and verified |
| `stage_attempts.runtime_seconds` | Stage attempt runtime | `stages.runtime_seconds` | seconds | Stage finish | Sum/avg/min/max/p95 by stage | stage_name, status | Null until finished | Implemented and verified |
| `stage_attempts.retry_count` | Retry events before terminal outcome | `stage_retrying` events | count | Stage retry log | Count retry events | stage_name, error_type | 0 if no retry events; null if logs unavailable | Partially implemented |
| `queue.wait_seconds` | Time waiting before execution | Future scheduler | seconds | Queue dequeue | Avg/p95/max | worker, priority | Null before queue exists | Deferred |
| `external_operations.runtime_seconds` | WebODM or other external operation runtime | terminal operation events and stage output | seconds | Operation completion | Per distinct `(project_id, task_id)`; aggregate by run | operation_type, task_key, status | Null when the external service does not report a trustworthy duration | Partially implemented; Task 4/Task 2 collection verified |
| `external_operations.success_count` | Successful external operations | terminal operation events | count | Operation completion | Count distinct successful `(project_id, task_id)` outcomes | task_key, operation_type | 0 | Partially implemented; Task 4/Task 2 collection verified |
| `external_operations.failed_count` | Failed external operations | terminal operation events | count | Operation failure | Count distinct failed `(project_id, task_id)` outcomes | task_key, bounded error_type | 0 | Partially implemented; Task 4/Task 2 collection verified |
| `storage.input_bytes` | Source dataset size | Future dataset scan/preflight | bytes | Admission/preflight | Sum by run/survey | source type | Null when not scanned | Proposed and awaiting approval |
| `storage.workspace_bytes` | Run workspace storage | Workspace scan or manifest | bytes | Stage completion/summary | Latest scan per run | stage, artifact kind | Null when not scanned | Proposed and awaiting approval |
| `storage.output_bytes` | Generated output size before publication | Stage output/artifacts | bytes | Stage completion | Sum artifact sizes | artifact kind, stage | Null when not collected | Partially implemented |
| `storage.diagnostic_bytes` | Retained diagnostic workspace/evidence size | Cleanup planner/future scans | bytes | Cleanup planning | Sum candidates/evidence | run_id, reason | Null when not scanned | Proposed and awaiting approval |
| `storage.published_bytes` | Active published artifact size | `publication.json` artifacts | bytes | Publication activation | Sum active artifacts | artifact kind, survey_id | Null without manifest | Partially implemented |
| `webodm.task4.runtime_seconds` | WebODM-reported Task 4 processing runtime | terminal operation event/stage output | seconds | Task 4 completion | Per distinct `(project_id, task_id)`; avg/p95 by period | status | Null when not run or when a reused task has no trustworthy duration | Implemented and verified |
| `webodm.task2.runtime_seconds` | WebODM-reported Task 2 processing runtime | terminal operation event/stage output | seconds | Task 2 completion | Per distinct `(project_id, task_id)`; avg/p95 by period | status | Null when not run or when a reused task has no trustworthy duration | Implemented and verified |
| `webodm.task4.output_bytes` | Task 4 artifacts size | Artifact records | bytes | Export/publication | Sum artifacts | artifact kind | Null when not collected | Partially implemented |
| `webodm.task2.output_bytes` | Task 2 artifacts size | Artifact records | bytes | Export/publication | Sum artifacts | artifact kind | Null when not collected | Partially implemented |
| `artifacts.count` | Artifact records | Publication manifest/stage output | count | Stage/publication | Count artifacts | kind, status, logical_name | 0 | Partially implemented |
| `artifacts.size_bytes` | Artifact size | Publication manifest/stage output | bytes | Stage/publication | Sum by run/survey/status | kind, logical_name | Null when unknown | Partially implemented |
| `errors.count` | Error count | stages/events/API errors | count | Failure event/row | Count by category | error_type, stage_name | 0 | Partially implemented |
| `errors.retryable_count` | Retryable error count | Future error taxonomy | count | Error classification | Count by retryable flag | stage, operation | Null before taxonomy | Proposed and awaiting approval |
| `retries.count` | Retry attempts | `stage_retrying` events | count | Retry event | Count events | stage, error_type | 0 or null if logs unavailable | Partially implemented |
| `retries.succeeded_count` | Retries that later succeeded | Stage retry events plus terminal stage status | count | Stage terminal state | Count attempts with retry events and completed terminal status | stage | Null if events unavailable | Proposed and awaiting approval |
| `quality.decisions_count` | Quality gate decisions | Future `quality_gate_decisions`; current stage output | count | Quality gate | Count by decision | actor, decision | Null until persisted | Proposed and awaiting approval |
| `quality.pending_count` | Pending quality decisions | Future gate state | count | Gate creation | Count pending | survey, age | Null before non-interactive gate | Deferred |
| `publication.attempts_count` | Publication attempts | Publication journals/manifests/future table | count | Publication staging/activation | Count attempts | status, run_id | Null when no evidence scan | Partially implemented |
| `publication.success_count` | Successful publication activations | `publication.json`/journals | count | Activation commit | Count committed/published | survey_id | 0 or null without manifest | Partially implemented |
| `publication.failed_count` | Failed publication attempts | Journals/audits | count | Activation failure | Count failed evidence | error_type | Null when not scanned | Partially implemented |
| `resources.free_storage_bytes` | Available storage at relevant roots | Future resource collector/preflight | bytes | Read-only probe | Latest sample | root kind | Null if unavailable | Proposed and awaiting approval |
| `resources.cpu_percent` | CPU use | Future resource collector | percent | Sample interval | Avg/latest | host, worker | Null before collector | Deferred |
| `dashboard.active_runs_count` | Runs not terminal | `runs.status` | count | Dashboard query | Count `running` and `paused` | year | 0 | Partially implemented |
| `dashboard.reliability_rate` | Successful terminal runs / terminal runs | `runs.status` | ratio | Dashboard query | completed / (completed + failed) | time window | Null when denominator is 0 | Proposed and awaiting approval |

## WebODM Operation Collection Semantics

Status: **Implemented and verified** for Task 4 and Task 2 text-log evidence.

- The existing `webodm_task_status` event is the metric collection boundary.
- `elapsed_seconds` is WebODM-reported processing time, not full pipeline stage
  wall time. The field is omitted when no trustworthy duration is available.
- Runtime, success, and failure aggregation deduplicates terminal observations
  by `(project_id, task_id)`. Resume may observe the same task again without
  creating another logical operation count.
- A replacement or retry with a new `task_id` is a distinct external operation.
- `task_key` distinguishes Task 4 and Task 2. `error_type` may be used as a
  bounded failure dimension; free-form error messages are never metric labels.
- This slice collects metric evidence only. Durable storage and API/UI
  aggregation remain deferred.

## Temporary Monitoring Spreadsheet

Status: **Deferred** as UI backend.

Any temporary spreadsheet or ad hoc monitoring file may inform requirements, but it must not become the UI backend or API source of truth.

## Implementation Notes

- Existing runtime fields use seconds.
- Existing artifact helpers use `size_bytes` and `total_bytes`.
- Future API aggregation must state whether it uses all attempts, latest completed attempts, or a future authoritative attempt pointer.
- Metrics derived from logs must label log availability and parser limitations.
