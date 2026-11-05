# Three-run execution opt-in

## Scope (2026-10-23)

The existing single-host coordinator now accepts **1, 2, or 3** local slots.
The no-policy default remains **one**; existing one/two-slot policies and version
1 journals remain valid. This is an opt-in extension, not a queue, new worker,
database migration, automatic worker selection, or permission to bypass recovery.
All pipeline terminals and the console must use this one repository installation
on the main workstation. NodeODM workers may be on other computers, but do not
launch another pipeline installation against the same surveys/database.

The [two-run guide](two-run-execution.md) still defines identity, storage, process
ownership, transaction, controls and resource safety. Only its upper limit of two
slots and rejection of a third ordinary run are superseded by this opt-in.

## Activation and rollback

1. Finish or cooperatively pause every local invocation and wait for its terminal
   prompt and verified child exit. An active quality-review prompt is not idle.
   Resolve uncertain ownership through the existing reviewed workflow, never by
   deleting journals or declaring a live invocation reconciled.
2. Restart an already-running console server after loading the updated source;
   an old server may still enforce the old two-slot validator. Stop only the
   console server, not Docker/WebODM or processing nodes. Retain all run evidence.
3. Inspect status, then opt in through the guarded command:

```powershell
py -3.10 tools/pipeline_control.py status
py -3.10 tools/pipeline_control.py configure --max-runs 3
py -3.10 tools/pipeline_control.py status
```

Configuration refuses occupied, unverified, or unreconciled ownership under the
same host mutex. It atomically changes only
`runtime/pipeline-ui/execution-policy.json`, preserving journal format version 1.
An unsuccessful configuration must leave the previous policy intact. No run is
started or replayed. After activation, the authenticated capacity API and console
display three slots without a browser policy-write endpoint or frontend redesign.

Rollback uses the updated coordinator while idle, **before** reverting source:

```powershell
py -3.10 tools/pipeline_control.py configure --max-runs 2
```

One slot remains supported as well. Older source rejects a saved three-slot
policy, so do not revert source while that policy remains active. Preserve all
journals, controls, storage commitments and output evidence.

## What three slots do not change

- Accepted requests, launching invocations and preflight helpers consume slots;
  a fourth request is rejected, not queued. Unknown owners block all admission.
- Forced stages, recovery, rebinding, publication maintenance and quality-triggered
  restarts remain exclusive. **Increasing capacity cannot fix an exclusive BUSY.**
- Duplicate surveys/run IDs, overlapping paths, mapped/UNC aliases and shared
  remote project ownership remain protected against every other owner.
- Storage commitments aggregate across all three invocations by physical
  volume/share. Free slots are not a guarantee of available disk space.
- Only one local compute permit and one bulk-transfer permit exist. GIS work,
  uploads/downloads, cache staging and publication copies retain existing limits.
- Global hotkeys are disabled for both two- and three-slot invocations. Use exact
  run-ID controls; requests remain pending until cooperative acknowledgment.
- `--node-id` selects the WebODM processing node for newly created tasks only.
  Three pipeline slots do not provide three remote workers or move bound tasks.

The compatibility property `Execution.two_runs` now denotes any multi-slot
invocation; it remains available to existing callers. Identity/storage evidence
is required for every non-preflight invocation whenever capacity exceeds one.

## Controlled rollout observations (2026-11-04 to 2026-11-05)

The first operator rollout used three independent surveys with the policy set to
`max_runs: 3` and two approved WebODM processing nodes (IDs 3 and 5). Three local
slots do not require three processing nodes: WebODM may queue work when both
nodes are occupied. They also do not prove that two nodes have enough CPU, RAM,
disk or network capacity for three concurrent surveys.

The initial three-terminal attempt stopped safely in processing-node preflight
because WebODM reported every node offline. No task was created by those failed
attempts. The nodes repeatedly returned online when clicked in WebODM and then
turned red again after several minutes. Diagnosis on the local WebODM host found
the `webapp` container running without its Celery beat scheduler; a stale beat
PID had also been observed. Starting the scheduler with
`docker exec webapp /webodm/worker.sh scheduler start` restored the beat process.
The operator must verify that it remains present after any WebODM/container
restart; clicking a red node is only a temporary refresh, not the repair.

