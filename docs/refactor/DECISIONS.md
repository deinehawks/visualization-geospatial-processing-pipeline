# Architectural Decision Log

## Usage

This file records architectural decisions for the scalability and concurrency refactor. It does not itself authorize implementation.

Allowed statuses:

- **Proposed** — a concrete option is being evaluated.
- **Pending** — the decision is required, but no preferred option has been selected.
- **Accepted** — approved for implementation.
- **Superseded** — replaced by a later decision.
- **Rejected** — considered and intentionally not selected.

When a decision is accepted, record the approval context and link any migration, rollback, or compatibility plan. Do not rewrite historical entries; supersede them with a new decision ID.

## Decision record template

### ADR-XXX — Short title

- **Decision ID:** ADR-XXX
- **Date:** YYYY-MM-DD
- **Status:** Proposed | Pending | Accepted | Superseded | Rejected
- **Context:** What problem, constraints, and evidence require a decision?
- **Decision:** What has been selected? Use “Pending” until approved.
- **Alternatives considered:** List credible alternatives and why they remain viable or were rejected.
- **Consequences:** Positive, negative, operational, compatibility, migration, and rollback consequences.
- **Related files or issues:** Repository paths, issue IDs, audit risks, and superseded ADRs.

## Initial unresolved decisions

These entries identify required decisions without selecting final architectures.

| Decision ID | Date | Status | Context | Decision | Alternatives considered | Consequences | Related files or issues |
|---|---|---|---|---|---|---|---|
| ADR-001 | 2026-07-16 | Accepted | Normal test discovery is currently unsafe and no framework is declared. | Use pytest as the default test framework. | Standard-library unittest; another approved runner. | pytest becomes a development/test dependency; configuration, markers, and safe handling of existing scripts are required. | R13; `tests/`; `requirements.txt` |
| ADR-002 | 2026-07-17 | Accepted | Run/stage log context must remain correct under threads and multiple pipeline instances. | Use owned per-run logger instances keyed by logical name, run ID, and log destination, while preserving logical logger names in records and using context-local stage state. | Pure `contextvars`; per-run logger adapters; explicit structured event objects; per-run logger names only. | Fixes handler/context leakage while preserving log parser columns; requires explicit handler cleanup in tests. | R05; `shared/logging.py`; `query_survey_stats.py`; `tests/test_logging_context_ownership.py` |
| ADR-003 | 2026-07-20 | Proposed | Intermediate work and final published survey artifacts need separate ownership. | Use a run-scoped workspace for mutable stage artifacts, then publish validated artifacts to legacy-compatible survey paths through a manifest-backed publish step. | Versioned immutable outputs plus pointer; serialized in-place writes; current shared tree. | Affects storage, compatibility, cleanup, recovery, and map consumers. | R02, R07; `docs/refactor/PHASE3_ARTIFACT_INVENTORY.md`; `modules/data_segregation/`; `pipelines/rgb_pipeline.py` |
| ADR-004 | 2026-07-16 | Pending | Conflicting survey/resource use must be coordinated across the intended deployment topology. | Pending. | Database leases; OS/file locks; lock service; scheduler-enforced exclusivity. | Affects stale recovery, multi-host support, and operational complexity. | R02–R04; Phase 4 |
| ADR-005 | 2026-07-16 | Pending | Survey IDs must be reserved atomically while preserving existing naming. | Pending. | SQLite allocation table/transaction; dedicated sequence service; atomic directory reservation; externally supplied IDs only. | Affects gaps, migration, legacy reconciliation, and database dependency. | R03; `generate_next_survey_id` |
| ADR-006 | 2026-07-16 | Pending | A forced rerun can create a newer failed attempt after an older success. | Pending. | Latest attempt authoritative; explicit selected attempt; successful output remains active until atomic replacement; stage generation model. | Defines resume, reporting, publication, and migration semantics. | R11; `stages`; `PipelineRepo.get_latest_stage` |
| ADR-007 | 2026-07-16 | Pending | State spans SQLite, checkpoint JSON, filesystem artifacts, and WebODM. | Pending. | Database-authoritative state plus artifact manifests; versioned atomic files; event journal with reconciliation. | Determines crash consistency and migration scope; cannot create a true distributed transaction with WebODM. | R07; `_save_webodm_checkpoint`; Phase 7 |
| ADR-008 | 2026-07-16 | Pending | Retries need explicit transient-error and idempotency semantics. | Pending. | Typed policy per operation; command objects with idempotency keys; reconciliation-first workflow; no automatic stage retries. | Affects resilience, duplicate prevention, and external API assumptions. | R06; `StageRunner.run`; WebODM methods |
| ADR-009 | 2026-07-16 | Pending | Pause and abort need bounded behavior for Python loops, subprocesses, and remote tasks. | Pending. | Cooperative token plus managed child processes; checkpoint-only pause; abort local only; abort local and remote. | Affects artifact safety, operator expectations, and WebODM data retention. | R08; `PipelineControl`; QGIS/WebODM modules |
| ADR-010 | 2026-07-16 | Pending | WebODM uploads currently open all files and use one multipart request. | Pending. | API-supported chunk/resume; bounded descriptor streaming; staged archive; admission-limited current protocol. | Constrained by WebODM API compatibility and task semantics. | R09; `create_task_with_images` |
| ADR-011 | 2026-07-16 | Pending | Job scheduling topology determines state-store and lock requirements. | Pending. | Single-host multi-process; single-host service with workers; multi-host durable queue; retain manual independent CLI runs. | Affects dependencies, SQLite viability, deployment, and operations. | R01, R04; Phase 12 |
| ADR-012 | 2026-07-16 | Pending | Quality approval must support unattended execution without losing manual oversight. | Pending. | Persisted manual approval; rules-based automatic gate; external API/UI; hybrid shadow mode. | Affects authorization, auditability, worker capacity, and output quality risk. | R12; `stage_quality_gate`; Phase 13 |
| ADR-013 | 2026-07-17 | Accepted | Phase 2 needs consistent machine-readable run/stage observability without a schema change. | Keep existing text log columns and standardize lifecycle messages as key=value event records. | JSON logs; database event journal; free-form human-only logs. | Preserves parser compatibility and avoids persistent migration; future DB refactor can promote the same event model into state tables. | Phase 2; `shared/logging.py`; `shared/stage_runner.py`; `pipelines/rgb_pipeline.py`; `query_survey_stats.py` |

