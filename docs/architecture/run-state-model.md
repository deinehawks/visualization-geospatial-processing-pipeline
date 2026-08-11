# Run State Model

## Status Legend

- **Implemented and verified:** Present in code and covered by inspected safe tests.
- **Partially implemented:** Present, but incomplete, helper-only, or not wired into normal runtime.
- **Approved target behavior:** Accepted direction, but not necessarily implemented.
- **Proposed and awaiting approval:** Candidate behavior needing design approval.
- **Deferred:** Not part of the initial read-only API/UI slice.
- **Unresolved:** Known ambiguity that must not be inferred.

## Current Durable State

Status: **Implemented and verified** for schema creation and focused persistence behavior.

Current SQLite tables from [shared/db/schema.py](../../shared/db/schema.py):

| Table | Current purpose | Status | Compatibility requirement |
|---|---|---|---|
| `runs` | Parent run record keyed by `run_id`; stores optional `survey_id`, status, timestamps, runtime, pause metadata, source, surveys root, and year | Implemented and verified | Existing rows must remain readable |
| `stages` | Append-only stage records keyed by autoincrement `id`; stores `run_id`, `stage_name`, status, timestamps, runtime, error message, and output JSON | Implemented and verified | Existing resume/reporting behavior must remain compatible |
| `surveys` | Survey-level status and total runtime | Implemented and verified | Survey analytics may keep using it |
| `webodm_tasks` | WebODM task metadata table | Implemented, limited use verified indirectly | Do not treat as the complete external-operation model |
| `schema_migrations` | Applied migration IDs | Implemented and verified | Migrations must remain repeatable |

Current timestamps are generated with timezone-aware UTC ISO strings through `utc_now_iso()`. Current duration values are seconds as floating point numbers.

## Current Status Values

Status: **Implemented and verified** for the listed values.

| Entity | Current status values | Notes |
|---|---|---|
| `runs.status` | `running`, `paused`, `completed`, `failed`, `partially_completed` | WebODM UI cancellation currently marks the run paused with reason `webodm_ui_cancel`; `partially_completed` is used for the approved combined-mode Task 4 success / Task 2 failure case |
| `stages.status` | `running`, `completed`, `failed`, `requires_recovery` | WebODM UI cancellation records the stage as `failed` with error `Canceled in WebODM UI`; `requires_recovery` is implemented for typed post-mutation publication activation failures |
| `surveys.status` | `running`, `completed`, `failed`, `partially_completed` | Survey status is updated after run success/failure when `survey_id` is known |
| Publication manifest status | `staged`, `published` | Helper-level and explicit activation path only |
| Activation journals | helper-specific statuses such as `prepared`, `activated`, `committed`, `rolled_back`, `failed` | Partially implemented in artifact helpers |

`partially_completed` is implemented in runtime persistence for combined
`Orthomosaic + 3D` runs where Task 4 succeeds and Task 2 fails, but broader
operation-level API/UI projection remains incomplete.

## Workspace Cleanup Eligibility

Status: **Approved target behavior**.

Default completed-run workspace cleanup is the target runtime behavior for disk
pressure control. Cleanup is a post-success filesystem action and must not be
treated as proof that publication activation succeeded.

| Run or recovery state | Default workspace behavior | Notes |
|---|---|---|
| `completed` | Cleanup eligible by default | Operators can preserve the workspace with `--keep-workspace` |
| `partially_completed` | Retain | Partial cleanup is deferred until operation-level evidence and retry semantics are stronger |
| `failed` | Retain | Preserve evidence for diagnosis and retry planning |
| `paused` | Retain | Preserve state required for resume |
| Aborted or canceled paths | Retain | Preserve evidence around operator or external cancellation |
| Any path with `requires_recovery` evidence | Retain | Recovery evidence must remain available for diagnosis |

Cleanup must use explicit workspace ownership validation and resolved-path
containment checks before deleting anything. It must never delete published
survey outputs, production roots, WebODM state, pause flags, logs, or recovery
evidence.

## Current Transition Behavior

