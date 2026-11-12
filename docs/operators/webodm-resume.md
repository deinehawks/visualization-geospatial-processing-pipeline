# WebODM Pause, Resume, and Binding Repair

## Pause behavior

`tools/pause_run.py` pauses local pipeline orchestration only. A queued or running WebODM task continues in WebODM. The pipeline records active stage attempts as `paused` and retains the project/task binding.

Do not manually create a replacement project or task. If the remote task completes while the pipeline is paused, resume downloads and validates its orthomosaic.

## Normal Task 4 resume

Use the original run ID:

```powershell
python main.py ... --resume --run-id <run-id> --force-stage webodm_task4
```

`webodm_task4` reconciles the exact persisted project ID and task UUID. It does not upload a replacement. Remote states are handled as follows:

- queued/running: wait on the same UUID;
- completed: export and validate the orthomosaic;
- canceled: stop; restart that same task in WebODM if appropriate, then resume again;
- failed: stop and preserve the binding;
- confirmed missing or conflicting identity: require repair;
- network/authentication/server lookup failure: fail against the same binding without creating a task.

In a normal full pipeline invocation, forcing a WebODM operation also invalidates
its completed `quality_gate` and `qgis` results. After the exact Task 4 UUID is
exported, the pipeline reruns quality selection and QGIS clipping/tiles without
reusing the previous readable clip. Storage preflight therefore includes QGIS
staging, workspace and published-output capacity. An explicitly selected
programmatic WebODM-only run remains isolated for repair/testing workflows.

Always start a completed-task refresh with `--storage-preflight-only`. Each
artifact replacement remains atomic: a failed replacement preserves its prior
target, while a later-stage failure retains earlier successful replacements and
leaves not-yet-replaced artifacts available for diagnosis/resume.

## Repair a historical binding

Dry-run is the default:

```powershell
python tools/repair_webodm_binding.py `
  --run-id <run-id> `
  --operation task4 `
  --project-id <project-id> `
  --task-id <task-uuid>
```

The dry run validates the run, fetches the exact WebODM task, checks its name and status, reports conflicts, and lists the local changes required.

After reviewing the report, apply explicitly:

```powershell
python tools/repair_webodm_binding.py `
  --run-id <run-id> `
  --operation task4 `
  --project-id <project-id> `
  --task-id <task-uuid> `
  --apply
```

`--apply` records the validated binding and audit history and atomically updates the compatibility checkpoint. It does not restart/cancel a task, upload/download data, or delete duplicate projects. Review duplicate projects separately before any manual cleanup.

## Recover a persisted project with no tasks

Use this only when an earlier process persisted a Task 4 project but stopped
before WebODM created any task. The guarded resume requires the original run,
the persisted project ID, `--force-stage webodm_task4`, and the exact
confirmation phrase:

```powershell
python main.py ... `
  --resume `
  --run-id <run-id> `
  --force-stage webodm_task4 `
  --recover-empty-webodm-project task4 `
  --webodm-recovery-project-id <project-id> `
  --webodm-recovery-confirmation "CREATE TASK4 IN EMPTY WEBODM PROJECT <project-id> FOR RUN <run-id>"
```

Immediately before upload, the pipeline lists every task in the exact persisted
project. It creates Task 4 only when WebODM returns a well-formed, complete list
containing zero tasks. Any existing task, identity mismatch, malformed response,
or lookup/authentication/network failure stops without upload. If a task exists,
use the exact UUID repair workflow instead.

Authorization is appended to binding history before upload. The normal Task 4
path then stages imagery, creates one task, and persists its UUID. If task
creation returns ambiguously, do not repeat ordinary creation or delete the
project; inspect WebODM and repair the exact UUID if a task appeared.

## Recover a persisted project that was deleted

This is different from empty-project recovery. Use it only when the exact
persisted Task 4 project returns an authenticated WebODM `404`, the binding has
no Task 4 UUID, and no other operation has a durable task UUID.

Start with a local storage/authorization plan. It does not contact WebODM,
create a project, upload images, or update the database:

```powershell
python main.py ... `
  --resume `
  --run-id <run-id> `
  --force-stage webodm_task4 `
  --recover-missing-webodm-project task4 `
  --missing-webodm-project-id <missing-project-id> `
  --missing-webodm-recovery-confirmation "CREATE REPLACEMENT WEBODM PROJECT FOR MISSING PROJECT <missing-project-id> FOR RUN <run-id>" `
  --storage-preflight-only
```

