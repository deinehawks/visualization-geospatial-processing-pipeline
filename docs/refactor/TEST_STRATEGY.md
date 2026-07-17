# Test Strategy for the Scalability and Concurrency Refactor

## Safety objective

The default developer and CI test workflow must be hermetic with respect to production data and services. Tests should make unsafe access structurally difficult, not depend only on developer caution.

Normal test discovery must never:

- Start a real pipeline.
- Submit a real WebODM task.
- Access production survey directories.
- Modify the production SQLite database.
- Delete or reset real output directories.

## Current test baseline

### Canonical framework and command

pytest 8.4.2 is the accepted default framework and is pinned in `requirements.txt`. Configuration in `pytest.ini`:

- restricts discovery to `tests/`;
- registers the `external` marker; and
- applies `-m "not external"` by default.

The canonical normal command is:

```text
python -m pytest -q
```

External tests require explicit marker selection and are never part of the default command.

### Current layout and operator tools

Default-discovered tests contain definitions and assertions only:

| File | Coverage |
|---|---|
| `tests/test_experiment_naming.py` | Parameterized filter and DJIFP naming behavior |
| `tests/test_query_pipeline_db.py` | Temporary read-only SQLite query behavior |
| `tests/test_reset_environment.py` | Temporary-root cleanup guards and allowed deletion set |
| `tests/test_import_safety.py` | Import traps for runtime, external, interactive, deletion, and write side effects |
| `tests/test_fixture_infrastructure.py` | Temporary path ownership, composability, small placeholders, and directory isolation |
| `tests/test_temporary_database.py` | Current schema, independent records, handle releasability, and production database denial |
| `tests/test_fake_webodm.py` | Deterministic IDs, ordered calls, failure injection, and network independence |
| `tests/test_suite_safety_guards.py` | Network, subprocess, input, dotenv, keyboard, sensitive paths, and safe temporary operations |

The former executable scripts now live outside discovery:

| Tool | Safety boundary |
|---|---|
| `tools/run_rgb_pipeline.py` | Lazy production imports, explicit paths, main guard, and `--allow-external-run` |
| `tools/query_pipeline_db.py` | Explicit database path and run ID, existing-file requirement, SQLite `mode=ro`, reliable close |
| `tools/reset_environment.py` | Explicit root, sentinel, containment checks, main guard, and `--allow-destructive-reset` |

### Validated baseline

On 2026-07-16, syntax checks passed, default collection found 13 tests, and the full default suite passed 13 tests. All database and deletion tests used pytest temporary directories. No external tests were run.

The default suite now includes reusable temporary path/current-schema SQLite fixtures, a deterministic recording fake WebODM, credential/path isolation, autouse denial of network, subprocess, input, keyboard, dotenv, production database, and common production-path access, plus hermetic StageRunner coverage, hermetic RGBPipeline construction, and one hermetic RGBPipeline single-stage execution test. Phase 1 is complete enough to move to Phase 2; remaining gaps are tracked as non-blocking risks for later phases.

### Shared test infrastructure

`tests/conftest.py` provides:

- pytest-owned application, data, logs, surveys, field-data, upload-cache, and checkpoint directories;
- a structured `TemporaryPathLayout`;
- sample survey and dataset directories with tiny placeholder image files;
- a current-schema SQLite path, explicit-close connection fixture, and isolated database factory; and
- suite-wide safety guards restored after every test by pytest's monkeypatch fixture.

`tests/fakes/webodm.py` provides deterministic authentication/preflight, project and task creation, task lookup/status/wait behavior, download request recording, ordered call inspection, and configurable transient or permanent failures. It imports no network client and does not read image contents.

RGBPipeline now accepts an existing WebODM processor. Its primary WebODM stage, fallback task, and quality-gate restart path all resolve the processor through one helper, while the default still constructs the real WebODMProcessor only when a stage requests it. Constructor tests retain FakeWebODM without invoking authentication, preflight, or any fake call.

## Desired test architecture

Use the approved pytest framework with a layout that makes test level and external effects explicit.

Suggested logical layout:

```text
tests/
├── unit/
├── component/
├── integration/
├── external/
├── smoke/
├── fakes/
├── fixtures/
└── safety/
```

Operational cleanup/query scripts should not remain discoverable test modules. Their long-term location and safety interface require a separate approved task.

### Level 1 — Unit tests

**Scope:** Pure functions and small classes with all I/O replaced by passed collaborators or temporary resources.

