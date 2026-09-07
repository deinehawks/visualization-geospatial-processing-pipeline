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
â”œâ”€â”€ unit/
â”œâ”€â”€ component/
â”œâ”€â”€ integration/
â”œâ”€â”€ external/
â”œâ”€â”€ smoke/
â”œâ”€â”€ fakes/
â”œâ”€â”€ fixtures/
â””â”€â”€ safety/
```

Operational cleanup/query scripts should not remain discoverable test modules. Their long-term location and safety interface require a separate approved task.

### Level 1 â€” Unit tests

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

### Level 2 â€” Component tests

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

### Level 3 â€” Integration tests using fakes or mocks

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

### Level 4 â€” Explicit opt-in external integration tests

**Scope:** Compatibility checks against installed QGIS/GDAL/PDAL and a dedicated non-production WebODM environment.

Requirements:

- Never included in normal discovery/default CI.
- Require an explicit marker/command and an affirmative environment gate such as a dedicated test-environment identifier.
- Refuse to run when paths overlap configured production roots or when production credentials are detected.
- Use a dedicated WebODM project/tenant and unique test task names.
- Use bounded datasets and clean only artifacts carrying test ownership metadata.
- Report created remote resources so operators can clean them safely.

These tests validate compatibility, not every error path.

### Level 5 â€” Small-dataset pipeline smoke tests

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
- Cross-process file-handler behavior and subprocess-output attribution under future worker execution.
- Pause, resume, abort, cancellation, and child-process cleanup.
- Upload descriptor/memory bounds and resource cleanup.
- Filesystem scan/copy performance and interruption.
- Tile generation command construction, resume, limits, and partial outputs.
- Quality-gate approval, rejection, restart, timeout, and non-interactive behavior.
- Map-export compatibility with refactored paths/state.
- Multi-worker scheduling, leases, duplicate delivery, and crash recovery.



## Phase 3 artifact workspace coverage

Added on 2026-07-20, `tests/test_phase3_artifact_workspace.py` covers the first ADR-003 implementation slice.

Coverage proves:

- two run IDs for the same survey receive distinct workspace roots and mutable working directories;
- unsafe run IDs and escaping published artifact paths are rejected;
- published survey path planning preserves the legacy `rgb/` layout expected by existing consumers;
- a complete staged publish set is copied under the run workspace and receives a manifest only after artifacts are staged;
- staged publication does not modify the existing published survey tree; and
- injected copy failure leaves the published tree untouched and does not write a complete publication manifest.

This remains helper-level coverage. It does not run the real pipeline, copy real survey data, contact WebODM, execute QGIS/GDAL, change the database schema, activate a publish set, or alter map export behavior.



## Phase 3 RGBPipeline workspace seam coverage

Added on 2026-07-20, construction and single-stage tests cover the RGBPipeline seam for ADR-003 artifact ownership.

Coverage proves:

- default construction computes a run workspace layout under `<base_dir>/data/workspaces/<run_id>` without creating it;
- injected workspace and published layouts are preserved;
- ambiguous `workspace_root` and `workspace_layout` inputs are rejected;
- default construction keeps `published_layout` unset before a survey path is known;
- data segregation success derives `published_layout` from the actual returned `survey_path`; and
- resume-state hydration derives `published_layout` from the actual stored `survey_path`, including layouts without a year subdirectory.

This is seam coverage only. It does not migrate a stage to the run workspace, create workspace directories during construction, activate a publish set, run the real pipeline, contact WebODM, execute QGIS/GDAL, or touch production storage.



## Phase 3 data segregation workspace metadata coverage

Added on 2026-07-20, the hermetic RGBPipeline single-stage tests cover data segregation workspace metadata.

Coverage proves:

- successful data segregation creates the run-owned workspace directories;
- successful data segregation persists additive `workspace` and `published` metadata in stage output;
- existing top-level `survey_id`, `survey_path`, and fake dependency output remain backward-compatible;
- the published layout still derives from the actual returned `survey_path`;
- failed data segregation prepares the workspace but records no successful stage output; and
- all workspace, survey, checkpoint, control, and SQLite paths remain under pytest-owned temporary roots.

This is still not a full stage migration. The real data segregation implementation continues to populate the legacy survey tree, and no publish activation, map export manifest preference, WebODM, QGIS/GDAL, or network behavior is exercised.

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

The tests use temporary log paths, temporary RGBPipeline construction, fake WebODM, and explicit logger cleanup. They do not run pipeline stages or external tools. Explicit threaded/interleaved logging coverage is intentionally deferred until concurrency becomes part of the production execution design; adding it now would duplicate isolation guarantees already covered without matching a current runtime path. Direct `rgb.*` logger bypasses have been inventoried: `rgb.source_resolver` and `rgb.map_export` are outside the six owned RGBPipeline run loggers and do not currently need isolation fixes, but they remain candidates for future run-context observability coverage. ADR-013 defines the next logging test target: parseable `event=<name> key=value ...` lifecycle records inside the existing text log format.
## Phase 2 observability contract test target

ADR-013 keeps the existing `time | level | logger | run_id | stage | message` log format and standardizes parseable lifecycle messages as `event=<name> key=value ...` records.

Future Phase 2 implementation tests should verify:

- generated parseable events retain the logical logger name, run ID column, and stage column;
- required fields are present for run, stage, retry, failure, and external-boundary events;
- failure records bound error messages and preserve exception type information;
- human-readable banners can coexist with parseable lifecycle events; and
- `query_survey_stats.py` can read new explicit events while retaining compatibility with historical free-form logs.

These tests must continue to use pytest-owned temporary log files and fake external boundaries. No database schema or persistent event journal is expected during Phase 2.
## StageRunner lifecycle event coverage

Added on 2026-07-17, StageRunner orchestration coverage now verifies the first ADR-013 parseable lifecycle events. The focused test uses a temporary owned log file and a real `StageRunner` over temporary SQLite to exercise retry, success, skip, and saved-output loading.

Coverage proves that generated StageRunner lifecycle events:

- preserve the existing log columns and logical logger name;
- carry the run ID in the existing `run_id` column;
- carry the stage name in the existing `stage` column;
- emit parseable `event=stage_started`, `event=stage_retrying`, `event=stage_completed`, `event=stage_skipped`, and `event=stage_output_loaded` messages; and
- quote error messages containing spaces while preserving `error_type`, attempt count, max attempts, and retry delay.

This remains file-log based and does not introduce JSON logs, database events, migrations, external services, or threaded/concurrent execution tests.
## RGBPipeline run lifecycle event coverage

Added on 2026-07-17, the hermetic RGBPipeline single-stage tests now verify ADR-013 run-level parseable lifecycle events in a pytest-owned temporary pipeline log file.

Coverage proves that generated RGBPipeline run events:

- preserve the existing log columns and logical pipeline logger name;
- carry the run ID in the existing `run_id` column and leave the stage column empty for run-scoped events;
- emit `event=run_started` on both success and failure paths;
- emit `event=run_completed` with elapsed time and survey ID on the successful selected-stage path; and
- emit `event=run_failed` with exception type and quoted error message on the controlled failure path.

This remains file-log based and does not introduce JSON logs, database events, migrations, external services, or threaded/concurrent execution tests.
## WebODM boundary event coverage

Added on 2026-07-17, the hermetic RGBPipeline WebODM-stage test verifies the first ADR-013 parseable external-boundary events for WebODM.

Coverage proves that generated WebODM boundary events:

- preserve the existing log columns and logical WebODM logger name;
- carry the run ID in the existing `run_id` column;
- emit `event=webodm_project_created` with project ID/name for newly created projects;
- emit `event=webodm_task_created` with project ID, task key, task ID, and task name for primary Task 2 creation; and
- emit `event=webodm_task_status` with project ID, task key, task ID, status, success flag, and elapsed seconds after waiting for primary Task 2.

The test uses `FakeWebODM`, fake upload-cache behavior, pytest-owned paths, and a temporary log file. It does not contact WebODM, upload images, download assets, run QGIS/GDAL, or execute the full pipeline.
## QGIS command-boundary event coverage

Added on 2026-07-17, `tests/test_qgis_tools_observability.py` verifies ADR-013 parseable external-boundary events for QGIS/GDAL command execution.

Coverage proves that generated QGIS command-boundary events:

- preserve the existing log columns and logical QGIS logger name;
- carry the run ID in the existing `run_id` column;
- emit `event=qgis_command_started` with tool name, executable basename, and argument count before command execution;
- emit `event=qgis_command_completed` with tool name, executable basename, elapsed seconds, and return code after successful command execution;
- emit `event=qgis_command_failed` with tool name, executable basename, elapsed seconds, exception type, sanitized error message, and return code when a command fails; and
- avoid logging full input/output paths in the parseable failure event.

The tests patch the `modules.qgis.qgis_tools.subprocess.run` boundary with fakes, use pytest-owned temporary inputs, outputs, and log files, and do not run real QGIS, GDAL, shell commands, or `stage_qgis()`.
## Explicit event parser/reporting coverage

Added on 2026-07-17, `tests/test_logging_context_ownership.py` verifies parser/reporting support for ADR-013 explicit event records.

Coverage proves that:

- historical free-form log lines remain parseable with the existing columns and raw message field;
- generated `log_event()` messages round-trip through `parse_log_events()` as `event` plus parsed `fields`;
- quoted event field values with spaces are preserved;
- non-event messages return `event=None` and empty fields;
- `extract_log_insights()` recognizes explicit `run_paused` records for pause timelines;
- explicit `qgis_command_completed` records can populate QGIS clip timing and task attribution; and
- explicit `qgis_command_failed` records populate reportable stage errors without depending on historical free-form error text; and
- the survey summary can surface the total number of explicit events seen in logs.

This remains compatible with historical regex fallbacks and does not introduce JSON logs, a database event journal, migrations, external services, or real QGIS/GDAL execution.

## Phase 2 completion coverage

Added on 2026-07-20, Phase 2 logging and observability coverage now exercises the acceptance criteria that were previously deferred.

New coverage proves:

- owned loggers can be closed through `shared.logging.close_logger()`, releasing handlers, clearing filters, and allowing same-identity reconfiguration;
- two owned loggers writing from interleaved threads keep separate handler destinations, run IDs, stage names, and parseable event records;
- `RGBPipeline.run()` emits `run_paused` when a pause control signal is observed before a stage;
- `RGBPipeline.run()` emits `run_aborted` when an abort control signal is observed before a stage;
- WebODM UI cancellation translated by `StageRunner` records a failed WebODM stage and emits `run_canceled`; and
- the default safe test suite now collects 63 tests and passes 63/63.

This closes the Phase 2 acceptance gap for in-process threaded logger context, handler lifecycle, parser compatibility, and run-control observability. Remaining coverage belongs to later phases: cross-process worker logging, subprocess-output attribution, richer WebODM branch events, QGIS branch/artifact events, and typed control-state semantics.

Validation: changed Python files compiled; focused logging tests passed 8/8; focused RGBPipeline single-stage/run-event tests passed 6/6; collection found 63 tests; the full default suite passed 63/63; `git diff --check` passed with only LF-to-CRLF working-tree warnings. No external operation occurred.

## Phase 3 cross-run filter workspace coverage

Added on 2026-07-20, the hermetic RGBPipeline single-stage tests cover the cross-run filter workspace migration.

Coverage proves that:

- the enabled filter receives `rgb/images/raw` as input but writes kept images into the run workspace;
- excluded filter outputs are kept under the run workspace sibling `images/cross-runs` directory;
- successful filter results are mirrored back to legacy `rgb/images/path` and `rgb/images/cross-runs` paths for compatibility;
- stale legacy image outputs are replaced only after workspace outputs exist;
- disabled filtering copies raw images through the workspace before mirroring to legacy output folders;
- existing crossrun flag and experiment-label semantics are preserved for enabled and disabled modes;
- successful stage output includes additive `workspace` and `published` path metadata while preserving legacy top-level output paths; and
- a controlled filter failure leaves pre-existing legacy output folders untouched and records no crossrun state.

The tests patch the filter boundary with fakes, use pytest-owned survey and workspace paths, and do not process EXIF, run the real filter algorithm, execute the full pipeline, contact WebODM, run QGIS/GDAL, access production storage, or delete real data.

## Phase 3 KML boundary workspace coverage

Added on 2026-07-20, the hermetic RGBPipeline single-stage tests cover the KML boundary workspace migration.

Coverage proves that:

- the KML stage reads KML/KMZ inputs from the legacy `rgb/boundary` folder;
- derived GeoJSON and CSV outputs are first written under the run workspace `boundary` directory;
- successful derived files are mirrored back to legacy `rgb/boundary` paths for WebODM, QGIS, map export, and operator compatibility;
- successful stage output preserves legacy top-level `processed_files`, `geojson_dir`, `csv_dir`, `boundary_available`, and `boundary_geojson_path` values;
- successful stage output includes additive `workspace` and `published` path metadata;
- no-valid-boundary output keeps the existing fallback semantics while reporting workspace/published metadata; and
- a controlled KML processing failure leaves pre-existing legacy GeoJSON and CSV files untouched and records no boundary state.

The tests patch the KML boundary with fakes, use pytest-owned survey and workspace paths, and do not parse real KML, execute the full pipeline, contact WebODM, run QGIS/GDAL, access production storage, or delete real data.

## Phase 3 WebODM orthomosaic workspace coverage

Added on 2026-07-20, the hermetic RGBPipeline single-stage tests cover the WebODM orthomosaic workspace migration.

Coverage proves that:

- Task 2 orthomosaic export receives a task-specific run workspace output directory;
- fallback/Task 4 orthomosaic export receives a task-specific run workspace output directory;
- successful orthomosaic exports are mirrored back to legacy `rgb/ortho` paths for QGIS, quality-gate, map export, and operator compatibility;
- successful WebODM output preserves legacy `downloads.<task>.orthomosaic` and `selected_orthomosaic.source_path` values;
- successful WebODM output includes additive `workspace.webodm_ortho` and `published.webodm_ortho` metadata;
- a controlled orthomosaic export failure leaves pre-existing legacy orthomosaic files untouched; and
- partial workspace orthomosaic files remain available as diagnostic evidence after a controlled failure.

The tests use a fake WebODM processor, pytest-owned survey/workspace paths, fake upload-cache behavior, and orthomosaic-only export configuration. They do not contact WebODM, run QGIS/GDAL, execute the real pipeline, access production storage, or delete real data.
## Phase 3 QGIS workspace coverage

Date: 2026-07-20.

QGIS workspace migration coverage remains in `tests/test_rgb_pipeline_single_stage_execution.py` and uses a fake `QGISTools` boundary. The fake records clip/tile calls and writes tiny text/tile placeholders under pytest-owned temporary paths. It does not execute QGIS, GDAL, `gdalwarp`, `gdal2tiles`, `gdalinfo`, subprocesses, network shares, or real survey roots.

Covered behavior:

- bounded QGIS clips write to `workspace/qgis/clipped/ortho` first and then mirror to legacy `rgb/qgis/clipped/ortho`;
- tile generation writes to `workspace/qgis/tiles/<mode>` first and then mirrors to legacy `rgb/tiles/ortho/<mode>`;
- returned QGIS outputs preserve legacy-compatible `clip.output`, `tiles.output_dir`, `selected_orthomosaic.clipped_path`, and `selected_orthomosaic.tiles_dir`;
- additive `workspace` and `published` metadata reports both ownership layers; and
- controlled clip failure leaves existing legacy clipped/tile outputs untouched while preserving partial workspace evidence.

Validation added or rerun for this slice:

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py`
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k qgis`
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py`
- `python -m pytest --collect-only -q`
- `python -m pytest -q`
- `git diff --check`

