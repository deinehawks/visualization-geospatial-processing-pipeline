# Scalability and Concurrency Refactor Plan

## Planning principles

- Preserve data integrity, resumability, traceability, and backward compatibility.
- Establish safe tests before changing behavior.
- Separate run-owned working data from explicitly published survey artifacts.
- Make state transitions explicit before introducing concurrency.
- Add concurrency only after ownership, locking, idempotency, cancellation, and recovery semantics are enforceable.
- Use temporary directories, temporary databases, and fake external services in normal validation.
- Do not assign dates in this plan. Each phase requires explicit approval before production changes.

## Phase 0 — Planning and baseline inventory

This is the current planning phase. Its outputs are the audit, implementation sequence, decision log, status record, and test strategy in this directory. It does not authorize production changes.

## Phase 1 — Test isolation and safety baseline

- **Objective:** Make normal test discovery deterministic and incapable of touching real pipelines, services, survey roots, output trees, or the production database.
- **Why now:** Every later phase needs regression protection; the current test layout can trigger real effects during import.
- **Dependencies:** None beyond an approved test framework and temporary-resource policy.
- **Expected deliverables:** Side-effect-free test modules; fixtures for temporary directories/databases; explicit external-test markers or commands; environment/config injection seams; a safe default test command; documented test-data limits.
- **Acceptance criteria:** Importing/discovering tests performs no external I/O; the default suite runs with network blocked and without production `.env`; destructive helpers require explicit paths and opt-in execution; safety-guard regression tests pass.
- **Risks:** Tests may currently depend implicitly on local `.env`, Windows paths, QGIS installations, or real data. Moving setup behind fixtures may expose hidden coupling.
- **Testing requirements:** Validate discovery in an empty temporary workspace; assert no WebODM requests; assert only temporary SQLite/filesystem paths are used; include sentinel files proving real roots are untouched.

## Phase 2 — Logging and observability

- **Objective:** Replace mutable process-global run/stage context with concurrency-safe structured context and define state-transition/event fields.
- **Why now:** Correct attribution is necessary to diagnose every later ownership, recovery, and concurrency change.
- **Dependencies:** Phase 1; pending decision on logging context mechanism and compatibility with existing text logs.
- **Expected deliverables:** Run/attempt identifiers attached per record without shared mutation; handler lifecycle rules; structured event vocabulary; preserved human-readable logs; tests for concurrent runs and threads.
- **Acceptance criteria:** Interleaved concurrent test runs never exchange run, survey, stage, or attempt identifiers; each configured destination receives only intended records; existing operational messages remain usable or have a documented compatibility path.
- **Risks:** Third-party/subprocess output lacks native context; changing logger construction may duplicate handlers or change reporting tools.
- **Testing requirements:** Threaded and multi-instance logging tests; handler duplication tests; log parser compatibility tests for `query_survey_stats.py`; subprocess-output attribution tests.

## Phase 3 — Run-scoped workspace ownership

- **Objective:** Give each run an exclusive working directory and define an explicit, validated publish step for survey artifacts.
- **Why now:** Locking and concurrent scheduling cannot be safe while runs directly mutate shared survey outputs.
- **Dependencies:** Phases 1–2; artifact inventory; decisions on workspace layout, publication, retention, and backward-compatible final paths.
- **Expected deliverables:** Workspace manifest; run-owned paths for intermediate data, uploads, QGIS staging, and tiles; publication contract; ownership metadata; cleanup/retention policy; compatibility adapter for consumers.
- **Acceptance criteria:** Two runs for one survey cannot overwrite each other's workspaces; failed/aborted runs cannot partially replace published outputs; publish is atomic where supported or has a recoverable manifest protocol; existing consumers can resolve final artifacts.
- **Risks:** Large datasets may temporarily double storage; rename atomicity varies across volumes and network shares; legacy scripts may assume current paths.
- **Testing requirements:** Concurrent workspace tests; crash-at-publish fault injection; same-volume and cross-volume publication tests; compatibility tests for map export and reporting.

## Phase 4 — Survey and resource locking

- **Objective:** Prevent incompatible concurrent use of surveys, source datasets, WebODM nodes, output publication targets, and constrained local resources.
- **Why now:** Locks need clear ownership boundaries from Phase 3 and observable holders from Phase 2.
- **Dependencies:** Phases 1–3; lock-scope and lease/timeout decisions; deployment topology inventory.
- **Expected deliverables:** Lock manager abstraction; survey/resource lock keys; acquisition ordering; lease/heartbeat or local-lock policy; stale-lock recovery; operator diagnostics.
- **Acceptance criteria:** Conflicting operations serialize or fail clearly; non-conflicting surveys can proceed; stale locks are recoverable without allowing two owners; lock holder and wait reason are observable.
- **Risks:** Deadlocks, orphaned leases, clock assumptions, network filesystem lock semantics, and over-locking that removes throughput gains.
- **Testing requirements:** Multi-process contention tests; lock-order tests; owner crash and stale-recovery tests; network-share behavior tests where applicable.

