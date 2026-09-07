# Artifact Model

## Status Legend

- **Implemented and verified:** Present in code and covered by inspected safe tests.
- **Partially implemented:** Present, but incomplete, helper-only, or not wired into normal runtime.
- **Approved target behavior:** Accepted direction, but not necessarily implemented.
- **Proposed and awaiting approval:** Candidate behavior needing approval.
- **Deferred:** Not part of the initial API/UI slice.
- **Unresolved:** Known ambiguity that must not be inferred.

## Current Artifact Boundaries

Status: **Partially implemented**.

Current code defines:

- `RunWorkspaceLayout`
- `PublishedSurveyLayout`
- `PublicationArtifact`
- `prepare_publication()`
- `activate_publication_set_with_lock()`
- `reconcile_publication_set()`
- cleanup planning and guarded cleanup helpers

These are implemented in [shared/artifacts.py](../../shared/artifacts.py). Current `RGBPipeline.run()` still mirrors successful stage outputs to legacy survey paths during stage execution.

## Current Layouts

| Layout | Current implementation | Status | UI/API impact |
|---|---|---|---|
| Run workspace | `data/workspaces/<run_id>/...` by default or injected workspace root | Partially implemented | API can expose workspace paths only as diagnostic/provenance data |
| Published survey root | `<surveys_root>/<year>/<survey_id>/rgb/` | Implemented and verified | Existing consumers still depend on it |
| Publication manifest | `<published rgb>/publication.json` | Partially implemented | Manifest-aware consumers may prefer it when present |
| Activation evidence | `.activation/<run-id>/...` under published root | Partially implemented | UI should show as recovery/diagnostic evidence, not active output |
| Cleanup evidence | `.artifact-cleanup-audit/<cleanup-id>.json` | Implemented for guarded operator cleanup and completed-run cleanup | API may expose immutable cleanup outcome and retained metadata |

## Workspace Cleanup Semantics

Status: **Implemented and verified** for fully completed full-run workspaces.

Run workspaces can be hundreds of GB, so the target runtime behavior is to
delete the owned run workspace by default after a fully `completed` run. The
operator opt-out is `--keep-workspace`, which preserves
`data/workspaces/<run_id>` for one run when post-run inspection or debugging is
needed.

Compatibility and safety requirements:

- Automatic cleanup applies only to fully `completed` runs.
- `partially_completed`, `failed`, `paused`, aborted, canceled, and
  recovery-required runs retain their workspaces by default.
- Cleanup happens only after required outputs are already mirrored or
  published.
- Cleanup must never delete published survey outputs, production roots, WebODM
  state, pause flags, logs, checkpoints needed for recovery, or recovery
  evidence.
- `partially_completed` cleanup remains deferred until operation-level evidence
  and retry semantics are strong enough to preserve failed-operation evidence
  safely.
- Automatic cleanup applies only to full normal or resumed runs; selected-stage
  execution retains its workspace.
- New run workspaces contain `.run-workspace.json` ownership evidence. Older
  workspaces without that evidence remain resumable but fail closed for
  automatic deletion.
- Before deletion, the pipeline writes
  `.artifact-cleanup-audit/completed-run-<run_id>.json` with relative file
  inventory, byte totals, stage summaries, verified output mappings, and
  available cross-run image classifications.
- Cleanup failure does not reverse persisted processing success. The run remains
  `completed`, the workspace is retained where possible, and failure evidence
  is logged/audited.

## Artifact Record Contract

Status: **Approved target behavior**.

| Field | Description | Unit | Current status |
|---|---|---|---|
| `artifact_id` | Stable API identifier for one logical artifact | string | Proposed |
| `run_id` | Owning pipeline run | string | Partially implemented |
| `survey_id` | Owning survey when known | string | Partially implemented |
| `logical_name` | Machine-readable artifact family | string | Partially implemented |
| `kind` | `file` or `directory` | enum | Implemented in publication helpers |
| `workspace_path` | Run-owned source path when available | path string | Partially implemented |
| `published_relative_path` | Path relative to published survey root | relative path string | Implemented in publication manifests |
| `published_path` | Legacy-compatible published path | path string | Implemented in publication manifests/stage state |
| `status` | `planned`, `staged`, `published`, `skipped`, `blocked`, `failed`, `superseded`, or `deferred` | enum | Partially implemented |
| `size_bytes` | File size or directory total size | bytes | Partially implemented |
| `file_count` | Directory file count | count | Partially implemented |
| `required_paths` | Required relative paths inside a directory artifact | relative path list | Partially implemented |
| `created_at` | Artifact record timestamp | UTC timestamp | Proposed |
| `source_entity` | Stage, event, or operation that produced it | string | Proposed |