The tests intentionally do not validate real GDAL/QGIS command-line behavior, local/network throughput, or Windows SMB behavior. Those remain external/operator concerns outside the safe default test suite.
## Phase 3 WebODM pointcloud and all-assets ZIP workspace coverage

Date: 2026-07-20.

WebODM remaining-export coverage stays in `tests/test_rgb_pipeline_single_stage_execution.py` and uses a fake WebODM processor. The fake records pointcloud and all-assets ZIP calls and writes tiny placeholder files under pytest-owned temporary paths. It does not contact WebODM, upload images, download real assets, run QGIS/GDAL, convert real point clouds, execute subprocesses, access network shares, or touch real survey roots.

Covered behavior:

- Task 2 pointcloud export receives `workspace/webodm/3d/task2` as its output directory;
- successful pointcloud LAZ/PCD outputs are mirrored back to legacy `rgb/3d`;
- Task 2 all-assets ZIP download receives `workspace/webodm/odm/task2/<name>.zip` as its output path;
- successful all-assets ZIP output is mirrored back to legacy `rgb/odm`;
- returned WebODM downloads preserve legacy-compatible pointcloud and ZIP paths;
- additive `workspace` and `published` metadata reports both ownership layers; and
- controlled pointcloud failure leaves existing legacy pointcloud files untouched while preserving partial workspace evidence.

