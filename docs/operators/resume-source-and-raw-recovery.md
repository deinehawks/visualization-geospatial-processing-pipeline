# Resume Source and Raw-Input Recovery

Use this runbook when an existing run must keep its original run ID but its
persisted source directory is no longer valid, or when the segregated
`rgb/images/raw` directory is missing and the saved filtered `images/path`
output is empty or incomplete.

These options are recovery controls. They do not change normal fresh-run or
resume behavior unless explicitly supplied.

## Safety model

- Source rebind requires `--resume`, an existing run ID, an existing target
  dataset, and the exact phrase `REBIND SOURCE <run-id>`.
- Missing-raw recovery requires `--resume`, a completed data-segregation stage,
  `--force-stage cross_run_filter`, and the exact phrase
  `RECOVER RAW FOR RUN <run-id>`.
- `--storage-preflight-only` is plan-only. It does not update the database,
  copy raw images, construct the pipeline, or run a stage.
- Raw recovery stages source images beside the intended `images/raw` target,
  verifies filenames and sizes, and installs the directory atomically.
- Partial or mismatched existing raw content is never overwritten.
- Existing `images/path` and `images/cross-runs` remain unchanged until the
  replacement cross-run filter completes successfully.
- Recovery and source-rebind events are recorded as stage history. No database
  records are deleted.

## 1. Rebind a missing persisted source

Set the exact existing dataset path. Do not insert backslashes before
underscores in the dataset folder name.

```powershell
$runId = '25dc5134-1631-464b-ab47-8efc2b7c574a'
$survey = 'TLW-001_6.3Ha_A2S_80m_85f75s_5mps'
$source = 'Y:\field-data\2026\sorted\20260420\DelaCerna\TLW-001_6.3Ha_A2S_80m_85f75s_5mps'

Test-Path -LiteralPath $source -PathType Container
```

The final command must print `True`. Plan the rebind first:

```powershell
python main.py `
  --survey $survey `
  --year 2026 `
  --resume `
  --run-id $runId `
  --rebind-source-dir $source `
  --source-rebind-confirmation "REBIND SOURCE $runId" `
  --storage-preflight-only
```

After a passing storage report, apply the rebind by running the same command
without `--storage-preflight-only`. The database update occurs after storage
preflight and before pipeline construction. Repeating the same authorized
rebind is idempotent.

## 2. Diagnose missing segregated inputs

Check the survey image directories without modifying them:

```powershell
$rgb = 'Z:\surveys\2026\AH-026016\rgb'

@('raw', 'path', 'cross-runs') | ForEach-Object {
    $folder = Join-Path $rgb "images\$_"
    $jpegCount = if (Test-Path -LiteralPath $folder -PathType Container) {
        @(
            Get-ChildItem -LiteralPath $folder -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension.ToLowerInvariant() -in '.jpg', '.jpeg' }
        ).Count
    } else {
        0
    }

    [pscustomobject]@{
        Folder = $_
        Exists = Test-Path -LiteralPath $folder -PathType Container
        JPEGs = $jpegCount
    }
} | Format-Table -AutoSize
```

Use missing-raw recovery only when `raw` is missing and the persisted/rebound
source still contains the intended images. The command refuses partial or
mismatched nonempty raw directories instead of guessing which files to keep.

## 3. Plan missing-raw recovery

If the source rebind was already persisted, the source-rebind options are not
needed again:

```powershell
$runId = '25dc5134-1631-464b-ab47-8efc2b7c574a'
$survey = 'TLW-001_6.3Ha_A2S_80m_85f75s_5mps'

python main.py `
  --survey $survey `
  --year 2026 `
  --resume `
  --run-id $runId `
  --force-stage cross_run_filter `
  --recover-missing-raw `
  --raw-recovery-confirmation "RECOVER RAW FOR RUN $runId" `
  --storage-preflight-only
```

The report includes `Raw-input recovery: enabled` and reserves one additional
source-image copy on the survey volume. No files are copied in this mode.

## 4. Execute missing-raw recovery

After the plan passes, remove only `--storage-preflight-only`:

```powershell
python main.py `
  --survey $survey `
  --year 2026 `
  --resume `
  --run-id $runId `
  --force-stage cross_run_filter `
  --recover-missing-raw `
  --raw-recovery-confirmation "RECOVER RAW FOR RUN $runId"
```

The pipeline performs these operations in order:

1. Loads the completed data-segregation output for the existing survey.
2. Rechecks free space immediately before copying.
3. Copies the selected source images into run-owned recovery staging.
4. Verifies the staged filenames and sizes.
5. Atomically installs `images/raw`.
6. Runs a new forced `cross_run_filter` attempt.
7. Replaces `images/path` and `images/cross-runs` only after filtering succeeds.
8. Continues the normal resume flow.

If `delete_raw_after_success` is enabled, a successful filter can delete the
recovered raw directory again after verifying the kept and excluded counts.
That is expected behavior.

## Failure and retry behavior

- An interrupted source copy leaves run-owned staging that the same authorized
  command can resume.
- If recovery installed `raw` but filtering failed, rerunning the same command
  recognizes a complete matching raw directory and proceeds without replacing
  it.
- A partial or mismatched `raw` directory causes a fail-closed error. Inspect it
  before taking any manual action; the recovery command will not delete it.
- Existing filtered outputs are the rollback evidence until the forced filter
  succeeds. The recovery command does not delete or quarantine them first.
- To undo a persisted source rebind, do not edit SQLite manually. Perform a new
  reviewed rebind to the correct same-dataset location using the exact
  confirmation workflow.

## Prohibited combinations

The CLI rejects:

- `--recover-missing-raw` without `--resume`;
- recovery without `--force-stage cross_run_filter`;
- recovery with `--disable-cross-run`;
- a missing or incorrect run-scoped confirmation;
- recovery when the newest data-segregation attempt is not completed; and
- a source-rebind target whose dataset identity does not match the persisted
  dataset.
