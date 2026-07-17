# Refactor Current Status

## Summary

- **Refactor status:** In progress; Phase 2 logging and observability started with ADR-002 accepted and first isolation fix implemented
- **Current phase:** Phase 2 - Logging and observability
- **Completed work:** Canonical pytest configuration, safe operator tools, reusable temporary test infrastructure, fake WebODM behavior, suite-wide default safety guards, StageRunner failure classification, hermetic RGBPipeline construction, and one hermetic RGBPipeline stage execution
- **Current task:** ADR-002 parser compatibility coverage after logger isolation
- **Production code changed:** Yes - shared/logging.py for ADR-002 logger isolation
- **Next recommended task:** Inventory direct logging.getLogger("rgb.*") callers and define the next observability contract without adding redundant concurrency tests

## Completed tasks

- Accepted pytest as the canonical default runner in `DECISIONS.md`.
- Added `pytest.ini` with `tests/` as the test path, an `external` marker, and external-test exclusion by default.
- Added pinned pytest 8.4.2 using the existing `requirements.txt` convention.
- Converted experiment naming samples into four parameterized tests covering both original inputs, filter enabled/disabled, and `-DJIFP` normalization.
- Moved the real RGB workflow from `tests/test_rgb_pipeline.py` to `tools/run_rgb_pipeline.py`; imports are lazy and execution requires `--allow-external-run`.
- Moved `tests/query_test.py` to `tools/query_pipeline_db.py`; the database is an explicit argument, missing files are rejected, SQLite opens in read-only URI mode, and connections close in `finally`.
- Moved `tests/reset_environment.py` to `tools/reset_environment.py`; an explicit root, sentinel, and destructive flag are required, containment is checked, and only `logs/`, `pipeline.db`, `pipeline.db-wal`, and `pipeline.db-shm` are removed.
- Added temporary SQLite and filesystem tests plus import traps for pipeline construction, dotenv, WebODM/network access, subprocesses, SQLite, keyboard hooks, input, deletion, and writes.
- Added composable fixtures for an application root, data, logs, surveys, field data, upload cache, checkpoints, current-schema SQLite, sample survey/dataset directories, and harmless placeholder images. Every path is pytest-owned.
- Added an explicit-close temporary SQLite initializer and database factory using the current schema and migration definition.
- Added a small recording `FakeWebODM` with deterministic IDs, authentication/preflight, project/task creation, task status/waiting, download recording, and configurable transient/permanent failures.
- Added autouse guards that block socket/HTTP access, subprocess entry points, interactive input, keyboard hooks, dotenv loading, the production database, and common access to captured production data roots. Sensitive path variables are redirected to temporary directories and WebODM credentials are cleared per test.
- Added focused self-tests for fixture ownership/isolation, SQLite schema/isolation/releasability/path safety, fake behavior, network denial, subprocess denial, input denial, dotenv denial, keyboard denial, production-path denial, and continued temporary SQLite/filesystem use.
- No real pipeline, WebODM, QGIS/GDAL, production SQLite, production survey directory, or real runtime cleanup was used.

## Files moved or created

Moved and hardened:

- `tests/test_rgb_pipeline.py` -> `tools/run_rgb_pipeline.py`
- `tests/query_test.py` -> `tools/query_pipeline_db.py`
- `tests/reset_environment.py` -> `tools/reset_environment.py`

Created:

- `pytest.ini`
- `tests/conftest.py`
- `tests/fakes/__init__.py`
- `tests/fakes/webodm.py`
- `tests/test_fake_webodm.py`
- `tests/test_fixture_infrastructure.py`
- `tests/test_import_safety.py`
- `tests/test_query_pipeline_db.py`
- `tests/test_reset_environment.py`
- `tests/test_suite_safety_guards.py`
- `tests/test_temporary_database.py`

Modified:

- `requirements.txt`
- `tests/test_experiment_naming.py`
- `tests/test_import_safety.py`
- `docs/refactor/CURRENT_STATUS.md`
- `docs/refactor/TEST_STRATEGY.md`

## Validation performed

Date: 2026-07-16.

