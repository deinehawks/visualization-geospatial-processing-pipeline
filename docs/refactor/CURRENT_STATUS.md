# Refactor Current Status

## Summary

- **Refactor status:** In progress; Phase 2 logging and observability is complete enough to move to Phase 3 planning
- **Current phase:** Phase 3 - Run-scoped workspace ownership, remaining reachable WebODM pointcloud and all-assets ZIP outputs now write through the run workspace before legacy mirroring
- **Completed work:** Phase 1 safety baseline, Phase 2 logger isolation and observability, plus Phase 3 artifact planning, RGBPipeline workspace layout seam, data segregation workspace metadata, cross-run filter workspace-to-legacy mirroring, KML boundary workspace-to-legacy mirroring, WebODM orthomosaic workspace-to-legacy mirroring, QGIS clipped orthomosaic/tile workspace-to-legacy mirroring, and WebODM pointcloud/all-assets ZIP workspace-to-legacy mirroring
- **Current task:** WebODM pointcloud and all-assets ZIP migration completed for currently reachable exports; next Phase 3 slice should address map-export publication inputs/outputs or explicitly decide whether to activate dormant DEM downloads
- **Production code changed:** Yes - observability changes in shared/logging.py, shared/stage_runner.py, pipelines/rgb_pipeline.py, modules/qgis/qgis_tools.py, query_survey_stats.py, plus Phase 3 artifact planning groundwork in shared/artifacts.py, the RGBPipeline workspace layout seam, data segregation workspace metadata, cross-run filter workspace mirroring, KML boundary workspace mirroring, WebODM orthomosaic workspace mirroring, QGIS workspace mirroring, and WebODM pointcloud/all-assets workspace mirroring
- **Next recommended task:** Migrate map-export publication inputs/outputs toward manifest-aware workspace/published paths, or explicitly decide whether dormant WebODM DEM downloads should be activated before migrating them

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
- python -m pytest -q tests\test_logging_context_ownership.py - 4 passed in 0.16 seconds.
- python -m pytest --collect-only -q - 52 tests collected in 0.06 seconds.
- python -m pytest -q - 52 passed in 0.87 seconds.
- git diff --check - passed; Git reported an LF-to-CRLF working-tree warning for docs/refactor/CURRENT_STATUS.md.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- Explicit threaded/interleaved logging behavior remains untested and should be added only when concurrency becomes part of the production design.
- Direct users of `logging.getLogger("rgb.*")` have been inventoried. They are outside the owned RGBPipeline run logger set, but they do not carry run context or owned file destinations.
- Handler lifecycle in long-running worker processes still needs a later ownership/cleanup policy beyond test cleanup helpers.

## Logging ownership bypass inventory

Date: 2026-07-17.

Inventoried direct `logging.getLogger()` usage after ADR-002 to check whether any current `rgb.*` callers bypass the new owned logger path used by `RGBPipeline` run loggers.

### Findings

- `pipelines/rgb_pipeline.py` uses `shared.logging.get_logger()` for the six owned run loggers: `rgb.pipeline`, `rgb.data_segregation`, `rgb.cross_run_filter`, `rgb.kml`, `rgb.webodm`, and `rgb.qgis`.
- `main.py` uses `logging.getLogger("rgb.source_resolver")` before `RGBPipeline` construction and passes it to source dataset resolution. This logger is not one of the six owned per-run file loggers and does not currently carry run context.
- `modules/map_export/orthomosaic_finder.py` and `modules/map_export/survey_manifest.py` use module-level `logging.getLogger("rgb.map_export")`. These utilities are outside RGBPipeline run orchestration and do not currently write parser-compatible per-run logs.
- `shared/logging.py` uses `logging.getLogger(logger_name)` internally to create the concrete owned logger name; this is intended ADR-002 behavior.
- Non-`rgb.*` utility scripts (`check_perms.py`, `cleanup_task4.py`, `pause_run.py`) use conventional loggers and remain outside this per-run logger ownership contract. Some of these scripts perform external or operational actions and must remain outside default test/import paths.

### Assessment

No immediate production logging change is required from this inventory. The direct `rgb.*` bypasses are not sharing the six RGBPipeline run logger names, so they do not reintroduce the handler/run ID/stage leakage fixed by ADR-002. Their remaining limitation is observability completeness: source resolution and map export logs are not yet tied to a pipeline run ID, stage name, or parser-owned log file.

Threaded/interleaved logger coverage remains intentionally deferred until concurrency becomes part of the production execution design.

### Files modified

- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No production code, tests, database schema, migration, pipeline stage behavior, retry behavior, checkpoint behavior, dependency list, external-service behavior, or operator workflow changed.

### Validation

- `rg -n "logging\.getLogger|getLogger\(|get_logger\(" .` - completed for inventory.
- `git diff --check` - passed; Git reported an LF-to-CRLF working-tree warning for `docs/refactor/CURRENT_STATUS.md`.

### Recommended next Phase 2 task

Define the next observability contract before adding more implementation: decide which run/stage lifecycle events must be machine-readable in logs, which identifiers are mandatory (`run_id`, `survey_id`, `stage`, attempt, task/project IDs), and whether the contract stays log-line based or introduces structured event helpers around the existing logger.

## ADR-013 run/stage observability contract

Date: 2026-07-17.

Accepted ADR-013 to define the Phase 2 observability contract before adding more logging implementation.

### Contract summary

