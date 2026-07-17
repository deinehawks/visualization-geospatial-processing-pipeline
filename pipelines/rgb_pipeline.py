from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Set, List, Tuple, Mapping
from shared.logging import quality_gate_prompt, pipeline_header, pipeline_footer, pipeline_paused, pipeline_canceled, set_stage_context
from shared.constants import WEBODM_RESTART_STAGES, WEBODM_RESTART_STAGE_NAMES
from shared.logging import get_logger
from shared.db.repo import PipelineRepo
from shared.stage_runner import StageRunner
from shared.pipeline_control import PipelineControl
from shared.preflight_checks import PipelinePreflight, PreflightError
from shared.paths import db_path

from modules.kml_boundary_setter.kml_boundary_setter import run_kml
from modules.webodm.webodm_processor import WebODMProcessor
from modules.cross_run_image_filter.cross_run_image_filter import run_filter
from modules.data_segregation.data_segregation import run_data_segregation
from modules.qgis.qgis_tools import QGISTools

from pipelines.rgb_helpers import (
    RGBTaskNamingMixin,
    RGBOrthomosaicSelectionMixin,
    RGBUploadCacheMixin,
)

import time
import uuid
import logging
import os
import shutil
import tempfile
from typing import Any


class RGBPipeline(
    RGBTaskNamingMixin,
    RGBOrthomosaicSelectionMixin,
    RGBUploadCacheMixin,
):
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
        config: Mapping[str, Any],
        *,
        source_dir: Path,
        surveys_root: Path,
        year: int,
        run_id: Optional[str] = None,
        
        survey_id_override: Optional[str] = None,
        task_name_overrides: Optional[Dict[str, str]] = None,
        export_name_overrides: Optional[Dict[str, str]] = None,
        crossrun_enabled_override: Optional[bool] = None,
        skip_task1_webodm: bool = False,
        task1_bounded: bool = False,
        force_segregation: bool = False,
        webodm_mode: str = "task4",   # "task2" | "task4" | "both"
        use_year_subdir_override: Optional[bool] = None,
        db_file: Optional[Path] = None,
        repository: Optional[PipelineRepo] = None,
        loggers: Optional[Mapping[str, logging.Logger]] = None,
        logs_dir: Optional[Path] = None,
        checkpoint_dir: Optional[Path] = None,
        webodm_processor: Optional[Any] = None,
    ):
        if not isinstance(config, Mapping):
            raise TypeError("config must be a mapping")
        if repository is not None and db_file is not None:
            raise ValueError("repository and db_file are mutually exclusive")

        logger_names = {
            "pipeline",
            "segregation",
            "cross_run_filter",
            "kml",
            "webodm",
            "qgis",
        }
        if loggers is not None:
            missing_loggers = sorted(logger_names.difference(loggers))
            if missing_loggers:
                raise ValueError(
                    "loggers is missing required entries: "
                    + ", ".join(missing_loggers)
                )

        self.base_dir = Path(base_dir)
        self.config = config
        self.source_dir = Path(source_dir)
        self.surveys_root = Path(surveys_root)
        self.year = int(year)
        self.run_id = run_id or str(uuid.uuid4())
        self.survey_id: Optional[str] = None
        self.logs_dir = (
            Path(logs_dir)
            if logs_dir is not None
            else self.base_dir / "data" / "logs"
        )
        self.checkpoint_dir = (
            Path(checkpoint_dir)
            if checkpoint_dir is not None
            else self.logs_dir
        )
        self.webodm_processor = webodm_processor

        self.survey_id_override = survey_id_override
        self.task_name_overrides = task_name_overrides or {}
        self.export_name_overrides = export_name_overrides or {}
        self.crossrun_enabled_override = crossrun_enabled_override
        self.use_year_subdir_override = use_year_subdir_override

        self.skip_task1_webodm = skip_task1_webodm
        self.task1_bounded     = task1_bounded
        self.force_segregation = force_segregation
        self.webodm_mode       = webodm_mode.strip().lower()

        # Derive task skip flags from webodm_mode
        self.skip_task2_webodm = self.webodm_mode == "task4"
        self.skip_task4_webodm = self.webodm_mode == "task2"
        self.control = PipelineControl(
            self.base_dir,
            self.run_id,
        )

        if loggers is None:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            self.loggers: Mapping[str, logging.Logger] = {
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
        else:
            self.loggers = loggers

        self.state: Dict[str, Any] = {
            "run_id": self.run_id,
        }

        if repository is None:
            repository = PipelineRepo(
                Path(db_file) if db_file is not None else db_path(self.base_dir)
            )
        self.repo = repository
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
    def _create_webodm_processor(self, logger: logging.Logger) -> Any:
        if self.webodm_processor is not None:
            return self.webodm_processor

        webodm_cfg = self.config["webodm"]
        return WebODMProcessor(
            url=webodm_cfg["url"],
            username=webodm_cfg["username"],
            password=webodm_cfg["password"],
            logger=logger,
        )

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

        if not state.get("selected_orthomosaic"):
            qg = state.get("quality_gate") or {}
            sel = qg.get("selected_orthomosaic")
            if sel and isinstance(sel, dict):
                state["selected_orthomosaic"] = sel
                self.loggers["pipeline"].info(
                    f"Restored selected orthomosaic from quality_gate state: "
                    f"task={sel.get('task_key')} | file={sel.get('source_filename')}"
                )
            if not state.get("selected_webodm_task"):
                task_key = qg.get("selected_webodm_task")
                if task_key:
                    state["selected_webodm_task"] = task_key

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
            skip_task4_webodm=self.skip_task4_webodm,
        )

        self.loggers["pipeline"].info(
            f"PREFLIGHT OK | {stage_name} | {result}"
        )

    # Checkpoint helpers
    def _webodm_checkpoint_path(self) -> Path:
        return self.checkpoint_dir / f"webodm_checkpoint_{self.run_id}.json"

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
        # Naming semantics:
        # c  = crossrun/crosspath images were included
        # xc = crossrun/crosspath images were excluded by the filter
        #
        # If the filter is enabled and applied, use "xc" even when total_excluded is 0.
        # A zero exclusion count only means no crossrun images were detected, not that
        # the filter was bypassed.
        naming_cfg = self.config.get("naming", {})
        crossrun_flag = str(naming_cfg.get("crossrun_mode") or "xc")

        if crossrun_flag not in {"c", "xc"}:
            raise ValueError(f"Invalid crossrun flag: {crossrun_flag!r}")


        self.state["crossrun_flag"] = crossrun_flag
        result["crossrun_flag"] = crossrun_flag
        result["crossrun_excluded_count"] = excluded

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

        boundary_available = bool(self.state.get("boundary_available"))
        boundary_geojson_path = self.state.get("boundary_geojson_path")

        task1_flag = self._webodm_task_flag(
            "task1",
            default_boundary_mode="xb",
        )

        task2_flag = self._webodm_task_flag(
            "task2",
            default_boundary_mode="b",
        )

        task1_name = self.task_name_overrides.get("task1")
        task2_name = self.task_name_overrides.get("task2")

        if not task1_name:
            task1_name = self._webodm_task_name(
                survey_id=survey_id,
                flag=task1_flag,
                task_key="task1",
            )

        if not task2_name:
            task2_name = self._webodm_task_name(
                survey_id=survey_id,
                flag=task2_flag,
                task_key="task2",
            )

        task1_export_id = self.export_name_overrides.get("task1", task1_name)
        task2_export_id = self.export_name_overrides.get("task2", task2_name)

        skip_task1 = bool(getattr(self, "skip_task1_webodm", False))
        skip_task2 = bool(getattr(self, "skip_task2_webodm", False))
        skip_task4 = bool(getattr(self, "skip_task4_webodm", False))
        task1_bounded = bool(getattr(self, "task1_bounded", False))

        if skip_task1 and skip_task2 and skip_task4:
            raise RuntimeError("All WebODM tasks are skipped. Nothing to run.")

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

                self._remember_webodm_upload_folder(
                    upload_folder=upload_folder,
                    cached_dir=cached_dir,
                    source_folder=image_folder,
                    fallback_direct=False,
                )

            except Exception as e:
                logger.warning(
                    f"Upload cache unavailable, uploading directly from source. reason={e}"
                )
                cached_dir = None
                upload_folder = image_folder

                self._remember_webodm_upload_folder(
                    upload_folder=upload_folder,
                    cached_dir=None,
                    source_folder=image_folder,
                    fallback_direct=True,
                    fallback_reason=str(e),
                )

            processor = self._create_webodm_processor(logger)


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

            # Update state with project info so Task 4 can find it if run as primary
            self.state["webodm"] = {
                "project_id": project_id,
                "project_name": project_name,
                "task1": prev_task1 if prev_project_id else (None if skip_task1 else {}),
                "task2": prev_task2 if prev_project_id else None,
                "task4": prev_web.get("task4") or {},
                "downloads": prev_web.get("downloads") or {"task1": {}, "task2": {}, "task4": {}},
            }

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
                    filename = self._orthomosaic_filename(
                        task_key="task1",
                        flag=task1_flag,
                    )

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
            elif not boundary_available:
                msg = "Boundary not available. Skipping Task 2 (bounded models)."
                logger.warning(msg)
                result["boundary_reason"] = msg
            elif not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                msg = "Boundary flag is True but GeoJSON path is missing. Skipping Task 2."
                logger.warning(msg)
                result["boundary_reason"] = msg
            else:
                # Task 2 can run - boundary is available and not skipped
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

                # Update state with Task 2 results
                self.state["webodm"]["task2"] = result["task2"]
                self.state["webodm"]["downloads"] = result["downloads"]
    
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
                        filename = self._orthomosaic_filename(
                            task_key="task2",
                            flag=task2_flag,
                        )
    
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
    
                        selected = self._set_selected_orthomosaic(
                            task_key="task2",
                            source_path=out_path,
                            flag=task2_flag,
                            boundary_used=True,
                            fallback_used=False,
                        )
    
                        result["selected_webodm_task"] = "task2"
                        result["selected_orthomosaic"] = selected
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
                            pcd_name=f"{task2_export_id}.pcd",
                            candidates=laz_candidates,
                            max_points=int(pc_cfg.get("max_points", 3_000_000)),
                            viewpoint=str(pc_cfg.get("viewpoint", "0 0 0 1 0 0 0")),
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

            # ---------------- Quality Gate after Task 2 (when both tasks run) ----------------
            # If both Task 2 and Task 4 are enabled, run a quality gate after Task 2
            # to allow user to review before continuing to Task 4
            if not skip_task2 and not skip_task4:
                logger.info("Running intermediate quality gate after Task 2 (before Task 4)...")
                # Call the quality gate stage method directly
                try:
                    qg_result = self.stage_quality_gate()
                    if not qg_result.get("passed"):
                        logger.warning(
                            "Intermediate quality gate failed. Skipping Task 4 and returning."
                        )
                        result["quality_gate_intermediate"] = qg_result
                        result["task4_skip_reason"] = "Quality gate failed after Task 2"
                        self._clear_webodm_checkpoint()
                        return result
                    else:
                        logger.info("Intermediate quality gate passed. Continuing to Task 4...")
                        result["quality_gate_intermediate"] = qg_result
                except Exception as e:
                    logger.exception("Intermediate quality gate failed with exception.")
                    result["quality_gate_intermediate"] = {"passed": False, "error": str(e)}
                    result["task4_skip_reason"] = f"Quality gate exception: {e}"
                    self._clear_webodm_checkpoint()
                    return result

            # ---------------- TASK 4 (primary, not fallback) ----------------
            skip_task4 = bool(getattr(self, "skip_task4_webodm", True))

            if skip_task4:
                logger.info("Skipping WebODM Task 4 by request.")
            elif not boundary_available:
                msg = "Boundary not available. Skipping Task 4 (requires bounded model)."
                logger.warning(msg)
                result["task4_skip_reason"] = msg
            elif not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                msg = "Boundary GeoJSON path is missing. Skipping Task 4."
                logger.warning(msg)
                result["task4_skip_reason"] = msg
            else:
                # Task 4 can run - boundary is available and not skipped
                logger.info(
                    "Running Task 4 as primary task "
                    f"(webodm_mode={getattr(self, 'webodm_mode', 'both')})."
                )
                try:
                    t4_result = self.run_webodm_fallback_task(
                        task_key="task4",
                        fallback_reason="primary_task4",
                    )
                    result["task4"] = t4_result.get("task4")
                    result["downloads"]["task4"] = t4_result.get("downloads") or {}
                    result["selected_webodm_task"] = t4_result.get("selected_webodm_task")
                    result["selected_orthomosaic"] = t4_result.get("selected_orthomosaic")
                except RuntimeError as e:
                    if str(e) in ("__PIPELINE_PAUSED__", "__PIPELINE_ABORTED__"):
                        raise
                    logger.exception("Task 4 primary run failed.")
                    result["task4_failed"] = True
                    result["task4_error"] = str(e)

                    # If Task 4 is the only task running and it failed, fail the entire stage
                    if skip_task1 and skip_task2:
                        logger.error(
                            "Task 4 was the only WebODM task enabled and it failed. "
                            "Failing the WebODM stage."
                        )
                        self._clear_webodm_checkpoint()
                        raise

            self._clear_webodm_checkpoint()
            return result

        finally:
            # Do not clean the upload cache here.
            # Quality Gate may trigger a fallback WebODM task, which should reuse
            # the same staged upload folder instead of re-copying/re-uploading images.
            pass

    def _stage_webodm_upload_images(
        self,
        *,
        source_folder: Path,
        survey_id: str,
        stage_name: str,
    ) -> Path:
        source_folder = Path(source_folder)
        logger = self.loggers["webodm"]

        if not source_folder.exists():
            raise FileNotFoundError(f"Image source folder not found: {source_folder}")

        image_files = []
        for pattern in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG"):
            image_files.extend(source_folder.glob(pattern))

        # Deduplicate by filename, matching WebODM upload behavior.
        unique_images: dict[str, Path] = {}
        for image in image_files:
            if image.is_file():
                unique_images.setdefault(image.name.lower(), image)

        image_files = sorted(unique_images.values(), key=lambda p: p.name.lower())

        if not image_files:
            raise RuntimeError(f"No JPG/JPEG images found in {source_folder}")

        cache_root_env = os.getenv("WEBODM_UPLOAD_CACHE_ROOT")
        cache_root = (
            Path(cache_root_env)
            if cache_root_env
            else self.base_dir / "data" / "upload_cache"
        )

        target_folder = cache_root / survey_id / stage_name

        logger.info(f"Staging WebODM upload images to local cache: {target_folder}")

        if target_folder.exists():
            shutil.rmtree(target_folder)

        target_folder.mkdir(parents=True, exist_ok=True)

        total_bytes = 0

        for index, source_image in enumerate(image_files, start=1):
            target_image = target_folder / source_image.name

            try:
                shutil.copy2(source_image, target_image)
                total_bytes += target_image.stat().st_size
            except Exception as e:
                raise RuntimeError(
                    f"Failed to stage image for WebODM upload: {source_image}"
                ) from e

            if index % 50 == 0 or index == len(image_files):
                logger.info(
                    f"Staged {index}/{len(image_files)} images "
                    f"({total_bytes / (1024 ** 3):.2f} GB)"
                )

        logger.info(
            f"WebODM upload staging complete: {len(image_files)} images "
            f"| {total_bytes / (1024 ** 3):.2f} GB"
        )

        return target_folder

    def stage_qgis(self, *, resume: bool = False) -> Dict[str, Any]:
        logger = self.loggers["qgis"]
        logger.info("Stage: QGIS Processing (selected orthomosaic clip + tiles)")

        rgb_path = self._require_rgb_path()

        qgis_cfg = self.config.get("qgis") or {}
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

        def optional_dir_from_keys(
            keys: list[str],
            *,
            fallback: Path,
        ) -> Path:
            for key in keys:
                value = dirs.get(key)
                if value:
                    return Path(value)

            return Path(fallback)

        selected = self._get_selected_orthomosaic()

        task_key = str(selected.get("task_key") or "").strip()
        flag = str(selected.get("flag") or "").strip()
        source_path = Path(str(selected.get("source_path") or ""))
        boundary_used = bool(selected.get("boundary_used"))
        tile_mode = "round-corners" if boundary_used else "soft-corners"

        if not task_key:
            raise RuntimeError("Selected orthomosaic is missing task_key.")

        if not flag:
            raise RuntimeError("Selected orthomosaic is missing flag.")

        if not source_path.exists():
            raise FileNotFoundError(
                f"Selected orthomosaic does not exist: {source_path}"
            )

        logger.info(
            "QGIS selected orthomosaic: "
            f"task={task_key} | file={source_path.name} | "
            f"boundary_used={boundary_used} | tile_mode={tile_mode}"
        )

        boundary_geojson_path = self.state.get("boundary_geojson_path")
        boundary_geojson: Path | None = (
            Path(str(boundary_geojson_path))
            if boundary_geojson_path
            else None
        )

        mask_geojson: Path | None = None

        if boundary_used:
            if boundary_geojson is None or not boundary_geojson.exists():
                raise RuntimeError(
                    "Selected orthomosaic is marked as bounded, but boundary_geojson_path "
                    f"is missing or invalid: {boundary_geojson_path}"
                )

            mask_geojson = boundary_geojson
        else:
            if not boundary_geojson or not boundary_geojson.exists():
                logger.warning(
                    "No boundary GeoJSON available. QGIS will skip clipping and generate soft-corners tiles."
                )
            else:
                logger.info(
                    "Boundary GeoJSON exists, but selected orthomosaic is marked as unbounded. "
                    "QGIS will use soft-corners tile workflow."
                )

        clipped_ortho_dir = dir_from_key(
            "qgis_clipped_ortho",
            fallback=(rgb_path / "qgis" / "clipped" / "ortho"),
        )

        tiles_round_dir = dir_from_key(
            "tiles_ortho_round",
            fallback=(rgb_path / "tiles" / "ortho" / "round-corners"),
        )

        tiles_soft_dir = optional_dir_from_keys(
            ["tiles_ortho_soft"],
            fallback=(rgb_path / "tiles" / "ortho" / "soft-corners"),
        )

        clip_cfg = qgis_cfg.get("clip") or {}
        clip_enabled = bool(clip_cfg.get("enabled", True))

        qgis_tools_cfg = qgis_cfg.get("tools") or {}
        qgis_root = str(qgis_tools_cfg.get("qgis_root") or "")
        gdalwarp_path = str(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp")
        gdal2tiles_path = str(qgis_tools_cfg.get("gdal2tiles_path") or "gdal2tiles.py")

        gdalinfo_path = str(qgis_tools_cfg.get("gdalinfo_path") or "gdalinfo")

        tiles_cfg = qgis_cfg.get("tiles") or {}
        tiles_enabled = bool(tiles_cfg.get("enabled", True))
        zoom = str(tiles_cfg.get("zoom") or "11-24")
        profile = str(tiles_cfg.get("profile") or "mercator")
        webviewer = str(tiles_cfg.get("webviewer") or "none")
        copyright_text = str(tiles_cfg.get("copyright") or "ASIMOV-HAWKS")

        dst_nodata_raw = clip_cfg.get("dst_nodata") or ""
        dst_nodata = None

        if isinstance(dst_nodata_raw, (int, float)):
            dst_nodata = float(dst_nodata_raw)
        elif isinstance(dst_nodata_raw, str) and dst_nodata_raw.strip() != "":
            try:
                dst_nodata = float(dst_nodata_raw.strip())
            except ValueError:
                raise ValueError(
                    f"Invalid QGIS_CLIP_DST_NODATA value: '{dst_nodata_raw}' "
                    "(must be a number or empty)"
                )

        tools = QGISTools(
            logger=logger,
            qgis_root=qgis_root,
            gdalwarp_path=gdalwarp_path,
            gdal2tiles_path=gdal2tiles_path,
            gdalinfo_path=gdalinfo_path,
        )

        # ── Local staging setup ───────────────────────────────────────────────
        # gdalwarp and gdal2tiles both write to local disk first, then results
        # are copied to the network share. This avoids:
        #   - SMB write-cache corruption during clip (TIFFAppendToStrip errors)
        #   - sustained random-access reads over the network during tiling
        #
        # Staging root priority:
        #   1. QGIS_LOCAL_STAGING_DIR  (explicit override)
        #   2. UPLOAD_CACHE_ROOT       (reuse the existing E:\cache folder)
        #   3. tempfile.gettempdir()   (last resort — may be C:\)
        local_staging_cfg = qgis_cfg.get("local_staging") or {}
        local_staging_enabled = bool(local_staging_cfg.get("enabled", True))

        clip_staging_dir: Optional[Path] = None
        local_staging_root: Optional[Path] = None

        if local_staging_enabled:
            _explicit_dir = (local_staging_cfg.get("dir") or "").strip()
            if _explicit_dir:
                _staging_base = Path(_explicit_dir)
            else:
                # Reuse UPLOAD_CACHE_ROOT (E:\cache) so we never fall back
                # to C:\ temp. UPLOAD_CACHE_ROOT is already validated in
                # config.py so it's guaranteed to exist.
                _upload_cache_root = (
                    (self.config.get("paths") or {}).get("upload_cache_root")
                )
                if _upload_cache_root:
                    _staging_base = Path(_upload_cache_root) / "qgis-staging"
                else:
                    _staging_base = Path(tempfile.gettempdir()) / "ah-qgis-staging"
                    logger.warning(
                        f"UPLOAD_CACHE_ROOT not set — QGIS staging will use "
                        f"temp dir: {_staging_base}. Set UPLOAD_CACHE_ROOT in "
                        f".env to use your E:\\cache folder instead."
                    )

            clip_staging_dir = _staging_base / "clip" / self.run_id
            local_staging_root = _staging_base / "tiles" / self.run_id
            logger.info(f"QGIS local staging root: {_staging_base}")

        # ── Clip ─────────────────────────────────────────────────────────────
        clipped_path: Path

        if boundary_used and clip_enabled:
            clipped_filename = self._clipped_orthomosaic_filename(
                task_key=task_key,
                flag=flag,
            )
            clipped_path = clipped_ortho_dir / clipped_filename

            logger.info(
                f"Clipping selected orthomosaic ({task_key}) -> {clipped_path.name}"
            )

            if mask_geojson is None:
                raise RuntimeError("QGIS clipping requires a valid boundary GeoJSON mask.")

            skip_clip = False
            if resume and clipped_path.exists():
                logger.info(
                    f"Resume: clipped orthomosaic already exists "
                    f"({clipped_path.name}); verifying before reuse..."
                )
                try:
                    tools.verify_raster_readable(clipped_path, retries=1, delay_s=2.0)
                    skip_clip = True
                    logger.info(
                        f"Existing clip verified OK, skipping re-clip: {clipped_path.name}"
                    )
                except RuntimeError as e:
                    logger.warning(
                        f"Existing clipped file failed verification, will re-clip: {e}"
                    )

            if not skip_clip:
                tools.clip_raster_by_mask(
                    input_tif=source_path,
                    mask_geojson=mask_geojson,
                    output_tif=clipped_path,
                    dst_nodata=dst_nodata,
                    local_staging_dir=clip_staging_dir,
                )

        elif boundary_used and not clip_enabled:
            logger.warning(
                "QGIS clip disabled (qgis.clip.enabled=false). "
                "Using selected bounded orthomosaic directly for tile generation."
            )
            clipped_path = source_path

        else:
            logger.info(
                "Selected orthomosaic is unbounded. "
                "Skipping clip and using source orthomosaic for soft-corners tiles."
            )
            clipped_path = source_path

        selected["clipped_path"] = str(clipped_path)
        selected["clipped_filename"] = clipped_path.name
        selected["tile_mode"] = tile_mode

        self.state["selected_orthomosaic"] = selected

        tiles_dir = tiles_round_dir if boundary_used else tiles_soft_dir

        # ── Tile generation ──────────────────────────────────────────────────
        _staging_root: Optional[Path] = None
        if tiles_enabled:
            tile_resume = bool(resume)
            tile_clean = not tile_resume

            tiling_input_path = clipped_path
            tiling_output_dir = tiles_dir
            _local_tile_staging_active = False

            if local_staging_root is not None:
                _staging_root = local_staging_root 
                try:
                    _local_ortho_staging = local_staging_root / "ortho"
                    tiling_input_path = tools.stage_local_copy(
                        clipped_path,
                        _local_ortho_staging,
                    )
                    tiling_output_dir = local_staging_root / "tiles" / tile_mode
                    _local_tile_staging_active = True
                    logger.info(
                        f"Local staging enabled: tiling will read/write on "
                        f"local disk ({tiling_output_dir}) and copy results "
                        f"to {tiles_dir} afterward."
                    )
                except Exception as e:
                    logger.warning(
                        f"Local staging setup failed ({e}); falling back to "
                        f"tiling directly against {clipped_path}."
                    )
                    tiling_input_path = clipped_path
                    tiling_output_dir = tiles_dir
                    _local_tile_staging_active = False

            logger.info(
                f"Generating tiles ({tile_mode}) from "
                f"{tiling_input_path.name} -> {tiling_output_dir}"
            )

            tools.generate_tiles(
                input_tif=tiling_input_path,
                output_dir=tiling_output_dir,
                zoom=zoom,
                profile=profile,
                webviewer=webviewer,
                copyright_text=copyright_text,
                clean=tile_clean,
                resume=tile_resume,
            )

            if _local_tile_staging_active:
                logger.info(
                    f"Copying tiles from local staging to network share: "
                    f"{tiling_output_dir} -> {tiles_dir}"
                )
                t0 = time.perf_counter()
                tiles_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(tiling_output_dir, tiles_dir, dirs_exist_ok=True)
                logger.info(
                    f"Tile copy-back complete in {time.perf_counter() - t0:.1f}s"
                )

                # ── Cleanup local staging ────────────────────────────────────
                # Remove all staging dirs for this run now that tiles are
                # safely on the network share. Done here (after copy-back)
                # so that a failed copy-back leaves the local tiles intact
                # for manual recovery.
                for _stale_dir, _label in [
                    (clip_staging_dir,          "clip staging"),
                    (_staging_root / "ortho" if _staging_root else None, "ortho staging"),
                    (tiling_output_dir,          "tile staging"),
                ]:
                    if _stale_dir is not None and _stale_dir.exists():
                        try:
                            shutil.rmtree(_stale_dir)
                            logger.info(f"Cleaned up {_label}: {_stale_dir}")
                        except Exception as _e:
                            logger.warning(
                                f"Could not clean up {_label} ({_stale_dir}): {_e}"
                            )

            selected["tiles_dir"] = str(tiles_dir)
        else:
            logger.warning(
                "QGIS tiles disabled (qgis.tiles.enabled=false). Skipping tile generation."
            )
            selected["tiles_dir"] = None

        self.state["selected_orthomosaic"] = selected

        return {
            "boundary_geojson": str(boundary_geojson) if boundary_geojson else None,
            "selected_webodm_task": task_key,
            "selected_orthomosaic": selected,
            "tools": {
                "gdalwarp_path": gdalwarp_path,
                "gdal2tiles_path": gdal2tiles_path,
            },
            "clip": {
                "enabled": clip_enabled,
                "dst_nodata": dst_nodata,
                "input": str(source_path),
                "output": str(clipped_path),
            },
            "tiles": {
                "enabled": tiles_enabled,
                "mode": tile_mode,
                "output_dir": str(tiles_dir) if tiles_enabled else None,
                "zoom": zoom,
                "profile": profile,
                "webviewer": webviewer,
                "copyright": copyright_text,
            },
        }
    
    def run_webodm_fallback_task(
        self,
        *,
        task_key: str = "task4",
        fallback_reason: str = "quality_gate_requested_fallback",
    ) -> Dict[str, Any]:
        logger = self.loggers["webodm"]
        task_key = self._normalize_webodm_task_key(task_key)
        task_label = self._webodm_task_label(task_key)

        logger.info(f"Running WebODM fallback task: {task_key}")

        survey_id = self._require_survey_id()
        rgb_path = self._require_rgb_path()

        # Use setdefault to ensure we're modifying the state dict, not a temporary copy
        web = self.state.setdefault("webodm", {})
        project_id = web.get("project_id")
        project_name = web.get("project_name") or survey_id

        if not project_id:
            raise RuntimeError(f"{task_key} fallback requires existing WebODM project_id.")

        boundary_geojson_path = self.state.get("boundary_geojson_path")
        if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
            raise RuntimeError(f"{task_key} fallback requires boundary GeoJSON.")

        ds = self.state.get("data_segregation") or {}
        dirs = ds.get("dirs") or {}
        image_folder = Path(dirs.get("path") or (rgb_path / "images" / "path"))

        boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")

        webodm_cfg = self.config["webodm"]
        exports_cfg = self.config.get("exports", {})
        qgis_tools_cfg = (exports_cfg.get("tools") or {})

        task_options = self._webodm_task_options(task_key)
        task_options["boundary"] = boundary_geojson

        task_flag = self._webodm_task_flag(
            task_key,
            default_boundary_mode="b",
        )

        task_name = self.task_name_overrides.get(task_key)
        if not task_name:
            task_name = self._webodm_task_name(
                survey_id=survey_id,
                flag=task_flag,
                task_key=task_key,
            )

        task_ortho_dir = rgb_path / "ortho"
        task_ortho_dir.mkdir(parents=True, exist_ok=True)

        processor = self._create_webodm_processor(logger)

        existing_task_id = processor.find_task_by_name(int(project_id), task_name)

        if existing_task_id:
            logger.info(f"Found existing {task_key}: {existing_task_id}")
            current_task_id = str(existing_task_id)
            status = processor.get_task_status(int(project_id), current_task_id)

            if status != "completed":
                success, runtime, _info = processor.wait_for_completion(
                    int(project_id),
                    current_task_id,
                    live=False,
                    control_check=lambda: self._check_control_or_raise("webodm"),
                )
            else:
                success = True
                runtime = 0.0

        else:
            upload_image_folder = self._get_reusable_webodm_upload_folder(
                default_source_dir=image_folder,
                logger=logger,
            )

            # If no reusable cache was found, actively stage to local disk before
            # uploading — streaming a multi-GB multipart upload directly off Z:\
            # is prone to SMB hiccups killing the request mid-transfer.
            if upload_image_folder == image_folder:
                upload_cache_root_cfg = (self.config.get("paths") or {}).get("upload_cache_root")
                if not upload_cache_root_cfg:
                    upload_cache_root_cfg = os.getenv("UPLOAD_CACHE_ROOT")
                local_root = Path(upload_cache_root_cfg) if upload_cache_root_cfg else Path(
                    os.getenv("TEMP", r"C:\temp")
                )
                cache_root = local_root / "automation-pipeline" / "upload_cache" / f"{self.run_id}_{task_key}"

                try:
                    cached_dir, _ = self._stage_upload_cache(
                        src_dir=image_folder,
                        cache_root=cache_root,
                        logger=logger,
                        progress_every=25,
                        require_free_multiplier=1.2,
                    )
                    upload_image_folder = cached_dir
                    self._remember_webodm_upload_folder(
                        upload_folder=upload_image_folder,
                        cached_dir=cached_dir,
                        source_folder=image_folder,
                        fallback_direct=False,
                    )
                    logger.info(f"Staged {task_key} upload to local disk: {upload_image_folder}")
                except Exception as e:
                    logger.warning(
                        f"Local staging failed for {task_key} upload, falling back to "
                        f"direct upload from source. reason={e}"
                    )
                    upload_image_folder = image_folder

            current_task_id = processor.create_task_with_images(
                project_id=int(project_id),
                name=task_name,
                image_folder=str(upload_image_folder),
                options=task_options,
                processing_node=webodm_cfg.get("node_id"),
            )

            success, runtime, _info = processor.wait_for_completion(
                int(project_id),
                current_task_id,
                live=False,
                control_check=lambda: self._check_control_or_raise("webodm"),
            )

        task_state = {
            "id": str(current_task_id),
            "name": task_name,
            "success": bool(success),
            "runtime_seconds": float(runtime),
            "flag": task_flag,
            "task_label": task_label,
        }

        web[task_key] = task_state

        downloads = web.setdefault("downloads", {})
        task_downloads = downloads.setdefault(task_key, {})

        selected = None

        if exports_cfg.get("enabled", False) and exports_cfg.get("ortho", {}).get("enabled", False):
            ortho_cfg = exports_cfg["ortho"]
            epsg = int(ortho_cfg.get("reproject_epsg", 4326))
            candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

            filename = self._orthomosaic_filename(
                task_key=task_key,
                flag=task_flag,
            )

            out_path = processor.export_orthomosaic(
                int(project_id),
                str(current_task_id),
                out_dir=task_ortho_dir,
                filename=filename,
                epsg=epsg,
                candidates=candidates,
                gdalwarp_path=(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp"),
            )

            if out_path:
                task_downloads["orthomosaic"] = str(out_path)
                task_downloads["epsg"] = epsg

                self.state["webodm"] = web

                selected = self._set_selected_orthomosaic(
                    task_key=task_key,
                    source_path=out_path,
                    flag=task_flag,
                    boundary_used=True,
                    fallback_used=True,
                    fallback_reason=fallback_reason,
                )

                web["selected_webodm_task"] = task_key
                web["selected_orthomosaic"] = selected
            else:
                logger.warning(f"Could not download {task_key} orthomosaic.")

        self.state["webodm"] = web

        return {
            task_key: task_state,
            "downloads": task_downloads,
            "project_id": project_id,
            "project_name": project_name,
            "selected_webodm_task": task_key,
            "selected_orthomosaic": selected,
        }


    def run_task4_fallback(self) -> Dict[str, Any]:
        fallback_task = self._webodm_fallback_task_key()

        return self.run_webodm_fallback_task(
            task_key=fallback_task,
            fallback_reason=f"quality_gate_requested_{fallback_task}_fallback",
        )
        

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

        allowed_stages = WEBODM_RESTART_STAGES

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

            processor = self._create_webodm_processor(
                self.loggers["webodm"]
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

            if task4.get("id") and str(task4.get("id")) == str(task_id_to_restart):
                task4.update(updated_task_state)
                web["task4"] = task4
            elif task2.get("id") and str(task2.get("id")) == str(task_id_to_restart):
                task2.update(updated_task_state)
                web["task2"] = task2
            elif task1.get("id") and str(task1.get("id")) == str(task_id_to_restart):
                task1.update(updated_task_state)
                web["task1"] = task1

            self.state["webodm"] = web
            return {"passed": None, "restarts": restarts, "task": updated_task_state}

        # ── Interactive loop ─────────────────────────────────────────
        _fallback_reviewed = False
        _fallback_task_state: dict = {}
        fallback_task: str = self._webodm_fallback_task_key()

        while True:
            web = self.state.get("webodm") or {}
            task1 = web.get("task1") or {}
            task2 = web.get("task2") or {}
            task4 = web.get("task4") or {}

            raw = quality_gate_prompt(
                logger=logger,
                survey_id=survey_id,
                project_id=int(project_id),
                task1=task1,
                task2=task2 if task2.get("id") else {},
                webodm_url=self.config.get("webodm", {}).get("url", ""),
                task4=task4 if task4.get("id") else None,
                fallback_review=_fallback_reviewed,
            )

            if raw in ("yes", "y"):
                if _fallback_reviewed:
                    logger.info(
                        f"Quality gate PASSED by user — "
                        f"fallback ({fallback_task}) approved after review."
                    )
                else:
                    logger.info("Quality gate PASSED by user.")

                # Select the best available orthomosaic for QGIS
                # Priority: fallback (if reviewed) > task4 > task2
                selected = None
                try:
                    if _fallback_reviewed:
                        fallback_flag = self._webodm_task_flag(
                            fallback_task,
                            default_boundary_mode="b",
                        )
                        selected = self._select_existing_task_orthomosaic(
                            task_key=fallback_task,
                            flag=fallback_flag,
                            boundary_used=True,
                            fallback_used=True,
                        )
                    elif task4.get("id"):
                        task4_flag = self._webodm_task_flag(
                            "task4",
                            default_boundary_mode="b",
                        )
                        selected = self._select_existing_task_orthomosaic(
                            task_key="task4",
                            flag=task4_flag,
                            boundary_used=True,
                            fallback_used=False,
                        )
                    elif task2.get("id"):
                        task2_flag = self._webodm_task_flag(
                            "task2",
                            default_boundary_mode="b",
                        )
                        selected = self._select_existing_task_orthomosaic(
                            task_key="task2",
                            flag=task2_flag,
                            boundary_used=True,
                            fallback_used=False,
                        )
                except Exception as e:
                    logger.warning(f"Could not select orthomosaic after quality pass: {e}")
                    selected = None

                self._cleanup_webodm_upload_cache_from_state(self.loggers["webodm"])

                return {
                    "passed": True,
                    "restarts": restarts,
                    "project_id": project_id,
                    "fallback_task": fallback_task if _fallback_reviewed else None,
                    fallback_task: _fallback_task_state if _fallback_reviewed else None,
                    "selected_webodm_task": self.state.get("selected_webodm_task"),
                    "selected_orthomosaic": selected,
                }

            if raw in ("fail", "f"):
                logger.warning("Quality gate FAILED by user.")

                self._cleanup_webodm_upload_cache_from_state(self.loggers["webodm"])

                return {
                    "passed": False,
                    "restarts": restarts,
                    "project_id": project_id,
                }

            if raw == "restart":
                # Pick the best task to restart — task4 first, else task2
                if task4.get("id"):
                    default_task_id   = str(task4["id"])
                    default_task_name = str(task4.get("name") or "task4")
                elif task2.get("id"):
                    default_task_id   = str(task2["id"])
                    default_task_name = str(task2.get("name") or "task2")
                else:
                    default_task_id   = str(task1["id"])
                    default_task_name = str(task1.get("name") or "task1")

                res = restart_and_wait(default_task_id, default_task_name, "dataset")
                if res.get("passed") is False:
                    return res
                continue

            if raw.startswith("restart "):
                parts = raw.split()

                if len(parts) not in (2, 3):
                    logger.warning(
                        "Invalid format. Use: restart | restart t1|t2|t4 | restart t1|t2|t4 <stage>"
                    )
                    continue

                target     = parts[1]
                stage_alias = parts[2] if len(parts) == 3 else "load_dataset"

                target_map = {
                    "t1": task1, "task1": task1,
                    "t2": task2, "task2": task2,
                    "t4": task4, "task4": task4,
                }

                chosen = target_map.get(target)
                if chosen is None:
                    logger.warning("Invalid target. Use t1, t2, or t4.")
                    continue

                if not chosen or not chosen.get("id"):
                    logger.warning(f"{target} does not exist for this run.")
                    continue

                if stage_alias not in allowed_stages:
                    logger.warning(
                        f"Invalid stage '{stage_alias}'. "
                        f"Valid stages: {', '.join(sorted(WEBODM_RESTART_STAGE_NAMES))}"
                    )
                    continue

                res = restart_and_wait(
                    str(chosen["id"]),
                    str(chosen.get("name") or target),
                    allowed_stages[stage_alias],
                )
                if res.get("passed") is False:
                    return res
                continue

            logger.warning(
                "Unrecognised input. "
                "Use: yes | fail | restart | restart t1|t2|t4 [stage]"
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
                lambda: self.stage_qgis(resume=resume),
                output_key="qgis",
                state=self.state,
                force=_force("qgis"),
                stale_running_policy="rerun",
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

        # ── Preflight Failed ──────────────────────────────────────
        except PreflightError as e:
            self.state.update(
                {
                    "success": False,
                    "preflight_failed": True,
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
                pipeline_logger.exception(
                    "Failed to mark preflight-failed run as finished"
                )

            if self.survey_id:
                try:
                    self.repo.mark_survey_finished(
                        self.survey_id,
                        success=False,
                        total_runtime_seconds=total_runtime,
                    )
                except Exception:
                    pipeline_logger.exception(
                        "Failed to mark preflight-failed survey as finished"
                    )

            self.control.cleanup_flags()
            pipeline_footer(pipeline_logger, total_runtime, success=False)

            pipeline_logger.error("")
            pipeline_logger.error(str(e))

            return self.state

        # ── Paused / Canceled / Aborted / Runtime Failure ─────────
        except RuntimeError as e:
            if str(e) == "__PIPELINE_PAUSED__":
                self.state.update(
                    {
                        "success": False,
                        "paused": True,
                        "error": "paused_by_flag",
                    }
                )

                pipeline_paused(
                    pipeline_logger,
                    self.run_id,
                    after_stage=self.state.get("paused_after_stage", "?"),
                )

                # Clear only the pause flag so a future --resume
                # does not immediately re-trigger __PIPELINE_PAUSED__.
                self.control.clear_pause()
                return self.state

            # ── Canceled (WebODM UI) ───────────────────────────────
            if str(e) == "__PIPELINE_CANCELED__":
                self.state.update(
                    {
                        "success": False,
                        "canceled": True,
                        "error": "canceled_in_webodm_ui",
                    }
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

            # ── Aborted (Hotkey) ───────────────────────────────────
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
                    pipeline_logger.exception(
                        "Failed to mark aborted run as finished"
                    )

                if self.survey_id:
                    try:
                        self.repo.mark_survey_finished(
                            self.survey_id,
                            success=False,
                            total_runtime_seconds=total_runtime,
                        )
                    except Exception:
                        pipeline_logger.exception(
                            "Failed to mark aborted survey as finished"
                        )

                self.control.cleanup_flags()
                pipeline_footer(pipeline_logger, total_runtime, success=False)
                return self.state

            # ── Other Runtime Failure ──────────────────────────────
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
