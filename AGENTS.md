# AGENTS.md

## Project overview

This repository contains a geospatial processing pipeline that coordinates:

- Local and network filesystem operations
- SQLite state and checkpoint persistence
- QGIS and GDAL subprocesses
- WebODM projects, tasks, uploads, and outputs
- Large UAV and geospatial datasets
- Pause, resume, abort, retry, and recovery behavior
- Operator tools and automated tests

The pipeline can access large datasets, shared storage, external services, persistent databases, and destructive filesystem operations.

All changes must prioritize:

1. Data integrity
2. Safe recovery
3. Resumability
4. Traceability
5. Backward compatibility
6. Operational safety
7. Test isolation
8. Minimal and reviewable changes

## Authority and safety boundary

Full tool or filesystem permission does not imply authorization to use production resources.

Unless the current task explicitly authorizes an operation, treat all of the following as protected:

- Real survey datasets
- Production and staging databases
- Network-mounted storage
- Shared output directories
- Existing WebODM projects and tasks
- Production `.env` files and credentials
- QGIS and GDAL executables
- Operator workstations
- Upload caches
- Generated orthomosaics, point clouds, tiles, and reports
- Pause, resume, abort, and checkpoint files
- Git remotes and protected branches

Never infer authorization for destructive, external, production, or persistent operations from tool access alone.

## Mandatory prohibitions

Do not perform any of the following unless the current task explicitly authorizes it:

- Run the real RGB or geospatial pipeline
- Start a real WebODM project or task
- Upload imagery to WebODM
- Download or overwrite real WebODM outputs
- Execute QGIS, GDAL, ODM, or other external geospatial commands
- Connect to production or staging databases
- Modify `data/pipeline.db` or another persistent database
- Load production credentials from `.env`
- Access or modify real survey roots
- Access or modify network-mounted storage
- Delete, reset, rename, overwrite, or move real data
- Run destructive cleanup or reset tools
- Register keyboard hooks or hotkeys
- Request interactive operator input during automated tests
- Push commits, create pull requests, merge branches, or modify remotes
- Install or upgrade production dependencies without approval
- Change database schemas without an approved migration plan
- Disable tests, safety guards, validation, or error handling to make a task pass

If an explicitly requested task requires one of these actions, first state:

- The exact operation
- The target resource
- The expected side effects
- The rollback or recovery approach
- The validation that will be performed

## Working agreement

- Read this file before every task.
- Read relevant files under `docs/refactor/` before refactor work.
- Keep every task narrowly scoped.
- Do not modify files outside the approved scope.
- Do not perform broad architectural refactors unless explicitly requested.
- Do not combine unrelated fixes in one change.
- Do not rename or reformat unrelated symbols and files.
- Preserve backward compatibility unless a breaking change is explicitly approved.
- Preserve existing operator workflows unless the task intentionally replaces them.
- Do not silently change defaults, paths, statuses, retries, or recovery behavior.
- Do not introduce hidden test modes in production code.
- Do not detect pytest or CI from production code to alter runtime behavior.
- Prefer explicit dependency injection over mutable globals and environment-based test switches.
- Do not add production dependencies without explaining the need, alternatives, and operational impact.
- Avoid speculative abstractions that are not required by the current task.
- Report discovered unrelated defects instead of fixing them automatically.

## Protected path rules

Tests and validation must use pytest-owned temporary paths unless explicitly authorized otherwise.

Never write to or delete from:

- Repository runtime `data/`
- Configured field-data roots
- Configured survey roots
- Network shares
- Drive roots
- User profile directories
- System temporary directories used by production
- Existing upload caches
- Existing output directories

Any test that performs filesystem deletion must:

1. Operate only under a test-owned temporary root.
2. Require a test ownership sentinel.
3. Resolve and validate the target path.
4. Reject filesystem roots and repository roots.
5. Confirm that the target is contained within the approved temporary root.
6. Preserve sentinel files outside the target.