- Preserve the existing log file shape: `time | level | logger | run_id | stage | message`.
- Keep human-readable logs and banners, but make lifecycle/failure facts parseable through `event=<name> key=value ...` messages inside the existing message field.
- Treat the existing `run_id` column as mandatory for run-scoped parseable events.
- Treat the existing `stage` column as mandatory for stage-scoped parseable events.
- Include `survey_id` when known, `attempt` when retries or attempt semantics are involved, `elapsed_seconds` for completion/failure, and `error_type` plus bounded `error_message` for failures.
- Include external identifiers without secrets for WebODM/QGIS boundaries, such as project ID, task ID, selected task label, tool name, return code, and bounded artifact counts.
- Do not log credentials, tokens, private URLs, unbounded paths, or large payloads.

### Initial event vocabulary

- Run lifecycle: `run_started`, `run_completed`, `run_failed`, `run_paused`, `run_aborted`, `run_canceled`.
- Stage lifecycle: `stage_started`, `stage_skipped`, `stage_completed`, `stage_failed`, `stage_retrying`, `stage_canceled`, `stage_stale`.
- State/artifact diagnostics: `stage_output_loaded`, `checkpoint_saved`, `checkpoint_loaded`, `artifact_selected`, `artifact_published` where those actions are already present and safe to observe.
- External boundaries: `webodm_project_created`, `webodm_task_created`, `webodm_task_status`, `webodm_download_started`, `webodm_download_completed`, `qgis_command_started`, `qgis_command_completed`, `qgis_command_failed`.

### Scope decision

No database schema, migration, event journal, JSON log format, or persistent state model is introduced in Phase 2. The future database/state refactor should reuse this event vocabulary when attempts, transitions, and durable diagnostics are redesigned.

### Files modified

- Modified: `docs/refactor/DECISIONS.md`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No production code, tests, database schema, migration, pipeline stage behavior, retry behavior, checkpoint behavior, dependency list, external-service behavior, or operator workflow changed.

### Validation

- Documentation-only change; no pytest run was required.
- `git diff --check` - passed; Git reported LF-to-CRLF working-tree warnings for the edited refactor docs.

### Recommended next Phase 2 task

Implement a minimal parseable lifecycle helper around the existing logger and apply it first to `StageRunner` start, skip, retry, completion, failure, cancellation, and stale-stage logs. Keep the existing human-readable messages during the transition and add focused parser/format tests using temporary log files.

## ADR-013 StageRunner lifecycle event implementation

Date: 2026-07-17.

Implemented the first ADR-013 production slice by adding `shared.logging.log_event()` and applying it to the StageRunner-owned lifecycle helpers while preserving the existing human-readable log lines.

### Implemented behavior

- `log_event()` emits `event=<name> key=value ...` messages through the existing logger and existing log-file format.
- Values containing whitespace or `=` are quoted and ANSI color codes are stripped before formatting.
- Stage lifecycle helpers now emit parseable events for `stage_started`, `stage_completed`, `stage_skipped`, `stage_failed`, `stage_retrying`, `stage_canceled`, `stage_stale`, and `stage_output_loaded`.
- Stage context is set while emitting skipped-stage and output-loaded events so the existing `stage` log column remains populated.
- Existing banners, progress lines, retry messages, and failure messages remain in place for operator readability.

No database schema, migration, retry policy, state model, JSON log format, event journal, external-service behavior, QGIS/WebODM behavior, or operator workflow changed.

### Tests changed

`tests/test_stage_runner_orchestration.py` now includes one focused lifecycle-event test that writes to a pytest-owned temporary log file through `get_logger()`, runs a stage that retries once and succeeds, then runs the same stage again to exercise skip/output loading. It verifies parseable `stage_started`, `stage_retrying`, `stage_completed`, `stage_skipped`, and `stage_output_loaded` messages while preserving the logical logger name, run ID, and stage columns.

### Files modified

