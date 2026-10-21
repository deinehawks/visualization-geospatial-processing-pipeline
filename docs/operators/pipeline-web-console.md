# Local Pipeline Web Console V1

## Open it

1. Let the currently running, older terminal process finish or pause and return
   to its prompt. The console never takes over an unowned live terminal process.
2. Double-click `run_pipeline_ui.bat` in the repository. Keep its server window
   open. It opens **http://127.0.0.1:8765/** with a one-use launcher session.
3. Choose **New run**, enter the dataset, survey ID/year and processing mode,
   then **Check inputs & storage**. Select an exact candidate if prompted,
   recheck, and start only after PASS.

The isolated `.venv-ui` environment and static frontend were prepared on this
workstation during implementation. The server itself was not started. The
pipeline helper uses the existing `.venv310/Scripts/python.exe`; that environment
and its production dependencies were not modified. Source imagery is not sent
through the browser, and the application is not available on the office LAN.
Use `127.0.0.1`, not `localhost`; Host and Origin checks are intentionally exact.

Closing the browser does not stop a browser-launched run. Reopen the page while
the launcher is still running. If a browser session cookie is lost, restart the
local UI server with its launcher to obtain a new launcher session. Restarting
the server does not replay operations or terminate an owned pipeline helper.
An old launcher must be closed before starting another server instance. Neither
server shutdown nor browser closure is a substitute for Pause/Stop.

## Normal operation

- Dashboard displays persisted runs, newest stage attempts, failures, and console
  operation receipts. It does not create a missing pipeline database.
- New runs default to Task 4 orthomosaic. Task 2 and Both are explicit choices;
  Both preserves Task 4 then Task 2 ordering. Advanced fields preserve the CLI's
  RGB, UAV/date, node, cross-run and workspace-retention options.
- Each edit invalidates the current preflight. A passing ticket starts only one
  invocation; the helper repeats the shared preflight and rejects changed source,
  mode, paths, configuration fingerprint or stage-status evidence before execution.
- Start performs the normal pipeline's persistent/remote work: survey outputs,
  state, WebODM operations and the existing publication/mirroring/retention policy.
  Review the source and volume report before starting. This is not a dry run.
- Run Detail separates persisted pipeline status from last-observed WebODM status.
  Actual log progress is shown without fabricated percentages. Recorded output
  paths can be copied; the server does not scan/serve imagery or arbitrary files.
- WebODM project links use a safe base URL captured by an explicit preflight.
  Historical CLI records without that evidence do not invent a URL.
- Resume retains run/source/output identity. Recorded browser-run advanced options
  are prefilled; review old CLI-run options because some were never persisted.
  Unknown historical task mode requires explicit selection, not a default guess.

There is one active operation, including checks and launches. Additional requests
are rejected, not queued. Updated `main.py` executions share the same host guard.
Older processes cannot gain ownership retroactively: monitor them until they
have actually returned to the terminal prompt. A stale unowned `running` record
blocks browser dispatch; it is not automatically marked failed or cleared.

## Review, pause, stop and recovery

Quality review appears inside Run Detail with the exact WebODM project/task
identities. Approve, fail, or request an allowlisted internal restart. Restart
requires the displayed exact run/task/stage phrase. Every question has a unique
ID; stale or conflicting decisions cannot answer a later question. Decisions
and command IDs are retained with trusted local-session audit attribution.
The browser adapter retains the current pipeline's quality/restart and artifact
handling semantics; it does not introduce a new remote artifact-refresh policy.

Pause and Stop are **local cooperative requests**, not remote WebODM cancellation.
The UI distinguishes requested/acknowledged/not-applied commands. A blocking
GDAL operation can finish first. Browser controls are checked after a stage as
well as before the next one, preventing a pending final-stage control from
silently proceeding into successful workspace cleanup. The latest completed
stage remains reusable on resume. Current database stop semantics still record
failure; no new database status enum is introduced by this UI.

For unreadable resumed tiles: open the failed run, choose **Tile recovery**,
select Task 2/4, enter the displayed exact confirmation, check storage and then
start. This invokes the existing [guarded tile workflow](qgis-tile-recovery.md).
The old staging tree is quarantined, not discarded. Do not delete quarantine
until the regenerated outputs have been verified and cleanup is separately approved.

If a helper disappears or its identity cannot be verified, new dispatch is
blocked. **Review interrupted operation** requires `RECONCILE <operation_id>` and
verifies that the original process and named-job descendants are gone. PID reuse
is checked with process creation time. The action preserves receipts and does
not replay the request or change pipeline SQLite status.

An ambiguous WebODM restart cannot be resolved by this local acknowledgment.
Its remote intent remains blocking evidence. Stop and request exact-project/task
diagnosis; V1 has no blanket unlock, implicit remote retry, journal deletion or
automatic task replacement. Binding/project repair, raw restoration, source/root
rebinding, arbitrary forced stages, publication and cleanup tools remain deferred.
Existing CLI tools remain available subject to the same ownership/evidence guard.

