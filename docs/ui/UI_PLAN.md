# Pipeline Web Console V1 Plan

Approved 2026-10-20: replace terminal commands for normal single-operator use on
this Windows PC. Next.js static pages use FastAPI only; no browser DB/filesystem
access, LAN deployment, queue, parallel processing, or arbitrary shell endpoint.

Milestones:

1. Read-only dashboard/history/detail, stage attempts, bounded logs and recorded
   artifacts, with honest unknown/missing/remote-observation labels.
2. One owned helper for checks/start/resume; shared CLI exclusion; structured
   preflight/results; manual browser quality review; cooperative pause/local stop;
   immutable requests, idempotent decisions and crash evidence.
3. Exact-confirmation existing tile quarantine/rebuild, secure loopback session,
   local reconciliation without remote replay, tests/build and operator guidance.

The initial read-only restriction is superseded only for these approved controls.
Publication/cleanup/binding repair/raw recovery/rebinding/force-stage tooling,
map-editor integration, automatic quality approval and general scheduling remain
out of scope. Global kernel guards and named jobs are single-host admission and
process ownership, not completion of the broader locking/queue refactor phases.

Implementation and automated validation do not authorize real production testing.
See [status](UI_STATUS.md) and [operator guide](../operators/pipeline-web-console.md).
