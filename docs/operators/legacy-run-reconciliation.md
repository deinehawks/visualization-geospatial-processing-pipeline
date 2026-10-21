# Reconcile historical local-run warnings

This is an explicit local acknowledgment, **not** database cleanup or a WebODM
repair. The original run/stage statuses and timestamps remain authoritative and
unchanged. A separate receipt says only that the operator reviewed this exact
historical record and confirmed that no legacy pipeline is executing locally.
The console labels it locally reconciled with remote state unverified.

## Operator steps

1. Finish or safely pause all local pipelines and wait for their child tools to
   exit. Close the console server, Map Editor, QGIS and other Python applications
   except the explicitly verified UPSDiscord invocation described below.
   Do not force-kill a pipeline to make this check pass. Close old launchers too;
   an unmodified launcher does not participate in the repository mutex.
2. In a terminal at the repository root, preview **one exact run ID**:

   ```powershell
   py -3.10 tools/pipeline_control.py reconcile-legacy --run-id "<run-id>"
   ```

   This does not create receipts or change any database. Review the survey,
   latest stage, persisted status, owned-operation blocker and process-blocker
   PIDs/names. Use the base interpreter (`py -3.10`), not a virtualenv redirector:
   a second Python redirector process is deliberately considered a blocker.
3. Only if the evidence matches the interrupted run and you confirm there is no
   local execution, copy the **entire** returned `confirmation` string:

   ```powershell
   py -3.10 tools/pipeline_control.py reconcile-legacy --run-id "<run-id>" --confirmation "<entire confirmation from preview>"
   ```

   Do not invent/shorten the phrase. It includes the run ID, exact evidence hash
   and `NO LOCAL PIPELINE IS RUNNING`. Changed evidence requires a fresh preview.
   There is no blanket reconcile-all or force bypass. Repeat separately for each
   reviewed historical run. An owned invocation must use its existing operation
   reconciliation workflow, not this command.
4. Check `py -3.10 tools/pipeline_control.py status`, then restart the console.
   Valid acknowledgments appear separately from remaining active/unverified runs;
   the table still shows the original database status with a local-only note.

If checking processes or ownership fails, stop and diagnose. Nothing is killed,
resumed, replayed, or submitted to WebODM. Other unrelated Python applications
can conservatively block the check; close them normally, never disable the guard.

## Keep the UPSDiscord utility running

The optional `--allow-ups-discord-pid <PID>` exempts **one verified invocation**
for this reconciliation command only. It is not a persistent whitelist, an
exception for all Python processes, or permission to ignore a pipeline process.
Leave UPSDiscord running. Close other pipelines, the console server, Map Editor
and GIS tools normally as above. Use Administrator PowerShell at the repository
root, because background-process metadata may otherwise be inaccessible.

For the operator-reviewed invocation currently identified as PID 1448:

```powershell
py -3.10 tools/pipeline_control.py reconcile-legacy --run-id "<run-id>" --allow-ups-discord-pid 1448
```

This is still a read-only preview. Review `ups_discord_exception`: PID, exact
native creation time (`birth`), interpreter path, startup command line and identity
hash. Also review `process_blockers` and `owned_operations_blocking`. If this is
indeed your unrelated utility and no local pipeline is executing, apply using the
**new** confirmation string and the **same** explicit exception flag:

```powershell
py -3.10 tools/pipeline_control.py reconcile-legacy --run-id "<run-id>" --allow-ups-discord-pid 1448 --confirmation "<entire new confirmation>"
```

The phrase binds both database evidence and utility identity. Old confirmations,
changed/reused PIDs, restarted utilities, changed commands/interpreters and missing
details fail closed. Never replace a failed verification with a generic ignore-PID
option. If UPSDiscord restarts, identify its new PID and review a fresh preview.
Every remaining historical run still requires its own preview and confirmation.

