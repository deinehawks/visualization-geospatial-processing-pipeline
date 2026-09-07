# Compatibility Policy

## Status Legend

- **Implemented and verified:** Present in code and covered by inspected safe tests.
- **Partially implemented:** Present, but incomplete, helper-only, or not wired into normal runtime.
- **Approved target behavior:** Accepted direction, but not necessarily implemented.
- **Proposed and awaiting approval:** Candidate behavior needing approval.
- **Deferred:** Not part of the initial API/UI slice.
- **Unresolved:** Known ambiguity that must not be inferred.

## Scope

Status: **Approved target behavior**.

This policy applies to externally visible behavior used by operators, the future FastAPI layer, the future Next.js UI, map export, reporting, and automation.

## Compatibility Rules

| Contract area | Compatibility requirement | Current status | Owner |
|---|---|---|---|
| CLI flags | Preserve existing default Task 4, `--task4`, `--task2`, `--resume`, `--run-id`, `--survey-id`, `--date`, `--node-id`, and `--force-stage` behavior. Additive `--uav <folder>` filters source candidates by an exact parent-folder component; independent `--rgb` accepts only DJI `*_D.JPG` source images. | Implemented and verified | Pipeline workstream |
| WebODM selection | Preserve default Task 4, explicit Task 4, and explicit Task 2. Combined `Orthomosaic + 3D` mode uses `--both-tasks` and executes Task 4 before Task 2. | Implemented and verified | Pipeline workstream |
| WebODM all-assets ZIP | `EXPORT_ALL_ASSETS_ZIP` applies additively to Task 2 and Task 4. Task 4 records `downloads.task4.all_assets_zip`, `workspace.webodm_odm.task4_all_assets_zip`, and `published.webodm_odm.task4_all_assets_zip`; ZIP failure remains optional and must not replace an existing published file. | Implemented and verified | Pipeline workstream |
| SQLite schema | Existing `runs`, `stages`, `surveys`, `webodm_tasks`, and `schema_migrations` rows must remain readable | Implemented and verified | Pipeline workstream |
| Stage names | Existing stage names remain stable for resume/reporting/API projections | Implemented and verified | Pipeline workstream |
| Status values | Existing statuses must be mapped, not replaced silently | Implemented and verified | Shared contracts owner for API-visible changes |
| Text logs | Existing log columns and parseable `event=<name>` records remain readable | Implemented and verified | Pipeline workstream |
| Publication paths | Legacy survey paths remain readable while manifest-aware consumers are introduced | Partially implemented | Pipeline workstream |
| Publication activation | `Activate Publication` is implemented as a guarded runtime stage when explicit confirmation is supplied, and the production CLI now exposes `--activate-publication` plus `--publication-confirmation`; UI/API mutation remains deferred. | Partially implemented | Pipeline workstream |
| API responses | Machine-readable fields and units remain stable once implemented | Proposed and awaiting approval | API workstream |
| UI behavior | UI follows contracts and does not infer private pipeline state | Approved target behavior | UI workstream |
| Documentation | Current versus target status labels remain explicit | Approved target behavior | Workstream introducing change |

## Breaking Changes

Status: **Approved target behavior**.

A breaking change requires explicit approval when it changes any of:

- CLI flag meaning or defaults.
- Run, stage, operation, artifact, event, error, metric, or publication status values.
- Timestamp, duration, or storage units.
- API endpoint paths, response fields, enum values, or pagination semantics.
- Artifact path ownership or publication manifest meaning.
- Retry, pause, abort, cancellation, quality-gate, or publication behavior.
- Database schema or migration behavior.

## Deprecation Policy

Status: **Proposed and awaiting approval**.

Recommended process:

1. Add the replacement contract while preserving the old behavior.
2. Mark the old behavior as deprecated in shared docs.
3. Add compatibility tests or API projection tests.
4. Provide a rollback plan for production data and operators.
5. Remove or change the old behavior only after explicit approval.

## Documentation Ownership

Status: **Approved target behavior**.

- Pipeline workstream owns `docs/refactor/CURRENT_STATUS.md`, refactor plans, and pipeline testing strategy documents.
- UI workstream owns `docs/ui/UI_PLAN.md`, `docs/ui/UI_STATUS.md`, and UI implementation details.
- The workstream introducing externally visible behavior owns the corresponding shared-contract update.
- Cross-workstream decisions are recorded as small entries in [../architecture/decisions.md](../architecture/decisions.md).
- Approved contracts must not be changed silently.
