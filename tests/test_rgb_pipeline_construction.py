import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest


if importlib.util.find_spec("exifread") is None:
    exifread_stub = types.ModuleType("exifread")

    def reject_exif_processing(*args, **kwargs):
        pytest.fail("constructor test attempted EXIF processing")

    exifread_stub.process_file = reject_exif_processing
    sys.modules["exifread"] = exifread_stub


from pipelines import rgb_pipeline as rgb_module
from pipelines.rgb_pipeline import RGBPipeline
from shared.db.repo import PipelineRepo
from shared.artifacts import plan_published_survey_from_rgb_path, plan_run_workspace
from tests.fakes import FakeWebODM


LOGGER_KEYS = (
    "pipeline",
    "segregation",
    "cross_run_filter",
    "kml",
    "webodm",
    "qgis",
)


def explicit_config(temporary_path_layout):
    return {
        "paths": {
            "surveys_root": temporary_path_layout.surveys_dir,
            "field_data_root": temporary_path_layout.field_data_dir,
            "upload_cache_root": temporary_path_layout.upload_cache_dir,
        },
        "webodm": {
            "url": "https://webodm.invalid",
            "username": "test-user",
            "password": "test-password",
        },
    }


def isolated_loggers(prefix):
    return {
        key: logging.Logger(f"{prefix}.{key}")
        for key in LOGGER_KEYS
    }


def assert_within(path, root):
    resolved_path = Path(path).resolve()
    resolved_root = Path(root).resolve()
    assert resolved_path == resolved_root or resolved_root in resolved_path.parents


def fail_if_called(action):
    def fail(*args, **kwargs):
        pytest.fail(f"construction attempted to {action}")

    return fail


def build_pipeline(
    temporary_path_layout,
    sample_dataset_dir,
    *,
    run_id,
    repository=None,
    db_file=None,
    loggers=None,
    webodm_processor=None,
    workspace_root=None,
    workspace_layout=None,
    published_layout=None,
):
    return RGBPipeline(
        temporary_path_layout.application_root,
        explicit_config(temporary_path_layout),
        source_dir=sample_dataset_dir,
        surveys_root=temporary_path_layout.surveys_dir,
        year=2026,
        run_id=run_id,
        repository=repository,
        db_file=db_file,
        loggers=loggers,
        logs_dir=temporary_path_layout.logs_dir,
        checkpoint_dir=temporary_path_layout.checkpoint_dir,
        webodm_processor=webodm_processor,
        workspace_root=workspace_root,
        workspace_layout=workspace_layout,
        published_layout=published_layout,
    )


def test_rgb_pipeline_constructs_with_only_test_owned_dependencies(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    monkeypatch.setattr(
        rgb_module.StageRunner,
        "run",
        fail_if_called("execute a pipeline stage"),
    )
    monkeypatch.setattr(
        rgb_module.PipelinePreflight,
        "check_stage",
        fail_if_called("execute a preflight check"),
    )

    repository = PipelineRepo(temporary_path_layout.database_path)
    loggers = isolated_loggers("tests.rgb_pipeline.safe")
    fake_webodm = FakeWebODM()

    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        run_id="hermetic-construction",
        repository=repository,
        loggers=loggers,
        webodm_processor=fake_webodm,
    )

    assert pipeline.repo is repository
    assert pipeline.loggers is loggers
    assert pipeline.webodm_processor is fake_webodm
    assert pipeline._create_webodm_processor(loggers["webodm"]) is fake_webodm
    assert fake_webodm.calls == []
    assert pipeline.runner.run_id == "hermetic-construction"
    assert pipeline.state == {"run_id": "hermetic-construction"}
    assert pipeline.rgb_path is None
    assert pipeline.published_layout is None
    assert pipeline.workspace_layout.root == (
        temporary_path_layout.application_root
        / "data"
        / "workspaces"
        / "hermetic-construction"
    )

    owned_paths = (
        pipeline.base_dir,
        pipeline.source_dir,
        pipeline.surveys_root,
        pipeline.workspace_root,
        pipeline.workspace_layout.root,
        pipeline.workspace_layout.images_raw,
        pipeline.workspace_layout.publish,
        pipeline.logs_dir,
        pipeline.checkpoint_dir,
        pipeline._webodm_checkpoint_path(),
        pipeline.control.data_dir,
        pipeline.control.pause_flag,
        pipeline.control.abort_flag,
        repository.db_file,
        pipeline.config["paths"]["field_data_root"],
        pipeline.config["paths"]["upload_cache_root"],
    )
    for path in owned_paths:
        assert_within(path, temporary_path_layout.application_root)

    run = repository.get_run("hermetic-construction")
    assert run["status"] == "running"
    assert run["source_dir"] == str(sample_dataset_dir)
    assert run["surveys_root"] == str(temporary_path_layout.surveys_dir)
    assert run["year"] == 2026
    assert temporary_path_layout.database_path.is_file()
    assert list(temporary_path_layout.logs_dir.iterdir()) == []
    assert list(temporary_path_layout.checkpoint_dir.iterdir()) == []
    assert not pipeline.workspace_layout.root.exists()