After reviewing the plan, remove `--storage-preflight-only` to execute. The
pipeline authenticates with WebODM and fetches the exact old project first. It
creates one replacement only for a confirmed `404`. An existing project,
authentication/permission failure, timeout, connection failure, server error,
malformed response, conflicting local identity, existing Task 4 UUID, or
durable Task 1/2 UUID stops before project creation.

Before the non-idempotent WebODM create call, the pipeline atomically writes
`webodm_project_recovery_<run-id>.json` in the checkpoint directory. After a
successful response it records the returned project ID in that receipt, then
appends replacement bindings and audit evidence without deleting the old
binding history. Project-only legacy Task 1/2 placeholders are rebound to keep
future resume identity consistent; they never authorize Task 1/2 creation.

If the process stops after receiving the new project ID but before the binding
is durable, repeat the same command. It reuses the receipt only after the exact
replacement project exists, has the expected name, and contains zero tasks. If
creation returns ambiguously, the receipt is marked `creation_ambiguous` and
automatic retries are blocked. Inspect WebODM and the receipt before any
further recovery; never delete a possible replacement or retry project
creation blindly.

After the replacement binding is durable, Task 4 follows the normal guarded
creation path and persists its UUID immediately. Ordinary resume behavior is
unchanged when the missing-project recovery flags are absent. Empty-project
and missing-project recovery flags are mutually exclusive.

## Recover Task 2 when Task 4 already exists

Use this only for a combined run where Task 4 is durably bound and completed,
but the process stopped before Task 2 was created. Start with a plan-only
command:

```powershell
python main.py ... `
  --both-tasks `
  --resume `
  --run-id <run-id> `
  --force-stage webodm_task2 `
  --recover-missing-webodm-task task2 `
  --webodm-task-recovery-project-id <project-id> `
  --webodm-task-recovery-confirmation "CREATE TASK2 IN WEBODM PROJECT <project-id> FOR RUN <run-id>" `
  --storage-preflight-only
```

The plan does not contact WebODM, upload imagery, create a task, or change the
database. After reviewing it, remove `--storage-preflight-only` to execute.

Immediately before upload, the pipeline verifies the exact persisted project
and lists its complete task set. Recovery proceeds only when that set contains
exactly one well-formed task: the same Task 4 UUID and name already bound to
the run, with both local and remote Task 4 status completed. An extra task,
missing Task 4, identity mismatch, non-completed status, malformed response,
or lookup/authentication/network failure stops before upload. The explicit
authorization is appended to binding history before the ordinary Task 2
creation path runs, and the new Task 2 UUID is persisted immediately after a
successful create response. Task 4 is not restarted or replaced.

If task creation returns ambiguously, do not repeat the creation command. Check
the exact WebODM project. If Task 2 exists, use
`tools/repair_webodm_binding.py --operation task2` with its exact UUID; dry-run
is the default. If no Task 2 exists, preserve the failure evidence and diagnose
the create failure before attempting another explicitly authorized recovery.

## Run-scoped local Task 4 split/merge

For one large, geotagged Task 4 survey, enable WebODM's existing local
split/merge workflow without changing `.env` defaults:

```powershell
python main.py `
  --survey "<dataset>" `
  --year 2026 `
  --survey-id "<survey-id>" `
  --task4 `
  --node-id 3 `
  --task4-split 400 `
  --task4-split-overlap 100 `
  --storage-preflight-only
```

`--task4-split` is the average number of images per submodel. The overlap is a
radius in metres and defaults to 100 when omitted. The CLI values override only
the `split` and `split-overlap` keys from `WEBODM_TASK4_OPTIONS_JSON` for that
invocation; every other Task 4 option remains unchanged. This is local,
sequential split/merge on the selected processing node. It does not configure
or contact ClusterODM/ClusterODX.

The plan prints the selected node, source image count and approximate base
submodel count. The actual count and work per submodel can increase because of
overlap. Split size must be positive and smaller than the selected image count.
These flags are rejected for Task 2 and combined mode. The optional full ODM ZIP
retains its existing export setting and storage estimate.

Review the storage report and independently confirm free scratch space on the
processing node; local pipeline preflight cannot measure the NodeODM host's
scratch volume. Remove `--storage-preflight-only` only after both checks pass.

