from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv
from typing import Any
import json
import os
import shutil


def load_pipeline_config() -> dict:
    load_dotenv()

    # ============================================================
    # ENV READERS
    # ============================================================

    def read_str_env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            if required and default is None:
                raise ValueError(f"{name} missing in .env")
            return default
        return raw.strip()

    def read_bool_env(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default

        value = raw.strip().lower()
        if value in {"true", "1", "yes", "y", "on"}:
            return True
        if value in {"false", "0", "no", "n", "off"}:
            return False

        raise ValueError(f"Invalid boolean value for {name}: {raw!r}")

    def read_int_env(name: str, default: int, *, minimum: int | None = None) -> int:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            value = default
        else:
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f"Invalid integer value for {name}: {raw!r}")

        if minimum is not None and value < minimum:
            raise ValueError(f"{name} must be >= {minimum}, got {value}")

        return value

    def read_json_env(name: str, default: dict) -> dict:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {name}: {e}")

        if not isinstance(value, dict):
            raise ValueError(f"{name} must be a JSON object/dict, got {type(value).__name__}")

        return value

    def read_csv_env(name: str, default: list[str]) -> list[str]:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        values = [x.strip() for x in raw.split(",") if x.strip()]
        return values if values else default

    def read_path_env(name: str, default: str | None = None, *, required: bool = False) -> Path | None:
        raw = read_str_env(name, default=default, required=required)
        if raw is None:
            return None
        return Path(raw)

    # ============================================================
    # BUILD CONFIG
    # ============================================================

    config = {
        "paths": {
            "surveys_root": read_path_env("SURVEYS_ROOT", required=True),
            "field_data_root": read_path_env("FIELD_DATA_ROOT", required=True),
            "upload_cache_root": read_path_env("UPLOAD_CACHE_ROOT"),
        },
        "cross_run_filter": {
            "max_gap": read_int_env("CROSSRUN_MAX_GAP", 10, minimum=0),
            "window": read_int_env("CROSSRUN_WINDOW", 3, minimum=0),
            "delete_raw_after_success": read_bool_env("DELETE_RAW", False),
        },
        "naming": {
            "crossrun_mode": read_str_env("CROSSRUN_MODE", "xc"),
            "task1_boundary_mode": read_str_env("TASK1_BOUNDARY_MODE", "xb"),
            "task2_boundary_mode": read_str_env("TASK2_BOUNDARY_MODE", "b"),
        },
        "exports": {
            "enabled": read_bool_env("EXPORTS_ENABLED", True),
            "tools": {
                "gdalwarp_path": read_str_env("GDALWARP_PATH", "gdalwarp"),
                "pdal_path": read_str_env("PDAL_PATH", "pdal"),
            },
            "ortho": {
                "enabled": read_bool_env("EXPORT_ORTHO", True),
                "out_dir_key": read_str_env("ORTHO_OUT_DIR_KEY", "ortho"),
                "reproject_epsg": read_int_env("ORTHO_EPSG", 4326, minimum=1),
                "filename_template": read_str_env("ORTHO_FILENAME_TEMPLATE", "orthomosaic--{flag}.tif"),
                "asset_candidates": read_csv_env("ORTHO_ASSET_CANDIDATES", ["orthophoto.tif"]),
            },
            "dem": {
                "enabled": read_bool_env("EXPORT_DEM", False),
                "download": read_bool_env("EXPORT_DEM_DOWNLOAD", True),
                "reproject_epsg": read_int_env("DEM_EPSG", 3857, minimum=1),
                "dtm_dir_key": read_str_env("DTM_DIR_KEY", "dem_dtm"),
                "dsm_dir_key": read_str_env("DSM_DIR_KEY", "dem_dsm"),
                "filename_template": read_str_env("DEM_FILENAME_TEMPLATE", "{color}-{shading}.tif"),
                "models": read_csv_env("DEM_MODELS", ["dtm", "dsm"]),
                "colors": read_csv_env("DEM_COLORS", ["earth", "gray", "rainbow", "slope", "terrain"]),
                "shadings": read_csv_env("DEM_SHADINGS", ["none", "hillshade", "extruded"]),
            },
            "pointcloud": {
                "enabled": read_bool_env("EXPORT_POINTCLOUD", True),
                "out_dir_key": read_str_env("POINTCLOUD_OUT_DIR_KEY", "3d"),
                "filename_template": read_str_env("POINTCLOUD_FILENAME_TEMPLATE", "{survey_id}-RGB-{flag}.ply"),
                "asset_candidates": read_csv_env(
                    "POINTCLOUD_ASSET_CANDIDATES",
                    [
                        "georeferenced_model.laz",
                        "odm_georeferencing/georeferenced_model.laz",
                        "odm_georeferencing/odm_georeferenced_model.laz",
                        "odm_georeferencing/odm_georeferenced_model.laz.zip",
                        "odm_georeferencing/georeferenced_model.laz.zip",
                    ],
                ),
                "required": read_bool_env("POINTCLOUD_REQUIRED", True),
            },
            "all_assets_zip": {
                "enabled": read_bool_env("EXPORT_ALL_ASSETS_ZIP", True),
                "out_dir_key": read_str_env("ALL_ASSETS_OUT_DIR_KEY", "odm"),
                "filename_template": read_str_env(
                    "ALL_ASSETS_FILENAME_TEMPLATE",
                    "{survey_id}-RGB-{flag}-all.zip",
                ),
            },
        },
        "webodm": {
            "url": read_str_env("WEBODM_URL", required=True),
            "username": read_str_env("WEBODM_USERNAME", required=True),
            "password": read_str_env("WEBODM_PASSWORD", required=True),
            "node_id": read_int_env("WEBODM_NODE_ID", 0, minimum=1),
            "task1_options": read_json_env("WEBODM_TASK1_OPTIONS_JSON", {}),
            "task2_options": read_json_env("WEBODM_TASK2_OPTIONS_JSON", {}),
        },
        "qgis": {
            "enabled": read_bool_env("QGIS_ENABLED", True),
            "tools": {
                "qgis_root": read_str_env("QGIS_ROOT", ""),
                "gdalwarp_path": read_str_env("GDALWARP_PATH", "gdalwarp"),
                "gdal2tiles_path": read_str_env("GDAL2TILES_PATH", "gdal2tiles.py"),
            },
            "clip": {
                "enabled": read_bool_env("QGIS_CLIP_ENABLED", True),
                "filename_template": read_str_env(
                    "QGIS_CLIP_FILENAME_TEMPLATE",
                    "orthomosaic-clipped--{flag}.tif",
                ),
                "dst_nodata": read_str_env("QGIS_CLIP_DST_NODATA", ""),
            },
            "tiles": {
                "enabled": read_bool_env("QGIS_TILES_ENABLED", True),
                "zoom": read_str_env("QGIS_TILES_ZOOM", "11-24"),
                "profile": read_str_env("QGIS_TILES_PROFILE", "mercator"),
                "webviewer": read_str_env("QGIS_TILES_WEBVIEWER", "none"),
                "copyright": read_str_env("QGIS_TILES_COPYRIGHT", "ASIMOV-HAWKS"),
            },
        },
    }

    # ============================================================
    # VALIDATION
    # ============================================================

    def validate_existing_path(path_value: Path | None, label: str) -> None:
        if path_value is None:
            raise ValueError(f"{label} is required")
        if not path_value.exists():
            raise ValueError(f"{label} does not exist: {path_value}")

    def validate_executable(path_or_name: str | None, label: str, *, required: bool = False) -> None:
        if not path_or_name:
            if required:
                raise ValueError(f"{label} is required")
            return

        p = Path(path_or_name)
        if p.suffix:
            if not p.exists():
                if required:
                    raise ValueError(f"{label} not found: {path_or_name}")
                return
        else:
            found = shutil.which(path_or_name)
            if found is None and required:
                raise ValueError(f"{label} not found on PATH: {path_or_name}")

    def validate_choice(value: str | None, label: str, allowed: set[str]) -> None:
        if value is None:
            return
        if value not in allowed:
            raise ValueError(f"{label} must be one of {sorted(allowed)}, got {value!r}")

    def validate_template(value: str | None, label: str, required_tokens: list[str]) -> None:
        if not value:
            raise ValueError(f"{label} must not be empty")
        for token in required_tokens:
            if token not in value:
                raise ValueError(f"{label} must contain {token!r}, got {value!r}")

    def validate_nonempty_list(values: list[str], label: str) -> None:
        if not values:
            raise ValueError(f"{label} must not be empty")

    def validate_zoom(value: str) -> None:
        # accepts "11-24" or "18"
        if "-" in value:
            a, b = value.split("-", 1)
            try:
                start = int(a)
                end = int(b)
            except ValueError:
                raise ValueError(f"QGIS_TILES_ZOOM must be like '11-24' or '18', got {value!r}")
            if start < 0 or end < 0 or start > end:
                raise ValueError(f"QGIS_TILES_ZOOM invalid range: {value!r}")
        else:
            try:
                z = int(value)
            except ValueError:
                raise ValueError(f"QGIS_TILES_ZOOM must be like '11-24' or '18', got {value!r}")
            if z < 0:
                raise ValueError(f"QGIS_TILES_ZOOM must be >= 0, got {value!r}")

    # required paths
    validate_existing_path(config["paths"]["surveys_root"], "SURVEYS_ROOT")
    validate_existing_path(config["paths"]["field_data_root"], "FIELD_DATA_ROOT")

    # naming
    validate_choice(config["naming"]["crossrun_mode"], "CROSSRUN_MODE", {"c", "xc"})
    validate_choice(config["naming"]["task1_boundary_mode"], "TASK1_BOUNDARY_MODE", {"xb", "b", "cb", "cxb", "xcxb", "xcb"})
    validate_choice(config["naming"]["task2_boundary_mode"], "TASK2_BOUNDARY_MODE", {"b", "cb", "xcb"})

    # export templates
    validate_template(config["exports"]["ortho"]["filename_template"], "ORTHO_FILENAME_TEMPLATE", ["{flag}"])
    validate_template(config["exports"]["pointcloud"]["filename_template"], "POINTCLOUD_FILENAME_TEMPLATE", ["{survey_id}", "{flag}"])
    validate_template(config["exports"]["all_assets_zip"]["filename_template"], "ALL_ASSETS_FILENAME_TEMPLATE", ["{survey_id}", "{flag}"])
    validate_template(config["qgis"]["clip"]["filename_template"], "QGIS_CLIP_FILENAME_TEMPLATE", ["{flag}"])

    # non-empty candidate lists
    validate_nonempty_list(config["exports"]["ortho"]["asset_candidates"], "ORTHO_ASSET_CANDIDATES")
    validate_nonempty_list(config["exports"]["pointcloud"]["asset_candidates"], "POINTCLOUD_ASSET_CANDIDATES")

    # DEM validation
    dem_cfg = config["exports"]["dem"]
    validate_nonempty_list(dem_cfg["models"], "DEM_MODELS")
    validate_nonempty_list(dem_cfg["colors"], "DEM_COLORS")
    validate_nonempty_list(dem_cfg["shadings"], "DEM_SHADINGS")
    for model in dem_cfg["models"]:
        if model not in {"dtm", "dsm"}:
            raise ValueError(f"DEM_MODELS contains invalid value: {model!r}")

    # WebODM
    validate_choice(
        str(config["webodm"]["url"]).split("://", 1)[0],
        "WEBODM_URL scheme",
        {"http", "https"},
    )

    # tool validation
    validate_executable(config["exports"]["tools"]["gdalwarp_path"], "GDALWARP_PATH", required=False)
    validate_executable(config["exports"]["tools"]["pdal_path"], "PDAL_PATH", required=False)
    validate_executable(config["qgis"]["tools"]["gdalwarp_path"], "QGIS gdalwarp_path", required=False)
    validate_executable(config["qgis"]["tools"]["gdal2tiles_path"], "GDAL2TILES_PATH", required=False)

    # qgis tiles
    validate_choice(config["qgis"]["tiles"]["profile"], "QGIS_TILES_PROFILE", {"mercator", "geodetic", "raster"})
    validate_choice(config["qgis"]["tiles"]["webviewer"], "QGIS_TILES_WEBVIEWER", {"none", "all", "google", "openlayers", "leaflet"})
    validate_zoom(config["qgis"]["tiles"]["zoom"])

    # qgis nodata
    dst_nodata = config["qgis"]["clip"]["dst_nodata"]
    if dst_nodata not in ("", None):
        try:
            float(str(dst_nodata))
        except ValueError:
            raise ValueError(f"QGIS_CLIP_DST_NODATA must be empty or numeric, got {dst_nodata!r}")

    return config