Validation added or rerun for this slice:

- `python -m py_compile shared\artifacts.py pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py`
- `python -m pytest -q tests\test_phase3_artifact_workspace.py tests\test_rgb_pipeline_single_stage_execution.py -k "webodm_task2_remaining or webodm_pointcloud_failure"`
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py`
- `python -m pytest --collect-only -q`
- `python -m pytest -q`
- `git diff --check`

DEM downloads remain intentionally untested and unmigrated because production currently sets `dem_do_download = False`; activating that path should be a separate behavior decision with its own fake GDAL/WebODM coverage.

## Phase 3 map-export publication-manifest compatibility coverage

Date: 2026-07-20.

Map-export manifest compatibility coverage lives in `tests/test_map_export_manifest_resolution.py` and uses only pytest-owned survey roots and tiny placeholder files. It does not run `map.py`, QGIS print export, GDAL/QGIS, the real RGB pipeline, WebODM, network shares, production SQLite, or real survey roots.

Covered behavior:

- clipped orthomosaic lookup prefers an existing `publication.json` artifact path over legacy glob candidates;
- clipped orthomosaic lookup falls back to the legacy survey-tree scan when no publication manifest exists;
- clipped orthomosaic lookup falls back to the legacy survey-tree scan when the manifest-listed artifact is missing;
- boundary lookup prefers an existing publication-manifest KML/KMZ artifact before the legacy manifest `kml_file` path; and
- publication artifact paths are resolved inside the published `rgb` root before they are considered usable.

Validation added or rerun for this slice:

- `python -m py_compile modules/map_export/survey_manifest.py modules/map_export/orthomosaic_finder.py modules/map_export/boundary_finder.py tests/test_map_export_manifest_resolution.py`
- `python -m pytest -q tests/test_map_export_manifest_resolution.py`
- `python -m pytest -q tests/test_phase3_artifact_workspace.py tests/test_rgb_pipeline_construction.py tests/test_rgb_pipeline_single_stage_execution.py tests/test_map_export_manifest_resolution.py`

Publish activation, real map package export, print layout generation, large raster copying, and network-share behavior remain outside the safe default unit coverage for this slice.

## Phase 3 file-only publish activation coverage

Date: 2026-07-20.

`tests/test_phase3_artifact_workspace.py` now covers the dormant `activate_publication()` helper using only pytest-owned temporary workspace and survey roots with tiny text placeholders.

Coverage proves that:

- a valid staged file replaces its legacy-compatible target and the published manifest is written with `status: published`;
- the published manifest switch is the final replacement and the prior manifest is retained at a run-specific recovery path;
- the replaced file remains available at a run-specific `.previous` recovery path;
- copy failure before activation leaves all previous published files and the previous manifest unchanged;
- an injected failure during multi-file replacement rolls already changed files back to their previous contents;
- directory artifacts are rejected before the published root is created;
- a staged file whose size no longer matches its manifest record is rejected before publication; and
- a tampered manifest path that escapes the published root is rejected before publication.

Validation added or rerun for this slice:

- `python -m py_compile shared/artifacts.py tests/test_phase3_artifact_workspace.py`
- `python -m pytest -q tests/test_phase3_artifact_workspace.py` - 20 passed
- `python -m pytest -q tests/test_phase3_artifact_workspace.py tests/test_map_export_manifest_resolution.py tests/test_rgb_pipeline_construction.py tests/test_rgb_pipeline_single_stage_execution.py` - 50 passed
- `python -m pytest --collect-only -q` - 103 collected
- `python -m pytest -q` - 103 passed

The helper is not wired into `RGBPipeline`. Real survey publication, network-share behavior, large-file throughput, directory/tile activation, process-crash recovery, concurrent publisher locking, and backup retention remain outside the safe default suite.

## Phase 3 file publication recovery coverage

Date: 2026-07-20.

`tests/test_phase3_artifact_workspace.py` now covers retry and interrupted-attempt reconciliation for the dormant file-only activation helper using pytest-owned temporary paths and tiny text files.

Coverage proves that:

- calling activation again for an already published run performs no copy or replacement work;
- an interrupted temporary-file copy is detected, its run-specific temporary file is removed, and activation safely retries;
- a simulated process interruption after a published file was replaced but before `publication.json` switched restores the prior file and then completes a fresh activation;
- the recovered published manifest records `recovered_interrupted_activation: true`;
- a normal caught replacement failure rolls all changed files back and a subsequent activation succeeds; and
- recovery refuses to modify targets when the active manifest changed after the interrupted attempt, retaining the candidate and `.previous` evidence.

Validation added or rerun for this slice:

- `python -m py_compile shared/artifacts.py tests/test_phase3_artifact_workspace.py`
- `python -m pytest -q tests/test_phase3_artifact_workspace.py` - 24 passed
- `python -m pytest -q tests/test_phase3_artifact_workspace.py tests/test_map_export_manifest_resolution.py tests/test_rgb_pipeline_construction.py tests/test_rgb_pipeline_single_stage_execution.py` - 54 passed
- `python -m pytest --collect-only -q` - 107 collected
- `python -m pytest -q` - 107 passed
- `git diff --check`

This coverage assumes a single publisher. It does not exercise live `RGBPipeline` publication, concurrent same-survey publishers, process or host termination, network shares, large files, content hashing, directory/tile activation, or backup retention.

## Phase 3 publication ownership coverage

Date: 2026-07-20.

`tests/test_publication_lock.py` covers the dormant same-survey publication lock using only pytest-owned temporary roots with an explicit ownership sentinel.

Coverage proves that:

- acquisition writes a parseable ownership record and release removes it;
- two concurrent contenders for one published root produce exactly one owner and one fail-closed conflict;
- a caller with the wrong owner token cannot release another owner's lock;
- malformed existing lock evidence blocks acquisition and remains unchanged;
- replacing a lock with another valid owner's record prevents the old handle from deleting it; and
- filesystem roots are rejected as publication roots.

Validation added for this slice:

- `python -m py_compile shared/publication_lock.py tests/test_publication_lock.py`
- `python -m pytest -q tests/test_publication_lock.py` - 6 passed
- `python -m pytest -q tests/test_publication_lock.py tests/test_phase3_artifact_workspace.py tests/test_map_export_manifest_resolution.py tests/test_rgb_pipeline_construction.py tests/test_rgb_pipeline_single_stage_execution.py` - 60 passed
- `python -m pytest --collect-only -q` - 113 collected
- `python -m pytest -q` - 113 passed

This is hermetic helper-level coverage. It does not wire lock ownership into `activate_publication()` or `RGBPipeline`, terminate a real process, exercise stale-lock operator recovery, access network shares, validate Windows SMB atomicity, run the real pipeline, contact WebODM, execute QGIS/GDAL, or touch production storage.
## Phase 3 lock-owned activation coverage

Date: 2026-07-27.

`tests/test_phase3_artifact_workspace.py` now covers the dormant `activate_publication_with_lock()` coordinator using only pytest-owned temporary workspace and survey roots with tiny placeholder files.

Coverage proves that:

- successful lock-owned activation publishes the file artifact and releases `.publication.lock`;
- activation failure releases `.publication.lock` while leaving the previous published artifact unchanged; and
- an existing owner blocks activation before any copy or replace operation starts.

Validation added for this slice:

- `python -m py_compile shared\artifacts.py tests\test_phase3_artifact_workspace.py`
- `python -m pytest -q tests\test_phase3_artifact_workspace.py -k "publication_with_lock"` - 3 passed

This is hermetic helper-level coverage. It does not wire publication into `RGBPipeline`, run the real pipeline, contact WebODM, execute QGIS/GDAL, access network shares, validate Windows SMB lock behavior, activate directory/tile artifacts, or touch production storage.

## Phase 3 zero-copy directory activation coverage

Date: 2026-07-27.

`tests/test_phase3_artifact_workspace.py` now covers the dormant directory/tile activation primitive using only pytest-owned paths and tiny directory trees.

Coverage proves that:

- an external workspace-staged directory is copied once into the exact hidden activation path and activated;
- `prepare_publication()` records an exact `.activation/<run-id>/<name>.tmp` source without invoking `copytree` when generation metrics are supplied, and activation also avoids `copytree`;
- a merely hidden-looking path does not qualify for zero-copy mode;
- existing final directories are retained under the run-specific `.previous` path;
- copy, manifest validation, final-to-backup rename, and temp-to-final rename failures do not commit `publication.json`;
- a temp-to-final failure restores the previous final directory;
- journal transitions occur in order and `publication.json` is written after the `activated` state but before `committed`;
- an independent directory recount runs exactly once and post-rename validation remains lightweight;
- rename retries are bounded and use injected backoff;
- symlink/reparse-point directory entries are rejected;
- process-interruption simulations retain unambiguous `previous_moved` or `activated` evidence without falsely claiming commitment; and
- all existing file activation, recovery, and lock-composition tests continue to pass.

Validation for this slice:

- `python -m py_compile shared\artifacts.py tests\test_phase3_artifact_workspace.py`
- `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 39 passed
- `python -m pytest -q tests\test_phase3_artifact_workspace.py tests\test_publication_lock.py tests\test_map_export_manifest_resolution.py tests\test_rgb_pipeline_construction.py tests\test_rgb_pipeline_single_stage_execution.py` - 75 passed
- `python -m pytest --collect-only -q` - 128 collected
- `python -m pytest -q` - 128 passed