- Pre-change `python -m pytest -q` - 13 passed in 0.10 seconds.
- `python -m py_compile tests/conftest.py tests/fakes/__init__.py tests/fakes/webodm.py tests/test_fixture_infrastructure.py tests/test_temporary_database.py tests/test_fake_webodm.py tests/test_suite_safety_guards.py` - passed.
- `python -m pytest -q tests/test_fixture_infrastructure.py` - 3 passed.
- First `python -m pytest -q tests/test_temporary_database.py` - 4 passed and 1 failed because `PipelineRepo` initialization did not reliably release its SQLite file on Windows. The test initializer was changed to use the current schema/migration with an explicit close; production code was not changed.
- Corrected `python -m py_compile tests/conftest.py tests/test_fixture_infrastructure.py tests/test_temporary_database.py` - passed.
- Corrected `python -m pytest -q tests/test_temporary_database.py` - 5 passed.
- `python -m pytest -q tests/test_fake_webodm.py` - 5 passed.
- First `python -m pytest -q tests/test_suite_safety_guards.py` - 6 passed and 1 failed because the test retained a collection-time dotenv function alias; the self-test was corrected to call the patched module boundary.
- Corrected `python -m pytest -q tests/test_suite_safety_guards.py` - 7 passed, then 8 passed after adding direct production-path coverage.
- First corrected `python -m pytest -q tests/test_import_safety.py` exposed two compatibility issues: synthetic trap metadata confused pytest's failure reporter, and the old empty-`tmp_path` assertion saw the autouse fixture's application directory. The trap received inert metadata and now observes a dedicated empty directory.
- Corrected `python -m pytest -q tests/test_import_safety.py` - 1 passed.
- `python -m pytest --collect-only -q` before the final production-path self-test - 33 tests collected.
- `python -m pytest -q` before the final production-path self-test - 33 passed in 0.25 seconds.
- Final `python -m py_compile tests/conftest.py tests/fakes/__init__.py tests/fakes/webodm.py tests/test_fixture_infrastructure.py tests/test_temporary_database.py tests/test_fake_webodm.py tests/test_suite_safety_guards.py tests/test_import_safety.py` - passed.
- Final `python -m pytest -q tests/test_fixture_infrastructure.py` - 3 passed.
- Final `python -m pytest -q tests/test_temporary_database.py` - 5 passed.
- Final `python -m pytest -q tests/test_fake_webodm.py` - 5 passed.
- Final `python -m pytest -q tests/test_suite_safety_guards.py` - 8 passed.
- Final `python -m pytest -q tests/test_import_safety.py` - 1 passed.
- Final `python -m pytest --collect-only -q` - 34 tests collected.
- Final `python -m pytest -q` - 34 passed in 0.23 seconds.
- `git diff --check` - passed; Git reported only LF-to-CRLF working-tree conversion warnings.
- External tests run: none.
- External services contacted: none.
- Production pipeline commands run: none.
- Destructive operator scripts run: none.

## Remaining Phase 1 risks

Phase 1 is not complete.

- `RGBPipeline.stage_webodm()`, `run_webodm_fallback_task()`, and `stage_quality_gate()` directly construct `WebODMProcessor`; the fake cannot yet be injected into orchestration without monkeypatching the imported class.
- `PipelineRepo` uses `with connect(...)` throughout. SQLite's context manager commits or rolls back but does not guarantee an explicit close, and Windows releasability failed during test development. The test initializer closes explicitly; production lifecycle semantics remain unchanged.
- Filesystem denial covers captured production roots through common `Path`, `open`, and cleanup boundaries. It is intentionally not a global monkeypatch of every OS filesystem primitive.
- No external integration tests exist yet; the marker and default exclusion policy are configured but not exercised against a marked test.
- The current schema and migration initialize successfully in temporary SQLite, but `PipelineRepo` persistence APIs are not comprehensively tested.
- The cleanup tool's containment logic is tested without platform-dependent symlink creation; actual symlink/junction behavior remains an untested path.
- The real pipeline operator script was intentionally not executed.

## Next recommended task

The StageRunner non-cancellation `RuntimeError` defect was corrected in the following milestone. Next investigate minimal, backward-compatible configuration, repository, and WebODM injection needed for hermetic `RGBPipeline` construction without running stages.

## Hermetic StageRunner orchestration milestone

Date: 2026-07-17.

### Orchestration unit and production seams

- Tested `StageRunner.run()` with a real `PipelineRepo` over pytest-owned SQLite and a stage callable using `FakeWebODM` plus pytest-owned image paths.
- The existing repository, logger, and callable parameters were sufficient injection boundaries. No production seam or production file was changed.
- Success records the run/stage start, fake authentication/preflight, deterministic project `100` and task `task-0001`, state/output persistence, and completed stage status.
- Controlled failure authenticates and preflights the fake, surfaces a `ValueError`, records failed status/error, and proves project, task, and wait calls do not run.
- Full `RGBPipeline` construction is not yet hermetic and was not attempted.

### Files created

- `tests/test_stage_runner_orchestration.py`

### Exact validation and results

- `python -m py_compile tests/test_stage_runner_orchestration.py` - passed.
- `python -m pytest -q --ignore=tests/test_stage_runner_orchestration.py` - committed baseline: 34 passed in 1.21 seconds.
- `python -m pytest -q tests/test_stage_runner_orchestration.py -k successful` - 1 passed, 1 deselected.
- The first `python -m pytest -q tests/test_stage_runner_orchestration.py -k failed` failed because `PermanentWebODMError` derives from `RuntimeError`; `StageRunner` re-raised it without recording failed status, leaving the row `running`.
- Corrected `python -m py_compile tests/test_stage_runner_orchestration.py` - passed.
- Corrected `python -m pytest -q tests/test_stage_runner_orchestration.py -k successful` - 1 passed, 1 deselected.
- Corrected `python -m pytest -q tests/test_stage_runner_orchestration.py -k failed` - 1 passed, 1 deselected.
- `python -m pytest -q tests/test_fake_webodm.py` - 5 passed.
- `python -m pytest -q tests/test_suite_safety_guards.py` - 8 passed.
- `python -m pytest --collect-only -q` - 36 tests collected.
- `python -m pytest -q` - 36 passed in 0.31 seconds.
- `git diff --check` - passed.
- External tests and services, operator tools, real pipeline execution, WebODM, QGIS/GDAL, keyboard hooks, interactive input, production data, and production SQLite were not used.