Priority targets:

- Experiment and task naming.
- Survey-folder parsing and candidate selection.
- Cross-run distance/bearing and classification algorithms.
- WebODM status normalization and error classification.
- Retry policy decisions and backoff calculation.
- Stage/run state-transition validation.
- Path ownership validation and artifact-manifest rules.
- Logging context construction.

Requirements:

- No network, subprocess, environment `.env`, sleep, or production filesystem access.
- Deterministic time/UUID/random behavior through injected fakes where relevant.
- Table-driven edge cases for state transitions and idempotency classifications.

### Level 2 — Component tests

**Scope:** One repository component with real local implementation details but controlled boundaries.

Priority components:

- `PipelineRepo` against a temporary SQLite file, including migrations and concurrent access.
- `StageRunner` with fake stage callables and a temporary repository.
- `PipelineControl` using a temporary data directory.
- Logging handlers writing to temporary files.
- Data segregation and cross-run filtering against generated small image/KML fixtures.
- QGIS command construction with subprocess execution replaced by a recorder fake.
- WebODM request construction against a fake HTTP/session layer.

Requirements:

- Each test owns and cleans only its temporary root.
- Database tests use a fresh database or an explicitly copied migration fixture.
- Failures can be injected before and after each state/artifact boundary.

### Level 3 — Integration tests using fakes or mocks

**Scope:** Multiple real pipeline components wired together, with WebODM, QGIS/GDAL/PDAL, keyboard hooks, time, and production storage replaced.

Required scenarios:

- Happy-path run and resume from every completed stage.
- Crash/stale-running recovery at every stage.
- Forced rerun followed by success and failure.
- WebODM accepted-task/response-lost reconciliation.
- Transient versus permanent error retry behavior.
- Pause and abort during copy, upload, poll, and subprocess execution.
- Two runs for the same survey and two runs for different surveys.
- Database lock contention and worker loss.
- Atomic publish and rollback/failure before publish.

The fake WebODM service should record task creation, expose controlled statuses, simulate timeouts after commit, and reject duplicate/idempotency violations. The fake subprocess runner should support progress, failure, hanging, cancellation, and partial-output simulation.

### Level 4 — Explicit opt-in external integration tests

**Scope:** Compatibility checks against installed QGIS/GDAL/PDAL and a dedicated non-production WebODM environment.

Requirements:

- Never included in normal discovery/default CI.
- Require an explicit marker/command and an affirmative environment gate such as a dedicated test-environment identifier.
- Refuse to run when paths overlap configured production roots or when production credentials are detected.
- Use a dedicated WebODM project/tenant and unique test task names.
- Use bounded datasets and clean only artifacts carrying test ownership metadata.
- Report created remote resources so operators can clean them safely.

These tests validate compatibility, not every error path.

### Level 5 — Small-dataset pipeline smoke tests

**Scope:** A complete pipeline over a tiny committed/generated fixture, normally with fake external services and optionally in the dedicated external environment.

Requirements:

- Strict image count, byte-size, raster dimensions, zoom range, runtime, and output-file-count limits.
- Temporary run root and database.
- Assertions on stage state, artifact manifests, logs, resume behavior, and cleanup ownership.
- A smoke test must fail closed if any resolved path escapes its temporary root.

## Mandatory safety controls

Phase 1 should establish these controls before broad test execution:

1. **No import-time execution:** test modules contain definitions only; executable helpers use an explicit main guard and are not discoverable tests.
2. **Configuration injection:** tests construct configuration dictionaries directly and do not load repository `.env` by default.
3. **Temporary roots:** field data, surveys, upload cache, logs, exports, workspaces, and databases all resolve beneath a per-test temporary directory.
4. **Path containment guard:** destructive helpers resolve paths and reject anything outside the test-owned root.
5. **Network deny by default:** unexpected HTTP/session/socket use fails the test immediately.
6. **Subprocess deny by default:** unexpected QGIS/GDAL/PDAL or shell execution fails unless a recorder/fake is installed.
7. **Credential isolation:** normal tests clear or ignore WebODM credentials from the developer environment.
8. **Small-data budget:** fixtures and generated outputs have enforced size/file-count limits.
9. **Explicit external markers:** external tests require both selection and environment approval.
10. **Sentinel validation:** safety tests place sentinels in representative non-test paths and verify they remain unchanged.

