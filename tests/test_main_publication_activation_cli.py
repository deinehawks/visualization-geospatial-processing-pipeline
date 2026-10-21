from __future__ import annotations

import sys
import types
from contextlib import nullcontext
from pathlib import Path

import pytest

import main as rgb_main


class FakeRGBPipeline:
    instances = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.run_kwargs = None
        FakeRGBPipeline.instances.append(self)

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return {"success": True, "run_id": "run-cli-publication"}


def _install_fake_pipeline(monkeypatch):
    FakeRGBPipeline.instances = []
    module = types.ModuleType("pipelines.rgb_pipeline")
    module.RGBPipeline = FakeRGBPipeline
    monkeypatch.setitem(sys.modules, "pipelines.rgb_pipeline", module)
    return FakeRGBPipeline


def _prepare_cli(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(rgb_main, 'execution_guard_factory', lambda *_: nullcontext())
    monkeypatch.setattr(
        rgb_main,
        "load_pipeline_config",
        lambda: {
            "paths": {
                "field_data_root": str(tmp_path / "field-data"),
                "surveys_root": str(tmp_path / "surveys"),
                "upload_cache_root": str(tmp_path / "cache"),
                "workspace_root": str(tmp_path / "workspaces"),
            },
            "storage": {
                "min_free_gb": 10,
                "min_free_percent": 5,
                "published_min_free_gb": 10,
                "published_min_free_percent": 0,
            },
            "qgis": {
                "local_staging": {
                    "enabled": True,
                    "dir": str(tmp_path / "qgis-staging"),
                }
            },
            "exports": {
                "enabled": True,
                "all_assets_zip": {"enabled": True},
            },
            "webodm": {},
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "resolve_source_dataset_dir",
        lambda source_input, logger, date_hint=None, uav_folder=None: (
            tmp_path / "resolved-source"
        ),
    )
    _install_fake_pipeline(monkeypatch)
    monkeypatch.setattr(
        rgb_main,
        "build_storage_preflight",
        lambda **kwargs: {"ok": True, "volumes": [], **kwargs},
    )
    monkeypatch.setattr(
        rgb_main,
        "format_storage_report",
        lambda _report: "STORAGE PREFLIGHT PASS",
    )
    monkeypatch.setattr(sys, "argv", ["main.py", *argv])


def test_cli_leaves_publication_activation_disabled_by_default(monkeypatch, tmp_path):
    _prepare_cli(monkeypatch, tmp_path, ["--survey", "AH_026_source"])

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs["publication_confirmation"] is None
    assert pipeline.run_kwargs["keep_workspace"] is False
    assert pipeline.init_kwargs["crossrun_enabled_override"] is None


def test_cli_structured_preflight_adapter_never_acquires_execution_guard(monkeypatch, tmp_path):
    _prepare_cli(monkeypatch, tmp_path, ['--survey','source','--storage-preflight-only'])
    guard = lambda *_: pytest.fail('Report-only execution must not acquire a run lease')
    reports = []
    result = rgb_main.main(observer=reports.append, guard_factory=guard)
    assert reports == [result]
    assert result['mode'] == 'task4' and result['report']['ok']
    assert result['config_fingerprint'] and FakeRGBPipeline.instances == []


def test_cli_guard_prevents_pipeline_construction(monkeypatch, tmp_path):
    _prepare_cli(monkeypatch, tmp_path, ['--survey','source'])
    from contextlib import contextmanager
    @contextmanager
    def reject(*_):
        raise RuntimeError('another execution owns the slot')
        yield
    with pytest.raises(RuntimeError, match='owns the slot'):
        rgb_main.main(guard_factory=reject)
    assert FakeRGBPipeline.instances == []


def test_cli_passes_keep_workspace_opt_out(monkeypatch, tmp_path):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", "--keep-workspace"],
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs["keep_workspace"] is True


def test_cli_disables_cross_run_when_flag_is_present(monkeypatch, tmp_path):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", "--disable-cross-run"],
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.init_kwargs["crossrun_enabled_override"] is False


def test_cli_passes_independent_uav_and_rgb_selection(monkeypatch, tmp_path):
    resolver_calls = []
    preflight_calls = []
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            "BCO-121_11Ha_M3M_70m_85f75s_5mps",
            "--uav",
            "M3M_A",
            "--rgb",
        ],
    )

    def resolve_source(source_input, logger, date_hint=None, uav_folder=None):
        resolver_calls.append((source_input, date_hint, uav_folder))
        return tmp_path / "resolved-source"

    def build_preflight(**kwargs):
        preflight_calls.append(kwargs)
        return {"ok": True, "volumes": []}

    monkeypatch.setattr(rgb_main, "resolve_source_dataset_dir", resolve_source)
    monkeypatch.setattr(rgb_main, "build_storage_preflight", build_preflight)

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert resolver_calls == [
        (
            Path("BCO-121_11Ha_M3M_70m_85f75s_5mps"),
            None,
            "M3M_A",
        )
    ]
    assert preflight_calls[0]["rgb_only"] is True
    assert pipeline.init_kwargs["rgb_only"] is True


