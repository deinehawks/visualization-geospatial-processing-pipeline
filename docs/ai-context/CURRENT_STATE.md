# Current State

## Snapshot

- Snapshot date: 2026-09-02
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
- Storage preflight and durable workspace routing are implemented for the operational branch. An isolated follow-up adds stage-aware resume estimates and an exact-confirmation legacy workspace rebind to D: while retaining old recovery evidence; integration is pending.
- Missing local WebODM UUID repair now appends audit/history and preserves existing repair evidence.

## Known Gaps

- Publication activation runtime and CLI handoff are implemented behind explicit confirmation: default runs without confirmation do not activate publication, and UI/API mutation remains deferred.
- WebODM combined `Orthomosaic + 3D` mode is now wired in CLI/runtime via `--both-tasks` with Task 4 then Task 2 ordering.
- `partially_completed` is mapped for the run, survey, and compatibility WebODM coordinator when Task 4 succeeds and Task 2 fails; dedicated API/UI operation projection remains deferred.
- WebODM/QGIS stages still mirror outputs into legacy paths during stage execution; pipeline is not workspace-only-until-publish.
- Older workspaces without `.run-workspace.json` ownership evidence remain resumable but are retained rather than automatically deleted.
- Default collection and the full hermetic suite are healthy: the isolated split-reserve feature collected and passed 297 tests without exclusions.
- A validated isolated follow-up uses 10 GiB/5% for general bulk storage, 10 GiB/0% for exact published mirrors, persisted surveys-root routing for resume preflight, and non-retryable capacity failures; integration is pending.
- Base storage rollout is complete. Resume-aware integration, at least 10 GiB free on the E: state volume, a report-only check for the audited legacy run, controlled resume, and success verification remain pending. Its old 59.88 GiB workspace must remain until success is proven.
- Full production-scale SMB/open-handle/disconnect validation remains incomplete.

## Working Tree Notes

At the time this context layer was created, unrelated or pre-existing working tree items included `codes.txt`, deleted `address this issues.txt`, and `data/workspaces/`. Do not revert or modify those unless the user explicitly asks.