- Modified: `shared/logging.py`
- Modified: `shared/stage_runner.py`
- Modified: `tests/test_stage_runner_orchestration.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

### Validation

- `python -m py_compile shared\logging.py shared\stage_runner.py tests\test_stage_runner_orchestration.py` - passed.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 9 passed in 0.40 seconds.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 4 passed in 0.15 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 2 passed in 0.17 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 4 passed in 0.14 seconds.
- `python -m pytest --collect-only -q` - 53 tests collected in 0.06 seconds.
- `python -m pytest -q` - 53 passed in 1.10 seconds.
- `git diff --check` - passed; Git reported LF-to-CRLF working-tree warnings for edited files.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- Run-level `run_started`, `run_completed`, and `run_failed` events are not implemented yet.
- WebODM/QGIS external-boundary events are still free-form or absent.
- `query_survey_stats.py` does not yet prefer explicit `event=` records for analytics; it remains compatible with the existing text log parser.
- Threaded/interleaved logging behavior remains deferred until production concurrency is introduced.

### Recommended next Phase 2 task

Add parseable run-level lifecycle events in `RGBPipeline.run()` for run start, completion, failure, pause, abort, and cancellation while preserving current operator-facing output and without changing database schema.
## ADR-013 RGBPipeline run lifecycle event implementation

Date: 2026-07-17.

Implemented the next ADR-013 production slice by adding parseable run-level lifecycle events to `RGBPipeline.run()` while preserving the existing operator-facing pipeline header/footer messages.

### Implemented behavior

- `run_started` is emitted after the pipeline header with `resume` and `selected_stages` fields.
- `run_completed` is emitted before the success footer with `elapsed_seconds` and `survey_id` when known.
- `run_failed` is emitted for preflight failures, ordinary runtime failures, and unexpected exceptions with `elapsed_seconds`, `survey_id` when known, `error_type`, and bounded `error_message`.
- `run_paused`, `run_canceled`, and `run_aborted` are emitted for the existing explicit control signals with reason/after-stage fields where available.
- The existing log-file shape, human-readable logs, database state updates, retry policy, pause/abort/cancel behavior, and operator workflow are unchanged.

No database schema, migration, event journal, JSON log format, retry behavior, checkpoint behavior, WebODM behavior, QGIS/GDAL behavior, dependency list, or operator workflow changed.

### Tests changed

The existing hermetic RGBPipeline single-stage success and failure tests now use a pytest-owned temporary pipeline log file for the pipeline logger. They verify `run_started` plus `run_completed` on the successful selected-stage path, and `run_started` plus `run_failed` with exception type/message on the controlled failure path.

### Files modified

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

### Validation

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 2 passed in 0.20 seconds.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 4 passed in 0.14 seconds.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 9 passed in 0.32 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 4 passed in 0.14 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 2 passed in 0.19 seconds.
- `python -m pytest --collect-only -q` - 53 tests collected in 0.06 seconds.
- `python -m pytest -q` - 53 passed in 1.15 seconds.
- `git diff --check` - passed; Git reported LF-to-CRLF working-tree warnings for edited files.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- WebODM/QGIS external-boundary events are still free-form or absent.
- `query_survey_stats.py` does not yet prefer explicit `event=` records for analytics; it remains compatible with the existing text log parser.
- Pause, abort, and WebODM cancellation run-level events are implemented but not yet directly exercised by RGBPipeline tests.
- Threaded/interleaved logging behavior remains deferred until production concurrency is introduced.

### Recommended next Phase 2 task

Add parseable external-boundary events at the WebODM and QGIS seams where task/project IDs, selected task labels, tool names, return codes, and artifact counts are already known, using fake-backed tests only.
## ADR-013 WebODM boundary event implementation

Date: 2026-07-17.

Implemented the first WebODM external-boundary observability slice inside the main `stage_webodm()` Task 2 create/wait path.

### Implemented behavior

- New WebODM project creation emits `webodm_project_created` with `project_id` and `project_name`.
- New primary Task 2 creation emits `webodm_task_created` with `project_id`, `task_key`, `task_id`, and `task_name`.
- Primary Task 2 wait completion emits `webodm_task_status` with `project_id`, `task_key`, `task_id`, `status`, `success`, and `elapsed_seconds`.
- Existing WebODM behavior, task naming, checkpoint writes, retry behavior, downloads, fake WebODM behavior, and operator-facing logs are preserved.

This is intentionally the first narrow WebODM boundary slice. Resume/reattach branches, Task 1 branches, Task 4/fallback, quality-gate restart, and download/export boundary events remain for later slices.

### Tests changed

`tests/test_rgb_pipeline_single_stage_execution.py` now includes one hermetic WebODM-stage test. It constructs `RGBPipeline` with `FakeWebODM`, pytest-owned paths, a temporary WebODM log file, fake upload-cache behavior, Task 1 skipped, Task 2 enabled, Task 4 skipped, and exports disabled. The test verifies fake project/task/wait calls and parseable `webodm_project_created`, `webodm_task_created`, and `webodm_task_status` events.

### Files modified

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, event journal, JSON log format, retry behavior, checkpoint behavior, WebODM API implementation, QGIS/GDAL behavior, dependency list, or operator workflow changed.

### Validation

- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 3 passed in 0.28 seconds.
- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 4 passed in 0.26 seconds.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 9 passed in 0.37 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 4 passed in 0.15 seconds.
- `python -m pytest --collect-only -q` - 54 tests collected in 0.06 seconds.
- `python -m pytest -q` - 54 passed in 1.06 seconds.
- `git diff --check` - passed; Git reported LF-to-CRLF working-tree warnings for edited files.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- WebODM Task 1, Task 4/fallback, resume/reattach, quality-gate restart, and download/export branches do not yet emit or test parseable boundary events.
- QGIS external-boundary events are still free-form or absent.
- `query_survey_stats.py` does not yet prefer explicit `event=` records for analytics; it remains compatible with the existing text log parser.
- Threaded/interleaved logging behavior remains deferred until production concurrency is introduced.

### Recommended next Phase 2 task

Either extend WebODM boundary events to Task 1/Task 4/download branches, or move to QGIS command-boundary events if command start/completion/failure attribution is more valuable for operators right now.
## ADR-013 QGIS command-boundary event implementation

Date: 2026-07-17.

Implemented the first QGIS/GDAL external-boundary observability slice inside `modules/qgis/qgis_tools.py`.

### Implemented behavior

- `QGISTools` now routes its GDAL/QGIS subprocess calls through a small internal `_run_command()` helper.
- The helper emits `qgis_command_started` with `tool`, executable basename, and argument count before command execution.
- The helper emits `qgis_command_completed` with `tool`, executable basename, elapsed seconds, and return code after successful command execution.
- The helper emits `qgis_command_failed` with `tool`, executable basename, elapsed seconds, exception type, return code when available, and a bounded sanitized error message when command execution fails.
- Full command lines, credentials, private URLs, and input/output paths are not included in the parseable command-boundary event fields.
- Existing `gdalwarp`, optional `gdalinfo`, and `gdal2tiles` behavior, exception wrapping, output verification, tile generation, cleanup, and operator-facing logs are preserved.

This is intentionally a command-boundary slice. It does not execute `stage_qgis()` in tests, does not run real QGIS/GDAL, and does not introduce a JSON log format, database event journal, migration, retry redesign, checkpoint change, or operator workflow change.

### Tests changed

Added `tests/test_qgis_tools_observability.py` with fake-backed QGIS tool coverage. The tests patch the module subprocess boundary, use pytest-owned paths and temporary log files, and verify parseable `qgis_command_started`, `qgis_command_completed`, and `qgis_command_failed` events. The failure test also verifies that full input/output paths are not logged in the parseable failure event.

### Files modified

- Modified: `modules/qgis/qgis_tools.py`
- Created: `tests/test_qgis_tools_observability.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, event journal, JSON log format, retry behavior, checkpoint behavior, WebODM behavior, dependency list, real QGIS/GDAL execution, or operator workflow changed.