def test_cli_selects_both_tasks_mode_when_flag_is_present(monkeypatch, tmp_path):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", "--both-tasks"],
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.init_kwargs["webodm_mode"] == "both"


def test_cli_uses_configured_workspace_and_allocates_run_id(monkeypatch, tmp_path):
    _prepare_cli(monkeypatch, tmp_path, ["--survey", "AH_026_source"])

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.init_kwargs["workspace_root"] == tmp_path / "workspaces"
    assert pipeline.init_kwargs["run_id"]


def test_storage_preflight_only_does_not_construct_pipeline(monkeypatch, tmp_path):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", "--storage-preflight-only"],
    )

    rgb_main.main()

    assert FakeRGBPipeline.instances == []


def test_cli_passes_explicit_qgis_staging_root_to_preflight(monkeypatch, tmp_path):
    captured = {}
    _prepare_cli(monkeypatch, tmp_path, ["--survey", "AH_026_source"])

    def capture_preflight(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "volumes": []}

    monkeypatch.setattr(rgb_main, "build_storage_preflight", capture_preflight)

    rgb_main.main()

    assert captured["qgis_staging_root"] == tmp_path / "qgis-staging"
    assert captured["include_task4_all_assets_zip"] is True


def test_cli_does_not_add_task4_zip_estimate_for_task2_only(
    monkeypatch,
    tmp_path,
):
    captured = {}
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", "--task2"],
    )

    def capture_preflight(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "volumes": []}

    monkeypatch.setattr(rgb_main, "build_storage_preflight", capture_preflight)

    rgb_main.main()

    assert captured["webodm_mode"] == "task2"
    assert captured["include_task4_all_assets_zip"] is False


def test_resume_preflight_rebinds_workspace_and_skips_completed_webodm_cache(
    monkeypatch, tmp_path
):
    run_id = "legacy-run"
    captured = {}
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            "AH_026_source",
            "--resume",
            "--run-id",
            run_id,
            "--rebind-workspace-to-configured-root",
            "--workspace-rebind-confirmation",
            f"REBIND WORKSPACE {run_id}",
            "--storage-preflight-only",
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda _database, _run_id: {
            "run_id": run_id,
            "source_dir": str(tmp_path / "source"),
            "workspace_root": None,
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda _database, _run_id: {
            "data_segregation": "completed",
            "cross_run_filter": "completed",
            "kml_boundary": "completed",
            "webodm": "completed",
            "quality_gate": "completed",
            "qgis": "failed",
        },
    )

    def capture_preflight(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "volumes": []}

    monkeypatch.setattr(rgb_main, "build_storage_preflight", capture_preflight)

    rgb_main.main()

    assert captured["workspace_root"] == tmp_path / "workspaces"
    assert captured["include_upload_cache"] is False
    assert captured["include_qgis_staging"] is True
    assert captured["include_workspace"] is True
    assert captured["include_published_outputs"] is True
    assert captured["published_min_free_gb"] == 10
    assert captured["published_min_free_percent"] == 0
    assert FakeRGBPipeline.instances == []


