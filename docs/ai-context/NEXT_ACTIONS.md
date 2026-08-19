# Next Actions

## Highest Priority

1. Complete WebODM operation observability and read projection.

Why this is first: completed-run cleanup and separate Task 4/Task 2 stage persistence are implemented. The remaining operation-model gap is complete metrics/events and a read-only API projection; mutation controls remain deferred.

## Recommended Pipeline Sequence

1. Add complete Task 4 and Task 2 boundary events and bounded operation metrics.

2. Define stable read-only operation IDs and project existing stage evidence through the documented API schema.

3. Verify `partially_completed` and per-operation evidence in read-only UI/API consumers.

4. Keep operational UI controls disabled until mutation semantics are approved and implemented.

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
