from __future__ import annotations

import sys
import types
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


@pytest.mark.parametrize(
    "argv, expected_message",
    [
        (
            ["--survey", "AH_026_source", "--activate-publication"],
            "--publication-confirmation is required when using --activate-publication",
        ),
        (
            [
                "--survey",
                "AH_026_source",
                "--publication-confirmation",
                "PUBLISH AH-026019 run-cli-publication",
            ],
            "--publication-confirmation requires --activate-publication",
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