### Remaining Phase 1 acceptance criteria and risks

Phase 1 remains in progress.

- `RGBPipeline.stage_webodm()`, `run_webodm_fallback_task()`, and `stage_quality_gate()` still construct `WebODMProcessor` directly.
- Full pipeline construction still depends on configuration/environment, logger/global context, pipeline control paths, and database ownership that are not all explicitly injectable.
- `StageRunner.run()` re-raises non-cancellation `RuntimeError` exceptions before its general failure handler, which can leave stage rows `running`. This confirmed defect was not changed because retry/cancellation redesign was outside this task.
- Production `PipelineRepo` connection close behavior, platform-specific symlink/junction checks, and external-marker exercise remain unresolved.

### Recommended next task

Correct the narrowly confirmed StageRunner non-cancellation `RuntimeError` failure-recording defect with regression coverage and without redesigning retries. Then investigate the smallest backward-compatible seams needed for hermetic `RGBPipeline` construction without running stages.

## StageRunner RuntimeError classification correction

Date: 2026-07-17.

### Root cause and corrected behavior

`StageRunner.run()` previously caught every `RuntimeError` before its ordinary failure handler. Only `WEBODM_TASK_CANCELED` and `__PIPELINE_CANCELED__` were explicitly recognized; every other runtime error was immediately re-raised. Consequently, permanent WebODM failures and ordinary runtime failures bypassed retry/final-failure recording and left the stage row `running`.

The runner now classifies only the existing explicit control messages:

- `WEBODM_TASK_CANCELED` retains its current translation to `__PIPELINE_CANCELED__` after recording the stage as failed/canceled.
- `__PIPELINE_CANCELED__`, `__PIPELINE_PAUSED__`, and `__PIPELINE_ABORTED__` retain direct propagation for the outer pipeline control flow.
- Every other exception, including `RuntimeError` subclasses such as `PermanentWebODMError`, uses the existing retry and terminal-failure path. On exhaustion, the stage is marked failed, its message is stored, and the original exception is propagated.

No database schema, retry count/delay, pause/abort architecture, checkpoint behavior, or logging architecture changed.

### Files modified

- `shared/stage_runner.py`
- `tests/test_stage_runner_orchestration.py`
- `docs/refactor/CURRENT_STATUS.md`
- `docs/refactor/TEST_STRATEGY.md`

### Tests and exact validation

- Pre-change `python -m pytest -q tests/test_stage_runner_orchestration.py` - 2 passed.
- `python -m py_compile shared/stage_runner.py tests/test_stage_runner_orchestration.py` - passed.
- `python -m pytest -q tests/test_stage_runner_orchestration.py -k successful` - 1 passed, 7 deselected.
- `python -m pytest -q tests/test_stage_runner_orchestration.py -k failed_fake_webodm` - 1 passed, 7 deselected.
- `python -m pytest -q tests/test_stage_runner_orchestration.py -k permanent_webodm` - 1 passed, 7 deselected.
- `python -m pytest -q tests/test_stage_runner_orchestration.py -k generic_non_cancellation` - 1 passed, 7 deselected.
- `python -m pytest -q tests/test_stage_runner_orchestration.py -k "explicit_pipeline_control_signal or webodm_ui_cancellation"` - 4 passed, 4 deselected.
- `python -m pytest -q tests/test_fake_webodm.py` - 5 passed.
- `python -m pytest -q tests/test_suite_safety_guards.py` - 8 passed.
- `python -m pytest --collect-only -q` - 42 tests collected.
- `python -m pytest -q` - 42 passed in 0.49 seconds.
- Task-scoped `git diff --check -- shared/stage_runner.py tests/test_stage_runner_orchestration.py docs/refactor/CURRENT_STATUS.md docs/refactor/TEST_STRATEGY.md` - passed.
- Repository-wide `git diff --check` - passed after an unrelated `codes.txt` edit was updated externally; that file remains outside this task and was not modified by this work.

No external test, real pipeline, WebODM request, QGIS/GDAL command, keyboard hook, interactive input, production path, or production SQLite database was used.

### Remaining ambiguity and risks

Phase 1 remains in progress.

