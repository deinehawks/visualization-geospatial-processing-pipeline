from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Set
import time
import uuid
import logging

from shared import get_logger, PipelineRepo, db_path, StageRunner
from modules import run_kml, WebODMProcessor, run_filter, run_data_segregation


class RGBPipeline:
    """
    RGB Survey Pipeline (run_id-based, resume-safe)

    Flow:
      1) data_segregation (generates survey_id + creates folder structure in SURVEYS_ROOT)
      2) cross_run_filter  (path_raw -> path, excluded -> cross-runs)
      3) kml_boundary      (kml -> geojson + csv)
      4) webodm            (task1 unbounded, task2 bounded)
      5) quality_gate      (placeholder)
    """

    def __init__(
        self,
        base_dir: Path,
        config: Dict[str, Any],
        *,
        source_dir: Path,
        surveys_root: Path,
        year: int,
        run_id: Optional[str] = None,
    ):
        self.base_dir = Path(base_dir)
        self.config = config

        self.source_dir = Path(source_dir)
        self.surveys_root = Path(surveys_root)
        self.year = int(year)

        # run_id enables resume even before survey_id exists
        self.run_id = run_id or str(uuid.uuid4())

        # survey_id determined by data_segregation
        self.survey_id: Optional[str] = None

        # logs (not per-survey yet; survey_id unknown at start)
        self.logs_dir = self.base_dir / "data" / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.loggers: Dict[str, logging.Logger] = {
            "pipeline": get_logger("rgb.pipeline", self.logs_dir / "pipeline.log"),
            "segregation": get_logger("rgb.data_segregation", self.logs_dir / "data_segregation.log"),
            "cross_run_filter": get_logger("rgb.cross_run_filter", self.logs_dir / "cross_run_filter.log"),
            "kml": get_logger("rgb.kml", self.logs_dir / "kml.log"),
            "webodm": get_logger("rgb.webodm", self.logs_dir / "webodm.log"),
        }

        self.state: Dict[str, Any] = {
            "run_id": self.run_id,
        }

        # DB (run-based)
        self.repo = PipelineRepo(db_path(self.base_dir))
        self.repo.create_run(
            self.run_id,
            source_dir=str(self.source_dir),
            surveys_root=str(self.surveys_root),
            year=self.year,
        )

        self.runner = StageRunner(
            repo=self.repo,
            run_id=self.run_id,
            logger=self.loggers["pipeline"],
        )

        # Will be initialized after data_segregation
        self.rgb_path: Optional[Path] = None

    # ============================================================
    # Helpers
    # ============================================================

    def _require_survey_id(self) -> str:
        if not self.survey_id:
            raise RuntimeError("survey_id is not set yet. Run data_segregation first.")
        return self.survey_id

    def _require_rgb_path(self) -> Path:
        if not self.rgb_path:
            raise RuntimeError("rgb_path is not set yet. Run data_segregation first.")
        return self.rgb_path

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

        self.survey_id = summary["survey_id"]
        self.rgb_path = Path(summary["survey_path"])  # .../<year>/<survey_id>/rgb

        # Rename KML to match survey ID (required by your standard)
        boundary_dir = self.rgb_path / "boundary"
        kmls = sorted(boundary_dir.glob("*.kml"))
        if not kmls:
            raise FileNotFoundError(f"No .kml found after segregation in: {boundary_dir}")

        original_kml = kmls[0]
        new_kml_path = boundary_dir / f"{self.survey_id}.kml"

        if original_kml.name != new_kml_path.name:
            # If already exists, overwrite behavior is up to you; keep safe:
            if new_kml_path.exists():
                new_kml_path.unlink()
            original_kml.rename(new_kml_path)

        logger.info(f"KML renamed to: {new_kml_path.name}")

        # Attach survey_id to this run (this is the big fix)
        self.repo.attach_survey_id(self.run_id, self.survey_id)
        self.repo.upsert_survey_running(self.survey_id)

        return summary

    def stage_cross_run_image_filter(self) -> Dict[str, Any]:
        logger = self.loggers["cross_run_filter"]
        logger.info("Stage: Cross-run image filter")

        rgb_path = self._require_rgb_path()

        input_dir = rgb_path / "images" / "path_raw"
        output_dir = rgb_path / "images" / "path"

        # your filter script also creates cross-run-images;
        # but your standard output wants them in rgb/images/cross-runs
        # make your module accept excluded_dir if you can (recommended).
        # For now, we let module run as-is; ensure it writes to rgb/images/cross-runs.
        filter_cfg = self.config.get("cross_run_filter", {})
        max_gap = int(filter_cfg.get("max_gap", 10))
        window = int(filter_cfg.get("window", 3))

        result = run_filter(
            input_dir=input_dir,
            output_dir=output_dir,
            logger=logger,
            max_gap=max_gap,
            cross_run_window=window,
        )

        # Determine naming flag for WebODM tasks (c vs xc)
        # If filtering excludes crossruns, we mark "xc". If you keep crossruns, mark "c".
        # Here: we assume filter excludes crossruns => "xc".
        self.state["crossrun_flag"] = "xc"

        return result

    def stage_kml_boundary(self) -> Dict[str, Any]:
        logger = self.loggers["kml"]
        logger.info("Stage: KML Boundary Setter")

        rgb_path = self._require_rgb_path()

        # Your standard: boundary folder holds kml + geojson + csv
        boundary_dir = rgb_path / "boundary"

        return run_kml(
            kml_dir=boundary_dir,
            geojson_dir=boundary_dir,
            csv_dir=boundary_dir,
            logger=logger,
        )

    def stage_webodm(self) -> Dict[str, Any]:
        logger = self.loggers["webodm"]
        logger.info("Stage: WebODM Processing")

        survey_id = self._require_survey_id()
        rgb_path = self._require_rgb_path()

        webodm_cfg = self.config["webodm"]
        naming_cfg = self.config.get("naming", {})

        # Crossrun flag:
        # - Use pipeline-derived if available, else fallback to config/env
        crossrun_flag = self.state.get("crossrun_flag") or naming_cfg.get("crossrun_mode", "xc")  # "c" or "xc"

        # Boundary flags
        boundary_flag_task1 = naming_cfg.get("boundary_mode_task1", "xb")  # xb or b
        boundary_flag_task2 = naming_cfg.get("boundary_mode_task2", "b")   # b recommended

        task1_name = f"{survey_id}-RGB--{crossrun_flag}{boundary_flag_task1}"
        task2_name = f"{survey_id}-RGB--{crossrun_flag}{boundary_flag_task2}"

        processor = WebODMProcessor(
            url=webodm_cfg["url"],
            username=webodm_cfg["username"],
            password=webodm_cfg["password"],
            logger=logger,
        )

        project_id = processor.create_project(
            name=f"{survey_id}_RGB",
            description="RGB automated processing",
        )

        image_folder = rgb_path / "images" / "path"  # filtered canonical
        if not image_folder.exists():
            raise FileNotFoundError(f"Filtered image folder not found: {image_folder}")

        # -------- Task 1 (Unbounded Orthomosaic) --------
        task1_options = dict(webodm_cfg.get("task1_options", {}))
        task1_id = processor.create_task_with_images(
            project_id=project_id,
            name=task1_name,
            image_folder=str(image_folder),
            options=task1_options,
        )
        t1_success, t1_runtime, _ = processor.wait_for_completion(project_id, task1_id)

        # -------- Task 2 (Bounded) --------
        boundary_dir = rgb_path / "boundary"
        geojson_files = sorted(boundary_dir.glob("*.geojson"))
        if not geojson_files:
            raise FileNotFoundError(f"No .geojson boundary found in: {boundary_dir}")

        boundary_geojson = geojson_files[0].read_text(encoding="utf-8")

        task2_options = dict(webodm_cfg.get("task2_options", {}))
        task2_options["boundary"] = boundary_geojson

        task2_id = processor.create_task_with_images(
            project_id=project_id,
            name=task2_name,
            image_folder=str(image_folder),
            options=task2_options,
        )
        t2_success, t2_runtime, _ = processor.wait_for_completion(project_id, task2_id)

        # Optional: store WebODM tasks in your DB table if you added run_id-based webodm_tasks table.
        # (Only if you already implemented repo.insert_webodm_task)
        # self.repo.insert_webodm_task(...)

        return {
            "project_id": project_id,
            "task1": {"id": task1_id, "name": task1_name, "success": t1_success, "runtime_seconds": t1_runtime},
            "task2": {"id": task2_id, "name": task2_name, "success": t2_success, "runtime_seconds": t2_runtime},
        }

    def stage_quality_gate(self) -> Dict[str, Any]:
        self.loggers["pipeline"].info("Stage: Quality Gate Check")
        return {"passed": True}

    # ============================================================
    # RUN
    # ============================================================

    def run(self, *, resume: bool = True, force_stages: Optional[Set[str]] = None) -> Dict[str, Any]:
        pipeline_logger = self.loggers["pipeline"]
        pipeline_logger.info(f"Starting RGB Pipeline | run_id={self.run_id}")

        total_start = time.perf_counter()
        force_stages = force_stages or set()

        try:
            self.runner.run(
                "data_segregation",
                self.stage_data_segregation,
                output_key="data_segregation",
                state=self.state,
                force=("data_segregation" in force_stages) or (not resume),
            )

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
            self.repo.mark_run_finished(self.run_id, success=True, total_runtime_seconds=total_runtime)

            if self.survey_id:
                self.repo.mark_survey_finished(self.survey_id, success=True, total_runtime_seconds=total_runtime)

            pipeline_logger.info(f"RGB Pipeline finished | run_id={self.run_id} | success=True")
            return self.state

        except Exception as e:
            self.state["success"] = False
            self.state["error"] = str(e)

            total_runtime = time.perf_counter() - total_start
            self.repo.mark_run_finished(self.run_id, success=False, total_runtime_seconds=total_runtime)

            if self.survey_id:
                self.repo.mark_survey_finished(self.survey_id, success=False, total_runtime_seconds=total_runtime)

            pipeline_logger.exception(f"RGB Pipeline failed | run_id={self.run_id}")
            return self.state