Implemented controls are deliberately narrow. Common `Path`, `open`, and cleanup access to captured production roots is rejected, while normal reads outside those data roots and all pytest-owned temporary operations remain available. Direct patching of every `os` filesystem primitive is avoided to preserve Python imports and pytest internals.

## Latest validation

On 2026-07-17, ADR-002 was accepted and the first logger isolation fix was implemented. Validation compiled `shared/logging.py` and `tests/test_logging_context_ownership.py`, passed 3 focused logging tests, passed RGBPipeline construction, RGBPipeline single-stage, and StageRunner suites, collected 51 tests, and passed the full default suite 51/51 before documentation updates, then collected 51 tests and passed the final full default suite 51/51 after documentation updates. A follow-up parser compatibility test then passed the focused logging module 4/4, collection found 52 tests, the final full default suite passed 52/52, and `git diff --check` passed, proving generated isolated logger output remains readable by `query_survey_stats.parse_log_events()`. No external marker, network service, subprocess, production database, production survey directory, operator tool, real pipeline run, WebODM request, QGIS/GDAL command, keyboard hook, interactive input, or destructive cleanup operation was used.

Recommendation: Phase 1 is complete enough to move to Phase 2 - Logging and observability. The default suite now validates the most important safety guarantees for discovery, dotenv denial, network/WebODM denial, subprocess/QGIS/GDAL denial, production SQLite denial, production-root denial, destructive-helper opt-in, temporary filesystem/SQLite fixtures, fake WebODM, and import-time side-effect guards. Remaining gaps are non-blocking for Phase 2 and should be carried forward explicitly.

## Missing coverage inventory

Current tests do not provide confirmed automated coverage for:

- Configuration parsing and validation without real environment paths.
- Pipeline preflight behavior with fake services/tools.
- Database schema creation, migrations, foreign keys, WAL/busy behavior, and rollback.
- Run, survey, stage, and WebODM task persistence APIs.
- Resume output hydration and WebODM checkpoint parsing.
- Corrupt/truncated checkpoint recovery.
- Stage retries, classification, exhaustion, and idempotency.
- Forced-rerun and authoritative-attempt semantics.
- Survey ID concurrency.
- Shared output ownership and publication.
- Logger context under concurrent runs.
- Pause, resume, abort, cancellation, and child-process cleanup.
- Upload descriptor/memory bounds and resource cleanup.
- Filesystem scan/copy performance and interruption.
- Tile generation command construction, resume, limits, and partial outputs.
- Quality-gate approval, rejection, restart, timeout, and non-interactive behavior.
- Map-export compatibility with refactored paths/state.
- Multi-worker scheduling, leases, duplicate delivery, and crash recovery.

## Validation sequence for future changes

Once Phase 1 is approved and implemented, validation should progress from safest to broadest:

1. Import/discovery safety checks with network and subprocess blocked.
2. Unit tests.
3. Component tests with temporary files/databases.
4. Fake-backed integration tests and fault injection.
5. Small-dataset smoke test.
6. Explicit opt-in external compatibility tests only when separately authorized.

Never claim a phase validated when its required level could not be run. Record skipped external paths and remaining uncertainty in `CURRENT_STATUS.md`.

## Hermetic StageRunner orchestration baseline

Added on 2026-07-17, `tests/test_stage_runner_orchestration.py` exercises the smallest real orchestration unit without constructing the full RGB pipeline.

The test wires real `StageRunner` and `PipelineRepo` logic to pytest-owned SQLite, a temporary image directory, and `FakeWebODM`. Existing constructor/callable parameters provide the injection boundary, so no production seam was introduced.

The success path verifies:

- run and stage creation;
- fake authentication and preflight;
- deterministic project `100` and task `task-0001`;
- state and SQLite output persistence;
- completed stage status; and
- containment of all data paths beneath the temporary application root.

The controlled failure path verifies:

- the fake is called only through authentication and preflight;
- a stage `ValueError` is surfaced;
- failed status and the error message are persisted;
- no project, task, or wait side effect occurs; and
- all resources remain temporary.

This milestone does not make `RGBPipeline` construction hermetic. Direct WebODM construction, configuration/environment loading, logger/global context, pipeline control paths, and production database connection ownership remain untestable at that level without further seams.

That investigation confirmed the non-cancellation `RuntimeError` defect; the following milestone corrects it while preserving explicit control signals.

