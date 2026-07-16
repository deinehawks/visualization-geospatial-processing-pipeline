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

### Current frameworks

No active test framework is confirmed:

- `requirements.txt` does not declare `pytest`, and no repository test dependency file was found.
- No `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`, `noxfile.py`, Makefile test target, or CI workflow was found.
- No use of `unittest` or pytest APIs was found in the test files.
- File names such as `test_rgb_pipeline.py`, `test_experiment_naming.py`, and `query_test.py` resemble common discovery patterns, but their contents are top-level scripts rather than isolated test functions.

The actual historical execution command is therefore **unknown and requires confirmation**. The files appear runnable directly with Python, but this must not be treated as the desired test workflow.

### Existing directories and naming conventions

All current test-related scripts are under `tests/`:

| File | Current form | Discovery/safety concern |
|---|---|---|
| `tests/test_rgb_pipeline.py` | Top-level end-to-end script | Common test name; starts a real pipeline during import |
| `tests/test_experiment_naming.py` | Top-level print loop | Common test name; no assertions; current import appears invalid |
| `tests/query_test.py` | Top-level database query script | Matches some `*_test.py` discovery patterns; hard-coded external DB path |
| `tests/reset_environment.py` | Top-level cleanup script | Not a normal default test name, but deletes repository data when run |
| `tests/__init__.py` | Empty package marker | Allows test files to be imported as modules |

There is no separation between unit, integration, external integration, smoke, or operational maintenance scripts.

### How tests are currently executed

No canonical command is documented. `README.md` is empty, and no test-runner configuration or automation was found. Do not run broad discovery until Phase 1 isolates import-time effects.

### Import-time real pipeline and external effects

#### `tests/test_rgb_pipeline.py`

At import time it:

1. Calls `load_pipeline_config()`, which loads `.env` and validates configured production-like paths.
2. Selects a hard-coded survey folder under `FIELD_DATA_ROOT`.
3. Constructs `RGBPipeline`; construction creates `data/logs`, initializes/migrates `data/pipeline.db`, and creates a run record.
4. Calls `pipeline.run(resume=False)`, which can copy data, submit WebODM tasks, invoke QGIS/GDAL, write survey outputs, and update state.

This file must not be imported by normal discovery in its current form.

#### `tests/query_test.py`

At import time it opens a hard-coded database at `E:\dev\projects\automation-pipeline-viz\data\pipeline.db` and queries a hard-coded run ID. The selects are read-only, but the default SQLite connect mode can create a missing database file and the path is outside this repository.

#### `tests/reset_environment.py`

When executed, it recursively deletes `data/logs` and unlinks `data/pipeline.db` relative to the current working directory. It has no temporary-root requirement, confirmation guard, or main guard.

#### `tests/test_experiment_naming.py`

At import time it prints sample naming results and makes no assertions. It imports `resolve_rgb_exp01_names` from `shared`, but current `shared/__init__.py` deliberately exports no utilities, so the import appears to fail. This is a static finding; the file was not executed.

### Database-testing behavior

- No test creates a temporary SQLite database.
- The pipeline script uses the default `data/pipeline.db` through `RGBPipeline` and `shared.paths.db_path`.
- The query script uses a hard-coded database outside the repository.
- The reset script deletes the default repository database.
- No tests cover transactions, concurrent writers, busy timeouts, migrations, attempt ordering, stale attempts, or recovery.

### Filesystem-testing behavior

- No test uses a temporary directory fixture.
- The pipeline script derives real source and survey roots from `.env`.
- The reset script deletes fixed relative paths.
- No sentinel/allowlist guard verifies that writes and deletes remain inside a test-owned root.
- No tests cover concurrent output ownership, atomic publication, copy interruption, disk exhaustion, or network-share behavior.

### WebODM and other external-service behavior

- The pipeline script can authenticate to and submit work to configured WebODM.
- Pipeline preflight performs real HTTP requests when the relevant stage runs.
- WebODM is not faked or mocked in current tests.
- QGIS/GDAL/PDAL subprocesses are not faked or mocked.
- No explicit opt-in marker, separate environment, credential gate, or external-test command is defined.

## Desired test architecture

Adopt one approved framework and a layout that makes test level and external effects explicit. The framework choice remains pending in `DECISIONS.md`; examples below describe capabilities rather than approving a dependency.

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