## Phase 5 — Transaction-safe survey ID allocation

- **Objective:** Reserve each generated survey ID exactly once under concurrency and make allocation auditable.
- **Why now:** Allocation should use the locking/state foundations rather than introduce a separate ad hoc mechanism.
- **Dependencies:** Phases 1–4; decision on allocation authority and backward compatibility with existing folder-derived IDs.
- **Expected deliverables:** Transactional reservation API; uniqueness constraint or equivalent authority; reservation lifecycle; handling for explicit overrides; migration/rollback plan if persistence changes.
- **Acceptance criteria:** Concurrent allocation tests produce unique sequential or policy-valid IDs; a crash cannot silently reuse a reserved ID; explicit duplicate overrides fail safely; existing surveys remain resolvable.
- **Risks:** Gaps in sequences, abandoned reservations, legacy folders not represented in state, and migration conflicts.
- **Testing requirements:** High-contention multi-process allocation tests; crash after reserve/before publish; legacy-directory reconciliation; rollback tests.

## Phase 6 — Authoritative stage-attempt state model

- **Objective:** Represent every attempt explicitly and define which attempt/output is authoritative after retry, force, crash, cancellation, or supersession.
- **Why now:** Atomic recovery and safe retries require unambiguous attempt semantics.
- **Dependencies:** Phases 1–5; decision on state machine, attempt identity, supersession, and compatibility queries.
- **Expected deliverables:** Documented run/stage/attempt state machine; attempt identifiers and ordering; authoritative-attempt rule; transition API; migration and rollback strategy; reporting compatibility updates.
- **Acceptance criteria:** A failed forced rerun cannot be mistaken for the current successful state; illegal transitions are rejected; resume decisions are deterministic; historical successes remain queryable without shadowing current truth.
- **Risks:** Persistent schema/API changes, compatibility with existing databases and reports, and migration of ambiguous historical records.
- **Testing requirements:** Transition-table tests; forced-rerun scenarios; stale-running recovery; migration fixtures from representative database versions; reporting regression tests.

## Phase 7 — Atomic checkpoint and recovery behavior

- **Objective:** Define recoverable commit points across database state, checkpoint files, local artifacts, and external WebODM effects.
- **Why now:** Requires authoritative attempts and run-owned artifacts.
- **Dependencies:** Phases 1–6; decisions on checkpoint authority, write protocol, artifact manifests, and external-effect reconciliation.
- **Expected deliverables:** Atomic file-write helper; versioned checkpoint format or database-backed replacement; recovery/reconciliation algorithm; artifact validity markers; fault-injection matrix.
- **Acceptance criteria:** Crashes at each defined boundary recover to a deterministic action; truncated checkpoints are rejected without losing valid prior state; external tasks are reconciled before creation; partial artifacts are never published as complete.
- **Risks:** No true distributed transaction exists across SQLite, filesystem, and WebODM; network shares may weaken rename/durability guarantees.
- **Testing requirements:** Kill/fault injection around every commit point; malformed/truncated checkpoint tests; WebODM “accepted but response lost” fake; cross-volume artifact tests.

## Phase 8 — Retry classification and idempotency

- **Objective:** Retry only classified transient failures and require an idempotency/reconciliation policy for every retried operation.
- **Why now:** Safe classification depends on the attempt and recovery models from Phases 6–7.
- **Dependencies:** Phases 1–7; operation inventory; error taxonomy; WebODM API capability findings.
- **Expected deliverables:** Retry policy types; transient/permanent/canceled error classes; bounded backoff and jitter; idempotency keys or lookup-before-create behavior; per-operation retry declarations.
- **Acceptance criteria:** Validation/data errors fail immediately; retryable database/network failures do not repeat committed external effects; retry budgets are observable; nested retries do not exceed defined limits.
- **Risks:** Misclassification can reduce resilience or duplicate effects; WebODM may not support desired idempotency primitives.
- **Testing requirements:** Error-class matrix tests; duplicate-submission simulations; retry exhaustion tests; database-lock tests; deterministic fake-clock/backoff tests.

## Phase 9 — Cooperative pause and cancellation

- **Objective:** Propagate a run-scoped cancellation token through loops, copies, uploads, polling, and managed subprocesses with explicit pause versus abort semantics.
- **Why now:** Cancellation must preserve the atomic state and idempotency guarantees already established.
- **Dependencies:** Phases 1–8; cancellation-state decisions; child-process management design; remote-task cancellation policy.
- **Expected deliverables:** Cancellation-token abstraction; bounded check intervals; managed subprocess wrapper; pause-safe checkpoints; abort cleanup policy; remote WebODM behavior; operator-visible state.
- **Acceptance criteria:** Each long operation meets a defined cancellation-latency target; paused work resumes without duplication; aborted child processes are terminated/escalated predictably; published outputs remain valid.
- **Risks:** Forceful process termination may corrupt outputs; Windows process trees and QGIS wrappers need special handling; remote cancellation may be irreversible.
- **Testing requirements:** Cancellation at stage boundaries and mid-operation; subprocess-tree tests; pause/resume fault cases; remote-task fake tests; cleanup ownership assertions.

