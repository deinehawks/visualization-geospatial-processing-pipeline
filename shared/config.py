from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv
import os
import json


def load_pipeline_config() -> dict:
    load_dotenv()

    def read_json_env(name: str, default: dict) -> dict:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {name}: {e}")

    def read_csv_env(name: str, default: list[str]) -> list[str]:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        return [x.strip() for x in raw.split(",") if x.strip()]

    config = {
        "paths": {
            "surveys_root": Path(os.getenv("SURVEYS_ROOT")),
            "field_data_root": Path(os.getenv("FIELD_DATA_ROOT")),
            "upload_cache_root": os.getenv("UPLOAD_CACHE_ROOT"),
        },
        "cross_run_filter": {
            "max_gap": int(os.getenv("CROSSRUN_MAX_GAP", 10)),
            "window": int(os.getenv("CROSSRUN_WINDOW", 3)),
            "delete_raw_after_success": os.getenv("DELETE_RAW", "false").lower() == "true",
        },
        "naming": {
            # crossrun_flag is computed at runtime, but keep defaults here
            "crossrun_mode": os.getenv("CROSSRUN_MODE", "xc"),
            "task1_boundary_mode": os.getenv("TASK1_BOUNDARY_MODE", "xb"),
            "task2_boundary_mode": os.getenv("TASK2_BOUNDARY_MODE", "b"),
        },
        "exports": {
            "enabled": os.getenv("EXPORTS_ENABLED", "true").lower() == "true",
            "tools": {
                # optional override
                "gdalwarp_path": os.getenv("GDALWARP_PATH", "gdalwarp"),
            },

            # Task 1 Ortho (EPSG:4326, GeoTIFF raw from WebODM -> reproject to 4326 anyway)
            "ortho": {
                "enabled": os.getenv("EXPORT_ORTHO", "true").lower() == "true",
                "out_dir_key": os.getenv("ORTHO_OUT_DIR_KEY", "ortho"),
                "reproject_epsg": int(os.getenv("ORTHO_EPSG", 4326)),
                "filename_template": os.getenv("ORTHO_FILENAME_TEMPLATE", "orthomosaic--{flag}.tif"),
                # these are WebODM asset paths / names (depends on your WebODM build)
                "asset_candidates": read_csv_env("ORTHO_ASSET_CANDIDATES", ["orthophoto.tif"]),
            },

            # Task 2 DEM (optional)
            "dem": {
                "enabled": os.getenv("EXPORT_DEM", "false").lower() == "true",  
                "download": os.getenv("EXPORT_DEM_DOWNLOAD", "true").lower() == "true",
                "reproject_epsg": int(os.getenv("DEM_EPSG", 3857)),
                "dtm_dir_key": os.getenv("DTM_DIR_KEY", "dem_dtm"),
                "dsm_dir_key": os.getenv("DSM_DIR_KEY", "dem_dsm"),
                "filename_template": os.getenv("DEM_FILENAME_TEMPLATE", "{color}-{shading}.tif"),

                # choose which models to download
                "models": read_csv_env("DEM_MODELS", ["dtm", "dsm"]),
                # configure these lists to make “30 DEMs”
                "colors": read_csv_env("DEM_COLORS", ["earth", "gray", "rainbow", "slope", "terrain"]),
                "shadings": read_csv_env("DEM_SHADINGS", ["none", "hillshade", "extruded"]),
            },
            "pointcloud": {
                "enabled": os.getenv("EXPORT_POINTCLOUD", "true").lower() == "true",
                "out_dir_key": os.getenv("POINTCLOUD_OUT_DIR_KEY", "3d"),
                "filename_template": os.getenv(
                    "POINTCLOUD_FILENAME_TEMPLATE",
                    "{survey_id}-RGB-{flag}.ply"
                ),
                "asset_candidates": read_csv_env(
                    "POINTCLOUD_ASSET_CANDIDATES",
                    [
                        "model.ply",
                        "odm_georeferencing/odm_georeferenced_model.ply",
                        "odm_texturing/model.ply",
                    ],
                ),
                # if enabled but download fails: fail stage or just warn?
                "required": os.getenv("POINTCLOUD_REQUIRED", "true").lower() == "true",
            },

            # Task 2 All-assets zip (archive into rgb/odm)
            "all_assets_zip": {
                "enabled": os.getenv("EXPORT_ALL_ASSETS_ZIP", "true").lower() == "true",
                "out_dir_key": os.getenv("ALL_ASSETS_OUT_DIR_KEY", "odm"),
                "filename_template": os.getenv("ALL_ASSETS_FILENAME_TEMPLATE", "{survey_id}-RGB-{flag}-all.zip"),
            },
        },
        "webodm": {
            "url": os.getenv("WEBODM_URL"),
            "username": os.getenv("WEBODM_USERNAME"),
            "password": os.getenv("WEBODM_PASSWORD"),
            "node_id": int(os.getenv("WEBODM_NODE_ID", 0)),
            "task1_options": read_json_env("WEBODM_TASK1_OPTIONS_JSON", {}),
            "task2_options": read_json_env("WEBODM_TASK2_OPTIONS_JSON", {}),
        },
    }

    # Basic validation
    if not config["webodm"]["url"]:
        raise ValueError("WEBODM_URL missing in .env")
    if not config["webodm"]["username"]:
        raise ValueError("WEBODM_USERNAME missing in .env")
    if not config["webodm"]["password"]:
        raise ValueError("WEBODM_PASSWORD missing in .env")
    if not config["webodm"]["node_id"]:
        raise ValueError("WEBODM_NODE_ID missing in .env")

    return config