The effective static Task 4 options are stored in the existing canonical binding
before upload/task creation; the generated boundary GeoJSON is not stored in
`options_json`. A resume before task creation reuses that profile. Explicit
resume flags that disagree with it fail before upload. Once a task UUID exists,
normal resume reattaches to that exact task and never creates a replacement.
Historical bindings without stored options retain their prior behavior.

## Replace one proven-contaminated failed Task 4

Use this recovery only when all of the following are true: the canonical Task 4
is failed, its exact WebODM project contains no other task, the original source
can be recovered, and the failed ODM root log proves a different image count
from the clean local manifest. If that root line is missing, the exact failed
task may instead use a source-hash-verified copy of its NodeODM root
`img_list.txt`. It is not a general retry command.

For run `0d57c35b-0a6e-41a7-84be-2eda3217492b`, the retained filtered set had
3,953 images and `cross-runs` had 335, which exactly reconstructs the 4,288-image
source when cross-run filtering is disabled. The failed NodeODM task admitted
more images than WebODM's recorded 3,953 uploads. WebODM links the failed task
to node 3 and NodeODM UUID `5b03d450-cb52-47fa-aca3-c4be832e15a9`. Its
live NodeODM task API no longer has that UUID, so preserve the retained root
`img_list.txt` instead. On node 3, record the source SHA-256 and copy only
that file from the `nodeodm` container. Place the copy on the pipeline
workstation under a new operator-owned
`<evidence-root>\5b03d450-cb52-47fa-aca3-c4be832e15a9\img_list.txt`
directory; do not overwrite an existing copy. Keep the source hash separately.
If the root file is gone, stop rather than reconstructing it.

The operator can inspect the source without modifying the task:

```powershell
$nodeUuid = "5b03d450-cb52-47fa-aca3-c4be832e15a9"
$nodeFile = "/var/www/data/$nodeUuid/img_list.txt"
docker exec nodeodm sha256sum $nodeFile
docker exec nodeodm wc -l $nodeFile
```

On node 3, replace `D:\operator-evidence\AH-026153` below with a new
operator-owned destination. The UUID subdirectory must not already exist:

```powershell
$evidenceRoot = "D:\operator-evidence\AH-026153"
$evidenceDir = Join-Path $evidenceRoot $nodeUuid
if (Test-Path -LiteralPath $evidenceDir) { throw "Evidence destination already exists" }
New-Item -ItemType Directory -Path $evidenceDir -ErrorAction Stop | Out-Null
docker cp "nodeodm:$nodeFile" (Join-Path $evidenceDir "img_list.txt")
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $evidenceDir "img_list.txt")
```

Transfer that small text file to the pipeline workstation, preserving the UUID
subdirectory. Its copied hash must match the hash printed by `sha256sum` on
node 3. The CLI counts
actual entries rather than relying on `wc -l`, which counts newline characters.
Start with this local-only plan:

```powershell
python main.py `
  --survey "SMF-003_35.8Ha_M3M_50m_85f75s_3mps" `
  --year 2026 `
  --survey-id "AH-026153" `
  --task4 `
  --node-id 3 `
  --task4-split 400 `
  --task4-split-overlap 100 `
  --disable-cross-run `
  --resume `
  --run-id "0d57c35b-0a6e-41a7-84be-2eda3217492b" `
  --force-stage cross_run_filter `
  --force-stage webodm_task4 `
  --recover-missing-raw `
  --raw-recovery-confirmation "RECOVER RAW FOR RUN 0d57c35b-0a6e-41a7-84be-2eda3217492b" `
  --replace-failed-webodm-task task4 `
  --webodm-task-replacement-project-id 563 `
  --webodm-task-replacement-task-id "dbfd2162-e659-45f6-bbc9-e4019575888a" `
  --webodm-task-replacement-confirmation "CREATE REPLACEMENT TASK4 IN WEBODM PROJECT 563 FOR FAILED TASK dbfd2162-e659-45f6-bbc9-e4019575888a FOR RUN 0d57c35b-0a6e-41a7-84be-2eda3217492b" `
  --replacement-nodeodm-image-list "<evidence-root>\5b03d450-cb52-47fa-aca3-c4be832e15a9\img_list.txt" `
  --replacement-nodeodm-image-list-sha256 "<SHA-256 recorded on node 3>" `
  --storage-preflight-only