The suite does not generate real tiles, invoke QGIS/GDAL, access network shares, exercise SMB semantics, scan a million-file fixture, wire `RGBPipeline`, perform automatic restart recovery, clean old backups, or touch production storage.

## Phase 3 directory restart reconciliation coverage

Date: 2026-07-27.

`tests/test_phase3_artifact_workspace.py` now covers dormant directory activation restart reconciliation using only pytest-owned roots, tiny directory trees, injected filesystem operations, and simulated `BaseException` process interruptions.

Coverage proves that:

- valid `prepared` evidence retains the validated candidate and untouched prior final without a rename;
- a backup move that completed before the `prepared` journal advanced is detected and restored;
- `previous_moved` is rolled back when the candidate is still temporary;
- `previous_moved` is also rolled back when the candidate rename reached the final path before the journal advanced;
- `activated` is rolled back to the publication named by the older `publication.json`;
- first activation with no prior directory returns to no published final;
- a current-run `publication.json` finalizes an `activated` journal as committed without moving or rescanning the directory;
- committed and rolled-back reconciliation are idempotent;
- changed active publication, tampered journal paths, and missing previous backups fail closed without moving evidence; and
- injected reconciliation rename failure preserves the original activation status and records explicit retryable failure details.

Validation for this slice:

- `python -m py_compile shared\artifacts.py tests\test_phase3_artifact_workspace.py`
- `python -m pytest -q tests\test_phase3_artifact_workspace.py -k reconciliation` - 12 passed, 39 deselected
- `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 51 passed
- `python -m pytest -q tests\test_phase3_artifact_workspace.py tests\test_publication_lock.py tests\test_map_export_manifest_resolution.py tests\test_rgb_pipeline_construction.py tests\test_rgb_pipeline_single_stage_execution.py` - 87 passed
- `python -m pytest --collect-only -q` - 140 collected
- `python -m pytest -q` - 140 passed

The suite does not terminate a real process, recover a real stale lock, generate tiles, invoke QGIS/GDAL, access SMB/network shares, scan million-file fixtures, wire `RGBPipeline`, delete backups, or touch production storage.

## Phase 3 stale publication-lock recovery coverage

Date: 2026-07-27.

`tests/test_publication_lock_recovery.py` covers the dormant diagnosis helper, explicit recovery helper, and operator CLI using only pytest-owned temporary roots and injected filesystem races.

Coverage proves that diagnosis is read-only; age is diagnostic only; invalid timestamps never produce trusted age; exact confirmation and a non-empty reason are mandatory; changed evidence fails closed; valid and malformed evidence is archived byte-for-byte; audit records omit the raw owner token; recovery-ID collisions retain the active lock; a mutation during quarantine is detected and restored; missing and linked lock paths are rejected; and the CLI requires both the diagnosed digest and explicit `--allow-recovery` acknowledgement.

The suite does not determine real process liveness, terminate processes, recover production locks, access network shares, validate SMB cache/rename semantics, invoke QGIS/GDAL, contact WebODM, or run the pipeline.

Validation for this slice:

- `python -m py_compile shared\publication_lock.py tools\publication_lock_recovery.py tests\test_publication_lock.py tests\test_publication_lock_recovery.py`
- `python -m pytest tests\test_publication_lock.py tests\test_publication_lock_recovery.py -q` - 19 passed
- relevant Phase 3 and pipeline tests - 100 passed
- collection-only - 153 collected
- full safe default suite - 153 passed
- `git diff --check` - passed
## Phase 3 publication-protocol acceptance coverage

Date: 2026-07-27.

The acceptance review adds a focused regression proving that a staged mixed file/directory set is rejected before any published artifact or active manifest changes. This preserves the known composition boundary while ADR-018 remains pending and prevents a future live call site from silently treating a partial subset as a complete publication.

Validation for the acceptance gate:

- compile checks for publication helpers, operator tool, and tests - passed
- focused mixed-set/lock/reconciliation selection - 16 passed, 36 deselected
- relevant publication and pipeline suite - 101 passed
- collection-only - 154 collected
- full safe default suite - 154 passed
- `git diff --check` - passed

Distinct-volume, Windows SMB, real large-tree, live pipeline, QGIS/GDAL, WebODM, and production-storage validation remain excluded and must be explicitly authorized in a controlled environment.
## Phase 3 mixed publication-set activation coverage

Date: 2026-07-27.

`tests/test_phase3_artifact_workspace.py` now covers the dormant `activate_publication_set_with_lock()` coordinator using only pytest-owned roots, tiny files, tiny directory trees, injected copy/replace callables, and the existing publication lock helper.

Coverage proves that:

- one lock-owned activation can publish a mixed file plus directory/tile artifact set and commit one authoritative `publication.json`;
- the set-level journal reaches `committed` only after the manifest switch;
- a caught failure during the final manifest switch rolls both file and directory targets back to the previous visible publication;
- repeated activation for an already committed mixed set is idempotent and performs no copy/replace work; and
- an existing publication owner blocks mixed activation before any artifact mutation starts.

The original `activate_publication()` mixed-set rejection remains covered so older primitive contracts cannot silently expand.

Validation for this slice:

- `python -m py_compile shared\artifacts.py tests\test_phase3_artifact_workspace.py` - passed
- `python -m pytest -q tests\test_phase3_artifact_workspace.py -k "publication_set"` - 4 passed, 52 deselected
- `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 56 passed

