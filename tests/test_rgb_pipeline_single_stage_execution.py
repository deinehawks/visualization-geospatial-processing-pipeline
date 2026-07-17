
import importlib.util
import logging
import sqlite3
from pathlib import Path
import sys
import types

import pytest


if "exifread" not in sys.modules and importlib.util.find_spec("exifread") is None:
    exifread_stub = types.ModuleType("exifread")

    def reject_exif_processing(*args, **kwargs):
        pytest.fail("single-stage test attempted EXIF processing")

    exifread_stub.process_file = reject_exif_processing
    sys.modules["exifread"] = exifread_stub


from pipelines import rgb_pipeline as rgb_module
from pipelines.rgb_pipeline import RGBPipeline
from shared.db.repo import PipelineRepo
from shared.logging import get_logger
from tests.fakes import FakeWebODM


LOGGER_KEYS = (
    "pipeline",
    "segregation",
    "cross_run_filter",
    "kml",
    "webodm",
    "qgis",
)
RUN_ID = "single-stage-rgb-run"
SELECTED_STAGE = "data_segregation"
LATER_STAGES = (
    "cross_run_filter",
    "kml_boundary",
    "webodm",
    "quality_gate",
    "qgis",
)


class ControlledSegregationFailure(Exception):
    pass


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
        "experiment": {"use_year_subdir": True},
    }


def isolated_loggers(prefix, pipeline_log_path=None, webodm_log_path=None):
    loggers = {key: logging.Logger(f"{prefix}.{key}") for key in LOGGER_KEYS}
    if pipeline_log_path is not None:
        loggers["pipeline"] = get_logger(
            f"{prefix}.pipeline",
            pipeline_log_path,
            to_console=False,
            run_id=RUN_ID,
        )
    if webodm_log_path is not None:
        loggers["webodm"] = get_logger(
            f"{prefix}.webodm",
            webodm_log_path,
            to_console=False,
            run_id=RUN_ID,
        )
    return loggers



def cleanup_logger(logger):
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.filters.clear()
    if hasattr(logger, "_configured"):
        delattr(logger, "_configured")


def assert_within(path, root):
    resolved_path = Path(path).resolve()
    resolved_root = Path(root).resolve()
    assert resolved_path == resolved_root or resolved_root in resolved_path.parents


def read_stage(database_path, run_id=RUN_ID, stage_name=SELECTED_STAGE):
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT status, started_at, finished_at, error_message, output_json
            FROM stages
            WHERE run_id=? AND stage_name=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (run_id, stage_name),
        ).fetchone()
    return dict(row) if row else None


def all_stage_names(database_path, run_id=RUN_ID):
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT stage_name
            FROM stages
            WHERE run_id=?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    return [row[0] for row in rows]


def build_pipeline(
    temporary_path_layout,
    sample_dataset_dir,
    repository,
    fake_webodm,
    pipeline_log_path=None,
    webodm_log_path=None,
):
    return RGBPipeline(
        temporary_path_layout.application_root,
        explicit_config(temporary_path_layout),
        source_dir=sample_dataset_dir,
        surveys_root=temporary_path_layout.surveys_dir,
        year=2026,
        run_id=RUN_ID,
        repository=repository,
        loggers=isolated_loggers(
            "tests.rgb_pipeline.single_stage",
            pipeline_log_path=pipeline_log_path,
            webodm_log_path=webodm_log_path,
        ),
        logs_dir=temporary_path_layout.logs_dir,
        checkpoint_dir=temporary_path_layout.checkpoint_dir,
        webodm_processor=fake_webodm,
    )


def block_later_stages(pipeline, calls):
    def fail_later_stage(stage_name):
        def fail(*args, **kwargs):
            calls.append(stage_name)
            pytest.fail(f"unexpected later stage executed: {stage_name}")

        return fail

    pipeline.stage_cross_run_image_filter = fail_later_stage("cross_run_filter")
    pipeline.stage_kml_boundary = fail_later_stage("kml_boundary")
    pipeline.stage_webodm = fail_later_stage("webodm")
    pipeline.stage_quality_gate = fail_later_stage("quality_gate")
    pipeline.stage_qgis = fail_later_stage("qgis")


