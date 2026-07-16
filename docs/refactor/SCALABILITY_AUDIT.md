# Scalability and Concurrency Audit

## Scope and method

This audit describes the repository as it exists in the current working tree. It is a static code inspection, not a concurrency load test. No pipeline, external service, test suite, or production-sized dataset was run.

Status meanings:

- **Confirmed**: the implementation mechanism is directly visible in the repository.
- **Confirmed; impact requires measurement**: the mechanism is visible, but its practical limit or frequency has not been measured.
- **Further investigation required**: available code is insufficient to confirm the proposed behavior.

## Risk summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| R01 | Single-threaded orchestration | High | Confirmed |
| R02 | Unsafe shared output ownership | Critical | Confirmed |
| R03 | Survey ID allocation race | Critical | Confirmed |
| R04 | SQLite write contention | High | Confirmed; impact requires measurement |
| R05 | Process-global mutable logger context | High | Confirmed |
| R06 | Broad retries may repeat non-idempotent work | High | Confirmed |
| R07 | Non-atomic checkpoint and state updates | Critical | Confirmed |
| R08 | Coarse pause and abort handling | High | Confirmed |
| R09 | WebODM upload resource pressure | High | Confirmed; operating limits require measurement |
| R10 | Unbounded filesystem and tile work | High | Confirmed; operating limits require measurement |
| R11 | Stale-stage ambiguity after forced reruns | High | Confirmed |
| R12 | Interactive quality gate blocks unattended execution | Medium | Confirmed |
| R13 | Test modules have external/import-time side effects | Critical | Confirmed |

## R01 — Single-threaded orchestration

- **Description:** A pipeline run invokes each stage synchronously and serially. The repository has no job queue, worker scheduler, or run-level concurrency coordinator.
- **Evidence:** `RGBPipeline.run()` calls `StageRunner.run()` for `data_segregation`, `cross_run_filter`, `kml_boundary`, `webodm`, `quality_gate`, and `qgis` in a fixed sequence. `StageRunner.run()` executes the supplied callable inline. The only explicit thread found is a daemon poller used while uploading to WebODM; it does not parallelize jobs or pipeline stages.
- **Relevant files and symbols:** `pipelines/rgb_pipeline.py` — `RGBPipeline.run`; `shared/stage_runner.py` — `StageRunner.run`; `modules/webodm/webodm_processor.py` — `WebODMProcessor.create_task_with_images`.
- **Possible failure scenario:** A long WebODM or tile job occupies the process while later work waits. Throughput can only be increased by launching additional independent processes, which exposes the ownership and locking risks below.
- **Severity:** High.
- **Related risks:** R02, R03, R04, R05, R12.
- **Finding status:** Confirmed. Safe parallelism and the desired scheduler topology require design work.

## R02 — Unsafe shared output ownership

- **Description:** Several outputs are survey-scoped rather than run-scoped, and destructive reset/overwrite operations have no ownership check or lock.
- **Evidence:** Data segregation derives `<surveys_root>/<year>/<survey_id>/rgb` and creates shared `images`, `qgis`, and `tiles` trees. Cross-run filtering resets `path` and `cross-runs` with `shutil.rmtree`. QGIS tiling removes an existing output directory for a non-resume run. Pipeline code can replace a KML, remove raw images, copy staged tile trees into survey output, and clear upload-cache directories. Some temporary QGIS and upload paths are run-scoped, but final survey artifacts are not uniformly run-scoped.
- **Relevant files and symbols:** `modules/data_segregation/data_segregation.py` — `run`; `modules/cross_run_image_filter/cross_run_image_filter.py` — `_reset_dir`, `run_filter`; `modules/qgis/qgis_tools.py` — `QGISTools.generate_tiles`; `pipelines/rgb_pipeline.py` — `stage_data_segregation`, `stage_cross_run_image_filter`, `stage_qgis`, `_cleanup_upload_cache`.
- **Possible failure scenario:** Two runs for one survey interleave. One run removes or replaces an output directory while the other is copying images or generating tiles, producing missing, mixed, or incorrectly attributed artifacts.
- **Severity:** Critical because data integrity is at risk.
- **Related risks:** R03, R06, R07, R08, R10.
- **Finding status:** Confirmed. The complete set of paths that must become run-owned still requires an artifact inventory.

## R03 — Survey ID allocation race

- **Description:** The next survey ID is selected by scanning existing directories and adding one, without a lock, transaction, reservation record, or atomic directory claim.
- **Evidence:** `generate_next_survey_id()` iterates the year directory, calculates `max_number + 1`, and returns it. `run()` later checks whether the derived survey path exists and creates its directory tree. Allocation and creation are separate operations.
- **Relevant files and symbols:** `modules/data_segregation/data_segregation.py` — `generate_next_survey_id`, `run`.
- **Possible failure scenario:** Two processes scan at the same time and both choose the same ID. Both can pass the existence check before either completes directory creation, after which their files share one survey tree or one run fails partway through setup.
- **Severity:** Critical because the identifier and output ownership boundary can be corrupted.
- **Related risks:** R02, R04, R07.
- **Finding status:** Confirmed from the allocation algorithm. The observed frequency under current operations is unknown.