Status: **Partially implemented** because control-state semantics still rely on flag files and string-backed exceptions.

| Transition | Current implementation | UI/API impact |
|---|---|---|
| New run starts | `RGBPipeline.__init__()` creates or updates a `runs` row as `running`; `StageRunner` also calls `create_run()` | API should tolerate duplicate/idempotent run creation history |
| Stage starts | `StageRunner.start_stage()` inserts a new `running` row | UI should display attempts as rows, not one mutable stage |
| Stage completes | `finish_stage(success=True)` sets `completed`, runtime, and optional output JSON | API can expose completed output as a stage attempt result |
| Stage fails | `finish_stage(success=False)` sets `failed` and `error_message` | Error type is not persisted in SQLite today |
| Resume skip | `get_latest_stage()` prefers the latest completed row over newer failed/running rows | Unresolved for authoritative attempt semantics |
| Pause flag seen | Pipeline records a `run_paused` event and marks run paused in selected paths | Pause is current CLI/control-file behavior, not an API control contract |
| Abort flag seen | Pipeline marks run failed and emits `run_aborted` | Abort remote side effects are unresolved |
| WebODM UI cancel | Stage records failed; run is marked paused with reason `webodm_ui_cancel` | UI must not equate this with a fully approved cancellation model |

## Target Entity Mapping

Status: **Approved target behavior** as a contract model; **not implemented as tables** unless noted.

| Target entity | Current representation | Current status | Implementation dependency |
|---|---|---|---|
| `pipeline_runs` | `runs` | Partially represented | API read projection can map current rows |
| `stage_attempts` | `stages` rows | Partially represented | Needs attempt numbering/authoritative-attempt decision |
| `pipeline_events` | Text logs with `event=<name>` | Partially represented | Durable event table is a future state phase |
| `external_operations` | `webodm_tasks`, log events, stage output JSON | Partially represented | Needs operation identity/idempotency model |
| `dataset_metrics` | Stage output JSON, logs, ad hoc calculations | Proposed and awaiting approval | Metrics collection design |
| `artifacts` | Publication manifest records and stage output JSON | Partially represented | Artifact model and publication integration |
| `quality_gate_decisions` | Interactive prompt return in stage output | Proposed and awaiting approval | Non-interactive gate decision |
| `publication_attempts` | Publication manifests/journals, explicit activation CLI evidence, and `activate_publication` stage attempts | Partially represented | API projection and future recovery/cleanup controls |
| `resource_locks` | `.publication.lock` helper | Partially represented | Broader locks remain ADR-004 pending |
## Publication Activation Stage Contract

Status: **Partially implemented**.

| Rule | Current implementation | Approved target | UI/API impact | Dependency |
|---|---|---|---|---|
| Stage identity | Guarded runtime stage exists when confirmation is supplied or the stage is explicitly selected | One formal stage record with machine name `activate_publication` and label `Activate Publication` | UI displays `Activate Publication` | API/UI projection |
| Trigger | Guarded `RGBPipeline.run()` wiring is implemented only when confirmation is supplied or `activate_publication` is explicitly selected | Target remains activation after quality-gate approval | UI shows activation after quality review | CLI/UI confirmation surface |
| Authorization | `activate_publication_explicit()`, `RGBPipeline.run()`, and production CLI require `PUBLISH <survey_id> <run_id>` through explicit activation flags | CLI keeps exact phrase; future UI/API approval is deferred | UI approval control remains unavailable until implemented | Actor/authorization model |
| Success | Explicit activation can publish manifest/artifacts | Stage completes; existing run-status behavior decides run status | UI shows published artifacts and completed stage | Stage output projection |
| Pre-mutation block | Missing staged manifest, wrong confirmation, or existing lock blocks before visible artifact changes | Record stage as `failed` | UI shows failed activation with no visible artifact mutation | Failure taxonomy |
| Post-mutation failure | Activation journals can retain recovery evidence | Record target stage status `requires_recovery` | UI shows recovery-required publication state | `requires_recovery` status mapping |
| Stage record count | Runtime activation uses one `activate_publication` stage record; staging and activation are inside that boundary | One stage record only; staging and activation are not separate default stage records | UI shows one activation stage | Recovery/cleanup subflow design |
| Stale-lock recovery | Explicit recovery helper/CLI exists separately | Guarded recovery is part of `Activate Publication` stage contract, not a separate default stage | UI may later expose recovery inside publication flow | Recovery authorization/evidence |
| Cleanup | Cleanup planner/executor exists as guarded operator tooling | Guarded cleanup is part of `Activate Publication` stage contract, not a separate default stage | UI may later expose cleanup inside publication flow | Cleanup authorization/evidence |