### Validation

- `python -m py_compile modules\qgis\qgis_tools.py tests\test_qgis_tools_observability.py` - passed.
- First `python -m pytest -q tests\test_qgis_tools_observability.py` - 1 failed, 1 passed because the test expected 12 generated `gdal2tiles` arguments; the current non-Windows command path has 11. The test expectation was corrected.
- Corrected `python -m pytest -q tests\test_qgis_tools_observability.py` - 2 passed in 0.05 seconds.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 4 passed in 0.23 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 3 passed in 0.22 seconds.
- `python -m pytest -q tests\test_suite_safety_guards.py` - 8 passed in 0.06 seconds.
- `python -m pytest --collect-only -q` - 56 tests collected in 0.07 seconds.
- `python -m pytest -q` - 56 passed in 1.20 seconds.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- QGIS `stage_qgis()` branch-level attribution is still limited to command events; disabled/skipped clip or tile branches do not yet emit explicit parseable artifact/branch events.
- WebODM Task 1, Task 4/fallback, resume/reattach, quality-gate restart, and download/export branches do not yet emit or test parseable boundary events.
- `query_survey_stats.py` does not yet prefer explicit `event=` records for analytics; it remains compatible with the existing text log parser.
- Threaded/interleaved logging behavior remains deferred until production concurrency is introduced.

### Recommended next Phase 2 task

Either extend WebODM boundary events to Task 1/Task 4/download branches, or add parser/reporting support that recognizes explicit `event=` records so the lifecycle and external-boundary events become easier to query.
## ADR-013 parser/reporting support for explicit event records

Date: 2026-07-17.

Implemented additive parser/reporting support for ADR-013 `event=<name> key=value ...` log messages in `query_survey_stats.py`.

### Implemented behavior

- Added `parse_event_message()` to parse explicit event messages using shell-like quoting compatible with `shared.logging.log_event()` output.
- `parse_log_events()` now preserves the existing raw `message` field and additionally returns `event` and `fields` for parseable ADR-013 records.
- Historical free-form log messages remain supported and are returned with `event=None` and empty `fields`.
- `extract_log_insights()` now counts explicit events, recognizes `run_paused` and `run_aborted` records for timeline reporting, maps `qgis_command_completed` events for `gdalwarp` and `gdal2tiles` into QGIS timing insights, and converts explicit `run_failed`, `stage_failed`, and `qgis_command_failed` events into reportable stage errors.
- The survey summary now includes an `Explicit events (logs)` row so operators can see whether ADR-013 records are present for the selected runs.
- Existing regex fallbacks for historical WebODM upload, QGIS clip/tile, pause/abort, and error logs remain in place.

No database schema, migration, JSON log format, event journal, pipeline behavior, WebODM behavior, QGIS/GDAL execution behavior, dependency list, or operator command-line interface changed.

### Tests changed

`tests/test_logging_context_ownership.py` now covers explicit event parser/reporting behavior. It verifies that generated `log_event()` output round-trips through `parse_log_events()` with quoted fields intact, that historical free-form parser compatibility remains intact, and that `extract_log_insights()` prefers explicit pause, QGIS command completion, and QGIS command failure records for reporting.

### Files modified

- Modified: `query_survey_stats.py`
- Modified: `tests/test_logging_context_ownership.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

### Validation

- `python -m py_compile query_survey_stats.py tests\test_logging_context_ownership.py` - passed.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 6 passed in 0.18 seconds.
- `python -m pytest -q tests\test_qgis_tools_observability.py` - 2 passed in 0.05 seconds.
- `python -m pytest -q tests\test_stage_runner_orchestration.py` - 9 passed in 0.34 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 3 passed in 0.23 seconds.
- `python -m pytest --collect-only -q` - 58 tests collected in 0.08 seconds.
- `python -m pytest -q` - 58 passed in 1.15 seconds.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, or destructive cleanup operation was used.

### Remaining Phase 2 risks

- The report tables do not yet include a dedicated explicit-event summary section; explicit events currently feed existing pause/error/QGIS timing insights and are counted internally.
- WebODM Task 1, Task 4/fallback, resume/reattach, quality-gate restart, and download/export branches do not yet emit or test parseable boundary events.
- QGIS `stage_qgis()` branch-level attribution is still limited to command events; disabled/skipped clip or tile branches do not yet emit explicit parseable artifact/branch events.
- Threaded/interleaved logging behavior remains deferred until production concurrency is introduced.

### Recommended next Phase 2 task

Extend WebODM boundary events to the remaining Task 1, Task 4/fallback, resume/reattach, quality-gate restart, and download/export branches, or add a small operator-facing explicit-event summary to `query_survey_stats.py` if reporting visibility is more valuable first.

## Phase 2 completion assessment

Date: 2026-07-20.

Phase 2 logging and observability is complete enough to move to Phase 3 planning. The remaining WebODM branch and QGIS artifact/branch events are intentionally deferred because the Phase 2 acceptance criteria are now supported by owned logger isolation, threaded interleaving coverage, parseable lifecycle/external-boundary events, parser compatibility, and direct run-control event tests.

### Completion evidence

- `shared.logging.close_logger()` defines the handler lifecycle rule for tests and other owned logger callers: remove handlers, close them, clear filters, and allow later reconfiguration of the same concrete logger identity.
- `tests/test_logging_context_ownership.py` now proves threaded/interleaved owned loggers do not exchange run IDs, stage names, handler destinations, or parseable event records.
- `tests/test_rgb_pipeline_single_stage_execution.py` now directly exercises `run_paused`, `run_aborted`, and WebODM UI `run_canceled` event records without running real stages or external services.
- Existing tests continue to prove run start/completion/failure events, StageRunner lifecycle events, WebODM Task 2 boundary events, QGIS command boundary events, and explicit event parser/reporting support.

### Files modified

- Modified: `shared/logging.py`
- Modified: `tests/test_logging_context_ownership.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

