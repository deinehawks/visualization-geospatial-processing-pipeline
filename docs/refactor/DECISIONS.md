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
| ADR-003 | 2026-07-20 | Accepted | Intermediate work and final published survey artifacts need separate ownership. | Use a run-scoped workspace for mutable stage artifacts, then publish validated artifacts to legacy-compatible survey paths through a manifest-backed publish step. | Versioned immutable outputs plus pointer; serialized in-place writes; current shared tree. | Affects storage, compatibility, cleanup, recovery, and map consumers. | R02, R07; `docs/refactor/PHASE3_ARTIFACT_INVENTORY.md`; `modules/data_segregation/`; `pipelines/rgb_pipeline.py`; `shared/artifacts.py` |
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
| ADR-014 | 2026-07-20 | Accepted | File publication recovery requires one identifiable owner for a survey publication target. | Use an atomic, fail-closed filesystem ownership record in the published `rgb` root for the deliberately scoped publication boundary. | Local SQLite lease; OS advisory lock; external lock service; automatically expiring lease. | Coordinates contenders where exclusive file creation is reliable, but a crashed owner leaves evidence that requires explicit recovery. | R02, R04, R07; `shared/publication_lock.py`; `shared/artifacts.py`; Phase 3 |

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

ADR-001, ADR-002, ADR-003, ADR-013, ADR-014, ADR-015, ADR-016, ADR-017, ADR-018, ADR-019, ADR-020, and ADR-021 are accepted. ADR-004 through ADR-012 remain unresolved and must remain **Pending** or become **Proposed** only when a concrete option is prepared for review.

### ADR-003 - Separate run-owned workspace from published survey artifacts

- **Decision ID:** ADR-003
- **Date:** 2026-07-20
- **Status:** Accepted
- **Approval context:** On 2026-07-20, after the Phase 3 artifact inventory and ADR-003 proposal were prepared, the user approved moving into Phase 3 implementation.
- **Context:**
  - Current RGB processing writes many mutable intermediate and final artifacts into `<surveys_root>/<year>/<survey_id>/rgb/`.
  - Data segregation, cross-run filtering, WebODM exports, KML conversion, QGIS clipping, and tile copy-back can all modify the same shared survey tree.
  - QGIS and tile generation already use local run-scoped staging in some cases, but the final copy-back still targets legacy shared directories directly.
  - Map export and reporting consumers currently depend on the legacy survey layout and must remain compatible.
  - Failed, aborted, canceled, forced, or same-survey overlapping runs must not partially replace the active published artifact set.
- **Decision:**
  - Introduce a run-scoped workspace for mutable stage artifacts and publish only validated artifacts into the legacy-compatible survey tree.
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
- **Related files or issues:** R02, R07, R10, R11; `docs/refactor/PHASE3_ARTIFACT_INVENTORY.md`; `shared/artifacts.py`; `modules/data_segregation/data_segregation.py`; `pipelines/rgb_pipeline.py`; `modules/cross_run_image_filter/cross_run_image_filter.py`; `modules/kml_boundary_setter/kml_boundary_setter.py`; `modules/qgis/qgis_tools.py`; `modules/webodm/webodm_processor.py`; `modules/map_export/`.



### ADR-014 - Use fail-closed filesystem ownership for survey publication

- **Decision ID:** ADR-014
- **Date:** 2026-07-20
- **Status:** Accepted
- **Approval context:** On 2026-07-20, after the file-publication recovery milestone, the user approved the smallest safe same-survey publication-ownership slice before live RGBPipeline integration.
- **Context:**
  - File-publication retry and crash reconciliation deliberately assumes that only one run modifies a survey publication target at a time.
  - A published survey root can live on shared storage and can be reached by independent workstations, so a lease held only in one workstation's SQLite database would not necessarily coordinate every publisher.
  - Publication ownership must be diagnosable after a crash and must not silently guess that an existing owner is dead.
- **Decision:**
  - Represent ownership with an atomically exclusive-created `.publication.lock` JSON file in the published survey `rgb` root.
  - Record the lock version, run ID, survey ID, published root, creation time, and a unique owner token.
  - Treat any existing lock, including malformed or apparently stale evidence, as owned and fail closed. Do not automatically steal or expire it.
  - Release a lock only when the on-disk run ID, survey ID, and owner token match the caller's lock handle.
  - Introduce the helper as a dormant boundary first. Do not wire it into `activate_publication()` or `RGBPipeline` until a separate integration slice defines acquisition scope and exception-safe release behavior.
  - Keep ADR-004 pending for broader resource locking, scheduling topology, lease recovery, and non-publication resources.
