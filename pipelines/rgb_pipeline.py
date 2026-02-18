from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional
import time

import logging
from shared import get_logger, PipelineRepo, db_path, StageRunner
from modules import run_kml, WebODMProcessor, run_filter


class RGBPipeline:
    def __init__(self, survey_id: str, base_dir: Path, config: Dict[str, Any]):
        self.survey_id = survey_id
        self.base_dir = Path(base_dir)
        self.config = config

        self.raw_dir = self.base_dir / "data" / "raw" / survey_id
        self.staged_dir = self.base_dir / "data" / "staged" / survey_id
        self.intermediate_dir = self.base_dir / "data" / "intermediate" / survey_id
        self.final_dir = self.base_dir / "data" / "final" / survey_id

        self.logs_dir = self.base_dir / "data" / "logs" / survey_id
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.loggers = self._init_loggers()
        self.state: Dict[str, Any] = {}

        # DB repo + stage runner
        self.repo = PipelineRepo(db_path(self.base_dir))
        self.runner = StageRunner(
            repo=self.repo,
            survey_id=self.survey_id,
            logger=self.loggers["pipeline"],
        )

    def _init_loggers(self) -> Dict[str, logging.Logger]:
        return {
            "pipeline": get_logger(f"{self.survey_id}.pipeline", self.logs_dir / "pipeline.log"),
            "cross_run_filter": get_logger(f"{self.survey_id}.cross_run_filter", self.logs_dir / "cross_run_filter.log"),
            "kml": get_logger(f"{self.survey_id}.kml", self.logs_dir / "kml.log"),
            "webodm": get_logger(f"{self.survey_id}.webodm", self.logs_dir / "webodm.log"),
        }

    # ---------------- STAGES (return dicts if possible) ----------------

    def stage_cross_run_image_filter(self) -> Dict[str, Any]:
        logger = self.loggers["cross_run_filter"]
        logger.info("Stage: Cross-run image filter")

        return run_filter(
            input_dir=self.staged_dir / "images_raw",
            output_dir=self.staged_dir / "images",
            logger=logger,
            max_gap=int(self.config.get("cross_run_filter", {}).get("max_gap", 10)),
            cross_run_window=int(self.config.get("cross_run_filter", {}).get("window", 3)),
        )

    def stage_kml_boundary(self) -> Dict[str, Any]:
        logger = self.loggers["kml"]
        logger.info("Stage: KML Boundary Setter")

        return run_kml(
            kml_dir=self.staged_dir / "kml",
            geojson_dir=self.intermediate_dir / "geojson",
            csv_dir=self.intermediate_dir / "csv",
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

        image_folder = self.staged_dir / "images"

        task1_id = processor.create_task_with_images(
            project_id=project_id,
            name="Unbounded Orthomosaic",
            image_folder=str(image_folder),
            options=webodm_cfg.get("task1_options", {}),
        )
        t1_success, t1_runtime, _ = processor.wait_for_completion(project_id, task1_id)

        geojson_dir = self.intermediate_dir / "geojson"
        geojson_files = sorted(list(geojson_dir.glob("*.geojson")))
        if not geojson_files:
            raise FileNotFoundError(f"No .geojson boundary found in: {geojson_dir}")

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
        self.loggers["pipeline"].info("Stage: Quality Gate Check (TODO)")
        return {"passed": True}

    # ---------------- RUN ----------------

    def run(self, *, resume: bool = True, force_stages: Optional[set[str]] = None) -> Dict[str, Any]:
        pipeline_logger = self.loggers["pipeline"]
        pipeline_logger.info(f"Starting RGB Pipeline for {self.survey_id}")

        total_start = time.perf_counter()
        self.repo.upsert_survey_running(self.survey_id)

        force_stages = force_stages or set()

        try:
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
            pipeline_logger.info("RGB Pipeline completed successfully")

            total_runtime = time.perf_counter() - total_start
            self.repo.mark_survey_finished(self.survey_id, success=True, total_runtime_seconds=total_runtime)

        except Exception as e:
            self.state["success"] = False
            self.state["error"] = str(e)

            total_runtime = time.perf_counter() - total_start
            self.repo.mark_survey_finished(self.survey_id, success=False, total_runtime_seconds=total_runtime)

            pipeline_logger.exception("RGB Pipeline failed")

        return self.state