- Control flow still uses exact string-backed `RuntimeError` messages because no typed pause/abort/cancel exceptions exist. Classification is isolated to the four existing documented messages.
- Explicit pause, abort, and already-translated cancellation signals retain the existing behavior of propagating without finalizing the current stage row. Whether abort should instead terminally fail an active stage requires a separate control-state decision.
- Non-control runtime failures now participate in the existing broad retry policy. Retry count and delay were not redesigned; non-idempotent retry risk remains.
- The `stages.error_message` column stores the message but has no separate exception-type field.
- Full `RGBPipeline` construction is still not hermetic.

### Recommended next Phase 1 task

Investigate the smallest backward-compatible configuration, repository, and WebODM injection seams needed for hermetic `RGBPipeline` construction without executing stages. Treat typed pipeline-control exceptions as a separate design task rather than expanding this fix.

## Hermetic RGBPipeline construction milestone

Date: 2026-07-17.

### Constructor dependencies and seams

RGBPipeline construction previously owned fixed data/log/database/checkpoint
paths, six process-global named loggers, PipelineRepo creation, and later direct
WebODMProcessor construction at three stage call sites. The constructor already
accepted an explicit config dictionary, base/source/survey paths, year, and run
identifier; it never loaded dotenv or contacted WebODM itself.

The smallest backward-compatible optional seams are now:

- db_file or an existing repository, with the pair rejected as ambiguous;
- an existing six-entry logger mapping;
- explicit logs_dir and checkpoint_dir paths; and
- an existing webodm_processor used by all three processor call sites.

Configuration must be a mapping and is rejected before filesystem, logger,
database, or external collaborators are opened. Default callers still use
base_dir/data/logs, base_dir/data/pipeline.db, checkpoint files under the log
directory, PipelineControl under base_dir/data, the existing global logger
factory, and the real WebODMProcessor when a WebODM stage later requests it.

### Constructor-time side effects that remain

- Default logger construction creates the log directory and opens the existing
  six log files. Injected logger mappings bypass those global/path effects.
- Default PipelineRepo construction creates/opens SQLite, applies the current
  schema and migrations, and enables the existing WAL/PRAGMA settings.
- RGBPipeline creates the metadata-bearing run record, then StageRunner
  performs a second idempotent create_run(run_id) upsert. This duplicate
  constructor-time persistence is unchanged and remains a later design concern.
- PipelineControl, StageRunner, and PipelinePreflight objects are constructed.
  PipelineControl creates no flags and registers no hotkeys until run() starts.
- No preflight check, pipeline stage, input prompt, WebODM authentication,
  network call, QGIS/GDAL command, or external subprocess occurs in the
  constructor.

### Files modified or created

- Modified: pipelines/rgb_pipeline.py
- Created: tests/test_rgb_pipeline_construction.py
- Modified: docs/refactor/CURRENT_STATUS.md
- Modified: docs/refactor/TEST_STRATEGY.md

No database schema, migration, configuration loader, logger implementation,
retry, checkpoint-write protocol, or operator workflow changed.

### Tests and exact validation

- Pre-change python -m pytest -q - 42 passed in 0.59 seconds.
- python -m py_compile pipelines/rgb_pipeline.py
  tests/test_rgb_pipeline_construction.py - passed.
- The first focused collection exposed that exifread is imported by the
  cross-run module but is neither installed nor declared in requirements.txt.
  The construction test now supplies a narrow import-only stub that fails if
  EXIF processing is attempted; no dependency was installed or changed.
- First executable focused run - 3 passed and 1 failed because the compatibility
  recorder revealed StageRunner's second existing create_run upsert.
- Corrected python -m pytest -q tests/test_rgb_pipeline_construction.py -
  4 passed in 0.12 seconds.
- python -m pytest -q tests/test_stage_runner_orchestration.py -
  8 passed in 0.27 seconds.
- python -m pytest -q tests/test_fake_webodm.py - 5 passed in 0.04 seconds.
- python -m pytest -q tests/test_suite_safety_guards.py -
  8 passed in 0.05 seconds.
- python -m pytest --collect-only -q - 46 tests collected.
- python -m pytest -q - 46 passed in 0.63 seconds.

The four construction tests prove temporary path/database ownership, temporary
run persistence, fake WebODM retention without calls, logger/repository
retention, explicit db_file use, unchanged default wiring through safe
recorders, early invalid-config failure, and absence of stage/preflight
execution. Suite-wide guards continue to fail any dotenv, network, subprocess,
input, keyboard, or captured production-path access.

No external test, pipeline run, pipeline stage, real WebODM request,
QGIS/GDAL command, keyboard hook, interactive input, production path, or
production SQLite database was used.

### Remaining Phase 1 work and next task

Phase 1 remains in progress. Production PipelineRepo connection-close
semantics, direct configuration-loader validation without dotenv, actual
symlink/junction containment behavior, and an explicit external-marker
exercise remain unproven. Global logger context remains unsafe for concurrent
pipeline instances and is reserved for Phase 2.

The next scoped task should be a hermetic single-stage RGBPipeline execution
test using the new repository/logger/WebODM seams and a stage with every
external boundary faked. It must not run the full pipeline.


