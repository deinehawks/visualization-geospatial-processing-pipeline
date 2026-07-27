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
| Cleanup and retention are defined | ADR-019/ADR-020 define read-only planning plus guarded, sentinel-owned, audit-backed explicit execution | Partial pass; controlled filesystem validation still required |
| Same-volume, cross-volume, and SMB behavior is validated | ADR-021 local and SMB disposable validations passed for exclusive-create, file replace, directory rename, and JSON visibility; cross-volume, large-tree, open-handle, and disconnect behavior remain untested | Partial pass; representative stress validation still pending |

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

- Validate the cleanup planner/executor workflow in controlled local, cross-volume, large-tree, and Windows SMB environments before recommending operational cleanup.
- Run the ADR-021 validation tool on controlled same-volume, cross-volume, large-tree, and Windows SMB disposable roots with explicit authorization, then review reports before live wiring.
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

## ADR-018 follow-up

Date: 2026-07-27.

The mixed-artifact publication-set blocker identified by this review is now partially resolved by dormant code: `activate_publication_set_with_lock()` validates a complete staged file/directory generation, holds one publication lock, writes one set-level journal, activates all artifacts before a single authoritative `publication.json` commit, and rolls back caught pre-commit activation failures.

This does not approve live RGBPipeline wiring yet. Mixed-set restart reconciliation and the non-destructive cleanup planner are now implemented as dormant helpers. The remaining live-integration prerequisite is running and reviewing controlled local/cross-volume/SMB filesystem validation reports before enabling live publication behavior.
## ADR-019 follow-up

Date: 2026-07-27.

The retention/cleanup-policy deliverable is now partially resolved by dormant code: `plan_artifact_cleanup()` produces a read-only candidate plan for terminal activation evidence, previous publication manifests, previous artifact backups, and old run workspaces while protecting active/preserved runs and blocking active locks or unresolved evidence.

This does not approve deletion or live RGBPipeline wiring. The remaining cleanup prerequisite is a separately approved executor/operator flow that can validate ownership, preserve audit evidence, support dry-run review, and safely delete only planner-approved candidates.
## ADR-020 follow-up

Date: 2026-07-27.

The cleanup executor/operator-flow prerequisite is now partially resolved by dormant code: `execute_artifact_cleanup()` revalidates reviewed planner output, requires owner-root sentinels, defaults to dry-run, writes audit JSON for approved deletion attempts, and is exposed through `tools/artifact_cleanup.py` with explicit `--allow-delete` acknowledgement.

This does not approve live RGBPipeline publication wiring or routine production cleanup. Controlled local, cross-volume, large-tree, and Windows SMB validation remain required before the cleanup workflow or publication activation is recommended operationally.
## ADR-021 follow-up

Date: 2026-07-27.

The controlled filesystem validation prerequisite is now protocol-ready: `validate_publication_filesystem()` and `tools/filesystem_validation.py` can exercise exclusive-create, file-replace, directory-rename, and JSON visibility semantics under an explicit disposable sentinel root.

Disposable local and SMB validations now passed for the core filesystem primitives. This still does not prove representative production behavior for large tile trees, open handles, antivirus/indexer contention, cross-volume activation, interrupted operations, or SMB disconnect/reconnect scenarios before live publication activation is wired into `RGBPipeline`.
## Manual filesystem validation evidence

Date: 2026-07-27.

The ADR-021 validator was run against disposable local and SMB roots. Both passed `exclusive_create`, `file_replace`, `directory_rename`, and `json_visibility`. The SMB report is retained at `Z:\__pipeline_validation\filesystem-validation-001\reports\smb-001.json` and the validator cleaned its disposable run directory.

No real survey root, production output, QGIS/GDAL command, WebODM service, or live pipeline was used.