def test_rgb_pipeline_executes_one_selected_stage_successfully(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = FakeWebODM()
    pipeline_log_path = temporary_path_layout.logs_dir / "pipeline-run-events.log"
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
        pipeline_log_path=pipeline_log_path,
    )
    later_stage_calls = []
    preflight_calls = []
    control_checks = []
    running_observed = []
    dependency_calls = []
    block_later_stages(pipeline, later_stage_calls)

    pipeline.control.start_hotkeys = lambda logger: None
    original_check_control = pipeline._check_control_or_raise

    def record_control_check(stage_name):
        control_checks.append(stage_name)
        original_check_control(stage_name)

    pipeline._check_control_or_raise = record_control_check
    pipeline._preflight_stage = lambda stage_name: preflight_calls.append(stage_name)

    def fake_data_segregation(**kwargs):
        dependency_calls.append(kwargs)
        stage = repository.get_latest_stage(RUN_ID, SELECTED_STAGE)
        running_observed.append(stage["status"])

        survey_id = "TEST-SURVEY-001"
        survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
        boundary_dir = survey_path / "boundary"
        boundary_dir.mkdir(parents=True)
        (boundary_dir / "source-boundary.kml").write_text("<kml />", encoding="utf-8")
        (survey_path / "images" / "raw").mkdir(parents=True)
        return {
            "survey_id": survey_id,
            "survey_path": str(survey_path),
            "source_dir": str(kwargs["source_dir"]),
            "deterministic_marker": "segregated-by-fake",
        }

    monkeypatch.setattr(rgb_module, "run_data_segregation", fake_data_segregation)

    result = pipeline.run(
        selected_stages={SELECTED_STAGE},
        force_stages={SELECTED_STAGE},
    )

    selected_output = result["data_segregation"]
    survey_path = Path(selected_output["survey_path"])
    renamed_kml = survey_path / "boundary" / f"{selected_output['survey_id']}.kml"

    assert result["success"] is True
    assert selected_output["deterministic_marker"] == "segregated-by-fake"
    assert selected_output == repository.get_latest_stage_output(
        RUN_ID,
        SELECTED_STAGE,
    )
    assert running_observed == ["running"]
    assert len(dependency_calls) == 1
    assert dependency_calls[0]["source_dir"] == sample_dataset_dir
    assert preflight_calls == [SELECTED_STAGE]
    assert control_checks == [SELECTED_STAGE]
    assert later_stage_calls == []
    assert fake_webodm.calls == []
    assert all_stage_names(temporary_path_layout.database_path) == [SELECTED_STAGE]

    stage = read_stage(temporary_path_layout.database_path)
    assert stage["status"] == "completed"
    assert stage["started_at"] is not None
    assert stage["finished_at"] is not None
    assert stage["error_message"] is None
    assert repository.get_run(RUN_ID)["status"] == "completed"
    assert repository.get_run(RUN_ID)["survey_id"] == selected_output["survey_id"]
    assert renamed_kml.is_file()

    for path in (
        survey_path,
        renamed_kml,
        pipeline._webodm_checkpoint_path(),
        repository.db_file,
        pipeline.control.pause_flag,
        pipeline.control.abort_flag,
    ):
        assert_within(path, temporary_path_layout.application_root)
    content = pipeline_log_path.read_text(encoding="utf-8")
    assert (
        f"tests.rgb_pipeline.single_stage.pipeline | {RUN_ID} |  "
        "| event=run_started"
    ) in content
    assert "event=run_completed elapsed_seconds=" in content
    assert "survey_id=TEST-SURVEY-001" in content
    cleanup_logger(pipeline.loggers["pipeline"])

    assert list(temporary_path_layout.checkpoint_dir.iterdir()) == []