def test_failed_storage_preflight_exits_before_pipeline_construction(
    monkeypatch, tmp_path
):
    _prepare_cli(monkeypatch, tmp_path, ["--survey", "AH_026_source"])
    monkeypatch.setattr(
        rgb_main,
        "build_storage_preflight",
        lambda **_kwargs: {"ok": False, "volumes": []},
    )

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 3
    assert FakeRGBPipeline.instances == []


def test_cli_passes_publication_confirmation_when_activation_is_explicit(
    monkeypatch, tmp_path
):
    confirmation = "PUBLISH AH-026019 run-cli-publication"
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            "AH_026_source",
            "--activate-publication",
            "--publication-confirmation",
            confirmation,
        ],
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs["publication_confirmation"] == confirmation


def test_cli_passes_exact_empty_webodm_project_recovery(
    monkeypatch,
    tmp_path,
):
    run_id = "run-empty-project"
    confirmation = (
        f"CREATE TASK4 IN EMPTY WEBODM PROJECT 429 FOR RUN {run_id}"
    )
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            "AH_026_source",
            "--resume",
            "--run-id",
            run_id,
            "--force-stage",
            "webodm_task4",
            "--recover-empty-webodm-project",
            "task4",
            "--webodm-recovery-project-id",
            "429",
            "--webodm-recovery-confirmation",
            confirmation,
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda _database, _run_id: {
            "run_id": run_id,
            "source_dir": str(tmp_path / "source"),
            "workspace_root": str(tmp_path / "workspaces"),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda _database, _run_id: {"webodm": "requires_recovery"},
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs["empty_webodm_recovery"] == {
        "operation": "task4",
        "project_id": 429,
        "confirmation": confirmation,
    }


@pytest.mark.parametrize(
    "extra_args, expected_message",
    [
        (
            ["--recover-empty-webodm-project", "task4"],
            "--recover-empty-webodm-project requires --resume",
        ),
        (
            [
                "--resume",
                "--run-id",
                "recovery-run",
                "--recover-empty-webodm-project",
                "task4",
            ],
            "--webodm-recovery-project-id is required",
        ),
        (
            [
                "--resume",
                "--run-id",
                "recovery-run",
                "--recover-empty-webodm-project",
                "task4",
                "--webodm-recovery-project-id",
                "429",
            ],
            "--force-stage webodm_task4",
        ),
        (
            [
                "--resume",
                "--run-id",
                "recovery-run",
                "--force-stage",
                "webodm_task4",
                "--recover-empty-webodm-project",
                "task4",
                "--webodm-recovery-project-id",
                "429",
                "--webodm-recovery-confirmation",
                "wrong",
            ],
            "--webodm-recovery-confirmation must exactly match",
        ),
        (
            ["--webodm-recovery-project-id", "429"],
            "require --recover-empty-webodm-project",
        ),
    ],
)
def test_cli_rejects_invalid_empty_webodm_project_recovery(
    monkeypatch,
    tmp_path,
    capsys,
    extra_args,
    expected_message,
):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", *extra_args],
    )

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert expected_message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


