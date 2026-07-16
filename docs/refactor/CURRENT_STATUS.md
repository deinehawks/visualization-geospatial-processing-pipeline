# Refactor Current Status

## Summary

- **Refactor status:** In progress
- **Current phase:** Phase 1 - Test isolation and safety baseline
- **Completed work:** Canonical pytest configuration and isolation of the audited import-time test scripts
- **Current task:** Safe default discovery, operator-tool hardening, and baseline regression coverage
- **Production code changed:** No
- **Next recommended task:** Add suite-wide network, subprocess, credential, and production-path denial fixtures without changing production behavior

## Completed tasks

- Accepted pytest as the canonical default runner in `DECISIONS.md`.
- Added `pytest.ini` with `tests/` as the test path, an `external` marker, and external-test exclusion by default.
- Added pinned pytest 8.4.2 using the existing `requirements.txt` convention.
- Converted experiment naming samples into four parameterized tests covering both original inputs, filter enabled/disabled, and `-DJIFP` normalization.
- Moved the real RGB workflow from `tests/test_rgb_pipeline.py` to `tools/run_rgb_pipeline.py`; imports are lazy and execution requires `--allow-external-run`.
- Moved `tests/query_test.py` to `tools/query_pipeline_db.py`; the database is an explicit argument, missing files are rejected, SQLite opens in read-only URI mode, and connections close in `finally`.
- Moved `tests/reset_environment.py` to `tools/reset_environment.py`; an explicit root, sentinel, and destructive flag are required, containment is checked, and only `logs/`, `pipeline.db`, `pipeline.db-wal`, and `pipeline.db-shm` are removed.
- Added temporary SQLite and filesystem tests plus import traps for pipeline construction, dotenv, WebODM/network access, subprocesses, SQLite, keyboard hooks, input, deletion, and writes.
- No real pipeline, WebODM, QGIS/GDAL, production SQLite, production survey directory, or real runtime cleanup was used.

## Files moved or created

Moved and hardened:

- `tests/test_rgb_pipeline.py` -> `tools/run_rgb_pipeline.py`
- `tests/query_test.py` -> `tools/query_pipeline_db.py`
- `tests/reset_environment.py` -> `tools/reset_environment.py`

Created:

- `pytest.ini`
- `tests/test_import_safety.py`
- `tests/test_query_pipeline_db.py`
- `tests/test_reset_environment.py`

Modified:

- `requirements.txt`
- `tests/test_experiment_naming.py`
- `docs/refactor/CURRENT_STATUS.md`
- `docs/refactor/TEST_STRATEGY.md`

## Validation performed

Date: 2026-07-16.

- `python -m py_compile tests/test_experiment_naming.py tests/test_query_pipeline_db.py tests/test_reset_environment.py tests/test_import_safety.py tools/run_rgb_pipeline.py tools/query_pipeline_db.py tools/reset_environment.py` - passed.
- `python -m pytest -q tests/test_experiment_naming.py` - 4 passed.
- `python -m pytest -q tests/test_query_pipeline_db.py` - 2 passed.
- `python -m pytest -q tests/test_reset_environment.py` - 6 passed.
- First `python -m pytest -q tests/test_import_safety.py` - collection error because the test imported an unavailable optional `keyboard` package. The test was corrected to use synthetic trap modules.
- Corrected `python -m py_compile tests/test_import_safety.py` - passed.
- Corrected `python -m pytest -q tests/test_import_safety.py` - 1 passed.
- `python -m pytest --collect-only -q` - 13 tests collected.
- `python -m pytest -q` - 13 passed in 0.07 seconds.
- External tests run: none.
- External services contacted: none.
- Production pipeline commands run: none.
- Destructive operator scripts run: none.

## Remaining Phase 1 risks

Phase 1 is not complete.

- The default suite does not yet install a suite-wide network-deny or subprocess-deny fixture; current regression coverage proves the moved tools are import-safe.
- Production configuration and service boundaries are not generally injectable, so broader pipeline component tests may require production-code seams. Those seams were not implemented in this task.
- Credential isolation and production-path overlap checks are not yet enforced globally.
- No external integration tests exist yet; the marker and default exclusion policy are configured but not exercised against a marked test.
- Safe temporary database coverage is currently limited to the query operator tool, not repository migrations or `PipelineRepo`.
- The cleanup tool's containment logic is tested without platform-dependent symlink creation; actual symlink/junction behavior remains an untested path.
- The real pipeline operator script was intentionally not executed.

## Next recommended task

Add a narrowly scoped pytest safety layer that denies unexpected network and subprocess access, clears credentials, and supplies temporary configuration roots by default. If that requires a production-code injection seam, document and request separate approval before changing production code.