Do not use the current working directory as an implicit destructive target.

## Database safety rules

Automated tests must never open or create the production database.

Tests must use:

- A fresh temporary SQLite database
- An explicit database path
- Isolated records per test
- Proper connection cleanup

Before changing database behavior:

1. Identify affected tables, indexes, foreign keys, and migrations.
2. Document forward migration.
3. Document rollback or recovery.
4. Consider existing databases and partially migrated states.
5. Add migration and compatibility tests.
6. Confirm that migrations are repeatable or safely versioned.

Never delete database records merely to make a test or migration pass.

## External service safety

WebODM and other external services must be replaced by fakes or mocks in normal tests.

Default tests must not:

- Resolve external hosts
- Open outbound sockets
- Authenticate against WebODM
- Create WebODM projects or tasks
- Upload files
- Poll real tasks
- Download real artifacts

Tests marked `external` must remain excluded by default.

Do not run external tests unless the task explicitly authorizes them and identifies the target environment.

## Subprocess safety

Normal tests must not execute:

- QGIS
- GDAL
- ODM
- Shell scripts
- PowerShell scripts
- System commands
- External utilities

Use fake command runners or mocked subprocess boundaries.

If subprocess behavior itself is under test, use a harmless controlled executable or test double and document why it is safe.

Never construct shell commands through unsafe string concatenation when argument lists can be used.

## Logging and error-handling rules

- Preserve exception causes when wrapping errors.
- Never silently swallow exceptions.
- Record meaningful state transitions and failures.
- Include relevant identifiers such as run ID, survey ID, stage, and attempt where available.
- Do not use mutable process-global run or stage context.
- Do not log credentials, tokens, passwords, private URLs, or sensitive paths unnecessarily.
- Do not mark a stage completed unless its required side effects succeeded.
- Do not leave failed stages recorded as running.
- Distinguish cancellation, abort, pause, transient failure, permanent failure, and unexpected failure.
- Do not classify every `RuntimeError` as cancellation without explicit evidence.
- Do not retry non-idempotent operations without an approved idempotency strategy.

## State and recovery rules

Database state, checkpoint files, filesystem outputs, and external-service state may diverge after a crash.

When changing state transitions:

- Define the authoritative source of truth.
- Document valid states and transitions.
- Handle partial failures explicitly.
- Preserve attempt history.
- Do not overwrite evidence of a failed forced rerun.
- Do not treat an older completed attempt as proof that a newer forced attempt succeeded.
- Make checkpoint writes atomic where practical.
- Avoid creating states that cannot be resumed, retried, or diagnosed.

## Concurrency rules

Do not introduce parallel execution until ownership and state semantics are safe.

Concurrency-related changes must consider:

- Same-survey duplicate runs
- Shared output ownership
- Survey and resource locks
- Lock recovery
- SQLite write contention
- Atomic identifier allocation
- Retry idempotency
- Cancellation responsiveness
- File descriptor limits
- HDD and network I/O contention
- Process-safe and thread-safe logging

Do not add worker queues, background workers, or broad parallelism merely to improve speed.

## Required workflow

### Before implementation

1. Read `AGENTS.md`.
2. Read the relevant files under `docs/refactor/`.
3. Inspect the relevant code paths.
4. Explain the current behavior.
5. Identify affected files, symbols, tables, paths, and external boundaries.
6. Identify possible destructive or persistent side effects.
7. Propose the smallest safe implementation.
8. List assumptions and risks.
9. Identify required tests.
10. Stop for approval only when the change is architectural, destructive, persistent, externally visible, or explicitly requires approval.

For routine, scoped, non-destructive tasks, proceed with implementation and validation in one session.

### During implementation

