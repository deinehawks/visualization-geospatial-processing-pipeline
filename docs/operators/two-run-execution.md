# Safe two-run execution

## Rollout boundary

Implemented for one Windows workstation and one installed repository. The default
is still **one** local run. The agent has not read/changed the live policy or
enabled concurrency. Do not enable while an existing terminal/helper is active.
Hermetic tests are not the separately authorized Windows/GIS/storage smoke check.

No database migration, new dependency, queue, scheduler, or remote cancellation
is introduced. Multiple repository copies/workstations and arbitrary direct
`RGBPipeline` callers are not coordinated. Use the updated `main.py` or console.
The explicitly external low-level `tools/run_rgb_pipeline.py` now takes an
exclusive guard and is rejected in two-run mode because it lacks preflight.

## Policy and controls

Old unowned database rows still marked running require an explicit diagnostic
review. See [local legacy reconciliation](legacy-run-reconciliation.md) for the
preview/confirmation workflow; it preserves database statuses and WebODM evidence.
It does not enable concurrency or bypass uncertain owned operations.

After all old local runs finish/pause and return to their prompts, inspect state:

```powershell
python tools/pipeline_control.py status
```

After separately authorizing/completing the smoke checklist below, opt in:

```powershell
python tools/pipeline_control.py configure --max-runs 2
```

The configuration command refuses live, accepted, unverified, unreconciled, or
legacy-running ownership. It writes only the owned runtime policy, not `.env`,
database records, or source/output data. Repeating a normal pipeline command in
a second terminal can then admit a different survey. A third run fails clearly;
there is no automatic queue. Read-only browser preflight helpers temporarily
consume a slot too. Retry Start after a completed helper has fully exited.

Global Ctrl+Shift+P/Q hooks are disabled under the two-slot policy. Use the selected
run's browser buttons (also for owned CLI invocations), or explicit run IDs:

```powershell
python tools/pipeline_control.py pause --run-id "<exact-run-id>"
python tools/pipeline_control.py stop --run-id "<exact-run-id>"
```

Requests are invocation-bound, audited, idempotent by optional `--command-id`, and
acknowledged at cooperative checkpoints. Blocking GIS and terminal quality input
can delay acknowledgment. CLI quality review stays in that terminal; pending
controls are checked before its answer is applied. Neither command cancels ODM.
Returning to one slot, only while idle/reconciled, is the operational rollback:

```powershell
python tools/pipeline_control.py configure --max-runs 1
```

Do not remove journals/locks/flags to manufacture free capacity. Finished local
ownership is not proof that WebODM stopped. Remote binding repair and ambiguous
creation/restart outcomes still require explicit diagnosis; local reconciliation
cannot discard unresolved remote evidence. There is deliberately no blanket
force-unlock command or automatic remote retry.

## Isolation and waiting

Admission atomically reserves identity and storage: run ID, case-normalized
survey ID, source/workspace/output overlap, and remote project identity. Resume
keeps persisted source/output/year/identity and rechecks state after admission.
Automatic survey IDs are allocated and their RGB directory exclusively claimed
under the same coordinator mutex; preflight never allocates. Explicit IDs use
that ownership check too. Existing survey output locations do not move.

`survey-allocation.json` retains the first claim and an additive
`.pipeline-allocation.json` marker identifies its folder owner. Retries and a
later resume reuse that claim even if the DB survey attachment never completed.
Missing/mismatched markers require diagnosis; they never authorize a new ID or
unverified reuse of an existing partial folder.

At most one compute permit covers QGIS/GDAL commands, WebODM export reprojection,
and point-cloud conversion. At most one transfer permit covers uploads, downloads,
large copies/cache staging and publication. Do not nest unlike permits. Remote
polling and quality review hold neither permit, but do retain the run slot.
Resource waits show holder IDs in the console and poll cooperative controls.
Forced/recovery/rebind/publication operations are exclusive; a quality-triggered
task restart also requires atomic escalation to exclusive ownership.