def test_cli_passes_exact_missing_webodm_project_recovery(
    monkeypatch,
    tmp_path,
):
    run_id = "run-missing-project"
    confirmation = (
        "CREATE REPLACEMENT WEBODM PROJECT FOR MISSING PROJECT "
        f"370 FOR RUN {run_id}"
    )
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey", "AH_026_source",
            "--resume", "--run-id", run_id,
            "--force-stage", "webodm_task4",
            "--recover-missing-webodm-project", "task4",
            "--missing-webodm-project-id", "370",
            "--missing-webodm-recovery-confirmation", confirmation,
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda _database, _run_id: {
            "run_id": run_id,
            "source_dir": str(tmp_path / "source"),
            "workspace_root": str(tmp_path / "workspaces"),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda _database, _run_id: {"webodm": "requires_recovery"},
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs["missing_webodm_project_recovery"] == {
        "operation": "task4",
        "project_id": 370,
        "confirmation": confirmation,
    }


def test_cli_passes_exact_missing_task2_recovery(monkeypatch, tmp_path):
    run_id = 'run-missing-task2'
    confirmation = f'CREATE TASK2 IN WEBODM PROJECT 540 FOR RUN {run_id}'
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            '--survey', 'AH_026_source',
            '--resume', '--run-id', run_id,
            '--both-tasks',
            '--force-stage', 'webodm_task2',
            '--recover-missing-webodm-task', 'task2',
            '--webodm-task-recovery-project-id', '540',
            '--webodm-task-recovery-confirmation', confirmation,
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        'read_run_state_read_only',
        lambda _database, _run_id: {
            'run_id': run_id,
            'source_dir': str(tmp_path / 'source'),
            'workspace_root': str(tmp_path / 'workspaces'),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        'read_latest_stage_statuses_read_only',
        lambda _database, _run_id: {
            'webodm': 'requires_recovery',
            'webodm_task4': 'completed',
            'webodm_task2': 'requires_recovery',
        },
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.init_kwargs['webodm_mode'] == 'both'
    assert pipeline.run_kwargs['force_stages'] == {'webodm_task2'}
    assert pipeline.run_kwargs['missing_webodm_task_recovery'] == {
        'operation': 'task2',
        'project_id': 540,
        'confirmation': confirmation,
    }


def test_missing_task2_recovery_preflight_is_plan_only(
    monkeypatch,
    tmp_path,
    capsys,
):
    run_id = 'run-missing-task2-plan'
    confirmation = f'CREATE TASK2 IN WEBODM PROJECT 540 FOR RUN {run_id}'
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            '--survey', 'AH_026_source',
            '--resume', '--run-id', run_id,
            '--both-tasks',
            '--force-stage', 'webodm_task2',
            '--recover-missing-webodm-task', 'task2',
            '--webodm-task-recovery-project-id', '540',
            '--webodm-task-recovery-confirmation', confirmation,
            '--storage-preflight-only',
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        'read_run_state_read_only',
        lambda _database, _run_id: {
            'run_id': run_id,
            'source_dir': str(tmp_path / 'source'),
            'workspace_root': str(tmp_path / 'workspaces'),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        'read_latest_stage_statuses_read_only',
        lambda _database, _run_id: {
            'webodm': 'requires_recovery',
            'webodm_task4': 'completed',
            'webodm_task2': 'requires_recovery',
        },
    )

    rgb_main.main()

    assert FakeRGBPipeline.instances == []
    output = capsys.readouterr().out
    assert 'Missing WebODM task recovery: task2 project 540' in output
    assert 'PLAN ONLY - WebODM and database unchanged' in output


@pytest.mark.parametrize(
    'extra_args, expected_message',
    [
        (
            ['--recover-missing-webodm-task', 'task2'],
            '--recover-missing-webodm-task requires --resume',
        ),
        (
            [
                '--resume', '--run-id', 'recovery-run',
                '--recover-missing-webodm-task', 'task2',
            ],
            'requires --both-tasks',
        ),
        (
            [
                '--resume', '--run-id', 'recovery-run', '--both-tasks',
                '--recover-missing-webodm-task', 'task2',
            ],
            '--force-stage webodm_task2',
        ),
        (
            [
                '--resume', '--run-id', 'recovery-run', '--both-tasks',
                '--force-stage', 'webodm_task2',
                '--recover-missing-webodm-task', 'task2',
                '--webodm-task-recovery-project-id', '540',
                '--webodm-task-recovery-confirmation', 'wrong',
            ],
            '--webodm-task-recovery-confirmation must exactly match',
        ),
    ],
)
def test_cli_rejects_invalid_missing_task2_recovery(
    monkeypatch,
    tmp_path,
    capsys,
    extra_args,
    expected_message,
):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ['--survey', 'AH_026_source', *extra_args],
    )

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert expected_message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


def test_missing_webodm_project_preflight_is_plan_only(
    monkeypatch,
    tmp_path,
    capsys,
):
    run_id = "run-missing-project-plan"
    confirmation = (
        "CREATE REPLACEMENT WEBODM PROJECT FOR MISSING PROJECT "
        f"370 FOR RUN {run_id}"
    )
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey", "AH_026_source",
            "--resume", "--run-id", run_id,
            "--force-stage", "webodm_task4",
            "--recover-missing-webodm-project", "task4",
            "--missing-webodm-project-id", "370",
            "--missing-webodm-recovery-confirmation", confirmation,
            "--storage-preflight-only",
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda _database, _run_id: {
            "run_id": run_id,
            "source_dir": str(tmp_path / "source"),
            "workspace_root": str(tmp_path / "workspaces"),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda _database, _run_id: {"webodm": "requires_recovery"},
    )

    rgb_main.main()

    assert FakeRGBPipeline.instances == []
    output = capsys.readouterr().out
    assert "Missing WebODM project recovery" in output
    assert "PLAN ONLY - WebODM and database unchanged" in output


@pytest.mark.parametrize(
    "extra_args, expected_message",
    [
        (
            ["--recover-missing-webodm-project", "task4"],
            "--recover-missing-webodm-project requires --resume",
        ),
        (
            [
                "--resume", "--run-id", "recovery-run",
                "--recover-missing-webodm-project", "task4",
            ],
            "--missing-webodm-project-id is required",
        ),
        (
            [
                "--resume", "--run-id", "recovery-run",
                "--recover-missing-webodm-project", "task4",
                "--missing-webodm-project-id", "370",
            ],
            "--force-stage webodm_task4",
        ),
        (
            [
                "--resume", "--run-id", "recovery-run",
                "--force-stage", "webodm_task4",
                "--recover-missing-webodm-project", "task4",
                "--missing-webodm-project-id", "370",
                "--missing-webodm-recovery-confirmation", "wrong",
            ],
            "--missing-webodm-recovery-confirmation must exactly match",
        ),
        (
            ["--missing-webodm-project-id", "370"],
            "require --recover-missing-webodm-project",
        ),
        (
            [
                "--resume", "--run-id", "recovery-run",
                "--task2",
                "--force-stage", "webodm_task4",
                "--recover-missing-webodm-project", "task4",
                "--missing-webodm-project-id", "370",
                "--missing-webodm-recovery-confirmation",
                (
                    "CREATE REPLACEMENT WEBODM PROJECT FOR MISSING PROJECT "
                    "370 FOR RUN recovery-run"
                ),
            ],
            "requires Task 4-only mode",
        ),
    ],
)
def test_cli_rejects_invalid_missing_webodm_project_recovery(
    monkeypatch,
    tmp_path,
    capsys,
    extra_args,
    expected_message,
):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", *extra_args],
    )

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert expected_message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


