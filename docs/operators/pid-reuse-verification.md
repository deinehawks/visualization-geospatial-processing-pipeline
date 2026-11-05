# Finished invocation blocked by an inaccessible reused PID

An operation can be `finished` yet remain occupied if its recorded PID is now
inaccessible or its owned child-process job cannot be verified. A reused PID is
not permission to terminate the current process or delete the old journal.

## Verification behavior

Native Windows creation-time/exit checks remain the first choice. If native
identity is `unknown` for a recorded owner with a valid native birth value, the
adapter may run one fixed, local, PID-filtered CIM query helper. It reads two
creation-time snapshots, requires them to agree, and rechecks native identity.
It does not enumerate all processes or read command lines, datasets or credentials.

The helper uses trusted system PowerShell, explicit arguments, no shell/profile,
no input/window and a five-second timeout. Failure, missing data, changed samples,
unknown timezone, malformed/oversized output or future timestamps remain unknown.

CIM is deliberately **negative-only evidence**, not an exact native identity.
The current creation time must be more than one second later than the recorded
native birth; earlier or nearby timestamps are inconclusive. This prevents
coarser CIM precision from making a live owner look like a reused PID. CIM never
authorizes a new process claim, reconnection, pause/stop control, or UPS exception.
Those positive ownership checks retain their native verification requirements.

The existing guards still decide admission:

- A terminal invocation with a verified newer PID and zero owned children no
  longer occupies capacity. The historical journal and its exclusive flag stay
  unchanged; they are not deleted, reset, or marked successful.
- Live/unknown children, missing/ambiguous identity or unresolved remote
  creation/restart evidence still block execution.
- Nonterminal or `requires_recovery` records still require explicit supported
  reconciliation, even when PID reuse is proved. This patch does not perform it.
- One/two-slot limits, identity/storage checks and exclusive maintenance
  remain unchanged. Fallback evidence is re-read, not cached or persisted.

No database/schema migration, new dependency, queue, worker or process-termination
operation is introduced. The direct `WindowsProcesses.identity()` diagnostic
still reports native-only results; admission uses `owner_identity(pid, birth)`.

## Operator steps

From the repository, run the existing diagnostic again:

```powershell
py -3.10 tools/pipeline_control.py status
```

For the reported finished AH-026125 invocation, the record binds PID 1636 to
birth `134372383601303920`. The reported newer creation time and empty old job
are the case this fallback addresses, but actual Windows/CIM behavior still
needs this operator check. Do not assume that any particular PID always belongs
to an unrelated process, and do not add a permanent PID exception.

If it remains blocked, preserve and report the status plus any
`owner_identity_unverified` message. Do not remove reservations, disable child
checks, kill the reused PID, or simply increase `max_runs`. Additional real
process inspection/reconciliation requires explicit operator authorization.

Existing CLI launches load the updated code automatically. A long-running web
console needs a server restart to load it; do so only when its helpers are idle.
Restarting the console does not mean restarting Docker or interrupting pipelines.
After capacity is unblocked, resume the original failed run ID rather than
creating another fresh run. Storage and persisted identity are checked again.

Rollback: reverting the scoped source restores conservative unknown-owner
blocking; no journal/data rewrite is needed. Preserve all historical evidence.

## Validation

All native, process and CIM boundaries are fakes; databases and journals belong
to pytest. No real PowerShell/CIM/process query, pipeline, production database,
credential, source drive, GIS or external service is used during validation.
Tests cover the reported times, native fast path, timestamp precision, races,
missing evidence, PID injection, strict output validation, safe invocation,
error redaction, import safety, unchanged journals, admission and UI projections,
reconciliation, remote uncertainty, child guards and control refusal.

Commands executed:

```powershell
.\.venv310\Scripts\python.exe -m pytest tests/test_process_identity_fallback.py tests/test_pipeline_console_windows.py -q --tb=short
py -3.13 -m pytest tests/test_process_identity_fallback.py tests/test_pipeline_console_windows.py tests/test_two_run_execution.py tests/test_three_run_execution.py tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py tests/test_ups_process_exception.py tests/test_legacy_reconciliation.py tests/test_import_safety.py -q --tb=short
py -3.13 -m pytest -q --tb=short
py -3.13 -m pytest --collect-only -q | Select-Object -Last 3
git diff --check -- operator_ui/storage.py operator_ui/admission.py operator_ui/service.py docs/operators/two-run-execution.md docs/refactor/CURRENT_STATUS.md docs/refactor/DECISIONS.md docs/refactor/TEST_STRATEGY.md
```

Results: 87 new cases plus nine native adapter cases pass on Python 3.10;
411 focused and all 1322 safe default tests pass on Python 3.13, with 1322 safely
collected. No tests or guards are disabled. No frontend code/contract changed,
so frontend builds are outside this scoped backend patch.

Five scoped Python files compile in memory using `py -3.10 -B -c` and this probe:

```python
from pathlib import Path
files = ('operator_ui/process_identity.py', 'operator_ui/storage.py', 'operator_ui/admission.py', 'operator_ui/service.py', 'tests/test_process_identity_fallback.py')
for name in files:
    compile(Path(name).read_text(encoding='utf-8-sig'), name, 'exec')
for name in ('operator_ui/process_identity.py', 'tests/test_process_identity_fallback.py'):
    value = Path(name).read_text(encoding='utf-8-sig')
    assert value.endswith('\n') and not value.endswith('\n\n'), name
    assert all(line == line.rstrip() for line in value.splitlines()), name
```

New files additionally receive explicit whitespace/EOF checks, since untracked
files are not included by `git diff --check`. Three runtime files modified,
one helper/test module/guide added, this guide linked from the two-run guide and
three refactor records updated. No files moved/deleted; prior worktree edits,
deleted BATs and journals are preserved. No real reconciliation, production
write, commit, push or dependency installation was performed. Real Windows/CIM
smoke verification remains operator work, not claimed by these mocked tests.
