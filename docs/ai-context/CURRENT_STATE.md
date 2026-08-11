# Current State

## Snapshot

- Snapshot date: 2026-08-11
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
- Publication artifact allowlist is documented and implemented for current dry-run/staging bridge behavior.
- Shared contracts now describe run state, API, metrics, events, artifacts, compatibility policy, and UI requirements.

## Known Gaps

- Publication activation runtime and CLI handoff are implemented behind explicit confirmation: default runs without confirmation do not activate publication, and UI/API mutation remains deferred.
- WebODM combined `Orthomosaic + 3D` mode is now wired in CLI/runtime via `--both-tasks` with Task 4 then Task 2 ordering.
- `partially_completed` is now mapped in runtime persistence for combined WebODM runs where Task 4 succeeds and Task 2 fails; broader API/UI projection remains to be verified.
- WebODM/QGIS stages still mirror outputs into legacy paths during stage execution; pipeline is not workspace-only-until-publish.
- Default completed-run workspace cleanup and the `--keep-workspace` opt-out are accepted target behavior but not implemented.
- Full production-scale SMB/open-handle/disconnect validation remains incomplete.

## Working Tree Notes

At the time this context layer was created, unrelated or pre-existing working tree items included `codes.txt`, deleted `address this issues.txt`, and `data/workspaces/`. Do not revert or modify those unless the user explicitly asks.
