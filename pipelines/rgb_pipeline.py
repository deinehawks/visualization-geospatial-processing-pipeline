from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Set, List, Tuple
from shared import get_logger, PipelineRepo, db_path, StageRunner
from modules import run_kml, WebODMProcessor, run_filter, run_data_segregation

import time
import uuid
import logging
import os
import shutil
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
            "qgis": get_logger("rgb.qgis", self.logs_dir / "qgis.log"),
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
        step = 1024.0
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if num < step:
                return f"{num:.1f} {unit}" if unit != "B" else f"{num} {unit}"
            num /= step
        return f"{num:.1f} PB"

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
            raise FileNotFoundError(f"Upload cache source dir not found: {src_dir}")

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
                logger.info(f"Upload cache copy progress: {copied}/{len(images)} | elapsed={elapsed:.1f}s")

        elapsed = time.perf_counter() - t0
        logger.info(f"Upload cache ready: {cache_dir} | images={len(images)} | copy_time={elapsed:.1f}s")
        return cache_dir, len(images)

    def _cleanup_upload_cache(self, cache_dir: Path, logger: logging.Logger) -> None:
        try:
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
                logger.info(f"Upload cache cleaned: {cache_dir}")
        except Exception:
            logger.exception(f"Failed to clean upload cache: {cache_dir}")

    # ============================================================
    # Pause helpers
    # ============================================================

    def _pause_flag_path(self) -> Path:
        return self.base_dir / "data" / "pause.flag"

    def _should_pause(self) -> bool:
        return self._pause_flag_path().exists()

    def _check_pause_or_raise(self, stage_name: str) -> None:
        if not self._should_pause():
            return

        reason = f"pause.flag detected before stage '{stage_name}'"
        self.loggers["pipeline"].warning(f"⏸ PAUSE  | {reason}")

        try:
            self.repo.mark_run_paused(
                self.run_id,
                paused_after_stage=stage_name,
                reason="pause_flag",
            )
        except Exception:
            self.loggers["pipeline"].exception("Failed to mark run as paused in DB")

        raise RuntimeError("__PIPELINE_PAUSED__")

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

        # Rename KML to match survey ID
        boundary_dir = self.rgb_path / "boundary"
        kmls = sorted(boundary_dir.glob("*.kml"))
        if not kmls:
            raise FileNotFoundError(f"No .kml found after segregation in: {boundary_dir}")

        original_kml = kmls[0]
        new_kml_path = boundary_dir / f"{self.survey_id}.kml"

        if original_kml.name != new_kml_path.name:
            if new_kml_path.exists():
                new_kml_path.unlink()
            original_kml.rename(new_kml_path)

        logger.info(f"KML renamed to: {new_kml_path.name}")

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

        logger.info(f"Input (raw): {input_dir}")
        logger.info(f"Output (kept/path): {output_dir}")
        logger.info(f"Output (excluded/cross-runs): {excluded_dir}")

        result = run_filter(
            input_dir=input_dir,
            output_dir=output_dir,
            logger=logger,
            max_gap=max_gap,
            cross_run_window=window,
        )

        excluded = int(result.get("total_excluded") or 0)
        self.state["crossrun_flag"] = "xc" if excluded > 0 else "c"

        if delete_raw_after:
            try:
                total_images = int(result.get("total_images") or 0)
                kept = int(result.get("total_kept") or 0)
                excl = int(result.get("total_excluded") or 0)

                if total_images <= 0:
                    raise RuntimeError("Refusing to delete raw: total_images is 0 (unexpected).")
                if kept + excl != total_images:
                    raise RuntimeError(f"Refusing to delete raw: kept+excluded != total ({kept}+{excl}!={total_images})")
                if not output_dir.exists() or not excluded_dir.exists():
                    raise RuntimeError("Refusing to delete raw: output directories missing.")

                kept_fs = len([p for p in output_dir.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")])
                excl_fs = len([p for p in excluded_dir.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")])

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
        self.state["boundary_geojson_path"] = str(geojson_path) if geojson_path else None

        if not boundary_ok:
            logger.warning("No valid polygon boundary produced (GeoJSON missing). Pipeline will run Task 1 only.")

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
            raise RuntimeError("Missing data_segregation.dirs in state. Ensure segregation returns dirs mapping.")

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

        task1_name = f"{survey_id}-RGB--{crossrun_flag}{boundary_flag_task1}"
        task2_name = f"{survey_id}-RGB--{crossrun_flag}{boundary_flag_task2}"

        task1_flag = f"{crossrun_flag}{boundary_flag_task1}"
        task2_flag = f"{crossrun_flag}{boundary_flag_task2}"

        image_folder = Path(dirs.get("path") or (rgb_path / "images" / "path"))

        # ---------------- Local Upload Cache ----------------
        upload_cache_root_cfg = (self.config.get("paths") or {}).get("upload_cache_root")
        if not upload_cache_root_cfg:
            upload_cache_root_cfg = os.getenv("UPLOAD_CACHE_ROOT")
        local_root = Path(upload_cache_root_cfg) if upload_cache_root_cfg else Path(os.getenv("TEMP", r"C:\temp"))
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
                logger.warning(f"Upload cache unavailable, uploading directly from source. reason={e}")
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

            project_id = processor.create_project(
                name=project_name,
                description="RGB automated processing",
            )

            # ---------------- TASK 1 (always) ----------------
            task1_options = dict(webodm_cfg.get("task1_options", {}))
            current_task_id = processor.create_task_with_images(
                project_id=project_id,
                name=task1_name,
                image_folder=str(upload_folder),
                options=task1_options,
                processing_node=webodm_cfg.get("node_id"),
            )
            t1_success, t1_runtime, t1_info = processor.wait_for_completion(project_id, current_task_id, live=False)

            result: Dict[str, Any] = {
                "project_id": project_id,
                "project_name": project_name,
                "task1": {"id": current_task_id, "name": task1_name, "success": t1_success, "runtime_seconds": t1_runtime},
                "task2": None,
                "boundary_used": False,
                "boundary_reason": None,
                "boundary_geojson_path": boundary_geojson_path,
                "downloads": {"task1": {}, "task2": {}},
            }

            # ---------------- Downloads after TASK 1 ----------------
            if exports_cfg.get("enabled", False) and exports_cfg.get("ortho", {}).get("enabled", False):
                ortho_cfg = exports_cfg["ortho"]
                out_dir = dir_from_key(ortho_cfg["out_dir_key"])
                epsg = int(ortho_cfg.get("reproject_epsg", 4326))

                filename = ortho_cfg.get("filename_template", "orthomosaic--{flag}.tif").format(flag=task1_flag)
                candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

                out_path = processor.export_orthomosaic(
                    project_id,
                    current_task_id,
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

            # ---------------- TASK 2 (only if boundary exists) ----------------
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

            current_task_id = processor.create_task_with_images(
                project_id=project_id,
                name=task2_name,
                image_folder=str(upload_folder),
                options=task2_options,
                processing_node=webodm_cfg.get("node_id"),
            )

            t2_success, t2_runtime, t2_info = processor.wait_for_completion(project_id, current_task_id, live=False)

            result["task2"] = {"id": current_task_id, "name": task2_name, "success": t2_success, "runtime_seconds": t2_runtime}
            result["boundary_used"] = True

            # ---------------- Task 2 bounded orthomosaic ----------------
            if exports_cfg.get("enabled", False) and exports_cfg.get("ortho", {}).get("enabled", False):
                ortho_cfg = exports_cfg["ortho"]
                out_dir = dir_from_key(ortho_cfg["out_dir_key"])
                epsg = int(ortho_cfg.get("reproject_epsg", 4326))

                filename = ortho_cfg.get("filename_template", "orthomosaic--{flag}.tif").format(flag=task2_flag)
                candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

                out_path = processor.export_orthomosaic(
                    project_id,
                    current_task_id,
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
                # -------- DEM (config-driven, non-interactive) --------
                dem_cfg = (exports_cfg.get("dem") or {})
                # dem_do_download = bool(dem_cfg.get("enabled", False)) and bool(dem_cfg.get("download", True))
                dem_do_download = False  # MVP: disable DSM/DTM downloads until web/app supports DEM outputs

                if dem_do_download:
                    epsg = int(dem_cfg.get("reproject_epsg", 3857))  # per your doc: EPSG:3857
                    dtm_dir = dir_from_key(dem_cfg["dtm_dir_key"])
                    dsm_dir = dir_from_key(dem_cfg["dsm_dir_key"])

                    models = list(dem_cfg.get("models") or ["dtm", "dsm"])
                    colors = list(dem_cfg.get("colors") or [])
                    shadings = list(dem_cfg.get("shadings") or [])
                    tmpl = dem_cfg.get("filename_template", "{color}-{shading}.tif")

                    if not colors or not shadings:
                        logger.warning("DEM download enabled but colors/shadings not configured. Skipping DEM downloads.")
                    else:
                        for model in models:
                            if model not in ("dtm", "dsm"):
                                logger.warning(f"Unknown DEM model '{model}' (expected dtm/dsm). Skipping.")
                                continue

                            out_base = dtm_dir if model == "dtm" else dsm_dir

                            for color in colors:
                                for shading in shadings:
                                    fname = tmpl.format(color=color, shading=shading)
                                    tmp_raw = out_base / f"__tmp_raw_{fname}"
                                    final_out = out_base / fname

                                    asset_type = f"{model}/{color}/{shading}" 
                                    ok = processor.download_asset_safe(project_id, current_task_id, asset_type, tmp_raw)
                                    if ok:
                                        processor.run_gdalwarp(tmp_raw, final_out, epsg)
                                        try:
                                            tmp_raw.unlink(missing_ok=True)
                                        except Exception:
                                            pass

                        result["downloads"]["task2"]["dem_epsg"] = epsg
                        result["downloads"]["task2"]["dtm_dir"] = str(dtm_dir)
                        result["downloads"]["task2"]["dsm_dir"] = str(dsm_dir)

                # ------------- Point Cloud (LAZ -> PLY/PCD) --------------
                pc_cfg = (exports_cfg.get("pointcloud") or {})
                pc_enabled = bool(pc_cfg.get("enabled", True))

                if pc_enabled:
                    pc_dir_key = str(pc_cfg.get("out_dir_key") or "3d")
                    pc_dir = dir_from_key(pc_dir_key, fallback=(rgb_path / "3d"))

                    laz_candidates = list(pc_cfg.get("asset_candidates") or ["georeferenced_model.laz"])
                    pdal_path = qgis_tools_cfg.get("pdal_path") or "pdal"

                    pc_out = processor.export_pointcloud(
                        project_id,
                        current_task_id,
                        out_dir=pc_dir,
                        laz_archive_name=f"{survey_id}-RGB-{task2_flag}.laz",
                        ply_name="model.ply",
                        pcd_name="odm.pcd",
                        candidates=laz_candidates,
                        pdal_path=pdal_path,
                    )

                    result["downloads"]["task2"]["pointcloud_laz"] = pc_out.get("laz")
                    result["downloads"]["task2"]["pointcloud_ply"] = pc_out.get("ply")
                    result["downloads"]["task2"]["pointcloud_pcd"] = pc_out.get("pcd")
                    result["downloads"]["task2"]["pointcloud_asset_type"] = pc_out.get("asset_type")

                    # If you still want to fail hard when LAZ download fails:
                    if not pc_out.get("laz") and bool(pc_cfg.get("required", True)):
                        raise RuntimeError("POINTCLOUD_DOWNLOAD_FAILED")

                else:
                    logger.info("Point cloud download skipped (pointcloud.enabled=false).")
                    result["downloads"]["task2"]["pointcloud_skipped"] = True

                # -------- All Assets ZIP (archive) --------
                if exports_cfg.get("all_assets_zip", {}).get("enabled", False):
                    zcfg = exports_cfg["all_assets_zip"]
                    out_dir = dir_from_key(zcfg["out_dir_key"])
                    fname = zcfg.get("filename_template", "{survey_id}-RGB-{flag}-all.zip").format(
                        survey_id=survey_id,
                        flag=task2_flag,
                    )
                    zip_path = out_dir / fname

                    ok = processor.download_all_assets_safe(project_id, current_task_id, zip_path)
                    if ok:
                        result["downloads"]["task2"]["all_assets_zip"] = str(zip_path)
                    else:
                        logger.warning("All-assets zip was not downloaded (endpoint missing or failed).")

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

        # --- dirs map ---
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

        # --- boundary ---
        boundary_geojson_path = self.state.get("boundary_geojson_path")
        if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
            raise RuntimeError("QGIS stage requires boundary_geojson_path (missing).")

        # --- orthos from WebODM stage ---
        web = self.state.get("webodm") or {}
        dls = web.get("downloads") or {}
        t1 = (dls.get("task1") or {})
        t2 = (dls.get("task2") or {})

        unbounded_ortho = t1.get("orthomosaic")
        bounded_ortho = t2.get("orthomosaic")  # may be None if task2 skipped

        if not unbounded_ortho or not Path(unbounded_ortho).exists():
            raise RuntimeError("QGIS stage requires Task 1 orthomosaic (unbounded) downloaded first.")

        # --- naming flags (consistent) ---
        naming_cfg = self.config.get("naming", {})
        crossrun_flag = self.state.get("crossrun_flag") or naming_cfg.get("crossrun_mode", "xc")
        boundary_flag_task1 = naming_cfg.get("task1_boundary_mode", "xb")
        boundary_flag_task2 = naming_cfg.get("task2_boundary_mode", "b")
        task1_flag = f"{crossrun_flag}{boundary_flag_task1}"  # ex: xcxb
        task2_flag = f"{crossrun_flag}{boundary_flag_task2}"  # ex: xcb

        # --- output dirs ---
        clipped_ortho_dir = dir_from_key("qgis_clipped_ortho", fallback=(rgb_path / "qgis" / "clipped" / "ortho"))
        tiles_sharp_dir = dir_from_key("tiles_ortho_sharp", fallback=(rgb_path / "tiles" / "ortho" / "sharp-corners"))
        tiles_round_dir = dir_from_key("tiles_ortho_round", fallback=(rgb_path / "tiles" / "ortho" / "round-corners"))

        # --- output filenames (use config template) ---
        clip_cfg = (qgis_cfg.get("clip") or {})
        clip_enabled = bool(clip_cfg.get("enabled", True))
        clip_tmpl = str(clip_cfg.get("filename_template") or "orthomosaic-clipped--{flag}.tif")

        unbounded_clipped = clipped_ortho_dir / clip_tmpl.format(flag=task1_flag)
        bounded_clipped = clipped_ortho_dir / clip_tmpl.format(flag=task2_flag)

        # --- tool paths/options (FIXED: read from qgis.tools + qgis.tiles) ---
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

        # --- optional nodata ---
        dst_nodata_raw = (clip_cfg.get("dst_nodata") or "")
        dst_nodata = None
        if isinstance(dst_nodata_raw, (int, float)):
            dst_nodata = float(dst_nodata_raw)
        elif isinstance(dst_nodata_raw, str) and dst_nodata_raw.strip() != "":
            try:
                dst_nodata = float(dst_nodata_raw.strip())
            except ValueError:
                raise ValueError(f"Invalid QGIS_CLIP_DST_NODATA value: '{dst_nodata_raw}' (must be a number or empty)")

        # --- QGIS tools wrapper ---
        tools = QGISTools(
            logger=logger,
            qgis_root=qgis_root,
            gdalwarp_path=gdalwarp_path,
            gdal2tiles_path=gdal2tiles_path,
        )

        logger.info(f"Boundary (GeoJSON): {boundary_geojson_path}")
        logger.info(f"Using gdalwarp: {gdalwarp_path}")
        logger.info(f"Using gdal2tiles: {gdal2tiles_path}")

        # 1) Clip unbounded -> always (unless disabled)
        if clip_enabled:
            logger.info(f"Clipping UNBOUNDED ortho -> {unbounded_clipped.name}")
            tools.clip_raster_by_mask(
                input_tif=Path(unbounded_ortho),
                mask_geojson=Path(boundary_geojson_path),
                output_tif=unbounded_clipped,
                dst_nodata=dst_nodata,
            )
        else:
            logger.warning("QGIS clip disabled (qgis.clip.enabled=false). Using unbounded orthomosaic directly.")
            unbounded_clipped = Path(unbounded_ortho)

        # 2) Clip bounded -> only if exists (unless disabled)
        bounded_ok = False
        if bounded_ortho and Path(bounded_ortho).exists():
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
            logger.warning("Bounded orthomosaic missing. Skipping bounded clip + round-corners tiles.")

        # 3) Tiles (doc rule: unbounded → sharp-corners, bounded → round-corners)
        if tiles_enabled:
            logger.info(f"Generating tiles (sharp-corners) from {Path(unbounded_clipped).name}")
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
                logger.info(f"Generating tiles (round-corners) from {Path(bounded_clipped).name}")
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
            logger.warning("QGIS tiles disabled (qgis.tiles.enabled=false). Skipping tile generation.")

        return {
            "boundary_geojson": str(boundary_geojson_path),
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
                "input": str(unbounded_ortho),
                "clipped": str(unbounded_clipped),
                "tiles_dir": str(tiles_sharp_dir) if tiles_enabled else None,
            },
            "bounded": {
                "input": str(bounded_ortho) if bounded_ortho else None,
                "clipped": str(bounded_clipped) if bounded_ok else None,
                "tiles_dir": str(tiles_round_dir) if (tiles_enabled and bounded_ok) else None,
            },
        }

    def stage_quality_gate(self) -> Dict[str, Any]:
        logger = self.loggers["pipeline"]
        logger.info("Stage: Quality Gate Check")

        survey_id = self.survey_id or "?"
        max_restarts = 3
        restarts = 0

        web = self.state.get("webodm") or {}
        project_id = web.get("project_id")

        if not project_id:
            raise RuntimeError("Quality gate cannot run: missing webodm.project_id in pipeline state.")

        task1 = web.get("task1") or {}
        task2 = web.get("task2") or {}

        if not task1.get("id") and not task2.get("id"):
            raise RuntimeError("Quality gate cannot run: missing webodm task ids (task1/task2).")

        allowed_stages = {"load_dataset", "structure_from_motion", "multi_view_stereo", "texturing"}

        def _pick_default_task() -> tuple[str, str]:
            if task2.get("id"):
                return str(task2["id"]), str(task2.get("name") or "task2")
            return str(task1["id"]), str(task1.get("name") or "task1")

        def restart_and_wait(task_id_to_restart: str, task_name_to_restart: str, restart_from: str) -> Dict[str, Any]:
            nonlocal restarts, web, task1, task2

            restarts += 1
            if restarts > max_restarts:
                logger.error("Maximum WebODM restart attempts exceeded.")
                return {"passed": False, "restarts": restarts, "reason": "max_restarts_exceeded"}

            webodm_cfg = self.config["webodm"]
            processor = WebODMProcessor(
                url=webodm_cfg["url"],
                username=webodm_cfg["username"],
                password=webodm_cfg["password"],
                logger=self.loggers["webodm"],
            )

            logger.warning(
                f"Requesting WebODM internal restart | project_id={project_id} task_id={task_id_to_restart} "
                f"restart_from={restart_from} | attempt {restarts}/{max_restarts}"
            )

            processor.restart_task(project_id=int(project_id), task_id=str(task_id_to_restart), restart_from=restart_from)
            success, runtime, task_info = processor.wait_for_completion(int(project_id), str(task_id_to_restart))

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

        while True:
            web = self.state.get("webodm") or {}
            task1 = web.get("task1") or {}
            task2 = web.get("task2") or {}

            default_task_id, default_task_name = _pick_default_task()

            print("\n=========== QUALITY GATE ===========")
            print(f"Survey: {survey_id}")
            print(f"Project ID: {project_id}")
            print(f"Task1: {task1.get('name')} (id={task1.get('id')})")
            print(f"Task2: {task2.get('name')} (id={task2.get('id')})")
            print("\nInspect outputs in WebODM dashboard.")
            print("Commands:")
            print("  yes                         -> Proceed")
            print("  fail                        -> Mark pipeline as failed")
            print("  restart                      -> Restart QA task from load_dataset (Task2 if exists else Task1)")
            print("  restart t1                   -> Restart Task1 from load_dataset")
            print("  restart t2                   -> Restart Task2 from load_dataset")
            print("  restart t1 <stage>           -> Restart Task1 from stage")
            print("  restart t2 <stage>           -> Restart Task2 from stage")
            print("\nStages:")
            print("  load_dataset | structure_from_motion | multi_view_stereo | texturing")
            print("====================================\n")

            raw = input("Your decision: ").strip().lower()

            if raw in ("yes", "y"):
                logger.info("Quality gate PASSED by user.")
                return {"passed": True, "restarts": restarts, "project_id": project_id}

            if raw in ("fail", "f"):
                logger.warning("Quality gate FAILED by user.")
                return {"passed": False, "restarts": restarts, "project_id": project_id}

            if raw == "restart":
                res = restart_and_wait(default_task_id, default_task_name, "load_dataset")
                if res.get("passed") is False:
                    return res
                continue

            if raw.startswith("restart "):
                parts = raw.split()
                if len(parts) not in (2, 3):
                    print("Invalid format. Use: restart | restart t1|t2 | restart t1|t2 <stage>")
                    continue

                target = parts[1]
                stage = parts[2] if len(parts) == 3 else "load_dataset"

                if target not in ("t1", "t2"):
                    print("Invalid target. Use t1 or t2.")
                    continue

                if stage not in allowed_stages:
                    print(f"Invalid stage '{stage}'. Use one of: {', '.join(sorted(allowed_stages))}")
                    continue

                chosen = task1 if target == "t1" else task2
                if not chosen or not chosen.get("id"):
                    print(f"{target} does not exist for this run.")
                    continue

                tid = str(chosen["id"])
                tname = str(chosen.get("name") or target)

                res = restart_and_wait(tid, tname, stage)
                if res.get("passed") is False:
                    return res
                continue

            print("Invalid input. Use: yes | fail | restart | restart t1|t2 [stage]")

    # ============================================================
    # RUN
    # ============================================================

    def run(self, *, resume: bool = True, force_stages: Optional[Set[str]] = None) -> Dict[str, Any]:
        pipeline_logger = self.loggers["pipeline"]
        pipeline_logger.info(f"Starting RGB Pipeline | run_id={self.run_id}")

        try:
            r = self.repo.get_run(self.run_id)
            if r and r.get("status") == "paused":
                self.repo.mark_run_running(self.run_id)
                pipeline_logger.info("Resuming paused run -> status set to running")
        except Exception:
            pipeline_logger.exception("Failed while attempting to resume paused run")

        total_start = time.perf_counter()
        force_stages = force_stages or set()

        try:
            self._check_pause_or_raise("data_segregation")
            self.runner.run(
                "data_segregation",
                self.stage_data_segregation,
                output_key="data_segregation",
                state=self.state,
                force=("data_segregation" in force_stages) or (not resume),
            )

            self._check_pause_or_raise("cross_run_filter")
            self.runner.run(
                "cross_run_filter",
                self.stage_cross_run_image_filter,
                output_key="cross_run_filter",
                state=self.state,
                force=("cross_run_filter" in force_stages) or (not resume),
            )

            self._check_pause_or_raise("kml_boundary")
            self.runner.run(
                "kml_boundary",
                self.stage_kml_boundary,
                output_key="kml_boundary",
                state=self.state,
                force=("kml_boundary" in force_stages) or (not resume),
            )

            self._check_pause_or_raise("webodm")
            self.runner.run(
                "webodm",
                self.stage_webodm,
                output_key="webodm",
                state=self.state,
                force=("webodm" in force_stages) or (not resume),
            )

            self._check_pause_or_raise("qgis")
            self.runner.run(
                "qgis",
                self.stage_qgis,
                output_key="qgis",
                state=self.state,
                force=("qgis" in force_stages) or (not resume),
            )
            
            self._check_pause_or_raise("quality_gate")
            self.runner.run(
                "quality_gate",
                self.stage_quality_gate,
                output_key="quality_gate",
                state=self.state,
                force=("quality_gate" in force_stages) or (not resume),
            )

            q = self.state.get("quality_gate") or {}
            if q.get("passed") is False:
                raise RuntimeError("Pipeline stopped due to failed Quality Gate.")

            self.state["success"] = True

            total_runtime = time.perf_counter() - total_start
            self.repo.mark_run_finished(self.run_id, success=True, total_runtime_seconds=total_runtime)

            if self.survey_id:
                self.repo.mark_survey_finished(self.survey_id, success=True, total_runtime_seconds=total_runtime)

            pipeline_logger.info(f"RGB Pipeline finished | run_id={self.run_id} | success=True")
            return self.state

        except RuntimeError as e:
            if str(e) == "__PIPELINE_PAUSED__":
                self.state["success"] = False
                self.state["paused"] = True
                self.state["error"] = "paused_by_flag"
                pipeline_logger.warning(f"RGB Pipeline paused | run_id={self.run_id}")
                return self.state

            if str(e) == "__PIPELINE_CANCELED__":
                self.state["success"] = False
                self.state["canceled"] = True
                self.state["error"] = "canceled_in_webodm_ui"
                pipeline_logger.warning(f"RGB Pipeline canceled | run_id={self.run_id}")
                try:
                    self.repo.mark_run_paused(
                        self.run_id,
                        paused_after_stage="webodm",
                        reason="webodm_ui_cancel",
                    )
                except Exception:
                    pipeline_logger.exception("Failed to mark run paused after WebODM cancel")
                return self.state

            # any other RuntimeError -> treat as failure
            raise

        except Exception as e:
            self.state["success"] = False
            self.state["error"] = str(e)

            total_runtime = time.perf_counter() - total_start
            self.repo.mark_run_finished(self.run_id, success=False, total_runtime_seconds=total_runtime)

            if self.survey_id:
                self.repo.mark_survey_finished(self.survey_id, success=False, total_runtime_seconds=total_runtime)

            pipeline_logger.exception(f"RGB Pipeline failed | run_id={self.run_id}")
            return self.state