- **Alternatives considered:**
  - Local SQLite lease: useful for one database authority, but it may not coordinate independent hosts that share only the publication tree.
  - OS advisory lock: avoids a retained ownership file but has less portable Windows/SMB behavior and weaker post-crash diagnostic evidence.
  - External lock service: can coordinate multiple hosts robustly, but introduces a new operational dependency and is broader than this Phase 3 slice.
  - Automatically expiring lease: improves unattended recovery, but requires trustworthy clocks or heartbeats and can permit two owners when a slow or partitioned publisher is still active.
- **Consequences:**
  - Competing publishers can identify a single owner where the target filesystem honors exclusive file creation.
  - A process or host crash can leave a stale lock; an explicit, separately designed operator recovery path is required before live activation.
  - Malformed lock evidence blocks publication instead of being overwritten, preserving evidence for diagnosis.
  - Real Windows SMB atomic-create and disconnect behavior remains unvalidated by the hermetic suite.
  - No database schema, dependency, live pipeline behavior, or existing operator default changes in the dormant slice.
  - Rollback consists of removing the unused helper, its tests, and this decision record; no persistent migration is required.
- **Related files or issues:** R02, R04, R07; `shared/publication_lock.py`; `shared/artifacts.py`; `tests/test_publication_lock.py`; Phase 3; ADR-003; ADR-004.


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

### ADR-015 - Activate large directory publications through exact hidden same-filesystem staging

- **Decision ID:** ADR-015
- **Date:** 2026-07-27
- **Status:** Accepted
- **Approval context:** The user explicitly requested an optimized dormant tile publication protocol that avoids copying millions of small tile files twice and keeps live QGIS/RGBPipeline integration deferred.
- **Context:**
  - QGIS tile outputs can contain thousands or millions of small files, making generate-elsewhere, copy-to-staging, validate, and activate prohibitively expensive.
  - Publication must still validate a complete tree, retain the previous visible version, preserve crash evidence, and commit metadata only after activation.
  - Same-filesystem directory rename is the practical fast visibility switch on the target filesystem, subject to Windows handle contention and unvalidated SMB behavior.
- **Decision:**
  - Define the only zero-copy source as `.activation/<run-id>/<published-name>.tmp` beside the final directory, returned by `publication_activation_path()`.
  - When the staged directory is anywhere else inside the approved run workspace, copy it once into that exact path before validation.
  - Validate file count and total bytes in one independent scan before rename; use only lightweight existence and declared required-path checks after rename.
  - Journal `prepared`, `previous_moved`, `activated`, `committed`, `rolled_back`, and `failed` states in the run-specific activation workspace.
  - Preserve an existing final directory in `.previous/<published-name>.<run-id>` and keep backup deletion outside the critical path.
  - Use bounded retries with backoff around directory rename operations.
  - Commit `publication.json` last and treat it as the marker of a fully published version.
  - Keep the primitive dormant and support one directory artifact per activation until restart reconciliation and multi-artifact semantics are separately approved.
- **Alternatives considered:**
  - Always copy workspace output into hidden staging: simpler ownership, but duplicates the dominant million-file operation and defeats the performance objective.
  - Activate directly from any hidden-looking path: faster but unsafe because an arbitrary path could bypass ownership and containment rules.
  - Recount after rename: stronger repeated verification but needlessly rescans millions of files after a same-filesystem metadata operation.
  - Delete the old directory during activation: saves space sooner but puts a potentially hours-long recursive deletion in the critical path and weakens rollback.
  - Content hashes: stronger identity checking but materially increases scan cost and is not required for this initial directory boundary.
- **Consequences:**
  - Future QGIS integration can generate directly into the exact activation path and avoid a second full tree copy.
  - Compatibility callers can still supply a workspace-staged directory at the cost of one copy into the activation filesystem.
  - Interrupted states remain diagnosable, but automatic restart reconciliation and stale-lock recovery are separate required decisions before live integration.
  - Old large backups consume space until a separate retention/cleanup policy is implemented.
  - Real Windows SMB rename, disconnect, open-handle, and atomicity behavior remains unvalidated by hermetic tests.
  - No schema migration, dependency, live pipeline behavior, or operator default changes in this dormant slice.