def test_rgb_pipeline_selected_stage_failure_is_recorded_and_propagated(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = FakeWebODM()
    pipeline_log_path = temporary_path_layout.logs_dir / "pipeline-run-events.log"
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
        pipeline_log_path=pipeline_log_path,
    )
    later_stage_calls = []
    preflight_calls = []
    dependency_calls = []
    block_later_stages(pipeline, later_stage_calls)

    pipeline.control.start_hotkeys = lambda logger: None
    pipeline._preflight_stage = lambda stage_name: preflight_calls.append(stage_name)
    monkeypatch.setattr(rgb_module.time, "sleep", lambda seconds: None)

    def fail_data_segregation(**kwargs):
        dependency_calls.append(kwargs)
        raise ControlledSegregationFailure("controlled segregation failure")

    monkeypatch.setattr(rgb_module, "run_data_segregation", fail_data_segregation)

    with pytest.raises(
        ControlledSegregationFailure,
        match="controlled segregation failure",
    ):
        pipeline.run(
            selected_stages={SELECTED_STAGE},
            force_stages={SELECTED_STAGE},
            raise_on_error=True,
        )

    assert len(dependency_calls) == 3
    assert preflight_calls == [SELECTED_STAGE]
    assert later_stage_calls == []
    assert fake_webodm.calls == []
    assert all_stage_names(temporary_path_layout.database_path) == [SELECTED_STAGE]

    stage = read_stage(temporary_path_layout.database_path)
    assert stage["status"] == "failed"
    assert stage["finished_at"] is not None
    assert stage["error_message"] == "controlled segregation failure"
    assert stage["output_json"] is None
    assert repository.get_run(RUN_ID)["status"] == "failed"
    assert "cross_run_filter" not in pipeline.state
    assert "kml_boundary" not in pipeline.state
    assert "webodm" not in pipeline.state
    assert "quality_gate" not in pipeline.state
    assert "qgis" not in pipeline.state

    for path in (
        repository.db_file,
        pipeline._webodm_checkpoint_path(),
        pipeline.control.pause_flag,
        pipeline.control.abort_flag,
    ):
        assert_within(path, temporary_path_layout.application_root)
    content = pipeline_log_path.read_text(encoding="utf-8")
    assert (
        f"tests.rgb_pipeline.single_stage.pipeline | {RUN_ID} |  "
        "| event=run_started"
    ) in content
    assert "event=run_failed" in content
    assert "error_type=ControlledSegregationFailure" in content
    assert 'error_message="controlled segregation failure"' in content
    cleanup_logger(pipeline.loggers["pipeline"])
    assert list(temporary_path_layout.checkpoint_dir.iterdir()) == []

def test_rgb_pipeline_webodm_stage_emits_parseable_boundary_events(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = FakeWebODM()
    webodm_log_path = temporary_path_layout.logs_dir / "webodm-boundary-events.log"
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
        webodm_log_path=webodm_log_path,
    )
    survey_id = "TEST-SURVEY-ODM"
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    image_path = survey_path / "images" / "path"
    boundary_path = survey_path / "boundary" / f"{survey_id}.geojson"
    image_path.mkdir(parents=True)
    boundary_path.parent.mkdir(parents=True)
    boundary_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    pipeline.survey_id = survey_id
    pipeline.rgb_path = survey_path
    pipeline.skip_task1_webodm = True
    pipeline.skip_task2_webodm = False
    pipeline.skip_task4_webodm = True
    pipeline.state.update(
        {
            "data_segregation": {
                "dirs": {"path": str(image_path)},
                "survey_id": survey_id,
                "survey_path": str(survey_path),
            },
            "boundary_available": True,
            "boundary_geojson_path": str(boundary_path),
        }
    )
    pipeline._stage_upload_cache = lambda **kwargs: (image_path, 1)

    try:
        result = pipeline.stage_webodm()

        assert result["project_id"] == 100
        assert result["task2"]["id"] == "task-0001"
        assert [call.method for call in fake_webodm.calls] == [
            "create_project",
            "create_task_with_images",
            "wait_for_completion",
        ]

        content = webodm_log_path.read_text(encoding="utf-8")
        assert "event=webodm_project_created project_id=100" in content
        assert (
            "event=webodm_task_created project_id=100 task_key=task2 "
            "task_id=task-0001"
        ) in content
        assert (
            "event=webodm_task_status project_id=100 task_key=task2 "
            "task_id=task-0001 status=queued success=False elapsed_seconds=0.00"
        ) in content
    finally:
        cleanup_logger(pipeline.loggers["webodm"])