def test_cli_rejects_combined_empty_and_missing_project_recovery(
    monkeypatch,
    tmp_path,
    capsys,
):
    run_id = "combined-recovery-run"
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey", "AH_026_source",
            "--resume", "--run-id", run_id,
            "--force-stage", "webodm_task4",
            "--recover-empty-webodm-project", "task4",
            "--webodm-recovery-project-id", "370",
            "--webodm-recovery-confirmation",
            f"CREATE TASK4 IN EMPTY WEBODM PROJECT 370 FOR RUN {run_id}",
            "--recover-missing-webodm-project", "task4",
            "--missing-webodm-project-id", "370",
            "--missing-webodm-recovery-confirmation",
            (
                "CREATE REPLACEMENT WEBODM PROJECT FOR MISSING PROJECT "
                f"370 FOR RUN {run_id}"
            ),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


def test_cli_passes_guarded_qgis_tile_rebuild(monkeypatch, tmp_path):
    run_id = 'qgis-rebuild-run'
    phrase = f'REBUILD QGIS TILES task4 FOR RUN {run_id}'
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            '--survey', 'AH_026_source',
            '--resume', '--run-id', run_id,
            '--rebuild-qgis-tiles', 'task4',
            '--qgis-tile-rebuild-confirmation', phrase,
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        'read_run_state_read_only',
        lambda *_args: {
            'run_id': run_id,
            'source_dir': str(tmp_path / 'source'),
            'workspace_root': str(tmp_path / 'workspaces'),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        'read_latest_stage_statuses_read_only',
        lambda *_args: {'qgis': 'failed'},
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs['qgis_tile_rebuild'] == {
        'task_key': 'task4',
        'confirmation': phrase,
    }


def test_qgis_rebuild_preflight_reports_without_pipeline(
    monkeypatch, tmp_path, capsys
):
    run_id = 'qgis-rebuild-plan'
    phrase = f'REBUILD QGIS TILES task4 FOR RUN {run_id}'
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            '--survey', 'AH_026_source',
            '--resume', '--run-id', run_id,
            '--rebuild-qgis-tiles', 'task4',
            '--qgis-tile-rebuild-confirmation', phrase,
            '--storage-preflight-only',
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        'read_run_state_read_only',
        lambda *_args: {
            'source_dir': str(tmp_path / 'source'),
            'workspace_root': str(tmp_path / 'workspaces'),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        'read_latest_stage_statuses_read_only',
        lambda *_args: {'qgis': 'failed'},
    )

    rgb_main.main()

    assert FakeRGBPipeline.instances == []
    assert 'PLAN ONLY - no files moved' in capsys.readouterr().out


@pytest.mark.parametrize(
    'extra_args, message',
    [
        (
            ['--rebuild-qgis-tiles', 'task4'],
            '--rebuild-qgis-tiles requires --resume',
        ),
        (
            ['--qgis-tile-rebuild-confirmation', 'wrong'],
            'requires --rebuild-qgis-tiles',
        ),
    ],
)
def test_cli_rejects_incomplete_qgis_rebuild_flags(
    monkeypatch, tmp_path, capsys, extra_args, message
):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ['--survey', 'AH_026_source', *extra_args],
    )
    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()
    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_rejects_qgis_rebuild_for_completed_stage(
    monkeypatch, tmp_path, capsys
):
    run_id = 'qgis-completed-run'
    phrase = f'REBUILD QGIS TILES task4 FOR RUN {run_id}'
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            '--survey', 'AH_026_source',
            '--resume', '--run-id', run_id,
            '--rebuild-qgis-tiles', 'task4',
            '--qgis-tile-rebuild-confirmation', phrase,
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        'read_run_state_read_only',
        lambda *_args: {'source_dir': str(tmp_path / 'source')},
    )
    monkeypatch.setattr(
        rgb_main,
        'read_latest_stage_statuses_read_only',
        lambda *_args: {'qgis': 'completed'},
    )
    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()
    assert exc_info.value.code == 2
    assert 'failed or incomplete QGIS stage' in capsys.readouterr().err