The eligible command is exactly a verified absolute `python.exe` plus
`UPSDiscord.py` (bare name or absolute local script path), with no extra flags or
arguments. `-c`, `-m`, interactive Python, wrappers and other scripts are rejected.
A bare script name does **not** prove its working directory or inspect its code;
the operator is asserting that this exact invocation is their unrelated utility.
This must not be used for a utility that runs the pipeline. Other Python/GIS
processes, including its processing children, remain blockers. Owned-operation
and surviving/unknown-child guards cannot be bypassed by this option.

Verification uses native process creation identity before/after a PID-filtered
local CIM query. Only on explicit opt-in, a fixed noninteractive PowerShell
inspection expression is launched from the native Windows system directory, with
an explicit argument list, `shell=False`, no profile/window and a ten-second
timeout. No user-supplied script/command is executed; UPSDiscord is not launched,
stopped, restarted or modified. No script files or credentials are read. Full
blocking-process snapshots follow the potentially slow query so new blockers
are visible. Inspection is repeated during apply. Failed/hidden/partial metadata
never grants an exception, and failure messages do not echo raw process output.

The atomic reconciliation receipt records the exact approved exception and its
confirmation. It does not install an exemption for later commands or future
processes. Existing no-exception receipts remain readable. Rollback: omit the
option to restore the conservative process check; a source rollback preserves
all database/history/evidence. No database migration or dependency was added.
Windows API basis: [native system directory](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-getsystemdirectoryw)
and [read-only process metadata](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-process).

## Evidence and safety boundary

### Python 3.10 / UI Python 3.13 receipt compatibility

The 2026-10-21 fix removes interpreter-dependent `os.stat().st_dev/st_ino` from
new Windows fingerprint creation. The canonical identity now comes directly from
`GetFileInformationByHandleEx(FileIdInfo)` (full volume serial and 128-bit file
identifier). A shared read-only attributes handle also checks the classic file
information and filesystem type. On NTFS, only when these native representations
agree, legacy Python 3.10 hashes can be recomputed from that same handle. Prior
Python 3.13 hashes match the canonical identity. On ReFS, no 64-bit compatibility
alias is accepted: full 128-bit identity remains required. Missing, incomplete or
inconsistent native metadata fails closed; there is no unverified-stat fallback.

Existing receipts are read, not converted/reissued/overwritten. Compatibility
requires their exact old hash to match current file identity, database path, run,
stage history and task bindings. No status is cleared solely because a receipt
exists. New receipts add `fingerprint_scheme: native-file-identity-v2` and must
match the full canonical identity, never a legacy alias. The database file is
identified before opening SQLite and rechecked after the read transaction; a
replacement during inspection fails. Process/UPSDiscord/exclusive/admission
guards are unchanged. SQLite stays read-only; no table, index, FK or migration.

After deploying the fix, **do not reconcile all records again**. Stop only the UI
server with Ctrl+C in its own terminal, then relaunch `run_pipeline_ui.bat` and
use the browser tab it opens. Keep UPSDiscord and any independent pipeline
processes untouched. If evidence is still unchanged and receipts exist, the old
legacy warning should be replaced by the local-reconciliation notice. The normal
Check inputs & storage preflight is still required before Start. If blocking
remains, diagnose the reported state; never delete receipts, edit statuses, or
bypass admission. Roll back the coordinated source change only while safely
idle; preserve all journals/data. Older code may conservatively block again.

Native reference: [FILE_ID_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info)
and [classic file identity / ReFS limits](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information).

The command holds the same repository Windows mutex as admission, refuses any
occupied/unverified owner or surviving named-job children, and checks a complete
Windows process-name snapshot twice. Standard Python/PyPy, QGIS, GDAL/OGR, PDAL
and ODM executables other than this command block application. The snapshot is
read-only: no shell, subprocess launch or command-line collection by default,
and never any kill. The explicit UPSDiscord option adds only the scoped identity
inspection described above, not a broad process survey or credential collection.
See Microsoft's [Toolhelp process enumeration API](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-process32firstw).

