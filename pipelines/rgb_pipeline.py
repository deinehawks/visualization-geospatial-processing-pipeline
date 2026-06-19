
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Set, List, Tuple
from shared import get_logger, PipelineRepo, db_path, StageRunner, PipelineControl
from modules import run_kml, WebODMProcessor, run_filter, run_data_segregation
from shared.logging import quality_gate_prompt, pipeline_header, pipeline_footer, pipeline_paused, pipeline_canceled, set_stage_context
from shared.preflight_checks import PipelinePreflight, PreflightError

import time
import uuid
import logging
import os
import shutil
from typing import Any
from modules import QGISTools


class RGBPipeline:
    """
    RGB Survey Pipeline (run_id-based, resume-safe)

    Flow:
      1) data_segregation (generates survey_id + creates folder structure in SURVEYS_ROOT)
      2) cross_run_filter  (raw -> path, excluded -> cross-runs)
      3) kml_boundary      (kml -> geojson + csv)
      4) webodm            (task1 unbounded, task2 bounded)
      5) quality_gate      (placeholder)
      5) qgis      
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
        
        survey_id_override: Optional[str] = None,
        task_name_overrides: Optional[Dict[str, str]] = None,
        export_name_overrides: Optional[Dict[str, str]] = None,
        crossrun_enabled_override: Optional[bool] = None,
        use_year_subdir_override: Optional[bool] = None,
        skip_task1_webodm: bool = False, # TEMPORARY
        skip_task2_webodm: bool = False, # TEMPORARY
        task1_bounded: bool = False, # TEMPORARY
        force_segregation: bool = False, # TEMPORARY
    ):
        self.base_dir = Path(base_dir)
        self.config = config
        self.source_dir = Path(source_dir)
        self.surveys_root = Path(surveys_root)
        self.year = int(year)
        self.run_id = run_id or str(uuid.uuid4())
        self.survey_id: Optional[str] = None
        self.logs_dir = self.base_dir / "data" / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.survey_id_override = survey_id_override
        self.task_name_overrides = task_name_overrides or {}
        self.export_name_overrides = export_name_overrides or {}
        self.crossrun_enabled_override = crossrun_enabled_override
        self.use_year_subdir_override = use_year_subdir_override

        self.skip_task1_webodm = skip_task1_webodm # TEMPORARY
        self.skip_task2_webodm = skip_task2_webodm # TEMPORARY
        self.task1_bounded = task1_bounded # TEMPORARY
        self.force_segregation = force_segregation # TEMPORARY
        self.control = PipelineControl(
            self.base_dir,
            self.run_id,
        )

        self.loggers: Dict[str, logging.Logger] = {
            "pipeline": get_logger(
                "rgb.pipeline",
                self.logs_dir / "pipeline.log",
                run_id=self.run_id,
            ),
            "segregation": get_logger(
                "rgb.data_segregation",
                self.logs_dir / "data_segregation.log",
                run_id=self.run_id,
            ),
            "cross_run_filter": get_logger(
                "rgb.cross_run_filter",
                self.logs_dir / "cross_run_filter.log",
                run_id=self.run_id,
            ),
            "kml": get_logger(
                "rgb.kml",
                self.logs_dir / "kml.log",
                run_id=self.run_id,
            ),
            "webodm": get_logger(
                "rgb.webodm",
                self.logs_dir / "webodm.log",
                run_id=self.run_id,
            ),
            "qgis": get_logger(
                "rgb.qgis",
                self.logs_dir / "qgis.log",
                run_id=self.run_id,
            ),
        }

        self.state: Dict[str, Any] = {
            "run_id": self.run_id,
        }

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
            extra_loggers=[
                self.loggers["segregation"],
                self.loggers["cross_run_filter"],
                self.loggers["kml"],
                self.loggers["webodm"],
                self.loggers["qgis"],
            ],
        )

        self.preflight = PipelinePreflight(
            config=self.config,
            source_dir=self.source_dir,
            surveys_root=self.surveys_root,
            year=self.year,
            logger=self.loggers["pipeline"],
        )

        self.rgb_path: Optional[Path] = None

    # Helpers
    def _require_survey_id(self) -> str:
        if not self.survey_id:
            raise RuntimeError(
                "survey_id is not set yet. Run data_segregation first.")
        return self.survey_id

    def _require_rgb_path(self) -> Path:
        if not self.rgb_path:
            raise RuntimeError(
                "rgb_path is not set yet. Run data_segregation first.")
        return self.rgb_path

    def _hydrate_from_state(self) -> None:
        state = self.state

        seg = state.get("data_segregation") or {}
        if seg.get("survey_id"):
            self.survey_id = seg["survey_id"]
        if seg.get("survey_path"):
            self.rgb_path = Path(seg["survey_path"])

        flt = state.get("cross_run_filter") or {}
        if flt.get("crossrun_flag"):
            self.state["crossrun_flag"] = flt["crossrun_flag"]

        kml = state.get("kml_boundary") or {}
        if "boundary_available" in kml:
            self.state["boundary_available"] = kml["boundary_available"]
        if kml.get("boundary_geojson_path"):
            self.state["boundary_geojson_path"] = kml["boundary_geojson_path"]

        if not state.get("webodm"):
            ckpt = self._load_webodm_checkpoint()
            if ckpt:
                self.loggers["pipeline"].info(
                    f"Loaded webodm checkpoint: project_id={ckpt.get('project_id')} "
                    f"task1={(ckpt.get('task1') or {}).get('id')} "
                    f"task2={(ckpt.get('task2') or {}).get('id')}"
                )
                self.state["webodm"] = ckpt

    def _stage_will_run(self, stage_name: str, *, force: bool) -> bool:
        if force:
            return True

        try:
            latest = self.repo.get_latest_stage(self.run_id, stage_name)
            if latest and latest.get("status") == "completed":
                return False
        except Exception:
            self.loggers["pipeline"].exception(
                f"Failed to check latest stage status for preflight: {stage_name}"
            )

        return True

    def _preflight_stage(self, stage_name: str) -> None:
        result = self.preflight.check_stage(
            stage_name,
            state=self.state,
            survey_id=self.survey_id,
            rgb_path=self.rgb_path,
            skip_task1_webodm=self.skip_task1_webodm,
            skip_task2_webodm=self.skip_task2_webodm,
        )

        self.loggers["pipeline"].info(
            f"PREFLIGHT OK | {stage_name} | {result}"
        )

    # Checkpoint helpers
    def _webodm_checkpoint_path(self) -> Path:
        return self.base_dir / "data" / "logs" / f"webodm_checkpoint_{self.run_id}.json"

    def _save_webodm_checkpoint(self, data: dict) -> None:
        import json
        path = self._webodm_checkpoint_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            self.loggers["webodm"].warning(
                f"Could not save webodm checkpoint: {e}")

    def _load_webodm_checkpoint(self) -> dict:
        import json
        path = self._webodm_checkpoint_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            self.loggers["webodm"].warning(
                f"Could not load webodm checkpoint: {e}")
            return {}

    def _clear_webodm_checkpoint(self) -> None:
        path = self._webodm_checkpoint_path()
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    @staticmethod
    def _iter_jpeg_files(folder: Path) -> List[Path]:
        exts = {".jpg", ".jpeg"}
        files: List[Path] = []
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in exts:
                files.append(p)
        return sorted(files)

    @staticmethod
    def _fmt_bytes(num: int) -> str:
        size: float = float(num)
        step: float = 1024.0

        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < step:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            size /= step

        return f"{size:.1f} PB"
    
    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
            return int(float(value))
        return default

    def _stage_upload_cache(
        self,
        *,
        src_dir: Path,
        cache_root: Path,
        logger: logging.Logger,
        progress_every: int = 25,
        require_free_multiplier: float = 1.2,
    ) -> Tuple[Path, int]:
        """
        Copy JPEG images from src_dir to a local cache dir (fast local reads for upload).
        Returns: (cache_dir, image_count)
        """
        src_dir = Path(src_dir)
        if not src_dir.exists():
            raise FileNotFoundError(
                f"Upload cache source dir not found: {src_dir}")

        images = self._iter_jpeg_files(src_dir)
        if not images:
            raise FileNotFoundError(f"No JPG/JPEG images found in: {src_dir}")

        total_bytes = 0
        for p in images:
            total_bytes += p.stat().st_size

        cache_dir = Path(cache_root)
        cache_dir.mkdir(parents=True, exist_ok=True)

        usage = shutil.disk_usage(str(cache_dir))
        free_bytes = usage.free
        needed = int(total_bytes * require_free_multiplier)

        logger.info(
            f"Upload cache: preparing {len(images)} images "
            f"({self._fmt_bytes(total_bytes)}) -> {cache_dir} | free={self._fmt_bytes(free_bytes)}"
        )

        if free_bytes < needed:
            raise RuntimeError(
                f"Not enough free space for upload cache.\n"
                f"- Needed (with x{require_free_multiplier} buffer): {self._fmt_bytes(needed)}\n"
                f"- Free: {self._fmt_bytes(free_bytes)}\n"
                f"Cache root: {cache_dir}"
            )

        copied = 0
        t0 = time.perf_counter()

        for src in images:
            dst = cache_dir / src.name
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                copied += 1
            else:
                shutil.copy2(src, dst)
                copied += 1

            if copied % progress_every == 0 or copied == len(images):
                elapsed = time.perf_counter() - t0
                logger.info(
                    f"Upload cache copy progress: {copied}/{len(images)} | elapsed={elapsed:.1f}s")

        elapsed = time.perf_counter() - t0
        logger.info(
            f"Upload cache ready: {cache_dir} | images={len(images)} | copy_time={elapsed:.1f}s")
        return cache_dir, len(images)

    def _cleanup_upload_cache(self, cache_dir: Path, logger: logging.Logger) -> None:
        try:
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
                logger.info(f"Upload cache cleaned: {cache_dir}")
        except Exception:
            logger.exception(f"Failed to clean upload cache: {cache_dir}")

    def _check_control_or_raise(self, stage_name: str) -> None:
        try:
            self.control.check_or_raise()
        except RuntimeError as e:
            if str(e) == "__PIPELINE_PAUSED__":
                reason = f"pause requested during/before stage '{stage_name}'"
                self.loggers["pipeline"].warning(f"⏸ PAUSE | {reason}")

                try:
                    self.repo.mark_run_paused(
                        self.run_id,
                        paused_after_stage=stage_name,
                        reason="pause_hotkey",
                    )
                except Exception:
                    self.loggers["pipeline"].exception(
                        "Failed to mark run as paused in DB"
                    )

                raise

            if str(e) == "__PIPELINE_ABORTED__":
                self.loggers["pipeline"].error(
                    f"🛑 ABORT | abort requested during/before stage '{stage_name}'"
                )
                raise

            raise
    # STAGES
    def stage_data_segregation(self) -> Dict[str, Any]:
        logger = self.loggers["segregation"]
        logger.info("Stage: Data Segregation")

        summary = run_data_segregation(
            source_dir=self.source_dir,
            surveys_root=self.surveys_root,
            year=self.year,
            logger=logger,
            survey_id_override=self.survey_id_override,
            use_year_subdir=(
                self.use_year_subdir_override
                if self.use_year_subdir_override is not None
                else bool(self.config.get("experiment", {}).get("use_year_subdir", True))
            ),
            force=self.force_segregation,
        )

        self.survey_id = summary["survey_id"]
        # .../<year>/<survey_id>/rgb
        self.rgb_path = Path(summary["survey_path"])

        # Rename KML to match survey ID
        boundary_dir = self.rgb_path / "boundary"
        kmls = sorted(boundary_dir.glob("*.kml"))
        if not kmls:
            raise FileNotFoundError(
                f"No .kml found after segregation in: {boundary_dir}")

        original_kml = kmls[0]
        new_kml_path = boundary_dir / f"{self.survey_id}.kml"

        if original_kml.name != new_kml_path.name:
            if new_kml_path.exists():
                new_kml_path.unlink()
            original_kml.rename(new_kml_path)

        logger.info(f"KML renamed to: {new_kml_path.name}")

        if self.survey_id is None:
            raise RuntimeError("survey_id is not set")

        self.repo.attach_survey_id(self.run_id, self.survey_id)
        self.repo.upsert_survey_running(self.survey_id)

        return summary

    def stage_cross_run_image_filter(self) -> Dict[str, Any]:
        logger = self.loggers["cross_run_filter"]
        logger.info("Stage: Cross-run image filter")

        rgb_path = self._require_rgb_path()

        input_dir = rgb_path / "images" / "raw"
        output_dir = rgb_path / "images" / "path"
        excluded_dir = output_dir.parent / "cross-runs"

        filter_cfg = self.config.get("cross_run_filter", {})
        max_gap = int(filter_cfg.get("max_gap", 10))
        window = int(filter_cfg.get("window", 3))
        delete_raw_after = bool(filter_cfg.get("delete_raw_after_success", False))

        enabled = self.crossrun_enabled_override
        if enabled is None:
            enabled = bool(filter_cfg.get("enabled", True))

        logger.info(f"Input (raw): {input_dir}")
        logger.info(f"Output (kept/path): {output_dir}")
        logger.info(f"Output (excluded/cross-runs): {excluded_dir}")

        if not enabled:
            logger.info("Cross-run filter disabled. Copying RAW -> PATH without exclusions.")

            output_dir.mkdir(parents=True, exist_ok=True)
            excluded_dir.mkdir(parents=True, exist_ok=True)

            images = self._iter_jpeg_files(input_dir)
            for img in images:
                shutil.copy2(img, output_dir / img.name)

            result = {
                "filter_enabled": False,
                "total_images": len(images),
                "total_kept": len(images),
                "total_excluded": 0,
                "cross_runs_detected": 0,
                "too_close_exclusions": 0,
                "cluster_exclusions": 0,
                "crossrun_flag": "c",                 # keep default semantics
                "experiment_crossrun_label": "NF",   # experiment naming only
                "raw_deleted": False,
            }

            self.state["crossrun_flag"] = "c"
            self.state["experiment_crossrun_label"] = "NF"
            return result

        result = run_filter(
            input_dir=input_dir,
            output_dir=output_dir,
            logger=logger,
            max_gap=max_gap,
            cross_run_window=window,
        )

        excluded = self._safe_int(result.get("total_excluded"))

        # Default pipeline semantics
        crossrun_flag = "xc" if excluded > 0 else "c"
        self.state["crossrun_flag"] = crossrun_flag
        result["crossrun_flag"] = crossrun_flag

        # Experiment naming label
        self.state["experiment_crossrun_label"] = "F"
        result["experiment_crossrun_label"] = "F"

        if delete_raw_after:
            try:
                total_images = self._safe_int(result.get("total_images"))
                kept = self._safe_int(result.get("total_kept"))
                excl = self._safe_int(result.get("total_excluded"))

                if total_images <= 0:
                    raise RuntimeError(
                        "Refusing to delete raw: total_images is 0 (unexpected)."
                    )
                if kept + excl != total_images:
                    raise RuntimeError(
                        f"Refusing to delete raw: kept+excluded != total ({kept}+{excl}!={total_images})"
                    )
                if not output_dir.exists() or not excluded_dir.exists():
                    raise RuntimeError(
                        "Refusing to delete raw: output directories missing."
                    )

                kept_fs = len([
                    p for p in output_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                ])
                excl_fs = len([
                    p for p in excluded_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                ])

                if kept_fs + excl_fs != total_images:
                    raise RuntimeError(
                        f"Refusing to delete raw: filesystem output count mismatch "
                        f"({kept_fs}+{excl_fs}!={total_images})"
                    )

                logger.warning(
                    f"Deleting RAW images folder to save space: {input_dir} "
                    f"(total={total_images}, kept={kept_fs}, excluded={excl_fs})"
                )

                if input_dir.name == "raw" and input_dir.exists():
                    shutil.rmtree(input_dir)
                    logger.info(f"Raw folder deleted after filtering: {input_dir}")
                else:
                    logger.warning(f"Refusing to delete unexpected folder: {input_dir}")

                result["raw_deleted"] = True

            except Exception as e:
                logger.warning(f"Raw cleanup skipped/failed: {e}")
                result["raw_deleted"] = False
                result["raw_delete_error"] = str(e)
        else:
            result["raw_deleted"] = False

        return result

    def stage_kml_boundary(self) -> Dict[str, Any]:
        logger = self.loggers["kml"]
        logger.info("Stage: KML Boundary Setter")

        rgb_path = self._require_rgb_path()
        boundary_dir = rgb_path / "boundary"

        summary = run_kml(
            kml_dir=boundary_dir,
            geojson_dir=boundary_dir,
            csv_dir=boundary_dir,
            logger=logger,
        )

        processed_files = summary.get("processed_files") or []
        geojson_path = None
        if processed_files:
            geojson_path = processed_files[0].get("geojson")

        boundary_ok = bool(geojson_path and Path(geojson_path).exists())

        self.state["boundary_available"] = boundary_ok
        self.state["boundary_geojson_path"] = str(
            geojson_path) if geojson_path else None

        if not boundary_ok:
            logger.warning(
                "No valid polygon boundary produced (GeoJSON missing). Pipeline will run Task 1 only.")

        # Embed into returned dict so StageRunner persists them to DB
        summary["boundary_available"] = boundary_ok
        summary["boundary_geojson_path"] = str(
            geojson_path) if geojson_path else None

        return summary

    def stage_webodm(self) -> Dict[str, Any]:
        logger = self.loggers["webodm"]
        logger.info("Stage: WebODM Processing")

        survey_id = self._require_survey_id()
        rgb_path = self._require_rgb_path()

        webodm_cfg = self.config["webodm"]
        naming_cfg = self.config.get("naming", {})
        exports_cfg = self.config.get("exports", {})
        qgis_tools_cfg = (exports_cfg.get("tools") or {})

        ds = self.state.get("data_segregation") or {}
        dirs = ds.get("dirs") or {}
        if not dirs:
            raise RuntimeError(
                "Missing data_segregation.dirs in state. Ensure segregation returns dirs mapping."
            )

        def dir_from_key(key: str, *, fallback: Optional[Path] = None) -> Path:
            p = dirs.get(key)
            if p:
                return Path(p)
            if fallback is not None:
                return Path(fallback)
            raise KeyError(f"Missing dir key in data_segregation.dirs: {key}")

        crossrun_flag = self.state.get("crossrun_flag") or naming_cfg.get("crossrun_mode", "xc")

        boundary_available = bool(self.state.get("boundary_available"))
        boundary_geojson_path = self.state.get("boundary_geojson_path")

        boundary_flag_task1 = naming_cfg.get("task1_boundary_mode", "xb")
        boundary_flag_task2 = naming_cfg.get("task2_boundary_mode", "b")

        task1_flag = f"{crossrun_flag}{boundary_flag_task1}"
        task2_flag = f"{crossrun_flag}{boundary_flag_task2}"

        task1_name = self.task_name_overrides.get("task1")
        task2_name = self.task_name_overrides.get("task2")

        if not task1_name:
            task1_name = f"{survey_id}-RGB--{task1_flag}"

        if not task2_name:
            task2_name = f"{survey_id}-RGB--{task2_flag}"

        task1_export_id = self.export_name_overrides.get("task1", task1_name)
        task2_export_id = self.export_name_overrides.get("task2", task2_name)

        skip_task1 = bool(getattr(self, "skip_task1_webodm", False))
        skip_task2 = bool(getattr(self, "skip_task2_webodm", False))
        task1_bounded = bool(getattr(self, "task1_bounded", False))

        if skip_task1 and skip_task2:
            raise RuntimeError("Both WebODM Task 1 and Task 2 are skipped. Nothing to run.")

        if skip_task1 and task1_bounded:
            logger.warning("task1_bounded is set but Task 1 is skipped; ignoring task1_bounded.")

        production_mode = not bool(self.config.get("experiment", {}).get("enabled", False))

        if production_mode:
            task1_root_dir = rgb_path
            task2_root_dir = rgb_path
        else:
            task1_root_dir = rgb_path / task1_export_id
            task2_root_dir = rgb_path / task2_export_id

        task1_ortho_dir = task1_root_dir / "ortho"
        task1_odm_dir = task1_root_dir / "odm"

        task2_ortho_dir = task2_root_dir / "ortho"
        task2_3d_dir = task2_root_dir / "3d"
        task2_odm_dir = task2_root_dir / "odm"
        task2_dem_dtm_dir = task2_root_dir / "dem" / "odm" / "dtm"
        task2_dem_dsm_dir = task2_root_dir / "dem" / "odm" / "dsm"

        for p in [
            task1_root_dir,
            task1_ortho_dir,
            task1_odm_dir,
            task2_root_dir,
            task2_ortho_dir,
            task2_3d_dir,
            task2_odm_dir,
            task2_dem_dtm_dir,
            task2_dem_dsm_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)

        image_folder = Path(dirs.get("path") or (rgb_path / "images" / "path"))

        upload_cache_root_cfg = (self.config.get("paths") or {}).get("upload_cache_root")
        if not upload_cache_root_cfg:
            upload_cache_root_cfg = os.getenv("UPLOAD_CACHE_ROOT")
        local_root = Path(upload_cache_root_cfg) if upload_cache_root_cfg else Path(
            os.getenv("TEMP", r"C:\temp")
        )
        cache_root = local_root / "automation-pipeline" / "upload_cache" / self.run_id

        cached_dir: Optional[Path] = None
        upload_folder: Path = image_folder

        try:
            try:
                cached_dir, _ = self._stage_upload_cache(
                    src_dir=image_folder,
                    cache_root=cache_root,
                    logger=logger,
                    progress_every=25,
                    require_free_multiplier=1.2,
                )
                upload_folder = cached_dir
            except Exception as e:
                logger.warning(
                    f"Upload cache unavailable, uploading directly from source. reason={e}"
                )
                cached_dir = None
                upload_folder = image_folder

            processor = WebODMProcessor(
                url=webodm_cfg["url"],
                username=webodm_cfg["username"],
                password=webodm_cfg["password"],
                logger=logger,
            )

            project_suffix = str(self.state.get("webodm_project_suffix") or "").strip()
            project_name = f"{survey_id}{project_suffix}"

            prev_web = self.state.get("webodm") or {}
            prev_project_id = prev_web.get("project_id")
            prev_task1 = prev_web.get("task1") or {}
            prev_task2 = prev_web.get("task2") or {}

            if prev_project_id:
                project_id = prev_project_id
                logger.info(f"Resuming: reattaching to existing project (ID={project_id})")
            else:
                project_id = processor.create_project(
                    name=project_name,
                    description="RGB automated processing",
                )

                self._save_webodm_checkpoint({
                    "project_id": project_id,
                    "project_name": project_name,
                    "task1": None if skip_task1 else {},
                    "task2": None,
                    "downloads": {"task1": {}, "task2": {}},
                })

            # ---------------- TASK 1 ----------------
            if skip_task1:
                logger.info("Skipping WebODM Task 1 by request.")
                current_task1_id = ""
                t1_success = False
                t1_runtime = 0.0
            else:
                t1_already_done = (
                    prev_task1.get("id")
                    and prev_task1.get("success") is True
                )

                if t1_already_done:
                    current_task1_id = str(prev_task1["id"])
                    t1_success = True
                    t1_runtime = float(prev_task1.get("runtime_seconds") or 0)
                    logger.info(
                        f"Resuming: Task 1 already completed "
                        f"(id={current_task1_id}) — skipping upload and processing"
                    )
                else:
                    existing_task1_id = None
                    if prev_project_id:
                        existing_task1_id = processor.find_task_by_name(project_id, task1_name)
                        if existing_task1_id:
                            logger.info(
                                f"Resuming: found existing Task 1 in WebODM by name "
                                f"'{task1_name}' (id={existing_task1_id}) — reattaching"
                            )

                    if existing_task1_id:
                        task1_status = processor.get_task_status(project_id, existing_task1_id)
                        logger.info(
                            f"Resuming: Task 1 current status in WebODM: {task1_status!r}"
                        )

                        if task1_status == "completed":
                            current_task1_id = existing_task1_id
                            t1_success = True
                            t1_runtime = 0.0
                            logger.info(
                                f"Resuming: Task 1 already completed in WebODM "
                                f"(id={current_task1_id}) — reusing"
                            )

                        elif task1_status in ("queued", "running"):
                            current_task1_id = existing_task1_id
                            logger.info(
                                f"Resuming: Task 1 still {task1_status} in WebODM "
                                f"(id={current_task1_id}) — waiting for completion"
                            )
                            t1_success, t1_runtime, _t1_info = processor.wait_for_completion(
                                project_id, current_task1_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                            )

                        else:
                            logger.warning(
                                f"Resuming: Task 1 is '{task1_status}' in WebODM "
                                f"(id={existing_task1_id}) — deleting and re-uploading"
                            )
                            try:
                                processor.delete_task(project_id, existing_task1_id)
                            except Exception as del_err:
                                logger.warning(
                                    f"Could not delete failed task "
                                    f"{existing_task1_id}: {del_err} — continuing anyway"
                                )

                            task1_options = dict(webodm_cfg.get("task1_options", {}))

                            if task1_bounded:
                                if not boundary_available:
                                    raise RuntimeError("Task 1 bounded was requested, but boundary is not available.")
                                if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                                    raise RuntimeError("Task 1 bounded was requested, but boundary GeoJSON is missing.")

                                boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")
                                task1_options["boundary"] = boundary_geojson
                                logger.info("Task 1 will run as bounded (boundary injected into Task 1 options).")

                            current_task1_id = processor.create_task_with_images(
                                project_id=project_id,
                                name=task1_name,
                                image_folder=str(upload_folder),
                                options=task1_options,
                                processing_node=webodm_cfg.get("node_id"),
                            )
                            t1_success, t1_runtime, _t1_info = processor.wait_for_completion(
                                project_id, current_task1_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                            )

                    elif prev_task1.get("id") and prev_project_id:
                        current_task1_id = str(prev_task1["id"])
                        logger.info(
                            f"Resuming: Task 1 exists but incomplete "
                            f"(id={current_task1_id}) — checking WebODM status"
                        )
                        t1_success, t1_runtime, _t1_info = processor.wait_for_completion(
                            project_id, current_task1_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                        )

                    else:
                        task1_options = dict(webodm_cfg.get("task1_options", {}))

                        if task1_bounded:
                            if not boundary_available:
                                raise RuntimeError("Task 1 bounded was requested, but boundary is not available.")
                            if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                                raise RuntimeError("Task 1 bounded was requested, but boundary GeoJSON is missing.")

                            boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")
                            task1_options["boundary"] = boundary_geojson
                            logger.info("Task 1 will run as bounded (boundary injected into Task 1 options).")

                        current_task1_id = processor.create_task_with_images(
                            project_id=project_id,
                            name=task1_name,
                            image_folder=str(upload_folder),
                            options=task1_options,
                            processing_node=webodm_cfg.get("node_id"),
                        )
                        t1_success, t1_runtime, _t1_info = processor.wait_for_completion(
                            project_id, current_task1_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                        )

                    self._save_webodm_checkpoint({
                        "project_id": project_id,
                        "project_name": project_name,
                        "task1": {
                            "id": current_task1_id,
                            "name": task1_name,
                            "success": t1_success,
                            "runtime_seconds": t1_runtime,
                        },
                        "task2": None,
                        "downloads": {"task1": {}, "task2": {}},
                    })

            result: Dict[str, Any] = {
                "project_id": project_id,
                "project_name": project_name,
                "task1": None if skip_task1 else {
                    "id": current_task1_id,
                    "name": task1_name,
                    "success": t1_success,
                    "runtime_seconds": t1_runtime,
                },
                "task2": None,
                "boundary_used": bool(task1_bounded and not skip_task1),
                "boundary_reason": None,
                "boundary_geojson_path": boundary_geojson_path,
                "downloads": {
                    "task1": prev_web.get("downloads", {}).get("task1") or {},
                    "task2": {},
                },
            }

            if (not skip_task1) and prev_web.get("downloads", {}).get("task1"):
                logger.info("Resuming: reusing Task 1 downloads from previous run")

            # ---------------- Downloads after TASK 1 ----------------
            if (
                not skip_task1
                and exports_cfg.get("enabled", False)
                and exports_cfg.get("ortho", {}).get("enabled", False)
            ):
                ortho_cfg = exports_cfg["ortho"]

                out_dir = task1_ortho_dir
                epsg = int(ortho_cfg.get("reproject_epsg", 4326))
                candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

                task1_export_override = self.export_name_overrides.get("task1")
                if task1_export_override:
                    filename = f"{task1_export_override}.tif"
                else:
                    filename = ortho_cfg.get(
                        "filename_template",
                        "orthomosaic--{flag}.tif"
                    ).format(flag=task1_flag)

                out_path = processor.export_orthomosaic(
                    project_id,
                    current_task1_id,
                    out_dir=out_dir,
                    filename=filename,
                    epsg=epsg,
                    candidates=candidates,
                    gdalwarp_path=(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp"),
                )

                if out_path:
                    result["downloads"]["task1"]["orthomosaic"] = str(out_path)
                    result["downloads"]["task1"]["epsg"] = epsg
                else:
                    logger.warning("Could not download orthomosaic for Task 1.")

            # ---------------- TASK 2 ----------------
            if skip_task2:
                logger.info("Skipping WebODM Task 2 by request.")
                result["boundary_reason"] = "Task 2 skipped by request."
                self._clear_webodm_checkpoint()
                return result

            if not boundary_available:
                msg = "Boundary not available. Skipping Task 2 (bounded models)."
                logger.warning(msg)
                result["boundary_reason"] = msg
                return result

            if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                msg = "Boundary flag is True but GeoJSON path is missing. Skipping Task 2."
                logger.warning(msg)
                result["boundary_reason"] = msg
                return result

            boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")
            task2_options = dict(webodm_cfg.get("task2_options", {}))
            task2_options["boundary"] = boundary_geojson

            t2_already_done = (
                prev_task2.get("id")
                and prev_task2.get("success") is True
            )

            if t2_already_done:
                current_task2_id = str(prev_task2["id"])
                t2_success = True
                t2_runtime = float(prev_task2.get("runtime_seconds") or 0)
                logger.info(
                    f"Resuming: Task 2 already completed "
                    f"(id={current_task2_id}) — skipping upload and processing"
                )

            else:
                existing_task2_id = None
                if prev_project_id:
                    existing_task2_id = processor.find_task_by_name(project_id, task2_name)
                    if existing_task2_id:
                        logger.info(
                            f"Resuming: found existing Task 2 in WebODM by name "
                            f"'{task2_name}' (id={existing_task2_id}) — checking status"
                        )

                if existing_task2_id:
                    task2_status = processor.get_task_status(project_id, existing_task2_id)
                    logger.info(
                        f"Resuming: Task 2 current status in WebODM: {task2_status!r}"
                    )

                    if task2_status == "completed":
                        current_task2_id = existing_task2_id
                        t2_success = True
                        t2_runtime = 0.0
                        logger.info(
                            f"Resuming: Task 2 already completed in WebODM "
                            f"(id={current_task2_id}) — reusing"
                        )

                    elif task2_status in ("queued", "running"):
                        current_task2_id = existing_task2_id
                        logger.info(
                            f"Resuming: Task 2 still {task2_status} in WebODM "
                            f"(id={current_task2_id}) — waiting for completion"
                        )
                        t2_success, t2_runtime, _t2_info = processor.wait_for_completion(
                            project_id, current_task2_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                        )

                    else:
                        logger.warning(
                            f"Resuming: Task 2 is '{task2_status}' in WebODM "
                            f"(id={existing_task2_id}) — deleting and re-uploading"
                        )
                        try:
                            processor.delete_task(project_id, existing_task2_id)
                        except Exception as del_err:
                            logger.warning(
                                f"Could not delete failed Task 2 "
                                f"{existing_task2_id}: {del_err} — continuing anyway"
                            )

                        current_task2_id = processor.create_task_with_images(
                            project_id=project_id,
                            name=task2_name,
                            image_folder=str(upload_folder),
                            options=task2_options,
                            processing_node=webodm_cfg.get("node_id"),
                        )
                        t2_success, t2_runtime, _t2_info = processor.wait_for_completion(
                            project_id, current_task2_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                        )

                elif prev_task2.get("id") and prev_project_id:
                    current_task2_id = str(prev_task2["id"])
                    logger.info(
                        f"Resuming: Task 2 exists in checkpoint but incomplete "
                        f"(id={current_task2_id}) — checking WebODM status"
                    )
                    t2_success, t2_runtime, _t2_info = processor.wait_for_completion(
                        project_id, current_task2_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                    )

                else:
                    current_task2_id = processor.create_task_with_images(
                        project_id=project_id,
                        name=task2_name,
                        image_folder=str(upload_folder),
                        options=task2_options,
                        processing_node=webodm_cfg.get("node_id"),
                    )
                    t2_success, t2_runtime, _t2_info = processor.wait_for_completion(
                        project_id, current_task2_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                    )

            result["task2"] = {
                "id": current_task2_id,
                "name": task2_name,
                "success": t2_success,
                "runtime_seconds": t2_runtime,
            }
            result["boundary_used"] = True
            self._save_webodm_checkpoint({
                "project_id": project_id,
                "project_name": project_name,
                "task1": result["task1"],
                "task2": result["task2"],
                "downloads": result["downloads"],
            })

            # ---------------- Task 2 bounded orthomosaic ----------------
            if exports_cfg.get("enabled", False) and exports_cfg.get("ortho", {}).get("enabled", False):
                ortho_cfg = exports_cfg["ortho"]

                out_dir = task2_ortho_dir
                epsg = int(ortho_cfg.get("reproject_epsg", 4326))
                candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

                task2_export_override = self.export_name_overrides.get("task2")
                if task2_export_override:
                    filename = f"{task2_export_override}.tif"
                else:
                    filename = ortho_cfg.get(
                        "filename_template",
                        "orthomosaic--{flag}.tif"
                    ).format(flag=task2_flag)

                out_path = processor.export_orthomosaic(
                    project_id,
                    current_task2_id,
                    out_dir=out_dir,
                    filename=filename,
                    epsg=epsg,
                    candidates=candidates,
                    gdalwarp_path=(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp"),
                )

                if out_path:
                    result["downloads"]["task2"]["orthomosaic"] = str(out_path)
                    result["downloads"]["task2"]["epsg"] = epsg
                else:
                    logger.warning("Could not download bounded orthomosaic for Task 2.")

            # ---------------- Downloads after TASK 2 ----------------
            if exports_cfg.get("enabled", False):
                dem_cfg = (exports_cfg.get("dem") or {})
                dem_do_download = False

                if dem_do_download:
                    epsg = int(dem_cfg.get("reproject_epsg", 3857))
                    dtm_dir = task2_dem_dtm_dir
                    dsm_dir = task2_dem_dsm_dir

                    models = list(dem_cfg.get("models") or ["dtm", "dsm"])
                    colors = list(dem_cfg.get("colors") or [])
                    shadings = list(dem_cfg.get("shadings") or [])
                    tmpl = dem_cfg.get("filename_template", "{color}-{shading}.tif")

                    if not colors or not shadings:
                        logger.warning(
                            "DEM download enabled but colors/shadings not configured. Skipping DEM downloads."
                        )
                    else:
                        for model in models:
                            if model not in ("dtm", "dsm"):
                                logger.warning(
                                    f"Unknown DEM model '{model}' (expected dtm/dsm). Skipping."
                                )
                                continue

                            out_base = dtm_dir if model == "dtm" else dsm_dir

                            for color in colors:
                                for shading in shadings:
                                    fname = tmpl.format(color=color, shading=shading)
                                    tmp_raw = out_base / f"__tmp_raw_{fname}"
                                    final_out = out_base / fname

                                    asset_type = f"{model}/{color}/{shading}"
                                    ok = processor.download_asset_safe(
                                        project_id, current_task2_id, asset_type, tmp_raw
                                    )
                                    if ok:
                                        processor.run_gdalwarp(tmp_raw, final_out, epsg)
                                        try:
                                            tmp_raw.unlink(missing_ok=True)
                                        except Exception:
                                            pass

                        result["downloads"]["task2"]["dem_epsg"] = epsg
                        result["downloads"]["task2"]["dtm_dir"] = str(dtm_dir)
                        result["downloads"]["task2"]["dsm_dir"] = str(dsm_dir)

                pc_cfg = (exports_cfg.get("pointcloud") or {})
                pc_enabled = bool(pc_cfg.get("enabled", True))

                if pc_enabled:
                    pc_dir = task2_3d_dir
                    laz_candidates = list(
                        pc_cfg.get("asset_candidates") or ["georeferenced_model.laz"]
                    )
                    pdal_path = qgis_tools_cfg.get("pdal_path") or "pdal"

                    pc_out = processor.export_pointcloud(
                        project_id,
                        current_task2_id,
                        out_dir=pc_dir,
                        laz_archive_name=f"{task2_export_id}.laz",
                        ply_name=f"{task2_export_id}.ply",
                        pcd_name=f"{task2_export_id}.pcd",
                        candidates=laz_candidates,
                        pdal_path=pdal_path,
                    )

                    result["downloads"]["task2"]["pointcloud_laz"] = pc_out.get("laz")
                    result["downloads"]["task2"]["pointcloud_ply"] = pc_out.get("ply")
                    result["downloads"]["task2"]["pointcloud_pcd"] = pc_out.get("pcd")
                    result["downloads"]["task2"]["pointcloud_asset_type"] = pc_out.get("asset_type")

                    if not pc_out.get("laz") and bool(pc_cfg.get("required", True)):
                        raise RuntimeError("POINTCLOUD_DOWNLOAD_FAILED")

                else:
                    logger.info("Point cloud download skipped (pointcloud.enabled=false).")
                    result["downloads"]["task2"]["pointcloud_skipped"] = True

                if exports_cfg.get("all_assets_zip", {}).get("enabled", False):
                    zcfg = exports_cfg["all_assets_zip"]
                    out_dir = task2_odm_dir

                    task2_zip_override = self.export_name_overrides.get("task2")
                    if task2_zip_override:
                        fname = f"{task2_zip_override}-all.zip"
                    else:
                        fname = zcfg.get(
                            "filename_template",
                            "{survey_id}-RGB-{flag}-all.zip"
                        ).format(
                            survey_id=survey_id,
                            flag=task2_flag,
                        )

                    zip_path = out_dir / fname

                    ok = processor.download_all_assets_safe(
                        project_id, current_task2_id, zip_path
                    )
                    if ok:
                        result["downloads"]["task2"]["all_assets_zip"] = str(zip_path)
                    else:
                        logger.warning(
                            "All-assets zip was not downloaded (endpoint missing or failed)."
                        )

            self._clear_webodm_checkpoint()
            return result

        finally:
            if cached_dir is not None:
                self._cleanup_upload_cache(cached_dir, logger)

    def stage_qgis(self) -> Dict[str, Any]:
        logger = self.loggers["qgis"]
        logger.info("Stage: QGIS Processing (clip + tiles)")

        survey_id = self._require_survey_id()
        rgb_path = self._require_rgb_path()

        qgis_cfg = (self.config.get("qgis") or {})
        if not bool(qgis_cfg.get("enabled", True)):
            logger.info("QGIS stage disabled (qgis.enabled=false).")
            return {"skipped": True}

        ds = self.state.get("data_segregation") or {}
        dirs = ds.get("dirs") or {}
        if not dirs:
            raise RuntimeError("Missing data_segregation.dirs in state.")

        def dir_from_key(key: str, fallback: Optional[Path] = None) -> Path:
            p = dirs.get(key)
            if p:
                return Path(p)
            if fallback is not None:
                return Path(fallback)
            raise KeyError(f"Missing dir key in data_segregation.dirs: {key}")

        boundary_geojson_path = self.state.get("boundary_geojson_path")
        if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
            raise RuntimeError("QGIS stage requires boundary_geojson_path (missing).")

        web = self.state.get("webodm") or {}
        dls = web.get("downloads") or {}
        t1 = (dls.get("task1") or {})
        t2 = (dls.get("task2") or {})

        unbounded_ortho = t1.get("orthomosaic")
        bounded_ortho = t2.get("orthomosaic")

        main_ortho = None
        main_ortho_source = None

        if unbounded_ortho and Path(unbounded_ortho).exists():
            main_ortho = str(unbounded_ortho)
            main_ortho_source = "task1"
        elif bounded_ortho and Path(bounded_ortho).exists():
            main_ortho = str(bounded_ortho)
            main_ortho_source = "task2"
        else:
            raise RuntimeError(
                "QGIS stage requires an orthomosaic from Task 1 or Task 2, but none was found."
            )

        logger.info(f"QGIS main orthomosaic source: {main_ortho_source} -> {Path(main_ortho).name}")

        naming_cfg = self.config.get("naming", {})
        crossrun_flag = self.state.get("crossrun_flag") or naming_cfg.get("crossrun_mode", "xc")
        boundary_flag_task1 = naming_cfg.get("task1_boundary_mode", "xb")
        boundary_flag_task2 = naming_cfg.get("task2_boundary_mode", "b")
        task1_flag = f"{crossrun_flag}{boundary_flag_task1}"
        task2_flag = f"{crossrun_flag}{boundary_flag_task2}"

        clipped_ortho_dir = dir_from_key(
            "qgis_clipped_ortho",
            fallback=(rgb_path / "qgis" / "clipped" / "ortho")
        )
        tiles_sharp_dir = dir_from_key(
            "tiles_ortho_sharp",
            fallback=(rgb_path / "tiles" / "ortho" / "sharp-corners")
        )
        tiles_round_dir = dir_from_key(
            "tiles_ortho_round",
            fallback=(rgb_path / "tiles" / "ortho" / "round-corners")
        )

        clip_cfg = (qgis_cfg.get("clip") or {})
        clip_enabled = bool(clip_cfg.get("enabled", True))
        clip_tmpl = str(clip_cfg.get("filename_template") or "orthomosaic-clipped--{flag}.tif")

        unbounded_clipped = clipped_ortho_dir / clip_tmpl.format(flag=task1_flag)
        bounded_clipped = clipped_ortho_dir / clip_tmpl.format(flag=task2_flag)

        qgis_tools_cfg = (qgis_cfg.get("tools") or {})
        qgis_root = str(qgis_tools_cfg.get("qgis_root") or "")
        gdalwarp_path = str(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp")
        gdal2tiles_path = str(qgis_tools_cfg.get("gdal2tiles_path") or "gdal2tiles.py")

        tiles_cfg = (qgis_cfg.get("tiles") or {})
        tiles_enabled = bool(tiles_cfg.get("enabled", True))
        zoom = str(tiles_cfg.get("zoom") or "11-24")
        profile = str(tiles_cfg.get("profile") or "mercator")
        webviewer = str(tiles_cfg.get("webviewer") or "none")
        copyright_text = str(tiles_cfg.get("copyright") or "ASIMOV-HAWKS")

        dst_nodata_raw = (clip_cfg.get("dst_nodata") or "")
        dst_nodata = None
        if isinstance(dst_nodata_raw, (int, float)):
            dst_nodata = float(dst_nodata_raw)
        elif isinstance(dst_nodata_raw, str) and dst_nodata_raw.strip() != "":
            try:
                dst_nodata = float(dst_nodata_raw.strip())
            except ValueError:
                raise ValueError(
                    f"Invalid QGIS_CLIP_DST_NODATA value: '{dst_nodata_raw}' (must be a number or empty)"
                )

        tools = QGISTools(
            logger=logger,
            qgis_root=qgis_root,
            gdalwarp_path=gdalwarp_path,
            gdal2tiles_path=gdal2tiles_path,
        )

        # 1) Clip main ortho
        if clip_enabled:
            logger.info(f"Clipping MAIN ortho ({main_ortho_source}) -> {unbounded_clipped.name}")
            tools.clip_raster_by_mask(
                input_tif=Path(main_ortho),
                mask_geojson=Path(boundary_geojson_path),
                output_tif=unbounded_clipped,
                dst_nodata=dst_nodata,
            )
        else:
            logger.warning(
                "QGIS clip disabled (qgis.clip.enabled=false). Using main orthomosaic directly."
            )
            unbounded_clipped = Path(main_ortho)

        # 2) Clip bounded separately only if Task 2 is not already the main source
        bounded_ok = False
        if main_ortho_source == "task2":
            logger.info(
                "Task 2 orthomosaic is already the main QGIS source; skipping duplicate bounded clip."
            )
        elif bounded_ortho and Path(bounded_ortho).exists():
            if clip_enabled:
                logger.info(f"Clipping BOUNDED ortho -> {bounded_clipped.name}")
                tools.clip_raster_by_mask(
                    input_tif=Path(bounded_ortho),
                    mask_geojson=Path(boundary_geojson_path),
                    output_tif=bounded_clipped,
                    dst_nodata=dst_nodata,
                )
                bounded_ok = True
            else:
                bounded_clipped = Path(bounded_ortho)
                bounded_ok = True
        else:
            logger.warning(
                "Bounded orthomosaic missing. Skipping bounded clip + round-corners tiles."
            )

        # 3) Tiles
        if tiles_enabled:
            logger.info(
                f"Generating tiles (sharp-corners) from {Path(unbounded_clipped).name}"
            )
            tools.generate_tiles(
                input_tif=Path(unbounded_clipped),
                output_dir=tiles_sharp_dir,
                zoom=zoom,
                profile=profile,
                webviewer=webviewer,
                copyright_text=copyright_text,
                clean=True,
                resume=False,
            )

            if bounded_ok:
                logger.info(
                    f"Generating tiles (round-corners) from {Path(bounded_clipped).name}"
                )
                tools.generate_tiles(
                    input_tif=Path(bounded_clipped),
                    output_dir=tiles_round_dir,
                    zoom=zoom,
                    profile=profile,
                    webviewer=webviewer,
                    copyright_text=copyright_text,
                    clean=True,
                    resume=False,
                )
        else:
            logger.warning(
                "QGIS tiles disabled (qgis.tiles.enabled=false). Skipping tile generation."
            )

        return {
            "boundary_geojson": str(boundary_geojson_path),
            "main_ortho_source": main_ortho_source,
            "tools": {
                "gdalwarp_path": gdalwarp_path,
                "gdal2tiles_path": gdal2tiles_path,
            },
            "clip": {
                "enabled": clip_enabled,
                "dst_nodata": dst_nodata,
                "filename_template": clip_tmpl,
            },
            "tiles": {
                "enabled": tiles_enabled,
                "zoom": zoom,
                "profile": profile,
                "webviewer": webviewer,
                "copyright": copyright_text,
            },
            "unbounded": {
                "input": str(main_ortho),
                "clipped": str(unbounded_clipped),
                "tiles_dir": str(tiles_sharp_dir) if tiles_enabled else None,
            },
            "bounded": {
                "input": str(bounded_ortho) if bounded_ortho else None,
                "clipped": str(bounded_clipped) if bounded_ok else None,
                "tiles_dir": str(tiles_round_dir) if (tiles_enabled and bounded_ok) else None,
            },
        }
    
    def run_task4_fallback(self) -> Dict[str, Any]:
        logger = self.loggers["webodm"]
        logger.info("Running WebODM Task 4 fallback inside same project")

        survey_id = self._require_survey_id()
        rgb_path = self._require_rgb_path()

        web = self.state.get("webodm") or {}
        project_id = web.get("project_id")
        project_name = web.get("project_name") or survey_id

        if not project_id:
            raise RuntimeError("Task 4 fallback requires existing WebODM project_id.")

        boundary_geojson_path = self.state.get("boundary_geojson_path")
        if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
            raise RuntimeError("Task 4 fallback requires boundary GeoJSON.")

        ds = self.state.get("data_segregation") or {}
        dirs = ds.get("dirs") or {}
        image_folder = Path(dirs.get("path") or (rgb_path / "images" / "path"))

        boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")

        webodm_cfg = self.config["webodm"]
        exports_cfg = self.config.get("exports", {})
        qgis_tools_cfg = (exports_cfg.get("tools") or {})

        task4_options = dict(
            webodm_cfg.get("task4_options", {})
        )

        task4_options["boundary"] = boundary_geojson
        task4_name = f"{survey_id}-RGB--task4"
        task4_root_dir = rgb_path / survey_id
        task4_ortho_dir = task4_root_dir / "ortho"
        task4_ortho_dir.mkdir(parents=True, exist_ok=True)

        processor = WebODMProcessor(
            url=webodm_cfg["url"],
            username=webodm_cfg["username"],
            password=webodm_cfg["password"],
            logger=logger,
        )

        existing_task4_id = processor.find_task_by_name(int(project_id), task4_name)

        if existing_task4_id:
            logger.info(f"Found existing Task 4: {existing_task4_id}")
            current_task4_id = existing_task4_id
            status = processor.get_task_status(int(project_id), current_task4_id)

            if status != "completed":
                success, runtime, _info = processor.wait_for_completion(
                    int(project_id),
                    current_task4_id,
                    live=False,
                    control_check=lambda: self._check_control_or_raise("webodm"),
                )
            else:
                success = True
                runtime = 0.0
        else:
            current_task4_id = processor.create_task_with_images(
                project_id=int(project_id),
                name=task4_name,
                image_folder=str(image_folder),
                options=task4_options,
                processing_node=webodm_cfg.get("node_id"),
            )

            success, runtime, _info = processor.wait_for_completion(
                int(project_id),
                current_task4_id,
                live=False,
                control_check=lambda: self._check_control_or_raise("webodm"),
            )

        task4_state = {
            "id": str(current_task4_id),
            "name": task4_name,
            "success": bool(success),
            "runtime_seconds": float(runtime),
        }

        web["task4"] = task4_state

        downloads = web.setdefault("downloads", {})
        task4_downloads = downloads.setdefault("task4", {})

        if exports_cfg.get("enabled", False) and exports_cfg.get("ortho", {}).get("enabled", False):
            ortho_cfg = exports_cfg["ortho"]
            epsg = int(ortho_cfg.get("reproject_epsg", 4326))
            candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

            out_path = processor.export_orthomosaic(
                int(project_id),
                str(current_task4_id),
                out_dir=task4_ortho_dir,
                filename=f"{survey_id}.tif",
                epsg=epsg,
                candidates=candidates,
                gdalwarp_path=(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp"),
            )

            if out_path:
                task4_downloads["orthomosaic"] = str(out_path)
                task4_downloads["epsg"] = epsg
            else:
                logger.warning("Could not download Task 4 orthomosaic.")

        self.state["webodm"] = web

        return {
            "task4": task4_state,
            "downloads": task4_downloads,
            "project_id": project_id,
            "project_name": project_name,
        }
    

    def stage_quality_gate(self) -> Dict[str, Any]:
        logger = self.loggers["pipeline"]
        set_stage_context(logger, stage_name="quality_gate")
        logger.info("Stage: Quality Gate Check")

        survey_id = self.survey_id or "?"
        max_restarts = 3
        restarts = 0

        web = self.state.get("webodm") or {}
        project_id = web.get("project_id")

        if not project_id:
            raise RuntimeError(
                "Quality gate cannot run: missing webodm.project_id in pipeline state."
            )

        task1 = web.get("task1") or {}
        task2 = web.get("task2") or {}
        task4 = web.get("task4") or {}

        if not task1.get("id") and not task2.get("id") and not task4.get("id"):
            logger.error(
                "Quality gate cannot run: missing WebODM task IDs. "
                "Please check if WebODM Docker is running and if the node worker is online."
            )
            return {
                "passed": False,
                "reason": "missing_webodm_task_ids",
                "project_id": project_id,
            }

        allowed_stages = {
            # alias          : webodm internal name
            "dataset": "dataset",
            "load_dataset": "load_dataset",
            "sfm": "opensfm",
            "opensfm": "opensfm",
            "structure_from_motion": "opensfm",
            "openmvs": "openmvs",
            "multi_view_stereo": "openmvs",
            "filterpoints": "odm_filterpoints",
            "odm_filterpoints": "odm_filterpoints",
            "meshing": "odm_meshing",
            "odm_meshing": "odm_meshing",
            "texturing": "mvs_texturing",
            "mvs_texturing": "mvs_texturing",
            "georeferencing": "odm_georeferencing",
            "odm_georeferencing": "odm_georeferencing",
            "dem": "odm_dem",
            "odm_dem": "odm_dem",
            "orthophoto": "odm_orthophoto",
            "odm_orthophoto": "odm_orthophoto",
        }

        def _pick_default_task() -> tuple[str, str]:
            if task2.get("id"):
                return str(task2["id"]), str(task2.get("name") or "task2")
            return str(task1["id"]), str(task1.get("name") or "task1")

        def restart_and_wait(
            task_id_to_restart: str,
            task_name_to_restart: str,
            restart_from: str,
        ) -> Dict[str, Any]:
            nonlocal restarts, web, task1, task2

            restarts += 1
            if restarts > max_restarts:
                logger.error("Maximum WebODM restart attempts exceeded.")
                return {
                    "passed": False,
                    "restarts": restarts,
                    "reason": "max_restarts_exceeded",
                }

            webodm_cfg = self.config["webodm"]
            processor = WebODMProcessor(
                url=webodm_cfg["url"],
                username=webodm_cfg["username"],
                password=webodm_cfg["password"],
                logger=self.loggers["webodm"],
            )

            logger.warning(
                f"Requesting WebODM internal restart | project_id={project_id} "
                f"task_id={task_id_to_restart} restart_from={restart_from} "
                f"| attempt {restarts}/{max_restarts}"
            )

            processor.restart_task(
                project_id=int(project_id),
                task_id=str(task_id_to_restart),
                restart_from=restart_from,
            )
            success, runtime, task_info = processor.wait_for_completion(
                int(project_id), str(task_id_to_restart), control_check=lambda: self._check_control_or_raise("webodm"),
            )

            updated_task_state = {
                "id": str(task_id_to_restart),
                "name": task_name_to_restart,
                "success": bool(success),
                "runtime_seconds": float(runtime),
                "restart_from": restart_from,
                "restart_attempt": restarts,
                "status": task_info.get("status"),
            }

            if task2.get("id") and str(task2.get("id")) == str(task_id_to_restart):
                task2.update(updated_task_state)
                web["task2"] = task2
            elif task1.get("id") and str(task1.get("id")) == str(task_id_to_restart):
                task1.update(updated_task_state)
                web["task1"] = task1

            self.state["webodm"] = web
            return {"passed": None, "restarts": restarts, "task": updated_task_state}

        # ── Interactive loop ─────────────────────────────────────────
        while True:
            web = self.state.get("webodm") or {}
            task1 = web.get("task1") or {}
            task2 = web.get("task2") or {}

            default_task_id, default_task_name = _pick_default_task()

            raw = quality_gate_prompt(
                logger=logger,
                survey_id=survey_id,
                project_id=int(project_id),
                task1=task1,
                task2=task2 if task2.get("id") else {},
                webodm_url=self.config.get("webodm", {}).get("url", ""),
            )

            if raw in ("yes", "y"):
                logger.info("Quality gate PASSED by user.")
                return {"passed": True, "restarts": restarts, "project_id": project_id}

            if raw in ("fail", "f"):
                logger.warning("Quality gate FAILED by user.")
                return {"passed": False, "restarts": restarts, "project_id": project_id}

            if raw in ("fallback", "task4"):
                logger.warning("User requested Task 4 fallback workflow.")
                try:
                    task4_result = self.run_task4_fallback()
                except Exception:
                    logger.exception("Task 4 fallback failed.")
                    return {
                        "passed": False,
                        "restarts": restarts,
                        "project_id": project_id,
                        "task4_failed": True,
                    }

                task4_state = task4_result.get("task4") or {}
                if task4_state.get("success"):
                    logger.info("Task 4 fallback completed successfully.")
                    return {
                        "passed": True,
                        "restarts": restarts,
                        "project_id": project_id,
                        "task4": task4_state,
                    }

                logger.warning("Task 4 fallback ran but did not succeed.")
                return {
                    "passed": False,
                    "restarts": restarts,
                    "project_id": project_id,
                    "task4": task4_state,
                }

            if raw == "restart":
                res = restart_and_wait(
                    default_task_id, default_task_name, "dataset")
                if res.get("passed") is False:
                    return res
                continue

            if raw.startswith("restart "):
                parts = raw.split()

                if len(parts) not in (2, 3):
                    logger.warning(
                        "Invalid format. Use: restart | restart t1|t2 | restart t1|t2 <stage>")
                    continue

                target = parts[1]
                stage = parts[2] if len(parts) == 3 else "load_dataset"

                if target not in ("t1", "t2"):
                    logger.warning("Invalid target. Use t1 or t2.")
                    continue

                if stage not in allowed_stages:
                    logger.warning(
                        f"Invalid stage '{stage}'. "
                        f"Valid stages: {', '.join(sorted(set(allowed_stages.values())))}"
                    )
                    continue

                chosen = task1 if target == "t1" else task2
                if not chosen or not chosen.get("id"):
                    logger.warning(f"{target} does not exist for this run.")
                    continue

                webodm_stage = allowed_stages[stage]

                res = restart_and_wait(
                    str(chosen["id"]),
                    str(chosen.get("name") or target),
                    webodm_stage,
                )

                if res.get("passed") is False:
                    return res
                continue

            logger.warning(
                "Unrecognised input. Use: yes | fail | fallback | restart | restart t1|t2 [stage]"
            )
            
    # RUN
    def run(
        self,
        *,
        resume: bool = True,
        force_stages: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        pipeline_logger = self.loggers["pipeline"]
        pipeline_header(pipeline_logger, self.run_id)

        if not resume:
            self.control.cleanup_flags()
        else:
            self.control.clear_abort()
            self.control.clear_pause()

        self.control.start_hotkeys(pipeline_logger)

        # Resume a previously paused run
        try:
            r = self.repo.get_run(self.run_id)
            if r and r.get("status") == "paused":
                self.repo.mark_run_running(self.run_id)
                pipeline_logger.info(
                    "Resuming paused run → status set to running")
        except Exception:
            pipeline_logger.exception(
                "Failed while attempting to resume paused run")

        total_start = time.perf_counter()
        force_stages = force_stages or set()

        def _force(name: str) -> bool:
            return (name in force_stages) or (not resume)

        try:

            self._check_control_or_raise("data_segregation")

            if self._stage_will_run(
                "data_segregation",
                force=_force("data_segregation"),
            ):
                self._preflight_stage("data_segregation")

            self.runner.run(
                "data_segregation",
                self.stage_data_segregation,
                output_key="data_segregation",
                state=self.state,
                force=_force("data_segregation"),
            )
            self._hydrate_from_state()


            self._check_control_or_raise("cross_run_filter")

            if self._stage_will_run(
                "cross_run_filter",
                force=_force("cross_run_filter"),
            ):
                self._preflight_stage("cross_run_filter")

            self.runner.run(
                "cross_run_filter",
                self.stage_cross_run_image_filter,
                output_key="cross_run_filter",
                state=self.state,
                force=_force("cross_run_filter"),
            )
            self._hydrate_from_state()


            self._check_control_or_raise("kml_boundary")

            if self._stage_will_run(
                "kml_boundary",
                force=_force("kml_boundary"),
            ):
                self._preflight_stage("kml_boundary")

            self.runner.run(
                "kml_boundary",
                self.stage_kml_boundary,
                output_key="kml_boundary",
                state=self.state,
                force=_force("kml_boundary"),
            )
            self._hydrate_from_state()


            self._check_control_or_raise("webodm")

            if self._stage_will_run(
                "webodm",
                force=_force("webodm"),
            ):
                self._preflight_stage("webodm")

            self.runner.run(
                "webodm",
                self.stage_webodm,
                output_key="webodm",
                state=self.state,
                force=_force("webodm"),
                stale_running_policy="rerun",   # preserve partial state on resume
            )
            self._hydrate_from_state()

            self._check_control_or_raise("quality_gate")

            if self._stage_will_run(
                "quality_gate",
                force=_force("quality_gate"),
            ):
                self._preflight_stage("quality_gate")
                
            self.runner.run(
                "quality_gate",
                self.stage_quality_gate,
                output_key="quality_gate",
                state=self.state,
                force=_force("quality_gate"),
            )
            self._hydrate_from_state()

            q = self.state.get("quality_gate") or {}
            if q.get("passed") is False:
                reason = q.get("reason") or "quality_gate_failed"
                raise RuntimeError(
                    f"Pipeline stopped: Quality Gate failed ({reason})."
                )

            self._check_control_or_raise("qgis")

            if self._stage_will_run(
                "qgis",
                force=_force("qgis"),
            ):
                self._preflight_stage("qgis")

            self.runner.run(
                "qgis",
                self.stage_qgis,
                output_key="qgis",
                state=self.state,
                force=_force("qgis"),
            )

            self.state["success"] = True
            total_runtime = time.perf_counter() - total_start

            self.repo.mark_run_finished(
                self.run_id, success=True, total_runtime_seconds=total_runtime
            )
            if self.survey_id:
                self.repo.mark_survey_finished(
                    self.survey_id, success=True, total_runtime_seconds=total_runtime
                )

            self.control.cleanup_flags()
            pipeline_footer(pipeline_logger, total_runtime, success=True)
            return self.state

        # ── Paused ────────────────────────────────────────────────
        except RuntimeError as e:
            if str(e) == "__PIPELINE_PAUSED__":
                self.state.update(
                    {"success": False, "paused": True, "error": "paused_by_flag"})
                pipeline_paused(
                    pipeline_logger,
                    self.run_id,
                    after_stage=self.state.get("paused_after_stage", "?"),
                )
                # Clear only the pause flag (not abort) so a future --resume
                # doesn't immediately re-trip __PIPELINE_PAUSED__ again.
                self.control.clear_pause()
                return self.state

            # ── Canceled (WebODM UI) ───────────────────────────────
            if str(e) == "__PIPELINE_CANCELED__":
                self.state.update(
                    {"success": False, "canceled": True,
                        "error": "canceled_in_webodm_ui"}
                )
                pipeline_canceled(pipeline_logger, self.run_id)
                try:
                    self.repo.mark_run_paused(
                        self.run_id,
                        paused_after_stage="webodm",
                        reason="webodm_ui_cancel",
                    )
                except Exception:
                    pipeline_logger.exception(
                        "Failed to mark run paused after WebODM cancel"
                    )
                return self.state

            if str(e) == "__PIPELINE_ABORTED__":
                self.state.update(
                    {
                        "success": False,
                        "aborted": True,
                        "error": "aborted_by_hotkey",
                    }
                )

                total_runtime = time.perf_counter() - total_start

                try:
                    self.repo.mark_run_finished(
                        self.run_id,
                        success=False,
                        total_runtime_seconds=total_runtime,
                    )
                except Exception:
                    pipeline_logger.exception("Failed to mark aborted run as finished")

                if self.survey_id:
                    try:
                        self.repo.mark_survey_finished(
                            self.survey_id,
                            success=False,
                            total_runtime_seconds=total_runtime,
                        )
                    except Exception:
                        pipeline_logger.exception("Failed to mark aborted survey as finished")

                self.control.cleanup_flags()
                pipeline_footer(pipeline_logger, total_runtime, success=False)
                return self.state
        
            raise
        
        # ── Failure ───────────────────────────────────────────────
        except Exception as e:
            self.state.update(
                {
                    "success": False,
                    "error": str(e),
                }
            )

            total_runtime = time.perf_counter() - total_start

            try:
                self.repo.mark_run_finished(
                    self.run_id,
                    success=False,
                    total_runtime_seconds=total_runtime,
                )
            except Exception:
                pipeline_logger.exception("Failed to mark run as failed")

            if self.survey_id:
                try:
                    self.repo.mark_survey_finished(
                        self.survey_id,
                        success=False,
                        total_runtime_seconds=total_runtime,
                    )
                except Exception:
                    pipeline_logger.exception("Failed to mark survey as failed")

            self.control.cleanup_flags()
            pipeline_footer(pipeline_logger, total_runtime, success=False)
            pipeline_logger.error(str(e))
            return self.state