## Hermetic single-stage RGBPipeline execution milestone

Date: 2026-07-17.

### Selected stage and execution seam

The selected stage is `data_segregation`, reached through the real production path `RGBPipeline.run()` -> `StageRunner.run("data_segregation", self.stage_data_segregation, ...)` -> `RGBPipeline.stage_data_segregation()` -> the imported `run_data_segregation` dependency.

This is the safest meaningful stage because it is first in the fixed RGB sequence and requires no prior hydrated state, WebODM calls, QGIS/GDAL subprocesses, quality-gate input, or real imagery processing. The test replaces only the `run_data_segregation` module boundary with a deterministic fake that creates a tiny temporary survey tree and KML under pytest-owned paths.

`RGBPipeline.run()` now accepts two explicit, backward-compatible optional seams:

- `selected_stages`, defaulting to the complete existing stage set, executes only the named stages in the existing production order.
- `raise_on_error`, defaulting to `False`, preserves the current production behavior of returning failed state while allowing tests and future callers to request propagation after failure state has been recorded.

Production defaults remain compatible: omitting both arguments still attempts the same stage sequence with the same stage names, output keys, force behavior, stale-running policy for WebODM/QGIS, quality-gate stop behavior, and failure-state recording.

### Success and failure coverage

Created `tests/test_rgb_pipeline_single_stage_execution.py` with two focused tests.

The success path constructs `RGBPipeline` with temporary paths, temporary SQLite, injected loggers, injected `PipelineRepo`, and `FakeWebODM`; selects only `data_segregation`; observes the selected stage in `running` state from inside the fake dependency; verifies deterministic output and KML rename behavior; confirms the stage is completed in temporary SQLite; confirms the run and survey state are completed; confirms all checkpoint, database, control-flag, survey, and output paths remain under pytest's temporary application root; confirms no later stage row or fake/external operation occurs.

The controlled failure path configures the fake segregation dependency to raise `ControlledSegregationFailure`; preserves current `StageRunner` retry behavior by asserting three dependency calls; verifies the selected stage is failed with the original message and no output JSON; verifies the run is failed; verifies the original exception propagates with `raise_on_error=True`; and confirms later stages, WebODM calls, checkpoint writes, and non-temporary resources are not used.

### Files modified or created

- Modified: `pipelines/rgb_pipeline.py`
- Created: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, retry policy, checkpoint protocol, logging architecture, quality-gate behavior, WebODM implementation, QGIS/GDAL boundary, dependency list, or operator workflow changed.

### Tests and exact validation

- Pre-change full default suite was not rerun before editing in this session; the reviewed starting baseline was 46 passing tests from the previous committed milestone.
- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- First `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k successfully` failed because the new test file missed `Path` import; no production behavior changed for this fix.
- First `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k failure` failed for the same missing `Path` import after proving the stage failure path recorded and retried.
- Corrected `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k successfully` - 1 passed, 1 deselected.
- Corrected `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k failure` - 1 passed, 1 deselected.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 2 passed.
- `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 4 passed.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 8 passed.
- `python -m pytest -q tests\test_fake_webodm.py` - 5 passed.
- `python -m pytest -q tests\test_suite_safety_guards.py` - 8 passed.
- First `python -m pytest --collect-only -q` collected 46 existing tests and failed while collecting the new module because an existing `exifread` test stub had no `__spec__`; the new module's stub guard was made collection-order safe.
- Corrected `python -m py_compile tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- Corrected `python -m pytest --collect-only -q` - 48 tests collected.
- `python -m pytest -q` before documentation updates - 48 passed in 0.87 seconds.
- `git diff --check` - passed.
- Final `python -m pytest -q` after documentation updates - 48 passed in 1.00 seconds.

No external tests, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, or destructive cleanup tool was used.

### Remaining Phase 1 risks

Phase 1 remains in progress.

- Only `data_segregation` has been exercised through real `RGBPipeline.run()` orchestration; cross-run filtering, KML boundary, WebODM, quality gate, and QGIS remain unexecuted at RGBPipeline integration level.
- The selected stage test uses a fake `run_data_segregation` dependency to avoid real filesystem scanning and copying, so the real data segregation implementation is not validated here.
- `selected_stages` can execute later stages directly if a caller supplies them without prerequisite state; this is an explicit low-level execution seam, not a dependency resolver.
- `raise_on_error` preserves default compatibility but introduces a second caller-visible failure mode when explicitly requested.
- Broad `StageRunner` retries remain unchanged and can repeat non-idempotent work; the new failure test documents the current three-call behavior rather than redesigning it.
- The quality gate still requires interactive input in normal full runs, and WebODM/QGIS boundaries remain fake-only or untested in default suite integration.

### Recommended next task

Add one more high-value hermetic stage test only if it can be isolated without broad production changes; otherwise perform a Phase 1 completion assessment focused on remaining configuration-loader, repository connection-lifecycle, and external-boundary gaps.


## Phase 1 completion assessment

Date: 2026-07-17.