This is a point-in-time check plus an explicit operator assertion, not a claim
that arbitrary renamed executables, another repository/workstation or future
unmodified launchers are controlled. Do not start any old terminal run during or
after reconciliation; resume through updated launchers and normal preflight.

SQLite is opened only with `mode=ro` and `query_only=ON`; no repository constructor,
schema migration or UPDATE/DELETE is used. The snapshot includes the exact run,
stage history and available WebODM bindings, plus local database file identity.
An older database without `webodm_tasks` is supported. Oversized history fails
closed for separate diagnosis. Database paths/options are hashed, not copied into
receipts or displayed by this command.

Apply creates an atomic `runtime/pipeline-ui/legacy-reconciled-<UUID>.json` in the
existing owned workspace. It contains run ID, fingerprint, exact confirmation,
time, process-check method and explicit unverified remote status. Repeating the
same successful acknowledgment is idempotent. Malformed receipts block checks;
failed writes do not unblock the run. Temporary failure evidence is not deleted.
Any changed run, attempt, binding or replacement database invalidates the receipt.

CLI admission, browser dispatch and policy configuration share the same receipt
validation. Reconciliation does not enable two-run mode, allocate survey IDs,
release owned-operation guards, repair task bindings, cancel remote tasks or
authorize uncertain remote resubmissions. Resume retains original identities and
all normal recovery checks. WebODM may still be running remotely.

## Rollback and validation

Cross-interpreter follow-up (2026-10-21): **268 focused, 1108 safe default tests,
1108 safe collection**, changed-file compilation, Python 3.10 grammar and scoped
whitespace passed. Added 23 identity cases and one authenticated API case: old
3.10/3.13 receipts with/without UPS proofs are recognized unchanged; modified
rows/stages/bindings/path/volume/file IDs, unknown schemes, identity-read failures
and replacement races remain blocked. New-scheme receipts require the full
volume identifier even if an old low DWORD happens to collide. Fake Win32 tests
assert read-only access/share modes, ABI sizes and closure on all error paths;
ReFS coverage retains 128-bit IDs without truncation. The common temporary seed
fixture now closes its SQLite connection explicitly so WAL checkpoint timing
cannot invalidate unchanged-byte assertions. No assertion or guard was removed.

Actual Python 3.10.6 and UI Python 3.13.0 additionally produced identical native
identities for `AGENTS.md`. An isolated `:memory:` SQLite run, using that source
file's identity, produced identical canonical and compatibility fingerprints in
both interpreters. This did not open a production database or read runtime
receipts. All other native/service/process boundaries in new tests are fakes;
ordinary filesystem metadata reads are limited to pytest-owned files. Existing
production DB, runtime receipts, datasets, credentials, utility and WebODM/GIS
were untouched. Real UI/production receipt recognition still needs operator smoke.
No frontend source changed, so no frontend rebuild is required for this fix.

Created `operator_ui/file_identity.py`, `tests/test_receipt_file_identity.py`;
modified shared store/reconciler, two existing test modules, this guide and three
refactor records. No moves/deletes, dependency changes or production migrations.
Unrelated worktree changes were preserved. Exact automated commands:

```powershell
python -m pytest tests/test_receipt_file_identity.py tests/test_legacy_reconciliation.py tests/test_ups_process_exception.py tests/test_two_run_execution.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_pipeline_console_windows.py tests/test_import_safety.py -q --tb=short
python -m pytest -q --tb=short
python -m pytest --collect-only -q
python -m py_compile operator_ui/file_identity.py operator_ui/legacy_reconciliation.py operator_ui/storage.py tests/test_receipt_file_identity.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py
```