- **Related files or issues:** R02, R07, R10, R11; `shared/artifacts.py`; `tests/test_phase3_artifact_workspace.py`; Phase 3; ADR-003; ADR-014.

### ADR-016 - Treat publication metadata as authoritative during directory restart reconciliation

- **Decision ID:** ADR-016
- **Date:** 2026-07-27
- **Status:** Accepted
- **Approval context:** After accepting the dormant zero-copy directory activation protocol, the user approved implementing the next recommended restart-reconciliation safety slice before live QGIS/RGBPipeline integration.
- **Context:**
  - A process can stop after the previous directory moved, after the candidate became final, or after `publication.json` committed but before the journal advanced.
  - The filesystem and activation journal can therefore legitimately differ by one atomic rename or one atomic metadata write.
  - Recovery must not mistake an uncommitted visible directory for a committed publication or overwrite evidence when another run changed the active manifest.
- **Decision:**
  - Treat `publication.json` as the authoritative marker of a committed publication.
  - Record whether a previous final directory existed and the previous publication run ID in every new directory activation journal.
  - When `publication.json` names the current run, validate the current publication and finalize an `activated` journal as committed.
  - When `publication.json` still names the prior run or is absent as recorded, roll `previous_moved` and `activated` physical states back to that prior committed view.
  - Retain a valid `prepared` candidate when the committed view is still intact; restore the previous backup when its rename completed before the journal advanced.
  - Infer rename progress only from the exact validated temp/final/backup locations and reject any ambiguous combination.
  - Preserve candidates and backups; do not delete evidence during reconciliation.
  - Require exclusive ownership from the caller and keep stale-lock authorization/recovery as a separate decision.
- **Alternatives considered:**
  - Always roll forward the visible candidate: rejected because the candidate may not have passed metadata commitment and would make a partial attempt authoritative.
  - Trust only the journal status: rejected because a process can stop between an atomic rename and the following journal write.
  - Delete temp/backup evidence and restart: rejected because large-tree deletion is expensive and destroys diagnostic and rollback evidence.
  - Automatically clear stale publication locks: rejected as a separate ownership/authorization problem that can create two publishers.
- **Consequences:**
  - Interrupted directory activation can return to the last committed publication without copying or deleting the large tree.
  - A crash after metadata commit can be finalized idempotently without a second full directory scan.
  - Tampered, changed, or incomplete evidence blocks recovery and remains available for diagnosis.
  - Legacy previous-directory content identity remains limited without hashes or historical metrics.
  - Live integration still requires an explicit stale-lock diagnosis and operator recovery workflow.
  - No database migration, dependency, live pipeline behavior, or operator default changes in this dormant slice.
- **Related files or issues:** R02, R04, R07, R10, R11; `shared/artifacts.py`; `tests/test_phase3_artifact_workspace.py`; Phase 3; ADR-014; ADR-015.

### ADR-017 - Require snapshot-bound operator authorization for stale publication-lock recovery

- **Decision ID:** ADR-017
- **Date:** 2026-07-27
- **Status:** Accepted
- **Approval context:** The user approved the next prerequisite after dormant directory restart reconciliation, before any live QGIS/RGBPipeline publication wiring.
- **Context:**
  - Fail-closed publication locks prevent two publishers, but a process or host crash can leave ownership evidence after the owner has stopped.
  - File age alone cannot distinguish a dead owner from a legitimate long-running publication, especially across workstations and SMB reconnects.
  - Deleting a lock destroys diagnostic evidence and an operator can otherwise approve one snapshot while a different owner replaces it.
- **Decision:**
  - Separate read-only diagnosis from mutation.
  - Report timestamp validity and age as diagnostic information only; never declare a lock stale or recover it automatically by age.
  - Bind recovery approval to the exact SHA-256 lock snapshot and, when parseable, its survey/run identity through an exact confirmation phrase.
  - Require a non-empty operator reason and an explicit command acknowledgement.
  - Re-read the lock immediately before mutation and fail closed if any identity or byte evidence changed.
  - Archive the original lock bytes under a unique recovery ID and write a separate audit record instead of deleting evidence.
  - Permit malformed but readable evidence to use the same explicit archive workflow because it otherwise cannot be safely released through owner-token matching.
  - Do not infer owner liveness, terminate processes, acquire a replacement lock, reconcile publication state, or invoke recovery automatically from the pipeline.
