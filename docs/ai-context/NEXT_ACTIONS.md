# Next Actions

## Highest Priority

1. Implement default completed-run workspace cleanup with `--keep-workspace` opt-out.

Why this is first: retained run workspaces can consume hundreds of GB per run. The accepted target is to clean fully `completed` run workspaces by default while preserving partial, failed, paused, aborted, canceled, and recovery-required evidence.

## Recommended Pipeline Sequence

1. Add `--keep-workspace` without changing failure, pause, abort, cancellation, or recovery retention behavior.

2. Add guarded cleanup for fully `completed` runs only, after final success state persistence and after required outputs are already mirrored or published.

3. Preserve workspaces by default for `partially_completed`, failed, paused, aborted, canceled, and `requires_recovery` paths.

4. Validate cleanup with pytest-owned workspaces, ownership checks, containment checks, and regression coverage proving published/legacy outputs remain.

5. Continue hardening the approved combined WebODM `--both-tasks` runtime contract, especially separate per-operation stage records and API/UI projection.

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
