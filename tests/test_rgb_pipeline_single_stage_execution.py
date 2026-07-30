
import importlib.util
import json
import logging
import sqlite3
from pathlib import Path
import sys
import types

import pytest


if "exifread" not in sys.modules and importlib.util.find_spec("exifread") is None:
    exifread_stub = types.ModuleType("exifread")
    exifread_stub.__spec__ = importlib.util.spec_from_loader("exifread", loader=None)

    def reject_exif_processing(*args, **kwargs):
        pytest.fail("single-stage test attempted EXIF processing")

    exifread_stub.process_file = reject_exif_processing
    sys.modules["exifread"] = exifread_stub


from pipelines import rgb_pipeline as rgb_module
from pipelines.rgb_pipeline import RGBPipeline
from shared.db.repo import PipelineRepo
from shared.logging import get_logger
from shared.publication_lock import acquire_publication_lock, PublicationLockedError
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


class ControlledCrossRunFailure(Exception):
    pass


class ControlledKMLFailure(Exception):
    pass


class ControlledWebODMExportFailure(Exception):
    pass


class ControlledWebODMPointcloudFailure(Exception):
    pass


class ControlledQGISFailure(Exception):
    pass


class FakeQGISTools:
    instances = []
    fail_clip = False
    fail_tiles = False

    def __init__(
        self,
        *,
        logger,
        qgis_root="",
        gdalwarp_path="gdalwarp",
        gdal2tiles_path="gdal2tiles.py",
        gdalinfo_path="gdalinfo",
    ):
        self.logger = logger
        self.qgis_root = qgis_root
        self.gdalwarp_path = gdalwarp_path
        self.gdal2tiles_path = gdal2tiles_path
        self.gdalinfo_path = gdalinfo_path
        self.calls = []
        type(self).instances.append(self)

    def clip_raster_by_mask(
        self,
        *,
        input_tif,
        mask_geojson,
        output_tif,
        dst_nodata=None,
        local_staging_dir=None,
    ):
        self.calls.append(
            {
                "method": "clip_raster_by_mask",
                "input_tif": input_tif,
                "mask_geojson": mask_geojson,
                "output_tif": output_tif,
                "dst_nodata": dst_nodata,
                "local_staging_dir": local_staging_dir,
            }
        )
        output_tif.parent.mkdir(parents=True, exist_ok=True)
        output_tif.write_text("workspace clipped ortho", encoding="utf-8")
        if type(self).fail_clip:
            raise ControlledQGISFailure("controlled qgis clip failure")
        return output_tif

    def generate_tiles(
        self,
        *,
        input_tif,
        output_dir,
        zoom="11-24",
        profile="mercator",
        webviewer="none",
        copyright_text="ASIMOV-HAWKS",
        resume=False,
        clean=False,
    ):
        self.calls.append(
            {
                "method": "generate_tiles",
                "input_tif": input_tif,
                "output_dir": output_dir,
                "zoom": zoom,
                "profile": profile,
                "webviewer": webviewer,
                "copyright_text": copyright_text,
                "resume": resume,
                "clean": clean,
            }
        )
        if type(self).fail_tiles:
            raise ControlledQGISFailure("controlled qgis tile failure")
        output_dir.mkdir(parents=True, exist_ok=True)
        tile = output_dir / "12" / "345" / "678.png"
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_text("workspace tile", encoding="utf-8")
        return output_dir

    def stage_local_copy(self, src, local_dir):
        self.calls.append(
            {
                "method": "stage_local_copy",
                "src": src,
                "local_dir": local_dir,
            }
        )
        local_dir.mkdir(parents=True, exist_ok=True)
        dst = local_dir / Path(src).name
        dst.write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")
        return dst

    def verify_raster_readable(self, tif_path, *, retries=3, delay_s=5.0):
        self.calls.append(
            {
                "method": "verify_raster_readable",
                "tif_path": tif_path,
                "retries": retries,
                "delay_s": delay_s,
            }
        )


