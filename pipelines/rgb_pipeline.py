"""
RGB Survey Pipeline
Implements full workflow based on automation flowchart.
"""

from pathlib import Path
from typing import Dict, Any
from shared.logging import get_logger

from modules.kml_boundary_setter.kml_boundary_setter import run as run_kml
from modules.webodm.webodm_processor import WebODMProcessor


class RGBPipeline:

    def __init__(self, survey_id: str, base_dir: Path, config: Dict[str, Any]):
        self.survey_id = survey_id
        self.base_dir = Path(base_dir)
        self.config = config

        # Directories
        self.raw_dir = self.base_dir / "data/raw" / survey_id
        self.staged_dir = self.base_dir / "data/staged" / survey_id
        self.intermediate_dir = self.base_dir / "data/intermediate" / survey_id
        self.final_dir = self.base_dir / "data/final" / survey_id
        self.logs_dir = self.base_dir / "data/logs" / survey_id
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.loggers = self._init_loggers()

        self.state = {}

    # -----------------------------
    # Logger Initialization
    # -----------------------------

    def _init_loggers(self):
        return {
            "pipeline": get_logger(
                f"{self.survey_id}.pipeline",
                self.logs_dir / "pipeline.log"
            ),
            "kml": get_logger(
                f"{self.survey_id}.kml",
                self.logs_dir / "kml.log"
            ),
            "webodm": get_logger(
                f"{self.survey_id}.webodm",
                self.logs_dir / "webodm.log"
            ),
        }

    # ============================================================
    # PIPELINE STAGES
    # ============================================================

    def stage_kml_boundary(self):
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

    def stage_webodm(self):
        logger = self.loggers["webodm"]
        logger.info("Stage: WebODM Processing")

        processor = WebODMProcessor(
            url=self.config["webodm"]["url"],
            username=self.config["webodm"]["username"],
            password=self.config["webodm"]["password"],
            logger=logger,
        )

        project_id = processor.create_project(
            name=f"{self.survey_id}_RGB",
            description="RGB automated processing"
        )

        image_folder = self.staged_dir / "images"

        # -------------------
        # TASK 1 (Unbounded)
        # -------------------

        task1_id = processor.create_task_with_images(
            project_id,
            "Unbounded Orthomosaic",
            str(image_folder),
            self.config["webodm"]["task1_options"],
        )

        t1_success, t1_runtime, _ = processor.wait_for_completion(project_id, task1_id)

        # -------------------
        # TASK 2 (Bounded)
        # -------------------

        # Load boundary geojson
        geojson_files = list((self.intermediate_dir / "geojson").glob("*.geojson"))
        boundary_geojson = geojson_files[0].read_text() if geojson_files else None

        task2_options = self.config["webodm"]["task2_options"].copy()
        task2_options["boundary"] = boundary_geojson

        task2_id = processor.create_task_with_images(
            project_id,
            "Bounded Orthomosaic",
            str(image_folder),
            task2_options,
        )

        t2_success, t2_runtime, _ = processor.wait_for_completion(project_id, task2_id)

        self.state["webodm"] = {
            "project_id": project_id,
            "task1": {"id": task1_id, "success": t1_success},
            "task2": {"id": task2_id, "success": t2_success},
        }

    # ------------------------------------------------------------

    def stage_quality_gate(self):
        logger = self.loggers["pipeline"]
        logger.info("Stage: Quality Gate Check")

        # Future: implement visual defect check logic
        # For now assume pass
        quality_passed = True

        if not quality_passed:
            logger.warning("Quality check failed — restarting WebODM tasks")
            # Restart logic here
        else:
            logger.info("Quality check passed")

    # ------------------------------------------------------------

    def stage_qgis_processing(self):
        # Placeholder for future module
        self.loggers["pipeline"].info("Stage: QGIS Processing (TODO)")

    # ------------------------------------------------------------

    def stage_object_detection(self):
        self.loggers["pipeline"].info("Stage: Object Detection (TODO)")

    # ------------------------------------------------------------

    def stage_tile_generation(self):
        self.loggers["pipeline"].info("Stage: Tile Generation (TODO)")

    # ------------------------------------------------------------

    def stage_database_upload(self):
        self.loggers["pipeline"].info("Stage: Database Upload (TODO)")

    # ============================================================

    def run(self):

        pipeline_logger = self.loggers["pipeline"]
        pipeline_logger.info(f"Starting RGB Pipeline for {self.survey_id}")

        try:
            self.stage_kml_boundary()
            self.stage_webodm()
            self.stage_quality_gate()
            self.stage_qgis_processing()
            self.stage_object_detection()
            self.stage_tile_generation()
            self.stage_database_upload()

            pipeline_logger.info("RGB Pipeline completed successfully")

        except Exception:
            pipeline_logger.exception("RGB Pipeline failed")

        return self.state
