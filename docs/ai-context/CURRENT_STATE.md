# Current State

## Snapshot

- Snapshot date: 2026-09-07
- Repository state: pipeline refactor in progress; shared API/UI contracts have been added for coordinated pipeline and web-app workstreams.
- Current pipeline phase: Phase 3, run-scoped workspace ownership and publication activation preparation.
- Current implementation posture: guarded publication activation is wired through `RGBPipeline.run()` and an explicit CLI confirmation surface; default runs still do not activate publication.

## Recently Accepted Contract Decisions

- Combined WebODM mode target is `--both-tasks` with operator label `Orthomosaic + 3D`.
- Combined order is fixed: Task 4 first, then Task 2.
- If Task 4 succeeds and Task 2 fails, target run status is `partially_completed`.
- Task 4 output may be eligible for publication immediately after successful Task 4.
- QGIS should run once per successful WebODM operation.
- A single quality gate should run after all selected WebODM tasks complete.
- Task 4 and Task 2 need separate stage records; retrying a failed operation leaves successful outputs untouched by default.
- Publication activation target uses one stage named `activate_publication` with operator label `Activate Publication`, runs automatically after quality-gate approval, keeps CLI exact phrase `PUBLISH <survey_id> <run_id>` for now, maps pre-mutation blocks to `failed`, maps post-mutation failures to `requires_recovery`, and lets existing run-status behavior decide final run status.
- Fully `completed` runs should clean their owned run workspace by default, with `--keep-workspace` as the per-run opt-out. `partially_completed`, failed, paused, aborted, canceled, and recovery-required runs retain workspace evidence by default.

## Current Implementation Highlights

- Default pytest safety infrastructure blocks normal network, subprocess, dotenv, production database, keyboard/input, and protected-path access during tests.
- StageRunner failure classification records non-control runtime failures instead of leaving stages stuck as `running`, and now supports typed `requires_recovery` terminal stage attempts.
- RGBPipeline has injection seams for hermetic construction and fake WebODM use in tests.
- Run workspace and publication helper boundaries exist for planning, staging, explicit staged activation, a tracked explicit `activate_publication` method, guarded `RGBPipeline.run()` activation, CLI confirmation handoff, reconciliation, stale-lock recovery, cleanup planning/execution, and filesystem validation.
- Fully completed full runs now clean their exact owned workspace by default after success persistence, output verification, and cleanup-audit archival; `--keep-workspace` opts out.
- Combined WebODM runs now persist separate `webodm_task4` and `webodm_task2` stage attempts while retaining the aggregate `webodm` compatibility state; resume preserves completed Task 4 and retries failed Task 2.
- Workspace cleanup records relative inventory, bytes, stage summaries, verified output mappings, and available cross-run image classification reasons without deleting legacy survey outputs.
- Publication artifact allowlist is documented and implemented for current dry-run/staging bridge behavior.
- Shared contracts now describe run state, API, metrics, events, artifacts, compatibility policy, and UI requirements.
- Storage preflight, stage-aware resume estimates, split publication reserves, durable D: workspace/cache/QGIS routing, and exact-confirmation legacy workspace rebind are integrated and operationally preflighted.
- Missing local WebODM UUID repair now appends audit/history and preserves existing repair evidence.
- The existing all-assets ZIP export now supports Task 4 workspace-first
  download and legacy mirroring, with additive artifact metadata, publication
  allowlisting, startup estimates, and just-in-time capacity checks.
- Task 4 can explicitly recover a persisted project with no UUID only after exact
  run/project confirmation and a complete WebODM task listing proves the
  project contains zero tasks. Normal resume remains exact-UUID-only.
- DJI M3M ingestion supports independent `--uav <folder>` candidate filtering
  and strict `--rgb` `*_D.JPG` selection across arbitrary nested capture
  splits, with one selector shared by preflight and segregation.

## Known Gaps

- Publication activation runtime and CLI handoff are implemented behind explicit confirmation: default runs without confirmation do not activate publication, and UI/API mutation remains deferred.
- WebODM combined `Orthomosaic + 3D` mode is now wired in CLI/runtime via `--both-tasks` with Task 4 then Task 2 ordering.
- `partially_completed` is mapped for the run, survey, and compatibility WebODM coordinator when Task 4 succeeds and Task 2 fails; dedicated API/UI operation projection remains deferred.
- WebODM/QGIS stages still mirror outputs into legacy paths during stage execution; pipeline is not workspace-only-until-publish.
- Older workspaces without `.run-workspace.json` ownership evidence remain resumable but are retained rather than automatically deleted.
- Default collection and the full hermetic suite are healthy: 342 tests collect
  and pass without exclusions.
- Empty-project recovery was exercised once for the explicitly authorized
  production run/project and returned a durable Task 4 UUID before normal
  remote monitoring continued.
- The retained legacy workspace and old upload cache remain recovery evidence
  until their associated runs succeed and are separately approved for cleanup.
- Full production-scale SMB/open-handle/disconnect validation remains incomplete.
- Legacy run `1cd78d5e-331a-4ee5-b05d-047ca8f658e5` had its accidentally
  deleted filtered-image directory reconstructed under explicit authorization.
  The repair restored exactly 1,153 saved `kept` classifications
  (21,783,982,080 bytes), preserved all 229 verified `cross-runs` images, and
  left SQLite and the exact Task 4 binding unchanged. The run is ready for an
  ordinary resume against project `426` / task
  `8822d2e6-ea73-40d1-8c3f-0098acddc6a9`.
- A reusable guarded missing-image-artifact recovery command is not yet
  implemented. Current `--force-stage data_segregation` wiring does not safely
  restore the persisted survey ID or enable existing-folder recovery, so it
  must not be used as a substitute for that future repair path.

## Working Tree Notes

At the time this context layer was created, unrelated or pre-existing working tree items included `codes.txt`, deleted `address this issues.txt`, and `data/workspaces/`. Do not revert or modify those unless the user explicitly asks.
