# Phase 3 Artifact Inventory and ADR-003 Proposal

Date: 2026-07-20

## Purpose

Phase 3 separates per-run working artifacts from published survey artifacts. This document records the current artifact ownership model, the compatibility boundaries that depend on published paths, and the proposed ADR-003 direction before implementation begins.

This is a planning document only. It does not authorize runtime path changes, database migrations, cleanup behavior, network writes, or publication behavior changes.

## Current path model

The current RGB workflow mostly uses one shared survey tree as both the working area and the published output location:

```text
<surveys_root>/<year>/<survey_id>/rgb/
```

Several later-stage operations already use run-scoped temporary or cache paths, but many intermediate and final artifacts are still written directly under the shared survey tree. That makes crash recovery, retry behavior, forced reruns, same-survey concurrency, and network-share failure handling harder to reason about.

## Artifact inventory

| Artifact or path | Current producer | Current consumers | Current behavior | Phase 3 classification | Key risk |
|---|---|---|---|---|---|
| Field-data source dataset | Data segregation | Data segregation | Read from configured field-data root | External input, read-only | Must never be treated as owned output |
| `<surveys_root>/<year>/<survey_id>/rgb/` | Data segregation | All later stages, map export, reporting, operators | Created as the main working and published root | Published survey root | Mixed ownership lets failed runs leave visible partial artifacts |
| `manifest.json` under survey `rgb/` | Data segregation | Map boundary resolver, reporting/operators | Written early with source and survey metadata | Published metadata | Early publication can advertise an incomplete run |
| `boundary/<survey_id>.kml` | Data segregation and RGBPipeline rename logic | KML boundary stage, map export | Copied or renamed into published boundary directory | Published input artifact copied from source | Rename/copy behavior is visible before downstream validation |
| `boundary/*.geojson`, `boundary/*.csv` | KML boundary stage | QGIS clip, map export, operators | Written beside the source boundary in the shared tree | Derived working artifact, later published artifact | Failed conversion can leave stale or partial derived boundary files |
| `images/raw/` | Data segregation | Cross-run filter or WebODM fallback path | Images copied into shared survey tree | Run-owned working input copy | Large duplicate storage and unclear cleanup ownership |
| `images/path/` | Cross-run filter or disabled filter copy | WebODM upload stage | Output directory may be reset before reuse | Run-owned working artifact | Reset deletes previous contents inside shared survey tree |
| `images/cross-runs/` | Cross-run filter | Operators/debugging | Excluded images are copied here; directory may be reset | Run-owned diagnostic artifact; publish only if explicitly retained | Same-survey reruns can overwrite diagnostic evidence |
| `ortho/orthomosaic--*.tif` | WebODM export/fallback | Orthomosaic selection, QGIS stage, operators | Written directly under shared survey tree in production mode | Published candidate artifact after validation | Older and newer attempts can coexist without an active-owner manifest |
| `odm/`, `3d/`, `dem/odm/dtm`, `dem/odm/dsm` | WebODM export paths | Operators/future consumers | Created under shared survey tree | Published or retained export artifacts, depending on policy | Ownership and retention policy is not explicit |
| `qgis/clipped/ortho/orthomosaic-clipped--*.tif` | QGIS clip | Tile generation, map export, operators | Written to final path; may stage locally before copy-back | Published QGIS artifact | Network copy failure can leave missing or stale published output |
| `tiles/ortho/round-corners/` | QGIS tile generation | Map viewers/operators | Generated locally when configured, then copied into published tree | Published tile artifact | Directory copy is not atomic; partial copy can be visible |
| `tiles/ortho/soft-corners/` | QGIS tile generation fallback path | Map viewers/operators | Uses fallback path when not declared by data segregation | Published tile artifact | Directory may not be pre-created by data segregation |
| QGIS local staging `clip/<run_id>/` | QGIS clip | QGIS stage only | Temporary local raster staging | Run-scoped workspace/staging | Cleanup is deferred if copy-back fails |
| QGIS local staging `tiles/<run_id>/` | QGIS tiles | QGIS stage only | Temporary input and tile output staging | Run-scoped workspace/staging | Large storage; copy-back failure leaves recoverable local artifacts |
| WebODM upload cache `automation-pipeline/upload_cache/<run_id>/...` | RGB upload-cache mixin | WebODM task creation | Run-scoped image cache; cleaned after safe points | Run-scoped cache | Cleanup depends on in-memory/state tracking |
| Legacy WebODM upload cache `<survey_id>/<stage_name>` | Older upload helper | WebODM upload helper | Deletes existing stage cache before reuse | Legacy non-run-scoped cache | Same-survey collision risk if still reachable |
| WebODM checkpoint `webodm_checkpoint_<run_id>.json` | RGBPipeline WebODM checkpoint helpers | WebODM resume/reattach logic | Run-ID-specific JSON, written directly | Run-scoped checkpoint | Non-atomic write can corrupt checkpoint on crash |
| Stage output JSON in SQLite | StageRunner/RGBPipeline | Resume/reporting | Stores paths, selected artifacts, and stage outputs | Durable run state | Absolute path strings may outlive workspace or publish changes |
| Logs under `data/logs/` or injected logs dir | Shared logging | Operators/report parser | Per-run logger ownership, but conventional log destinations | Operational append-only evidence | Not a survey artifact; retention/publish semantics should stay separate |
| Map export package `exports/maps/<slug>/` or configured root | Map export CLI | Operators/viewers | Copies selected published artifacts into a separate package | Separate operator export artifact | Consumers assume legacy survey paths and glob patterns |