## Accepted decisions

### ADR-001 — Use pytest as the default test framework

- **Decision ID:** ADR-001
- **Date:** 2026-07-16
- **Status:** Accepted
- **Context:**
  - The repository currently has no configured test framework.
  - Existing files under `tests/` are executable scripts rather than isolated tests.
  - Future phases require temporary directories, temporary SQLite databases, monkeypatching, parameterization, markers, and controlled integration tests.
- **Decision:**
  - pytest will be the canonical default test runner.
  - Normal tests must run without production `.env`, real WebODM, QGIS/GDAL execution, production SQLite, or production survey directories.
  - External integration tests must be explicitly marked and excluded by default.
  - Operational and manual scripts must not live under default test discovery.
- **Alternatives considered:** Standard-library unittest or another approved runner.
- **Consequences:**
  - pytest becomes a development/test dependency.
  - pytest configuration and markers must be added.
  - Existing test scripts must be converted, moved, or safely gated.
- **Related files or issues:** R13; `tests/`; `requirements.txt`; `docs/refactor/TEST_STRATEGY.md`; Phase 1.

## Decision index

ADR-001, ADR-002, and ADR-013 are accepted. ADR-003 is proposed for review. ADR-004 through ADR-012 remain unresolved and must remain **Pending** or become **Proposed** only when a concrete option is prepared for review.

### ADR-003 - Separate run-owned workspace from published survey artifacts