class OrthomosaicExportFakeWebODM(FakeWebODM):
    def __init__(self, *, fail_export=False):
        super().__init__()
        self.fail_export = fail_export

    def export_orthomosaic(
        self,
        project_id,
        task_id,
        *,
        out_dir,
        filename,
        epsg,
        candidates,
        gdalwarp_path,
    ):
        self._record(
            "export_orthomosaic",
            project_id,
            str(task_id),
            out_dir=out_dir,
            filename=filename,
            epsg=epsg,
            candidates=list(candidates),
            gdalwarp_path=gdalwarp_path,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_text(f"workspace ortho {task_id}", encoding="utf-8")
        if self.fail_export:
            raise ControlledWebODMExportFailure("controlled orthomosaic export failure")
        return out_path




class RemainingWebODMExportFake(OrthomosaicExportFakeWebODM):
    def __init__(self, *, fail_pointcloud=False, fail_zip=False):
        super().__init__()
        self.fail_pointcloud = fail_pointcloud
        self.fail_zip = fail_zip

    def export_pointcloud(
        self,
        project_id,
        task_id,
        *,
        out_dir,
        laz_archive_name,
        pcd_name="odm.pcd",
        candidates=None,
        max_points=3_000_000,
        viewpoint="0 0 0 1 0 0 0",
    ):
        self._record(
            "export_pointcloud",
            project_id,
            str(task_id),
            out_dir=out_dir,
            laz_archive_name=laz_archive_name,
            pcd_name=pcd_name,
            candidates=list(candidates or []),
            max_points=max_points,
            viewpoint=viewpoint,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        laz_path = out_dir / laz_archive_name
        pcd_path = out_dir / pcd_name
        laz_path.write_text("workspace laz", encoding="utf-8")
        pcd_path.write_text("workspace pcd", encoding="utf-8")
        if self.fail_pointcloud:
            raise ControlledWebODMPointcloudFailure("controlled pointcloud failure")
        return {
            "laz": str(laz_path),
            "pcd": str(pcd_path),
            "asset_type": "georeferenced_model.laz",
        }

    def download_all_assets_safe(self, project_id, task_id, out_file):
        self._record(
            "download_all_assets_safe",
            project_id,
            str(task_id),
            str(out_file),
        )
        out_file = Path(out_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("workspace all assets", encoding="utf-8")
        return not self.fail_zip


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


def prepare_cross_run_context(pipeline, temporary_path_layout, survey_id="TEST-SURVEY-XRF"):
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    raw_dir = survey_path / "images" / "raw"
    raw_dir.mkdir(parents=True)
    for name in ("image-001.jpg", "image-002.jpeg"):
        (raw_dir / name).write_text(f"raw {name}", encoding="utf-8")

    pipeline._set_survey_artifact_context(survey_id, survey_path)
    pipeline.state["data_segregation"] = {
        "survey_id": survey_id,
        "survey_path": str(survey_path),
        "dirs": {
            "raw": str(raw_dir),
            "path": str(survey_path / "images" / "path"),
            "cross_runs": str(survey_path / "images" / "cross-runs"),
        },
    }
    return survey_path, raw_dir


def prepare_kml_boundary_context(pipeline, temporary_path_layout, survey_id="TEST-SURVEY-KML"):
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    boundary_dir = survey_path / "boundary"
    boundary_dir.mkdir(parents=True)
    (boundary_dir / f"{survey_id}.kml").write_text("<kml />", encoding="utf-8")

    pipeline._set_survey_artifact_context(survey_id, survey_path)
    pipeline.state["data_segregation"] = {
        "survey_id": survey_id,
        "survey_path": str(survey_path),
        "dirs": {
            "boundary": str(boundary_dir),
        },
    }
    return survey_path, boundary_dir


def prepare_webodm_ortho_context(pipeline, temporary_path_layout, survey_id="TEST-SURVEY-ODM-ORTHO"):
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    image_path = survey_path / "images" / "path"
    image_path.mkdir(parents=True)
    (image_path / "image-001.jpg").write_text("image", encoding="utf-8")
    boundary_path = survey_path / "boundary" / f"{survey_id}.geojson"
    boundary_path.parent.mkdir(parents=True)
    boundary_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    pipeline._set_survey_artifact_context(survey_id, survey_path)
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
    return survey_path, image_path, boundary_path




def prepare_qgis_context(pipeline, temporary_path_layout, survey_id="TEST-SURVEY-QGIS"):
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    source_ortho = survey_path / "ortho" / "orthomosaic--xcb-t2.tif"
    source_ortho.parent.mkdir(parents=True)
    source_ortho.write_text("source ortho", encoding="utf-8")

    boundary_path = survey_path / "boundary" / f"{survey_id}.geojson"
    boundary_path.parent.mkdir(parents=True)
    boundary_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    pipeline._set_survey_artifact_context(survey_id, survey_path)
    pipeline.state.update(
        {
            "data_segregation": {
                "survey_id": survey_id,
                "survey_path": str(survey_path),
                "dirs": {
                    "qgis_clipped_ortho": str(survey_path / "qgis" / "clipped" / "ortho"),
                    "tiles_ortho_round": str(
                        survey_path / "tiles" / "ortho" / "round-corners"
                    ),
                    "tiles_ortho_soft": str(
                        survey_path / "tiles" / "ortho" / "soft-corners"
                    ),
                },
            },
            "boundary_available": True,
            "boundary_geojson_path": str(boundary_path),
            "selected_webodm_task": "task2",
            "selected_orthomosaic": {
                "task_key": "task2",
                "task_label": "t2",
                "task_id": "task-0001",
                "task_name": "TEST-SURVEY-QGIS-RGB--xcb-t2",
                "flag": "xcb",
                "source_path": str(source_ortho),
                "source_filename": source_ortho.name,
                "boundary_used": True,
                "tile_mode": "round-corners",
                "fallback_used": False,
                "fallback_reason": None,
            },
        }
    )
    return survey_path, source_ortho, boundary_path


def configure_qgis_for_fake_tools(pipeline):
    pipeline.config["qgis"] = {
        "enabled": True,
        "clip": {"enabled": True, "dst_nodata": "0"},
        "tiles": {
            "enabled": True,
            "zoom": "11-12",
            "profile": "mercator",
            "webviewer": "none",
            "copyright": "TEST-COPYRIGHT",
        },
        "local_staging": {"enabled": False},
        "tools": {
            "qgis_root": "",
            "gdalwarp_path": "fake-gdalwarp",
            "gdal2tiles_path": "fake-gdal2tiles",
            "gdalinfo_path": "fake-gdalinfo",
        },
    }



def enable_remaining_webodm_exports(pipeline):
    pipeline.config["exports"] = {
        "enabled": True,
        "ortho": {"enabled": False},
        "pointcloud": {
            "enabled": True,
            "required": True,
            "asset_candidates": ["georeferenced_model.laz"],
            "max_points": 123,
            "viewpoint": "0 0 0 1 0 0 0",
        },
        "all_assets_zip": {
            "enabled": True,
            "filename_template": "{survey_id}-RGB-{flag}-all.zip",
        },
        "tools": {"pdal_path": "fake-pdal"},
    }

def enable_only_orthomosaic_export(pipeline):
    pipeline.config["exports"] = {
        "enabled": True,
        "ortho": {
            "enabled": True,
            "reproject_epsg": 4326,
            "asset_candidates": ["orthophoto.tif"],
        },
        "pointcloud": {"enabled": False},
        "all_assets_zip": {"enabled": False},
        "tools": {"gdalwarp_path": "fake-gdalwarp"},
    }


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
    assert selected_output["workspace"]["root"] == str(pipeline.workspace_layout.root)
    assert selected_output["workspace"]["images_raw"] == str(
        pipeline.workspace_layout.images_raw
    )
    assert selected_output["published"]["root"] == str(survey_path)
    assert selected_output["published"]["boundary"] == str(survey_path / "boundary")
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
    assert pipeline.workspace_layout.root.is_dir()
    assert pipeline.workspace_layout.images_raw.is_dir()
    assert pipeline.workspace_layout.publish.is_dir()
    assert "data_segregation" not in pipeline.state
    assert repository.get_latest_stage_output(RUN_ID, SELECTED_STAGE) is None
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



def test_cross_run_filter_writes_workspace_then_mirrors_legacy_outputs(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_path, raw_dir = prepare_cross_run_context(pipeline, temporary_path_layout)
    legacy_output_dir = survey_path / "images" / "path"
    legacy_excluded_dir = survey_path / "images" / "cross-runs"
    legacy_output_dir.mkdir(parents=True)
    (legacy_output_dir / "stale.jpg").write_text("old output", encoding="utf-8")
    calls = []

    def fake_run_filter(input_dir, output_dir, logger, max_gap, cross_run_window):
        calls.append(
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "max_gap": max_gap,
                "cross_run_window": cross_run_window,
            }
        )
        excluded_dir = output_dir.parent / "cross-runs"
        output_dir.mkdir(parents=True, exist_ok=True)
        excluded_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "kept.jpg").write_text("kept", encoding="utf-8")
        (excluded_dir / "excluded.jpg").write_text("excluded", encoding="utf-8")
        return {
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "excluded_dir": str(excluded_dir),
            "total_images": 2,
            "total_kept": 1,
            "total_excluded": 1,
            "cross_runs_detected": 1,
        }

    monkeypatch.setattr(rgb_module, "run_filter", fake_run_filter)

    result = pipeline.stage_cross_run_image_filter()

    assert calls == [
        {
            "input_dir": raw_dir,
            "output_dir": pipeline.workspace_layout.images_path,
            "max_gap": 10,
            "cross_run_window": 3,
        }
    ]
    assert (pipeline.workspace_layout.images_path / "kept.jpg").is_file()
    assert (pipeline.workspace_layout.images_cross_runs / "excluded.jpg").is_file()
    assert (legacy_output_dir / "kept.jpg").is_file()
    assert (legacy_excluded_dir / "excluded.jpg").is_file()
    assert not (legacy_output_dir / "stale.jpg").exists()
    assert result["output_dir"] == str(legacy_output_dir)
    assert result["excluded_dir"] == str(legacy_excluded_dir)
    assert result["workspace"]["output_dir"] == str(pipeline.workspace_layout.images_path)
    assert result["workspace"]["excluded_dir"] == str(
        pipeline.workspace_layout.images_cross_runs
    )
    assert result["published"]["output_dir"] == str(legacy_output_dir)
    assert result["crossrun_flag"] == "xc"
    assert result["experiment_crossrun_label"] == "F"
    assert pipeline.state["crossrun_flag"] == "xc"
    assert pipeline.state["experiment_crossrun_label"] == "F"

    for path_to_check in (
        pipeline.workspace_layout.images_path,
        pipeline.workspace_layout.images_cross_runs,
        legacy_output_dir,
        legacy_excluded_dir,
    ):
        assert_within(path_to_check, temporary_path_layout.application_root)


def test_cross_run_filter_disabled_copies_raw_through_workspace_then_legacy(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_path, raw_dir = prepare_cross_run_context(pipeline, temporary_path_layout)
    legacy_output_dir = survey_path / "images" / "path"
    legacy_excluded_dir = survey_path / "images" / "cross-runs"
    legacy_output_dir.mkdir(parents=True)
    (legacy_output_dir / "stale.jpg").write_text("old output", encoding="utf-8")
    pipeline.crossrun_enabled_override = False

    result = pipeline.stage_cross_run_image_filter()

    assert sorted(path.name for path in pipeline.workspace_layout.images_path.iterdir()) == [
        "image-001.jpg",
        "image-002.jpeg",
    ]
    assert list(pipeline.workspace_layout.images_cross_runs.iterdir()) == []
    assert sorted(path.name for path in legacy_output_dir.iterdir()) == [
        "image-001.jpg",
        "image-002.jpeg",
    ]
    assert list(legacy_excluded_dir.iterdir()) == []
    assert not (legacy_output_dir / "stale.jpg").exists()
    assert result["filter_enabled"] is False
    assert result["total_images"] == 2
    assert result["total_kept"] == 2
    assert result["total_excluded"] == 0
    assert result["raw_deleted"] is False
    assert result["output_dir"] == str(legacy_output_dir)
    assert result["workspace"]["input_dir"] == str(raw_dir)
    assert result["workspace"]["output_dir"] == str(pipeline.workspace_layout.images_path)
    assert result["published"]["excluded_dir"] == str(legacy_excluded_dir)
    assert pipeline.state["crossrun_flag"] == "c"
    assert pipeline.state["experiment_crossrun_label"] == "NF"


def test_cross_run_filter_failure_leaves_legacy_outputs_untouched(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_path, _raw_dir = prepare_cross_run_context(pipeline, temporary_path_layout)
    legacy_output_dir = survey_path / "images" / "path"
    legacy_excluded_dir = survey_path / "images" / "cross-runs"
    legacy_output_dir.mkdir(parents=True)
    legacy_excluded_dir.mkdir(parents=True)
    (legacy_output_dir / "keep-existing.jpg").write_text("existing", encoding="utf-8")
    (legacy_excluded_dir / "keep-excluded.jpg").write_text("existing", encoding="utf-8")

    def failing_run_filter(input_dir, output_dir, logger, max_gap, cross_run_window):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "partial-workspace.jpg").write_text("partial", encoding="utf-8")
        raise ControlledCrossRunFailure("controlled cross-run failure")

    monkeypatch.setattr(rgb_module, "run_filter", failing_run_filter)

    with pytest.raises(ControlledCrossRunFailure, match="controlled cross-run failure"):
        pipeline.stage_cross_run_image_filter()

    assert (pipeline.workspace_layout.images_path / "partial-workspace.jpg").is_file()
    assert (legacy_output_dir / "keep-existing.jpg").read_text(encoding="utf-8") == "existing"
    assert (legacy_excluded_dir / "keep-excluded.jpg").read_text(encoding="utf-8") == "existing"
    assert not (legacy_output_dir / "partial-workspace.jpg").exists()
    assert "crossrun_flag" not in pipeline.state
    assert "experiment_crossrun_label" not in pipeline.state

def test_rgb_pipeline_pause_signal_emits_parseable_run_event(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline_log_path = temporary_path_layout.logs_dir / "pipeline-paused-events.log"
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
        pipeline_log_path=pipeline_log_path,
    )

    pipeline.control.start_hotkeys = lambda logger: None

    def pause_requested():
        raise RuntimeError("__PIPELINE_PAUSED__")

    pipeline.control.check_or_raise = pause_requested

    try:
        result = pipeline.run(selected_stages={SELECTED_STAGE})

        assert result["success"] is False
        assert result["paused"] is True
        assert result["error"] == "paused_by_flag"
        assert repository.get_run(RUN_ID)["status"] == "paused"
        assert all_stage_names(temporary_path_layout.database_path) == []

        content = pipeline_log_path.read_text(encoding="utf-8")
        assert "event=run_started" in content
        assert "event=run_paused reason=paused_by_flag after_stage=" in content
    finally:
        cleanup_logger(pipeline.loggers["pipeline"])


def test_rgb_pipeline_abort_signal_emits_parseable_run_event(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline_log_path = temporary_path_layout.logs_dir / "pipeline-aborted-events.log"
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
        pipeline_log_path=pipeline_log_path,
    )

    pipeline.control.start_hotkeys = lambda logger: None

    def abort_requested():
        raise RuntimeError("__PIPELINE_ABORTED__")

    pipeline.control.check_or_raise = abort_requested

    try:
        result = pipeline.run(selected_stages={SELECTED_STAGE})

        assert result["success"] is False
        assert result["aborted"] is True
        assert result["error"] == "aborted_by_hotkey"
        assert repository.get_run(RUN_ID)["status"] == "failed"
        assert all_stage_names(temporary_path_layout.database_path) == []

        content = pipeline_log_path.read_text(encoding="utf-8")
        assert "event=run_started" in content
        assert "event=run_aborted" in content
        assert "reason=aborted_by_hotkey" in content
    finally:
        cleanup_logger(pipeline.loggers["pipeline"])


def test_rgb_pipeline_webodm_ui_cancel_emits_parseable_run_event(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline_log_path = temporary_path_layout.logs_dir / "pipeline-canceled-events.log"
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
        pipeline_log_path=pipeline_log_path,
    )
    preflight_calls = []

    pipeline.control.start_hotkeys = lambda logger: None
    pipeline._preflight_stage = lambda stage_name: preflight_calls.append(stage_name)

    def canceled_in_webodm_ui():
        raise RuntimeError("WEBODM_TASK_CANCELED")

    pipeline.stage_webodm = canceled_in_webodm_ui

    try:
        result = pipeline.run(
            selected_stages={"webodm"},
            force_stages={"webodm"},
        )

        assert result["success"] is False
        assert result["canceled"] is True
        assert result["error"] == "canceled_in_webodm_ui"
        assert preflight_calls == ["webodm"]
        assert repository.get_run(RUN_ID)["status"] == "paused"

        stage = read_stage(
            temporary_path_layout.database_path,
            stage_name="webodm",
        )
        assert stage["status"] == "failed"
        assert stage["error_message"] == "Canceled in WebODM UI"

        content = pipeline_log_path.read_text(encoding="utf-8")
        assert "event=run_started" in content
        assert "event=stage_canceled" in content
        assert "event=run_canceled reason=webodm_ui_cancel after_stage=webodm" in content
    finally:
        cleanup_logger(pipeline.loggers["pipeline"])

def test_kml_boundary_writes_workspace_then_mirrors_legacy_outputs(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    _survey_path, boundary_dir = prepare_kml_boundary_context(
        pipeline,
        temporary_path_layout,
    )
    legacy_geojson = boundary_dir / "TEST-SURVEY-KML.geojson"
    legacy_csv = boundary_dir / "TEST-SURVEY-KML.csv"
    legacy_geojson.write_text("old geojson", encoding="utf-8")
    legacy_csv.write_text("old csv", encoding="utf-8")
    calls = []

    def fake_run_kml(kml_dir, geojson_dir, csv_dir, logger):
        calls.append(
            {
                "kml_dir": kml_dir,
                "geojson_dir": geojson_dir,
                "csv_dir": csv_dir,
            }
        )
        geojson_dir.mkdir(parents=True, exist_ok=True)
        csv_dir.mkdir(parents=True, exist_ok=True)
        workspace_geojson = geojson_dir / "TEST-SURVEY-KML.geojson"
        workspace_csv = csv_dir / "TEST-SURVEY-KML.csv"
        workspace_geojson.write_text('{"type":"FeatureCollection"}', encoding="utf-8")
        workspace_csv.write_text("id\nTEST-SURVEY-KML\n", encoding="utf-8")
        return {
            "success": True,
            "processed": 1,
            "failed": 0,
            "processed_files": [
                {
                    "kml": "TEST-SURVEY-KML.kml",
                    "geojson": str(workspace_geojson),
                    "csv": str(workspace_csv),
                }
            ],
            "failed_files": [],
            "geojson_dir": str(geojson_dir),
            "csv_dir": str(csv_dir),
        }

    monkeypatch.setattr(rgb_module, "run_kml", fake_run_kml)

    result = pipeline.stage_kml_boundary()

    assert calls == [
        {
            "kml_dir": boundary_dir,
            "geojson_dir": pipeline.workspace_layout.boundary,
            "csv_dir": pipeline.workspace_layout.boundary,
        }
    ]
    assert (pipeline.workspace_layout.boundary / "TEST-SURVEY-KML.geojson").is_file()
    assert (pipeline.workspace_layout.boundary / "TEST-SURVEY-KML.csv").is_file()
    assert legacy_geojson.read_text(encoding="utf-8") == '{"type":"FeatureCollection"}'
    assert legacy_csv.read_text(encoding="utf-8") == "id\nTEST-SURVEY-KML\n"
    assert result["geojson_dir"] == str(boundary_dir)
    assert result["csv_dir"] == str(boundary_dir)
    assert result["processed_files"][0]["geojson"] == str(legacy_geojson)
    assert result["processed_files"][0]["csv"] == str(legacy_csv)
    assert result["workspace"]["geojson_dir"] == str(pipeline.workspace_layout.boundary)
    assert result["workspace"]["processed_files"][0]["geojson"] == str(
        pipeline.workspace_layout.boundary / "TEST-SURVEY-KML.geojson"
    )
    assert result["published"]["processed_files"][0]["geojson"] == str(legacy_geojson)
    assert result["boundary_available"] is True
    assert result["boundary_geojson_path"] == str(legacy_geojson)
    assert pipeline.state["boundary_available"] is True
    assert pipeline.state["boundary_geojson_path"] == str(legacy_geojson)

    for path_to_check in (
        pipeline.workspace_layout.boundary,
        legacy_geojson,
        legacy_csv,
    ):
        assert_within(path_to_check, temporary_path_layout.application_root)


def test_kml_boundary_without_processed_files_preserves_legacy_shape(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    _survey_path, boundary_dir = prepare_kml_boundary_context(
        pipeline,
        temporary_path_layout,
    )

    def fake_run_kml(kml_dir, geojson_dir, csv_dir, logger):
        return {
            "success": True,
            "processed": 0,
            "failed": 1,
            "processed_files": [],
            "failed_files": ["TEST-SURVEY-KML.kml"],
            "geojson_dir": str(geojson_dir),
            "csv_dir": str(csv_dir),
        }

    monkeypatch.setattr(rgb_module, "run_kml", fake_run_kml)

    result = pipeline.stage_kml_boundary()

    assert result["processed_files"] == []
    assert result["geojson_dir"] == str(boundary_dir)
    assert result["csv_dir"] == str(boundary_dir)
    assert result["workspace"]["geojson_dir"] == str(pipeline.workspace_layout.boundary)
    assert result["published"]["processed_files"] == []
    assert result["boundary_available"] is False
    assert result["boundary_geojson_path"] is None
    assert pipeline.state["boundary_available"] is False
    assert pipeline.state["boundary_geojson_path"] is None


def test_kml_boundary_failure_leaves_legacy_outputs_untouched(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    _survey_path, boundary_dir = prepare_kml_boundary_context(
        pipeline,
        temporary_path_layout,
    )
    legacy_geojson = boundary_dir / "TEST-SURVEY-KML.geojson"
    legacy_csv = boundary_dir / "TEST-SURVEY-KML.csv"
    legacy_geojson.write_text("old geojson", encoding="utf-8")
    legacy_csv.write_text("old csv", encoding="utf-8")

    def failing_run_kml(kml_dir, geojson_dir, csv_dir, logger):
        geojson_dir.mkdir(parents=True, exist_ok=True)
        (geojson_dir / "TEST-SURVEY-KML.geojson").write_text(
            "partial workspace geojson",
            encoding="utf-8",
        )
        raise ControlledKMLFailure("controlled kml failure")

    monkeypatch.setattr(rgb_module, "run_kml", failing_run_kml)

    with pytest.raises(ControlledKMLFailure, match="controlled kml failure"):
        pipeline.stage_kml_boundary()

    assert (
        pipeline.workspace_layout.boundary / "TEST-SURVEY-KML.geojson"
    ).read_text(encoding="utf-8") == "partial workspace geojson"
    assert legacy_geojson.read_text(encoding="utf-8") == "old geojson"
    assert legacy_csv.read_text(encoding="utf-8") == "old csv"
    assert "boundary_available" not in pipeline.state
    assert "boundary_geojson_path" not in pipeline.state


def test_webodm_task2_orthomosaic_exports_workspace_then_legacy(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = OrthomosaicExportFakeWebODM()
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
    )
    survey_path, image_path, _boundary_path = prepare_webodm_ortho_context(
        pipeline,
        temporary_path_layout,
    )
    enable_only_orthomosaic_export(pipeline)
    pipeline.skip_task1_webodm = True
    pipeline.skip_task2_webodm = False
    pipeline.skip_task4_webodm = True
    pipeline.export_name_overrides["task2"] = "task2-output"
    pipeline._stage_upload_cache = lambda **kwargs: (image_path, 1)

    result = pipeline.stage_webodm()

    workspace_ortho = pipeline.workspace_layout.webodm_ortho / "task2" / "task2-output.tif"
    legacy_ortho = survey_path / "ortho" / "task2-output.tif"
    export_calls = fake_webodm.calls_for("export_orthomosaic")

    assert len(export_calls) == 1
    assert export_calls[0].kwargs["out_dir"] == pipeline.workspace_layout.webodm_ortho / "task2"
    assert export_calls[0].kwargs["filename"] == "task2-output.tif"
    assert workspace_ortho.read_text(encoding="utf-8") == "workspace ortho task-0001"
    assert legacy_ortho.read_text(encoding="utf-8") == "workspace ortho task-0001"
    assert result["downloads"]["task2"]["orthomosaic"] == str(legacy_ortho)
    assert result["workspace"]["webodm_ortho"]["task2"] == str(workspace_ortho)
    assert result["published"]["webodm_ortho"]["task2"] == str(legacy_ortho)
    assert result["selected_orthomosaic"]["source_path"] == str(legacy_ortho)
    assert pipeline.state["selected_orthomosaic"]["source_path"] == str(legacy_ortho)

    for path_to_check in (
        workspace_ortho,
        legacy_ortho,
        repository.db_file,
    ):
        assert_within(path_to_check, temporary_path_layout.application_root)


def test_webodm_orthomosaic_export_failure_leaves_legacy_output_untouched(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = OrthomosaicExportFakeWebODM(fail_export=True)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
    )
    survey_path, image_path, _boundary_path = prepare_webodm_ortho_context(
        pipeline,
        temporary_path_layout,
    )
    enable_only_orthomosaic_export(pipeline)
    pipeline.skip_task1_webodm = True
    pipeline.skip_task2_webodm = False
    pipeline.skip_task4_webodm = True
    pipeline.export_name_overrides["task2"] = "task2-output"
    pipeline._stage_upload_cache = lambda **kwargs: (image_path, 1)

    legacy_ortho = survey_path / "ortho" / "task2-output.tif"
    legacy_ortho.parent.mkdir(parents=True)
    legacy_ortho.write_text("old legacy ortho", encoding="utf-8")

    with pytest.raises(
        ControlledWebODMExportFailure,
        match="controlled orthomosaic export failure",
    ):
        pipeline.stage_webodm()

    workspace_ortho = pipeline.workspace_layout.webodm_ortho / "task2" / "task2-output.tif"

    assert workspace_ortho.read_text(encoding="utf-8") == "workspace ortho task-0001"
    assert legacy_ortho.read_text(encoding="utf-8") == "old legacy ortho"
    assert "selected_orthomosaic" not in pipeline.state


def test_webodm_fallback_orthomosaic_exports_workspace_then_legacy(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = OrthomosaicExportFakeWebODM()
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
    )
    survey_path, _image_path, _boundary_path = prepare_webodm_ortho_context(
        pipeline,
        temporary_path_layout,
    )
    enable_only_orthomosaic_export(pipeline)
    reusable_upload = temporary_path_layout.upload_cache_dir / "reusable-webodm-upload"
    reusable_upload.mkdir(parents=True)
    pipeline._get_reusable_webodm_upload_folder = lambda **kwargs: reusable_upload
    pipeline.state["webodm"] = {
        "project_id": 100,
        "project_name": "TEST-SURVEY-ODM-ORTHO",
        "downloads": {},
    }

    result = pipeline.run_webodm_fallback_task(
        task_key="task4",
        fallback_reason="test_fallback",
    )

    export_calls = fake_webodm.calls_for("export_orthomosaic")
    assert len(export_calls) == 1
    filename = export_calls[0].kwargs["filename"]
    workspace_ortho = pipeline.workspace_layout.webodm_ortho / "task4" / filename
    legacy_ortho = survey_path / "ortho" / filename

    assert export_calls[0].kwargs["out_dir"] == pipeline.workspace_layout.webodm_ortho / "task4"
    assert workspace_ortho.read_text(encoding="utf-8") == "workspace ortho task-0001"
    assert legacy_ortho.read_text(encoding="utf-8") == "workspace ortho task-0001"
    assert result["downloads"]["orthomosaic"] == str(legacy_ortho)
    assert result["selected_orthomosaic"]["source_path"] == str(legacy_ortho)
    assert pipeline.state["webodm"]["workspace"]["webodm_ortho"]["task4"] == str(
        workspace_ortho
    )
    assert pipeline.state["webodm"]["published"]["webodm_ortho"]["task4"] == str(
        legacy_ortho
    )





def test_webodm_task2_remaining_exports_workspace_then_legacy(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = RemainingWebODMExportFake()
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
    )
    survey_path, image_path, _boundary_path = prepare_webodm_ortho_context(
        pipeline,
        temporary_path_layout,
    )
    enable_remaining_webodm_exports(pipeline)
    pipeline.skip_task1_webodm = True
    pipeline.skip_task2_webodm = False
    pipeline.skip_task4_webodm = True
    pipeline.export_name_overrides["task2"] = "task2-output"
    pipeline._stage_upload_cache = lambda **kwargs: (image_path, 1)

    result = pipeline.stage_webodm()

    workspace_3d_dir = pipeline.workspace_layout.webodm_3d / "task2"
    workspace_laz = workspace_3d_dir / "task2-output.laz"
    workspace_pcd = workspace_3d_dir / "task2-output.pcd"
    legacy_laz = survey_path / "3d" / "task2-output.laz"
    legacy_pcd = survey_path / "3d" / "task2-output.pcd"
    workspace_zip = pipeline.workspace_layout.webodm_odm / "task2" / "task2-output-all.zip"
    legacy_zip = survey_path / "odm" / "task2-output-all.zip"
    pointcloud_calls = fake_webodm.calls_for("export_pointcloud")
    zip_calls = fake_webodm.calls_for("download_all_assets_safe")

    assert len(pointcloud_calls) == 1
    assert pointcloud_calls[0].kwargs["out_dir"] == workspace_3d_dir
    assert pointcloud_calls[0].kwargs["laz_archive_name"] == "task2-output.laz"
    assert pointcloud_calls[0].kwargs["pcd_name"] == "task2-output.pcd"
    assert pointcloud_calls[0].kwargs["max_points"] == 123
    assert len(zip_calls) == 1
    assert zip_calls[0].args[2] == str(workspace_zip)
    assert workspace_laz.read_text(encoding="utf-8") == "workspace laz"
    assert workspace_pcd.read_text(encoding="utf-8") == "workspace pcd"
    assert legacy_laz.read_text(encoding="utf-8") == "workspace laz"
    assert legacy_pcd.read_text(encoding="utf-8") == "workspace pcd"
    assert workspace_zip.read_text(encoding="utf-8") == "workspace all assets"
    assert legacy_zip.read_text(encoding="utf-8") == "workspace all assets"
    assert result["downloads"]["task2"]["pointcloud_laz"] == str(legacy_laz)
    assert result["downloads"]["task2"]["pointcloud_pcd"] == str(legacy_pcd)
    assert result["downloads"]["task2"]["pointcloud_asset_type"] == "georeferenced_model.laz"
    assert result["downloads"]["task2"]["all_assets_zip"] == str(legacy_zip)
    assert result["workspace"]["webodm_3d"]["task2"]["laz"] == str(workspace_laz)
    assert result["workspace"]["webodm_3d"]["task2"]["pcd"] == str(workspace_pcd)
    assert result["published"]["webodm_3d"]["task2"]["laz"] == str(legacy_laz)
    assert result["published"]["webodm_3d"]["task2"]["pcd"] == str(legacy_pcd)
    assert result["workspace"]["webodm_odm"]["task2_all_assets_zip"] == str(workspace_zip)
    assert result["published"]["webodm_odm"]["task2_all_assets_zip"] == str(legacy_zip)

    for path_to_check in (
        workspace_laz,
        workspace_pcd,
        workspace_zip,
        legacy_laz,
        legacy_pcd,
        legacy_zip,
    ):
        assert_within(path_to_check, temporary_path_layout.application_root)


def test_webodm_pointcloud_failure_leaves_legacy_outputs_untouched(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    fake_webodm = RemainingWebODMExportFake(fail_pointcloud=True)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        fake_webodm,
    )
    survey_path, image_path, _boundary_path = prepare_webodm_ortho_context(
        pipeline,
        temporary_path_layout,
    )
    enable_remaining_webodm_exports(pipeline)
    pipeline.skip_task1_webodm = True
    pipeline.skip_task2_webodm = False
    pipeline.skip_task4_webodm = True
    pipeline.export_name_overrides["task2"] = "task2-output"
    pipeline._stage_upload_cache = lambda **kwargs: (image_path, 1)
    legacy_laz = survey_path / "3d" / "task2-output.laz"
    legacy_pcd = survey_path / "3d" / "task2-output.pcd"
    legacy_laz.parent.mkdir(parents=True)
    legacy_laz.write_text("old laz", encoding="utf-8")
    legacy_pcd.write_text("old pcd", encoding="utf-8")

    with pytest.raises(
        ControlledWebODMPointcloudFailure,
        match="controlled pointcloud failure",
    ):
        pipeline.stage_webodm()

    workspace_laz = pipeline.workspace_layout.webodm_3d / "task2" / "task2-output.laz"
    workspace_pcd = pipeline.workspace_layout.webodm_3d / "task2" / "task2-output.pcd"
    assert workspace_laz.read_text(encoding="utf-8") == "workspace laz"
    assert workspace_pcd.read_text(encoding="utf-8") == "workspace pcd"
    assert legacy_laz.read_text(encoding="utf-8") == "old laz"
    assert legacy_pcd.read_text(encoding="utf-8") == "old pcd"
def test_qgis_outputs_workspace_then_mirrors_legacy_paths(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_path, source_ortho, boundary_path = prepare_qgis_context(
        pipeline,
        temporary_path_layout,
    )
    configure_qgis_for_fake_tools(pipeline)
    FakeQGISTools.instances = []
    FakeQGISTools.fail_clip = False
    FakeQGISTools.fail_tiles = False
    monkeypatch.setattr(rgb_module, "QGISTools", FakeQGISTools)

    legacy_clipped = (
        survey_path / "qgis" / "clipped" / "ortho" / "orthomosaic-clipped--xcb-t2.tif"
    )
    legacy_clipped.parent.mkdir(parents=True)
    legacy_clipped.write_text("old clipped", encoding="utf-8")
    legacy_tiles = survey_path / "tiles" / "ortho" / "round-corners"
    legacy_stale_tile = legacy_tiles / "stale.png"
    legacy_stale_tile.parent.mkdir(parents=True)
    legacy_stale_tile.write_text("old tile", encoding="utf-8")

    result = pipeline.stage_qgis()

    workspace_clipped = (
        pipeline.workspace_layout.qgis_clipped_ortho / "orthomosaic-clipped--xcb-t2.tif"
    )
    workspace_tile = pipeline.workspace_layout.qgis_tiles_round / "12" / "345" / "678.png"
    legacy_tile = legacy_tiles / "12" / "345" / "678.png"
    fake_tools = FakeQGISTools.instances[-1]

    assert [call["method"] for call in fake_tools.calls] == [
        "clip_raster_by_mask",
        "generate_tiles",
    ]
    assert fake_tools.calls[0]["input_tif"] == source_ortho
    assert fake_tools.calls[0]["mask_geojson"] == boundary_path
    assert fake_tools.calls[0]["output_tif"] == workspace_clipped
    assert fake_tools.calls[0]["dst_nodata"] == 0.0
    assert fake_tools.calls[0]["local_staging_dir"] is None
    assert fake_tools.calls[1]["input_tif"] == workspace_clipped
    assert fake_tools.calls[1]["output_dir"] == pipeline.workspace_layout.qgis_tiles_round
    assert fake_tools.calls[1]["clean"] is True
    assert fake_tools.calls[1]["resume"] is False

    assert workspace_clipped.read_text(encoding="utf-8") == "workspace clipped ortho"
    assert legacy_clipped.read_text(encoding="utf-8") == "workspace clipped ortho"
    assert workspace_tile.read_text(encoding="utf-8") == "workspace tile"
    assert legacy_tile.read_text(encoding="utf-8") == "workspace tile"
    assert not legacy_stale_tile.exists()
    assert result["clip"]["output"] == str(legacy_clipped)
    assert result["tiles"]["output_dir"] == str(legacy_tiles)
    assert result["selected_orthomosaic"]["clipped_path"] == str(legacy_clipped)
    assert result["selected_orthomosaic"]["tiles_dir"] == str(legacy_tiles)
    assert result["workspace"]["qgis_clipped_ortho"] == str(workspace_clipped)
    assert result["workspace"]["tiles_dir"] == str(pipeline.workspace_layout.qgis_tiles_round)
    assert result["published"]["qgis_clipped_ortho"] == str(legacy_clipped)
    assert result["published"]["tiles_dir"] == str(legacy_tiles)
    assert pipeline.state["selected_orthomosaic"]["tiles_dir"] == str(legacy_tiles)

    for path_to_check in (
        workspace_clipped,
        workspace_tile,
        legacy_clipped,
        legacy_tile,
    ):
        assert_within(path_to_check, temporary_path_layout.application_root)


def test_qgis_clip_failure_leaves_legacy_outputs_untouched(
    monkeypatch,
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_path, source_ortho, _boundary_path = prepare_qgis_context(
        pipeline,
        temporary_path_layout,
    )
    configure_qgis_for_fake_tools(pipeline)
    FakeQGISTools.instances = []
    FakeQGISTools.fail_clip = True
    FakeQGISTools.fail_tiles = False
    monkeypatch.setattr(rgb_module, "QGISTools", FakeQGISTools)

    legacy_clipped = (
        survey_path / "qgis" / "clipped" / "ortho" / "orthomosaic-clipped--xcb-t2.tif"
    )
    legacy_clipped.parent.mkdir(parents=True)
    legacy_clipped.write_text("old clipped", encoding="utf-8")
    legacy_tiles = survey_path / "tiles" / "ortho" / "round-corners"
    legacy_tile = legacy_tiles / "keep.png"
    legacy_tile.parent.mkdir(parents=True)
    legacy_tile.write_text("old tile", encoding="utf-8")

    with pytest.raises(ControlledQGISFailure, match="controlled qgis clip failure"):
        pipeline.stage_qgis()

    workspace_clipped = (
        pipeline.workspace_layout.qgis_clipped_ortho / "orthomosaic-clipped--xcb-t2.tif"
    )
    assert workspace_clipped.read_text(encoding="utf-8") == "workspace clipped ortho"
    assert legacy_clipped.read_text(encoding="utf-8") == "old clipped"
    assert legacy_tile.read_text(encoding="utf-8") == "old tile"
    assert pipeline.state["selected_orthomosaic"]["source_path"] == str(source_ortho)
    assert "clipped_path" not in pipeline.state["selected_orthomosaic"]

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


def test_publication_dry_run_plans_workspace_backed_mixed_artifacts_without_activation(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_id = "TEST-SURVEY-PUBLISH-PLAN"
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    pipeline._set_survey_artifact_context(survey_id, survey_path)

    workspace_geojson = pipeline.workspace_layout.boundary / f"{survey_id}.geojson"
    workspace_geojson.parent.mkdir(parents=True, exist_ok=True)
    workspace_geojson.write_text('{"type":"FeatureCollection"}', encoding="utf-8")
    published_geojson = survey_path / "boundary" / workspace_geojson.name
    published_geojson.parent.mkdir(parents=True, exist_ok=True)
    published_geojson.write_text("legacy geojson remains untouched", encoding="utf-8")

    workspace_ortho = pipeline.workspace_layout.webodm_ortho / "task2" / "orthomosaic--xcb-t2.tif"
    workspace_ortho.parent.mkdir(parents=True, exist_ok=True)
    workspace_ortho.write_text("workspace ortho", encoding="utf-8")
    published_ortho = survey_path / "ortho" / workspace_ortho.name
    published_ortho.parent.mkdir(parents=True, exist_ok=True)
    published_ortho.write_text("legacy ortho remains untouched", encoding="utf-8")

    workspace_clipped = pipeline.workspace_layout.qgis_clipped_ortho / "orthomosaic-clipped--xcb-t2.tif"
    workspace_clipped.parent.mkdir(parents=True, exist_ok=True)
    workspace_clipped.write_text("workspace clipped", encoding="utf-8")
    published_clipped = survey_path / "qgis" / "clipped" / "ortho" / workspace_clipped.name
    published_clipped.parent.mkdir(parents=True, exist_ok=True)
    published_clipped.write_text("legacy clipped remains untouched", encoding="utf-8")

    workspace_tiles = pipeline.workspace_layout.qgis_tiles_round
    workspace_tile = workspace_tiles / "12" / "345" / "678.png"
    workspace_tile.parent.mkdir(parents=True, exist_ok=True)
    workspace_tile.write_text("workspace tile", encoding="utf-8")
    published_tiles = survey_path / "tiles" / "ortho" / "round-corners"
    published_tile = published_tiles / "12" / "345" / "678.png"
    published_tile.parent.mkdir(parents=True, exist_ok=True)
    published_tile.write_text("legacy tile remains untouched", encoding="utf-8")

    pipeline.state.update(
        {
            "data_segregation": {
                "survey_id": survey_id,
                "survey_path": str(survey_path),
            },
            "kml_boundary": {
                "workspace": {
                    "processed_files": [{"geojson": str(workspace_geojson)}],
                },
                "published": {
                    "processed_files": [{"geojson": str(published_geojson)}],
                },
            },
            "webodm": {
                "workspace": {"webodm_ortho": {"task2": str(workspace_ortho)}},
                "published": {"webodm_ortho": {"task2": str(published_ortho)}},
            },
            "qgis": {
                "workspace": {
                    "qgis_clipped_ortho": str(workspace_clipped),
                    "tiles_dir": str(workspace_tiles),
                },
                "published": {
                    "qgis_clipped_ortho": str(published_clipped),
                    "tiles_dir": str(published_tiles),
                },
            },
        }
    )

    plan = pipeline.plan_publication_dry_run()

    assert plan["status"] == "planned"
    assert plan["activation_enabled"] is False
    assert plan["survey_id"] == survey_id
    assert plan["publication_manifest"] == str(survey_path / "publication.json")
    assert plan["artifact_count"] == 4
    artifacts = {artifact["logical_name"]: artifact for artifact in plan["artifacts"]}
    assert artifacts["kml_boundary.published.processed_files.geojson"]["kind"] == "file"
    assert artifacts["webodm.published.webodm_ortho.task2"]["published_relative_path"] == str(
        Path("ortho") / workspace_ortho.name
    )
    assert artifacts["qgis.published.qgis_clipped_ortho"]["kind"] == "file"
    assert artifacts["qgis.published.tiles_dir"]["kind"] == "directory"
    assert not (survey_path / "publication.json").exists()
    assert published_geojson.read_text(encoding="utf-8") == "legacy geojson remains untouched"
    assert published_ortho.read_text(encoding="utf-8") == "legacy ortho remains untouched"
    assert published_clipped.read_text(encoding="utf-8") == "legacy clipped remains untouched"
    assert published_tile.read_text(encoding="utf-8") == "legacy tile remains untouched"



def test_prepare_publication_staging_writes_workspace_manifest_without_activation(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_id = "TEST-SURVEY-PUBLISH-STAGED"
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    pipeline._set_survey_artifact_context(survey_id, survey_path)

    workspace_geojson = pipeline.workspace_layout.boundary / "boundary.geojson"
    workspace_geojson.parent.mkdir(parents=True, exist_ok=True)
    workspace_geojson.write_text("workspace geojson", encoding="utf-8")
    published_geojson = survey_path / "boundary" / "boundary.geojson"
    published_geojson.parent.mkdir(parents=True, exist_ok=True)
    published_geojson.write_text("legacy geojson remains untouched", encoding="utf-8")

    workspace_tiles = pipeline.workspace_layout.qgis_tiles_round
    workspace_tile = workspace_tiles / "12" / "345" / "678.png"
    workspace_tile.parent.mkdir(parents=True, exist_ok=True)
    workspace_tile.write_text("workspace tile", encoding="utf-8")
    published_tiles = survey_path / "tiles" / "ortho" / "round-corners"
    published_tile = published_tiles / "12" / "345" / "678.png"
    published_tile.parent.mkdir(parents=True, exist_ok=True)
    published_tile.write_text("legacy tile remains untouched", encoding="utf-8")

    pipeline.state.update(
        {
            "data_segregation": {
                "survey_id": survey_id,
                "survey_path": str(survey_path),
            },
            "kml_boundary": {
                "workspace": {
                    "processed_files": [{"geojson": str(workspace_geojson)}],
                },
                "published": {
                    "processed_files": [{"geojson": str(published_geojson)}],
                },
            },
            "qgis": {
                "workspace": {"tiles_dir": str(workspace_tiles)},
                "published": {"tiles_dir": str(published_tiles)},
            },
        }
    )

    result = pipeline.prepare_publication_staging()

    staged_manifest = Path(result["staged_manifest"])
    assert result["status"] == "staged"
    assert result["activation_enabled"] is False
    assert result["artifact_count"] == 2
    assert staged_manifest == pipeline.workspace_layout.publish / "staged" / "publication.json"
    assert staged_manifest.exists()
    assert not (survey_path / "publication.json").exists()

    manifest = json.loads(staged_manifest.read_text(encoding="utf-8"))
    assert manifest["status"] == "staged"
    assert manifest["run_id"] == pipeline.run_id
    assert manifest["survey_id"] == survey_id
    assert manifest["published_root"] == str(survey_path)
    artifact_records = {
        artifact["logical_name"]: artifact for artifact in manifest["artifacts"]
    }
    geojson_record = artifact_records["kml_boundary.published.processed_files.geojson"]
    tiles_record = artifact_records["qgis.published.tiles_dir"]
    assert geojson_record["kind"] == "file"
    assert tiles_record["kind"] == "directory"
    assert Path(geojson_record["staged_path"]).read_text(encoding="utf-8") == "workspace geojson"
    assert (Path(tiles_record["staged_path"]) / "12" / "345" / "678.png").read_text(
        encoding="utf-8"
    ) == "workspace tile"
    assert published_geojson.read_text(encoding="utf-8") == "legacy geojson remains untouched"
    assert published_tile.read_text(encoding="utf-8") == "legacy tile remains untouched"

def test_prepare_publication_staging_blocks_empty_plan(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_id = "TEST-SURVEY-PUBLISH-EMPTY"
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    pipeline._set_survey_artifact_context(survey_id, survey_path)
    pipeline.state.update(
        {
            "data_segregation": {
                "survey_id": survey_id,
                "survey_path": str(survey_path),
            },
        }
    )

    result = pipeline.prepare_publication_staging()

    assert result["status"] == "blocked"
    assert result["artifact_count"] == 0
    assert result["blocked_reasons"] == ["publication plan contains no artifacts"]
    assert not (pipeline.workspace_layout.publish / "staged" / "publication.json").exists()
    assert not (survey_path / "publication.json").exists()

def _seed_simple_publication_state(pipeline: RGBPipeline, temporary_path_layout, survey_id: str):
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    pipeline._set_survey_artifact_context(survey_id, survey_path)

    workspace_geojson = pipeline.workspace_layout.boundary / "boundary.geojson"
    workspace_geojson.parent.mkdir(parents=True, exist_ok=True)
    workspace_geojson.write_text("workspace geojson", encoding="utf-8")
    published_geojson = survey_path / "boundary" / "boundary.geojson"
    published_geojson.parent.mkdir(parents=True, exist_ok=True)
    published_geojson.write_text("legacy geojson", encoding="utf-8")

    workspace_tiles = pipeline.workspace_layout.qgis_tiles_round
    workspace_tile = workspace_tiles / "12" / "345" / "678.png"
    workspace_tile.parent.mkdir(parents=True, exist_ok=True)
    workspace_tile.write_text("workspace tile", encoding="utf-8")
    published_tiles = survey_path / "tiles" / "ortho" / "round-corners"
    published_tile = published_tiles / "12" / "345" / "678.png"
    published_tile.parent.mkdir(parents=True, exist_ok=True)
    published_tile.write_text("legacy tile", encoding="utf-8")

    pipeline.state.update(
        {
            "data_segregation": {
                "survey_id": survey_id,
                "survey_path": str(survey_path),
            },
            "kml_boundary": {
                "workspace": {
                    "processed_files": [{"geojson": str(workspace_geojson)}],
                },
                "published": {
                    "processed_files": [{"geojson": str(published_geojson)}],
                },
            },
            "qgis": {
                "workspace": {"tiles_dir": str(workspace_tiles)},
                "published": {"tiles_dir": str(published_tiles)},
            },
        }
    )
    return {
        "survey_path": survey_path,
        "published_geojson": published_geojson,
        "published_tile": published_tile,
    }


def test_activate_publication_explicit_blocks_without_exact_confirmation(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    paths = _seed_simple_publication_state(
        pipeline,
        temporary_path_layout,
        "TEST-SURVEY-PUBLISH-CONFIRM",
    )
    pipeline.prepare_publication_staging()

    result = pipeline.activate_publication_explicit(confirmation="publish please")

    assert result["status"] == "blocked"
    assert result["activation_enabled"] is True
    assert "confirmation must exactly match" in result["blocked_reasons"][0]
    assert not (paths["survey_path"] / "publication.json").exists()
    assert paths["published_geojson"].read_text(encoding="utf-8") == "legacy geojson"
    assert paths["published_tile"].read_text(encoding="utf-8") == "legacy tile"


def test_activate_publication_explicit_blocks_missing_staged_manifest(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    paths = _seed_simple_publication_state(
        pipeline,
        temporary_path_layout,
        "TEST-SURVEY-PUBLISH-MISSING-STAGED",
    )

    result = pipeline.activate_publication_explicit(
        confirmation=pipeline.expected_publication_confirmation(),
    )

    assert result["status"] == "blocked"
    assert "staged publication manifest is missing" in result["blocked_reasons"][0]
    assert not (paths["survey_path"] / "publication.json").exists()
    assert paths["published_geojson"].read_text(encoding="utf-8") == "legacy geojson"
    assert paths["published_tile"].read_text(encoding="utf-8") == "legacy tile"


def test_activate_publication_explicit_publishes_staged_mixed_set(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    paths = _seed_simple_publication_state(
        pipeline,
        temporary_path_layout,
        "TEST-SURVEY-PUBLISH-ACTIVATE",
    )
    pipeline.prepare_publication_staging()

    result = pipeline.activate_publication_explicit(
        confirmation=pipeline.expected_publication_confirmation(),
    )

    publication_manifest = paths["survey_path"] / "publication.json"
    assert result["status"] == "activated"
    assert result["activation_enabled"] is True
    assert result["publication_manifest"] == str(publication_manifest)
    assert publication_manifest.exists()
    manifest = json.loads(publication_manifest.read_text(encoding="utf-8"))
    assert manifest["status"] == "published"
    assert manifest["run_id"] == pipeline.run_id
    assert paths["published_geojson"].read_text(encoding="utf-8") == "workspace geojson"
    assert paths["published_tile"].read_text(encoding="utf-8") == "workspace tile"
    assert not (paths["survey_path"] / ".publication.lock").exists()


def test_activate_publication_explicit_existing_lock_blocks_before_mutation(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    paths = _seed_simple_publication_state(
        pipeline,
        temporary_path_layout,
        "TEST-SURVEY-PUBLISH-LOCKED",
    )
    pipeline.prepare_publication_staging()
    acquire_publication_lock(
        published_root=paths["survey_path"],
        run_id="other-run",
        survey_id="TEST-SURVEY-PUBLISH-LOCKED",
        owner_token="other-owner",
        created_at="2026-07-30T00:00:00Z",
    )

    with pytest.raises(PublicationLockedError):
        pipeline.activate_publication_explicit(
            confirmation=pipeline.expected_publication_confirmation(),
        )

    assert paths["published_geojson"].read_text(encoding="utf-8") == "legacy geojson"
    assert paths["published_tile"].read_text(encoding="utf-8") == "legacy tile"
    assert not (paths["survey_path"] / "publication.json").exists()

def test_publication_dry_run_blocks_non_workspace_source(
    temporary_path_layout,
    sample_dataset_dir,
):
    repository = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(
        temporary_path_layout,
        sample_dataset_dir,
        repository,
        FakeWebODM(),
    )
    survey_id = "TEST-SURVEY-PUBLISH-BLOCKED"
    survey_path = temporary_path_layout.surveys_dir / "2026" / survey_id / "rgb"
    pipeline._set_survey_artifact_context(survey_id, survey_path)
    legacy_source = survey_path / "ortho" / "legacy-only.tif"
    legacy_source.parent.mkdir(parents=True, exist_ok=True)
    legacy_source.write_text("legacy source", encoding="utf-8")

    pipeline.state.update(
        {
            "data_segregation": {
                "survey_id": survey_id,
                "survey_path": str(survey_path),
            },
            "webodm": {
                "workspace": {"webodm_ortho": {"task2": str(legacy_source)}},
                "published": {"webodm_ortho": {"task2": str(legacy_source)}},
            },
        }
    )

    plan = pipeline.plan_publication_dry_run()

    assert plan["status"] == "blocked"
    assert plan["artifact_count"] == 0
    assert "not run-workspace owned" in plan["blocked_reasons"][0]
    assert not (survey_path / "publication.json").exists()

    staging_result = pipeline.prepare_publication_staging()

    assert staging_result["status"] == "blocked"
    assert staging_result["artifact_count"] == 0
    assert "not run-workspace owned" in staging_result["blocked_reasons"][0]
    assert not (pipeline.workspace_layout.publish / "staged" / "publication.json").exists()