This coverage does not run the real pipeline, generate real tiles, invoke QGIS/GDAL, contact WebODM, access SMB/network shares, validate real Windows rename behavior, reconcile an interrupted mixed-set journal, clean backups, or touch production storage.
## Phase 3 mixed publication-set reconciliation coverage

Date: 2026-07-27.

`tests/test_phase3_artifact_workspace.py` now covers the dormant `reconcile_publication_set()` helper using pytest-owned roots, tiny file/directory artifacts, injected replace behavior, and simulated `BaseException` process interruptions.

Coverage proves that:

- a prepared mixed-set journal can roll back a partial artifact activation when `publication.json` still names the previous run;
- an activated mixed-set journal can roll back all visible candidates when the authoritative manifest did not switch;
- an activated mixed-set journal is finalized as committed when `publication.json` already names the current run;
- changed active manifests fail closed without moving evidence; and
- the existing mixed activation, lock ownership, and old mixed-set rejection tests continue to pass.

Validation for this slice:

- `python -m py_compile shared\artifacts.py tests\test_phase3_artifact_workspace.py` - passed
- `python -m pytest -q tests\test_phase3_artifact_workspace.py -k "publication_set"` - 8 passed, 52 deselected
- `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 60 passed

This coverage does not terminate a real process, recover a real stale lock, generate real tiles, invoke QGIS/GDAL, contact WebODM, access SMB/network shares, validate real Windows rename semantics, clean backups, or touch production storage.
## Phase 3 artifact cleanup planner coverage

Date: 2026-07-27.

`tests/test_phase3_artifact_workspace.py` now covers the dormant `plan_artifact_cleanup()` helper using only pytest-owned published roots, activation journals, previous-artifact placeholders, and workspace directories.

Coverage proves that:

- terminal unprotected publication evidence produces read-only cleanup candidates for activation directories, previous manifests, previous file artifacts, previous directory artifacts, and old workspaces;
- active and caller-preserved run IDs are protected;
- an existing `.publication.lock` blocks planning before candidates are returned;
- non-terminal evidence produces blocked reasons and no candidates;
- mixed terminal and non-terminal evidence in the same run directory fails closed without candidates; and
- minimum-age gating suppresses otherwise valid candidates until they are old enough.

Validation for this slice:

- `python -m py_compile shared\artifacts.py tests\test_phase3_artifact_workspace.py` - passed
- `python -m pytest -q tests\test_phase3_artifact_workspace.py -k "cleanup"` - 5 passed, 60 deselected

This coverage does not delete files, run a cleanup executor, access network shares, validate SMB behavior, scan million-file tile trees, run the real pipeline, invoke QGIS/GDAL, contact WebODM, touch production storage, or change retention defaults.
## Phase 3 artifact cleanup executor coverage

Date: 2026-07-27.

`tests/test_phase3_artifact_workspace.py` now covers the dormant `execute_artifact_cleanup()` helper and `tools/artifact_cleanup.py` CLI using only pytest-owned roots, explicit sentinel files, tiny previous-artifact placeholders, and temporary workspace directories.

Coverage proves that:

- executor dry-run mode deletes nothing and requires no sentinel;
- deletion is blocked when owner-root sentinels are missing;
- a fresh `.publication.lock` or changed fresh plan blocks execution after review;
- approved deletion removes only planner-approved file/directory candidates under sentinel-owned roots;
- audit JSON records candidates, deleted entries, and per-candidate attempts; and
- the operator CLI plans read-only, rejects `execute` without `--allow-delete`, and executes only after sentinels plus acknowledgement are present.

Validation for this slice:

- `python -m py_compile shared\artifacts.py tools\artifact_cleanup.py tests\test_phase3_artifact_workspace.py` - passed
- `python -m pytest -q tests\test_phase3_artifact_workspace.py -k "cleanup"` - 10 passed, 60 deselected
- `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 70 passed