## R04 — SQLite write contention

- **Description:** Each repository operation opens a SQLite connection and commits independently. WAL improves concurrent reads, but writers remain serialized and the busy timeout is five seconds.
- **Evidence:** `connect()` enables WAL, `synchronous=NORMAL`, and `busy_timeout=5000`. `PipelineRepo` opens a new connection for most operations and performs explicit commits. No application-level write queue, transaction retry policy, or concurrency limit is present. The schema contains shared `runs`, `stages`, and `surveys` tables.
- **Relevant files and symbols:** `shared/db/connection.py` — `connect`; `shared/db/repo.py` — `PipelineRepo`; `shared/db/schema.py` — `runs`, `stages`, `surveys`, `webodm_tasks`.
- **Possible failure scenario:** Multiple workers finish stages or update survey state together. A writer remains locked beyond five seconds and another run receives an operational lock error; the generic stage retry may then repeat stage work rather than only retrying the state update.
- **Severity:** High.
- **Related risks:** R03, R06, R07, R11, R01.
- **Finding status:** The contention mechanism and timeout are confirmed. Actual write latency, worker capacity, and network-filesystem placement of the DB require measurement.

## R05 — Process-global mutable logger context

- **Description:** Named Python loggers are cached globally, while run and stage values live in one mutable `ContextFilter` per logger.
- **Evidence:** `get_logger()` calls `logging.getLogger(name)`, sets a custom `_configured` flag, and mutates the existing filter's `run_id` on subsequent calls. `set_stage_context()` mutates the filter's `stage_name`. Every `RGBPipeline` uses the same logger names such as `rgb.pipeline` and `rgb.webodm`.
- **Relevant files and symbols:** `shared/logging.py` — `ContextFilter`, `get_context_filter`, `set_stage_context`, `get_logger`; `pipelines/rgb_pipeline.py` — `RGBPipeline.__init__`; `shared/stage_runner.py` — `StageRunner.run`.
- **Possible failure scenario:** Two pipelines run in one process. Initializing or advancing one pipeline changes the shared filter, causing the other pipeline's records to be written with the wrong run or stage context. Both also share the first configured handlers and log-file destination.
- **Severity:** High because traceability is a safety requirement for concurrent execution.
- **Related risks:** R01, R04, R07, R11.
- **Finding status:** Confirmed for concurrent pipelines in one process. Cross-process file-handler behavior still requires investigation.

## R06 — Broad retries may repeat non-idempotent work

- **Description:** The stage runner retries every ordinary exception up to three attempts with a fixed delay, without classifying errors or declaring stage idempotency.
- **Evidence:** `StageRunner.run()` has a broad `except Exception` and invokes the same stage callable again. A single stage can create WebODM projects/tasks, upload images, download assets, reset output trees, or copy files. Some internal operations have additional retry loops, producing nested retries.
- **Relevant files and symbols:** `shared/stage_runner.py` — `StageRunner.run`; `pipelines/rgb_pipeline.py` — `stage_data_segregation`, `stage_webodm`, `stage_qgis`; `modules/webodm/webodm_processor.py` — `create_project`, `create_task_with_images`, `get_task`; `modules/data_segregation/data_segregation.py` — `run`.
- **Possible failure scenario:** WebODM accepts a task but the client times out before receiving its ID. The stage retry submits another task. Similar retries can reset or recopy shared outputs after a permanent validation or data error.
- **Severity:** High.
- **Related risks:** R02, R04, R07, R09, R11.
- **Finding status:** Confirmed. Which individual operations are safe, conditionally safe, or unsafe to retry needs an operation-by-operation idempotency audit.

## R07 — Non-atomic checkpoint and state updates

