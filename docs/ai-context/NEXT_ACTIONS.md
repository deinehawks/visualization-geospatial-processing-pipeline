# Next Actions

## Highest Priority

1. Integrate the validated split-reserve storage follow-up, update the
   user-managed percentage setting to 5, then rerun report-only preflight for
   the audited legacy QGIS resume.

Why this is first: the prior resume reached QGIS successfully and retained a
verified D: clip, then exposed a configured-versus-persisted publication-root
mismatch and an unsuitable percentage reserve for exact network mirrors. The
isolated correction passes 297 safe tests. The old workspace remains rollback
evidence until the resume finishes successfully.

## Recommended Pipeline Sequence

1. Integrate the isolated resume-aware feature without disturbing unrelated E: changes.

2. Set `STORAGE_MIN_FREE_PERCENT=5`; the new published reserve variables default
   safely but may also be written explicitly as 10 GiB/0%. Do not delete the
   audited legacy workspace yet.

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