@pytest.mark.parametrize(
    'argv, expected_message',
    [
        (
            ['--survey', 'AH_026_source', '--activate-publication'],
            '--publication-confirmation is required when using --activate-publication',
        ),
        (
            [
                '--survey',
                'AH_026_source',
                '--publication-confirmation',
                'PUBLISH AH-026019 run-cli-publication',
            ],
            '--publication-confirmation requires --activate-publication',
        ),
    ],
)
def test_cli_rejects_half_enabled_publication_activation(
    monkeypatch, tmp_path, capsys, argv, expected_message
):
    _prepare_cli(monkeypatch, tmp_path, argv)

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert expected_message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


@pytest.mark.parametrize(
    "argv, expected_message",
    [
        (
            ["--survey", "AH_026_source", "--rebind-workspace-to-configured-root"],
            "--rebind-workspace-to-configured-root requires --resume",
        ),
        (
            [
                "--survey",
                "AH_026_source",
                "--workspace-rebind-confirmation",
                "REBIND WORKSPACE legacy-run",
            ],
            "--workspace-rebind-confirmation requires",
        ),
    ],
)
def test_cli_rejects_half_enabled_workspace_rebind(
    monkeypatch, tmp_path, capsys, argv, expected_message
):
    _prepare_cli(monkeypatch, tmp_path, argv)

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert expected_message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