### Validation

- `python -m py_compile shared\logging.py tests\test_logging_context_ownership.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_logging_context_ownership.py` - 8 passed in 0.22 seconds.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 6 passed in 0.46 seconds.
- `python -m pytest --collect-only -q` - 63 tests collected in 0.07 seconds.
- `python -m pytest -q` - 63 passed in 1.19 seconds.
- `git diff --check` - passed; Git reported LF-to-CRLF working-tree warnings for edited files.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Residual risks carried into later phases

- WebODM Task 1, Task 4/fallback, resume/reattach, quality-gate restart, and download/export branches still do not all emit dedicated parseable boundary events.
- QGIS `stage_qgis()` branch-level attribution is still limited to command events; disabled/skipped clip or tile branches do not emit dedicated parseable artifact/branch events.
- StageRunner still propagates pause/abort/direct cancellation signals without terminally finalizing the active stage row; typed control exceptions and active-stage terminal semantics remain a later control-state design topic.
- Cross-process file-handler behavior and subprocess-output attribution remain future concerns for the worker/scheduler phases.

### Recommended next task

Review the data segregation workspace metadata slice. Next migrate cross-run filter inputs and outputs toward run-owned workspace paths while preserving legacy published paths until publish activation exists.

## Phase 3 artifact inventory and ADR-003 proposal

Date: 2026-07-20.

### Planning outcome

Prepared the Phase 3 artifact inventory and promoted ADR-003 from Pending to Proposed for review. The proposed direction is a run-scoped workspace for mutable artifacts plus a manifest-backed publish step into legacy-compatible survey paths.

### Inventory coverage

The inventory covers:

- External field-data inputs.
- Shared survey-published paths under `<surveys_root>/<year>/<survey_id>/rgb/`.
- Data segregation outputs, boundary files, cross-run filter outputs, WebODM exports, QGIS clipped rasters, tile directories, upload caches, checkpoints, SQLite path state, logs, and map export consumers.
- Current compatibility consumers in `modules/map_export/`, `map.py`, and `query_survey_stats.py`.

### Files modified or created

- Created: `docs/refactor/PHASE3_ARTIFACT_INVENTORY.md`
- Modified: `docs/refactor/DECISIONS.md`
- Modified: `docs/refactor/CURRENT_STATUS.md`

No runtime code, database schema, migration, path behavior, retry behavior, cleanup behavior, map export behavior, or operator workflow changed.

### Validation

Validation for this docs-only planning task is limited to Markdown/content review and repository diff checks. Runtime tests are not required because no executable code changed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- ADR-003 is now Accepted, but only the reusable groundwork exists; runtime stages still use legacy shared survey paths.
- Publish semantics for Windows network shares and cross-volume replacement still need detailed design.
- Workspace retention and cleanup policy is intentionally deferred.
- Historical SQLite outputs and logs may contain legacy absolute paths and need compatibility handling.
- Map export should remain legacy-compatible and may later prefer publication manifests when present.

## Phase 3 path-planning and staged-publication groundwork

Date: 2026-07-20.

### Implemented behavior

Accepted ADR-003 and added the first reusable Phase 3 helper without wiring it into runtime stage execution.

`shared/artifacts.py` now provides:

- `plan_run_workspace()` for run-ID-owned workspace paths.
- `plan_published_survey()` for legacy-compatible published survey paths.
- `create_run_workspace()` for creating only directories contained by the run workspace root.
- `PublicationArtifact` and `prepare_publication()` for staging a complete publish set under the run workspace and writing `publication.json` only after artifact staging succeeds.

The staged-publication helper intentionally does not modify the published survey tree. This preserves current production behavior while giving later stage migrations a tested contract for preparing publishable artifacts.

### Files modified or created

- Created: `shared/artifacts.py`
- Created: `tests/test_phase3_artifact_workspace.py`
- Modified: `docs/refactor/DECISIONS.md`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, existing RGBPipeline stage path, retry behavior, cleanup behavior, map export behavior, operator workflow, or external-service behavior changed.

### Validation

- `python -m py_compile shared\artifacts.py tests\test_phase3_artifact_workspace.py` - passed.
- First `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 12 passed.
- Final `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 12 passed.
- `python -m pytest --collect-only -q` - 75 tests collected.
- `python -m pytest -q` - 75 passed in 1.29 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Runtime stages are not yet using the run workspace helper.
- There is no activation step that replaces or points consumers at the staged publish set.
- Directory activation across Windows network shares remains intentionally unimplemented.
- Retention and cleanup of staged or superseded workspaces remains deferred.
- Map export still uses legacy path discovery only.

## Phase 3 RGBPipeline workspace layout seam

Date: 2026-07-20.

### Implemented behavior

Wired the Phase 3 artifact helper into `RGBPipeline` as a backward-compatible constructor and resume seam.