## Compatibility consumers

Current consumers assume the legacy published survey layout:

- `modules/map_export/orthomosaic_finder.py` searches for `rgb/qgis/clipped/ortho/orthomosaic-clipped--*.tif`.
- `modules/map_export/boundary_finder.py` reads `manifest.json` and searches under the survey `rgb/` tree for boundary files.
- `modules/map_export/survey_manifest.py` scans for `rgb/manifest.json`.
- `map.py` and map export packaging copy published boundary and orthomosaic artifacts into export packages.
- `query_survey_stats.py` reads SQLite stage output and logs; it may display path strings stored by previous stages.

Phase 3 should preserve these consumers while introducing a clearer active-published-artifact record.

## Proposed target ownership model

The recommended ADR-003 direction is run-scoped workspace plus manifest-backed publication to legacy-compatible paths.

Under this model:

1. Each run owns a unique workspace keyed by `run_id`.
2. Intermediate files, stage outputs, checkpoints, upload caches, and QGIS staging are written under that run workspace or an explicitly run-scoped cache.
3. A publish step validates completed artifacts before making them visible under the existing survey tree.
4. Published paths remain backward compatible for current operators and map/report consumers.
5. A publication manifest records the active run, artifact source, destination, counts or checksums where practical, previous active version, and publish status.
6. Failed, aborted, or canceled runs do not replace active published artifacts.
7. Cleanup and retention are separate policy decisions and should not be bundled into the first workspace implementation.

Candidate workspace layout:

```text
<workspace_root>/<run_id>/
  images/raw/
  images/path/
  images/cross-runs/
  boundary/
  webodm/ortho/
  webodm/odm/
  webodm/dem/
  qgis/clipped/ortho/
  qgis/tiles/round-corners/
  qgis/tiles/soft-corners/
  checkpoints/
  publish/
```

Candidate published layout, preserved for compatibility:

```text
<surveys_root>/<year>/<survey_id>/rgb/
  manifest.json
  boundary/
  ortho/
  qgis/clipped/ortho/
  tiles/ortho/round-corners/
  tiles/ortho/soft-corners/
```

## Publish protocol expectations

The publish protocol should be designed before runtime changes are made.

Minimum expectations:

- Publish only artifacts from a completed and validated run workspace.
- Use a temporary sibling destination for file or directory replacement where practical.
- Validate required files, tile counts, sizes, and readability before activation.
- Prefer atomic rename on the same volume.
- For network shares or cross-volume copies, use a manifest-backed protocol: copy to a temporary destination, validate, mark complete, then switch the active pointer or final destination.
- Preserve the previous active artifact set until the new set is complete.
- Record enough information to diagnose interrupted publish attempts.

## ADR-003 recommendation

Prepare ADR-003 as `Proposed`, not `Accepted`:

- **Decision:** Use a run-scoped workspace for mutable stage artifacts, then publish validated artifacts to legacy-compatible survey paths through a manifest-backed publish step.
- **Primary reason:** This directly addresses shared-output collision risk while preserving current operators and consumers.
- **Rejected for now:** Immediate broad migration to only versioned immutable published paths, because it would require consumer changes before Phase 3 can reduce collision risk.
- **Rejected for now:** Serialized in-place writes, because it reduces same-survey overlap but does not fix partial publication, crash recovery, or forced-rerun ambiguity.

## Open questions before implementation

- What should the default workspace root be when no explicit configuration is supplied?
- Should same-survey concurrent runs be blocked in Phase 3 or deferred to the locking phase?
- Which artifacts are required for an atomic publish set: manifest, boundary, WebODM outputs, QGIS clipped raster, tiles, or all selected outputs?
- How should publication manifests represent legacy runs that predate Phase 3?
- Should map export prefer a publication manifest when present and fall back to globbing otherwise?
- What retention policy should apply to failed, canceled, and superseded run workspaces?
- How should checksums be balanced against very large orthomosaics and tile trees?
- How should Windows network-share failures during publish be surfaced and resumed?

## Proposed test matrix

All tests must use pytest-owned temporary paths and faked external boundaries.

- Path-planning tests prove two same-survey runs receive different workspace paths.
- Publish success tests prove validated artifacts become visible at legacy-compatible paths.
- Publish failure tests inject copy/rename failures and prove previous published artifacts remain active.
- Tile publish tests prove partial tile copy does not become active.
- Manifest tests prove active run ID, artifact paths, counts, and status are recorded.
- Compatibility tests prove map boundary and orthomosaic finders still resolve legacy layouts.
- Compatibility tests prove manifest-backed layouts are preferred once enabled.
- Checkpoint tests prove run-scoped checkpoint writes do not collide across run IDs.

## Scope guard for the first implementation slice

The first Phase 3 implementation slice should not redesign survey ID allocation, database schema, WebODM retry policy, global scheduling, cleanup retention, or map export packaging. It should introduce the smallest path-planning and publish abstraction needed to stop writing mutable intermediate artifacts directly into the published survey tree.