@pytest.mark.parametrize(
    "argv, expected_message",
    [
        (
            ["--survey", "dataset", "--rebind-source-dir", "new-source"],
            "--rebind-source-dir requires --resume",
        ),
        (
            [
                "--survey",
                "dataset",
                "--source-rebind-confirmation",
                "REBIND SOURCE run-1",
            ],
            "--source-rebind-confirmation requires --rebind-source-dir",
        ),
        (
            [
                "--survey",
                "dataset",
                "--resume",
                "--run-id",
                "run-1",
                "--rebind-source-dir",
                "new-source",
                "--source-rebind-confirmation",
                "wrong",
            ],
            "--source-rebind-confirmation must exactly match",
        ),
    ],
)
def test_cli_rejects_unsafe_source_rebind_combinations(
    monkeypatch, tmp_path, capsys, argv, expected_message
):
    _prepare_cli(monkeypatch, tmp_path, argv)

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert expected_message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


def test_source_rebind_preflight_only_does_not_update_database(
    monkeypatch, tmp_path
):
    run_id = "run-source-rebind-plan"
    old_source = tmp_path / "old-root" / "ABC_001_same_dataset"
    new_source = tmp_path / "new-root" / old_source.name
    new_source.mkdir(parents=True)
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            old_source.name,
            "--resume",
            "--run-id",
            run_id,
            "--rebind-source-dir",
            str(new_source),
            "--source-rebind-confirmation",
            f"REBIND SOURCE {run_id}",
            "--storage-preflight-only",
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda _database, _run_id: {
            "run_id": run_id,
            "source_dir": str(old_source),
            "workspace_root": str(tmp_path / "workspaces"),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda _database, _run_id: {},
    )

    class UnexpectedRepository:
        def __init__(self, _database):
            pytest.fail("preflight-only source rebind must not open a writer")

    monkeypatch.setattr(rgb_main, "PipelineRepo", UnexpectedRepository)

    rgb_main.main()

    assert FakeRGBPipeline.instances == []


def test_source_rebind_persists_before_pipeline_construction(
    monkeypatch, tmp_path
):
    run_id = "run-source-rebind-apply"
    old_source = tmp_path / "old-root" / "ABC_001_same_dataset"
    new_source = tmp_path / "new-root" / old_source.name
    new_source.mkdir(parents=True)
    calls = []
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            old_source.name,
            "--resume",
            "--run-id",
            run_id,
            "--rebind-source-dir",
            str(new_source),
            "--source-rebind-confirmation",
            f"REBIND SOURCE {run_id}",
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda _database, _run_id: {
            "run_id": run_id,
            "source_dir": str(old_source),
            "workspace_root": str(tmp_path / "workspaces"),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda _database, _run_id: {},
    )

    class RecordingRepository:
        def __init__(self, database):
            self.database = database

        def rebind_run_source_dir(
            self,
            requested_run_id,
            *,
            expected_source_dir,
            new_source_dir,
        ):
            calls.append(
                {
                    "run_id": requested_run_id,
                    "expected": expected_source_dir,
                    "new": new_source_dir,
                }
            )
            return {"changed": True}

    monkeypatch.setattr(rgb_main, "PipelineRepo", RecordingRepository)

    rgb_main.main()

    assert calls == [
        {
            "run_id": run_id,
            "expected": str(old_source),
            "new": str(new_source.resolve(strict=True)),
        }
    ]
    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.init_kwargs["source_dir"] == new_source.resolve(strict=True)