This coverage deletes only pytest-owned temporary files/directories. It does not clean production artifacts, access network shares, validate SMB behavior, scan million-file tile trees, run the real pipeline, invoke QGIS/GDAL, contact WebODM, touch production storage, or change live cleanup defaults.
## Phase 3 controlled filesystem validation coverage

Date: 2026-07-27.

`tests/test_filesystem_validation.py` covers the controlled validation helper and CLI using only pytest-owned temporary roots with `.filesystem-validation-root` sentinels.

Coverage proves that:

- validation refuses to run without explicit destructive acknowledgement;
- validation refuses roots missing the sentinel;
- disposable validation runs cover exclusive create, file replace, directory rename, and JSON read-after-write checks;
- disposable run directories are removed by default and can be kept for inspection;
- reports are written only under the validation root; and
- the CLI emits JSON, requires `--allow-destructive-validation`, and can write a report.

Validation for this slice:

- `python -m py_compile shared\filesystem_validation.py tools\filesystem_validation.py tests\test_filesystem_validation.py` - passed
- `python -m pytest -q tests\test_filesystem_validation.py` - 5 passed

This coverage mutates only pytest-owned temporary directories. It does not access network shares, validate SMB behavior, run cross-volume checks, scan million-file tile trees, run the real pipeline, invoke QGIS/GDAL, contact WebODM, touch production storage, or change live publication defaults.
## Phase 3 manual filesystem validation evidence

Date: 2026-07-27.

Manual opt-in validation was run outside the default pytest suite using the ADR-021 disposable-root protocol.

Validated disposable targets:

- Local: `.tmp/filesystem-validation/local-001` - passed `exclusive_create`, `file_replace`, `directory_rename`, and `json_visibility`.
- SMB: `Z:\__pipeline_validation\filesystem-validation-001` / `\\192.168.10.5\Visualization\__pipeline_validation\filesystem-validation-001` - passed `exclusive_create`, `file_replace`, `directory_rename`, and `json_visibility`.

The SMB report is retained at `Z:\__pipeline_validation\filesystem-validation-001\reports\smb-001.json`. The validation run directory was removed by the tool; the sentinel and report remain.

This manual evidence is not part of the safe default suite. It did not run the real pipeline, access `Z:\surveys`, invoke QGIS/GDAL, contact WebODM, or touch production survey outputs. Large-tree, open-handle, disconnect/reconnect, and cross-volume behavior remain manual validation gaps.

## Phase 3 large-tree SMB filesystem validation evidence

Date: 2026-07-27.

The controlled filesystem validator now includes an opt-in `large_tree_rename` check, covered by `tests/test_filesystem_validation.py`, plus CLI coverage for `--large-tree-files`. The safe default suite still uses pytest-owned temporary roots and tiny counts only.

Manual opt-in validation was run against the disposable SMB root `Z:\__pipeline_validation\filesystem-validation-001` with `--large-tree-files 1000`. The result passed all checks, observed 1,000 files after rename, recorded create/rename/count timings, wrote `reports\smb-large-tree-001.json`, and removed its disposable run directory. No production survey path, real pipeline, QGIS/GDAL, WebODM, or `Z:\surveys` data was touched.

This manual evidence is intentionally outside normal pytest. It is useful for accepting the Phase 3 filesystem primitive on the current SMB share at small representative scale, but it does not replace future validation for full tile counts, cross-volume behavior, open handles, interrupted operations, disconnect/reconnect behavior, or host-loss scenarios.

## CLI resume source-selection coverage

Date: 2026-07-30.

`tests/test_main_resume_source.py` covers the production CLI source-resolution helper using a pytest-owned temporary SQLite database and monkeypatched resolver behavior.

Coverage proves that:

- `--resume --run-id` reuses `runs.source_dir` and does not call ambiguous dataset resolution again;
- fresh runs still call `resolve_source_dataset_dir()` with the expected `FIELD_DATA_ROOT / survey` input and date hint; and
- resume fails closed when no matching run record exists.

The tests do not run the real RGB pipeline, load production `.env`, access field-data roots, contact WebODM, execute QGIS/GDAL, or open the production database.

## Phase 3 RGBPipeline publication dry-run planning coverage

Date: 2026-07-30.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers the opt-in `RGBPipeline.plan_publication_dry_run()` bridge with pytest-owned workspace and survey roots.

Coverage proves that:

- workspace-backed file and directory artifacts from KML, WebODM, and QGIS stage state are represented in one mixed publication plan;
- planned targets are expressed relative to the published survey root;
- activation remains disabled and no `publication.json` is written;
- existing legacy mirrored files/directories are not changed by planning; and
- non-workspace-owned sources make the plan fail closed with a blocked reason.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open the production database, or mutate production survey roots.

## Phase 3 RGBPipeline staged publication coverage

Date: 2026-07-30.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers the opt-in staged publication bridge with pytest-owned workspace and survey roots.

Coverage proves that:

- staged publication writes a manifest under the run workspace `publish/staged` directory;
- staged file and directory artifacts are copied from workspace-owned sources into the staging tree;
- the published survey `publication.json` is not written;
- existing legacy mirrored files and tile directories remain untouched; and
- blocked dry-run plans remain blocked and do not create a staged manifest.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open the production database, or mutate production survey roots.
## Phase 3 RGBPipeline explicit publication activation coverage

Date: 2026-07-30.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers the opt-in explicit activation bridge with pytest-owned workspace and survey roots.

Coverage proves that:

- activation requires the exact `PUBLISH <survey_id> <run_id>` confirmation phrase;
- missing staged publication manifests block before published outputs change;
- confirmed activation publishes a mixed file/directory set and writes the published survey `publication.json`;
- the publication lock is released after successful activation; and
- an existing publication lock blocks activation before visible artifacts change.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open the production database, recover stale locks, run cleanup, or mutate production survey roots.
## Phase 3 publication activation CLI coverage

Date: 2026-07-30.

`tests/test_phase3_artifact_workspace.py` now covers `tools/publication_activate.py` using only pytest-owned workspace and published roots.

Coverage proves that:

- activation refuses to run without `--allow-activation`;
- activation refuses an incorrect confirmation phrase before mutating published artifacts;
- activation validates and publishes an already staged mixed file/directory publication set; and
- the publication lock is released after successful CLI activation.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open production databases, recover stale locks, run cleanup, or touch production survey roots.

## Phase 3 RGBPipeline publication artifact allowlist coverage

Date: 2026-07-30.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers the RGBPipeline publication allowlist using only pytest-owned workspace and survey roots.