`RGBPipeline` now:

- accepts optional `workspace_root`, `workspace_layout`, and `published_layout` parameters;
- computes a default run workspace layout at `<base_dir>/data/workspaces/<run_id>` without creating directories;
- keeps `published_layout` unset until the actual survey `rgb` path is known;
- derives `published_layout` from the actual `survey_path` after data segregation or state hydration, preserving year-subdir and no-year-subdir layouts; and
- rejects ambiguous `workspace_root` plus `workspace_layout` inputs before opening default collaborators.

This does not move any runtime stage output yet. Existing stages still use the legacy shared survey tree until each stage is migrated with its own tests.

### Files modified or created

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `shared/artifacts.py`
- Modified: `tests/test_phase3_artifact_workspace.py`
- Modified: `tests/test_rgb_pipeline_construction.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, existing stage output path, retry behavior, cleanup behavior, map export behavior, operator workflow, or external-service behavior changed.

### Validation

- `python -m py_compile shared\artifacts.py pipelines\rgb_pipeline.py tests\test_phase3_artifact_workspace.py tests\test_rgb_pipeline_construction.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_phase3_artifact_workspace.py` - 13 passed.
- First `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 6 passed.
- Final `python -m pytest -q tests\test_rgb_pipeline_construction.py` - 7 passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py` - 6 passed.
- `python -m pytest -q tests\test_phase3_artifact_workspace.py tests\test_rgb_pipeline_construction.py tests\test_rgb_pipeline_single_stage_execution.py` - 26 passed in 0.64 seconds.
- `python -m pytest --collect-only -q` - 79 tests collected.
- `python -m pytest -q` - 79 passed in 1.35 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Runtime stages still write to legacy shared survey paths.
- Run workspace directories are planned but not created by `RGBPipeline` construction.
- No publish activation step exists yet.
- No map export manifest preference exists yet.
- Retention and cleanup remain deferred.

## Phase 3 data segregation workspace metadata slice

Date: 2026-07-20.

### Implemented behavior

`RGBPipeline.stage_data_segregation()` now prepares the run workspace and records artifact ownership metadata while preserving the existing legacy data segregation behavior.

The stage now:

- creates the run-owned workspace directory tree through `create_run_workspace(self.workspace_layout)` when data segregation starts;
- continues to call `run_data_segregation()` with the existing source, survey root, year, override, and force parameters;
- continues to use the returned legacy `survey_path` as `rgb_path`;
- continues to rename the KML in the published survey boundary directory;
- preserves existing top-level `survey_id`, `survey_path`, and other data segregation output keys; and
- adds additive `workspace` and `published` dictionaries to the successful stage output for later stage migration and reporting.

The failure path prepares the run-owned workspace before the stage dependency runs, but still records no successful `data_segregation` output when the stage fails after retries.

### Files modified or created

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `shared/artifacts.py`
- Modified: `tests/test_phase3_artifact_workspace.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, legacy survey output path, data segregation copy behavior, retry behavior, cleanup behavior, map export behavior, operator workflow, or external-service behavior changed.

### Validation

- `python -m py_compile shared\artifacts.py pipelines\rgb_pipeline.py tests\test_phase3_artifact_workspace.py tests\test_rgb_pipeline_construction.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_phase3_artifact_workspace.py tests\test_rgb_pipeline_construction.py tests\test_rgb_pipeline_single_stage_execution.py` - 27 passed in 0.70 seconds.
- `python -m pytest --collect-only -q` - 80 tests collected.
- `python -m pytest -q` - 80 passed in 1.32 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Data segregation still creates and populates the legacy published survey tree directly.
- Cross-run filter, KML, WebODM, QGIS, and map export still consume legacy paths.
- No publish activation step exists yet.
- Workspace retention and cleanup remain deferred.
- Failed data segregation attempts can leave an empty run workspace, which is intentional evidence for now but needs later retention policy.

## Phase 3 cross-run filter workspace migration

Date: 2026-07-20.

### Implemented behavior

`RGBPipeline.stage_cross_run_image_filter()` now treats the run workspace as the first writable destination for mutable filter outputs, then mirrors a successful result back into the existing legacy survey image folders.

The stage now:

- creates the run-owned workspace directory tree through `create_run_workspace(self.workspace_layout)` when cross-run filtering starts;
- keeps the raw-image input at the legacy `rgb/images/raw` location for compatibility with the current data segregation output;
- writes kept images to `workspace/images/path` and excluded images to `workspace/images/cross-runs`;
- mirrors only successful workspace outputs back to the legacy `rgb/images/path` and `rgb/images/cross-runs` folders;
- replaces legacy image output folders through a temporary-and-backup rename sequence instead of deleting them before filter success;
- preserves existing top-level `output_dir`, `excluded_dir`, crossrun flag, experiment label, and raw-cleanup result semantics; and
- adds additive `workspace` and `published` dictionaries to the stage output for later publication/reporting work.

The disabled-filter branch now also copies raw images through the workspace before mirroring to legacy output folders. If the enabled filter raises before completion, legacy output folders remain untouched and any partial workspace files are left as diagnostic evidence.

### Files modified or created

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, raw image input path, experiment naming default, retry behavior, map export behavior, operator workflow, external-service behavior, or production dependency changed.