```

The plan reads only local run state, storage information and the evidence copy.
It verifies the file hash and image entries. It does not open a
writer, contact WebODM, rebuild image directories or upload anything. Confirm
that it reports 4,288 source images, node 3, split 400, overlap 100, approximately
11 base submodels, the NodeODM evidence count and remote checks pending. Remove only
`--storage-preflight-only` to execute after that review.

Execution recovers all 4,288 images from the persisted source, atomically
replaces `images/path` with the full set and `images/cross-runs` with an empty
directory, and therefore uses the corrected `cb-t4` task name. The old 3,953
image upload cache and failed task are retained unchanged. A new cache scoped to
the run and failed UUID is built; an unexpected cache image, duplicate
case-insensitive filename or manifest change stops before upload.

Immediately before creation, the pipeline proves project 563, the exact failed
UUID and name, a one-task project, the persisted split profile and the old ODM
count mismatch. The newest local Task 4 attempt may be `failed`, `paused` or
`requires_recovery`. A paused task is accepted because a detached remote task
can continue after the local pipeline pauses; `requires_recovery` permits a
guarded retry after a local pre-creation safety failure. In every case, both
remote views of the exact task must report `failed`; a running or completed
remote task is rejected. The existing local attempt row is retained unchanged.
For the copied image-list path, the guard also checks WebODM's stored NodeODM
UUID, processing node and 3,953-image upload count, then rechecks the source
hash before it audits the old NodeODM count. ODM writes `img_list.txt` after
photo validation, so its accepted-image count may be lower than the earlier
`Loading N images` count. The list must exceed WebODM's upload count; when
both ODM counts exist, the loading count must be at least the list count.
A mismatch stops before upload.
The old binding is also retained until authorization succeeds, avoiding a
conflict between the old `xcb-t4` UUID and the new `cb-t4` name. Authorization
is appended before upload. The returned replacement UUID is appended
immediately with audited rebind history; the failed UUID remains in earlier rows.

Every newly created Task 1, Task 2 and Task 4 now has a deterministic local
manifest. WebODM's `images_count`, when present, must match it. Once the task is
running, the first root-level ODM `Loading <N> images` line is authoritative;
later submodel counts are ignored. A mismatch, or no root count within 15
minutes of running, requests WebODM cancellation and waits up to two minutes for
confirmation. A failed or uncertain cancellation retains the new UUID and
requires manual recovery.

Never repeat the replacement command after creation starts. The durable
`replacement_creation_started`, replacement UUID or recovery status blocks a
second replacement, so the pipeline cannot automatically create a third task.
Inspect and repair the exact new UUID instead. Normal historical exact-UUID
resume remains compatible because the new count requirement applies only to
tasks created after this guard.

If a disabled cross-run retry previously completed RAW, workspace `path` and
published `path` but failed while removing or renaming a same-run network-share
temporary directory, resume can reuse those outputs. Reuse is limited to this
explicit failed-Task-4 replacement workflow and requires identical deterministic
filename/size manifests across all three image directories, both workspace and
published `cross-runs` directories to be empty, and no same-run backup directory.
RAW may also contain its regular `.raw-recovery.json` ownership marker; other
non-image entries still prevent reuse.
The stage records `reused_existing_outputs=true` and leaves any stuck
`.path.tmp-<run-id>` directory untouched. Any mismatch uses the normal full-copy
and atomic-publication path instead.

### Storage reservation rechecks

The storage preflight and the final pre-work recheck must cover all estimated
writes plus the configured reserve. After work starts, the coordinator keeps
that full estimate as a durable commitment for other admissions, but it does
not charge the same estimate again against free space already consumed by this
run. Each managed compute or transfer boundary still verifies that the physical
reserve remains available. A reserve loss stops with `STORAGE_RESERVE`; a new
or competing admission that cannot cover requested and committed writes stops
with `STORAGE_COMMITTED`.

Normal resume also credits only bytes actually present in the completed run's
owned `images/raw` directory. The report prints that credit. When raw retention
is enabled, the remaining estimate includes both the cross-run mirror and the
optional full ODM ZIP; when successful filtering deletes raw, it models the
lower sequential peak. The credit never comes from an unrelated directory and
does not bypass the reserve or per-write capacity checks.

For a run that failed at a managed-resource boundary before any WebODM project
or task was created, confirm the coordinator has no holder, then use the exact
original mode, node and split flags with `--resume --run-id <run-id>`. Start with
`--storage-preflight-only`. Do not create a replacement run or delete the
completed data-segregation output; normal resume reuses completed stages.

## Processing nodes repeatedly turn offline

When every processing node turns red several minutes after it was manually
clicked online, check the WebODM scheduler rather than repeatedly refreshing the
nodes. From the directory containing WebODM's `docker-compose.yml`:

```powershell
docker compose ps
docker top webapp | Select-String -Pattern "celery|beat"
```

The `webapp` container should have a `celery -A worker beat` process. A PID file
alone is not proof: read it inside the container and verify the corresponding
`/proc/<pid>` directory. If the scheduler is absent and no scheduler process is
alive, start it through WebODM's own helper:

```powershell
docker exec webapp /webodm/worker.sh scheduler start
docker top webapp | Select-String -Pattern "celery|beat"
docker exec webapp cat /webodm/celerybeat.pid
```

WebODM uses this scheduler to refresh processing-node state. Without it, a node
can appear healthy immediately after a manual click and become offline again as
its recorded health grows stale. Recheck after more than five minutes and after
container/host restarts. Do not delete an apparently stale PID until the process
and container state have been verified; the helper may remove or replace it.

## Field recovery record (2026-11-04 to 2026-11-05)

Run `699df6a3-5237-4297-8c9a-83687896aec2` (`AH-026152`) was interrupted with
Ctrl+C after its first invocation omitted `--disable-cross-run`. The coordinator
correctly retained an owned `requires_recovery` record. The operator reviewed and
reconciled the exact stopped operation; no journal, database row or output was
manually deleted.

After the missing boundary archive was supplied, the boundary conversion itself
succeeded. Publication initially stopped at the storage-commitment guard because
another interrupted-operation record still owned the commitment. Exact-operation
reconciliation followed by a fresh storage preflight allowed resume without
replaying already completed work.

In WebODM project 562, Task 4
`8f2de23d-7b83-4fa0-8db9-e04e42428b41` completed with 536 images, while Task 2
had never been created and had no durable UUID. Ordinary resume therefore failed
closed instead of guessing whether a duplicate remote task existed. The operator
visually confirmed that the project contained only the completed Task 4, ran the
plan-only missing-Task-2 recovery, and then supplied the exact authorization.

The first execution attempt printed the authorization summary but was rejected
as `BUSY` before admission by operation
`082cadfab2cd4e0e9f9429f6f174e7bb`. Printing `AUTHORIZED` confirms the recovery
flags and phrase only; it does not mean upload or task creation occurred. Status
showed that exact holder as `requires_recovery`; after confirming its process was
gone and Task 2 was still absent, the operator reconciled that exact record. The
same guarded recovery was then admitted and was running when this record was
written. Task 2 completion and output validation remain pending and must not be
inferred from admission alone.

On 2026-11-06 the operator reported that this run had completed, but the Task 4
clipped orthomosaic still showed the earlier distorted generation after Task 4
had been rerun. The forced WebODM operation exported its task output, while the
already completed quality/QGIS stages were skipped and QGIS had previously
accepted the old clip because it was readable. Source now treats a normal forced WebODM
refresh as invalidating quality and QGIS, and disables clip reuse for that
downstream QGIS attempt. This source change does not mutate the completed run or
its files automatically; the operator must review a fresh storage preflight and
explicitly rerun the exact-task refresh after deploying the patch.

## Download the complete Task 4 ODM ZIP

Task 4 honors the same export controls as Task 2:

```ini
EXPORTS_ENABLED=true
EXPORT_ALL_ASSETS_ZIP=true
ALL_ASSETS_OUT_DIR_KEY=odm
ALL_ASSETS_FILENAME_TEMPLATE={survey_id}-RGB-{flag}-all.zip
```

After a successful Task 4 operation, the pipeline downloads WebODM's
`all.zip` into the run workspace first and then mirrors the completed file to
`<survey>/rgb/odm`. The default filename is
`<survey-id>-RGB-<task4-flag>-all.zip`.

Startup preflight reserves one additional source-image-size estimate on both
the workspace and published-output destinations. The Task 4 download performs
another capacity check immediately before writing, and the existing file
mirror checks the actual completed ZIP size before changing the published
file.

The ZIP is an optional export, matching Task 2 compatibility behavior. A
missing endpoint or failed download logs a warning and does not replace an
existing published ZIP. The required orthomosaic remains authoritative for
Task 4 success.

An ordinary resume skips an already completed `webodm_task4` stage. To
backfill the ZIP from a completed Task 4, use the original run ID with
`--resume --force-stage webodm_task4` only after reviewing storage preflight.
The forced operation reconciles the exact persisted task UUID; it must not
create a replacement task.