- **Decision ID:** ADR-003
- **Date:** 2026-07-20
- **Status:** Proposed
- **Context:**
  - Current RGB processing writes many mutable intermediate and final artifacts into `<surveys_root>/<year>/<survey_id>/rgb/`.
  - Data segregation, cross-run filtering, WebODM exports, KML conversion, QGIS clipping, and tile copy-back can all modify the same shared survey tree.
  - QGIS and tile generation already use local run-scoped staging in some cases, but the final copy-back still targets legacy shared directories directly.
  - Map export and reporting consumers currently depend on the legacy survey layout and must remain compatible.
  - Failed, aborted, canceled, forced, or same-survey overlapping runs must not partially replace the active published artifact set.
- **Decision:**
  - Proposed: introduce a run-scoped workspace for mutable stage artifacts and publish only validated artifacts into the legacy-compatible survey tree.
  - The publish step should be manifest-backed, record the active run and artifact set, and preserve the previous active published artifacts until the new set is complete.
  - Existing consumers should continue resolving legacy paths. When a publication manifest exists, consumers may later prefer the manifest and fall back to legacy globbing for older runs.
  - Cleanup and retention of failed or superseded workspaces should be handled by a later explicit policy.
- **Alternatives considered:**
  - Versioned immutable outputs plus pointer: strong recovery and audit model, but broader because every consumer must learn the pointer/version layout before Phase 3 can reduce shared-tree collision risk.
  - Serialized in-place writes: reduces concurrent same-survey writes, but still exposes partial outputs, stale artifacts, and crash ambiguity inside the published tree.
  - Current shared tree: lowest implementation cost, but keeps the main R02/R07 risks in place.
- **Consequences:**
  - Same-survey runs can perform mutable work in separate directories.
  - Failed, aborted, or canceled runs can retain diagnostic workspaces without replacing active published artifacts.
  - Existing published paths can remain stable for operators, map export, and reporting.
  - Storage usage will increase until retention is designed and implemented.
  - Publish behavior must handle Windows and network-share copy/rename semantics explicitly.
  - SQLite stage output and historical logs may contain legacy absolute paths; migration or compatibility handling must be planned before changing stored path meaning.
  - Rollback should be possible by disabling the new workspace/publish path planner and continuing to use the legacy shared layout, provided no destructive migration has been performed.
- **Related files or issues:** R02, R07, R10, R11; `docs/refactor/PHASE3_ARTIFACT_INVENTORY.md`; `modules/data_segregation/data_segregation.py`; `pipelines/rgb_pipeline.py`; `modules/cross_run_image_filter/cross_run_image_filter.py`; `modules/kml_boundary_setter/kml_boundary_setter.py`; `modules/qgis/qgis_tools.py`; `modules/webodm/webodm_processor.py`; `modules/map_export/`.


### ADR-002 - Isolate logger context and handler ownership per run

- **Decision ID:** ADR-002
- **Date:** 2026-07-17
- **Status:** Accepted
- **Context:**
  - Phase 2 characterization confirmed that repeated `get_logger()` calls with the same logical name reused one process-global logger, retained the first file handler destination, and mutated shared `ContextFilter.run_id` / `stage_name` values.
  - Two default `RGBPipeline` instances in one process could therefore write the second run ID into the first pipeline's log file.
  - `query_survey_stats.py` parses existing log columns as `time | level | logger | run_id | stage | message`, so compatibility with logical logger names and column order matters.
- **Decision:**
  - `get_logger()` will keep the existing public API but create owned concrete logger instances when a run ID or file destination is supplied.
  - Concrete logger identity is keyed by logical logger name, run ID, and log file path.
  - Log records preserve the logical logger name in the existing formatter field so parser compatibility is maintained.
  - Stage context is stored in context-local state keyed by concrete logger identity instead of mutating one process-global stage field.
  - Existing callers that use `logging.getLogger()` directly or call `get_logger()` with no run ID and no log file keep conventional logger-name behavior.
- **Alternatives considered:**
  - Pure `contextvars`: rejected because it does not by itself fix shared file-handler destinations.
  - Per-run logger names only: insufficient because it exposes internal names in existing log formats unless additional record rewriting is added, and it does not address context-local stage state.
  - `LoggerAdapter` at every call site: explicit but broader and more invasive than needed for this phase.
  - Structured event objects: a likely future direction, but too broad for the current compatibility-preserving fix.
