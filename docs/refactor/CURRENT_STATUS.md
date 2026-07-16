# Refactor Current Status

## Summary

- **Refactor status:** Planning
- **Current phase:** Phase 0 — Baseline and test safety
- **Completed work:** Initial repository scalability audit and creation of AGENTS.md
- **Current task:** Repository inspection and refactor-document initialization
- **Production code changed:** No
- **Next recommended task:** Investigate and isolate test-import side effects

## Completed tasks

- Read the repository-level `AGENTS.md` and confirmed that it does not contradict this documentation-only task.
- Inspected pipeline orchestration, stage execution, state persistence, checkpoints, logging, pause/abort controls, WebODM upload behavior, QGIS/GDAL processing, filesystem operations, entry points, and existing tests.
- Recorded evidence and confirmation status for all 13 known scalability and concurrency risks.
- Initialized the phased refactor plan, architectural decision log, current status, and test strategy.
- Preserved all pre-existing working-tree changes.

## Current task

Repository inspection and refactor-document initialization. This task changes planning documentation only and does not authorize fixes, migrations, dependency installation, or pipeline execution.

## Blockers

- Safe normal test discovery is not yet established.
- No test framework or canonical safe test command is declared in the repository.
- External-service and production-path boundaries are not injectable throughout the current code.
- Several architectural decisions in `DECISIONS.md` must be resolved before persistent-state or concurrency implementation.

## Known test failures

No test command was run because current discovery can execute real side effects.

Static inspection found these likely or definite problems:

- `tests/test_rgb_pipeline.py` executes a real pipeline at module import using `.env` paths.
- `tests/query_test.py` opens a hard-coded database outside this repository at module import.
- `tests/reset_environment.py` deletes `data/logs` and `data/pipeline.db` when manually executed.
- `tests/test_experiment_naming.py` contains no assertions and imports `resolve_rgb_exp01_names` from `shared`; the current `shared/__init__.py` does not export that symbol, so the import appears invalid.
- No `pytest`, `unittest`, `tox`, `nox`, or CI test configuration was found, and no test framework is declared in `requirements.txt`.

These observations are not reported as test-run results.

## Remaining risks

All audited risks remain open:

1. Single-threaded orchestration.
2. Unsafe shared output ownership.
3. Survey ID allocation race.
4. SQLite write contention.
5. Process-global mutable logger context.
6. Broad retries that may repeat non-idempotent operations.
7. Non-atomic checkpoint and state updates.
8. Coarse pause and abort handling.
9. Large-memory and file-descriptor pressure during WebODM uploads.
10. Unbounded filesystem scanning, copying, and tile generation.
11. Stale-stage ambiguity after forced reruns.
12. Interactive quality gate blocking unattended execution.
13. Test modules with external side effects during import.

See `SCALABILITY_AUDIT.md` for evidence, severity, relationships, and investigation status.

## Next approved task

None yet. The next recommended task is a narrowly scoped Phase 1 investigation and plan to isolate test-import side effects. Production or persistent-data edits still require explicit approval under `AGENTS.md`.

## Last validation performed

- **Date:** 2026-07-16
- **Validation type:** Static repository inspection and documentation scope validation.
- **Commands:** Read-only file listing, targeted text searches, focused source reads, and Git status/diff inspection.
- **Tests run:** None.
- **External services contacted:** None.
- **Production pipeline commands run:** None.
- **Destructive commands run:** None.
- **Validation limitation:** Runtime concurrency limits, WebODM API capabilities, network-share semantics, and production workload thresholds were not measured.
