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

The default suite now includes reusable temporary path/current-schema SQLite fixtures, a deterministic recording fake WebODM, credential/path isolation, autouse denial of network, subprocess, input, keyboard, dotenv, production database, and common production-path access, plus hermetic RGBPipeline construction. Phase 1 remains in progress because explicit production database connection ownership, platform-specific symlink/junction checks, and configuration-loader coverage remain incomplete.

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

On 2026-07-16, the pre-change default suite passed 13 tests. Final focused validation passed 3 fixture, 5 database, 5 fake WebODM, 8 safety-guard, and 1 import-safety tests. Default collection found 34 tests, the full suite passed 34 tests in 0.23 seconds, changed Python files compiled, and `git diff --check` passed with line-ending conversion warnings only. No external marker, network service, subprocess, production database, production survey directory, or operator tool was used.

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