- **Consequences:**
  - Multiple pipeline instances in one process no longer share file handlers or mutable stage/run context through same-named loggers.
  - Existing text log columns and logical logger names remain compatible with `query_survey_stats.py`.
  - Tests that create owned loggers should close and remove their handlers explicitly to avoid process-global logging registry residue.
  - Rollback is straightforward: revert `shared/logging.py` and `tests/test_logging_context_ownership.py`; no schema or data migration is involved.
- **Related files or issues:** R05; `shared/logging.py`; `query_survey_stats.py`; `tests/test_logging_context_ownership.py`; Phase 2.

### ADR-013 - Standardize Phase 2 run/stage observability events

- **Decision ID:** ADR-013
- **Date:** 2026-07-17
- **Status:** Accepted
- **Context:**
  - ADR-002 fixed logger ownership and context isolation, but Phase 2 still needs a stable observability contract before adding more log lines or helpers.
  - `query_survey_stats.py` and operators currently rely on human-readable text logs using `time | level | logger | run_id | stage | message`.
  - Later phases will need richer state and database semantics, but Phase 2 must avoid schema changes and persistent migrations.
- **Decision:**
  - Preserve the existing text log columns and logical logger names.
  - Lifecycle and diagnostic messages that are intended for parsing should use a predictable `event=<name> key=value ...` message convention inside the existing message field.
  - Required common fields for parseable events are `event`, `run_id` in the existing column, and `stage` in the existing column when the event is stage-scoped.
  - Include `survey_id` when known, `attempt` when retry or attempt semantics are involved, `elapsed_seconds` for completed or failed work, and `error_type` plus a bounded `error_message` for failures.
  - External-effect events should include stable identifiers without secrets: WebODM project/task IDs and labels, subprocess/tool names, return codes, selected task labels, and bounded artifact counts where available.
  - Human banners and progress lines may remain free-form, but they must not be the only source for required lifecycle/failure facts once this contract is implemented.
  - Do not log credentials, tokens, private URLs, unbounded paths, or large payloads.
- **Event vocabulary for Phase 2 implementation:**
  - Run lifecycle: `run_started`, `run_completed`, `run_failed`, `run_paused`, `run_aborted`, `run_canceled`.
  - Stage lifecycle: `stage_started`, `stage_skipped`, `stage_completed`, `stage_failed`, `stage_retrying`, `stage_canceled`, `stage_stale`.
  - State/artifact diagnostics: `stage_output_loaded`, `checkpoint_saved`, `checkpoint_loaded`, `artifact_selected`, `artifact_published` where those actions are already present and safe to observe.
  - External boundaries: `webodm_project_created`, `webodm_task_created`, `webodm_task_status`, `webodm_download_started`, `webodm_download_completed`, `qgis_command_started`, `qgis_command_completed`, `qgis_command_failed`.
- **Alternatives considered:**
  - JSON logs: stronger structure, but a larger compatibility shift for existing operators and parser tooling.
  - Database event journal: likely valuable in a future DB/state phase, but it requires schema design, migration, rollback, and historical compatibility work outside Phase 2.
  - Free-form human logs only: lowest implementation cost, but insufficient for diagnostics, parser stability, and later concurrency/recovery phases.
- **Consequences:**
  - Phase 2 can add small logging helpers and tests against a stable vocabulary without changing the log-file format or database schema.
  - `query_survey_stats.py` can evolve incrementally to prefer explicit `event=` records while retaining fallback regexes for historical logs.
  - The future database/state refactor can promote this event vocabulary into durable attempt/state tables instead of inventing a second model.
  - Rollback is documentation-only until implementation begins; future implementation rollback should remove added helper calls without data migration.
- **Related files or issues:** Phase 2; `shared/logging.py`; `shared/stage_runner.py`; `pipelines/rgb_pipeline.py`; `query_survey_stats.py`; future ADRs for database/state phases.