## WebODM Task Selection Contract

Status: **Partially implemented**.

| Selection rule | Current implementation | Approved target | Compatibility requirement | UI/API impact | Dependency |
|---|---|---|---|---|---|
| No flags | Task 4 only | Task 4 only | Preserve default Task 4 behavior | UI default should show Task 4 | None |
| `--task4` | Task 4 only | Task 4 only | Preserve explicit Task 4 | UI can model this safely | None |
| `--task2` | Task 2 only | Task 2 only | Preserve explicit Task 2 | UI can model this safely | None |
| `--both-tasks` | Implemented in CLI/runtime | Canonical flag for combined `Orthomosaic + 3D` mode | Do not break existing single-task flags | UI may label this mode `Orthomosaic + 3D`; mutation controls remain deferred until API semantics are approved | Per-operation projection hardening |
| `--task4 --task2` | Not accepted by CLI because flags are mutually exclusive | May be accepted as equivalent to `--both-tasks` after deliberate CLI change | Preserve existing `--task4` and `--task2` meanings | UI should prefer `Orthomosaic + 3D`, not raw flag wording | CLI parser change |
| Combined order | Implemented as Task 4 followed by Task 2 | Fixed Task 4 followed by Task 2 | Preserve existing single-task behavior | UI can show deterministic operation order once projection is available | Per-operation projection hardening |
| Shared preprocessing | Some preprocessing is common before the `webodm` stage | Common preprocessing should run once when technically valid | Preserve stage outputs and resume behavior | UI may show one pipeline run with multiple operations | Operation split model |
| Per-task records | One `webodm` stage output may contain multiple task keys; `webodm_tasks` table is limited | Task 4 and Task 2 use separate stage records plus separate operation data | Do not collapse successful operation evidence when another fails | UI needs operation-level rows | Stage split and external operation model |
| Task 4 failure in combined mode | Implemented in current combined flow | Stop combined WebODM immediately; do not run Task 2 | Preserve existing failure recording | UI shows Task 2 as not started due to Task 4 failure | Per-operation projection hardening |
| Task 2 failure after Task 4 success | Runtime status persistence implemented as `partially_completed` | Run status becomes `partially_completed`; Task 4 output remains eligible for publication | Preserve Task 4 evidence and outputs | UI shows partial completion and publishable Task 4 artifacts | API/UI projection hardening |
| QGIS behavior | Operation-aware QGIS stage runs once per successful WebODM operation in combined mode | Run QGIS once per successful WebODM operation in combined mode | Do not rerun successful operation outputs by default | UI shows QGIS outputs per operation | API/UI projection hardening |
| Quality gate | Current full run has one `quality_gate` stage after `webodm`; internal current combined path has an intermediate gate before Task 4 | One quality gate after all selected WebODM tasks complete | Do not expose premature live controls | UI shows one review point for selected operations | Quality gate contract update |

## Unresolved State Decisions

- Attempt authority after forced rerun remains unresolved in ADR-006.
- Database versus logs/checkpoints/filesystem/WebODM source-of-truth remains unresolved in ADR-007.
- Retry idempotency remains unresolved in ADR-008.
- Pause/abort semantics remain unresolved in ADR-009.
- Broader resource locking remains unresolved in ADR-004.
- Quality gate API/UI approval semantics remain unresolved in ADR-012.
- Exact database migration and backward-compatible persistence shape for `partially_completed` remains unresolved until the implementation slice.