### Recommendation

Phase 1 is complete enough to move to Phase 2 - Logging and observability.

The Phase 1 objective was to make normal test discovery deterministic and incapable of touching real pipelines, services, survey roots, output trees, or the production database. The current default suite provides direct safety guards, self-tests for those guards, temporary filesystem and SQLite fixtures, fake WebODM coverage, import-time side-effect checks, safe operator-tool boundaries, hermetic StageRunner coverage, hermetic RGBPipeline construction, and one hermetic single-stage RGBPipeline execution test.

No blocking Phase 1 risks remain for starting Phase 2. Remaining gaps are real integration breadth and deeper persistence/platform edge cases, not blockers for logging/observability work. Those gaps should remain tracked and must be addressed before later phases that introduce ownership changes, recovery semantics, external integration, or concurrency.

### Criteria assessment

| # | Criterion | Status | Evidence | Remaining risk | Blocks Phase 2? |
|---|---|---|---|---|---|
| 1 | Normal test discovery performs no external I/O. | Pass | `pytest.ini` restricts discovery to `tests/`; `python -m pytest --collect-only -q` collected 48 tests; `tests/test_import_safety.py` traps network, subprocess, input, keyboard, deletion, and write side effects during tool imports. | Guards cover normal Python boundaries, not every possible OS primitive. | No. |
| 2 | Default tests do not use production `.env`. | Pass | `tests/conftest.py` patches `dotenv.load_dotenv`; `tests/test_suite_safety_guards.py::test_dotenv_loading_is_blocked` asserts denial; tests construct explicit config dictionaries. | Full configuration-loader behavior without dotenv is not comprehensively tested. | No. |
| 3 | Default tests do not contact WebODM. | Pass | Autouse guards block sockets and `requests.sessions.Session.request`; `FakeWebODM` is used in StageRunner/RGBPipeline tests; `tests/test_fake_webodm.py` proves fake behavior without network. | Real WebODM compatibility remains external-only and untested by default. | No. |
| 4 | Default tests do not run QGIS/GDAL. | Pass | Autouse guards block `subprocess.Popen`, `run`, `call`, `check_call`, and `check_output`; `tests/test_suite_safety_guards.py::test_external_subprocess_is_blocked` asserts denial; RGBPipeline construction/stage tests avoid QGIS. | QGIS/GDAL command construction and external compatibility remain untested. | No. |
| 5 | Default tests do not use production SQLite. | Pass | Autouse SQLite guard rejects `data/pipeline.db`; `tests/test_temporary_database.py::test_production_database_path_is_rejected_without_opening_or_creating_it`; all repo tests use pytest-owned DB paths. | Production `PipelineRepo` connection-close semantics remain a later operational concern. | No. |
| 6 | Default tests do not access production survey roots. | Pass | Autouse env redirection sets `SURVEYS_ROOT`, `FIELD_DATA_ROOT`, and `UPLOAD_CACHE_ROOT` to pytest-owned dirs; common `Path`, `open`, and cleanup boundaries reject captured production roots; safety tests assert path redirection and known production path denial. | Guard is deliberately narrow and not a global monkeypatch of every filesystem primitive. | No. |
| 7 | Destructive helpers require explicit paths and opt-in execution. | Pass | `tools/reset_environment.py` requires an explicit root, sentinel, and `--allow-destructive-reset`; `tests/test_reset_environment.py` covers opt-in, missing sentinel, repository root, filesystem root, containment escape, and allowed deletion set. | Platform-specific symlink/junction behavior remains untested. | No. |
| 8 | Temporary filesystem fixtures are available. | Pass | `tests/conftest.py` provides application/data/logs/surveys/field-data/upload-cache/checkpoint fixtures; `tests/test_fixture_infrastructure.py` proves ownership, composability, and isolation. | Fixture set is enough for Phase 1; later phases will need richer artifact/workspace fixtures. | No. |
| 9 | Temporary SQLite fixtures are available. | Pass | `temporary_sqlite_db_path`, `temporary_sqlite_connection`, and `temporary_pipeline_db_factory` initialize current schema under pytest temp roots; `tests/test_temporary_database.py` proves isolation, schema, releasability, and production DB denial. | SQLite concurrency, busy behavior, and broad repository API coverage remain incomplete. | No. |
| 10 | Fake external services are available. | Pass | `tests/fakes/webodm.py` provides deterministic `FakeWebODM`; fake tests cover deterministic IDs, call recording, transient/permanent failures, and network independence. | No fake QGIS/GDAL runner exists yet; not required to start logging work. | No. |
| 11 | Import-time side effects are guarded. | Pass | `tests/test_import_safety.py` imports operator tools under traps for runtime, external, interactive, deletion, and write effects; collection remains safe at 48 tests. | Import traps target known risky modules/tools, not every repository module. | No. |
| 12 | A safe default test command is documented. | Pass | `pytest.ini` defines `python -m pytest -q` behavior through default marker exclusion; `docs/refactor/TEST_STRATEGY.md` documents the canonical command. | None blocking. | No. |
| 13 | Remaining external/integration tests are explicitly excluded. | Pass | `pytest.ini` registers `external` and sets `addopts = -m "not external"`; `tools/run_rgb_pipeline.py` requires `--allow-external-run`; no external tests are collected by default. | No marked external test exists to exercise the marker path; future external tests need environment gates. | No. |
| 14 | Current suite validates the most important safety guarantees. | Pass | Safety guards, fixture infrastructure, temporary DB, fake WebODM, import safety, safe tools, StageRunner, RGBPipeline construction, and one selected RGBPipeline stage are covered; full suite is 48 passing tests. | Later RGBPipeline stages, real data segregation, quality gate, WebODM/QGIS integration, and concurrency/recovery are not covered by default. | No. |

