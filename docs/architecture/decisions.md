# Cross-Workstream Decisions

## Status Legend

- **Implemented and verified:** Present in code and covered by inspected safe tests.
- **Partially implemented:** Present, but incomplete, helper-only, or not wired into normal runtime.
- **Approved target behavior:** Accepted direction, but not necessarily implemented.
- **Proposed and awaiting approval:** Candidate behavior needing design approval.
- **Deferred:** Not part of the initial read-only API/UI slice.
- **Unresolved:** Known ambiguity that must not be inferred.

## Ownership

Status: **Approved target behavior**.

This file records small cross-workstream decisions that affect pipeline/API/UI contracts. It does not replace [docs/refactor/DECISIONS.md](../refactor/DECISIONS.md), which remains the pipeline refactor ADR log.

Architecture decisions are append-only except for status changes, corrections, or explicit supersession.

## Decision Index

| ID | Status | Decision |
|---|---|---|
| CW-ADR-001 | Approved target behavior | Shared docs define API/UI contracts; `docs/refactor/` remains pipeline roadmap authority |
| CW-ADR-002 | Approved target behavior | Initial API is read-only |
| CW-ADR-003 | Approved target behavior | UI must use FastAPI contracts, not SQLite, log files, filesystem scans, or Python internals |
| CW-ADR-004 | Approved target behavior | Target architecture and current implementation must remain visibly separated |
| CW-ADR-005 | Approved target behavior | Combined WebODM mode uses `--both-tasks` / `Orthomosaic + 3D` with fixed Task 4 then Task 2 execution |
| CW-ADR-006 | Approved target behavior | Publication runs as one `Activate Publication` stage after quality-gate approval |
| CW-ADR-007 | Approved target behavior | Fully completed runs clean their run workspace by default, with `--keep-workspace` opt-out |
| CW-ADR-008 | Implemented and verified | WebODM pause is local detach; task resume uses canonical exact-ID bindings |

## CW-ADR-001 - Shared Contracts Reference Refactor Roadmap

- **Status:** Approved target behavior
- **Context:** The pipeline refactor is already planned and tracked under `docs/refactor/`.
- **Decision:** Shared docs under `docs/architecture/`, `docs/contracts/`, and `docs/ui/` describe cross-workstream boundaries and reference `docs/refactor/` instead of duplicating the roadmap.
- **Compatibility requirement:** Existing refactor documents are preserved as the source of truth for implementation sequence and status.
- **UI/API impact:** API and UI teams use shared contracts for integration boundaries, then consult refactor docs for implementation readiness.
- **Implementation dependency:** Contract changes must stay narrow and reviewable.

## CW-ADR-002 - Initial API Is Read-Only

- **Status:** Approved target behavior
- **Context:** Start/pause/resume/abort/retry/rerun/publish/recover semantics still involve protected resources and unresolved state decisions.
- **Decision:** The initial FastAPI layer exposes read-only run, stage, event, artifact, statistics, and system-resource views only.
- **Compatibility requirement:** No endpoint may mutate SQLite, flag files, workspaces, WebODM, QGIS/GDAL, publication manifests, or cleanup state until explicitly approved.
- **UI/API impact:** Initial UI screens may use live read-only API data or contract-valid mock data.
- **Implementation dependency:** Operational controls require approved state, authorization, idempotency, and recovery contracts.

## CW-ADR-003 - UI Uses API Contracts

- **Status:** Approved target behavior
- **Context:** Direct SQLite/filesystem/log access would couple the UI to unstable internals and could bypass safety guards.
- **Decision:** The Next.js UI communicates through FastAPI only.
- **Compatibility requirement:** UI behavior follows documented statuses, events, metrics, artifacts, and error contracts.
- **UI/API impact:** UI screens must not infer undocumented pipeline state.
- **Implementation dependency:** FastAPI projections must preserve current-versus-target status labels.

## CW-ADR-004 - Current and Target State Stay Separate