### Validation

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - passed.
- First `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - failed because the single-stage test `exifread` stub lacked a module spec and because the fake filter used an unrealistic `mkdir(..., exist_ok=False)` after workspace creation.
- Final `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - 30 passed in 1.00 seconds.
- `python -m pytest --collect-only -q` - 83 tests collected.
- `python -m pytest -q` - 83 passed in 1.48 seconds.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Data segregation still creates and populates the legacy published survey tree directly.
- KML, WebODM, QGIS, and map export still primarily consume or produce legacy paths.
- Cross-run filter legacy mirroring is compatibility glue, not the final manifest-backed publish activation step.
- If a mirror operation fails after creating a temporary mirror directory, the temporary path can remain for diagnosis and the next retry will fail fast until it is inspected or removed.
- Workspace retention and cleanup remain deferred.

## Phase 3 KML boundary workspace migration

Date: 2026-07-20.

### Implemented behavior

`RGBPipeline.stage_kml_boundary()` now treats the run workspace as the first writable destination for derived boundary artifacts, then mirrors successful derived files back into the existing legacy survey boundary folder.

The stage now:

- creates the run-owned workspace directory tree through `create_run_workspace(self.workspace_layout)` when KML boundary processing starts;
- keeps KML/KMZ input discovery at the legacy `rgb/boundary` folder for compatibility with the current data segregation output;
- writes derived GeoJSON and CSV files to `workspace/boundary`;
- mirrors successful derived GeoJSON and CSV files back to legacy `rgb/boundary` paths;
- replaces individual legacy derived files through a temporary-and-backup rename sequence;
- preserves existing top-level `processed_files`, `geojson_dir`, `csv_dir`, `boundary_available`, and `boundary_geojson_path` semantics using legacy paths; and
- adds additive `workspace` and `published` dictionaries to the stage output for later publication/reporting work.

If KML processing raises before returning a successful summary, pre-existing legacy GeoJSON and CSV files remain untouched and partial workspace files are left as diagnostic evidence. If no valid boundary is produced, the stage preserves the legacy no-boundary shape while still reporting workspace and published path metadata.

### Files modified or created

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, KML input path, WebODM behavior, QGIS/GDAL behavior, retry behavior, map export behavior, operator workflow, external-service behavior, or production dependency changed.

### Validation

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - 33 passed in 0.97 seconds.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Data segregation still creates and populates the legacy published survey tree directly.
- WebODM, QGIS, and map export still primarily consume or produce legacy paths.
- KML boundary legacy mirroring is compatibility glue, not the final manifest-backed publish activation step.
- A failure during a later individual file mirror could leave an earlier derived file mirrored; full multi-file publish activation remains deferred to the manifest-backed publish step.
- Workspace retention and cleanup remain deferred.

## Phase 3 WebODM orthomosaic workspace migration

Date: 2026-07-20.

### Implemented behavior

`RGBPipeline.stage_webodm()` and `RGBPipeline.run_webodm_fallback_task()` now treat the run workspace as the first writable destination for WebODM orthomosaic exports, then mirror successful orthomosaic files back into the existing legacy survey orthomosaic folder.

This slice intentionally migrates orthomosaic exports only. DEM, ODM, point-cloud, all-assets ZIP, WebODM task creation, upload-cache behavior, quality-gate behavior, and WebODM retry/resume semantics remain unchanged.

The WebODM orthomosaic paths now:

- create the run-owned workspace directory tree through `create_run_workspace(self.workspace_layout)` before WebODM export handling;
- export Task 1, Task 2, and fallback/Task 4 orthomosaics to task-specific workspace folders under `workspace/webodm/ortho/<task_key>`;
- mirror successful orthomosaic files back to the same legacy directory that the pre-migration branch would have used, including production `rgb/ortho` and experiment task-specific `ortho` directories;
- replace individual legacy orthomosaic files through the existing temporary-and-backup file mirror helper;
- preserve existing `downloads.<task>.orthomosaic` and `selected_orthomosaic.source_path` semantics by storing legacy-compatible mirrored paths; and
- add additive `workspace.webodm_ortho` and `published.webodm_ortho` path metadata for exported orthomosaics.

If orthomosaic export raises before returning a successful path, pre-existing legacy orthomosaic files remain untouched and partial workspace files are left as diagnostic evidence.

### Files modified or created

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, WebODM task creation behavior, upload-cache behavior, QGIS/GDAL execution behavior, retry behavior, map export behavior, operator workflow, external-service behavior, or production dependency changed.

### Validation

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- First `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - 35 passed in 1.07 seconds.
- Final focused `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - 36 passed in 0.95 seconds.
- `python -m pytest --collect-only -q` - 89 tests collected.
- `python -m pytest -q` - 89 passed in 1.74 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Data segregation still creates and populates the legacy published survey tree directly.
- WebODM DEM, ODM, point-cloud, and all-assets ZIP outputs still primarily use legacy paths.
- QGIS and map export still primarily consume or produce legacy paths.
- WebODM orthomosaic legacy mirroring is compatibility glue, not the final manifest-backed publish activation step.
- Workspace retention and cleanup remain deferred.
## Phase 3 QGIS clipped orthomosaic and tile workspace migration

Date: 2026-07-20.

### Implemented behavior

`RGBPipeline.stage_qgis()` now treats the run workspace as the first writable destination for QGIS-produced clipped orthomosaics and tile directories, then mirrors successful artifacts back into the existing legacy survey paths.

The QGIS paths now:

