# Refactor Current Status

## Summary

- **Refactor status:** In progress
- **Current phase:** Phase 1 - Test isolation and safety baseline
- **Completed work:** Canonical pytest configuration, safe operator tools, reusable temporary test infrastructure, fake WebODM behavior, and suite-wide default safety guards
- **Current task:** Temporary filesystem/SQLite fixtures, fake WebODM, and default-suite external-effect denial
- **Production code changed:** No
- **Next recommended task:** Evaluate minimal configuration, repository, and WebODM injection seams for hermetic RGBPipeline construction

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