Coverage proves that dry-run planning and staging include only the intended final publication families, skip cross-run image directories and unknown sidecars, report skipped logical names through `skipped_artifacts`, preserve the existing blocked behavior for unsafe allowed sources, and avoid writing the published survey `publication.json` during staging.

Validation for this slice:

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "publication"` - 9 passed, 19 deselected.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open production databases, activate publication automatically, recover stale locks, run cleanup, or mutate production survey roots.

## Phase 3 directory/tile staging optimization coverage

Date: 2026-07-30.

`tests/test_phase3_artifact_workspace.py` and `tests/test_rgb_pipeline_single_stage_execution.py` now cover optimized directory publication staging using pytest-owned roots.

Coverage proves that `prepare_publication(stage_directories_for_activation=True)` stages a workspace directory directly at the exact hidden activation path, writes that path into the staged manifest, and allows activation to rename the prepared directory without invoking `copytree`. RGBPipeline staged publication now records QGIS tiles at that hidden activation path while preserving file staging under the run workspace and avoiding writes to the visible published `publication.json`.

Validation for this slice:

- `python -m py_compile shared\artifacts.py pipelines\rgb_pipeline.py tests\test_phase3_artifact_workspace.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_phase3_artifact_workspace.py -k "stage_directory or zero_copy"` - 3 passed, 70 deselected.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "publication"` - 9 passed, 19 deselected.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, activate publication automatically, clean hidden activation candidates, or mutate production survey roots.
## Phase 3 explicit Activate Publication stage boundary coverage

Date: 2026-08-03.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers the explicit `RGBPipeline.activate_publication_stage()` wrapper using pytest-owned workspace and published roots.

Coverage proves that:

- confirmed activation records one completed `activate_publication` stage and persists activation output JSON;
- confirmation mismatch records the activation stage as failed before visible publication changes;
- missing staged manifest records the activation stage as failed before visible publication changes; and
- an existing publication lock records the activation stage as failed before visible publication changes.

Validation for this slice:

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "activate_publication_stage"` - 4 passed, 28 deselected.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "publication"` - 13 passed, 19 deselected.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open production databases, recover stale locks, run cleanup, wire activation into `RGBPipeline.run()`, or mutate production survey roots. Persisted `requires_recovery` remains unimplemented pending a StageRunner/status-mapping slice.
## StageRunner requires_recovery status coverage

Date: 2026-08-03.

`tests/test_stage_runner_orchestration.py` now covers the generic `StageRequiresRecovery` signal. The regression proves that StageRunner persists `requires_recovery`, stores bounded output evidence in the latest stage row, updates in-memory state when an output key is supplied, and keeps `get_latest_stage_output()` completed-only for resume compatibility.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers post-mutation publication activation failure by monkeypatching the activation helper to mutate a pytest-owned published artifact, write recovery evidence, and raise. The activation stage persists `requires_recovery` with recovery output while leaving visible production resources untouched.

Validation for this slice:

- `python -m py_compile shared\db\repo.py shared\stage_runner.py pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py tests\test_stage_runner_orchestration.py` - passed.
- `python -m pytest -q tests\test_stage_runner_orchestration.py -k "requires_recovery"` - 1 passed, 9 deselected.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "activate_publication_stage"` - 5 passed, 28 deselected.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 10 passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "publication"` - 14 passed, 19 deselected.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open production databases, recover stale locks, run cleanup, wire activation into `RGBPipeline.run()`, or mutate production survey roots.
## Phase 3 guarded RGBPipeline run publication wiring coverage

Date: 2026-08-03.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers guarded `RGBPipeline.run()` publication wiring using monkeypatched safe stage callables, pytest-owned workspaces, and pytest-owned published roots.

Coverage proves that:

- when confirmation is supplied and `activate_publication` is selected, `run()` executes quality gate, QGIS, and one activation stage in order;
- the runtime activation stage stages publication and publishes the manifest/artifacts through the existing guarded helper;
- wrong confirmation records the activation stage as failed before visible publication changes; and
- activation retries are disabled for this non-idempotent boundary.

Validation for this slice:

- `python -m py_compile shared\db\repo.py shared\stage_runner.py pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py tests\test_stage_runner_orchestration.py` - passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "run_wires_activate_publication or run_activate_publication or activate_publication_stage"` - 7 passed, 28 deselected.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 10 passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "publication"` - 16 passed, 19 deselected.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open production databases, recover stale locks, run cleanup, or mutate production survey roots.
## Phase 3 guarded publication activation CLI coverage

Date: 2026-08-03.

`tests/test_main_publication_activation_cli.py` covers the operator CLI handoff into guarded `RGBPipeline.run()` using monkeypatched config/source resolution and a fake `RGBPipeline` module.

Coverage proves that:

- default CLI runs leave `publication_confirmation` unset;
- `--activate-publication --publication-confirmation "PUBLISH <survey_id> <run_id>"` passes the phrase into `pipeline.run()`;
- `--activate-publication` without a confirmation phrase fails before pipeline construction; and
- `--publication-confirmation` without `--activate-publication` also fails before pipeline construction.

Validation for this slice:

- `python -m py_compile main.py tests\test_main_publication_activation_cli.py` - passed.
- `python -m pytest -q tests\test_main_publication_activation_cli.py tests\test_main_resume_source.py` - 7 passed.

The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open production databases, recover stale locks, run cleanup, or mutate production survey roots.
## Phase 3 same-run legacy mirror recovery coverage

Date: 2026-08-12.

`tests/test_rgb_pipeline_single_stage_execution.py` now covers same-run stale legacy mirror reconciliation for both directory and file replacement helpers using only pytest-owned workspace and survey roots.

Coverage proves that:

- same-run stale directory temp paths are removed before a fresh workspace directory is copied into the legacy mirror target;
- same-run stale directory backup paths are restored when the visible target is missing before the new workspace directory replaces them;
- same-run stale file temp paths are removed before a fresh workspace file is copied into the legacy mirror target; and
- same-run stale file backup paths are restored when the visible target is missing before the new workspace file replaces them.

The relevant QGIS mirror tests continue to prove workspace outputs are mirrored to legacy paths while stale visible legacy content is replaced. The tests do not run the real RGB pipeline, contact WebODM, execute QGIS/GDAL, access network shares, open production databases, recover production stale paths, run cleanup, or mutate production survey roots.

Validation for this slice:

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k "legacy_directory_mirror or legacy_file_mirror or qgis_outputs_workspace_then_mirrors_legacy_paths"` - 6 passed, 37 deselected.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 43 passed.
- `git diff --check -- pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.

## Default suite compatibility restoration

Added on 2026-08-19, test imports and resolver expectations were aligned with the tracked module layout and current public helper signature. `tests/test_logging_context_ownership.py` imports parser helpers from `tools.query_survey_stats`; `tests/test_main_resume_source.py` passes the survey argument directly and no longer supplies removed `field_data_root` arguments.

Validation collected 229 tests without exclusions and passed the full default suite 229/229. Focused coverage passed 11/11, compilation passed, and scoped `git diff --check` passed. No external service, subprocess, production path, production database, or real pipeline was used.