## Phase 10 — Large-data and upload resource handling

- **Objective:** Bound memory, file descriptors, staging disk, bandwidth, and concurrent upload use.
- **Why now:** Resource controls should integrate with locking, retries, checkpoints, and cancellation rather than bypass them.
- **Dependencies:** Phases 1–9; measured dataset envelopes; WebODM upload API investigation.
- **Expected deliverables:** Streaming/batched upload strategy where supported; descriptor limits; staging quotas; resource admission checks; progress metrics; cache retention/reuse rules.
- **Acceptance criteria:** Upload resource use stays within documented bounds as image count grows; cancellation closes resources promptly; concurrent uploads respect quotas; resume/reconciliation avoids duplicate tasks.
- **Risks:** WebODM API limitations may require a server-side change or conservative single-upload policy; batching may alter task semantics.
- **Testing requirements:** Thousands-of-files synthetic tests; descriptor-leak checks; disk-full and network interruption tests; concurrent upload quota tests; small opt-in WebODM compatibility test.

## Phase 11 — Filesystem and tile-performance improvements

- **Objective:** Reduce repeated scans and copies, bound tile work, and make progress/resume proportional to changed data.
- **Why now:** Optimization follows correctness, ownership, and cancellation so faster operations remain safe.
- **Dependencies:** Phases 1–10; profiling results; artifact manifest design; acceptable tile limits and output formats.
- **Expected deliverables:** Scan/copy manifest or index; single-pass inventories; incremental copy strategy; tile-work estimates and quotas; configurable GDAL concurrency; disk-space forecasting; benchmark suite.
- **Acceptance criteria:** Benchmarks show reduced repeated traversal and bounded resource use; resume avoids full recount where a valid manifest exists; oversized requests fail before destructive work; results remain byte/semantically compatible as required.
- **Risks:** Cached manifests can become stale; parallel filesystem work may overload SMB/storage; tile limits may reject previously accepted workloads.
- **Testing requirements:** Representative local and network-share benchmarks; manifest invalidation tests; output equivalence tests; quota and disk-exhaustion tests.

## Phase 12 — Job queue and worker scheduling

- **Objective:** Introduce controlled run-level concurrency with admission control, priorities, worker leases, and resource-aware scheduling.
- **Why now:** A queue is unsafe until state, ownership, locking, retry, cancellation, and resource limits are reliable.
- **Dependencies:** Phases 1–11; deployment topology decision; durable queue technology decision; operational requirements.
- **Expected deliverables:** Job contract; scheduler/worker lifecycle; worker heartbeat and lease recovery; per-resource concurrency limits; graceful shutdown; operational metrics and run commands.
- **Acceptance criteria:** Multiple non-conflicting jobs run concurrently; conflicting/resource-heavy jobs obey limits; worker loss results in safe recovery; no job is silently lost or executed concurrently by two owners; single-run CLI compatibility is documented.
- **Risks:** Distributed coordination complexity, unfair scheduling, duplicate delivery, database capacity, and additional operational dependencies.
- **Testing requirements:** Multi-worker stress tests; worker crash/restart; duplicate delivery; fairness/backpressure; resource saturation; end-to-end smoke tests with fake WebODM/QGIS.

## Phase 13 — Automated quality-gate support

- **Objective:** Support unattended, persisted quality-gate decisions while retaining an explicit manual-review path.
- **Why now:** Worker scheduling cannot tolerate terminal input, but automation should build on durable jobs and authoritative state.
- **Dependencies:** Phases 1–12; approval-policy and authorization decisions; quality metrics and timeout/escalation requirements.
- **Expected deliverables:** Non-interactive gate interface; persisted pending/approved/rejected state; policy plug-in or rules engine boundary; timeout/escalation behavior; manual approval command/API; audit trail.
- **Acceptance criteria:** Workers never block on `input()`; decisions survive restart; manual and automated decisions are attributable and idempotent; rejection/restart paths follow attempt and retry rules; existing interactive behavior has an intentional compatibility path.
- **Risks:** Automated acceptance can publish poor outputs; authorization and audit requirements may expand scope; metric thresholds may differ by survey type.
- **Testing requirements:** Approval/rejection/restart state-machine tests; restart while awaiting approval; timeout/escalation tests; authorization tests; shadow-mode evaluation against historical manual decisions.

## Phase gates

Before each phase begins:

1. Resolve or explicitly defer its entries in `DECISIONS.md`.
2. Confirm the production and persistent-data scope.
3. Approve migration and rollback plans where state or paths change.
4. Define the exact safe validation command.
5. Update `CURRENT_STATUS.md` without marking unvalidated work complete.