### Validation for this assessment

- `python -m pytest --collect-only -q` - 48 tests collected in 0.12 seconds.
- `python -m pytest -q` - 48 passed in 0.79 seconds.
- `git diff --check` - passed.

No production code was modified. No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining non-blocking risks

- The real data segregation implementation is not exercised in the hermetic RGBPipeline stage test; the heavy dependency is faked to preserve safety.
- Later RGBPipeline stages are not yet executed through full RGBPipeline orchestration in the default suite.
- The quality gate remains interactive in production full runs and is intentionally not automated in Phase 1.
- QGIS/GDAL command behavior and WebODM compatibility require future fake-backed or explicitly external tests.
- Production `PipelineRepo` connection lifecycle, SQLite lock behavior, and broader repository API coverage remain incomplete.
- Filesystem safety guards cover common Python boundaries and captured production roots, not every OS-level primitive or platform-specific symlink/junction behavior.
- The external marker is configured and excluded by default, but no external test currently exercises the opt-in marker/environment-gate workflow.

### Blocking risks

None for moving to Phase 2.

### Recommended first Phase 2 task

Characterize and test current logger context and handler ownership before changing it: build focused tests that demonstrate whether two `RGBPipeline` or logger instances in one process can exchange `run_id`, `stage_name`, handlers, or log destinations. Use injected temporary log paths and no pipeline stages. This should directly inform ADR-002 without starting broad logging rewrites.


## Phase 2 logging context characterization milestone

Date: 2026-07-17.

### Scope

Started Phase 2 without changing production logging code. The goal was to characterize the current logger context and handler ownership behavior so ADR-002 can be decided from evidence rather than assumption.

Created `tests/test_logging_context_ownership.py` with focused tests that use only pytest-owned temporary log paths and temporary RGBPipeline construction. The tests clean up the named loggers and handlers they create so process-global logging state does not leak to other tests.

### Confirmed current behavior

- `shared.logging.get_logger()` returns the same process-global `logging.Logger` for repeated calls with the same name.
- The first configured file handler remains attached; a later call with the same logger name and a different `log_file` does not add or replace the file handler.
- A later call with the same logger name and a new `run_id` mutates the existing `ContextFilter.run_id`.
- `stage_name` is also shared mutable state on the existing `ContextFilter`; later logical users inherit and can overwrite it.
- Two default `RGBPipeline` constructions in one process reuse the same `rgb.pipeline` logger object. The second pipeline's `run_id` is written through the first pipeline's file handler destination.

This confirms the Phase 2 risk described by ADR-002 and the scalability audit: current logger context and handler ownership are not isolated across multiple logical pipeline instances in one process.

### Files created or modified