Volume reservations retain the original conservative estimate until the local
invocation and its children safely exit. They are not exact disk-use telemetry;
already-written bytes can be budgeted again, intentionally rejecting borderline
admission rather than promising space twice. Current free capacity is rechecked
before resource-backed work. Other apps/storage hosts can still consume space.
Two local slots do not limit remote WebODM tasks left running after local pause;
node/Docker capacity and remote scheduling remain separate operator concerns.
Windows mapped shares resolve to UNC; local mount points use volume identity.
Unverifiable mappings fail closed. Storage server DNS/share aliases or different
shares backed by the same remote disk are not automatically discovered; use a
single consistent share namespace and validate the workstation mapping in smoke.

Write transactions and repository initialization serialize independently of
admission. Verified SQLITE_BUSY commits alone have bounded retries; a failed
state commit never authorizes replaying processing. Missing final stage state
becomes recovery-required where writable, with retained journal/error evidence
otherwise. Returned project/task IDs have creation receipts before binding and
binding acknowledgments after durable state. No older completion masks failure.

New writes use `data/logs/runs/<run-id>/<invocation-id>/`; checkpoints use the
parent run directory. Historical global logs/checkpoints remain readable and
unchanged. Workspace/cache/staging paths remain run-owned, and generated GDAL
batch filenames are unique. A paused run releases its local slot only after
owned descendants are gone; project ownership is not reassigned to another run.

## Validation

Final automated results (2026-10-21): **985 passed**, **318 focused passed**,
**985 safely collected**; 48 dedicated coordination cases, two added API cases,
and 10 frontend fixtures. Changed-file compilation, Python 3.10 grammar for 20
runtime modules, frontend type generation/typecheck/static build, isolated UI
dependency consistency and scoped tracked/new-file whitespace checks passed.
No optional API checks were skipped. These are not real-process/GIS smoke results.

Automated checks use temporary SQLite/filesystems, fake Windows/kernel/service/
GIS/process boundaries and existing network/credential/deletion guards. Coverage
includes races, alias failure, duplicate and overlapping identities, ID allocation,
aggregate space, idle policy changes, legacy/crash/PID reuse, surviving children,
wait cancellation, independent controls/logs, state commit failure without replay,
uncertain mutation receipts, capacity API authentication and frontend action rules.

Commands (repository root unless noted):

```powershell
python -m pytest tests/test_two_run_execution.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_pipeline_console_windows.py tests/test_main_publication_activation_cli.py tests/test_rgb_pipeline_single_stage_execution.py tests/test_stage_runner_orchestration.py tests/test_qgis_tools_observability.py tests/test_import_safety.py -q --tb=short
python -m pytest -q --tb=short
python -m pytest --collect-only -q
```

Changed Python modules are checked with `python -m py_compile` and Python 3.10
grammar parsing. In `web/pipeline-console`, run `npm.cmd test`,
`npm.cmd run typecheck`, and `npm.cmd run build`; run isolated UI `pip check`.
Whitespace checks are scoped to changed/new task files, not local operator edits.

Exact compile command used:

```powershell
python -m py_compile main.py modules/data_segregation/data_segregation.py modules/qgis/qgis_tools.py modules/webodm/webodm_processor.py operator_ui/admission.py operator_ui/api.py operator_ui/controls.py operator_ui/coordination.py operator_ui/identities.py operator_ui/projections.py operator_ui/runner.py operator_ui/service.py operator_ui/storage.py pipelines/rgb_pipeline.py shared/db/repo.py shared/execution.py shared/stage_runner.py shared/storage_preflight.py tests/test_import_safety.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_two_run_execution.py tools/pipeline_control.py tools/run_rgb_pipeline.py
.\.venv-ui\Scripts\python.exe -m pip check
```

Windows API basis: [mapped-path universal names](https://learn.microsoft.com/en-us/windows/win32/api/winnetwk/nf-winnetwk-wnetgetuniversalnamew)
and [volume mount paths](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumepathnamew).

Pending separately authorized checks: real mutex/job inheritance across two
terminals and the console, workstation drive/UNC aliases and volume mapping,
one upload/compute owner at a time, controls while waiting/reviewing, safe
pause/resume, and small real-survey output consistency. Never use real data
for crash injection. Keep one slot until these checks are accepted.