- create the run-owned workspace directory tree through `create_run_workspace(self.workspace_layout)` when QGIS processing starts;
- keep selected orthomosaic input and boundary GeoJSON discovery legacy-compatible for the current WebODM/data-segregation flow;
- write clipped orthomosaics to `workspace/qgis/clipped/ortho` before mirroring them to legacy `rgb/qgis/clipped/ortho`;
- generate round-corners or soft-corners tiles into `workspace/qgis/tiles/<mode>` before mirroring them to legacy `rgb/tiles/ortho/<mode>`;
- keep optional local QGIS staging as a performance/network-safety layer, but copy local staged tiles back into the run workspace before any legacy mirror;
- replace legacy tile directories through the existing temporary-and-backup directory mirror helper;
- preserve existing legacy-compatible `selected_orthomosaic.clipped_path`, `selected_orthomosaic.tiles_dir`, `clip.output`, and `tiles.output_dir` values; and
- add additive `workspace` and `published` path metadata for QGIS clipped and tile outputs.

If clipping raises before returning successfully, pre-existing legacy clipped orthomosaic and tile outputs remain untouched and any partial workspace output remains as diagnostic evidence. If tile generation fails after a successful clip mirror, legacy tile outputs remain untouched; full multi-artifact publish atomicity remains deferred to the later manifest-backed publish activation step.

### Files modified

- Modified: `pipelines/rgb_pipeline.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, selected orthomosaic input path, WebODM behavior, real QGIS/GDAL execution behavior, retry behavior, map export behavior, operator workflow, external-service behavior, or production dependency changed.

### Validation

- `python -m py_compile pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py -k qgis` - 2 passed, 15 deselected.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - 38 passed in 1.16 seconds.
- `python -m pytest --collect-only -q` - 91 tests collected in 0.07 seconds.
- `python -m pytest -q` - 91 passed in 2.17 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Data segregation still creates and populates the legacy published survey tree directly.
- WebODM DEM, ODM, point-cloud, and all-assets ZIP outputs still primarily use legacy paths.
- Map export still primarily consumes legacy paths.
- QGIS legacy mirroring is compatibility glue, not the final manifest-backed publish activation step.
- A failure after one QGIS artifact has mirrored can leave another artifact unpublished; full multi-artifact publish activation remains deferred.
- Workspace retention and cleanup remain deferred.
## Phase 3 WebODM pointcloud and all-assets ZIP workspace migration

Date: 2026-07-20.

### Implemented behavior

`RGBPipeline.stage_webodm()` now treats the run workspace as the first writable destination for the remaining currently reachable WebODM Task 2 non-orthomosaic downloads: pointcloud LAZ/PCD files and the all-assets ZIP. Successful artifacts are mirrored back into the existing legacy survey paths for compatibility.

This slice intentionally does not activate DEM downloads. The existing production code has `dem_do_download = False`, so DEM configuration remains dormant. That behavior was preserved to avoid silently introducing new WebODM/GDAL work in production.

The WebODM non-orthomosaic paths now:

- include a run-owned `workspace/webodm/3d` layout directory for pointcloud outputs;
- export Task 2 pointcloud files to `workspace/webodm/3d/task2` before mirroring LAZ/PCD/PLY files that exist to legacy `rgb/3d`;
- download Task 2 all-assets ZIP files to `workspace/webodm/odm/task2` before mirroring the ZIP to legacy `rgb/odm`;
- preserve existing legacy-compatible `downloads.task2.pointcloud_laz`, `downloads.task2.pointcloud_pcd`, `downloads.task2.pointcloud_ply`, `downloads.task2.pointcloud_asset_type`, and `downloads.task2.all_assets_zip` values; and
- add additive `workspace.webodm_3d`, `published.webodm_3d`, `workspace.webodm_odm`, and `published.webodm_odm` metadata for migrated files.

If pointcloud export raises before returning successfully, pre-existing legacy pointcloud files remain untouched and partial workspace files remain as diagnostic evidence. If an all-assets ZIP endpoint is unavailable or returns false through the safe downloader, the legacy ZIP path is not updated.

### Files modified

- Modified: `shared/artifacts.py`
- Modified: `pipelines/rgb_pipeline.py`
- Modified: `tests/test_rgb_pipeline_single_stage_execution.py`
- Modified: `docs/refactor/CURRENT_STATUS.md`
- Modified: `docs/refactor/TEST_STRATEGY.md`

No database schema, migration, WebODM task creation behavior, upload-cache behavior, orthomosaic behavior, QGIS/GDAL execution behavior, retry behavior, map export behavior, operator workflow, external-service behavior, production dependency, or dormant DEM behavior changed.

### Validation

- `python -m py_compile shared\artifacts.py pipelines\rgb_pipeline.py tests\test_rgb_pipeline_single_stage_execution.py` - passed.
- `python -m pytest -q tests\test_phase3_artifact_workspace.py tests\test_rgb_pipeline_single_stage_execution.py -k "webodm_task2_remaining or webodm_pointcloud_failure"` - 2 passed, 31 deselected.
- `python -m pytest -q tests\test_rgb_pipeline_single_stage_execution.py tests\test_rgb_pipeline_construction.py tests\test_phase3_artifact_workspace.py` - 40 passed in 1.31 seconds.
- `python -m pytest --collect-only -q` - 93 tests collected in 0.08 seconds.
- `python -m pytest -q` - 93 passed in 2.21 seconds.
- `git diff --check` - passed.

No external test, real pipeline execution, WebODM request, QGIS/GDAL subprocess, keyboard hook, interactive input, production path, production SQLite database, real survey root, network storage, git remote operation, or destructive cleanup operation was used.

### Remaining Phase 3 risks

- Data segregation still creates and populates the legacy published survey tree directly.
- DEM downloads remain dormant because production currently hard-disables them.
- Map export still primarily consumes legacy paths.
- WebODM legacy mirroring is compatibility glue, not the final manifest-backed publish activation step.
- A failure after one artifact has mirrored can leave another artifact unpublished; full multi-artifact publish activation remains deferred.
- Workspace retention and cleanup remain deferred.
