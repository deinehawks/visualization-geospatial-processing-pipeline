# Next Actions

## Highest Priority

1. Apply the approved storage settings and run an authorized report-only preflight.

Why this is first: implementation and the 278-test safe suite are complete, and
the D: runtime directories are prepared. The protected operator `.env` remains
user-managed. A real report needs an explicitly authorized source dataset.

## Recommended Pipeline Sequence

1. Add the five approved storage/QGIS settings to the operator `.env`.

2. Identify one authorized dataset and run `--storage-preflight-only` without constructing the pipeline.

3. Confirm state-volume, D:, output-volume, and temp-volume reporting before a controlled run.

4. Resume WebODM operation observability and read projection after rollout evidence is accepted.

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