## Deterministic WebODM Task 4 resume coverage

Added on 2026-08-28, `tests/test_webodm_resume_recovery.py` and the WebODM resume cases in `tests/test_rgb_pipeline_single_stage_execution.py` use temporary SQLite databases, temporary checkpoints/workspaces, and fake WebODM clients.

Coverage proves:

- WebODM raw status codes normalize to queued, running, failed, completed, and canceled correctly;
- project/task identity is durable before polling and survives a local pause;
- Task 1 and Task 2 also persist immediately and reuse their exact UUIDs without name lookup, implicit deletion, or duplicate upload;
- a fresh-process resume reattaches the exact UUID and a remotely completed task proceeds directly to export without another upload;
- failed, canceled, confirmed-missing, conflicting-name/project/task, and missing historical bindings fail closed without remote replacement;
- artifact export failure preserves the binding and leaves Task 4 retryable locally;
- the newest stage attempt controls resume and local pause finalizes active attempts as `paused`;
- checkpoints are atomic compatibility mirrors and the additive migration upgrades a legacy table repeatably;
- the repair workflow is read-only in dry-run, audited on apply, append-preserving, and idempotent for the same validated identity;
- `webodm_task4` is a valid force alias and unknown force targets are rejected before stage execution; and
- combined-mode ordering and existing successful Task 4 output compatibility remain covered.

Validation compiled every changed Python file, passed 100/100 relevant WebODM/orchestration/database tests, collected 260 tests, and passed the full safe default suite 260/260.

The default suite did not contact WebODM, run the real pipeline, upload or download imagery, execute QGIS/GDAL, open production SQLite, access production survey/network roots, register hotkeys, or perform destructive cleanup. Manual compatibility checks against a disposable non-production WebODM instance remain intentionally unexecuted and require explicit environment authorization.

## Storage preflight coverage

Added on 2026-09-02, storage tests use fake disk-usage values, pytest-owned JPEG
placeholders, temporary SQLite databases, fake pipeline construction, and
explicit zero reserves in stage tests that exercise copy behavior.

Coverage proves same-volume requirements are aggregated, reserves use the larger
absolute/percentage threshold, JPEG estimates are exact, capacity failure is
typed, SQLite-full recognition is specific, report-only/failing CLI paths never
construct the pipeline, workspace roots persist without rebind, legacy migrations
are repeatable, legacy resume remains read-only, and existing RGB stage behavior
remains compatible. QGIS staging has an explicit CLI root, reserves twice source
bytes at startup, and typed capacity failure cannot fall back to direct tiling.
The complete safe suite collects and passes 278/278.

## Resume-aware storage and legacy rebind coverage

Added on 2026-09-02, focused tests prove newest-attempt stage status is read
through SQLite read-only mode, a QGIS-only resume omits completed WebODM cache
writes, incomplete quality-gate work retains fallback upload capacity, and
state-only volumes use the absolute reserve. Rebind tests require the exact run
confirmation, preserve the old workspace, refuse conflicting persisted roots,
and refuse existing unowned targets. CLI coverage proves report-only rebind
passes the configured workspace and stage-aware flags without constructing the
pipeline. SQLite-full recognition is compatible with Python 3.10.

Changed Python compilation passed, focused storage/resume/CLI coverage passed
35/35, collection found 291 tests, and the full safe suite passed 291/291.
`git diff --check` passed with line-ending warnings only. No real pipeline,
external service, QGIS/GDAL process, production database, network share,
operator `.env`, legacy workspace mutation, or deletion was used.

## Split published-storage policy coverage

Added on 2026-09-02, configuration tests cover the 10 GiB/5% general defaults
and 10 GiB/0% published defaults. Storage tests prove an exact 5.55 GiB mirror
passes with 55 GiB free on a large volume, and that same-volume general writes
retain the stricter 5% reserve. Resume tests prove persisted surveys-root
routing; CLI tests prove published thresholds and pending-output estimates reach
startup preflight. RGBPipeline tests prove file/directory mirrors use the split
policy. StageRunner coverage proves a typed capacity failure executes once,
records failure, and preserves the exception.

Changed Python compilation and 117 focused tests passed. Collection found 297
tests and the full safe suite passed 297/297. No external service, subprocess,
production path/database, operator `.env`, network share, or destructive action
was used.

## Explicit empty-WebODM-project recovery coverage

Added on 2026-09-03, recovery tests use fake WebODM clients, mocked paginated
HTTP responses, temporary SQLite databases, and pytest-owned image/workspace
paths. Coverage proves exact CLI and programmatic confirmation, strict persisted
project/operation identity, zero-task verification across pages, additive
authorization history, immediate UUID persistence, and one normal Task 4
creation. Nonempty projects, existing UUIDs, mismatched IDs, malformed
responses, and authentication/network/lookup failures fail closed without
upload. Normal missing-UUID resume does not list tasks or create replacements.

Changed Python compilation passed, 25 focused recovery cases passed, the broader
WebODM/CLI/RGB regression set passed 120/120, collection found 322 tests, and the
full safe suite passed 322/322. No real pipeline, WebODM, QGIS/GDAL, production
database, survey data, network share, operator `.env`, or destructive operation
was accessed.

## DJI M3M ingestion coverage

Added on 2026-09-04, M3M tests use only pytest-owned field-data and survey
directories. Coverage proves exact case-insensitive UAV ancestor filtering,
future UAV folder names, date and prompt ordering, direct/resumed path
validation, arbitrary nested capture splits through `15of15`, strict
`*_D.JPG` selection, legacy JPEG compatibility, pre-copy collision refusal,
manifest audit counts, and consistent stage/storage/segregation selection.

Changed Python compilation passed, focused coverage passed 139/139, collection
found 337 tests, and the complete safe suite passed 337/337. No real pipeline,
field-data root, network share, production database, WebODM, QGIS/GDAL,
operator `.env`, or destructive operation was accessed.

## Task 4 all-assets ZIP coverage

Added on 2026-09-04, Task 4 ZIP tests use fake WebODM clients, fake disk usage,
pytest-owned workspaces, and pytest-owned published roots.

Coverage proves:

- enabled Task 4 downloads one all-assets ZIP to
  `workspace/webodm/odm/task4` and mirrors it to legacy `rgb/odm`;
- Task 4 download, workspace, and published metadata use additive stable keys;
- a failed optional download does not replace an existing published ZIP;
- publication planning accepts the exact Task 4 ZIP and continues rejecting
  unrelated ODM sidecars;
- startup preflight adds the ZIP estimate to workspace and published writes,
  and CLI wiring enables that estimate for default Task 4 but not Task 2-only;
- runtime performs the pre-download workspace capacity check and the existing
  actual-size published mirror check; and
- existing Task 2 and Task 4 orthomosaic behavior remains compatible.

Changed Python compilation passed, the complete focused storage/CLI/RGBPipeline
files passed 108/108, collection found 342 tests, and the full safe suite
passed 342/342. No real pipeline, WebODM, QGIS/GDAL, production database,
survey data, network share, operator `.env`, or destructive operation was
used.