- **Alternatives considered:**
  - Timeout/age-based automatic stealing: rejected because a legitimate long-running operation can exceed any guessed timeout.
  - Process-ID probing: insufficient across hosts, restarts, containers, and SMB clients, and PID reuse can produce false ownership.
  - Delete-after-confirmation: rejected because it removes the exact evidence needed for incident review and rollback diagnosis.
  - Recovery embedded in pipeline resume: rejected because resume must not silently convert ambiguous ownership into a new publisher.
- **Consequences:**
  - Operators can recover a verified abandoned or malformed lock without losing evidence or allowing a changed snapshot to reuse prior approval.
  - The workflow is intentionally manual and requires an independent liveness check outside this helper.
  - Real SMB consistency and rename behavior still require controlled environment validation before live activation becomes the default.
  - No schema migration, dependency, or live pipeline behavior changes.
- **Related files or issues:** R04, R07, R11; `shared/publication_lock.py`; `tools/publication_lock_recovery.py`; `tests/test_publication_lock_recovery.py`; Phase 3; ADR-014; ADR-016.

### ADR-018 - Define complete mixed-artifact publication-set semantics

- **Decision ID:** ADR-018
- **Date:** 2026-07-27
- **Status:** Accepted
- **Approval context:** After the publication-protocol acceptance review identified mixed file/directory activation as the blocker before live RGBPipeline wiring, the user approved resolving that prerequisite first.
- **Context:**
  - The accepted file protocol commits multiple files atomically enough for its recovery model, while the accepted directory protocol commits exactly one large directory through a journaled rename.
  - A real RGB run produces both kinds plus multiple logical artifact families.
  - Each old activation writes `publication.json` from its own staged manifest, so independent subset activation can discard previously listed artifacts or expose a mixed-run view.
  - Live RGBPipeline integration must not begin until one complete run-level publication authority exists.
- **Decision:**
  - Add a separate dormant `activate_publication_set_with_lock()` coordinator for complete publication generations instead of broadening `activate_publication()`.
  - Acquire the existing survey publication lock before any mixed-set mutation and release it in a `finally` block.
  - Validate the complete staged manifest and all artifact targets up front, including duplicate/nested target rejection.
  - Stage file artifacts to target-adjacent temporary files and directory artifacts to the exact hidden same-filesystem activation path unless generation already placed them there.
  - Write one set-level journal at `.activation/<run-id>/publication-set.json` and write one authoritative `publication.json` only after every artifact target has been activated.
  - Roll back all activated artifacts on caught activation failure before the final manifest switch, restoring the prior visible targets and leaving the prior manifest authoritative.
  - Treat any existing non-committed publication-set journal as requiring explicit reconciliation before retrying activation.
  - Reconcile interrupted mixed-set journals by treating `publication.json` as authoritative: finalize when it names the current run, otherwise roll physical moves back to the prior committed view using only exact recorded paths.
  - Keep current RGBPipeline workspace-to-legacy mirrors as the production default until cleanup and controlled filesystem validation are complete.
- **Alternatives considered:**
  - One versioned generation directory containing the complete artifact set, made active through one pointer or directory switch. Stronger atomicity, but broader because legacy consumers expect stable concrete file and directory paths.
  - Incremental per-artifact activation with manifest merge. Rejected because a crash can expose a mixed-run or incomplete manifest without an additional transaction model.
  - Broadening `activate_publication()` directly. Rejected to preserve the existing constrained file-only and one-directory contracts while the new set coordinator matures.
- **Consequences:**
  - The mixed file/directory publication authority now has one lock, one journal, and one final manifest commit in dormant code.
  - Existing publication primitives and live pipeline behavior remain backward compatible.
  - Interrupted mixed-set journals can now be reconciled by the dormant helper, while malformed, changed, failed, or ambiguous evidence still fails closed.
  - Retention/cleanup, real Windows/SMB validation, and live RGBPipeline activation remain separate follow-up requirements.