- **Description:** Run state is split across SQLite rows, an in-memory dictionary, a directly overwritten checkpoint JSON file, filesystem artifacts, flag files, and WebODM. No transaction spans these systems.
- **Evidence:** `_save_webodm_checkpoint()` uses `Path.write_text()` directly, not temporary-file plus atomic replace. Stage start and finish are separate database commits. External and filesystem work occurs between them. A completed stage serializes output JSON only at stage finish; checkpoint writes and WebODM changes occur independently.
- **Relevant files and symbols:** `pipelines/rgb_pipeline.py` — `state`, `_save_webodm_checkpoint`, `_load_webodm_checkpoint`, `_clear_webodm_checkpoint`, `stage_webodm`; `shared/stage_runner.py` — `StageRunner.run`; `shared/db/repo.py` — `start_stage`, `finish_stage`; `shared/pipeline_control.py` — flag-file methods.
- **Possible failure scenario:** A process exits after creating a WebODM task or publishing an output but before persisting the matching checkpoint/stage record. Resume cannot reliably distinguish “not started” from “external effect completed,” so it may duplicate work or reuse incomplete artifacts. A crash during direct checkpoint overwrite can leave invalid JSON.
- **Severity:** Critical because recovery can repeat costly work or misstate artifact validity.
- **Related risks:** R02, R04, R06, R08, R11.
- **Finding status:** Confirmed. Filesystem atomicity on every supported local/network volume and WebODM deduplication capabilities require investigation.

## R08 — Coarse pause and abort handling

- **Description:** Pause and abort are cooperative flag checks. Checks exist between top-level stages and in selected WebODM polling/upload loops, but not throughout all long-running work or inside QGIS/GDAL subprocesses.
- **Evidence:** `PipelineControl.check_or_raise()` checks per-run pause and abort files. `RGBPipeline.run()` checks before each stage. WebODM polling and upload callbacks receive a control callback. Data copies/filtering and `subprocess.run()` calls in QGIS/GDAL do not receive a cancellation token. `subprocess.run()` is blocking and no child-process termination path is visible.
- **Relevant files and symbols:** `shared/pipeline_control.py` — `PipelineControl`; `pipelines/rgb_pipeline.py` — `_check_control_or_raise`, `run`; `modules/webodm/webodm_processor.py` — `wait_for_completion`, `create_task_with_images`; `modules/qgis/qgis_tools.py` — `clip_raster_by_mask`, `generate_tiles`.
- **Possible failure scenario:** An operator requests abort during high-zoom tiling or a large network copy. The flag is not observed until the operation ends; meanwhile the external process continues consuming resources and writing artifacts.
- **Severity:** High.
- **Related risks:** R02, R07, R09, R10.
- **Finding status:** Confirmed. Acceptable cancellation latency and child-process behavior on Windows require explicit requirements and tests.

## R09 — WebODM upload resource pressure

- **Description:** Upload construction enumerates all images, holds a full path list, opens every image file, and builds one multipart request containing all file handles.
- **Evidence:** `create_task_with_images()` builds `image_files`, initializes `opened` and `fields`, opens every image in a loop, appends every handle to the multipart fields, and closes them only in `finally` after the request. Upload staging may also copy the entire dataset to a local cache first.
- **Relevant files and symbols:** `modules/webodm/webodm_processor.py` — `WebODMProcessor.create_task_with_images`; `pipelines/rgb_pipeline.py` — `_stage_upload_cache`, `_stage_webodm_upload_images`, `stage_webodm`; `pipelines/rgb_helpers/upload_cache.py` — `RGBUploadCacheMixin`.
- **Possible failure scenario:** A survey with more images than the process file-descriptor limit fails before upload, or multiple concurrent uploads multiply open handles, memory metadata, local staging space, and bandwidth usage.
- **Severity:** High.
- **Related risks:** R01, R06, R08, R10.
- **Finding status:** The all-files-open behavior is confirmed. Peak memory, descriptor limits, and WebODM/API support for chunking or resumable upload require measurement and API investigation.

## R10 — Unbounded filesystem and tile work

- **Description:** Multiple stages recursively scan entire source/output trees, copy files sequentially, recount tile trees, and allow broad zoom ranges without repository-level work quotas.
- **Evidence:** Data segregation performs several `rglob("*")` scans and sequential copies. Cross-run filtering copies retained and excluded images sequentially. QGIS tiling recursively counts multiple tile extensions before and after generation, can delete whole output trees, and invokes `gdal2tiles` for configured zooms. The default configuration supports zoom `11-24`.
- **Relevant files and symbols:** `modules/data_segregation/data_segregation.py` — source resolution helpers and `run`; `modules/cross_run_image_filter/cross_run_image_filter.py` — `run_filter`; `modules/qgis/qgis_tools.py` — `QGISTools.generate_tiles`; `pipelines/rgb_pipeline.py` — upload staging and `stage_qgis`; `shared/config.py` — QGIS tile configuration.
- **Possible failure scenario:** Deep source trees or high-resolution rasters lead to repeated full-tree scans, millions of tile files, exhausted disk space, long network copy times, and slow resume checks. Concurrent runs amplify I/O contention.
- **Severity:** High.
- **Related risks:** R01, R02, R08, R09.
- **Finding status:** The algorithms are confirmed. Dataset-size limits, actual tile counts, storage consumption, and bottleneck profiles require benchmarks.

## R11 — Stale-stage ambiguity after forced reruns