- **Status:** Approved target behavior
- **Context:** Several target entities and behaviors are not implemented yet.
- **Decision:** Shared docs must label current implementation, partial implementation, approved targets, proposed behavior, deferred work, and unresolved decisions.
- **Compatibility requirement:** Planned statuses, endpoints, metrics, database entities, UI controls, and workspace layouts must not be described as implemented.
- **UI/API impact:** Mock data must be visibly contract-valid but not represented as live operational behavior.
- **Implementation dependency:** API responses should include machine-readable status fields that allow the UI to display unavailable/deferred controls honestly.

## CW-ADR-005 - Combined Orthomosaic + 3D Mode

- **Status:** Approved target behavior
- **Context:** The approved cross-workstream contract says one run may select both Task 4 and Task 2. At decision time, CLI flags were mutually exclusive, and the internal `"both"` mode ran Task 2 before Task 4.
- **Decision:** Add an explicit combined-mode flag named `--both-tasks`. `--both-tasks` is the canonical CLI expression for selecting both WebODM operations and is semantically equivalent to selecting Task 4 and Task 2 together. The operator-facing UI/API label is `Orthomosaic + 3D`. Combined execution order is fixed: Task 4 first, then Task 2. If Task 4 fails, the combined WebODM sequence stops immediately and Task 2 does not run. If Task 4 succeeds and Task 2 fails, the run-level target status is `partially_completed`. Task 4 output is immediately eligible for publication. QGIS runs once per successful WebODM operation. The quality gate runs once after all selected WebODM tasks complete. Task 4 and Task 2 use separate stage records. Retrying the failed WebODM operation leaves successful operation outputs untouched by default.
- **Compatibility requirement:** Existing default Task 4, explicit Task 4, and explicit Task 2 behavior must remain compatible. `--task4 --task2` may be accepted as equivalent to `--both-tasks` only when the current CLI mutual-exclusion conflict is removed deliberately. Until implementation lands, API/UI must not claim combined mode is operational.
- **UI/API impact:** UI should display the mode as `Orthomosaic + 3D`, but keep live controls disabled until the pipeline implements the contract. API schemas may include `partially_completed` and per-operation/stage projections as approved targets, not current values.
- **Implementation dependency:** Pipeline implementation must add CLI parsing, fixed operation ordering, separate stage records, per-operation resume/retry behavior, operation-level artifacts and publication eligibility, one quality-gate boundary after selected operations, QGIS-per-successful-operation behavior, and `partially_completed` persistence/API mapping.
### CW-ADR-005 implementation note - 2026-08-11

The pipeline workstream has implemented the canonical `--both-tasks` CLI/runtime path, Task 4 then Task 2 ordering, separate `webodm_task4` and `webodm_task2` stage records, resume that preserves completed Task 4 while retrying failed Task 2, `partially_completed` persistence for Task 4 success followed by Task 2 failure, and operation-aware QGIS behavior. Complete operation metrics, durable operation identities, and API/UI projection hardening remain in progress.

## CW-ADR-006 - Activate Publication Stage

- **Status:** Approved target behavior
- **Context:** Phase 3 publication helpers can stage and activate publication explicitly, but live automatic `RGBPipeline.run()` publication activation is not yet implemented as a formal stage. The UI/API need one operator-friendly contract before live controls or runtime wiring are claimed.
- **Decision:** Add one formal publication stage with machine stage name `activate_publication` and operator-facing label `Activate Publication`. The stage runs automatically after quality-gate approval. For CLI/runtime implementation, authorization uses the exact phrase `PUBLISH <survey_id> <run_id>`; future UI/API approval is deferred until operational endpoints and actor/authorization semantics are implemented. Successful activation marks only the `Activate Publication` stage completed; existing run-status behavior decides the final run status. Missing staged manifest, missing authorization, or an existing publication lock before visible artifact mutation is recorded as `failed`. A failure after visible artifact mutation is recorded with target status `requires_recovery`. The stage uses one stage record. Stale publication-lock recovery and cleanup are part of the `Activate Publication` stage contract rather than separate default stages, but remain guarded subflows that require explicit authorization/evidence before mutation.
- **Compatibility requirement:** Existing explicit activation helpers, CLI confirmation phrase, legacy publication paths, and read-only API/UI defaults remain compatible. This decision does not make publication activation live, automatic cleanup, stale-lock recovery, or mutation endpoints implemented.
- **UI/API impact:** UI should display the stage as `Activate Publication`. Live publication controls remain deferred until the pipeline stage and future approval API are implemented. API projections may expose `requires_recovery` as an approved target status for publication activation attempts, not as a current SQLite value.
- **Implementation dependency:** Pipeline work must add the formal stage boundary after quality-gate approval, preserve exact CLI confirmation for now, map pre-mutation blocks to failed stage attempts, map post-mutation failures to `requires_recovery`, coordinate guarded recovery/cleanup subflows under the same stage contract, and add regression tests with pytest-owned roots.
### CW-ADR-006 implementation note - 2026-08-03

