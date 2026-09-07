# Next Actions

## Highest Priority

1. Resume run `1cd78d5e-331a-4ee5-b05d-047ca8f658e5` normally and verify that
   it reattaches to its exact existing Task 4 UUID without creating another
   remote task.

Why this is first: the deleted filtered-image directory has been reconstructed
and independently verified at 1,153 kept images plus 229 preserved exclusions.
SQLite and WebODM identity were intentionally left unchanged, so the normal
resume path is the smallest validation of the repaired artifact state.

## Recommended Pipeline Sequence

1. Run the ordinary resume command without forcing segregation or filtering;
   confirm storage preflight passes and the exact persisted Task 4 UUID is
   reused.

2. Verify Task 4 artifact delivery, including the optional full ODM ZIP, and
   the remaining quality-gate/QGIS stages before considering the run complete.

3. Design a reusable fail-closed recovery tool that binds restoration to the
   persisted run/survey identity and saved image classifications. Cover missing
   source, identity mismatch, existing outputs, interrupted copying, and atomic
   activation with temporary paths and databases.

4. Perform the pending controlled M3M `--uav`/`--rgb` validation and keep
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