- **Related files or issues:** R02, R07, R10; `shared/artifacts.py`; `pipelines/rgb_pipeline.py`; `docs/refactor/PHASE3_PUBLICATION_ACCEPTANCE_REVIEW.md`; ADR-003; ADR-014; ADR-015; ADR-016; ADR-017; Phase 3.
### ADR-019 - Use a non-destructive cleanup planner before any artifact deletion

- **Decision ID:** ADR-019
- **Date:** 2026-07-27
- **Status:** Accepted
- **Approval context:** After mixed publication-set activation and reconciliation were implemented as dormant helpers, the user approved tackling backup retention and workspace cleanup policy before live RGBPipeline publication wiring.
- **Context:**
  - Phase 3 publication now intentionally retains activation journals, previous manifests, previous file artifacts, previous directory artifacts, and run workspaces as recovery and diagnostic evidence.
  - Large tile directories and run workspaces can consume significant storage if retained forever.
  - Cleanup is destructive by nature and must not race an active publisher, remove active artifacts, remove unresolved recovery evidence, or operate on production paths during normal tests.
- **Decision:**
  - Introduce cleanup as a two-step workflow: first produce a read-only plan, then use a separately approved executor later.
  - The planner must protect the active `publication.json` run ID and caller-provided preserved run IDs.
  - Any existing `.publication.lock` blocks cleanup planning for that published root.
  - Only terminal publication evidence, currently `committed` or `rolled_back`, may produce cleanup candidates.
  - Non-terminal, failed, malformed, changed, or unknown journal evidence blocks cleanup instead of producing candidates.
  - Candidates must resolve under an explicit owner root, be old enough according to an explicit minimum age, and carry a reason, kind, run ID, and owner root.
  - The planner may report terminal activation directories, previous publication manifests, previous artifact backups referenced by terminal journals, and run workspace directories not protected by active/preserved run IDs.
  - The planner must not delete, move, truncate, or overwrite anything.
- **Alternatives considered:**
  - Immediate cleanup during activation: rejected because it lengthens the critical path, risks deleting rollback evidence too early, and is dangerous for million-file tile trees.
  - Time-based background deletion: rejected because age alone cannot prove a publication is safe to remove, especially around SMB disconnects and long-running jobs.
  - Manual filesystem cleanup only: safe in the short term but untraceable and error-prone once run workspaces and retained backups accumulate.
- **Consequences:**
  - Operators and future tooling can inspect exactly what would be cleaned before any deletion feature exists.
  - Live RGBPipeline wiring remains blocked on a later executor/approval flow and controlled filesystem validation.
  - Storage usage still grows until a deletion executor is approved and implemented.
  - No schema migration, dependency, live pipeline behavior, or destructive operation is introduced by this decision.
- **Related files or issues:** R02, R07, R10, R11; `shared/artifacts.py`; `tests/test_phase3_artifact_workspace.py`; ADR-003; ADR-015; ADR-016; ADR-018; Phase 3.
### ADR-020 - Execute artifact cleanup only from a revalidated plan

- **Decision ID:** ADR-020
- **Date:** 2026-07-27
- **Status:** Accepted
- **Approval context:** After ADR-019 introduced a read-only cleanup planner, the user approved implementing the explicit cleanup executor/operator flow before live RGBPipeline publication wiring.
- **Context:**
  - Retained activation evidence, previous artifacts, and workspaces need a way to be removed eventually, but deletion is destructive and cannot be inferred from a previous plan alone.
  - A valid cleanup plan can become stale if a publication lock appears, the active manifest changes, evidence changes, or a candidate disappears.
  - Operator cleanup needs dry-run review, explicit acknowledgement, containment checks, ownership sentinels, and audit evidence.
- **Decision:**
  - Add a dormant `execute_artifact_cleanup()` helper that defaults to dry-run and deletes only when `allow_delete=True`.
  - Re-run `plan_artifact_cleanup()` immediately before mutation and reject execution if the fresh candidate set or blocked reasons differ from the caller-reviewed plan.
  - Require an explicit `.artifact-cleanup-root` sentinel file at each owner root before any candidate under that root can be deleted.
  - Reject filesystem roots, owner-root candidates, wrong candidate kinds, missing sentinels, active publication locks, and stale plans before deletion.
  - Write an audit record under `.artifact-cleanup-audit/<cleanup-id>.json` for approved delete attempts, including candidates, deleted entries, failed attempts, and blocked reasons.
  - Add `tools/artifact_cleanup.py` with read-only `plan` and guarded `execute` commands; `execute` requires `--allow-delete` and a caller-supplied cleanup ID.
  - Keep the executor dormant and operator-invoked; do not call it from `RGBPipeline` or background jobs.
