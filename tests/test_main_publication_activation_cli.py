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
            },
            "webodm": {},
        },
    )
    monkeypatch.setattr(
        rgb_main,
        "resolve_source_dataset_dir",
        lambda source_input, logger, date_hint=None: tmp_path / "resolved-source",
    )
    _install_fake_pipeline(monkeypatch)
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


def test_cli_selects_both_tasks_mode_when_flag_is_present(monkeypatch, tmp_path):
    _prepare_cli(
        monkeypatch,
        tmp_path,
        ["--survey", "AH_026_source", "--both-tasks"],
    )

    rgb_main.main()

    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.init_kwargs["webodm_mode"] == "both"


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