def test_cli_passes_guarded_raw_input_recovery(monkeypatch, tmp_path):
    run_id = "run-raw-recovery"
    confirmation = f"RECOVER RAW FOR RUN {run_id}"
    captured = {}
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            "AH_026_source",
            "--resume",
            "--run-id",
            run_id,
            "--force-stage",
            "cross_run_filter",
            "--recover-missing-raw",
            "--raw-recovery-confirmation",
            confirmation,
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda *_args: {
            "run_id": run_id,
            "source_dir": str(tmp_path / "source"),
            "workspace_root": str(tmp_path / "workspaces"),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda *_args: {
            "data_segregation": "completed",
            "cross_run_filter": "completed",
        },
    )

    def capture_preflight(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "volumes": []}

    monkeypatch.setattr(rgb_main, "build_storage_preflight", capture_preflight)

    rgb_main.main()

    assert captured["include_raw_input_recovery"] is True
    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs["force_stages"] == {"cross_run_filter"}
    assert pipeline.run_kwargs["raw_input_recovery"] == {
        "confirmation": confirmation,
    }


def test_raw_recovery_preflight_only_does_not_construct_pipeline(
    monkeypatch,
    tmp_path,
    capsys,
):
    run_id = "run-raw-recovery-plan"
    confirmation = f"RECOVER RAW FOR RUN {run_id}"
    _prepare_cli(
        monkeypatch,
        tmp_path,
        [
            "--survey",
            "AH_026_source",
            "--resume",
            "--run-id",
            run_id,
            "--force-stage",
            "cross_run_filter",
            "--recover-missing-raw",
            "--raw-recovery-confirmation",
            confirmation,
            "--storage-preflight-only",
        ],
    )
    monkeypatch.setattr(
        rgb_main,
        "read_run_state_read_only",
        lambda *_args: {
            "source_dir": str(tmp_path / "source"),
            "workspace_root": str(tmp_path / "workspaces"),
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "read_latest_stage_statuses_read_only",
        lambda *_args: {"data_segregation": "completed"},
    )

    rgb_main.main()

    assert FakeRGBPipeline.instances == []
    assert "PLAN ONLY - no files copied" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra_args, expected_message",
    [
        (
            ["--recover-missing-raw"],
            "--recover-missing-raw requires --resume",
        ),
        (
            ["--raw-recovery-confirmation", "wrong"],
            "requires --recover-missing-raw",
        ),
        (
            [
                "--resume",
                "--run-id",
                "run-raw",
                "--recover-missing-raw",
            ],
            "--force-stage cross_run_filter",
        ),
        (
            [
                "--resume",
                "--run-id",
                "run-raw",
                "--force-stage",
                "cross_run_filter",
                "--recover-missing-raw",
                "--raw-recovery-confirmation",
                "wrong",
            ],
            "--raw-recovery-confirmation must exactly match",
        ),
        (
            [
                "--resume",
                "--run-id",
                "run-raw",
                "--force-stage",
                "cross_run_filter",
                "--recover-missing-raw",
                "--raw-recovery-confirmation",
                "RECOVER RAW FOR RUN run-raw",
                "--disable-cross-run",
            ],
            "cannot be combined with --disable-cross-run",
        ),
    ],
)
def test_cli_rejects_unsafe_raw_recovery_combinations(
    monkeypatch,
    tmp_path,
    capsys,
    extra_args,
    expected_message,
):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", *extra_args],
    )

    with pytest.raises(SystemExit) as exc_info:
        rgb_main.main()

    assert exc_info.value.code == 2
    assert expected_message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []

