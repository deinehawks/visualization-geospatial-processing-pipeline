from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional
import time
import logging

from shared import get_logger, PipelineRepo, db_path, StageRunner
from modules import run_kml, WebODMProcessor, run_filter
from modules.data_segregation import run as run_data_segregation


class RGBPipeline:
    def __init__(
        self,
        base_dir: Path,
        config: Dict[str, Any],
        *,
        source_dir: Path,
        surveys_root: Path,
        year: int,
    ):
        self.base_dir = Path(base_dir)
        self.config = config

        self.source_dir = Path(source_dir)
        self.surveys_root = Path(surveys_root)
        self.year = year

        # survey_id will be determined by data_segregation
        self.survey_id: Optional[str] = None

        self.logs_dir = self.base_dir / "data" / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.loggers = {
            "pipeline": get_logger("rgb.pipeline", self.logs_dir / "pipeline.log"),
            "cross_run_filter": get_logger("rgb.cross_run_filter", self.logs_dir / "cross_run_filter.log"),
            "kml": get_logger("rgb.kml", self.logs_dir / "kml.log"),
            "webodm": get_logger("rgb.webodm", self.logs_dir / "webodm.log"),
            "segregation": get_logger("rgb.data_segregation", self.logs_dir / "data_segregation.log"),
        }

        self.state: Dict[str, Any] = {}

        # DB
        self.repo = PipelineRepo(db_path(self.base_dir))
        self.runner = StageRunner(
            repo=self.repo,
            survey_id="pending",  # will update after segregation
            logger=self.loggers["pipeline"],
        )

    # ============================================================
    # STAGES
    # ============================================================

    def stage_data_segregation(self) -> Dict[str, Any]:
        logger = self.loggers["segregation"]
        logger.info("Stage: Data Segregation")

        summary = run_data_segregation(
            source_dir=self.source_dir,
            surveys_root=self.surveys_root,
            year=self.year,
            logger=logger,
        )

        # Update survey_id after generation
        self.survey_id = summary["survey_id"]

        # Now update StageRunner survey_id
        self.runner.survey_id = self.survey_id

        # Update working directories
        survey_rgb_path = Path(summary["survey_path"])
        self.staged_dir = survey_rgb_path / "images"
        self.intermediate_dir = survey_rgb_path / "boundary"

        return summary

    def stage_cross_run_image_filter(self) -> Dict[str, Any]:
        logger = self.loggers["cross_run_filter"]
        logger.info("Stage: Cross-run image filter")

        return run_filter(
            input_dir=self.surveys_root / str(self.year) / self.survey_id / "rgb" / "images" / "path_raw",
            output_dir=self.surveys_root / str(self.year) / self.survey_id / "rgb" / "images" / "path",
            logger=logger,
            max_gap=int(self.config.get("cross_run_filter", {}).get("max_gap", 10)),
            cross_run_window=int(self.config.get("cross_run_filter", {}).get("window", 3)),
        )

    def stage_kml_boundary(self) -> Dict[str, Any]:
        logger = self.loggers["kml"]
        logger.info("Stage: KML Boundary Setter")

        rgb_path = self.surveys_root / str(self.year) / self.survey_id / "rgb"

        return run_kml(
            kml_dir=rgb_path / "boundary",
            geojson_dir=rgb_path / "boundary",
            csv_dir=rgb_path / "boundary",
            logger=logger,
        )

    def stage_webodm(self) -> Dict[str, Any]:
        logger = self.loggers["webodm"]
        logger.info("Stage: WebODM Processing")

        webodm_cfg = self.config["webodm"]

        processor = WebODMProcessor(
            url=webodm_cfg["url"],
            username=webodm_cfg["username"],
            password=webodm_cfg["password"],
            logger=logger,
        )

        project_id = processor.create_project(
            name=f"{self.survey_id}_RGB",
            description="RGB automated processing",
        )

        rgb_path = self.surveys_root / str(self.year) / self.survey_id / "rgb"
        image_folder = rgb_path / "images" / "path"

        task1_id = processor.create_task_with_images(
            project_id=project_id,
            name="Unbounded Orthomosaic",
            image_folder=str(image_folder),
            options=webodm_cfg.get("task1_options", {}),
        )

        t1_success, t1_runtime, _ = processor.wait_for_completion(project_id, task1_id)

        geojson_files = list((rgb_path / "boundary").glob("*.geojson"))
        if not geojson_files:
            raise FileNotFoundError("No boundary GeoJSON found")

        boundary_geojson = geojson_files[0].read_text(encoding="utf-8")

        task2_options = dict(webodm_cfg.get("task2_options", {}))
        task2_options["boundary"] = boundary_geojson

        task2_id = processor.create_task_with_images(
            project_id=project_id,
            name="Bounded Orthomosaic",
            image_folder=str(image_folder),
            options=task2_options,
        )

        t2_success, t2_runtime, _ = processor.wait_for_completion(project_id, task2_id)

        return {
            "project_id": project_id,
            "task1": {"id": task1_id, "success": t1_success, "runtime_seconds": t1_runtime},
            "task2": {"id": task2_id, "success": t2_success, "runtime_seconds": t2_runtime},
        }

    def stage_quality_gate(self) -> Dict[str, Any]:
        self.loggers["pipeline"].info("Stage: Quality Gate Check")
        return {"passed": True}

    # ============================================================
    # RUN
    # ============================================================

    def run(self, *, resume: bool = True, force_stages: Optional[set[str]] = None) -> Dict[str, Any]:
        pipeline_logger = self.loggers["pipeline"]
        pipeline_logger.info("Starting RGB Pipeline")

        total_start = time.perf_counter()
        force_stages = force_stages or set()

        try:
            # DATA SEGREGATION FIRST
            self.runner.run(
                "data_segregation",
                self.stage_data_segregation,
                output_key="data_segregation",
                state=self.state,
                force=("data_segregation" in force_stages) or (not resume),
            )

            # mark survey running AFTER ID exists
            self.repo.upsert_survey_running(self.survey_id)

            self.runner.run(
                "cross_run_filter",
                self.stage_cross_run_image_filter,
                output_key="cross_run_filter",
                state=self.state,
                force=("cross_run_filter" in force_stages) or (not resume),
            )

            self.runner.run(
                "kml_boundary",
                self.stage_kml_boundary,
                output_key="kml",
                state=self.state,
                force=("kml_boundary" in force_stages) or (not resume),
            )

            self.runner.run(
                "webodm",
                self.stage_webodm,
                output_key="webodm",
                state=self.state,
                force=("webodm" in force_stages) or (not resume),
            )

            self.runner.run(
                "quality_gate",
                self.stage_quality_gate,
                output_key="quality_gate",
                state=self.state,
                force=("quality_gate" in force_stages) or (not resume),
            )

            self.state["success"] = True

            total_runtime = time.perf_counter() - total_start
            self.repo.mark_survey_finished(self.survey_id, success=True, total_runtime_seconds=total_runtime)

        except Exception as e:
            self.state["success"] = False
            self.state["error"] = str(e)

            total_runtime = time.perf_counter() - total_start
            if self.survey_id:
                self.repo.mark_survey_finished(self.survey_id, success=False, total_runtime_seconds=total_runtime)

            pipeline_logger.exception("RGB Pipeline failed")

        return self.state