Runtime modules were parsed with `ast.parse(..., feature_version=(3,10))`.
Native/file hashing probes were run using `py -3.10 -c <probe>` and
`.\.venv-ui\Scripts\python.exe -c <same-probe>`: only `AGENTS.md` metadata and an
explicit `sqlite3.connect(':memory:')` fixture, never `data/pipeline.db`.
Whitespace: `git diff --check -- operator_ui/storage.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md`,
plus `git diff --no-index --check -- /dev/null <file>` for the new identity helper,
identity tests, untracked reconciler and this guide.

UPSDiscord follow-up (2026-10-21): 57 new fake-process/CIM checks, 244 focused
checks, all 1084 safe tests and 1084 safely collected cases passed. Changed-file
compilation, Python 3.10 grammar and scoped whitespace checks passed. No frontend
source changed in this follow-up, so prior frontend validation remains unchanged.
Tests cover default rejection, exact opt-in, wrong scripts/flags/arguments,
birth/PID/interpreter/command changes, unknown permissions, other Python/GIS and
owned-child blockers, new processes during CIM queries, oversized evidence,
audit tampering, CLI allowlists and immutable DB bytes. All native/CIM/subprocess
calls are fake; no production DB, runtime receipt, utility process, survey,
credential or external service was accessed. Real elevated Windows verification
remains an operator check. No reconciliation was applied during implementation.

Created `operator_ui/ups_process.py` and `tests/test_ups_process_exception.py`;
modified the reconciler, operator CLI, this guide and three refactor records.
No files moved/deleted; unrelated existing work remains intact. Exact follow-up
commands:

```powershell
python -m pytest tests/test_ups_process_exception.py tests/test_legacy_reconciliation.py tests/test_two_run_execution.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_pipeline_console_windows.py tests/test_import_safety.py -q --tb=short
python -m pytest -q --tb=short
python -m pytest --collect-only -q
python -m py_compile operator_ui/ups_process.py operator_ui/legacy_reconciliation.py tools/pipeline_control.py tests/test_ups_process_exception.py
```

Runtime files were additionally parsed with `ast.parse(..., feature_version=(3,10))`.
Whitespace: `git diff --check -- docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md`,
plus `git diff --no-index --check -- /dev/null <file>` for the five untracked
task files (new module/test, reconciler, CLI and this operator guide).

There are no database/table/index/foreign-key changes and no data downgrade is
needed. A source rollback that stops recognizing these additive receipts restores
the conservative legacy block; keep receipts/history intact. Stop new invocations
safely before operational rollback. Do not delete journals or manufacture success
statuses. Existing generated data and all processing attempts remain untouched.

Automated validation on 2026-10-21: 187 focused Python checks, all 1027 safe-default
tests and 1027 collection checks; 11 frontend fixtures, Python compilation,
frontend type generation/typecheck/static build. Tests use pytest-owned SQLite
and files, fake process inventories/kernel handles and in-memory ASGI. Coverage
includes no database changes, wrong/stale confirmation, changed bindings/stages,
independent unresolved records, old schema compatibility, idempotence, receipt
write/corruption failure, child/process uncertainty and exact API/UI semantics.
No real DB, runtime journals, process enumeration, GIS or service was used during
implementation. Real Windows process/redirector checks remain operator smoke work.

Exact validation commands:

```powershell
python -m pytest tests/test_legacy_reconciliation.py tests/test_two_run_execution.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_pipeline_console_windows.py tests/test_import_safety.py -q --tb=short
python -m pytest -q --tb=short
python -m pytest --collect-only -q
python -m py_compile operator_ui/legacy_reconciliation.py operator_ui/admission.py operator_ui/service.py operator_ui/api.py tools/pipeline_control.py tests/test_legacy_reconciliation.py tests/test_pipeline_console_api.py
```

In `web/pipeline-console`: `npm.cmd test`, `npm.cmd run typecheck`,
`npm.cmd run build`. Scope `git diff --check` to the task's changed files;
also check new files with `git diff --no-index --check -- /dev/null <new-file>`.