1. Keep the diff focused.
2. Add or update regression tests.
3. Use temporary directories and temporary databases.
4. Use fake WebODM and mocked external boundaries.
5. Preserve current default production behavior.
6. Avoid mutable process-global execution context.
7. Avoid broad formatting or unrelated cleanup.
8. Make filesystem and database operations safe under retries where applicable.
9. Add useful logs for state transitions and failures.
10. Preserve exception causes.
11. Do not weaken existing safety guards.
12. Do not remove a test merely because it exposes a defect.
13. Do not make production behavior conditional on the test runner.
14. Stop if the requested task requires a much broader architectural change than approved.

### After implementation

1. Run syntax or compile checks for changed files.
2. Run focused regression tests.
3. Run relevant existing tests.
4. Run the full safe default suite where practical.
5. Run collection-only validation.
6. Run `git diff --check`.
7. Inspect changed file names for scope violations.
8. Show exact commands executed.
9. Summarize created, modified, moved, and deleted files.
10. Explain behavior before and after.
11. Map acceptance criteria to tests.
12. Report untested paths and remaining risks.
13. State whether any production file changed.
14. State whether any external or destructive operation occurred.
15. Do not claim completion when mandatory validation failed or was skipped.

## Test requirements

Normal test discovery must never:

- Start a real pipeline
- Load production `.env`
- Contact WebODM
- Execute QGIS or GDAL
- Open the production database
- Touch production survey roots
- Register keyboard hooks
- Request interactive input
- Delete real files
- Write outside test-owned temporary paths

Tests must be:

- Deterministic
- Isolated
- Repeatable
- Small enough for normal development
- Explicit about external dependencies
- Safe to collect and import

A test must assert meaningful behavior. Construction-only tests without behavioral assertions are insufficient unless construction itself is the behavior under test.

## Dependency changes

Before adding or upgrading a dependency:

1. Explain why it is required.
2. Check whether the standard library or an existing dependency is sufficient.
3. Identify whether it is runtime, development, or optional.
4. Avoid placing test-only dependencies in production requirements where a development dependency file exists.
5. Consider version compatibility with the project’s supported Python version.
6. Update documentation and lock files where applicable.
7. Run compatibility tests.

Do not perform broad dependency upgrades during an unrelated task.

## Git safety

Unless explicitly requested:

- Do not commit
- Do not push
- Do not merge
- Do not rebase
- Do not reset
- Do not clean untracked files
- Do not modify remotes
- Do not force push
- Do not change branches

Git commands used for inspection must be read-only unless the task explicitly requests a write operation.

Do not use destructive commands such as:

```text
git reset --hard
git clean -fd
git checkout -- .
git restore .
```

without explicit authorization.

## Documentation rules

Update `docs/refactor/CURRENT_STATUS.md` after a completed refactor milestone.

Update `docs/refactor/TEST_STRATEGY.md` when test architecture, fixtures, guards, commands, or test levels change.

Update `docs/refactor/DECISIONS.md` when an architectural choice is accepted, rejected, or superseded.

Do not mark a phase complete unless each acceptance criterion has supporting evidence.

Do not rewrite historical decision records to hide earlier conclusions. Add a new decision or mark an old one superseded.

## Definition of done

A task is complete only when:

- The requested behavior is implemented.
- The diff remains within scope.
- Existing relevant behavior remains working.
- Regression tests are included.
- Focused tests pass.
- The safe default test suite passes where practical.
- Collection remains safe.
- Logging is sufficient to diagnose failures.
- Database and filesystem compatibility have been considered.
- External-service behavior has been faked or explicitly authorized.
- No protected resource was accessed without authorization.
- No unrelated changes are included.
- Remaining risks and unvalidated paths are reported honestly.

## Stop conditions

Stop implementation and report the issue when:

- The requested change requires an unapproved database migration.
- The task would delete, overwrite, or move real data.
- A production credential or external service is required unexpectedly.
- The necessary change is substantially broader than the approved scope.
- Existing behavior is ambiguous and the choice would affect persistent data.
- Tests reveal corruption, unsafe path handling, or an unrecoverable state.
- Validation would require executing the real pipeline or external tools without authorization.
- A safe rollback or recovery path cannot be identified.
