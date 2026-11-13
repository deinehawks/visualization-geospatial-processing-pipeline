# Operator console redesign validation — 2026-11-13

Branch: `feat/operator-console-guidance`, based on Task 4 commit `5dccb0d`.
This report records implementation validation before the requested commit/push.
Operator smoke testing is in progress; no successful result has been recorded.
Unrelated `codes.txt` and `tools/query_survey_stats.py` edits were preserved.

## Changes and acceptance evidence

Created frontend `Overview.tsx`/`Ui.tsx`, guidance tests and explicit fixture
preview/browser-check scripts. Modified the shell, form, run detail, presentation
helpers and CSS. Python changes are confined to console input fields, CLI mapping,
preflight comparison and recorded-setting provenance, with console regressions.
Operator/API/UI/refactor/architecture documentation is aligned. No moved/deleted
files, dependency changes or schema changes.

Previously technical history, raw durations and equally weighted controls filled
one page. Current status and the next safe action now sit above Progress,
Outputs and Technical details tabs. Overview and forms share branded sections.

| Requirement | Passing evidence |
| --- | --- |
| Current status and readable durations | Node tests cover newest attempts/operations, old failure followed by resumed success and newer forced failure after old success |
| Accessible tabs | Actual Arrow/Home navigation, selected-tab focus and polling persistence; Node Home/End/wrap tests |
| New/large survey flow | Browser enters dataset/node, checks 400/100 profile and 11 base models, submits only to fake API |
| Check invalidation/storage failure | Editing overlap after PASS disables Start; failed storage keeps Start disabled |
| Historical settings/saved profile | Explicit review required; saved profile has no editable toggle; Python tests prove source evidence and UUID-bound profile protection |
| Recovery | Interrupted/remote-failed fixtures show exact fake IDs; wrong phrase disables confirmation; Cancel dispatches no mutation |
| Mapping/recheck | Fake Python launch verifies CLI args, defaults, incompatible modes and split-profile drift rejection before work |
| Outputs/empty history | Completed fixture shows recorded path/copy action; empty history is explicit |
| Responsive visuals | Desktop 1600x1000, laptop 1280x800, narrow 390x844, 900x540 CSS viewport at device scale 2 |

Screenshots were inspected; no whole-page horizontal overflow or browser runtime
exceptions occurred. The scale-2 check is **200%-equivalent viewport reflow**,
not native Windows display scaling. Text contrast against the actual solid
backgrounds: primary 9.25:1, headings 11.00:1, supporting 5.74:1, success 6.54:1,
error 7.01:1, warning 6.30:1 and running 6.49:1 (all above 4.5:1).

## Commands and results

From the repository root:

```powershell
.venv310\Scripts\python.exe -m pytest -q --tb=short tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py
py -3.13 -m pytest -q --tb=short tests/test_pipeline_console.py tests/test_pipeline_console_api.py tests/test_pipeline_console_hooks.py
py -3.13 -m pytest -q --tb=short
.venv310\Scripts\python.exe -m pytest --collect-only -q
py -3.13 -m pytest --collect-only -q
.venv310\Scripts\python.exe -m pytest -q -rs tests/test_pipeline_console_api.py
npm.cmd --prefix web/pipeline-console test
npm.cmd --prefix web/pipeline-console run typecheck
$env:NEXT_TELEMETRY_DISABLED='1'
npm.cmd --prefix web/pipeline-console run build
```

- Python 3.10 focused: **80 passed, 1 skipped**. FastAPI is absent in that
  environment; the optional API module requires `requirements-ui-dev.txt`.
  No dependency was installed in the production Python environment.
- Python 3.13 focused: **100 passed**, including API/session/security tests.
- Python 3.13 full safe suite: **1,460 passed**; collection: **1,460**.
- Python 3.10 collection: **1,440**, with that optional API module unavailable.
- Node: **21 passed**. TypeScript route generation/check and static export pass.

Compile changed Python bytes in memory under both interpreters, without imports
or bytecode (four files each):

```powershell
.venv310\Scripts\python.exe -c "from pathlib import Path; paths=['operator_ui/models.py','operator_ui/runner.py','operator_ui/service.py','tests/test_pipeline_console.py']; [compile(Path(p).read_bytes(),p,'exec') for p in paths]; print('Python 3.10 in-memory compile passed: 4 files')"
py -3.13 -c "from pathlib import Path; paths=['operator_ui/models.py','operator_ui/runner.py','operator_ui/service.py','tests/test_pipeline_console.py']; [compile(Path(p).read_bytes(),p,'exec') for p in paths]; print('Python 3.13 in-memory compile passed: 4 files')"
```

Explicit browser walkthrough, in separate terminals from `web/pipeline-console`:

```powershell
node tests/preview.mjs
node tests/visual.mjs
```

All scenario groups pass. The preview serves static assets and fake API only on
127.0.0.1:8877. It imports no backend/configuration and cannot dispatch a real
pipeline. Browser requests outside this origin are blocked. The installed browser
uses a unique test-owned profile, not the operator's profile. The original browser
tool failed sandbox initialization; the isolated headless browser supplied the
visual checks instead. No browser download occurred.

Screenshots and `result.json` are generated under ignored
`.next/visual-checks/<uuid>/` and may be replaced by future builds. Final inspected
run: `a72f9b5c-9a82-4459-8770-3b17cd60158f`.

The commit review corrected an accidental text insertion in the dashboard
navigation condition and restored the overview description. A browser regression
now verifies the selected Overview styling and description. The static build
and all fixture walkthrough groups pass after that correction. The commit gate
also reran all 21 Node tests, TypeScript and 100 focused Python 3.13 tests.

Final syntax, whitespace and file-scope checks:

```powershell
node --check web/pipeline-console/tests/preview.mjs
node --check web/pipeline-console/tests/visual.mjs
git diff --check -- . ':!codes.txt' ':!tools/query_survey_stats.py'
$consoleNewFiles = @(git ls-files --others --exclude-standard -- web/pipeline-console docs/ui/console-redesign-validation.md)
foreach ($consoleNewFile in $consoleNewFiles) { git diff --no-index --check -- NUL $consoleNewFile; if ($LASTEXITCODE -gt 1) { exit $LASTEXITCODE } }
git diff --name-only
git status --short
```

No production data/configuration/database, survey root, upload cache, network
share, existing task/project, GIS executable or operational service was accessed
or modified. Source/static build files changed as requested. Real Windows scaling,
process ownership and GIS/WebODM operational smoke checks remain separate.