Validation: the committed suite remained 34/34; focused success and failure tests passed; fake WebODM passed 5/5; safety guards passed 8/8; collection found 36 tests; the full default suite passed 36/36 in 0.31 seconds; compile and `git diff --check` passed. No external operation occurred.

The StageRunner correction is recorded in the following milestone. The next testability task is investigation of minimal backward-compatible seams for hermetic `RGBPipeline` construction.

## StageRunner failure classification coverage

Added on 2026-07-17, StageRunner orchestration coverage now distinguishes terminal failures from explicit pipeline-control signals.

Production classification in `shared/stage_runner.py` recognizes only the existing exact messages `WEBODM_TASK_CANCELED`, `__PIPELINE_CANCELED__`, `__PIPELINE_PAUSED__`, and `__PIPELINE_ABORTED__`. Non-control exceptions, including `RuntimeError` subclasses, enter the existing retry/failure path and are terminally recorded as failed before propagation when attempts are exhausted.

The focused test module now covers:

- unchanged successful completion;
- ordinary `ValueError` failure recording and propagation;
- permanent fake WebODM runtime failure recording and propagation;
- generic non-cancellation `RuntimeError` failure recording and propagation;
- unchanged pause, abort, and translated cancellation propagation;
- unchanged WebODM UI cancellation translation and canceled-stage recording; and
- prevention of later fake WebODM calls after failure.

Validation collected 42 tests and passed 42/42. Focused StageRunner, fake WebODM, and safety-guard tests all passed. No external operation occurred.

Remaining test-architecture risks are the legacy message-backed control signals, intentional non-terminal active-stage behavior for propagated pause/abort/cancel signals, the unchanged broad retry policy, and the lack of a hermetic RGBPipeline stage execution test.

## Hermetic RGBPipeline construction coverage

Added on 2026-07-17, tests/test_rgb_pipeline_construction.py imports and
constructs the real RGBPipeline while the default-suite safety guards deny
dotenv, network, subprocess, input, keyboard, production database, and captured
production-path access.

The constructor now exposes only the seams required for this level:

- db_file or an existing PipelineRepo;
- an existing logger mapping and explicit log/checkpoint directories; and
- an existing WebODM processor.

Base, source, survey, year, run ID, and configuration were already explicit.
PipelineControl and PipelinePreflight constructors are inert, so no extra seam
was introduced for them. QGIS command execution, quality-gate input, hotkey
registration, preflight checks, and StageRunner.run remain outside constructor
execution.

Coverage proves:

- all owned paths, control flags, checkpoints, and SQLite files resolve beneath
  the pytest application root;
- the constructor's existing run upserts affect only temporary SQLite;
- injected repository, logger objects, database path, and FakeWebODM are
  retained;
- FakeWebODM receives no calls during construction;
- default repository, logger, checkpoint, control, and WebODM wiring remains
  compatible through non-I/O recorder doubles;
- invalid non-mapping configuration fails before any logger, repository,
  directory, or WebODM side effect; and
- no pipeline stage or preflight check executes.

The environment does not contain exifread and requirements.txt does not declare
it, although importing RGBPipeline imports the cross-run filter module. The
construction test supplies a test-only import stub whose process_file function
fails immediately if used. This permits import/constructor coverage without
installing a production dependency and cannot mask stage execution in these
tests.

Validation compiled the changed Python files, passed 4 focused construction
tests, 8 StageRunner orchestration tests, 5 FakeWebODM tests, and 8 safety-guard
tests. Default collection found 46 tests and the full default suite passed
46/46. No external test or operation ran.

Remaining coverage does not yet execute a real RGBPipeline stage, exercise the
configuration loader without dotenv, or resolve production PipelineRepo
connection-close semantics. The next testability target is one hermetic
single-stage RGBPipeline execution with every external boundary faked.


## Hermetic RGBPipeline single-stage execution coverage

Added on 2026-07-17, `tests/test_rgb_pipeline_single_stage_execution.py` executes one real `RGBPipeline.run()` stage with all external and persistent boundaries replaced by temporary or fake collaborators.

The selected stage is `data_segregation`. The production orchestration path is real through `RGBPipeline.run()`, `StageRunner.run()`, repository state updates, shared pipeline state hydration, run/survey finalization, control-flag cleanup, and output persistence. The stage's heavy dependency, `run_data_segregation`, is faked at the imported module boundary so no real survey scan, real imagery copy, production root, WebODM call, QGIS/GDAL command, quality-gate prompt, or external subprocess is used.

