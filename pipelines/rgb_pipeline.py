"""
RGB Survey Pipeline
Implements workflow based on automation flowchart.

Current implemented stages:
- Cross-run image filter
- KML boundary setter
- WebODM task 1 (unbounded) + task 2 (bounded)

Future stages (placeholders):
- Quality gate automation
- QGIS processing
- Object detection
- Tile generation
- Database upload
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional
import logging
from logging import get_logger

from modules import run_kml, WebODMProcessor, run_filter



class RGBPipeline:
    def __init__(self, survey_id: str, base_dir: Path, config: Dict[str, Any]):
        self.survey_id = survey_id
        self.base_dir = Path(base_dir)
        self.config = config

        # Directories (per survey)
        self.raw_dir = self.base_dir / "data" / "raw" / survey_id
        self.staged_dir = self.base_dir / "data" / "staged" / survey_id
        self.intermediate_dir = self.base_dir / "data" / "intermediate" / survey_id
        self.final_dir = self.base_dir / "data" / "final" / survey_id

        self.logs_dir = self.base_dir / "data" / "logs" / survey_id
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.loggers = self._init_loggers()
        self.state: Dict[str, Any] = {}

    # -----------------------------
    # Logger Initialization
    # -----------------------------

    def _init_loggers(self) -> Dict[str, logging.Logger]:
        return {
            "pipeline": get_logger(
                name=f"{self.survey_id}.pipeline",
                log_file=self.logs_dir / "pipeline.log",
            ),
            "cross_run_filter": get_logger(
                name=f"{self.survey_id}.cross_run_filter",
                log_file=self.logs_dir / "cross_run_filter.log",
            ),
            "kml": get_logger(
                name=f"{self.survey_id}.kml",
                log_file=self.logs_dir / "kml.log",
            ),
            "webodm": get_logger(
                name=f"{self.survey_id}.webodm",
                log_file=self.logs_dir / "webodm.log",
            ),
        }

    # ============================================================
    # PIPELINE STAGES
    # ============================================================

    def stage_cross_run_image_filter(self) -> None:
        """
        Input:  data/staged/<survey_id>/images_raw
        Output: data/staged/<survey_id>/images (filtered, canonical)
        """
        logger = self.loggers["cross_run_filter"]
        logger.info("Stage: Cross-run image filter")

        input_dir = self.staged_dir / "images_raw"
        output_dir = self.staged_dir / "images"

        filter_summary = run_filter(
            input_dir=input_dir,
            output_dir=output_dir,
            logger=logger,
            max_gap=int(self.config.get("cross_run_filter", {}).get("max_gap", 10)),
            cross_run_window=int(self.config.get("cross_run_filter", {}).get("window", 3)),
        )

        self.state["cross_run_filter"] = filter_summary

    # ------------------------------------------------------------

    def stage_kml_boundary(self) -> None:
        """
        Input:  data/staged/<survey_id>/kml/*.kml
        Output: data/intermediate/<survey_id>/geojson + csv
        """
        logger = self.loggers["kml"]
        logger.info("Stage: KML Boundary Setter")

        summary = run_kml(
            kml_dir=self.staged_dir / "kml",
            geojson_dir=self.intermediate_dir / "geojson",
            csv_dir=self.intermediate_dir / "csv",
            logger=logger,
        )

        self.state["kml"] = summary

    # ------------------------------------------------------------

    def stage_webodm(self) -> None:
        """
        Uses filtered images and boundary GeoJSON.
        Creates 2 tasks:
        - Task 1: Unbounded orthomosaic
        - Task 2: Bounded orthomosaic (boundary from geojson)
        """
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

        image_folder = self.staged_dir / "images"  # canonical after filter

        # -------- Task 1 (Unbounded) --------
        task1_id = processor.create_task_with_images(
            project_id=project_id,
            name="Unbounded Orthomosaic",
            image_folder=str(image_folder),
            options=webodm_cfg.get("task1_options", {}),
        )
        t1_success, t1_runtime, t1_info = processor.wait_for_completion(project_id, task1_id)

        # -------- Task 2 (Bounded) --------
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
        t2_success, t2_runtime, t2_info = processor.wait_for_completion(project_id, task2_id)

        self.state["webodm"] = {
            "project_id": project_id,
            "task1": {"id": task1_id, "success": t1_success, "runtime_seconds": t1_runtime},
            "task2": {"id": task2_id, "success": t2_success, "runtime_seconds": t2_runtime},
        }

    # ------------------------------------------------------------

    def stage_quality_gate(self) -> None:
        logger = self.loggers["pipeline"]
        logger.info("Stage: Quality Gate Check (TODO)")

        # For now: always pass
        self.state["quality_gate"] = {"passed": True}

    # ------------------------------------------------------------

    def stage_qgis_processing(self) -> None:
        self.loggers["pipeline"].info("Stage: QGIS Processing (TODO)")

    def stage_object_detection(self) -> None:
        self.loggers["pipeline"].info("Stage: Object Detection (TODO)")

    def stage_tile_generation(self) -> None:
        self.loggers["pipeline"].info("Stage: Tile Generation (TODO)")

    def stage_database_upload(self) -> None:
        self.loggers["pipeline"].info("Stage: Database Upload (TODO)")

    # ============================================================

    def run(self) -> Dict[str, Any]:
        pipeline_logger = self.loggers["pipeline"]
        pipeline_logger.info(f"Starting RGB Pipeline for {self.survey_id}")

        try:
            self.stage_cross_run_image_filter()
            self.stage_kml_boundary()
            self.stage_webodm()
            self.stage_quality_gate()
            self.stage_qgis_processing()
            self.stage_object_detection()
            self.stage_tile_generation()
            self.stage_database_upload()

            pipeline_logger.info("RGB Pipeline completed successfully")
            self.state["success"] = True

        except Exception as e:
            pipeline_logger.exception("RGB Pipeline failed")
            self.state["success"] = False
            self.state["error"] = str(e)

        return self.state