## RGB Publication Allowlist

Status: **Implemented and verified** for dry-run/staging selection.

Allowed publication families:

- KML boundary GeoJSON and CSV processed files.
- WebODM orthomosaic outputs.
- WebODM 3D outputs with `.laz`, `.ply`, or `.pcd` extensions.
- WebODM Task 2 all-assets ZIP.
- QGIS clipped orthomosaic.
- QGIS tile directory.

Current skipped families:

- Cross-run image directories.
- Unknown WebODM sidecars.
- Debug logs or other unapproved mirrored pairs.

Compatibility requirement:

- New artifact families require code, tests, and documentation before activation includes them.
- Skipped artifacts are not failures merely because they exist.
- Unsafe allowed artifacts still fail closed when they are missing, outside the run workspace, duplicated, or target paths escape the published root.

## WebODM Operation Artifacts

Status: **Partially implemented**.

Current implementation:

- Task 4 is the default production CLI mode.
- Task 2-only, Task 4-only, and canonical `--both-tasks` modes are implemented.
- Combined `--both-tasks` mode runs Task 4 before Task 2.
- Task 2 outputs include orthomosaic, pointcloud LAZ/PCD/PLY when available, and all-assets ZIP.
- Task 4/fallback orthomosaic export has workspace-to-legacy mirroring.
- `partially_completed` runtime persistence is mapped for Task 4 success followed by Task 2 failure.
- QGIS runs once per successful WebODM operation in combined mode.

Approved target:

- A combined `Orthomosaic + 3D` run uses canonical flag `--both-tasks` and contains Task 4 and Task 2 as separate WebODM operations.
- Combined execution order is fixed: Task 4 first, then Task 2.
- If Task 4 fails, the combined WebODM stage stops immediately and Task 2 does not run.
- If Task 4 succeeds and Task 2 fails, the run-level target status is `partially_completed`.
- Task 4 output is immediately eligible for publication after a successful Task 4 operation.
- QGIS runs once per successful WebODM operation.
- The quality gate runs once after all selected WebODM tasks complete.
- Each selected WebODM task must retain separate stage records, WebODM identifiers, configuration snapshots, workspaces, outputs/artifacts, runtime and storage metrics, errors/retries, and final operation status.
- A successful operation must not lose outputs when another selected operation fails, and retrying the failed operation leaves successful outputs untouched by default.
- One combined job remains one pipeline run containing two WebODM operations.

Remaining implementation gaps:

- `--task4 --task2` remains rejected by the CLI unless a later deliberate compatibility change accepts it as equivalent to `--both-tasks`.
- Separate per-operation stage records, complete operation metrics, and API/UI projections still need hardening.

## Publication Semantics

Status: **Partially implemented**.

Current implementation:

- `prepare_publication()` writes staged manifest data.
- `activate_publication_set_with_lock()` can activate mixed file/directory sets with lock-owned publication behavior.
- `activate_publication_explicit()` and `tools/publication_activate.py` require explicit confirmation.
- Guarded `RGBPipeline.run()` activation wiring is implemented only when publication confirmation is supplied or `activate_publication` is explicitly selected; default runs without confirmation do not activate publication.

Approved target:

- Publication should be recoverable and auditable.
- `publication.json` is the authoritative active set for manifest-aware consumers.
- Previous active artifacts remain active until the new set is complete.
- Failed or partial publication attempts retain diagnostic evidence.
- Runtime publication activation uses one formal stage with machine name `activate_publication` and operator label `Activate Publication`.
- The stage runs automatically after quality-gate approval.
- CLI/runtime authorization keeps the exact phrase `PUBLISH <survey_id> <run_id>`; future UI/API approval is deferred.
- Missing staged manifest, missing authorization, or an existing publication lock before visible artifact mutation records the stage as `failed`.
- Failure after visible artifact mutation records target status `requires_recovery` and preserves activation evidence for diagnosis.
- Stale-lock recovery and cleanup are part of the `Activate Publication` stage contract, not separate default stages, but remain guarded subflows requiring explicit authorization/evidence before mutation.
- Successful activation completes only the `Activate Publication` stage; existing run-status behavior decides final run status.

UI/API impact:

- Artifact views should distinguish workspace, staged, published, skipped, blocked, failed, and recovery-required evidence.
- UI should label the publication stage `Activate Publication`.
- Publish controls remain deferred until the stage implementation and future approval semantics are implemented.

