# AGENTS.md

## Project overview

This repository contains a geospatial processing pipeline that coordinates local filesystem operations, SQLite state, QGIS/GDAL subprocesses, and WebODM operations.

The pipeline processes large datasets. Changes must prioritize data integrity, resumability, traceability, and backward compatibility.

## Working agreement

- Do not modify files outside the scope of the current task.
- Do not perform broad architectural refactors unless explicitly requested.
- Do not change database schemas without first documenting the migration and rollback strategy.
- Do not add production dependencies without explaining why they are required.
- Do not delete or reset user data during tests.
- Do not run real WebODM jobs unless explicitly authorized.
- Do not run production-sized datasets during tests.
- Do not modify unrelated formatting or rename unrelated symbols.
- Preserve backward compatibility unless the task explicitly approves a breaking change.

## Required workflow

Before implementation:

1. Inspect the relevant code paths.
2. Explain the current behavior.
3. Identify affected modules, database tables, files, and external side effects.
4. Propose the smallest safe implementation.
5. List risks and assumptions.
6. Wait for explicit approval before editing when the task is architectural or affects persistent data.

During implementation:

1. Keep the diff focused.
2. Add or update regression tests.
3. Use temporary directories and temporary databases in tests.
4. Mock WebODM and other external services.
5. Avoid mutable process-global execution context.
6. Make filesystem and database operations safe under retries where applicable.
7. Add useful structured logs for state transitions and failures.
8. Do not hide exceptions without logging and preserving their cause.

After implementation:

1. Run the relevant unit tests.
2. Run static checks and formatting checks.
3. Show the exact commands executed.
4. Summarize changed files.
5. Explain behavior before and after the change.
6. Report remaining risks and untested paths.
7. Do not claim success when validation could not be completed.

## Definition of done

A task is complete only when:

- The requested behavior is implemented.
- Existing relevant behavior remains working.
- Regression tests are included.
- Tests pass.
- Logging is sufficient to diagnose failures.
- Database and filesystem compatibility have been considered.
- The diff contains no unrelated changes.