The failed node preflight previously included the raw node API object in its
error text. The scoped security fix now emits only allowlisted scalar diagnostic
fields and has mocked regression coverage for token/password/nested-secret
redaction. Historical logs are not rewritten and must still be treated as
sensitive.

This evidence establishes fail-closed admission and identifies the scheduler
dependency. It does **not** complete the controlled rollout: three simultaneous
survey runs have not yet completed successfully under observation. Record task
routing, queue time, node stability, host/storage pressure and final artifacts
before declaring three-slot production readiness.

## Validation evidence

All new tests use pytest-owned temporary paths/SQLite and fake process, lock,
service and GIS boundaries. No safety fixture, assertion or test level was relaxed.
The existing coordination fixture runs at both two and three slots; added cases
cover a fourth-run race, all-owner conflicts, aggregate storage, preflight slot
consumption, invalid policies, idle-only switching, three independent controls,
two cancellable permit waiters, three-way ID allocation and CLI hotkey suppression.
API and frontend fixtures verify three slots and unchanged exclusivity.

Executed commands:

```powershell
.\.venv310\Scripts\python.exe -m pytest tests/test_two_run_execution.py -q --tb=short -k "three-slots and updated_cli"
.\.venv310\Scripts\python.exe -m pytest tests/test_two_run_execution.py tests/test_three_run_execution.py tests/test_pipeline_console_api.py -q --tb=short
py -3.13 -m pytest tests/test_two_run_execution.py tests/test_three_run_execution.py tests/test_pipeline_console_api.py -q --tb=short
.\.venv310\Scripts\python.exe -m pytest tests/test_three_run_execution.py -q --tb=short
py -3.13 -m pytest -q --tb=short
py -3.13 -m pytest --collect-only -q
```

The first command confirmed the old policy rejects three before the runtime fix.
The initial Python 3.10 focused run reported 125 passed, one failed and one skipped:
the unchanged commit-retry test references `sqlite3.SQLITE_BUSY`, unavailable in
that interpreter, and optional API dependencies are absent in that environment.
No test was removed or weakened. All **40** new cases pass on Python 3.10; all
**147** focused cases and **1187** default cases pass on existing development
Python 3.13, with **1187** safely collected. All six changed Python files also
compile in memory under Python 3.10 without executing modules or writing bytecode.

Syntax command (the same six scoped files, no module execution):

```powershell
$probe = @'
from pathlib import Path
files = ('operator_ui/admission.py', 'operator_ui/coordination.py', 'tools/pipeline_control.py', 'tests/test_two_run_execution.py', 'tests/test_three_run_execution.py', 'tests/test_pipeline_console_api.py')
for name in files:
    compile(Path(name).read_text(encoding='utf-8'), name, 'exec')
print('Python 3.10 syntax passed for all 6 changed Python files; no modules executed')
'@
py -3.10 -B -c $probe
```

Frontend validation runs in `web/pipeline-console`, with telemetry disabled and
local `.env` files rejected before typecheck/build:

```powershell
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

All 13 frontend contract cases, typecheck and static build passed. Scoped
whitespace/diff inspection includes the three runtime modules, three Python test
modules, the frontend fixture, this guide, the capacity contract, the two linked
operator guides and the three refactor records. Unrelated operator edits/deleted
BATs are not staged, reverted or restored. No dependency installation, real
pipeline/GIS/WebODM call, production DB write or destructive operation is part
of this implementation validation.

```powershell
git diff --check -- operator_ui/admission.py operator_ui/coordination.py tools/pipeline_control.py tests/test_two_run_execution.py tests/test_pipeline_console_api.py web/pipeline-console/tests/contract.test.mjs docs/contracts/api-contract.md docs/operators/two-run-execution.md docs/operators/pipeline-web-console.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md docs/refactor/CURRENT_STATUS.md
```

The two new untracked files additionally receive explicit trailing-whitespace and
blank-EOF checks before handoff; `git diff --check` alone omits untracked files.

Real three-terminal Windows/job inheritance, NodeODM routing, storage and GIS
smoke checks are still operator work. Tests do not establish production capacity,
adequacy of worker RAM/storage, or removal of the exclusive maintenance boundary.