- **Description:** Stage attempts are append-only, but the lookup API deliberately prefers any completed attempt over newer failed or running attempts. This obscures attempt chronology after a forced rerun.
- **Evidence:** `PipelineRepo.get_latest_stage()` first queries the most recent completed row and only considers the newest row of any status when no completed row exists. `get_latest_stage_output()` also returns the newest completed output. `StageRunner` uses this result for skip/stale decisions, while `--force-stage` creates another attempt under the same stage name. The schema has no attempt number, supersession marker, authoritative-attempt pointer, or uniqueness rule for an active attempt.
- **Relevant files and symbols:** `shared/db/repo.py` — `get_latest_stage`, `get_latest_stage_output`; `shared/db/schema.py` — `stages`; `shared/stage_runner.py` — `StageRunner.run`; `main.py` — `--force-stage`; `pipelines/rgb_pipeline.py` — `_stage_will_run`, `run`.
- **Possible failure scenario:** A stage completed once, then a forced rerun failed after changing artifacts. Resume loads the older successful output and may skip the stage, even though the newest attempt failed and the artifacts may no longer match the older output.
- **Severity:** High.
- **Related risks:** R02, R06, R07.
- **Finding status:** Confirmed. Desired backward-compatible semantics for successful history versus authoritative current state require an architectural decision.

## R12 — Interactive quality gate blocks unattended execution

- **Description:** The normal quality-gate stage waits indefinitely for terminal input and contains its restart decision loop inside pipeline execution.
- **Evidence:** `quality_gate_prompt()` calls `input()`. `stage_quality_gate()` loops until the user approves, fails, or requests a valid restart. No non-interactive policy, timeout, persisted approval request, or external approval API is present.
- **Relevant files and symbols:** `shared/logging.py` — `quality_gate_prompt`; `pipelines/rgb_pipeline.py` — `stage_quality_gate`, `restart_and_wait`.
- **Possible failure scenario:** A worker launched without an attached terminal blocks forever after WebODM processing, occupies its worker slot, and never reaches QGIS or final state.
- **Severity:** Medium for current manual use, but a prerequisite for worker scheduling.
- **Related risks:** R01, R07, R08.
- **Finding status:** Confirmed. Required approval actors, timeout policy, and default automated rules require product decisions.

## R13 — Test modules have external/import-time side effects

- **Description:** Existing files under `tests/` are script-style and execute work at import time; discovery is not isolated from real configuration, databases, filesystems, or external services.
- **Evidence:** `tests/test_rgb_pipeline.py` loads `.env`, constructs `RGBPipeline` (which initializes the repository database), and runs it at module scope. `tests/query_test.py` opens a hard-coded database outside this repository at module scope. `tests/reset_environment.py` deletes `data/logs` and `data/pipeline.db` when executed. `tests/test_experiment_naming.py` has no assertions and imports `resolve_rgb_exp01_names` from `shared`, although current `shared/__init__.py` does not export it. No `pytest`, `unittest`, `tox`, `nox`, or CI configuration was found, and no test framework is declared in `requirements.txt`.
- **Relevant files and symbols:** `tests/test_rgb_pipeline.py`; `tests/query_test.py`; `tests/reset_environment.py`; `tests/test_experiment_naming.py`; `shared/__init__.py`; `shared/config.py` — `load_pipeline_config`; `pipelines/rgb_pipeline.py` — `RGBPipeline.__init__`, `run`.
- **Possible failure scenario:** A conventional test-discovery command imports `test_rgb_pipeline.py`, starts a real pipeline against configured survey roots, creates/updates the production SQLite database, and may submit WebODM work. Manual execution of the reset script deletes real local run state.
- **Severity:** Critical because validation itself can mutate or delete real data.
- **Related risks:** All phases depend on a safe test baseline; especially R02, R06, R07.
- **Finding status:** Confirmed statically. No tests were executed because doing so would violate the repository working agreement. Exact behavior under a chosen future test runner remains to be established safely.

## Cross-cutting investigation backlog

The following questions should be answered before committing to implementation designs:

1. Are `SURVEYS_ROOT`, `FIELD_DATA_ROOT`, upload cache, and `data/pipeline.db` ever hosted on SMB/NFS or synchronized storage?
2. Must multiple runs for the same survey be rejected, serialized, or supported as isolated attempts?
3. What is the largest image count, upload size, raster size, zoom range, and tile count expected in production?
4. What WebODM operations support client-provided idempotency keys, name-based reconciliation, chunking, or resumable upload?
5. What pause/abort latency is operationally acceptable, and should abort cancel remote WebODM tasks?
6. Is SQLite a long-term single-host state store, or must the worker model support multiple hosts?
7. Which stage attempt should be authoritative after a failed forced rerun?
8. Who or what may approve a quality gate in unattended execution, and what is the timeout/escalation policy?
