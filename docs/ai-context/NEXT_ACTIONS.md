# Next Actions

## Highest Priority

1. Design and implement a guarded missing-image-artifact recovery for legacy
   resume run `1cd78d5e-331a-4ee5-b05d-047ca8f658e5` before forcing data
   segregation or cross-run filtering.

Why this is first: both published `images/raw` and `images/path` are empty, but
the database marks their producing stages complete. Current
`--force-stage data_segregation` wiring does not safely restore the persisted
survey ID or enable existing-folder recovery, so issuing the obvious force
command could fail or target the wrong survey folder.

## Recommended Pipeline Sequence

1. Perform a separately authorized read-only audit of the affected run's
   persisted survey ID, source path, stage outputs, and source-image count.

2. Add a fail-closed recovery path that binds forced segregation to the
   persisted run/survey identity, validates the original source, and refuses
   ambiguous or nonempty destinations without explicit authorization.

3. Cover missing source, identity mismatch, existing outputs, partial copying,
   and successful regeneration with temporary paths and databases.

4. After review, execute a separately authorized production recovery and verify
   regenerated raw/filtered counts before WebODM resumes.

5. Then perform the pending controlled M3M `--uav`/`--rgb` validation and keep
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
