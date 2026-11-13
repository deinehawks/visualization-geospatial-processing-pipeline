# Pipeline Web Console Status

## Branded guidance release (2026-11-13)

The approved redesign is implemented across Overview, New/Resume and Run Detail
on `feat/operator-console-guidance`. It uses local reusable components and the
supplied brand colors, with routine progress, outputs and technical recovery
separated. Pending split controls and historical-setting review are completed.
There is no schema, dependency, security-boundary or ordinary CLI behavior change.

Validation: 21 Node tests, TypeScript/static build, 100 focused Python 3.13 tests,
80 focused Python 3.10 tests plus one optional API-module skip, 1,460 passing
safe defaults and collection on Python 3.13, 1,440 collected on Python 3.10,
in-memory compilation and scoped whitespace review. A fake-API headless-browser
walkthrough passed responsive, tab, polling, confirmation and execution-form
scenarios. It did not contact real services or start production processing.
Exact commands and acceptance mapping:
[console-redesign-validation.md](console-redesign-validation.md).

Native Windows display scaling, real GIS/WebODM behavior and operational smoke
rollout remain outside this automated visual check. Operator smoke testing is
in progress; its result has not yet been recorded. Commit and push of this
validated UI work were requested on 2026-11-13.

## Two-run extension (2026-10-21)

Explicit legacy local reconciliation is available through the operator CLI.
The dashboard displays local-only acknowledgments separately, including a note
beneath unchanged persisted statuses; it never presents them as completed or
remote-stopped. New evidence invalidates acknowledgment and restores blocking.
The feature has no HTTP apply/bypass endpoint. Automated checks: 187 focused,
1027 defaults/collection, 11 frontend fixtures, compile/typecheck/static build;
real Windows/visual/production reconciliation remains unperformed. Operator steps
and safety scope are in `docs/operators/legacy-run-reconciliation.md`.

The console now uses server-authoritative capacity, shows both active local
invocations/resource waits and offers targeted controls for verified terminal
ownership. Default is one; two-slot policy is an idle operator-only opt-in.
Maintenance/restarts remain exclusive. Legacy/uncertain owners still fail closed.
See `docs/operators/two-run-execution.md` for checks and the separately authorized
real-workstation rollout gate. No live application/policy was started or changed.

## Original V1 baseline

Date: 2026-10-20.

All three V1 implementation milestones are present. Automated acceptance covers
read projections and the static workspace; owned noninteractive execution,
controls/review and restart evidence; and the existing tile-recovery handoff.
Production rollout and visual/real-process smoke verification are **not complete**.

Validation: 257 focused regressions, all 935 safe defaults and safe collection of
935 tests; eight frontend contract tests; syntax, TypeScript route generation/
typecheck, static export, isolated-server `pip check`, and scoped whitespace.
The operator guide contains the exact commands and acceptance mapping. No API
checks were skipped in this environment. Final validation evidence is retained
in the working session rather than copied as large logs into documentation.

The launcher is `run_pipeline_ui.bat`, serving the generated frontend at
`http://127.0.0.1:8765`. `.venv-ui`, frontend dependencies and a static build were
prepared locally. The production `.venv310` environment was not changed, and
the application/server/helper was not started against production resources.

Data/state compatibility: no SQLite migration or new pipeline status enum;
existing CLI defaults preserved; only the shared execution guard intentionally
rejects concurrent launches. New console state is isolated under ignored
`runtime/pipeline-ui`. Historical task modes/settings are never silently inferred
from incomplete bindings. Missing/unsupported data remains explicitly unavailable.

Pending: separately authorized Windows scaling/real kernel-job/process checks,
source preflight, actual WebODM/GDAL review/control/reconnect/resume, remote
restart artifact-readiness validation, and tile-recovery smoke checks. Old
unowned `running` records and ambiguous remote restarts require diagnosis, not
automatic status rewriting or blanket unlock. UI/server closure does not promise
remote cancellation or recovery after a PC reboot; use explicit resume.

No production data/configuration/database, source drive/share, active pipeline,
WebODM/QGIS/GDAL operation, prior output or checkpoint was accessed or changed.
Only optional workspace-local package installation and public docs/index access
occurred. Prior unrelated/map-editor work remains intact. No commit or push.
