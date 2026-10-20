from __future__ import annotations

import json
import math
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable


class RasterMetadataError(RuntimeError):
    """Raised when required raster metadata cannot be read or validated."""


_BITS_BY_GDAL_TYPE = {
    "Byte": 8,
    "Int8": 8,
    "UInt16": 16,
    "Int16": 16,
    "Float16": 16,
    "UInt32": 32,
    "Int32": 32,
    "Float32": 32,
    "CInt16": 32,
    "UInt64": 64,
    "Int64": 64,
    "Float64": 64,
    "CInt32": 64,
    "CFloat32": 64,
    "CFloat64": 128,
}


def inspect_raster_metadata(
    tif_path: Path,
    *,
    gdalinfo_path: str,
    command_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Read and validate required GeoTIFF metadata through ``gdalinfo -json``."""

    tif_path = Path(tif_path)
    if not tif_path.is_file():
        raise RasterMetadataError(f"Raster metadata source is not a file: {tif_path}")
    if tif_path.stat().st_size <= 0:
        raise RasterMetadataError(f"Raster metadata source is empty: {tif_path}")

    command = [str(gdalinfo_path), "-json", str(tif_path)]
    try:
        completed = command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RasterMetadataError(f"gdalinfo not found: {gdalinfo_path}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = str(exc.stderr or "").strip()[:1000]
        detail = f": {stderr}" if stderr else ""
        raise RasterMetadataError(
            f"gdalinfo could not read raster metadata for {tif_path.name}{detail}"
        ) from exc
    except OSError as exc:
        raise RasterMetadataError(
            f"Unable to execute gdalinfo for {tif_path.name}: {exc}"
        ) from exc

    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RasterMetadataError(
            f"gdalinfo returned invalid JSON for {tif_path.name}"
        ) from exc
    if not isinstance(payload, dict):
        raise RasterMetadataError(
            f"gdalinfo JSON must be an object for {tif_path.name}"
        )

    driver = payload.get("driverShortName")
    if driver != "GTiff":
        raise RasterMetadataError(
            f"Expected GTiff raster for {tif_path.name}, received {driver!r}"
        )

    size = payload.get("size")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in size)
        or any(value <= 0 for value in size)
    ):
        raise RasterMetadataError(
            f"Missing or invalid raster dimensions for {tif_path.name}"
        )
    width, height = size

    bands = payload.get("bands")
    if not isinstance(bands, list) or not bands:
        raise RasterMetadataError(f"Raster has no readable bands: {tif_path.name}")

    band_records: list[dict[str, Any]] = []
    bit_depths: list[int] = []
    for index, band in enumerate(bands, start=1):
        if not isinstance(band, dict):
            raise RasterMetadataError(
                f"Invalid band metadata for {tif_path.name} band {index}"
            )
        data_type = band.get("type")
        bit_depth = _BITS_BY_GDAL_TYPE.get(str(data_type))
        if bit_depth is None:
            raise RasterMetadataError(
                f"Unsupported GDAL band type for {tif_path.name}: {data_type!r}"
            )
        band_number = band.get("band", index)
        if (
            isinstance(band_number, bool)
            or not isinstance(band_number, int)
            or band_number <= 0
        ):
            raise RasterMetadataError(
                f"Invalid band number for {tif_path.name} band {index}"
            )
        bit_depths.append(bit_depth)
        band_records.append(
            {
                "band": band_number,
                "data_type": str(data_type),
                "bit_depth": bit_depth,
                "color_interpretation": band.get("colorInterpretation"),
            }
        )

    unique_bit_depths = set(bit_depths)
    if len(unique_bit_depths) != 1:
        raise RasterMetadataError(
            f"Raster bands do not share one bit depth: {tif_path.name}"
        )

    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    default_metadata = metadata.get("")
    default_metadata = default_metadata if isinstance(default_metadata, dict) else {}
    image_structure = metadata.get("IMAGE_STRUCTURE")
    image_structure = image_structure if isinstance(image_structure, dict) else {}

    program_name = default_metadata.get("TIFFTAG_SOFTWARE")
    if not isinstance(program_name, str) or not program_name.strip():
        raise RasterMetadataError(
            f"Required TIFFTAG_SOFTWARE is missing for {tif_path.name}"
        )

    compression = image_structure.get("COMPRESSION", "NONE")
    if not isinstance(compression, str) or not compression.strip():
        raise RasterMetadataError(
            f"Invalid compression metadata for {tif_path.name}"
        )

    geotransform = payload.get("geoTransform")
    if (
        not isinstance(geotransform, list)
        or len(geotransform) != 6
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in geotransform
        )
        or any(not math.isfinite(float(value)) for value in geotransform)
        or float(geotransform[1]) == 0.0
        or float(geotransform[5]) == 0.0
    ):
        raise RasterMetadataError(
            f"Missing or invalid pixel size for {tif_path.name}"
        )

    coordinate_system = payload.get("coordinateSystem")
    coordinate_system = (
        coordinate_system if isinstance(coordinate_system, dict) else {}
    )
    wkt = coordinate_system.get("wkt")
    if not isinstance(wkt, str) or not wkt.strip():
        raise RasterMetadataError(f"Required CRS is missing for {tif_path.name}")
    epsg_matches = re.findall(r'ID\["EPSG",\s*(\d+)\]', wkt)
    if not epsg_matches:
        raise RasterMetadataError(
            f"Unable to identify an EPSG CRS for {tif_path.name}"
        )

    bits_per_channel = unique_bit_depths.pop()
    return {
        "driver": driver,
        "program_name": program_name.strip(),
        "dimensions": f"{width} x {height}",
        "width_pixels": width,
        "height_pixels": height,
        "band_count": len(bands),
        "bits_per_channel": bits_per_channel,
        "bits_per_pixel": sum(bit_depths),
        "compression": compression.strip(),
        "pixel_size_x": float(geotransform[1]),
        "pixel_size_y": float(geotransform[5]),
        "pixel_size_unit": _pixel_size_unit(wkt),
        "crs": f"EPSG:{epsg_matches[-1]}",
        "bands": band_records,
        "file_size_bytes": tif_path.stat().st_size,
    }


def build_raster_manifest_record(
    raster_metadata: dict[str, Any],
    *,
    published_path: Path,
    manifest_root: Path,
    role: str,
    task_key: str,
    run_id: str,
) -> dict[str, Any]:
    published_path = Path(published_path).resolve(strict=False)
    manifest_root = Path(manifest_root).resolve(strict=False)
    try:
        relative_path = published_path.relative_to(manifest_root)
    except ValueError as exc:
        raise RasterMetadataError(
            f"Raster output is outside the survey manifest root: {published_path}"
        ) from exc

    record = dict(raster_metadata)
    record.update(
        {
            "role": role,
            "task_key": task_key,
            "run_id": run_id,
            "filename": published_path.name,
            "relative_path": relative_path.as_posix(),
        }
    )
    _require_strict_json_object(record, "Raster manifest record")
    return record


def update_raster_manifest(
    manifest_path: Path,
    *,
    survey_id: str,
    raster_key: str,
    raster_record: dict[str, Any],
) -> Path:
    """Atomically add or replace one idempotent raster record."""

    manifest_path = Path(manifest_path)
    payload = validate_raster_manifest(manifest_path, survey_id=survey_id)

    existing = payload.get("raster_outputs")
    if existing is None:
        raster_outputs: dict[str, Any] = {}
    elif isinstance(existing, dict):
        raster_outputs = dict(existing)
    else:
        raise RasterMetadataError("Survey manifest raster_outputs must be an object")

    raster_outputs[raster_key] = dict(raster_record)
    updated = dict(payload)
    updated["raster_outputs"] = raster_outputs
    _write_json_atomic(manifest_path, updated)
    return manifest_path


def validate_raster_manifest(
    manifest_path: Path,
    *,
    survey_id: str,
) -> dict[str, Any]:
    """Validate the survey identity and raster-output container without writing."""

    manifest_path = Path(manifest_path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RasterMetadataError(f"Survey manifest not found: {manifest_path}")
    except (OSError, json.JSONDecodeError) as exc:
        raise RasterMetadataError(
            f"Unable to read survey manifest: {manifest_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise RasterMetadataError("Survey manifest must contain a JSON object")
    _require_strict_json_object(payload, "Survey manifest")
    if payload.get("survey_id") != survey_id:
        raise RasterMetadataError(
            "Survey manifest identity does not match the raster output"
        )

    existing = payload.get("raster_outputs")
    if existing is not None and not isinstance(existing, dict):
        raise RasterMetadataError("Survey manifest raster_outputs must be an object")
    return payload


def _require_strict_json_object(payload: dict[str, Any], label: str) -> None:
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RasterMetadataError(
            f"{label} contains values that cannot be written as strict JSON"
        ) from exc


def _pixel_size_unit(wkt: str) -> str:
    normalized = wkt.lstrip().upper()
    if normalized.startswith(("GEOGCRS[", "GEOGCS[")):
        return "degree"
    if normalized.startswith(("PROJCRS[", "PROJCS[")):
        match = re.search(r'LENGTHUNIT\["([^"]+)"', wkt)
        return match.group(1) if match else "linear_unit"
    raise RasterMetadataError("Unsupported CRS type for raster pixel size")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