- Created: `tests/test_logging_context_ownership.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No production code, database schema, migration, retry behavior, checkpoint behavior, pipeline stage behavior, external-service behavior, or operator workflow changed.

### Tests and validation

- `python -m py_compile tests\test_logging_context_ownership.py` - passed.
- First `python -m pytest -q tests\test_logging_context_ownership.py` - 3 passed in 0.13 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 4 passed.
- `python -m pytest -q tests\test_suite_safety_guards.py` - 8 passed.
- First `python -m pytest --collect-only -q` found a collection-order issue: the new test's `exifread` import stub lacked `__spec__`, causing the older RGBPipeline construction test's `find_spec("exifread")` guard to raise. The new stub was changed to include a `ModuleSpec`.
- Corrected `python -m py_compile tests\test_logging_context_ownership.py` - passed.
- Corrected `python -m pytest -q tests\test_logging_context_ownership.py` - 3 passed in 0.12 seconds.
- Corrected `python -m pytest --collect-only -q` before documentation updates - 51 tests collected.
- `python -m pytest -q` before documentation updates - 51 passed in 0.87 seconds.
- Final `python -m pytest --collect-only -q` after documentation updates - 51 tests collected in 0.10 seconds.
- Final `python -m pytest -q` after documentation updates - 51 passed in 0.99 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks and recommended next task

ADR-002 remains pending. The next task should choose the smallest backward-compatible logging direction, likely one of:

- per-run/per-instance logger names with explicit handler ownership;
- logger adapters or structured event wrappers that carry immutable per-record context;
- `contextvars` for run/stage context if concurrent async/thread behavior is required; or
- another explicit design recorded in `DECISIONS.md` before production changes.

Before implementing the fix, add or adjust tests so the desired behavior is expressed as isolation requirements rather than current-behavior characterization: two logical pipeline/logger instances must not exchange run IDs, stage names, handlers, or file destinations.


## ADR-002 logger isolation implementation milestone

Date: 2026-07-17.

### Implemented behavior

Accepted ADR-002 and implemented the first production logging isolation fix in `shared/logging.py`.

`get_logger()` keeps its existing public signature, but when a run ID or log file is supplied it now creates an owned concrete logger keyed by logical logger name, run ID, and log destination. A `ContextFilter` preserves the logical logger name in emitted records, so file logs continue to use the existing `time | level | logger | run_id | stage | message` shape expected by `query_survey_stats.py`.

Stage context is now stored in context-local state keyed by the owned concrete logger identity. This prevents one logger instance's stage context from leaking into another same-named logical logger instance. Existing callers that use `get_logger()` without a run ID and without a log file retain conventional `logging.getLogger(name)` behavior.

### Tests changed

`tests/test_logging_context_ownership.py` was converted from characterization of the old broken behavior into desired-behavior coverage. It now proves:

- same logical logger names with different run IDs/log files produce distinct logger objects;
- each owned logger writes only to its own temporary file handler destination;
- run IDs do not cross between owned loggers;
- stage names do not cross between owned loggers; and
- two default `RGBPipeline` constructions in one process own separate `rgb.pipeline` handlers and write to their own log files while preserving the logical `rgb.pipeline` record name.

### Files modified or created

- Modified: `shared/logging.py`
- Modified: `tests/test_logging_context_ownership.py`
- Modified: `docs/refactor/DECISIONS.md`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, pipeline stage behavior, retry behavior, checkpoint behavior, WebODM behavior, QGIS/GDAL behavior, dependency list, or operator workflow changed.

### Validation

- `python -m py_compile shared\logging.py tests\test_logging_context_ownership.py` - passed.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 3 passed in 0.16 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 4 passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 2 passed.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 8 passed.
- `python -m pytest --collect-only -q` before documentation updates - 51 tests collected in 0.06 seconds.
- `python -m pytest -q` before documentation updates - 51 passed in 0.91 seconds.
- Final `python -m pytest --collect-only -q` after documentation updates - 51 tests collected in 0.06 seconds.
- Final `python -m pytest -q` after documentation updates - 51 passed in 0.98 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- Stage context is isolated by context-local logger identity, but explicit concurrent/threaded interleaving tests have not yet been added. This is a tracked future risk rather than a current blocker because the pipeline has not introduced worker-thread execution.
- `query_survey_stats.py` parser compatibility now has one parser-level generated-log test, but broader parser analytics and historical log-format compatibility remain outside this logging isolation task.
- Direct users of `logging.getLogger("rgb.*")` bypass `get_logger()` ownership; current RGBPipeline default construction uses `get_logger()`, but this remains a boundary to inventory.
- Handler lifecycle in long-running worker processes needs a later ownership/cleanup policy beyond test cleanup helpers.

## ADR-002 parser compatibility coverage

Date: 2026-07-17.

Added one lightweight parser-level compatibility test for the logging isolation change. The test writes a temporary `pipeline.log` line through `get_logger("rgb.pipeline", ..., run_id="run-parser")`, sets stage context to `data_segregation`, then verifies `query_survey_stats.parse_log_events()` reads the generated line with the expected logical logger name, run ID filter, stage, message, and `pipeline.log` source.

This keeps coverage focused on the contract changed by ADR-002: owned concrete logger names must not leak into the existing `time | level | logger | run_id | stage | message` parser surface. No threaded/interleaved test was added in this step; that remains a future concurrency risk to cover when worker-thread execution or concurrent pipeline orchestration is introduced.

### Files modified

- Modified: `tests/test_logging_context_ownership.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No production code, database schema, migration, pipeline stage behavior, retry behavior, checkpoint behavior, dependency list, external-service behavior, or operator workflow changed.

### Validation

- `python -m py_compile tests\test_logging_context_ownership.py` - passed.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 4 passed in 0.16 seconds.`r`n- `python -m pytest --collect-only -q` - 52 tests collected in 0.06 seconds.`r`n- `python -m pytest -q` - 52 passed in 0.87 seconds.`r`n- `git diff --check` - passed; Git reported an LF-to-CRLF working-tree warning for `docs/refactor/CURRENT_STATUS.md`.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- Explicit threaded/interleaved logging behavior remains untested and should be added only when concurrency becomes part of the production design.
- Direct users of `logging.getLogger("rgb.*")` still bypass `get_logger()` ownership and should be inventoried before broadening observability work.
- Handler lifecycle in long-running worker processes still needs a later ownership/cleanup policy beyond test cleanup helpers.