`RGBPipeline.run()` now has two explicit optional testability seams with production-compatible defaults:

- `selected_stages=None` preserves the complete existing stage order; passing a set executes only those existing stage names in production order and rejects unknown names.
- `raise_on_error=False` preserves the current returned failure-state behavior; passing `True` re-raises after failure state has been recorded.

Coverage proves:

- temporary construction with injected repository, loggers, checkpoint path, and `FakeWebODM`;
- exactly one selected stage executes;
- `data_segregation` reaches SQLite `running` state before the fake dependency returns;
- deterministic fake output is persisted as completed stage output;
- survey ID attachment, survey running/upsert, KML rename, run completion, and survey completion remain temporary;
- checkpoint/control/database/output paths are under the pytest-owned application root;
- later stages do not execute and do not create stage rows;
- controlled ordinary failure is retried three times under the current StageRunner default, then marked failed and propagated with `raise_on_error=True`; and
- network, subprocess, dotenv, input, keyboard, production path, and WebODM fake-call guards remain effective.

Validation compiled the changed files, passed the focused success and failure tests, passed existing RGBPipeline construction, StageRunner, FakeWebODM, and safety-guard suites, collected 48 tests, passed `git diff --check`, and passed the final full default suite 48/48. No external operation occurred.

Remaining default-suite integration gaps are every later RGBPipeline stage, the real data segregation implementation, full-run quality-gate behavior, real WebODM/QGIS/GDAL compatibility, configuration-loader coverage without dotenv, and repository connection-lifecycle semantics.


## Phase 1 completion assessment summary

Phase 1 acceptance criteria from `REFACTOR_PLAN.md` are satisfied well enough to start Phase 2. The assessment status is:

| Area | Status | Evidence |
|---|---|---|
| Safe collection/imports | Pass | 48-test collection succeeds; import-safety tests trap runtime, external, interactive, deletion, and write side effects. |
| No production `.env` | Pass | Autouse dotenv denial plus safety-guard self-test. |
| No WebODM/network | Pass | Socket and requests guards plus FakeWebODM tests and fake-backed orchestration tests. |
| No QGIS/GDAL/subprocess | Pass | Subprocess guard plus safety-guard self-test; no default QGIS/GDAL execution. |
| No production SQLite | Pass | SQLite guard, temporary DB fixtures, production DB denial test. |
| No production survey roots | Pass | Environment redirection and captured production-root guards. |
| Destructive helper opt-in | Pass | Reset tool requires explicit root, sentinel, containment, and `--allow-destructive-reset`. |
| Temporary resources | Pass | Temporary filesystem and current-schema SQLite fixtures are tested. |
| Fake services | Pass | FakeWebODM supports deterministic calls and failure injection. |
| External exclusion | Pass | `pytest.ini` excludes `external` by default; real RGB runner requires `--allow-external-run`. |

Non-blocking risks remain: later RGBPipeline stages, real data segregation, quality gate, WebODM/QGIS/GDAL compatibility, SQLite concurrency/connection lifecycle, symlink/junction containment, and explicit external marker exercise. These do not block Phase 2 because Phase 2 can proceed with temporary log paths, injected collaborators, and the established safety guards.

The recommended first Phase 2 test task is to characterize existing logger context and handler ownership under two simultaneous logger/pipeline constructions, using temporary log paths and no pipeline stage execution. This should provide evidence for ADR-002 before changing logging internals.


## Phase 2 logging isolation coverage

Added on 2026-07-17, `tests/test_logging_context_ownership.py` first characterized the current logging isolation problem, then was converted to desired-behavior coverage after ADR-002 was accepted.

Coverage now proves:

- repeated logical logger names with different run IDs/log files receive separate owned logger objects;
- each owned logger writes only to its own file handler destination;
- run IDs do not cross between owned loggers;
- stage names do not cross between owned loggers; and
- two default `RGBPipeline` constructions in one process own separate `rgb.pipeline` handlers while preserving the logical `rgb.pipeline` record name and existing log parser column shape;
- generated isolated `rgb.pipeline` output remains readable by `query_survey_stats.parse_log_events()` when filtering by run ID.

The tests use temporary log paths, temporary RGBPipeline construction, fake WebODM, and explicit logger cleanup. They do not run pipeline stages or external tools. Explicit threaded/interleaved logging coverage is intentionally deferred until concurrency becomes part of the production execution design; adding it now would duplicate isolation guarantees already covered without matching a current runtime path.
