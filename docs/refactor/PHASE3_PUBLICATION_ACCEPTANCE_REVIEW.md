# Phase 3 Publication Protocol Acceptance Review

Date: 2026-07-27

## Scope and verdict

This review evaluates the dormant Phase 3 publication protocol against the Phase 3 plan, ADRs, implementation, and hermetic tests. It does not authorize a production pipeline run, network-share experiment, or live publication call site.

**Verdict: conditional pass for the isolated publication primitives; not yet accepted for live RGBPipeline integration or Phase 3 completion.**

The file-only activation, one-directory activation, restart reconciliation, ownership lock, and explicit stale-lock recovery boundaries are individually coherent and fail closed. The remaining blocker is composition: a real RGB run publishes multiple files plus at least one large tile directory, while the current activation contract deliberately supports either a file-only set or exactly one directory. A live call site could therefore publish an incomplete `publication.json` or split one logical run across independently committed views.

## Acceptance matrix

| Phase 3 requirement | Evidence | Result |
|---|---|---|
| Two runs for one survey receive exclusive workspaces | `plan_run_workspace()` includes the run ID; uniqueness and unsafe-ID tests pass | Pass at the path-ownership layer |
| Failed work cannot partially replace published files | File activation stages temporary siblings, commits metadata last, and rolls back injected failures | Pass for dormant file-only activation |
| Failed work cannot partially replace a tile directory | One-directory activation journals rename states, retains the prior directory, and rolls back before metadata commitment | Pass for dormant one-directory activation |
| Interrupted publication is recoverable | File recovery and directory reconciliation cover crash gaps and reject ambiguous evidence | Pass in hermetic tests |
| Concurrent publishers cannot silently overlap | `.publication.lock` is acquired atomically, ownership-checked, and composed with activation | Pass for the dormant local-filesystem contract; SMB behavior unvalidated |
| Abandoned lock evidence is recoverable without automatic stealing | Diagnosis binds approval to the exact digest; recovery archives evidence and requires explicit operator authorization | Pass in hermetic tests |
| Existing consumers resolve final artifacts | Map boundary and orthomosaic resolvers prefer valid publication-manifest entries and retain legacy fallback | Pass for covered consumers |
| One run publishes a complete coherent artifact set | Mixed file/directory activation is rejected; each directory activation writes a manifest containing only that directory | Blocker |
| Current RGBPipeline uses the validated publish boundary | Stages still mirror successful workspace outputs directly to legacy survey paths | Blocker for Phase 3 completion |
| Cleanup and retention are defined | `.previous`, `.activation`, workspaces, and recovery evidence are intentionally retained; no approved policy exists | Deferred deliverable |
| Same-volume, cross-volume, and SMB behavior is validated | Unit tests exercise copy/rename boundaries under temporary paths, not distinct volumes or a network share | External validation gap |

## What is accepted

- The run-scoped workspace layout and containment checks.
- Manifest-backed staging for file sets and one directory.
- Metadata-last file activation with rollback and interrupted-attempt recovery.
- Exact hidden same-filesystem directory activation with a recoverable journal.
- `publication.json` as the authoritative committed marker for the current constrained activation.
- Fail-closed publication ownership and exception-safe lock release.
- Read-only stale-lock diagnosis and snapshot-bound, evidence-preserving operator recovery.
- Legacy-compatible consumer fallback while the protocol remains dormant.

These components are suitable foundations for the next design slice. They must remain dormant until the composition blockers below are resolved.

## Blocking gaps before live integration

### 1. Complete publication-set semantics

A normal run can produce boundary files, orthomosaics, point clouds, archives, clipped rasters, and a tile directory. The protocol currently cannot commit those as one authoritative set. Publishing them separately would allow the last activation to replace `publication.json` with only its own subset and would make recovery ordering ambiguous.

A decision is required on whether to use:

- one journaled mixed-artifact transaction;
- one versioned generation containing the complete artifact set and a single active pointer/switch; or
- another explicitly atomic composition model.

Incrementally merging independently activated subsets into the active manifest is not accepted without a recovery model because a crash can expose a mixed-run publication.

### 2. One lock-owned reconciliation/activation coordinator

Live integration needs one explicit order of operations:

1. acquire publication ownership;
2. inspect and reconcile only evidence belonging to the intended run;
3. validate the complete staged publication set;
4. activate it and commit the authoritative manifest last; and
5. release ownership.

Stale-lock recovery remains a separate operator action and must not be embedded as an automatic resume behavior.

### 3. Removal of direct legacy mirroring from the authoritative path

The current workspace migrations intentionally mirror successful stage outputs to legacy paths for compatibility. Until a final publish boundary replaces those mirrors, later-stage failure or abort can leave some outputs from a newer run visible beside older outputs. The migration must be opt-in first, with the legacy path retained as a rollback/default mode until controlled validation succeeds.

## Non-blocking but required before Phase 3 completion

- Define retention and cleanup rules for workspaces, `.previous`, `.activation`, and `.publication-lock-recovery` evidence, including ownership sentinels and failure preservation.
- Run controlled same-volume, cross-volume, and Windows SMB validation with disposable data and explicit authorization.
- Measure large-tree scan and rename behavior with representative tile counts.
- Document the operator sequence for diagnosing an abandoned lock, verifying liveness externally, recovering evidence, and resuming reconciliation.
- Confirm reporting consumers beyond the covered map-export resolvers either use the manifest or intentionally remain on legacy paths.

## Recommended next slice

Resolve the complete publication-set decision before wiring any live pipeline stage. The smallest safe next task is to design and test a coherent mixed file/directory publication generation under one lock and one authoritative manifest, while keeping the current RGBPipeline mirroring behavior unchanged.

## Review evidence

- Focused acceptance tests: 16 passed, 36 deselected.
- Relevant publication and pipeline tests: 101 passed.
- Collection-only validation: 154 tests collected.
- Full safe default suite: 154 passed.
- git diff --check: passed.
- No external service, production database, real survey root, network share, QGIS/GDAL executable, or live pipeline was used.