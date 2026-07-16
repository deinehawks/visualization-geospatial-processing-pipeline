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
| ADR-002 | 2026-07-16 | Pending | Run/stage log context must remain correct under threads and multiple pipeline instances. | Pending. | `contextvars`; per-run logger adapters; explicit structured event objects; per-run logger names. | Affects log compatibility, handler ownership, and reporting tools. | R05; `shared/logging.py`; `query_survey_stats.py` |
| ADR-003 | 2026-07-16 | Pending | Intermediate work and final published survey artifacts need separate ownership. | Pending. | Run-scoped workspace plus atomic publish; versioned immutable outputs plus pointer; serialized in-place writes. | Affects storage, compatibility, cleanup, recovery, and map consumers. | R02, R07; `modules/data_segregation/`; `pipelines/rgb_pipeline.py` |
| ADR-004 | 2026-07-16 | Pending | Conflicting survey/resource use must be coordinated across the intended deployment topology. | Pending. | Database leases; OS/file locks; lock service; scheduler-enforced exclusivity. | Affects stale recovery, multi-host support, and operational complexity. | R02–R04; Phase 4 |
| ADR-005 | 2026-07-16 | Pending | Survey IDs must be reserved atomically while preserving existing naming. | Pending. | SQLite allocation table/transaction; dedicated sequence service; atomic directory reservation; externally supplied IDs only. | Affects gaps, migration, legacy reconciliation, and database dependency. | R03; `generate_next_survey_id` |
| ADR-006 | 2026-07-16 | Pending | A forced rerun can create a newer failed attempt after an older success. | Pending. | Latest attempt authoritative; explicit selected attempt; successful output remains active until atomic replacement; stage generation model. | Defines resume, reporting, publication, and migration semantics. | R11; `stages`; `PipelineRepo.get_latest_stage` |
| ADR-007 | 2026-07-16 | Pending | State spans SQLite, checkpoint JSON, filesystem artifacts, and WebODM. | Pending. | Database-authoritative state plus artifact manifests; versioned atomic files; event journal with reconciliation. | Determines crash consistency and migration scope; cannot create a true distributed transaction with WebODM. | R07; `_save_webodm_checkpoint`; Phase 7 |
| ADR-008 | 2026-07-16 | Pending | Retries need explicit transient-error and idempotency semantics. | Pending. | Typed policy per operation; command objects with idempotency keys; reconciliation-first workflow; no automatic stage retries. | Affects resilience, duplicate prevention, and external API assumptions. | R06; `StageRunner.run`; WebODM methods |
| ADR-009 | 2026-07-16 | Pending | Pause and abort need bounded behavior for Python loops, subprocesses, and remote tasks. | Pending. | Cooperative token plus managed child processes; checkpoint-only pause; abort local only; abort local and remote. | Affects artifact safety, operator expectations, and WebODM data retention. | R08; `PipelineControl`; QGIS/WebODM modules |
| ADR-010 | 2026-07-16 | Pending | WebODM uploads currently open all files and use one multipart request. | Pending. | API-supported chunk/resume; bounded descriptor streaming; staged archive; admission-limited current protocol. | Constrained by WebODM API compatibility and task semantics. | R09; `create_task_with_images` |
| ADR-011 | 2026-07-16 | Pending | Job scheduling topology determines state-store and lock requirements. | Pending. | Single-host multi-process; single-host service with workers; multi-host durable queue; retain manual independent CLI runs. | Affects dependencies, SQLite viability, deployment, and operations. | R01, R04; Phase 12 |
| ADR-012 | 2026-07-16 | Pending | Quality approval must support unattended execution without losing manual oversight. | Pending. | Persisted manual approval; rules-based automatic gate; external API/UI; hybrid shadow mode. | Affects authorization, auditability, worker capacity, and output quality risk. | R12; `stage_quality_gate`; Phase 13 |

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

ADR-001 is accepted. ADR-002 through ADR-012 remain unresolved and must remain **Pending** or become **Proposed** only when a concrete option is prepared for review.