def test_rgb_pipeline_preserves_injected_dependencies_and_database_path(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    loggers = isolated_loggers("tests.rgb_pipeline.injected")
    fake_webodm = FakeWebODM()

    injected = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        run_id="injected-dependencies",
        repository=repository,
        loggers=loggers,
        webodm_processor=fake_webodm,
    )

    assert injected.repo is repository
    assert injected.loggers is loggers
    assert all(injected.loggers[key] is loggers[key] for key in LOGGER_KEYS)
    assert injected.webodm_processor is fake_webodm
    assert injected.logs_dir == temporary_path_layout.logs_dir
    assert injected.checkpoint_dir == temporary_path_layout.checkpoint_dir

    explicit_database_path = (
        temporary_path_layout.data_dir / "explicit-construction.db"
    )
    database_injected = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        run_id="injected-database-path",
        db_file=explicit_database_path,
        loggers=isolated_loggers("tests.rgb_pipeline.database"),
        webodm_processor=FakeWebODM(),
    )

    assert database_injected.repo.db_file == explicit_database_path
    assert explicit_database_path.is_file()
    assert database_injected.repo.get_run("injected-database-path")["status"] == (
        "running"
    )
    assert_within(explicit_database_path, temporary_path_layout.application_root)


def test_rgb_pipeline_preserves_injected_phase3_artifact_layouts(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    workspace = plan_run_workspace(
        temporary_path_layout.data_dir / "custom-workspaces",
        "layout-injection",
    )
    published = plan_published_survey_from_rgb_path(
        temporary_path_layout.surveys_dir / "custom" / "rgb"
    )

    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        run_id="layout-injection",
        repository=repository,
        loggers=isolated_loggers("tests.rgb_pipeline.layouts"),
        webodm_processor=FakeWebODM(),
        workspace_layout=workspace,
        published_layout=published,
    )

    assert pipeline.workspace_layout is workspace
    assert pipeline.workspace_root == (
        temporary_path_layout.application_root / "data" / "workspaces"
    )
    assert pipeline.published_layout is published
    assert not workspace.root.exists()
    assert_within(workspace.root, temporary_path_layout.application_root)
    assert_within(published.root, temporary_path_layout.application_root)


