# Next Actions

## Highest Priority

1. Run storage-preflight-only for the guarded Task 4 tile rebuild on BCO-102
   run `47cd8922-630c-4a22-90d0-8494669ecb80`, review D: free capacity, then
   run the same exact-confirmation command without report-only mode.

Why this is first: ordinary `gdal2tiles --resume` deterministically reuses the
same unreadable partial tile and previously spent hours across retries. The
guarded flow preserves the damaged tree as rollback evidence, starts the tile
target empty, and reuses the verified clipped orthomosaic.

## Recommended Pipeline Sequence

1. Run the guarded BCO-102 command with `--storage-preflight-only`; confirm the
   requested task is Task 4 and sufficient D: space remains for quarantine plus
   a clean rebuild.

2. Remove report-only mode and run the exact same authorization. Preserve the
   quarantine until regenerated tiles are validated and the run completes.

3. Reconcile AH-026044 WebODM state and optional Task 4 ZIP delivery without
   creating another remote task.

4. Design a reusable fail-closed recovery tool that binds restoration to the
   persisted run/survey identity and saved image classifications. Cover missing
   source, identity mismatch, existing outputs, interrupted copying, and atomic
   activation with temporary paths and databases.

5. Perform the pending controlled M3M `--uav`/`--rgb` validation and keep
   multispectral processing deferred.

## Recommended Web/API/UI Sequence

1. Build read-only mock UI/API surfaces from `docs/contracts/` and `docs/ui/` only.
2. Keep operational controls disabled until corresponding pipeline behavior exists.
3. Show combined mode as `Orthomosaic + 3D` where contract-backed data is available, but keep operational controls disabled until corresponding API mutation semantics are approved.

## Daily Sanity Check Prompt

Use `$project-sanity-check` or paste:

```text
Run a bounded project sanity check. Use only AGENTS.md, docs/ai-context/PROJECT_BRIEF.md, docs/ai-context/CURRENT_STATE.md, docs/ai-context/NEXT_ACTIONS.md, docs/ai-context/DOC_INDEX.md, git status, and changed filenames. Do not read the rest of docs/ unless a specific gap requires it. Report alignment, drift, blockers, and the single highest-priority next step.
```

## End-of-Session Update Prompt

```text
Update docs/ai-context/CURRENT_STATE.md and docs/ai-context/NEXT_ACTIONS.md only if the task changed project status, accepted decisions, validation evidence, or next-step priority. Keep the update compact. Do not copy long test logs; summarize command names and outcomes.
```