- **Alternatives considered:**
  - Delete directly from planner output without revalidation: rejected because the plan can become stale after review.
  - Use age-only cleanup: rejected because age does not prove publication safety or owner liveness.
  - Require only `--allow-delete`: rejected because a mistyped root would still be dangerous without owner-root sentinels.
  - Move to trash/quarantine instead of deletion: safer for some filesystems but not reliable for large tile trees, network shares, or cross-volume roots without separate storage policy.
- **Consequences:**
  - Operators get a dry-run-first workflow and explicit audit trail before any future cleanup use.
  - The helper can delete files/directories when intentionally invoked, so production use still requires operator authorization and controlled path selection.
  - Normal tests delete only pytest-owned temporary files/directories with sentinels.
  - Live pipeline behavior, publication activation defaults, database schema, and dependencies remain unchanged.
  - Real SMB deletion performance, interrupted deletion recovery, antivirus/indexer contention, and large-tree timing remain unvalidated outside controlled environment tests.
- **Related files or issues:** R02, R07, R10, R11; `shared/artifacts.py`; `tools/artifact_cleanup.py`; `tests/test_phase3_artifact_workspace.py`; ADR-019; Phase 3.
### ADR-021 - Validate publication filesystem behavior only under disposable sentinel roots

- **Decision ID:** ADR-021
- **Date:** 2026-07-27
- **Status:** Accepted
- **Approval context:** After the guarded cleanup executor was implemented, the user approved the next prerequisite: controlled local/cross-volume/SMB filesystem validation before live RGBPipeline publication wiring.
- **Context:**
  - Phase 3 publication relies on filesystem behaviors that vary by local disk, cross-volume paths, Windows SMB shares, open handles, caching, antivirus/indexers, and disconnect/reconnect behavior.
  - The hermetic suite proves helper logic but cannot prove operational filesystem semantics for production-like storage.
  - Validation itself must create, rename, replace, and delete files, so it must never run against production survey roots or broad paths by accident.
- **Decision:**
  - Add an explicit opt-in filesystem validator that runs only under a caller-supplied disposable root containing `.filesystem-validation-root`.
  - Require `--allow-destructive-validation` before creating, replacing, renaming, or deleting validation artifacts.
  - Keep validation artifacts under `.filesystem-validation-runs/<validation-id>` and reject filesystem roots, unsafe validation IDs, missing sentinels, and reused validation IDs.
  - Validate the primitives needed by Phase 3 publication: exclusive-create lock behavior, file replacement, directory backup/candidate rename, and JSON read-after-write visibility.
  - Emit structured JSON results and optionally write a report beneath the validation root.
  - Keep this separate from `RGBPipeline`, publication activation, cleanup execution, and normal default tests.
- **Alternatives considered:**
  - Treat hermetic tests as sufficient: rejected because SMB/cross-volume behavior is explicitly outside their scope.
  - Run validation automatically from the pipeline: rejected because it mutates the target filesystem and could collide with real work.
  - Validate directly inside survey roots: rejected because disposable validation must not touch real survey outputs.
  - Use external benchmark tools: rejected for this slice because the required publication semantics are narrow and can be tested with standard-library primitives.
- **Consequences:**
  - Operators get a repeatable protocol for local, cross-volume, and SMB validation before live publication wiring is enabled.
  - The tool is intentionally destructive within its disposable sentinel root, so target selection remains an operator responsibility.
  - Normal tests exercise only pytest-owned temporary roots and do not validate real SMB behavior.
  - Live RGBPipeline publication remains deferred until controlled validation is run and reviewed in the intended environment.
- **Related files or issues:** R02, R04, R07, R10, R11; `shared/filesystem_validation.py`; `tools/filesystem_validation.py`; `tests/test_filesystem_validation.py`; Phase 3; ADR-014; ADR-015; ADR-018; ADR-020.