def test_rgb_pipeline_defaults_keep_existing_production_wiring(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository_calls = []
    logger_calls = []
    webodm_calls = []

    class RecordingRepository:
        def __init__(self, database_path):
            self.db_file = Path(database_path)
            self.created_runs = []
            repository_calls.append(self)

        def create_run(self, run_id, **kwargs):
            self.created_runs.append((run_id, kwargs))

    def recording_get_logger(name, log_file, *, run_id):
        logger_calls.append((name, Path(log_file), run_id))
        return logging.Logger(name)

    class StubWebODM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            webodm_calls.append(self)

    monkeypatch.setattr(rgb_module, "PipelineRepo", RecordingRepository)
    monkeypatch.setattr(rgb_module, "get_logger", recording_get_logger)
    monkeypatch.setattr(rgb_module, "WebODMProcessor", StubWebODM)

    pipeline = RGBPipeline(
        temporary_path_layout.application_root,
        explicit_config(temporary_path_layout),
        source_dir=sample_dataset_dir,
        surveys_root=temporary_path_layout.surveys_dir,
        year=2026,
        run_id="default-construction",
    )

    expected_logs_dir = temporary_path_layout.application_root / "data" / "logs"
    assert len(repository_calls) == 1
    assert pipeline.repo is repository_calls[0]
    assert pipeline.repo.db_file == (
        temporary_path_layout.application_root / "data" / "pipeline.db"
    )
    assert pipeline.repo.created_runs == [
        (
            "default-construction",
            {
                    "source_dir": str(sample_dataset_dir),
                    "surveys_root": str(temporary_path_layout.surveys_dir),
                    "year": 2026,
                    "workspace_root": str(
                        temporary_path_layout.application_root
                        / "data"
                        / "workspaces"
                    ),
                },
        ),
        ("default-construction", {}),
    ]
    assert [name for name, _, _ in logger_calls] == [
        "rgb.pipeline",
        "rgb.data_segregation",
        "rgb.cross_run_filter",
        "rgb.kml",
        "rgb.webodm",
        "rgb.qgis",
    ]
    assert all(path.parent == expected_logs_dir for _, path, _ in logger_calls)
    assert all(run_id == "default-construction" for _, _, run_id in logger_calls)
    assert pipeline.logs_dir == expected_logs_dir
    assert pipeline.checkpoint_dir == expected_logs_dir
    assert pipeline.workspace_root == (
        temporary_path_layout.application_root / "data" / "workspaces"
    )
    assert pipeline.workspace_layout.root == (
        temporary_path_layout.application_root
        / "data"
        / "workspaces"
        / "default-construction"
    )
    assert not pipeline.workspace_layout.root.exists()
    assert pipeline.published_layout is None
    assert pipeline._webodm_checkpoint_path() == (
        expected_logs_dir / "webodm_checkpoint_default-construction.json"
    )
    assert pipeline.control.data_dir == (
        temporary_path_layout.application_root / "data"
    )
    assert pipeline.webodm_processor is None
    assert webodm_calls == []

    processor = pipeline._create_webodm_processor(
        pipeline.loggers["webodm"]
    )
    assert processor is webodm_calls[0]
    assert processor.kwargs == {
        "url": "https://webodm.invalid",
        "username": "test-user",
        "password": "test-password",
        "logger": pipeline.loggers["webodm"],
    }


def test_rgb_pipeline_invalid_config_fails_before_side_effects(
    monkeypatch,
    tmp_path,
):
    invalid_root = tmp_path / "invalid-application"

    monkeypatch.setattr(
        rgb_module,
        "PipelineRepo",
        fail_if_called("open a repository for invalid configuration"),
    )
    monkeypatch.setattr(
        rgb_module,
        "get_logger",
        fail_if_called("create loggers for invalid configuration"),
    )
    monkeypatch.setattr(
        rgb_module,
        "WebODMProcessor",
        fail_if_called("construct WebODM for invalid configuration"),
    )

    with pytest.raises(TypeError, match="config must be a mapping"):
        RGBPipeline(
            invalid_root,
            None,
            source_dir=invalid_root / "source",
            surveys_root=invalid_root / "surveys",
            year=2026,
            run_id="invalid-config",
        )

    assert not invalid_root.exists()

def test_rgb_pipeline_hydrates_published_layout_from_actual_state_survey_path(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    survey_path_without_year = temporary_path_layout.surveys_dir / "AH-026019" / "rgb"

    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        run_id="hydrate-layout",
        repository=repository,
        loggers=isolated_loggers("tests.rgb_pipeline.hydrate_layout"),
        webodm_processor=FakeWebODM(),
    )
    pipeline.state["data_segregation"] = {
        "survey_id": "AH-026019",
        "survey_path": str(survey_path_without_year),
    }

    pipeline._hydrate_from_state()

    assert pipeline.survey_id == "AH-026019"
    assert pipeline.rgb_path == survey_path_without_year
    assert pipeline.published_layout is not None
    assert pipeline.published_layout.root == survey_path_without_year
    assert pipeline.published_layout.manifest == survey_path_without_year / "manifest.json"
    assert pipeline.published_layout.tiles_ortho_round == (
        survey_path_without_year / "tiles" / "ortho" / "round-corners"
    )


def test_rgb_pipeline_rejects_ambiguous_workspace_layout_inputs(
    temporary_path_layout,
    sample_dataset_dir,
):
    workspace = plan_run_workspace(
        temporary_path_layout.data_dir / "custom-workspaces",
        "ambiguous-layout",
    )

    with pytest.raises(ValueError, match="workspace_root and workspace_layout"):
        build_pipeline(
            temporary_path_layout,
            sample_dataset_dir,
            run_id="ambiguous-layout",
            repository=PipelineRepo(temporary_path_layout.database_path),
            loggers=isolated_loggers("tests.rgb_pipeline.ambiguous"),
            webodm_processor=FakeWebODM(),
            workspace_root=temporary_path_layout.data_dir / "other-workspaces",
            workspace_layout=workspace,
        )
