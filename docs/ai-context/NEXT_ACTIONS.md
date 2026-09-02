# Next Actions

## Highest Priority

1. Integrate the validated resume-aware storage feature, then run report-only
   preflight for the audited legacy QGIS resume after E: meets the 10 GiB state reserve.

Why this is first: the isolated implementation and 291-test safe suite are
complete, D: is prepared, and the audited run no longer needs WebODM upload
capacity. E: still has only about 5.54 GiB free and must meet the absolute 10
GiB state reserve. The old workspace remains rollback evidence until the resume
finishes successfully.

## Recommended Pipeline Sequence

1. Integrate the isolated resume-aware feature without disturbing unrelated E: changes.

2. Free at least enough unrelated E: storage to exceed the 10 GiB state reserve;
   do not delete the audited legacy workspace yet.

3. Run the audited command with explicit workspace rebind and
   `--storage-preflight-only`; confirm E: state, D: QGIS/workspace, output, and temp reporting.

4. After a separate external-operation review, run the real resume and verify
   terminal success plus required outputs. Only then review deletion of the exact
   retained 59.88 GiB E: workspace.

5. Resume WebODM operation observability and read projection after rollout evidence is accepted.

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