## Setup on another checkout

Prerequisites: Windows 10/11, the working production `.venv310`, its configured
QGIS/GDAL/WebODM environment, Python 3.10+ for the server, and Node 22 for building.
Do not copy credentials into the frontend or load `.env` in its build.

```powershell
python -m venv .venv-ui
.\.venv-ui\Scripts\python.exe -m pip install --disable-pip-version-check --cache-dir .venv-ui\.pip-cache -r requirements-ui.txt
npm.cmd --prefix web/pipeline-console ci --ignore-scripts --no-audit --no-fund --cache web/pipeline-console/.npm-cache
$env:NEXT_TELEMETRY_DISABLED='1'
npm.cmd --prefix web/pipeline-console run build
.\run_pipeline_ui.bat
```

`requirements-ui-lock.txt` constrains the isolated server to the tested resolution;
the frontend has its own package lock. No production requirements were upgraded.
The initial implementation installed frontend dependencies using
`npm.cmd install --ignore-scripts --no-audit --no-fund --cache .npm-cache` from
`web/pipeline-console`, then created and aligned `.venv-ui` with the commands above.
Its first unconstrained resolution was replaced only inside that new environment
by the tested lock. `pip check` passed. Package downloads/public documentation
were the only external access performed; no operational service was contacted.

## Validation evidence

All helper, Windows kernel, network, QGIS and WebODM boundaries in these tests
are fakes. SQLite and journal paths are pytest-owned. ASGI tests replace asyncio
socketpair wakeups with an in-memory scheduler; normal network guards remain on.
Optional HTTP tests require `requirements-ui-dev.txt` in a developer environment
and explicitly skip when those extras are absent; none were skipped in validation.

```powershell
python -m py_compile main.py pipelines/rgb_pipeline.py shared/pipeline_control.py modules/data_segregation/data_segregation.py operator_ui/__init__.py operator_ui/storage.py operator_ui/models.py operator_ui/projections.py operator_ui/service.py operator_ui/runner.py operator_ui/api.py run_pipeline_ui.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_pipeline_console_windows.py tests/test_main_publication_activation_cli.py
python -m pytest tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_pipeline_console_windows.py tests/test_main_publication_activation_cli.py tests/test_rgb_pipeline_single_stage_execution.py tests/test_qgis_tile_recovery.py tests/test_import_safety.py -q --tb=short
python -m pytest -q --tb=short
python -m pytest --collect-only -q
$env:NEXT_TELEMETRY_DISABLED='1'
npm.cmd --prefix web/pipeline-console test
npm.cmd --prefix web/pipeline-console run typecheck
npm.cmd --prefix web/pipeline-console run build
.\.venv-ui\Scripts\python.exe -m pip check
git diff --check -- .gitignore main.py modules/data_segregation/data_segregation.py pipelines/rgb_pipeline.py shared/pipeline_control.py tests/test_main_publication_activation_cli.py docs/architecture/decisions.md docs/architecture/run-state-model.md docs/contracts/api-contract.md docs/contracts/compatibility-policy.md docs/ai-context/CURRENT_STATE.md docs/ai-context/NEXT_ACTIONS.md docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md
$consoleNewFiles = @(git ls-files --others --exclude-standard -- operator_ui web/pipeline-console run_pipeline_ui.py run_pipeline_ui.bat requirements-ui.txt requirements-ui-dev.txt requirements-ui-lock.txt tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_pipeline_console_windows.py docs/operators/pipeline-web-console.md docs/ui)
foreach ($consoleNewFile in $consoleNewFiles) { git diff --no-index --check -- NUL $consoleNewFile; if ($LASTEXITCODE -gt 1) { exit $LASTEXITCODE } }
git status --short
```

Results and the final command counts are recorded in `docs/ui/UI_STATUS.md`.
Source changes are confined to the launch/control/source-selection seams and
the new console, tests, launcher/manifests and documentation. Earlier map-editor
changes and unrelated deleted BATs/local notes/query defaults were left intact.
Nothing was committed or pushed. No production database, credentials, dataset,
network share, existing map, checkpoint or running pipeline was accessed/modified.

## Authorized smoke check still required

After current processing exits, authorize a small sample and verify: dashboard
and Windows display scaling; exact source/mode/storage review; one-run exclusion
with a second CLI; browser closure/server reconnect during processing; manual
review and exact task restart behavior/artifact readiness; cooperative pause/stop
during WebODM and GDAL; exact-ID resume; and successful tile quarantine/rebuild.
Retain all evidence. No real storage, WebODM, QGIS/GDAL, Windows job inheritance,
or production-scale/UI visual rollout was exercised by the mocked test suite.
Do not mark production rollout complete on the strength of these tests alone.

Rollback: stop only the local web server, leave active helpers/evidence intact,
and use the CLI after ownership is clear. Reverting source changes requires no
database downgrade. Do not remove `runtime/pipeline-ui` as an unlock mechanism.