The pipeline workstream has implemented the guarded `activate_publication` StageRunner boundary, `requires_recovery` status persistence for typed post-mutation activation failures, guarded `RGBPipeline.run()` wiring when explicit confirmation is supplied, and the production CLI handoff through `--activate-publication --publication-confirmation "PUBLISH <survey_id> <run_id>"`. UI/API mutation controls, stale-lock recovery wiring, and cleanup wiring remain deferred.
## CW-ADR-007 - Default Completed-Run Workspace Cleanup

- **Status:** Approved target behavior
- **Context:** Run workspaces can consume hundreds of GB per run. Keeping every successful workspace indefinitely is useful for audit and debugging, but the default behavior must control disk pressure for normal operations.
- **Decision:** Fully `completed` runs clean their owned run workspace by default after final success state is persisted and required outputs have already been mirrored or published. Operators can pass `--keep-workspace` to preserve the workspace for a specific successful run. `partially_completed`, failed, paused, aborted, canceled, and recovery-required runs retain workspace evidence by default.
- **Compatibility requirement:** This intentionally changes the default runtime retention target for successful runs. Cleanup must validate workspace ownership and resolved-path containment, and must never delete published survey outputs, production roots, WebODM state, pause flags, logs, checkpoints needed for recovery, or recovery evidence.
- **UI/API impact:** Future read projections may expose workspace state such as `retained`, `cleaned`, `cleanup_skipped`, or `cleanup_failed`. UI mutation controls remain deferred until operational cleanup semantics are implemented safely.
- **Implementation dependency:** Pipeline work must add `--keep-workspace`, default cleanup for fully `completed` runs only, retention for all non-completed paths, clear cleanup logging, and tests using pytest-owned workspaces and published roots.

## CW-ADR-008 - Deterministic WebODM Task Reattachment

- **Status:** Implemented and verified
- **Context:** A process restart could lose Task 4 identity, causing resume to create a new WebODM project/task or search the wrong project. Pipeline pause also left attempts running while the remote task continued.
- **Decision:** Pipeline pause is a local detach and does not pause, cancel, or restart WebODM. The newest stage attempt controls resume. The canonical `webodm_tasks` binding stores run, operation, project ID, task UUID, expected name, remote status, and local artifact state. Normal Task 1, Task 2, and Task 4 resume fetches the exact task and fails closed on conflicts, missing identity, confirmed 404, failed/canceled state, or lookup failure. Normal orchestration does not search by name or implicitly delete/re-upload a bound task. Forced Task 4 execution means reconciliation and artifact delivery, never implicit remote replacement.
- **Compatibility requirement:** The legacy JSON checkpoint remains an atomic recovery mirror and completed-stage output can seed legacy migration only when it does not conflict with the canonical binding. Existing databases receive additive repeatable columns; historical rows remain readable. Duplicate WebODM projects/tasks are never deleted automatically.
- **UI/API impact:** Read projections should expose the newest stage attempt separately from binding history and should distinguish local `paused`, remote `running/completed/failed/canceled`, `requires_recovery`, and artifact readiness. Mutation endpoints remain deferred.
- **Recovery:** `tools/repair_webodm_binding.py` validates an exact project/task read-only by default. Explicit `--apply` appends repair/audit evidence and atomically updates the compatibility checkpoint without restarting, canceling, uploading, downloading, or deleting WebODM resources.

